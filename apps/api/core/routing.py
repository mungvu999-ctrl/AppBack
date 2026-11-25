"""`AppRoute` — thứ tự bất biến của một request (BE-00 §5, §7, W10, W21).

Mọi route của mọi module đi qua đúng một đường:

1. **xác thực trước khi đọc thân.** FastAPI đọc thân *trước* dependency, nên nếu
   để việc kiểm token cho một dependency thì một request 64 MiB không token vẫn
   được đọc hết rồi mới 401 (W10: 401 phải tới trước 400);
2. mở session của request, gắn ngữ cảnh log;
3. handler gốc của FastAPI: đọc thân → dependency của router/route (quyền, rate
   limit) → **guard của khung** (W21, 428, nhận việc idempotency) → lỗi Pydantic
   422 → endpoint. Guard được gắn **sau** `super().__init__()` để nằm cuối danh
   sách: FastAPI *chèn đầu* mọi dependency khai qua `dependencies=`;
4. hoàn tất idempotency **trong cùng giao dịch** → commit → chờ callback sau
   commit → đóng session → mới trả response.

Chỉ **ngoại lệ** mới rollback. Response 4xx mà handler *trả về* (không ném) vẫn là
một lượt chạy thành công: nó đã ghi gì thì giữ nguyên (BE-00 §7).
"""

import inspect
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal

from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import get_dependant
from fastapi.routing import APIRoute, request_response
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match
from starlette.types import Scope

from apps.api.core import idempotency
from apps.api.core.auth import Principal, TokenVerifier
from packages.core.clock import Clock
from packages.core.error_codes import PATH_BODY_MISMATCH, PRECONDITION_REQUIRED, UNAUTHENTICATED
from packages.core.logging import bind_log_context
from packages.db.hooks import after_commit_idle

API_PREFIX: Final = "/api"
"""Mọi route của mọi module nằm dưới `/api` (W19)."""

DEFAULT_BODY_LIMIT: Final = 1024 * 1024
"""JSON 1 MiB (BE-00 §11). Route cần rộng hơn (#35 = 8 MiB) buộc phải `idempotency="off"`."""

BASE_VERSION_KEY: Final = "baseVersion"
AUTHORIZATION_HEADER: Final = "authorization"
BEARER: Final = "bearer"
OPTIONS_ATTR: Final = "__app_route_options__"

WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

type Idempotency = Literal["auto", "off"]


@dataclass(frozen=True, slots=True)
class RouteOptions:
    """Khai báo của một route mà khung cần biết trước khi chạy request."""

    versioned: bool = False
    body_limit: int = DEFAULT_BODY_LIMIT
    idempotency: Idempotency = "auto"


DEFAULT_OPTIONS: Final = RouteOptions()


def route_options(
    *,
    versioned: bool = False,
    body_limit: int = DEFAULT_BODY_LIMIT,
    idempotency: Idempotency = "auto",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Gắn tuỳ chọn lên hàm endpoint; sai luật thì hỏng **lúc nạp module**, không lúc chạy."""
    if body_limit < 1:
        raise ValueError(f"body_limit phải ≥ 1 byte, nhận {body_limit}")
    if body_limit > DEFAULT_BODY_LIMIT and idempotency != "off":
        raise ValueError(
            f"body_limit {body_limit} > {DEFAULT_BODY_LIMIT} buộc idempotency='off' "
            "(thân lớn hơn 1 MiB không lưu lại được, BE-00 §7)"
        )
    options = RouteOptions(versioned=versioned, body_limit=body_limit, idempotency=idempotency)

    def decorate(endpoint: Callable[..., Any]) -> Callable[..., Any]:
        """Gắn `RouteOptions` lên chính hàm endpoint."""
        setattr(endpoint, OPTIONS_ATTR, options)
        return endpoint

    return decorate


def options_of(endpoint: Callable[..., Any]) -> RouteOptions:
    """Tuỳ chọn của một endpoint; không khai gì thì dùng mặc định của hiến chương."""
    options = getattr(endpoint, OPTIONS_ATTR, DEFAULT_OPTIONS)
    return options if isinstance(options, RouteOptions) else DEFAULT_OPTIONS


async def _json_body(request: Request) -> object:
    """Thân đã giải JSON, hay `None` khi không có/không phải JSON.

    Đi qua `request.json()` chứ không `json.loads` lại: Starlette nhớ kết quả giải trên
    chính đối tượng request, và FastAPI đã giải thân JSON trước mọi dependency. Nhờ vậy
    hai guard (W21, 428) trên cùng một thân 8 MiB không giải lại lần nào (PERF-04).
    """
    if not await request.body():
        return None
    try:
        return await request.json()
    except ValueError:
        return None  # JSON hỏng đã thành 400 `MALFORMED_JSON` ở chỗ khác


def _single_method(name: str, methods: set[str] | None) -> str:
    """Phương thức duy nhất của route (không tính `HEAD` Starlette tự thêm cho GET).

    Một route nhiều phương thức là **một** `operationId` cho nhiều thao tác: không khớp
    được một dòng hợp đồng nào, và `Operation.method` chỉ báo được một trong số đó — nên
    lượt POST của `GET|POST` mất idempotency và cổng case không đòi test cho nó (LOG-02).
    """
    real = sorted((methods or set()) - {"HEAD"})
    if len(real) != 1:
        raise ValueError(f"{name}: mỗi route đúng một phương thức (một operationId một thao tác), nhận {real}")
    return real[0]


async def _principal_of(request: Request) -> Principal:
    """Đọc `Authorization: Bearer` rồi gọi verifier. Thiếu header → 401 ngay, chưa chạm thân."""
    scheme, _, token = request.headers.get(AUTHORIZATION_HEADER, "").partition(" ")
    if scheme.lower() != BEARER or not token:
        raise UNAUTHENTICATED.error()
    verifier: TokenVerifier = request.app.state.token_verifier
    return await verifier.verify(token, request)


def _path_body_guard(route: "AppRoute") -> Callable[[Request], Awaitable[None]]:
    """W21: khoá thân camelCase trùng tham số đường mà khác giá trị → 422 `PATH_BODY_MISMATCH`."""

    async def guard(request: Request) -> None:
        """So từng tham số đường với khoá thân camelCase cùng nghĩa."""
        body = await _json_body(request)
        if not isinstance(body, dict):
            return
        for name, value in request.path_params.items():
            camel = to_camel(name)
            if camel in body and body[camel] != value:
                raise PATH_BODY_MISMATCH.error(field=camel)

    return guard


def _versioned_guard(route: "AppRoute") -> Callable[[Request], Awaitable[None]]:
    """W20: thiếu `baseVersion` → 428, **trước** Pydantic (HOP-DONG-MOI §1.2)."""

    async def guard(request: Request) -> None:
        """Thân phải là object có `baseVersion`."""
        body = await _json_body(request)
        if not isinstance(body, dict) or BASE_VERSION_KEY not in body:
            raise PRECONDITION_REQUIRED.error()

    return guard


def _idempotency_guard(route: "AppRoute") -> Callable[[Request], Awaitable[None]]:
    """Nhận việc idempotency — chạy **sau** mọi dependency quyền của route (BE-00 §7), qua pool riêng."""

    async def guard(request: Request) -> None:
        """Có header thì nhận việc; không có thì route chạy như thường."""
        raw = request.headers.get(idempotency.HEADER)
        if raw is None:
            return
        principal: Principal = request.state.principal
        clock: Clock = request.app.state.clock
        body = await request.body()
        request.state.idempotency_claim = await idempotency.begin(
            request.app.state.claim_sessionmaker,
            user_id=principal.user_id,
            method=request.method,
            route_template=route.path_format,
            key=idempotency.check_key(raw),
            digest=idempotency.request_digest(request.method, request.url.path, request.url.query, body),
            now=clock.now(),
        )

    return guard


def _declared_path_params(dependant: Dependant) -> set[str]:
    """Tên mọi tham số đường mà cả cây dependency (endpoint lẫn dependency lồng) đã khai."""
    names = {param.name for param in dependant.path_params}
    for sub in dependant.dependencies:
        names |= _declared_path_params(sub)
    return names


def _path_declaration(names: Sequence[str]) -> Callable[..., None]:
    """Dependency rỗng **khai** các tham số đường mà cả cây dependency không khai (NO-237).

    Cổng quyền như `require_project` đọc thẳng `request.path_params`, nên OpenAPI (dựng từ cây
    `dependant`) thiếu tham số đó. Khai ở đây chứ không đẩy vào `dependant.path_params` của endpoint:
    FastAPI truyền mọi tham số cấp endpoint vào hàm endpoint, mà endpoint không nhận chúng.
    """

    def declare(**_: str) -> None:
        """Không làm gì: Starlette đã tách tham số đường (luôn là `str`), hàm chỉ để OpenAPI liệt kê."""

    parameters = [inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=str) for name in names]
    setattr(declare, "__signature__", inspect.Signature(parameters))  # noqa: B010 — gán thẳng thì mypy chặn
    return declare


class AppRoute(APIRoute):
    """Route **được bảo vệ**: đòi `Authorization: Bearer` trước khi đọc thân."""

    protected: ClassVar[bool] = True

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        """Dựng route như FastAPI, rồi đọc `RouteOptions` và gắn guard của khung vào **cuối** cây dependency."""
        super().__init__(path, endpoint, **kwargs)
        self.options = options_of(endpoint)
        self.wire_method = _single_method(self.name, self.methods)
        if self.options.versioned and self.wire_method not in WRITE_METHODS:
            # Guard 428 đòi `baseVersion` trong thân: gắn lên GET là 428 cho **mọi** lượt đọc.
            raise ValueError(f"{self.name}: versioned=True chỉ dùng cho phương thức ghi, không cho {self.wire_method}")
        self.uses_idempotency = (
            self.protected
            and self.options.idempotency != "off"
            and not self.options.versioned
            and self.wire_method != "GET"
        )
        # Gắn SAU super().__init__(): `dependencies=` được FastAPI chèn vào đầu, còn
        # guard của khung phải chạy cuối cùng, sau quyền và rate limit (BE-00 §7).
        for build in self._guard_builders():
            self.dependant.dependencies.append(get_dependant(path=self.path_format, call=build(self)))
        declared = _declared_path_params(self.dependant)
        undeclared = [name for name in self.param_convertors if name not in declared]
        if undeclared:
            self.dependant.dependencies.append(get_dependant(path=self.path_format, call=_path_declaration(undeclared)))

    def _guard_builders(self) -> list[Callable[["AppRoute"], Callable[[Request], Awaitable[None]]]]:
        """Guard nào áp cho route này — chỉ gắn cái thật sự cần, để route GET không đọc thân.

        W21 xét tham số của **đường** (`param_convertors`), không của endpoint: cổng quyền như `require_project`
        đọc thẳng `request.path_params`, nên endpoint có thể không khai tham số đường nào (NO-351).
        """
        builders: list[Callable[[AppRoute], Callable[[Request], Awaitable[None]]]] = []
        if self.dependant.body_params and self.param_convertors:
            builders.append(_path_body_guard)
        if self.options.versioned:
            builders.append(_versioned_guard)
        if self.uses_idempotency:
            builders.append(_idempotency_guard)
        return builders

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Bọc handler của FastAPI theo đúng thứ tự: xác thực → session → handler → hoàn tất + commit → đóng session."""
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            """Một request đi trọn đường của `AppRoute`; replay trả response đã lưu, ngoại lệ thì rollback."""
            if self.protected:
                request.state.principal = await _principal_of(request)
            request.state.idempotency_claim = None
            maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
            session = maker()
            request.state.db_session = session
            log_token = bind_log_context(routeTemplate=self.path_format, method=self.wire_method)
            try:
                try:
                    response = await original(request)
                except idempotency.Replay as replay:
                    await _abort(request, session)
                    return replay.response
                except BaseException:
                    await _abort(request, session)
                    raise
                return await _finish(request, session, response)
            finally:
                # Token của contextvar mang sẵn biến của nó; `packages.core.logging`
                # không phơi hàm gỡ, mà không gỡ thì ngữ cảnh route rò sang request sau
                # khi hai request dùng chung một task (client ASGI của test).
                log_token.var.reset(log_token)

        return handler


class PublicRoute(AppRoute):
    """Route công khai: không xác thực, không idempotency (BE-00 §7, [7] của B0-06)."""

    protected: ClassVar[bool] = False


def _router(route_class: type[AppRoute], kwargs: dict[str, Any]) -> APIRouter:
    """Router mang sẵn tiền tố `/api`, để đường của route là đường thật ngay lúc khai.

    Tiền tố đặt ở đây chứ không ở `include_router(prefix=…)`: từ FastAPI 0.141,
    `include_router` không chép route vào app nữa mà bọc chúng trong một router
    trung gian và **dựng lại** `dependant` cho đường đã thêm tiền tố — làm rơi mất
    guard mà `AppRoute` gắn cuối danh sách dependency.
    """
    prefix = API_PREFIX + kwargs.pop("prefix", "")
    return APIRouter(route_class=route_class, prefix=prefix, **kwargs)


def protected_router(**kwargs: Any) -> APIRouter:
    """Router mặc định của mọi module: mọi route đều được bảo vệ."""
    return _router(AppRoute, kwargs)


def public_router(**kwargs: Any) -> APIRouter:
    """Router cho các `operationId` được phép công khai (BE-00 §5, khối [7] của B0-06)."""
    return _router(PublicRoute, kwargs)


def mount_routers(app: FastAPI, routers: Sequence[tuple[str, APIRouter]]) -> None:
    """Đưa route của từng router thẳng vào app, giữ nguyên chính đối tượng `AppRoute`.

    `app.routes` vì thế chứa route thật (middleware khớp được đường, `operations()`
    đọc được `dependant`). Handler phải dựng lại sau khi gắn `app` làm nguồn
    `dependency_overrides`: `APIRoute.__init__` đã chốt nguồn đó vào `self.app`,
    mà lúc router được khai thì app chưa tồn tại.
    """
    for _name, router in routers:
        for route in router.routes:
            assert isinstance(route, AppRoute)  # noqa: S101 — `check_routers` đã kiểm trước đó
            route.dependency_overrides_provider = app
            route.app = request_response(route.get_route_handler())
            app.router.routes.append(route)


async def _abort(request: Request, session: AsyncSession) -> None:
    """Lượt chạy hỏng: rollback, đóng session, xoá dòng idempotency của chính mình.

    Mọi bước dọn đều chạy dù bước trước ném: rollback hỏng (DB rớt, request bị huỷ) mà bỏ luôn
    `discard` là để dòng claim `in_progress` mồ côi tới hết TTL — lượt lặp cùng khoá bị từ chối
    (`IDEMPOTENCY_IN_PROGRESS`) thay vì chạy lại. Ngoại lệ **đầu tiên** được ném lại sau khi đã dọn hết.
    """
    claim = request.state.idempotency_claim
    steps: list[Callable[[], Awaitable[None]]] = [session.rollback, session.close]
    if claim is not None:
        steps.append(lambda: idempotency.discard(request.app.state.claim_sessionmaker, claim))
    first: BaseException | None = None
    for step in steps:
        try:
            await step()
        except BaseException as exc:  # noqa: BLE001 — ném lại ở cuối, sau khi đã dọn hết
            first = first or exc
    if first is not None:
        raise first


async def _finish(request: Request, session: AsyncSession, response: Response) -> Response:
    """Hoàn tất idempotency → commit → chờ callback sau commit → đóng session."""
    claim: idempotency.Claim | None = request.state.idempotency_claim
    keep = claim is not None and idempotency.storable(response)
    try:
        if keep and claim is not None:
            clock: Clock = request.app.state.clock
            await idempotency.complete(session, claim, response, clock.now())
        await session.commit()
        await after_commit_idle(session)
    except BaseException:
        await _abort(request, session)
        raise
    await session.close()
    if claim is not None and not keep:
        # 401/408/429 hay 5xx do handler trả về: không lưu, để lượt sau chạy lại thật.
        await idempotency.discard(request.app.state.claim_sessionmaker, claim)
    return response


def route_of(scope: Scope) -> AppRoute | None:
    """Route khớp `scope`; khớp đường mà lệch method vẫn tính (giới hạn thân áp cho mọi method)."""
    partial: AppRoute | None = None
    for route in scope["app"].routes:
        if not isinstance(route, AppRoute):
            continue
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return route
        if match is Match.PARTIAL and partial is None:
            partial = route
    return partial


def check_routers(routers: Sequence[tuple[str, APIRouter]]) -> None:
    """Mỗi `router.py` phải góp ≥ 1 route và mọi route phải là `AppRoute` (BE-00 §2)."""
    for name, router in routers:
        if not router.routes:
            raise RuntimeError(f"{name}: ROUTERS có router không góp route nào")
        for route in router.routes:
            if not isinstance(route, AppRoute):
                raise RuntimeError(f"{name}: route {route!r} không dùng AppRoute (protected_router/public_router)")
