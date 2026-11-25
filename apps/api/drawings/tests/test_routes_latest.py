"""N7 `GET /api/projects/{project_id}/drawings/uploads/latest` (B2-04 [8], cổng case).

Route N7 khai trong `apps/api/drawings/latest.py`; `router.py` nhập router đó vào `ROUTERS`
nên `api_client` (app thật, `discover_routers()`) đã phục vụ nó.

Thời gian: mỗi lượt mồi `commit` **và** đẩy `fake_clock` một giây, vì `created_at` dùng
`now()` của Postgres (một giá trị cho cả giao dịch) còn ULID của `run_` lấy ms từ clock —
hai khoá sắp xếp chỉ cùng chiều khi cả hai cùng tiến.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.progress import FIRST_STEP
from apps.api.drawings.runs import record_step, start_run
from apps.api.drawings.tests._drawing_helpers import attach_floor_order
from apps.api.drawings.tests._drawing_helpers import (
    test_signer as test_signer,
)
from apps.api.drawings.tests._helpers import Scene, make_scene
from apps.api.drawings.tests._helpers import sync_bus_reset as sync_bus_reset
from apps.api.projects.tests.test_routes_common import headers_of
from packages.db.models.drawings import UploadRow
from packages.db.models.floors import FloorRow
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.drawings import (
    make_complete_upload,
    make_drawing,
    make_upload,
    png_bytes,
)
from packages.testing.factories.floors import make_floor
from packages.testing.fixtures.clock import FakeClock

PNG = png_bytes(40, 30)


def latest_path(project_id: str) -> str:
    """Đường N7 của một dự án."""
    return f"/api/projects/{project_id}/drawings/uploads/latest"


async def _tick(db: AsyncSession, clock: FakeClock) -> None:
    """Đóng giao dịch mồi và đẩy đồng hồ: hai lượt mồi liền nhau phải khác `created_at`."""
    await db.commit()
    clock.advance(timedelta(seconds=1))


async def _uploaded_run(
    db: AsyncSession, storage: LocalDiskStorage, scene: Scene, clock: FakeClock, *, floor: FloorRow | None = None
) -> UploadRow:
    """Một lượt tải `complete` đã có lượt chạy — ứng viên "mới nhất" của tầng."""
    upload = await make_complete_upload(db, storage, project=scene.project, floor=floor or scene.floor, data=PNG)
    await start_run(db, upload_id=upload.id, clock=clock)
    await _tick(db, clock)
    return upload


async def test_drawings_list_latest_uploads__C01(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Hai tầng có lượt tải → hai mục theo `Floor.order`, mục có bản vẽ kèm `sourceImageUrl`."""
    scene = await make_scene(db_session)
    upper = await make_floor(db_session, project=scene.project, order=1)
    await _tick(db_session, fake_clock)
    first = await _uploaded_run(db_session, local_storage, scene, fake_clock)
    await make_drawing(db_session, local_storage, upload=first, png=PNG)
    second = await _uploaded_run(db_session, local_storage, scene, fake_clock, floor=upper)

    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id, upper.level_id])
    assert response.status_code == 200
    body = response.json()
    assert [item["floorId"] for item in body["items"]] == [scene.floor.level_id, upper.level_id]
    assert [item["uploadId"] for item in body["items"]] == [first.id, second.id]
    assert body["items"][0]["floorName"] == scene.floor.name
    assert body["items"][0]["sourceImageUrl"]
    assert "nextCursor" not in body


async def test_drawings_list_latest_uploads__C06(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Người ngoài dự án (vai `admin` hệ thống) → 404 `resource:"project"` (K08)."""
    scene = await make_scene(db_session)
    outsider = await make_scene(db_session)
    await _uploaded_run(db_session, local_storage, scene, fake_clock)

    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(outsider.user, role="admin"))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_drawings_list_latest_uploads__C08(
    db_session: AsyncSession, api_client: AsyncClient, fake_clock: FakeClock
) -> None:
    """Dự án không tồn tại → 404: N7 không phân biệt "chưa từng có" với "không phải của bạn"."""
    scene = await make_scene(db_session)
    await _tick(db_session, fake_clock)
    response = await api_client.get(latest_path("prj_" + "0" * 26), headers=headers_of(scene.user))
    assert response.status_code == 404


async def test_drawings_list_latest_uploads__C15_empty(
    db_session: AsyncSession, api_client: AsyncClient, fake_clock: FakeClock
) -> None:
    """Dự án chưa tầng nào tải gì → `items` rỗng, không `nextCursor`."""
    scene = await make_scene(db_session)
    await _tick(db_session, fake_clock)
    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_drawings_list_latest_uploads__C15_single(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Một tầng, một lượt tải → đúng một mục và hết trang."""
    scene = await make_scene(db_session)
    upload = await _uploaded_run(db_session, local_storage, scene, fake_clock)
    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    body = response.json()
    assert [item["uploadId"] for item in body["items"]] == [upload.id]
    assert "nextCursor" not in body


async def test_drawings_list_latest_uploads__C15(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Ba tầng với `limit=2` → trang đầu hai mục + `nextCursor`, trang sau mục còn lại."""
    scene = await make_scene(db_session)
    floors = [scene.floor]
    for order in (1, 2):
        floors.append(await make_floor(db_session, project=scene.project, order=order))
    await _tick(db_session, fake_clock)
    uploads = [await _uploaded_run(db_session, local_storage, scene, fake_clock, floor=floor) for floor in floors]

    first = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user), params={"limit": 2})
    attach_floor_order(first, [floor.level_id for floor in floors])
    assert first.status_code == 200
    page_one = first.json()
    assert [item["uploadId"] for item in page_one["items"]] == [uploads[0].id, uploads[1].id]
    assert page_one["nextCursor"]

    second = await api_client.get(
        latest_path(scene.project.id),
        headers=headers_of(scene.user),
        params={"limit": 2, "cursor": page_one["nextCursor"]},
    )
    attach_floor_order(second, [floor.level_id for floor in floors])
    assert second.status_code == 200
    page_two = second.json()
    assert [item["uploadId"] for item in page_two["items"]] == [uploads[2].id]
    assert "nextCursor" not in page_two


async def test_drawings_list_latest_uploads__C15_foreign_cursor(
    db_session: AsyncSession, api_client: AsyncClient, fake_clock: FakeClock
) -> None:
    """Cursor của dự án khác → 422 `CURSOR_INVALID` (cursor ký kèm `project`)."""
    scene = await make_scene(db_session)
    await _tick(db_session, fake_clock)
    response = await api_client.get(
        latest_path(scene.project.id), headers=headers_of(scene.user), params={"cursor": "khong-phai-cursor"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "CURSOR_INVALID"


async def test_drawings_list_latest_uploads__C17(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Tầng chưa có bản vẽ đang dùng → `sourceImageUrl` **vắng khoá** (W2)."""
    scene = await make_scene(db_session)
    await _uploaded_run(db_session, local_storage, scene, fake_clock)
    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    assert "sourceImageUrl" not in response.json()["items"][0]


async def test_drawings_list_latest_uploads__C17_other_upload_drawing(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Bản vẽ đang dùng thuộc lượt tải **cũ** → mục của lượt mới không có `sourceImageUrl`."""
    scene = await make_scene(db_session)
    old = await _uploaded_run(db_session, local_storage, scene, fake_clock)
    await make_drawing(db_session, local_storage, upload=old, png=PNG)
    fresh = await _uploaded_run(db_session, local_storage, scene, fake_clock)

    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["uploadId"] == fresh.id
    assert "sourceImageUrl" not in item


async def test_drawings_list_latest_uploads__C01_abandoned_init_is_ignored(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Init bỏ dở (chưa có lượt chạy) không che lượt tải đang chạy pipeline."""
    scene = await make_scene(db_session)
    running = await _uploaded_run(db_session, local_storage, scene, fake_clock)
    await make_upload(db_session, project=scene.project, floor=scene.floor)
    await _tick(db_session, fake_clock)

    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    assert [item["uploadId"] for item in response.json()["items"]] == [running.id]


async def test_drawings_list_latest_uploads__C01_restart_on_older_upload(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    api_client: AsyncClient,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """U2 hỏng rồi người dùng chạy lại pipeline trên U1 → mục của tầng là U1."""
    scene = await make_scene(db_session)
    first = await _uploaded_run(db_session, local_storage, scene, fake_clock)
    second = await make_complete_upload(db_session, local_storage, project=scene.project, floor=scene.floor, data=PNG)
    failing = await start_run(db_session, upload_id=second.id, clock=fake_clock)
    await _tick(db_session, fake_clock)
    await record_step(
        db_session, run_id=failing.id, step=FIRST_STEP, status="failed", clock=fake_clock, error_code="BOOM"
    )
    await _tick(db_session, fake_clock)
    await start_run(db_session, upload_id=first.id, clock=fake_clock)
    await _tick(db_session, fake_clock)

    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    assert [item["uploadId"] for item in response.json()["items"]] == [first.id]


async def test_drawings_list_latest_uploads__C15_rejected_upload_without_run(
    db_session: AsyncSession, api_client: AsyncClient, fake_clock: FakeClock
) -> None:
    """Tầng chỉ có lượt tải `rejected` và chưa lượt chạy nào → không có mục nào."""
    scene = await make_scene(db_session)
    await make_upload(
        db_session, project=scene.project, floor=scene.floor, status="rejected", rejected_code="FILE_CORRUPT"
    )
    await _tick(db_session, fake_clock)
    response = await api_client.get(latest_path(scene.project.id), headers=headers_of(scene.user))
    attach_floor_order(response, [scene.floor.level_id])
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.parametrize("limit", [0, 201])
async def test_drawings_list_latest_uploads__C15_limit_out_of_range(
    db_session: AsyncSession, api_client: AsyncClient, fake_clock: FakeClock, limit: int
) -> None:
    """`limit` ngoài `1…200` → 422 `VALIDATION` ngay ở dependency (`page_params`)."""
    scene = await make_scene(db_session)
    await _tick(db_session, fake_clock)
    response = await api_client.get(
        latest_path(scene.project.id), headers=headers_of(scene.user), params={"limit": limit}
    )
    assert response.status_code == 422
