"""N42 `POST …/lighting-bakes`, N43 `GET …/latest`, N44 `GET …/files/{file_name}` (LB-01, CASE §2.3).

Postgres, kho local và broker thật (K23). Case chung C04 C05 C12 C13 C25 ở `test_common.py`.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.lighting_bake.settings import reset_lighting_bake_settings_cache
from apps.api.lighting_bake.tests._helpers import (
    BAKE_QUEUE,
    MISSING_BAKE,
    MISSING_PROJECT,
    availability_path,
    bakes_path,
    file_path,
    glb_bytes,
    latest_path,
)
from apps.api.lighting_bake.tests._helpers import bake_broker as bake_broker
from apps.api.projects.tests.test_routes_common import headers_of
from packages.db.models.auth import User
from packages.db.models.projects import Project
from packages.messaging.payloads.lighting_bake import LIGHTING_BAKE_INPUT
from packages.messaging.redis import SyncRedis
from packages.storage.keys import lighting_bake_object
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.lighting_bake import make_lighting_bake
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.messaging import queued_payloads


@pytest.fixture(autouse=True)
def bake_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mặc định bật `LIGHTING_BAKE_ENABLED`; test tắt đặt lại bằng `monkeypatch` riêng."""
    monkeypatch.setenv("LIGHTING_BAKE_ENABLED", "true")
    reset_lighting_bake_settings_cache()
    yield
    monkeypatch.undo()
    reset_lighting_bake_settings_cache()


async def _project(db: AsyncSession, *, viewer: bool = False) -> tuple[User, Project, list[User]]:
    """Chủ dự án (và một viewer nếu cần) đã commit — route đọc bằng session khác."""
    owner = await make_user(db)
    members = [await make_user(db, role="viewer")] if viewer else []
    project = await make_project(db, owner=owner, members=members)
    await db.commit()
    return owner, project, members


async def _outsider(db: AsyncSession) -> User:
    """Người dùng không thuộc dự án nào, đã commit."""
    user = await make_user(db)
    await db.commit()
    return user


async def _statuses(db: AsyncSession, project_id: str) -> list[str]:
    """Trạng thái mọi lượt của dự án, đọc lại từ DB."""
    rows = await db.execute(text("SELECT status FROM lighting_bakes WHERE project_id = :p"), {"p": project_id})
    return list(rows.scalars())


async def _stored(storage: LocalDiskStorage, project_id: str) -> list[str]:
    """Khoá mọi object nướng ánh sáng của dự án."""
    return [info.key async for info in storage.list_prefix(f"projects/{project_id}/lighting-bakes/")]


async def test_lighting_bake_start_bake__C01(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage, bake_broker: SyncRedis
) -> None:
    """GLB hợp lệ → 202 `queued`, dòng trong DB, `input.glb` trong kho, task trên hàng `bake`."""
    owner, project, _ = await _project(db_session)

    response = await api_client.post(bakes_path(project.id), content=glb_bytes(), headers=headers_of(owner))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["id"].startswith("bak_")
    assert await _statuses(db_session, project.id) == ["queued"]
    assert await local_storage.stat(lighting_bake_object(project.id, body["id"], LIGHTING_BAKE_INPUT)) is not None
    assert [p["bake_id"] for p in queued_payloads(bake_broker, BAKE_QUEUE)] == [body["id"]]


async def test_lighting_bake_start_bake__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Admin hệ thống không là thành viên → 404 `resource:"project"` (K08)."""
    _, project, _ = await _project(db_session)
    outsider = await _outsider(db_session)

    response = await api_client.post(
        bakes_path(project.id), content=glb_bytes(), headers=headers_of(outsider, role="admin")
    )

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_lighting_bake_start_bake__C07(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Thành viên `viewer` (không có `layer.edit`) → 403, không mở lượt nào."""
    _, project, members = await _project(db_session, viewer=True)

    response = await api_client.post(
        bakes_path(project.id), content=glb_bytes(), headers=headers_of(members[0], role="viewer")
    )

    assert response.status_code == 403
    assert await _statuses(db_session, project.id) == []


async def test_lighting_bake_start_bake__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Dự án không tồn tại → 404."""
    owner, _, _ = await _project(db_session)
    response = await api_client.post(bakes_path(MISSING_PROJECT), content=glb_bytes(), headers=headers_of(owner))
    assert response.status_code == 404


async def test_lighting_bake_start_bake__C14(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage, bake_broker: SyncRedis
) -> None:
    """Hai lượt bắt đầu song song → đúng một 202, một 409; một dòng, một object, một task."""
    owner, project, _ = await _project(db_session)

    responses = await asyncio.gather(
        *(api_client.post(bakes_path(project.id), content=glb_bytes(), headers=headers_of(owner)) for _ in range(2))
    )

    assert sorted(r.status_code for r in responses) == [202, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["code"] == "LIGHTING_BAKE_IN_PROGRESS"
    assert await _statuses(db_session, project.id) == ["queued"]
    assert len(await _stored(local_storage, project.id)) == 1
    assert len(queued_payloads(bake_broker, BAKE_QUEUE)) == 1


async def test_lighting_bake_start_bake__C17(
    db_session: AsyncSession, api_client: AsyncClient, bake_broker: SyncRedis
) -> None:
    """Lượt vừa mở: `startedAt`, `endedAt`, `failureCode` **vắng** khoá."""
    owner, project, _ = await _project(db_session)
    response = await api_client.post(bakes_path(project.id), content=glb_bytes(), headers=headers_of(owner))
    assert response.status_code == 202, response.text
    assert set(response.json()) == {"id", "status", "createdAt"}


async def test_start_bake_refuses_while_a_bake_is_active(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Dự án đang có lượt `running` → 409 `LIGHTING_BAKE_IN_PROGRESS`, không thêm dòng."""
    owner, project, _ = await _project(db_session)
    await make_lighting_bake(db_session, project_id=project.id, status="running")

    response = await api_client.post(bakes_path(project.id), content=glb_bytes(), headers=headers_of(owner))

    assert response.status_code == 409
    assert response.json()["code"] == "LIGHTING_BAKE_IN_PROGRESS"
    assert await _statuses(db_session, project.id) == ["running"]


async def test_start_bake_rejects_non_glb_and_deletes_the_object(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage
) -> None:
    """Thân không phải GLB → 422 `FILE_TYPE_MISMATCH`, không dòng, không object sót."""
    owner, project, _ = await _project(db_session)

    response = await api_client.post(bakes_path(project.id), content=b"not a glb at all", headers=headers_of(owner))

    assert response.status_code == 422
    assert response.json()["code"] == "FILE_TYPE_MISMATCH"
    assert await _statuses(db_session, project.id) == []
    assert await _stored(local_storage, project.id) == []


async def test_lighting_bake_read_latest__C01(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Hai lượt → trả lượt có `created_at` mới hơn."""
    owner, project, _ = await _project(db_session)
    older = datetime.now(UTC) - timedelta(minutes=5)
    await make_lighting_bake(db_session, project_id=project.id, status="failed", created_at=older)
    newer = await make_lighting_bake(db_session, project_id=project.id, status="running")

    response = await api_client.get(latest_path(project.id), headers=headers_of(owner))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == newer.id
    assert body["status"] == "running"


async def test_lighting_bake_read_latest__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Người ngoài (admin hệ thống) → 404 `resource:"project"`."""
    _, project, _ = await _project(db_session)
    await make_lighting_bake(db_session, project_id=project.id)
    outsider = await _outsider(db_session)
    response = await api_client.get(latest_path(project.id), headers=headers_of(outsider, role="admin"))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_lighting_bake_read_latest__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Dự án chưa nướng lần nào → 404 `resource:"lightingBake"`; dự án không có → 404."""
    owner, project, _ = await _project(db_session)
    empty = await api_client.get(latest_path(project.id), headers=headers_of(owner))
    missing = await api_client.get(latest_path(MISSING_PROJECT), headers=headers_of(owner))
    assert empty.status_code == 404
    assert empty.json()["resource"] == "lightingBake"
    assert missing.status_code == 404


async def test_lighting_bake_read_latest__C17(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Lượt `queued`: `startedAt`, `endedAt`, `failureCode` vắng khoá."""
    owner, project, _ = await _project(db_session)
    await make_lighting_bake(db_session, project_id=project.id)
    response = await api_client.get(latest_path(project.id), headers=headers_of(owner))
    assert response.status_code == 200
    assert set(response.json()) == {"id", "status", "createdAt"}


async def test_lighting_bake_download_file__C01(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage
) -> None:
    """Lượt `done` → byte của tệp, `attachment`, `Cache-Control: private, max-age=600`."""
    owner, project, _ = await _project(db_session)
    bake = await make_lighting_bake(db_session, project_id=project.id, status="done")
    data = glb_bytes()
    key = lighting_bake_object(project.id, bake.id, "baked.glb")
    await local_storage.put(key, data, content_type="model/gltf-binary", max_bytes=len(data))

    response = await api_client.get(file_path(project.id, bake.id, "baked.glb"), headers=headers_of(owner))

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["cache-control"] == "private, max-age=600"
    assert response.headers["content-disposition"].startswith("attachment")


async def test_lighting_bake_download_file__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Người ngoài → 404 `resource:"project"`."""
    _, project, _ = await _project(db_session)
    bake = await make_lighting_bake(db_session, project_id=project.id, status="done")
    outsider = await _outsider(db_session)
    response = await api_client.get(
        file_path(project.id, bake.id, "baked.glb"), headers=headers_of(outsider, role="admin")
    )
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_lighting_bake_download_file__C08(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage
) -> None:
    """404 `lightingBake`: id không có, lượt chưa `done`, `input.glb`, tệp `done` mà object vắng."""
    owner, project, _ = await _project(db_session)
    running = await make_lighting_bake(db_session, project_id=project.id, status="running")
    done = await make_lighting_bake(db_session, project_id=project.id, status="done")
    data = glb_bytes()
    key = lighting_bake_object(project.id, done.id, LIGHTING_BAKE_INPUT)
    await local_storage.put(key, data, content_type="model/gltf-binary", max_bytes=len(data))
    paths = [
        file_path(project.id, MISSING_BAKE, "baked.glb"),
        file_path(project.id, running.id, "baked.glb"),
        file_path(project.id, done.id, LIGHTING_BAKE_INPUT),
        file_path(project.id, done.id, "lightmap.png"),
    ]
    for path in paths:
        response = await api_client.get(path, headers=headers_of(owner))
        assert response.status_code == 404, path
        assert response.json()["resource"] == "lightingBake"


async def test_download_file_refuses_a_bake_of_another_project(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage
) -> None:
    """IDOR: chủ dự án B đưa id lượt `done` của dự án A vào đường của B → 404 `lightingBake`.

    Đặt cả object dưới tiền tố của B với id lượt của A: chỉ bộ lọc `project_id` của truy vấn chặn được.
    """
    owner_a, project_a, _ = await _project(db_session)
    owner_b, project_b, _ = await _project(db_session)
    bake_a = await make_lighting_bake(db_session, project_id=project_a.id, status="done")
    data = glb_bytes()
    for project_id in (project_a.id, project_b.id):
        key = lighting_bake_object(project_id, bake_a.id, "baked.glb")
        await local_storage.put(key, data, content_type="model/gltf-binary", max_bytes=len(data))

    own = await api_client.get(file_path(project_a.id, bake_a.id, "baked.glb"), headers=headers_of(owner_a))
    cross = await api_client.get(file_path(project_b.id, bake_a.id, "baked.glb"), headers=headers_of(owner_b))

    assert own.status_code == 200
    assert cross.status_code == 404
    assert cross.json()["resource"] == "lightingBake"


async def test_start_bake_refuses_a_body_over_the_upload_ceiling(
    db_session: AsyncSession,
    api_client: AsyncClient,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thân vượt trần `storage.put` của dịch vụ → 413 `PAYLOAD_TOO_LARGE`, không dòng, không object dở.

    Trần thật 128 MiB hạ xuống 1 KiB qua biến môi trường để không gửi 128 MiB trong test; trần thân
    của middleware (cùng 128 MiB) đã có `test_common__C12[lighting_bake_start_bake]`.
    """
    owner, project, _ = await _project(db_session)
    monkeypatch.setenv("LIGHTING_BAKE_UPLOAD_MAX_BYTES", "1024")
    reset_lighting_bake_settings_cache()
    try:
        response = await api_client.post(bakes_path(project.id), content=glb_bytes(4096), headers=headers_of(owner))
    finally:
        monkeypatch.undo()
        reset_lighting_bake_settings_cache()

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert await _statuses(db_session, project.id) == []
    assert await _stored(local_storage, project.id) == []


async def test_lighting_bake_read_availability__C01(
    db_session: AsyncSession, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cờ bật → `{enabled: true}`; cờ tắt (mặc định) → `{enabled: false}`."""
    owner, project, _ = await _project(db_session)
    on = await api_client.get(availability_path(project.id), headers=headers_of(owner))
    monkeypatch.setenv("LIGHTING_BAKE_ENABLED", "false")
    reset_lighting_bake_settings_cache()
    off = await api_client.get(availability_path(project.id), headers=headers_of(owner))
    assert on.status_code == 200
    assert on.json() == {"enabled": True}
    assert off.json() == {"enabled": False}


async def test_lighting_bake_read_availability__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Người ngoài (admin hệ thống) → 404 `resource:"project"`."""
    _, project, _ = await _project(db_session)
    outsider = await _outsider(db_session)
    response = await api_client.get(availability_path(project.id), headers=headers_of(outsider, role="admin"))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_lighting_bake_read_availability__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Dự án không có → 404."""
    owner, _, _ = await _project(db_session)
    response = await api_client.get(availability_path(MISSING_PROJECT), headers=headers_of(owner))
    assert response.status_code == 404


async def test_lighting_bake_read_availability__C17(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Thân chỉ có khoá `enabled`."""
    owner, project, _ = await _project(db_session)
    response = await api_client.get(availability_path(project.id), headers=headers_of(owner))
    assert set(response.json()) == {"enabled"}


async def test_start_bake_refused_when_disabled(
    db_session: AsyncSession,
    api_client: AsyncClient,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cờ tắt → 503 `LIGHTING_BAKE_DISABLED`, không dòng, không object."""
    owner, project, _ = await _project(db_session)
    monkeypatch.setenv("LIGHTING_BAKE_ENABLED", "false")
    reset_lighting_bake_settings_cache()
    response = await api_client.post(bakes_path(project.id), content=glb_bytes(), headers=headers_of(owner))
    assert response.status_code == 503
    assert response.json()["code"] == "LIGHTING_BAKE_DISABLED"
    assert await _statuses(db_session, project.id) == []
    assert await _stored(local_storage, project.id) == []
