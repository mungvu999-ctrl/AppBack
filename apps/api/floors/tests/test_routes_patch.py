"""Test hợp đồng của #34 `floors_patch_spatial_floor` (B2-03 [2], [6], [8]).

Route (`router.py`, `service.py`, `lookup.py`) đã hợp nhất. Ma trận case:
G → C01 C02 C03 C06 C07 C08 C16 C17 C18.
"""

import unicodedata
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.floors.tests._bodies import (
    FORBIDDEN_ROLE,
    floor_body,
    floor_path,
    floors_path,
    headers_of,
    live_floor_row,
    project_path,
    seed_project,
    spatial_path,
)
from packages.testing.factories.auth import make_user
from packages.testing.factories.floors import make_floor
from packages.testing.fixtures.access import activity_rows, assert_one_activity
from packages.testing.fixtures.clock import FakeClock

# ---------------------------------------------------------------------------
# G: C01 C02 C03 C06 C07 C08 C16 C17 C18
# ---------------------------------------------------------------------------


async def test_floors_patch_spatial_floor__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Đúng: thân FE trọn có `id` bằng đường → 200, `order` và bảng đếm đổi theo (K22)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, name="Cũ", order=0)
    await db_session.commit()

    body = floor_body(id=floor.level_id, name="Mới", order=1, elevation_mm=100, height_mm=3200)
    response = await api_client.patch(spatial_path(project.id, floor.level_id), json=body, headers=headers_of(owner))
    assert response.status_code == 200
    out = response.json()
    assert (out["name"], out["order"], out["elevationMm"], out["heightMm"]) == ("Mới", 1, 100, 3200)

    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=floor.level_id)
    assert row is not None
    assert (row.name, row.floor_order) == ("Mới", 1)


async def test_floors_patch_spatial_floor__C01_hidden_in_3d(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """FE3D-B3: `hiddenIn3d` lưu và trả lại (cả ở #12); gửi lại cùng giá trị = không ghi; `false` hiện lại."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    path = spatial_path(project.id, floor.level_id)

    hidden = await api_client.patch(path, json={"hiddenIn3d": True}, headers=headers_of(owner))
    assert hidden.status_code == 200
    assert hidden.json()["hiddenIn3d"] is True
    listed = await api_client.get(floors_path(project.id), headers=headers_of(owner))
    assert [item["hiddenIn3d"] for item in listed.json()] == [True]

    again = await api_client.patch(path, json={"hiddenIn3d": True}, headers=headers_of(owner))
    assert again.json() == hidden.json()
    assert len(await activity_rows(db_sessionmaker, actor_id=owner.id, kind=ActivityKind.FLOOR_EDIT)) == 1

    shown = await api_client.patch(path, json={"hiddenIn3d": False}, headers=headers_of(owner))
    assert shown.json()["hiddenIn3d"] is False
    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=floor.level_id)
    assert row is not None
    assert row.hidden_in_3d is False


async def test_floors_patch_spatial_floor__C01_is_section(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """HS-03: `isSection` lưu vào dòng tầng, kéo ẩn khỏi 3D; bỏ dấu không tự hiện lại; thân nói rõ thì theo thân."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    path = spatial_path(project.id, floor.level_id)

    marked = await api_client.patch(path, json={"isSection": True}, headers=headers_of(owner))
    assert (marked.status_code, marked.json()["hiddenIn3d"]) == (200, True)
    assert "isSection" not in marked.json()  # `FloorOut` không đổi: trạng thái đọc ở `GET …/house-proposal`
    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=floor.level_id)
    assert row is not None
    assert (row.is_section, row.hidden_in_3d) == (True, True)

    cleared = await api_client.patch(path, json={"isSection": False}, headers=headers_of(owner))
    assert cleared.json()["hiddenIn3d"] is True
    shown = await api_client.patch(path, json={"isSection": True, "hiddenIn3d": False}, headers=headers_of(owner))
    assert shown.json()["hiddenIn3d"] is False
    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=floor.level_id)
    assert row is not None
    assert (row.is_section, row.hidden_in_3d) == (True, False)


async def test_floors_patch_spatial_floor__C01_is_section_resent(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """HS-03: gửi lại `isSection: true` cho dòng đã là mặt cắt không ẩn lại tầng người dùng đã cho hiện,
    không ghi nhật ký."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    path = spatial_path(project.id, floor.level_id)

    await api_client.patch(path, json={"isSection": True}, headers=headers_of(owner))
    shown = await api_client.patch(path, json={"hiddenIn3d": False}, headers=headers_of(owner))
    assert shown.json()["hiddenIn3d"] is False
    resent = await api_client.patch(path, json={"isSection": True}, headers=headers_of(owner))
    assert (resent.status_code, resent.json()["hiddenIn3d"]) == (200, False)
    assert len(await activity_rows(db_sessionmaker, actor_id=owner.id, kind=ActivityKind.FLOOR_EDIT)) == 2
    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=floor.level_id)
    assert row is not None
    assert (row.is_section, row.hidden_in_3d) == (True, False)


@pytest.mark.parametrize(
    "bad_overrides",
    [
        {"order": "0"},
        {"elevationMm": True},
        {"heightMm": "3000"},
        {"name": None},
        {"hiddenIn3d": "true"},
        {"hiddenIn3d": 1},
        {"hiddenIn3d": None},
        {"isSection": "true"},
        {"isSection": None},
    ],
)
async def test_floors_patch_spatial_floor__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, bad_overrides: dict[str, Any]
) -> None:
    """Số kiểu sai, `name: null` tường minh → 422 `VALIDATION` có `field`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    body = {"id": floor.level_id} | bad_overrides
    response = await api_client.patch(spatial_path(project.id, floor.level_id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


async def test_floors_patch_spatial_floor__C03(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khoá lạ → 422 `VALIDATION`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"lạ": 1}, headers=headers_of(owner)
    )
    assert response.status_code == 422


async def test_floors_patch_spatial_floor__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống không phải thành viên → 404 `resource:"project"` (K08).
    Catalog: B09-flr-015.
    """
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    outsider = await make_user(db_session, role="admin")
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": "X"}, headers=headers_of(outsider)
    )
    assert response.status_code == 404


async def test_floors_patch_spatial_floor__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Vai mạnh nhất thiếu `layer.edit` (`viewer`) → 403.
    Catalog: B09-flr-014, B11-set-002., B11-set-002.
    """
    owner = await make_user(db_session, role="engineer")
    viewer = await make_user(db_session, role=FORBIDDEN_ROLE)
    project = await seed_project(db_session, owner=owner, members=[viewer])
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": "X"}, headers=headers_of(viewer)
    )
    assert response.status_code == 403


async def test_floors_patch_spatial_floor__C08_project_deleted(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    project.deleted_at = fake_clock.now()
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": "X"}, headers=headers_of(owner)
    )
    assert response.status_code == 404


async def test_floors_patch_spatial_floor__C08_floor_deleted(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Tầng đã xoá mềm → 404 `resource:"floor"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    await api_client.delete(floor_path(floor.level_id), headers=headers_of(owner))
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": "X"}, headers=headers_of(owner)
    )
    assert response.status_code == 404


async def test_floors_patch_spatial_floor__C16(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tên NFD → lưu và trả NFC."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    nfd_name = unicodedata.normalize("NFD", "Tầng Áp Mái")
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": nfd_name}, headers=headers_of(owner)
    )
    assert response.status_code == 200
    assert response.json()["name"] == unicodedata.normalize("NFC", nfd_name)


async def test_floors_patch_spatial_floor__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`areaM2` vắng khi bảng đếm chưa có diện tích."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": "Đổi tên"}, headers=headers_of(owner)
    )
    body = response.json()
    assert "areaM2" not in body
    assert None not in body.values()


async def test_floors_patch_spatial_floor__C18(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Đúng một dòng `activity_log`, `kind=FLOOR_EDIT`, nhãn = tên mới."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, name="Trước", order=0)
    await db_session.commit()
    await api_client.patch(spatial_path(project.id, floor.level_id), json={"name": "Sau"}, headers=headers_of(owner))
    row = await assert_one_activity(
        db_sessionmaker, actor_id=owner.id, kind=ActivityKind.FLOOR_EDIT, object_code=floor.level_id
    )
    assert row.object_label == "Sau"


# ---------------------------------------------------------------------------
# Test đặt tên theo việc (B2-03 [8] "Cách dựng")
# ---------------------------------------------------------------------------


async def test_floors_patch_spatial_floor__id_mismatch_with_path(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """`id` trong thân khác `{floor_id}` trên đường → 422 `PATH_BODY_MISMATCH`, không đổi gì."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, name="Không đụng", order=0)
    await db_session.commit()

    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"id": "L-KHAC0000000"}, headers=headers_of(owner)
    )
    assert response.status_code == 422
    assert response.json()["code"] == "PATH_BODY_MISMATCH"

    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=floor.level_id)
    assert row is not None
    assert row.name == "Không đụng"


async def test_floors_patch_spatial_floor__id_equal_to_path_is_ignored(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`id` trong thân bằng `{floor_id}` trên đường → bỏ qua, 200 như bình thường."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"id": floor.level_id, "name": "OK"}, headers=headers_of(owner)
    )
    assert response.status_code == 200
    assert response.json()["name"] == "OK"


async def test_floors_patch_spatial_floor__empty_body_is_a_noop(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """`{}` → 200 hiện trạng, `projects.updated_at` **không** đổi, không nhật ký."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner, name="Giữ nguyên")
    floor = await make_floor(db_session, project=project, name="Giữ nguyên", order=0)
    await db_session.commit()
    before = await api_client.get(project_path(project.id), headers=headers_of(owner))

    response = await api_client.patch(spatial_path(project.id, floor.level_id), json={}, headers=headers_of(owner))
    assert response.status_code == 200
    assert response.json()["name"] == "Giữ nguyên"
    after = await api_client.get(project_path(project.id), headers=headers_of(owner))
    assert after.json()["updatedAt"] == before.json()["updatedAt"]
    assert await activity_rows(db_sessionmaker, actor_id=owner.id, kind=ActivityKind.FLOOR_EDIT) == []


async def test_floors_patch_spatial_floor__same_values_behaves_like_empty_body(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Gửi lại đúng giá trị hiện tại → như `{}`: không nhật ký, không đổi `updatedAt` (last-write-wins)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, name="Y nguyên", order=2)
    await db_session.commit()
    before = await api_client.get(project_path(project.id), headers=headers_of(owner))

    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"name": "Y nguyên", "order": 2}, headers=headers_of(owner)
    )
    assert response.status_code == 200
    after = await api_client.get(project_path(project.id), headers=headers_of(owner))
    assert after.json()["updatedAt"] == before.json()["updatedAt"]
    assert await activity_rows(db_sessionmaker, actor_id=owner.id, kind=ActivityKind.FLOOR_EDIT) == []


async def test_floors_patch_spatial_floor__height_below_minimum(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`heightMm: 1999` (dưới trần 2000) → 422 `field:"heightMm"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"heightMm": 1999}, headers=headers_of(owner)
    )
    assert response.status_code == 422
    assert response.json()["field"] == "heightMm"


async def test_floors_patch_spatial_floor__height_as_string(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`heightMm: "3000"` (chuỗi) → 422 `field:"heightMm"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"heightMm": "3000"}, headers=headers_of(owner)
    )
    assert response.status_code == 422
    assert response.json()["field"] == "heightMm"


async def test_floors_patch_spatial_floor__elevation_rounds_to_nearest_int(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`elevationMm` cách số nguyên ≤ 0,01 → làm tròn, lưu và trả `int` (giống #10)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"elevationMm": 1004.9999999999999}, headers=headers_of(owner)
    )
    assert response.status_code == 200
    assert response.json()["elevationMm"] == 1005


async def test_floors_patch_spatial_floor__elevation_rounding_out_of_tolerance(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`elevationMm` lệch số nguyên gần nhất > 0,01 (`1004.5`) → 422 `field:"elevationMm"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    response = await api_client.patch(
        spatial_path(project.id, floor.level_id), json={"elevationMm": 1004.5}, headers=headers_of(owner)
    )
    assert response.status_code == 422
    assert response.json()["field"] == "elevationMm"
