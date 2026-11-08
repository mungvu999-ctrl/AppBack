"""N15 — cả đồ thị không gian của một dự án kèm `revision` từng tầng (B3-02 [6]).

**Không N+1.** Số truy vấn phải bằng nhau ở dự án 1 tầng và dự án 8 tầng, vì màn
`SpatialJsonViewer` và `ExplodedView` gọi thẳng route này: một vòng cho dòng `projects`,
một vòng `floor_outs_with_pk` (kèm cổng bản vẽ), một vòng `load_documents`, một vòng `load_pages`,
một vòng `project_rollups`. Mỗi thứ đúng một lượt, lấy theo lô `floor_pks`.

**Đọc không ghi** ([6]): tầng chưa có dòng `floor_documents` dùng `empty_document(pk)` —
`revision 0`, lớp rỗng — chứ không `ensure_document`. Một lượt `GET` không được đổi
`updated_at` của dự án hay sinh dòng mà người dùng không yêu cầu.

Thứ tự là hợp đồng: `floor_outs_with_pk` đã sắp `(floor_order, pk)`, và mọi danh sách thực thể nối
theo đúng thứ tự tầng đó, nên H1 ngữ cảnh `n15` ("`levels` không giảm theo `order`") đúng
cả khi hai tầng trùng `order`.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.floors.lookup import floor_outs_with_pk
from apps.api.projects.summaries import project_rollups
from apps.api.spatial_read.assemble import effective_scale_source, level_out
from apps.api.spatial_read.documents import empty_document, load_documents
from apps.api.spatial_read.pages import load_pages
from apps.api.spatial_read.wire import (
    BuildingOut,
    FloorRevisionOut,
    LevelOut,
    SpatialGraphDocumentOut,
    SpatialGraphOut,
    axes_out,
    dimensions_out,
    layer_out,
    suggestion_fields,
)
from packages.db.models.projects import Project

_DATUM_ELEVATION_MM = 0
"""Mốc `+0.000` của dự án; v1 chưa cho người dùng đặt cốt nền khác (HOP-DONG-MOI §4.1)."""

_HUMAN_CONFIDENCE = 1.0
"""`Building` do server suy ra chứ không do mô hình đoán (HOP-DONG-MOI §4.1)."""


def _building(name: str, address: str | None, gross_area: float, levels: list[LevelOut]) -> BuildingOut:
    """`Building` của đồ thị; `reviewed` đúng khi có ≥ 1 tầng và **mọi** tầng đã duyệt."""
    return BuildingOut(
        name=name,
        address=address,
        datum_elevation_mm=_DATUM_ELEVATION_MM,
        gross_floor_area_m2=gross_area,
        confidence=_HUMAN_CONFIDENCE,
        source="human",
        reviewed=bool(levels) and all(level.reviewed for level in levels),
    )


async def spatial_graph(db: AsyncSession, project_id: str, *, app: object | None = None) -> SpatialGraphDocumentOut:
    """Đồ thị + `floorRevisions` của một dự án; dự án chưa có tầng → hai danh sách rỗng.

    Người gọi đã kiểm quyền và sự tồn tại của dự án (`require_project`), nên dòng `projects`
    ở đây chắc chắn có — nó chỉ để lấy `name` và `address` mà `ProjectAccess` không mang.
    """
    project = (await db.execute(select(Project.name, Project.address).where(Project.id == project_id))).one()
    # `pk` đi cùng `FloorOut` từ một câu `floors` (một snapshot): tầng xoá mềm sau câu ấy vẫn
    # nằm trong đồ thị, đủ cả `levels` lẫn `floorRevisions`, thay vì lệch giữa hai câu đọc.
    pairs = [(floor, pk) for pk, floor in await floor_outs_with_pk(db, project_id=project_id, app=app)]
    pks = [pk for _, pk in pairs]
    documents = await load_documents(db, pks)
    pages = await load_pages(db, pks, app=app)
    rollup = (await project_rollups(db, [project_id]))[project_id]

    docs = [documents[pk] if pk in documents else empty_document(pk) for pk in pks]
    levels = [
        level_out(floor, doc, effective_scale_source(doc, pages.get(pk)))
        for (floor, pk), doc in zip(pairs, docs, strict=True)
    ]
    layers = [layer_out(doc.layer) for doc in docs]
    graph = SpatialGraphOut(
        building=_building(project.name, project.address, float(rollup.area_m2), levels),
        levels=levels,
        walls=[wall for layer in layers for wall in layer.walls],
        openings=[opening for layer in layers for opening in layer.openings],
        furniture=[item for layer in layers for item in layer.furniture],
        rooms=[room for layer in layers for room in layer.rooms],
        axes=[item for doc in docs for item in axes_out(doc.axes)],
        dimensions=[item for doc in docs for item in dimensions_out(doc.dimensions)],
        notes=[],
        roofs=[roof for layer in layers for roof in layer.roofs or ()] or None,
    )
    revisions = [
        FloorRevisionOut(
            floor_id=level.id,
            revision=doc.revision,
            **suggestion_fields(doc.suggestions, doc.suggestions_meta, enabled=doc.suggestions_enabled),
        )
        for level, doc in zip(levels, docs, strict=True)
    ]
    return SpatialGraphDocumentOut(graph=graph, floor_revisions=revisions)
