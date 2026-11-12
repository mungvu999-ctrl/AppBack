"""#14 `library_list_items` có `limit`/`cursor` (M15, hợp đồng duyệt 12/11/2026): C15 trang, cursor, dấu phiên bản."""

import base64
import json
from typing import Final

import httpx
import pytest
from sqlalchemy import event, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.library import service
from apps.api.library.router import NEXT_CURSOR_HEADER, PAGE_MAX, REVISION_HEADER
from apps.api.library.settings import LibrarySettings
from apps.api.library.tests._helpers import LIST_PATH, headers_of
from packages.db.models.auth import User
from packages.db.models.library import LibraryItemRow
from packages.db.seeds.library import sync_catalogue
from packages.domain.library import CatalogueItem
from packages.storage.keys import library_object
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.library import make_library_item

SYNTHETIC: Final = LibrarySettings().library_list_max + 150
"""Danh mục tổng hợp vượt trần danh sách cũ: phần sau trần chỉ tới được bằng cursor."""


async def _page(client: httpx.AsyncClient, user: User, **params: str | int) -> httpx.Response:
    """#14 với `limit`/`cursor`."""
    return await client.get(LIST_PATH, headers=headers_of(user), params=params)


async def _read_all(client: httpx.AsyncClient, user: User, limit: int) -> tuple[list[str], set[str], int]:
    """Đi theo `X-Next-Cursor` tới hết: `(id theo thứ tự, các dấu đã thấy, số trang)`."""
    ids: list[str] = []
    revisions: set[str] = set()
    pages = 0
    cursor: str | None = None
    while True:
        response = await _page(client, user, limit=limit, **({"cursor": cursor} if cursor else {}))
        assert response.status_code == 200
        ids += [item["id"] for item in response.json()]
        revisions.add(response.headers[REVISION_HEADER])
        pages += 1
        cursor = response.headers.get(NEXT_CURSOR_HEADER)
        if cursor is None:
            return ids, revisions, pages


async def _seed_three(db: AsyncSession, storage: LocalDiskStorage) -> None:
    """Ba mục hệ thống `item-a…c` (thứ tự 1, 1, 2: hoà `sort_order` phân xử bằng `id`)."""
    for item_id, order in (("item-b", 1), ("item-c", 2), ("item-a", 1)):
        await make_library_item(db, storage, id=item_id, sort_order=order)


async def _retire(db: AsyncSession, item_id: str) -> None:
    """Rút một mục như seed làm (`retired_at`, `updated_at` = now)."""
    await db.execute(
        update(LibraryItemRow).where(LibraryItemRow.id == item_id).values(retired_at=func.now(), updated_at=func.now())
    )
    await db.commit()


async def test_library_list_items__C15_pages(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """5 mục, `limit=2` → 2 + 2 + 1 theo `(sort_order, id)`; trang cuối không `X-Next-Cursor`; một dấu cho mọi trang."""
    user = await make_user(db_session)
    await _seed_three(db_session, local_storage)
    for item_id, order in (("item-e", 9), ("item-d", 3)):
        await make_library_item(db_session, local_storage, id=item_id, sort_order=order)
    whole = await api_client.get(LIST_PATH, headers=headers_of(user))
    assert NEXT_CURSOR_HEADER not in whole.headers

    ids, revisions, pages = await _read_all(api_client, user, limit=2)
    assert ids == [item["id"] for item in whole.json()] == ["item-a", "item-b", "item-c", "item-d", "item-e"]
    assert (pages, revisions) == (3, {whole.headers[REVISION_HEADER]})
    assert await _read_all(api_client, user, limit=2) == (ids, revisions, pages)


async def test_library_list_items__C15_empty_has_revision(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Rỗng: `[]`, có `X-Library-Revision`, không `X-Next-Cursor` — có hay không có `limit`."""
    user = await make_user(db_session)
    for params in ({}, {"limit": 1}):
        response = await _page(api_client, user, **params)
        assert response.json() == []
        assert response.headers[REVISION_HEADER]
        assert NEXT_CURSOR_HEADER not in response.headers


async def test_library_list_items__C15_past_old_cap(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Danh mục tổng hợp vượt trần 2000 (tiêm qua `sync_catalogue`): đi theo cursor đọc đủ, đúng thứ tự, không trùng.

    Không tham số → đúng trần như cũ, cộng `X-Next-Cursor`; cursor đó (không `limit`) đưa tới phần còn lại.
    """
    user = await make_user(db_session)
    items = [CatalogueItem(f"syn-{n:05d}", f"Mẫu {n}", "decor", ()) for n in range(SYNTHETIC)]
    await sync_catalogue(db_session, items)
    await db_session.execute(
        update(LibraryItemRow).values(
            width_mm=100,
            depth_mm=100,
            height_mm=100,
            triangle_count=12,
            file_size_bytes=1000,
            model_key=library_object("syn-00000", "model.glb"),
            model_sha256="0" * 64,
            published_at=func.now(),
        )
    )
    await db_session.commit()
    expected = [item.id for item in items]

    ids, revisions, pages = await _read_all(api_client, user, limit=PAGE_MAX)
    assert ids == expected
    assert (len(revisions), pages) == (1, -(-SYNTHETIC // PAGE_MAX))

    whole = await api_client.get(LIST_PATH, headers=headers_of(user))
    capped = [item["id"] for item in whole.json()]
    assert capped == expected[: LibrarySettings().library_list_max]
    rest = await _page(api_client, user, cursor=whole.headers[NEXT_CURSOR_HEADER])
    assert capped + [item["id"] for item in rest.json()] == expected
    assert NEXT_CURSOR_HEADER not in rest.headers


async def test_library_list_items__C15_revision_tracks_visible_set(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Thêm mục giữa hai trang → dấu khác; rút mục → khác nữa; kiểm lại object (`verified_at`) → không đổi."""
    user = await make_user(db_session)
    await _seed_three(db_session, local_storage)
    first = await _page(api_client, user, limit=2)
    start = first.headers[REVISION_HEADER]

    await db_session.execute(update(LibraryItemRow).values(verified_at=func.now(), updated_at=func.now()))
    await db_session.commit()
    assert (await _page(api_client, user, limit=2)).headers[REVISION_HEADER] == start

    await make_library_item(db_session, local_storage, id="item-z", sort_order=99)
    added = await _page(api_client, user, limit=2, cursor=first.headers[NEXT_CURSOR_HEADER])
    assert [item["id"] for item in added.json()] == ["item-c", "item-z"]
    assert added.headers[REVISION_HEADER] != start

    await _retire(db_session, "item-z")
    retired = (await _page(api_client, user, limit=2)).headers[REVISION_HEADER]
    assert retired != added.headers[REVISION_HEADER]
    assert retired == start  # tập mục quay về đúng ba mục ban đầu → cùng dấu (dấu theo nội dung, không theo giờ)

    await db_session.execute(update(LibraryItemRow).where(LibraryItemRow.id == "item-a").values(name="Tên mới"))
    await db_session.commit()
    assert (await _page(api_client, user, limit=2)).headers[REVISION_HEADER] != start


async def test_library_list_items__C15_retired_between_pages(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Rút mục của trang sau giữa hai trang: trang sau vắng nó và mang dấu khác; trang rỗng vẫn có dấu."""
    user = await make_user(db_session)
    await _seed_three(db_session, local_storage)
    first = await _page(api_client, user, limit=2)
    assert [item["id"] for item in first.json()] == ["item-a", "item-b"]

    await _retire(db_session, "item-c")
    second = await _page(api_client, user, limit=2, cursor=first.headers[NEXT_CURSOR_HEADER])
    assert second.status_code == 200
    assert second.json() == []
    assert second.headers[REVISION_HEADER] != first.headers[REVISION_HEADER]
    assert NEXT_CURSOR_HEADER not in second.headers
    assert second.headers[REVISION_HEADER] == (await _page(api_client, user, limit=2)).headers[REVISION_HEADER]


async def test_list_page_empty_page_one_statement(db_session: AsyncSession, local_storage: LocalDiskStorage) -> None:
    """Trang rỗng: dấu đi cùng **một** câu SQL với các dòng (cùng snapshot), bằng dấu của tập hiện tại."""
    user = await make_user(db_session)
    await _seed_three(db_session, local_storage)
    whole = await service.list_page(db_session, local_storage, user.id, size=10)
    statements: list[str] = []

    def count(state: object) -> None:
        """Đếm mỗi lượt `execute` của session."""
        statements.append(str(state))

    event.listen(db_session.sync_session, "do_orm_execute", count)
    try:
        empty = await service.list_page(db_session, local_storage, user.id, size=10, after=(99, "z"))
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", count)
    assert (empty.items, empty.following, len(statements)) == ([], None, 1)
    assert empty.revision == whole.revision
    assert [item.id for item in whole.items] == ["item-a", "item-b", "item-c"]


async def test_library_list_items__C15_mine_only_for_owner(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Mục `mine` chỉ có trong các trang của chủ; dấu của hai người khác nhau, của người kia không đổi vì nó."""
    me, other = await make_user(db_session), await make_user(db_session)
    await _seed_three(db_session, local_storage)
    before = (await _page(api_client, other, limit=2)).headers[REVISION_HEADER]
    await make_library_item(db_session, local_storage, id="a-mine", owner=me, sort_order=1)

    mine, mine_revs, _ = await _read_all(api_client, me, limit=2)
    theirs, their_revs, _ = await _read_all(api_client, other, limit=2)
    assert mine == ["a-mine", "item-a", "item-b", "item-c"]
    assert theirs == ["item-a", "item-b", "item-c"]
    assert their_revs == {before}
    assert mine_revs != their_revs


def _forge(cursor: str) -> str:
    """Sửa vị trí trong cursor thật mà giữ chữ ký cũ (MAC không còn khớp)."""
    head, mac = cursor.split(".")
    body = json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))
    body["p"]["order"] = -1
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return f"{base64.urlsafe_b64encode(raw).decode().rstrip('=')}.{mac}"


async def test_library_list_items__C15_cursor_invalid(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Cursor của người khác, cursor bị sửa, cursor bịa → 422 `CURSOR_INVALID` (cursor ký kèm `user`)."""
    me, other = await make_user(db_session), await make_user(db_session)
    await _seed_three(db_session, local_storage)
    foreign = (await _page(api_client, other, limit=1)).headers[NEXT_CURSOR_HEADER]
    mine = (await _page(api_client, me, limit=1)).headers[NEXT_CURSOR_HEADER]

    for cursor in (foreign, _forge(mine), "khong-phai-cursor"):
        response = await _page(api_client, me, cursor=cursor)
        assert response.status_code == 422
        assert response.json()["code"] == "CURSOR_INVALID"


@pytest.mark.parametrize("limit", [0, PAGE_MAX + 1, -1])
async def test_library_list_items__C15_limit_out_of_range(
    api_client: httpx.AsyncClient, db_session: AsyncSession, limit: int
) -> None:
    """`limit` ngoài `1…500` → 422."""
    user = await make_user(db_session)
    await db_session.commit()
    assert (await _page(api_client, user, limit=limit)).status_code == 422


async def test_library_list_items__C15_limit_edges(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """`limit=1` → một mục và cursor; `limit=500` (trần) với 3 mục → đủ 3, không cursor."""
    user = await make_user(db_session)
    await _seed_three(db_session, local_storage)
    one = await _page(api_client, user, limit=1)
    assert [item["id"] for item in one.json()] == ["item-a"]
    assert NEXT_CURSOR_HEADER in one.headers
    top = await _page(api_client, user, limit=PAGE_MAX)
    assert len(top.json()) == 3
    assert NEXT_CURSOR_HEADER not in top.headers
