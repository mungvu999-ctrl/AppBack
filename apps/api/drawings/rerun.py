"""N51 — chạy lại một lượt tải đã `complete` từ tệp gốc trong kho (K-06, phương án A2).

Không tải lại byte: `start_run` mở lượt chạy mới cho **cùng** lượt tải, giữ `original_key`,
`page_index`, `crop_corners` của nó, nên pipeline chạy lại đúng trang và vùng đã chọn. Chỉ
lượt tải mới nhất của tầng, và chỉ khi lượt chạy mới nhất của nó đã `failed`.

Ba chặng như #7 (`complete.py`): đọc dưới giao dịch request → `rollback` rồi hỏi kho (R-20,
không giữ kết nối Postgres khi gọi MinIO) → giao dịch cuối khoá `floors` → `uploads`, kiểm lại
mọi điều kiện rồi `start_run`. Hai lượt bấm đôi: lượt sau thấy lượt chạy `pending` → 409.

Không ghi nhật ký hoạt động: lượt tải đã có dòng `floor.upload_complete`; chạy lại là việc
máy của cùng lượt tải (`drawings_rerun_upload` không `logged`).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.errors import (
    PIPELINE_NOT_FAILED,
    PIPELINE_RUNNING,
    UPLOAD_NOT_COMPLETE,
    UPLOAD_SOURCE_GONE,
    UPLOAD_SUPERSEDED,
)
from apps.api.drawings.progress import progress_wire
from apps.api.drawings.runs import LIVE_RUN_STATUSES, start_run
from apps.api.drawings.uploads import load_upload
from apps.api.floors.lookup import get_floor
from packages.core.clock import Clock
from packages.core.error_codes import NOT_FOUND
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.storage.port import ObjectStorage


async def _newer_upload_exists(db: AsyncSession, *, floor_pk: int, upload_id: str) -> bool:
    """Tầng có lượt tải `complete` tạo sau lượt tải này (bất kể lượt chạy của nó ra sao)."""
    created = select(UploadRow.created_at).where(UploadRow.id == upload_id).scalar_subquery()
    stmt = (
        select(UploadRow.id)
        .where(UploadRow.floor_pk == floor_pk, UploadRow.status == "complete", UploadRow.created_at > created)
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def _latest_run_status(db: AsyncSession, upload_id: str) -> str | None:
    """`status` của lượt chạy mới nhất (`created_at`, rồi id ULID) của lượt tải; chưa có → `None`."""
    stmt = (
        select(PipelineRunRow.status)
        .where(PipelineRunRow.upload_id == upload_id)
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _lock_complete_upload(db: AsyncSession, upload_id: str) -> bool:
    """Khoá dòng `uploads` (sau `floors`, BE-00 §7); đúng khi dòng còn và vẫn `complete`."""
    stmt = select(UploadRow.status).where(UploadRow.id == upload_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none() == "complete"


async def rerun_upload(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, upload_id: str, clock: Clock
) -> dict[str, object]:
    """N51 — mở lượt chạy mới cho lượt tải hỏng; trả `Progress` `pending` của nó.

    404 `upload` (không thuộc dự án, tầng đã xoá) · 409 `UPLOAD_NOT_COMPLETE` · 409
    `UPLOAD_SOURCE_GONE` (kho mất `original.*`) · 409 `UPLOAD_SUPERSEDED` (tầng có bản mới hơn) ·
    409 `PIPELINE_RUNNING` (lượt mới nhất còn chạy) · 409 `PIPELINE_NOT_FAILED` (đã xong).
    """
    facts = await load_upload(db, project_id=project_id, upload_id=upload_id)
    if facts.status != "complete":
        raise UPLOAD_NOT_COMPLETE.error()
    key_stmt = select(UploadRow.original_key).where(UploadRow.id == upload_id)
    original_key = (await db.execute(key_stmt)).scalar_one_or_none()
    await db.rollback()
    if original_key is None or await storage.stat(original_key) is None:
        raise UPLOAD_SOURCE_GONE.error()

    floor = await get_floor(db, project_id=project_id, level_id=facts.level_id, for_update=True)
    if floor is None or not await _lock_complete_upload(db, upload_id):
        raise NOT_FOUND.error(resource="upload")
    if await _newer_upload_exists(db, floor_pk=floor.pk, upload_id=upload_id):
        raise UPLOAD_SUPERSEDED.error()
    status = await _latest_run_status(db, upload_id)
    if status in LIVE_RUN_STATUSES:
        raise PIPELINE_RUNNING.error()
    if status == "completed":
        raise PIPELINE_NOT_FAILED.error()
    await start_run(db, upload_id=upload_id, clock=clock)
    return await progress_wire(db, upload_id)
