"""Lịch nền của xuất GLB (khuôn BE-00 §7, như `apps/api/lighting_bake/jobs.py`): `sweep_model_exports`.

Chỉ nhập `packages.*` và `state`/`settings`. Một lượt quét, trong **một** giao dịch:

1. chốt `failed` `MODEL_EXPORT_STALLED` lượt `queued` > 2 giờ và `running` > 15 phút;
2. gửi lại task của lượt `queued` im > 10 phút, đẩy `updated_at` lên để lượt sau không gửi lặp;
3. dọn: mỗi dự án giữ object của 5 lượt `done` mới nhất (mọi chất lượng, gồm `blocked`); dòng cũ hơn
   giữ lại với `glb_bytes = NULL` và `report_summary.pruned = true`;
4. dọn object (`snapshot.json`) của lượt `superseded` đã quá trần `running` (15 phút): lượt bị thay khi còn
   `queued` không có worker nào dọn hộ. Đánh dấu `report_summary.pruned` như bước 3 để không dọn lặp.

Object của lượt bị chốt/dọn xoá sau commit.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.model_export.settings import get_model_export_settings
from apps.api.model_export.state import MODEL_EXPORT_STALLED
from packages.core.clock import Clock, SystemClock
from packages.db.engine import worker_sessionmaker
from packages.db.hooks import after_commit_idle, on_after_commit
from packages.messaging import periodic, send_task
from packages.messaging.payloads.model_export import MODEL_EXPORT_TASK, ModelExportPayload
from packages.storage.keys import model_export_prefix
from packages.storage.port import ObjectStorage

_log: Final = logging.getLogger(__name__)

SWEEP_TASK: Final = "default.model_export.sweep"
SWEEP_EVERY: Final = timedelta(minutes=5)
SWEEP_BATCH: Final = 100

_FAIL_STALLED: Final = text(
    "UPDATE model_exports SET status = 'failed', failure_code = :code, ended_at = :now, updated_at = :now "
    "WHERE (status = 'queued' AND created_at < :queued_cutoff) "
    "OR (status = 'running' AND COALESCE(started_at, updated_at) < :running_cutoff) RETURNING id, project_id"
)
_RESEND: Final = text(
    "SELECT id FROM model_exports WHERE status = 'queued' AND updated_at < :cutoff "
    "ORDER BY updated_at, id LIMIT :batch FOR UPDATE SKIP LOCKED"
)
_TOUCH: Final = text("UPDATE model_exports SET updated_at = :now WHERE id = ANY(:ids)")
_PRUNE: Final = text(
    "UPDATE model_exports SET glb_bytes = NULL, updated_at = :now, "
    "report_summary = COALESCE(report_summary, '{}'::jsonb) || '{\"pruned\": true}'::jsonb WHERE id IN ("
    "SELECT id FROM (SELECT id, report_summary, row_number() OVER "
    "(PARTITION BY project_id ORDER BY created_at DESC, id DESC) AS rn FROM model_exports WHERE status = 'done') t "
    "WHERE t.rn > :keep AND NOT COALESCE((t.report_summary ->> 'pruned')::boolean, false) LIMIT :batch) "
    "RETURNING id, project_id"
)
"""Đánh dấu đã dọn bằng `report_summary.pruned` (không bằng `glb_bytes`): bản `blocked` không có GLB
nhưng vẫn có `snapshot.json`/`report.json`/`CREDITS.txt` cần xoá theo cùng luật giữ 5 bản."""
_CLEAN_SUPERSEDED: Final = text(
    "UPDATE model_exports SET updated_at = :now, "
    "report_summary = COALESCE(report_summary, '{}'::jsonb) || '{\"pruned\": true}'::jsonb WHERE id IN ("
    "SELECT id FROM model_exports WHERE status = 'superseded' AND updated_at < :cutoff "
    "AND NOT COALESCE((report_summary ->> 'pruned')::boolean, false) LIMIT :batch) RETURNING id, project_id"
)
"""Chờ quá trần `running`: lượt `running` bị thay thì worker của nó đã xong hoặc bị giết, không còn đọc
`snapshot.json`; lượt đó tự dọn ở `finish → False`, nên ở đây thường chỉ còn object của lượt bị thay khi `queued`."""


@dataclass(frozen=True, slots=True)
class SweepResult:
    """Số lượt bị chốt `failed`, được gửi lại và bị dọn object trong một lượt quét."""

    failed: int
    resent: int
    pruned: int


def _send_later(db: AsyncSession, export_id: str) -> None:
    """Đăng ký gửi task xuất cho `export_id` sau khi giao dịch của `db` commit."""
    on_after_commit(db, lambda: send_task(MODEL_EXPORT_TASK, ModelExportPayload(export_id=export_id)))


async def run_model_export_sweep(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
    *,
    batch: int = SWEEP_BATCH,
) -> SweepResult:
    """Một lượt quét: chốt lượt kẹt, gửi lại lượt `queued` quá hạn, dọn bản `done` cũ và lượt `superseded` cũ.

    Object xoá sau commit.
    """
    settings = get_model_export_settings()
    now = clock.now()
    async with sessionmaker() as db:
        failed = list(
            (
                await db.execute(
                    _FAIL_STALLED,
                    {
                        "code": MODEL_EXPORT_STALLED,
                        "now": now,
                        "queued_cutoff": now - timedelta(seconds=settings.model_export_queued_max_age_s),
                        "running_cutoff": now - timedelta(seconds=settings.model_export_running_max_age_s),
                    },
                )
            ).tuples()
        )
        cutoff = now - timedelta(seconds=settings.model_export_resend_after_s)
        ids = list((await db.execute(_RESEND, {"cutoff": cutoff, "batch": batch})).scalars())
        if ids:
            await db.execute(_TOUCH, {"now": now, "ids": ids})
        for export_id in ids:
            _send_later(db, export_id)
        pruned = list(
            (await db.execute(_PRUNE, {"now": now, "keep": settings.model_export_keep_done, "batch": batch})).tuples()
        )
        running_cutoff = now - timedelta(seconds=settings.model_export_running_max_age_s)
        params = {"now": now, "cutoff": running_cutoff, "batch": batch}
        pruned += list((await db.execute(_CLEAN_SUPERSEDED, params)).tuples())
        await db.commit()
        await after_commit_idle(db)
    for export_id, project_id in failed:
        _log.warning("model_export_stalled", extra={"export_id": export_id})
        await storage.delete_prefix(model_export_prefix(project_id, export_id))
    for export_id, project_id in pruned:
        await storage.delete_prefix(model_export_prefix(project_id, export_id))
    return SweepResult(failed=len(failed), resent=len(ids), pruned=len(pruned))


@periodic(SWEEP_TASK, every=SWEEP_EVERY)
async def sweep_model_exports() -> None:
    """Hàm lịch: dựng tài nguyên thật rồi gọi lõi quét (nhập trễ `create_storage` như LB-01)."""
    from packages.core.settings import get_core_settings
    from packages.storage.factory import create_storage
    from packages.storage.settings import get_storage_settings

    clock = SystemClock()
    storage = create_storage(get_storage_settings(), get_core_settings(), clock)
    result = await run_model_export_sweep(worker_sessionmaker(), storage, clock)
    _log.info("model_export_sweep", extra={"failed": result.failed, "resent": result.resent, "pruned": result.pruned})
