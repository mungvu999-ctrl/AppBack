"""Model **request** của module tầng (B2-03 [2]).

`clean_name`, `clean_mm` là hai hàm thuần dùng lại ở `view_parts.py` (hook `project.create_floors`,
R-07): kiểm lại đúng luật tên/mm mà không tin bên gọi (kể cả B2-01) đã kiểm.
"""

import math
from typing import Annotated, Any, Final

from pydantic import BeforeValidator, Field, JsonValue, StrictBool, field_validator

from apps.api.core.wire import WireRequest
from packages.core.ids import is_spatial_id
from packages.core.text import clean_text

NAME_MAX: Final = 120
ORDER_MIN: Final = 0
ORDER_MAX: Final = 999
ELEVATION_MIN: Final = -30_000
ELEVATION_MAX: Final = 300_000
HEIGHT_MIN: Final = 2_000
HEIGHT_MAX: Final = 10_000
_ROUND_TOLERANCE: Final = 0.01


def clean_name(value: Any) -> Any:
    """Trim + NFC rồi 1-120 ký tự, cấm Cc và ký tự đảo chiều; không phải chuỗi thì trả nguyên."""
    if not isinstance(value, str):
        return value
    text = clean_text(value)
    if not 1 <= len(text) <= NAME_MAX:
        raise ValueError(f"name phải 1-{NAME_MAX} ký tự sau chuẩn hoá")
    return text


def clean_mm(value: Any, *, field: str, lo: int, hi: int) -> int:
    """Số JSON hữu hạn, không `bool`/chuỗi; lệch số nguyên gần nhất ≤ 0,01 thì làm tròn, lệch hơn → lỗi."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} phải là số")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} phải là số hữu hạn")
    rounded = round(value)
    if abs(value - rounded) > _ROUND_TOLERANCE:
        raise ValueError(f"{field} lệch quá {_ROUND_TOLERANCE} so với số nguyên gần nhất")
    if not lo <= rounded <= hi:
        raise ValueError(f"{field} phải trong khoảng {lo}-{hi}")
    return int(rounded)


def clean_level_id(value: Any) -> Any:
    """`id` phải đúng `is_spatial_id("level", …)`; không phải chuỗi thì trả nguyên cho Pydantic."""
    if not isinstance(value, str):
        return value
    if not is_spatial_id("level", value):
        raise ValueError("id phải đúng dạng L-<...>")
    return value


def _reject_null(value: Any) -> Any:
    """`null` tường minh cho một trường tuỳ chọn của #34 → 422; vắng khoá mới là "không đổi"."""
    if value is None:
        raise ValueError("trường tuỳ chọn không nhận null; bỏ hẳn khoá nếu không muốn đổi")
    return value


type FloorName = Annotated[str, BeforeValidator(clean_name)]
type FloorIdField = Annotated[str, BeforeValidator(clean_level_id)]
type OrderField = Annotated[int, BeforeValidator(lambda v: clean_mm(v, field="order", lo=ORDER_MIN, hi=ORDER_MAX))]
type ElevationField = Annotated[
    int, BeforeValidator(lambda v: clean_mm(v, field="elevationMm", lo=ELEVATION_MIN, hi=ELEVATION_MAX))
]
type HeightField = Annotated[
    int, BeforeValidator(lambda v: clean_mm(v, field="heightMm", lo=HEIGHT_MIN, hi=HEIGHT_MAX))
]


class FloorCreateIn(WireRequest):
    """Thân #10; `drawings`, `areaM2` nhận rồi bỏ (W18)."""

    id: FloorIdField
    name: FloorName
    order: OrderField
    elevation_mm: ElevationField
    height_mm: HeightField
    drawings: JsonValue = None
    area_m2: JsonValue = None


class FloorPatchIn(WireRequest):
    """Thân #34; mọi khoá tuỳ chọn, `null` tường minh → 422 (vắng khoá mới là "không đổi")."""

    id: FloorIdField | None = None
    name: FloorName | None = None
    order: OrderField | None = None
    elevation_mm: ElevationField | None = None
    height_mm: HeightField | None = None
    # FE3D-B3: alias tay vì `to_camel("hidden_in_3d")` ra `hiddenIn3D` (chữ sau số bị viết hoa).
    hidden_in_3d: StrictBool | None = Field(default=None, alias="hiddenIn3d")
    # HS-03: bản vẽ của tầng này là mặt cắt. Chỉ ghi; trạng thái đọc lại ở `GET …/house-proposal` (`isSection`).
    is_section: StrictBool | None = None
    drawings: JsonValue = None
    area_m2: JsonValue = None

    @field_validator(
        "id",
        "name",
        "order",
        "elevation_mm",
        "height_mm",
        "hidden_in_3d",
        "is_section",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def _no_explicit_null(cls, value: Any) -> Any:
        """`null` tường minh cho một khoá bất kỳ ở trên → 422 kèm đúng `field`."""
        return _reject_null(value)


class FloorReorderIn(WireRequest):
    """Thân #13 (`{floorIds}`) — khai để `openapi.json` có `requestBody` (API-04, R-11).

    Luật thật (1-`FLOORS_MAX` phần tử, không trùng, khoá lạ → 422 `field:"floorIds"`) chỉ sống
    ở `resolvers._parsed_floor_ids` (một nguồn, R-07): resolver đọc thân thô **trước** khi
    FastAPI ràng buộc `body` này, nên lỗi resolver thắng mọi lỗi **sau** bước giải JSON (thân
    JSON hỏng thì FastAPI giải trước mọi dependency → 400 `MALFORMED_JSON`, `core/routing.py`),
    và `floor_ids` ở đây luôn đã hợp lệ khi tới tay handler — lớp này chỉ còn nhiệm vụ tài liệu hoá
    hợp đồng, không kiểm gì thêm
    ngoài kiểu (`list[str]`, `extra="forbid"` của `WireRequest`).
    """

    floor_ids: list[str]
