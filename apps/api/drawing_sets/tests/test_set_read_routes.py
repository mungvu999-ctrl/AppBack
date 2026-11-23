"""Case của N57 `drawing_sets_read_set` (Đ), N58 `drawing_sets_list_sets` (Đ, C15), N61 `drawing_sets_delete_set` (G)
— K-32a. Đọc và xoá chạy cả khi công tắc tắt (L4-N04)."""

from datetime import timedelta
from typing import Final

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.drawing_sets.tests._set_helpers import MISSING_SET, seed_review_set, set_path, sets_path
from apps.api.drawings.tests._upload_helpers import headers_of, make_stage
from packages.db.models.drawing_sets import DrawingSetRow
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawing_sets import make_drawing_set
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.access import assert_one_activity
from packages.testing.fixtures.clock import FakeClock

OFF: Final = "false"


@pytest.fixture(autouse=True)
def switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mọi test ở đây chạy với công tắc **tắt**: đọc và xoá không phụ thuộc nó."""
    monkeypatch.setenv("DRAWING_SET_ENABLED", OFF)
    reset_drawing_set_settings_cache()


# --------------------------------------------------------------------------- N57


async def test_drawing_sets_read_set__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Bộ `review` → 200 đầy đủ tệp/hình; hình có `imageUrl` ký sẵn, câu hỏi lạ bị bỏ, khung cả trang."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, views=2, pages=2)
    response = await api_client.get(set_path(stage.project_id, seeded.drawing_set.id), headers=stage.headers)
    assert response.status_code == 200, response.text
    wire = response.json()
    assert (wire["status"], wire["revision"], len(wire["files"]), len(wire["views"])) == ("review", 0, 1, 2)
    first = wire["views"][0]
    assert first["imageUrl"].startswith("https://appback.test/")
    assert first["questions"] == [{"code": "NO_TEXT"}]
    assert first["crop"] == {"xRatio": 0.0, "yRatio": 0.0, "widthRatio": 1.0, "heightRatio": 1.0}
    assert [view["pageIndex"] for view in wire["views"]] == [0, 1]


async def test_drawing_sets_read_set__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án → 404 `resource:"project"`."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.get(set_path(stage.project_id, drawing_set.id), headers=headers_of(outsider))
    assert (response.status_code, response.json()["resource"]) == (404, "project")


@pytest.mark.parametrize("deleted", [False, True])
async def test_drawing_sets_read_set__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock, deleted: bool
) -> None:
    """Bộ không có, hay đã xoá mềm → 404 `resource:"drawingSet"`."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project, deleted_at=fake_clock.now())
    await db_session.commit()
    set_id = drawing_set.id if deleted else MISSING_SET
    response = await api_client.get(set_path(stage.project_id, set_id), headers=stage.headers)
    assert (response.status_code, response.json()["resource"]) == (404, "drawingSet")


async def test_drawing_sets_read_set__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bộ `receiving` chưa tên → `name`, `errorCode`, `confirmedAt` vắng; không hình nào nên không `imageUrl`."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project)
    await db_session.commit()
    wire = (await api_client.get(set_path(stage.project_id, drawing_set.id), headers=stage.headers)).json()
    assert {"name", "errorCode", "confirmedAt"}.isdisjoint(wire)
    assert None not in wire.values()


async def test_read_set_expires_stalled_analysis(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """`analyzing` quá `DRAWING_SET_ANALYSIS_STALL_S` → đọc ra `failed` `DRAWING_SET_ANALYSIS_FAILED` và đã ghi."""
    stage = await make_stage(db_session)
    stalled = await make_drawing_set(db_session, project=stage.scene.project, status="analyzing")
    fresh = await make_drawing_set(db_session, project=stage.scene.project, status="analyzing")
    await db_session.execute(
        update(DrawingSetRow)
        .where(DrawingSetRow.id == stalled.id)
        .values(analysis_started_at=DrawingSetRow.created_at - timedelta(hours=1))
    )
    await db_session.execute(
        update(DrawingSetRow).where(DrawingSetRow.id == fresh.id).values(analysis_started_at=DrawingSetRow.created_at)
    )
    await db_session.commit()
    wire = (await api_client.get(set_path(stage.project_id, stalled.id), headers=stage.headers)).json()
    assert (wire["status"], wire["errorCode"]) == ("failed", "DRAWING_SET_ANALYSIS_FAILED")
    async with db_sessionmaker() as session:
        status = (await session.execute(select(DrawingSetRow.status).where(DrawingSetRow.id == fresh.id))).scalar_one()
    assert status == "analyzing"


# --------------------------------------------------------------------------- N58


async def test_drawing_sets_list_sets__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Danh sách mới trước kèm số tệp/hình; bộ đã xoá mềm không hiện."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, views=3)
    await make_drawing_set(db_session, project=stage.scene.project, name="Bộ hai")
    await db_session.commit()
    response = await api_client.get(sets_path(stage.project_id), headers=stage.headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item.get("name"), item["fileCount"], item["viewCount"]) for item in items] == [
        ("Bộ hai", 0, 0),
        (None, 1, 3),
    ]
    assert items[1]["id"] == seeded.drawing_set.id
    assert "nextCursor" not in response.json()


async def test_drawing_sets_list_sets__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án → 404."""
    stage = await make_stage(db_session)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.get(sets_path(stage.project_id), headers=headers_of(outsider))
    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_drawing_sets_list_sets__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    owner = await make_user(db_session)
    project = await make_project(db_session, owner=owner, deleted_at=fake_clock.now())
    await db_session.commit()
    assert (await api_client.get(sets_path(project.id), headers=headers_of(owner))).status_code == 404


@pytest.mark.parametrize("count", [0, 1, 3, 4])
async def test_drawing_sets_list_sets__C15(
    api_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    """Trần = 3: rỗng, một, chạm trần (không cursor), vượt trần (cursor ổn định, trang hai đúng phần còn lại)."""
    monkeypatch.setenv("DRAWING_SET_LIST_LIMIT", "3")
    reset_drawing_set_settings_cache()
    stage = await make_stage(db_session)
    ids = [(await make_drawing_set(db_session, project=stage.scene.project)).id for _ in range(count)]
    await db_session.commit()
    first = (await api_client.get(sets_path(stage.project_id), headers=stage.headers)).json()
    seen = [item["id"] for item in first["items"]]
    if count > 3:
        again = (await api_client.get(sets_path(stage.project_id), headers=stage.headers)).json()
        assert again["nextCursor"] == first["nextCursor"]
        rest = await api_client.get(
            sets_path(stage.project_id), params={"cursor": first["nextCursor"]}, headers=stage.headers
        )
        seen += [item["id"] for item in rest.json()["items"]]
        assert "nextCursor" not in rest.json()
    else:
        assert "nextCursor" not in first
    assert sorted(seen) == sorted(ids)
    assert len(first["items"]) == min(count, 3)


async def test_list_sets_rejects_foreign_cursor(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Cursor sửa tay → 422 `CURSOR_INVALID`."""
    stage = await make_stage(db_session)
    response = await api_client.get(sets_path(stage.project_id), params={"cursor": "abc.def"}, headers=stage.headers)
    assert (response.status_code, response.json()["code"]) == (422, "CURSOR_INVALID")


async def test_drawing_sets_list_sets__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Mục chưa tên, chưa xác nhận → `name`, `confirmedAt` vắng khoá."""
    stage = await make_stage(db_session)
    await make_drawing_set(db_session, project=stage.scene.project)
    await db_session.commit()
    item = (await api_client.get(sets_path(stage.project_id), headers=stage.headers)).json()["items"][0]
    assert {"name", "confirmedAt"}.isdisjoint(item)


# --------------------------------------------------------------------------- N61


async def test_drawing_sets_delete_set__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Xoá mềm → 200 ảnh chụp trước khi ẩn; đọc lại 404; dòng còn với `deleted_at`."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project, status="confirmed")
    await db_session.commit()
    response = await api_client.delete(set_path(stage.project_id, drawing_set.id), headers=stage.headers)
    assert (response.status_code, response.json()["status"]) == (200, "confirmed")
    assert (await api_client.get(set_path(stage.project_id, drawing_set.id), headers=stage.headers)).status_code == 404
    async with db_sessionmaker() as session:
        row = (await session.execute(select(DrawingSetRow).where(DrawingSetRow.id == drawing_set.id))).scalar_one()
    assert row.deleted_at is not None


async def test_drawing_sets_delete_set__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án → 404 `resource:"project"`."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.delete(set_path(stage.project_id, drawing_set.id), headers=headers_of(outsider))
    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_drawing_sets_delete_set__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`viewer` → 403."""
    owner = await make_user(db_session)
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    drawing_set = await make_drawing_set(db_session, project=project)
    await db_session.commit()
    response = await api_client.delete(set_path(project.id, drawing_set.id), headers=headers_of(viewer, role="viewer"))
    assert response.status_code == 403


async def test_drawing_sets_delete_set__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bộ không có (hay đã xoá — gọi lại) → 404."""
    stage = await make_stage(db_session)
    response = await api_client.delete(set_path(stage.project_id, MISSING_SET), headers=stage.headers)
    assert (response.status_code, response.json()["resource"]) == (404, "drawingSet")


async def test_drawing_sets_delete_set__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Ảnh chụp bộ chưa tên → `name` vắng khoá."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project)
    await db_session.commit()
    wire = (await api_client.delete(set_path(stage.project_id, drawing_set.id), headers=stage.headers)).json()
    assert "name" not in wire


async def test_drawing_sets_delete_set__C18(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Một dòng nhật ký `drawingset.delete`."""
    stage = await make_stage(db_session)
    drawing_set = await make_drawing_set(db_session, project=stage.scene.project)
    await db_session.commit()
    await api_client.delete(set_path(stage.project_id, drawing_set.id), headers=stage.headers)
    await assert_one_activity(
        db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.DRAWING_SET_DELETE, object_code=drawing_set.id
    )
