"""Đề xuất cho cả ngôi nhà (NHA-05a): tầng của `solve_house`, mâu thuẫn theo nguồn, hệ đơn vị, vi phạm nhóm T."""

import pytest

from packages.domain.drawing_set import solve_house
from packages.domain.drawing_set.building import (
    CONFLICT_CAP,
    BuildingProposal,
    Conflict,
    Measured,
    _section_storeys,
    effective,
    level_conflict,
    propose_building,
)
from packages.domain.drawing_set.tests.test_proposal import _SECTION, _section, _stack, _view

_SECTIONS = [_SECTION, _SECTION]  # hai mặt cắt cùng thang 0 / 3,3 / 6,6 / 9,9 m: ba tầng và mái
_PLAN = Measured(value=3900, confidence=0.8, source="plan", view_ids=("f1",))
_SIDE = Measured(value=3300, confidence=0.7, source="section")


def codes(proposal: BuildingProposal) -> list[str]:
    """Mã các vi phạm, theo thứ tự phát."""
    return [hit.code for hit in proposal.violations]


# 1. Bộ mẫu


def test_sample_set_wraps_solve_house_and_raises_nothing() -> None:
    """Ba mặt bằng khớp hai mặt cắt: tầng đúng như `solve_house`, không mâu thuẫn, không vi phạm, hệ mét."""
    views = _stack("±0.000", "+3.300", "+6.600")
    proposal = propose_building(views, _SECTIONS, order_known=True)
    assert proposal.floors == solve_house(views, order_known=True, sections=_SECTIONS).floors
    assert (proposal.conflicts, proposal.violations) == ((), ())
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
    assumed = [hit for hit in proposal.violations if hit.code == "T-07"]
    assert [(hit.floor_refs, hit.values) for hit in assumed] == [(("f1",), ("heightMm", "default"))]


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
    assert codes(propose_building(with_basement, [ladder, ladder], order_known=True)) == []


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
