"""`register_h` / `register_v` (K-32d): 4 mặt cắt thật so với nhân nghiên cứu, ca tổng hợp và ca không căn được.

`data/register_cases.json`: mặt bằng (bong bóng) và mặt cắt (bong bóng, dấu cốt) của 4 hình trong bộ kiểm nghiên cứu
(DS-261 f05, DS-454 f04, DS-396 f06, DS-248 f10; nhãn hoàn hảo) kèm đầu ra của chính `register_h.py` / `register_v.py`.
"""

import json
from functools import cache
from typing import Any, Final

import pytest

from packages.vision.stacking import (
    Bubble,
    FloorObservation,
    LevelPoint,
    fit_line,
    level_rows,
    plan_axes,
    register_h,
    register_v,
    row_labels,
)
from packages.vision.stacking.register_h import PlanAxes
from packages.vision.stacking.register_v import _confidence as v_confidence
from packages.vision.stacking.tests.support import DATA

_MM: Final = 10.0


@cache
def _cases() -> list[dict[str, Any]]:
    """Các ca thật của `register_cases.json`."""
    rows: list[dict[str, Any]] = json.loads((DATA / "register_cases.json").read_text(encoding="utf-8"))
    return rows


def _obs(raw: dict[str, Any]) -> FloorObservation:
    """Một quan sát chỉ có tỉ lệ và bong bóng từ dạng JSON của ca thật."""
    return FloorObservation(raw["mm_per_px"], [Bubble(b["label"], b["x"], b["y"], b["r"]) for b in raw["bubbles"]])


def _row(labels_x: dict[str, float], y: float = 50.0) -> FloorObservation:
    """Một hàng bong bóng nằm ngang tại y (px) với x mm cho từng nhãn, tỉ lệ 10 mm/px."""
    return FloorObservation(_MM, [Bubble(lab, x / _MM, y, 5) for lab, x in labels_x.items()])


@pytest.mark.parametrize("case", [c["name"] for c in _cases()])
def test_register_h__matches_research_kernel_on_real_sections(case: str) -> None:
    """Căn ngang trên mặt cắt thật: hướng, lật, hệ số, độ dời, nhãn nội lai và độ tin trùng nhân gốc."""
    row = next(c for c in _cases() if c["name"] == case)
    plan = _obs(row["plan"])
    axes = plan_axes(plan)
    assert axes == {"x": pytest.approx(row["axes"]["x"]), "y": pytest.approx(row["axes"]["y"])}
    want = row["h"]
    got = register_h(_obs(row["side"]), axes)
    assert (got.along, got.flipped, got.n_axes, got.why) == (
        want["along"],
        want["flipped"],
        want["n_axes"],
        want["why"],
    )
    assert got.scale == pytest.approx(want["scale"], abs=1e-9)
    assert got.offset_mm == pytest.approx(want["offset_mm"], abs=1e-6)
    assert got.residual_mm == pytest.approx(want["residual_mm"], abs=1e-6)
    assert got.confidence == pytest.approx(want["confidence"], abs=1e-9)
    assert list(got.labels) == want["labels"]
    assert got.confidence >= 0.9  # báo cáo register_h: 44/61 mặt cắt căn được ở ≥ 0,9


@pytest.mark.parametrize("case", [c["name"] for c in _cases()])
def test_register_v__matches_research_kernel_on_real_sections(case: str) -> None:
    """Căn dọc trên mặt cắt thật: y0, hệ số, tỉ lệ, số nội lai/ngoại lai, phần dư, độ tin trùng nhân gốc."""
    row = next(c for c in _cases() if c["name"] == case)
    marks = [LevelPoint(m["value_m"], m["y"]) for m in row["side"]["levels"]]
    want = row["v"]
    got = register_v(marks, row["side"]["mm_per_px"])
    assert got is not None
    assert got.y0_px == pytest.approx(want["y0_px"], abs=1e-6)
    assert got.px_per_m == pytest.approx(want["px_per_m"], abs=1e-9)
    assert got.scale_ratio == pytest.approx(want["scale_ratio"], abs=1e-9)
    assert (got.n_marks, got.n_inliers, len(got.outliers)) == (
        want["n_marks"],
        want["n_inliers"],
        len(want["outliers"]),
    )
    assert sorted(m.value_m for m in got.outliers) == sorted(o["value_m"] for o in want["outliers"])
    assert got.residual_mm == pytest.approx(want["residual_mm"], abs=1e-6)
    assert got.confidence == pytest.approx(want["confidence"], abs=1e-9)
    assert abs(got.scale_ratio - 1) <= 0.02  # báo cáo register_v: 94 % trong 2 % của 1


def _plan() -> tuple[FloorObservation, PlanAxes]:
    """Mặt bằng tổng hợp của nhân: trục 1-4 (x) trên một hàng, A-C (y) trên một cột."""
    bubbles = [Bubble(lab, x / _MM, 100, 5) for lab, x in {"1": 1000, "2": 3000, "3": 5000, "4": 8000}.items()]
    bubbles += [Bubble(lab, 50, y / _MM, 5) for lab, y in (("A", 500), ("B", 2500), ("C", 6000))]
    plan = FloorObservation(_MM, bubbles)
    return plan, plan_axes(plan)


def test_register_h__synthetic_plan_axes_offset_flip_and_outlier() -> None:
    """Ca tổng hợp của nhân: dời 2000 mm; lật qua 20000 mm; một bong bóng sai nhãn bị loại, ba trục còn lại vẫn khớp."""
    _, axes = _plan()
    assert axes["x"]["3"] == 5000
    assert axes["y"]["B"] == 2500
    r = register_h(_row({lab: v + 2000 for lab, v in axes["x"].items()}), axes)
    assert r.along == "x"
    assert not r.flipped
    assert r.n_axes == 4
    assert r.offset_mm == pytest.approx(2000)
    assert r.confidence >= 0.9
    r = register_h(_row({lab: 20000 - v for lab, v in axes["x"].items()}), axes)
    assert r.flipped
    assert r.scale == pytest.approx(-1)
    assert r.offset_mm == pytest.approx(20000)
    bad = {lab: v + 2000 for lab, v in axes["x"].items()}
    bad["4"] += 1500
    r = register_h(_row(bad), axes)
    assert r.n_axes == 3
    assert "4" not in r.labels
    assert r.offset_mm == pytest.approx(2000)
    assert r.confidence >= 0.9


def test_register_h__reasons_when_not_registered() -> None:
    """Không căn được thì `along` None và `why` nêu lý do: thiếu tỉ lệ, < 2 bong bóng, < 2 nhãn khớp, không khớp."""
    _, axes = _plan()
    good = {"1": 1000.0, "2": 3000.0, "3": 5000.0}
    assert register_h(FloorObservation(None, [Bubble("1", 1, 1, 5)]), axes).why == "no_scale"
    assert register_h(_row({"1": 1000.0}), axes).why == "lt2_bubbles_in_row"
    assert register_h(FloorObservation(_MM), axes).why == "lt2_bubbles_in_row"
    assert register_h(_row({"X": 1.0, "Y": 2.0, "1": 5.0}), axes).why == "lt2_labels_match_plan"
    # hai nhãn khớp nhưng tỉ lệ 3 lần (ngoài dải 0,5-2): không giả thuyết nào hợp lệ
    assert register_h(_row({"1": 1000.0, "2": 9000.0}), axes).why == "no_consistent_fit"
    assert register_h(_row(good), axes).along == "x"


def test_register_h__confidence_by_axes_and_scale() -> None:
    """Hai trục đúng tỉ lệ 0,8; ba trục đúng tỉ lệ ≥ 0,9; sai tỉ lệ (hệ số 1,5) hạ xuống 0,5 / 0,6."""
    _, axes = _plan()
    two = register_h(_row({"1": 1000.0, "2": 3000.0}), axes)
    assert (two.n_axes, two.confidence) == (2, 0.8)
    skewed2 = register_h(_row({"1": 1000.0, "2": 4000.0}), axes)  # 3000 mm → 4500: hệ số 1,5
    assert (skewed2.n_axes, skewed2.confidence) == (2, 0.5)
    skewed3 = register_h(_row({"1": 1500.0, "2": 4500.0, "3": 7500.0}), axes)
    assert (skewed3.n_axes, skewed3.confidence) == (3, 0.6)


def test_register_h__plan_axes_without_scale_and_row_labels_median() -> None:
    """Mặt bằng không tỉ lệ cho trục 0 mm (không chia cho None); hai bong bóng cùng nhãn trong hàng lấy trung vị."""
    plan = FloorObservation(None, [Bubble("1", 10, 5, 5), Bubble("2", 20, 5, 5)])
    assert plan_axes(plan) == {"x": {"1": 0.0, "2": 0.0}, "y": {}}
    side = _row({"1": 1000.0, "2": 3000.0})
    twin = FloorObservation(_MM, [*side.bubbles, Bubble("1", 30, 50, 5)])
    assert row_labels(twin)["1"] == pytest.approx(650.0)  # trung vị của 1000 và 300 mm


def test_fit_line__needs_two_shared_labels() -> None:
    """`fit_line` trả None khi chưa đủ hai nhãn chung hợp lệ; còn lại khớp đúng (s = 1, t = 7)."""
    assert fit_line({"a": 1.0}, {"a": 1.0, "b": 2.0}) is None
    assert fit_line({"a": 1.0, "b": 2.0}, {"a": 5.0, "b": 5.0}) is None  # hai nhãn trùng vị trí mặt bằng
    line = fit_line({"a": 107.0, "b": 207.0}, {"a": 100.0, "b": 200.0})
    assert line is not None
    assert line.s == pytest.approx(1.0)
    assert line.t == pytest.approx(7.0)


def _levels(vals_y: list[tuple[float, float]]) -> list[LevelPoint]:
    """Danh sách dấu cốt (giá trị m, y px)."""
    return [LevelPoint(v, y) for v, y in vals_y]


def test_register_v__synthetic_clean_and_outlier() -> None:
    """Ca tổng hợp của nhân: 4 mốc sạch trên đường y = 1000 - 100·v → y0 1000, tỉ lệ 1; một mốc sai giá trị bị loại."""
    k, y0 = 100.0, 1000.0
    clean = [(v, y0 - k * v) for v in (0.0, 3.6, 7.2, 10.8)]
    fit = register_v(_levels(clean), _MM)
    assert fit is not None
    assert fit.y0_px == pytest.approx(1000)
    assert fit.scale_ratio == pytest.approx(1)
    assert fit.confidence >= 0.9
    assert fit.outliers == ()
    bad = register_v(_levels([(0.6 if v == 3.6 else v, y) for v, y in clean]), _MM)
    assert bad is not None
    assert bad.n_inliers == 3
    assert [o.value_m for o in bad.outliers] == [0.6]
    assert bad.y0_px == pytest.approx(1000)
    assert level_rows(fit, [0.0, 3.6]) == {0.0: pytest.approx(1000.0), 3.6: pytest.approx(640.0)}


def test_register_v__not_enough_marks_or_no_scale_gives_none() -> None:
    """< 2 giá trị cốt phân biệt (hai mốc cùng giá trị, một mốc, rỗng), không tỉ lệ, hay đường dốc ngược → None."""
    assert register_v(_levels([(3.6, 640.0), (3.6, 640.0)]), _MM) is None
    assert register_v(_levels([(3.6, 640.0)]), _MM) is None
    assert register_v([], _MM) is None
    assert register_v(_levels([(0.0, 1000.0), (3.6, 640.0)]), None) is None
    assert register_v(_levels([(0.0, 1000.0), (3.6, 1360.0)]), _MM) is None  # cốt cao hơn nằm THẤP hơn: dốc âm


@pytest.mark.parametrize(
    ("distinct", "ratio", "want"),
    [
        (3, 1.0, 0.9),
        (9, 1.0, 1.0),
        (3, 1.04, 0.8),
        (2, 1.01, 0.8),
        (2, 1.04, 0.7),
        (3, 2.0, 0.5),  # tỉ lệ sai gấp đôi (5/84 mặt cắt trong nghiên cứu)
        (2, 2.0, 0.5),
        (1, 1.0, 0.0),
    ],
)
def test_register_v__confidence_table(distinct: int, ratio: float, want: float) -> None:
    """Bảng độ tin theo số giá trị cốt phân biệt và hệ số so tỉ lệ danh nghĩa."""
    assert v_confidence(distinct, ratio) == pytest.approx(want)
