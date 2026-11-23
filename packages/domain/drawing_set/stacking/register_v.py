"""Căn dọc một mặt cắt / mặt đứng từ các dấu cốt của nó: `y_px = y0 - k · cốt_m` (K-32d).

Chuyển từ nhân nghiên cứu `register_v.py`. Khác `drawing_set/sections.py` (HS-03, chỉ chữ OCR, không tỉ lệ): ở đây có
tỉ lệ bản vẽ `mm_per_px` nên kiểm được hệ số khớp so với tỉ lệ danh nghĩa (`scale_ratio` = k / (1000 / mm_per_px))
và độ tin dùng nó; mốc cốt lệch hơn 80 mm khỏi đường khớp là ngoại lai (cốt hình bên cạnh, cốt mái đặt lệch, đọc nhầm).

Số đo nghiên cứu (nhãn hoàn hảo, 30 bộ dev+test, 102 mặt cắt/mặt đứng): 84 có ≥ 2 giá trị cốt phân biệt và đều khớp
được (75 ở độ tin ≥ 0,9); hệ số trong 2 % của 1 ở 94 % (5 ca còn lại đều gấp đôi: tỉ lệ ghi trên hình sai một nửa);
phần dư trung vị 0 mm, p90 19,9 mm; ngoại lai 92/1116 mốc = 8 %; 155/176 cốt sàn trùng một mốc nội lai (≤ 0,06 m).
"""

import itertools
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

TOL_MM: Final = 80.0  # một mốc là nội lai khi cách đường khớp không quá mức này (mm)
_SCALE_BAND: Final = 0.05  # giả thuyết đòi hệ số lệch tỉ lệ danh nghĩa không quá mức này
_ROUND_M: Final = 3  # làm tròn giá trị cốt (m) khi đếm giá trị phân biệt


@dataclass(frozen=True, slots=True)
class LevelPoint:
    """Một dấu cốt trên mặt cắt: giá trị (m) và toạ độ y (px ảnh dựng)."""

    value_m: float
    y: float


@dataclass(frozen=True, slots=True)
class VerticalFit:
    """Đường khớp dọc `y_px = y0_px - px_per_m · cốt_m`, mốc ngoại lai và độ tin (0,5-1,0)."""

    y0_px: float
    px_per_m: float
    scale_ratio: float
    n_marks: int
    n_inliers: int
    residual_mm: float
    outliers: tuple[LevelPoint, ...]
    confidence: float


def _confidence(distinct: int, ratio: float) -> float:
    """Độ tin theo số giá trị cốt phân biệt nội lai và hệ số so tỉ lệ danh nghĩa (≥ 3 giá trị, ±2 %: 0,9-1,0)."""
    r = abs(ratio - 1)
    if distinct >= 3 and r <= 0.02:
        return 0.9 + 0.02 * min(distinct - 3, 5)
    if distinct >= 3 and r <= _SCALE_BAND:
        return 0.8
    if distinct == 2 and r <= 0.02:
        return 0.8
    if distinct == 2 and r <= _SCALE_BAND:
        return 0.7
    return 0.5 if distinct >= 2 else 0.0


def _distinct(marks: Sequence[LevelPoint]) -> int:
    """Số giá trị cốt phân biệt (làm tròn mm)."""
    return len({round(m.value_m, _ROUND_M) for m in marks})


def _lsq(marks: Sequence[LevelPoint]) -> tuple[float, float]:
    """Bình phương tối thiểu `y = y0 - k·v` trên ≥ 2 giá trị v phân biệt → (y0, k)."""
    mv, my = statistics.fmean(m.value_m for m in marks), statistics.fmean(m.y for m in marks)
    k = -sum((m.value_m - mv) * (m.y - my) for m in marks) / sum((m.value_m - mv) ** 2 for m in marks)
    return my + k * mv, k


def _best_hypothesis(marks: Sequence[LevelPoint], nominal: float, tol: float) -> tuple[float, float] | None:
    """Giả thuyết cặp mốc (y0, k) tốt nhất theo (hệ số trong 5 %, số giá trị nội lai, số mốc nội lai, -sai số)."""
    best: tuple[tuple[bool, int, int, float], float, float] | None = None
    for a, b in itertools.combinations(marks, 2):
        if abs(a.value_m - b.value_m) < 1e-6:
            continue
        k = (a.y - b.y) / (b.value_m - a.value_m)
        if k <= 0:
            continue
        y0 = a.y + k * a.value_m
        err = [(abs(y0 - k * m.value_m - m.y), m) for m in marks]
        inl = [(e, m) for e, m in err if e <= tol]
        score = (abs(k / nominal - 1) <= _SCALE_BAND, _distinct([m for _, m in inl]), len(inl), -sum(e for e, _ in inl))
        if best is None or score > best[0]:
            best = (score, y0, k)
    return None if best is None else (best[1], best[2])


def register_v(marks: Sequence[LevelPoint], mm_per_px: float | None) -> VerticalFit | None:
    """Khớp đường bền vững trên các dấu cốt; None khi thiếu tỉ lệ, < 2 giá trị cốt phân biệt hay không có giả thuyết."""
    if not mm_per_px or _distinct(marks) < 2:
        return None
    nominal, tol = 1000.0 / mm_per_px, TOL_MM / mm_per_px
    hyp = _best_hypothesis(marks, nominal, tol)
    if hyp is None:
        return None
    y0, k = hyp
    inl = [m for m in marks if abs(y0 - k * m.value_m - m.y) <= tol]
    if _distinct(inl) >= 2:
        y0, k = _lsq(inl)
        inl = [m for m in marks if abs(y0 - k * m.value_m - m.y) <= tol]
    res = (sum((y0 - k * m.value_m - m.y) ** 2 for m in inl) / len(inl)) ** 0.5 * mm_per_px
    outliers = tuple(m for m in marks if abs(y0 - k * m.value_m - m.y) > tol)
    return VerticalFit(
        y0_px=y0,
        px_per_m=k,
        scale_ratio=k / nominal,
        n_marks=len(marks),
        n_inliers=len(inl),
        residual_mm=res,
        outliers=outliers,
        confidence=_confidence(_distinct(inl), k / nominal),
    )


def level_rows(fit: VerticalFit, levels_m: Sequence[float]) -> dict[float, float]:
    """{cốt: y px} trên đường khớp: dòng sàn của từng tầng trên mặt cắt."""
    return {v: fit.y0_px - fit.px_per_m * v for v in levels_m}
