"""Kiểm thử chọn tầng cho bộ bản vẽ (HS-01, T3): ca selfcheck của kernel, từng luật, sáu bộ vàng."""

import json
import time
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
    storeys_apart,
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
    assert storey_confidence({"words": 0.8}, True) == 0.55  # phiếu chữ không tự mạnh (soát mã F2)
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
    assert words == {2: 0.8}  # ghi chú cấu tạo "SAN TANG" không phải tên hình: không bỏ phiếu (soát mã F2)


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
    # 5,7 m cho một tầng trệt -> lầu 1 cũng hỏng phép thử khoảng cách: "level_gap", trệt không còn >= 0,9.
    got = solve(
        plan("v00", "MAT BANG TRET", 0.0, 0.0),
        plan("v01", "MAT BANG LUNG", 3.0),
        plan("v02", "MAT BANG LAU 1", 5.7),
        plan("v03", "MAT BANG LAU 2", 9.3),
    )
    assert (got[0].number, got[0].confidence) == (1, 0.85)
    assert got[1].label == "mezzanine"
    assert got[1].number is None
    wrong = ([("level_order", 3), ("level_gap", 3)], [("level_order", 4)])
    for c, conflicts in zip(got[2:], wrong, strict=True):
        assert c.confidence == 0.65
        assert [(x.source, x.number) for x in c.conflict] == conflicts


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


# ---------------------------------------------------------------- sửa sau soát mã
def test_set_convention_applies_to_word_cues() -> None:
    # Lỗi 1: bộ trệt + tầng 1, "TANG 1" ở 3,3 m là tầng 2; chữ tên của chính nó không được bỏ phiếu tầng 1.
    got = solve(plan("v00", "MAT BANG TRET", 0.0), plan("v01", "MAT BANG TANG 1", 3.3))
    assert [c.number for c in got] == [1, 2]
    assert got[1].conflict == ()
    assert got[1].confidence >= 0.9
    got = solve(plan("v00", "FIRST FLOOR PLAN", 0.0), plan("v01", "SECOND FLOOR PLAN", 3.2))
    assert [(c.number, c.conflict) for c in got] == [(1, ()), (2, ())]
    assert floor_relaxed("MAT BANG TANG 1", "tret_plus_tang1") == 2
    assert floor_relaxed("MAT BANG LAU 1", "tret_plus_tang1") == 2
    assert floor_relaxed("FIRST FLOOR", "us_english") == 1
    assert floor_relaxed("MAT BANG TANG HAM", "tret_plus_tang1") is None


@pytest.mark.parametrize(
    ("title", "number"),
    [
        ("BASEMENT 4 FLOOR PLAN", -3),
        ("MAT BANG TANG HAM 4", -3),
        ("MAT BANG HAM B4", -3),
        ("MAT BANG TANG HAM 1", 0),
        ("MAT BANG TANG HAM 6", -5),
        ("MAT BANG TANG HAM", 0),
        ("MAT BANG HAM B10", None),
        ("MAT BANG TANG HAM 0", None),
        ("MAT BANG TANG HAM " + "4" * 40, None),
    ],
)
def test_deep_basements_keep_their_depth(title: str, number: int | None) -> None:
    # Lỗi 2: hầm N -> 1 - N với N = 1..6; không ghi = hầm duy nhất; ngoài khoảng -> không số, độ tin < 0,7.
    got = solve(plan("v00", title))[0]
    assert (got.label, got.number) == ("basement", number)
    assert (got.confidence == 0.8) if number is not None else (got.confidence < 0.7)


def test_basement_depth_for_every_synonym() -> None:
    # Soát rv2 #4: độ sâu đọc được sau mọi từ hầm và mã "B2"; mã "B" không số -> hỏi, không lặng lẽ thành hầm 1.
    got = [solve(plan("v00", t))[0] for t in ("CELLAR B2 PLAN", "UNDERGROUND 3 PLAN", "CELLAR B PLAN")]
    assert [(c.label, c.number) for c in got] == [("basement", -1), ("basement", -2), ("basement", None)]
    assert got[2].confidence < 0.7


def test_second_plan_title_in_view_is_a_conflict() -> None:
    # Soát rv2 #2: hai tên "MẶT BẰNG TẦNG n" khác số trên một hình là bất đồng; ghi chú nhắc tầng khác thì không.
    rival = ("MAT BANG TANG 1", "MAT BANG TANG 2")
    got = solve(plan("v00", "MAT BANG TANG 1", 0.0, texts=rival), plan("v01", "MAT BANG TANG 2", 3.3))
    assert (got[0].number, got[0].confidence) == (1, 0.65)
    assert [(c.source, c.number) for c in got[0].conflict] == [("words", 2)]
    notes = ("MAT BANG TANG 1", "XEM MAT BANG TANG 2", "LEN TANG 2", "NHA PHO 1 TRET 2 LAU")
    got = solve(plan("v00", "MAT BANG TANG 1", 0.0, texts=notes), plan("v01", "MAT BANG TANG 2", 3.3))
    assert (got[0].conflict, got[0].confidence >= 0.9) == ((), True)


def test_hostile_input_is_bounded() -> None:
    # Lỗi 3: số tầng lạ, 10 000 dòng chữ, 200 hình không được làm bùng khối lượng việc.
    noise = tuple(f"ROOM {i}" for i in range(10_000))
    big = [
        plan("v00", "MAT BANG TANG 1"),
        plan("v01", "MAT BANG TANG 999999999999"),
        PlanView("v02", None, (), 2, ("ROOM",)),
        PlanView("v03", None, (), 3, noise),
    ]
    start = time.perf_counter()
    for order_known in (True, False):
        got = solve(*big, order_known=order_known)
        assert got[0].number == 1
        assert got[1].number != 999999999999
    # ~0,1 s không đo độ phủ (cổng đo độ phủ chậm ~12 lần nên chặn rộng); trước khi sửa: list 10^12 tầng, không xong.
    assert time.perf_counter() - start < 3.0
    many = [plan(f"v{i:03d}", f"MAT BANG TANG {i % 80 + 1}", 3.0 * i) for i in range(200)]
    start = time.perf_counter()
    assert len(solve_storeys(many)) == 200
    # ~0,06 s không đo độ phủ; cổng đo độ phủ chậm ~12 lần nên chặn rộng.
    assert time.perf_counter() - start < 3.0
    over = [*many, plan("v999", "MAT BANG TANG 1")]
    assert all(c.number is None and c.confidence == 0.0 for c in solve_storeys(over).values())


# ---------------------------------------------------------------- sửa sau soát mã rvA (F1-F3)
def test_tret_and_tang1_in_one_title_is_ground() -> None:
    # F1: "TẦNG 1 (TRỆT)" là tầng trệt; không bật quy ước trệt + tầng 1 cho cả bộ.
    views = [plan("v00", "MAT BANG TANG 1 (TRET)"), plan("v01", "MAT BANG TANG 2"), plan("v02", "MAT BANG TANG 3")]
    assert normalise_convention(views)[0] == "unchanged"
    assert [(c.number, c.confidence) for c in solve(*views)] == [(1, 0.85), (2, 0.85), (3, 0.85)]


def test_unchecked_convention_asks() -> None:
    # F1: quy ước khác mặc định mà không có cốt để kiểm -> tầng phụ thuộc nó dưới 0,7; tầng trệt không đổi.
    got = solve(plan("v00", "MAT BANG TRET"), plan("v01", "MAT BANG TANG 1"), plan("v02", "MAT BANG TANG 2"))
    assert [c.number for c in got] == [1, 2, 3]
    assert got[0].confidence == 0.85
    assert all(c.confidence < 0.7 for c in got[1:])
    got = solve(plan("v00", "FIRST FLOOR PLAN"), plan("v01", "SECOND FLOOR PLAN"))
    assert all(c.confidence < 0.7 for c in got)


def test_project_and_stair_lines_do_not_vote() -> None:
    # F2a: dòng mô tả công trình không phải tên hình -> không ai thành tầng 1 tự tin.
    note = ("CONG TRINH: NHA PHO 1 TRET 2 LAU",)
    views = [PlanView(f"v0{i}", None, (3.6 * i,), i, note) for i in range(3)]
    assert all(c.confidence < 0.7 and c.conflict for c in solve(*views))
    # F2b: ghi chú thang "LÊN TẦNG 3", kể cả khi bị chọn làm tên, không đặt hình ở tầng 3.
    for title in (None, parse_title("LEN TANG 3")):
        mid = PlanView("v01", title, (), 1, ("LEN TANG 3",))
        got = solve(plan("v00", "MAT BANG TANG 1"), mid, plan("v02", "MAT BANG TANG 3"))
        assert (got[1].number, got[1].confidence < 0.7, got[2].conflict) == (2, True, ())


def test_duplicate_plain_storeys() -> None:
    # F2: hai bản thường cùng tầng, cốt như nhau -> cả hai bất đồng 0,65.
    got = solve(
        plan("v00", "MAT BANG TANG 1", 0.0), plan("v01", "MAT BANG TANG 2", 3.6), plan("v02", "MAT BANG TANG 2", 3.6)
    )
    assert [(c.number, c.confidence) for c in got[1:]] == [(2, 0.65), (2, 0.65)]
    assert all([(x.source, x.number) for x in c.conflict] == [("duplicate_storey", 2)] for c in got[1:])
    # Cốt lệch > 0,5 m: bản khớp hàng xóm giữ số, bản kia không số và bất đồng.
    got = solve(
        plan("v00", "MAT BANG TANG 1", 0.0), plan("v01", "MAT BANG TANG 2", 3.6), plan("v02", "MAT BANG TANG 2", 9.0)
    )
    assert (got[1].number, got[1].conflict) == (2, ())
    assert (got[2].label, got[2].number, got[2].confidence) == (None, None, 0.65)
    # Biến thể nội thất của cùng tầng không phải trùng.
    got = solve(
        plan("v00", "MAT BANG TANG 1", 0.0),
        plan("v01", "MAT BANG TANG 2", 3.6),
        plan("v02", "MAT BANG NOI THAT TANG 2", 3.6),
    )
    assert all(c.confidence >= 0.9 and c.conflict == () for c in got)


def test_level_gap_is_independent_evidence() -> None:
    # F3: "TẦNG 2" (đọc sai, thật là tầng 3) ở 7,2 m trên TẦNG 1 ở 0: cốt nói hai tầng -> bất đồng.
    got = solve(plan("v00", "MAT BANG TANG 1", 0.0, 0.0), plan("v01", "MAT BANG TANG 2", 7.2, 7.2), order_known=False)
    assert (got[0].number, got[0].confidence) == (1, 0.85)
    assert (got[1].number, got[1].confidence) == (2, 0.65)
    assert [(x.source, x.number) for x in got[1].conflict] == [("level_order", 3), ("level_gap", 3)]
    assert (storeys_apart(3.3), storeys_apart(7.2), storeys_apart(8.0), storeys_apart(1.8)) == (1, 2, None, None)
    # Cốt trội là chiếu nghỉ (1,8) nhưng hình có cốt sàn 3,6: phép thử khoảng cách vẫn qua (không "level_gap");
    # cốt trội 1,8 không nói tầng nào (thứ tự cốt đọc nó là lửng) nên tầng 2 chỉ có tên, dưới 0,9.
    got = solve(plan("v00", "MAT BANG TANG 1", 0.0), plan("v01", "MAT BANG TANG 2", 3.6, 1.8, 1.8))
    assert [(c.confidence, c.conflict) for c in got] == [(0.95, ()), (0.84, ())]
    # Một tầng có cốt +-0.000 mà không hàng xóm nào có cốt: mốc tuyệt đối vẫn tính.
    assert solve(plan("v00", "MAT BANG TANG 1", 0.0), plan("v01", "MAT BANG TANG 2"))[0].confidence >= 0.9


def test_candidate_ranges_are_bounded() -> None:
    assert _cue_level("x", {"x": 5.0, "a": 0.0, "b": 900.0}, {"a": 1, "b": 80}) == []
    assert _cue_order("c", ["a", "c", "d"], {"a": 1, "d": 80}) == []
    assert _cue_elim("x", {"a": 1, "b": 80}, 3) == []
    assert _cue_elim("x", {"a": 1, "b": 3}, 3) == [(2, 0.5)]
    views = [plan(f"v{i:02d}", "MAT BANG TANG") for i in range(2)]
    assert _cue_rank(views, {"z": 10**9}) == {"v00": [(1, 0.6)], "v01": [(2, 0.6)]}


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


# Hình mà bản này cố ý khác kernel: (độ tin đúng, nguồn bất đồng). Ghim bằng ĐẲNG THỨC (soát mã F14) để
# lùi về một câu trả lời tự tin sai không lọt qua.
#  * english_basement: kernel ghi bất đồng giả "words" (đọc tên của chính hình theo quy ước mặc định thay vì
#    kiểu Mỹ). Ở đây số 1/2/3 đúng, đã kiểm bằng cốt; chuỗi không neo (hầm thấp nhất) -> 0,85, không bất đồng.
#  * tret_tang1: bộ có HAI bản thường cho mỗi tầng (f01-f06 và f10-f15 cùng tên, cùng cốt): không biết bản
#    nào là mặt bằng của tầng -> bất đồng "duplicate_storey" 0,65 cho cả hai (soát mã F2); số tầng vẫn đúng.
_PINNED: dict[str, tuple[float, tuple[str, ...]]] = {
    **{f"DS-836__f0{i}_plan": (0.85, ()) for i in (1, 2, 3)},
    **{f"DS-266__f{i:02d}_plan": (0.65, ("duplicate_storey",)) for i in (*range(1, 7), *range(10, 16))},
}


@pytest.mark.parametrize("name", sorted(p.name for p in DATA.glob("*.json")))
def test_golden_parity(name: str) -> None:
    golden = json.loads((DATA / name).read_text(encoding="utf-8"))
    got = solve_storeys(_golden_views(golden["input"]["views"]))
    for exp in golden["expected_known"]["views"]:
        c = got[exp["id"]]
        assert c.number == exp.get("floor_no"), exp["id"]
        if exp["id"] in _PINNED:
            assert (c.confidence, tuple(x.source for x in c.conflict)) == _PINNED[exp["id"]], exp["id"]
            continue
        if exp["id"] not in _ACCEPTED_BAND:
            assert _band(c.confidence) == _band(exp.get("floor_confidence") or 0.0), exp["id"]
        if exp.get("conflict"):
            # Kernel ghi "chain" (levels_chain); bản này ghi "level_order" cho cùng bất đồng, cùng số tầng.
            assert [x.number for x in c.conflict] == [x["floor_no"] for x in exp["conflict"]], exp["id"]
