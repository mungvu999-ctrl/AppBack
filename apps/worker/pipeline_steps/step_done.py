"""Lõi task `pipeline.orchestrate.step_done` (B5-06c [6]) và `queue_build` dùng chung với quét bù.

Một giao dịch ngắn: khoá lượt, kiểm kết quả dưới khoá (không tin `apps/ml`, B5-05), đẩy bước
theo thứ tự, xếp bước sau qua `on_after_commit` (J09). Mọi hàm trả `None` của B2-04/B5-06a
nghĩa là lượt đã kết thúc hay bị thay: rollback, log `pipeline_result_ignored`.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.drawings import current_drawing
from apps.api.drawings.runs import RunRow, lock_run, record_step
from apps.api.project_settings.read import read_settings
from apps.worker.pipeline_build.artifacts import input_key, layer_key
from apps.worker.pipeline_orchestrate.keys import run_prefix
from apps.worker.pipeline_orchestrate.pins import RunPins, load_pins, record_used, set_step_requeue
from apps.worker.pipeline_orchestrate.section import finish_section_run, is_section_run
from apps.worker.pipeline_steps.errors import MODEL_PIN_MISMATCH, PIPELINE_RESULT_INVALID
from packages.core.clock import Clock
from packages.core.pipeline import PIPELINE_STEPS
from packages.db.engine import session_scope
from packages.db.hooks import after_commit_idle, on_after_commit
from packages.db.models.drawings import DrawingRow
from packages.db.models.floors import FloorRow
from packages.messaging.celery_app import send_task
from packages.messaging.payloads.pipeline import BuildStepPayload, RunStepPayload
from packages.ml_contracts.families import FAMILY_STEP, ModelFamily
from packages.ml_contracts.payloads import ModelRef, StepResultPayload

STEP_DONE_TASK: Final = "pipeline.orchestrate.step_done"
BUILD_TASK: Final = "pipeline.build.run"
PERSIST_TASK: Final = "pipeline.persist.run"
QUALITY_TASK: Final = "pipeline.quality.run"
"""Tên task bước sau; tự khai ở đây vì [1] cấm nhập module của B5-06b, B5-07."""
BUILD_STEP: Final = "spatialDataBuild"

_log: Final = logging.getLogger(__name__)
_CODE_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
"""Mẫu `ErrorCode` của `packages.ml_contracts.payloads`, lặp lại để kiểm mã người gọi trong tiến trình."""

_STEP_ORDER: Final = tuple(step for step, _ in PIPELINE_STEPS)
_STEP_INDEX: Final = {step: index for index, step in enumerate(_STEP_ORDER)}
_STEP_FAMILY: Final = {step: family for family, step in FAMILY_STEP.items()}
ML_STEPS: Final = tuple(step for step in _STEP_ORDER if step in _STEP_FAMILY)
"""Ba bước ML **theo thứ tự `PIPELINE_STEPS`** — thứ tự đẩy bước, không phải thứ tự `MODEL_FAMILIES`."""


def _ignored(run_id: str, reason: str) -> None:
    """Log một kết quả bước bị bỏ (BE-00 §7): không phải lỗi, chỉ là kết quả tới muộn hay lặp."""
    _log.info("pipeline_result_ignored", extra={"run_id": run_id, "task": "step_done", "reason": reason})


@dataclass(frozen=True, slots=True)
class RunContext:
    """Dữ kiện để xếp lại một bước: bản vẽ hiện hành của tầng (thuộc đúng lượt tải) và tiền tố."""

    drawing: DrawingRow
    project_id: str
    level_id: str
    run_prefix: str


async def load_run_context(db: AsyncSession, run: RunRow) -> RunContext | None:
    """Bản vẽ hiện hành của tầng + tiền tố artifact; bản vẽ vắng hay của lượt tải khác → `None`."""
    drawing = await current_drawing(db, run.floor_pk)
    if drawing is None or drawing.upload_id != run.upload_id:
        return None
    stmt = select(FloorRow.project_id, FloorRow.level_id).where(FloorRow.pk == run.floor_pk)
    floor = (await db.execute(stmt)).one()
    prefix = run_prefix(project_id=floor.project_id, level_id=floor.level_id, upload_id=run.upload_id, run_id=run.id)
    return RunContext(drawing=drawing, project_id=floor.project_id, level_id=floor.level_id, run_prefix=prefix)


async def queue_build(db: AsyncSession, run: RunRow, *, clock: Clock) -> bool:
    """Đăng ký gửi `pipeline.build.run` sau commit; bản vẽ không khớp lượt → `failed` và `False`.

    Gọi trong giao dịch đang giữ `lock_run`, không commit. `run` chỉ cần `id`, `upload_id`,
    `floor_pk` (ảnh chụp trước khi đẩy bước vẫn dùng được).
    """
    ctx = await load_run_context(db, run)
    if ctx is None:
        await record_step(
            db, run_id=run.id, step=BUILD_STEP, status="failed", clock=clock, error_code=PIPELINE_RESULT_INVALID
        )
        return False
    settings = await read_settings(db, ctx.project_id)
    payload = BuildStepPayload(
        run_id=run.id,
        level_id=ctx.level_id,
        run_prefix=ctx.run_prefix,
        width_px=ctx.drawing.width_px,
        height_px=ctx.drawing.height_px,
        fallback_mm_per_px=settings.default_scale_mm_per_px,
        page_key=ctx.drawing.page_key,
        px_per_paper_mm=ctx.drawing.px_per_paper_mm,
        detect_furniture=settings.detect_furniture,
    )
    on_after_commit(db, lambda: send_task(BUILD_TASK, payload))
    return True


async def _live(db: AsyncSession, payload: StepResultPayload) -> tuple[RunRow, RunPins] | None:
    """Lượt còn nhận kết quả + dòng ghim đã khoá, hay `None` sau khi log lý do bỏ ([6] bước 1-2).

    Ba lý do bỏ đều **không** ghi gì: lượt đã kết thúc/bị thay, chưa có dòng ghim, và họ của
    bước đã có trong `used` (giao lặp J06 hay `failed` cũ tới sau kết quả thật).
    """
    run = await lock_run(db, run_id=payload.run_id)
    pins = await load_pins(db, payload.run_id, for_update=True) if run is not None else None
    if run is None or pins is None:
        _ignored(payload.run_id, "run_missing" if run is None else "pins_missing")
        return None
    family = _STEP_FAMILY.get(payload.step)
    if family is not None and family in pins.used:
        _ignored(run.id, f"family_used_{family}")
        return None
    return run, pins


async def _record_failed(db: AsyncSession, run_id: str, step: str, code: str, clock: Clock) -> None:
    """`record_step(step, failed, …)`; mã sai mẫu `ErrorCode` → `PIPELINE_RESULT_INVALID` ([6] bước 2).

    Kiểm mẫu ở đây là phòng thủ: `StepResultPayload.error_code` đã có `pattern`, nên mã xấu chỉ
    tới được từ người gọi trong tiến trình (lõi quét bù) hay payload dựng bằng `model_construct`.
    """
    checked = code if _CODE_RE.fullmatch(code) else PIPELINE_RESULT_INVALID
    await record_step(db, run_id=run_id, step=step, status="failed", clock=clock, error_code=checked)


def _used_value(family: ModelFamily, ref: ModelRef) -> str:
    """Giá trị ghi vào `used`: id bản ghim, hay tên ghim, hay `"classic"` (chỉ tường) / `"none"`."""
    default = "classic" if family == "wallSegmentation" else "none"
    return ref.version_id or ref.pinned_name or default


def _keys_ok(prefix: str, family: ModelFamily, keys: tuple[str, ...]) -> bool:
    """Đủ khoá JSON đầu vào của bước và không khoá nào nằm ngoài `f"{prefix}{step}/"` ([6] bước 3)."""
    folder = f"{prefix}{FAMILY_STEP[family]}/"
    return input_key(prefix, family) in keys and all(key.startswith(folder) for key in keys)


async def _ml_completed(
    db: AsyncSession, run: RunRow, payload: StepResultPayload, pins: RunPins, clock: Clock
) -> Mapping[ModelFamily, str] | None:
    """Kiểm kết quả một bước ML rồi `record_used`; `None` = đã ghi `failed` và phải dừng.

    Trả `used` **sau** lượt ghi này (ảnh chụp `pins.used` cộng họ vừa xong) để `_advance` không
    phải đọc lại dòng ghim trong cùng giao dịch.
    """
    family = cast("ModelFamily", payload.step)
    ref = pins.pinned[family]
    if payload.model_version_id is not None and payload.model_version_id != ref.version_id:
        await _record_failed(db, run.id, payload.step, MODEL_PIN_MISMATCH, clock)
        return None
    ctx = await load_run_context(db, run)
    if ctx is None or not _keys_ok(ctx.run_prefix, family, payload.artifact_keys):
        await _record_failed(db, run.id, payload.step, PIPELINE_RESULT_INVALID, clock)
        return None
    value = _used_value(family, ref)
    await record_used(db, run_id=run.id, family=family, used=value)
    return {**pins.used, family: value}


async def _advance(db: AsyncSession, run: RunRow, used: Mapping[ModelFamily, str], clock: Clock) -> bool:
    """Đẩy các bước ML liên tiếp đã có kết quả; trả `True` khi lượt vừa tới `spatialDataBuild`.

    Ba bước ML chạy song song nên kết quả về không theo thứ tự: chỉ đẩy từ `run.current_step`
    và dừng ở bước đầu chưa có kết quả (`record_step` tự bỏ bước nhỏ hơn `current_step`).
    """
    first = _STEP_INDEX[run.current_step]
    advanced = False
    reached_build = False
    for step in ML_STEPS:
        if _STEP_INDEX[step] < first:
            continue
        if _STEP_FAMILY[step] not in used:
            break
        if await record_step(db, run_id=run.id, step=step, status="completed", clock=clock) is None:
            break
        advanced = True
        reached_build = step == ML_STEPS[-1]
    if advanced:
        await set_step_requeue(db, run_id=run.id, count=0)
    return reached_build


async def _build_result(db: AsyncSession, run: RunRow, payload: StepResultPayload, pins: RunPins, clock: Clock) -> None:
    """Kết quả `spatialDataBuild` ([6] bước 4): `failed` ghi nguyên mã, `completed` xếp bước ghi.

    `completed` **không** `record_step`: B5-06b đóng bước này sau khi ghi tài liệu tầng xong.
    """
    if payload.status == "failed":
        await _record_failed(db, run.id, BUILD_STEP, payload.error_code or "", clock)
        return
    ctx = await load_run_context(db, run)
    expected = () if ctx is None else (layer_key(ctx.run_prefix),)
    if ctx is None or run.current_step != BUILD_STEP or payload.artifact_keys != expected:
        await _record_failed(db, run.id, BUILD_STEP, PIPELINE_RESULT_INVALID, clock)
        return
    if pins.persisted_revision is None:
        send = RunStepPayload(run_id=run.id)
        on_after_commit(db, lambda: send_task(PERSIST_TASK, send))


async def _step_done(db: AsyncSession, payload: StepResultPayload, clock: Clock) -> bool:
    """Thân giao dịch của `run_pipeline_step_done`; `False` = không ghi gì, người gọi rollback."""
    live = await _live(db, payload)
    if live is None:
        return False
    run, pins = live
    if payload.step == BUILD_STEP:
        await _build_result(db, run, payload, pins, clock)
        return True
    if payload.status == "failed":
        await _record_failed(db, run.id, payload.step, payload.error_code or "", clock)
        return True
    used = await _ml_completed(db, run, payload, pins, clock)
    if used is not None and await _advance(db, run, used, clock):
        if is_section_run(used):
            await finish_section_run(db, run_id=run.id, clock=clock)  # K-32c: mặt cắt không dựng, không ghi tầng
        else:
            await queue_build(db, run, clock=clock)
    return True


async def run_pipeline_step_done(
    payload: StepResultPayload, *, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    """Nhận kết quả một bước ML hay `spatialDataBuild`, đẩy bước, xếp bước sau ([6] "step_done").

    Một giao dịch duy nhất, ngắn (K36): mọi việc gửi đi qua `on_after_commit`, nên rollback ở
    đây (kết quả muộn) không bao giờ để lại task đã gửi (J09).
    """
    _log.info(
        "pipeline_step_timing",
        extra={
            "run_id": payload.run_id,
            "task": "step_done",
            "step": payload.step,
            "status": payload.status,
            "duration_ms": payload.duration_ms,
        },
    )
    async with session_scope(sessionmaker) as db:
        if not await _step_done(db, payload, clock):
            await db.rollback()
            return
    await after_commit_idle(db)


async def fail_pipeline_step_done_core(
    payload: StepResultPayload, code: str, *, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    """Thân `on_failed`: lượt còn sống và họ chưa có trong `used` → `record_step(step, failed, code)`.

    Giao dịch riêng của mình vì `on_failed` chạy sau khi lõi đã rollback: không mượn lại session
    nào, và không đẩy bước (kết quả bước này không có).
    """
    async with session_scope(sessionmaker) as db:
        live = await _live(db, payload)
        if live is None:
            await db.rollback()
            return
        await _record_failed(db, live[0].id, payload.step, code, clock)
    await after_commit_idle(db)
