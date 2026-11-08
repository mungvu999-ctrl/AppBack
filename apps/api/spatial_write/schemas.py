"""Model dây của #35 (B3-03 [2], [6]): thân `FloorLayerWriteSchema`, kết quả `FloorLayerWriteResultSchema`.

`layer` là **mô hình miền của B3-01** nguyên vẹn: luật trường (mm nguyên W3, id W4, chuỗi NFC,
`extra="forbid"`) đã ở đó, viết lại ở đây là hai nguồn cho một luật và chỉ tạo cơ hội lệch.
A5 (`source="ai"` kèm `reviewed=True`) **không** kiểm ở tầng này — `write_layer` phải trả
`REVIEW_BY_AI_FORBIDDEN` chứ không phải `VALIDATION` (W5), nên mô hình miền cố ý để nó qua.

Tỉ lệ **vào** là số JSON (bool, chuỗi, `Infinity` bị từ chối), làm tròn `ROUND_HALF_UP` 6 chữ
số rồi mới kiểm dải: `0`, `0.0000004` và số âm đều rơi vào `gt=0` → 422 kèm `field`, đúng luật
"bằng 0 sau làm tròn → 422" mà không cần một câu kiểm thứ hai.

Tên lớp theo zod của FE (`src/api/schemas/spatialLayer.ts:104-147`) để hai đầu dây đọc cùng
một tên; khác quy ước `…In`/`…Out` của các module ghi khác là có ý.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BeforeValidator, Field, JsonValue, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from apps.api.core.wire import WireModel, WireRequest
from apps.api.spatial_read.wire import SpatialLayerOut, SuggestionOut, SuggestionsMetaOut
from packages.domain.spatial import Axis, Dimension, Point, SpatialLayer
from packages.domain.suggestions import SUGGESTIONS_CAP
from packages.domain.suggestions.model import OVERRIDE_MAX, SUGGESTION_ID_PATTERN
from packages.domain.suggestions.ruleset import ParamKey

SCALE_PLACES: Final = 6
"""Số chữ số thập phân của `floor_documents.scale_mm_per_px` (`Numeric(12,6)`, B3-02)."""

SCALE_MAX: Final = 999_999
"""Trần tỉ lệ ([6]): `Numeric(12,6)` chỉ còn 6 chữ số phần nguyên."""

_EXPONENT: Final = Decimal(1).scaleb(-SCALE_PLACES)


def _scale(value: Any) -> Any:
    """Số JSON trong dải `(0, SCALE_MAX]` → `Decimal` làm tròn 6 chữ số; mọi thứ khác → `ValueError`.

    Phép kiểm dải đứng **trước** mọi phép đổi kiểu, và đó là điểm quan trọng: `BeforeValidator`
    chỉ đổi `ValueError`/`AssertionError` thành 422, nên bất kỳ `ArithmeticError` nào thoát ra
    đều thành 500 `INTERNAL`. Hai đường đã biết là `math.isfinite(10**310)` → `OverflowError`
    và `Decimal(str(1e22)).quantize(1E-6)` → `InvalidOperation`. So sánh `0 < value <= SCALE_MAX`
    không đổi kiểu gì cả (`int` so với `int` vẫn là `int`), nên nó đóng **mọi** đường như vậy
    một lần, kể cả đường chưa biết — thay vì thêm một `except` cho từng cái.
    `NaN` cũng rơi vào đây: mọi so sánh với `NaN` đều `False`.

    Luật "0 sau khi làm tròn → 422" giữ nguyên: `0.0000004` qua được guard rồi `quantize` về 0,
    và `Field(gt=0)` của `LayerScaleMmPerPx` từ chối nó.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("tỉ lệ phải là số")
    if not 0 < value <= SCALE_MAX:
        raise ValueError("tỉ lệ ngoài dải")
    return Decimal(str(value)).quantize(_EXPONENT, rounding=ROUND_HALF_UP)


type LayerScaleMmPerPx = Annotated[Decimal, BeforeValidator(_scale), Field(gt=0, le=SCALE_MAX)]
"""Tỉ lệ của #35. Tên có tiền tố `Layer` vì `project_settings.schemas` đã có `ScaleMmPerPx`
(dải khác: `0.01..1000`): hai bí danh trùng tên làm OpenAPI đổi **cả hai** component thành
`apps__api__…__ScaleMmPerPx` và đổi luôn schema của prompt khác."""


class SuggestionStateRowSchema(WireRequest):
    """Một dòng `suggestionState` (M2 §4): trạng thái người dùng của **một** đề xuất.

    `params`, `stale`, `createdRevision` là trường máy chủ sở hữu: FE gửi lại nguyên bản ghi thì nhận và bỏ qua (W18),
    không 422. Id lạ hay khoá `override` ngoài `overridable` của luật do `write_layer` kiểm (cần tài liệu đã lưu).
    """

    id: Annotated[StrictStr, Field(pattern=SUGGESTION_ID_PATTERN)]
    state: Literal["active", "disabled", "overridden", "adopted"]
    override: (
        Annotated[
            dict[ParamKey, StrictStr | StrictBool | StrictInt | StrictFloat],
            Field(min_length=1, max_length=OVERRIDE_MAX),
        ]
        | None
    ) = None
    params: JsonValue = None
    stale: JsonValue = None
    created_revision: JsonValue = None

    def as_state(self) -> dict[str, object]:
        """Phần người dùng sở hữu, đúng dạng `apply_state` nhận (`override` vắng khi không có)."""
        row: dict[str, object] = {"id": self.id, "state": self.state}
        if self.override is not None:
            row["override"] = self.override
        return row


class FloorLayerWriteBodySchema(WireRequest):
    """`body` của #35: lớp, tỉ lệ, kích thước, trục, gốc toạ độ, lớp đề xuất — ít nhất một khoá (FE `refine` ≥ 1).

    `axes` thay nguyên danh sách trục như `dimensions`; `origin` là gốc toạ độ màn Trục (mm, G4b-17).
    `suggestionState` là trạng thái **thưa** cả tầng của lớp đề xuất M2, `suggestionsEnabled` bật/tắt lớp ấy.
    `suggestions` (danh sách đề xuất) do máy chủ sinh: client gửi lên thì nhận và bỏ qua (W18), không tính là khoá.
    """

    layer: SpatialLayer | None = None
    scale_millimetres_per_pixel: LayerScaleMmPerPx | None = None
    dimensions: list[Dimension] | None = None
    axes: list[Axis] | None = None
    origin: Point | None = None
    suggestion_state: Annotated[list[SuggestionStateRowSchema], Field(max_length=SUGGESTIONS_CAP)] | None = None
    suggestions_enabled: StrictBool | None = None
    suggestions: JsonValue = None

    @model_validator(mode="after")
    def _at_least_one_key(self) -> Self:
        """`body: {}` là lượt ghi không nói gì → 422 `field:"body"`, không phải 200 im lặng."""
        keys = (
            self.layer,
            self.scale_millimetres_per_pixel,
            self.dimensions,
            self.axes,
            self.origin,
            self.suggestion_state,
            self.suggestions_enabled,
        )
        if all(key is None for key in keys):
            raise ValueError(
                "body phải có layer, dimensions, axes, origin, scaleMillimetresPerPixel, suggestionState "
                "hoặc suggestionsEnabled"
            )
        return self


class FloorLayerWriteSchema(WireRequest):
    """Thân đầy đủ `{baseVersion, body}` (W20); thiếu `baseVersion` bị guard 428 chặn trước Pydantic."""

    base_version: Annotated[int, Field(strict=True, ge=0)]
    body: FloorLayerWriteBodySchema


class FloorLayerWriteResultSchema(WireModel):
    """200 của #35: bản ghi mới, lớp đã lưu và lớp đề xuất M2 cùng revision — nguồn thay store của F-04b.

    Ba trường đề xuất vắng khi rỗng/tắt, đúng như N16 (`suggestion_fields`).
    """

    revision: Annotated[int, Field(ge=0)]
    layer: SpatialLayerOut
    suggestions: list[SuggestionOut] | None = None
    suggestions_meta: SuggestionsMetaOut | None = None
    suggestions_enabled: Literal[True] | None = None
