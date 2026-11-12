"""`GET /api/library` (#14) và `GET /api/library/{item_id}` (#15) — chỉ đọc (B2-06 [2], [7]).

Route bảo vệ, chỉ cần `current_principal`: khoá quyền `—` nên không gắn `require_permission`.
#14 phân trang theo mẫu #19 (`apps/api/notifications/router.py`): thân vẫn là mảng trần, trang sau qua header.
"""

from typing import Annotated, Final

from fastapi import Query, Response

from apps.api.core.deps import CurrentPrincipal, DbSession, Storage
from apps.api.core.pagination import decode_cursor, encode_cursor
from apps.api.core.routing import protected_router
from apps.api.library import service
from apps.api.library.schemas import LibraryItemOut
from apps.api.library.settings import get_library_settings

router = protected_router(tags=["library"])
ROUTERS: Final = (router,)

LIST_OP: Final = "library_list_items"
"""`op` ký vào cursor của #14; cursor ký kèm `user` nên không dùng được cho người khác."""
NEXT_CURSOR_HEADER: Final = "X-Next-Cursor"
REVISION_HEADER: Final = "X-Library-Revision"
PAGE_MAX: Final = 500
"""Trần `limit` của #14 (hợp đồng duyệt 12/11/2026)."""


@router.get(
    "/library",
    responses={
        200: {
            "headers": {
                NEXT_CURSOR_HEADER: {
                    "description": "Cursor cho trang sau; vắng khi đây là trang cuối.",
                    "schema": {"type": "string"},
                },
                REVISION_HEADER: {
                    "description": "Chuỗi mờ, đổi khi tập mục người gọi thấy đổi; hai trang khác dấu → đọc lại.",
                    "schema": {"type": "string"},
                },
            }
        }
    },
)
async def library_list_items(
    principal: CurrentPrincipal,
    db: DbSession,
    storage: Storage,
    response: Response,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=PAGE_MAX)] = None,
) -> list[LibraryItemOut]:
    """#14: mục người gọi thấy theo `(sort_order, id)`; không tham số → trọn tới `LIBRARY_LIST_MAX` như cũ.

    Còn mục sau trang → `X-Next-Cursor`. Mọi trang có `X-Library-Revision` (cùng snapshot với các mục của trang);
    hai trang khác dấu → client đọc lại từ đầu.
    """
    cap = get_library_settings().library_list_max
    size = min(limit or cap, cap)
    after = None
    if cursor is not None:
        pos = decode_cursor(cursor, LIST_OP, {"user": principal.user_id})
        after = (int(pos["order"]), str(pos["id"]))
    page = await service.list_page(db, storage, principal.user_id, size=size, after=after)
    response.headers[REVISION_HEADER] = page.revision
    if page.following is not None:
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor(
            LIST_OP, {"user": principal.user_id}, {"order": page.following[0], "id": page.following[1]}
        )
    return page.items


@router.get("/library/{item_id}")
async def library_read_item(
    item_id: str, principal: CurrentPrincipal, db: DbSession, storage: Storage
) -> LibraryItemOut:
    """#15: một mục; không thấy hay không hiển thị → 404 `libraryItem`."""
    return await service.read_item(db, storage, principal.user_id, item_id)
