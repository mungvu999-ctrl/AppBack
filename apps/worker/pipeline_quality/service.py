"""Lõi task `pipeline.quality.run` (B5-07 [6]): kiểm lớp đã ghi, ghi `quality.json`, đóng lượt.

Ba pha vì K36, cùng khuôn `pipeline_persist.service`: pha 1 (một session) khoá lượt, đọc lớp
và cài đặt dự án rồi **đóng**; pha 2 dựng báo cáo và `put` kho **ngoài** mọi session; pha 3
(session mới) khoá lại, `record_step(completed)`, commit. Một lượt bị thay ở pha 2 (giữa
`put` và khoá lại pha 3) chỉ còn `skipped` — báo cáo đã ghi đè vô hại, lượt mới sẽ ghi lại.

Nhánh J10 (lượt đã kết thúc khi tới `lock_run`, kể cả đã thay) soát sự kiện cuối trên stream
tiến độ: còn thiếu trạng thái cuối (ví dụ tiến trình trước chết ngay sau `record_step` mà
chưa phát) thì phát lại một lần; đã có thì không chạm Redis thêm. Redis treo/timeout coi như
"đã có" để không phát đôi `completed` (K18).
"""

import asyncio
import json
import logging
from decimal import Decimal
from typing import Final, Literal, cast

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.design_director.state import enqueue_auto
from apps.api.drawings.progress import LAST_STEP
from apps.api.drawings.runs import lock_run, publish_progress_after_commit, record_step
from apps.api.project_settings.read import read_settings
from apps.api.spatial_read.codec import DocumentCorruptError
from apps.api.spatial_read.documents import FloorDocument, load_document
from apps.worker.pipeline_build.build import BuiltLayer
from apps.worker.pipeline_orchestrate.pins import load_pins, record_quality_summary
from apps.worker.pipeline_persist.context import PersistContext, load_context
from apps.worker.pipeline_persist.errors import PIPELINE_RESULT_INVALID
from apps.worker.pipeline_persist.service import read_built
from apps.worker.pipeline_quality.report import (
    QUALITY_ARTIFACT,
    QUALITY_REPORT_MAX_BYTES,
    RunQualityReport,
    build_report,
)
from packages.core.clock import Clock
from packages.db.engine import session_scope
from packages.db.hooks import after_commit_idle
from packages.db.models.drawings import PipelineRunRow
from packages.messaging.payloads.pipeline import RunStepPayload
from packages.messaging.redis import streams_redis
from packages.messaging.streams import FIELD, upload_stream
from packages.messaging.tasks import PermanentError
from packages.storage.keys import run_artifact
from packages.storage.port import ObjectStorage

_log: Final = logging.getLogger(__name__)

STEP: Final = LAST_STEP
"""Tên bước `qualityCheck`; cùng một hằng với FE xem là bước cuối (BE-BIND §4)."""

QualityOutcome = Literal["completed", "skipped"]
"""Kết quả một lượt giao: đã đóng lượt, hay bỏ qua (lượt muộn, bị thay, chưa ghi lớp, giao lặp)."""

_XREVRANGE_TIMEOUT_S: Final = 1.0
"""Trần đọc sự kiện cuối (nhánh J10): dọn dẹp phụ, không phải đường chính, không đáng chờ lâu."""


async def _last_event_is_settled(upload_id: str) -> bool:
    """Sự kiện cuối của stream tiến độ đã là trạng thái cuối, hay không rõ (Redis treo/rỗng).

    Chỉ khi biết chắc sự kiện cuối **không** phải `completed` mới đáng phát lại; mọi trường hợp
    khác (đã `completed`, stream rỗng/đã hết hạn, hay không đọc được) đều để yên (K18).
    """
    try:
        client = streams_redis()
        entries = await asyncio.wait_for(
            client.xrevrange(upload_stream(upload_id), "+", "-", count=1), _XREVRANGE_TIMEOUT_S
        )
    except (TimeoutError, RedisError):
        return True
    if not entries:
        return True
    _, fields = entries[0]
    # `xrevrange` không bao giờ trả `fields=None` (R-11: không viết nhánh không tới được);
    # stub kiểu của redis-py chỉ rộng hơn giá trị thật.
    data: dict[str, object] = json.loads(cast("dict[str, str]", fields)[FIELD])
    return bool(data.get("status") == "completed")


async def _reconcile_stale_progress(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str) -> None:
    """Phát lại `Progress` một lần nếu sự kiện cuối chưa phản ánh trạng thái cuối (nhánh J10)."""
    if await _last_event_is_settled(upload_id):
        return
    async with session_scope(sessionmaker) as db:
        await publish_progress_after_commit(db, upload_id)
    await after_commit_idle(db)


async def _run_status(sessionmaker: async_sessionmaker[AsyncSession], run_id: str) -> str | None:
    """`status` hiện tại của lượt, đọc không khoá (K33 cấm ghi `pipeline_runs`, không cấm đọc)."""
    async with session_scope(sessionmaker) as db:
        stmt = select(PipelineRunRow.status).where(PipelineRunRow.id == run_id)
        return (await db.execute(stmt)).scalar_one_or_none()


_EarlyExit = Literal["missing", "closed", "before_persist"]
"""`missing`: không có lượt. `closed`: `lock_run` thấy lượt đã kết thúc/bị thay. `before_persist`:
chưa qua B5-06b."""


async def _phase_one(
    db: AsyncSession, run_id: str
) -> tuple[PersistContext, FloorDocument, int, Decimal] | tuple[_EarlyExit, PersistContext | None]:
    """Khoá lượt, đọc lớp đã ghi + cài đặt dự án; thoát sớm kèm lý do khi không còn gì để kiểm."""
    ctx = await load_context(db, run_id)
    if ctx is None:
        return "missing", None
    if await lock_run(db, run_id=run_id) is None:
        return "closed", ctx
    pins = await load_pins(db, run_id)
    if pins is None or pins.persisted_revision is None:
        _log.info("quality_before_persist", extra={"run_id": run_id})
        return "before_persist", ctx
    try:
        document = await load_document(db, ctx.floor_pk)
    except DocumentCorruptError as exc:
        raise PermanentError(PIPELINE_RESULT_INVALID) from exc
    if document is None:
        raise PermanentError(PIPELINE_RESULT_INVALID)
    threshold = (await read_settings(db, ctx.project_id)).confidence_threshold
    return ctx, document, pins.persisted_revision, threshold


async def _handle_early_exit(
    sessionmaker: async_sessionmaker[AsyncSession], outcome: _EarlyExit, ctx: PersistContext | None
) -> None:
    """J10 chỉ soát Redis cho lượt đã thật sự `completed`; mọi lý do đóng khác không chạm Redis ([6])."""
    if outcome != "closed" or ctx is None:
        return
    if await _run_status(sessionmaker, ctx.run_id) == "completed":
        await _reconcile_stale_progress(sessionmaker, ctx.upload_id)


def _critical_count(report: RunQualityReport) -> int:
    """Số lỗi toàn vẹn `critical` cộng số kiểm hình học `critical`."""
    return sum(1 for issue in report.issues if issue.severity == "critical") + sum(
        1 for check in report.checks if check.critical
    )


NOTICE_CODES: Final = frozenset({"OPENING_SIZE_IMPLAUSIBLE", "ROOM_AREA_TEXT_MISMATCH"})
"""Mã kiểm mức nhắc được đưa tới người dùng (K-15, K-16). Danh sách trắng, không phải "mọi kiểm `critical:false`": đo
replay 96 ca, `ROOMS_WITHOUT_DOOR` bản không chặn có ở 41 ca (cả `OPEN_ENVELOPE`, `MULTIPLE_PLANS` không chặn nữa), hiện
hết sẽ là nhắc chung chung ở nửa số tầng. Thêm mã (`ROTATED_PLAN`…) khi FE có câu riêng."""


def _notice_checks(report: RunQualityReport) -> list[dict[str, object]]:
    """Kiểm hình học mức nhắc (`critical:false`, trong `NOTICE_CODES`) kèm id cho `readiness.notices` (NO-488).

    Id lấy từ `detail.openingIds` (K-15) hoặc `detail.roomIds` (K-16); kiểm không có id → `entityIds: []`.
    Kiểm `critical` đã có đường `criticalChecks`, không lặp ở đây.
    """
    notices: list[dict[str, object]] = []
    for check in report.checks:
        if check.critical or check.code not in NOTICE_CODES:
            continue
        ids = check.detail.get("openingIds") or check.detail.get("roomIds") or ()
        entity_ids = [] if isinstance(ids, float) else list(ids)
        notices.append({"code": check.code, "entityIds": entity_ids})
    return notices


def _summary(report: RunQualityReport, built: BuiltLayer | None) -> dict[str, object]:
    """Tóm tắt lưu cột `quality_summary` (FIX-P0-09): đủ để tính readiness mà không cần `quality.json`.

    `criticalCount` đếm cả lỗi toàn vẹn lẫn kiểm hình học `critical` (BENCH3D-01): `hasCritical` true thì nó ≥ 1.
    BENCH3D-09: `criticalChecks` (mã kiểm hình học critical), `scaleBasis` và `scaleChecks` (khoá nghi vấn tỉ lệ)
    của `layer.json` cho `readiness.checks`, `scaleBasis` của N16; `layer.json` không đọc được → vắng hai khoá tỉ lệ.
    NO-488: `noticeChecks` (`[{code, entityIds}]` của kiểm `critical:false`) cho `readiness.notices`; vắng khi không có.
    """
    summary: dict[str, object] = {
        "hasCritical": report.has_critical,
        "criticalCount": _critical_count(report),
        "lowConfidence": dict(report.low_confidence),
        "checks": [check.code for check in report.checks],
        "criticalChecks": [check.code for check in report.checks if check.critical],
    }
    notices = _notice_checks(report)
    if notices:
        summary["noticeChecks"] = notices
    if built is not None and built.scale_basis is not None:
        summary["scaleBasis"] = built.scale_basis
        summary["scaleChecks"] = sorted(built.scale_checks)
    return summary


async def _built_or_none(storage: ObjectStorage, ctx: PersistContext) -> BuiltLayer | None:
    """`layer.json` của lượt (đã qua persist nên thường còn); vắng hay hỏng → `None` + log, không làm hỏng bước quality.

    Mất tệp thì N16 mất `scaleBasis` và `scaleUnresolved` quay lại: log `quality_layer_unreadable` để còn dấu vết.
    """
    try:
        return await read_built(storage, ctx)
    except PermanentError as exc:
        _log.warning("quality_layer_unreadable", extra={"run_id": ctx.run_id, "code": exc.code})
        return None


async def _enqueue_design_concept(
    sessionmaker: async_sessionmaker[AsyncSession], project_id: str, clock: Clock
) -> None:
    """AID-01: lượt đã `completed` → xếp lượt "AI tự chọn kiểu" cho dự án, giao dịch riêng sau commit.

    Mọi lỗi chỉ ghi log: concept là phần thêm, không được làm hỏng hay giao lại bước cuối của pipeline.
    """
    try:
        async with session_scope(sessionmaker) as db:
            await enqueue_auto(db, project_id, clock=clock)
        await after_commit_idle(db)
    except Exception:  # noqa: BLE001 — cố ý nuốt: lỗi concept không được chạm lượt pipeline
        _log.exception("design_concept_enqueue_failed", extra={"project_id": project_id})


async def run_quality(
    payload: RunStepPayload,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
) -> QualityOutcome:
    """Kiểm chất lượng một lượt theo B5-07 [6] bước 1 tới 4."""
    async with session_scope(sessionmaker) as db:
        read = await _phase_one(db, payload.run_id)
        await db.rollback()
    if isinstance(read[0], str):
        await _handle_early_exit(sessionmaker, *read)
        return "skipped"

    ctx, document, revision, threshold = read
    built = await _built_or_none(storage, ctx)
    report = build_report(
        run_id=payload.run_id,
        revision=revision,
        layer=document.layer,
        level_id=ctx.level_id,
        threshold=threshold,
        scale_basis=None if built is None else built.scale_basis,
        label_orientation=None if built is None else built.label_orientation,
        area_notes=() if built is None else built.area_notes,
        area_labels=() if built is None else built.area_labels,
        page_unavailable=built is not None and built.page_unavailable,
    )
    key = run_artifact(ctx.project_id, ctx.level_id, ctx.upload_id, payload.run_id, STEP, QUALITY_ARTIFACT)
    await storage.put(key, report.to_json_bytes(), content_type="application/json", max_bytes=QUALITY_REPORT_MAX_BYTES)

    completed = False
    async with session_scope(sessionmaker) as db:
        if await lock_run(db, run_id=payload.run_id) is None:
            await db.rollback()
        else:
            # Ghi trước `record_step` trong cùng giao dịch: lượt `completed` luôn có tóm tắt. Giao
            # lặp gặp cột đã có nên `record_quality_summary` trả `False` — ghi đúng một lần.
            await record_quality_summary(db, run_id=payload.run_id, summary=_summary(report, built))
            step_result = await record_step(db, run_id=payload.run_id, step=STEP, status="completed", clock=clock)
            completed = step_result is not None
            # `record_step` trả `None` sau khi đã tự `_abandon(FLOOR_DELETED)` khi tầng quá cửa sổ:
            # không `rollback` ở đây, để `session_scope` commit bản ghi hỏng đó thay vì xoá nó (R-05).
    await after_commit_idle(db)
    if not completed:
        return "skipped"
    await _enqueue_design_concept(sessionmaker, ctx.project_id, clock)
    if report.has_critical:
        _log.info(
            "quality_critical_issues",
            extra={
                "run_id": payload.run_id,
                "issue_count": _critical_count(report),
                "checks": [check.code for check in report.checks],
            },
        )
    return "completed"


async def fail_quality(
    payload: RunStepPayload,
    code: str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: Clock,
) -> None:
    """`on_failed`: đánh hỏng bước `qualityCheck` với mã đã cho (K33)."""
    async with session_scope(sessionmaker) as db:
        await record_step(db, run_id=payload.run_id, step=STEP, status="failed", clock=clock, error_code=code)
