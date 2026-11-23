"""Hình học hộp và dịch chồng tầng dùng chung (K-32e): giao trên hộp nhỏ, tâm, dịch hộp, cộng dịch về tầng thấp."""

import math
from collections.abc import Sequence

from packages.domain.drawing_set.cores.types import Box, Point, StackShift

_MIN_WIDTH_MM = 800.0  # "một bề rộng thang" nằm trong 800-2500 mm
_MAX_WIDTH_MM = 2500.0


def move(box: Box, offset: Point) -> Box:
    """Hộp dịch đi `offset` mm."""
    return (box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1])


def centre(box: Box) -> Point:
    """Tâm hộp."""
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def overlap(a: Box, b: Box) -> float:
    """Diện tích giao chia cho diện tích hộp nhỏ hơn (0 khi không giao hoặc hộp suy biến)."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def stair_width(a: Box, b: Box) -> float:
    """Cỡ "một bề rộng thang": cạnh ngắn nhất của hai hộp, kẹp trong 800-2500 mm."""
    short = min(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return min(_MAX_WIDTH_MM, max(_MIN_WIDTH_MM, short))


def dist(a: Point, b: Point) -> float:
    """Khoảng cách hai điểm (mm)."""
    return math.dist(a, b)


def to_lowest(shifts: Sequence[StackShift | None], k0: int, k: int) -> Point | None:
    """Dịch đưa mặt bằng chỉ số k về khung mặt bằng chỉ số k0; None nếu thiếu một dịch dọc đường."""
    tx = ty = 0.0
    for m in range(k0, k):
        step = shifts[m]
        s = step.shift_mm if step else None
        if s is None:
            return None
        tx, ty = tx + s[0], ty + s[1]
    return (tx, ty)


def shift_confidence(shifts: Sequence[StackShift | None], k0: int, k: int) -> float:
    """Độ chắc nhỏ nhất của các dịch từ k0 tới k (thiếu dịch = 0)."""
    return min((step.confidence if (step := shifts[m]) else 0.0) for m in range(k0, k))
