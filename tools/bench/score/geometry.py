"""Hình học và mức chấm dùng chung: khoảng cách điểm-đoạn, điểm trong đa giác, mức Đ/C/T theo ngưỡng."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[Any]
Point = tuple[float, float]


def band(v: float | None, good: float, warn: float) -> str:
    """Mức Đ/C/T của số đo `v` (càng nhỏ càng tốt) theo hai ngưỡng; `v` None -> "—" (không đo được)."""
    if v is None:
        return "—"
    return "Đ" if v <= good else "C" if v <= warn else "T"


def count_crit(got: int, want: int | None, confident: bool) -> str:
    """Chấm số đếm (cửa, cửa sổ) so đáp án: lệch ≤ 1 (thêm 1 nếu đáp án không chắc) và ≥ 70 % → Đ."""
    if want is None:
        return "—"
    d, slack = abs(got - want), 0 if confident else 1
    if d <= 1 + slack and got >= 0.7 * want:
        return "Đ"
    return "C" if d <= 2 + slack else "T"


def seg_dist(p: Arr, segs: Arr) -> Arr:
    """Khoảng cách từ các điểm p (N,2) tới tập đoạn (M,4) → (N,) nhỏ nhất; không có đoạn → vô cực."""
    if len(segs) == 0:
        return np.full(len(p), np.inf)
    a, b = segs[:, :2][None], segs[:, 2:][None]
    ab = b - a
    t = np.clip(((p[:, None] - a) * ab).sum(-1) / np.maximum((ab**2).sum(-1), 1e-9), 0, 1)
    return np.asarray(np.linalg.norm(p[:, None] - (a + t[..., None] * ab), axis=-1).min(1))


def sample(segs: Arr, step: float = 50) -> Arr:
    """Lấy mẫu điểm dọc các đoạn (M,4), cách nhau ≤ `step` mm; không có đoạn → mảng (0,2)."""
    pts = [np.linspace(s[:2], s[2:], max(2, int(math.dist(s[:2], s[2:]) / step) + 1)) for s in segs]
    return np.concatenate(pts) if pts else np.zeros((0, 2))


def inside(pt: Point, poly: list[Point]) -> bool:
    """Điểm có nằm trong đa giác không (quy tắc chẵn-lẻ, tia ngang)."""
    x, y = pt
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _cross(o: Point, a: Point, b: Point) -> float:
    """Tích có hướng (a-o)x(b-o)."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def self_cross(poly: list[Point]) -> bool:
    """Đa giác có hai cạnh không kề cắt nhau (tự cắt) không."""
    e = list(zip(poly, poly[1:] + poly[:1], strict=True))
    for i in range(len(e)):
        for j in range(i + 2, len(e)):
            if i == 0 and j == len(e) - 1:
                continue
            (p1, p2), (p3, p4) = e[i], e[j]
            if _cross(p1, p2, p3) * _cross(p1, p2, p4) < 0 and _cross(p3, p4, p1) * _cross(p3, p4, p2) < 0:
                return True
    return False
