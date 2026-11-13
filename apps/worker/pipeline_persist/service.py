"""Lõi task `pipeline.persist.run` (B5-06b [6]): ghi lớp AI của một lượt vào tài liệu tầng.

Ba pha vì K36: một session ngắn đọc dữ kiện rồi **đóng**, `open_read` gom `layer.json` ngoài
mọi session, rồi **một** giao dịch ghi tất cả dưới khoá của `lock_run`. Không tin `layer.json`
trước khi kiểm lượt dưới khoá: dữ kiện pha 1 chỉ để dựng khoá kho, pha 3 đọc lại tất cả.

**Thứ tự khoá** (BE-00 §7): `lock_run` khoá `floors … FOR UPDATE` **trước tiên** và giữ tới
commit, nên mọi người ghi cùng tầng (#35, N19, lượt lặp) nối đuôi sau nó. Sau đó chỉ khoá mới
`versions`, `pipeline_run_models`, `pipeline_runs`, `notifications` — đúng ngoại lệ mà §7 cho
phép người đang giữ `floors` dùng sau `touch_project` (mọi người ghi khác chỉ chạm các dòng ấy
khi đã giữ `floors`), nên không có chu trình khoá với `write_layer`/`create_version`.

Giao lặp (J06, J10) rẽ theo `persisted_revision`: không phiên bản, không ghi lớp, không
`record_step` lần hai (K18, K32) — chỉ làm lại các tác dụng idempotent rồi trả `replayed`.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.drawings import current_drawing
from apps.api.drawings.errors import FLOOR_DELETED
from apps.api.drawings.runs import lock_run, publish_progress_after_commit, record_step, restore_window_elapsed
from apps.api.notifications.messages import ai_completed
from apps.api.notifications.service import notify
from apps.api.projects.memberships import is_member
from apps.api.spatial_write.changes import human_removals
from apps.api.spatial_write.errors import (
    LAYER_INTEGRITY_BROKEN,
    LAYER_LEVEL_MISMATCH,
    REVIEW_BY_AI_FORBIDDEN,
)
from apps.api.spatial_write.writer import LayerWrite, WriteResult, write_layer
from apps.api.versions.messages import before_pipeline_note
from apps.api.versions.snapshots import create_version
from apps.worker.pipeline_build.build import BuiltLayer
from apps.worker.pipeline_build.errors import PIPELINE_ARTIFACT_INVALID, PIPELINE_ARTIFACT_MISSING
from apps.worker.pipeline_build.settings import get_pipeline_build_settings
from apps.worker.pipeline_orchestrate.pins import load_pins, mark_persisted
from apps.worker.pipeline_persist.constants import LAYER_ARTIFACT, QUALITY_TASK, STEP, SYSTEM_PIPELINE_NAME
from apps.worker.pipeline_persist.context import PersistContext, load_context
from apps.worker.pipeline_persist.errors import PIPELINE_RESULT_INVALID
from apps.worker.pipeline_persist.merge import make_merge, with_floor_height
from packages.core.clock import Clock
from packages.core.error_codes import VALIDATION
from packages.core.errors import SYSTEM_PIPELINE, AppError
from packages.db.engine import session_scope
from packages.db.hooks import after_commit_idle, on_after_commit
from packages.domain.rules_ai.merge import MergeResult, Removed
from packages.domain.spatial.model import SpatialLayer
from packages.messaging.celery_app import send_task
from packages.messaging.payloads.pipeline import RunStepPayload
from packages.messaging.tasks import PermanentError
from packages.storage.keys import run_artifact
from packages.storage.port import ObjectStorage, read_all_capped

_log: Final = logging.getLogger(__name__)

PersistOutcome = Literal["persisted", "replayed", "skipped"]
"""Kết quả một lượt giao: đã ghi, đã ghi từ trước (giao lặp), hay bỏ qua (lượt muộn/tầng gỡ)."""

_TxOutcome = Literal["persisted", "replayed", "skipped", "deferred"]
"""Như trên, thêm `deferred` = chưa ghi gì nên người gọi rollback rồi coi là `skipped`."""

_LAYER_CODES: Final = (LAYER_INTEGRITY_BROKEN, LAYER_LEVEL_MISMATCH, REVIEW_BY_AI_FORBIDDEN)
"""Mã của `write_layer` lan nguyên vào `Progress.error` ([2]); mã khác thành `PIPELINE_RESULT_INVALID`."""


def _ignored(run_id: str, reason: str) -> None:
    """Log một lượt giao bị bỏ (BE-00 §7): không phải lỗi, chỉ là kết quả tới muộn."""
    _log.info("pipeline_result_ignored", extra={"run_id": run_id, "task": "persist", "reason": reason})


async def read_built(storage: ObjectStorage, ctx: PersistContext) -> BuiltLayer:
    """Gom `layer.json` của lượt tới trần rồi giải; chạy **ngoài** mọi session (K36).

    Khoá vắng → `PIPELINE_ARTIFACT_MISSING`; vượt trần hay JSON sai hợp đồng B5-05 →
    `PIPELINE_ARTIFACT_INVALID`. `DEPENDENCY_UNAVAILABLE` lan ra cho B0-05 thử lại.
    """
    key = run_artifact(ctx.project_id, ctx.level_id, ctx.upload_id, ctx.run_id, STEP, LAYER_ARTIFACT)
    max_bytes = get_pipeline_build_settings().PIPELINE_ARTIFACT_MAX_BYTES
    data = await read_all_capped(
        storage,
        key,
        max_bytes=max_bytes,
        too_large=lambda: PermanentError(PIPELINE_ARTIFACT_INVALID),
        on_missing=lambda: PermanentError(PIPELINE_ARTIFACT_MISSING),
    )
    try:
        return BuiltLayer.from_json(data)
    except ValueError as exc:
        raise PermanentError(PIPELINE_ARTIFACT_INVALID) from exc


def _floor_state(ctx: PersistContext, clock: Clock) -> Literal["live", "deferred", "gone"]:
    """Tầng còn ghi được, còn trong cửa sổ khôi phục, hay đã mất hẳn (BE-00 §7).

    Dự án xoá không có cửa sổ: tầng theo dự án nên `gone` ngay. Luật cửa sổ là
    `restore_window_elapsed` của B2-04, dùng chung với `runs.record_step`.
    """
    if ctx.project_deleted:
        return "gone"
    deleted_at: datetime | None = ctx.floor_deleted_at
    if deleted_at is None:
        return "live"
    return "gone" if restore_window_elapsed(deleted_at, clock) else "deferred"


async def _write_ai_layer(
    db: AsyncSession, ctx: PersistContext, built: BuiltLayer, clock: Clock, removed: Removed
) -> WriteResult:
    """Bước 5: trộn lớp AI vào tài liệu tầng qua `write_layer(merge=…)`, ánh xạ lỗi ([6] "Lỗi").

    Chỉ `write_layer` được bọc — `make_merge` chạy bên trong nó, nên `RescaleError` và
    `ValueError` của trộn cũng rơi vào đây, còn `ValueError` của `notify`/`record_step`/
    `mark_persisted`/`create_version` vẫn lan nguyên (lỗi lập trình → `INTERNAL` kèm stack).
    `base_revision=0` hợp lệ vì có `merge`: `write_layer` bỏ qua nó và tự đọc revision dưới
    khoá, nên không có vòng 409. Tầng chưa có bản vẽ → `page_key=None` (tỉ lệ không gắn trang).
    Tường AI cao theo `floors.height_mm` đọc dưới khoá (`with_floor_height`). `removed` là bia
    mộ người đã xoá: mục AI trùng bia mộ bị chặn và đếm vào log `pipeline_suppressed_by_removal`.
    """
    drawing = await current_drawing(db, ctx.floor_pk)
    merge = make_merge(with_floor_height(built.layer, ctx.floor_height_mm), built.scale_mm_per_px, removed)

    def merge_and_log(base: SpatialLayer, target: Decimal | None) -> MergeResult:
        """Trộn như `make_merge`, rồi log số mục AI bị chặn vì người đã xoá (FIX-P0-07)."""
        outcome = merge(base, target)
        if outcome.suppressed_ids:
            _log.info(
                "pipeline_suppressed_by_removal",
                extra={"run_id": ctx.run_id, "count": len(outcome.suppressed_ids)},
            )
        return outcome

    try:
        return await write_layer(
            db,
            floor_pk=ctx.floor_pk,
            base_revision=0,
            body=LayerWrite(None, built.scale_mm_per_px, built.dimensions),
            actor_id=SYSTEM_PIPELINE,
            actor_name=SYSTEM_PIPELINE_NAME,
            clock=clock,
            merge=merge_and_log,
            scale_source=built.persisted_scale_source,
            page_key=None if drawing is None else drawing.page_key,
        )
    except AppError as exc:
        if exc.code in _LAYER_CODES:
            raise PermanentError(exc.code.code) from exc
        if exc.code is VALIDATION:
            raise PermanentError(PIPELINE_RESULT_INVALID) from exc
        raise
    except ValueError as exc:
        raise PermanentError(PIPELINE_RESULT_INVALID) from exc


def _queue_quality(db: AsyncSession, run_id: str) -> None:
    """Xếp `pipeline.quality.run` **sau** commit (K17); B5-07 chịu được giao lặp."""
    payload = RunStepPayload(schema_version=1, run_id=run_id)

    def send() -> None:
        """Callback sau commit; chạy ngoài vòng sự kiện nên không đụng session."""
        send_task(QUALITY_TASK, payload)

    on_after_commit(db, send)


async def _settle(db: AsyncSession, ctx: PersistContext, clock: Clock, *, replay: bool) -> None:
    """Bước 9 và 10, cũng là toàn bộ nhánh phát lại: chỉ những tác dụng chịu được giao lặp.

    `notify` khử trùng bằng `dedupe_key` nên lượt hai không sinh dòng hay mục `user_stream`
    thứ hai (K32), mà vẫn đăng ký phát nếu lượt trước chết trước khi phát (J10).
    Người tải đã bị gỡ khỏi dự án → không thông báo ai cả ([7]).

    `publish_progress_after_commit` **chỉ** ở nhánh phát lại ([6]): nhánh ghi đã có
    `record_step` tự hẹn phát khung `Progress` sau commit (`runs._settle`), gọi thêm ở đây
    là phát đôi cùng một trạng thái.
    """
    if await is_member(db, ctx.project_id, ctx.uploader_id):
        await notify(
            db,
            user_id=ctx.uploader_id,
            kind="aiCompleted",
            place="walls",
            project_id=ctx.project_id,
            project_name=ctx.project_name,
            object_label=ctx.floor_name,
            message=ai_completed(ctx.project_name),
            dedupe_key=f"aiCompleted:{ctx.run_id}:{ctx.uploader_id}",
            clock=clock,
            floor_level_id=ctx.level_id,
        )
    if replay:
        await publish_progress_after_commit(db, ctx.upload_id)
    _queue_quality(db, ctx.run_id)


async def _persist(db: AsyncSession, ctx: PersistContext, built: BuiltLayer, clock: Clock) -> None:
    """Bước 4 tới 8: phiên bản "trước", ghi lớp, phiên bản "sau", ghim revision, đẩy bước.

    Bia mộ người xoá đọc trước khi ghi gì, vẫn dưới khoá tầng của `lock_run` nên không người
    ghi nào chen giữa lượt đọc và lượt trộn (FIX-P0-07).
    """
    removed = await human_removals(db, floor_pk=ctx.floor_pk)
    await create_version(
        db,
        floor_pk=ctx.floor_pk,
        actor_id=SYSTEM_PIPELINE,
        actor_name=SYSTEM_PIPELINE_NAME,
        note=before_pipeline_note(),
        clock=clock,
    )
    result = await _write_ai_layer(db, ctx, built, clock, removed)
    await create_version(
        db,
        floor_pk=ctx.floor_pk,
        actor_id=SYSTEM_PIPELINE,
        actor_name=SYSTEM_PIPELINE_NAME,
        note=None,
        clock=clock,
    )
    if not await mark_persisted(db, run_id=ctx.run_id, revision=result.revision):
        raise RuntimeError(
            f"không ghi được persisted_revision của lượt {ctx.run_id}: đã có, hoặc lượt không có dòng "
            "pipeline_run_models, tuy đang giữ khoá tầng"
        )
    await record_step(db, run_id=ctx.run_id, step=STEP, status="completed", clock=clock)


async def _run_transaction(db: AsyncSession, run_id: str, built: BuiltLayer, clock: Clock) -> _TxOutcome:
    """Giao dịch duy nhất của [6] bước 3: kiểm lại mọi điều kiện dưới khoá rồi ghi."""
    if await lock_run(db, run_id=run_id) is None:
        _ignored(run_id, "run_missing_or_superseded")
        return "deferred"
    ctx = await load_context(db, run_id)
    if ctx is None:
        _ignored(run_id, "context_missing")
        return "deferred"
    state = _floor_state(ctx, clock)
    if state == "deferred":
        _log.info("persist_deferred_floor_deleted", extra={"run_id": run_id, "floor_pk": ctx.floor_pk})
        return "deferred"
    if state == "gone":
        await record_step(db, run_id=run_id, step=STEP, status="failed", clock=clock, error_code=FLOOR_DELETED)
        return "skipped"
    pins = await load_pins(db, run_id)
    replay = pins is not None and pins.persisted_revision is not None
    if not replay:
        await _persist(db, ctx, built, clock)
    await _settle(db, ctx, clock, replay=replay)
    return "replayed" if replay else "persisted"


async def run_persist(
    payload: RunStepPayload,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
) -> PersistOutcome:
    """Ghi kết quả pipeline của một lượt chạy; trả `persisted`, `replayed` hay `skipped`.

    Lỗi của kho và của lớp thành `PermanentError` (B0-05 gọi `fail_step` ghi `Progress.failed`);
    `DEPENDENCY_UNAVAILABLE` lan ra để B0-05 thử lại. Mọi lỗi rollback cả phiên bản "trước"
    nên không có thông báo hay task lạc (J09).
    """
    async with session_scope(sessionmaker) as first:
        ctx = await load_context(first, payload.run_id)
    if ctx is None:
        _ignored(payload.run_id, "run_missing")
        return "skipped"
    built = await read_built(storage, ctx)
    async with session_scope(sessionmaker) as db:
        outcome = await _run_transaction(db, payload.run_id, built, clock)
        if outcome == "deferred":
            await db.rollback()
            return "skipped"
    await after_commit_idle(db)
    return outcome


async def fail_step(
    payload: RunStepPayload,
    code: str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: Clock,
) -> None:
    """`on_failed` của task: đánh hỏng bước `spatialDataBuild` với mã đã cho (K33).

    Lượt không còn (kết thúc, bị thay, tầng quá cửa sổ) → `record_step` trả `None` và không
    ghi gì; đó là kết quả tới muộn chứ không phải lỗi, nên hàm vẫn commit rỗng rồi trả về.
    """
    async with session_scope(sessionmaker) as db:
        await record_step(db, run_id=payload.run_id, step=STEP, status="failed", clock=clock, error_code=code)
    await after_commit_idle(db)
