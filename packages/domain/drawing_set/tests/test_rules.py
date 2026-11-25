"""Luật ghép nhóm T (NHA-05a): mỗi luật một ca đạt và một ca vi phạm."""

import pytest

from packages.domain.drawing_set.rules import (
    RULES,
    RuleHit,
    t01_stack,
    t02_storey_count,
    t03_height,
    t04_one_plan_per_storey,
    t05_sources,
    t06_units,
    t07_assumed,
)


def test_rule_table_lists_every_t_rule() -> None:
    """Bảng dữ liệu có đủ bảy mã nhóm T, mỗi mã một mức; dung sai nằm ở bảng chứ không rải trong hàm."""
    assert sorted(RULES) == ["T-01", "T-02", "T-03", "T-04", "T-05", "T-06", "T-07"]
    assert (RULES["T-01"].level, RULES["T-01"].tolerance) == ("error", 50)
    assert (RULES["T-04"].level, RULES["T-05"].tolerance) == ("error", 250)
    assert {RULES[code].level for code in ("T-02", "T-03", "T-05", "T-06", "T-07")} == {"warning"}


# T-01: cốt tầng n+1 = cốt tầng n + chiều cao tầng n


def test_t01_passes_when_floors_stack_within_tolerance() -> None:
    """Lệch 50 mm trở xuống: không vi phạm."""
    assert t01_stack([("V1", 0, 3600), ("V2", 3600, 3300), ("V3", 6950, 3300)]) == []


def test_t01_overlap_is_an_error_and_gap_a_warning() -> None:
    """Tầng trên thấp hơn đỉnh tầng dưới quá 50 mm là lỗi; hở quá 50 mm là cảnh báo. Giá trị kèm là độ lệch (mm)."""
    hits = t01_stack([("V1", 0, 3600), ("V2", 3500, 3300), ("V3", 7000, 3300)])
    assert hits == [
        RuleHit(code="T-01", level="error", floor_refs=("V1", "V2"), values=(-100,)),
        RuleHit(code="T-01", level="warning", floor_refs=("V2", "V3"), values=(200,)),
    ]


def test_t01_fires_one_millimetre_past_the_tolerance() -> None:
    """Biên: 51 mm chồng lấn là lỗi, 51 mm hở là cảnh báo."""
    assert [hit.level for hit in t01_stack([("V1", 0, 3600), ("V2", 3549, 3300)])] == ["error"]
    assert [hit.level for hit in t01_stack([("V1", 0, 3600), ("V2", 3651, 3300)])] == ["warning"]


# T-02: số tầng khớp giữa mặt bằng và mặt cắt


@pytest.mark.parametrize(("plans", "sections"), [(3, 3), (3, None)])
def test_t02_passes_when_counts_agree_or_no_section_speaks(plans: int, sections: int | None) -> None:
    """Hai con số bằng nhau, hoặc không mặt cắt nào đọc được: không vi phạm."""
    assert t02_storey_count(plans, sections) is None


def test_t02_warns_with_both_counts() -> None:
    """Lệch: cảnh báo kèm hai con số (mặt bằng trước, mặt cắt sau)."""
    assert t02_storey_count(2, 3) == RuleHit(code="T-02", level="warning", floor_refs=(), values=(2, 3))


# T-03: chiều cao tầng trong 2,4-4,6 m


@pytest.mark.parametrize("height_mm", [2400, 3300, 4600])
def test_t03_passes_inside_the_plausible_band(height_mm: int) -> None:
    """Hai đầu dải vẫn hợp lệ."""
    assert t03_height("V1", height_mm) is None


@pytest.mark.parametrize("height_mm", [2399, 4601])
def test_t03_warns_outside_the_plausible_band(height_mm: int) -> None:
    """Ngoài dải: cảnh báo kèm chiều cao."""
    hit = RuleHit(code="T-03", level="warning", floor_refs=("V1",), values=(height_mm,))
    assert t03_height("V1", height_mm) == hit


# T-04: mỗi tầng một mặt bằng đại diện


def test_t04_passes_when_every_target_has_one_plan() -> None:
    """Mỗi đích tầng một hình."""
    assert t04_one_plan_per_storey({"V1": "L1", "V2": "L2"}) == []


def test_t04_is_an_error_for_two_plans_on_one_target() -> None:
    """Hai hình cùng đích tầng: lỗi, kèm cả hai hình theo thứ tự khai."""
    assert t04_one_plan_per_storey({"V1": "L1", "V2": "L2", "V3": "L1"}) == [
        RuleHit(code="T-04", level="error", floor_refs=("V1", "V3"), values=("L1",))
    ]


# T-05: cốt cùng tầng từ các nguồn khớp nhau


def test_t05_fires_one_millimetre_past_the_tolerance() -> None:
    """Biên: lệch 251 mm đã là vi phạm."""
    assert t05_sources("V2", (3600, 3851)) is not None


@pytest.mark.parametrize("values", [(), (3600,), (3600, 3850), (3850, 3600, 3700)])
def test_t05_passes_within_250_mm(values: tuple[int, ...]) -> None:
    """Một nguồn, hoặc các nguồn cách nhau không quá 250 mm."""
    assert t05_sources("V2", values) is None


def test_t05_warns_when_sources_disagree() -> None:
    """Lệch quá 250 mm: cảnh báo kèm mọi giá trị ứng viên, theo thứ tự truyền vào."""
    hit = RuleHit(code="T-05", level="warning", floor_refs=("V2",), values=(3600, 3900))
    assert t05_sources("V2", (3600, 3900)) == hit


# T-06: hệ đơn vị xác định và nhất quán


@pytest.mark.parametrize(("metric", "imperial"), [(12, 0), (0, 7)])
def test_t06_passes_for_one_unit_system(metric: int, imperial: int) -> None:
    """Chỉ có chữ của một hệ."""
    assert t06_units(metric, imperial) is None


@pytest.mark.parametrize(("metric", "imperial"), [(12, 7), (0, 0)])
def test_t06_warns_when_mixed_or_unknown(metric: int, imperial: int) -> None:
    """Lẫn hai hệ, hoặc không đủ chữ để suy: cảnh báo kèm hai số đếm."""
    hit = RuleHit(code="T-06", level="warning", floor_refs=(), values=(metric, imperial))
    assert t06_units(metric, imperial) == hit


# T-07: giá trị mặc định phải được người duyệt xem


@pytest.mark.parametrize("source", ["plan", "section", "plan+section", "elevation", "reviewer", None])
def test_t07_passes_for_read_values(source: str | None) -> None:
    """Giá trị đọc từ bản vẽ, do người duyệt nhập, hoặc không có giá trị: không cần xem lại."""
    assert t07_assumed("V1", "heightMm", source) is None


@pytest.mark.parametrize("source", ["default", "convention"])
def test_t07_warns_for_assumed_values(source: str) -> None:
    """Mặc định và quy ước: cảnh báo kèm tên trường và nguồn."""
    hit = RuleHit(code="T-07", level="warning", floor_refs=("V1",), values=("heightMm", source))
    assert t07_assumed("V1", "heightMm", source) == hit
