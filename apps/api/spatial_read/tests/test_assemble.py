"""`apps.api.spatial_read.assemble` — `scaleStatus`, `level_reviewed`, `Level` (B3-02 [6], [8]).

Hàm thuần, không DB: đây là chỗ hai route phải đồng ý với nhau, nên mỗi luật có một test
tham số hoá thay vì kiểm gián tiếp qua HTTP.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from apps.api.projects.wire import FloorOut
from apps.api.spatial_read.assemble import (
    effective_scale_source,
    level_out,
    level_reviewed,
    provisional_scale_source,
    scale_status,
    suggested_scale_mm_per_px,
)
from apps.api.spatial_read.documents import FloorDocument, ScaleSource, empty_document
from packages.domain.spatial import SpatialLayer
from packages.testing.factories.spatial import sample_floor_dimensions, sample_floor_layer

LEVEL_ID = "L-LEVEL000000"
FLOOR_PK = 1

EMPTY_LAYER = SpatialLayer(walls=(), openings=(), rooms=(), furniture=())


def _doc(
    *,
    layer: SpatialLayer | None = None,
    scale_source: ScaleSource = "none",
    scale: Decimal | None = None,
    page_key: str | None = None,
) -> FloorDocument:
    """Tài liệu trong bộ nhớ; `empty_document` là nguồn của mọi trường không liên quan."""
    return replace(
        empty_document(FLOOR_PK),
        layer=EMPTY_LAYER if layer is None else layer,
        scale_source=scale_source,
        scale_mm_per_px=scale,
        scale_page_key=page_key,
    )


def _floor_out(*, area_m2: float | None = None) -> FloorOut:
    """`FloorOut` của #12/#33 — nguồn duy nhất của `id`, `name`, `order`, mm và `areaM2`."""
    return FloorOut(id=LEVEL_ID, name="Tầng 1", order=0, elevation_mm=0, height_mm=3000, area_m2=area_m2, drawings=[])


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("human", None),
        ("measured", None),
        ("none", None),
        ("pipeline", "unresolved"),
        ("project_default", "unresolved"),
    ],
)
def test_scale_status_for_every_source(source: ScaleSource, expected: str | None) -> None:
    """Đủ năm nguồn: chỉ tỉ lệ **tạm** mới kèm `scaleStatus` trên dây (W24, HOP-DONG-MOI §4.2)."""
    assert scale_status(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("human", None),
        ("measured", None),
        ("none", None),
        ("pipeline", "pipeline"),
        ("project_default", "project_default"),
    ],
)
def test_provisional_scale_source_follows_scale_status(source: ScaleSource, expected: str | None) -> None:
    """`scaleSource` có đúng khi `scaleStatus` có, và tách "không đo được" khỏi "máy đo" (UX-R1 K12)."""
    assert provisional_scale_source(source) == expected
    assert (provisional_scale_source(source) is None) == (scale_status(source) is None)


def _walls_thick(*thickness_mm: int) -> SpatialLayer:
    """Lớp mẫu tầng 0 với bề dày tường lần lượt `thickness_mm` (lặp vòng nếu ít hơn số tường)."""
    layer = sample_floor_layer(0, level_id=LEVEL_ID)
    walls = tuple(
        wall.model_copy(update={"thickness_mm": thickness_mm[i % len(thickness_mm)]})
        for i, wall in enumerate(layer.walls[: len(thickness_mm)])
    )
    return layer.model_copy(update={"walls": walls})


def test_suggested_scale_brings_median_wall_to_150_mm() -> None:
    """UX-R2 K01: tường 7/10/16 mm ở 1 mm/px → trung vị 10 → đề xuất 15 mm/px; 3 chữ số thập phân."""
    assert suggested_scale_mm_per_px(_doc(layer=_walls_thick(7, 10, 16), scale=Decimal(1)), "project_default") == 15.0
    assert suggested_scale_mm_per_px(_doc(layer=_walls_thick(220), scale=Decimal("12.7")), "project_default") == 8.659


@pytest.mark.parametrize("source", ["human", "none", "pipeline"])
def test_suggested_scale_only_for_project_default(source: ScaleSource) -> None:
    """Tỉ lệ máy đo hay người đặt không cần đề xuất."""
    assert suggested_scale_mm_per_px(_doc(layer=_walls_thick(10), scale=Decimal(1)), source) is None


def test_suggested_scale_absent_without_walls_scale_or_range() -> None:
    """Không tường, không tỉ lệ, hay kết quả ngoài 1-200 mm/px → vắng."""
    assert suggested_scale_mm_per_px(_doc(scale=Decimal(1)), "project_default") is None
    assert suggested_scale_mm_per_px(_doc(layer=_walls_thick(10)), "project_default") is None
    assert suggested_scale_mm_per_px(_doc(layer=_walls_thick(300), scale=Decimal(1)), "project_default") is None


def test_level_reviewed_needs_at_least_one_wall() -> None:
    """0 tường không phải "đã duyệt xong" mà là "chưa có gì để duyệt"."""
    assert level_reviewed(EMPTY_LAYER, "human") is False


def test_level_reviewed_false_when_one_item_unreviewed() -> None:
    """Một món đồ đạc chưa duyệt đủ làm cả tầng chưa duyệt (HOP-DONG-MOI §4.1)."""
    assert level_reviewed(sample_floor_layer(0, level_id=LEVEL_ID), "human") is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [("human", True), ("measured", True), ("none", True), ("pipeline", False), ("project_default", False)],
)
def test_level_reviewed_follows_scale_source(source: ScaleSource, expected: bool) -> None:
    """Duyệt hết nhưng tỉ lệ còn `unresolved` → tầng chưa duyệt; `human`, `measured` và `none` thì đạt."""
    layer = sample_floor_layer(0, level_id=LEVEL_ID, reviewed=True)
    assert level_reviewed(layer, source) is expected


def test_level_reviewed_ignores_dimensions() -> None:
    """Kích thước chưa duyệt **không** làm hỏng `Level.reviewed` (v1 chỉ tính bốn họ của lớp)."""
    layer = sample_floor_layer(0, level_id=LEVEL_ID, reviewed=True)
    dimensions = sample_floor_dimensions(0, level_id=LEVEL_ID)
    assert any(not dimension.reviewed for dimension in dimensions)
    document = _doc(layer=layer, scale_source="human", scale=Decimal("12.700000"))
    assert level_out(_floor_out(), replace(document, dimensions=dimensions), "human").reviewed is True


@pytest.mark.parametrize("source", ["none", "pipeline", "project_default"])
def test_effective_scale_source_keeps_unbound_sources(source: ScaleSource) -> None:
    """Nguồn khác `human`, `measured` không gắn với trang: cổng trả gì cũng không đổi hạng."""
    assert effective_scale_source(_doc(scale_source=source, page_key="P1"), "P2") == source


@pytest.mark.parametrize("source", ["human", "measured"])
def test_effective_scale_source_demotes_on_other_page(source: ScaleSource) -> None:
    """Tỉ lệ đặt/đọc trên trang P1, bản vẽ hiện tại là P2 → tụt xuống `pipeline` (#35); cùng trang thì giữ."""
    assert effective_scale_source(_doc(scale_source=source, page_key="P1"), "P2") == "pipeline"
    assert effective_scale_source(_doc(scale_source=source, page_key="P1"), "P1") == source


def test_effective_scale_source_keeps_human_on_same_page() -> None:
    """Cùng trang thì tỉ lệ vẫn do người xác nhận."""
    assert effective_scale_source(_doc(scale_source="human", page_key="P1"), "P1") == "human"


@pytest.mark.parametrize(("page_key", "gate_key"), [(None, "P2"), ("P1", None)])
def test_effective_scale_source_keeps_human_without_evidence(page_key: str | None, gate_key: str | None) -> None:
    """Thiếu một trong hai trang = không có bằng chứng trang đã đổi → giữ nguyên `human`."""
    assert effective_scale_source(_doc(scale_source="human", page_key=page_key), gate_key) == "human"


def test_level_out_copies_floor_fields() -> None:
    """`Level` lấy `id`, `name`, `order`, mm và `areaM2` thẳng từ `FloorOut` của #12/#33."""
    level = level_out(_floor_out(area_m2=68.0), empty_document(FLOOR_PK), "none")
    assert (level.id, level.name, level.order, level.elevation_mm, level.height_mm) == (LEVEL_ID, "Tầng 1", 0, 0, 3000)
    assert level.area_m2 == 68.0
    assert (level.source, level.confidence) == ("human", 1.0)


def test_level_out_converts_scale_to_float() -> None:
    """Tỉ lệ `numeric(12,6)` lên dây là **số** JSON, không phải chuỗi `Decimal` (K01)."""
    level = level_out(_floor_out(), _doc(scale_source="human", scale=Decimal("12.700000")), "human")
    assert level.scale_millimetres_per_pixel == 12.7


def test_level_out_omits_scale_when_none() -> None:
    """Nguồn `none` → vắng cả tỉ lệ (CHECK của DB buộc `scale_mm_per_px` NULL)."""
    assert level_out(_floor_out(), empty_document(FLOOR_PK), "none").scale_millimetres_per_pixel is None
