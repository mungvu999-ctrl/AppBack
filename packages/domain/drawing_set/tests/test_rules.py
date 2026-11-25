"""Luật ghép nhóm T (NHA-05a) và nhóm M kiểm lúc đề xuất (NHA-05b): mỗi luật một ca đạt và một ca vi phạm."""

import pytest

from packages.domain.drawing_set.rules import (
    RULES,
    RuleHit,
    m02_eave,
    m03_pitch,
    m05_form,
    m06_rooftop,
    m07_flat_drain,
    m09_min_pitch,
    m17_consistent,
    m18_roof_plan,
    m19_ambiguous,
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
    assert sorted(code for code in RULES if code.startswith("T-")) == [f"T-0{n}" for n in range(1, 8)]
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


# Nhóm M: mái, kiểm lúc đề xuất


def test_rule_table_lists_the_proposal_time_m_rules() -> None:
    """Chín luật M kiểm lúc đề xuất có trong bảng; M-17 chặn xác nhận, M-07 và M-09 chỉ gợi ý."""
    codes = ["M-02", "M-03", "M-05", "M-06", "M-07", "M-09", "M-17", "M-18", "M-19"]
    assert sorted(code for code in RULES if code.startswith("M-")) == codes
    assert (RULES["M-02"].tolerance, RULES["M-03"].tolerance) == (100, 3)
    assert {code: RULES[code].level for code in ("M-07", "M-09", "M-17")} == {
        "M-07": "hint",
        "M-09": "hint",
        "M-17": "error",
    }


@pytest.mark.parametrize("eave_mm", [6500, 6600, 6700])
def test_m02_passes_when_the_eave_sits_on_the_wall_top(eave_mm: int) -> None:
    """Cốt diềm cách đỉnh tường không quá 100 mm."""
    assert m02_eave("V2", eave_mm, 6600) is None


def test_m02_warns_with_both_levels() -> None:
    """Lệch 101 mm: cảnh báo kèm cốt diềm rồi đỉnh tường."""
    hit = RuleHit(code="M-02", level="warning", floor_refs=("V2",), values=(6701, 6600))
    assert m02_eave("V2", 6701, 6600) == hit


@pytest.mark.parametrize("degs", [(), (30.0,), (30.0, 33.0), (31.5, 30.0, 28.5)])
def test_m03_passes_within_three_degrees(degs: tuple[float, ...]) -> None:
    """Một nguồn, hoặc các nguồn cách nhau không quá 3°."""
    assert m03_pitch("V2", degs) is None


def test_m03_boundary_survives_float_drift() -> None:
    """4,4 trừ 1,4 ra 3,0000000000000004 trong số thực: vẫn là lệch đúng 3°, không vi phạm."""
    assert m03_pitch("V2", (1.4, 4.4)) is None


def test_m03_warns_when_sources_disagree() -> None:
    """Lệch quá 3°: cảnh báo kèm mọi trị số, theo thứ tự truyền vào."""
    hit = RuleHit(code="M-03", level="warning", floor_refs=("V2",), values=(30.0, 33.1))
    assert m03_pitch("V2", (30.0, 33.1)) == hit


@pytest.mark.parametrize("forms", [(), ("gable",), ("flat", "flat")])
def test_m05_passes_when_sources_name_one_form(forms: tuple[str, ...]) -> None:
    """Không nguồn, một nguồn, hoặc các nguồn cùng dạng."""
    assert m05_form("V2", forms) is None


def test_m05_warns_when_forms_differ() -> None:
    """Hai nguồn hai dạng: cảnh báo kèm các dạng."""
    hit = RuleHit(code="M-05", level="warning", floor_refs=("V2",), values=("flat", "sloped"))
    assert m05_form("V2", ("flat", "sloped")) == hit


@pytest.mark.parametrize("label", ["floor", "ground", "roof_terrace", None])
def test_m06_passes_when_the_top_storey_is_not_a_tum(label: str | None) -> None:
    """Tầng cao nhất là tầng thường: không hỏi."""
    assert m06_rooftop(("V3",), label) is None


@pytest.mark.parametrize("label", ["tum", "attic"])
def test_m06_asks_about_a_tum(label: str) -> None:
    """Tầng cao nhất là tum/áp mái: cảnh báo kèm tum và tầng dưới nó."""
    hit = RuleHit(code="M-06", level="warning", floor_refs=("V3", "V2"), values=(label,))
    assert m06_rooftop(("V3", "V2"), label) == hit
    assert m06_rooftop(("V3", "V2"), label, sloped=True) == hit.model_copy(update={"values": (label, "sloped")})


@pytest.mark.parametrize(("form", "drains"), [("flat", True), ("gable", False), ("hip", True)])
def test_m07_passes_for_sloped_or_drained_roofs(form: str, drains: bool) -> None:
    """Mái dốc, hoặc mái bằng có chữ thoát nước."""
    assert m07_flat_drain("V2", form, drains=drains) is None


def test_m07_hints_at_a_flat_roof_without_drainage() -> None:
    """Mái bằng không thấy parapet hay sê nô: gợi ý."""
    assert m07_flat_drain("V2", "flat", drains=False) == RuleHit(
        code="M-07", level="hint", floor_refs=("V2",), values=()
    )


@pytest.mark.parametrize(("pitch_deg", "material"), [(11.8, "tile"), (9.5, "shingle"), (2.0, "metal"), (2.0, None)])
def test_m09_passes_at_or_above_the_material_minimum(pitch_deg: float, material: str | None) -> None:
    """Đúng ngưỡng tối thiểu vẫn đạt; vật liệu không có ngưỡng thì không kiểm."""
    assert m09_min_pitch("V2", pitch_deg, material) is None


@pytest.mark.parametrize(("pitch_deg", "material", "min_deg"), [(11.0, "tile", 11.8), (9.0, "shingle", 9.5)])
def test_m09_hints_below_the_material_minimum(pitch_deg: float, material: str, min_deg: float) -> None:
    """Dưới ngưỡng (ngói 2½:12, shingle 2:12): gợi ý kèm độ dốc, ngưỡng (độ) và vật liệu."""
    hit = RuleHit(code="M-09", level="hint", floor_refs=("V2",), values=(pitch_deg, min_deg, material))
    assert m09_min_pitch("V2", pitch_deg, material) == hit


@pytest.mark.parametrize(
    ("form", "pitch_deg", "eave_mm", "ridge_mm"),
    [("gable", 30.0, 6600, 8900), ("gable", 3.0, 6600, None), ("flat", 0.0, 6600, 6600), ("hip", 84.9, None, 9000)],
)
def test_m17_passes_for_consistent_roofs(
    form: str, pitch_deg: float, eave_mm: int | None, ridge_mm: int | None
) -> None:
    """Đỉnh cao hơn diềm, mái dốc từ 5 % (2,86°) trở lên và dưới 85°; mái bằng không kiểm độ dốc tối thiểu."""
    assert m17_consistent("V2", form, pitch_deg, eave_mm, ridge_mm) is None


@pytest.mark.parametrize(
    ("form", "pitch_deg", "ridge_mm", "failed"),
    [
        ("gable", 30.0, 6600, ("ridgeBelowEave",)),
        ("hip", 2.8, None, ("pitchTooLow",)),
        ("flat", 85.0, None, ("pitchTooHigh",)),
        ("shed", 85.0, 6000, ("ridgeBelowEave", "pitchTooHigh")),
    ],
)
def test_m17_is_an_error_naming_what_failed(
    form: str, pitch_deg: float, ridge_mm: int | None, failed: tuple[str, ...]
) -> None:
    """Sai một điều là lỗi chặn xác nhận, kèm tên từng điều sai."""
    hit = RuleHit(code="M-17", level="error", floor_refs=("V2",), values=failed)
    assert m17_consistent("V2", form, pitch_deg, 6600, ridge_mm) == hit


@pytest.mark.parametrize(("views", "source"), [((), None), ((), "default"), (("R1",), "roofPlan"), (("R1",), "plan")])
def test_m18_passes_without_a_roof_plan_or_with_a_read_form(views: tuple[str, ...], source: str | None) -> None:
    """Bộ không có hình mái, hoặc dạng mái đọc được từ bản vẽ."""
    assert m18_roof_plan(views, source) is None


@pytest.mark.parametrize(("source", "shown"), [("default", "default"), ("convention", "convention"), (None, "none")])
def test_m18_warns_when_a_roof_plan_exists_but_the_form_is_assumed(source: str | None, shown: str) -> None:
    """Có hình mặt bằng mái mà mái vắng hoặc dạng là giả định: cảnh báo kèm các hình mái."""
    hit = RuleHit(code="M-18", level="warning", floor_refs=("R1", "R2"), values=(shown,))
    assert m18_roof_plan(("R1", "R2"), source) == hit


def test_m19_passes_for_a_clear_reading_and_warns_for_an_ambiguous_one() -> None:
    """Độ dốc ghi rõ thì thôi; dạng `a:b` nhập nhằng thì cảnh báo kèm chữ gốc."""
    assert m19_ambiguous("V2", None) is None
    assert m19_ambiguous("V2", "1:2") == RuleHit(code="M-19", level="warning", floor_refs=("V2",), values=("1:2",))
