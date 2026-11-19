"""Kiểm thử chọn tầng cho bộ bản vẽ (HS-01, T3): ca selfcheck của kernel, từng luật, sáu bộ vàng."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from packages.domain.drawing_set.levels import levels_from_texts
from packages.domain.drawing_set.storeys import (
    PlanView,
    StoreyChoice,
    _candidates,
    _cue_elim,
    _cue_level,
    _cue_order,
    _cue_rank,
    _cue_words,
    _disagreeing,
    _holes,
    _rows,
    _unary,
    dominant,
    floor_relaxed,
    normalise_convention,
    solve_storeys,
    storey_confidence,
)
from packages.domain.drawing_set.titles import parse_title, pick_title

DATA = Path(__file__).parent / "data"


def plan(vid: str, title: str, *levels: float, texts: tuple[str, ...] = ()) -> PlanView:
    """Mặt bằng thử: vị trí lấy từ hai chữ số cuối của mã."""
    return PlanView(vid, parse_title(title), tuple(levels), int(vid[-2:]), texts or (title,))


def solve(*views: PlanView, order_known: bool = True) -> list[StoreyChoice]:
    """Kết quả theo thứ tự đầu vào."""
    out = solve_storeys(views, order_known)
    return [out[v.view_id] for v in views]


# ---------------------------------------------------------------- selfcheck của kernel
def test_kernel_scoring_rules() -> None:
    assert _holes({"a": 1, "b": 3}) == 1
    assert _holes({"a": 1, "b": 2, "c": 2}) == 0
    assert _holes({}) == 0
    assert storey_confidence({"title": 0.95, "level_order": 0.74}, True) == 0.95
    assert 0.7 <= storey_confidence({"title": 0.95}, True) <= 0.9
    assert storey_confidence({"title": 0.95}, False) == 0.7
    assert storey_confidence({"order": 0.3, "elim": 0.2}, True) < 0.7
    assert storey_confidence({"title": 0.95, "level": 0.7}, True, True) < 0.7
    assert storey_confidence({}, True) == 0.0
    assert storey_confidence({}, True, True) == 0.0
    assert storey_confidence({"words": 0.5}, True) == 0.72
    assert _unary({"title": 0.9, "level": 0.7}) > _unary({"title": 0.9}) > _unary({"order": 0.3})
    got = _disagreeing({2: {"title": 0.9}, 3: {"words": 0.8, "order": 0.3}}, 2)
    assert [(c.source, c.family, c.number) for c in got] == [("words", "text", 3)]
    got = _disagreeing({4: {"level": 0.7}, 5: {"level_order": 0.74, "words": 0.8}}, 5)
    assert [(c.source, c.family, c.number) for c in got] == [("level", "level", 4)]


def test_kernel_storey_order_selfcheck() -> None:
    def numbers(titles: list[str], levels: list[float]) -> list[int | None]:
        views = [plan(f"v{i:02d}", t, v, v, v) for i, (t, v) in enumerate(zip(titles, levels, strict=True))]
        return [c.number for c in solve(*views)]

    assert numbers(["TRET", "TANG 1", "TANG 2"], [0.0, 3.6, 7.2]) == [1, 2, 3]
    assert numbers(["FIRST FLOOR PLAN", "SECOND FLOOR PLAN"], [0.0, 3.2]) == [1, 2]
    assert numbers(["TANG 1", "TANG LUNG", "TANG 2"], [0.0, 2.6, 3.8]) == [1, None, 2]


def test_kernel_storey_order_level_only() -> None:
    views = [plan("v00", "TANG 1", 0.0), plan("v01", "MAT BANG", 3.6), plan("v02", "TANG 3", 7.2)]
    rows = _rows(views)
    assert rows["v01"].floor == 2
    assert rows["v01"].source == "level"


def test_kernel_basement_ground_floor() -> None:
    got = solve(
        plan("v00", "MAT BANG TANG HAM", -3.0, -3.0),
        plan("v01", "MAT BANG TANG TRET", 0.0, 0.0),
        plan("v02", "MAT BANG LAU 1", 3.0, 3.0),
    )
    assert [(c.number, c.label) for c in got] == [(0, "basement"), (1, "ground"), (2, "floor")]


def test_kernel_storey_cues_selfcheck() -> None:
    assert floor_relaxed("MAT BANGTRET") == 1
    assert floor_relaxed("MAT BANG LAU 1") == 2
    assert floor_relaxed("MAT BANG TANG 3") == 3
    assert floor_relaxed("SECOND FLOOR") == 3
    assert floor_relaxed("MAT BANG TANG HAM") is None
    assert floor_relaxed("MAT BANG TANG") is None
    assert _cue_order("c", ["a", "b", "c", "d"], {"b": 2, "d": 2}) == [(2, 0.6)]
    assert _cue_order("a", ["a", "b"], {"b": 1}) == [(1, 0.3)]
    assert _cue_order("b", ["a", "b"], {"a": 1}) == [(1, 0.3), (2, 0.3)]
    assert _cue_order("a", ["a"], {}) == []
    assert _cue_elim("x", {"a": 1, "b": 3}) == [(2, 0.5)]
    assert _cue_elim("a", {"a": 1}) == []
    assert _cue_level("x", {"x": 3.6, "a": 0.0, "b": 3.6}, {"a": 1, "b": 2}) == [(2, 0.7)]
    assert _cue_level("x", {"x": 0.0, "a": 3.6}, {"a": 2}) == [(1, 0.6)]
    assert _cue_level("x", {"x": 5.0, "a": 0.0, "b": 9.0}, {"a": 1, "b": 4}) == [(2, 0.25), (3, 0.25)]
    assert _cue_level("x", {"x": 3.6, "y": 0.0}, {}) == [(2, 0.4)]
    assert _cue_level("x", {"x": None}, {}) == []
    views = [plan(f"v{i:02d}", "MAT BANG TANG") for i in range(3)]
    assert _cue_rank(views, {}) == {"v00": [(1, 0.6)], "v01": [(2, 0.6)], "v02": [(3, 0.6)]}
    words = dict(_cue_words(plan("v00", "MAT BANG LAU 1", texts=("MAT BANG LAU 1", "CAU TAO SAN TANG TRET"))))
    assert words[2] > words[1]


def test_dominant_ties() -> None:
    assert dominant([]) is None
    assert dominant([0.0, 3.0]) == 3.0
    assert dominant([-0.45, 0.0], near_zero=True) == 0.0


# ---------------------------------------------------------------- từng luật
def test_set_convention_tret_plus_tang1() -> None:
    views = [plan("v00", "MAT BANG TANG TRET"), plan("v01", "MAT BANG TANG 1"), plan("v02", "MAT BANG TANG 2")]
    assert normalise_convention(views)[0] == "tret_plus_tang1"
    assert [c.number for c in solve(*views)] == [1, 2, 3]


def test_lau_counts_above_ground() -> None:
    assert [c.number for c in solve(plan("v00", "MAT BANG LAU 1"), plan("v01", "MAT BANG LAU 2"))] == [2, 3]


def test_convention_switched_by_levels() -> None:
    # TẦNG 1 ở 3,6 m trong bộ có TRỆT ở 0: chỉ quy ước trệt + tầng 1 khớp cốt.
    views = [plan("v00", "MAT BANG TANG 1", 3.6), plan("v01", "MAT BANG TANG 2", 0.0)]
    assert normalise_convention(views)[1] is False


def test_english_ordinal_capped() -> None:
    got = solve(plan("v00", "GROUND FLOOR PLAN", 0.0), plan("v01", "1ST FLOOR PLAN", 3.3))
    assert got[1].number == 2
    assert got[1].confidence <= 0.85


def test_title_never_outvoted_conflict() -> None:
    other = ("MAT BANG TANG 3",)
    got = solve(plan("v00", "MAT BANG TANG 1"), plan("v01", "MAT BANG TANG 2", texts=other))
    assert got[1].number == 2
    assert got[1].confidence == 0.65
    assert [(c.source, c.number) for c in got[1].conflict] == [("words", 3)]


def test_two_families_anchored_reach_09() -> None:
    got = solve(plan("v00", "MAT BANG TANG 1", 0.0), plan("v01", "MAT BANG TANG 2", 3.6))
    assert all(c.confidence >= 0.9 for c in got)
    assert all(c.sources == ("level", "order", "text") for c in got)


def test_text_alone_never_09() -> None:
    got = solve(plan("v00", "MAT BANG TANG 1"), plan("v01", "MAT BANG TANG 2"))
    assert all(0.72 <= c.confidence <= 0.85 for c in got)


def test_unanchored_levels_capped() -> None:
    # Mặt bằng trệt không nằm trên cốt thấp nhất: bậc cốt không được dùng, hai họ không đủ 0,9.
    got = solve(plan("v00", "MAT BANG TANG 1", 3.0), plan("v01", "MAT BANG TANG 2", 0.0))
    assert all(c.confidence < 0.9 for c in got)


def test_weak_only_below_07() -> None:
    got = solve(plan("v00", "MAT BANG TANG 1"), PlanView("v01", None, (), 1, ()), plan("v02", "MAT BANG TANG 3"))
    assert got[1].number == 2
    assert got[1].confidence < 0.7
    assert got[1].sources == ("order",)


def test_level_order_conflict_landing_shift() -> None:
    # DS-254: bậc cốt của lửng (3,0) làm chuỗi cốt lệch một bậc: LẦU 1 ở 5,7 là bậc 3 -> bất đồng, 0,65.
    got = solve(
        plan("v00", "MAT BANG TRET", 0.0, 0.0),
        plan("v01", "MAT BANG LUNG", 3.0),
        plan("v02", "MAT BANG LAU 1", 5.7),
        plan("v03", "MAT BANG LAU 2", 9.3),
    )
    assert got[0].number == 1
    assert got[0].confidence >= 0.9
    assert got[1].label == "mezzanine"
    assert got[1].number is None
    for c, wrong in zip(got[2:], (3, 4), strict=True):
        assert c.confidence == 0.65
        assert [(x.source, x.number) for x in c.conflict] == [("level_order", wrong)]


def test_roof_terrace_and_attic_on_top() -> None:
    got = solve(plan("v00", "MAT BANG TRET"), plan("v01", "MAT BANG LAU 1"), plan("v02", "MAT BANG SAN THUONG"))
    assert (got[2].label, got[2].number, got[2].confidence, got[2].sources) == ("roof_terrace", 3, 0.7, ("text",))
    assert solve(plan("v00", "MAT BANG TUM"))[0].number == 1


def test_second_basement_and_unknown() -> None:
    got = solve(plan("v00", "MAT BANG TANG HAM 2"), plan("v01", "MAT BANG MAI"), PlanView("v02", None))
    assert got[0].number == -1
    assert got[1].number is None
    assert got[1].label is None
    assert got[2].number == 1
    assert got[2].confidence < 0.7


def test_order_known_false_turns_off_order_cues() -> None:
    views = [plan(f"v{i:02d}", "MAT BANG TANG") for i in range(2)]
    rows = _rows(views)
    on = {s for cs in _candidates(views, rows, True).values() for s, _, _ in cs}
    off = {s for cs in _candidates(views, rows, False).values() for s, _, _ in cs}
    assert "rank" in on
    assert "rank" not in off
    assert "order" not in off
    assert solve(*views, order_known=False)[0].confidence < 0.7


# ---------------------------------------------------------------- sáu bộ vàng
@dataclass(frozen=True)
class _Text:
    text: str
    box: tuple[float, float, float, float]
    confidence: float = 1.0


def _band(c: float) -> str:
    return "high" if c >= 0.9 else "mid" if c >= 0.7 else "low"


def _golden_views(views: list[dict[str, Any]]) -> list[PlanView]:
    out = []
    for v in views:
        texts = [_Text(t["text"], tuple(t["box"])) for t in v["texts"]]
        picked = pick_title(texts, tuple(v["size"]))
        levels = tuple(m.value_m for m in levels_from_texts(texts))
        out.append(PlanView(v["id"], parse_title(picked.text), levels, v["order"], tuple(t.text for t in texts)))
    return out


# Khác biệt chấp nhận (id -> lý do). Mọi hình khác phải khớp số tầng và dải độ tin.
_ACCEPTED_BAND = {
    # T3 quyết định (a): sân thượng = tầng cao nhất + 1, tối đa 0,7 (kernel 0,8); cả hai ở dải 0,7-0,9.
    "DS-466__f03_plan": "roof terrace capped at 0.7",
    # Lửng: kernel 0,0 khi không có cốt, ở đây 0,6 từ tên; cả hai dưới 0,7, không số.
    "DS-466__f01_plan": "mezzanine from title",
}


@pytest.mark.parametrize("name", sorted(p.name for p in DATA.glob("*.json")))
def test_golden_parity(name: str) -> None:
    golden = json.loads((DATA / name).read_text(encoding="utf-8"))
    got = solve_storeys(_golden_views(golden["input"]["views"]))
    for exp in golden["expected_known"]["views"]:
        c = got[exp["id"]]
        assert c.number == exp.get("floor_no"), exp["id"]
        if exp["id"] not in _ACCEPTED_BAND:
            assert _band(c.confidence) == _band(exp.get("floor_confidence") or 0.0), exp["id"]
        if exp.get("conflict"):
            # Kernel ghi "chain" (levels_chain); bản này ghi "level_order" cho cùng bất đồng, cùng số tầng.
            assert [x.number for x in c.conflict] == [x["floor_no"] for x in exp["conflict"]], exp["id"]
