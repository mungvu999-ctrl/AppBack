"""`swing.read_leaf`: cửa một cánh vẽ tổng hợp (cánh + cung 1/4) ở 4 hướng tường x 2 bản lề x 2 phía; cửa đôi, cửa lùa
(K-13a)."""

import math

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from apps.worker.pipeline_build import swing
from apps.worker.pipeline_build.residual import PageRaster
from apps.worker.pipeline_build.swing import Leaf, Vec, read_leaf, rule_side, settle_reversed
from packages.domain.spatial import sample_layer

SIZE = 200
WIDTH = 60.0
HALF = 5.0
CENTRE = np.array([100.0, 100.0])


def _door(direction: Vec, hinge_at_b: bool, side: int, *, arc: bool = True) -> tuple[PageRaster, tuple[Vec, Vec]]:
    """Trang trắng, tường dày 10 px theo `direction` có khe rộng 60 px giữa trang, cánh + cung vẽ đen."""
    u = np.array(direction, float)
    n = np.array([-u[1], u[0]])
    a, b = CENTRE - u * WIDTH / 2, CENTRE + u * WIDTH / 2
    gray = np.full((SIZE, SIZE), 255, np.uint8)
    walls = np.zeros((SIZE, SIZE), np.uint8)
    for end, out in ((a, -u), (b, u)):
        cv2.line(walls, tuple(np.rint(end).astype(int)), tuple(np.rint(end + out * 80).astype(int)), 1, int(2 * HALF))
    gray[walls > 0] = 60
    hinge, other = (b, a) if hinge_at_b else (a, b)
    pivot = hinge + side * n * HALF
    tip = pivot + side * n * WIDTH
    cv2.line(gray, tuple(np.rint(pivot).astype(int)), tuple(np.rint(tip).astype(int)), 0, 2)
    if arc:
        t = (other - hinge) / WIDTH
        pts = [pivot + WIDTH * (t * math.cos(q) + side * n * math.sin(q)) for q in np.linspace(0, math.pi / 2, 40)]
        cv2.polylines(gray, [np.rint(pts).astype(np.int32)], False, 0, 1)
    return PageRaster(gray=gray, walls=walls.astype(bool)), ((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))


@pytest.mark.parametrize("direction", [(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)])
@pytest.mark.parametrize("hinge_at_b", [False, True])
@pytest.mark.parametrize("side", [1, -1])
def test_reads_hinge_and_side(direction: tuple[float, float], hinge_at_b: bool, side: int) -> None:
    page, (a, b) = _door(direction, hinge_at_b, side)
    assert read_leaf(page, a, b, HALF) == Leaf(hinge_at_b=hinge_at_b, side=side)


def test_no_arc_is_none() -> None:
    """Chỉ có nét cánh (hay đường kích thước) mà không có cung → không đoán."""
    page, (a, b) = _door((1.0, 0.0), False, 1, arc=False)
    assert read_leaf(page, a, b, HALF) is None


def test_blank_and_narrow_are_none() -> None:
    blank = PageRaster(gray=np.full((SIZE, SIZE), 255, np.uint8), walls=np.zeros((SIZE, SIZE), bool))
    assert read_leaf(blank, (70.0, 100.0), (130.0, 100.0), HALF) is None
    page, _ = _door((1.0, 0.0), False, 1)
    assert read_leaf(page, (95.0, 100.0), (100.0, 100.0), HALF) is None


def test_settle_reversed_flips_only_against_rule() -> None:
    """Cánh đọc được cùng phía luật FE → giữ `reversed=False`; ngược phía → `True`."""
    layer = sample_layer(0)
    opening = layer.openings[0]
    wall = next(w for w in layer.walls if w.id == opening.wall_id)
    start, end = wall.centreline.start, wall.centreline.end
    length = math.hypot(end.x - start.x, end.y - start.y)
    rule = rule_side(opening, wall, layer.rooms)
    normal = (-(end.y - start.y) / length * rule, (end.x - start.x) / length * rule)
    same = settle_reversed(layer, {opening.id: normal})
    flipped = settle_reversed(layer, {opening.id: (-normal[0], -normal[1])})
    assert not same.openings[0].reversed
    assert flipped.openings[0].reversed
    assert settle_reversed(layer, {}) is layer


# K-13a: giả thuyết hai cánh và ray cửa lùa. Trang 20 mm/px: 1200/1400/1800 mm = 60/70/90 px, cửa đơn 800 mm = 40 px.
MM_PER_PX = 20.0


def _gap_page(
    direction: Vec, width: float
) -> tuple[NDArray[np.uint8], NDArray[np.uint8], NDArray[np.float64], tuple[Vec, Vec]]:
    """Trang trắng + tường dày 10 px theo `direction`, khe rộng `width` px giữa trang; trả (xám, mặt nạ, n, (a, b))."""
    u = np.array(direction, float)
    n = np.array([-u[1], u[0]])
    a, b = CENTRE - u * width / 2, CENTRE + u * width / 2
    gray = np.full((SIZE, SIZE), 255, np.uint8)
    walls = np.zeros((SIZE, SIZE), np.uint8)
    for end, out in ((a, -u), (b, u)):
        cv2.line(walls, _px(end), _px(end + out * 80), 1, int(2 * HALF))
    gray[walls > 0] = 60
    return gray, walls, n, ((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))


def _px(p: NDArray[np.float64]) -> tuple[int, int]:
    return round(float(p[0])), round(float(p[1]))


def _double_door(direction: Vec, side: int, width_mm: float) -> tuple[PageRaster, tuple[Vec, Vec]]:
    """Cửa đôi: hai cánh dài rộng/2 vuông tường ở hai đầu khe cùng phía `side`, mỗi cánh một cung 1/4 về tâm khe."""
    width = width_mm / MM_PER_PX
    gray, walls, n, (a, b) = _gap_page(direction, width)
    u = (np.array(b) - np.array(a)) / width
    for hinge, toward in ((np.array(a), u), (np.array(b), -u)):
        pivot = hinge + side * n * HALF
        cv2.line(gray, _px(pivot), _px(pivot + side * n * width / 2), 0, 2)
        pts = [
            pivot + width / 2 * (toward * math.cos(q) + side * n * math.sin(q)) for q in np.linspace(0, math.pi / 2, 40)
        ]
        cv2.polylines(gray, [np.rint(pts).astype(np.int32)], False, 0, 1)
    return PageRaster(gray=gray, walls=walls.astype(bool)), (a, b)


def _sliding_door(direction: Vec, width_mm: float) -> tuple[PageRaster, tuple[Vec, Vec]]:
    """Cửa lùa: hai tấm mảnh song song tường trong bề dày tường, lệch nhau 4 px, mỗi tấm phủ 60 % khe từ một đầu."""
    width = width_mm / MM_PER_PX
    gray, walls, n, (a, b) = _gap_page(direction, width)
    pa, pb = np.array(a), np.array(b)
    cv2.line(gray, _px(pa - n * 2), _px(pa + (pb - pa) * 0.6 - n * 2), 0, 1)
    cv2.line(gray, _px(pb + n * 2), _px(pb + (pa - pb) * 0.6 + n * 2), 0, 1)
    return PageRaster(gray=gray, walls=walls.astype(bool)), (a, b)


DIRECTIONS = [(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)]


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("width_mm", [1200.0, 1400.0, 1800.0])
def test_double_door_reads_double(direction: Vec, side: int, width_mm: float) -> None:
    page, (a, b) = _double_door(direction, side, width_mm)
    assert read_leaf(page, a, b, HALF) == Leaf(hinge_at_b=False, side=side, kind="double")


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_sliding_door_with_tracks_reads_sliding(direction: Vec) -> None:
    page, (a, b) = _sliding_door(direction, 1200.0)
    assert read_leaf(page, a, b, HALF) == Leaf(hinge_at_b=False, side=0, kind="sliding")


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("hinge_at_b", [False, True])
def test_single_800_unchanged(direction: Vec, hinge_at_b: bool) -> None:
    """Cửa đơn 800 mm vẫn đọc một cánh (giả thuyết hai cánh không đủ ngưỡng trên nửa không có cung)."""
    gray, walls, n, (a, b) = _gap_page(direction, 800.0 / MM_PER_PX)
    width = 800.0 / MM_PER_PX
    hinge, other = (np.array(b), np.array(a)) if hinge_at_b else (np.array(a), np.array(b))
    pivot = hinge + n * HALF
    cv2.line(gray, _px(pivot), _px(pivot + n * width), 0, 2)
    t = (other - hinge) / width
    pts = [pivot + width * (t * math.cos(q) + n * math.sin(q)) for q in np.linspace(0, math.pi / 2, 40)]
    cv2.polylines(gray, [np.rint(pts).astype(np.int32)], False, 0, 1)
    assert read_leaf(PageRaster(gray=gray, walls=walls.astype(bool)), a, b, HALF) == Leaf(hinge_at_b=hinge_at_b, side=1)


@pytest.mark.parametrize("across", [0.0, 3.0])
def test_dimension_line_across_gap_is_none(across: float) -> None:
    """Đường kích thước / ngưỡng chạy hết khe (không so le, không cung) → không đoán, kể cả hai nét song song."""
    gray, walls, n, (a, b) = _gap_page((1.0, 0.0), 1200.0 / MM_PER_PX)
    pa, pb = np.array(a), np.array(b)
    cv2.line(gray, _px(pa - n * across - (pb - pa) * 0.2), _px(pb - n * across + (pb - pa) * 0.2), 0, 1)
    cv2.line(gray, _px(pa + n * 3), _px(pb + n * 3), 0, 1)
    assert read_leaf(PageRaster(gray=gray, walls=walls.astype(bool)), a, b, HALF) is None


def test_switch_off_restores_single_leaf_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DOOR_KIND_HYPOTHESES = False`: cửa đôi và cửa lùa lại không đọc được (hành vi trước K-13a)."""
    monkeypatch.setattr(swing, "DOOR_KIND_HYPOTHESES", False)
    double, (a, b) = _double_door((1.0, 0.0), 1, 1400.0)
    sliding, (c, d) = _sliding_door((1.0, 0.0), 1200.0)
    assert read_leaf(double, a, b, HALF) is None
    assert read_leaf(sliding, c, d, HALF) is None
    page, (e, f) = _door((1.0, 0.0), True, -1)
    assert read_leaf(page, e, f, HALF) == Leaf(hinge_at_b=True, side=-1)


def test_off_page_gap_is_none() -> None:
    page, _ = _door((1.0, 0.0), False, 1)
    assert read_leaf(page, (-400.0, -400.0), (-300.0, -400.0), HALF) is None
