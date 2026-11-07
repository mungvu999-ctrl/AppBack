"""Mô hình đồ thị không gian: gương `src/domain/spatial/types.ts` và ràng buộc zod `src/api/schemas/spatial.ts`.

Mọi thứ BE dựng từ các lớp này phải giải mã được ở FE (W1, W3, W4). Xuất dây bằng
`model_dump(mode="json", by_alias=True, exclude_none=True)`: trường tuỳ chọn vắng
thì vắng khoá, không bao giờ `null` (W2, K02).

A5 (`source="ai"` kèm `reviewed=True`) **không** kiểm ở đây: B3-03 cần trả
`REVIEW_BY_AI_FORBIDDEN` thay cho `VALIDATION`, nên dùng `kinds.ai_reviewed_ids`.
"""

import json
import math
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic.alias_generators import to_camel

from packages.core.ids import SPATIAL_PREFIX, SpatialKind, is_spatial_id
from packages.core.instants import parse_wire
from packages.core.text import first_forbidden_char, nfc

MAX_SAFE_INTEGER: Final = 2**53 - 1
"""`Number.MAX_SAFE_INTEGER`: FE cộng số nguyên bằng số thực chính xác tới đây."""


class DomainModel(BaseModel):
    """Gốc của mọi mô hình miền: bất biến, cấm khoá lạ (W1), khoá dây camelCase.

    Không bật `strict` toàn cục: jsonb đọc ra `list`, còn strict từ chối `list` cho
    trường `tuple`. Kiểu chặt đặt theo từng trường (`Mm`, `StrictBool`…).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True)


def _spatial_id(kind: SpatialKind | None) -> AfterValidator:
    """Kiểm id W4 bằng `is_spatial_id` của lõi: đúng `kind`, hoặc loại bất kỳ khi `kind` là `None`."""
    kinds = tuple(SPATIAL_PREFIX) if kind is None else (kind,)

    def check(value: str) -> str:
        """Trả nguyên id khi đạt mẫu, không thì `ValueError` (pydantic gói thành lỗi của trường)."""
        if not any(is_spatial_id(candidate, value) for candidate in kinds):
            raise ValueError(f"id không đúng mẫu W4 của {kind or 'thực thể không gian'}")
        return value

    return AfterValidator(check)


def _human_text(value: str) -> str:
    """Chuỗi người nhập (BE-00 §6): lưu NFC, không rỗng, không ký tự điều khiển hay đảo chiều."""
    text = nfc(value)
    if not text:
        raise ValueError("chuỗi rỗng")
    if first_forbidden_char(text) is not None:
        raise ValueError("chuỗi chứa ký tự điều khiển hoặc ký tự đảo chiều")
    return text


def _wire_instant(value: str) -> str:
    """Ngày giờ dây W3 (`.sssZ`), kiểm bằng `parse_wire` nhưng giữ nguyên chuỗi."""
    parse_wire(value)
    return value


SafeInt = Annotated[StrictInt, Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]
Mm = SafeInt
"""Độ dài mm nguyên (W3, K20): `1.5`, `True`, `"12"` đều hỏng."""
PositiveMm = Annotated[Mm, Field(gt=0)]
NonNegativeMm = Annotated[Mm, Field(ge=0)]
SquareMetres = Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]
"""`float`, không `Decimal`: `mode="json"` xuất `Decimal` thành chuỗi, `z.number()` hỏng cả lớp."""
Confidence = Annotated[StrictFloat, Field(ge=0, le=1, allow_inf_nan=False)]
Degrees = Annotated[StrictFloat, Field(ge=0, lt=360, allow_inf_nan=False)]
HumanText = Annotated[StrictStr, AfterValidator(_human_text)]
NonEmptyStr = Annotated[StrictStr, Field(min_length=1)]
WireInstant = Annotated[StrictStr, AfterValidator(_wire_instant)]
EntityIdStr = Annotated[StrictStr, _spatial_id(None)]
LevelIdStr = Annotated[StrictStr, _spatial_id("level")]
WallIdStr = Annotated[StrictStr, _spatial_id("wall")]
OpeningIdStr = Annotated[StrictStr, _spatial_id("opening")]
FurnitureIdStr = Annotated[StrictStr, _spatial_id("furniture")]
RoomIdStr = Annotated[StrictStr, _spatial_id("room")]
AxisIdStr = Annotated[StrictStr, _spatial_id("axis")]
DimensionIdStr = Annotated[StrictStr, _spatial_id("dimension")]
RoofIdStr = Annotated[StrictStr, _spatial_id("roof")]
MaterialRef = Annotated[StrictStr, Field(pattern=r"^(library|scene):[a-z0-9_-]{1,64}$")]
"""Vật liệu Pascal (FE3D-B J-v3): `library:<id>` trong thư viện hoặc `scene:<id>` của cảnh."""
SlotName = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")]

DressingId = Annotated[StrictStr, Field(pattern=r"^DRESS-[A-Za-z0-9_.:-]{1,160}$")]
"""Id món AppFront tự bày (`sceneDressingRules.ts`), vd `DRESS-rug-R-01`; BE không sinh, chỉ giữ."""

DRESSING_OFF_MAX: Final = 200
"""Trần số món tự bày đã xoá của một phòng (`RoomSchema.dressingOff` của FE)."""
RoomFinish = Literal["wood", "tile", "mosaic", "decking", "concrete"]
"""`RoomFinish` của FE `src/domain/spatial/types.ts`."""
FurnitureVariant = Literal[
    "bed",
    "nightstand",
    "tableLamp",
    "wardrobe",
    "rug",
    "plant",
    "shower",
    "wc",
    "basin",
    "mirror",
    "artwork",
    "shelves",
    "table",
    "chair",
    "baseRun",
    "upperRun",
    "hood",
    "hob",
    "sink",
    "fridge",
    "stool",
    "cabinet",
    "sofa",
    "sconce",
    "tv",
    "armchair",
    "sideTable",
    "floorLamp",
    "bench",
    "pendant",
    "vase",
    "lounger",
    "planter",
    "bamboo",
    "shrub",
    "straight",
    "curved",
    "spiral",
]
"""35 `FURNITURE_VARIANTS` của FE `src/domain/spatial/types.ts`, đúng thứ tự, cộng 3 kiểu cầu thang `StairType` của
Pascal (`straight`, `curved`, `spiral`) mà `toSpatial` ghi cho cầu thang (M1-01 H3.4)."""


def _straight(curve_offset_mm: int) -> bool:
    """Tường thẳng thì vắng khoá `curveOffsetMm`: tài liệu cũ dump ra đúng jsonb như trước."""
    return curve_offset_mm == 0


def _not_edited(edited: bool) -> bool:
    """`edited=False` thì bỏ khoá khi dump, như `roofs` rỗng."""
    return not edited


class Reviewed(DomainModel):
    """Trường duyệt mọi thực thể mang (`ReviewMetadata`); `reviewed=True` chỉ do người đặt (A5).

    `edited`: một người đã thay đổi thực thể AI này (H1). Không phải duyệt, không đổi
    `source`; BE tự suy ra lúc người ghi (`provenance.mark_edited`), không tin client.
    `False` thì vắng khoá khi dump: tài liệu cũ ra đúng jsonb như trước, FE chưa biết
    trường này vẫn giải được mọi mục chưa sửa.
    """

    confidence: Confidence
    source: Literal["ai", "human"]
    reviewed: StrictBool
    edited: StrictBool = Field(default=False, exclude_if=_not_edited)


class Point(DomainModel):
    """Điểm trên mặt bằng, mm."""

    x: Mm
    y: Mm


class Segment(DomainModel):
    """Đoạn thẳng thật sự có chiều dài: `start` trùng `end` là tường không tồn tại."""

    start: Point
    end: Point

    @model_validator(mode="after")
    def _has_length(self) -> Self:
        """Hai đầu phải khác nhau."""
        if self.start == self.end:
            raise ValueError("đoạn thẳng dài 0")
        return self


class BoundingBox(DomainModel):
    """Hộp bao đúng chiều: `max` không nhỏ hơn `min` trên cả hai trục."""

    min: Point
    max: Point

    @model_validator(mode="after")
    def _not_inverted(self) -> Self:
        """`max ≥ min` trên cả hai trục; hộp suy biến thành một điểm vẫn đạt."""
        if self.max.x < self.min.x or self.max.y < self.min.y:
            raise ValueError("hộp bao lộn ngược")
        return self


class Building(Reviewed):
    """Công trình; `datumElevationMm` có dấu (tầng hầm nằm dưới mốc)."""

    name: HumanText
    address: HumanText | None = None
    datum_elevation_mm: Mm
    gross_floor_area_m2: SquareMetres | None = None


class Level(Reviewed):
    """Một tầng; tầng chưa hiệu chỉnh thì vắng `scaleMillimetresPerPixel` (W24)."""

    id: LevelIdStr
    name: HumanText
    order: SafeInt
    elevation_mm: Mm
    height_mm: PositiveMm
    area_m2: SquareMetres | None = None
    scale_millimetres_per_pixel: Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)] | None = None


class WallPaint(DomainModel):
    """Sơn hai mặt tường; mặt vắng khoá là màu mặc định."""

    interior: MaterialRef | None = None
    exterior: MaterialRef | None = None


class FurnitureModel(DomainModel):
    """Mô hình catalogue chọn cho món đồ (Pascal "replace all"); `src` là đường dẫn `/items/<id>/model.glb`."""

    id: Annotated[NonEmptyStr, Field(max_length=120)]
    src: Annotated[StrictStr, Field(pattern=r"^/items/[a-z0-9_-]+/model\.glb$")]


class Wall(Reviewed):
    """Một đoạn tường trên một tầng, đo theo đường tim."""

    id: WallIdStr
    level_id: LevelIdStr
    centreline: Segment
    thickness_mm: PositiveMm
    height_mm: PositiveMm
    kind: Literal["loadBearing", "partition", "envelope"]
    opening_ids: tuple[OpeningIdStr, ...]
    paint: WallPaint | None = None
    detected_thickness_mm: PositiveMm | None = None
    """Bề dày máy đo (chưa bắt về bảng chuẩn) lúc dựng (U48); người sửa `thicknessMm` không đổi nó, B3-03 giữ."""
    curve_offset_mm: Mm = Field(default=0, exclude_if=_straight)
    """Tường cong (cung ≤ 180°): sagitta có dấu, điểm giữa cung = trung điểm tim - giá trị · n với `n = (-dy, dx) / L`
    (quy ước `curveOffset` của Pascal); 0 là tường thẳng. Kiểm |giá trị| ≤ L/2 ở `_curve_fits`."""

    @model_validator(mode="after")
    def _curve_fits(self) -> Self:
        """Cung không quá nửa vòng: |sagitta| ≤ nửa dây (Pascal kẹp phần thừa, lưu thừa là sai lặng lẽ)."""
        start, end = self.centreline.start, self.centreline.end
        if 2 * abs(self.curve_offset_mm) > math.hypot(end.x - start.x, end.y - start.y) + 1:
            raise ValueError("curveOffsetMm lớn hơn nửa chiều dài tường")
        return self


class Opening(Reviewed):
    """Ô mở (cửa đi hoặc cửa sổ) khoét trong một tường; `offsetMm` tính từ đầu đường tim."""

    id: OpeningIdStr
    wall_id: WallIdStr
    kind: Literal["door", "window"]
    offset_mm: NonNegativeMm
    width_mm: PositiveMm
    height_mm: PositiveMm
    sill_height_mm: NonNegativeMm
    swing: Literal["left", "right", "double", "sliding", "fixed"]
    door_style: Literal["pocket", "folding"] | None = None
    """Cửa lùa âm tường / cửa xếp (H4); chỉ cửa đi `swing="sliding"`. Vắng = lùa thường."""
    reversed: StrictBool = Field(default=False, exclude_if=_not_edited)
    """"Đổi hướng mở" (H4): cánh bản lề mở về mặt kia so với luật FE, cửa lùa lùa sang phải; `False` vắng khoá."""

    @model_validator(mode="after")
    def _door_style_on_sliding_door(self) -> Self:
        """`doorStyle` chỉ tinh chỉnh cửa đi lùa; trên cửa sổ hay cửa bản lề là dữ liệu hỏng."""
        if self.door_style is not None and (self.kind != "door" or self.swing != "sliding"):
            raise ValueError("doorStyle chỉ dùng cho cửa đi swing=sliding")
        return self


class Furniture(Reviewed):
    """Đồ đạc đặt trên mặt bằng; `roomId` vắng khi chưa gắn phòng.

    `suggested`: gợi ý của máy người duyệt chưa nhận (B3) — FE vẽ nét đứt, không dựng 3D,
    không thành nhãn huấn luyện. Nhận = duyệt và bỏ cờ; `False` vắng khoá khi dump như `edited`.
    """

    id: FurnitureIdStr
    level_id: LevelIdStr
    room_id: RoomIdStr | None = None
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
    centre: Point
    bounding_box: BoundingBox
    rotation_deg: Degrees
    variant: FurnitureVariant | None = None
    height_mm: PositiveMm | None = None
    lift_mm: NonNegativeMm | None = None
    model: FurnitureModel | None = None
    slots: Annotated[dict[SlotName, MaterialRef], Field(max_length=8)] | None = None
    suggested: StrictBool = Field(default=False, exclude_if=_not_edited)


class Room(Reviewed):
    """Phòng khép kín; điểm đầu không lặp ở cuối nên ít nhất ba điểm."""

    id: RoomIdStr
    level_id: LevelIdStr
    name: HumanText
    usage: Literal[
        "livingRoom", "bedroom", "kitchen", "bathroom", "corridor", "stairwell", "utility", "loggia", "other"
    ]
    outline: Annotated[tuple[Point, ...], Field(min_length=3)]
    area_m2: SquareMetres
    wall_ids: tuple[WallIdStr, ...]
    finish: RoomFinish | None = None
    floor_material: MaterialRef | None = None
    dressing_off: Annotated[
        tuple[DressingId, ...] | None,
        Field(max_length=DRESSING_OFF_MAX),
        AfterValidator(lambda ids: ids or None),
    ] = None
    """Món AppFront tự bày người dùng đã xoá trong 3D (H4): FE không bày lại; vắng = không có (`[]` cũng vắng)."""
    ceiling_material: MaterialRef | None = None
    """Vật liệu trần (H4); vắng = thạch cao mặc định của FE."""


class Axis(Reviewed):
    """Trục định vị của màn Trục; #35 ghi, N15/N16 trả lại (G4b-17)."""

    id: AxisIdStr
    level_id: LevelIdStr
    label: HumanText
    direction: Literal["horizontal", "vertical"]
    line: Segment


class Dimension(Reviewed):
    """Chuỗi kích thước; `referenceIds` được rỗng (kích thước người tự vẽ chưa gắn thực thể)."""

    id: DimensionIdStr
    level_id: LevelIdStr
    kind: Literal["linear", "chain", "radial", "angular", "elevation"]
    reference_ids: tuple[EntityIdStr, ...]
    line: Segment
    value_mm: Mm
    override_value_mm: Mm | None = None

    @model_validator(mode="after")
    def _positive_unless_elevation(self) -> Self:
        """`kind ≠ elevation` ⇒ `valueMm > 0`: cao độ được 0 hay âm (cốt nền, tầng hầm), chiều dài 0 là OCR hỏng."""
        if self.kind != "elevation" and self.value_mm <= 0:
            raise ValueError("valueMm phải > 0 khi kind khác elevation")
        return self


class Note(Reviewed):
    """Ghi chú gắn vào một thực thể; `id` ngoài bảng tiền tố nên là chuỗi tự do."""

    id: NonEmptyStr
    entity_id: EntityIdStr
    body: HumanText
    created_at: WireInstant
    author_id: NonEmptyStr


ROOF_NODES_MAX: Final = 33
"""Trần số node của một mái: node `roof` + tối đa 32 `roof-segment` (roof-contract.md)."""

ROOF_JSON_MAX_BYTES: Final = 64 * 1024
"""Trần JSON (UTF-8) của cả một mái trên dây (roof-contract.md)."""


class Roof(Reviewed):
    """Mái Pascal: `nodes[0]` là node `roof`, các node sau là `roof-segment` con của nó.

    BE không đọc bên trong `nodes` (hình học do FE giữ); chỉ kiểm số node, là object JSON,
    và cỡ JSON của cả mái. `JsonValue` giữ nguyên giá trị và thứ tự khoá khi khứ hồi.
    """

    id: RoofIdStr
    level_id: LevelIdStr
    nodes: Annotated[tuple[dict[str, JsonValue], ...], Field(min_length=1, max_length=ROOF_NODES_MAX)]

    @model_validator(mode="after")
    def _json_size(self) -> Self:
        """JSON của cả mái ≤ 64 KB; `NaN`/`Infinity` hỏng ở đây (jsonb không nhận) thay vì 500 lúc ghi."""
        raw = json.dumps(self.model_dump(mode="json", by_alias=True), ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > ROOF_JSON_MAX_BYTES:
            raise ValueError("JSON của mái vượt 64 KB")
        return self


def _no_roofs(roofs: tuple[Roof, ...]) -> bool:
    """Không có mái thì vắng khoá `roofs`: tài liệu cũ dump ra đúng jsonb như trước."""
    return not roofs


Roofs = Annotated[tuple[Roof, ...], Field(default=(), exclude_if=_no_roofs)]


class SpatialLayer(DomainModel):
    """Danh sách thực thể của một tầng, đúng `SpatialLayerSchema` (N16, #35); `roofs` tuỳ chọn."""

    walls: tuple[Wall, ...]
    openings: tuple[Opening, ...]
    rooms: tuple[Room, ...]
    furniture: tuple[Furniture, ...]
    roofs: Roofs = ()

    def entities(self) -> tuple[Wall | Opening | Room | Furniture | Roof, ...]:
        """Mọi thực thể theo thứ tự cố định `walls → openings → rooms → furniture → roofs` (toàn vẹn, diff)."""
        return (*self.walls, *self.openings, *self.rooms, *self.furniture, *self.roofs)


class SpatialGraph(DomainModel):
    """Đồ thị của cả công trình (N15); danh sách chỉ trỏ nhau qua id, không lồng."""

    building: Building
    levels: tuple[Level, ...]
    walls: tuple[Wall, ...]
    openings: tuple[Opening, ...]
    furniture: tuple[Furniture, ...]
    rooms: tuple[Room, ...]
    axes: tuple[Axis, ...]
    dimensions: tuple[Dimension, ...]
    notes: tuple[Note, ...]
    roofs: Roofs = ()
