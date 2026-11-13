"""Miền không gian thuần: gương Python của `src/domain/spatial` FE, cho cùng con số với FE.

Mô hình thực thể, A5, loại thay đổi, toàn vẹn, diện tích dây giày, diff theo
trường, bộ mẫu A14. Không DB, không mạng, không tệp; lỗi là `ValueError` (hoặc lớp
con), prompt gọi tự đổi sang mã HTTP của mình.
"""

from packages.domain.spatial.area import clear_area_m2, js_round, polygon_area_m2, signed_area_mm2, total_area_m2
from packages.domain.spatial.diff import DELETED, FieldChange, diff_layers, has_untracked_changes
from packages.domain.spatial.document import LayerCounts, ScaleAcceptance, document_to_json, entity_ids, layer_counts
from packages.domain.spatial.integrity import IntegrityIssue, check_integrity, check_level_order, has_critical
from packages.domain.spatial.kinds import (
    ID_PREFIX_BY_KIND,
    ChangeEntityType,
    EntityKind,
    ai_reviewed_ids,
    change_entity_type,
    vertex_id,
)
from packages.domain.spatial.model import (
    Axis,
    BoundingBox,
    Building,
    Dimension,
    Furniture,
    Level,
    Note,
    Opening,
    Point,
    Roof,
    Room,
    Segment,
    SpatialGraph,
    SpatialLayer,
    Wall,
)
from packages.domain.spatial.provenance import mark_edited, mark_edited_dimensions
from packages.domain.spatial.samples import sample_building, sample_layer, sample_roof

__all__ = [
    "DELETED",
    "ID_PREFIX_BY_KIND",
    "Axis",
    "BoundingBox",
    "Building",
    "ChangeEntityType",
    "Dimension",
    "EntityKind",
    "FieldChange",
    "Furniture",
    "IntegrityIssue",
    "LayerCounts",
    "Level",
    "Note",
    "Opening",
    "Point",
    "Roof",
    "Room",
    "ScaleAcceptance",
    "Segment",
    "SpatialGraph",
    "SpatialLayer",
    "Wall",
    "ai_reviewed_ids",
    "change_entity_type",
    "check_integrity",
    "check_level_order",
    "clear_area_m2",
    "diff_layers",
    "document_to_json",
    "entity_ids",
    "has_critical",
    "has_untracked_changes",
    "js_round",
    "layer_counts",
    "mark_edited",
    "mark_edited_dimensions",
    "polygon_area_m2",
    "sample_building",
    "sample_layer",
    "sample_roof",
    "signed_area_mm2",
    "total_area_m2",
    "vertex_id",
]
