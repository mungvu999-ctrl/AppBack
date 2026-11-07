"""Route ghi lớp không gian #35 (B3-03 [2], [6], [7]); router **mỏng**, việc ở `writer.py`.

`require_project("layer.edit")` là tham số nên nó chạy **trước** cả guard 428 và Pydantic:
người ngoài dự án nhận 404 `resource:"project"` mà không biết tầng nào có thật (BE-00 §4),
viewer nhận 403. Duyệt QC (cấp `reviewed=True` mới) còn đòi `qc.approve`: `write_layer` kiểm
dưới khoá tầng, so với tài liệu thật (NFR-02). Guard 428 của khung (`route_options(versioned=True)`)
gắn cuối cây dependency nên "thiếu `baseVersion`" ra 428 chứ không phải 422.

`body_limit=8 MiB` (HOP-DONG-MOI #35) buộc `idempotency="off"`: bảng `idempotency_records`
không nhận việc của route này, header `Idempotency-Key` FE gắn sẵn bị bỏ qua và lượt lặp đi
qua C09b của `write_layer`. Handler **không** `commit` — `AppRoute` commit khi handler xong.
"""

from typing import Annotated, Final

from fastapi import Depends

from apps.api.core.deps import ClockDep, DbSession
from apps.api.core.routing import protected_router, route_options
from apps.api.floors.lookup import get_floor
from apps.api.projects.access import ProjectAccess, require_project
from apps.api.spatial_read.wire import layer_out
from apps.api.spatial_write.schemas import FloorLayerWriteResultSchema, FloorLayerWriteSchema
from apps.api.spatial_write.writer import LayerWrite, actor_name, write_layer
from packages.core.error_codes import NOT_FOUND
from packages.domain.permissions import can

BODY_LIMIT: Final = 8 * 1024 * 1024
"""Trần thân của #35 (HOP-DONG-MOI §4 "Ghi lớp"): tầng 3.000 tường vượt 1 MiB mặc định."""

router = protected_router(tags=["spatial"])
ROUTERS: Final = (router,)


@router.put("/projects/{project_id}/floors/{floor_id}/spatial/layer")
@route_options(versioned=True, body_limit=BODY_LIMIT, idempotency="off")
async def spatial_write_layer(
    floor_id: str,
    body: FloorLayerWriteSchema,
    access: Annotated[ProjectAccess, Depends(require_project("layer.edit"))],
    db: DbSession,
    clock: ClockDep,
) -> FloorLayerWriteResultSchema:
    """#35 — ghi lớp và/hoặc tỉ lệ một tầng: base cũ → 409 W20, lượt lặp của chính mình → 200."""
    floor = await get_floor(db, project_id=access.project_id, level_id=floor_id)
    if floor is None:
        raise NOT_FOUND.error(resource="floor")
    result = await write_layer(
        db,
        floor_pk=floor.pk,
        base_revision=body.base_version,
        body=LayerWrite(
            layer=body.body.layer,
            scale_mm_per_px=body.body.scale_millimetres_per_pixel,
            dimensions=None if body.body.dimensions is None else tuple(body.body.dimensions),
            axes=None if body.body.axes is None else tuple(body.body.axes),
            origin=body.body.origin,
        ),
        actor_id=access.principal.user_id,
        actor_name=await actor_name(db, access.principal.user_id),
        clock=clock,
        may_approve=can(access.role, "qc.approve"),
    )
    return FloorLayerWriteResultSchema(revision=result.revision, layer=layer_out(result.layer))
