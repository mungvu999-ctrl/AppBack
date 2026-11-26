"""Hai route luồng SSE: S1 tiến độ, S2 thông báo (hợp đồng S1, S2; B4-01 [2], [6], [7]).

Cả hai **công khai** theo B0-06 [7] (Loại S): không `Authorization: Bearer`, xác thực
bằng cookie luồng `appback_stream` cộng `check_session`. GET không đòi `Origin` (K31):
trình duyệt không gửi header đó cho `EventSource` cùng origin, nên đòi nó là chặn hết
người dùng thật; chỉ `Origin` **có mà lệch** mới 403 (S09).

Tham số đường kiểm bằng `is_id` chứ không bằng ràng buộc Pydantic: sai mẫu phải là 404
`NOT_FOUND` (người ngoài không được biết id nào có thật), còn Pydantic sẽ trả 422.

`lifespan` của router mở pool Redis **riêng** cho SSE và dựng sổ nhà cung cấp. Cả hai
hỏng là hỏng lúc khởi động, không phải lúc người dùng đầu tiên mở luồng (R-17).
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Annotated, Any, Final

from fastapi import Depends, FastAPI, Query
from redis.asyncio import ConnectionPool
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from apps.api.core.origin import reject_foreign_origin
from apps.api.core.permissions import ANY_MEMBER, permission_dependency
from apps.api.core.routing import PublicRoute, public_router
from apps.api.streams.registry import NOTIFICATIONS, UPLOAD_PROGRESS, build_registry
from apps.api.streams.settings import StreamSettings, get_stream_settings
from apps.api.streams.sse import StreamRequest, authenticate_stream, open_stream, release_held
from packages.core.error_codes import NOT_FOUND
from packages.core.ids import is_id
from packages.messaging.redis import CONNECT_TIMEOUT_S, STREAM_DB, STREAM_READ_TIMEOUT_S, with_db
from packages.messaging.settings import get_messaging_settings
from packages.messaging.streams import upload_stream, user_stream

LastEventId = Annotated[str | None, Query(alias="lastEventId")]
"""Query nối lại của FE (`src/lib/realtime/eventChannel.ts:62-72`); sai mẫu = mở mới."""


def build_stream_pool(settings: StreamSettings) -> ConnectionPool:
    """Pool Redis chỉ dành cho SSE, **có trần** `STREAM_MAX_GLOBAL / API_WORKERS` (B4-01 [5], PERF-03).

    Không dùng pool của `streams_redis()`: trần mặc định của nó là 2**31, đủ để vài nghìn
    luồng chạm `maxclients` của `redis-broker` — instance dùng chung với Celery và DB an
    toàn, nên hết socket ở đây là mất cả việc nền lẫn khoá đăng nhập.
    """
    url = with_db(get_messaging_settings().redis_broker_url, STREAM_DB)
    return ConnectionPool.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=CONNECT_TIMEOUT_S,
        socket_timeout=STREAM_READ_TIMEOUT_S,
        max_connections=settings.stream_max_per_process,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Dựng sổ nhà cung cấp, pool SSE và bộ đếm chỗ toàn cục; đóng pool khi app dừng."""
    settings = get_stream_settings()
    app.state.stream_registry = build_registry(app)
    # `BoundedSemaphore`: một lượt trả chỗ dư (dọn chạy hai lần) ném `ValueError` ngay thay vì
    # âm thầm nới trần toàn cục lên quá cỡ pool SSE (RES-02).
    app.state.stream_slots = asyncio.BoundedSemaphore(settings.stream_max_per_process)
    pool = build_stream_pool(settings)
    app.state.stream_pool = pool
    try:
        yield
    finally:
        await pool.aclose()


class StreamRoute(PublicRoute):
    """Route công khai của luồng, thêm một lưới dọn cho bước **sau** handler (NO-156).

    `AppRoute._finish` (idempotency → commit → chờ callback sau commit) chạy sau khi handler
    đã trả `StreamingResponse`. Nó ném là response bị bỏ, `stream_body` không bao giờ chạy,
    và ba thứ `open_stream` đã giữ — chỗ ZSET của người dùng, chỗ toàn cục, kết nối pool SSE
    — rò cho tới khi TTL dọn hộ. Chỉ chủ của route luồng vá được chỗ này: khung không biết
    handler đã giữ gì.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Bọc handler của khung: ngoại lệ nào lọt ra cũng trả lại tài nguyên đã giữ trước."""
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            """Đường thường không đổi; hỏng thì dọn rồi mới để lỗi đi tiếp."""
            try:
                return await original(request)
            except BaseException:
                await release_held(request)
                raise

        return handler


router = public_router(prefix="/streams", tags=["streams"], lifespan=lifespan)
# Đặt sau khi dựng router: `public_router` đã truyền `route_class=PublicRoute`, nên không
# thêm được qua `**kwargs`. Route được thêm bởi các decorator **dưới** dòng này, và
# `add_api_route` đọc `self.route_class` lúc đó, nên cả hai route luồng đều là `StreamRoute`.
router.route_class = StreamRoute
ROUTERS: Final = (router,)


@permission_dependency(ANY_MEMBER)
async def membership_checked_by_provider() -> None:
    """Khai khoá "thành viên" của S1 cho lượt quét route; **không** tự kiểm gì.

    Route Loại S không có `Principal` từ Bearer nên `require_project` của B2-01 dùng không
    được ở đây. Thành viên được kiểm thật trong `StreamAccessPolicy.authorize` của B2-04 —
    cả lúc mở luồng (S06) lẫn mỗi `STREAM_RECHECK_S` sau đó (S07). Dependency này chỉ để
    `Operation.permission_key` khớp `lock` ở `cases.toml` (BE-00 §5, `apps/api/core/openapi.py`).
    """


@router.get(
    "/projects/{project_id}/uploads/{upload_id}/progress",
    response_class=StreamingResponse,
    dependencies=[Depends(membership_checked_by_provider)],
)
async def streams_open_progress(
    request: Request, project_id: str, upload_id: str, last_event_id: LastEventId = None
) -> StreamingResponse:
    """Luồng tiến độ của một lượt tải (S1); mở mới hoặc id đã bị cắt → ảnh chụp trước (S08)."""
    reject_foreign_origin(request)
    services, principal, claims = await authenticate_stream(request)
    if not (is_id("prj", project_id) and is_id("upl", upload_id)):
        raise NOT_FOUND.error(resource="upload")
    spec = StreamRequest(
        kind=UPLOAD_PROGRESS,
        stream=upload_stream(upload_id),
        params={"project_id": project_id, "upload_id": upload_id},
        principal=principal,
        claims=claims,
        services=services,
        last_event_id=last_event_id,
    )
    return await open_stream(request, spec)


@router.get("/notifications", response_class=StreamingResponse)
async def streams_open_notifications(request: Request, last_event_id: LastEventId = None) -> StreamingResponse:
    """Luồng thông báo của **chính** người gọi (S2); không ảnh chụp, không phát lại khi mở mới (K32)."""
    reject_foreign_origin(request)
    services, principal, claims = await authenticate_stream(request)
    spec = StreamRequest(
        kind=NOTIFICATIONS,
        stream=user_stream(principal.user_id),
        params={},
        principal=principal,
        claims=claims,
        services=services,
        last_event_id=last_event_id,
    )
    return await open_stream(request, spec)
