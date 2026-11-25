"""`streams_open_progress` (S1) — S01-S09 trên route thật (B4-01 [8]; cổng case).

Mọi test đi qua app thật: Postgres, hai Redis và cookie luồng do chính
`POST /api/auth/refresh` cấp (K23 cấm mock, S09 đòi đúng đường đó). Nhà cung cấp giả ở
`apps/api/streams/tests/fakes.py` đóng vai B2-04.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Final

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.sessions import bump_token_version, revoke_sessions
from apps.api.auth.settings import get_auth_settings, reset_auth_settings_cache
from apps.api.streams.connections import conn_key
from apps.api.streams.sse import PING, RETRY_AFTER_S
from apps.api.streams.tests.fakes import FakePolicy, FakeSnapshot, progress_provider
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.db.hooks import after_commit_idle
from packages.db.models.auth import User
from packages.messaging.redis import AsyncRedis
from packages.messaging.streams import EventBus, upload_stream
from packages.testing.fixtures.auth import ORIGIN, REFRESH_PATH
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import db_client_count
from packages.testing.fixtures.streams import (
    SseOpen,
    StreamAppFactory,
    publish_progress,
    record_stream_frames,
    sample_progress,
    signed_stream_user,
)

OP: Final = "streams_open_progress"
PROJECT_ID: Final = "prj_01ARZ3NDEKTSV4RRFFQ69G5FAV"
UPLOAD_ID: Final = "upl_01BX5ZZKBKACTAV9WEVGEMMVRZ"

_log: Final = logging.getLogger(__name__)
FAST: Final = {"stream_heartbeat_s": "0.2", "stream_recheck_s": "0.2", "stream_read_block_ms": "100"}
"""Nhịp nhỏ của test: 0,2 * 1,2 + 0,1 = 0,34 s, vẫn thoả ràng buộc S07 lúc nạp."""

CLOSE_BUDGET_S: Final = 1.0
CLOSE_BUDGET_CACHED_S: Final = 1.5
"""Nhánh không xoá cache `Principal` phải chờ thêm `AUTH_PRINCIPAL_CACHE_TTL_S` = 1 s."""

WAIT_S: Final = 3.0
"""Trần mọi lượt chờ khung — hỏng thì test đỏ chứ không treo."""

BUSY_WAIT_S: Final = 8.0
"""Luồng bận chạy hơn 2 s (dài hơn TTL 1 s của khoá ZSET) mới chứng minh được việc gia hạn."""


def progress_path(project_id: str = PROJECT_ID, upload_id: str = UPLOAD_ID) -> str:
    """Đường của S1 cho một cặp id."""
    return f"/api/streams/projects/{project_id}/uploads/{upload_id}/progress"


def _short_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AUTH_PRINCIPAL_CACHE_TTL_S=1`: S07 chờ được cả nhánh **không** xoá cache."""
    monkeypatch.setenv("AUTH_PRINCIPAL_CACHE_TTL_S", "1")
    reset_auth_settings_cache()


# ---------------------------------------------------------------------------
# S01 — cookie luồng thiếu hoặc hỏng → 401
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S01(stream_app: StreamAppFactory, sse_open: SseOpen) -> None:
    """Không có cookie luồng → 401 `UNAUTHENTICATED`, thân W7 chứ không phải SSE."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with sse_open(app, progress_path()) as stream:
        assert stream.status == 401
        assert json.loads(stream.body)["code"] == "UNAUTHENTICATED"


async def test_streams_open_progress__S01_malformed(stream_app: StreamAppFactory, sse_open: SseOpen) -> None:
    """Cookie luồng không phải JWT (chữ ký khoá lạ cũng đi đường này) → 401."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with sse_open(app, progress_path(), cookies={"appback_stream": "khong.phai.jwt"}) as stream:
        assert stream.status == 401
        assert json.loads(stream.body)["code"] == "UNAUTHENTICATED"


async def test_streams_open_progress__S01_wrong_audience(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Access token (`aud="access"`) đặt vào cookie luồng → 401: mỗi khoá con một việc."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        cookies = {"appback_stream": owner.signed.access_token}
        async with sse_open(app, progress_path(), cookies=cookies) as stream:
            assert stream.status == 401
            assert json.loads(stream.body)["code"] == "UNAUTHENTICATED"


async def test_streams_open_progress__S01_expired(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Cookie luồng quá `STREAM_TOKEN_TTL_S` → 401 (đồng hồ giả nhảy qua hạn)."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        fake_clock.advance(timedelta(seconds=get_auth_settings().stream_token_ttl_s + 1))
        async with sse_open(app, progress_path(), cookies=owner.cookies) as stream:
            assert stream.status == 401
            assert json.loads(stream.body)["code"] == "UNAUTHENTICATED"


# ---------------------------------------------------------------------------
# S02 — nối lại bằng lastEventId
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S02(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, event_bus: EventBus
) -> None:
    """Nhận 2/5 rồi rớt; nối lại với `lastEventId` thứ 2 → đúng 3, 4, 5, không trùng, không ảnh chụp."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        async with sse_open(app, progress_path(), cookies=owner.cookies) as first:
            await first.next_frames(1, WAIT_S)  # ảnh chụp của luồng mở mới (S08)
            ids = [
                await publish_progress(event_bus, UPLOAD_ID, sample_progress(UPLOAD_ID, progressPercent=step))
                for step in (10, 20, 30, 40, 50)
            ]
            received = await first.next_frames(2, WAIT_S)
            assert [frame.id for frame in received] == ids[:2]
            await first.disconnect()
        async with sse_open(app, progress_path(), cookies=owner.cookies, query={"lastEventId": ids[1]}) as second:
            rest = await second.next_frames(3, WAIT_S)
            assert [frame.id for frame in rest] == ids[2:]
            assert [json.loads(frame.data)["progressPercent"] for frame in rest] == [30, 40, 50]


# ---------------------------------------------------------------------------
# S03 — định dạng khung
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S03(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, event_bus: EventBus
) -> None:
    """Dây W15: header đúng, mọi khung có `id:`/`data:`, **không** `event:`; `data` ghi mẫu H5."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies) as stream,
    ):
        assert stream.status == 200
        assert stream.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert stream.headers["cache-control"] == "no-store"
        assert stream.headers["x-accel-buffering"] == "no"
        frames = await stream.next_frames(1, WAIT_S)
        await publish_progress(event_bus, UPLOAD_ID)
        frames += await stream.next_frames(1, WAIT_S)
        record_stream_frames(OP, "S03", frames)
        for frame in frames:
            assert frame.event is None, "K03: luồng không bao giờ gửi dòng event:"
            assert frame.id
            assert json.loads(frame.data)["id"] == UPLOAD_ID
        assert b"event:" not in stream._raw


async def test_streams_open_progress__S03_invalid_event(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    event_bus: EventBus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sự kiện sai `event_model` bị bỏ, luồng vẫn sống, log **không** chứa dữ liệu sự kiện (K11)."""
    app = stream_app(providers=[progress_provider()], **FAST)
    private_text = "du-lieu-khong-duoc-vao-log"
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies) as stream,
    ):
        await stream.next_frames(1, WAIT_S)
        with caplog.at_level(logging.WARNING, logger="apps.api.streams.sse"):
            await event_bus.publish(upload_stream(UPLOAD_ID), {"khong": private_text})
            good = await publish_progress(event_bus, UPLOAD_ID)
            frames = await stream.next_frames(1, WAIT_S)
        assert [frame.id for frame in frames] == [good], "sự kiện hỏng phải bị bỏ, không làm chết luồng"
        assert any(record.getMessage() == "stream_event_invalid" for record in caplog.records)
        assert private_text not in caplog.text


# ---------------------------------------------------------------------------
# S04 — heartbeat
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S04(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """`STREAM_HEARTBEAT_S=0.1` → luồng im lặng vẫn gửi `: ping` (W15)."""
    app = stream_app(
        providers=[progress_provider()], stream_heartbeat_s="0.1", stream_recheck_s="0.2", stream_read_block_ms="100"
    )
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies) as stream,
    ):
        raw = await stream.raw_until(lambda data: PING.encode() in data, WAIT_S)
        assert PING.encode() in raw


# ---------------------------------------------------------------------------
# S05 — client rớt
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S05(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    cache_client: AsyncRedis,
    streams_client: AsyncRedis,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rớt client → chỗ giữ bị `ZREM`; 20 lượt mở-đóng làm số kết nối Redis tăng ≤ 1."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        key = conn_key(owner.user.id)
        async with sse_open(app, progress_path(), cookies=owner.cookies) as stream:
            await stream.next_frames(1, WAIT_S)
            assert await cache_client.zcard(key) == 1
            await stream.disconnect()
        assert await cache_client.zcard(key) == 0

        before = await db_client_count(streams_client)
        for _ in range(20):
            async with sse_open(app, progress_path(), cookies=owner.cookies) as loop_stream:
                await loop_stream.next_frames(1, WAIT_S)
                await loop_stream.disconnect()
        after = await db_client_count(streams_client)
        with capsys.disabled():
            print(f"\n[S05] CLIENT LIST trước={before} sau={after} (20 lượt mở-đóng)")
        assert after - before <= 1
        assert await cache_client.zcard(key) == 0


# ---------------------------------------------------------------------------
# S06 — không được phép mở (chỉ luồng gắn dự án)
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S06(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Chính sách của B2-04 từ chối (người ngoài dự án) → 404, **không** 403."""
    app = stream_app(providers=[progress_provider(policy=FakePolicy(allowed=False))], **FAST)
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies) as stream,
    ):
        assert stream.status == 404
        body = json.loads(stream.body)
        assert body["code"] == "NOT_FOUND"
        assert body["resource"] == "upload"


async def test_streams_open_progress__S06_no_provider(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Chưa có nhà cung cấp nào (B2-04 chưa hợp nhất) → mặc định fail-closed, 404."""
    app = stream_app(**FAST)
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies) as stream,
    ):
        assert stream.status == 404
        assert json.loads(stream.body)["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    ("project_id", "upload_id"),
    [(PROJECT_ID, "upl_khong-phai-ulid"), ("prj_xx", UPLOAD_ID), (PROJECT_ID, PROJECT_ID)],
)
async def test_streams_open_progress__S06_bad_id(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, project_id: str, upload_id: str
) -> None:
    """Id sai mẫu `prj_`/`upl_` + ULID → 404 (không 422: người ngoài không được dò id)."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(project_id, upload_id), cookies=owner.cookies) as stream,
    ):
        assert stream.status == 404
        assert json.loads(stream.body)["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# S07 — mất quyền giữa chừng → server đóng luồng
# ---------------------------------------------------------------------------


S07_CLOSE_BUDGETS: Final = {
    "revoke_sessions": CLOSE_BUDGET_S,
    "bump_token_version": CLOSE_BUDGET_S,
    "disabled_user": CLOSE_BUDGET_CACHED_S,
    "policy_revoked": CLOSE_BUDGET_S,
}
"""Trần đóng luồng theo kịch bản mất quyền (S07): `disabled_user` chờ hết TTL cache 1 s nên rộng hơn."""


async def _s07_close_elapsed(
    scenario: str,
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[float, FakePolicy]:
    """Mở luồng, gây mất quyền theo `scenario`, trả (số giây tới lúc server đóng luồng, chính sách).

    `wait_closed(WAIT_S)` ném `TimeoutError` nếu luồng không đóng — nên test case chỉ cần gọi hàm này;
    phép so với trần `S07_CLOSE_BUDGETS` ở test `perf`.
    """
    _short_cache(monkeypatch)
    policy = FakePolicy()
    app = stream_app(providers=[progress_provider(policy=policy)], **FAST)
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies) as stream,
    ):
        await stream.next_frames(1, WAIT_S)
        if scenario == "revoke_sessions":
            await revoke_sessions(db_session, user_id=owner.user.id, reason="logout", clock=fake_clock)
            await db_session.commit()
            await after_commit_idle(db_session)
        elif scenario == "bump_token_version":
            await bump_token_version(db_session, owner.user.id)
            await db_session.commit()
            await after_commit_idle(db_session)
        elif scenario == "disabled_user":
            await db_session.execute(update(User).where(User.id == owner.user.id).values(status="disabled"))
            await db_session.commit()
        else:
            policy.allowed = False
        return await stream.wait_closed(WAIT_S), policy


async def test_streams_open_progress__S07(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thu hồi mọi phiên → luồng đóng (trần 1 s ở test `perf` bên dưới), không gửi thêm khung nào."""
    elapsed, _ = await _s07_close_elapsed("revoke_sessions", stream_app, sse_open, db_session, fake_clock, monkeypatch)
    _log.info("s07_progress_revoke_sessions closed_after_s=%.3f", elapsed)


async def test_streams_open_progress__S07_token_version(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`bump_token_version` (đổi vai) → `ver` của cookie lệch → luồng đóng."""
    elapsed, _ = await _s07_close_elapsed(
        "bump_token_version", stream_app, sse_open, db_session, fake_clock, monkeypatch
    )
    _log.info("s07_progress_bump_token_version closed_after_s=%.3f", elapsed)


async def test_streams_open_progress__S07_disabled_user(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Người dùng bị vô hiệu **thẳng trong DB** (không xoá cache) → luồng đóng sau khi cache hết hạn (TTL 1 s)."""
    elapsed, _ = await _s07_close_elapsed("disabled_user", stream_app, sse_open, db_session, fake_clock, monkeypatch)
    _log.info("s07_progress_disabled_user closed_after_s=%.3f", elapsed)


async def test_streams_open_progress__S07_policy_revoked(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gỡ thành viên giữa chừng (chính sách đổi sang từ chối) → luồng đóng."""
    elapsed, policy = await _s07_close_elapsed(
        "policy_revoked", stream_app, sse_open, db_session, fake_clock, monkeypatch
    )
    _log.info("s07_progress_policy_revoked closed_after_s=%.3f", elapsed)
    assert policy.calls > 1, "recheck phải gọi lại authorize, không chỉ lúc mở"


@pytest.mark.perf
@pytest.mark.parametrize("scenario", list(S07_CLOSE_BUDGETS))
async def test_progress_stream_closes_within_budget_after_access_loss(
    scenario: str,
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trần S07 (≤ 1 s; 1,5 s khi chờ hết TTL cache) của từng kịch bản mất quyền, đo tuần tự ở bước 5b."""
    elapsed, _ = await _s07_close_elapsed(scenario, stream_app, sse_open, db_session, fake_clock, monkeypatch)
    _log.info("s07_progress_%s closed_after_s=%.3f", scenario, elapsed)
    assert elapsed <= S07_CLOSE_BUDGETS[scenario]


# ---------------------------------------------------------------------------
# S08 — ảnh chụp (chỉ luồng tiến độ)
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S08(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, event_bus: EventBus
) -> None:
    """Mở mới → khung đầu là ảnh chụp mang `id` = id đuôi stream."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        tail = ""
        for step in (10, 20, 30):
            tail = await publish_progress(event_bus, UPLOAD_ID, sample_progress(UPLOAD_ID, progressPercent=step))
        async with sse_open(app, progress_path(), cookies=owner.cookies) as stream:
            frames = await stream.next_frames(1, WAIT_S)
            assert frames[0].id == tail
            assert json.loads(frames[0].data) == sample_progress(UPLOAD_ID)


async def test_streams_open_progress__S08_trimmed(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, streams_client: AsyncRedis
) -> None:
    """`lastEventId` đã bị `MAXLEN` cắt → ảnh chụp lại từ đầu, không im lặng."""
    app = stream_app(providers=[progress_provider()], **FAST)
    bus = EventBus(streams_client, maxlen=10)
    first = await publish_progress(bus, UPLOAD_ID)
    tail = first
    for _ in range(300):
        tail = await publish_progress(bus, UPLOAD_ID)
    assert await bus.is_trimmed(upload_stream(UPLOAD_ID), first), "MAXLEN ~ chưa cắt: test sẽ không kiểm gì"
    async with (
        signed_stream_user(app, db_session) as owner,
        sse_open(app, progress_path(), cookies=owner.cookies, query={"lastEventId": first}) as stream,
    ):
        frames = await stream.next_frames(1, WAIT_S)
        assert frames[0].id == tail


async def test_streams_open_progress__S08_ahead_of_tail(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, event_bus: EventBus
) -> None:
    """`lastEventId` lớn hơn đuôi (id bịa) → ảnh chụp, không phải luồng câm vĩnh viễn."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        tail = await publish_progress(event_bus, UPLOAD_ID)
        ahead = f"{int(tail.partition('-')[0]) + 100_000}-0"
        async with sse_open(app, progress_path(), cookies=owner.cookies, query={"lastEventId": ahead}) as stream:
            frames = await stream.next_frames(1, WAIT_S)
            assert frames[0].id == tail


async def test_streams_open_progress__S08_invalid_snapshot(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Ảnh chụp sai `event_model` → log rồi đóng luồng, không gửi khung hỏng ra dây."""
    broken = FakeSnapshot(lambda upload_id: {"khong": "dung schema"})
    app = stream_app(providers=[progress_provider(snapshot=broken)], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        with caplog.at_level(logging.WARNING, logger="apps.api.streams.sse"):
            async with sse_open(app, progress_path(), cookies=owner.cookies) as stream:
                assert stream.status == 200
                await stream.wait_closed(WAIT_S)
                assert stream._raw == b""
        assert any(record.getMessage() == "stream_snapshot_invalid" for record in caplog.records)


# ---------------------------------------------------------------------------
# S09 — refresh cấp lại cookie; Origin
# ---------------------------------------------------------------------------


async def test_streams_open_progress__S09(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Refresh thật xoay cookie luồng; cookie mới mở được, GET **không** `Origin` vẫn 200 (K31)."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        refreshed = await owner.client.post(REFRESH_PATH, headers=ORIGIN)
        assert refreshed.status_code == 200, refreshed.text
        fresh = {"appback_stream": owner.client.cookies["appback_stream"]}
        async with sse_open(app, progress_path(), cookies=fresh) as stream:
            assert stream.status == 200
            await stream.next_frames(1, WAIT_S)


async def test_streams_open_progress__S09_foreign_origin(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """`Origin` **có mà lệch** → 403 `ORIGIN_MISMATCH`, trước cả khi đọc cookie."""
    app = stream_app(providers=[progress_provider()], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        headers = {"Origin": "https://ke-tan-cong.example"}
        async with sse_open(app, progress_path(), cookies=owner.cookies, headers=headers) as stream:
            assert stream.status == 403
            assert json.loads(stream.body)["code"] == "ORIGIN_MISMATCH"


# ---------------------------------------------------------------------------
# Luồng bận vẫn giữ được chỗ (đặt tên theo việc, prompt [8])
# ---------------------------------------------------------------------------


async def test_busy_stream_keeps_its_slot(
    stream_app: StreamAppFactory,
    sse_open: SseOpen,
    db_session: AsyncSession,
    event_bus: EventBus,
    cache_client: AsyncRedis,
) -> None:
    """Luồng bận không bao giờ ping; chỗ giữ vẫn phải được gia hạn theo đồng hồ, không theo ping.

    `STREAM_HEARTBEAT_S=0.2` → TTL khoá ZSET 1 s. Sau 2 s mà mục vẫn còn nghĩa là vòng gia
    hạn đã chạy; nếu nó buộc vào nhịp ping thì khoá đã hết hạn từ giây đầu.
    """
    app = stream_app(providers=[progress_provider()], stream_heartbeat_s="0.2", stream_read_block_ms="100")
    async with signed_stream_user(app, db_session) as owner:
        key = conn_key(owner.user.id)
        async with sse_open(app, progress_path(), cookies=owner.cookies) as stream:
            await stream.next_frames(1, WAIT_S)
            stop = asyncio.Event()

            async def feed() -> None:
                """Một sự kiện mỗi 0,05 s cho tới khi test bảo dừng."""
                while not stop.is_set():
                    await publish_progress(event_bus, UPLOAD_ID)
                    await asyncio.sleep(0.05)

            feeder = asyncio.ensure_future(feed())
            try:
                await stream.raw_until(lambda raw: raw.count(b"id: ") >= 40, BUSY_WAIT_S)
                assert await cache_client.zcard(key) == 1
                assert PING.encode() not in stream._raw, "luồng bận không được ping"
            finally:
                stop.set()
                await feeder


async def test_snapshot_failure_releases_the_reserved_slot(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession, cache_client: AsyncRedis
) -> None:
    """Ảnh chụp ném lỗi hạ tầng **trước** byte đầu → 503 và chỗ giữ được trả lại ngay.

    Đây là đường "hỏng sau khi đã giữ chỗ": nếu `open_stream` không dọn thì mỗi lượt 503 ăn
    mất một chỗ của người dùng cho tới khi TTL khoá ZSET hết, và người đó bị 429 oan.
    """

    def boom(upload_id: str) -> Mapping[str, object]:
        """Nhà cung cấp mất Postgres: 503 chứ không trả ảnh chụp."""
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=RETRY_AFTER_S)

    app = stream_app(providers=[progress_provider(snapshot=FakeSnapshot(boom))], **FAST)
    async with signed_stream_user(app, db_session) as owner:
        async with sse_open(app, progress_path(), cookies=owner.cookies) as stream:
            assert stream.status == 503
            assert json.loads(stream.body)["code"] == "DEPENDENCY_UNAVAILABLE"
        assert await cache_client.zcard(conn_key(owner.user.id)) == 0
