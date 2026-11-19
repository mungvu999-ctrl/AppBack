"""Đề xuất cho cả nhà: từng mã câu hỏi, luật ghép (đại diện mỗi tầng, độ tin gấp), và trùng khớp sáu bộ vàng."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from packages.domain.drawing_set import HouseProposal, HouseView, solve_house
from packages.domain.drawing_set.proposal import ORDER_DIFFERS, FloorProposal
from packages.domain.drawing_set.tests.test_storeys import _own_words_conflict

_DATA = Path(__file__).parent / "data"
_GOLDENS = ("conflict", "english_basement", "mezzanine_no_marks", "no_level_marks", "plain3_titles", "tret_tang1")
_SIZE = (2000, 1200)
_CODES = {"STOREY_UNSURE", "STOREY_CONFLICT", "LEVEL_MISSING", "HEIGHT_ASSUMED", "NOT_A_PLAN", "NO_TEXT"}


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
    assert got.questions == ("STOREY_UNSURE", "HEIGHT_ASSUMED")


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
    assert first.questions == ("HEIGHT_ASSUMED",)
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
    """Trệt không ghi cốt: cốt 0 và chiều cao 3300 đều "convention"; chiều cao phải hỏi, cốt giữ độ tin thấp."""
    first, second = solve_house(_stack("", "+3.300")).floors
    assert (first.elevation_mm, first.elevation_source) == (0, "convention")
    assert (first.height_mm, first.height_source) == (3300, "convention")
    assert first.questions == ("HEIGHT_ASSUMED",)
    assert first.elevation_confidence is not None
    assert first.elevation_confidence < 0.7  # chưa có mã "cốt này là giả định": giữ đề xuất với độ tin thấp
    assert "LEVEL_MISSING" not in first.questions
    assert second.height_source == "default"


def test_height_out_of_product_range_is_not_proposed() -> None:
    first = solve_house(_stack("±0.000", "+12.000")).floors[0]
    assert (first.height_mm, first.height_confidence, first.height_source) == (None, None, None)
    assert first.elevation_mm == 0
    assert first.questions == ("HEIGHT_ASSUMED",)


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
    assert mezz.questions == ("STOREY_UNSURE",)
    assert (got["a"].height_mm, got["b"].storey_no) == (5200, 2)  # lửng chia khoảng cao nhưng không cắt cặp tầng


def test_order_differs_is_never_raised_here() -> None:
    assert ORDER_DIFFERS == "ORDER_DIFFERS"
    views = [_view(f"v{i}", "MẶT BẰNG TẦNG", position=i) for i in range(3)] + _stack("±0.000", "+3.300")
    for known in (False, True):
        asked = {q for f in solve_house(views, order_known=known).floors for q in f.questions}
        assert ORDER_DIFFERS not in asked
        assert asked <= _CODES


def test_order_known_matters_for_titles_without_numbers() -> None:
    views = [_view(f"v{i}", "MẶT BẰNG TẦNG", position=i) for i in range(2)]
    off = solve_house(views).floors  # mặc định order_known=False
    on = solve_house(views, order_known=True).floors
    assert all(f.storey_confidence < 0.7 and "STOREY_UNSURE" in f.questions for f in off)
    assert [f.storey_no for f in on] == [1, 2]
    assert on[0].storey_confidence >= off[0].storey_confidence
    assert solve_house(views, order_known=False) == solve_house(views)


# ---------------------------------------------------------------- nhiều hình cùng một tầng
def test_plain_variant_is_the_representative_over_more_marks() -> None:
    views = [
        _view("furn", "MẶT BẰNG NỘI THẤT TẦNG 2", "+3.600", "+3.600", "+3.600", position=0),
        _view("plain", "MẶT BẰNG TẦNG 2", "+3.300", position=1),
        _view("g", "MẶT BẰNG TẦNG 1", "±0.000", position=2),
    ]
    got = _by_id(solve_house(views))
    assert got["plain"].elevation_mm == got["furn"].elevation_mm == 3300
    assert got["furn"].storey_no == 2


def test_more_marks_then_lowest_position_pick_the_representative() -> None:
    more = [
        _view("a", "MẶT BẰNG TẦNG 2", "+3.300", position=1),
        _view("b", "MẶT BẰNG TẦNG 2", "+3.600", "+3.600", position=2),
        _view("g", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
    ]
    got = _by_id(solve_house(more))
    assert got["a"].elevation_mm == got["b"].elevation_mm == 3600
    tie = [
        _view("a", "MẶT BẰNG TẦNG 2", "+3.300", position=2),
        _view("b", "MẶT BẰNG TẦNG 2", "+3.600", position=1),
        _view("g", "MẶT BẰNG TẦNG 1", "±0.000", position=0),
    ]
    got = _by_id(solve_house(tie))
    assert got["a"].elevation_mm == got["b"].elevation_mm == 3600


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
        if _own_words_conflict(exp):
            assert floor.storey_confidence >= 0.85, exp["id"]
            assert "STOREY_CONFLICT" not in floor.questions, exp["id"]
            continue
        assert _band(floor.storey_confidence) == _band(exp.get("floor_confidence") or 0.0), exp["id"]


def _check_levels(got: dict[str, FloorProposal], known: dict[str, Any]) -> None:
    """Cốt: giá trị trong 0,01 m, nguồn, và dải độ chắc = `confidence` của mẫu (đã gấp độ chắc tầng)."""
    fixed = {v["id"] for v in known["views"] if _own_words_conflict(v)}
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
        if exp["view_id"] in fixed:
            # Mẫu gấp độ chắc tầng 0,65 của bất đồng giả (soát mã, lỗi 1); ở đây tầng chắc hơn nên cốt không thấp hơn.
            assert floor.elevation_confidence >= expected, exp["view_id"]
            continue
        if exp["floor_no"] is None:
            # Lửng: mẫu giữ 0,7 của cốt; ở đây gấp thêm độ chắc tầng 0,6 của lửng theo luật min.
            expected = min(expected, floor.storey_confidence)
        assert _band(floor.elevation_confidence) == _band(expected), exp["view_id"]


def _check_heights(got: dict[str, FloorProposal], known: dict[str, Any]) -> None:
    """Chiều cao: giá trị, nguồn, dải độ chắc bằng đúng của mẫu; tầng trên cùng luôn 3,3 m ở 0,4 và hỏi."""
    by_no = {f["floor_no"]: got[f["view_id"]] for f in known["floors"] if f["floor_no"] is not None}
    wrong = {v["id"] for v in known["views"] if _own_words_conflict(v)}
    fixed = {f["floor_no"] for f in known["floors"] if f["view_id"] in wrong}
    for exp in known["storey_heights"]:
        floor = by_no[exp["from"]]
        assert floor.height_mm is not None
        assert floor.height_mm / 1000 == pytest.approx(exp["height_m"], abs=0.01)
        assert floor.height_source == exp["source"]
        assert floor.height_confidence is not None
        if {exp["from"], exp["to"]} & fixed:
            # Mẫu gấp độ chắc 0,65 của bất đồng giả ở một đầu (soát mã, lỗi 1): ở đây không thấp hơn mẫu.
            assert floor.height_confidence >= exp["confidence"], (exp["from"], exp["to"])
            continue
        assert _band(floor.height_confidence) == _band(exp["confidence"]), (exp["from"], exp["to"])
    top = by_no[max(by_no)]
    assert (top.height_mm, top.height_confidence, top.height_source) == (3300, 0.4, "default")
    assert "HEIGHT_ASSUMED" in top.questions


@pytest.mark.parametrize("name", _GOLDENS)
def test_golden_parity(name: str) -> None:
    golden = json.loads((_DATA / f"{name}.json").read_text(encoding="utf-8"))
    proposal = solve_house(_golden_views(golden), order_known=True)
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
        asked |= {q for f in solve_house(_golden_views(golden), order_known=True).floors for q in f.questions}
    assert asked == _CODES - {"NO_TEXT"}
