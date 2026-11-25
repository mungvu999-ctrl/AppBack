"""Dependency quyền cấp hệ thống trên app thử: verifier **thật** của B1-01, Postgres và Redis thật.

Route mẫu không có dòng hợp đồng (trừ một route cố ý mang `users_list_users` để chứng
minh khoá `role:…` làm phép so của `test_routes.py` hỏng), nên tên test mô tả việc.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.access.deps import require_admin, require_permission, require_role
from apps.api.auth.sessions import principal_key
from apps.api.core.app import create_app, discover_routers
from apps.api.core.openapi import Operation, operations
from apps.api.core.permissions import ADMIN, ANY_MEMBER, ANY_ROLE
from apps.api.core.routing import protected_router
from apps.api.core.wire import WireRequest
from packages.core.settings import get_core_settings
from packages.db.models.auth import User
from packages.domain.permissions import PERMISSION_KEYS
from packages.messaging.redis import AsyncRedis
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.auth import sign_in
from packages.testing.fixtures.clock import FakeClock
from tools.charter import load_bind_rows

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
PREFIX: Final = "/api/access-probe"
BIND_OP: Final = "users_list_users"
"""Thao tác hợp đồng thật (khoá `user.manage`) mà route thử mượn — `operationId` là tên hàm (`route.name`)."""

STAFF: Final = require_role("engineer", "admin")

router = protected_router(prefix="/access-probe", tags=["access-probe"])


class RoleBody(WireRequest):
    """Thân mang `role` — để chứng minh vai không bao giờ đọc từ thân (K05)."""

    role: str


USER_MANAGE: Final = require_permission("user.manage")


@router.get("/users", dependencies=[Depends(USER_MANAGE)])
async def access_probe_user_manage() -> dict[str, bool]:
    """Route mẫu của `require_permission("user.manage")`."""
    return {"ok": True}


@router.post("/users", dependencies=[Depends(USER_MANAGE)])
async def access_probe_user_manage_body(body: RoleBody) -> dict[str, str]:
    """Route mẫu nhận JSON có `role`; cổng vẫn theo `Principal`."""
    return {"role": body.role}


@router.get("/admin", dependencies=[Depends(require_admin)])
async def access_probe_admin() -> dict[str, bool]:
    """Route mẫu của `require_admin`."""
    return {"ok": True}


@router.get("/staff", dependencies=[Depends(STAFF)])
async def access_probe_staff() -> dict[str, bool]:
    """Route mẫu của `require_role("engineer", "admin")`, không có dòng hợp đồng."""
    return {"ok": True}


@router.get("/bind-mismatch", dependencies=[Depends(STAFF)])
async def users_list_users() -> dict[str, bool]:
    """Mượn `operationId` của hợp đồng #38 với khoá sai — không bao giờ gọi, chỉ để quét."""
    return {"ok": True}


@pytest.fixture
def access_app(auth_env: None, fake_clock: FakeClock) -> FastAPI:
    """App thật của repo cộng `router` thử, verifier thật (không tiêm)."""
    return create_app(get_core_settings(), clock=fake_clock, routers=[*discover_routers(), ("access_probe", router)])


@pytest_asyncio.fixture(loop_scope="function")
async def access_client(access_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client của `access_app`, có chạy `lifespan`."""
    async with make_api_client(access_app) as client:
        yield client


async def _token(client: httpx.AsyncClient, db: AsyncSession, role: str) -> dict[str, str]:
    """Header `Authorization` của một người vai `role` vừa đăng nhập thật."""
    return (await sign_in(client, await make_user(db, role=role))).headers


def _assert_forbidden(response: httpx.Response) -> None:
    """403 đúng thân W7: chỉ `code` và `requestId` khớp header, không `resource`."""
    assert response.status_code == 403, response.text
    assert response.json() == {"code": "FORBIDDEN", "requestId": response.headers["X-Request-Id"]}


def _ops(app: FastAPI) -> dict[str, Operation]:
    """Metadata thao tác của app thử theo `operationId`."""
    return {operation.op: operation for operation in operations(app)}


@pytest.mark.parametrize(("role", "status"), [("admin", 200), ("engineer", 403), ("viewer", 403)])
async def test_require_permission_user_manage_admits_only_admin(
    access_client: httpx.AsyncClient, db_session: AsyncSession, role: str, status: int
) -> None:
    """`user.manage` chỉ admin; engineer — vai mạnh nhất sau admin — vẫn 403 (C07)."""
    response = await access_client.get(f"{PREFIX}/users", headers=await _token(access_client, db_session, role))
    if status == 403:
        _assert_forbidden(response)
    else:
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(("role", "status"), [("admin", 200), ("engineer", 403)])
async def test_require_admin_admits_only_admin(
    access_client: httpx.AsyncClient, db_session: AsyncSession, role: str, status: int
) -> None:
    """`require_admin`: admin 200, engineer 403."""
    response = await access_client.get(f"{PREFIX}/admin", headers=await _token(access_client, db_session, role))
    if status == 403:
        _assert_forbidden(response)
    else:
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(("role", "status"), [("engineer", 200), ("admin", 200), ("viewer", 403)])
async def test_require_role_off_bind_route_still_gates(
    access_client: httpx.AsyncClient, db_session: AsyncSession, role: str, status: int
) -> None:
    """`require_role("engineer", "admin")` trên route không có dòng hợp đồng vẫn chạy đúng."""
    response = await access_client.get(f"{PREFIX}/staff", headers=await _token(access_client, db_session, role))
    if status == 403:
        _assert_forbidden(response)
    else:
        assert response.status_code == 200, response.text


async def test_role_in_request_body_is_ignored(access_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """K05/K34: viewer gửi `{"role": "admin"}` vẫn 403 — vai chỉ từ `Principal`."""
    headers = await _token(access_client, db_session, "viewer")
    _assert_forbidden(await access_client.post(f"{PREFIX}/users", json={"role": "admin"}, headers=headers))


async def test_demoted_role_applies_once_cache_is_dropped(
    access_client: httpx.AsyncClient, db_session: AsyncSession, cache_client: AsyncRedis
) -> None:
    """C25/K34: hạ vai trong DB và xoá ảnh chụp phiên → lượt kế 403 ngay, không theo token cũ."""
    user = await make_user(db_session, role="admin")
    me = await sign_in(access_client, user)
    assert (await access_client.get(f"{PREFIX}/admin", headers=me.headers)).status_code == 200
    await db_session.execute(update(User).where(User.id == user.id).values(role="viewer"))
    await db_session.commit()
    assert await cache_client.delete(principal_key(me.sid)) == 1
    _assert_forbidden(await access_client.get(f"{PREFIX}/admin", headers=me.headers))


def test_operations_expose_permission_keys(access_app: FastAPI) -> None:
    """`permission_key` đọc được từ dependency: khoá quyền, `admin`, hay `role:<vai>`."""
    ops = _ops(access_app)
    assert ops["access_probe_user_manage"].permission_key == "user.manage"
    assert ops["access_probe_admin"].permission_key == ADMIN
    assert ops["access_probe_staff"].permission_key == "role:admin,engineer"


def test_role_key_on_bind_route_fails_the_route_scan(access_app: FastAPI) -> None:
    """Phép so của `test_permission_keys_match_bind_rows` bắt route hợp đồng gắn `require_role` lệch."""
    rows = {row.operation_id: row for row in load_bind_rows(REPO_ROOT)}
    mismatched = {
        operation.op
        for operation in operations(access_app)
        if operation.op in rows and operation.permission_key != rows[operation.op].lock
    }
    assert mismatched == {BIND_OP}
    key = _ops(access_app)[BIND_OP].permission_key
    assert key not in {ANY_ROLE, ANY_MEMBER, ADMIN, *PERMISSION_KEYS}


@pytest.mark.parametrize("key", ["layer.edit", "khoa.la", ""])
def test_require_permission_rejects_non_system_keys(key: str) -> None:
    """Khoá dự án hay khoá lạ → `ValueError` lúc khai (K08)."""
    with pytest.raises(ValueError, match="cấp hệ thống"):
        require_permission(key)


@pytest.mark.parametrize("roles", [(), ("root",), ("admin", "root")])
def test_require_role_rejects_empty_or_unknown_roles(roles: tuple[str, ...]) -> None:
    """Không vai hay vai lạ → `ValueError` lúc khai (fail-closed)."""
    with pytest.raises(ValueError, match="vai"):
        require_role(*roles)


def test_factories_are_memoised() -> None:
    """Cùng khoá hay cùng tập vai (khác thứ tự) → cùng dependency; khoá khác → khác."""
    assert require_permission("user.manage") is require_permission("user.manage")
    assert require_role("admin", "engineer") is STAFF
    assert require_role("admin") is require_admin
    assert require_permission("library.manage") is not require_admin
