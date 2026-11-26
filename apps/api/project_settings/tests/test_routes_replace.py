"""Test hợp đồng của N6 `settings_replace_settings` (B2-02 [6], [8]): GV — C01-C03 C06-C09 C09b C16-C18.

C14 (hai người song song) ở `test_concurrency.py`.
"""

import unicodedata
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.project_settings.read import find_row, read_settings
from apps.api.project_settings.tests.support import (
    headers_of,
    put_settings,
    replace_body,
    seed_engineer_project,
    seed_project,
    settings_path,
)
from packages.db.models.projects import Project
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.access import activity_rows, assert_one_activity
from packages.testing.fixtures.clock import FakeClock


async def test_settings_replace_settings__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Base 0 → revision 1; base 1 → revision 2; số ra là số JSON; `read_settings` khớp response."""
    owner, project = await seed_engineer_project(db_session)
    first = await put_settings(api_client, project.id, owner, replace_body(0, notes="Nhà phố"))
    assert first.status_code == 200
    assert first.json() == {
        "revision": 1,
        "buildingType": "commercial",
        "notes": "Nhà phố",
        "lengthUnit": "m",
        "snapToleranceMm": 40,
        "confidenceThreshold": 0.5,
        "defaultScaleMmPerPx": 2.5,
        "detectFurniture": True,
        "separateUnits": False,
    }
    second = await put_settings(api_client, project.id, owner, replace_body(1, snapToleranceMm=41))
    assert second.status_code == 200
    assert second.json()["revision"] == 2
    assert "notes" not in second.json()  # PUT thay thế: vắng `notes` là xoá ghi chú

    stored = await read_settings(db_session, project.id)
    assert (stored.revision, stored.snap_tolerance_mm, stored.notes) == (2, 41, None)
    assert isinstance(second.json()["confidenceThreshold"], float)


async def test_settings_replace_settings__rounding_and_writer(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`0.12345` → `0.123` (HALF_UP); `last_writer_id` là `sub` của người gọi (K05)."""
    owner, project = await seed_engineer_project(db_session)
    response = await put_settings(
        api_client, project.id, owner, replace_body(0, confidenceThreshold=0.12345, defaultScaleMmPerPx=0.0000005)
    )
    assert response.status_code == 422  # 0,0000005 làm tròn 6 chữ số ra 0,000001 < 0,01

    response = await put_settings(
        api_client, project.id, owner, replace_body(0, confidenceThreshold=0.12345, defaultScaleMmPerPx=0.1234565)
    )
    assert response.status_code == 200
    assert response.json()["confidenceThreshold"] == 0.123
    assert response.json()["defaultScaleMmPerPx"] == 0.123457
    row = await find_row(db_session, project.id)
    assert row is not None
    assert row.last_writer_id == owner.id
    assert len(row.last_body_sha256) == 64


@pytest.mark.parametrize(
    "override",
    [
        {"snapToleranceMm": 121},
        {"snapToleranceMm": True},
        {"snapToleranceMm": 0},
        {"confidenceThreshold": 1.5},
        {"confidenceThreshold": -0.0004},
        {"confidenceThreshold": 1.0004},
        {"defaultScaleMmPerPx": 0.0099999},
        {"confidenceThreshold": "0.5"},
        {"defaultScaleMmPerPx": 1000.1},
        {"buildingType": "villa"},
        {"notes": ""},
        {"notes": "   "},
        {"notes": "x" * 501},
        {"notes": "a‮b"},
        {"notes": "a\x07b"},
        {"notes": 5},
        {"sceneStyle": "neon"},
        {"sceneStyle": 1},
        {"detectFurniture": "yes"},
        {"detectFurniture": 1},
        {"separateUnits": "true"},
        {"separateUnits": 1},
    ],
)
async def test_settings_replace_settings__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, override: dict[str, Any]
) -> None:
    """Thân sai kiểu/dải/ký tự cấm → 422 `VALIDATION`, `field` bắt đầu `body.`."""
    owner, project = await seed_engineer_project(db_session)
    response = await put_settings(api_client, project.id, owner, replace_body(0, **override))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"
    assert response.json()["field"].startswith("body.")


async def test_settings_replace_settings__C01_scene_style(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """FE3D-B2: `sceneStyle` lưu và trả lại; PUT vắng khoá giữ giá trị cũ (client cũ); `null` xoá về vắng."""
    owner, project = await seed_engineer_project(db_session)
    first = await put_settings(api_client, project.id, owner, replace_body(0, sceneStyle="go-am"))
    assert first.status_code == 200
    assert first.json()["sceneStyle"] == "go-am"

    kept = await put_settings(api_client, project.id, owner, replace_body(1, snapToleranceMm=41))
    assert kept.status_code == 200
    assert kept.json()["sceneStyle"] == "go-am"
    read = await api_client.get(settings_path(project.id), headers=headers_of(owner))
    assert read.json() == kept.json()

    payload = replace_body(2)
    payload["body"]["sceneStyle"] = None
    cleared = await put_settings(api_client, project.id, owner, payload)
    assert cleared.status_code == 200
    assert "sceneStyle" not in cleared.json()
    assert (await read_settings(db_session, project.id)).scene_style is None


async def test_settings_replace_settings__C01_detect_furniture(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """QT-FURN-TOGGLE: mặc định tắt; bật lưu và trả lại; PUT vắng khoá (hay `null`) giữ giá trị cũ; tắt lại được."""
    owner, project = await seed_engineer_project(db_session)
    on = await put_settings(api_client, project.id, owner, replace_body(0, detectFurniture=True))
    assert on.status_code == 200
    assert on.json()["detectFurniture"] is True

    kept = await put_settings(api_client, project.id, owner, replace_body(1, snapToleranceMm=41))
    assert kept.status_code == 200
    assert kept.json()["detectFurniture"] is True
    payload = replace_body(2)
    payload["body"]["detectFurniture"] = None
    still = await put_settings(api_client, project.id, owner, payload)
    assert still.json()["detectFurniture"] is True

    off = await put_settings(api_client, project.id, owner, replace_body(3, detectFurniture=False))
    assert off.json()["detectFurniture"] is False
    assert (await read_settings(db_session, project.id)).detect_furniture is False


async def test_settings_replace_settings__C01_separate_units(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """UX-R2 K34: `separateUnits` lưu và trả lại; PUT vắng khoá giữ giá trị cũ (client cũ); `false` tắt lại."""
    owner, project = await seed_engineer_project(db_session)
    first = await put_settings(api_client, project.id, owner, replace_body(0, separateUnits=True))
    assert first.status_code == 200
    assert first.json()["separateUnits"] is True

    kept = await put_settings(api_client, project.id, owner, replace_body(1, snapToleranceMm=41))
    assert kept.status_code == 200
    assert kept.json()["separateUnits"] is True
    read = await api_client.get(settings_path(project.id), headers=headers_of(owner))
    assert read.json() == kept.json()

    off = await put_settings(api_client, project.id, owner, replace_body(2, separateUnits=False))
    assert off.status_code == 200
    assert off.json()["separateUnits"] is False
    assert (await read_settings(db_session, project.id)).separate_units is False


async def test_settings_replace_settings__C02_missing_field(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Thiếu trường bắt buộc (`lengthUnit`) → 422 tại `body.lengthUnit`."""
    owner, project = await seed_engineer_project(db_session)
    response = await put_settings(api_client, project.id, owner, replace_body(0, lengthUnit=None))
    assert response.status_code == 422
    assert response.json()["field"] == "body.lengthUnit"


async def test_settings_replace_settings__C03(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khoá lạ `areaUnit` trong `body` → 422 (W1)."""
    owner, project = await seed_engineer_project(db_session)
    response = await put_settings(api_client, project.id, owner, replace_body(0, areaUnit="m2"))
    assert response.status_code == 422
    assert response.json()["field"].startswith("body.")


async def test_settings_replace_settings__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống không phải thành viên → 404, không ghi gì.
    Catalog: B09-set-006.
    """
    _, project = await seed_engineer_project(db_session)
    outsider = await make_user(db_session, role="admin")
    response = await put_settings(api_client, project.id, outsider, replace_body(0))
    assert response.status_code == 404
    assert await find_row(db_session, project.id) is None


async def test_settings_replace_settings__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thành viên `viewer` → 403 `FORBIDDEN`, không ghi gì.
    Catalog: B09-set-005.
    """
    owner = await make_user(db_session, role="engineer")
    viewer = await make_user(db_session, role="viewer")
    project = await seed_project(db_session, owner=owner, members=[viewer])
    response = await put_settings(api_client, project.id, viewer, replace_body(0))
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert await find_row(db_session, project.id) is None


async def test_settings_replace_settings__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    owner = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner, deleted_at=fake_clock.now())
    response = await put_settings(api_client, project.id, owner, replace_body(0))
    assert response.status_code == 404


async def test_settings_replace_settings__C09(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Base cũ và base 0 khi đã có dòng → 409 đúng W20; thiếu `baseVersion` → 428.
    Catalog: B11-set-003.
    """
    owner = await make_user(db_session, role="engineer")
    other = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=owner, members=[other])
    await put_settings(api_client, project.id, owner, replace_body(0))
    await put_settings(api_client, project.id, owner, replace_body(1, snapToleranceMm=41))

    for stale in (1, 0):
        response = await put_settings(api_client, project.id, other, replace_body(stale))
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "VERSION_CONFLICT"
        assert body["currentVersion"] == 2
        assert body["remoteChanges"] == []

    missing = await api_client.put(
        settings_path(project.id), json={"body": replace_body(0)["body"]}, headers=headers_of(owner)
    )
    assert missing.status_code == 428
    assert (await read_settings(db_session, project.id)).revision == 2


async def test_settings_replace_settings__C09_no_row(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Base > 0 khi chưa có dòng → 409 với `currentVersion` 0."""
    owner, project = await seed_engineer_project(db_session)
    response = await put_settings(api_client, project.id, owner, replace_body(3))
    assert response.status_code == 409
    assert response.json()["currentVersion"] == 0


async def test_settings_replace_settings__C09b(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Cùng người, cùng thân, base = revision - 1 → 200, revision giữ nguyên, đúng một dòng nhật ký."""
    owner, project = await seed_engineer_project(db_session)
    first = await put_settings(api_client, project.id, owner, replace_body(0, notes="Lặp"))
    repeat = await put_settings(api_client, project.id, owner, replace_body(0, notes="Lặp"))
    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    assert (await read_settings(db_session, project.id)).revision == 1
    rows = await activity_rows(db_sessionmaker, actor_id=owner.id, kind=ActivityKind.PROJECT_SETTINGS_UPDATE)
    assert len(rows) == 1


async def test_settings_replace_settings__C09b_not_repeat(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Người khác cùng thân, hay cùng người khác thân → vẫn 409."""
    owner = await make_user(db_session, role="engineer")
    other = await make_user(db_session, role="engineer")
    shared = await seed_project(db_session, owner=owner, members=[other])
    await put_settings(api_client, shared.id, owner, replace_body(0))
    assert (await put_settings(api_client, shared.id, other, replace_body(0))).status_code == 409
    assert (await put_settings(api_client, shared.id, owner, replace_body(0, snapToleranceMm=41))).status_code == 409
    assert (await read_settings(db_session, shared.id)).revision == 1


async def test_settings_replace_settings__C16(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`notes` NFD → lưu và trả NFC; gửi lại bản NFD với base = revision - 1 vẫn là C09b."""
    owner, project = await seed_engineer_project(db_session)
    nfd = unicodedata.normalize("NFD", "  Nhà Ở Xã Hội\n\tTầng trệt  ")
    first = await put_settings(api_client, project.id, owner, replace_body(0, notes=nfd))
    assert first.json()["notes"] == unicodedata.normalize("NFC", "Nhà Ở Xã Hội\n\tTầng trệt")
    again = await put_settings(api_client, project.id, owner, replace_body(0, notes=nfd))
    assert again.status_code == 200
    assert again.json()["revision"] == 1


async def test_settings_replace_settings__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Không gửi `notes` → response vắng khoá, không `null`."""
    owner, project = await seed_engineer_project(db_session)
    response = await put_settings(api_client, project.id, owner, replace_body(0))
    assert "notes" not in response.json()


async def test_settings_replace_settings__C18(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Ghi thắng → đúng một dòng nhật ký `PROJECT_SETTINGS_UPDATE` mang tên dự án; 409 không ghi thêm."""
    owner, project = await seed_engineer_project(db_session)
    await put_settings(api_client, project.id, owner, replace_body(0))
    row = await assert_one_activity(
        db_sessionmaker, actor_id=owner.id, kind=ActivityKind.PROJECT_SETTINGS_UPDATE, object_code=project.id
    )
    assert row.object_label == project.name
    await put_settings(api_client, project.id, owner, replace_body(0, snapToleranceMm=41))
    await assert_one_activity(
        db_sessionmaker, actor_id=owner.id, kind=ActivityKind.PROJECT_SETTINGS_UPDATE, object_code=project.id
    )


async def test_settings_replace_settings__idempotency_key_ignored(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Route GV không dùng bảng idempotency: cùng `Idempotency-Key`, thân khác → vẫn xử lý bình thường."""
    owner, project = await seed_engineer_project(db_session)
    headers = {**headers_of(owner), "Idempotency-Key": "khoa-fe-tu-gan-0001"}
    first = await api_client.put(settings_path(project.id), json=replace_body(0), headers=headers)
    second = await api_client.put(settings_path(project.id), json=replace_body(1, snapToleranceMm=41), headers=headers)
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["revision"] == 2


async def test_settings_replace_settings__cascade_on_project_delete(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Xoá cứng dự án → dòng cài đặt đi theo (FK `ON DELETE CASCADE`)."""
    owner, project = await seed_engineer_project(db_session)
    await put_settings(api_client, project.id, owner, replace_body(0))
    assert await find_row(db_session, project.id) is not None
    await db_session.execute(delete(Project).where(Project.id == project.id))
    await db_session.commit()
    assert await find_row(db_session, project.id) is None
