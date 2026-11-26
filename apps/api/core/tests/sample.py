"""App thử của B0-06: module mẫu để kiểm **máy móc** của khung.

Khung phải chứng minh được thứ tự xác thực, guard, idempotency, rate limit… nhưng
hôm nay repo chưa có một route nghiệp vụ nào. Vì vậy bộ test dựng một app riêng từ
các router mẫu ở đây — vẫn là `create_app` thật, vẫn Postgres và Redis thật (K23),
chỉ khác ở chỗ route là route mẫu.

Route mẫu **không** nằm dưới `apps/api/<module>/router.py` nên không bị `create_app`
dò vào app thật, và đường của chúng (`/api/sample/...`) không khớp thao tác nào của
bảng hợp đồng nên không sinh vết case giả.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Final

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, Body, Depends, FastAPI
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from apps.api.core.app import create_app
from apps.api.core.auth import FakeTokenVerifier
from apps.api.core.deps import Bus, ClockDep, CurrentPrincipal, DbSession, Settings, Storage
from apps.api.core.origin import reject_foreign_origin, require_origin
from apps.api.core.pagination import CursorPage, PageParams, encode_cursor, page_params
from apps.api.core.permissions import permission_dependency
from apps.api.core.ratelimit import key_ip, key_user, rate_limit
from apps.api.core.routing import protected_router, public_router, route_options
from apps.api.core.wire import KeepNull, NfcStr, WireDatetime, WireModel, WireRequest
from packages.core.error_codes import NOT_FOUND
from packages.core.settings import CoreSettings, get_core_settings
from packages.db.hooks import on_after_commit
from packages.messaging.redis import broker_redis_sync
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.clock import FakeClock

SAMPLE_PREFIX: Final = "/sample"
BIG_BODY_LIMIT: Final = 8 * 1024 * 1024
HUGE_BODY_LIMIT: Final = 128 * 1024 * 1024
PAGE_MAX_LIMIT: Final = 3
LIMITED_QUOTA: Final = 2
LIMITED_WINDOW_S: Final = 60
GATE_TIMEOUT_S: Final = 90.0
"""Lưới an toàn của route treo: rộng hơn `WAIT_TIMEOUT_S` để cổng chỉ mở do test, không do hết giờ."""
WAIT_TIMEOUT_S: Final = 60.0
POLL_S: Final = 0.01

# Câu lệnh viết thẳng, không f-string: tên bảng là hằng của chính file này, và
# ghép chuỗi ở đây chỉ làm ruff phải phân biệt "an toàn" với "chưa chắc" (S608).
CREATE_TABLE: Final = "CREATE TABLE IF NOT EXISTS sample_rows (id text primary key)"
INSERT_ROW: Final = "INSERT INTO sample_rows (id) VALUES (:id)"
SELECT_ROWS: Final = "SELECT id FROM sample_rows ORDER BY id"

router = protected_router(prefix=SAMPLE_PREFIX, tags=["sample"])
public = public_router(prefix=SAMPLE_PREFIX, tags=["sample"])

SAMPLE_ROUTERS: Final[tuple[tuple[str, APIRouter], ...]] = (
    ("apps.api.sample_protected.router", router),
    ("apps.api.sample_public.router", public),
)

after_commit_marks: Final[list[str]] = []
"""Dấu vết callback sau commit; fixture `clear_after_commit_marks` dọn quanh test (J09)."""


class ItemBody(WireRequest):
    """Thân đơn giản có chuỗi người nhập (C16) và một trường tuỳ chọn."""

    name: NfcStr
    note: str | None = None


class ProjectItemBody(WireRequest):
    """Thân mang id trùng nghĩa với tham số đường (W21)."""

    project_id: str
    name: NfcStr


class VersionedItemBody(WireRequest):
    """Thân của ghi có version — khai tay để dùng được làm chú thích kiểu (W20)."""

    base_version: int = Field(ge=0)
    body: ItemBody


class OverrideItem(WireRequest):
    """Một mục trong dict do người dùng đặt khoá (`WALL-THICKNESS`, `registry.ts:443`)."""

    severity: str


class OverridesBody(WireRequest):
    """Thân có dict khoá tự do — nguồn của loc Pydantic không phải tên trường."""

    overrides: dict[str, OverrideItem]


class DepsOut(WireModel):
    """Bằng chứng mọi dependency của `deps.py` lấy đúng tài nguyên từ `app.state`."""

    user_id: str
    bus: str
    storage: str
    base_url: str
    now: WireDatetime


class ItemOut(WireModel):
    """Response bắt buộc đủ trường — cho `has_optional_response_fields=False`."""

    name: str


class OptionalOut(WireModel):
    """Response có trường tuỳ chọn và một trường `KeepNull` (W2, C17)."""

    name: str
    note: str | None = None
    seen_at: KeepNull[WireDatetime | None] = None


@permission_dependency("sample.manage")
def deny_sample(request: Request) -> None:
    """Cổng quyền mẫu: luôn 404 như "không phải thành viên" (BE-00 §4, K08)."""
    raise NOT_FOUND.error(resource="project")


@public.get("/public", response_model=ItemOut)
async def sample_public_read() -> ItemOut:
    """Route công khai: không đọc `Authorization`, không nhận việc idempotency."""
    return ItemOut(name="public")


@public.get("/echo/{item_id}", response_model=ItemOut)
async def sample_echo_item(item_id: str) -> ItemOut:
    """Route công khai **có tham số đường** — để đo metric RED gắn mẫu đường, không đường thật."""
    return ItemOut(name=item_id)


@router.post("/items", response_model=ItemOut)
async def sample_create_item(body: ItemBody) -> ItemOut:
    """Route được bảo vệ, có idempotency — trục của mọi test C10/C22."""
    return ItemOut(name=body.name)


async def read_db_first(db: DbSession) -> None:
    """Cổng quyền giả đọc DB, như cổng thật: session của request giữ một kết nối **trước** guard idempotency."""
    await db.execute(text("SELECT 1"))


@router.post("/db-first", response_model=ItemOut, dependencies=[Depends(read_db_first)])
async def sample_create_after_db_read(body: ItemBody) -> ItemOut:
    """Ghi có idempotency sau một dependency đã giữ kết nối của pool request."""
    return ItemOut(name=body.name)


@router.post("/projects/{project_id}/items", response_model=ItemOut)
async def sample_create_project_item(project_id: str, body: ProjectItemBody, db: DbSession) -> ItemOut:
    """Id trên đường và trong thân cùng nghĩa → guard W21 chạy trước handler."""
    await db.execute(text(INSERT_ROW), {"id": project_id})
    return ItemOut(name=body.name)


def read_project_path(request: Request) -> None:
    """Cổng mẫu đọc thẳng `request.path_params` như `require_project`; endpoint không khai `project_id` (NO-351)."""
    request.state.sample_project = request.path_params["project_id"]


@router.post("/projects/{project_id}/notes", response_model=ItemOut, dependencies=[Depends(read_project_path)])
async def sample_create_project_note(body: ItemBody) -> ItemOut:
    """Tham số đường chỉ dependency đọc, thân `extra="forbid"` không có `projectId` → guard W21 vẫn phải chạy."""
    return ItemOut(name=body.name)


@router.put("/versions/{project_id}", response_model=ItemOut)
@route_options(versioned=True)
async def sample_replace_version(project_id: str, body: VersionedItemBody) -> ItemOut:
    """Ghi có version: thiếu `baseVersion` → 428 trước Pydantic (W20)."""
    return ItemOut(name=body.body.name)


@router.post("/big", response_model=ItemOut)
@route_options(body_limit=BIG_BODY_LIMIT, idempotency="off")
async def sample_write_big(body: ItemBody) -> ItemOut:
    """Trần thân rộng hơn 1 MiB nên bắt buộc `idempotency="off"` (BE-00 §7)."""
    return ItemOut(name=body.name)


@router.post("/huge", response_model=ItemOut)
@route_options(body_limit=HUGE_BODY_LIMIT, idempotency="off")
async def sample_write_huge(body: ItemBody) -> ItemOut:
    """Trần rất rộng: dùng để chứng minh 401 tới **trước** khi app đọc thân."""
    return ItemOut(name=body.name)


@router.post("/guarded", response_model=ItemOut, dependencies=[Depends(deny_sample)])
async def sample_guarded_create(body: ItemBody) -> ItemOut:
    """Quyền luôn 404: chứng minh nhận việc idempotency chạy **sau** dependency quyền."""
    return ItemOut(name=body.name)


@router.post("/gated", response_model=ItemOut, dependencies=[Depends(deny_sample)])
async def sample_gated_create(body: ItemBody, request: Request) -> ItemOut:
    """Treo cho tới khi test mở cổng — để dựng hai lượt song song cùng khoá (C14, C22)."""
    gate: asyncio.Event = request.app.state.gate
    await asyncio.wait_for(gate.wait(), timeout=GATE_TIMEOUT_S)
    return ItemOut(name=body.name)


def grant_stub() -> None:
    """Bản ghi đè cho mọi dependency quyền trong test chung (cổng case)."""


@router.post("/conflict")
async def sample_write_conflict(body: ItemBody, db: DbSession) -> Response:
    """Handler **trả về** 409: ghi của nó vẫn phải được commit (BE-00 §7)."""
    await db.execute(text(INSERT_ROW), {"id": body.name})
    return JSONResponse({"code": "SAMPLE_CONFLICT", "requestId": "sample"}, status_code=409)


@router.post("/overrides", response_model=ItemOut)
async def sample_write_overrides(body: OverridesBody) -> ItemOut:
    """Lỗi Pydantic sâu trong dict: `field` phải dừng trước khoá không phải tên trường."""
    return ItemOut(name=",".join(sorted(body.overrides)))


@router.get("/deps", response_model=DepsOut)
async def sample_read_deps(
    principal: CurrentPrincipal,
    bus: Bus,
    store: Storage,
    settings: Settings,
    clock: ClockDep,
) -> DepsOut:
    """Mọi dependency của khung trong một route."""
    return DepsOut(
        user_id=principal.user_id,
        bus=type(bus).__name__,
        storage=type(store).__name__,
        base_url=settings.public_base_url,
        now=clock.now(),
    )


@router.post("/throttled")
async def sample_throttled(body: ItemBody) -> Response:
    """Handler **trả** 429: status không lưu, dòng idempotency phải bị xoá (BE-00 §7)."""
    return JSONResponse({"code": "RATE_LIMITED", "requestId": "sample"}, status_code=429)


@router.post("/broken-tx", response_model=ItemOut)
async def sample_broken_transaction(body: ItemBody, db: DbSession) -> ItemOut:
    """Giao dịch hỏng mà handler nuốt lỗi: `commit()` mới ném, `AppRoute` phải rollback."""
    with suppress(Exception):
        await db.execute(text("SELECT 1 / 0"))
    return ItemOut(name=body.name)


@router.post("/scalar", response_model=ItemOut)
async def sample_write_scalar(amount: Annotated[int, Body(embed=True)]) -> ItemOut:
    """Thân là tham số nhúng, không phải model — `Operation.body_mirrors_path` phải chịu được."""
    return ItemOut(name=str(amount))


@router.post("/boom", response_model=ItemOut)
async def sample_boom(body: ItemBody) -> ItemOut:
    """Ngoại lệ lạ → 500 `INTERNAL`, không lộ stack."""
    raise RuntimeError("bể rồi")


@router.post("/after-commit", response_model=ItemOut)
async def sample_after_commit(body: ItemBody, db: DbSession) -> ItemOut:
    """Xếp việc **sau** commit (J09); `AppRoute` chờ callback xong mới trả response."""
    await db.execute(text(INSERT_ROW), {"id": body.name})
    name = body.name
    on_after_commit(db, lambda: after_commit_marks.append(name))
    return ItemOut(name=name)


@router.post("/dead-broker", response_model=ItemOut)
async def sample_dead_broker(body: ItemBody, db: DbSession) -> ItemOut:
    """Callback sau commit chạm broker đã dừng: request vẫn phải xong nhanh."""
    on_after_commit(db, _ping_broker)
    return ItemOut(name=body.name)


def _ping_broker() -> None:
    """Callback cố ý chạm broker; broker chết thì `on_after_commit` nuốt lỗi và log."""
    broker_redis_sync().ping()


@router.get("/page", response_model=CursorPage[ItemOut])
async def sample_list_page(page: Annotated[PageParams, Depends(page_params(PAGE_MAX_LIMIT))]) -> CursorPage[ItemOut]:
    """Danh sách mới: `limit` có trần cố định lúc khai route (C02, C15)."""
    items = [ItemOut(name=f"item-{index}") for index in range(page.limit)]
    return CursorPage(items=items, next_cursor=encode_cursor("sample_list_page", {}, {"after": page.limit}))


@router.get("/optional", response_model=OptionalOut)
async def sample_read_optional() -> OptionalOut:
    """`None` bị bỏ, `KeepNull` giữ lại (W2, C17)."""
    return OptionalOut(name="tuỳ chọn", seen_at=None)


@router.get("/now", response_model=OptionalOut)
async def sample_read_now(clock: ClockDep) -> OptionalOut:
    """Ngày giờ ra dây đúng `.sssZ` (W3)."""
    return OptionalOut(name="giờ", seen_at=clock.now())


@router.post("/origin", response_model=ItemOut, dependencies=[Depends(require_origin)])
async def sample_origin_write(body: ItemBody) -> ItemOut:
    """Ghi dùng cookie: thiếu hay lệch `Origin` đều 403 (C24)."""
    return ItemOut(name=body.name)


@router.get("/origin", response_model=ItemOut, dependencies=[Depends(reject_foreign_origin)])
async def sample_origin_read() -> ItemOut:
    """GET luồng: không có `Origin` vẫn qua, có mà lệch thì chặn (K31)."""
    return ItemOut(name="origin")


@router.get(
    "/limited",
    response_model=ItemOut,
    dependencies=[
        Depends(
            rate_limit(
                "sample_ip",
                limit=LIMITED_QUOTA,
                window_s=LIMITED_WINDOW_S,
                key=key_ip,
                store="cache",
                on_error="closed",
            )
        )
    ],
)
async def sample_limited_read() -> ItemOut:
    """Hạn mức theo IP, Redis hỏng → 503 (`on_error="closed"`)."""
    return ItemOut(name="limited")


@router.get(
    "/limited-open",
    response_model=ItemOut,
    dependencies=[
        Depends(
            rate_limit(
                "sample_user",
                limit=LIMITED_QUOTA,
                window_s=LIMITED_WINDOW_S,
                key=key_user,
                store="safe",
                on_error="open",
            )
        )
    ],
)
async def sample_limited_open_read() -> ItemOut:
    """Hạn mức theo người dùng ở kho an toàn, Redis hỏng → cho qua."""
    return ItemOut(name="open")


def build_sample_app(
    clock: FakeClock,
    *,
    settings: CoreSettings | None = None,
    routers: Sequence[tuple[str, APIRouter]] | None = None,
) -> FastAPI:
    """App thử dùng chính `create_app` (cùng middleware, cùng `lifespan`)."""
    return create_app(
        settings or get_core_settings(),
        token_verifier=FakeTokenVerifier(),
        clock=clock,
        routers=list(routers if routers is not None else SAMPLE_ROUTERS),
    )


def lifespan_router(name: str, log: list[str]) -> APIRouter:
    """Router mẫu có `lifespan` riêng — để kiểm lifespan của từng router được gộp."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Ghi dấu mở/đóng để test thấy `lifespan` của từng router được gộp vào app."""
        log.append(f"{name}:mở")
        yield
        log.append(f"{name}:đóng")

    made = protected_router(prefix=f"/{name}", lifespan=lifespan)

    @made.get("/ping", response_model=ItemOut, name=f"{name}_ping")
    async def ping() -> ItemOut:
        """Route duy nhất của router mẫu — `check_routers` đòi mỗi router góp ít nhất một route."""
        return ItemOut(name=name)

    return made


async def create_sample_table(maker: async_sessionmaker[AsyncSession]) -> None:
    """Bảng thật cho handler mẫu ghi vào — mỗi test một database riêng (K22, K23)."""
    async with maker() as session:
        await session.execute(text(CREATE_TABLE))
        await session.commit()


async def sample_row_ids(maker: async_sessionmaker[AsyncSession]) -> list[str]:
    """Đọc lại bảng mẫu bằng **session mới** — chứng minh ghi đã bền vững (K22)."""
    async with maker() as session:
        rows = await session.execute(text(SELECT_ROWS))
        return [str(row[0]) for row in rows.all()]


async def wait_until(condition: Callable[[], Awaitable[bool]], *, timeout_s: float = WAIT_TIMEOUT_S) -> bool:
    """Chờ một **trạng thái** (đọc từ DB) thành đúng; trả `False` khi hết giờ.

    Test đồng thời xếp thứ tự request bằng trạng thái chứ không bằng `sleep`: một lượt
    bắt tay Postgres chậm (NO-007) là đủ làm đảo thứ tự mà `sleep` đã giả định.
    Trần rộng vì hàm trả **ngay** khi điều kiện đúng; trần chỉ để test không treo mãi.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await condition():
            return True
        await asyncio.sleep(POLL_S)
    return await condition()


@pytest.fixture
def sample_app(api_env: None, fake_clock: FakeClock) -> FastAPI:
    """App thử mặc định."""
    return build_sample_app(fake_clock)


@pytest_asyncio.fixture(loop_scope="function")
async def sample_client(sample_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Client của app thử, đã chạy `lifespan` và đã tạo bảng mẫu."""
    async with make_api_client(sample_app) as client:
        await create_sample_table(sample_app.state.sessionmaker)
        yield client


@pytest.fixture
def clear_after_commit_marks() -> Iterator[None]:
    """Sổ callback sau commit là biến module; dọn trước và sau mỗi test dùng nó."""
    after_commit_marks.clear()
    yield
    after_commit_marks.clear()
