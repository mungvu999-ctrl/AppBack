"""Đọc bộ hồ sơ ra dây: N57 (một bộ), N58 (danh sách), và bản `DrawingSet` mà mọi lệnh ghi trả về.

Một bộ ra dây bằng **ba** câu SQL cố định (bộ, tệp, hình + `level_id` của tầng đích) bất kể số tệp/hình — không N+1
(R-20). Ảnh trang ký cả lô trong một lượt `signed_urls` (NO-207), chỉ khi bộ đã qua phân tích (trang đã dựng).
Lượt phân tích treo quá `drawing_set_analysis_stall_s` được chốt `failed` ngay lúc đọc bằng **một** câu `UPDATE` có
điều kiện (không lịch quét riêng: chỉ người đang nhìn bộ mới cần biết nó đã hỏng).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final, get_args

from sqlalchemy import Row, func, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.pagination import CursorPage, decode_cursor, encode_cursor
from apps.api.drawing_sets.errors import ANALYSIS_FAILED, set_not_found
from apps.api.drawing_sets.schemas import FloorKind, QuestionCode, StoreyLabel
from apps.api.drawing_sets.set_schemas import (
    CropOut,
    DrawingSetFileOut,
    DrawingSetOut,
    DrawingSetSummaryOut,
    DrawingSetTitleBlockOut,
    DrawingSetViewOut,
    ViewQuestionOut,
    ViewTargetOut,
)
from apps.api.drawing_sets.settings import get_drawing_set_settings
from apps.api.drawings.drawings import drawing_urls
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow, DrawingSetViewRow
from packages.db.models.floors import FloorRow
from packages.domain.drawing_set.set_title_block import common_title_block, sheet_no_of
from packages.storage.keys import drawing_set_page
from packages.storage.port import ObjectStorage

LIST_OP: Final = "drawing_sets_list_sets"
"""`op` ký vào cursor N58: cursor của danh sách khác dùng ở đây → 422 `CURSOR_INVALID`."""
FULL_PAGE: Final = {"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0}
"""Khung "cả trang" — hình máy tách mặc định và giá trị khi `crop` vắng."""
_PAGED_STATUSES: Final = frozenset({"review", "confirmed"})
"""Trạng thái mà trang đã được dựng ra kho (ký được `imageUrl`)."""
_KINDS: Final = frozenset(get_args(FloorKind.__value__))
_LABELS: Final = frozenset(get_args(StoreyLabel.__value__))
_CODES: Final = frozenset(get_args(QuestionCode.__value__))


async def expire_stalled(db: AsyncSession, *, project_id: str) -> None:
    """Chốt `failed` `DRAWING_SET_ANALYSIS_FAILED` các bộ `analyzing` của dự án đã treo quá hạn (K-32a N57).

    So bằng `now()` của Postgres vì `analysis_started_at` cũng do server đặt cùng đồng hồ ấy ở N56.
    """
    window = timedelta(seconds=get_drawing_set_settings().drawing_set_analysis_stall_s)
    await db.execute(
        update(DrawingSetRow)
        .where(
            DrawingSetRow.project_id == project_id,
            DrawingSetRow.status == "analyzing",
            DrawingSetRow.deleted_at.is_(None),
            DrawingSetRow.analysis_started_at < func.now() - window,
        )
        .values(status="failed", error_code=ANALYSIS_FAILED)
    )


async def load_set(db: AsyncSession, *, project_id: str, set_id: str, for_update: bool = False) -> DrawingSetRow:
    """Bộ chưa xoá mềm của **đúng** dự án trên đường; khác → 404 `resource:"drawingSet"` (không 403, K08)."""
    stmt = select(DrawingSetRow).where(
        DrawingSetRow.id == set_id, DrawingSetRow.project_id == project_id, DrawingSetRow.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        raise set_not_found()
    return row


def _file_out(row: DrawingSetFileRow) -> DrawingSetFileOut:
    """Một tệp ra dây (W2: mã từ chối và số trang vắng khi chưa có)."""
    return DrawingSetFileOut.model_validate(
        {
            "id": row.id,
            "file_name": row.file_name,
            "size_bytes": row.declared_size_bytes,
            "chunk_count": row.chunk_count,
            "status": row.status,
            "rejected_code": row.rejected_code,
            "page_count": row.page_count,
        }
    )


def _unit(value: float | None) -> float | None:
    """Độ tin kẹp vào [0, 1] (W3)."""
    return None if value is None else min(1.0, max(0.0, value))


def _questions(machine: Any) -> list[ViewQuestionOut]:
    """Câu hỏi máy để lại trong `machine.questions`; mã ngoài bộ mã N52 bị bỏ (không 500 cả bộ)."""
    codes = machine.get("questions", []) if isinstance(machine, dict) else []
    return [ViewQuestionOut(code=code) for code in codes if code in _CODES]


def _target_out(view: DrawingSetViewRow, floor_level_id: str | None) -> ViewTargetOut | None:
    """Đích đã lưu ra dây; đích "tầng có sẵn" mà tầng đã bị xoá cứng (`target_floor_pk` NULL) → vắng."""
    if view.target_mode == "floor":
        if floor_level_id is None:
            return None
        return ViewTargetOut(mode="floor", floor_id=floor_level_id, replace=True if view.replace_drawing else None)
    if view.target_mode == "newFloor":
        return ViewTargetOut(mode="newFloor", floor_id=view.new_floor_id, name=view.new_floor_name)
    if view.target_mode == "section":
        return ViewTargetOut(mode="section", name=view.new_floor_name)
    return None


def _view_out(view: DrawingSetViewRow, floor_level_id: str | None, image_url: str | None) -> DrawingSetViewOut:
    """Một hình ra dây; loại/nhãn ngoài tập dây → `other`/vắng (mẫu N52)."""
    return DrawingSetViewOut.model_validate(
        {
            "id": view.id,
            "file_id": view.file_id,
            "page_index": view.page_index,
            "crop": CropOut.model_validate(view.crop or FULL_PAGE),
            "source": view.source,
            "excluded": view.excluded,
            "image_url": image_url,
            "kind": view.kind if view.kind in _KINDS else "other",
            "kind_confidence": _unit(view.kind_confidence),
            "title_text": view.title_text,
            "sheet_no": sheet_no_of(view.machine),
            "storey_label": view.storey_label if view.storey_label in _LABELS else None,
            "storey_no": view.storey_no,
            "storey_confidence": _unit(view.storey_confidence),
            "questions": _questions(view.machine),
            "target": _target_out(view, floor_level_id),
            "upload_id": view.upload_id,
        }
    )


def _title_block(views: Sequence[DrawingSetViewRow]) -> DrawingSetTitleBlockOut | None:
    """Khung tên chung của bộ từ các hình đang dùng (K-65); không trường nào chung thì cả khoá vắng."""
    common = common_title_block(((view.file_id, view.page_index), view.machine) for view in views if not view.excluded)
    return DrawingSetTitleBlockOut.model_validate(common) if common else None


async def _image_urls(
    storage: ObjectStorage, drawing_set: DrawingSetRow, views: Sequence[DrawingSetViewRow]
) -> list[str | None]:
    """URL ký ảnh trang của từng hình (cùng thứ tự), `None` cho cả bộ khi trang chưa dựng.

    Khoá trang do server đặt và tự mã hoá PNG (tác vụ phân tích), nên `drawing_urls` ký `inline` + `kind` png (K15).
    """
    if drawing_set.status not in _PAGED_STATUSES or not views:
        return [None] * len(views)
    keys = [drawing_set_page(drawing_set.project_id, drawing_set.id, view.file_id, view.page_index) for view in views]
    return list(await drawing_urls(storage, keys))


async def set_wire(db: AsyncSession, storage: ObjectStorage, drawing_set: DrawingSetRow) -> DrawingSetOut:
    """Bộ đầy đủ tệp + hình ra dây (hai câu SQL sau dòng bộ đã có trong tay)."""
    files = (
        (
            await db.execute(
                select(DrawingSetFileRow)
                .where(DrawingSetFileRow.set_id == drawing_set.id)
                .order_by(DrawingSetFileRow.ord)
            )
        )
        .scalars()
        .all()
    )
    view_rows = (
        await db.execute(
            select(DrawingSetViewRow, FloorRow.level_id)
            .outerjoin(FloorRow, FloorRow.pk == DrawingSetViewRow.target_floor_pk)
            .where(DrawingSetViewRow.set_id == drawing_set.id)
            .order_by(DrawingSetViewRow.ord)
        )
    ).all()
    views = [row[0] for row in view_rows]
    urls = await _image_urls(storage, drawing_set, views)
    return DrawingSetOut.model_validate(
        {
            "id": drawing_set.id,
            "status": drawing_set.status,
            "revision": drawing_set.revision,
            "progress_percent": drawing_set.progress_percent,
            "name": drawing_set.name,
            "error_code": drawing_set.error_code,
            "created_at": drawing_set.created_at,
            "confirmed_at": drawing_set.confirmed_at,
            "title_block": _title_block(views),
            "files": [_file_out(row) for row in files],
            "views": [_view_out(view, level_id, url) for (view, level_id), url in zip(view_rows, urls, strict=True)],
        }
    )


async def read_set(db: AsyncSession, storage: ObjectStorage, *, project_id: str, set_id: str) -> DrawingSetOut:
    """N57: chốt bộ treo của dự án rồi đọc một bộ ra dây."""
    await expire_stalled(db, project_id=project_id)
    return await set_wire(db, storage, await load_set(db, project_id=project_id, set_id=set_id))


def _summary_stmt(project_id: str) -> Any:
    """Bộ chưa xoá của dự án kèm số tệp, số hình (hai truy vấn con vô hướng, phủ bởi UNIQUE `(set_id, ord)`)."""
    file_count = (
        select(func.count()).where(DrawingSetFileRow.set_id == DrawingSetRow.id).scalar_subquery().label("file_count")
    )
    view_count = (
        select(func.count()).where(DrawingSetViewRow.set_id == DrawingSetRow.id).scalar_subquery().label("view_count")
    )
    return (
        select(DrawingSetRow, file_count, view_count)
        .where(DrawingSetRow.project_id == project_id, DrawingSetRow.deleted_at.is_(None))
        .order_by(DrawingSetRow.created_at.desc(), DrawingSetRow.id.desc())
    )


def _summary_out(row: Row[Any]) -> DrawingSetSummaryOut:
    """Một mục N58 ra dây."""
    drawing_set: DrawingSetRow = row[0]
    return DrawingSetSummaryOut.model_validate(
        {
            "id": drawing_set.id,
            "status": drawing_set.status,
            "name": drawing_set.name,
            "created_at": drawing_set.created_at,
            "confirmed_at": drawing_set.confirmed_at,
            "file_count": row.file_count,
            "view_count": row.view_count,
        }
    )


async def list_sets(db: AsyncSession, *, project_id: str, cursor: str | None) -> CursorPage[DrawingSetSummaryOut]:
    """N58: bộ mới trước, trần `drawing_set_list_limit`, cursor `(created_at, id)` ký HMAC (W22, C15).

    Đi index một phần `ix_drawing_sets_project_id_created_at_id`; lấy dư một dòng để biết còn trang sau.
    """
    await expire_stalled(db, project_id=project_id)
    limit = get_drawing_set_settings().drawing_set_list_limit
    stmt = _summary_stmt(project_id).limit(limit + 1)
    filters = {"project": project_id}
    if cursor is not None:
        after = decode_cursor(cursor, LIST_OP, filters)
        stmt = stmt.where(
            tuple_(DrawingSetRow.created_at, DrawingSetRow.id)
            < tuple_(literal(datetime.fromisoformat(after["at"])), literal(after["id"]))
        )
    rows = (await db.execute(stmt)).all()
    page = rows[:limit]
    last = page[-1][0] if page else None
    next_cursor = (
        encode_cursor(LIST_OP, filters, {"at": last.created_at.isoformat(), "id": last.id})
        if len(rows) > limit and last is not None
        else None
    )
    return CursorPage[DrawingSetSummaryOut](items=[_summary_out(row) for row in page], next_cursor=next_cursor)
