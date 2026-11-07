"""Chuyển trạng thái `model_exports` — nguồn duy nhất cho worker `bake`, lịch quét và N49.

"Hàm worker nhập" (BE-00 §7): SQL thuần trên session, không `apps.api.core`, không `fastapi`.
Không hàm nào `commit`: người gọi commit. Mỗi `UPDATE` lọc trạng thái nguồn, nên tin nhắn lặp,
chuyển muộn hay lượt đã `superseded` tự nhiên là no-op và trả `None`/`False`.
"""

import json
from collections.abc import Mapping
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.clock import Clock

MODEL_EXPORT_FAILED: Final = "MODEL_EXPORT_FAILED"
MODEL_EXPORT_TIMEOUT: Final = "MODEL_EXPORT_TIMEOUT"
MODEL_EXPORT_OUTPUT_INVALID: Final = "MODEL_EXPORT_OUTPUT_INVALID"
MODEL_EXPORT_STALLED: Final = "MODEL_EXPORT_STALLED"
MODEL_EXPORT_PACK_MISSING: Final = "MODEL_EXPORT_PACK_MISSING"
FAILURE_CODES: Final = frozenset(
    (
        MODEL_EXPORT_FAILED,
        MODEL_EXPORT_TIMEOUT,
        MODEL_EXPORT_OUTPUT_INVALID,
        MODEL_EXPORT_STALLED,
        MODEL_EXPORT_PACK_MISSING,
    )
)
QUALITIES: Final = frozenset(("ready", "degraded", "blocked"))

_CLAIM: Final = text(
    "UPDATE model_exports SET status = 'running', started_at = :now, updated_at = :now "
    "WHERE id = :id AND status IN ('queued', 'running') RETURNING project_id"
)
_FINISH: Final = text(
    "UPDATE model_exports SET status = 'done', quality = :quality, report_summary = CAST(:summary AS jsonb), "
    "glb_bytes = :glb, ended_at = :now, updated_at = :now WHERE id = :id AND status = 'running' RETURNING id"
)
_FAIL: Final = text(
    "UPDATE model_exports SET status = 'failed', failure_code = :code, ended_at = :now, updated_at = :now "
    "WHERE id = :id AND status IN ('queued', 'running') RETURNING id"
)
_SUPERSEDE: Final = text(
    "UPDATE model_exports SET status = 'superseded', ended_at = :now, updated_at = :now "
    "WHERE project_id = :p AND status IN ('queued', 'running') RETURNING id"
)


async def claim(db: AsyncSession, export_id: str, clock: Clock) -> str | None:
    """`queued|running` → `running`; trả `project_id`, hoặc `None` khi lượt không còn chạy được.

    Nhận cả `running` để Celery giao lại sau khi worker chết; mỗi lần nhận đặt lại `started_at`.
    """
    found = await db.execute(_CLAIM, {"id": export_id, "now": clock.now()})
    project_id: str | None = found.scalar_one_or_none()
    return project_id


async def finish(
    db: AsyncSession,
    export_id: str,
    clock: Clock,
    *,
    quality: str,
    report_summary: Mapping[str, object],
    glb_bytes: int | None,
) -> bool:
    """`running` → `done` kèm chất lượng; `False` khi lượt đã `superseded|failed` (người gọi xoá object)."""
    if quality not in QUALITIES:
        raise ValueError(f"chất lượng lạ: {quality!r}")
    params = {
        "id": export_id,
        "now": clock.now(),
        "quality": quality,
        "summary": json.dumps(report_summary, ensure_ascii=False),
        "glb": glb_bytes,
    }
    found = await db.execute(_FINISH, params)
    return found.scalar_one_or_none() is not None


async def fail(db: AsyncSession, export_id: str, code: str, clock: Clock) -> bool:
    """`queued|running` → `failed` với `code` ∈ `FAILURE_CODES`; `False` khi lượt đã kết thúc."""
    if code not in FAILURE_CODES:
        raise ValueError(f"mã hỏng lạ: {code!r}")
    found = await db.execute(_FAIL, {"id": export_id, "code": code, "now": clock.now()})
    return found.scalar_one_or_none() is not None


async def supersede(db: AsyncSession, project_id: str, clock: Clock) -> list[str]:
    """Mọi lượt `queued|running` của dự án → `superseded`; trả id các lượt bị thay."""
    found = await db.execute(_SUPERSEDE, {"p": project_id, "now": clock.now()})
    return list(found.scalars())
