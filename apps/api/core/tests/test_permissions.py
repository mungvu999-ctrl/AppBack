"""Sổ quyền, `Origin`, `Principal` và dependency tài nguyên của khung."""

from typing import Final

import httpx
import pytest

from apps.api.core import auth as core_auth
from apps.api.core.auth import (
    DenyAllTokenVerifier,
    FakeTokenVerifier,
    Principal,
    current_principal,
    fake_token,
)
from apps.api.core.permissions import ANY_ROLE, permission_dependency, permission_key_of, registered
from apps.api.core.tests.sample import deny_sample, sample_app, sample_client
from packages.core.errors import AppError
from packages.core.ids import new_id
from packages.domain import permissions as domain_permissions
from packages.testing.fixtures.api import auth_headers
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.storage import PUBLIC_BASE_URL

__all__ = ["sample_app", "sample_client"]

FOREIGN_ORIGIN: Final = "https://ke-la.example"


class _FakeState:
    """`request.state` rỗng — đúng cảnh route công khai."""


class _FakeRequest:
    """Request giả chỉ có `state` rỗng."""

    state = _FakeState()


def test_permission_dependency_exposes_key() -> None:
    """`Operation.permission_key` đọc đúng thuộc tính này (cổng case)."""
    assert permission_key_of(deny_sample) == "sample.manage"
    assert deny_sample in registered()


def test_permission_key_of_plain_callable_is_none() -> None:
    """Callable không khai khoá thì không phải cổng quyền."""

    def plain() -> None:
        """Callable thường, không phải cổng quyền."""

    assert permission_key_of(plain) is None


def test_permission_dependency_rejects_empty_key() -> None:
    """Khoá rỗng không khớp được `lock` nào ở `cases.toml`."""
    with pytest.raises(ValueError, match="không được rỗng"):
        permission_dependency("")


def test_any_role_is_the_bind_dash() -> None:
    """Route bảo vệ không có cổng quyền nào mang khoá `—` đúng như `lock` vắng ở `cases.toml`."""
    assert ANY_ROLE == "—"


def test_principal_rejects_bad_user_id() -> None:
    """Id người dùng sai mẫu W4 không bao giờ thành `Principal`."""
    with pytest.raises(ValueError, match="usr_"):
        Principal(user_id="nguoi-dung", session_id="sid", role="admin")


def test_principal_rejects_empty_session(fake_clock: FakeClock) -> None:
    """Phiên rỗng thì không thu hồi được — từ chối ngay."""
    with pytest.raises(ValueError, match="session_id"):
        Principal(user_id=new_id("usr", fake_clock), session_id="", role="admin")


def test_principal_rejects_unknown_role(fake_clock: FakeClock) -> None:
    """Chỉ ba vai của hợp đồng (K04)."""
    with pytest.raises(ValueError, match="vai lạ"):
        Principal(user_id=new_id("usr", fake_clock), session_id="sid", role="owner")  # type: ignore[arg-type]  # kiểm đúng vai lạ


async def test_fake_verifier_reads_token(fake_principal: Principal) -> None:
    """Token mẫu giải đúng về `Principal` đã ký."""
    verified = await FakeTokenVerifier().verify(fake_token(fake_principal), _FakeRequest())  # type: ignore[arg-type]  # verifier mẫu không dùng request
    assert verified == fake_principal


@pytest.mark.parametrize("token", ["rac", "fake:usr_x:sid:admin", "fake:a:b:c:d", "khac:1:2:3"])
async def test_fake_verifier_rejects_bad_tokens(token: str) -> None:
    """C05: chuỗi rác, id sai mẫu, sai số phần đều là 401 `UNAUTHENTICATED`."""
    with pytest.raises(AppError, match="UNAUTHENTICATED"):
        await FakeTokenVerifier().verify(token, _FakeRequest())  # type: ignore[arg-type]  # như trên


async def test_deny_all_verifier_rejects_everything() -> None:
    """Chưa có B1-01 thì không token nào qua."""
    with pytest.raises(AppError, match="UNAUTHENTICATED"):
        await DenyAllTokenVerifier().verify("bat-ky", _FakeRequest())  # type: ignore[arg-type]  # như trên


def test_current_principal_needs_protected_route() -> None:
    """Gọi trên route công khai là lỗi lập trình, phải lộ ra ngay."""
    with pytest.raises(RuntimeError, match="route được bảo vệ"):
        current_principal(_FakeRequest())  # type: ignore[arg-type]  # request giả đủ cho hàm này


async def test_require_origin_needs_matching_origin(
    sample_client: httpx.AsyncClient, fake_principal: Principal
) -> None:
    """C24: thiếu hoặc lệch `Origin` ở endpoint ghi là 403 `ORIGIN_MISMATCH`."""
    headers = auth_headers(fake_principal)
    missing = await sample_client.post("/api/sample/origin", json={"name": "a"}, headers=headers)
    foreign = await sample_client.post(
        "/api/sample/origin", json={"name": "a"}, headers={**headers, "Origin": FOREIGN_ORIGIN}
    )
    assert missing.status_code == foreign.status_code == 403
    assert missing.json()["code"] == "ORIGIN_MISMATCH"


async def test_require_origin_accepts_own_origin(sample_client: httpx.AsyncClient, fake_principal: Principal) -> None:
    """Cùng origin với `PUBLIC_BASE_URL` thì ghi được."""
    response = await sample_client.post(
        "/api/sample/origin",
        json={"name": "a"},
        headers={**auth_headers(fake_principal), "Origin": PUBLIC_BASE_URL},
    )
    assert response.status_code == 200


async def test_reject_foreign_origin_allows_missing_header(
    sample_client: httpx.AsyncClient, fake_principal: Principal
) -> None:
    """K31: GET cùng origin không gửi `Origin`; chỉ lệch mới chặn."""
    headers = auth_headers(fake_principal)
    assert (await sample_client.get("/api/sample/origin", headers=headers)).status_code == 200
    foreign = await sample_client.get("/api/sample/origin", headers={**headers, "Origin": FOREIGN_ORIGIN})
    assert foreign.status_code == 403


async def test_deps_resolve_from_app_state(sample_client: httpx.AsyncClient, fake_principal: Principal) -> None:
    """Mọi dependency của `deps.py` lấy đúng tài nguyên mà `lifespan` đã gắn."""
    response = await sample_client.get("/api/sample/deps", headers=auth_headers(fake_principal))
    assert response.status_code == 200
    body = response.json()
    assert body["userId"] == fake_principal.user_id
    assert body["bus"] == "EventBus"
    assert body["storage"] == "LocalDiskStorage"
    assert body["baseUrl"] == PUBLIC_BASE_URL
    assert body["now"].endswith("Z")


@pytest.mark.parametrize(
    "malformed", ["http://[", "http://[::1", "://"], ids=["IPv6 hở", "IPv6 thiếu ngoặc", "không scheme"]
)
async def test_malformed_origin_is_403_not_500(
    sample_client: httpx.AsyncClient, fake_principal: Principal, malformed: str
) -> None:
    """`Origin` sai dạng là header client gửi: 403 `ORIGIN_MISMATCH`, không bao giờ 500 (SEC-05)."""
    headers = {**auth_headers(fake_principal), "Origin": malformed}
    write = await sample_client.post("/api/sample/origin", json={"name": "a"}, headers=headers)
    read = await sample_client.get("/api/sample/origin", headers=headers)
    assert (write.status_code, read.status_code) == (403, 403)
    assert write.json()["code"] == read.json()["code"] == "ORIGIN_MISMATCH"


def test_core_auth_reuses_the_role_mirror_of_the_domain() -> None:
    """NO-097: `Role`/`ROLES` chỉ khai **một** nơi — gương quyền của `packages.domain` (R-07).

    Khai lại ở `apps/api/core/auth.py` là hai nguồn sự thật cho cùng một danh sách vai: H3
    canh gương với FE, còn bản chép này không ai canh.
    """
    assert core_auth.ROLES is domain_permissions.ROLES
    assert core_auth.Role is domain_permissions.Role
