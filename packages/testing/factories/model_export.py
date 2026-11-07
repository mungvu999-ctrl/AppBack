"""Factory lượt xuất GLB cho test (M1-01), mẫu `packages/testing/factories/lighting_bake.py`.

Ghi thẳng `model_exports` (mọi trạng thái), có `commit` vì route/worker đọc bằng session khác.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.clock import SystemClock
from packages.core.errors import SYSTEM_PIPELINE
from packages.core.ids import new_id
from packages.db.models.model_export import ModelExportRow


async def make_model_export(
    db: AsyncSession,
    *,
    project_id: str,
    status: str = "queued",
    fingerprint: str | None = None,
    quality: str | None = None,
    report_summary: dict[str, Any] | None = None,
    glb_bytes: int | None = None,
    created_at: datetime | None = None,
    started_at: datetime | None = None,
) -> ModelExportRow:
    """Một dòng `model_exports` đã `commit`; `fingerprint` mặc định là duy nhất (theo id)."""
    clock = SystemClock()
    at = created_at if created_at is not None else clock.now()
    export_id = new_id("mex", clock)
    row = ModelExportRow(
        id=export_id,
        project_id=project_id,
        created_by=SYSTEM_PIPELINE,
        fingerprint=fingerprint if fingerprint is not None else f"fp-{export_id}",
        snapshot_sha="0" * 64,
        snapshot_revisions=[],
        compiler_version="m1-01.dev",
        pack_version="dev",
        status=status,
        quality=quality,
        report_summary=report_summary,
        glb_bytes=glb_bytes,
        started_at=started_at if started_at is not None or status == "queued" else at,
        ended_at=at if status in {"done", "failed", "superseded"} else None,
        created_at=at,
        updated_at=at,
    )
    db.add(row)
    await db.commit()
    return row
