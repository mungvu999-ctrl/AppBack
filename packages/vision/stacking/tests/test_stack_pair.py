"""`stack_pair` (K-32d): hồi quy trên 6 cặp tầng thật so với nhân nghiên cứu, ca tổng hợp của nhân và ca biên.

Cặp thật lấy từ bộ kiểm nghiên cứu (`muc3/truth_review/stack_ref.json`, quan sát nhãn) — 4 cặp theo mặt nạ tường, 2 cặp
theo mực bản vẽ; mặt nạ và ảnh xám thu nhỏ để mỗi tệp < 200 KB. `data/golden.json` là đầu ra của chính
`stacking2.stack_pair2` trên các tệp đó; số của báo cáo `stacking2_report.md` được nhắc lại ở `_REPORT`.
"""

import math
from dataclasses import replace
from functools import cache
from typing import Any, Final

import cv2
import numpy as np
import pytest

from packages.vision.stacking import FloorObservation, StackResult, confidence, stack_pair
from packages.vision.stacking.tests.support import conflict_case, golden, load_view, synth

_TOL_MM: Final = 100  # ngưỡng chấm của nghiên cứu (đáp án đúng khi cách đáp án chuẩn ≤ mức này)
# Dòng báo cáo `stacking2_report.md`, bảng nhãn quan sát: (dịch, độ tin, nền, điểm, tỉ lệ đối thủ, xác nhận bởi)
_REPORT: Final = {
    "DS-261_2-3": ((0, 322), 0.963, "mask", 1.185, 0.492, ("grid", "columns")),
    "DS-257_2-3": ((444, -800), 0.91, "mask", 0.653, 0.67, ("columns",)),
    "DS-254_2-3": ((-908, 541), 0.784, "mask", 0.9, 0.64, ()),
    "DS-264_1-2": ((0, 0), 0.786, "mask", 0.868, 0.627, ()),
    "DS-571_1-2": ((0, 0), 0.903, "ink", 0.582, 0.671, ("grid",)),
    "DS-292_1-2": ((2750, 0), 0.777, "ink", 0.574, 0.688, ()),
}
_GOLDEN: Final = {row["name"]: row for row in golden()}


@cache
def _run(name: str) -> StackResult:
    """Chồng hai mặt bằng thật của một dòng golden."""
    a, b = (load_view(v) for v in _GOLDEN[name]["views"])
    return stack_pair(a, b)


@pytest.mark.parametrize("name", sorted(_GOLDEN))
def test_stack_pair__matches_research_kernel_on_real_pairs(name: str) -> None:
    """Bản chuyển cho cùng dịch, độ tin, nền, đầu mối, xác nhận và danh sách ứng viên như nhân gốc trên ca thật."""
    want: dict[str, Any] = _GOLDEN[name]["result"]
    got = _run(name)
    assert list(got.shift_mm or ()) == want["shift_mm"]
    assert got.confidence == pytest.approx(want["confidence"], abs=1e-9)
    assert got.basis == want["basis"]
    assert got.score == pytest.approx(want["score"], abs=1e-9)
    assert got.ratio == pytest.approx(want["ratio"], abs=1e-9)
    assert list(got.confirmed_by) == want["confirmed_by"]
    assert [(c.cue, list(c.shift_mm)) for c in got.conflict] == [(c["cue"], c["shift_mm"]) for c in want["conflict"]]
    assert {k: list(v) for k, v in got.cues.items()} == want["cues"]
    assert [(list(c.names), list(c.shift_mm), c.score) for c in got.candidates] == [
        (c["names"], c["shift_mm"], c["score"]) for c in want["candidates"]
    ]


@pytest.mark.parametrize("name", sorted(_REPORT))
def test_stack_pair__reproduces_report_numbers(name: str) -> None:
    """Số của báo cáo nhân (dịch, độ tin, nền, điểm, tỉ lệ đối thủ, đầu mối xác nhận) lặp lại trên bản chuyển."""
    shift, conf, basis, score, ratio, confirmed = _REPORT[name]
    got = _run(name)
    assert got.shift_mm == shift
    assert (got.confidence, got.basis, got.score, got.ratio) == (conf, basis, score, ratio)
    assert got.confirmed_by == confirmed
    assert got.conflict == ()


def test_stack_pair__real_pairs_are_right_within_100mm_and_two_source_rule_holds() -> None:
    """Trên 6 cặp có đáp án người chấm: đúng trong 100 mm; ≥ 0,9 chỉ khi có đầu mối độc lập xác nhận."""
    for name, row in _GOLDEN.items():
        got = _run(name)
        truth = row["truth"]
        if truth is not None:  # DS-292 là cặp "không ai xác nhận được": đáp án so với ghi chú, không có truth
            assert got.shift_mm is not None
            assert math.dist(got.shift_mm, truth) <= _TOL_MM, name
        if got.confidence >= 0.9:
            assert got.confirmed_by, name


def test_stack_pair__synthetic_shift_found_with_two_sources() -> None:
    """Ca tổng hợp của nhân: dịch (500, -300) tìm được trong 100 mm, độ tin ≥ 0,9, không xung đột."""
    a, b = synth((500, -300))
    got = stack_pair(a, b)
    assert got.shift_mm is not None
    assert math.dist(got.shift_mm, (500, -300)) <= _TOL_MM
    assert got.confidence >= 0.9
    assert got.basis == "mask"
    assert got.conflict == ()


def test_stack_pair__without_independent_cue_confidence_stays_below_point_nine() -> None:
    """Bỏ bong bóng và cột của B (không đầu mối độc lập): vẫn tìm đúng dịch nhưng độ tin < 0,9."""
    a, b = synth((500, -300))
    got = stack_pair(a, replace(b, bubbles=(), col_mask=None))
    assert got.shift_mm is not None
    assert math.dist(got.shift_mm, (500, -300)) <= _TOL_MM
    assert got.confidence < 0.9
    assert got.confirmed_by == ()


def test_stack_pair__missing_scale_gives_no_answer() -> None:
    """Một tầng không có tỉ lệ (`mm_per_px` None hay 0): không trả lời, độ tin 0 — không đoán."""
    a, b = synth((500, -300))
    for bad in (None, 0.0):
        got = stack_pair(a, replace(b, mm_per_px=bad))
        assert (got.shift_mm, got.confidence, got.basis, got.candidates) == (None, 0.0, None, ())


def test_stack_pair__no_wall_mask_and_no_ink_gives_no_answer() -> None:
    """Không mặt nạ tường và không ảnh xám: không dựng được lớp kiểm → không trả lời."""
    a, b = synth((0, 0))
    got = stack_pair(replace(a, wall_mask=None), replace(b, wall_mask=None))
    assert got.shift_mm is None
    assert got.confidence == 0.0


def test_stack_pair__conflicting_independent_cues_cap_confidence() -> None:
    """Nhãn lưới nói 1000 mm ở hai trục mà mặt nạ giống hệt nói 0: kẹp ≤ 0,65 và ghi xung đột (cũ: 0,965 im lặng)."""
    got = stack_pair(*conflict_case())
    assert got.shift_mm == (0, 0)
    assert got.confidence <= 0.65
    assert any(c.cue == "grid" for c in got.conflict)
    assert got.confirmed_by == ("columns",)  # cột vẫn trùng đáp án, nhưng xung đột thắng


def test_stack_pair__result_is_deterministic() -> None:
    """Cùng đầu vào cho đúng cùng kết quả (lấy mẫu điểm tường dùng hạt giống cố định)."""
    a, b = synth((500, -300))
    assert stack_pair(a, b) == stack_pair(a, b)


def test_stack_pair__sparse_wall_mask_falls_back_to_ink() -> None:
    """DS-292: mặt nạ tường chỉ có vài đoạn (< 3000 điểm lưới) nên lớp kiểm lấy từ mực (`basis == "ink"`)."""
    a, b = (load_view(v) for v in _GOLDEN["DS-292_1-2"]["views"])
    assert a.wall_mask is not None
    assert a.ink is not None
    assert stack_pair(a, b).basis == "ink"
    no_ink = stack_pair(replace(a, ink=None), replace(b, ink=None))
    assert no_ink.shift_mm is None  # mặt nạ thưa mà không có mực → không còn lớp nào


def test_stack_pair__ink_at_other_scale_gives_same_shift() -> None:
    """Ảnh xám ở 1,0 ảnh dựng (phóng gấp đôi) cho cùng dịch như ở 0,5: bước co về nửa ảnh dựng đúng."""
    a, b = (load_view(v) for v in _GOLDEN["DS-571_1-2"]["views"])
    base = stack_pair(a, b)

    def double(o: FloorObservation) -> FloorObservation:
        assert o.ink is not None
        big = np.asarray(cv2.resize(o.ink, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST), dtype=np.uint8)
        return replace(o, ink=big, ink_scale=1.0)

    got = stack_pair(double(a), double(b))
    assert got.basis == "ink"
    assert base.shift_mm is not None
    assert got.shift_mm is not None
    assert math.dist(base.shift_mm, got.shift_mm) <= 25


@pytest.mark.parametrize(
    ("score", "ratio", "confirmed", "lo", "hi"),
    [
        (0.2, 0.5, True, 0.0, 0.7),  # điểm yếu → < 0,7
        (0.8, 0.99, True, 0.0, 0.7),  # hoà với đối thủ → < 0,7
        (0.8, 0.5, False, 0.7, 0.9),  # điểm tốt, một nguồn → 0,7-0,9
        (0.8, 0.5, True, 0.9, 1.0),  # điểm tốt, hai nguồn → ≥ 0,9
        (5.0, 0.0, True, 0.98, 0.981),  # trần 0,98
    ],
)
def test_confidence__two_source_rule(score: float, ratio: float, confirmed: bool, lo: float, hi: float) -> None:
    """Luật hai nguồn: ≥ 0,9 cần điểm tốt, đối thủ kém rõ ràng và có xác nhận; yếu/hoà dưới 0,7."""
    c = confidence(score, ratio, confirmed)
    assert lo <= c < hi
