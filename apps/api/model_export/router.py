"""Route N49-N51 của xuất GLB phía máy chủ; router mỏng, nghiệp vụ ở `service.py`.

`discover_routers()` tự nạp `ROUTERS`. N49 đòi `model.export` (viewer → 403), N50/N51 chỉ đòi là
thành viên; người ngoài → 404 (K08).
"""

from typing import Annotated, Final

from fastapi import Depends
from starlette.responses import Response, StreamingResponse

from apps.api.core.deps import ClockDep, CurrentPrincipal, DbSession, Storage
from apps.api.core.routing import protected_router, route_options
from apps.api.model_export import service
from apps.api.model_export.schemas import ModelExport
from apps.api.model_export.settings import ModelExportSettings, get_model_export_settings
from apps.api.projects.access import require_project

router = protected_router(tags=["model_export"])
ROUTERS: Final = (router,)

_export_gate: Final = Depends(require_project("model.export"))
_member_gate: Final = Depends(require_project())
ExportSettings = Annotated[ModelExportSettings, Depends(get_model_export_settings)]


@router.post("/projects/{project_id}/model-exports", status_code=202, dependencies=[_export_gate])
@route_options(idempotency="off")
async def model_export_start_export(
    project_id: str,
    response: Response,
    db: DbSession,
    storage: Storage,
    principal: CurrentPrincipal,
    clock: ClockDep,
    settings: ExportSettings,
) -> ModelExport:
    """N49 — thân `{}`; 202 với lượt `queued` mới, 200 khi đã có lượt sống cùng dấu vân (cache)."""
    result, status = await service.start_export(
        db=db, storage=storage, project_id=project_id, principal=principal, clock=clock, settings=settings
    )
    response.status_code = status
    return result


@router.get("/projects/{project_id}/model-exports/latest", dependencies=[_member_gate])
async def model_export_read_latest(project_id: str, db: DbSession, settings: ExportSettings) -> ModelExport:
    """N50 — lượt mới nhất không `superseded`, kèm `stale`; chưa có → 404 `modelExport`."""
    return await service.read_latest(db, project_id=project_id, settings=settings)


@router.get(
    "/projects/{project_id}/model-exports/{export_id}/files/{file_name}",
    response_class=StreamingResponse,
    status_code=200,
    dependencies=[_member_gate],
)
async def model_export_download_file(
    project_id: str, export_id: str, file_name: str, db: DbSession, storage: Storage
) -> StreamingResponse:
    """N51 — stream `model.glb` | `report.json` | `CREDITS.txt` của lượt `done`."""
    return await service.download_file(db, storage, project_id=project_id, export_id=export_id, file_name=file_name)
