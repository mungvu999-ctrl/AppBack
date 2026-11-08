"""`state` (claim/finish/fail/supersede) và lịch `sweep_model_exports` trên Postgres thật."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.lighting_bake.tests._helpers import bake_broker as bake_broker
from apps.api.model_export import state
from apps.api.model_export.jobs import run_model_export_sweep, sweep_model_exports
from apps.api.model_export.settings import reset_model_export_settings_cache
from packages.core.clock import SystemClock
from packages.core.settings import reset_settings_cache
from packages.db.engine import create_engine, create_sessionmaker
from packages.db.settings import get_database_settings, reset_database_settings_cache
from packages.messaging.redis import SyncRedis
from packages.storage.keys import model_export_object, model_export_prefix
from packages.storage.local import LocalDiskStorage
from packages.storage.settings import reset_storage_settings_cache
from packages.testing.factories.auth import make_user
from packages.testing.factories.model_export import make_model_export
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.messaging import queued_payloads

CLOCK = SystemClock()


async def _project_id(db: AsyncSession) -> str:
    """Dự án mới đã commit."""
    project = await make_project(db, owner=await make_user(db))
    await db.commit()
    return project.id


async def _status(db: AsyncSession, export_id: str) -> tuple[str, str | None]:
    """(status, quality) đọc lại từ DB."""
    row = (await db.execute(text("SELECT status, quality FROM model_exports WHERE id = :id"), {"id": export_id})).one()
    return row.status, row.quality


async def test_claim_finish__J01(db_session: AsyncSession) -> None:
    """queued → running → done có chất lượng; finish lặp → False."""
    p = await _project_id(db_session)
    row = await make_model_export(db_session, project_id=p)

    assert await state.claim(db_session, row.id, CLOCK) == p
    assert await state.finish(db_session, row.id, CLOCK, quality="ready", report_summary={"counts": {}}, glb_bytes=5)
    assert not await state.finish(db_session, row.id, CLOCK, quality="ready", report_summary={}, glb_bytes=5)
    await db_session.commit()
    assert await _status(db_session, row.id) == ("done", "ready")


async def test_finish_after_superseded__J02(db_session: AsyncSession) -> None:
    """Lượt bị `superseded` giữa chừng: finish → False, dòng không đổi; claim → None."""
    p = await _project_id(db_session)
    row = await make_model_export(db_session, project_id=p, status="running")

    assert await state.supersede(db_session, p, CLOCK) == [row.id]
    assert not await state.finish(db_session, row.id, CLOCK, quality="ready", report_summary={}, glb_bytes=1)
    assert await state.claim(db_session, row.id, CLOCK) is None
    await db_session.commit()
    assert await _status(db_session, row.id) == ("superseded", None)


async def test_fail__J03(db_session: AsyncSession) -> None:
    """fail chỉ nhận mã đã khai; lượt đã kết thúc → False."""
    p = await _project_id(db_session)
    row = await make_model_export(db_session, project_id=p)

    with pytest.raises(ValueError, match="mã hỏng lạ"):
        await state.fail(db_session, row.id, "NOPE", CLOCK)
    with pytest.raises(ValueError, match="chất lượng lạ"):
        await state.finish(db_session, row.id, CLOCK, quality="nope", report_summary={}, glb_bytes=None)
    assert await state.fail(db_session, row.id, state.MODEL_EXPORT_TIMEOUT, CLOCK)
    assert not await state.fail(db_session, row.id, state.MODEL_EXPORT_TIMEOUT, CLOCK)


async def _put(storage: LocalDiskStorage, project_id: str, export_id: str, name: str) -> None:
    """Một object nhỏ dưới tiền tố của lượt."""
    await storage.put(model_export_object(project_id, export_id, name), b"x", content_type="text/plain", max_bytes=1)


async def _objects(storage: LocalDiskStorage, project_id: str, export_id: str) -> list[str]:
    """Khoá object còn dưới tiền tố của lượt."""
    return [info.key async for info in storage.list_prefix(model_export_prefix(project_id, export_id))]


async def test_sweep_model_exports__J06(
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    messaging_env: None,
) -> None:
    """Chốt lượt kẹt, gửi lại lượt im; dọn object bản `done` ngoài 5 bản mới nhất, kể cả bản `blocked`.

    Lượt quét thứ hai không dọn lặp (`report_summary.pruned`).
    """
    p = await _project_id(db_session)
    now = CLOCK.now()
    stuck = await make_model_export(db_session, project_id=p, status="running", created_at=now - timedelta(hours=1))
    done = [
        await make_model_export(
            db_session,
            project_id=p,
            status="done",
            quality="blocked" if i == 0 else "ready",
            glb_bytes=None if i == 0 else 1,
            report_summary={"counts": {}},
            created_at=now - timedelta(minutes=30 - i),
        )
        for i in range(7)
    ]
    for row in done[:3]:
        await _put(local_storage, p, row.id, "report.json")
    other = await _project_id(db_session)
    idle = await make_model_export(db_session, project_id=other, created_at=now - timedelta(minutes=20))

    result = await run_model_export_sweep(db_sessionmaker, local_storage, CLOCK)
    again = await run_model_export_sweep(db_sessionmaker, local_storage, CLOCK)

    assert (result.failed, result.resent, result.pruned) == (1, 1, 2)
    assert again.pruned == 0
    assert await _status(db_session, stuck.id) == ("failed", None)
    assert await _status(db_session, idle.id) == ("queued", None)
    assert await _objects(local_storage, p, done[0].id) == []
    assert await _objects(local_storage, p, done[1].id) == []
    assert await _objects(local_storage, p, done[2].id) != []
    glb = await db_session.execute(text("SELECT glb_bytes FROM model_exports WHERE id = :id"), {"id": done[1].id})
    assert glb.scalar_one() is None


async def test_sweep_model_exports__superseded_snapshot(
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    messaging_env: None,
) -> None:
    """Lượt bị thay khi còn `queued` để lại `snapshot.json`: quét dọn sau trần `running`, một lần.

    Lượt vừa bị thay chưa dọn.
    """
    p = await _project_id(db_session)
    an_hour_ago = CLOCK.now() - timedelta(hours=1)
    old = await make_model_export(db_session, project_id=p, status="superseded", created_at=an_hour_ago)
    fresh = await make_model_export(db_session, project_id=p, status="superseded")
    for row in (old, fresh):
        await _put(local_storage, p, row.id, "snapshot.json")

    result = await run_model_export_sweep(db_sessionmaker, local_storage, CLOCK)
    again = await run_model_export_sweep(db_sessionmaker, local_storage, CLOCK)

    assert (result.pruned, again.pruned) == (1, 0)
    assert await _objects(local_storage, p, old.id) == []
    assert await _objects(local_storage, p, fresh.id) != []
    assert await _status(db_session, old.id) == ("superseded", None)


@pytest.fixture
def process_env(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, storage_env: None) -> Iterator[None]:
    """Môi trường của tiến trình beat: DB test riêng, kho local `tmp_path` (như test khói LB-01)."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))
    caches = (
        reset_settings_cache,
        reset_database_settings_cache,
        reset_model_export_settings_cache,
        reset_storage_settings_cache,
    )
    for reset in caches:
        reset()
    yield
    for reset in caches:
        reset()


async def _seed_old_queued() -> str:
    """Một lượt `queued` tạo 20 phút trước, ghi vào DB của môi trường tiến trình; trả id."""
    engine = create_engine(get_database_settings())
    try:
        async with create_sessionmaker(engine)() as db:
            project_id = await _project_id(db)
            row = await make_model_export(db, project_id=project_id, created_at=CLOCK.now() - timedelta(minutes=20))
            return row.id
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("process_env", "celery_test_app")
def test_sweep_model_exports__J01(bake_broker: SyncRedis) -> None:
    """Hàm lịch thật (đồng bộ, như test khói LB-01): lượt `queued` im được gửi lại lên hàng `bake`."""
    export_id = asyncio.run(_seed_old_queued())

    sweep_model_exports()
    reset_database_settings_cache()

    assert [p["export_id"] for p in queued_payloads(bake_broker, "bake")] == [export_id]
