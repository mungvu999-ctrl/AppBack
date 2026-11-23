"""Căn ngang một mặt cắt / mặt đứng vào mặt bằng bằng bong bóng trục lưới (K-32d).

Chuyển từ nhân nghiên cứu `register_h.py`. Mặt bằng cho vị trí mm của từng nhãn trục (`plan_axes`); mặt cắt cho các
bong bóng cùng một hàng (`row_labels`); khớp đường thẳng bền vững `x_mặt_cắt = s · x_mặt_bằng + t` trên nhãn chung
cho biết mặt cắt chạy dọc hướng nào của mặt bằng, có lật không, và gốc ở đâu. Nhãn đọc sai bị loại là ngoại lai.

Số đo nghiên cứu (nhãn hoàn hảo, 30 bộ dev+test, 102 mặt cắt/mặt đứng): 61 căn được (44 ở độ tin ≥ 0,9); 41 không
căn được vì bản vẽ không có bong bóng dùng được (36 hình < 2 bong bóng trong hàng). Số đo trên ảnh thật chưa có.
"""

import itertools
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Final, Literal, NamedTuple

from packages.domain.drawing_set.stacking.model import FloorObservation

TOL: Final = 100.0  # mm: một nhãn là nội lai khi lệch khỏi đường khớp không quá mức này
_SCALE_RANGE: Final = (0.5, 2.0)  # |hệ số| ngoài dải này không phải cùng một bản vẽ
_SCALE_OK: Final = 0.02  # |hệ số| lệch 1 không quá mức này là đúng tỉ lệ
_MIN_AXES: Final = 2  # số nhãn chung tối thiểu để khớp

Axis = Literal["x", "y"]
PlanAxes = dict[Axis, dict[str, float]]  # {"x": {nhãn: x mm}, "y": {nhãn: y mm}}


class _Line(NamedTuple):
    """Đường khớp `side = s · plan + t`: hệ số, độ dời, nhãn nội lai, sai số bình phương trung bình căn (mm)."""

    s: float
    t: float
    inliers: list[str]
    rms: float


@dataclass(frozen=True, slots=True)
class HorizontalFit:
    """Kết quả căn ngang. `along` None = không căn được, `why` ghi lý do (`no_scale`, `lt2_bubbles_in_row`, ...).

    `scale` âm nghĩa là mặt cắt nhìn từ phía đối diện (`flipped`). `offset_mm` là `t` của đường khớp.
    """

    along: Axis | None
    scale: float = 0.0
    offset_mm: float = 0.0
    flipped: bool = False
    n_axes: int = 0
    residual_mm: float = 0.0
    confidence: float = 0.0
    labels: tuple[str, ...] = ()
    why: str = ""


def plan_axes(plan: FloorObservation) -> PlanAxes:
    """Bong bóng cùng hàng cho x của nhãn, cùng cột cho y của nhãn (trung vị khi có nhiều bong bóng cùng nhãn)."""
    mm = plan.mm_per_px or 0.0
    vert: dict[str, list[float]] = defaultdict(list)
    horiz: dict[str, list[float]] = defaultdict(list)
    for b in plan.bubbles:
        row = {o.label for o in plan.bubbles if abs(o.y - b.y) < b.r and o is not b and o.label != b.label}
        col = {o.label for o in plan.bubbles if abs(o.x - b.x) < b.r and o is not b and o.label != b.label}
        if len(row) > len(col):
            vert[b.label].append(b.x)
        elif len(col) > len(row):
            horiz[b.label].append(b.y)
    return {
        "x": {k: statistics.median(v) * mm for k, v in vert.items()},
        "y": {k: statistics.median(v) * mm for k, v in horiz.items()},
    }


def row_labels(side: FloorObservation) -> dict[str, float]:
    """{nhãn: x mm} của hàng bong bóng nhiều nhãn nhất (cùng y trong một bán kính); rỗng khi < 2 nhãn."""
    mm = side.mm_per_px or 0.0
    rows = ([o for o in side.bubbles if abs(o.y - b.y) < b.r] for b in side.bubbles)
    best = max(rows, key=lambda g: len({o.label for o in g}), default=[])
    xs: dict[str, list[float]] = defaultdict(list)
    for o in best:
        xs[o.label].append(o.x * mm)
    return {k: statistics.median(v) for k, v in xs.items()} if len(xs) >= _MIN_AXES else {}


def _best_pair(side: dict[str, float], plan: dict[str, float]) -> list[str]:
    """Giả thuyết cặp nhãn cho nhiều nhãn nội lai nhất (hoà: sai số tổng nhỏ hơn); [] khi không giả thuyết hợp lệ."""
    labs = [lab for lab in side if lab in plan]
    best: tuple[tuple[int, float], list[str]] | None = None
    for a, b in itertools.combinations(labs, 2):
        if abs(plan[a] - plan[b]) < 1.0:
            continue
        s = (side[a] - side[b]) / (plan[a] - plan[b])
        if not _SCALE_RANGE[0] < abs(s) < _SCALE_RANGE[1]:
            continue
        t = side[a] - s * plan[a]
        inl = [lab for lab in labs if abs(s * plan[lab] + t - side[lab]) <= TOL]
        score = (len(inl), -sum(abs(s * plan[lab] + t - side[lab]) for lab in inl))
        if best is None or score > best[0]:
            best = (score, inl)
    return best[1] if best else []


def fit_line(side: dict[str, float], plan: dict[str, float]) -> _Line | None:
    """Khớp `side = s · plan + t` bền vững trên nhãn chung: cặp giả thuyết tốt nhất, rồi bình phương tối thiểu."""
    inl = _best_pair(side, plan)
    if len(inl) < _MIN_AXES:
        return None
    px, sy = [plan[lab] for lab in inl], [side[lab] for lab in inl]
    mx, my = statistics.fmean(px), statistics.fmean(sy)
    s = sum((p - mx) * (q - my) for p, q in zip(px, sy, strict=True)) / sum((p - mx) ** 2 for p in px)
    t = my - s * mx
    rms = (sum((s * p + t - q) ** 2 for p, q in zip(px, sy, strict=True)) / len(inl)) ** 0.5
    return _Line(s, t, inl, rms)


def _confidence(n: int, s: float) -> float:
    """Độ tin theo số trục nội lai n và hệ số s: ≥ 3 trục đúng tỉ lệ 0,9-1,0; 2 trục 0,8; sai tỉ lệ thì thấp hơn."""
    ok = abs(abs(s) - 1) <= _SCALE_OK
    if n >= 3:
        return 0.9 + 0.02 * min(n - 3, 5) if ok else 0.6
    return 0.8 if ok else 0.5


def register_h(side: FloorObservation, axes: PlanAxes) -> HorizontalFit:
    """Mặt cắt chạy dọc hướng nào của mặt bằng và ánh xạ x_mặt_cắt ↔ toạ độ mặt bằng; `along` None khi < 2 nhãn khớp."""
    if not side.mm_per_px:
        return HorizontalFit(None, why="no_scale")
    row = row_labels(side)
    if not row:
        return HorizontalFit(None, why="lt2_bubbles_in_row")
    cand: list[tuple[int, int, Axis, _Line | None]] = []
    families: tuple[Axis, Axis] = ("x", "y")
    for fam in families:
        n_match = sum(lab in axes[fam] for lab in row)
        fit = fit_line(row, axes[fam]) if n_match >= _MIN_AXES else None
        cand.append((n_match, len(fit.inliers) if fit else 0, fam, fit))
    n_match, _, fam, fit = max(cand, key=lambda c: c[:2])
    if n_match < _MIN_AXES:
        return HorizontalFit(None, why="lt2_labels_match_plan")
    if fit is None:
        return HorizontalFit(None, why="no_consistent_fit")
    return HorizontalFit(
        along=fam,
        scale=fit.s,
        offset_mm=fit.t,
        flipped=fit.s < 0,
        n_axes=len(fit.inliers),
        residual_mm=fit.rms,
        confidence=_confidence(len(fit.inliers), fit.s),
        labels=tuple(sorted(fit.inliers)),
    )
