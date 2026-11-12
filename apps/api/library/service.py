"""Đọc thư viện (#14, #15): điều kiện hiển thị, thứ tự, URL ký cục bộ (B2-06 [6] "Đọc").

Mục hiển thị khi đã phát hành, chưa rút và (của hệ thống hoặc của chính người gọi); mục
`mine` của người khác vắng như không có (không lộ tồn tại). URL do `signed_url` tính cục bộ,
không `stat` (W23): CHECK `published` của bảng bảo đảm mục hiển thị luôn có số đo và khoá.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, cast

from sqlalchemy import ColumnElement, Select, Text, and_, func, literal, literal_column, or_, select, true, tuple_
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnClause

from apps.api.library.schemas import LibraryItemOut
from packages.core.error_codes import NOT_FOUND
from packages.db.models.library import LibraryItemRow
from packages.domain.library import FILE_CATALOGUE, is_item_id
from packages.storage.port import ObjectStorage, SignRequest

RESOURCE: Final = "libraryItem"
_ATTRIBUTION: Final = {item.id: item.attribution for item in FILE_CATALOGUE}
# Phân cách trường/dòng của dấu phiên bản: ký tự điều khiển US, RS — không có trong id/tên NFC của danh mục.
_FIELD_SEP: Final[ColumnClause[str]] = literal_column("chr(31)")
_ROW_SEP: Final[ColumnClause[str]] = literal_column("chr(30)")


def visible_to(user_id: str) -> ColumnElement[bool]:
    """Điều kiện hiển thị của một mục đối với `user_id` (dùng chung cho #14 và #15)."""
    return and_(
        LibraryItemRow.published_at.is_not(None),
        LibraryItemRow.retired_at.is_(None),
        or_(LibraryItemRow.owner_id.is_(None), LibraryItemRow.owner_id == user_id),
    )


async def _to_outs(storage: ObjectStorage, rows: Sequence[LibraryItemRow], user_id: str) -> list[LibraryItemOut]:
    """Dựng response từ các dòng đã phát hành; URL ký `attachment`, không `kind`, không `stat`.

    Mô hình và ảnh xem trước của mọi dòng ký trong **một** lượt `signed_urls` (NO-207).
    """
    requests: list[SignRequest] = []
    for row in rows:
        # CHECK `published`: mục hiển thị luôn có khoá mô hình
        requests.append(SignRequest(cast("str", row.model_key), "attachment", filename=f"{row.id}.glb"))
        if row.preview_key is not None:
            requests.append(SignRequest(row.preview_key, "attachment", filename=f"{row.id}.png"))
    signed = iter(await storage.signed_urls(requests))
    outs = []
    for row in rows:
        model = next(signed)
        preview = None if row.preview_key is None else next(signed)
        outs.append(
            LibraryItemOut.model_validate(
                {
                    "id": row.id,
                    "name": row.name,
                    "group": row.item_group,
                    "source": "mine" if row.owner_id == user_id else "catalogue",
                    "width_mm": row.width_mm,
                    "depth_mm": row.depth_mm,
                    "height_mm": row.height_mm,
                    "triangle_count": row.triangle_count,
                    "file_size_bytes": row.file_size_bytes,
                    "model_url": model.url,
                    "preview_url": None if preview is None else preview.url,
                    "attribution": _ATTRIBUTION.get(row.id),
                }
            )
        )
    return outs


type Position = tuple[int, str]
"""`(sort_order, id)` của dòng cuối trang trước — vị trí keyset của #14."""


@dataclass(frozen=True, slots=True)
class LibraryPage:
    """Một trang #14: mục đã ký URL, vị trí trang sau (nếu còn) và dấu phiên bản cùng snapshot với các mục."""

    items: list[LibraryItemOut]
    following: Position | None
    revision: str


def _revision_of(user_id: str) -> Select[tuple[str]]:
    """Dấu phiên bản của tập mục `user_id` thấy: md5 của `(id, tên, nhóm, sort_order)` mọi mục hiển thị.

    Một truy vấn gộp (~1.000 dòng, vài ms). Không dùng `updated_at`: lịch kiểm lại object chạm nó mỗi giờ nên
    client sẽ đọc lại vô cớ; ở đây chỉ thêm/rút/đổi tên/nhóm/thứ tự mới đổi dấu. Sha/khoá object không vào dấu
    (URL đổi không bắt client đọc lại danh sách).
    """
    row = LibraryItemRow
    entry = func.concat_ws(_FIELD_SEP, row.id, row.name, row.item_group, sql_cast(row.sort_order, Text))
    # `string_agg(x, sep ORDER BY …)`: SQLAlchemy dựng mệnh đề ORDER BY qua đối số phân cách.
    joined = func.string_agg(entry, aggregate_order_by(_ROW_SEP, row.sort_order, row.id))
    return select(func.md5(func.coalesce(joined, "")).label("revision")).where(visible_to(user_id))


async def list_page(
    db: AsyncSession, storage: ObjectStorage, user_id: str, *, size: int, after: Position | None = None
) -> LibraryPage:
    """#14: tối đa `size` mục hiển thị theo `(sort_order, id)` sau `after`, kèm vị trí trang sau và dấu phiên bản.

    Keyset, không OFFSET (chỉ mục `ix_library_items_sort_order_id_live`); đọc dư một dòng để biết còn trang sau;
    chỉ ký URL cho các dòng của trang. Dấu và các dòng đi trong **một** câu (subquery dấu LEFT JOIN trang) nên cùng
    snapshot, kể cả khi trang rỗng (một dòng, mục `None`): thay đổi chen giữa hai trang luôn ra hai dấu khác nhau.
    """
    rows = (
        select(LibraryItemRow)
        .where(visible_to(user_id))
        .order_by(LibraryItemRow.sort_order, LibraryItemRow.id)
        .limit(size + 1)
    )
    if after is not None:
        rows = rows.where(
            tuple_(LibraryItemRow.sort_order, LibraryItemRow.id) > tuple_(literal(after[0]), literal(after[1]))
        )
    page_rows = rows.subquery()
    item = aliased(LibraryItemRow, page_rows)
    rev = _revision_of(user_id).subquery()
    stmt = (
        select(rev.c.revision, item)
        .select_from(rev)
        .outerjoin(page_rows, true())
        .order_by(page_rows.c.sort_order, page_rows.c.id)
    )
    found = (await db.execute(stmt)).all()
    found_items = [row for _, row in found if row is not None]
    page = found_items[:size]
    following = (page[-1].sort_order, page[-1].id) if len(found_items) > size else None
    return LibraryPage(await _to_outs(storage, page, user_id), following, found[0][0])


async def read_item(db: AsyncSession, storage: ObjectStorage, user_id: str, item_id: str) -> LibraryItemOut:
    """#15: một mục hiển thị; id sai mẫu → 404 không truy vấn, không hiển thị → 404."""
    if not is_item_id(item_id):
        raise NOT_FOUND.error(resource=RESOURCE)
    row = (
        await db.execute(select(LibraryItemRow).where(LibraryItemRow.id == item_id, visible_to(user_id)))
    ).scalar_one_or_none()
    if row is None:
        raise NOT_FOUND.error(resource=RESOURCE)
    return (await _to_outs(storage, [row], user_id))[0]
