"""Bốn đầu mối đề xuất (K-32d): lưới trục, thang, tường, cột — ca thường, bất định (trả None) và biên."""

import numpy as np
import pytest

from packages.domain.drawing_set.stacking import Box, Bubble, FloorObservation
from packages.domain.drawing_set.stacking.cues import (
    Prepared,
    _peak2,
    binarize,
    cue_columns,
    cue_grid,
    cue_stairs,
    cue_walls,
    prepare,
)
from packages.domain.drawing_set.stacking.tests.support import synth

_MM = 10.0


def _prep(obs: FloorObservation) -> Prepared:
    """Quan sát → dạng mm ở tỉ lệ 10 mm/px."""
    return prepare(obs, _MM)


def _row(labels_x: list[tuple[str, float]], y: float = 100.0) -> Prepared:
    """Một hàng bong bóng (nhãn, x px) trên cùng y: các trục đứng của lưới."""
    return _prep(FloorObservation(_MM, [Bubble(lab, x, y, 5) for lab, x in labels_x]))


def _stairs(boxes: list[tuple[float, float, float, float]]) -> Prepared:
    """Mặt bằng chỉ có các ô thang (px)."""
    return _prep(FloorObservation(_MM, stairs=[Box(*b) for b in boxes]))


def _cols(points: list[tuple[int, int]]) -> Prepared:
    """Mặt bằng chỉ có mặt nạ cột: mỗi điểm (x, y) mặt nạ là một đốm 4x4 (mặt nạ ở 0,5 → 20 mm/điểm)."""
    m = np.zeros((300, 300), np.uint8)
    for x, y in points:
        m[y : y + 4, x : x + 4] = 255
    return _prep(FloorObservation(_MM, col_mask=m))


def test_binarize__bool_and_uint8_masks() -> None:
    """Mặt nạ bool lấy nguyên; uint8 chỉ giá trị > 127 là điểm đặt (127 không)."""
    assert binarize(np.array([[True, False]])).tolist() == [[1, 0]]
    assert binarize(np.array([[255, 128, 127, 0]], np.uint8)).tolist() == [[1, 1, 0, 0]]


def test_prepare__empty_observation_has_no_wall_no_columns() -> None:
    """Quan sát không mặt nạ: không tường, không cột; bong bóng đổi sang mm, bán kính tối thiểu 150 mm."""
    p = _prep(FloorObservation(_MM, [Bubble("A", 10, 20, 5)], [Box(0, 0, 10, 20)]))
    assert p.wall is None
    assert p.cols == []
    assert p.bubbles == [("A", 100.0, 200.0, 150.0)]
    assert p.stairs == [(50.0, 100.0, 100.0, 200.0)]


def test_cue_grid__two_axes_one_axis_and_none() -> None:
    """Lưới có cả hàng lẫn cột → dịch đủ hai trục; chỉ một hàng → trục y None; không bong bóng → None."""
    a, b = synth((500, -300))
    full = cue_grid(_prep(a), _prep(b))
    assert full is not None
    assert full[0] == pytest.approx(500)
    assert full[1] == pytest.approx(-300)
    one = cue_grid(_row([("1", 10), ("2", 30), ("3", 50)]), _row([("1", 0), ("2", 20), ("3", 40)]))
    assert one is not None
    assert one[0] == pytest.approx(100)
    assert one[1] is None
    assert cue_grid(_prep(FloorObservation(_MM)), _prep(FloorObservation(_MM))) is None


def test_cue_grid__single_label_match_or_periodic_rival_is_ambiguous() -> None:
    """Chỉ một nhãn chung (< 2 khớp nhãn) → None; hai dịch cách xa được ủng hộ ngang nhau (nhãn lặp) → None."""
    assert cue_grid(_row([("1", 10), ("2", 30)]), _row([("1", 0), ("9", 20)])) is None
    a = _row([("1", 0), ("2", 100), ("1", 200), ("2", 300)])
    b = _row([("1", 0), ("2", 100)])
    assert cue_grid(a, b) is None  # dịch 0 và 2000 mm đều khớp cả hai nhãn


def test_cue_stairs__match_and_none_cases() -> None:
    """Thang trùng hình → dịch giữa hai tâm; khác cỡ, ít cặp khớp trong số nhiều, hay hai đối thủ ngang nhau → None."""
    a = _stairs([(100, 100, 120, 160)])
    assert cue_stairs(a, _stairs([(70, 90, 90, 150)])) == pytest.approx((300.0, 100.0))
    assert cue_stairs(a, _stairs([(0, 0, 400, 400)])) is None  # khác cỡ quá 1,6 lần
    pair_a = _stairs([(0, 0, 10, 20), (300, 0, 310, 20), (0, 300, 10, 320)])
    only_one = _stairs([(0, 0, 10, 20), (1000, 1000, 1010, 1020), (2000, 2000, 2010, 2020)])
    assert cue_stairs(pair_a, only_one) is None  # chỉ 1 trong 3 thang khớp (< một nửa)
    row_a = _stairs([(0, 0, 10, 20), (200, 0, 210, 20), (400, 0, 410, 20)])
    row_b = _stairs([(0, 0, 10, 20), (200, 0, 210, 20)])
    assert cue_stairs(row_a, row_b) is None  # B khớp A ở hai chỗ (dịch 0 và 2000 mm) ngang điểm: bất định


def test_cue_walls__missing_weak_and_clear_peaks() -> None:
    """Thiếu/ít tường → None; tường trùng nhau → dịch 0; tường bị dời 40 điểm mặt nạ → dịch ngược dấu theo mm."""
    assert cue_walls(_prep(FloorObservation(_MM)), _prep(FloorObservation(_MM))) is None
    tiny = np.zeros((40, 40), np.uint8)
    tiny[5, 5] = 255
    assert cue_walls(_prep(FloorObservation(_MM, wall_mask=tiny)), _prep(FloorObservation(_MM, wall_mask=tiny))) is None
    base = np.zeros((300, 400), np.uint8)
    base[50:54, 40:300] = 255
    base[50:200, 100:104] = 255
    base[180:184, 60:250] = 255
    moved = np.roll(base, (0, 40), axis=(0, 1))  # dời 40 điểm mặt nạ = 40 x 20 mm = 800 mm
    a, same, b = (_prep(FloorObservation(_MM, wall_mask=m)) for m in (base, base, moved))
    assert cue_walls(a, same) == pytest.approx((0.0, 0.0), abs=35.0)
    got = cue_walls(a, b)
    assert got is not None
    assert got[0] == pytest.approx(-800.0, abs=70.0)
    assert got[1] == pytest.approx(0.0, abs=35.0)
    back = cue_walls(b, a)  # đổi chiều: dx dương, đi qua nhánh bọc vòng của FFT
    assert back is not None
    assert back[0] == pytest.approx(800.0, abs=70.0)


def test_cue_walls__periodic_walls_have_no_dominant_peak() -> None:
    """Tường lặp theo chu kỳ (lưới đều): đỉnh ở dịch 0 chỉ hơn đỉnh dịch một chu kỳ vài phần trăm → None."""
    lattice = np.zeros((400, 400), np.uint8)
    lattice[::20, :] = 255
    lattice[:, ::20] = 255
    assert (
        cue_walls(_prep(FloorObservation(_MM, wall_mask=lattice)), _prep(FloorObservation(_MM, wall_mask=lattice)))
        is None
    )


def test_peak2__window_covering_everything_returns_zero() -> None:
    """Mọi điểm đều trong bán kính loại trừ (mảng nhỏ) thì không có đỉnh xa: 0,0."""
    assert _peak2(np.ones((4, 4)), 0, 0) == 0.0
    big = np.zeros((40, 40))
    big[20, 20] = 3.0
    big[0, 0] = 2.0
    assert _peak2(big, 20, 20) == 2.0


def test_cue_columns__match_and_none_cases() -> None:
    """Đủ cột khớp → dịch; < 3 cột, ít cột khớp (< 40 %), hay lưới cột lặp chu kỳ → None."""
    pts_a = [(10, 10), (60, 10), (10, 70), (80, 90), (30, 40)]
    pts_b = [(x - 8, y + 3) for x, y in pts_a]
    got = cue_columns(_cols(pts_a), _cols(pts_b))
    assert got == pytest.approx((160.0, -60.0), abs=1e-6)
    assert cue_columns(_cols(pts_a[:2]), _cols(pts_b)) is None  # bên A < 3 cột
    scattered = [(200, 200), (250, 20), (20, 260), (120, 150), (280, 280), (150, 30)]
    assert cue_columns(_cols(pts_a), _cols(scattered)) is None  # không dịch nào khớp đủ cột
    row = [(10 + 50 * i, 10) for i in range(6)]
    assert cue_columns(_cols(row), _cols(row[:3])) is None  # hàng cột đều: dịch 0, 1000, 2000... khớp 3 cột ngang nhau
