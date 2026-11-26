"""`POST /api/auth/login` (hợp đồng #1, B1-01 [6], [8]; CASE loại C)."""

import asyncio
import logging
import statistics
import time
import unicodedata
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any, Final

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth import passwords
from apps.api.auth.cookies import REFRESH_COOKIE, STREAM_COOKIE
from apps.api.auth.emails import email_key
from apps.api.auth.passwords import hash_slot, needs_rehash
from apps.api.auth.settings import get_auth_settings, reset_auth_settings_cache
from apps.api.auth.tests.support import (
    client_from,
    cookie_value,
    session_row,
    set_cookies,
    user_row,
    wait_until,
)
from apps.api.core.app import create_app
from packages.core.logging import JsonFormatter
from packages.core.settings import get_core_settings
from packages.db.models.auth import User
from packages.db.settings import reset_database_settings_cache
from packages.messaging.redis import AsyncRedis
from packages.testing.factories.auth import TEST_PASSWORD, make_user
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.auth import LOGIN_PATH, ORIGIN, PROBE_PATH, build_probe_app, login, sign_in
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import ephemeral_broker

_log: Final = logging.getLogger(__name__)

WRONG_PASSWORD: Final = "khong-dung-mat-khau"  # noqa: S105 — mật khẩu sai có chủ đích của test
LOCAL_IP: Final = "127.0.0.1"
TIMING_ROUNDS: Final = 20
MAX_MEDIAN_GAP_S: Final = 0.050
POOL_LOGINS: Final = 40
IDLE_POLLS: Final = 4


def _raise_limits(monkeypatch: pytest.MonkeyPatch, **values: int) -> None:
    """Đổi hạn mức qua biến môi trường rồi đọc lại cấu hình (cấu hình đọc lười mỗi request)."""
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))
    reset_auth_settings_cache()


def _without_request_id(response: httpx.Response) -> dict[str, object]:
    """Thân lỗi bỏ `requestId` — phần hai nhánh chống dò phải giống hệt nhau."""
    body = dict(response.json())
    body.pop("requestId")
    return body


async def test_auth_login__C01(auth_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Đúng: 204 thân rỗng, hai cookie đúng thuộc tính BE-00 §5, phiên ghi nhớ 7/30 ngày."""
    user = await make_user(db_session)
    response = await login(auth_client, user.email, TEST_PASSWORD)
    assert response.status_code == 204
    assert response.content == b""
    cookies = set_cookies(response)
    refresh, stream = cookies[REFRESH_COOKIE], cookies[STREAM_COOKIE]
    for header in (refresh, stream):
        lowered = header.lower()
        assert "httponly" in lowered
        assert "secure" in lowered
        assert "samesite=strict" in lowered
    assert "Path=/api/auth" in refresh
    assert f"Max-Age={int(timedelta(days=7).total_seconds())}" in refresh
    assert "Path=/api/streams" in stream
    assert "Max-Age=600" in stream
    sid = cookie_value(refresh).split(".", 1)[0]
    row = await session_row(db_session, sid)
    now = fake_clock.now()
    assert (row.remember, row.idle_expires_at, row.absolute_expires_at) == (
        True,
        now + timedelta(days=7),
        now + timedelta(days=30),
    )
    assert str(row.created_ip) == LOCAL_IP
    assert len(row.current_token_hash) == 64
    assert (await user_row(db_session, user.id)).last_active_at == now


async def test_auth_login__C02(auth_client: httpx.AsyncClient) -> None:
    """`rememberMe` không phải boolean (zod không ép kiểu) → 422 `field:"rememberMe"`."""
    body = {"email": "ai-do@example.com", "password": TEST_PASSWORD, "rememberMe": "yes"}
    response = await auth_client.post(LOGIN_PATH, json=body, headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"
    assert response.json()["field"] == "rememberMe"


async def test_auth_login__C02_short_password(auth_client: httpx.AsyncClient) -> None:
    """Mật khẩu ngắn hơn `MIN_PASSWORD_LENGTH` của FE → 422 `field:"password"`."""
    body = {"email": "ai-do@example.com", "password": "ngan", "rememberMe": True}
    response = await auth_client.post(LOGIN_PATH, json=body, headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["field"] == "password"


@pytest.mark.parametrize(
    "email",
    [
        "admin@localhost",
        "\u00e1nh@cty.vn",
        "ro\u017fe@example.com",
        "\u212aelvin@example.com",
        "\u0130@x.com",
        "\u0131@example.com",
        ".dau-cham@example.com",
        "hai..cham@example.com",
        "",
        "a@" + "b" * 250 + ".com",
    ],
    ids=["localhost", "unicode", "long-s", "kelvin", "i-cham", "i-khong-cham", "cham-dau", "hai-cham", "rong", "dai"],
)
async def test_auth_login__C02_email(auth_client: httpx.AsyncClient, email: str) -> None:
    """Email ngoài `.email()` của zod 3.23.8 (K37) → 422 `field:"email"`."""
    body = {"email": email, "password": TEST_PASSWORD, "rememberMe": True}
    response = await auth_client.post(LOGIN_PATH, json=body, headers=ORIGIN)
    assert response.status_code == 422
    assert response.json() | {"requestId": ""} == {"code": "VALIDATION", "field": "email", "count": 1, "requestId": ""}


async def test_auth_login__C03(auth_client: httpx.AsyncClient) -> None:
    """Khoá lạ (`fullName` của tab đăng ký) → 422 `VALIDATION` (`SignInSchema` strict)."""
    body = {"email": "ai-do@example.com", "password": TEST_PASSWORD, "rememberMe": True, "fullName": "A"}
    response = await auth_client.post(LOGIN_PATH, json=body, headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


async def test_auth_login__C11(auth_client: httpx.AsyncClient) -> None:
    """Lượt thứ 31 trong 60 s từ một IP → 429 kèm `Retry-After` ≤ 10 (email khác nhau mỗi lượt)."""
    limit = get_auth_settings().login_ip_limit
    for index in range(limit):
        response = await login(auth_client, f"nguoi-la-{index}@example.com", WRONG_PASSWORD)
        assert response.status_code == 401, index
    blocked = await login(auth_client, "nguoi-la-cuoi@example.com", WRONG_PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert 1 <= int(blocked.headers["Retry-After"]) <= 10


async def test_auth_login__C16(auth_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Email khác hoa thường vẫn là một người; mật khẩu đặt dạng NFD đăng nhập được bằng NFC (và NFD)."""
    nfc_password = "Mật-khẩu-tiếng-Việt"  # noqa: S105 — mật khẩu giả của test
    nfd_password = unicodedata.normalize("NFD", nfc_password)
    assert nfd_password != nfc_password
    user = await make_user(db_session, email="Anh.Nguyen@Cty.vn", password=nfd_password)
    assert (await login(auth_client, "anh.nguyen@CTY.VN", nfc_password)).status_code == 204
    assert (await login(auth_client, "  ANH.NGUYEN@cty.vn ", nfd_password)).status_code == 204
    assert user.email == "Anh.Nguyen@Cty.vn"


async def test_auth_login__C24(auth_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thiếu hoặc lệch `Origin` → 403 `ORIGIN_MISMATCH`, trước cả kiểm thân."""
    user = await make_user(db_session)
    body = {"email": user.email, "password": TEST_PASSWORD, "rememberMe": True}
    missing = await auth_client.post(LOGIN_PATH, json=body)
    foreign = await auth_client.post(LOGIN_PATH, json=body, headers={"Origin": "https://ke-gian.example"})
    broken = await auth_client.post(LOGIN_PATH, json={"x": 1}, headers={"Origin": "https://ke-gian.example"})
    for response in (missing, foreign, broken):
        assert response.status_code == 403
        assert response.json()["code"] == "ORIGIN_MISMATCH"
        assert REFRESH_COOKIE not in set_cookies(response)


async def _timed_login(client: httpx.AsyncClient, email: str) -> tuple[httpx.Response, float]:
    """Một lượt đăng nhập sai và thời gian của nó."""
    started = time.perf_counter()
    response = await login(client, email, WRONG_PASSWORD)
    return response, time.perf_counter() - started


async def test_auth_login__C27(
    auth_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chống dò: email có thật (sai mật khẩu) và email lạ cùng 401, cùng thân, trung vị lệch < 50 ms."""
    _raise_limits(monkeypatch, LOGIN_FAILURE_LIMIT=1000, LOGIN_EMAIL_FAILURE_LIMIT=1000, LOGIN_IP_LIMIT=1000)
    user = await make_user(db_session)
    stranger = "chua-tung-co@example.com"
    await _timed_login(auth_client, user.email)  # lượt làm ấm: băm giả dựng lười, pool mở kết nối
    await _timed_login(auth_client, stranger)
    known: list[float] = []
    unknown: list[float] = []
    for _ in range(TIMING_ROUNDS):
        for email, bucket in ((user.email, known), (stranger, unknown)):
            response, elapsed = await _timed_login(auth_client, email)
            assert response.status_code == 401
            assert _without_request_id(response) == {"code": "INVALID_CREDENTIALS"}
            bucket.append(elapsed)
    gap = abs(statistics.median(known) - statistics.median(unknown))
    _log.info(
        "c27 known_median_ms=%.2f unknown_median_ms=%.2f gap_ms=%.2f",
        statistics.median(known) * 1000,
        statistics.median(unknown) * 1000,
        gap * 1000,
    )
    assert len(known) + len(unknown) == 2 * TIMING_ROUNDS
    assert gap < MAX_MEDIAN_GAP_S


async def test_auth_login__C27_real_limits(auth_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Hạn mức thật: hai nhánh cùng bị 429 ở đúng lượt `LOGIN_FAILURE_LIMIT + 1`."""
    user = await make_user(db_session)
    limit = get_auth_settings().login_failure_limit
    for email in (user.email, "khong-ton-tai@example.com"):
        statuses = [(await login(auth_client, email, WRONG_PASSWORD)).status_code for _ in range(limit + 1)]
        assert statuses == [401] * limit + [429], email


async def test_pending_user_gets_the_same_401_as_a_wrong_password(
    auth_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Người `pending` (kể cả khi dòng lỡ có băm) → 401 đúng như sai mật khẩu."""
    pending = await make_user(db_session, status="pending")
    active = await make_user(db_session)
    first = await login(auth_client, pending.email, TEST_PASSWORD)
    second = await login(auth_client, active.email, WRONG_PASSWORD)
    assert (first.status_code, second.status_code) == (401, 401)
    assert _without_request_id(first) == _without_request_id(second)


async def test_disabled_user_learns_nothing_without_the_password(
    auth_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`disabled`: sai mật khẩu → 401; đúng mật khẩu → 403 `ACCOUNT_DISABLED`, không phiên nào."""
    user = await make_user(db_session, status="disabled")
    wrong = await login(auth_client, user.email, WRONG_PASSWORD)
    right = await login(auth_client, user.email, TEST_PASSWORD)
    assert wrong.status_code == 401
    assert right.status_code == 403
    assert right.json()["code"] == "ACCOUNT_DISABLED"
    assert REFRESH_COOKIE not in set_cookies(right)


async def test_unusable_stored_hash_is_a_wrong_password(
    auth_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Băm hỏng trong DB → 401 như sai mật khẩu (có log), không 500."""
    user = await make_user(db_session)
    await db_session.execute(update(User).where(User.id == user.id).values(password_hash="khong-phai-argon2"))  # noqa: S106 — băm hỏng có chủ đích
    await db_session.commit()
    response = await login(auth_client, user.email, TEST_PASSWORD)
    assert response.status_code == 401


async def _lock_gone(safe: AsyncRedis, key: str) -> bool:
    """Khoá Redis đã hết hạn (theo giờ thật)."""
    return not await safe.exists(key)


async def test_sixth_attempt_is_locked_until_the_counter_expires(
    auth_client: httpx.AsyncClient, db_session: AsyncSession, safe_client: AsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5 lần sai → lần 6 bị 429 dù đúng; hết khoá mà bộ đếm còn → lại khoá (BE-00 §11); hết đếm → vào được."""
    _raise_limits(monkeypatch, LOGIN_LOCK_S=1, LOGIN_FAILURE_WINDOW_S=4)
    user = await make_user(db_session)
    k = email_key(user.email)
    for _ in range(get_auth_settings().login_failure_limit):
        assert (await login(auth_client, user.email, WRONG_PASSWORD)).status_code == 401
    locked = await login(auth_client, user.email, TEST_PASSWORD)
    assert locked.status_code == 429
    assert locked.json()["code"] == "RATE_LIMITED"
    lock_key, fail_key = f"auth:login:lock:{k}:{LOCAL_IP}", f"auth:login:fail:{k}:{LOCAL_IP}"
    await wait_until(lambda: _lock_gone(safe_client, lock_key))
    assert await safe_client.exists(fail_key)
    assert (await login(auth_client, user.email, TEST_PASSWORD)).status_code == 429
    await wait_until(lambda: _lock_gone(safe_client, fail_key))
    await wait_until(lambda: _lock_gone(safe_client, lock_key))
    assert (await login(auth_client, user.email, TEST_PASSWORD)).status_code == 204
    assert not await safe_client.exists(fail_key)


async def test_email_soft_limit_admits_only_known_addresses(
    auth_app: FastAPI, auth_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """20 lần sai từ nhiều IP → IP lạ bị 429 dù đúng mật khẩu; IP từng đăng nhập đúng vẫn vào được."""
    user = await make_user(db_session)
    async with client_from(auth_app, "10.0.0.1") as known:
        assert (await login(known, user.email, TEST_PASSWORD)).status_code == 204
        for index in range(get_auth_settings().login_email_failure_limit):
            async with client_from(auth_app, f"10.0.1.{index + 1}") as attacker:
                assert (await login(attacker, user.email, WRONG_PASSWORD)).status_code == 401
        async with client_from(auth_app, "10.0.2.1") as stranger:
            blocked = await login(stranger, user.email, TEST_PASSWORD)
        assert blocked.status_code == 429
        assert (await login(known, user.email, TEST_PASSWORD)).status_code == 204


# FIX-112: trần **đồng hồ tường**. Bước 5 chạy `pytest -n 6`, mỗi tiến trình chỉ được ~1/6 CPU nên
# trần đo lúc tuần tự không thể đạt (đo: `wait_until(timeout_s=1.5)` quá hạn); chạy riêng thì đạt. Marker `perf` là đúng
# cơ chế của repo cho loại này — bước 5b chạy `-m perf` **tuần tự**, không dưới coverage (BE-00 §12).
@pytest.mark.perf
async def test_no_connection_is_held_while_hashing(
    auth_env: None,
    fake_clock: FakeClock,
    safe_client: AsyncRedis,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K36: pool 1 kết nối, 40 lượt đăng nhập kẹt chờ băm → pool trống, route bảo vệ đọc DB vẫn < 1 s."""
    for name, value in {"DB_POOL_SIZE": "1", "DB_MAX_OVERFLOW": "0", "DB_POOL_TIMEOUT_S": "1"}.items():
        monkeypatch.setenv(name, value)
    reset_database_settings_cache()
    _raise_limits(monkeypatch, LOGIN_IP_LIMIT=1000)
    # Test giữ mọi chỗ băm trong lúc chờ pool rảnh và gọi route mẫu: trần chờ 2 s của sản phẩm
    # thì máy chậm sẽ đỏ giả (503) — thứ đang kiểm là kết nối DB, không phải trần chờ băm.
    monkeypatch.setattr(passwords, "HASH_WAIT_S", 30.0)
    user = await make_user(db_session)
    app = build_probe_app(fake_clock)
    async with make_api_client(app) as client:
        me = await sign_in(client, user)
        async with AsyncExitStack() as held:
            for _ in range(get_auth_settings().password_hash_concurrency):
                await held.enter_async_context(hash_slot())
            waiting = [
                asyncio.create_task(login(client, f"ngau-nhien-{index}@example.com", WRONG_PASSWORD))
                for index in range(POOL_LOGINS)
            ]
            pool, fired = app.state.engine.pool, time.perf_counter()
            await wait_until(lambda: _fail_keys_at_least(safe_client, POOL_LOGINS), timeout_s=1.5)
            # Vi phạm K36 thì lượt đầu giữ kết nối duy nhất suốt lúc chờ băm: pool không bao giờ rảnh.
            idle = _SustainedIdle(pool)
            await wait_until(idle.check, timeout_s=1.5)
            _log.info("pool idle_after_ms=%.1f", (time.perf_counter() - fired) * 1000)
            assert pool.checkedout() == 0
            started = time.perf_counter()
            probe = await client.get(PROBE_PATH, headers=me.headers)
            elapsed = time.perf_counter() - started
            _log.info("pool probe_ms=%.1f", elapsed * 1000)
            assert probe.status_code == 200
            assert elapsed < 1.0
        results = await asyncio.gather(*waiting)
    assert [response.status_code for response in results] == [401] * POOL_LOGINS


class _SustainedIdle:
    """Pool request rảnh `IDLE_POLLS` lượt liền nhau: mọi lượt đăng nhập đã qua bước đọc DB."""

    def __init__(self, pool: Any) -> None:
        """Theo dõi một pool, đếm từ 0."""
        self._pool = pool
        self._streak = 0

    async def check(self) -> bool:
        """Một lượt quan sát; pool bận là đếm lại từ đầu."""
        self._streak = self._streak + 1 if int(self._pool.checkedout()) == 0 else 0
        return self._streak >= IDLE_POLLS


async def _fail_keys_at_least(safe: AsyncRedis, count: int) -> bool:
    """Đủ `count` bộ đếm lượt thử: mọi lượt đăng nhập đã qua bước 3."""
    keys = [key async for key in safe.scan_iter(match="auth:login:fail:*")]
    return len(keys) >= count


async def test_safe_redis_down_is_503(
    auth_env: None, fake_clock: FakeClock, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB an toàn dừng (khoá đăng nhập không đọc được) → 503, không cho qua (fail-closed)."""
    user = await make_user(db_session)
    with ephemeral_broker(monkeypatch) as admin:
        async with make_api_client(create_app(get_core_settings(), clock=fake_clock)) as client:
            admin.shutdown(nosave=True)
            response = await login(client, user.email, TEST_PASSWORD)
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "Retry-After" in response.headers


async def test_outdated_hash_is_rehashed_on_login(auth_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Băm với tham số cũ (`time_cost=2`) → đăng nhập đúng thì băm lại theo hồ sơ hiện hành."""
    user = await make_user(db_session, password=None)
    old = PasswordHasher(time_cost=2, memory_cost=1024, parallelism=1).hash(TEST_PASSWORD)
    await db_session.execute(update(User).where(User.id == user.id).values(password_hash=old))
    await db_session.commit()
    assert needs_rehash(old)
    assert (await login(auth_client, user.email, TEST_PASSWORD)).status_code == 204
    fresh = (await user_row(db_session, user.id)).password_hash
    assert fresh is not None
    assert fresh != old
    assert not needs_rehash(fresh)


async def test_remember_me_false_is_a_browser_session(
    auth_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """`rememberMe:false` → cookie refresh không `Max-Age`; phiên idle 12 giờ, tuyệt đối 24 giờ."""
    user = await make_user(db_session)
    response = await login(auth_client, user.email, TEST_PASSWORD, remember=False)
    assert response.status_code == 204
    refresh = set_cookies(response)[REFRESH_COOKIE]
    assert "max-age" not in refresh.lower()
    assert "expires" not in refresh.lower()
    row = await session_row(db_session, cookie_value(refresh).split(".", 1)[0])
    now = fake_clock.now()
    assert (row.remember, row.idle_expires_at, row.absolute_expires_at) == (
        False,
        now + timedelta(hours=12),
        now + timedelta(hours=24),
    )


async def test_register_is_not_mounted(auth_client: httpx.AsyncClient) -> None:
    """K7 = B: không có đăng ký công khai → 404."""
    body = {"email": "moi@example.com", "password": TEST_PASSWORD, "rememberMe": True, "fullName": "Mới"}
    response = await auth_client.post("/api/auth/register", json=body, headers=ORIGIN)
    assert response.status_code == 404


async def test_login_over_another_users_cookie_revokes_that_session(
    auth_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Trình duyệt dùng chung: người sau đăng nhập thì phiên của người trước bị thu hồi (`replaced`)."""
    first, second = await make_user(db_session), await make_user(db_session)
    earlier = await login(auth_client, first.email, TEST_PASSWORD)
    old_sid = cookie_value(set_cookies(earlier)[REFRESH_COOKIE]).split(".", 1)[0]
    later = await login(auth_client, second.email, TEST_PASSWORD)
    assert later.status_code == 204
    old = await session_row(db_session, old_sid)
    assert old.revoked_at is not None
    assert old.revoked_reason == "replaced"


async def test_lock_is_logged_once_without_the_email(
    auth_client: httpx.AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Khoá vừa đặt → một dòng `login_throttled` mang `email_key`; lượt đập vào khoá đang có không log thêm."""
    user = await make_user(db_session)
    limit = get_auth_settings().login_failure_limit
    with caplog.at_level(logging.WARNING, logger="apps.api.auth.login_guard"):
        statuses = [(await login(auth_client, user.email, WRONG_PASSWORD)).status_code for _ in range(limit + 2)]
    assert statuses == [401] * limit + [429, 429]
    records = [record for record in caplog.records if record.getMessage() == "login_throttled"]
    assert len(records) == 1
    line = JsonFormatter().format(records[0])
    assert f'"emailKey": "{email_key(user.email)}"' in line
    assert '"reason": "email_ip_locked"' in line
    assert all(user.email not in JsonFormatter().format(record) for record in caplog.records)
