"""Đề xuất cho cả nhà: từng mã câu hỏi, luật ghép (đại diện mỗi tầng, độ tin gấp), và trùng khớp sáu bộ vàng."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from packages.domain.drawing_set import HouseProposal, HouseView, SectionView, solve_house
from packages.domain.drawing_set.heights import FloorLevel, StoreyHeight
from packages.domain.drawing_set.order import OrderConflict
from packages.domain.drawing_set.proposal import (
    CONFIDENT,
    ORDER_TEXT_CONFLICT,
    SINGLE_SOURCE,
    FloorProposal,
    _floor_values,
    _own_values,
    _propose,
    _representatives,
    _storey_questions,
    _Values,
    _with_height,
    unsure,
)
from packages.domain.drawing_set.storeys import PlanView, StoreyChoice, StoreyConflict
from packages.domain.drawing_set.tests.test_storeys import _PINNED
from packages.domain.drawing_set.titles import parse_title

_DATA = Path(__file__).parent / "data"
_GOLDENS = ("conflict", "english_basement", "mezzanine_no_marks", "no_level_marks", "plain3_titles", "tret_tang1")
_SIZE = (2000, 1200)
_CODES = {"STOREY_UNSURE", "STOREY_CONFLICT", "LEVEL_MISSING", "HEIGHT_ASSUMED", "NOT_A_PLAN", "NO_TEXT"}
_CLASH = StoreyConflict("title", "text", 3)


@dataclass(frozen=True)
class _Text:
    """Dòng OCR giả: đủ ba thuộc tính của `OcrText`."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float = 1.0


def _view(floor_id: str, title: str | None, *marks: str, position: int = 0, extra: int = 6) -> HouseView:
    """Hình thử: tên cao ở giữa dưới trang, các cốt, và vài chữ nhỏ làm nền trung vị."""
    texts = [_Text(f"P{i}", (100 + 90 * i, 100, 160 + 90 * i, 130)) for i in range(extra)]
    texts += [_Text(m, (100 + 200 * i, 300, 220 + 200 * i, 330)) for i, m in enumerate(marks)]
    if title is not None:
        texts.append(_Text(title, (700, 1000, 1300, 1080)))
    return HouseView(floor_id, texts, _SIZE, position)


def _by_id(proposal: HouseProposal) -> dict[str, FloorProposal]:
    return {f.floor_id: f for f in proposal.floors}


def _stack(*levels: str) -> list[HouseView]:
    """Các mặt bằng "TẦNG 1", "TẦNG 2"… mỗi hình một cốt cho trước (chuỗi rỗng = không cốt)."""
    return [_view(f"f{i}", f"MẶT BẰNG TẦNG {i + 1}", *([lv] if lv else []), position=i) for i, lv in enumerate(levels)]


def _band(confidence: float) -> int:
    """Dải độ chắc: 2 = >= 0,9; 1 = >= 0,7; 0 = thấp hơn."""
    return 2 if confidence >= 0.9 else 1 if confidence >= 0.7 else 0


# ---------------------------------------------------------------- trường hợp biên
def test_empty_set() -> None:
    assert solve_house([]) == HouseProposal(())


def test_duplicate_floor_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="floor_id"):
        solve_house([_view("a", "MẶT BẰNG TẦNG 1"), _view("a", "MẶT BẰNG TẦNG 2")])


def test_no_text_view_gets_no_proposal() -> None:
    got = solve_house([HouseView("x", [], _SIZE, 0)]).floors[0]
    assert (got.kind, got.kind_confidence, got.questions) == ("other", 0.0, ("NO_TEXT",))
    assert got.storey_no is None
    assert got.storey_confidence == 0.0
    assert (got.elevation_mm, got.height_mm) == (None, None)


def test_not_a_plan_in_the_middle_does_not_break_the_chain() -> None:
    views = [
        _view("f0", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("cut", "MẶT CẮT A-A", "+1.200", position=1),
        _view("f2", "MẶT BẰNG TẦNG 2", "+3.300", position=2),
    ]
    got = solve_house(views).floors
    cut = got[1]
    assert (cut.kind, cut.questions) == ("section", ("NOT_A_PLAN",))
    assert cut.kind_confidence >= 0.7
    assert (cut.storey_no, cut.storey_label, cut.storey_confidence) == (None, None, 0.0)
    assert (cut.elevation_mm, cut.elevation_confidence, cut.height_mm, cut.height_confidence) == (None,) * 4
    assert (got[0].storey_no, got[2].storey_no) == (1, 2)
    assert (got[0].elevation_mm, got[2].elevation_mm, got[0].height_mm) == (0, 3300, 3300)
    assert [f.floor_id for f in got] == ["f0", "cut", "f2"]


@pytest.mark.parametrize("title", ["MẶT ĐỨNG TRỤC 1-6", "MẶT BẰNG MÁI", "MẶT BẰNG TỔNG THỂ", "DETAIL A"])
def test_other_kinds_raise_not_a_plan(title: str) -> None:
    got = solve_house([_view("v", title)]).floors[0]
    assert got.questions == ("NOT_A_PLAN",)
    assert got.kind != "plan"


def test_weak_kind_guess_is_still_treated_as_plan() -> None:
    """Không tên, ít chữ: `view_kind` đoán "mặt đứng" 0,3 < 0,7 nên vẫn là mặt bằng đã tải lên (độ tin 0,5)."""
    got = solve_house([_view("v", None, extra=1)]).floors[0]
    assert (got.kind, got.kind_confidence) == ("plan", 0.5)
    assert got.questions == ("STOREY_UNSURE", "LEVEL_MISSING", "HEIGHT_ASSUMED")  # cốt 0 quy ước <= 0,7: hỏi (A-F4)


def test_plan_kind_confidence_comes_from_the_title_guess() -> None:
    got = solve_house(_stack("±0.000")).floors[0]
    assert got.kind == "plan"
    assert got.kind_confidence > 0.5  # tên "MẶT BẰNG" nói mặt bằng: lấy độ tin của `view_kind`, không phải mức mặc định


# ---------------------------------------------------------------- mã câu hỏi
def test_clean_two_storeys_ask_only_about_the_assumed_top_height() -> None:
    first, top = solve_house(_stack("±0.000", "+3.300")).floors
    assert first.questions == ()
    assert (first.elevation_mm, first.elevation_source) == (0, "plan")
    assert (first.height_mm, first.height_source) == (3300, "plan")
    assert first.elevation_confidence == pytest.approx(0.8)
    assert top.questions == ("HEIGHT_ASSUMED",)
    assert (top.elevation_mm, top.height_mm, top.height_confidence, top.height_source) == (3300, 3300, 0.4, "default")


def test_level_missing_and_assumed_height() -> None:
    first, second = solve_house(_stack("", "")).floors
    assert (first.elevation_mm, first.elevation_source) == (0, "convention")  # trệt không ghi cốt = 0.000 theo quy ước
    assert first.elevation_confidence == pytest.approx(0.6)
    assert first.questions == ("LEVEL_MISSING", "HEIGHT_ASSUMED")  # cốt quy ước <= 0,7 vẫn đề xuất nhưng hỏi (A-F4)
    assert second.elevation_mm is None
    assert (second.elevation_confidence, second.elevation_source) == (None, None)
    assert second.questions == ("LEVEL_MISSING", "HEIGHT_ASSUMED")


def test_title_sources_disagreeing_is_a_storey_conflict() -> None:
    """Tên dưới hình "TẦNG 1", khung tên "TẦNG 2": `pick_title` báo lệch (0,6) thì tầng bị chặn 0,65 và hỏi."""
    base = _view("a", "MAT BANG TANG 1", "+0.000")
    block = [_Text("TEN BAN VE", (100, 700, 300, 730)), _Text("MAT BANG TANG 2", (320, 700, 700, 730))]
    view = HouseView("a", [*base.texts, *block], _SIZE, 0)
    got = solve_house([view]).floors[0]
    assert got.storey_no == 1  # vẫn lấy tên dưới hình
    assert got.storey_confidence <= 0.65
    assert got.questions[0] == "STOREY_CONFLICT"
    assert "STOREY_UNSURE" not in got.questions
    assert got.elevation_confidence is not None
    assert got.elevation_confidence <= 0.65
    assert got.height_confidence is not None
    assert got.height_confidence <= 0.65


def test_height_built_on_the_ground_convention_is_flagged() -> None:
    """Trệt không ghi cốt: cốt 0 và chiều cao 3300 đều "convention"; cả hai vẫn đề xuất nhưng đều hỏi.

    Soát A-F4: giá trị giả định phải được báo; "Bản vẽ không ghi cao độ" đúng nghĩa với cốt quy ước.
    """
    first, second = solve_house(_stack("", "+3.300")).floors
    assert (first.elevation_mm, first.elevation_source) == (0, "convention")
    assert (first.height_mm, first.height_source) == (3300, "convention")
    assert first.questions == ("LEVEL_MISSING", "HEIGHT_ASSUMED")
    assert first.elevation_confidence is not None
    assert first.elevation_confidence < 0.7
    assert second.height_source == "default"


def test_height_out_of_product_range_is_not_proposed() -> None:
    first = solve_house(_stack("±0.000", "+12.000")).floors[0]
    assert (first.height_mm, first.height_confidence, first.height_source) == (None, None, None)
    assert first.elevation_mm == 0
    assert first.questions == ("LEVEL_MISSING", "HEIGHT_ASSUMED")  # cốt 0 bị hạ 0,6 vì khoảng 12 m vô lý: hỏi (rv2-5)


def test_height_spanning_a_missing_storey_is_flagged() -> None:
    views = [_view("a", "MẶT BẰNG TẦNG 1", "±0.000"), _view("b", "MẶT BẰNG TẦNG 3", "+6.600", position=2)]
    first = solve_house(views).floors[0]
    assert (first.height_mm, first.height_source) == (6600, "plan")
    assert first.height_confidence is not None
    assert first.height_confidence <= 0.65
    assert first.questions[-1] == "HEIGHT_ASSUMED"


def test_unknown_storey_is_unsure_and_has_no_level() -> None:
    """Bản vẽ điện không được đánh số tầng: không tầng, không cốt, không chiều cao."""
    views = [*_stack("±0.000"), _view("u", "MẶT BẰNG ĐIỆN TẦNG 1", position=1)]
    got = solve_house(views).floors[1]
    assert got.storey_no is None
    assert got.questions == ("STOREY_UNSURE", "LEVEL_MISSING")
    assert (got.elevation_mm, got.height_mm) == (None, None)


def test_mezzanine_has_a_level_but_no_height() -> None:
    views = [
        _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("m", "MẶT BẰNG LỬNG", "+2.800", "+2.800", position=1),
        _view("b", "MẶT BẰNG TẦNG 2", "+5.200", position=2),
    ]
    got = _by_id(solve_house(views))
    mezz = got["m"]
    assert (mezz.storey_label, mezz.storey_no) == ("mezzanine", None)
    assert (mezz.elevation_mm, mezz.height_mm) == (2800, None)
    assert mezz.questions == ("STOREY_UNSURE", "LEVEL_MISSING")  # cốt gấp độ tin lửng 0,6: hỏi (rv2-5)
    assert (got["a"].height_mm, got["b"].storey_no) == (5200, 2)  # lửng chia khoảng cao nhưng không cắt cặp tầng


def test_order_conflict_needs_known_order() -> None:
    """Thứ tự chưa biết: không bao giờ hỏi `ORDER_TEXT_CONFLICT`, dù tên hình đi ngược vị trí."""
    views = [_view("a", "MẶT BẰNG TẦNG 2", position=0), _view("b", "MẶT BẰNG TẦNG 1", position=1)]
    asked = {q for f in solve_house(views).floors for q in f.questions}
    assert ORDER_TEXT_CONFLICT not in asked
    assert asked <= _CODES


def test_order_known_matters_for_titles_without_numbers() -> None:
    views = [_view(f"v{i}", "MẶT BẰNG TẦNG", position=i) for i in range(2)]
    off = solve_house(views).floors  # mặc định order_known=False
    on = solve_house(views, order_known=True).floors
    # Hai hình cùng nhận một tầng thì `storeys` có thể báo bất đồng thay vì chưa chắc: cả hai đều là câu hỏi về tầng.
    assert all(f.storey_confidence < 0.7 and {"STOREY_UNSURE", "STOREY_CONFLICT"} & set(f.questions) for f in off)
    assert [f.storey_no for f in on] == [1, 2]
    assert on[0].storey_confidence >= off[0].storey_confidence
    assert solve_house(views, order_known=False) == solve_house(views)


# ---------------------------------------------------------------- nhiều hình cùng một tầng
def test_plain_variant_is_the_representative_over_more_marks() -> None:
    # Cốt hai bản cách nhau 0,1 m (<= 0,25): bản kia chép cốt của đại diện. Lệch hơn thì xem test hai nhà (A-F9).
    views = [
        _view("furn", "MẶT BẰNG NỘI THẤT TẦNG 2", "+3.400", "+3.400", "+3.400", position=0),
        _view("plain", "MẶT BẰNG TẦNG 2", "+3.300", position=1),
        _view("g", "MẶT BẰNG TẦNG 1", "±0.000", position=2),
    ]
    got = _by_id(solve_house(views))
    assert got["plain"].elevation_mm == got["furn"].elevation_mm == 3300
    assert got["furn"].storey_no == 2


def test_more_marks_then_lowest_position_pick_the_representative() -> None:
    more = [
        _view("a", "MẶT BẰNG TẦNG 2", "+3.300", position=1),
        _view("b", "MẶT BẰNG TẦNG 2", "+3.500", "+3.500", position=2),
        _view("g", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
    ]
    got = _by_id(solve_house(more))
    assert got["a"].elevation_mm == got["b"].elevation_mm == 3500
    tie = [
        _view("a", "MẶT BẰNG TẦNG 2", "+3.300", position=2),
        _view("b", "MẶT BẰNG TẦNG 2", "+3.500", position=1),
        _view("g", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
    ]
    got = _by_id(solve_house(tie))
    assert got["a"].elevation_mm == got["b"].elevation_mm == 3500


def test_titled_plan_beats_untitled_as_representative() -> None:
    """Soát A-F4: bản không tên nhiều cốt hơn không được làm đại diện thay bản có tên."""
    titled = PlanView("t", parse_title("MẶT BẰNG TẦNG 2"), (3.6,), 2)
    variant = PlanView("v", parse_title("MẶT BẰNG NỘI THẤT TẦNG 2"), (3.6, 3.6), 1)
    untitled = PlanView("u", None, (2.0, 2.0, 2.0), 0)
    choices = {p.view_id: StoreyChoice("floor", 2, 0.8, (), None, ()) for p in (titled, variant, untitled)}
    assert [p.view_id for p in _representatives([untitled, variant, titled], choices)] == ["t"]
    assert [p.view_id for p in _representatives([untitled, variant], choices)] == ["v"]


def test_two_houses_mixed_keep_their_own_levels_and_conflict() -> None:
    """Soát A-F9: bản "TẦNG 2" của nhà Y ghi +3.300 không chép 3600 của nhà X; hỏi bất đồng, không chiều cao."""
    views = [
        _view("x1", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("x2", "MẶT BẰNG TẦNG 2", "+3.600", position=1),
        _view("y1", "MẶT BẰNG TẦNG 1", "±0.000", position=2),
        _view("y2", "MẶT BẰNG TẦNG 2", "+3.300", position=3),
    ]
    got = _by_id(solve_house(views))
    y2 = got["y2"]
    assert (y2.storey_no, y2.elevation_mm, y2.elevation_source) == (2, 3300, "plan")
    assert y2.elevation_confidence is not None
    assert y2.elevation_confidence <= 0.65
    assert y2.storey_confidence <= 0.65
    assert (y2.height_mm, y2.height_confidence, y2.height_source) == (None, None, None)
    assert y2.questions[0] == "STOREY_CONFLICT"
    assert got["x2"].elevation_mm == 3600


def test_copy_without_own_level_takes_the_representative() -> None:
    rep = _Values(3300, 0.8, "plan", 3300, 0.8, "plan", ())
    bare = PlanView("b", None, (), 0)
    assert _own_values(bare, 0.9, rep) is rep
    assert _own_values(bare, 0.9, None) == _Values()
    near = PlanView("n", None, (3.5,), 0)
    assert _own_values(near, 0.9, rep) is rep  # cách 0,2 m <= 0,25 m
    far = PlanView("f", None, (3.6,), 0)
    assert _own_values(far, 0.9, rep) == _Values(3600, 0.65, "plan", questions=("LEVEL_MISSING",), level_conflict=True)
    no_rep_level = _Values()
    assert _own_values(far, 0.5, no_rep_level) == _Values(3600, 0.5, "plan", questions=("LEVEL_MISSING",))


def test_untitled_set_proposes_each_view_its_own_level() -> None:
    """Soát A-F11: không tên, cốt 0 / 3,6 / 7,2: không chép cốt 0 cho cả ba; không giá trị nào được tin."""
    views = [
        _view("a", None, "±0.000", "±0.000", position=0),
        _view("b", None, "+3.600", "+3.600", position=1),
        _view("c", None, "+7.200", "+7.200", position=2),
    ]
    for known in (False, True):
        got = solve_house(views, order_known=known).floors
        assert [f.elevation_mm for f in got] == [0, 3600, 7200]
        for floor in got:
            assert floor.elevation_confidence is not None
            assert floor.elevation_confidence <= floor.storey_confidence
            assert floor.elevation_confidence > 0.7 or floor.questions, floor.floor_id


def test_unknown_storey_with_marks_proposes_its_own_level() -> None:
    plan = PlanView("u", None, (3.3, 3.3), 0)
    assert _own_values(plan, 0.3, None) == _Values(3300, 0.3, "plan", questions=("LEVEL_MISSING",))


def test_implausible_height_is_shown_but_asked() -> None:
    """Soát A-F3/F4: 2,1 m một tầng vẫn đề xuất (lửng thấp/tầng hầm có thể) nhưng <= 0,65 và hỏi."""
    first = solve_house(_stack("±0.000", "+2.100")).floors[0]
    assert (first.height_mm, first.height_source) == (2100, "plan")
    assert first.height_confidence is not None
    assert first.height_confidence <= 0.65
    assert "HEIGHT_ASSUMED" in first.questions


def test_implausible_flag_caps_height_from_any_solver() -> None:
    base = _Values(0, 0.8, "plan", questions=())
    height = StoreyHeight(1, 2, 3.0, "plan", 0.85, ("implausible",))
    got = _with_height(base, height, 3000, {1: 0.95, 2: 0.95})
    assert (got.height_mm, got.height_confidence, got.questions) == (3000, 0.65, ("HEIGHT_ASSUMED",))


def test_two_mm_levels_on_one_view_are_not_trusted() -> None:
    """Soát A-F15: "+3600" và "+3300" trên một hình: cốt nhập nhằng không quá 0,65. Từ vựng chưa có mã
    "hai cốt có thể" nên hỏi `LEVEL_MISSING`, mã gần nhất: giá trị <= 0,70 không đứng một mình (rv2-5)."""
    views = [_view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0), _view("b", "MẶT BẰNG TẦNG 2", "+3600", "+3300")]
    second = _by_id(solve_house(views))["b"]
    assert second.elevation_mm in (3300, 3600)
    assert second.elevation_confidence is not None
    assert second.elevation_confidence <= 0.65
    assert "LEVEL_MISSING" in second.questions
    ambiguous = FloorLevel(3.3, "plan", 0.7, True, (3.6,))
    assert _floor_values(ambiguous, 3300, 0.95).elevation_confidence == 0.65


# ---------------------------------------------------------------- ranh giới 0,70 (soát A-F4, Codex 6)
@pytest.mark.parametrize(
    ("confidence", "asked"),
    [(0.5, True), (0.69, True), (0.7, True), (0.7 + 1e-12, True), (0.701, False), (0.71, False), (0.9, False)],
)
def test_band_boundary_sweep(confidence: float, asked: bool) -> None:
    """Độ tin đúng 0,70 trở xuống không bao giờ đứng một mình không câu hỏi, bất kể bộ giải trả gì."""
    assert unsure(confidence) is asked
    choice = StoreyChoice("floor", 2, confidence, (), None, ())
    assert ("STOREY_UNSURE" in _storey_questions(choice)) is asked
    assert _storey_questions(StoreyChoice("floor", 2, confidence, (), None, (_CLASH,))) == ("STOREY_CONFLICT",)
    for source in ("convention", "plan"):  # nguồn nào cũng vậy (rv2-5)
        level = _floor_values(FloorLevel(0.0, source, confidence), 0, 0.95)
        assert ("LEVEL_MISSING" in level.questions) is asked
    height = _with_height(_Values(questions=()), StoreyHeight(1, 2, 3.0, "plan", confidence, ()), 3000, {1: 1, 2: 1})
    assert ("HEIGHT_ASSUMED" in height.questions) is asked
    assert SINGLE_SOURCE == 0.7
    assert CONFIDENT == 0.9


def test_lone_terrace_sheet_is_unsure() -> None:
    """Codex 6: chỉ một tờ "SÂN THƯỢNG": tầng giả định (<= 0,7) phải hỏi."""
    got = solve_house([_view("a", "MẶT BẰNG SÂN THƯỢNG")]).floors[0]
    assert got.storey_confidence <= 0.7
    assert "STOREY_UNSURE" in got.questions or "STOREY_CONFLICT" in got.questions


def test_no_value_at_or_below_the_boundary_stands_without_a_question() -> None:
    """Lời hứa sản phẩm trên các bộ thử: tầng, cốt, chiều cao <= 0,70 luôn kèm câu hỏi về chính trường đó."""
    sets = [
        [
            _view("a", "MẶT BẰNG TẦNG 1", "±0.000"),
            _view("m", None, "+2.000", "+2.000", "+2.000", position=1),
            _view("b", "MẶT BẰNG TẦNG 2", "+3.600", position=2),
        ],
        [_view("a", "MẶT BẰNG SÂN THƯỢNG")],
        [_view(f"v{i}", "MẶT BẰNG TẦNG", position=i) for i in range(3)],
        _stack("±0.000", "+3.300", "+3.500"),
        _stack("", "+2.100", "+12.000"),
        _STACKED_READS,
        _SAME_STOREY_CLASH,
    ]
    for views in sets:
        for floor in solve_house(views).floors:
            if floor.storey_no is not None and unsure(floor.storey_confidence):
                assert {"STOREY_UNSURE", "STOREY_CONFLICT"} & set(floor.questions), floor.floor_id
            if floor.elevation_confidence is not None and unsure(floor.elevation_confidence):
                assert "LEVEL_MISSING" in floor.questions, floor.floor_id
            if floor.height_confidence is not None and unsure(floor.height_confidence):
                assert "HEIGHT_ASSUMED" in floor.questions, floor.floor_id


# Soát rv2-3: bản nội thất cũng ghi "TẦNG 2" nhưng cốt +6.600 lệch +3.300 của bản kiến trúc tầng 2.
_SAME_STOREY_CLASH = [
    _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
    _view("b", "MẶT BẰNG TẦNG 2", "+3.300", position=1),
    _view("c", "MẶT BẰNG NỘI THẤT TẦNG 2", "+6.600", position=2),
    _view("d", "MẶT BẰNG TẦNG 3", "+6.600", position=3),
]
# Soát rv2-5: cốt nhập nhằng ở tầng 2, tầng 3 ghi lặp cốt tầng 2: các giá trị đọc được đều <= 0,70.
_STACKED_READS = [
    _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
    _view("b", "MẶT BẰNG TẦNG 2", "+3.300", "+3.500", position=1),
    _view("c", "MẶT BẰNG TẦNG 3", "+3.300", "+3.300", "+6.600", position=2),
]


def test_same_storey_level_clash_flags_both_views_and_dependent_heights() -> None:
    """Soát rv2-3: hai hình cùng "TẦNG 2" mà cốt lệch nhau: cả đại diện lẫn hình kia đều bị chặn 0,65 và
    hỏi bất đồng; cốt đại diện và hai chiều cao nối tầng 2 cũng không quá 0,65 và hỏi."""
    got = _by_id(solve_house(_SAME_STOREY_CLASH))
    for fid in ("b", "c"):
        assert got[fid].storey_no == 2
        assert got[fid].storey_confidence <= 0.65
        assert got[fid].questions[0] == "STOREY_CONFLICT", fid
    rep = got["b"]
    assert (rep.elevation_mm, rep.height_mm) == (3300, 3300)
    assert rep.elevation_confidence is not None
    assert rep.elevation_confidence <= 0.65
    assert rep.height_confidence is not None
    assert rep.height_confidence <= 0.65
    below = got["a"]  # chiều cao 1 -> 2 dựa trên cốt tầng 2 đang tranh chấp
    assert below.height_confidence is not None
    assert below.height_confidence <= 0.65
    assert "HEIGHT_ASSUMED" in below.questions
    assert "STOREY_CONFLICT" not in got["a"].questions + got["d"].questions


def test_low_confidence_read_values_always_ask() -> None:
    """Soát rv2-5: cốt/chiều cao đọc từ chữ (không giả định, không cờ) mà <= 0,70 vẫn phải hỏi."""
    got = _by_id(solve_house(_STACKED_READS))
    first, second = got["a"], got["b"]
    assert first.height_confidence is not None
    assert first.height_confidence <= 0.7
    assert "HEIGHT_ASSUMED" in first.questions
    assert second.storey_confidence > 0.7
    assert second.elevation_confidence is not None
    assert second.elevation_confidence <= 0.65
    assert second.questions == ("LEVEL_MISSING", "HEIGHT_ASSUMED")


def test_copies_keep_their_own_storey_confidence_but_share_values() -> None:
    views = [*_stack("±0.000", "+3.300"), _view("dup", "MẶT BẰNG TẦNG 2", "+3.300", position=5)]
    got = _by_id(solve_house(views))
    assert got["dup"].storey_no == 2
    original, copy = got["f1"], got["dup"]
    assert (copy.elevation_mm, copy.elevation_confidence, copy.height_mm, copy.height_confidence) == (
        original.elevation_mm,
        original.elevation_confidence,
        original.height_mm,
        original.height_confidence,
    )
    assert copy.questions == original.questions


# ---------------------------------------------------------------- sáu bộ vàng
def _golden_views(golden: dict[str, Any]) -> list[HouseView]:
    """Mọi hình của mẫu như đầu vào sản phẩm: chỉ chữ OCR, kích thước và thứ tự."""
    return [
        HouseView(v["id"], [_Text(t["text"], tuple(t["box"])) for t in v["texts"]], tuple(v["size"]), v["order"])
        for v in golden["input"]["views"]
    ]


def _check_storeys(got: dict[str, FloorProposal], known: dict[str, Any]) -> None:
    """Số tầng và dải độ chắc từng hình khớp mẫu (hình mà nhân gọi lửng/mái không số: không số)."""
    for exp in known["views"]:
        floor = got[exp["id"]]
        assert floor.storey_no == exp["floor_no"], exp["id"]
        if "NOT_A_PLAN" not in floor.questions:
            assert floor.kind == "plan", exp["id"]
        if exp["id"] in _PINNED:
            # Hình `storeys` cố ý khác nhân (soát mã F14, F2): ghim bằng đẳng thức theo `test_storeys._PINNED`.
            confidence, conflicts = _PINNED[exp["id"]]
            assert floor.storey_confidence == confidence, exp["id"]
            assert ("STOREY_CONFLICT" in floor.questions) is bool(conflicts), exp["id"]
            continue
        assert _band(floor.storey_confidence) == _band(exp.get("floor_confidence") or 0.0), exp["id"]


def _check_levels(got: dict[str, FloorProposal], known: dict[str, Any]) -> None:
    """Cốt: giá trị trong 0,01 m, nguồn, và dải độ chắc = `confidence` của mẫu (đã gấp độ chắc tầng)."""
    for exp in known["floors"]:
        floor = got[exp["view_id"]]
        if exp["level_m"] is None:
            assert floor.elevation_mm is None, exp["view_id"]
            assert "LEVEL_MISSING" in floor.questions
            continue
        assert floor.elevation_mm is not None
        assert floor.elevation_mm / 1000 == pytest.approx(exp["level_m"], abs=0.01), exp["view_id"]
        assert floor.elevation_source == exp["level_source"]
        assert floor.elevation_confidence is not None
        expected = exp["confidence"]
        if exp["view_id"] in _PINNED:
            # Độ tin tầng đã ghim khác mẫu: cốt nằm giữa mẫu gấp độ tin ghim và chính độ tin ghim (luật min).
            pinned = _PINNED[exp["view_id"]][0]
            assert min(expected, pinned) <= floor.elevation_confidence <= pinned, exp["view_id"]
            continue
        if exp["floor_no"] is None:
            # Lửng: mẫu giữ 0,7 của cốt; ở đây gấp thêm độ chắc tầng 0,6 của lửng theo luật min.
            expected = min(expected, floor.storey_confidence)
        assert _band(floor.elevation_confidence) == _band(expected), exp["view_id"]


def _check_heights(got: dict[str, FloorProposal], known: dict[str, Any]) -> None:
    """Chiều cao: giá trị, nguồn, dải độ chắc bằng đúng của mẫu; tầng trên cùng luôn 3,3 m ở 0,4 và hỏi."""
    by_no = {f["floor_no"]: got[f["view_id"]] for f in known["floors"] if f["floor_no"] is not None}
    pinned = {f["floor_no"]: _PINNED[f["view_id"]][0] for f in known["floors"] if f["view_id"] in _PINNED}
    for exp in known["storey_heights"]:
        floor = by_no[exp["from"]]
        assert floor.height_mm is not None
        assert floor.height_mm / 1000 == pytest.approx(exp["height_m"], abs=0.01)
        assert floor.height_source == exp["source"]
        assert floor.height_confidence is not None
        ends = [pinned[n] for n in (exp["from"], exp["to"]) if n in pinned]
        if ends:
            # Một đầu có độ tin tầng ghim khác mẫu: chiều cao nằm giữa mẫu gấp độ tin ghim và độ tin ghim.
            low = min(exp["confidence"], *ends)
            assert low <= floor.height_confidence <= min(ends), (exp["from"], exp["to"])
            continue
        assert _band(floor.height_confidence) == _band(exp["confidence"]), (exp["from"], exp["to"])
    top = by_no[max(by_no)]
    assert (top.height_mm, top.height_confidence, top.height_source) == (3300, 0.4, "default")
    assert "HEIGHT_ASSUMED" in top.questions


@pytest.mark.parametrize("name", _GOLDENS)
def test_golden_parity(name: str) -> None:
    golden = json.loads((_DATA / f"{name}.json").read_text(encoding="utf-8"))
    proposal = _propose(_golden_views(golden), cues=True, follow=False)  # bộ nghiên cứu: nhiều hình một tầng
    got = _by_id(proposal)
    known = golden["expected_known"]
    assert [f.floor_id for f in proposal.floors] == [v["id"] for v in golden["input"]["views"]]
    _check_storeys(got, known)
    _check_levels(got, known)
    _check_heights(got, known)


def test_goldens_cover_conflict_unsure_missing_assumed_and_not_a_plan() -> None:
    asked: set[str] = set()
    for name in _GOLDENS:
        golden = json.loads((_DATA / f"{name}.json").read_text(encoding="utf-8"))
        floors = _propose(_golden_views(golden), cues=True, follow=False).floors
        asked |= {q for f in floors for q in f.questions}
    assert asked == _CODES - {"NO_TEXT"}


# ---------------------------------------------------------------- thứ tự tầng của dự án (HS-03)
def test_order_known_text_agrees() -> None:
    """Tên hình khớp thứ tự dự án: số giữ nguyên, hai nguồn cùng ý nên tầng đạt 0,95, không câu hỏi về thứ tự."""
    got = solve_house(_stack("±0.000", "+3.300", "+6.600"), order_known=True).floors
    assert [f.storey_no for f in got] == [1, 2, 3]
    assert [f.storey_label for f in got] == ["ground", "floor", "floor"]
    assert all(f.storey_confidence >= CONFIDENT and f.order_conflict is None for f in got)
    assert not {ORDER_TEXT_CONFLICT, "STOREY_UNSURE", "STOREY_CONFLICT"} & {q for f in got for q in f.questions}


def test_order_known_text_conflicts() -> None:
    """Tên hình ngược thứ tự dự án: giữ thứ tự, hỏi kèm lý do và số chữ ghi; cốt ngược thứ tự: lý do "level"."""
    views = [
        _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("b", "MẶT BẰNG TẦNG 3", "+3.300", position=1),
        _view("c", "MẶT BẰNG TẦNG 2", "+6.600", position=2),
        _view("d", "MẶT BẰNG TẦNG 4", "+9.900", position=3),
    ]
    got = _by_id(solve_house(views, order_known=True))
    assert [got[k].storey_no for k in "abcd"] == [1, 2, 3, 4]
    assert (got["b"].order_conflict, got["c"].order_conflict) == (OrderConflict("title", 3), OrderConflict("title", 2))
    assert all(got[k].questions[0] == ORDER_TEXT_CONFLICT and got[k].storey_confidence <= 0.65 for k in "bc")
    assert got["a"].order_conflict is None
    assert got["d"].order_conflict is None
    by_level = [
        _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("b", "MẶT BẰNG TẦNG 2", "+6.600", "+6.600", position=1),
        _view("c", "MẶT BẰNG TẦNG 3", "+3.300", "+3.300", position=2),
    ]
    got = _by_id(solve_house(by_level, order_known=True))
    assert [got[k].storey_no for k in "abc"] == [1, 2, 3]
    assert {got[k].order_conflict for k in "bc"} == {OrderConflict("level")}
    assert all(ORDER_TEXT_CONFLICT in got[k].questions and got[k].storey_confidence <= 0.65 for k in "bc")


def test_order_known_project_with_mezzanine() -> None:
    """Lửng là một sàn riêng không số; tầng sau nó đếm tiếp. Sàn không có chữ vẫn chiếm một chỗ trong thứ tự."""
    views = [
        _view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
        _view("m", "MẶT BẰNG LỬNG", "+2.800", "+2.800", position=1),
        _view("b", "MẶT BẰNG TẦNG", position=2),
        HouseView("x", (), _SIZE, 3),
        _view("t", "MẶT BẰNG TUM", "+12.200", position=4),
    ]
    got = _by_id(solve_house(views, order_known=True))
    assert (got["m"].storey_label, got["m"].storey_no) == ("mezzanine", None)
    assert [got[k].storey_no for k in "abt"] == [1, 2, 4]  # "x" không chữ giữ chỗ tầng 3
    assert (got["t"].storey_label, got["x"].questions) == ("tum", ("NO_TEXT",))
    assert not any(f.order_conflict for f in got.values())
    assert 0.7 < got["b"].storey_confidence < CONFIDENT  # chỉ suy từ thứ tự: một nguồn


# ---------------------------------------------------------------- mặt cắt của dự án (HS-03)
def _section(*marks: tuple[str, float]) -> SectionView:
    """Mặt cắt thử: mỗi cốt một dòng chữ ở cao độ px cho trước (cốt cao hơn nằm cao hơn trên trang)."""
    return SectionView([_Text(text, (1500, y - 15, 1620, y + 15)) for text, y in marks], _SIZE)


_SECTION = _section(("±0.000", 1000.0), ("+3.300", 670.0), ("+6.600", 340.0), ("+9.900", 10.0))


def test_sections_make_levels_and_heights_two_source() -> None:
    """Mặt bằng và mặt cắt cùng ý, thứ tự dự án khớp tên hình: cốt và chiều cao đạt >= 0,9, nguồn "plan+section"."""
    views = _stack("±0.000", "+3.300", "+6.600")
    alone = solve_house(views, order_known=True).floors
    assert all(f.height_confidence is not None and f.height_confidence < CONFIDENT for f in alone)
    got = solve_house(views, order_known=True, sections=[_SECTION, _SECTION]).floors
    assert [f.elevation_mm for f in got] == [0, 3300, 6600]
    assert [(f.elevation_source, f.elevation_confidence) for f in got] == [("plan+section", 0.93)] * 3
    assert [(f.height_mm, f.height_source, f.height_confidence) for f in got[:2]] == [(3300, "plan+section", 0.93)] * 2
    assert not got[0].questions
    assert not got[1].questions


def test_sections_fill_a_plan_without_level_marks() -> None:
    """Mặt bằng không ghi cốt: cốt lấy từ bậc thang của mặt cắt (một nguồn, dưới 0,9)."""
    views = _stack("±0.000", "", "+6.600")
    got = solve_house(views, order_known=True, sections=[_SECTION, _SECTION]).floors
    assert (got[1].elevation_mm, got[1].elevation_source) == (3300, "section")
    assert got[1].elevation_confidence is not None
    assert 0.7 < got[1].elevation_confidence < CONFIDENT


def test_unreadable_section_changes_nothing() -> None:
    """Mặt cắt chỉ có một cốt (không khớp dọc được) hay không có chữ: kết quả như khi không có mặt cắt."""
    views = _stack("±0.000", "+3.300")
    poor = [_section(("+3.300", 670.0)), SectionView((), _SIZE)]
    assert solve_house(views, order_known=True, sections=poor) == solve_house(views, order_known=True)


def test_section_rung_stands_in_for_a_storey_without_a_drawing() -> None:
    """Dự án thiếu bản vẽ tầng 2: chiều cao tầng 1 lấy tới bậc thang 3,3 m, nguồn mặt cắt, chưa chắc nên hỏi."""
    views = [_view("a", "MẶT BẰNG TẦNG 1", "±0.000", position=0), _view("c", "MẶT BẰNG TẦNG 3", "+6.600", position=1)]
    got = _by_id(solve_house(views, order_known=True, sections=[_SECTION, _SECTION]))
    assert (got["a"].height_mm, got["a"].height_source) == (3300, "section")
    assert got["a"].height_confidence is not None
    assert got["a"].height_confidence <= 0.6
    assert "HEIGHT_ASSUMED" in got["a"].questions
