"""Nghiệp vụ N42-N44. SQL thuần trên bảng `lighting_bakes` (tên khung đã chốt ở hợp đồng).

N42 theo thứ tự của `apps/api/admin_ml_registry/upload.py`:

1. lượt đang chạy → 409 sớm, trước khi đọc byte nào;
2. `await db.rollback()`: thân tới 128 MiB, không giữ kết nối pool trong lúc nhận (K36);
3. `storage.put(input_key, request.stream(), max_bytes)` — đọc theo luồng, không gom RAM;
4. magic bytes không phải GLB → xoá object, 422 `FILE_TYPE_MISMATCH` (không tin `Content-Type`);
5. giao dịch ngắn: chèn dòng `queued`, đăng ký `send_task` sau commit, tự `commit` (route
   `idempotency="off"`). Hai lượt song song cùng qua bước 1 thì unique partial
   `uq_lighting_bakes_active` cho đúng một lượt thắng; lượt thua xoá object rồi 409 (C14).
"""

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import StreamingResponse

from apps.api.core.auth import Principal
from apps.api.lighting_bake.errors import LIGHTING_BAKE_DISABLED, LIGHTING_BAKE_IN_PROGRESS, bake_not_found
from apps.api.lighting_bake.schemas import LightingBake, LightingBakeAvailability
from apps.api.lighting_bake.settings import LightingBakeSettings
from packages.core.clock import Clock
from packages.core.error_codes import FILE_TYPE_MISMATCH
from packages.core.ids import is_id, new_id
from packages.db.hooks import on_after_commit
from packages.messaging import send_task
from packages.messaging.payloads.lighting_bake import (
    LIGHTING_BAKE_FILES,
    LIGHTING_BAKE_INPUT,
    LIGHTING_BAKE_TASK,
    LightingBakePayload,
)
from packages.storage.keys import lighting_bake_object
from packages.storage.port import ObjectStorage, content_disposition

GLB_CONTENT_TYPE = "model/gltf-binary"
CACHE_CONTROL = "private, max-age=600"

_ACTIVE = text("SELECT 1 FROM lighting_bakes WHERE project_id = :p AND status IN ('queued', 'running') LIMIT 1")
_INSERT = text(
    "INSERT INTO lighting_bakes (id, project_id, created_by, status, created_at, updated_at) "
    "VALUES (:id, :p, :by, 'queued', :now, :now)"
)
_LATEST = text(
    "SELECT id, status, created_at, started_at, ended_at, failure_code FROM lighting_bakes "
    "WHERE project_id = :p ORDER BY created_at DESC, id DESC LIMIT 1"
)
_DONE = text("SELECT 1 FROM lighting_bakes WHERE id = :id AND project_id = :p AND status = 'done'")


def _wire(row: Row[tuple[object, ...]]) -> LightingBake:
    """Một dòng của `_LATEST` → `LightingBake`."""
    return LightingBake.model_validate(row._asdict())


async def start_bake(
    request: Request,
    *,
    db: AsyncSession,
    storage: ObjectStorage,
    project_id: str,
    principal: Principal,
    clock: Clock,
    settings: LightingBakeSettings,
) -> LightingBake:
    """N42 — nhận GLB thô theo luồng, mở lượt `queued`, gửi task sau commit; tự `commit`."""
    if not settings.lighting_bake_enabled:
        raise LIGHTING_BAKE_DISABLED.error(retry_after=10)
    if (await db.execute(_ACTIVE, {"p": project_id})).first() is not None:
        raise LIGHTING_BAKE_IN_PROGRESS.error()
    await db.rollback()
    bake_id = new_id("bak", clock)
    key = lighting_bake_object(project_id, bake_id, LIGHTING_BAKE_INPUT)
    info = await storage.put(
        key, request.stream(), content_type=GLB_CONTENT_TYPE, max_bytes=settings.lighting_bake_upload_max_bytes
    )
    if info.kind != "glb":
        await storage.delete(key)
        raise FILE_TYPE_MISMATCH.error()
    now = clock.now()
    try:
        await db.execute(_INSERT, {"id": bake_id, "p": project_id, "by": principal.user_id, "now": now})
        on_after_commit(db, lambda: send_task(LIGHTING_BAKE_TASK, LightingBakePayload(bake_id=bake_id)))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        await storage.delete(key)
        raise LIGHTING_BAKE_IN_PROGRESS.error() from exc
    except BaseException:
        await storage.delete(key)
        raise
    return LightingBake(id=bake_id, status="queued", created_at=now)


def read_availability(settings: LightingBakeSettings) -> LightingBakeAvailability:
    """N49 — công tắc nướng ánh sáng của máy chủ."""
    return LightingBakeAvailability(enabled=settings.lighting_bake_enabled)


async def read_latest(db: AsyncSession, *, project_id: str) -> LightingBake:
    """N43 — lượt mới nhất (`created_at`, rồi id ULID) của dự án; chưa có → 404 `lightingBake`."""
    row = (await db.execute(_LATEST, {"p": project_id})).first()
    if row is None:
        raise bake_not_found()
    return _wire(row)


async def download_file(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, bake_id: str, file_name: str
) -> StreamingResponse:
    """N44 — stream một tệp kết quả của lượt `done`; mọi thất bại là cùng 404 `lightingBake`.

    Tên ngoài `LIGHTING_BAKE_FILES` (gồm `input.glb`) hay id sai mẫu → 404 **không** truy vấn DB.
    Lượt thuộc dự án khác cũng 404: `require_project` chỉ chốt dự án trên đường.
    """
    if file_name not in LIGHTING_BAKE_FILES or not is_id("bak", bake_id):
        raise bake_not_found()
    if (await db.execute(_DONE, {"id": bake_id, "p": project_id})).first() is None:
        raise bake_not_found()
    key = lighting_bake_object(project_id, bake_id, file_name)
    info = await storage.stat(key)
    if info is None:
        raise bake_not_found()
    return StreamingResponse(
        storage.open_read(key),
        media_type=info.content_type,
        headers={"Content-Disposition": content_disposition("attachment", file_name), "Cache-Control": CACHE_CONTROL},
    )
