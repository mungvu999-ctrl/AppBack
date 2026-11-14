"""Test hợp đồng của N5 `settings_read_settings` (B2-02 [8]): Đ — C01 C06 C08 C17."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.project_settings.tests.support import (
    headers_of,
    put_settings,
    replace_body,
    seed_engineer_project,
    seed_project,
    settings_path,
)
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.clock import FakeClock


async def test_settings_read_settings__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Chưa có dòng → mặc định `revision 0`; viewer (thành viên) đọc được; sau khi ghi thì trả đúng giá trị đã lưu.

    Hàng catalog: [BE4-G-220].
    """
    owner, project = await seed_engineer_project(db_session)
    viewer = await make_user(db_session, role="viewer")
    shared = await seed_project(db_session, owner=owner, members=[viewer])

    default = await api_client.get(settings_path(shared.id), headers=headers_of(viewer))
    assert default.status_code == 200
    assert default.json() == {
        "revision": 0,
        "buildingType": "residential",
        "lengthUnit": "mm",
        "snapToleranceMm": 50,
        "confidenceThreshold": 0.75,
        "defaultScaleMmPerPx": 1,
        "detectFurniture": True,
        "separateUnits": False,
    }

    written = await put_settings(api_client, project.id, owner, replace_body(0, notes="Ghi chú"))
    assert written.status_code == 200
    read = await api_client.get(settings_path(project.id), headers=headers_of(owner))
    assert read.json() == written.json()
    assert read.json()["revision"] == 1


async def test_settings_read_settings__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống không phải thành viên → 404 `resource:"project"`, không phải 403.
    Catalog: B09-set-002.
    """
    _, project = await seed_engineer_project(db_session)
    outsider = await make_user(db_session, role="admin")
    response = await api_client.get(settings_path(project.id), headers=headers_of(outsider))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_settings_read_settings__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner, deleted_at=fake_clock.now())
    response = await api_client.get(settings_path(project.id), headers=headers_of(owner))
    assert response.status_code == 404


async def test_settings_read_settings__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`notes` NULL → vắng khoá (không `null`); có giá trị thì có khoá."""
    owner, project = await seed_engineer_project(db_session)
    await put_settings(api_client, project.id, owner, replace_body(0))
    bare = await api_client.get(settings_path(project.id), headers=headers_of(owner))
    assert "notes" not in bare.json()

    await put_settings(api_client, project.id, owner, replace_body(1, notes="Có ghi chú"))
    noted = await api_client.get(settings_path(project.id), headers=headers_of(owner))
    assert noted.json()["notes"] == "Có ghi chú"
