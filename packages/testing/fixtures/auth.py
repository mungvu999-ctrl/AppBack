"""Fixture của đăng nhập và phiên (B1-01): app thật với verifier **thật**.

- `auth_env` — `api_env` + `ARGON2_PROFILE=test` (1 MiB, 1 vòng; chỉ hợp lệ khi `APP_ENV=test`);
- `auth_app` — `create_app(clock=fake_clock)` **không** tiêm verifier: B0-06 tự nhận
  `apps.api.auth.verifier.build_verifier`. DB an toàn (khoá đăng nhập, hạn mức theo IP)
  được `api_env` `FLUSHDB` sau mỗi test — mọi lượt ASGI đều từ `127.0.0.1`;
- `auth_client` — `make_api_client(auth_app)` (https, có observer vết case);
- `signed_in(user, *, client=None)` — đăng nhập **thật** qua API rồi refresh lấy access
  token. Lượt refresh đó đã xoay cookie: test về chính thuật toán refresh tự đăng nhập;
- `probe_app`/`probe_client` — app thật **cộng** một route được bảo vệ mẫu
  (`GET /api/auth-probe/me`, có đọc DB) để kiểm verifier thật. Đường của nó không khớp
  thao tác hợp đồng nào nên không sinh vết case hay mẫu golden.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Final

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from apps.api.auth.cookies import REFRESH_COOKIE
from apps.api.auth.settings import reset_auth_settings_cache
from apps.api.core.app import create_app, discover_routers
from apps.api.core.deps import CurrentPrincipal, DbSession
from apps.api.core.routing import protected_router
from packages.core.clock import Clock
from packages.core.settings import get_core_settings
from packages.db.models.auth import User
from packages.testing.factories.auth import TEST_PASSWORD
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.storage import PUBLIC_BASE_URL

ORIGIN: Final = {"Origin": PUBLIC_BASE_URL}
"""Header `Origin` đúng của app thử — mọi POST tới `/api/auth/*` đều cần (C24)."""

LOGIN_PATH: Final = "/api/auth/login"
REFRESH_PATH: Final = "/api/auth/refresh"
LOGOUT_PATH: Final = "/api/auth/logout"
PROBE_PATH: Final = "/api/auth-probe/me"

probe_router = protected_router(prefix="/auth-probe", tags=["auth-probe"])


@probe_router.get("/me")
async def auth_probe_me(principal: CurrentPrincipal, db: DbSession) -> dict[str, str]:
    """Route được bảo vệ mẫu: trả `Principal` do verifier thật dựng, sau một lượt đọc DB."""
    await db.execute(text("SELECT 1"))
    return {"userId": principal.user_id, "sessionId": principal.session_id, "role": principal.role}


@dataclass(frozen=True, slots=True)
class SignedIn:
    """Kết quả của `signed_in`: client giữ cookie, access token, `sid` của phiên."""

    client: httpx.AsyncClient
    access_token: str
    sid: str

    @property
    def headers(self) -> dict[str, str]:
        """Header `Authorization: Bearer` của phiên này."""
        return {"Authorization": f"Bearer {self.access_token}"}


type SignIn = Callable[..., Awaitable[SignedIn]]


@pytest.fixture
def auth_env(api_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Biến môi trường của app thật cộng hồ sơ băm nhanh của test."""
    monkeypatch.setenv("ARGON2_PROFILE", "test")
    reset_auth_settings_cache()
    yield
    reset_auth_settings_cache()


@pytest.fixture
def auth_app(auth_env: None, fake_clock: FakeClock) -> FastAPI:
    """App thật của repo, đồng hồ giả, verifier thật."""
    return create_app(get_core_settings(), clock=fake_clock)


@pytest_asyncio.fixture(loop_scope="function")
async def auth_client(auth_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client của `auth_app`, có chạy `lifespan`."""
    async with make_api_client(auth_app) as client:
        yield client


async def login(client: httpx.AsyncClient, email: str, password: str, *, remember: bool = True) -> httpx.Response:
    """Một lượt `POST /api/auth/login` đúng `Origin`."""
    body = {"email": email, "password": password, "rememberMe": remember}
    return await client.post(LOGIN_PATH, json=body, headers=ORIGIN)


async def sign_in(client: httpx.AsyncClient, user: User) -> SignedIn:
    """Login 204 rồi refresh 200 trên `client`; hỏng ở bước nào thì test hỏng ngay, nêu thân."""
    logged = await login(client, user.email, TEST_PASSWORD)
    assert logged.status_code == 204, logged.text  # noqa: S101 — fixture test
    refreshed = await client.post(REFRESH_PATH, headers=ORIGIN)
    assert refreshed.status_code == 200, refreshed.text  # noqa: S101 — fixture test
    sid = client.cookies[REFRESH_COOKIE].split(".", 1)[0]
    return SignedIn(client=client, access_token=refreshed.json()["accessToken"], sid=sid)


@pytest.fixture
def signed_in(auth_client: httpx.AsyncClient) -> SignIn:
    """Hàm đăng nhập thật một người dùng (mật khẩu `TEST_PASSWORD`), mặc định trên `auth_client`."""

    async def run(user: User, *, client: httpx.AsyncClient | None = None) -> SignedIn:
        """`sign_in` trên client cho trước, hay `auth_client`."""
        return await sign_in(client if client is not None else auth_client, user)

    return run


def build_probe_app(clock: Clock) -> FastAPI:
    """App thật của repo cộng `probe_router`, verifier thật (không tiêm)."""
    return create_app(get_core_settings(), clock=clock, routers=[*discover_routers(), ("auth_probe", probe_router)])


@pytest.fixture
def probe_app(auth_env: None, fake_clock: FakeClock) -> FastAPI:
    """`auth_app` cộng route được bảo vệ mẫu."""
    return build_probe_app(fake_clock)


@pytest_asyncio.fixture(loop_scope="function")
async def probe_client(probe_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client của `probe_app`, có chạy `lifespan`."""
    async with make_api_client(probe_app) as client:
        yield client
