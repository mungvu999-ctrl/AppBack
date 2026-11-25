"""`start_run`, `record_step`, `lock_run` — mục "`Progress`" của B2-04 [8].

Postgres và Redis thật (K23). Test nào cần tác dụng **sau commit** (stream, hàng đợi) thì
tự mở session từ `db_sessionmaker` và `commit`; các test còn lại dùng `db_session` và không
commit, nên `on_after_commit` không chạy và không cần Redis.
"""

import re
import socket
import threading
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import timedelta
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.errors import FLOOR_DELETED
from apps.api.drawings.runs import (
    fail_run,
    lock_run,
    publish_progress_after_commit,
    record_step,
    restore_window_elapsed,
    start_run,
)
from apps.api.drawings.tests._helpers import Scene, make_scene, read_run, sync_bus_reset
from apps.api.floors.settings import get_floors_settings
from apps.api.projects.summaries import unregister_floor
from packages.core.pipeline import PipelineCode
from packages.db.hooks import after_commit_idle
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.db.models.notifications import NotificationRow
from packages.db.models.projects import ProjectFloorSummary, ProjectMembership
from packages.messaging.redis import broker_redis_sync
from packages.messaging.settings import reset_messaging_settings_cache
from packages.messaging.streams import upload_stream
from packages.testing.factories.drawings import make_upload
from packages.testing.factories.floors import make_floor
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import queued_payloads

PIPELINE_QUEUE: Final = "pipeline.cpu"
SUPERSEDED: Final = PipelineCode.PIPELINE_SUPERSEDED.value

__all__ = ["sync_bus_reset"]  # fixture nhập từ `_helpers`, pytest tìm theo tên trong module này


async def _complete_upload(db: AsyncSession, scene: Scene) -> UploadRow:
    """Lượt tải đã `complete` nhưng **không** có object nào — đủ cho mọi test của lượt chạy."""
    return await make_upload(db, project=scene.project, floor=scene.floor, status="complete")


async def _started_run(db: AsyncSession, clock: FakeClock) -> tuple[Scene, PipelineRunRow]:
    """Sân khấu + một lượt chạy `pending` vừa mở."""
    scene = await make_scene(db)
    upload = await _complete_upload(db, scene)
    row = await start_run(db, upload_id=upload.id, clock=clock)
    return scene, await read_run(db, row.id)


async def _summary(db: AsyncSession, scene: Scene) -> ProjectFloorSummary:
    """Dòng đếm của tầng, `refresh` sau khi `set_upload_state` ghi bằng một câu `UPDATE` thô."""
    stmt = select(ProjectFloorSummary).where(
        ProjectFloorSummary.project_id == scene.project.id,
        ProjectFloorSummary.floor_level_id == scene.floor.level_id,
    )
    row = (await db.execute(stmt)).scalar_one()
    await db.refresh(row)
    return row


@pytest_asyncio.fixture(loop_scope="function")
async def committing_db(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Session mà test tự `commit` — để callback sau commit thật sự chạy (J09, S08)."""
    async with db_sessionmaker() as session:
        yield session


# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------


async def test_start_run_opens_a_pending_run_at_the_first_step(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt mới luôn bắt đầu ở `pending`/`preprocess`/0 và dòng đếm của tầng theo ngay."""
    scene, run = await _started_run(db_session, fake_clock)
    assert (run.status, run.current_step, run.progress_percent) == ("pending", "preprocess", 0)
    summary = await _summary(db_session, scene)
    assert (summary.has_upload, summary.pipeline_state) == (True, "pending")


async def test_start_run_supersedes_the_previous_live_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Tải lại cùng tầng: lượt cũ thành `failed` `PIPELINE_SUPERSEDED`, trỏ tới lượt mới."""
    scene, first = await _started_run(db_session, fake_clock)
    second_upload = await _complete_upload(db_session, scene)
    second = await start_run(db_session, upload_id=second_upload.id, clock=fake_clock)

    old = await read_run(db_session, first.id)
    assert (old.status, old.error_code, old.superseded_by) == ("failed", SUPERSEDED, second.id)
    assert old.ended_at is None


async def test_start_run_rejects_unknown_upload(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Không có dòng upload là lỗi lập trình: #7 đã tra nó dưới khoá trước khi gọi."""
    with pytest.raises(ValueError, match="không có lượt tải"):
        await start_run(db_session, upload_id="upl_00000000000000000000000000", clock=fake_clock)


async def test_start_run_publishes_progress_and_expires_the_superseded_stream(
    committing_db: AsyncSession,
    streams_client: AsyncRedis,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Lượt bị thay phát một khung `failed` rồi stream của nó được hẹn giờ xoá (BE-00 §7)."""
    scene = await make_scene(committing_db)
    first_upload = await _complete_upload(committing_db, scene)
    await start_run(committing_db, upload_id=first_upload.id, clock=fake_clock)
    await committing_db.commit()
    await after_commit_idle(committing_db)

    second_upload = await _complete_upload(committing_db, scene)
    await start_run(committing_db, upload_id=second_upload.id, clock=fake_clock)
    await committing_db.commit()
    await after_commit_idle(committing_db)

    stream = upload_stream(first_upload.id)
    events = await streams_client.xrange(stream)
    assert events is not None
    assert len(events) == 2, events
    ttl = await streams_client.ttl(stream)
    assert isinstance(ttl, int)
    assert ttl > 0


async def test_start_run_queues_nothing_when_the_transaction_rolls_back(
    committing_db: AsyncSession,
    messaging_env: None,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """J09: `pipeline.orchestrate.start` chỉ rời tiến trình sau commit; rollback → hàng rỗng."""
    broker = broker_redis_sync()
    broker.delete(PIPELINE_QUEUE)
    scene = await make_scene(committing_db)
    upload = await _complete_upload(committing_db, scene)
    await start_run(committing_db, upload_id=upload.id, clock=fake_clock)
    await committing_db.rollback()
    await after_commit_idle(committing_db)

    assert queued_payloads(broker, PIPELINE_QUEUE) == []
    broker.close()


async def test_start_run_queues_the_payload_after_commit(
    committing_db: AsyncSession,
    messaging_env: None,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Commit xong thì đúng **một** thông điệp mang `run_id` và `upload_id` của lượt vừa mở."""
    broker = broker_redis_sync()
    broker.delete(PIPELINE_QUEUE)
    scene = await make_scene(committing_db)
    upload = await _complete_upload(committing_db, scene)
    run = await start_run(committing_db, upload_id=upload.id, clock=fake_clock)
    await committing_db.commit()
    await after_commit_idle(committing_db)

    assert queued_payloads(broker, PIPELINE_QUEUE) == [{"schema_version": 1, "run_id": run.id, "upload_id": upload.id}]
    broker.close()


# ---------------------------------------------------------------------------
# record_step — đối số sai
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "status", "error_code"),
    [
        ("quality_check", "running", None),
        ("preprocess", "done", None),
        ("preprocess", "failed", None),
        ("preprocess", "failed", "khong phai upper snake"),
    ],
)
async def test_record_step_rejects_bad_arguments(
    db_session: AsyncSession, fake_clock: FakeClock, step: str, status: str, error_code: str | None
) -> None:
    """Bước lạ, `status` lạ, `failed` thiếu mã UPPER_SNAKE → `ValueError` trước khi chạm DB."""
    _, run = await _started_run(db_session, fake_clock)
    with pytest.raises(ValueError, match=r"lạ|UPPER_SNAKE"):
        await record_step(db_session, run_id=run.id, step=step, status=status, clock=fake_clock, error_code=error_code)


async def test_record_step_ignores_unknown_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt đã bị lịch dọn xoá: kết quả tới muộn rơi im lặng, không ném."""
    result = await record_step(
        db_session, run_id="run_00000000000000000000000000", step="preprocess", status="running", clock=fake_clock
    )
    assert result is None


# ---------------------------------------------------------------------------
# record_step — đường thường
# ---------------------------------------------------------------------------


async def test_record_step_running_then_completed_moves_forward(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """`running` mở lượt ở phần trăm của các bước **trước**; `completed` đẩy sang bước kế."""
    _, run = await _started_run(db_session, fake_clock)
    first = await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock)
    assert first is not None
    assert isinstance(first.pop("queueAhead"), int)
    assert first == {
        "id": run.upload_id,
        "status": "running",
        "step": "preprocess",
        "progressPercent": 0,
        "startedAt": "2026-01-01T00:00:00.000Z",
    }

    fake_clock.advance(timedelta(minutes=1))
    second = await record_step(db_session, run_id=run.id, step="preprocess", status="completed", clock=fake_clock)
    assert second is not None
    assert second["progressPercent"] == 5
    assert second["step"] == "wallSegmentation"
    assert (await read_run(db_session, run.id)).started_at is not None


async def test_record_step_last_step_completes_the_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Bước cuối xong → lượt `completed`, 100, có `endedAt`; dòng đếm sang `completed`."""
    scene, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="qualityCheck", status="running", clock=fake_clock)
    wire = await record_step(db_session, run_id=run.id, step="qualityCheck", status="completed", clock=fake_clock)

    assert wire is not None
    assert wire["status"] == "completed"
    assert wire["progressPercent"] == 100
    assert "endedAt" in wire
    assert (await _summary(db_session, scene)).pipeline_state == "completed"


async def test_record_step_percent_never_goes_backwards(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Bước sau chạy trước rồi bước trước mới xong: phần trăm giữ giá trị lớn hơn."""
    _, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="dimensionReading", status="running", clock=fake_clock)
    assert (await read_run(db_session, run.id)).progress_percent == 55

    wire = await record_step(db_session, run_id=run.id, step="dimensionReading", status="completed", clock=fake_clock)
    assert wire is not None
    assert wire["progressPercent"] == 70


async def test_record_step_failed_keeps_percent_and_has_no_ended_at(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Lượt hỏng giữ phần trăm cuối, mang mã lỗi, **không** `endedAt` (K33)."""
    scene, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="wallSegmentation", status="running", clock=fake_clock)
    wire = await record_step(
        db_session,
        run_id=run.id,
        step="wallSegmentation",
        status="failed",
        clock=fake_clock,
        error_code="IMAGE_TOO_LARGE",
    )

    assert wire is not None
    assert wire["status"] == "failed"
    assert wire["error"] == "IMAGE_TOO_LARGE"
    assert "endedAt" not in wire
    assert (await read_run(db_session, run.id)).ended_at is None
    assert (await _summary(db_session, scene)).pipeline_state == "failed"


# ---------------------------------------------------------------------------
# record_step — giao muộn, giao lặp
# ---------------------------------------------------------------------------


async def test_record_step_ignores_a_step_behind_the_current_one(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Bước lùi (J06) không được phép ghi đè tiến độ đang có."""
    _, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="spatialDataBuild", status="running", clock=fake_clock)
    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None


async def test_record_step_ignores_a_duplicate_delivery(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Giao lặp cùng bước, cùng trạng thái: lượt thứ hai không đổi gì nên trả `None`."""
    _, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock)
    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None


async def test_record_step_ignores_a_superseded_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Kết quả của lượt đã bị thay bị bỏ, dù bước và trạng thái đều hợp lệ."""
    scene, first = await _started_run(db_session, fake_clock)
    second_upload = await _complete_upload(db_session, scene)
    await start_run(db_session, upload_id=second_upload.id, clock=fake_clock)

    assert await record_step(db_session, run_id=first.id, step="preprocess", status="running", clock=fake_clock) is None


# ---------------------------------------------------------------------------
# record_step — xoá mềm
# ---------------------------------------------------------------------------


async def test_record_step_writes_like_a_live_floor_inside_the_restore_window(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Tầng vừa gỡ mà còn trong cửa sổ khôi phục: ghi bình thường (A8, BE-00 §7)."""
    scene, run = await _started_run(db_session, fake_clock)
    scene.floor.deleted_at = fake_clock.now()
    await unregister_floor(db_session, project_id=scene.project.id, floor_level_id=scene.floor.level_id)
    await db_session.flush()

    wire = await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock)
    assert wire is not None
    assert wire["status"] == "running"


async def test_record_step_fails_the_run_when_the_floor_is_gone_for_good(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Quá cửa sổ khôi phục → lượt `failed` `FLOOR_DELETED`, dòng đếm ẩn vẫn nhận `failed`."""
    scene, run = await _started_run(db_session, fake_clock)
    scene.floor.deleted_at = fake_clock.now()
    await unregister_floor(db_session, project_id=scene.project.id, floor_level_id=scene.floor.level_id)
    await db_session.flush()
    fake_clock.advance(timedelta(seconds=get_floors_settings().floor_restore_window_s + 1))

    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None
    stored = await read_run(db_session, run.id)
    assert (stored.status, stored.error_code, stored.ended_at) == ("failed", FLOOR_DELETED, None)
    summary = await _summary(db_session, scene)
    assert (summary.hidden, summary.pipeline_state) == (True, "failed")


async def test_record_step_leaves_the_counts_of_a_recreated_floor_alone(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Tầng tạo lại cùng `level_id` dùng chung dòng đếm — lượt của tầng cũ không được đè lên."""
    scene, run = await _started_run(db_session, fake_clock)
    scene.floor.deleted_at = fake_clock.now()
    await unregister_floor(db_session, project_id=scene.project.id, floor_level_id=scene.floor.level_id)
    await db_session.flush()
    fake_clock.advance(timedelta(seconds=get_floors_settings().floor_restore_window_s + 1))
    await make_floor(db_session, project=scene.project, level_id=scene.floor.level_id)

    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None
    assert (await read_run(db_session, run.id)).status == "failed"
    summary = await _summary(db_session, scene)
    assert (summary.hidden, summary.pipeline_state) == (False, "none")


async def test_record_step_fails_the_run_when_the_project_is_soft_deleted(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án xoá mềm không có cửa sổ chờ: lượt hỏng ngay (BE-00 §7)."""
    scene, run = await _started_run(db_session, fake_clock)
    scene.project.deleted_at = fake_clock.now()
    await db_session.flush()

    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None
    assert (await read_run(db_session, run.id)).error_code == FLOOR_DELETED


# ---------------------------------------------------------------------------
# lock_run
# ---------------------------------------------------------------------------


async def test_lock_run_returns_the_live_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt còn sống: ảnh chụp đông cứng đủ cho người gọi kiểm `upload_id` và bước."""
    _, run = await _started_run(db_session, fake_clock)
    locked = await lock_run(db_session, run_id=run.id)
    assert locked is not None
    assert (locked.id, locked.status, locked.superseded_by) == (run.id, "pending", None)


async def test_lock_run_returns_none_for_a_finished_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt đã xong không nhận thêm kết quả nào."""
    _, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="qualityCheck", status="completed", clock=fake_clock)
    assert await lock_run(db_session, run_id=run.id) is None


async def test_lock_run_returns_none_for_a_superseded_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt bị thay cũng bị từ chối, dù nó chưa kịp chạy bước nào."""
    scene, first = await _started_run(db_session, fake_clock)
    second_upload = await _complete_upload(db_session, scene)
    await start_run(db_session, upload_id=second_upload.id, clock=fake_clock)
    assert await lock_run(db_session, run_id=first.id) is None


async def test_lock_run_returns_none_for_an_unknown_run(db_session: AsyncSession) -> None:
    """Id lạ trả `None`, không ném — worker giao muộn không làm hỏng giao dịch của ai."""
    assert await lock_run(db_session, run_id="run_00000000000000000000000000") is None


# ---------------------------------------------------------------------------
# fail_run (lõi quét bù)
# ---------------------------------------------------------------------------


async def test_fail_run_marks_a_live_run_without_ended_at(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Hết trần quét bù: lượt `failed` `PIPELINE_STALLED`, dòng đếm theo, không `endedAt`."""
    scene, run = await _started_run(db_session, fake_clock)
    assert await fail_run(db_session, run_id=run.id, error_code="PIPELINE_STALLED", clock=fake_clock) is True
    stored = await read_run(db_session, run.id)
    assert (stored.status, stored.error_code, stored.ended_at) == ("failed", "PIPELINE_STALLED", None)
    assert (await _summary(db_session, scene)).pipeline_state == "failed"


async def test_fail_run_skips_a_run_that_already_ended(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt đã kết thúc không bị ghi đè — quét bù chạy song song với worker cũng an toàn."""
    _, run = await _started_run(db_session, fake_clock)
    await record_step(db_session, run_id=run.id, step="qualityCheck", status="completed", clock=fake_clock)
    assert await fail_run(db_session, run_id=run.id, error_code="PIPELINE_STALLED", clock=fake_clock) is False
    assert (await read_run(db_session, run.id)).status == "completed"


async def test_fail_run_ignores_an_unknown_run(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Id lạ trả `False`, không ném."""
    unknown = "run_00000000000000000000000000"
    assert await fail_run(db_session, run_id=unknown, error_code="PIPELINE_STALLED", clock=fake_clock) is False


async def test_fail_run_rejects_a_non_upper_snake_code(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Mã lỗi sai mẫu là lỗi lập trình: `error` của `Progress` luôn UPPER_SNAKE."""
    _, run = await _started_run(db_session, fake_clock)
    with pytest.raises(ValueError, match="UPPER_SNAKE"):
        await fail_run(db_session, run_id=run.id, error_code="stalled", clock=fake_clock)


def test_restore_window_elapsed__boundary(fake_clock: FakeClock) -> None:
    """NO-297: luật cửa sổ khôi phục công khai — chưa xoá = chưa hết; đúng `window` giây = đã hết."""
    window = get_floors_settings().floor_restore_window_s
    assert restore_window_elapsed(None, fake_clock) is False
    assert restore_window_elapsed(fake_clock.now() - timedelta(seconds=window - 1), fake_clock) is False
    assert restore_window_elapsed(fake_clock.now() - timedelta(seconds=window), fake_clock) is True


async def test_record_step_fails_the_run_exactly_at_the_window_edge(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Biên cửa sổ: đúng `FLOOR_RESTORE_WINDOW_S` giây sau khi xoá là đã hết (nửa mở [0, window))."""
    scene, run = await _started_run(db_session, fake_clock)
    scene.floor.deleted_at = fake_clock.now()
    await unregister_floor(db_session, project_id=scene.project.id, floor_level_id=scene.floor.level_id)
    await db_session.flush()
    fake_clock.advance(timedelta(seconds=get_floors_settings().floor_restore_window_s))

    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None
    assert (await read_run(db_session, run.id)).error_code == FLOOR_DELETED


# ---------------------------------------------------------------------------
# publish_progress_after_commit — không trùng khi mất phản hồi (NO-187)
# ---------------------------------------------------------------------------

_EVENT_ID_REPLY: Final = re.compile(rb"\$\d+\r\n\d+-\d+\r\n")
"""Phản hồi RESP mang id mục stream (`XADD`, hay script `publish_once`): lệnh ghi đã chạy xong."""


class _ReplyCutter:
    """Proxy TCP tới Redis **thật**: chuyển nguyên từng byte, trừ phản hồi id sự kiện đầu tiên.

    Gặp nó thì đóng kết nối phía client thay vì chuyển — máy chủ đã chạy lệnh ghi, client thấy
    `ConnectionError` ở pha đọc và redis-py thử lại trên kết nối mới (đúng cảnh NO-187 còn hở).
    """

    def __init__(self, host: str, port: int) -> None:
        """Mở cổng nghe cục bộ, chuyển tới `host:port`."""
        self._target = (host, port)
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._server.getsockname()[1])
        self.cut = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        """Mỗi kết nối client một kết nối tới Redis, hai luồng bơm byte hai chiều."""
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._target)
            threading.Thread(target=self._pump, args=(client, upstream, False), daemon=True).start()
            threading.Thread(target=self._pump, args=(upstream, client, True), daemon=True).start()

    def _pump(self, source: socket.socket, sink: socket.socket, replies: bool) -> None:
        """Chép `source` → `sink`; chiều phản hồi cắt đúng một lần tại id sự kiện đầu tiên."""
        try:
            while chunk := source.recv(65536):
                if replies and not self.cut and _EVENT_ID_REPLY.match(chunk):
                    self.cut = True
                    break
                sink.sendall(chunk)
        except OSError:
            pass
        finally:
            for side in (source, sink):
                with suppress(OSError):
                    side.shutdown(socket.SHUT_RDWR)
                side.close()

    def close(self) -> None:
        """Ngừng nhận kết nối mới."""
        self._server.close()


async def test_publish_progress_after_commit__a_lost_reply_publishes_one_frame(
    committing_db: AsyncSession,
    streams_client: AsyncRedis,
    redis_broker_url: str,
    sync_bus_reset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO-187: máy chủ đã ghi khung `Progress` nhưng phản hồi mất → lượt thử lại không ghi khung thứ hai."""
    target = urlsplit(redis_broker_url)
    assert target.hostname is not None
    assert target.port is not None
    cutter = _ReplyCutter(target.hostname, target.port)
    monkeypatch.setenv("REDIS_BROKER_URL", urlunsplit(target._replace(netloc=f"127.0.0.1:{cutter.port}")))
    reset_messaging_settings_cache()
    try:
        scene = await make_scene(committing_db)
        upload = await _complete_upload(committing_db, scene)
        await committing_db.commit()
        await publish_progress_after_commit(committing_db, upload.id)
        await committing_db.commit()
        await after_commit_idle(committing_db)
    finally:
        cutter.close()
        monkeypatch.setenv("REDIS_BROKER_URL", redis_broker_url)
        reset_messaging_settings_cache()

    assert cutter.cut, "proxy phải đã cắt một phản hồi id sự kiện"
    assert await streams_client.xlen(upload_stream(upload.id)) == 1


# ---------------------------------------------------------------------------
# Thông báo lượt hỏng (FIX-P0-12)
# ---------------------------------------------------------------------------


async def _failed_notifications(db: AsyncSession, user_id: str) -> list[NotificationRow]:
    """Mọi thông báo `aiFailed` của một người, cũ trước."""
    stmt = (
        select(NotificationRow)
        .where(NotificationRow.user_id == user_id, NotificationRow.kind == "aiFailed")
        .order_by(NotificationRow.created_at, NotificationRow.id)
    )
    return list((await db.execute(stmt)).scalars())


async def _fail_step(db: AsyncSession, run_id: str, clock: FakeClock) -> dict[str, object] | None:
    """Bước đầu chạy rồi hỏng với một mã thường (không phải thay lượt hay xoá tầng)."""
    await record_step(db, run_id=run_id, step="preprocess", status="running", clock=clock)
    return await record_step(
        db, run_id=run_id, step="preprocess", status="failed", clock=clock, error_code="IMAGE_TOO_LARGE"
    )


async def test_record_step_failed_notifies_uploader(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Bước hỏng → người tải nhận đúng một `aiFailed` ở `floors`, dẫn tới tầng, nhãn là tên tầng."""
    scene, run = await _started_run(db_session, fake_clock)
    assert await _fail_step(db_session, run.id, fake_clock) is not None

    rows = await _failed_notifications(db_session, scene.user.id)
    assert [(row.place, row.floor_level_id, row.object_label, row.dedupe_key) for row in rows] == [
        ("floors", scene.floor.level_id, scene.floor.name, f"aiFailed:{run.id}:{scene.user.id}")
    ]
    assert rows[0].message == f"Dự án {scene.project.name}: AI chưa xử lý được bản vẽ"


async def test_fail_run_stalled_notifies_uploader(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lõi quét bù đánh hỏng `PIPELINE_STALLED` → người tải cũng được báo."""
    scene, run = await _started_run(db_session, fake_clock)
    assert await fail_run(db_session, run_id=run.id, error_code="PIPELINE_STALLED", clock=fake_clock) is True
    assert len(await _failed_notifications(db_session, scene.user.id)) == 1


async def test_failed_twice_notifies_once(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Bước hỏng rồi quét bù cũng đánh hỏng (giao lặp, chạy song song) → vẫn một thông báo."""
    scene, run = await _started_run(db_session, fake_clock)
    await _fail_step(db_session, run.id, fake_clock)
    await _fail_step(db_session, run.id, fake_clock)
    await fail_run(db_session, run_id=run.id, error_code="PIPELINE_STALLED", clock=fake_clock)
    assert len(await _failed_notifications(db_session, scene.user.id)) == 1


async def test_superseded_does_not_notify(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt bị lượt mới thay là do người dùng tải lại: không báo."""
    scene, first = await _started_run(db_session, fake_clock)
    second_upload = await _complete_upload(db_session, scene)
    await start_run(db_session, upload_id=second_upload.id, clock=fake_clock)

    assert (await read_run(db_session, first.id)).error_code == PipelineCode.PIPELINE_SUPERSEDED.value
    assert await _failed_notifications(db_session, scene.user.id) == []


async def test_floor_deleted_does_not_notify(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Tầng xoá quá cửa sổ khôi phục → lượt `FLOOR_DELETED` nhưng không báo."""
    scene, run = await _started_run(db_session, fake_clock)
    scene.floor.deleted_at = fake_clock.now()
    await unregister_floor(db_session, project_id=scene.project.id, floor_level_id=scene.floor.level_id)
    await db_session.flush()
    fake_clock.advance(timedelta(seconds=get_floors_settings().floor_restore_window_s + 1))

    assert await record_step(db_session, run_id=run.id, step="preprocess", status="running", clock=fake_clock) is None
    assert (await read_run(db_session, run.id)).error_code == FLOOR_DELETED
    assert await _failed_notifications(db_session, scene.user.id) == []


async def test_uploader_left_project_not_notified(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Người tải đã bị gỡ khỏi dự án → lượt vẫn hỏng, không ai được báo."""
    scene, run = await _started_run(db_session, fake_clock)
    await db_session.execute(
        delete(ProjectMembership).where(
            ProjectMembership.project_id == scene.project.id, ProjectMembership.user_id == scene.user.id
        )
    )
    await db_session.flush()

    assert await _fail_step(db_session, run.id, fake_clock) is not None
    assert (await read_run(db_session, run.id)).status == "failed"
    assert await _failed_notifications(db_session, scene.user.id) == []
