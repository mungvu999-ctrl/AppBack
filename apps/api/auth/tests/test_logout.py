"""`POST /api/auth/logout` (hợp đồng #4; CASE loại P): idempotent, luôn xoá hai cookie."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.cookies import REFRESH_COOKIE, STREAM_COOKIE
from apps.api.auth.tests.support import refresh_cookie, session_row, set_cookies
from packages.messaging.redis import AsyncRedis
from packages.testing.factories.auth import TEST_PASSWORD, make_user
from packages.testing.fixtures.auth import LOGOUT_PATH, ORIGIN, login


def _clears_both(response: httpx.Response) -> None:
    """Hai lệnh xoá: cùng tên, cùng `Path`, `Max-Age=0` (BE-00 §5)."""
    cookies = set_cookies(response)
    assert "Path=/api/auth" in cookies[REFRESH_COOKIE]
    assert "Path=/api/streams" in cookies[STREAM_COOKIE]
    for header in cookies.values():
        assert "Max-Age=0" in header


def _with_cookie(value: str, origin: dict[str, str]) -> dict[str, str]:
    """Header của một lượt đăng xuất mang đúng cookie refresh cho trước."""
    return {**origin, "Cookie": f"{REFRESH_COOKIE}={value}"}


async def test_auth_logout__C01(
    auth_client: httpx.AsyncClient, db_session: AsyncSession, cache_client: AsyncRedis
) -> None:
    """Đúng: 204 thân rỗng, xoá hai cookie, phiên bị thu hồi (`logout`), ảnh chụp cache bị xoá.
    Catalog: B11-out-001.
    """
    user = await make_user(db_session)
    cookie = refresh_cookie(await login(auth_client, user.email, TEST_PASSWORD))
    sid = cookie.split(".", 1)[0]
    await cache_client.set(f"auth:principal:{sid}", "{}")
    response = await auth_client.post(LOGOUT_PATH, headers=_with_cookie(cookie, ORIGIN))
    assert response.status_code == 204
    assert response.content == b""
    _clears_both(response)
    row = await session_row(db_session, sid)
    assert row.revoked_at is not None
    assert row.revoked_reason == "logout"
    assert not await cache_client.exists(f"auth:principal:{sid}")


async def test_auth_logout__C24(auth_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`Origin` lệch hay thiếu → 403 `ORIGIN_MISMATCH` **kèm** lệnh xoá cookie; phiên không bị thu hồi.
    Catalog: B11-out-004.
    """
    user = await make_user(db_session)
    cookie = refresh_cookie(await login(auth_client, user.email, TEST_PASSWORD))
    foreign = await auth_client.post(LOGOUT_PATH, headers=_with_cookie(cookie, {"Origin": "https://ke-gian.example"}))
    missing = await auth_client.post(LOGOUT_PATH, headers=_with_cookie(cookie, {}))
    for response in (foreign, missing):
        assert response.status_code == 403
        assert response.json()["code"] == "ORIGIN_MISMATCH"
        _clears_both(response)
    assert (await session_row(db_session, cookie.split(".", 1)[0])).revoked_at is None


async def test_logout_without_a_cookie_is_204(auth_client: httpx.AsyncClient) -> None:
    """Không cookie (hay cookie rác) → vẫn 204 và vẫn xoá cookie.
    Catalog: B11-out-003.
    """
    auth_client.cookies.clear()
    bare = await auth_client.post(LOGOUT_PATH, headers=ORIGIN)
    junk = await auth_client.post(LOGOUT_PATH, headers=_with_cookie("rac", ORIGIN))
    for response in (bare, junk):
        assert response.status_code == 204
        _clears_both(response)


async def test_logout_twice_is_204(auth_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Đăng xuất lặp lại cùng cookie → 204 cả hai lần; lần hai không đổi lý do thu hồi.
    Catalog: B11-out-002.
    """
    user = await make_user(db_session)
    cookie = refresh_cookie(await login(auth_client, user.email, TEST_PASSWORD))
    first = await auth_client.post(LOGOUT_PATH, headers=_with_cookie(cookie, ORIGIN))
    second = await auth_client.post(LOGOUT_PATH, headers=_with_cookie(cookie, ORIGIN))
    assert (first.status_code, second.status_code) == (204, 204)
    assert (await session_row(db_session, cookie.split(".", 1)[0])).revoked_reason == "logout"
