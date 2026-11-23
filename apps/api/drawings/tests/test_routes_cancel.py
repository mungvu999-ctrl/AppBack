"""Case của N53 `drawings_cancel_upload` (K-24; G +ngoài: C01 C06 C07 C08 C17, C10; C16 miễn ở `cases.toml`).

Lượt sống dựng bằng #5-#7 thật (dịch vụ thật, lượt `pending` mà #7 mở; không cần worker). Cờ huỷ
của worker `ml` đọc trên Redis thật (DB `safe`), như hàng `pipeline.cpu` của test #7/N51.
"""

from collections.abc import Iterator
from typing import Final

import httpx
import pytest
from redis import Redis as SyncRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.tests._helpers import Scene
from apps.api.drawings.tests._helpers import sync_bus_reset as sync_bus_reset
from apps.api.drawings.tests._upload_helpers import (
    Stage,
    headers_of,
    make_stage,
    upload_png,
    upload_through,
)
from packages.db.models.drawings import PipelineRunRow
from packages.db.models.projects import ProjectFloorSummary
from packages.messaging import safe_redis_sync
from packages.messaging.run_cancel import cancel_key, reset_cancel_client
from packages.messaging.settings import get_messaging_settings
from packages.testing.factories.auth import make_user
from packages.testing.factories.floors import make_floor
from packages.testing.factories.projects import make_project

MISSING_UPLOAD: Final = "upl_00000000000000000000000000"
CANCELLED: Final = "PIPELINE_CANCELLED"


def cancel_path(project_id: str, upload_id: str) -> str:
    """Đường N53."""
    return f"/api/projects/{project_id}/drawings/uploads/{upload_id}/cancel"


@pytest.fixture
def flags(messaging_env: None) -> Iterator[SyncRedis]:
    """Client DB `safe` để đọc cờ huỷ; client của `run_cancel` dựng lại theo URL của lượt test."""
    reset_cancel_client()
    client = safe_redis_sync(get_messaging_settings())
    yield client
    client.close()
    reset_cancel_client()


async def _runs(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str) -> list[tuple[str, str, str | None]]:
    """`(id, status, error_code)` các lượt chạy của lượt tải, cũ trước."""
    async with sessionmaker() as session:
        stmt = (
            select(PipelineRunRow.id, PipelineRunRow.status, PipelineRunRow.error_code)
            .where(PipelineRunRow.upload_id == upload_id)
            .order_by(PipelineRunRow.created_at, PipelineRunRow.id)
        )
        return [tuple(row) for row in (await session.execute(stmt)).all()]


async def _set_runs(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str, status: str) -> None:
    """Đặt `status` mọi lượt chạy của lượt tải (giả bước đầu đã `running`, hay lượt đã `completed`)."""
    async with sessionmaker() as session:
        await session.execute(update(PipelineRunRow).where(PipelineRunRow.upload_id == upload_id).values(status=status))
        await session.commit()


async def _summary_state(sessionmaker: async_sessionmaker[AsyncSession], stage: Stage) -> tuple[bool, str]:
    """`(has_upload, pipeline_state)` của dòng đếm tầng."""
    async with sessionmaker() as session:
        stmt = select(ProjectFloorSummary.has_upload, ProjectFloorSummary.pipeline_state).where(
            ProjectFloorSummary.project_id == stage.project_id,
            ProjectFloorSummary.floor_level_id == stage.level_id,
        )
        has_upload, state = (await session.execute(stmt)).one()
        return has_upload, state


async def _live_upload(client: httpx.AsyncClient, stage: Stage) -> str:
    """#5-#7 cho một PNG: lượt tải `complete` với một lượt chạy `pending`; trả `upload_id`."""
    upload_id, response = await upload_through(client, stage, upload_png(900))
    assert response.status_code == 200, response.text
    return upload_id


async def test_drawings_cancel_upload__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Lượt `running` → 200 `failed` `PIPELINE_CANCELLED`; cờ huỷ cho `ml` đã cắm; tầng không kẹt "đang xử lý"."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)
    await _set_runs(db_sessionmaker, upload_id, "running")

    response = await api_client.post(cancel_path(stage.project_id, upload_id), headers=stage.headers)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert (wire["id"], wire["status"], wire["error"]) == (upload_id, "failed", CANCELLED)
    [(run_id, status, code)] = await _runs(db_sessionmaker, upload_id)
    assert (status, code) == ("failed", CANCELLED)
    assert flags.exists(cancel_key(run_id)) == 1
    assert (flags.ttl(cancel_key(run_id)) or 0) > 0
    assert await _summary_state(db_sessionmaker, stage) == (False, "none")


async def test_drawings_cancel_upload__C17(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Lượt huỷ từ `pending`: `endedAt`, `startedAt`, `queueAhead` **vắng khoá** (W2, K33), `error` có mặt."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)

    response = await api_client.post(cancel_path(stage.project_id, upload_id), headers=stage.headers)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert {"startedAt", "endedAt", "queueAhead"}.isdisjoint(wire)
    assert wire["error"] == CANCELLED


async def test_drawings_cancel_upload__C06(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Người ngoài dự án (kể cả admin hệ thống) → 404 `resource:"project"`, lượt vẫn sống."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await api_client.post(cancel_path(stage.project_id, upload_id), headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"
    assert [status for _, status, _ in await _runs(db_sessionmaker, upload_id)] == ["pending"]


async def test_drawings_cancel_upload__C07(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Thành viên vai `viewer` (không có `floor.upload`) → 403, lượt vẫn sống."""
    owner = await make_user(db_session)
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    floor = await make_floor(db_session, project=project, order=0)
    await db_session.commit()
    stage = Stage(scene=Scene(user=owner, project=project, floor=floor), headers=headers_of(owner))
    upload_id = await _live_upload(api_client, stage)

    response = await api_client.post(cancel_path(project.id, upload_id), headers=headers_of(viewer, role="viewer"))

    assert response.status_code == 403
    assert [status for _, status, _ in await _runs(db_sessionmaker, upload_id)] == ["pending"]


async def test_drawings_cancel_upload__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Lượt tải không có → 404 `resource:"upload"`."""
    stage = await make_stage(db_session)

    response = await api_client.post(cancel_path(stage.project_id, MISSING_UPLOAD), headers=stage.headers)

    assert response.status_code == 404
    assert response.json()["resource"] == "upload"


async def test_drawings_cancel_upload__C10_same_key_replays(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Lặp `Idempotency-Key` → cùng response; vẫn đúng một lượt, đã huỷ một lần."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)

    headers = {**stage.headers, "Idempotency-Key": "k24-cancel-once"}
    first = await api_client.post(cancel_path(stage.project_id, upload_id), headers=headers)
    second = await api_client.post(cancel_path(stage.project_id, upload_id), headers=headers)

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json()
    assert [(status, code) for _, status, code in await _runs(db_sessionmaker, upload_id)] == [("failed", CANCELLED)]


async def test_drawings_cancel_upload__C10_double_click_without_key(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Bấm đôi **không** khoá → cả hai 200 cùng `Progress` (idempotent theo kết quả), không ghi lại lượt."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)

    first = await api_client.post(cancel_path(stage.project_id, upload_id), headers=stage.headers)
    second = await api_client.post(cancel_path(stage.project_id, upload_id), headers=stage.headers)

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json()
    assert [(status, code) for _, status, code in await _runs(db_sessionmaker, upload_id)] == [("failed", CANCELLED)]


async def test_cancel_leaves_a_completed_run_alone(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Bấm huỷ khi lượt vừa xong → 200 `completed`; kết quả không bị bỏ, không cắm cờ."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)
    await _set_runs(db_sessionmaker, upload_id, "completed")

    response = await api_client.post(cancel_path(stage.project_id, upload_id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    [(run_id, status, code)] = await _runs(db_sessionmaker, upload_id)
    assert (status, code) == ("completed", None)
    assert flags.exists(cancel_key(run_id)) == 0


async def test_cancel_keeps_the_floor_completed_when_an_older_model_exists(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Tầng đã có mô hình từ lượt tải trước: huỷ lượt mới → dòng đếm về `completed`, không `none`/`failed`."""
    stage = await make_stage(db_session)
    older = await _live_upload(api_client, stage)
    await _set_runs(db_sessionmaker, older, "completed")
    newer = await _live_upload(api_client, stage)

    response = await api_client.post(cancel_path(stage.project_id, newer), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert await _summary_state(db_sessionmaker, stage) == (True, "completed")


async def test_cancelled_upload_can_be_rerun_from_the_original(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    flags: SyncRedis,
    sync_bus_reset: None,
) -> None:
    """Sau huỷ, N51 "Chạy lại từ bản gốc" nhận lượt tải này (lượt mới nhất `failed`)."""
    stage = await make_stage(db_session)
    upload_id = await _live_upload(api_client, stage)
    await api_client.post(cancel_path(stage.project_id, upload_id), headers=stage.headers)

    rerun = await api_client.post(
        f"/api/projects/{stage.project_id}/drawings/uploads/{upload_id}/rerun", headers=stage.headers
    )

    assert rerun.status_code == 200, rerun.text
    assert [status for _, status, _ in await _runs(db_sessionmaker, upload_id)] == ["failed", "pending"]
