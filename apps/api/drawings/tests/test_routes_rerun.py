"""Case của N51 `drawings_rerun_upload` (G +ngoài: C01 C06 C07 C08 C17, C10; C16 miễn ở `cases.toml`).

Lượt hỏng dựng bằng cách đánh `failed` lượt chạy mà #7 vừa mở (không cần worker). C10 đếm
thông điệp `pipeline.cpu` trên Redis thật như test của #7.
"""

from collections.abc import Iterator
from typing import Final

import httpx
import pytest
from redis import Redis as SyncRedis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.tests._helpers import Scene
from apps.api.drawings.tests._helpers import sync_bus_reset as sync_bus_reset
from apps.api.drawings.tests._upload_helpers import (
    Stage,
    headers_of,
    init_body,
    init_path,
    make_stage,
    upload_png,
    upload_through,
)
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.messaging import broker_redis_sync
from packages.messaging.settings import get_messaging_settings
from packages.storage.port import ObjectStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.floors import make_floor
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.messaging import queued_payloads

CPU_QUEUE: Final = "pipeline.cpu"
MISSING_UPLOAD: Final = "upl_00000000000000000000000000"


def rerun_path(project_id: str, upload_id: str) -> str:
    """Đường N51."""
    return f"/api/projects/{project_id}/drawings/uploads/{upload_id}/rerun"


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Client Redis của broker; hàng `pipeline.cpu` dọn trước và sau vì broker là session-scope."""
    client = broker_redis_sync(get_messaging_settings())
    client.delete(CPU_QUEUE)
    yield client
    client.delete(CPU_QUEUE)
    client.close()


async def _fail_runs(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str, status: str = "failed") -> None:
    """Đánh mọi lượt chạy của lượt tải thành `status` (mặc định `failed` `PIPELINE_STALLED`)."""
    async with sessionmaker() as session:
        values: dict[str, object] = {"status": status}
        if status == "failed":
            values["error_code"] = "PIPELINE_STALLED"
        await session.execute(update(PipelineRunRow).where(PipelineRunRow.upload_id == upload_id).values(**values))
        await session.commit()


async def _runs(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str) -> list[str]:
    """`status` các lượt chạy của lượt tải, cũ trước."""
    async with sessionmaker() as session:
        stmt = (
            select(PipelineRunRow.status)
            .where(PipelineRunRow.upload_id == upload_id)
            .order_by(PipelineRunRow.created_at, PipelineRunRow.id)
        )
        return list((await session.execute(stmt)).scalars())


async def _failed_upload(
    client: httpx.AsyncClient, stage: Stage, sessionmaker: async_sessionmaker[AsyncSession]
) -> str:
    """#5-#7 cho một PNG rồi đánh hỏng lượt chạy; trả `upload_id`."""
    upload_id, response = await upload_through(client, stage, upload_png(900))
    assert response.status_code == 200, response.text
    await _fail_runs(sessionmaker, upload_id)
    return upload_id


async def test_drawings_rerun_upload__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Lượt hỏng → 200 `Progress` `pending`; lượt cũ giữ `failed`, có đúng một lượt mới."""
    stage = await make_stage(db_session)
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)

    response = await api_client.post(rerun_path(stage.project_id, upload_id), headers=stage.headers)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire.pop("queueAhead") >= 0
    assert wire == {"id": upload_id, "status": "pending", "step": "preprocess", "progressPercent": 0}
    assert await _runs(db_sessionmaker, upload_id) == ["failed", "pending"]


async def test_drawings_rerun_upload__C17(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Lượt mới `pending` → `startedAt`, `endedAt`, `error` **vắng khoá** (W2), không mang lỗi của lượt cũ."""
    stage = await make_stage(db_session)
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)

    response = await api_client.post(rerun_path(stage.project_id, upload_id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert {"startedAt", "endedAt", "error"}.isdisjoint(response.json())


async def test_drawings_rerun_upload__C06(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Người ngoài dự án (kể cả admin hệ thống) → 404 `resource:"project"`."""
    stage = await make_stage(db_session)
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await api_client.post(rerun_path(stage.project_id, upload_id), headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"
    assert await _runs(db_sessionmaker, upload_id) == ["failed"]


async def test_drawings_rerun_upload__C07(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Thành viên vai `viewer` (không có `floor.upload`) → 403, không mở lượt."""
    owner = await make_user(db_session)
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    stage = Stage(scene=Scene(user=owner, project=project, floor=floor), headers=headers_of(owner))
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)

    response = await api_client.post(rerun_path(project.id, upload_id), headers=headers_of(viewer, role="viewer"))

    assert response.status_code == 403
    assert await _runs(db_sessionmaker, upload_id) == ["failed"]


async def test_drawings_rerun_upload__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Lượt tải không có → 404 `resource:"upload"`."""
    stage = await make_stage(db_session)

    response = await api_client.post(rerun_path(stage.project_id, MISSING_UPLOAD), headers=stage.headers)

    assert response.status_code == 404
    assert response.json()["resource"] == "upload"


async def test_drawings_rerun_upload__C10_queue_once(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Lặp `Idempotency-Key` → cùng response và **một** thông điệp `pipeline.cpu` mới."""
    stage = await make_stage(db_session)
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)
    broker.delete(CPU_QUEUE)

    headers = {**stage.headers, "Idempotency-Key": "k06-rerun-once"}
    first = await api_client.post(rerun_path(stage.project_id, upload_id), headers=headers)
    second = await api_client.post(rerun_path(stage.project_id, upload_id), headers=headers)

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json()
    assert len(queued_payloads(broker, CPU_QUEUE)) == 1


async def test_drawings_rerun_upload__C10_no_key_replay(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Bấm đôi **không** khoá → lượt sau 409 `PIPELINE_RUNNING`, vẫn đúng một thông điệp."""
    stage = await make_stage(db_session)
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)
    broker.delete(CPU_QUEUE)

    first = await api_client.post(rerun_path(stage.project_id, upload_id), headers=stage.headers)
    second = await api_client.post(rerun_path(stage.project_id, upload_id), headers=stage.headers)

    assert first.status_code == 200, first.text
    assert (second.status_code, second.json()["code"]) == (409, "PIPELINE_RUNNING")
    assert len(queued_payloads(broker, CPU_QUEUE)) == 1
    assert await _runs(db_sessionmaker, upload_id) == ["failed", "pending"]


async def test_rerun_refuses_a_completed_run(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Lượt mới nhất `completed` → 409 `PIPELINE_NOT_FAILED`; kết quả đã có không bị thay."""
    stage = await make_stage(db_session)
    upload_id, _ = await upload_through(api_client, stage, upload_png(900))
    await _fail_runs(db_sessionmaker, upload_id, status="completed")

    response = await api_client.post(rerun_path(stage.project_id, upload_id), headers=stage.headers)

    assert (response.status_code, response.json()["code"]) == (409, "PIPELINE_NOT_FAILED")
    assert await _runs(db_sessionmaker, upload_id) == ["completed"]


async def test_rerun_refuses_an_older_upload(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Tầng đã có lượt tải mới hơn (dù hỏng) → 409 `UPLOAD_SUPERSEDED` cho bản cũ; bản mới chạy lại được."""
    stage = await make_stage(db_session)
    older = await _failed_upload(api_client, stage, db_sessionmaker)
    newer = await _failed_upload(api_client, stage, db_sessionmaker)

    refused = await api_client.post(rerun_path(stage.project_id, older), headers=stage.headers)
    accepted = await api_client.post(rerun_path(stage.project_id, newer), headers=stage.headers)

    assert (refused.status_code, refused.json()["code"]) == (409, "UPLOAD_SUPERSEDED")
    assert accepted.status_code == 200, accepted.text


async def test_rerun_refuses_an_upload_still_receiving(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Lượt tải chưa `complete` (mới #5) → 409 `UPLOAD_NOT_COMPLETE`."""
    stage = await make_stage(db_session)
    body = init_body(stage.project_id, stage.level_id, size_bytes=900)
    init = await api_client.post(init_path(stage.project_id, stage.level_id), json=body, headers=stage.headers)
    assert init.status_code == 200, init.text

    response = await api_client.post(rerun_path(stage.project_id, init.json()["id"]), headers=stage.headers)

    assert (response.status_code, response.json()["code"]) == (409, "UPLOAD_NOT_COMPLETE")


async def test_rerun_refuses_when_the_original_is_gone(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: ObjectStorage,
    sync_bus_reset: None,
) -> None:
    """`original.*` đã mất khỏi kho → 409 `UPLOAD_SOURCE_GONE`, không mở lượt."""
    stage = await make_stage(db_session)
    upload_id = await _failed_upload(api_client, stage, db_sessionmaker)
    async with db_sessionmaker() as session:
        key = (await session.execute(select(UploadRow.original_key).where(UploadRow.id == upload_id))).scalar_one()
    assert key is not None
    await local_storage.delete(key)

    response = await api_client.post(rerun_path(stage.project_id, upload_id), headers=stage.headers)

    assert (response.status_code, response.json()["code"]) == (409, "UPLOAD_SOURCE_GONE")
    async with db_sessionmaker() as session:
        count = select(func.count()).select_from(PipelineRunRow).where(PipelineRunRow.upload_id == upload_id)
        assert (await session.execute(count)).scalar_one() == 1
