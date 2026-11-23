"""Factory của bộ hồ sơ (K-32a): bộ, tệp của bộ, hình của bộ — dòng đã `flush`, không object nào trong kho."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow, DrawingSetViewRow
from packages.db.models.projects import Project


async def make_drawing_set(db: AsyncSession, *, project: Project, **overrides: Any) -> DrawingSetRow:
    """Một bộ `receiving` của `project` do chủ dự án tạo; `overrides` ghi đè cột bất kỳ (trạng thái, revision…)."""
    values: dict[str, Any] = {
        "id": new_id("dws", SystemClock()),
        "project_id": project.id,
        "status": "receiving",
        "created_by": project.created_by,
    }
    row = DrawingSetRow(**(values | overrides))
    db.add(row)
    await db.flush()
    return row


async def make_set_file(
    db: AsyncSession, *, drawing_set: DrawingSetRow, ord: int = 0, **overrides: Any
) -> DrawingSetFileRow:
    """Một tệp PNG 1 KiB, một khúc, `receiving`, ở vị trí `ord` của bộ."""
    values: dict[str, Any] = {
        "id": new_id("dwf", SystemClock()),
        "set_id": drawing_set.id,
        "ord": ord,
        "file_name": "ban-ve.png",
        "declared_size_bytes": 1024,
        "declared_type": "image/png",
        "chunk_count": 1,
        "status": "receiving",
    }
    row = DrawingSetFileRow(**(values | overrides))
    db.add(row)
    await db.flush()
    return row


async def make_set_view(
    db: AsyncSession, *, file: DrawingSetFileRow, ord: int = 0, **overrides: Any
) -> DrawingSetViewRow:
    """Một hình máy tách (`ai`) cả trang 0 của `file`, loại `other` độ tin 0.5, chưa có đích."""
    values: dict[str, Any] = {
        "id": new_id("dwv", SystemClock()),
        "set_id": file.set_id,
        "file_id": file.id,
        "ord": ord,
        "page_index": 0,
        "source": "ai",
        "kind": "other",
        "kind_confidence": 0.5,
    }
    row = DrawingSetViewRow(**(values | overrides))
    db.add(row)
    await db.flush()
    return row
