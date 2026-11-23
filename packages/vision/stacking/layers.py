"""Lớp kiểm (verification layer) của chồng tầng và điểm trùng khớp hai lớp dưới một dịch chuyển (K-32d).

Chuyển từ `stacking2.py` (`_building_box`, `_ink`, `layer`, `_dir_score`, `verify`, `refine`, `corr_peaks`,
`search_axis`). Một lớp là tập điểm tường trên lưới G mm/điểm kèm bản đồ khoảng cách; điểm kiểm của một dịch t là mức
tường của tầng này rơi gần tường của tầng kia (cắt ở D0 mm), lấy hướng tốt hơn trong hai hướng, cộng thưởng cột.

Lớp lấy từ MẶT NẠ TƯỜNG; khi mặt nạ thiếu hay thưa (< MIN_PTS điểm lưới, vài đoạn tường) lùi về MỰC của bản vẽ đã
làm sạch: xoá hộp chữ và bong bóng; chỉ giữ trong hộp bong bóng trục (bỏ khung, khung tên, chuỗi kích thước ngoài lưới)
trừ khối đặc > 300 mm; không có lưới bong bóng thì bỏ khung/đường dài và giữ nét dày (~80 mm).
"""

from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray

from packages.vision.stacking.cues import COL_TOL, binarize, centroids
from packages.vision.stacking.model import FloorObservation, Mask, Shift

G: Final = 25.0  # mm mỗi điểm của lưới kiểm
D0: Final = 100.0  # mm: cách xa hơn mức này một điểm tường không còn tính là trùng
WIN: Final = 4000.0  # mm: cửa sổ tương quan / tìm 1-D
REFINE: Final = 150.0  # mm: bán kính tinh chỉnh quanh một ứng viên
MAX_PTS: Final = 40000  # trần số điểm tường mỗi lớp (lấy mẫu ngẫu nhiên cố định hạt giống)
MIN_PTS: Final = 3000  # điểm lưới: mặt nạ tường thưa hơn mức này (vài đoạn) bị bỏ, lùi về mực
_MIN_MASK_SUM: Final = 50  # tổng điểm mặt nạ/mực dưới mức này coi như trống
_MIN_BOX_MM: Final = 3000.0  # hộp trong lưới bong bóng nhỏ hơn mức này (mỗi chiều) là không dùng được
_MIN_BUBBLES: Final = 4  # ít bong bóng hơn thì không dựng được hộp lưới
_FILL_MM: Final = 300.0  # khối đặc lớn hơn mức này (mảng tô) bị bỏ khỏi mực trong lưới
_STROKE_MM: Final = 80.0  # nét tường dày ~ mức này khi không có lưới bong bóng
_INK_DARK: Final = 128  # điểm ảnh xám dưới mức này là mực
_WORK_SCALE: Final = 0.5  # mực xử lý ở nửa ảnh dựng (tọa độ px ảnh dựng chia 2)


@dataclass(frozen=True, slots=True)
class Layer:
    """Lớp kiểm: điểm tường (y, x điểm lưới), khoảng cách tới tường gần nhất (mm), cột (x, y mm), bản mờ cho FFT."""

    pts: NDArray[np.intp]
    dt: NDArray[np.float32]
    cols: list[tuple[float, float]]
    blur: NDArray[np.float32]


# ---------- mực bản vẽ ----------


def _building_box(obs: FloorObservation, mm: float, shape: tuple[int, ...]) -> tuple[int, int, int, int] | None:
    """Hộp (x0, y0, x1, y1) nửa ảnh trong các bong bóng trục (tâm co 1,3 bán kính); None khi < 4 bong bóng/hộp bé."""
    if len(obs.bubbles) < _MIN_BUBBLES:
        return None
    r = 1.3 * max(b.r for b in obs.bubbles) / 2
    xs, ys = [b.x / 2 for b in obs.bubbles], [b.y / 2 for b in obs.bubbles]
    x0, x1, y0, y1 = min(xs) + r, max(xs) - r, min(ys) + r, max(ys) - r
    if (x1 - x0) * mm * 2 < _MIN_BOX_MM or (y1 - y0) * mm * 2 < _MIN_BOX_MM:
        return None
    return max(0, int(x0)), max(0, int(y0)), min(shape[1], int(x1) + 1), min(shape[0], int(y1) + 1)


def _drop_frame(ink: Mask) -> None:
    """Bỏ khung tờ (thành phần dài > 70 % bề rộng/cao và mọi thứ dính nó) và đường dài > 50 % tờ, tại chỗ."""
    h, w = ink.shape
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink)
    big = [i for i in range(1, n) if stats[i][2] > 0.7 * w or stats[i][3] > 0.7 * h]
    if big:
        ink[np.isin(lab, big)] = 0
    for kern in ((1, max(3, int(0.5 * w))), (max(3, int(0.5 * h)), 1)):  # (cao, rộng): đường ngang rồi đứng
        ink[cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones(kern, np.uint8)) > 0] = 0


def _erase_marks(ink: Mask, obs: FloorObservation) -> None:
    """Xoá hộp chữ (nới 2 điểm) và bong bóng trục (đĩa bán kính 0,65 r + 3) khỏi mực, tại chỗ."""
    for t in obs.texts:
        x0, y0, x1, y1 = (int(v / 2) for v in (t.x0, t.y0, t.x1, t.y1))
        ink[max(0, y0 - 2) : y1 + 3, max(0, x0 - 2) : x1 + 3] = 0
    for b in obs.bubbles:
        cv2.circle(ink, (int(b.x / 2), int(b.y / 2)), int(0.65 * b.r) + 3, 0, -1)


def _half_ink(obs: FloorObservation) -> Mask | None:
    """Ảnh xám của quan sát → mực 0/1 ở nửa ảnh dựng (co/giãn khi `ink_scale` khác 0,5); None khi không có ảnh."""
    if obs.ink is None:
        return None
    f = _WORK_SCALE / obs.ink_scale
    g = obs.ink if f == 1.0 else cv2.resize(obs.ink, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return (g < _INK_DARK).astype(np.uint8)


def ink_layer_mask(obs: FloorObservation, mm: float) -> tuple[Mask, float] | None:
    """Mực xây dựng (0/1) và mm mỗi điểm của nó từ ảnh xám; None khi không có ảnh. Xem docstring module."""
    ink = _half_ink(obs)
    if ink is None:
        return None
    k = mm * 2
    box = _building_box(obs, mm, ink.shape)
    if box:  # trong lưới bong bóng: không khung, không khung tên
        keep = np.zeros_like(ink)
        keep[box[1] : box[3], box[0] : box[2]] = 1
        ink &= keep
    else:
        _drop_frame(ink)
    _erase_marks(ink, obs)
    if box:  # trong lưới: mọi nét trừ khối đặc (mảng tô, hình tô)
        r = max(3, round(_FILL_MM / k))
        fill = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r)))
        grown = np.asarray(cv2.dilate(fill, np.ones((3, 3), np.uint8)), dtype=np.uint8)
        return ink & (1 - grown), k
    r = max(1, round(_STROKE_MM / k))
    thick = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r)))
    return np.asarray(thick if thick.sum() > 0.002 * ink.size else ink, dtype=np.uint8), k


# ---------- lớp kiểm ----------


def layer(obs: FloorObservation, mm: float, use_ink: bool = False) -> Layer | None:
    """Quan sát → lớp kiểm từ mặt nạ tường (kèm cột) hoặc từ mực; None khi thiếu/thưa (mặt nạ thưa hơn MIN_PTS)."""
    cols: list[tuple[float, float]] = []
    if use_ink:
        res = ink_layer_mask(obs, mm)
        m, k = (None, 0.0) if res is None else res
    else:
        m, k = None, mm / obs.mask_scale
        if obs.wall_mask is not None:
            m = binarize(obs.wall_mask)
        if obs.col_mask is not None:
            cols = [(x, y) for x, y, _ in centroids(binarize(obs.col_mask), k)]
    if m is None or m.sum() < _MIN_MASK_SUM:
        return None
    f = k / G
    grid = cv2.resize(m.astype(np.float32), None, fx=f, fy=f, interpolation=cv2.INTER_AREA) > 0.2
    pts = np.argwhere(grid)
    if not use_ink and len(pts) < MIN_PTS:
        return None
    if len(pts) > MAX_PTS:
        pts = pts[np.random.default_rng(0).choice(len(pts), MAX_PTS, replace=False)]
    dt = cv2.distanceTransform((~grid).astype(np.uint8), cv2.DIST_L2, 3) * G
    blur = np.asarray(cv2.GaussianBlur(grid.astype(np.float32), (0, 0), 1.5), dtype=np.float32)
    return Layer(pts, dt, cols, blur)


# ---------- điểm kiểm ----------


def _dir_score(pts: NDArray[np.intp], other: Layer, ty: int, tx: int) -> float:
    """Trung bình max(0, 1 - d/D0) của các điểm (dịch ty, tx điểm lưới) tra trong bản đồ khoảng cách của `other`."""
    y, x = pts[:, 0] + ty, pts[:, 1] + tx
    h, w = other.dt.shape
    ok = (y >= 0) & (y < h) & (x >= 0) & (x < w)
    d = np.full(len(pts), D0)
    d[ok] = other.dt[y[ok], x[ok]]
    return float(np.clip(1 - d / D0, 0, 1).mean())


def _col_rate(la: Layer, lb: Layer, t: Shift) -> float | None:
    """Tỉ lệ cột B rơi trong COL_TOL của một cột A sau dịch t; None khi một bên có < 3 cột."""
    if len(la.cols) < 3 or len(lb.cols) < 3:
        return None
    arr = np.array(la.cols)
    hit = [bool(np.hypot(arr[:, 0] - x - t[0], arr[:, 1] - y - t[1]).min() < COL_TOL) for x, y in lb.cols]
    return sum(hit) / len(hit)


def verify(la: Layer, lb: Layer, t: Shift) -> float:
    """Điểm trùng khớp của B dịch t lên A: hướng tốt hơn trong hai hướng + 0,2 x tỉ lệ cột trùng."""
    tx, ty = round(t[0] / G), round(t[1] / G)
    s = max(_dir_score(lb.pts, la, ty, tx), _dir_score(la.pts, lb, -ty, -tx))
    c = _col_rate(la, lb, t)
    return s + (0.2 * c if c is not None else 0.0)


def refine(la: Layer, lb: Layer, t: Shift) -> tuple[float, Shift]:
    """Dịch tốt nhất trong ±REFINE quanh t trên lưới G mm → (điểm, dịch)."""
    steps = np.arange(-REFINE, REFINE + 1, G)
    return max(
        (verify(la, lb, (t[0] + dx, t[1] + dy)), (t[0] + float(dx), t[1] + float(dy))) for dx in steps for dy in steps
    )


# ---------- đề xuất từ tương quan ----------


def corr_peaks(la: Layer, lb: Layer, k: int = 4, nms: float = 400.0) -> list[Shift]:
    """k đỉnh cao nhất (mm) của tương quan FFT hai bản mờ trong ±WIN, loại đỉnh cách nhau dưới `nms` mm."""
    a, b = la.blur, lb.blur
    h = cv2.getOptimalDFTSize(a.shape[0] + b.shape[0])
    w = cv2.getOptimalDFTSize(a.shape[1] + b.shape[1])
    corr = np.fft.irfft2(np.fft.rfft2(a, (h, w)) * np.conj(np.fft.rfft2(b, (h, w))), s=(h, w))
    r = int(WIN / G)
    idx = np.arange(-r, r + 1)
    sub = corr[np.ix_(idx % h, idx % w)].copy()
    out: list[Shift] = []
    rn = int(nms / G)
    for _ in range(k):
        iy, ix = (int(i) for i in np.unravel_index(np.argmax(sub), sub.shape))
        if sub[iy, ix] <= 0:
            break
        out.append((float(idx[ix] * G), float(idx[iy] * G)))
        sub[max(0, iy - rn) : iy + rn + 1, max(0, ix - rn) : ix + rn + 1] = -np.inf
    return out


def search_axis(la: Layer, lb: Layer, fixed: float, axis: int, step: float = 50.0) -> float:
    """Tìm 1-D điểm kiểm cao nhất dọc `axis` (0 = x, 1 = y) khi thành phần kia cố định → giá trị trục đó (mm)."""
    best_v, best_s = 0.0, -1.0
    for v in np.arange(-WIN, WIN + 1, step):
        t = (fixed, float(v)) if axis == 1 else (float(v), fixed)
        s = verify(la, lb, t)
        if s > best_s:
            best_v, best_s = float(v), s
    return best_v
