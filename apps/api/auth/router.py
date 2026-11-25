"""`POST /api/auth/login`, `/refresh`, `/logout` (hợp đồng #1, #3, #4; BE-00 §5, §11).

Ba route **công khai** (không Bearer), đều đòi `Origin` khớp `PUBLIC_BASE_URL` (C24) vì
chúng dùng cookie. `POST /api/auth/register` **không** có ở đây (K7 = B: chỉ vào qua lời mời).

- **login** chống dò (C27): email lạ, người `pending` và sai mật khẩu đi cùng một đường —
  cùng kịch bản Redis, cùng một lượt argon2 (băm giả khi không có băm) — và cùng 401.
  `ACCOUNT_DISABLED` chỉ lộ ra **sau** khi mật khẩu đúng. Đọc người dùng xong là trả kết
  nối (`db.rollback()`) rồi mới băm (K36). Trả 204 bằng `None`: một `Response` mới sẽ
  làm FastAPI bỏ `Set-Cookie` đã đặt trên `response`.
- **refresh** theo đúng thuật toán `sessions.refresh_session`; hạn mức hai tầng ở
  `redis-cache`, `on_error="open"` — FE đăng xuất mọi thẻ khi refresh lỗi, nên Redis cache
  chập chờn không được đá người dùng ra (BE-00 §11). Từ chối **trả** 401 chứ không ném, để
  lệnh thu hồi vì dùng lại được commit.
- **logout** idempotent: luôn 204 kèm lệnh xoá hai cookie; `Origin` lệch vẫn xoá cookie
  (403) nhưng không thu hồi gì ở server.
"""

from dataclasses import dataclass, field
from typing import Annotated, Final, cast
from uuid import UUID

from fastapi import Depends
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from apps.api.auth.cookies import clear_auth_cookies, refresh_cookie_of
from apps.api.auth.emails import email_key, validate_wire_email
from apps.api.auth.login_guard import admit, bump, peek, record_failure, record_success
from apps.api.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, needs_rehash, verify_password
from apps.api.auth.services import AuthServices, soft_redis
from apps.api.auth.sessions import (
    RefreshDenied,
    Refreshed,
    issue_session_cookies,
    refresh_session,
    revoke_by_cookie,
    start_session,
    touch_last_active,
)
from apps.api.auth.settings import AuthSettings, get_auth_settings
from apps.api.auth.tokens import issue_token, parse_refresh_cookie, token_hash
from apps.api.core.auth import Role
from apps.api.core.deps import ClockDep, DbSession
from apps.api.core.errors import error_response, request_id
from apps.api.core.origin import require_origin
from apps.api.core.ratelimit import key_ip, rate_limit, retry_after
from apps.api.core.routing import public_router
from apps.api.core.wire import WireDatetime, WireModel, WireRequest
from packages.core.clock import Clock
from packages.core.error_codes import ACCOUNT_DISABLED, INVALID_CREDENTIALS, RATE_LIMITED, UNAUTHENTICATED
from packages.core.errors import AppError
from packages.core.text import normalize_email
from packages.db.models.auth import User
from packages.messaging.redis import AsyncRedis

FAIL_BUCKET_HEX: Final = 16
"""`sid` + 16 ký tự đầu SHA-256 của token: đủ tách kẻ thử token rác khỏi chủ phiên (BE-00 §11)."""
UNKNOWN_SID: Final = "unknown"

router = public_router(prefix="/auth", tags=["auth"])
ROUTERS: Final = (router,)


class SignInBody(WireRequest):
    """`SignInSchema` strict của FE (`src/api/schemas/index.ts:74-80`)."""

    email: str
    password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH)]
    remember_me: Annotated[bool, Field(strict=True)]

    @field_validator("email")
    @classmethod
    def _wire_email(cls, value: str) -> str:
        """Email theo `.email()` của zod; sai → 422 `VALIDATION` `field:"email"` (K37)."""
        return validate_wire_email(value)


class RefreshUser(WireModel):
    """`user` của thân W16."""

    id: str
    name: str
    email: str


class RefreshBody(WireModel):
    """Thân W16: `roles` đúng một vai, `expiresAt` = `exp` của access token."""

    access_token: str
    expires_at: WireDatetime
    roles: tuple[Role]
    user: RefreshUser


@dataclass(frozen=True, slots=True)
class _Account:
    """Người dùng đọc cho đăng nhập — bản ghi thường, còn dùng được sau `db.rollback()`."""

    id: str
    status: str
    token_version: int
    password_hash: str | None = field(repr=False)


async def _cookie_sid(request: Request) -> str:
    """Khoá tầng tổng của refresh: `sid` trong cookie.

    Handler kiểm mẫu cookie **trước** khi gọi limiter, nên `UNKNOWN_SID` chỉ là chỗ đỡ để hàm
    khoá không bao giờ ném (hợp đồng `KeyFn` của B0-06), không phải một bucket dùng thật.
    """
    parsed = parse_refresh_cookie(refresh_cookie_of(request))
    return str(parsed[0]) if parsed is not None else UNKNOWN_SID


# Hạn mức đọc từ `AuthSettings` ở mỗi lượt (hàm, không phải số): cấu hình đọc lười, nên
# B0-06 nhập được module khi chưa có biến môi trường và test nâng được hạn mức (NO-054).
_login_ip_limit: Final = rate_limit(
    "auth_login_ip",
    limit=lambda: get_auth_settings().login_ip_limit,
    window_s=lambda: get_auth_settings().login_ip_window_s,
    key=key_ip,
    store="safe",
    on_error="closed",
)
"""Bước 1 của đăng nhập: hạn mức theo IP ở DB an toàn; Redis hỏng → 503 (fail-closed)."""

_refresh_total_limit: Final = rate_limit(
    "auth_refresh_total",
    limit=lambda: get_auth_settings().refresh_total_limit,
    window_s=lambda: get_auth_settings().refresh_total_window_s,
    key=_cookie_sid,
    store="cache",
    on_error="open",
)
"""Tầng 2 của refresh: tổng lượt theo `sid` (đủ cho nhiều thẻ khôi phục cùng lúc), `on_error="open"`."""


async def _find_account(db: AsyncSession, email: str) -> _Account | None:
    """Người dùng chưa xoá mềm có email này (so bằng `normalize_email`, C16)."""
    row = (
        await db.execute(
            select(User.id, User.status, User.token_version, User.password_hash).where(
                User.email_normalized == normalize_email(email), User.deleted_at.is_(None)
            )
        )
    ).one_or_none()
    return None if row is None else _Account(*row)


def _client_host(request: Request) -> str | None:
    """IP người gọi (ghi vào `created_ip`), không gom /64 như khoá hạn mức."""
    return request.client.host if request.client is not None else None


@router.post(
    "/login",
    status_code=204,
    response_class=Response,
    response_model=None,
    dependencies=[Depends(require_origin), Depends(_login_ip_limit)],
)
async def auth_login(body: SignInBody, request: Request, response: Response, db: DbSession, clock: ClockDep) -> None:
    """Đăng nhập đúng thứ tự B1-01 [6]; đúng và `active` → phiên mới, 204 + hai cookie."""
    settings, safe = get_auth_settings(), AuthServices(request.app).safe
    ip, k = await key_ip(request), email_key(body.email)
    await admit(safe, settings, k, ip)
    account = await _find_account(db, body.email)
    await db.rollback()  # chưa ghi gì: trả kết nối về pool trước khi băm (K36)
    usable_hash = account.password_hash if account is not None and account.status != "pending" else None
    matched = await verify_password(usable_hash, body.password)
    if account is None or not matched:
        await record_failure(safe, settings, k)
        raise INVALID_CREDENTIALS.error()
    if account.status != "active":
        raise ACCOUNT_DISABLED.error()
    await record_success(safe, k, ip)
    if usable_hash is not None and needs_rehash(usable_hash):
        await _rehash(db, account.id, usable_hash, body.password)
    await start_session(
        db,
        response,
        user=account,
        remember=body.remember_me,
        ip=_client_host(request),
        clock=clock,
        replaced_cookie=refresh_cookie_of(request),
    )
    await touch_last_active(db, account.id, clock.now())


async def _rehash(db: AsyncSession, user_id: str, old_hash: str, password: str) -> None:
    """Băm lại theo hồ sơ hiện hành; so-và-đặt để không đè một lần đổi mật khẩu song song."""
    fresh = await hash_password(password)
    await db.execute(
        update(User)
        .where(User.id == user_id, User.password_hash == old_hash)
        .values(password_hash=fresh)
        .execution_options(synchronize_session=False)
    )


def _fail_bucket(sid: UUID, token: str) -> str:
    """Khoá bộ đếm lượt refresh **thất bại** của một cặp (phiên, token)."""
    return f"rl:auth_refresh_fail:{sid}:{token_hash(token)[:FAIL_BUCKET_HEX]}"


async def _check_failures(cache: AsyncRedis, settings: AuthSettings, bucket: str) -> None:
    """Tầng 1, cửa trước: đã đủ `REFRESH_FAIL_LIMIT` lượt 401 trong cửa sổ → 429, khỏi chạm DB.

    Chỉ **đọc**: bộ đếm tăng sau khi biết lượt này thất bại (`_denied`), vì chủ phiên refresh
    đúng — kể cả hàng chục thẻ cùng lúc — không bao giờ được tự khoá mình. Redis hỏng → cho qua.
    """
    seen = await soft_redis(peek(cache, bucket), "rate_limit_open")
    if seen is not None and seen[0] >= settings.refresh_fail_limit and seen[1] >= 0:
        raise RATE_LIMITED.error(retry_after=retry_after(seen[1]))


async def _denied(cache: AsyncRedis, settings: AuthSettings, bucket: str, outcome: RefreshDenied) -> Response:
    """Tầng 1, cửa sau: tăng bộ đếm nguyên khối **rồi** mới chọn mã trả về.

    Nhiều lượt song song cùng lọt cửa trước (cùng đọc số cũ); nhờ `INCR` nguyên khối, đúng
    `REFRESH_FAIL_LIMIT` lượt đầu nhận 401, phần vượt nhận 429 — trần giữ đúng dưới tải song
    song. Trả response chứ không ném: lệnh thu hồi vì dùng lại của lượt này vẫn phải commit.
    """
    counted = await soft_redis(bump(cache, bucket, settings.refresh_fail_window_s), "rate_limit_open")
    error = outcome.code.error()
    if counted is not None and counted[0] > settings.refresh_fail_limit:
        error = RATE_LIMITED.error(retry_after=retry_after(counted[1]))
    return error_response(error, request_id())


@router.post("/refresh", status_code=200, response_model=RefreshBody, dependencies=[Depends(require_origin)])
async def auth_refresh(request: Request, response: Response, db: DbSession, clock: ClockDep) -> RefreshBody | Response:
    """Xoay (hay trả lại) cookie refresh, cấp access token và cookie luồng mới (W16)."""
    parsed = parse_refresh_cookie(refresh_cookie_of(request))
    if parsed is None:
        raise UNAUTHENTICATED.error()
    sid, token = parsed
    settings, cache = get_auth_settings(), AuthServices(request.app).cache
    bucket = _fail_bucket(sid, token)
    await _check_failures(cache, settings, bucket)
    await _refresh_total_limit(request)
    outcome = await refresh_session(db, sid=sid, token=token, clock=clock, settings=settings)
    if isinstance(outcome, RefreshDenied):
        return await _denied(cache, settings, bucket, outcome)
    return await _refreshed(db, response, outcome, clock, settings)


async def _refreshed(
    db: AsyncSession, response: Response, outcome: Refreshed, clock: Clock, settings: AuthSettings
) -> RefreshBody:
    """Access token cùng `sid` và `ver` hiện tại, hai cookie, `last_active_at`, thân W16."""
    state = outcome.state
    access_token, expires_at = issue_token(
        "access",
        user_id=state.user_id,
        sid=str(state.sid),
        ver=state.token_version,
        now=clock.now(),
        ttl_s=settings.access_token_ttl_s,
    )
    issue_session_cookies(
        response,
        sid=state.sid,
        token=outcome.token,
        remember=state.remember,
        idle_expires_at=outcome.idle_expires_at,
        user_id=state.user_id,
        ver=state.token_version,
        clock=clock,
    )
    await touch_last_active(db, state.user_id, clock.now())
    return RefreshBody(
        access_token=access_token,
        expires_at=expires_at,
        roles=(cast("Role", state.role),),  # CHECK của `users.role` giữ đúng ba vai
        user=RefreshUser(id=state.user_id, name=state.name, email=state.email),
    )


@router.post("/logout", status_code=204, response_class=Response, response_model=None)
async def auth_logout(request: Request, response: Response, db: DbSession, clock: ClockDep) -> Response | None:
    """Thu hồi phiên của cookie (nếu còn sống) và xoá hai cookie; lặp lại vẫn 204."""
    try:
        require_origin(request)
    except AppError as exc:
        denied = error_response(exc, request_id())
        clear_auth_cookies(denied)
        return denied
    await revoke_by_cookie(db, refresh_cookie_of(request), reason="logout", clock=clock)
    clear_auth_cookies(response)
    return None
