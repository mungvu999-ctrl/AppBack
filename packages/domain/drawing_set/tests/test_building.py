"""Đề xuất cho cả ngôi nhà (NHA-05a, NHA-05b): tầng, mâu thuẫn theo nguồn, hệ đơn vị, mái, vi phạm luật ghép."""

import pytest

from packages.domain.drawing_set import solve_house
from packages.domain.drawing_set.building import (
    CONFLICT_CAP,
    BuildingProposal,
    Conflict,
    Measured,
    Source,
    _section_storeys,
    effective,
    level_conflict,
    propose_building,
)
from packages.domain.drawing_set.proposal import SectionView
from packages.domain.drawing_set.tests.test_proposal import _SECTION, _section, _stack, _view

_SECTIONS = [_SECTION, _SECTION]  # hai mặt cắt cùng thang 0 / 3,3 / 6,6 / 9,9 m: ba tầng và mái
_PLAN = Measured(value=3900, confidence=0.8, source="plan", view_ids=("f1",))
_SIDE = Measured(value=3300, confidence=0.7, source="section")


def codes(proposal: BuildingProposal) -> list[str]:
    """Mã các vi phạm, theo thứ tự phát."""
    return [hit.code for hit in proposal.violations]


def floor_codes(proposal: BuildingProposal) -> list[str]:
    """Mã các vi phạm về tầng: bỏ nhóm M và các T-07 của trường mái (bộ nào cũng có mái đề xuất, NHA-05b)."""
    found = [hit for hit in proposal.violations if hit.code.startswith("T-")]
    return [hit.code for hit in found if not str(hit.values[0]).startswith("roof")]


def hits(proposal: BuildingProposal, code: str) -> list[tuple[tuple[str, ...], tuple[float | str, ...]]]:
    """Hình liên quan và số liệu của mọi vi phạm mang mã `code`."""
    return [(hit.floor_refs, hit.values) for hit in proposal.violations if hit.code == code]


# 1. Bộ mẫu


def test_sample_set_wraps_solve_house_and_raises_nothing() -> None:
    """Ba mặt bằng khớp hai mặt cắt: tầng đúng như `solve_house`, không mâu thuẫn, không vi phạm về tầng, hệ mét."""
    views = _stack("±0.000", "+3.300", "+6.600")
    proposal = propose_building(views, _SECTIONS, order_known=True)
    assert proposal.floors == solve_house(views, order_known=True, sections=_SECTIONS).floors
    assert (proposal.conflicts, floor_codes(proposal)) == ((), [])
    assert (proposal.unit_system.value, proposal.unit_system.source) == ("metric", "plan")


def test_proposal_survives_the_json_round_trip() -> None:
    """Hình dạng lưu JSONB: dump ra khoá dây rồi đọc lại được đúng đề xuất."""
    proposal = propose_building(_stack("±0.000", "+3.900"), _SECTIONS, order_known=True)
    assert proposal.conflicts
    assert BuildingProposal.model_validate(proposal.model_dump(mode="json", by_alias=True)) == proposal


# 2. Vi phạm nhóm T lúc đề xuất


def test_default_top_height_must_be_seen() -> None:
    """Không mặt cắt: chiều cao tầng trên cùng là mặc định 3,3 m → T-07 trên đúng hình đó."""
    proposal = propose_building(_stack("±0.000", "+3.300"), order_known=True)
    assumed = [found for found in hits(proposal, "T-07") if found[1][0] != "roofForm"]  # mái mặc định hỏi riêng
    assert assumed == [(("f1",), ("heightMm", "default"))]


def test_fewer_plans_than_section_storeys_warns() -> None:
    """Hai mặt bằng mà thang cốt có ba khoảng tầng: T-02 kèm hai con số."""
    proposal = propose_building(_stack("±0.000", "+3.300"), _SECTIONS, order_known=True)
    assert [hit.values for hit in proposal.violations if hit.code == "T-02"] == [(2, 3)]


def test_implausible_height_warns() -> None:
    """Tầng 1 cao 6 m: T-03 kèm chiều cao."""
    proposal = propose_building(_stack("±0.000", "+6.000"), order_known=True)
    assert [(hit.floor_refs, hit.values) for hit in proposal.violations if hit.code == "T-03"] == [(("f0",), (6000,))]


# 3. Mâu thuẫn giữa các nguồn


def test_plan_mark_off_the_section_ladder_is_a_conflict() -> None:
    """Mặt bằng tầng 2 ghi +3.900 nhưng `solve_house` lấy bậc 3,3 m của mặt cắt: lộ ra thành mâu thuẫn chưa chọn."""
    proposal = propose_building(_stack("±0.000", "+3.900"), _SECTIONS, order_known=True)
    (conflict,) = proposal.conflicts
    assert (conflict.key, conflict.field, conflict.chosen_source) == ("elevationMm:f1", "elevationMm", None)
    assert [(c.source, c.value, c.view_ids) for c in conflict.candidates] == [
        ("section", 3300, ("f1",)),
        ("plan", 3900, ("f1",)),
    ]
    assert [hit.values for hit in proposal.violations if hit.code == "T-05"] == [(3300, 3900)]


def test_plan_mark_near_the_ladder_is_no_conflict() -> None:
    """Hình ghi +3.500, mặt cắt 3,3 m: lệch 200 mm, trong dung sai, không hỏi."""
    assert propose_building(_stack("±0.000", "+3.500"), _SECTIONS, order_known=True).conflicts == ()


def test_floor_without_its_own_mark_has_nothing_to_dispute() -> None:
    """Hình không ghi cốt, cốt lấy từ mặt cắt: chỉ một nguồn nên không có mâu thuẫn."""
    assert propose_building(_stack("", "+3.300"), _SECTIONS, order_known=True).conflicts == ()


def test_reviewers_pick_feeds_the_stacking_rule() -> None:
    """Chọn cốt của hình (3900) thay cho bậc thang (3300): tầng dưới cao 3300 nên hở 600 mm, T-01 phải báo ngay.

    `floors` vẫn là giá trị máy đề xuất; lựa chọn cho khoá không tồn tại bị bỏ qua.
    """
    picks = {"elevationMm:f1": "plan", "elevationMm:gone": "plan"}
    proposal = propose_building(_stack("±0.000", "+3.900"), _SECTIONS, order_known=True, picks=picks)  # type: ignore[arg-type]
    assert [floor.elevation_mm for floor in proposal.floors] == [0, 3300]
    stacking = [hit for hit in proposal.violations if hit.code == "T-01"]
    assert [(hit.level, hit.floor_refs, hit.values) for hit in stacking] == [("warning", ("f0", "f1"), (600,))]


@pytest.mark.parametrize(
    ("rungs_mm", "storeys"),
    [
        ([0, 3300, 6600, 9900], 3),
        ([0, 3300, 6600, 7400], 2),  # 7,4 m chỉ cách 6,6 m có 0,8 m: parapet, không phải tầng
        ([-3000, 0, 3300, 6600], 2),  # bậc tầng hầm không tính vào tầng nổi
        ([3300, 6600, 9900], None),  # không có bậc trệt: không biết đang đứng ở tầng nào
        ([0, 3300, 8300], None),  # khoảng trống 5 m: thang thiếu bậc, không đếm hụt
        ([0, 1500], None),  # không khoảng nào đủ một tầng
        ([], None),
    ],
)
def test_section_ladder_storey_count(rungs_mm: list[int], storeys: int | None) -> None:
    """Số tầng nổi trên thang cốt; thang không đọc được thì không nói gì, thay vì nói sai."""
    assert _section_storeys(rungs_mm) == storeys


def test_rooftop_and_basement_plans_do_not_break_the_storey_count() -> None:
    """Mặt bằng tum (trên mái) và tầng hầm không tính vào số tầng nổi đem so với mặt cắt."""
    with_tum = [*_stack("±0.000", "+3.300", "+6.600"), _view("t", "MẶT BẰNG TUM", "+9.900", position=3)]
    assert "T-02" not in codes(propose_building(with_tum, _SECTIONS, order_known=True))
    ladder = _section(("-3.000", 1300.0), ("±0.000", 1000.0), ("+3.300", 670.0), ("+6.600", 340.0))
    with_basement = [
        _view("h", "MẶT BẰNG TẦNG HẦM", "-3.000", position=0),
        _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=1),
        _view("b", "MẶT BẰNG TẦNG 2", "+3.300", position=2),
    ]
    assert floor_codes(propose_building(with_basement, [ladder, ladder], order_known=True)) == []


def test_two_views_of_one_storey_raise_no_false_stacking() -> None:
    """Hai hình cùng tầng 1 (bản thường và bản nội thất): tầng chỉ được xếp chồng một lần."""
    views = [
        _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("a2", "MẶT BẰNG NỘI THẤT TẦNG 1", "±0.000", position=1),
        _view("b", "MẶT BẰNG TẦNG 2", "+3.300", position=2),
    ]
    assert not {"T-01", "T-03"} & set(codes(propose_building(views)))


def test_duplicate_view_ids_are_rejected() -> None:
    """Hai hình trùng mã: lỗi của nơi gọi, ném như `solve_house`."""
    with pytest.raises(ValueError, match="floor_id"):
        propose_building([_view("a", "MẶT BẰNG TẦNG 1"), _view("a", "MẶT BẰNG TẦNG 2")])


def test_order_conflict_survives_the_json_round_trip() -> None:
    """Tầng mang `order_conflict` (dataclass lồng trong `FloorProposal`) cũng dump ra khoá dây và đọc lại nguyên vẹn."""
    views = [_view("a", "MẶT BẰNG TẦNG 2", "+3.300", position=0), _view("b", "MẶT BẰNG TẦNG 1", "±0.000", position=1)]
    proposal = propose_building(views, order_known=True)
    dumped = proposal.model_dump(mode="json", by_alias=True)
    assert all(floor.order_conflict is not None for floor in proposal.floors)
    assert {"floorId", "storeyNo", "orderConflict"} <= set(dumped["floors"][0])
    assert BuildingProposal.model_validate(dumped) == proposal


@pytest.mark.parametrize(
    ("candidates", "chosen", "message"),
    [
        ((_SIDE, _PLAN), "elevation", "không có trong ứng viên"),  # nguồn đã chọn không còn
        ((_PLAN,), None, "ít nhất hai ứng viên"),
        ((_PLAN, _PLAN.model_copy(update={"value": 3300})), None, "mỗi nguồn một ứng viên"),
        ((_SIDE, _PLAN.model_copy(update={"value": "3900"})), None, "phải là số mm"),
    ],
)
def test_conflict_rejects_malformed_data(candidates: tuple[Measured, ...], chosen: str | None, message: str) -> None:
    """JSONB cũ hay bị sửa tay sai dạng: hỏng ngay lúc đọc thay vì chọn bừa."""
    with pytest.raises(ValueError, match=message):
        Conflict(key="elevationMm:f1", field="elevationMm", candidates=candidates, chosen_source=chosen)


def test_picking_a_source_settles_the_conflict() -> None:
    """Người duyệt chọn nguồn: mâu thuẫn vẫn hiện nhưng đã có `chosenSource`, T-05 hết."""
    views = _stack("±0.000", "+3.900")
    proposal = propose_building(views, _SECTIONS, order_known=True, picks={"elevationMm:f1": "plan"})
    assert [conflict.chosen_source for conflict in proposal.conflicts] == ["plan"]
    assert "T-05" not in codes(proposal)


def test_stale_pick_is_dropped() -> None:
    """Lựa chọn trỏ tới nguồn không còn trong ứng viên: coi như chưa chọn."""
    assert level_conflict("f1", [_PLAN, _SIDE], "elevation") == level_conflict("f1", [_PLAN, _SIDE], None)


def test_agreeing_sources_make_no_conflict() -> None:
    """Hai nguồn cách nhau không quá 250 mm: không có mâu thuẫn."""
    assert level_conflict("f1", [_PLAN, _SIDE.model_copy(update={"value": 3700})], None) is None


@pytest.mark.parametrize("pick", [None, "plan", "section"])
def test_choice_by_source_survives_reordering(pick: str | None) -> None:
    """Ứng viên đổi thứ tự sau một lần tính lại: mâu thuẫn và giá trị hiệu lực vẫn y nguyên."""
    forward = level_conflict("f1", [_PLAN, _SIDE], pick)  # type: ignore[arg-type]
    backward = level_conflict("f1", [_SIDE, _PLAN], pick)  # type: ignore[arg-type]
    assert forward is not None
    assert forward == backward
    assert effective(forward) == effective(backward)


def test_effective_value_follows_the_pick_else_the_priority_table() -> None:
    """Đã chọn: lấy đúng ứng viên của nguồn đó. Chưa chọn: theo bảng ưu tiên, độ tin chặn ở 0,65."""
    chosen = Conflict(key="elevationMm:f1", field="elevationMm", candidates=(_SIDE, _PLAN), chosen_source="plan")
    assert effective(chosen) == _PLAN
    open_conflict = chosen.model_copy(update={"chosen_source": None})
    assert effective(open_conflict) == _SIDE.model_copy(update={"confidence": CONFLICT_CAP})


# 4. Hệ đơn vị


def test_feet_inch_text_makes_the_set_imperial() -> None:
    """Chỉ có chữ feet-inch: hệ imperial, không vi phạm T-06."""
    proposal = propose_building([_view("a", "FIRST FLOOR PLAN", "10'-6\"", "8'-0\"")])
    assert (proposal.unit_system.value, proposal.unit_system.source) == ("imperial", "plan")
    assert "T-06" not in codes(proposal)


def test_bare_feet_axis_labels_do_not_make_the_set_imperial() -> None:
    """Nhãn trục phụ `1'`, `2'` trên bản vẽ hệ mét không phải bằng chứng feet-inch: hệ mét, không hỏi T-06; khi
    bộ không còn chữ nào khác thì hệ đơn vị là "chưa rõ" (mặc định mét, có hỏi), không phải imperial 0,9."""
    metric = propose_building([_view("a", "MẶT BẰNG TẦNG 1", "±0.000", "1'", "2'")])
    assert (metric.unit_system.value, metric.unit_system.confidence) == ("metric", 0.9)
    assert "T-06" not in codes(metric)
    unknown = propose_building([_view("a", "MẶT BẰNG TẦNG 1", "1'", "2'")])
    assert (unknown.unit_system.value, unknown.unit_system.source) == ("metric", "default")
    assert "T-06" in codes(unknown)
    assert propose_building([_view("a", "FIRST FLOOR PLAN", "EL. 100'")]).unit_system.value == "imperial"


def test_mixed_units_ask_the_reviewer() -> None:
    """Lẫn cốt mét và chữ feet-inch: T-06, độ tin hệ đơn vị không quá 0,5."""
    proposal = propose_building([_view("a", "MẶT BẰNG TẦNG 1", "±0.000", "+3.300", "10'-6\"")])
    assert proposal.unit_system.confidence <= 0.5
    assert [hit.values for hit in proposal.violations if hit.code == "T-06"] == [(2, 1)]


def test_no_unit_evidence_defaults_to_metric_and_asks() -> None:
    """Không chữ nào cho biết hệ đơn vị: mặc định mét, nguồn `default`, T-06."""
    proposal = propose_building([_view("a", "MẶT BẰNG TẦNG 1")])
    assert (proposal.unit_system.value, proposal.unit_system.source) == ("metric", "default")
    assert [hit.values for hit in proposal.violations if hit.code == "T-06"] == [(0, 0)]


def test_empty_set_proposes_nothing() -> None:
    """Không hình nào: không tầng, không mâu thuẫn; chỉ còn câu hỏi về hệ đơn vị."""
    proposal = propose_building([])
    assert (proposal.floors, proposal.conflicts, codes(proposal)) == ((), (), ["T-06"])


# 6. Mái (NHA-05b)

_ROOF_PLAN = "MẶT BẰNG MÁI"
_TWO = ("±0.000", "+3.300")  # hai tầng; không mặt cắt nên chiều cao tầng trên cùng là mặc định 3,3 m


def _roofed(*marks: str, sections: tuple[SectionView, ...] = (), **kwargs: Source) -> BuildingProposal:
    """Nhà hai tầng kèm một hình mặt bằng mái `r0` mang các dòng chữ `marks`; `kwargs` là lựa chọn của người duyệt."""
    roofs = [_view("r0", _ROOF_PLAN, *marks)]
    picks = {key.replace("_", ":"): source for key, source in kwargs.items()}
    return propose_building(_stack(*_TWO), sections, roofs=roofs, order_known=True, picks=picks)


def _measured(item: Measured | None) -> tuple[float | str, str, float, tuple[str, ...]]:
    """Bốn thứ đáng so của một `Measured`: giá trị, nguồn, độ tin, hình."""
    assert item is not None
    return item.value, item.source, item.confidence, item.view_ids


def test_set_without_roof_evidence_gets_a_default_flat_roof_and_a_question() -> None:
    """Không hình mái, không mặt cắt: tầng trên cùng nhận mái bằng mặc định, diềm ở đỉnh tường, kèm câu hỏi."""
    proposal = propose_building(_stack(*_TWO), order_known=True)
    (roof,) = proposal.roofs
    assert roof.level_ref == "f1"
    assert _measured(roof.form) == ("flat", "default", 0.3, ())
    assert _measured(roof.pitch_deg) == (0.0, "default", 0.3, ())
    assert _measured(roof.eave_elevation_mm) == (6600, "default", 0.4, ("f1",))  # thừa kế chiều cao tầng mặc định
    assert _measured(roof.overhang_mm) == (0, "convention", 0.4, ())
    assert _measured(roof.quarter_turns) == (0, "convention", 0.6, ())
    assert (roof.material, roof.ridge_elevation_mm, roof.parapet_mm) == (None, None, None)
    assert (("f1",), ("roofForm", "default")) in hits(proposal, "T-07")
    assert hits(proposal, "M-07") == [(("f1",), ())]


def test_roof_plan_slope_gives_a_sourced_pitch_and_an_unsure_form() -> None:
    """Mặt bằng mái ghi `i=30%`: độ dốc có nguồn và hình; dạng là quy ước (hai mái) nên M-18 hỏi, kèm hình mái."""
    proposal = _roofed("i=30%")
    (roof,) = proposal.roofs
    assert _measured(roof.pitch_deg) == (16.7, "roofPlan", 0.8, ("r0",))
    assert _measured(roof.form) == ("gable", "convention", 0.4, ())
    assert roof.overhang_mm.value == 300
    assert hits(proposal, "M-18") == [(("r0",), ("convention",))]
    assert not hits(proposal, "M-07")
    assert (("f1",), ("roofForm", "convention")) not in hits(proposal, "T-07")  # M-18 đã hỏi đúng câu đó


def test_two_percent_drainage_slope_is_a_flat_roof() -> None:
    """`i=2%` là dốc thoát nước: dạng `flat` đọc từ hình mái, trị số chỉ ghi lại; có sê nô thì hết gợi ý M-07."""
    proposal = _roofed("i=2%")
    (roof,) = proposal.roofs
    assert _measured(roof.form) == ("flat", "roofPlan", 0.8, ("r0",))
    assert (roof.pitch_deg.value, roof.overhang_mm.value) == (1.1, 0)
    assert not hits(proposal, "M-18")
    assert hits(proposal, "M-07") == [(("f1",), ())]
    assert not hits(_roofed("i=2%", "SÊ NÔ"), "M-07")


def test_tum_gets_its_own_roof_and_a_slab_over_the_storey_below() -> None:
    """Tầng cao nhất là tum: hai mái bằng (trên tum, và phủ tầng dưới), kèm câu hỏi M-06."""
    views = [*_stack(*_TWO), _view("t0", "MẶT BẰNG TUM", "+6.600", position=2)]
    proposal = propose_building(views, order_known=True)
    assert [(roof.level_ref, roof.form.value, roof.eave_elevation_mm) for roof in proposal.roofs] == [
        ("t0", "flat", Measured(value=9900, confidence=0.4, source="default", view_ids=("t0",))),
        ("f1", "flat", Measured(value=6600, confidence=0.75, source="plan", view_ids=("f1",))),
    ]
    assert hits(proposal, "M-06") == [(("t0", "f1"), ("tum",))]


def test_sloped_evidence_with_a_tum_builds_no_roof() -> None:
    """Có tum mà hình mái ghi độ dốc: mái dốc sẽ xuyên qua tum, nên không đề xuất mái nào, chỉ hỏi."""
    tum = _view("t0", "MẶT BẰNG TUM", "+6.600", position=2)
    roof_plan = [_view("r0", _ROOF_PLAN, "30°")]
    proposal = propose_building([*_stack(*_TWO), tum], roofs=roof_plan, order_known=True)
    assert proposal.roofs == ()
    assert hits(proposal, "M-06") == [(("t0", "f1"), ("tum", "sloped"))]  # không có hình mái thì đây là dấu duy nhất
    assert hits(proposal, "M-18") == [(("r0",), ("none",))]


def test_attic_keeps_the_sloped_roof_the_drawings_show() -> None:
    """Áp mái nằm dưới mái dốc: chữ ghi độ dốc thì áp mái nhận mái dốc đó, không có tấm phủ tầng dưới; vẫn hỏi M-06."""
    attic = _view("a0", "MẶT BẰNG ÁP MÁI", "+6.600", position=2)
    roof_plan = [_view("r0", _ROOF_PLAN, "30°")]
    proposal = propose_building([*_stack(*_TWO), attic], roofs=roof_plan, order_known=True)
    assert [floor.storey_label for floor in proposal.floors[:3]] == ["ground", "floor", "attic"]
    assert [(roof.level_ref, roof.form.value, roof.pitch_deg.value) for roof in proposal.roofs] == [
        ("a0", "gable", 30.0)
    ]
    assert hits(proposal, "M-06") == [(("a0", "f1"), ("attic",))]


def test_roof_terrace_plan_means_a_flat_roof_on_the_storey_below() -> None:
    """Hình sân thượng là mặt trên của mái bằng: mái gắn vào tầng dưới nó, dạng suy từ nhãn hình, không hỏi M-06."""
    views = [*_stack(*_TWO), _view("s0", "MẶT BẰNG SÂN THƯỢNG", "+6.600", position=2)]
    proposal = propose_building(views, order_known=True)
    (roof,) = proposal.roofs
    assert (roof.level_ref, _measured(roof.form)) == ("f1", ("flat", "plan", 0.6, ("s0",)))
    assert not hits(proposal, "M-06")
    assert not [values for _, values in hits(proposal, "T-07") if values[0] == "roofForm"]


def test_pitch_sources_that_disagree_become_a_conflict() -> None:
    """Hình mái 30°, mặt cắt 40°: mâu thuẫn chưa chọn và M-03; chọn nguồn thì độ dốc theo nguồn đó và hết hỏi."""
    side = _section(("40°", 200.0))
    proposal = _roofed("30°", sections=(side,))
    (conflict,) = proposal.conflicts
    assert (conflict.key, conflict.field, conflict.chosen_source) == ("pitchDeg:f1", "pitchDeg", None)
    assert [(c.source, c.value) for c in conflict.candidates] == [("roofPlan", 30.0), ("section", 40.0)]
    assert _measured(proposal.roofs[0].pitch_deg) == (30.0, "roofPlan", CONFLICT_CAP, ("r0",))
    assert hits(proposal, "M-03") == [(("f1",), (30.0, 40.0))]
    assert not hits(proposal, "M-05")  # cả hai nguồn đều nói mái dốc

    picked = _roofed("30°", sections=(side,), pitchDeg_f1="section")
    assert _measured(picked.roofs[0].pitch_deg) == (40.0, "section", 0.8, ())
    assert not hits(picked, "M-03")
    dumped = picked.model_dump(mode="json", by_alias=True)
    assert BuildingProposal.model_validate(dumped) == picked
    wire = dumped["roofs"][0]
    assert (wire["levelRef"], wire["pitchDeg"]["value"], wire["eaveElevationMm"]["value"]) == ("f1", 40.0, 6600)
    assert [type(wire[key]["value"]) for key in ("pitchDeg", "eaveElevationMm", "overhangMm")] == [float, int, int]

    stale = _roofed("30°", sections=(side,), pitchDeg_f1="elevation")  # nguồn đã chọn không còn trong ứng viên
    assert (stale.conflicts[0].chosen_source, hits(stale, "M-03")) == (None, [(("f1",), (30.0, 40.0))])


def test_sources_within_three_degrees_make_no_conflict() -> None:
    """Hình mái 30°, mặt cắt 32°: trong dung sai, lấy trị số của hình mái, không hỏi."""
    proposal = _roofed("30°", sections=(_section(("32°", 200.0)),))
    assert (proposal.conflicts, hits(proposal, "M-03")) == ((), [])
    assert proposal.roofs[0].pitch_deg.source == "roofPlan"


def test_flat_and_sloped_sources_also_dispute_the_form() -> None:
    """Hình mái `i=2%` (bằng), mặt cắt 30° (dốc): thêm M-05; dạng đi theo nguồn đang hiệu lực (hình mái)."""
    proposal = _roofed("i=2%", sections=(_section(("30°", 200.0)),))
    assert hits(proposal, "M-05") == [(("f1",), ("flat", "sloped"))]
    assert proposal.roofs[0].form.value == "flat"


def test_clear_section_reading_beats_an_agreeing_ambiguous_roof_plan() -> None:
    """Hình mái ghi `1:2` (26,6°, nhập nhằng), mặt cắt ghi rõ 27°: hai nguồn khớp nên lấy trị số rõ, khỏi hỏi M-19."""
    proposal = _roofed("1:2", sections=(_section(("27°", 200.0)),))
    assert _measured(proposal.roofs[0].pitch_deg) == (27.0, "section", 0.8, ())
    assert (proposal.conflicts, hits(proposal, "M-19")) == ((), [])


def test_ambiguous_slope_notation_asks() -> None:
    """`1:2` không rõ cao:ngang hay ngang:cao: đọc theo cao:ngang, độ tin 0,5, M-19 kèm chữ gốc."""
    proposal = _roofed("1:2")
    assert _measured(proposal.roofs[0].pitch_deg) == (26.6, "roofPlan", 0.5, ("r0",))
    assert hits(proposal, "M-19") == [(("f1",), ("1:2",))]
    assert not hits(_roofed("30°"), "M-19")


def test_material_alone_means_a_sloped_roof_by_convention() -> None:
    """Chỉ có chữ `MÁI NGÓI`: mái dốc theo quy ước, 30° của ngói; vật liệu có nguồn."""
    (roof,) = _roofed("MÁI NGÓI").roofs
    assert _measured(roof.material) == ("tile", "roofPlan", 0.7, ("r0",))
    assert (_measured(roof.form), _measured(roof.pitch_deg)) == (
        ("gable", "convention", 0.4, ()),
        (30.0, "convention", 0.4, ()),
    )


def test_pitch_below_the_material_minimum_is_hinted() -> None:
    """Ngói ở 15 % (8,5°) thấp hơn ngưỡng 2½:12: gợi ý M-09 kèm ngưỡng."""
    assert hits(_roofed("MÁI NGÓI", "i=15%"), "M-09") == [(("f1",), (8.5, 11.8, "tile"))]
    assert not hits(_roofed("MÁI NGÓI", "30°"), "M-09")


def test_imperial_set_defaults_to_a_gable() -> None:
    """Bộ feet-inch không có bằng chứng mái: hai mái theo quy ước, 30° mặc định, đua 450 mm; cả hai đều phải xem.

    Tầng trên cùng không có cốt nên chưa tính được cốt diềm.
    """
    ground = _view("f0", "FIRST FLOOR PLAN", "10'-6\"", "12'-0\"")
    proposal = propose_building([ground, _view("f1", "SECOND FLOOR PLAN", "9'-0\"", position=1)], order_known=True)
    (roof,) = proposal.roofs
    assert (_measured(roof.form), _measured(roof.pitch_deg)) == (
        ("gable", "convention", 0.4, ()),
        (30.0, "default", 0.3, ()),
    )
    assert (roof.level_ref, roof.overhang_mm.value, roof.eave_elevation_mm) == ("f1", 450, None)
    assert [values for _, values in hits(proposal, "T-07") if str(values[0]).startswith("roof")] == [
        ("roofForm", "convention"),
        ("roofPitchDeg", "default"),
    ]


def test_roof_plan_without_any_storey_is_reported() -> None:
    """Chỉ có hình mái, không mặt bằng nào: không có mái để đề xuất, M-18 báo hình mái bị bỏ."""
    proposal = propose_building([], roofs=[_view("r0", _ROOF_PLAN, "30°")])
    assert (proposal.roofs, hits(proposal, "M-18")) == ((), [(("r0",), ("none",))])


def test_terrace_below_a_regular_storey_says_nothing_about_the_top_roof() -> None:
    """Sân thượng giữa nhà (có tầng thường phía trên) không phải bằng chứng mái bằng: mái trên cùng là mặc định."""
    views = [
        _view("f0", "MẶT BẰNG TẦNG 1", "±0.000"),
        _view("s0", "MẶT BẰNG SÂN THƯỢNG", "+3.300", position=1),
        _view("f2", "MẶT BẰNG TẦNG 3", "+6.600", position=2),
    ]
    proposal = propose_building(views, order_known=True)
    assert [floor.storey_label for floor in proposal.floors] == ["ground", "roof_terrace", "floor"]
    (roof,) = proposal.roofs
    assert (roof.level_ref, _measured(roof.form)) == ("f2", ("flat", "default", 0.3, ()))


def test_top_storey_without_a_level_has_no_eave_yet() -> None:
    """Bộ hệ mét mà tầng trên cùng không có cốt: vẫn đề xuất mái, cốt diềm để trống cho người duyệt."""
    (roof,) = propose_building(_stack("±0.000", ""), order_known=True).roofs
    assert (roof.level_ref, roof.eave_elevation_mm) == ("f1", None)


def test_slab_under_a_tum_uses_the_lower_storeys_own_wall_top() -> None:
    """Hai hình cùng một tầng dưới tum: tấm phủ gắn vào hình đại diện có cốt, diềm ở đỉnh tường của chính tầng đó."""
    views = [
        _view("f0", "MẶT BẰNG TẦNG 1", "±0.000"),
        _view("f1x", "MẶT BẰNG TẦNG 2", position=1),
        _view("f1", "MẶT BẰNG TẦNG 2", "+3.300", position=2),
        _view("t0", "MẶT BẰNG TUM", "+6.600", position=3),
    ]
    proposal = propose_building(views, order_known=True)
    by_ref = {roof.level_ref: roof for roof in proposal.roofs}
    assert sorted(by_ref) == ["f1", "t0"]
    assert _measured(by_ref["f1"].eave_elevation_mm)[0] == 6600


def test_only_views_passed_as_roofs_are_roof_text_sources() -> None:
    """Loại hình do người duyệt chốt: hình mái lẫn trong `views` không được đọc làm nguồn mái, cũng không gây M-18."""
    proposal = propose_building([*_stack(*_TWO), _view("r0", _ROOF_PLAN, "30°", position=2)], order_known=True)
    (roof,) = proposal.roofs
    assert (_measured(roof.form), hits(proposal, "M-18")) == (("flat", "default", 0.3, ()), [])
