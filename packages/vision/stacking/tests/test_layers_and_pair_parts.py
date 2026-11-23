"""Lớp kiểm và các mảnh của `pair.py` (K-32d): dựng lớp từ mặt nạ/mực, điểm kiểm, đề xuất, gộp, kẹp độ tin."""

import math
from dataclasses import replace

import cv2
import numpy as np
import pytest

from packages.vision.stacking import Box, Bubble, Conflict, FloorObservation, confidence
from packages.vision.stacking.layers import (
    MAX_PTS,
    MIN_PTS,
    G,
    Layer,
    _building_box,
    corr_peaks,
    ink_layer_mask,
    layer,
    refine,
    search_axis,
    verify,
)
from packages.vision.stacking.pair import (
    CAP_NO_CLEAR,
    CAP_UNCONFIRMED,
    _complete,
    _final_confidence,
    _gap,
    _merge,
    independent,
    proposals,
)
from packages.vision.stacking.tests.support import synth

_MM = 10.0


def _synth_layers(shift: tuple[float, float] = (500, -300)) -> tuple[Layer, Layer]:
    """Lớp kiểm của hai mặt bằng tổng hợp (cùng toà nhà, B dời -shift)."""
    a, b = synth(shift)
    la, lb = layer(a, _MM), layer(b, _MM)
    assert la is not None
    assert lb is not None
    return la, lb


def _ink_image(w: int = 600, h: int = 400, *, frame: bool = True) -> np.ndarray[tuple[int, ...], np.dtype[np.uint8]]:
    """Ảnh xám tổng hợp (nền trắng): khung tờ và hai tường 4 điểm dày dài 200 điểm (ngắn hơn nửa khổ giấy)."""
    g = np.full((h, w), 255, np.uint8)
    if frame:
        cv2.rectangle(g, (2, 2), (w - 3, h - 3), 0, 2)
    cv2.line(g, (150, 100), (350, 100), 0, 4)
    cv2.line(g, (150, 100), (150, 300), 0, 4)
    return g


def _bubbles_box() -> list[Bubble]:
    """Bốn bong bóng ở bốn góc của khung lưới 4 m x 3 m (px ảnh dựng, 10 mm/px, bán kính 5)."""
    return [Bubble("1", 200, 200, 5), Bubble("2", 1000, 200, 5), Bubble("A", 200, 800, 5), Bubble("B", 1000, 800, 5)]


def test_building_box__needs_four_bubbles_and_a_big_enough_box() -> None:
    """< 4 bong bóng hay hộp bé hơn 3 m một chiều → None; còn lại hộp co 1,3 bán kính, kẹp trong ảnh."""
    shape = (600, 800)
    assert _building_box(FloorObservation(_MM, _bubbles_box()[:3]), _MM, shape) is None
    tiny = [Bubble(str(i), 100 + 10 * i, 100 + 10 * i, 5) for i in range(4)]
    assert _building_box(FloorObservation(_MM, tiny), _MM, shape) is None
    flat = [Bubble(str(i), 100 + 200 * i, 100, 5) for i in range(4)]  # rộng đủ, cao 0
    assert _building_box(FloorObservation(_MM, flat), _MM, shape) is None
    box = _building_box(FloorObservation(_MM, _bubbles_box()), _MM, shape)
    assert box == (103, 103, 497, 397)  # nửa ảnh: x 100-500 co 3 điểm mỗi phía, kẹp bởi khổ 800x600
    assert _building_box(FloorObservation(_MM, _bubbles_box()), _MM, (200, 300)) == (103, 103, 300, 200)


def test_ink_layer_mask__no_ink_and_frame_dropped_without_bubbles() -> None:
    """Không ảnh xám → None; không lưới bong bóng: bỏ khung tờ, giữ tường dày; ít nét dày thì giữ toàn bộ mực."""
    assert ink_layer_mask(FloorObservation(_MM), _MM) is None
    obs = FloorObservation(_MM, ink=_ink_image(), ink_scale=0.5)
    got = ink_layer_mask(obs, _MM)
    assert got is not None
    mask, k = got
    assert k == 2 * _MM
    assert mask[2:6, :].sum() == 0
    assert mask[:, 2:6].sum() == 0
    assert mask[100, 200:300].sum() > 0  # tường dày vẫn còn
    long_wall = _ink_image()
    cv2.line(long_wall, (20, 350), (580, 350), 0, 4)  # dài hơn nửa khổ giấy: coi như đường khung/khung tên
    dropped = ink_layer_mask(FloorObservation(_MM, ink=long_wall, ink_scale=0.5), _MM)
    assert dropped is not None
    assert dropped[0][350, :].sum() == 0
    thin = np.full((400, 600), 255, np.uint8)
    cv2.line(thin, (150, 100), (350, 100), 0, 1)
    kept = ink_layer_mask(FloorObservation(_MM, ink=thin, ink_scale=0.5), _MM)
    assert kept is not None
    assert kept[0][100, 200:300].sum() > 0


def test_ink_layer_mask__inside_bubble_grid_erases_text_bubbles_and_solid_fills() -> None:
    """Có lưới bong bóng: chỉ giữ trong hộp lưới; hộp chữ, bong bóng và mảng tô đặc (> 300 mm) bị xoá."""
    g = np.full((450, 600), 255, np.uint8)
    cv2.line(g, (150, 160), (350, 160), 0, 4)  # tường trong hộp lưới (hộp ≈ x 103-497, y 103-397)
    cv2.line(g, (20, 20), (580, 20), 0, 4)  # đường ngoài hộp lưới (khung tên, chuỗi kích thước)
    cv2.rectangle(g, (250, 280), (330, 360), 0, -1)  # mảng tô đặc 80 điểm = 1,6 m
    cv2.circle(g, (300, 250), 5, 0, -1)  # ký hiệu giống bong bóng nằm trong hộp
    texts = [Box(400, 300, 500, 340)]  # nửa ảnh: x 200-250, y 150-170 — cắt ngang tường
    with_bubble = [*_bubbles_box(), Bubble("X", 600, 500, 5)]  # nửa ảnh (300, 250)
    plain = FloorObservation(_MM, _bubbles_box(), texts=texts, ink=g, ink_scale=0.5)
    got = ink_layer_mask(replace(plain, bubbles=with_bubble), _MM)
    assert got is not None
    mask, _ = got
    assert mask[:90, :].sum() == 0  # ngoài hộp lưới: đường ở y = 20 bị bỏ
    assert mask[158:162, 155:190].sum() > 0
    assert mask[158:162, 205:245].sum() == 0
    assert mask[290:350, 260:320].sum() == 0  # mảng tô đặc đã bị bỏ
    assert mask[245:255, 295:305].sum() == 0  # đĩa bong bóng xoá ký hiệu
    no_bubble = ink_layer_mask(plain, _MM)
    assert no_bubble is not None
    assert no_bubble[0][245:255, 295:305].sum() > 0


def test_layer__from_mask_ink_none_and_too_sparse() -> None:
    """Lớp từ mặt nạ có điểm/cột; thiếu, trống hay thưa hơn MIN_PTS → None; từ mực thì không đòi MIN_PTS."""
    a, _ = synth((0, 0))
    lay = layer(a, _MM)
    assert lay is not None
    assert len(lay.pts) >= MIN_PTS
    assert len(lay.cols) == 11
    assert layer(replace(a, wall_mask=None), _MM) is None
    assert layer(replace(a, wall_mask=np.zeros((10, 10), np.uint8)), _MM) is None
    sparse = np.zeros((300, 300), np.uint8)
    sparse[100:104, 20:200] = 255
    assert layer(FloorObservation(_MM, wall_mask=sparse), _MM) is None
    assert layer(FloorObservation(_MM, wall_mask=sparse, ink=None), _MM, use_ink=True) is None
    ink_obs = FloorObservation(_MM, ink=_ink_image(), ink_scale=0.5)
    assert layer(ink_obs, _MM, use_ink=True) is not None


def test_layer__caps_points_deterministically() -> None:
    """Mặt nạ đặc cỡ lớn: số điểm bị cắt xuống MAX_PTS bằng lấy mẫu có hạt giống cố định (hai lần cho cùng điểm)."""
    full = np.full(
        (260, 260), 255, np.uint8
    )  # 12,5 mm/điểm mặt nạ → 2 điểm lưới G mỗi điểm: 130x130... dùng tỉ lệ khác
    obs = FloorObservation(2 * G, wall_mask=full, mask_scale=1.0)  # 50 mm/điểm → 260 điểm = 13 m → 520x520 lưới
    first, second = layer(obs, 2 * G), layer(obs, 2 * G)
    assert first is not None
    assert second is not None
    assert len(first.pts) == MAX_PTS
    assert np.array_equal(first.pts, second.pts)


def test_verify__peaks_at_true_shift_and_falls_off() -> None:
    """Điểm kiểm cao nhất ở dịch đúng (≥ 1) và thấp hẳn khi lệch 2 m hay đẩy ra khỏi ảnh."""
    la, lb = _synth_layers()
    right = verify(la, lb, (500.0, -300.0))
    assert right >= 1.0
    assert verify(la, lb, (2500.0, -300.0)) < right - 0.3
    assert verify(la, lb, (90000.0, 0.0)) < 0.2  # ra ngoài bản đồ khoảng cách: coi như xa D0
    best, shift = refine(la, lb, (450.0, -250.0))
    assert best >= right - 1e-9
    assert math.dist(shift, (500.0, -300.0)) <= 25.0


def test_search_axis__finds_missing_component() -> None:
    """Biết x = 500, tìm y trong ±4 m theo bước 50 mm: ra -300; biết y, tìm x: ra 500."""
    la, lb = _synth_layers()
    assert search_axis(la, lb, 500.0, 1) == pytest.approx(-300.0, abs=50.0)
    assert search_axis(la, lb, -300.0, 0) == pytest.approx(500.0, abs=50.0)


def test_corr_peaks__top_peak_is_true_shift_and_peaks_are_spread() -> None:
    """Đỉnh cao nhất của tương quan nằm gần dịch đúng; các đỉnh cách nhau ≥ 400 mm theo ít nhất một trục."""
    la, lb = _synth_layers()
    peaks = corr_peaks(la, lb)
    assert math.dist(peaks[0], (500.0, -300.0)) <= 50.0
    assert len(peaks) == 4
    assert all(max(abs(p[0] - q[0]), abs(p[1] - q[1])) >= 400.0 for i, p in enumerate(peaks) for q in peaks[i + 1 :])
    assert corr_peaks(la, lb, k=1) == peaks[:1]


def test_complete__full_one_axis_and_nothing() -> None:
    """Dịch đủ hai trục giữ nguyên; thiếu y (hay x) thì tìm 1-D (hậu tố "(1 axis)"); không biết gì → None."""
    la, lb = _synth_layers()
    assert _complete((500.0, -300.0), la, lb) == ((500.0, -300.0), "")
    miss_y = _complete((500.0, None), la, lb)
    assert miss_y is not None
    assert miss_y[1] == "(1 axis)"
    assert miss_y[0][1] == pytest.approx(-300.0, abs=50.0)
    miss_x = _complete((None, -300.0), la, lb)
    assert miss_x is not None
    assert miss_x[1] == "(1 axis)"
    assert miss_x[0][0] == pytest.approx(500.0, abs=50.0)
    assert _complete((None, None), la, lb) is None


def test_proposals__zero_cues_and_correlation_peaks_named() -> None:
    """Đề xuất luôn có "zero" và các đỉnh "corr1…"; đầu mối im lặng (None) không thêm gì; lưới một trục có hậu tố."""
    la, lb = _synth_layers()
    out = dict(proposals({"grid": None, "stairs": (500.0, -300.0), "walls": (500.0, None)}, la, lb))
    assert "zero" in out
    assert out["zero"] == (0.0, 0.0)
    assert out["stairs"] == (500.0, -300.0)
    assert "walls(1 axis)" in out
    assert "grid" not in out
    assert {"corr1", "corr2"} <= set(out)


def test_independent__only_grid_and_columns_with_axis_suffix() -> None:
    """Chỉ lưới và cột là độc lập (thang/tường/zero không); lưới thiếu một trục mang tên "grid(1 axis)"."""
    cues = {"grid": (1.0, None), "stairs": (2.0, 2.0), "walls": (3.0, 3.0), "columns": (4.0, 4.0)}
    assert independent(cues) == [("grid(1 axis)", (1.0, None)), ("columns", (4.0, 4.0))]
    assert independent({"grid": (1.0, 2.0), "columns": None}) == [("grid", (1.0, 2.0))]
    assert independent({}) == []


def test_gap_and_merge() -> None:
    """`_gap`: lệch lớn nhất trên trục đã biết (0 khi chẳng biết gì); `_merge`: gộp ứng viên cách ≤ 100 mm."""
    assert _gap((10.0, None), (0.0, 500.0)) == 10.0
    assert _gap((None, None), (0.0, 500.0)) == 0.0
    merged = _merge([(0.5, (0.0, 0.0), ["a"]), (0.9, (50.0, 0.0), ["b", "a"]), (0.4, (900.0, 0.0), ["c"])])
    assert [(m.score, m.shift, m.names) for m in merged] == [(0.9, (50.0, 0.0), ["b", "a"]), (0.4, (900.0, 0.0), ["c"])]


def test_final_confidence__caps() -> None:
    """Đầu mối đủ hai trục mà không xác nhận: ≤ 0,8 khi điểm tốt, đối thủ kém rõ; không thì ≤ 0,69; xung đột ≤ 0,65."""
    ind = [("grid", (10.0, 20.0))]
    assert _final_confidence(0.9, 0.3, ind, [], []) <= CAP_UNCONFIRMED
    assert _final_confidence(0.9, 0.9, ind, [], []) <= CAP_NO_CLEAR
    one_axis = [("grid(1 axis)", (10.0, None))]
    assert _final_confidence(0.9, 0.3, one_axis, [], []) == confidence(0.9, 0.3, False)  # lưới một trục không kẹp
    assert _final_confidence(0.9, 0.3, ind, ["grid"], []) == confidence(0.9, 0.3, True)
    assert _final_confidence(0.9, 0.3, ind, ["grid"], [Conflict("columns", (1, None))]) <= 0.65
