"""Bước chữ nhật trước xương (WALLS-RECT): tường thẳng trục đọc thẳng từ dải mặt nạ.

Xương Zhang-Suen + `distanceTransform` kéo tim ra mép và lấy bề dày sai khi mặt nạ là **khối dày**
(tường dính sàn WC, lô gia, dải cửa sổ): trên 345 trang held-out IoU mặt nạ 0,807 → tường 0,671.
Quét run-length theo hàng/cột gộp các hàng kề có run trùng mép thành chữ nhật, mỗi chữ nhật là một
đoạn tim + bề dày đúng bằng cạnh ngắn → 0,771, không thêm tường mới nên không sinh tường giả.
Bản vẽ xoay (góc trội theo Hough trên mặt nạ) được xoay về trục rồi xoay ngược đầu mút.
Lát cắt mỏng hơn bề dày thật tại chỗ (hình cong, chéo bị xắt theo hàng/cột) không thành tường; phần mặt nạ
chữ nhật không phủ (tường chéo, cong, cụm vụn) trả về cho vector hoá xương.
Gói không nhập `ml_contracts`/`apps.*`, không đọc biến môi trường; kết quả tất định.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from packages.vision.walls.ink_refine import AxisWall, Frame, refine_line
from packages.vision.walls.types import PointPx, WallSegment

MIN_RUN_PX: Final = 12
"""Run ngắn hơn ngần này (px) không góp chữ nhật: nét mảnh hơn thế là chữ/nét kích thước, xương lo."""
_EDGE_TOL_RATIO: Final = 0.10
"""Hai hàng kề cùng một dải khi mép run lệch ≤ max(2 px, ngần này x độ dài run)."""
_EDGE_TOL_PX: Final = 3.0
"""Răng cưa của mặt nạ xoay bằng `INTER_NEAREST` lệch mép tới 3 px giữa hai hàng kề."""
_ASPECT_MIN: Final = 2.0
"""Chữ nhật phải dài ≥ ngần này lần cạnh ngắn; ô vuông là giao của hai lượt quét, bỏ."""
_FILL_MIN: Final = 0.85
"""Chữ nhật phải được mặt nạ tô ≥ ngần này diện tích: dải cong/chéo gộp bbox tô thưa hơn, để cho xương."""
_SLICE_RATIO: Final = 0.7
"""Chữ nhật mỏng hơn ngần này lần bề dày thật tại chỗ là lát cắt ngang của hình cong/chéo, bỏ.

Bề dày thật = 2 x phân vị 80 của distance transform dọc tim chữ nhật: phân vị thay vì max để nút giao với tường
dày hơn (distance lớn ở một đoạn ngắn) không làm vách mảnh bị coi là lát cắt."""
_SLICE_MAX_LEN: Final = 4 * MIN_RUN_PX
"""Chữ nhật dài từ ngần này trở lên không bị coi là lát cắt (cung bán kính tới ~10 bề dày cho dây ≤ 4,5 bề dày)."""
_COVER_DROP: Final = 0.9
"""Chữ nhật đã bị chữ nhật lớn hơn phủ ≥ ngần này diện tích thì bỏ (cùng tường, hai lượt quét)."""
_FUSE_OFFSET_RATIO: Final = 0.2
"""Hai đoạn cùng đường khi tim lệch ≤ max(2 px, ngần này x bề dày)."""
_FUSE_THICK_TOL: Final = 0.4
"""Bề dày lệch tương đối ≤ ngần này mới gộp thẳng hàng."""
_FUSE_GAP_RATIO: Final = 2.0
"""Khe dọc trục ≤ ngần này lần bề dày thì gộp (mép scan răng cưa cắt dải); khe cửa ≥ 600 mm rộng hơn nhiều."""
_SLIVER_RATIO: Final = 0.5
"""Dải mỏng hơn ngần này lần dải song song kề mặt nó (mép răng cưa, nhất là sau khi xoay) bị dải kia nuốt."""
_TILT_MIN_DEG: Final = 1.5
"""Góc trội nhỏ hơn ngần này coi như thẳng ảnh, không xoay."""
_RESIDUAL_PAD_PX: Final = 2
"""Phần mặt nạ cách mép chữ nhật ≤ ngần này không tính là còn lại (răng cưa mép)."""
_RESIDUAL_MIN_SIDE: Final = 2 * MIN_RUN_PX
"""Thành phần còn lại phải có cạnh bao ≥ ngần này mới đưa cho xương; nhỏ hơn là vụn."""
_CONFIDENCE: Final = 0.9
"""Độ tin của đoạn chữ nhật: cạnh ngắn đo trực tiếp, không qua xương (xương cho 0,5-1,0)."""

Axis = Literal["h", "v"]


@dataclass(slots=True)
class _Strip:
    """Một đoạn thẳng trục đang gộp, trong hệ đã xoay: tim `value`, chạy `lo..hi`, bề dày `thick`."""

    axis: Axis
    value: float
    lo: float
    hi: float
    thick: float

    def length(self) -> float:
        """Độ dài dọc trục."""
        return self.hi - self.lo


def _rects(mask: NDArray[np.bool_], axis: Axis) -> list[tuple[int, int, int, int]]:
    """Chữ nhật `(x, y, w, h)` từ run-length theo hàng (`h`) hay cột (`v`, quét trên ma trận chuyển vị).

    Mỗi hàng: run ≥ `MIN_RUN_PX`; run nối vào dải đang mở nếu dải kết thúc ở hàng trước và hai mép lệch
    ≤ dung sai, không thì mở dải mới. Dải mở là danh sách `[lo, hi, hàng đầu, hàng cuối + 1]`.
    """
    grid = mask if axis == "h" else mask.T
    open_: list[list[int]] = []
    done: list[list[int]] = []
    for row_index in range(grid.shape[0]):
        edges = np.diff(np.concatenate(([0], grid[row_index].astype(np.int8), [0])))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        nxt: list[list[int]] = []
        for lo, hi in zip(starts.tolist(), ends.tolist(), strict=True):
            if hi - lo < MIN_RUN_PX:
                continue
            tol = max(_EDGE_TOL_PX, _EDGE_TOL_RATIO * (hi - lo))
            hit = next(
                (s for s in open_ if s[3] == row_index and abs(s[0] - lo) <= tol and abs(s[1] - hi) <= tol), None
            )
            if hit is None:
                nxt.append([lo, hi, row_index, row_index + 1])
                continue
            hit[0], hit[1], hit[3] = min(hit[0], lo), max(hit[1], hi), row_index + 1
            open_.remove(hit)
            nxt.append(hit)
        done.extend(open_)
        open_ = nxt
    done.extend(open_)
    if axis == "h":
        return [(lo, r0, hi - lo, r1 - r0) for lo, hi, r0, r1 in done]
    return [(r0, lo, r1 - r0, hi - lo) for lo, hi, r0, r1 in done]


def _is_slice(strip: _Strip, dist: NDArray[np.float32]) -> bool:
    """Dải mỏng hơn `_SLICE_RATIO` lần bề dày mặt nạ thật dọc tim nó (2 x phân vị 80 của distance transform).

    Chỉ xét dải ngắn hơn `_SLICE_MAX_LEN`: lát cắt của cung/chéo đều ngắn, còn nét mảnh 1-2 px chạy dài là vách
    thật (bản CAD) — distance transform của nét 1 px là 1 nên không thể so bề dày. Đo sau `_trim_ends`: phần dải nằm
    trong tường vuông góc (nút T, góc) đã bị cắt, không làm bề dày đo được phình lên.
    """
    if strip.length() >= _SLICE_MAX_LEN:
        return False
    lo, hi = math.floor(strip.lo), max(math.floor(strip.lo) + 1, math.ceil(strip.hi))
    centre = min(dist.shape[0 if strip.axis == "h" else 1] - 1, math.floor(strip.value))
    line = dist[centre, lo:hi] if strip.axis == "h" else dist[lo:hi, centre]
    return strip.thick < _SLICE_RATIO * 2.0 * float(np.percentile(line, 80))


def _trim_ends(strips: list[_Strip]) -> None:
    """Đầu dải nằm trong thân dải vuông góc (góc L, nút T) kéo về tim dải đó, như nút của xương.

    Run-length chạy tới mặt ngoài tường vuông góc nên dải dài thêm nửa bề dày ở mỗi góc; nhịp đo giữa hai tường
    (ghép chữ kích thước) sẽ sai nửa bề dày. Dải vuông góc được ghi vào ô lưới theo dải bề dày của nó quanh tim.
    """
    grid: defaultdict[tuple[Axis, int], list[_Strip]] = defaultdict(list)
    for o in strips:
        half = o.thick / 2.0 + _EDGE_TOL_PX
        for cell in _bins(o.value - half, o.value + half):
            grid[(o.axis, cell)].append(o)
    other: dict[Axis, Axis] = {"h": "v", "v": "h"}
    for s in strips:
        for end in ("lo", "hi"):
            at = getattr(s, end)
            hits = [
                o
                for o in grid[(other[s.axis], math.floor(at / _BIN_PX))]
                if o.lo <= s.value <= o.hi and abs(at - o.value) <= o.thick / 2.0 + _EDGE_TOL_PX
            ]
            if hits:
                node = min(hits, key=lambda o: abs(at - o.value)).value
                if node > s.lo if end == "hi" else node < s.hi:
                    setattr(s, end, node)


def _strips(mask: NDArray[np.bool_]) -> list[_Strip]:
    """Chữ nhật hai lượt quét → đoạn thẳng trục: bỏ ô vuông, hình tô thưa, hình đã bị hình lớn hơn phủ; cắt đầu dải
    về tim dải vuông góc rồi mới bỏ lát cắt mỏng."""
    dist = np.asarray(cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5), np.float32)
    rects = [
        r
        for r in _rects(mask, "h") + _rects(mask, "v")
        if max(r[2], r[3]) >= _ASPECT_MIN * min(r[2], r[3])
        and mask[r[1] : r[1] + r[3], r[0] : r[0] + r[2]].mean() >= _FILL_MIN
    ]
    rects.sort(key=lambda r: -r[2] * r[3])
    cover = np.zeros(mask.shape, np.uint8)
    out: list[_Strip] = []
    for x, y, w, h in rects:
        if cover[y : y + h, x : x + w].mean() >= _COVER_DROP:
            continue
        cover[y : y + h, x : x + w] = 1
        if w >= h:
            out.append(_Strip("h", y + h / 2.0, float(x), float(x + w), float(h)))
        else:
            out.append(_Strip("v", x + w / 2.0, float(y), float(y + h), float(w)))
    _trim_ends(out)
    return [s for s in out if not _is_slice(s, dist)]


def _mergeable(kept: _Strip, new: _Strip) -> bool:
    """Cùng trục, tim gần, bề dày gần, chạm/chồng hay hở ≤ `_FUSE_GAP_RATIO` bề dày."""
    thick = max(kept.thick, new.thick)
    if kept.axis != new.axis or abs(kept.value - new.value) > max(_EDGE_TOL_PX, _FUSE_OFFSET_RATIO * thick):
        return False
    if abs(kept.thick - new.thick) / thick > _FUSE_THICK_TOL:
        return False
    return new.lo - kept.hi <= _FUSE_GAP_RATIO * thick and kept.lo - new.hi <= _FUSE_GAP_RATIO * thick


_BIN_PX: Final = 16
"""Ô lưới (px) của chỉ mục theo tim/toạ độ: so cặp chỉ trong ô kề, tránh O(n²) khi trang có hàng vạn dải."""


def _bins(low: float, high: float) -> range:
    """Các ô lưới chứa khoảng `[low, high]`."""
    return range(math.floor(low / _BIN_PX), math.floor(high / _BIN_PX) + 1)


def _fuse(strips: list[_Strip]) -> list[_Strip]:
    """Gộp lặp các đoạn thẳng hàng (tim, bề dày trung bình trọng số độ dài) tới khi không còn cặp nào.

    Ứng viên gộp tra theo ô lưới của tim (`_BIN_PX` ≥ dung sai tim) nên mỗi dải chỉ so với vài dải lân cận.
    """
    changed = True
    while changed:
        changed = False
        out: list[_Strip] = []
        index: defaultdict[tuple[str, int], list[_Strip]] = defaultdict(list)
        for s in sorted(strips, key=lambda s: (s.axis, s.value, s.lo)):
            cell = math.floor(s.value / _BIN_PX)
            near = (o for k in (cell - 1, cell, cell + 1) for o in index[(s.axis, k)])
            kept = next((o for o in near if _mergeable(o, s)), None)
            if kept is None:
                out.append(s)
                index[(s.axis, cell)].append(s)
                continue
            w0, w1 = kept.length(), s.length()
            kept.value = (kept.value * w0 + s.value * w1) / (w0 + w1)
            kept.thick = (kept.thick * w0 + s.thick * w1) / (w0 + w1)
            kept.lo, kept.hi = min(kept.lo, s.lo), max(kept.hi, s.hi)
            changed = True
        strips = out
    return strips


def _bbox(s: _Strip) -> tuple[float, float, float, float]:
    """Hộp bao `(x0, y0, x1, y1)` của thân dải."""
    half = s.thick / 2.0
    if s.axis == "h":
        return (s.lo, s.value - half, s.hi, s.value + half)
    return (s.value - half, s.lo, s.value + half, s.hi)


def _absorb_slivers(strips: list[_Strip]) -> list[_Strip]:
    """Bỏ dải mỏng ≤ `_SLIVER_RATIO` lần một dải dày hơn mà thân nó nằm trong thân dải kia (nới bề dày + dung sai mép).

    Mép răng cưa (scan, mặt nạ đã xoay) để lại dải 1-3 px áp mặt thân tường, cùng trục hay vuông góc; chúng không
    phải tường riêng. Dải dày đã giữ được ghi vào mọi ô lưới mà hộp bao của nó chạm; dải mỏng chỉ tra ô của mình.
    """
    out: list[_Strip] = []
    grid: defaultdict[tuple[int, int], list[_Strip]] = defaultdict(list)
    for s in sorted(strips, key=lambda s: -s.thick):
        x0, y0, x1, y1 = _bbox(s)
        pad = s.thick + _EDGE_TOL_PX
        cx, cy = math.floor((x0 + x1) / 2.0 / _BIN_PX), math.floor((y0 + y1) / 2.0 / _BIN_PX)
        if any(
            s.thick <= _SLIVER_RATIO * o.thick
            and (h := _bbox(o))[0] - pad <= x0
            and h[1] - pad <= y0
            and x1 <= h[2] + pad
            and y1 <= h[3] + pad
            for o in grid[(cx, cy)]
        ):
            continue
        out.append(s)
        for bx in _bins(x0 - pad, x1 + pad):
            for by in _bins(y0 - pad, y1 + pad):
                grid[(bx, by)].append(s)
    return out


def _dominant_tilt(mask: NDArray[np.bool_]) -> float:
    """Góc trội (độ, -45..45) của nét mặt nạ theo HoughLinesP, trọng số độ dài; không đủ nét → 0."""
    edges = cv2.Canny(mask.astype(np.uint8) * 255, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, 60, minLineLength=40, maxLineGap=4)
    if lines is None:
        return 0.0
    angles, weights = [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4).tolist():
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 90.0
        angles.append(angle - 90.0 if angle > 45.0 else angle)
        weights.append(math.hypot(x2 - x1, y2 - y1))
    hist, _ = np.histogram(angles, bins=90, range=(-45.0, 45.0), weights=weights)
    return float(-45.0 + hist.argmax() + 0.5)


def _refine(strips: list[_Strip], gray: NDArray[np.uint8], frame: Frame) -> None:
    """Dời tim từng đường về giữa nét mực và lấy bề dày mực (cùng `refine_line` của bước 8c xương)."""
    lines: defaultdict[tuple[Axis, float], list[_Strip]] = defaultdict(list)
    for s in strips:
        lines[(s.axis, round(s.value, 2))].append(s)
    for members in lines.values():
        fit = refine_line(gray, [AxisWall(m.axis, m.value, m.lo, m.hi, m.thick) for m in members], frame)
        if fit is None:
            continue
        for member, width in zip(members, fit.thicknesses, strict=True):
            member.value = fit.value
            if width is not None:
                member.thick = width


def _to_walls(strips: Sequence[_Strip], back: NDArray[np.float64] | None) -> list[WallSegment]:
    """Đoạn thẳng trục → `WallSegment` toạ độ ảnh gốc; `back` là affine 2x3 xoay ngược (None: không xoay)."""
    walls: list[WallSegment] = []
    for s in strips:
        a, b = ((s.lo, s.value), (s.hi, s.value)) if s.axis == "h" else ((s.value, s.lo), (s.value, s.hi))
        if back is not None:
            a = tuple((back @ np.array([a[0], a[1], 1.0])).tolist())
            b = tuple((back @ np.array([b[0], b[1], 1.0])).tolist())
        start: PointPx = (round(a[0], 2), round(a[1], 2))
        end: PointPx = (round(b[0], 2), round(b[1], 2))
        if end < start:
            start, end = end, start
        walls.append(WallSegment(start, end, round(s.thick, 2), _CONFIDENCE))
    return walls


def _raster(walls: Sequence[WallSegment], shape: tuple[int, int]) -> NDArray[np.bool_]:
    """Thân tường của các đoạn (chữ nhật quanh tim) trên lưới `shape`."""
    out = np.zeros(shape, np.uint8)
    for w in walls:
        a, b = np.array(w.start), np.array(w.end)
        d = b - a
        n = np.array([-d[1], d[0]]) / max(float(np.hypot(*d)), 1e-6) * (w.thickness_px / 2.0)
        poly = np.rint(np.array([a + n, b + n, b - n, a - n]) * 16).astype(np.int32)
        cv2.fillConvexPoly(out, poly, 1, cv2.LINE_8, 4)
    return out.astype(bool)


def _iou(a: NDArray[np.bool_], b: NDArray[np.bool_]) -> float:
    """IoU hai mặt nạ bool (rỗng cả hai → 0)."""
    return float((a & b).sum() / max(1, int((a | b).sum())))


def _pass(mask: NDArray[np.bool_], gray: NDArray[np.uint8] | None, frame: Frame) -> list[_Strip]:
    """Một lượt trên mặt nạ đã thẳng trục: chữ nhật → gộp → (tuỳ) chỉnh theo mực."""
    strips = _fuse(_absorb_slivers(_fuse(_strips(mask))))
    if gray is not None and strips:
        _refine(strips, gray, frame)
    return strips


def rect_walls(
    mask: NDArray[np.bool_], gray: NDArray[np.uint8] | None = None
) -> tuple[list[WallSegment], NDArray[np.bool_]]:
    """Tường thẳng trục từ dải mặt nạ và phần mặt nạ còn lại cho vector hoá xương.

    Thử ở trục ảnh; nếu góc trội ≥ `_TILT_MIN_DEG` thì thử thêm ở hệ đã xoay và giữ kết quả phủ
    mặt nạ tốt hơn (IoU thân tường với mặt nạ). `gray` (ảnh xám `uint8` cùng khổ) → tim/bề dày
    theo nét mực như bước 8c. Phần còn lại = mặt nạ trừ thân tường nới `_RESIDUAL_PAD_PX`, chỉ giữ
    thành phần có cạnh bao ≥ `_RESIDUAL_MIN_SIDE`.
    """
    height, width = mask.shape
    centre = ((width - 1) / 2.0, (height - 1) / 2.0)
    walls = _to_walls(_pass(mask, gray, Frame(0.0, centre)), None)
    tilt = _dominant_tilt(mask)
    if abs(tilt) >= _TILT_MIN_DEG:
        size = math.ceil(math.hypot(height, width))
        pad_y, pad_x = (size - height) // 2, (size - width) // 2
        big = np.zeros((size, size), np.uint8)
        big[pad_y : pad_y + height, pad_x : pad_x + width] = mask
        pivot = (size / 2.0, size / 2.0)
        turn = cv2.getRotationMatrix2D(pivot, tilt, 1.0)
        rotated = cv2.warpAffine(big, turn, (size, size), flags=cv2.INTER_NEAREST).astype(bool)
        rotated_gray = None
        if gray is not None:
            big_gray = np.full((size, size), 255, np.uint8)
            big_gray[pad_y : pad_y + height, pad_x : pad_x + width] = gray
            rotated_gray = np.asarray(cv2.warpAffine(big_gray, turn, (size, size), borderValue=255), np.uint8)
        back = np.asarray(cv2.getRotationMatrix2D(pivot, -tilt, 1.0), np.float64)
        back[:, 2] -= (pad_x, pad_y)
        candidate = _to_walls(_pass(rotated, rotated_gray, Frame(0.0, pivot)), back)
        if _iou(_raster(candidate, mask.shape), mask) > _iou(_raster(walls, mask.shape), mask):
            walls = candidate
    body = _raster(walls, mask.shape).astype(np.uint8)
    kernel = np.ones((2 * _RESIDUAL_PAD_PX + 1, 2 * _RESIDUAL_PAD_PX + 1), np.uint8)
    rest = (mask & ~cv2.dilate(body, kernel).astype(bool)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(rest, connectivity=8)
    keep = [
        i
        for i in range(1, count)
        if max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= _RESIDUAL_MIN_SIDE
    ]
    residual = np.isin(labels, keep) if keep else np.zeros(mask.shape, bool)
    return walls, residual
