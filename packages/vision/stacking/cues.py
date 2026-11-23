"""Bốn đầu mối đề xuất dịch chuyển giữa hai mặt bằng liền tầng: lưới trục, thang, tường, cột (K-32d).

Chuyển từ nhân nghiên cứu `stacking.py` (`_prep`, `cue_grid`, `cue_stairs`, `cue_walls`, `cue_columns`). Mỗi đầu mối
chỉ ĐỀ XUẤT một dịch (mm, đưa B lên A); bước kiểm (`layers.py`, `pair.py`) mới quyết định đáp án. Bản nghiên cứu còn
phiếu bầu `_vote` và độ chất lượng từng đầu mối: bản chồng hai bước (`stacking2.py`) không dùng, nên không chuyển.

Nhiều bộ chỉ ghi nhãn trục một hướng (số mà không có chữ): lưới một trục trả `(dx, None)` hay `(None, dy)`, trục kia
do bước kiểm tìm. Đầu mối bất định (hai đối thủ ngang điểm, thang/cột lặp theo chu kỳ) trả None: nhầm tệ hơn bỏ sót.
"""

import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

import cv2
import numpy as np
from numpy.typing import NDArray

from packages.vision.stacking.model import FloorObservation, Mask, PartialShift, Shift

GRID_TOL: Final = 200.0  # mm: một trục của B khớp một trục của A
STAIR_HIT: Final = 300.0  # mm: hai tâm thang gần hơn mức này là cùng một thang
COL_TOL: Final = 150.0  # mm: hai trọng tâm cột gần hơn mức này là cùng một cột
TARGET_MM: Final = 35.0  # mm mỗi điểm của lưới tương quan tường
PEAK_RATIO: Final = 1.15  # đỉnh tường phải trội hơn đỉnh xa nhất khác chừng này
MIN_COLS: Final = 3  # số cột khớp tối thiểu để đầu mối lên tiếng
_RIVAL_FACTOR: Final = 0.85  # đối thủ đạt chừng này điểm của đỉnh nhất thì đầu mối bất định
_MASK_ON: Final = 127  # mặt nạ: giá trị > mức này là điểm đặt
_MIN_COL_AREA: Final = 6  # điểm ảnh: đốm cột nhỏ hơn là nhiễu
_MIN_WALL_SUM: Final = 50.0  # tổng mặt nạ tường dưới mức này coi như không có tường

Bubbles = list[tuple[str, float, float, float]]  # (nhãn, x mm, y mm, bán kính mm)
Axes = dict[str, list[float]]  # nhãn → các vị trí (mm) của trục mang nhãn đó


@dataclass(frozen=True, slots=True)
class Prepared:
    """Quan sát đã đổi sang mm: bong bóng, ô thang (cx, cy, rộng, cao), mặt nạ tường làm mờ, cột (x, y, diện tích)."""

    bubbles: Bubbles
    stairs: list[tuple[float, float, float, float]]
    wall: NDArray[np.float32] | None
    cols: list[tuple[float, float, int]]


CueFn = Callable[[Prepared, Prepared], PartialShift | None]


def binarize(mask: NDArray[np.generic]) -> Mask:
    """Mặt nạ bất kỳ → uint8 0/1. Mặt nạ bool lấy nguyên; còn lại điểm đặt là giá trị > 127 (PNG 0/255)."""
    if mask.dtype == np.bool_:
        return mask.astype(np.uint8)
    return (np.asarray(mask, dtype=np.int32) > _MASK_ON).astype(np.uint8)


def centroids(mask01: Mask, mm_per_px: float) -> list[tuple[float, float, int]]:
    """Thành phần liên thông của mặt nạ cột → [(x mm, y mm, diện tích px)], bỏ đốm nhiễu."""
    n, _, stats, cen = cv2.connectedComponentsWithStats(mask01)
    return [
        (float(cen[i][0]) * mm_per_px, float(cen[i][1]) * mm_per_px, int(stats[i][4]))
        for i in range(1, n)
        if stats[i][4] >= _MIN_COL_AREA
    ]


def prepare(obs: FloorObservation, mm: float) -> Prepared:
    """Quan sát (px) → bong bóng/thang/tường/cột theo mm; `mm` là tỉ lệ đã biết của quan sát."""
    k = mm / obs.mask_scale  # mm mỗi điểm mặt nạ
    bubbles = [(b.label, b.x * mm, b.y * mm, max(b.r * mm, 150.0)) for b in obs.bubbles]
    stairs = [
        ((s.x0 + s.x1) / 2 * mm, (s.y0 + s.y1) / 2 * mm, (s.x1 - s.x0) * mm, (s.y1 - s.y0) * mm) for s in obs.stairs
    ]
    wall = None
    if obs.wall_mask is not None:
        f = k / TARGET_MM
        small = cv2.resize(binarize(obs.wall_mask).astype(np.float32), None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        wall = np.asarray(cv2.GaussianBlur(small, (0, 0), 1.0), dtype=np.float32)
    cols = centroids(binarize(obs.col_mask), k) if obs.col_mask is not None else []
    return Prepared(bubbles, stairs, wall, cols)


# ---------- đầu mối 1: lưới trục ----------


def _merge_axis(values: list[float]) -> list[float]:
    """Vị trí cùng nhãn gần nhau hơn GRID_TOL là một trục (hai đầu trục đều có bong bóng) → trung vị mỗi nhóm."""
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] < GRID_TOL:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [statistics.median(g) for g in groups]


def axes_of(bubbles: Bubbles) -> tuple[Axes, Axes]:
    """Bong bóng cùng hàng đánh dấu trục đứng (biết x), cùng cột đánh dấu trục ngang (biết y) → (V, H) theo nhãn."""
    vert: Axes = defaultdict(list)
    horiz: Axes = defaultdict(list)
    for lab, x, y, r in bubbles:
        row = sum(abs(y2 - y) < r and (l2, x2) != (lab, x) for l2, x2, y2, _ in bubbles)
        col = sum(abs(x2 - x) < r and (l2, y2) != (lab, y) for l2, x2, y2, _ in bubbles)
        if row > col:
            vert[lab].append(x)
        elif col > row:
            horiz[lab].append(y)
    return {k: _merge_axis(v) for k, v in vert.items()}, {k: _merge_axis(v) for k, v in horiz.items()}


class _Support(NamedTuple):
    """Độ ủng hộ một giả thuyết dịch `t` của lưới: điểm, phần dư các trục khớp nhãn, số khớp nhãn."""

    score: float
    residuals: list[float]
    label_hits: int
    t: float


def _support(t: float, a_axes: Axes, b_axes: Axes) -> _Support:
    """Các trục B rơi lên trục A sau dịch t: khớp nhãn tính 1,0, chỉ khớp vị trí tính 0,5."""
    all_a = [p for ps in a_axes.values() for p in ps]
    score, res, hits = 0.0, [], 0
    for lab, ps in b_axes.items():
        for p in ps:
            same = [a - (p + t) for a in a_axes.get(lab, []) if abs(a - (p + t)) < GRID_TOL]
            if same:
                score, hits = score + 1.0, hits + 1
                res.append(t + min(same, key=abs))
            elif any(abs(a - (p + t)) < GRID_TOL for a in all_a):
                score += 0.5
    return _Support(score, res, hits, t)


def _grid_dir(a_axes: Axes, b_axes: Axes) -> float | None:
    """Dịch tốt nhất theo một hướng; None khi không có hay mơ hồ (cần ≥ 2 trục khớp nhãn, không đối thủ gần ngang)."""
    hyp = [a - b for lab in sorted(set(a_axes) & set(b_axes)) for a in a_axes[lab] for b in b_axes[lab]]
    if not hyp:
        return None
    scored = sorted((_support(t, a_axes, b_axes) for t in hyp), key=lambda s: -s.score)
    best = scored[0]
    if best.label_hits < 2:
        return None
    rival = [s for s in scored if abs(s.t - best.t) > 2 * GRID_TOL]
    if rival and rival[0].score >= _RIVAL_FACTOR * best.score:
        return None
    return statistics.median(best.residuals)


def cue_grid(a: Prepared, b: Prepared) -> PartialShift | None:
    """Dịch của B lên A từ nhãn trục. Một hướng không có thì thành phần đó None; không hướng nào → None."""
    (va, ha), (vb, hb) = axes_of(a.bubbles), axes_of(b.bubbles)
    dx, dy = _grid_dir(va, vb), _grid_dir(ha, hb)
    return None if dx is None and dy is None else (dx, dy)


# ---------- đầu mối 2: thang ----------

_Stair = tuple[float, float, float, float]  # (cx, cy, rộng, cao) mm


def _iou(a: _Stair, b: _Stair) -> float:
    """IoU hai ô thang cho dạng (cx, cy, rộng, cao)."""
    w = min(a[0] + a[2] / 2, b[0] + b[2] / 2) - max(a[0] - a[2] / 2, b[0] - b[2] / 2)
    h = min(a[1] + a[3] / 2, b[1] + b[3] / 2) - max(a[1] - a[3] / 2, b[1] - b[3] / 2)
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / (a[2] * a[3] + b[2] * b[3] - w * h)


def _stair_fit(t: Shift, sa: list[_Stair], sb: list[_Stair]) -> tuple[float, list[Shift]]:
    """Mỗi thang A nhận thang B (đã dịch t) có IoU cao nhất trong STAIR_HIT → (điểm, dịch từng cặp khớp)."""
    score, res = 0.0, []
    for a in sa:
        cands = [
            (_iou(a, (b[0] + t[0], b[1] + t[1], b[2], b[3])), b)
            for b in sb
            if math.hypot(b[0] + t[0] - a[0], b[1] + t[1] - a[1]) < STAIR_HIT
        ]
        if cands:
            iou, b = max(cands, key=lambda c: c[0])
            score += 0.5 + iou
            res.append((a[0] - b[0], a[1] - b[1]))
    return score, res


def cue_stairs(a: Prepared, b: Prepared) -> Shift | None:
    """Dịch đưa nhiều thang A lên thang B nhất; hai đối thủ ngang điểm hoặc ít thang khớp trong số nhiều → None."""
    cands = [
        (x[0] - y[0], x[1] - y[1])
        for x in a.stairs
        for y in b.stairs
        if 0.6 < x[2] / max(y[2], 1) < 1.6 and 0.6 < x[3] / max(y[3], 1) < 1.6
    ]
    if not cands:
        return None
    fits = sorted(((*_stair_fit(t, a.stairs, b.stairs), t) for t in cands), key=lambda r: -r[0])
    score, res, best_t = fits[0]
    rival = [f for f in fits if math.hypot(f[2][0] - best_t[0], f[2][1] - best_t[1]) > STAIR_HIT]
    if (rival and rival[0][0] >= _RIVAL_FACTOR * score) or len(res) < round(0.5 * min(len(a.stairs), len(b.stairs))):
        return None
    return statistics.mean(r[0] for r in res), statistics.mean(r[1] for r in res)


# ---------- đầu mối 3: tường ----------


def _peak2(corr: NDArray[np.floating[Any]], iy: int, ix: int, r: int = 10) -> float:
    """Giá trị tương quan lớn nhất cách đỉnh hơn r điểm (tính cả vòng quanh của FFT)."""
    h, w = corr.shape
    dy = np.abs(np.arange(h)[:, None] - iy)
    dx = np.abs(np.arange(w)[None, :] - ix)
    far = (np.minimum(dy, h - dy) ** 2 + np.minimum(dx, w - dx) ** 2) > r * r
    return float(corr[far].max()) if far.any() else 0.0


def cue_walls(a: Prepared, b: Prepared) -> Shift | None:
    """Tương quan FFT hai mặt nạ tường; đỉnh phải trội PEAK_RATIO lần đỉnh xa nhất và ≥ 5 % tổng nhỏ hơn."""
    wa, wb = a.wall, b.wall
    if wa is None or wb is None or wa.sum() < _MIN_WALL_SUM or wb.sum() < _MIN_WALL_SUM:
        return None
    h = cv2.getOptimalDFTSize(wa.shape[0] + wb.shape[0])
    w = cv2.getOptimalDFTSize(wa.shape[1] + wb.shape[1])
    h, w = h + h % 2, w + w % 2
    corr = np.fft.irfft2(np.fft.rfft2(wa, (h, w)) * np.conj(np.fft.rfft2(wb, (h, w))), s=(h, w))
    iy, ix = (int(i) for i in np.unravel_index(np.argmax(corr), corr.shape))
    peak, rest = float(corr[iy, ix]), _peak2(corr, iy, ix)
    if peak < PEAK_RATIO * rest or peak < 0.05 * min(wa.sum(), wb.sum()):
        return None
    dy, dx = (iy - h if iy > h // 2 else iy), (ix - w if ix > w // 2 else ix)
    return dx * TARGET_MM, dy * TARGET_MM


# ---------- đầu mối 4: cột ----------

_Col = tuple[float, float, int]


def _col_fit(t: Shift, ca: list[_Col], cb: list[_Col]) -> list[Shift]:
    """Các cặp cột khớp một-một (theo khoảng cách < COL_TOL) dưới dịch t → dịch từng cặp."""
    pairs: list[Shift] = []
    used: set[int] = set()
    for a in ca:
        d = [(math.hypot(b[0] + t[0] - a[0], b[1] + t[1] - a[1]), j) for j, b in enumerate(cb) if j not in used]
        if d and min(d)[0] < COL_TOL:
            j = min(d)[1]
            used.add(j)
            pairs.append((a[0] - cb[j][0], a[1] - cb[j][1]))
    return pairs


def cue_columns(a: Prepared, b: Prepared) -> Shift | None:
    """Dịch khớp nhiều trọng tâm cột nhất (≥ MIN_COLS và ≥ 40 % số cột ít hơn); lưới cột lặp chu kỳ → None."""
    ca, cb = a.cols, b.cols
    if len(ca) < MIN_COLS or len(cb) < MIN_COLS:
        return None
    cands = [(x[0] - y[0], x[1] - y[1]) for x in ca for y in cb if 0.5 < x[2] / y[2] < 2.0]
    fits = sorted(((_col_fit(t, ca, cb), t) for t in cands), key=lambda r: -len(r[0]))
    if not fits or len(fits[0][0]) < MIN_COLS or len(fits[0][0]) < 0.4 * min(len(ca), len(cb)):
        return None
    pairs, best_t = fits[0]
    rival = [f for f in fits if math.hypot(f[1][0] - best_t[0], f[1][1] - best_t[1]) > 2 * COL_TOL]
    if rival and len(rival[0][0]) >= len(pairs):
        return None
    return statistics.mean(p[0] for p in pairs), statistics.mean(p[1] for p in pairs)


CUES: Final[tuple[tuple[str, CueFn], ...]] = (
    ("grid", cue_grid),
    ("stairs", cue_stairs),
    ("walls", cue_walls),
    ("columns", cue_columns),
)
