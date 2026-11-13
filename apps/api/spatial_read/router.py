"""Ba route đọc lớp không gian: #33, N15, N16 (B3-02 [2], [7]).

Router **mỏng** như `apps/api/floors/router.py`: `require_project()` đặt ở `dependencies=`
và vì thế chạy **trước** mọi câu tìm tầng (K08) — người ngoài dự án nhận 404
`resource:"project"` mà không biết tầng nào có thật. Quyền là "thành viên bất kỳ": viewer
đọc được.

**Không route nào ghi** ([6]). Tầng chưa có dòng `floor_documents` dùng `empty_document`;
`ensure_document` là việc của người ghi (B3-03).

`request.app` đi thẳng xuống `floor_outs` và `load_pages` để `extensions.override` của test
áp đúng app của request thay vì bảng dò toàn cục.
"""

from typing import Final

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from apps.api.core.deps import DbSession, Storage
from apps.api.core.routing import protected_router
from apps.api.floors.lookup import floor_outs, get_floor
from apps.api.projects.access import require_project
from apps.api.projects.wire import FloorOut
from apps.api.spatial_read import detected
from apps.api.spatial_read.assemble import (
    effective_scale_source,
    level_out,
    provisional_scale_source,
    scale_status,
    suggested_scale_mm_per_px,
)
from apps.api.spatial_read.documents import empty_document, load_document
from apps.api.spatial_read.graph import spatial_graph
from apps.api.spatial_read.http_cache import cached_json
from apps.api.spatial_read.pages import load_pages
from apps.api.spatial_read.readiness import latest_run_facts, readiness_of, scale_basis_of, scale_state_of
from apps.api.spatial_read.wire import (
    FloorLayerDocumentOut,
    PointOut,
    SpatialGraphDocumentOut,
    axes_out,
    dimensions_out,
    layer_out,
)
from packages.core.error_codes import NOT_FOUND
from packages.db.models.drawings import DrawingRow
from packages.db.models.floors import FloorRow

router = protected_router(tags=["spatial"])
ROUTERS: Final = (router,)
_MM_PER_INCH: Final = 25.4


async def _floor_or_404(db: AsyncSession, project_id: str, floor_id: str) -> FloorRow:
    """Tầng chưa xoá của dự án, hay 404 `resource:"floor"` — một luật cho cả #33 và N16."""
    floor = await get_floor(db, project_id=project_id, level_id=floor_id)
    if floor is None:
        raise NOT_FOUND.error(resource="floor")
    return floor


async def _source_dpi(db: AsyncSession, floor_pk: int) -> float | None:
    """`sourceDpi` của N16: `px_per_paper_mm · 25,4` của bản vẽ hiện hành; ảnh raster hay chưa có bản vẽ → `None`."""
    stmt = select(DrawingRow.px_per_paper_mm).where(DrawingRow.floor_pk == floor_pk)
    value = (await db.execute(stmt)).scalar_one_or_none()
    return None if value is None else round(value * _MM_PER_INCH, 3)


async def _floor_out_or_404(db: AsyncSession, project_id: str, floor_pk: int, *, app: object) -> FloorOut:
    """`FloorOut` của một tầng vừa tìm thấy; rỗng nghĩa là tầng bị xoá **giữa** hai câu lệnh.

    Trả 404 thay vì `IndexError`: một lượt xoá chen vào giữa hai truy vấn là chuyện bình
    thường của đọc không khoá, và câu trả lời đúng cho nó là "tầng không còn".
    """
    outs = await floor_outs(db, project_id=project_id, floor_pks=[floor_pk], app=app)
    if not outs:
        raise NOT_FOUND.error(resource="floor")
    return outs[0]


@router.get("/projects/{project_id}/floors/{floor_id}/spatial", dependencies=[Depends(require_project())])
async def spatial_read_floor(project_id: str, floor_id: str, request: Request, db: DbSession) -> FloorOut:
    """#33 — siêu dữ liệu một tầng, đúng `Floor` mà #12 trả (cùng đường `floor_outs`)."""
    floor = await _floor_or_404(db, project_id, floor_id)
    return await _floor_out_or_404(db, project_id, floor.pk, app=request.app)


@router.get(
    "/projects/{project_id}/spatial",
    dependencies=[Depends(require_project())],
    response_model=SpatialGraphDocumentOut,
)
async def spatial_read_graph(project_id: str, request: Request, db: DbSession) -> Response:
    """N15 — cả đồ thị của dự án kèm `revision` từng tầng; dự án chưa có tầng → danh sách rỗng.

    Serialize một lần + `ETag`/304 (`cached_json`); `response_model` giữ openapi như cũ.
    """
    return cached_json(request, await spatial_graph(db, project_id, app=request.app))


@router.get(
    "/projects/{project_id}/floors/{floor_id}/spatial/layer",
    dependencies=[Depends(require_project())],
    response_model=FloorLayerDocumentOut,
)
async def spatial_read_layer(project_id: str, floor_id: str, request: Request, db: DbSession) -> Response:
    """N16 — lớp của một tầng: `revision`, `level`, `scaleStatus`/`readiness`, bốn danh sách, trục, kích thước.

    Serialize một lần + `ETag`/304 (`cached_json`); `response_model` giữ openapi như cũ.
    """
    floor = await _floor_or_404(db, project_id, floor_id)
    stored = await load_document(db, floor.pk)
    document = empty_document(floor.pk) if stored is None else stored
    page_key = (await load_pages(db, [floor.pk], app=request.app)).get(floor.pk)
    source = effective_scale_source(document, page_key)
    out = await _floor_out_or_404(db, project_id, floor.pk, app=request.app)
    run = await latest_run_facts(db, floor.pk)
    basis = scale_basis_of(run, document.scale_source, source)
    state, reasons = scale_state_of(source, document.scale_source, basis, None if run is None else run.quality_summary)
    out_document = FloorLayerDocumentOut(
        revision=document.revision,
        level=level_out(out, document, source),
        scale_status=scale_status(source),
        scale_basis=basis,
        scale_state=state,
        scale_reasons=None if state is None else reasons,
        source_dpi=await _source_dpi(db, floor.pk),
        scale_source=provisional_scale_source(source),
        suggested_scale_mm_per_px=suggested_scale_mm_per_px(document, source),
        readiness=readiness_of(run=run, scale_source=source, layer=document.layer, scale_basis=basis),
        layer=layer_out(document.layer),
        axes=axes_out(document.axes),
        dimensions=dimensions_out(document.dimensions),
        origin=None if document.origin is None else PointOut.model_validate(document.origin.model_dump()),
    )
    return cached_json(request, out_document)


@router.get(
    "/projects/{project_id}/floors/{floor_id}/dimension-strings",
    dependencies=[Depends(require_project())],
    response_model=detected.DimensionStringsOut,
)
async def spatial_read_dimension_strings(
    project_id: str, floor_id: str, request: Request, db: DbSession, storage: Storage
) -> Response:
    """Chuỗi kích thước toàn số của lượt mới nhất (QT-SCALE-API); chưa có lượt/artifact → `items: []`."""
    floor = await _floor_or_404(db, project_id, floor_id)
    run = await detected.latest_run(db, floor.pk)
    data = (
        None
        if run is None
        else await detected.read_artifact(
            storage, project_id=project_id, floor_id=floor_id, run=run, step="dimensionReading"
        )
    )
    out = detected.DimensionStringsOut(
        run_id=None if run is None else run[0],
        page_px=await detected.page_px(db, floor.pk),
        items=detected.dimension_items(detected.parse_texts(data)),
    )
    return cached_json(request, out)


@router.get(
    "/projects/{project_id}/floors/{floor_id}/detected-geometry",
    dependencies=[Depends(require_project())],
    response_model=detected.DetectedGeometryOut,
)
async def spatial_read_detected_geometry(
    project_id: str, floor_id: str, request: Request, db: DbSession, storage: Storage
) -> Response:
    """Điểm bắt, bề dày tường, cửa, phòng lớn nhất, hộp ngoài theo px trang (QT-SCALE-API)."""
    floor = await _floor_or_404(db, project_id, floor_id)
    run = await detected.latest_run(db, floor.pk)
    walls_bytes = objects_bytes = None
    if run is not None:
        walls_bytes = await detected.read_artifact(
            storage, project_id=project_id, floor_id=floor_id, run=run, step="wallSegmentation"
        )
        objects_bytes = await detected.read_artifact(
            storage, project_id=project_id, floor_id=floor_id, run=run, step="openingAndFurnitureDetection"
        )
    walls = detected.parse_walls(walls_bytes)
    stored = await load_document(db, floor.pk)
    document = empty_document(floor.pk) if stored is None else stored
    scale = None if document.scale_mm_per_px is None else float(document.scale_mm_per_px)
    out = detected.DetectedGeometryOut(
        page_px=await detected.page_px(db, floor.pk),
        wall_thickness_px=detected.thickness(walls),
        snap_points=detected.snap_points(walls),
        doors=detected.doors(detected.parse_detections(objects_bytes)),
        largest_room=detected.largest_room(document.layer, scale),
        outer_box_px=detected.outer_box(walls),
    )
    return cached_json(request, out)
