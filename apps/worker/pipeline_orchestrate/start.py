"""Lõi task `pipeline.orchestrate.start` (B5-06a [6]): hai giao dịch ngắn quanh việc chậm.

Chia ba pha vì K36: GD1 đọc dữ kiện dưới khoá lượt rồi **nhả** session, `prepare_page`
dựng/nắn/đo trang ngoài mọi session, GD2 mở session mới ghi kết quả và xếp ba bước ML.
Mọi hàm của B2-04/B2-05b trả `None` nghĩa là lượt đã kết thúc hay bị thay: khi đó rollback,
log `pipeline_result_ignored` và xoá trang **mới** lượt này vừa ghi (không đụng `pages/{i}.png`).
Task chỉ gửi qua `on_after_commit` (J09); lỗi tạm/vĩnh viễn để `define_task` xử.
"""

import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.admin_ml_registry.registry import active_versions
from apps.api.drawings.drawings import current_drawing, upsert_drawing
from apps.api.drawings.runs import RunRow, lock_run, publish_progress_after_commit, record_step
from apps.api.quality.assessments import load_assessment, save_assessment
from apps.worker.pipeline_orchestrate.dispatch import queue_infer
from apps.worker.pipeline_orchestrate.keys import run_prefix
from apps.worker.pipeline_orchestrate.pins import load_pins, pin_models, set_step_requeue
from apps.worker.pipeline_orchestrate.preprocess import (
    PagePlan,
    PageSource,
    PreparedPage,
    choose_path,
    prepare_page,
)
from apps.worker.pipeline_orchestrate.section import skip_section_steps
from apps.worker.pipeline_orchestrate.settings import OrchestrateSettings, get_orchestrate_settings
from packages.core.clock import Clock
from packages.db.engine import session_scope
from packages.db.hooks import after_commit_idle
from packages.db.models.drawings import UploadRow
from packages.db.models.floors import FloorRow
from packages.messaging.payloads.drawings import PipelineStartPayload
from packages.storage.port import ObjectStorage

_log: Final = logging.getLogger(__name__)

STEP: Final = "preprocess"
"""Bước mà task này sở hữu; mọi `record_step` của module ghi đúng bước này."""


def _ignored(run_id: str, reason: str) -> None:
    """Log một lượt giao bị bỏ (BE-00 §7): không phải lỗi, chỉ là kết quả tới muộn."""
    _log.info("pipeline_result_ignored", extra={"run_id": run_id, "task": "start", "reason": reason})


@dataclass(frozen=True, slots=True)
class _Phase1:
    """Dữ kiện GD1 đọc dưới khoá, đủ cho việc chậm và GD2 chạy không cần đọc lại."""

    plan: PagePlan
    floor_pk: int
    is_section: bool = False
    """Dòng tầng là mặt cắt/mặt đứng (HS-03): lượt chỉ đọc chữ (K-32c)."""


async def _upload_facts(db: AsyncSession, run: RunRow) -> tuple[UploadRow, str, bool]:
    """Dòng `uploads` của lượt, `level_id` công khai của tầng (khoá storage dùng nó) và cờ `is_section` của tầng."""
    upload = (await db.execute(select(UploadRow).where(UploadRow.id == run.upload_id))).scalar_one()
    floor = (await db.execute(select(FloorRow.level_id, FloorRow.is_section).where(FloorRow.pk == run.floor_pk))).one()
    return upload, floor.level_id, bool(floor.is_section)


async def _enter_running(db: AsyncSession, run: RunRow, clock: Clock) -> RunRow | None:
    """`pending` → ghi `preprocess running` rồi khoá lại; `running` sẵn (thử lại) → giữ nguyên."""
    if run.status != "pending":
        return run
    await record_step(db, run_id=run.id, step=STEP, status="running", clock=clock)
    return await lock_run(db, run_id=run.id)


async def _gd1(db: AsyncSession, payload: PipelineStartPayload, clock: Clock) -> _Phase1 | None:
    """GD1 của [6] bước 1: kiểm lượt, ghim model, mở bước, đọc dữ kiện, chọn đường.

    `None` = dừng sau khi commit những gì đã ghi (lượt muộn, lượt đã qua bước ML — J06/J10).
    """
    run = await lock_run(db, run_id=payload.run_id)
    if run is None or run.upload_id != payload.upload_id:
        _ignored(payload.run_id, "run_missing" if run is None else "upload_mismatch")
        return None
    if run.current_step != STEP:
        await publish_progress_after_commit(db, run.upload_id)
        _ignored(run.id, f"step_is_{run.current_step}")
        return None
    if await load_pins(db, run.id) is None:
        await pin_models(db, run_id=run.id, models=await active_versions(db))
    opened = await _enter_running(db, run, clock)
    if opened is None:
        _ignored(run.id, "run_gone_after_record_step")
        return None
    upload, level_id, is_section = await _upload_facts(db, opened)
    source = PageSource(
        run_id=opened.id,
        project_id=upload.project_id,
        level_id=level_id,
        upload_id=upload.id,
        page_index=upload.page_index,
        original_key=upload.original_key,
        kind=upload.sniffed_kind,
        crop=tuple((float(x), float(y)) for x, y in upload.crop_corners) if upload.crop_corners else None,
    )
    drawing = await current_drawing(db, opened.floor_pk)
    assessment = await load_assessment(db, opened.floor_pk)
    plan = choose_path(source, drawing, assessment)
    return _Phase1(plan=plan, floor_pk=opened.floor_pk, is_section=is_section)


async def _write_page(db: AsyncSession, run: RunRow, page: PreparedPage, clock: Clock) -> bool:
    """Đường (ii)/(iii): ghi `drawings` rồi `quality_assessments`; `None` ở một trong hai → `False`.

    Đường (i) không có `report`/`homography` nên không đi vào đây (GD2 chỉ ghi `record_step`).
    """
    if page.report is None or page.homography is None:
        return True
    drawing = await upsert_drawing(
        db,
        run_id=run.id,
        floor_pk=run.floor_pk,
        upload_id=run.upload_id,
        page_key=page.page_key,
        width_px=page.width_px,
        height_px=page.height_px,
        clock=clock,
        px_per_paper_mm=page.px_per_paper_mm,
    )
    if drawing is None:
        return False
    saved = await save_assessment(
        db,
        run_id=run.id,
        floor_pk=run.floor_pk,
        drawing_id=drawing.id,
        page_key=page.page_key,
        width_px=page.width_px,
        height_px=page.height_px,
        report=page.report,
        homography=page.homography,
        corners=page.corners,
        clock=clock,
    )
    return saved is not None


async def _gd2(
    db: AsyncSession, payload: PipelineStartPayload, phase1: _Phase1, page: PreparedPage, clock: Clock
) -> bool:
    """GD2 của [6] bước 4: ghi kết quả dưới khoá rồi xếp ba bước ML; `False` = rollback, dừng."""
    run = await lock_run(db, run_id=payload.run_id)
    if run is None or run.upload_id != payload.upload_id:
        _ignored(payload.run_id, "run_superseded_during_slow_work")
        return False
    if not await _write_page(db, run, page, clock):
        _ignored(run.id, "drawing_or_assessment_rejected")
        return False
    if await record_step(db, run_id=run.id, step=STEP, status="completed", clock=clock) is None:
        _ignored(run.id, "record_step_completed_rejected")
        return False
    await set_step_requeue(db, run_id=run.id, count=0)
    pins = await load_pins(db, run.id)
    if pins is None:
        _ignored(run.id, "pins_missing")
        return False
    if phase1.is_section:
        # K-32c: mặt cắt chỉ đọc chữ — bỏ tách tường/dò đồ đạc, `queue_infer` sẽ chỉ gửi `dimensionReading`.
        if not await skip_section_steps(db, run_id=run.id, pins=pins, clock=clock):
            _ignored(run.id, "section_skip_rejected")
            return False
        pins = await load_pins(db, run.id)
        if pins is None:
            _ignored(run.id, "pins_missing")
            return False
    source = phase1.plan.source
    queue_infer(
        db,
        pins=pins,
        run_prefix=run_prefix(
            project_id=source.project_id,
            level_id=source.level_id,
            upload_id=source.upload_id,
            run_id=run.id,
        ),
        page_key=page.page_key,
        width_px=page.width_px,
        height_px=page.height_px,
        px_per_paper_mm=page.px_per_paper_mm,
        vectors_key=page.vectors_key,
    )
    return True


async def _commit_gd2(
    payload: PipelineStartPayload,
    phase1: _Phase1,
    page: PreparedPage,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
) -> None:
    """Chạy GD2 và dọn trang mới khi không commit được — kể cả khi GD2 ném giữa chừng.

    Cờ `committed` đặt **sau** khi `session_scope` thoát (tức sau commit thật), nên commit
    hỏng cũng dẫn tới xoá trang; `finally` không bắt ngoại lệ nên lỗi vẫn nổi lên cho `define_task`.
    """
    committed = False
    try:
        async with session_scope(sessionmaker) as db:
            if not await _gd2(db, payload, phase1, page, clock):
                await db.rollback()
                return
        committed = True
        await after_commit_idle(db)
    finally:
        if not committed and page.created_key is not None:
            await storage.delete(page.created_key)


async def run_pipeline_start(
    payload: PipelineStartPayload,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
    settings: OrchestrateSettings | None = None,
) -> None:
    """Lõi của `pipeline.orchestrate.start`: GD1 → việc chậm → GD2 ([6] bước 1-4).

    `settings=None` → `get_orchestrate_settings()`; tham số này là lệch khỏi prompt có chủ
    đích để test hạ trần (J03, U06) mà không đụng biến môi trường của cả tiến trình.
    """
    limits = get_orchestrate_settings() if settings is None else settings
    async with session_scope(sessionmaker) as db:
        phase1 = await _gd1(db, payload, clock)
    await after_commit_idle(db)
    if phase1 is None:
        return
    page = await prepare_page(phase1.plan, storage=storage, settings=limits, clock=clock)
    await _commit_gd2(payload, phase1, page, sessionmaker=sessionmaker, storage=storage, clock=clock)


async def fail_pipeline_start_core(
    payload: PipelineStartPayload,
    code: str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: Clock,
) -> None:
    """`on_failed` của task: đánh hỏng bước `preprocess` nếu lượt còn đứng ở đúng bước đó.

    Lượt đã sang bước ML (J05) hay đã kết thúc/bị thay → không ghi gì: lỗi này thuộc về một
    lượt giao cũ, ghi `failed` sẽ giết một lượt đang chạy tốt.
    """
    async with session_scope(sessionmaker) as db:
        run = await lock_run(db, run_id=payload.run_id)
        if run is None or run.current_step != STEP:
            _ignored(payload.run_id, "not_at_preprocess")
            return
        await record_step(db, run_id=run.id, step=STEP, status="failed", clock=clock, error_code=code)
    await after_commit_idle(db)
