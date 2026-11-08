"""Model **response** của #33, N15, N16 — một lớp cho mỗi schema FE (B3-02 [2]).

Soi gương `S:api/schemas/spatial.ts`, `spatialGraph.ts`, `spatialLayer.ts` ở commit ghim
và HOP-DONG-MOI §4.1. Ba luật hay quên:

- **m² và tỉ lệ là `float`, không `Decimal`** (K01): zod khai `z.number()`, còn Pydantic in
  `Decimal` ra **chuỗi** JSON. Cột `numeric` phải đổi kiểu ở đúng biên này;
- **trường tuỳ chọn vắng khoá, không `null`** (W2, K02): `WireModel` tự bỏ `None`, nên mọi
  trường tuỳ chọn khai `| None = None` chứ không `KeepNull`;
- **mm là số nguyên** (B3-01 `Mm`): `int`, không `float` — `12.5` trên dây là dấu hiệu ai đó
  nhân tỉ lệ mà quên làm tròn.

Mô hình miền B3-01 đã dump ra đúng hình dạng dây (`by_alias`, `exclude_none`), nên
`layer_out`/`dimensions_out` chỉ `model_validate` lại: một lượt kiểm thật sự chứ không phải
phép chép trường bằng tay, và một trường thêm vào miền mà quên khai ở đây sẽ đỏ ngay.

Module này nằm trong nhóm được nhập `fastapi` (B3-02 [2]: trừ `router`, `graph`, `wire`,
`cli`) nhưng thực tế không cần — nó chỉ nhập `apps.api.core.wire`.
"""

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, JsonValue

from apps.api.core.wire import WireDatetime, WireModel
from packages.domain.spatial import Axis, Dimension, SpatialLayer, clear_area_m2
from packages.domain.spatial.model import FurnitureVariant, RoomFinish
from packages.domain.suggestions import Suggestion, SuggestionsMeta

Millimetres = int
"""Mọi toạ độ và kích thước hình học là **số nguyên** milimét (`S:domain/spatial/types.ts`)."""

SquareMetres = Annotated[float, Field(ge=0)]
"""Diện tích trên dây là `z.number().nonnegative()`; `Decimal` của DB đổi sang `float` ở biên."""


class _ReviewedOut(WireModel):
    """Ba trường duyệt mà mọi thực thể mang (`reviewMetadataShape`, HOP-DONG-MOI §4.1).

    Không kiểm A5 (`source='ai'` + `reviewed=True`) ở đây: luật ấy do B3-01 giữ lúc
    `model_validate` tài liệu, và lặp lại phép kiểm ở lớp dây sẽ thành hai nguồn luật.
    """

    confidence: Annotated[float, Field(ge=0, le=1)]
    source: Literal["ai", "human"]
    reviewed: bool
    # Chỉ có khi người đã sửa mục AI (FIX-P0-04): miền bỏ khoá khi `False`, dây vắng khoá theo W2.
    edited: bool | None = None


class PointOut(WireModel):
    """Một điểm trên mặt bằng, mm nguyên."""

    x: Millimetres
    y: Millimetres


class SegmentOut(WireModel):
    """Đoạn thẳng hai đầu; `segmentSchema` của FE chặn đoạn dài 0 mm."""

    start: PointOut
    end: PointOut


class BoundingBoxOut(WireModel):
    """Hộp bao của một món đồ đạc."""

    min: PointOut
    max: PointOut


class WallPaintOut(WireModel):
    """Sơn hai mặt tường (`MaterialRef`); miền đã kiểm mẫu, dây chỉ chép lại."""

    interior: str | None = None
    exterior: str | None = None


class FurnitureModelOut(WireModel):
    """Mô hình catalogue của món đồ: `{id, src}`."""

    id: str
    src: str


class FurnitureOriginOut(WireModel):
    """Đề xuất M2 nguồn của một món đã nhận nuôi (`origin`, HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.4)."""

    suggestion_id: str
    rule: str
    rule_version: Annotated[int, Field(ge=1)]


class WallOut(_ReviewedOut):
    """`WallSchema` — một đoạn tường đo theo đường tim."""

    id: str
    level_id: str
    centreline: SegmentOut
    thickness_mm: Millimetres
    detected_thickness_mm: Millimetres | None = None
    """Bề dày máy đo lúc dựng (U48), không đổi khi người sửa `thicknessMm`; tường người vẽ vắng."""
    height_mm: Millimetres
    kind: Literal["loadBearing", "partition", "envelope"]
    opening_ids: list[str]
    paint: WallPaintOut | None = None
    curve_offset_mm: Millimetres = Field(default=0, exclude_if=lambda value: value == 0)
    """Tường cong: sagitta có dấu theo quy ước `curveOffset` của Pascal (pháp tuyến `(-dy, dx)/L`); 0 là thẳng.

    Tường thẳng vắng khoá: `WallSchema` của FE là `.strict()`, bản FE chưa biết khoá này vẫn đọc được tầng không cong.
    """


class OpeningOut(_ReviewedOut):
    """`OpeningSchema` — cửa đi hay cửa sổ khoét trong một tường."""

    id: str
    wall_id: str
    kind: Literal["door", "window"]
    offset_mm: Millimetres
    width_mm: Millimetres
    height_mm: Millimetres
    sill_height_mm: Millimetres
    swing: Literal["left", "right", "double", "sliding", "fixed"]
    door_style: Literal["pocket", "folding"] | None = None
    reversed: bool | None = None


class RoomOut(_ReviewedOut):
    """`RoomSchema` — phòng khép kín; `areaM2` do server tính từ `outline` (W18)."""

    id: str
    level_id: str
    name: str
    usage: Literal[
        "livingRoom", "bedroom", "kitchen", "bathroom", "corridor", "stairwell", "utility", "loggia", "other"
    ]
    outline: Annotated[list[PointOut], Field(min_length=3)]
    area_m2: SquareMetres
    clear_area_m2: SquareMetres | None = None
    """Thông thủy (BENCH3D-09): đường bao lùi nửa bề dày tường; vắng khi không tính được (FE dùng `areaM2`)."""
    wall_ids: list[str]
    finish: RoomFinish | None = None
    floor_material: str | None = None
    dressing_off: list[str] | None = None
    ceiling_material: str | None = None


class FurnitureOut(_ReviewedOut):
    """`FurnitureSchema` — `roomId` **vắng khoá** khi món đồ chưa gắn phòng (C17)."""

    id: str
    level_id: str
    room_id: str | None = None
    kind: Literal[
        "table",
        "chair",
        "bed",
        "wardrobe",
        "kitchenCabinet",
        "sanitaryFixture",
        "stair",
        "other",
        "washingMachine",
        "shower",
    ]
    centre: PointOut
    bounding_box: BoundingBoxOut
    rotation_deg: Annotated[float, Field(ge=0, lt=360)]
    variant: FurnitureVariant | None = None
    height_mm: Millimetres | None = None
    lift_mm: Millimetres | None = None
    model: FurnitureModelOut | None = None
    slots: dict[str, str] | None = None
    # Gợi ý của máy chưa nhận (B3); miền bỏ khoá khi `False`, dây vắng khoá theo W2.
    suggested: bool | None = None
    origin: FurnitureOriginOut | None = None
    """Món nhận nuôi từ đề xuất M2; vắng với đồ thường."""


class RoofOut(_ReviewedOut):
    """`RoofSchema` — mái Pascal; `nodes` là blob FE tự đọc, BE trả nguyên (roof-contract.md)."""

    id: str
    level_id: str
    nodes: list[dict[str, JsonValue]]


class LevelOut(_ReviewedOut):
    """`LevelSchema` — một tầng; `id` là `Floor.id` của #12/#33, không phải một mã thứ hai.

    Tầng chưa hiệu chỉnh vắng `scaleMillimetresPerPixel`; tỉ lệ "tạm" vẫn gửi nhưng N16 kèm
    `scaleStatus: 'unresolved'` (HOP-DONG-MOI §4.2).
    """

    id: str
    name: str
    order: int
    elevation_mm: Millimetres
    height_mm: Millimetres
    area_m2: SquareMetres | None = None
    scale_millimetres_per_pixel: Annotated[float, Field(gt=0)] | None = None


class BuildingOut(_ReviewedOut):
    """`BuildingSchema` — `datumElevationMm` **có dấu** (tầng hầm nằm dưới mốc `+0.000`)."""

    name: str
    address: str | None = None
    datum_elevation_mm: Millimetres
    gross_floor_area_m2: SquareMetres | None = None


class AxisOut(_ReviewedOut):
    """`AxisSchema` — trục định vị đã lưu qua #35 (G4b-17)."""

    id: str
    level_id: str
    label: str
    direction: Literal["horizontal", "vertical"]
    line: SegmentOut


class DimensionOut(_ReviewedOut):
    """`DimensionSchema` — chuỗi kích thước; `referenceIds` **được rỗng**."""

    id: str
    level_id: str
    kind: Literal["linear", "chain", "radial", "angular", "elevation"]
    reference_ids: list[str]
    line: SegmentOut
    value_mm: Millimetres
    override_value_mm: Millimetres | None = None


class NoteOut(_ReviewedOut):
    """`NoteSchema` — ghi chú gắn vào một thực thể; v1 `notes` luôn rỗng ở N15."""

    id: str
    entity_id: str
    body: str
    created_at: WireDatetime
    author_id: str


class SpatialLayerOut(WireModel):
    """`SpatialLayerSchema` — danh sách thực thể của **một** tầng; `roofs` vắng khoá khi tầng không có mái."""

    walls: list[WallOut]
    openings: list[OpeningOut]
    rooms: list[RoomOut]
    furniture: list[FurnitureOut]
    roofs: list[RoofOut] | None = None


class SpatialGraphOut(WireModel):
    """`SpatialGraphSchema` — cả công trình; v1 `axes: []`, `notes: []`."""

    building: BuildingOut
    levels: list[LevelOut]
    walls: list[WallOut]
    openings: list[OpeningOut]
    furniture: list[FurnitureOut]
    rooms: list[RoomOut]
    axes: list[AxisOut]
    dimensions: list[DimensionOut]
    notes: list[NoteOut]
    roofs: list[RoofOut] | None = None
    """Vắng khoá khi cả công trình không có mái (đồ thị cũ giữ đúng hình dạng)."""


ReadinessState = Literal["ready", "needsReview", "notEvaluated"]
ReadinessReason = Literal[
    "scaleUnresolved",
    "criticalIssues",
    "fallbackWallModel",
    "fallbackObjectModel",
    "fallbackTextModel",
    "geometrySuspect",
]
ScaleBasis = Literal["chain", "paired", "note", "door", "wallThickness", "default", "human"]
"""Tỉ lệ đến từ đâu (BENCH3D-09): sáu gốc của pipeline (`layer.json` `scaleBasis`) cộng `human` (người hiệu chỉnh)."""


class ReadinessOut(WireModel):
    """Tầng dùng được chưa (FIX-P0-10, thiết kế H5 §2.2): trạng thái kèm lý do, thứ tự cố định."""

    state: ReadinessState
    reasons: tuple[ReadinessReason, ...]
    checks: tuple[str, ...] | None = None
    """Mã kiểm hình học critical của lượt mới nhất (BENCH3D-09), chỉ có khi có `geometrySuspect`; FE gặp mã lạ
    thì hiện câu chung, nên không khai `Literal`."""


SuggestionState = Literal["active", "disabled", "overridden", "adopted"]
SuggestionKind = Literal[
    "skirting",
    "wallTile",
    "cornice",
    "casing",
    "sash",
    "railing",
    "cabinetRun",
    "curtain",
    "rug",
    "ceilingLight",
    "item",
    "modelPick",
]


class SuggestionHostRefOut(WireModel):
    """`hostRef`: id W4 của chủ, hay id đề xuất chủ (P2 bám giường của P1); ít nhất một khoá."""

    wall: str | None = None
    room: str | None = None
    opening: str | None = None
    furniture: str | None = None
    suggestion: str | None = None


class SuggestionStaleOut(WireModel):
    """`stale`: BE đặt khi luật thôi sinh bản ghi; tách khỏi `state` của người dùng."""

    since: Annotated[int, Field(ge=0)]
    reason_vi: str


class SuggestionOut(WireModel):
    """Một đề xuất đã lưu (§2.2). `params` theo lược đồ của `kind`, mm nguyên; `override` chỉ vô hướng."""

    id: str
    rule: str
    rule_version: Annotated[int, Field(ge=1)]
    kind: SuggestionKind
    host_ref: SuggestionHostRefOut
    params: dict[str, JsonValue]
    state: SuggestionState
    override: dict[str, str | bool | int | float] | None = None
    created_revision: Annotated[int, Field(ge=0)]
    stale: SuggestionStaleOut | None = None
    adopted_as: str | None = None


class SuggestionsMetaOut(WireModel):
    """`suggestionsMeta`: bộ luật đã sinh danh sách và revision lớp làm nền."""

    ruleset: str
    ruleset_sha: str
    basis_revision: Annotated[int, Field(ge=0)]


class FloorLayerDocumentOut(WireModel):
    """N16 — lớp của một tầng; `scaleStatus` vắng khi tỉ lệ do người đặt hay chưa có (W24).

    `readiness` luôn có (FIX-P0-10): "lượt chạy xong" chưa có nghĩa "tầng dùng được".
    """

    revision: Annotated[int, Field(ge=0)]
    level: LevelOut
    scale_status: Literal["unresolved"] | None = None
    scale_basis: ScaleBasis | None = None
    """Gốc của tỉ lệ đang dùng (BENCH3D-09); vắng khi chưa có tỉ lệ hay lượt cũ không ghi gốc."""
    source_dpi: Annotated[float, Field(gt=0)] | None = None
    """DPI của trang pipeline đã dựng (PDF biết khổ giấy), `null` với ảnh raster; FE đổi `1:N` → mm/px (U01)."""
    scale_source: Literal["pipeline", "project_default"] | None = None
    """Nguồn tỉ lệ tạm, chỉ có khi có `scaleStatus` (UX-R1 K12): `project_default` = không đo được."""
    suggested_scale_mm_per_px: Annotated[float, Field(gt=0)] | None = None
    """Tỉ lệ đề xuất (mm/px) khi `scaleSource` là `project_default` và tầng có tường (UX-R2 K01): đưa bề dày
    tường trung vị về 150 mm. Chỉ để người dùng xác nhận, không áp vào tầng."""
    readiness: ReadinessOut
    layer: SpatialLayerOut
    axes: list[AxisOut]
    dimensions: list[DimensionOut]
    origin: PointOut | None = None
    """Gốc toạ độ màn Trục (mm, G4b-17); vắng khi chưa đặt."""
    suggestions: list[SuggestionOut] | None = None
    """Lớp đề xuất M2 (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2); vắng khi rỗng, cùng `suggestionsMeta`."""
    suggestions_meta: SuggestionsMetaOut | None = None
    suggestions_enabled: Literal[True] | None = None
    """Lớp đề xuất đang bật ở tầng này; vắng = tắt (như `edited`)."""


class FloorRevisionOut(WireModel):
    """`FloorRevisionSchema` — đủ để mở một lượt `PUT` có version lên đúng tầng ấy (#35)."""

    floor_id: str
    revision: Annotated[int, Field(ge=0)]
    suggestions: list[SuggestionOut] | None = None
    """Lớp đề xuất M2 của tầng như N16 (editor nạp tầng qua N15); vắng khi rỗng, cùng `suggestionsMeta`."""
    suggestions_meta: SuggestionsMetaOut | None = None
    suggestions_enabled: Literal[True] | None = None
    """Lớp đề xuất đang bật ở tầng này; vắng = tắt."""


class SpatialGraphDocumentOut(WireModel):
    """N15 — đồ thị cộng bảng `revision`; zod refine hai danh sách là **song ánh**."""

    graph: SpatialGraphOut
    floor_revisions: list[FloorRevisionOut]


def suggestion_fields(items: Sequence[Suggestion], meta: SuggestionsMeta | None, *, enabled: bool) -> dict[str, Any]:
    """Ba trường dây lớp đề xuất của N16 và kết quả #35: cùng luật "vắng khi rỗng" của tài liệu đã lưu."""
    fields: dict[str, Any] = {"suggestions_enabled": True if enabled else None}
    if items and meta is not None:
        fields["suggestions"] = [SuggestionOut.model_validate(_dump(item)) for item in items]
        fields["suggestions_meta"] = SuggestionsMetaOut.model_validate(_dump(meta))
    return fields


def layer_out(layer: SpatialLayer) -> SpatialLayerOut:
    """Lớp miền → lớp dây, đi qua `model_validate` để lệch trường nào là đỏ ngay chỗ này.

    `clearAreaM2` (BENCH3D-09) chỉ có ở phía đọc: miền không lưu, tính ở đây từ đường bao và tường hiện tại,
    nên N15, N16, phản hồi PUT #35 và phiên bản đều mang nó; thân PUT không được gửi nó (`Room` cấm khoá lạ).
    """
    data = layer.model_dump(mode="json", by_alias=True, exclude_none=True)
    for room, wire_room in zip(layer.rooms, data["rooms"], strict=True):
        if (clear := clear_area_m2(room.outline, layer.walls)) is not None:
            wire_room["clearAreaM2"] = float(clear)
    return SpatialLayerOut.model_validate(data)


def dimensions_out(dimensions: Sequence[Dimension]) -> list[DimensionOut]:
    """Kích thước miền → dây, cùng đường kiểm với `layer_out`."""
    return [DimensionOut.model_validate(_dump(dimension)) for dimension in dimensions]


def axes_out(axes: Sequence[Axis]) -> list[AxisOut]:
    """Trục miền → dây (G4b-17), cùng đường kiểm với `layer_out`."""
    return [AxisOut.model_validate(_dump(axis)) for axis in axes]


def _dump(model: BaseModel) -> dict[str, object]:
    """Dạng dây của một mô hình miền (camelCase, vắng khoá thay `null`)."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
