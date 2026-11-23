"""`cancel_upload_runs` (K-24): khoá tầng, kết quả muộn bị bỏ, đua bấm đôi, đua với worker.

Postgres và Redis thật. Test đua mở hai session riêng từ `db_sessionmaker` và `commit` thật, để
khoá dòng `floors` của Postgres — không phải mã Python — là thứ xếp hàng hai bên.
"""

import asyncio
from collections.abc import Iterator

import pytest
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.runs import cancel_upload_runs, lock_run, record_step, start_run
from apps.api.drawings.tests._helpers import make_scene, read_run
from apps.api.drawings.tests._helpers import sync_bus_reset as sync_bus_reset
from packages.core.pipeline import PipelineCode
from packages.db.hooks import after_commit_idle
from packages.db.models.drawings import UploadRow
from packages.db.models.notifications import NotificationRow
from packages.messaging.run_cancel import reset_cancel_client
from packages.messaging.streams import upload_stream
from packages.testing.factories.drawings import make_upload
from packages.testing.fixtures.clock import FakeClock

CANCELLED = PipelineCode.PIPELINE_CANCELLED.value


@pytest.fixture
def cancel_client_reset(messaging_env: None) -> Iterator[None]:
    """Client cờ huỷ dựng lười theo tiến trình; Redis của test đổi URL mỗi lượt."""
    reset_cancel_client()
    yield
    reset_cancel_client()


async def _live_run(sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock) -> tuple[str, str]:
    """Một lượt tải `complete` + lượt chạy `pending` đã commit; trả `(upload_id, run_id)`."""
    async with sessionmaker() as session:
        scene = await make_scene(session)
        upload = await make_upload(session, project=scene.project, floor=scene.floor, status="complete")
        run = await start_run(session, upload_id=upload.id, clock=clock)
        await session.commit()
        await after_commit_idle(session)
        return upload.id, run.id


async def _cancel(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str, clock: FakeClock) -> tuple[str, ...]:
    """Một lời huỷ trong session riêng, commit thật."""
    async with sessionmaker() as session:
        ids = await cancel_upload_runs(session, upload_id=upload_id, clock=clock)
        await session.commit()
        await after_commit_idle(session)
        return ids


async def test_late_worker_results_are_ignored_after_cancel(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
    sync_bus_reset: None,
    cancel_client_reset: None,
) -> None:
    """Huỷ xong, `lock_run`/`record_step` của task tới muộn trả `None` và không ghi gì (BE-00 §7)."""
    upload_id, run_id = await _live_run(db_sessionmaker, fake_clock)
    assert await _cancel(db_sessionmaker, upload_id, fake_clock) == (run_id,)

    async with db_sessionmaker() as session:
        assert await lock_run(session, run_id=run_id) is None
        assert await record_step(session, run_id=run_id, step="preprocess", status="running", clock=fake_clock) is None
        late = await record_step(session, run_id=run_id, step="preprocess", status="completed", clock=fake_clock)
        assert late is None
        run = await read_run(session, run_id)
        assert (run.status, run.error_code, run.superseded_by, run.progress_percent) == ("failed", CANCELLED, None, 0)


async def test_two_concurrent_cancels_cancel_once(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
    sync_bus_reset: None,
    cancel_client_reset: None,
) -> None:
    """Bấm đôi chạy song song: khoá tầng xếp hàng hai lời huỷ — một bên huỷ, bên kia thấy không còn gì."""
    upload_id, run_id = await _live_run(db_sessionmaker, fake_clock)

    results = await asyncio.gather(*(_cancel(db_sessionmaker, upload_id, fake_clock) for _ in range(2)))

    assert sorted(results) == [(), (run_id,)]


async def test_cancel_races_a_worker_step_without_losing_the_cancel(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
    sync_bus_reset: None,
    cancel_client_reset: None,
) -> None:
    """Huỷ đua với `record_step`: bên nào lấy khoá trước thì lượt vẫn kết thúc `failed` `PIPELINE_CANCELLED`."""
    upload_id, run_id = await _live_run(db_sessionmaker, fake_clock)

    async def _step() -> None:
        async with db_sessionmaker() as session:
            await record_step(session, run_id=run_id, step="preprocess", status="running", clock=fake_clock)
            await session.commit()
            await after_commit_idle(session)

    await asyncio.gather(_step(), _cancel(db_sessionmaker, upload_id, fake_clock))

    async with db_sessionmaker() as session:
        run = await read_run(session, run_id)
        assert (run.status, run.error_code) == ("failed", CANCELLED)


async def test_cancel_touches_only_its_own_upload_and_notifies_nobody(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
    sync_bus_reset: None,
    cancel_client_reset: None,
) -> None:
    """Lượt của tầng khác vẫn sống; huỷ là việc của người dùng nên không sinh thông báo `aiFailed`."""
    upload_id, run_id = await _live_run(db_sessionmaker, fake_clock)
    _, other_run_id = await _live_run(db_sessionmaker, fake_clock)

    await _cancel(db_sessionmaker, upload_id, fake_clock)

    async with db_sessionmaker() as session:
        uploader = (await session.execute(select(UploadRow.created_by).where(UploadRow.id == upload_id))).scalar_one()

    async with db_sessionmaker() as session:
        assert (await read_run(session, other_run_id)).status == "pending"
        count = select(func.count()).where(NotificationRow.dedupe_key == f"aiFailed:{run_id}:{uploader}")
        assert (await session.execute(count)).scalar_one() == 0


async def test_cancel_publishes_a_final_progress_frame(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    streams_client: AsyncRedis,
    fake_clock: FakeClock,
    sync_bus_reset: None,
    cancel_client_reset: None,
) -> None:
    """SSE nhận khung cuối `failed` `PIPELINE_CANCELLED` rồi stream được hẹn giờ xoá — FE thoát màn chờ."""
    upload_id, _ = await _live_run(db_sessionmaker, fake_clock)

    await _cancel(db_sessionmaker, upload_id, fake_clock)

    events = await streams_client.xrange(upload_stream(upload_id))
    assert events is not None
    _, last = events[-1]
    assert CANCELLED in str(last)
    ttl = await streams_client.ttl(upload_stream(upload_id))
    assert isinstance(ttl, int)
    assert ttl > 0


async def test_cancel_of_an_unknown_upload_is_a_no_op(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Không có dòng upload → tuple rỗng, không ném (route đã trả 404 trước đó)."""
    assert await cancel_upload_runs(db_session, upload_id="upl_00000000000000000000000000", clock=fake_clock) == ()
