"""Test hợp đồng của #10 `floors_create_floor` (B2-03 [2], [6], [8]).

Route (`router.py`, `service.py`, `lookup.py`) và model (`packages/db/models/floors.py`,
`packages/testing/factories/floors.py`) đã hợp nhất. Mọi test
chỉ mồi qua factory/model đã chốt tên và gọi HTTP theo đúng hợp đồng; không nhập `router`,
`service`, `lookup`, `view_parts`, `resolvers`, `errors`.

Ma trận case (B2-03 [8]): G → C01 C02 C03 C06 C07 C08 C14 C16 C17 C18 (C14 thêm cố định).
"""

import asyncio
import unicodedata
from collections.abc import Iterator, Sequence
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.core import extensions
from apps.api.floors.settings import get_floors_settings, reset_floors_settings_cache
from apps.api.floors.tests._bodies import (
    FLOORS_MAX_TEST,
    FORBIDDEN_ROLE,
    floor_body,
    floors_path,
    headers_of,
    live_floor_row,
    new_level_id,
    seed_project,
    summary_row,
)
from apps.api.projects.parts import FLOOR_DRAWINGS, SUBMODULE, ViewPart
from apps.api.projects.wire import DrawingOut
from packages.db.models.floors import FloorRow
from packages.db.models.projects import ProjectFloorSummary
from packages.testing.factories.auth import make_user
from packages.testing.factories.floors import make_floor
from packages.testing.fixtures.access import assert_one_activity
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.clock import FakeClock

# ---------------------------------------------------------------------------
# G: C01 C02 C03 C06 C07 C08 C14 C16 C17 C18
# ---------------------------------------------------------------------------


async def test_floors_create_floor__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Đúng: 201, dây khớp thân, dòng `floors` và `project_floor_summaries` đã ghi (K22)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body(name="Tầng trệt", order=0, elevation_mm=0, height_mm=3000)
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 201
    out = response.json()
    assert out["id"] == body["id"]
    assert (out["name"], out["order"], out["elevationMm"], out["heightMm"]) == ("Tầng trệt", 0, 0, 3000)
    assert out["drawings"] == []

    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=body["id"])
    assert row is not None
    assert (row.name, row.floor_order, row.created_by) == ("Tầng trệt", 0, owner.id)
    summary = await summary_row(db_sessionmaker, project_id=project.id, floor_level_id=body["id"])
    assert summary is not None
    assert summary.hidden is False


@pytest.mark.parametrize(
    "bad_overrides",
    [{"order": "0"}, {"elevationMm": True}, {"heightMm": "3000"}],
)
async def test_floors_create_floor__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, bad_overrides: dict[str, Any]
) -> None:
    """Số kiểu sai (chuỗi, `bool`) → 422 `VALIDATION` có `field`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body() | bad_overrides
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    out = response.json()
    assert out["code"] == "VALIDATION"
    assert "field" in out


async def test_floors_create_floor__C03(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khoá lạ, kể cả `projectId`, → 422 `VALIDATION` (`extra="forbid"`, W21)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body() | {"projectId": project.id}
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


async def test_floors_create_floor__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống không phải thành viên → 404 `resource:"project"` (K08).
    Catalog: B09-flr-006.
    """
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    outsider = await make_user(db_session, role="admin")
    response = await api_client.post(floors_path(project.id), json=floor_body(), headers=headers_of(outsider))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_floors_create_floor__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Vai mạnh nhất thiếu `layer.edit` (`viewer`) → 403.
    Catalog: B09-flr-005.
    """
    owner = await make_user(db_session, role="engineer")
    viewer = await make_user(db_session, role=FORBIDDEN_ROLE)
    project = await seed_project(db_session, owner=owner, members=[viewer])
    response = await api_client.post(floors_path(project.id), json=floor_body(), headers=headers_of(viewer))
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_floors_create_floor__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner, deleted_at=fake_clock.now())
    response = await api_client.post(floors_path(project.id), json=floor_body(), headers=headers_of(owner))
    assert response.status_code == 404


async def test_floors_create_floor__C14(
    api_app: FastAPI, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Hai POST song song cùng `id` (hai client thật, K23) → {201, 409}, đúng một dòng sống."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    level_id = new_level_id()
    async with make_api_client(api_app) as client_a, make_api_client(api_app) as client_b:
        first, second = await asyncio.gather(
            client_a.post(floors_path(project.id), json=floor_body(id=level_id), headers=headers_of(owner)),
            client_b.post(floors_path(project.id), json=floor_body(id=level_id), headers=headers_of(owner)),
        )
    statuses = {first.status_code, second.status_code}
    assert statuses == {201, 409}
    loser = first if first.status_code == 409 else second
    assert (loser.json()["code"], loser.json()["field"]) == ("FLOOR_ID_TAKEN", "id")
    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=level_id)
    assert row is not None


async def test_floors_create_floor__C16(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tên NFD người dùng gõ → lưu và trả NFC (K20)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    nfd_name = unicodedata.normalize("NFD", "Tầng Áp Mái")
    body = floor_body(name=nfd_name)
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 201
    assert response.json()["name"] == unicodedata.normalize("NFC", nfd_name)


async def test_floors_create_floor__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bảng đếm mới toanh (`area_m2 IS NULL`) → `areaM2` vắng khoá, không `null` (W2, K02)."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    response = await api_client.post(floors_path(project.id), json=floor_body(), headers=headers_of(owner))
    assert response.status_code == 201
    body = response.json()
    assert "areaM2" not in body
    assert None not in body.values()


async def test_floors_create_floor__C18(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Đúng một dòng `activity_log`, `kind=FLOOR_CREATE`, `object_code` = id tầng mới."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body(name="Ghi nhật ký")
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    row = await assert_one_activity(
        db_sessionmaker, actor_id=owner.id, kind=ActivityKind.FLOOR_CREATE, object_code=response.json()["id"]
    )
    assert row.object_label == "Ghi nhật ký"


# ---------------------------------------------------------------------------
# Test đặt tên theo việc (B2-03 [8] "Cách dựng")
# ---------------------------------------------------------------------------


async def test_floors_create_floor__missing_id(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thiếu `id` → 422 `field:"id"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body()
    del body["id"]
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["field"] == "id"


async def test_floors_create_floor__id_wrong_pattern(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`id` quá ngắn, không khớp `L-[0-9A-Z]{10,64}` → 422 `field:"id"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body(id="L-abc")
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["field"] == "id"


async def test_floors_create_floor__name_bidi_override_rejected(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`name` chứa U+202E (đảo chiều) → 422 `field:"name"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body(name="Tầng‮Lật")
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["field"] == "name"


async def test_floors_create_floor__elevation_rounds_to_nearest_int(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`elevationMm` cách số nguyên ≤ 0,01 → làm tròn, lưu và trả `int`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body(elevation_mm=1004.9999999999999)
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 201
    assert response.json()["elevationMm"] == 1005


async def test_floors_create_floor__elevation_rounding_out_of_tolerance(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`elevationMm` lệch số nguyên gần nhất > 0,01 (`1004.5`) → 422 `field:"elevationMm"`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    body = floor_body(elevation_mm=1004.5)
    response = await api_client.post(floors_path(project.id), json=body, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["field"] == "elevationMm"


@pytest.fixture
def _floors_max_test(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`FLOORS_MAX=3` cho các test biên; dọn cache cả trước lẫn sau (dinh-chinh.md #8)."""
    monkeypatch.setenv("FLOORS_MAX", str(FLOORS_MAX_TEST))
    reset_floors_settings_cache()
    yield
    reset_floors_settings_cache()


@pytest.mark.usefixtures("_floors_max_test")
async def test_floors_create_floor__limit_reached(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Dự án đã có `FLOORS_MAX` tầng chưa xoá → tầng thứ `FLOORS_MAX + 1` → 422 `FLOOR_LIMIT_REACHED`."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    for index in range(FLOORS_MAX_TEST):
        await make_floor(db_session, project=project, order=index)
    await db_session.commit()
    response = await api_client.post(floors_path(project.id), json=floor_body(), headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["code"] == "FLOOR_LIMIT_REACHED"


async def test_floors_create_floor__restore_within_window_keeps_pk_and_counts(
    api_client: httpx.AsyncClient,
    api_app: FastAPI,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Xoá rồi POST cùng `id` sau 9 phút → 201, cùng `pk`, `walls_total` giữ, hết ẩn, hết dấu mặt cắt (HS-03),
    `floor.drawings` quay lại. Catalog: B11-flr-013.
    """
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    level_id = new_level_id()
    floor = await make_floor(db_session, project=project, level_id=level_id, name="Cũ", order=0)
    await db_session.execute(
        update(ProjectFloorSummary)
        .where(ProjectFloorSummary.project_id == project.id, ProjectFloorSummary.floor_level_id == level_id)
        .values(walls_total=5, hidden=True)
    )
    await db_session.execute(
        update(FloorRow)
        .where(FloorRow.pk == floor.pk)
        .values(deleted_at=fake_clock.now(), hidden_in_3d=True, is_section=True)
    )
    await db_session.commit()
    fake_clock.advance(timedelta(minutes=9))

    fake_drawing = DrawingOut(
        id="drw_" + "0" * 26,
        name="Bản vẽ giả",
        url="https://appback.test/x.png",
        width_mm=100,
        height_mm=100,
        uploaded_at=fake_clock.now(),
        uploader_id=owner.id,
    )

    async def _drawings(_db: AsyncSession, pks: Sequence[str]) -> dict[str, list[DrawingOut]]:
        """Trả bản vẽ giả cho `pk` của tầng vừa khôi phục, `[]` cho mọi `pk` khác."""
        return {str(floor.pk): [fake_drawing] if str(floor.pk) in pks else []}

    extensions.override(api_app, SUBMODULE, [("test_drawings", [ViewPart(kind=FLOOR_DRAWINGS, load=_drawings)])])

    response = await api_client.post(
        floors_path(project.id),
        json=floor_body(id=level_id, name="Mới"),
        headers=headers_of(owner),
    )
    assert response.status_code == 201
    out = response.json()
    assert len(out["drawings"]) == 1
    assert out["hiddenIn3d"] is False

    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=level_id)
    assert row is not None
    assert (row.pk, row.hidden_in_3d, row.is_section) == (floor.pk, False, False)

    summary = await summary_row(db_sessionmaker, project_id=project.id, floor_level_id=level_id)
    assert summary is not None
    assert (summary.walls_total, summary.hidden) == (5, False)


async def test_floors_create_floor__restore_outside_window_gets_new_pk(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Xoá rồi POST cùng `id` sau 11 phút (quá cửa sổ 600 s) → 201, `pk` **mới**.
    Catalog: B11-flr-014.
    """
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    level_id = new_level_id()
    floor = await make_floor(db_session, project=project, level_id=level_id, order=0)
    await db_session.execute(update(FloorRow).where(FloorRow.pk == floor.pk).values(deleted_at=fake_clock.now()))
    await db_session.commit()
    fake_clock.advance(timedelta(minutes=11))

    response = await api_client.post(floors_path(project.id), json=floor_body(id=level_id), headers=headers_of(owner))
    assert response.status_code == 201

    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=level_id)
    assert row is not None
    assert row.pk != floor.pk


@pytest.mark.usefixtures("_floors_max_test")
async def test_floors_create_floor__restore_blocked_by_limit(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Khôi phục khi dự án đã đủ `FLOORS_MAX` tầng sống → 422 `FLOOR_LIMIT_REACHED` (không khôi phục).
    Catalog: B11-flr-016.
    """
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    deleted_level_id = new_level_id()
    deleted_floor = await make_floor(db_session, project=project, level_id=deleted_level_id, order=0)
    await db_session.execute(
        update(FloorRow).where(FloorRow.pk == deleted_floor.pk).values(deleted_at=fake_clock.now())
    )
    for index in range(FLOORS_MAX_TEST):
        await make_floor(db_session, project=project, order=index + 1)
    await db_session.commit()
    fake_clock.advance(timedelta(minutes=9))

    response = await api_client.post(
        floors_path(project.id), json=floor_body(id=deleted_level_id), headers=headers_of(owner)
    )
    assert response.status_code == 422
    assert response.json()["code"] == "FLOOR_LIMIT_REACHED"


async def test_floors_create_floor__restore_exactly_at_window_gets_new_pk(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Biên: đúng `FLOOR_RESTORE_WINDOW_S` giây sau khi xoá là hết cửa sổ (cùng luật `restore_window_elapsed`).
    Catalog: B11-flr-015.
    """
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner)
    level_id = new_level_id()
    floor = await make_floor(db_session, project=project, level_id=level_id, order=0)
    await db_session.execute(update(FloorRow).where(FloorRow.pk == floor.pk).values(deleted_at=fake_clock.now()))
    await db_session.commit()
    fake_clock.advance(timedelta(seconds=get_floors_settings().floor_restore_window_s))

    response = await api_client.post(floors_path(project.id), json=floor_body(id=level_id), headers=headers_of(owner))
    assert response.status_code == 201

    row = await live_floor_row(db_sessionmaker, project_id=project.id, level_id=level_id)
    assert row is not None
    assert row.pk != floor.pk
