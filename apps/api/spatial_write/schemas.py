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
from typing import Annotated, Any, Final, Self

from pydantic import BeforeValidator, Field, ValidationInfo, field_validator, model_validator

from apps.api.core.wire import WireModel, WireRequest
from apps.api.spatial_read.wire import SpatialLayerOut
from apps.api.spatial_write.writer import ScaleEvidence
from packages.domain.spatial import Axis, Dimension, Point, SpatialLayer

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


class FloorLayerWriteBodySchema(WireRequest):
    """`body` của #35: lớp, tỉ lệ, kích thước, trục, gốc toạ độ — ít nhất một khoá (FE `refine` ≥ 1 khoá).

    `axes` thay nguyên danh sách trục như `dimensions`; `origin` là gốc toạ độ màn Trục (mm, G4b-17).
    `scaleEvidence` (ML-ACC-R10 K4): căn cứ của tỉ lệ gửi kèm, vắng = `measured`; không có tỉ lệ thì 422.
    """

    layer: SpatialLayer | None = None
    scale_millimetres_per_pixel: LayerScaleMmPerPx | None = None
    dimensions: list[Dimension] | None = None
    axes: list[Axis] | None = None
    origin: Point | None = None
    scale_evidence: ScaleEvidence | None = None

    @field_validator("scale_evidence")
    @classmethod
    def _evidence_needs_scale(cls, value: ScaleEvidence | None, info: ValidationInfo) -> ScaleEvidence | None:
        """`scaleEvidence` nói về tỉ lệ gửi kèm: thân không có `scaleMillimetresPerPixel` → 422 `body.scaleEvidence`."""
        if info.data.get("scale_millimetres_per_pixel") is None:
            raise ValueError("scaleEvidence cần scaleMillimetresPerPixel")
        return value

    @model_validator(mode="after")
    def _at_least_one_key(self) -> Self:
        """`body: {}` là lượt ghi không nói gì → 422 `field:"body"`, không phải 200 im lặng."""
        keys = (self.layer, self.scale_millimetres_per_pixel, self.dimensions, self.axes, self.origin)
        if all(key is None for key in keys):
            raise ValueError("body phải có layer, dimensions, axes, origin hoặc scaleMillimetresPerPixel")
        return self


class FloorLayerWriteSchema(WireRequest):
    """Thân đầy đủ `{baseVersion, body}` (W20); thiếu `baseVersion` bị guard 428 chặn trước Pydantic."""

    base_version: Annotated[int, Field(strict=True, ge=0)]
    body: FloorLayerWriteBodySchema


class FloorLayerWriteResultSchema(WireModel):
    """200 của #35: bản ghi mới và lớp đã lưu — nguồn thay store của F-04b."""

    revision: Annotated[int, Field(ge=0)]
    layer: SpatialLayerOut
