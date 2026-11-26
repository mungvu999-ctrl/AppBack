"""Cốt sàn và chiều cao tầng chỉ từ mặt bằng: các `selfcheck` của `heights3.py`/`solve_set.py` không cần
thang cốt, từng luật của `heights.py`, và trùng khớp với sáu mẫu vàng (khối `expected_known`)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from packages.domain.drawing_set.heights import (
    DbValue,
    FloorLevel,
    StoreyHeight,
    StoreyPlan,
    _sunk_rung,
    solve_heights,
    to_db_ranges,
)
from packages.domain.drawing_set.levels import level_groups, levels_from_texts

_DATA = Path(__file__).parent / "data"
_GOLDENS = ("conflict", "english_basement", "mezzanine_no_marks", "no_level_marks", "plain3_titles", "tret_tang1")


def _plans(*marks: list[float]) -> list[StoreyPlan]:
    """Mặt bằng số 1, 2, 3… với cốt cho sẵn."""
    return [StoreyPlan(i + 1, tuple(m)) for i, m in enumerate(marks)]


def _levels(floors: tuple[FloorLevel, ...]) -> list[float | None]:
    return [f.level_m for f in floors]


def _band(confidence: float) -> int:
    """Dải độ chắc: 2 = ≥ 0,9; 1 = ≥ 0,7; 0 = thấp hơn."""
    return 2 if confidence >= 0.9 else 1 if confidence >= 0.7 else 0


def test_ground_rule_beats_frequent_yard_mark() -> None:
    floors, heights = solve_heights(_plans([-0.45] * 3 + [0.0] * 2, [3.6] * 4))
    assert floors[0] == FloorLevel(0.0, "plan", 0.8)
    assert heights[0] == StoreyHeight(1, 2, 3.6, "plan", 0.8)


def test_no_marks_at_all_gives_convention_and_defaults() -> None:
    floors, heights = solve_heights(_plans([], [], []))
    assert floors[0] == FloorLevel(0.0, "convention", 0.6)
    assert floors[1:] == (FloorLevel(None, None, 0.5),) * 2
    assert [(h.height_m, h.source, h.confidence, h.flags) for h in heights] == [(3.3, "default", 0.4, ("assumed",))] * 3
    assert heights[-1].to_number is None


def test_convention_level_makes_convention_height() -> None:
    floors, heights = solve_heights(_plans([], [3.3, 3.3]))
    assert _levels(floors) == [0.0, 3.3]
    assert heights[0] == StoreyHeight(1, 2, 3.3, "convention", 0.6)


def test_plausible_chain_without_section_stays_below_09() -> None:
    floors, heights = solve_heights(_plans([0.0] * 3, [3.3], [6.6]))
    assert _levels(floors) == [0.0, 3.3, 6.6]
    assert all(f.confidence == 0.8 for f in floors)
    assert max(h.confidence for h in heights) < 0.9


def test_implausible_gap_lowers_confidence() -> None:
    floors, heights = solve_heights(_plans([0.0], [6.0]))
    assert [(f.level_m, f.confidence) for f in floors] == [(0.0, 0.6), (6.0, 0.6)]
    assert heights[0].height_m == 6.0
    assert heights[0].confidence == 0.6
    assert heights[0].flags == ("implausible",)  # soát A-F3: lớp trên phải hỏi, không chỉ hạ độ tin


@pytest.mark.parametrize(
    ("upper", "flagged"), [(2.0, True), (2.39, True), (2.4, False), (3.3, False), (4.6, False), (4.61, True)]
)
def test_implausible_flag_band_per_storey(upper: float, flagged: bool) -> None:
    """Ngoài 2,4-4,6 m mỗi tầng thì gắn cờ nhưng vẫn giữ giá trị (lửng thấp/tầng hầm 2,0-2,4 m vẫn có thể đúng)."""
    _, heights = solve_heights(_plans([0.0], [upper]))
    assert heights[0].height_m == upper
    assert ("implausible" in heights[0].flags) is flagged


def test_implausible_flag_counts_storeys_spanned() -> None:
    _, heights = solve_heights([StoreyPlan(1, (0.0,)), StoreyPlan(3, (7.2,))])
    assert heights[0].flags == ("spans_missing_storey",)  # 3,6 m mỗi tầng: hợp lý


def test_ambiguous_split_three_to_two() -> None:
    floors, _ = solve_heights(_plans([0.0], [3.3] * 3 + [3.6] * 2, [6.9]))
    assert floors[1] == FloorLevel(3.3, "plan", 0.7, True, (3.6,))
    assert not floors[0].ambiguous


def test_main_finished_floor_beats_sunken_wet_area() -> None:
    floors, _ = solve_heights(_plans([0.0], [3.55] * 3 + [3.6]))
    assert _levels(floors) == [0.0, 3.6]


def test_raised_ground_keeps_its_own_mark() -> None:
    floors, heights = solve_heights(_plans([1.3] * 3 + [0.9], [4.6] * 2, [7.9]))
    assert _levels(floors) == [1.3, 4.6, 7.9]
    assert heights[0].height_m == 3.3


def test_raised_mark_seen_once_or_with_own_zero_keeps_zero() -> None:
    assert _levels(solve_heights(_plans([1.3], [3.3]))[0])[0] == 0.0
    assert _levels(solve_heights(_plans([0.0, 1.3, 1.3], [3.3]))[0])[0] == 0.0


def test_basement_below_ground() -> None:
    plans = [StoreyPlan(0, (-3.0,)), StoreyPlan(1, (0.0,)), StoreyPlan(2, (3.0,))]
    floors, heights = solve_heights(plans)
    assert _levels(floors) == [-3.0, 0.0, 3.0]
    assert (heights[0].from_number, heights[0].to_number, heights[0].height_m) == (0, 1, 3.0)


def test_basement_misread_zero_is_unknown() -> None:
    plans = [StoreyPlan(0, (0.0,)), StoreyPlan(1, (0.0, 0.1, 1.6)), StoreyPlan(2, (4.4,))]
    floors, heights = solve_heights(plans)
    assert _levels(floors) == [None, 0.0, 4.4]
    assert heights[0].flags == ("assumed",)
    assert heights[0].confidence == 0.4


def test_mezzanine_is_skipped_and_explains_tall_gap() -> None:
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(None, (2.8, 2.8)), StoreyPlan(2, (5.2,)), StoreyPlan(3, (8.4,))]
    floors, heights = solve_heights(plans)
    assert floors[1] == FloorLevel(2.8, "plan", 0.7)
    assert floors[2] == FloorLevel(5.2, "plan", 0.8)  # 5,2 m vô lý cho một tầng, nhưng lửng 2,8 chia đôi
    assert heights[0] == StoreyHeight(1, 2, 5.2, "plan", 0.8, ("skips_mezzanine",))
    assert heights[1].flags == ()


def test_mezzanine_level_outside_gap_is_not_skipped() -> None:
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(2, (3.3,)), StoreyPlan(None, (4.2,)), StoreyPlan(3, (6.6,))]
    _, heights = solve_heights(plans)
    assert heights[0].flags == ()
    assert heights[1].flags == ("skips_mezzanine",)
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(None, (0.2,)), StoreyPlan(2, (3.3,))]
    assert solve_heights(plans)[1][0].flags == ()


def test_mezzanine_without_marks_is_skipped_by_position() -> None:
    floors, heights = solve_heights([StoreyPlan(1, (0.0,)), StoreyPlan(None, ()), StoreyPlan(2, (3.3,))])
    assert floors[1] == FloorLevel(None, None, 0.5)
    assert heights[0].flags == ("skips_mezzanine",)


def test_missing_storey_is_flagged_not_divided() -> None:
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(3, (7.0,))]
    floors, heights = solve_heights(plans)
    assert _levels(floors) == [0.0, 7.0]
    assert floors[1].confidence == 0.8
    assert heights[0] == StoreyHeight(1, 3, 7.0, "plan", 0.65, ("spans_missing_storey",))
    _, heights = solve_heights([StoreyPlan(1, (0.0,)), StoreyPlan(3, ())])
    assert heights[0] == StoreyHeight(1, 3, 6.6, "default", 0.4, ("spans_missing_storey", "assumed"))


def test_input_order_kept_and_storeys_sorted_by_number() -> None:
    floors, heights = solve_heights([StoreyPlan(2, (3.3,)), StoreyPlan(1, (0.0,))])
    assert _levels(floors) == [3.3, 0.0]
    assert (heights[0].from_number, heights[0].to_number) == (1, 2)


def test_negative_gap_is_penalised() -> None:
    floors, _ = solve_heights(_plans([0.0], [3.3, 3.3, -1.0]))
    assert floors[1].level_m == 3.3


def test_only_mezzanines_or_nothing() -> None:
    assert solve_heights([]) == ((), ())
    floors, heights = solve_heights([StoreyPlan(None, (2.8,))])
    assert floors == (FloorLevel(2.8, "plan", 0.7),)
    assert heights == ()


def test_to_db_ranges_reports_instead_of_clamping() -> None:
    floors = (FloorLevel(-2.4, "plan", 0.8), FloorLevel(None, None, 0.5), FloorLevel(-31.0, "plan", 0.6))
    heights = (
        StoreyHeight(0, 1, 2.4, "plan", 0.8),
        StoreyHeight(1, 2, 12.0, "plan", 0.6),
        StoreyHeight(2, 3, 1.5, "plan", 0.6),
    )
    elevations, storey_heights = to_db_ranges(floors, heights)
    assert elevations == (DbValue(-2400, True), DbValue(None, False), DbValue(-31000, False))
    assert storey_heights == (DbValue(2400, True), DbValue(12000, False), DbValue(1500, False))
    assert to_db_ranges((FloorLevel(300.0, "plan", 0.8),), ())[0] == (DbValue(300000, True),)


@dataclass(frozen=True)
class _Text:
    """Chữ OCR của mẫu vàng: chỉ hai trường `levels_from_texts` đọc."""

    text: str
    box: tuple[float, ...]


def _golden_input(golden: dict[str, Any]) -> tuple[list[dict[str, Any]], list[StoreyPlan]]:
    """Mặt bằng của mẫu theo thứ tự id (thứ tự bộ bản vẽ): mặt bằng nhân đã chọn cho mỗi tầng và các lửng."""
    known = golden["expected_known"]
    texts = {v["id"]: v["texts"] for v in golden["input"]["views"]}
    used = {f["view_id"] for f in known["floors"]}
    views = sorted(
        (v for v in known["views"] if v["id"] in used or v["storey_label"] == "mezzanine"), key=lambda v: v["id"]
    )
    plans = [
        StoreyPlan(
            v["floor_no"],
            tuple(m.value_m for m in levels_from_texts(_Text(t["text"], tuple(t["box"])) for t in texts[v["id"]])),
        )
        for v in views
    ]
    return views, plans


@pytest.mark.parametrize("name", _GOLDENS)
def test_golden_parity(name: str) -> None:
    golden = json.loads((_DATA / f"{name}.json").read_text(encoding="utf-8"))
    known = golden["expected_known"]
    views, plans = _golden_input(golden)
    floors, heights = solve_heights(plans)
    expected = {f["view_id"]: f for f in known["floors"]}
    for view, got in zip(views, floors, strict=True):
        exp = expected.get(view["id"])
        if exp is None:  # lửng không có cốt: nhân không ghi tầng, ở đây là "không biết" 0,5
            assert got == FloorLevel(None, None, 0.5)
            continue
        exp_conf = exp.get("level_confidence", exp["confidence"])
        assert got.source == exp["level_source"]
        assert _band(got.confidence) == _band(exp_conf)
        assert (got.level_m is None) == (exp["level_m"] is None)
        if got.level_m is not None:
            assert got.level_m == pytest.approx(exp["level_m"], abs=0.01)
    by_view = {f["floor_no"]: f for f in known["floors"] if f["floor_no"] is not None}
    # Tầng trên cùng: mẫu vàng không có chiều cao; ở đây không có thang cốt nên là 3,3 m ở 0,4 "assumed".
    assert heights[-1].to_number is None
    assert heights[-1].flags == ("assumed",)
    assert len(heights) - 1 == len(known["storey_heights"])
    for got_h, exp_h in zip(heights, known["storey_heights"], strict=False):
        assert (got_h.from_number, got_h.to_number, got_h.source) == (exp_h["from"], exp_h["to"], exp_h["source"])
        assert got_h.height_m == pytest.approx(exp_h["height_m"], abs=0.01)
        # Khác có chủ ý: độ chắc chiều cao của mẫu đã nhân thêm độ chắc gán tầng (0,65 khi chữ mâu thuẫn),
        # việc đó thuộc lớp đề xuất (T5). Ở đây so với min độ chắc cốt của hai đầu.
        ends = (by_view[exp_h["from"]], by_view[exp_h["to"]])
        exp_conf = min(f.get("level_confidence", f["confidence"]) for f in ends)
        assert _band(got_h.confidence) == _band(exp_h["confidence"] if got_h.source == "default" else exp_conf)
        # Khác có chủ ý: lửng không cốt nằm giữa hai mặt bằng theo thứ tự bộ vẫn được gắn "skips_mezzanine";
        # nhân chỉ gắn khi lửng có cốt (nó không biết thứ tự bộ).
        assert ("skips_mezzanine" in got_h.flags) >= ("skips_mezzanine" in exp_h)


def test_many_distinct_marks_solve_fast() -> None:
    """rv2-1: 80 tầng, mỗi tầng 460 cốt khác nhau mất ~20 s (quy hoạch động 460² mỗi cặp tầng, tính lại cả
    tổng điểm cho từng ứng viên thay thế, gom cụm O(k²)); API chờ 12 s và không huỷ được luồng."""
    import time

    marks = tuple(round(i * 0.13, 3) for i in range(460))
    start = time.perf_counter()
    floors, heights = solve_heights([StoreyPlan(n, marks) for n in range(1, 81)])
    assert time.perf_counter() - start < 5.0
    assert len(floors) == 80
    assert len(heights) == 80


# ---- Thang cốt từ mặt cắt (HS-02): các ca `heights3.selfcheck` có `sides`, và luật hậu xử lý của solve_set.

_TWO = 2  # mỗi ca của selfcheck lặp một mặt cắt hai lần: bậc có trên hai mặt cắt


def _sides(*values: float) -> list[list[float]]:
    return [list(values)] * _TWO


def test_ladder_ground_rule_beats_frequent_yard_mark() -> None:
    floors, heights = solve_heights(_plans([-0.45] * 3 + [0.0] * 2, [3.6] * 4), _sides(0, 3.6, 7.2))
    assert floors[0].level_m == 0.0
    assert heights[0].height_m == 3.6


def test_ladder_only_storeys_are_section_at_08() -> None:
    floors, _ = solve_heights(_plans([0.0] * 3, [], []), _sides(0, 3.3, 6.6))
    assert _levels(floors) == [0.0, 3.3, 6.6]
    assert [(f.source, f.confidence) for f in floors[1:]] == [("section", 0.8)] * 2


def test_plan_and_section_with_tight_gaps_reach_093() -> None:
    floors, _ = solve_heights(_plans([0.0], [3.6] * 4 + [3.3], [6.9]), _sides(0, 3.3, 3.6, 6.9))
    assert floors[1].level_m == 3.6
    assert floors[1].source == "plan+section"
    assert floors[1].confidence == 0.93
    assert not floors[1].ambiguous


def test_ladder_without_zero_keeps_ground_convention() -> None:
    floors, _ = solve_heights(_plans([], [], [], []), _sides(-1.9, 3.9, 7.5, 11.1, 14.7))
    assert _levels(floors) == [0.0, 3.9, 7.5, 11.1]
    assert floors[0].source == "convention"
    assert floors[0].confidence < 0.9


def test_unlabelled_mezzanine_rung_explains_tall_ground_gap() -> None:
    floors, heights = solve_heights(_plans([-0.15, 0.0, 0.15], [5.7, 5.7], [9.0, 9.0]), _sides(3.2, 5.7, 9.0, 12.3))
    assert _levels(floors) == [0.0, 5.7, 9.0]
    assert heights[0].height_m == 5.7
    assert heights[0].confidence <= 0.8
    assert "implausible" not in heights[0].flags


def test_unmarked_rung_higher_up_is_not_skipped() -> None:
    floors, _ = solve_heights(_plans([0.0], [], [10.4] * 3, [13.6]), _sides(4.4, 7.2, 10.4, 13.6))
    assert floors[1].level_m == 7.2


def test_ladder_basement_below_ground() -> None:
    plans = [StoreyPlan(0, (-3.0,)), StoreyPlan(1, (0.0,)), StoreyPlan(2, (3.0,))]
    floors, heights = solve_heights(plans, _sides(-3.0, 0.0, 3.0))
    assert _levels(floors) == [-3.0, 0.0, 3.0]
    assert heights[0].height_m == 3.0
    unmarked = [StoreyPlan(0, ()), StoreyPlan(1, ()), StoreyPlan(2, ())]
    assert _levels(solve_heights(unmarked, _sides(-3.0, 0.0, 3.0, 6.0))[0]) == [-3.0, 0.0, 3.0]


def test_ladder_basement_misread_zero_is_unknown() -> None:
    plans = [StoreyPlan(0, (0.0,)), StoreyPlan(1, (0.0, 0.1, 1.6)), StoreyPlan(2, (4.4,))]
    floors, _ = solve_heights(plans, _sides(0.0, 4.4, 7.2))
    assert _levels(floors) == [None, 0.0, 4.4]


def test_ladder_main_floor_prefers_rung() -> None:
    floors, _ = solve_heights(_plans([0.0], [3.55] * 3 + [3.6]), _sides(0.0, 3.6, 7.2))
    assert _levels(floors) == [0.0, 3.6]
    assert level_groups([0.0, 0.1, 0.2], prefer_zero=True, ladder=(0.0, 0.2)) == [(0.0, 2), (0.2, 1)]


def test_ladder_raised_ground_keeps_its_own_mark() -> None:
    floors, heights = solve_heights(_plans([1.3] * 3 + [0.9], [4.6] * 2, [7.9]), _sides(0.0, 1.3, 4.6, 7.9))
    assert _levels(floors) == [1.3, 4.6, 7.9]
    assert heights[0].height_m == 3.3
    ladder = _sides(0.0, 1.3)
    assert _levels(solve_heights([StoreyPlan(1, (1.3,))], ladder)[0]) == [0.0]  # gặp một lần: 0.000 giữ
    assert _levels(solve_heights([StoreyPlan(1, (0.0, 1.3, 1.3))], ladder)[0]) == [0.0]  # 0.000 riêng thắng


def test_labelled_mezzanine_rung_not_given_to_storey() -> None:
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(None, (2.8,)), StoreyPlan(2, ()), StoreyPlan(3, (8.4,))]
    floors, heights = solve_heights(plans, _sides(0.0, 2.8, 5.2, 8.4))
    assert _levels(floors) == [0.0, 2.8, 5.2, 8.4]
    assert floors[1] == FloorLevel(2.8, "plan+section", 0.8)  # lửng có cốt trên thang (solve_set.mezz_floors)
    assert heights[0].height_m == 5.2
    assert "skips_mezzanine" in heights[0].flags


def test_sunken_wc_mark_snaps_to_rung() -> None:
    floors, heights = solve_heights(_plans([0.0], [3.55, 3.55], [6.9]), _sides(0, 3.6, 6.9))
    assert _levels(floors) == [0.0, 3.6, 6.9]
    assert floors[1].source == "plan+section"
    assert floors[1].snapped_from_m == 3.55  # thấp hơn cốt tầng: sàn trũng
    assert [h.height_m for h in heights[:2]] == [3.6, 3.3]


@pytest.mark.parametrize(
    ("value", "ladder", "expected"),
    [
        (3.55, {0.0: 2, 3.6: 2, 6.9: 2}, 3.6),  # trũng
        (3.62, {3.6: 2}, 3.6),  # nâng
        (3.6, {3.6: 2}, None),  # trên bậc
        (3.59, {3.6: 2}, None),
        (3.55, {3.55: 2, 3.6: 2}, None),  # bậc khác
        (3.45, {3.6: 2}, None),  # quá xa dưới
        (3.68, {3.6: 2}, None),  # quá xa trên
    ],
)
def test_sunk_rung(value: float, ladder: dict[float, int], expected: float | None) -> None:
    assert _sunk_rung(value, ladder) == expected


def test_missing_storey_filled_from_rung() -> None:
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(3, (7.0,))]
    floors, heights = solve_heights(plans, _sides(0.0, 3.6, 7.0))
    assert _levels(floors) == [0.0, 7.0]
    assert heights[0] == StoreyHeight(1, 2, 3.6, "section", 0.6, ("missing_storey_filled",))
    _, heights = solve_heights(plans, _sides(0.0, 7.0))  # không có bậc chia: giữ cờ bắc qua
    assert heights[0].flags == ("spans_missing_storey",)
    assert heights[0].confidence == 0.65


def test_chain_ok_lets_height_reach_093() -> None:
    plans = _plans([0.0], [3.6])
    sides = _sides(0.0, 3.6, 7.2)
    floors, heights = solve_heights(plans, sides, ((0.0, 0.9, True), (3.6, 0.9, True)))
    assert [(f.source, f.confidence) for f in floors] == [("plan+section", 0.93)] * 2
    assert heights[0] == StoreyHeight(1, 2, 3.6, "plan+section", 0.93)
    assert solve_heights(plans, sides)[1][0].confidence == 0.85  # không có chuỗi neo
    assert solve_heights(plans, sides, ((0.0, 0.9, True), (3.7, 0.9, True)))[1][0].confidence == 0.85  # lệch > 0,05
    assert solve_heights(plans, (), ((0.0, 0.9, False), (3.6, 0.9, False)))[1][0].confidence == 0.8  # chỉ mặt bằng


def test_chain_fills_missing_level_at_most_085() -> None:
    floors, heights = solve_heights(_plans([0.0], []), (), ((0.0, 0.9, False), (3.3, 0.95, True)))
    assert floors[1] == FloorLevel(3.3, "plan+section", 0.85)
    assert heights[0].height_m == 3.3
    assert heights[0].confidence == 0.8  # chain_ok sai với cốt lấy từ chuỗi


def test_fill_defaults_takes_plausible_rung_above() -> None:
    # Bậc duy nhất là cốt lửng nên tầng 2 không có ứng viên; chiều cao mặc định thay bằng bậc phía trên.
    plans = [StoreyPlan(1, (0.0,)), StoreyPlan(None, (3.0,)), StoreyPlan(2, ())]
    floors, heights = solve_heights(plans, _sides(3.0))
    assert heights[0] == StoreyHeight(1, 2, 3.0, "section", 0.55, ("skips_mezzanine",))
    assert floors[2] == FloorLevel(3.0, "section", 0.55)
    _, heights = solve_heights(plans)  # không có thang: vẫn mặc định
    assert (heights[0].source, heights[0].confidence) == ("default", 0.4)


@pytest.mark.parametrize(("rung", "height"), [(5.99, None), (6.0, 2.4), (6.9, 3.3), (8.2, 4.6), (8.21, None)])
def test_top_storey_height_from_plausible_rung_above(rung: float, height: float | None) -> None:
    """NHA-04: tầng trên cùng cao tới bậc thang phía trên sàn (đỉnh tường / sàn mái đọc trên mặt cắt) khi bậc cách
    2,4-4,6 m — nguồn "section" ở 0,55; bậc ngoài dải thì giữ mặc định 3,3 m ở 0,4 "assumed"."""
    _, heights = solve_heights(_plans([0.0], [3.6]), _sides(0.0, 3.6, rung))
    default = StoreyHeight(2, None, 3.3, "default", 0.4, ("assumed",))
    assert heights[-1] == (default if height is None else StoreyHeight(2, None, height, "section", 0.55))


def test_top_storey_height_prefers_rung_near_default_over_lowest() -> None:
    """Hai bậc trong dải (cốt lanh tô +2,7 và sàn mái +3,3 trên sàn): chọn như `_fill_defaults` — bậc gần 3,3 m."""
    _, heights = solve_heights(_plans([0.0], [3.6]), _sides(0.0, 3.6, 6.3, 6.9))
    assert heights[-1] == StoreyHeight(2, None, 3.3, "section", 0.55)
