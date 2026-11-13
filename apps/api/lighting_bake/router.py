"""Route N42-N44, N49 của nướng ánh sáng; router mỏng, nghiệp vụ ở `service.py`.

`discover_routers()` tự nạp `ROUTERS` của module này — không có file đăng ký riêng.
N42 đòi `layer.edit` (viewer → 403), N43/N44 chỉ đòi là thành viên; người ngoài → 404 (K08).
"""

from typing import Annotated, Final

from fastapi import Depends
from starlette.requests import Request
from starlette.responses import StreamingResponse

from apps.api.core.deps import ClockDep, CurrentPrincipal, DbSession, Storage
from apps.api.core.routing import protected_router, route_options
from apps.api.lighting_bake import service
from apps.api.lighting_bake.schemas import LightingBake, LightingBakeAvailability
from apps.api.lighting_bake.settings import UPLOAD_BODY_LIMIT, LightingBakeSettings, get_lighting_bake_settings
from apps.api.projects.access import require_project

router = protected_router(tags=["lighting_bake"])
ROUTERS: Final = (router,)

_edit_gate: Final = Depends(require_project("layer.edit"))
_member_gate: Final = Depends(require_project())
BakeSettings = Annotated[LightingBakeSettings, Depends(get_lighting_bake_settings)]


@router.post("/projects/{project_id}/lighting-bakes", status_code=202, dependencies=[_edit_gate])
@route_options(body_limit=UPLOAD_BODY_LIMIT, idempotency="off")
async def lighting_bake_start_bake(
    project_id: str,
    request: Request,
    db: DbSession,
    storage: Storage,
    principal: CurrentPrincipal,
    clock: ClockDep,
    settings: BakeSettings,
) -> LightingBake:
    """N42 — thân là byte GLB thô; 202 với lượt `queued`, 409 khi dự án đang nướng."""
    return await service.start_bake(
        request, db=db, storage=storage, project_id=project_id, principal=principal, clock=clock, settings=settings
    )


@router.get("/projects/{project_id}/lighting-bakes/availability", dependencies=[_member_gate])
async def lighting_bake_read_availability(project_id: str, settings: BakeSettings) -> LightingBakeAvailability:
    """N49 — `enabled`: máy chủ có worker nướng hay không (cờ `LIGHTING_BAKE_ENABLED`)."""
    return service.read_availability(settings)


@router.get("/projects/{project_id}/lighting-bakes/latest", dependencies=[_member_gate])
async def lighting_bake_read_latest(project_id: str, db: DbSession) -> LightingBake:
    """N43 — lượt mới nhất của dự án; chưa có → 404 `lightingBake`."""
    return await service.read_latest(db, project_id=project_id)


@router.get(
    "/projects/{project_id}/lighting-bakes/{bake_id}/files/{file_name}",
    response_class=StreamingResponse,
    status_code=200,
    dependencies=[_member_gate],
)
async def lighting_bake_download_file(
    project_id: str, bake_id: str, file_name: str, db: DbSession, storage: Storage
) -> StreamingResponse:
    """N44 — stream tệp kết quả của lượt `done` (attachment, cache riêng 10 phút)."""
    return await service.download_file(db, storage, project_id=project_id, bake_id=bake_id, file_name=file_name)
