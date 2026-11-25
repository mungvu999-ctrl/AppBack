"""Fixture HTTP dùng chung cho mọi module `apps/api/*` (B0-06, BE-00 §2.2).

- `api_app` — app thật, trỏ vào Postgres/Redis/kho **thật** của fixture dịch vụ
  (K23 cấm mock chúng), với `FakeTokenVerifier` và `fake_clock`;
- `make_api_client(app)` — client ASGI **có chạy `lifespan`**; prompt sau dùng nó
  cho app của mình. `base_url` là `https://` để cookie `Secure` đi qua được;
- `API_RESPONSE_OBSERVERS` — mọi response đi qua client được đưa cho các observer
  đã đăng ký. Observer sẵn có ghi **vết case** cho `tools/case_gate.py`: không có
  vết thì một test đặt đúng tên vẫn không được tính (cổng case).

Bucket rate limit sống ở `redis-cache` **và** ở DB an toàn (`store="safe"`, khoá đăng
nhập), mà mọi lượt ASGI đều đến từ IP `127.0.0.1`; vì vậy `api_env` phụ thuộc cả
`cache_client` lẫn `safe_client` để cả hai DB được `FLUSHDB` sau mỗi test, không thì
test sau bị chính test trước làm 429 (NO-055).
"""

import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from apps.api.auth.settings import reset_auth_settings_cache
from apps.api.core.app import create_app
from apps.api.core.auth import FakeTokenVerifier, Principal, fake_token
from packages.core.ids import new_id
from packages.core.settings import get_core_settings, reset_settings_cache
from packages.db.engine import GATE_CONNECT_TIMEOUT_S
from packages.db.settings import reset_database_settings_cache
from packages.messaging.redis import AsyncRedis
from packages.storage.settings import reset_storage_settings_cache
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.storage import PUBLIC_BASE_URL, STORAGE_SECRET
from packages.testing.golden.recorder import resolve_operation

BASE_URL: Final = "https://testserver"
TRACE_ENV: Final = "CASE_TRACE_FILE"
JSON_PREFIX: Final = "application/json"

type ResponseObserver = Callable[[pytest.Item, httpx.Response], None]

API_RESPONSE_OBSERVERS: Final = pytest.StashKey[list[ResponseObserver]]()

_current_item: pytest.Item | None = None


def pytest_configure(config: pytest.Config) -> None:
    """Dựng sổ observer một lần và gắn sẵn observer ghi vết case."""
    observers = config.stash.setdefault(API_RESPONSE_OBSERVERS, [])
    if trace_case not in observers:
        observers.append(trace_case)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Nhớ test đang chạy — observer nhận nó cùng với response."""
    global _current_item  # sổ của plugin pytest; một tiến trình chỉ chạy một test mỗi lúc
    _current_item = item


def operation_of(method: str, path: str) -> str | None:
    """`operationId` của một request đã gửi, hay `None` nếu nó không thuộc app thật.

    Dùng chung bảng khớp với bộ ghi golden (FIX-028, R-07): vết case và mẫu golden của
    cùng một response luôn quy về cùng thao tác. Tên được gắn lúc nhập, nên test của bộ ghi
    thay `recorder.resolve_operation` bằng bảng của app thử không làm vết case lệch theo.
    """
    matched = resolve_operation(method, path)
    return None if matched is None else matched.op


def _error_code(response: httpx.Response) -> str | None:
    """Mã lỗi W7 trong thân, nếu có — `case_gate` đối chiếu nó với case."""
    if not response.headers.get("content-type", "").startswith(JSON_PREFIX):
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def trace_case(item: pytest.Item, response: httpx.Response) -> None:
    """Ghi một dòng JSON Lines vào `CASE_TRACE_FILE` (docstring `tools/case_gate.py`)."""
    path = os.environ.get(TRACE_ENV)
    if not path:
        return
    op = operation_of(response.request.method, response.request.url.path)
    if op is None:
        return
    line = json.dumps(
        {"test": item.name, "op": op, "status": response.status_code, "code": _error_code(response)},
        ensure_ascii=False,
    )
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _notify(response: httpx.Response) -> None:
    """Đưa response cho mọi observer; observer hỏng thì test hỏng, không im lặng."""
    item = _current_item
    if item is None:
        return
    for observer in item.config.stash.get(API_RESPONSE_OBSERVERS, []):
        observer(item, response)


class ObservedClient(httpx.AsyncClient):
    """`httpx.AsyncClient` gọi observer sau mỗi response đã đọc xong thân."""

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        """Gửi như `httpx`, rồi đưa response (đã đọc thân) cho các observer."""
        response = await super().request(*args, **kwargs)
        _notify(response)
        return response


@asynccontextmanager
async def make_api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client ASGI cho một app, **có chạy `lifespan`** (không chạy thì `app.state` rỗng)."""
    async with (
        app.router.lifespan_context(app),
        ObservedClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        yield client


@pytest.fixture
def api_env(
    db_url: str,
    tmp_path: Path,
    messaging_env: None,
    cache_client: AsyncRedis,
    safe_client: AsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Biến môi trường của một app thật trỏ vào dịch vụ thật của test."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC_BASE_URL)
    monkeypatch.setenv("SECRET_KEY", STORAGE_SECRET)
    monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Trần bắt tay của **đường cổng**, không phải 10 s của đường phục vụ request: app thử
    # nối Postgres qua chặng `host.docker.internal` hay kẹt tới ~68 s (NO-002, NO-007).
    # Mỗi test một engine mới, nên để 10 s là cổng đỏ giả theo số lượng test.
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_S", str(int(GATE_CONNECT_TIMEOUT_S)))
    # Tắt exporter `/metrics`: `metrics_lifespan` gắn vào router công khai, nên để mặc định
    # 9464 là **mọi** app test mở một cổng thật rồi chờ tới `POLL_INTERVAL_S` lúc tắt — đo
    # được ~1,8 s trên 215 test của module khác (NO-159). Test lifespan tự đặt cổng riêng.
    monkeypatch.setenv("METRICS_PORT", "0")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    # Cùng gốc với fixture `local_storage`: test ghi object bằng nó, app đọc lại được.
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))
    # Hồ sơ argon2 nhanh (R1 BE-5): không đặt thì `make_user()` băm bằng `rfc9106_low_memory`
    # (61,76 ms/lượt) thay vì `test` (1,61 ms/lượt) — 626/754 lượt gọi không qua `auth_env`.
    monkeypatch.setenv("ARGON2_PROFILE", "test")
    _reset_settings()
    yield
    _reset_settings()


def _reset_settings() -> None:
    """Bốn cache cấu hình (core, db, storage, auth) đều đọc biến môi trường một lần mỗi tiến trình."""
    reset_settings_cache()
    reset_database_settings_cache()
    reset_storage_settings_cache()
    reset_auth_settings_cache()


@pytest.fixture
def api_app(api_env: None, fake_clock: FakeClock) -> FastAPI:
    """App thật của repo (mọi `apps/api/*/router.py` đã hợp nhất)."""
    return create_app(get_core_settings(), token_verifier=FakeTokenVerifier(), clock=fake_clock)


@pytest_asyncio.fixture(loop_scope="function")
async def api_client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client của app thật — dùng cho test case chung và test của từng route."""
    async with make_api_client(api_app) as client:
        yield client


@pytest.fixture
def fake_principal(fake_clock: FakeClock) -> Principal:
    """Người gọi mặc định của test: vai `admin` (test nào cần vai yếu hơn thì tự dựng)."""
    return Principal(user_id=new_id("usr", fake_clock), session_id="sid-test", role="admin")


def auth_headers(principal: Principal) -> dict[str, str]:
    """Header `Authorization` mà `FakeTokenVerifier` nhận cho một `Principal`."""
    return {"Authorization": f"Bearer {fake_token(principal)}"}
