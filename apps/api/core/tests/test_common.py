"""Test case chung, tham số hoá theo route **được bảo vệ** của app thật (CASE §2.1).

Hôm nay repo chưa có route được bảo vệ nào, nên tập tham số rỗng và pytest báo
"empty parameter set" — `case_gate` chấp nhận đúng một lý do bỏ qua này (BE-00 §12).
Prompt sau thêm route là các case dưới đây tự áp cho nó, không ai phải sửa file này.

Máy móc của từng case được chứng minh bằng app thử ở `test_routing.py`,
`test_idempotency.py` và `test_middleware.py`.

C10 mồi sẵn một dòng `completed` (như C22 mồi `in_progress`) thay vì gửi `json={}`
thật rồi đợi lượt lặp phát lại: thân/đường mẫu (`PATH_VALUES`) không khớp schema
thật của route ghi, nên lượt đầu luôn lỗi (422 thân sai hoặc 404 dependency quyền),
và BE-00 §7 xoá dòng idempotency khi handler lỗi — không còn gì để phát lại
(NO-127). Mồi sẵn chứng minh đúng máy móc route↔idempotency mà không phụ thuộc
thân có hợp lệ với route thật.
"""

import re
from typing import Final, Literal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from apps.api.core.app import create_app
from apps.api.core.auth import Principal, TokenVerifier
from apps.api.core.idempotency import LEASE, TTL, request_digest
from apps.api.core.openapi import Operation, operations
from apps.api.core.permissions import registered
from apps.api.projects.access import ProjectAccess
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE, SESSION_REVOKED
from packages.core.settings import get_core_settings
from packages.db.errors import translate_db_error
from packages.db.models.idempotency import STATE_COMPLETED, STATE_IN_PROGRESS, IdempotencyRecord
from packages.testing.fixtures.api import auth_headers, make_api_client
from packages.testing.fixtures.clock import FakeClock
from packages.testing.golden.recorder import SAMPLES_ENV

IDEMPOTENCY_KEY: Final = "khoa-chung-cho-case"
SEEDED_STATUS: Final = 201
SEEDED_CONTENT_TYPE: Final = "application/json"
SEEDED_BODY: Final = b'{"mau":"c10"}'
"""Response mẫu mồi sẵn cho dòng `completed` của C10 (NO-127)."""

ULID: Final = "01JABCDEFGHJKMNPQRSTVWXYZ0"
"""Thân ULID hợp lệ (Crockford base32 HOA, 26 ký tự) cho mọi id mẫu (W4)."""

# Giá trị mẫu cho mọi tên tham số đường đang dùng trong BE-BIND (khối [8] của B0-06).
PATH_VALUES: Final[dict[str, str]] = {
    "project_id": f"prj_{ULID}",
    "upload_id": f"upl_{ULID}",
    "version_id": f"ver_{ULID}",
    "notification_id": f"ntf_{ULID}",
    "user_id": f"usr_{ULID}",
    "model_version_id": f"mdl_{ULID}",
    "dataset_id": f"dst_{ULID}",
    "job_id": f"job_{ULID}",
    "floor_id": "L-ABCDEFGHIJ",
    "measurement_id": "MS-0001",
    "item_id": "muc-thu-vien",
    "link_id": f"shl_{ULID}",
    "family": "wallSegmentation",
    "token": "token-mau-cua-case-chung",
    "bake_id": f"bak_{ULID}",
    "export_id": f"mex_{ULID}",
    "file_name": "baked.glb",
}

BAD_TOKENS: Final = (
    "chuoi-rac",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c3JfeCJ9.chu-ky-ngau-nhien",
    f"fake:usr_{ULID}:sid:owner",
    "",
)

_PARAM_RE: Final = re.compile(r"\{([^}]+)\}")


def _protected() -> list[Operation]:
    """Thao tác được bảo vệ của app thật — tập tham số của case chung."""
    return [operation for operation in operations() if operation.protected]


def _idempotent() -> list[Operation]:
    """Thao tác được bảo vệ có idempotency — tập tham số của C10, C22."""
    return [operation for operation in _protected() if operation.idempotency != "off"]


def _url(operation: Operation) -> str:
    """Đường thật của một thao tác; tham số lạ làm test hỏng và **nêu tên** nó."""

    def value(match: re.Match[str]) -> str:
        """Giá trị mẫu của một tham số đường; tham số lạ làm test hỏng."""
        name = match.group(1)
        if name not in PATH_VALUES:
            raise AssertionError(f"{operation.op}: chưa có giá trị mẫu cho tham số đường {name!r}")
        return PATH_VALUES[name]

    return _PARAM_RE.sub(value, operation.path)


async def _send(client: httpx.AsyncClient, operation: Operation, **kwargs: object) -> httpx.Response:
    """Gửi đúng method của thao tác tới đường thật của nó."""
    return await client.request(operation.method, _url(operation), **kwargs)  # type: ignore[arg-type]  # kwargs httpx


GRANTED_PROJECT_NAME: Final = "Dự án giả của case chung"


def _grant_everything(app: FastAPI, principal: Principal) -> None:
    """Mọi dependency quyền chạy **trước** bước nhận việc, nên C10/C22 phải qua được chúng.

    Bản ghi đè trả một `ProjectAccess` **đúng kiểu** thay vì `None`: route đọc kết quả cổng
    quyền qua tham số (B2-02 là người dùng đầu tiên của `project_name`) sẽ 500 ngay trong
    case chung nếu nhận `None` — một lỗi của giàn test đội lốt lỗi của route (NO-137).
    Cổng quyền cấp hệ thống không trả gì, nên giá trị này chỉ bị bỏ qua: một bản ghi đè đủ
    cho cả hai loại cổng.
    """
    granted = ProjectAccess(
        project_id=PATH_VALUES["project_id"],
        project_name=GRANTED_PROJECT_NAME,
        principal=principal,
        role=principal.role,
    )
    for dependency in registered():
        app.dependency_overrides[dependency] = lambda: granted


class _BrokenVerifier:
    """C13: verifier ném đúng lỗi đã dịch từ Postgres mất kết nối."""

    async def verify(self, token: str, request: object) -> Principal:
        """Ném 503 đã dịch, như khi Postgres mất kết nối."""
        translated = translate_db_error(OperationalError("SELECT 1", {}, Exception("mất kết nối")))
        raise translated if translated is not None else DEPENDENCY_UNAVAILABLE.error(retry_after=5)


class _RevokedVerifier:
    """C25: phiên đã thu hồi → 401 `SESSION_REVOKED` (W10, K30)."""

    async def verify(self, token: str, request: object) -> Principal:
        """Ném 401 `SESSION_REVOKED`, như khi phiên đã bị thu hồi."""
        raise SESSION_REVOKED.error()


def _app_with(verifier: TokenVerifier, clock: FakeClock) -> FastAPI:
    """App thật với verifier tiêm vào — để dựng C13, C25."""
    return create_app(get_core_settings(), token_verifier=verifier, clock=clock)


@pytest.mark.case("C04")
@pytest.mark.parametrize("operation", _protected(), ids=lambda item: item.op)
async def test_common__C04(operation: Operation, api_client: httpx.AsyncClient) -> None:
    """Không có token → 401 `UNAUTHENTICATED`.

    Hàng catalog: [BE5-G-007].
    """
    response = await _send(api_client, operation)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.case("C05")
@pytest.mark.parametrize("operation", _protected(), ids=lambda item: item.op)
async def test_common__C05(operation: Operation, api_client: httpx.AsyncClient) -> None:
    """Token rác, chữ ký lạ, vai lạ, rỗng → 401 `UNAUTHENTICATED`.

    `BAD_TOKENS` lặp trong thân thay vì tham số hoá riêng (NO-129): tham số hoá hai
    chiều đổi id thành `test_common__C05[<token>-<op>]`, và `_TEST_COMMON_RE`
    (`packages/core/case_names.py`) chỉ tách đúng op khi id là `test_common__C05[<op>]`.

    Hàng catalog: [BE4-G-002].
    """
    for token in BAD_TOKENS:
        response = await _send(api_client, operation, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, f"token={token!r}"


@pytest.mark.case("C12")
@pytest.mark.parametrize("operation", _protected(), ids=lambda item: item.op)
async def test_common__C12(operation: Operation, api_client: httpx.AsyncClient, fake_principal: Principal) -> None:
    """Thân vượt trần → 413 `PAYLOAD_TOO_LARGE` trước khi handler chạy."""
    response = await _send(
        api_client,
        operation,
        headers={**auth_headers(fake_principal), "Content-Type": "application/json"},
        content=b"a" * (operation.body_limit + 1),
    )
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.case("C13")
@pytest.mark.parametrize("operation", _protected(), ids=lambda item: item.op)
async def test_common__C13(
    operation: Operation, api_env: None, fake_clock: FakeClock, fake_principal: Principal
) -> None:
    """Phụ thuộc hỏng → 503 kèm `Retry-After`, thân không lộ stack.

    Hàng catalog: [BE4-G-012].
    """
    async with make_api_client(_app_with(_BrokenVerifier(), fake_clock)) as client:
        response = await _send(client, operation, headers=auth_headers(fake_principal))
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "Retry-After" in response.headers
    assert "Traceback" not in response.text


@pytest.mark.case("C25")
@pytest.mark.parametrize("operation", _protected(), ids=lambda item: item.op)
async def test_common__C25(
    operation: Operation, api_env: None, fake_clock: FakeClock, fake_principal: Principal
) -> None:
    """Phiên đã thu hồi → 401 `SESSION_REVOKED`.

    Hàng catalog: [BE4-G-003].
    """
    async with make_api_client(_app_with(_RevokedVerifier(), fake_clock)) as client:
        response = await _send(client, operation, headers=auth_headers(fake_principal))
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_REVOKED"


@pytest.mark.case("C22")
@pytest.mark.parametrize("operation", _idempotent(), ids=lambda item: item.op)
async def test_common__C22(
    operation: Operation, api_app: FastAPI, api_client: httpx.AsyncClient, fake_principal: Principal
) -> None:
    """Lượt trước còn hạn thuê → 503 `IDEMPOTENCY_IN_PROGRESS` + `Retry-After: 1`."""
    _grant_everything(api_app, fake_principal)
    headers = {**auth_headers(fake_principal), "Idempotency-Key": IDEMPOTENCY_KEY}
    await _seed_in_progress(api_app, operation, fake_principal)
    response = await _send(api_client, operation, headers=headers, json={})
    assert response.status_code == 503
    assert response.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert response.headers["Retry-After"] == "1"


@pytest.mark.case("C10")
@pytest.mark.parametrize("operation", _idempotent(), ids=lambda item: item.op)
async def test_common__C10(
    operation: Operation,
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    fake_principal: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lượt lặp cùng thân trả lại response đã mồi (handler không chạy); thân khác → 422.

    Mồi sẵn một dòng `completed` thay vì gửi `json={}` thật rồi đợi phát lại: thân/đường
    mẫu không khớp schema route ghi thật, lượt đầu luôn lỗi, và BE-00 §7 xoá dòng
    idempotency khi handler lỗi — không còn gì để phát lại (NO-127).

    Tắt bộ ghi golden (`CONTRACT_SAMPLES_DIR`, B0-07) trong phạm vi test: response đã mồi
    (`SEEDED_BODY`) không theo schema thật của operation (`{"mau": "c10"}` không phải một
    `Project`), nên nếu lọt vào `contract-samples` sẽ làm H1 (bước 7) hỏng ở mọi op ghi được —
    C01 mới là nguồn mẫu 2xx thật cho H1/case_gate, không phải case chung này.
    """
    monkeypatch.delenv(SAMPLES_ENV, raising=False)
    _grant_everything(api_app, fake_principal)
    headers = {**auth_headers(fake_principal), "Idempotency-Key": IDEMPOTENCY_KEY}
    await _seed(api_app, operation, fake_principal, state=STATE_COMPLETED)

    replay = await _send(api_client, operation, headers=headers, json={})
    assert replay.status_code == SEEDED_STATUS
    assert replay.headers["content-type"] == SEEDED_CONTENT_TYPE
    assert replay.content == SEEDED_BODY

    reused = await _send(api_client, operation, headers=headers, json={"khac": True})
    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def _seed_in_progress(app: FastAPI, operation: Operation, principal: Principal) -> None:
    """Dựng sẵn một dòng `in_progress` còn hạn cho đúng request mà C22 sắp gửi."""
    await _seed(app, operation, principal, state=STATE_IN_PROGRESS)


async def _seed(
    app: FastAPI,
    operation: Operation,
    principal: Principal,
    *,
    state: Literal["in_progress", "completed"],
) -> None:
    """Dựng sẵn một dòng idempotency `in_progress` hoặc `completed` cho đúng request C10/C22 sắp gửi.

    `state` gõ kiểu `Literal` (không phải `str`) để một state lạ bị mypy chặn ngay, thay vì âm thầm
    mồi một dòng `completed` thiếu `status_code`/`response_body` và làm C10 hỏng bằng assert khó đọc.
    """
    now = app.state.clock.now()
    completed = state == STATE_COMPLETED
    async with app.state.sessionmaker() as session:
        session.add(
            IdempotencyRecord(
                user_id=principal.user_id,
                method=operation.method,
                route_template=operation.path,
                key=IDEMPOTENCY_KEY,
                request_hash=request_digest(operation.method, _url(operation), "", b"{}"),
                state=state,
                claim_token=uuid4(),
                lease_until=now + LEASE,
                expires_at=now + TTL,
                status_code=SEEDED_STATUS if completed else None,
                content_type=SEEDED_CONTENT_TYPE if completed else None,
                response_body=SEEDED_BODY if completed else None,
            )
        )
        await session.commit()


def test_grant_everything_overrides_with_a_typed_project_access(api_app: FastAPI, fake_principal: Principal) -> None:
    """NO-137: cổng quyền giả phải trả đúng kiểu, không `None`.

    Handler đọc `ProjectAccess` qua tham số (B2-02 là người dùng đầu tiên của
    `project_name`) sẽ 500 ngay trong case chung nếu bản ghi đè trả `None` — một lỗi của
    giàn test đội lốt lỗi của route.
    """
    _grant_everything(api_app, fake_principal)

    overrides = [api_app.dependency_overrides[dependency] for dependency in registered()]
    assert overrides
    for override in overrides:
        granted = override()
        assert isinstance(granted, ProjectAccess)
        assert granted.principal is fake_principal
        assert granted.project_name
