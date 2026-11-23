"""Tách một tờ bản vẽ thành các hình (mặt bằng, mặt cắt…) bằng xử lý ảnh cổ điển (K-32a PR(3)).

Chuyển từ nhân nghiên cứu `muc3/kernel/split_views.py` (`split_views_report.md`): mặt nạ "giấy" (mọi điểm
không trắng tinh) → thành phần liên thông → cắt đệ quy ở bậc xám giấy đổi đột ngột → thay một hộp bằng các ô
khung kín (đường kẻ dài bao quanh) khi >= 2 ô lát kín nó. Không học máy, không đọc chữ. Số đo của nhân (IoU
>= 0,5): tìm được 66-72 %, chính xác 71-86 % — vì vậy máy chỉ **gợi ý** khung, người duyệt luôn sửa (K-32a §6.3).

Khác nhân có chủ ý: bỏ cắt theo khe trắng trên mặt nạ giấy (`use_gap`, nhân tắt mặc định), bỏ đường ảnh chụp
(`split_photo`, nhân đo 15 % — NO-543); thêm chốt "hộp phải phủ phần lớn mực": tờ CAD dựng từ PDF có nền trắng
tinh nên mặt nạ giấy chỉ còn mảng mực rời, nhân sẽ băm vụn tờ.

Nâng độ đúng (NO-543, `changes/K-32a.md` mục "Nâng độ đúng tách hình"):
- Cắt ở **đường kẻ chạy suốt hộp** trước bậc xám: hai tờ có khung dán sát nhau chung một cạnh khung dài bằng cả
  hộp, hai cạnh vuông góc của hộp cũng nằm trên đường kẻ (khung). Đường khung lấy ở ngưỡng xám nhạt hơn mực
  (`FRAME_INK`) vì khung bản quét thường xám nhạt — ngưỡng mực 160 bỏ sót gần hết khung (nguyên nhân chính bản
  chuyển chỉ tìm được 55 %).
- Bậc xám so cả hai trục, lấy bậc mạnh hơn (nhân lấy trục x trước, cắt dọc nhầm ở vệt sáng trong một tấm).
- Tờ nền trắng: thay vì một hộp cả tờ, cắt hộp bao mực theo đường kẻ chạy suốt rồi theo khe trắng
  (hàng/cột không mực rộng >= `GAP_MIN`); không cắt được mới trả một hộp cả tờ. Hàm thuần, tất định.
"""

import warnings
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray

WORK_PX: Final = 1600
"""Cạnh dài của bản làm việc: đủ thấy khung và bậc xám, rẻ trên tờ 40 Mpx."""
PAPER_MAX: Final = 251
"""Xám < mức này là giấy hay mực (nền tờ ghép là trắng tinh 255)."""
INK_MAX: Final = 160
INK_SHARE: Final = 40
"""Mực = xám < 160 ở ảnh gốc; sau khi co, điểm còn > 40/255 phần mực vẫn là mực (nét mảnh sống qua phép co)."""
FRAME_INK: Final = 190
"""Nét dùng để tìm đường khung: xám < 190 ở ảnh gốc (khung bản quét xám nhạt). Chỉnh trên 10 tờ DS- test + 9 tờ
dev: 160 → tìm được 57 %, 190 → 75 %, 215 cắt vụn thêm (chính xác 58 %)."""
SPAN_MIN: Final = 0.9
"""Đường cắt phải phủ >= 90 % bề ngang (hay bề cao) của hộp."""
SPAN_EDGE: Final = 0.2
"""Đường cắt cách mép hộp >= 20 %: dải khung tên, đường đáy khung không thành hình riêng."""
SIDE_BAND: Final = 0.06
SIDE_MIN: Final = 0.4
"""Hai cạnh vuông góc của hộp (dải 6 % sát mép) phải có đường kẻ trên >= 40 % chiều dài mỗi nửa: chỉ cắt giữa các
tờ có khung, không cắt một hình theo đường nền/đường trục dài của chính nó."""
GAP_MIN: Final = 0.01
"""Tờ nền trắng: khe trắng (hàng/cột không mực) rộng >= 1 % cạnh dài bản làm việc ngăn hai hình."""
GAP_EDGE: Final = 0.1
"""Khe trắng cách mép hộp >= 10 %."""
LINE_LEN: Final = 0.06
"""Đường khung = đoạn mực thẳng dài >= 6 % cạnh dài."""
CELL_MIN: Final = 0.02
CELL_MAX: Final = 0.9
"""Ô khung chiếm 2-90 % tờ."""
SIDE_COVER: Final = 0.8
"""Mỗi cạnh của ô khung phải nằm trên đường kẻ >= 80 % chiều dài."""
CELL_PAD: Final = 3
EDGE_FRAC: Final = 0.12
"""Lát cắt phải cách mép hộp >= 12 % (hai nửa đều đáng kể)."""
STEP_MIN: Final = 7.0
"""Bậc xám giấy (mức) ngăn hai tấm hình ghép."""
STEP_WINDOW: Final = 12
BARE_MIN: Final = 190
BARE_SHARE: Final = 0.3
"""Chỉ so bậc xám trên giấy trống (xám >= 190) và ở hàng/cột có >= 30 % giấy trống."""
MIN_AREA: Final = 0.004
"""Hộp dưới 0,4 % tờ là ký hiệu lẻ, không phải hình (nhân chấm 0,2 rồi bỏ)."""
COMPONENT_MIN: Final = 0.0005
LEAF_MIN: Final = 0.03
MAX_DEPTH: Final = 6
CELL_INSIDE: Final = 0.9
FRAME_INSIDE: Final = 0.8
FRAME_COVER: Final = 0.5
INK_COVER_MIN: Final = 0.6
"""Các hộp giữ lại phải phủ >= 60 % điểm mực của tờ; ít hơn là mặt nạ giấy hỏng (nền trắng) → một hộp cả tờ."""
COVER_PAD: Final = 0.01
"""Khi đo độ phủ mực, nới mỗi hộp 1 % cạnh dài: nét khung bao quanh ô (nằm ngay ngoài ô) vẫn tính là được phủ."""
VIEW_SCORE: Final = 0.9
"""Độ chắc của một khung máy gợi ý (nhân: 0,9 cho mọi hộp đủ lớn; khung luôn được người duyệt xem lại)."""
WHOLE_SCORE: Final = 0.5
"""Độ chắc của hộp bao mực cả tờ khi không tách được."""
INK_SPLIT_SCORE: Final = 0.7
"""Độ chắc của khung tách trên tờ nền trắng (đường khung/khe trắng; đo trên tờ giả lập nền trắng, kém tờ quét)."""

type Box = tuple[int, int, int, int]
type Gray = NDArray[np.uint8]
type Mask = NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class ViewBox:
    """Khung một hình trên tờ, theo điểm ảnh của ảnh vào (`x1`, `y1` loại trừ), kèm độ chắc của máy."""

    x0: int
    y0: int
    x1: int
    y1: int
    score: float


@dataclass(frozen=True, slots=True)
class _Work:
    """Bản làm việc đã co: mặt nạ giấy, mặt nạ mực, ảnh xám, diện tích, đường kẻ ngang/dọc (ngưỡng khung)."""

    paper: Mask
    ink: Mask
    grey: Gray
    area: int
    across: Mask
    down: Mask


def _prepare(gray: Gray) -> tuple[float, _Work]:
    """`(tỉ lệ co, bản làm việc)`: co về `WORK_PX`; mực lấy từ ảnh gốc trước khi co để nét mảnh không mất."""
    height, width = gray.shape
    scale = min(1.0, WORK_PX / max(height, width))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    small = np.asarray(cv2.resize(gray, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)
    ink = _shrunk(gray < INK_MAX, size)
    across, down = _axis_lines(_shrunk(gray < FRAME_INK, size))
    opened = cv2.morphologyEx((small < PAPER_MAX).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return scale, _Work(np.asarray(opened) > 0, ink, small, small.shape[0] * small.shape[1], across, down)


def _shrunk(dark: Mask, size: tuple[int, int]) -> Mask:
    """Mặt nạ nét ở ảnh gốc → bản làm việc khổ `size`: điểm còn > `INK_SHARE`/255 phần nét vẫn là nét."""
    return np.asarray(cv2.resize(dark.astype(np.uint8) * 255, size, interpolation=cv2.INTER_AREA)) > INK_SHARE


def _axis_lines(marks: Mask) -> tuple[Mask, Mask]:
    """Đường kẻ dài ngang và dọc riêng (mở hình thái theo `LINE_LEN`), mỗi mặt nạ nong 1 px."""
    height, width = marks.shape
    length = max(15, min(int(LINE_LEN * max(height, width)), int(0.4 * min(height, width))))
    mask = marks.astype(np.uint8)
    grow = np.ones((3, 3), np.uint8)
    across = cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, length), np.uint8)), grow)
    down = cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8)), grow)
    return np.asarray(across) > 0, np.asarray(down) > 0


def _tight(mask: Mask, box: Box) -> Box | None:
    """Thu hộp về hộp bao các điểm của `mask` bên trong nó; `None` khi không có điểm nào."""
    x0, y0, x1, y1 = box
    sub = mask[y0:y1, x0:x1]
    rows, cols = np.flatnonzero(sub.any(1)), np.flatnonzero(sub.any(0))
    if rows.size == 0:
        return None
    return x0 + int(cols[0]), y0 + int(rows[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[-1]) + 1


def _profile(work: _Work, box: Box, axis: int) -> NDArray[np.float64]:
    """Trung vị xám của giấy trống theo từng cột (`axis` 1) hay hàng (`axis` 0); NaN ở chỗ ít giấy trống."""
    x0, y0, x1, y1 = box
    grey = work.grey[y0:y1, x0:x1].astype(np.float64)
    bare = work.paper[y0:y1, x0:x1] & (grey >= BARE_MIN)
    grey[~bare] = np.nan
    across = 0 if axis == 1 else 1
    with warnings.catch_warnings():  # hàng/cột toàn mực cho "All-NaN slice": chuyện thường, không phải lỗi
        warnings.simplefilter("ignore", RuntimeWarning)
        profile = np.asarray(np.nanmedian(grey, axis=across), dtype=np.float64)
    profile[bare.mean(axis=across) < BARE_SHARE] = np.nan
    return profile


def _step_cut(work: _Work, box: Box, axis: int) -> tuple[float, int] | None:
    """`(bậc, vị trí tương đối)` của bậc xám giấy lớn nhất >= `STEP_MIN` dọc `axis`, cách mép >= `EDGE_FRAC`.

    Đó là ranh hai tấm hình ghép; `None` khi không có.
    """
    profile = _profile(work, box, axis)
    size = len(profile)
    best: tuple[float, int] | None = None
    low, high = max(STEP_WINDOW, int(EDGE_FRAC * size)), min(size - STEP_WINDOW, int((1 - EDGE_FRAC) * size) + 1)
    for index in range(low, high):
        before, after = profile[index - STEP_WINDOW : index], profile[index : index + STEP_WINDOW]
        if np.isnan(before).any() or np.isnan(after).any():
            continue
        step = abs(float(np.median(after) - np.median(before)))
        if step >= STEP_MIN and (best is None or (step, index) > best):
            best = (step, index)
    return best


def _line_cut(work: _Work, box: Box) -> tuple[int, int] | None:
    """`(trục, vị trí tương đối)` của đường kẻ chạy suốt hộp ngăn hai tờ có khung (trục như `_pieces`); `None`.

    Đường phải phủ >= `SPAN_MIN` hộp, cách mép >= `SPAN_EDGE`, và hai cạnh vuông góc của hộp có đường kẻ trên
    >= `SIDE_MIN` mỗi nửa. Nhiều ứng viên → đường phủ nhiều nhất, rồi gần giữa nhất.
    """
    x0, y0, x1, y1 = box
    best: tuple[tuple[float, float], int, int] | None = None
    for axis, lines, sides in ((0, work.across, work.down), (1, work.down, work.across)):
        along = lines[y0:y1, x0:x1].mean(axis=1 if axis == 0 else 0)
        edge = sides[y0:y1, x0:x1] if axis == 0 else sides[y0:y1, x0:x1].T
        band = max(4, int(SIDE_BAND * edge.shape[1]))
        start, end = edge[:, :band].any(axis=1), edge[:, -band:].any(axis=1)
        size = len(along)
        for index in range(max(1, int(SPAN_EDGE * size)), int((1 - SPAN_EDGE) * size)):
            if along[index] < SPAN_MIN:
                continue
            framed = min(start[:index].mean(), start[index:].mean(), end[:index].mean(), end[index:].mean())
            key = (round(float(along[index]), 2), -abs(index - size / 2))
            if framed >= SIDE_MIN and (best is None or key > best[0]):
                best = (key, axis, index)
    return None if best is None else (best[1], best[2])


def _gap_cut(ink: Mask, box: Box) -> tuple[int, int] | None:
    """`(trục, vị trí tương đối)` giữa khe trắng rộng nhất (>= `GAP_MIN`, cách mép >= `GAP_EDGE`) của hộp; `None`."""
    x0, y0, x1, y1 = box
    sub = ink[y0:y1, x0:x1]
    need = max(3, int(GAP_MIN * max(ink.shape)))
    best: tuple[int, int, int] | None = None
    for axis, empty in ((1, ~sub.any(axis=0)), (0, ~sub.any(axis=1))):
        size = len(empty)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], empty.astype(np.int8), [0]))))
        for start, end in zip(edges[::2], edges[1::2], strict=True):
            middle, wide = int(start + end) // 2, int(end - start)
            inner = GAP_EDGE * size <= middle <= (1 - GAP_EDGE) * size
            if wide >= need and inner and (best is None or wide > best[0]):
                best = (wide, axis, middle)
    return None if best is None else (best[1], best[2])


def _pieces(box: Box, axis: int, cuts: list[int]) -> list[Box]:
    """Chia hộp ở các lát cắt tương đối dọc `axis` (1 = x); lát sát mép (< `EDGE_FRAC`) bị bỏ."""
    x0, y0, x1, y1 = box
    low, high = (x0, x1) if axis == 1 else (y0, y1)
    size = high - low
    inner = [c for c in sorted(cuts) if EDGE_FRAC * size <= c <= (1 - EDGE_FRAC) * size]
    marks = [0, *inner, size]
    out: list[Box] = []
    for a, b in pairwise(marks):
        if b - a >= EDGE_FRAC * size * 0.5:
            out.append((low + a, y0, low + b, y1) if axis == 1 else (x0, low + a, x1, low + b))
    return out


def _cut(work: _Work, box: Box, depth: int = 0) -> list[Box]:
    """Cắt đệ quy một hộp theo bậc xám (ngang trước, dọc sau); dừng ở `MAX_DEPTH` hay hộp < `LEAF_MIN` tờ."""
    tight = _tight(work.paper, box)
    if tight is None:
        return []
    width, height = tight[2] - tight[0], tight[3] - tight[1]
    if depth > MAX_DEPTH or width * height < LEAF_MIN * work.area:
        return [tight]
    framed = _line_cut(work, tight)
    if framed is not None and len(parts := _pieces(tight, framed[0], [framed[1]])) > 1:
        return [leaf for part in parts for leaf in _cut(work, part, depth + 1)]
    steps = [(found[0], axis, found[1]) for axis in (1, 0) if (found := _step_cut(work, tight, axis)) is not None]
    if steps:
        _step, axis, at = max(steps)
        if len(parts := _pieces(tight, axis, [at])) > 1:
            return [leaf for part in parts for leaf in _cut(work, part, depth + 1)]
    return [tight]


def _ink_cut(work: _Work, box: Box, depth: int = 0) -> list[Box]:
    """Tờ nền trắng: cắt đệ quy hộp bao mực theo đường kẻ chạy suốt (tờ có khung), rồi theo khe trắng."""
    tight = _tight(work.ink, box)
    if tight is None:
        return []
    if depth > MAX_DEPTH or _area(tight) < LEAF_MIN * work.area:
        return [tight]
    for found in (_line_cut(work, tight), _gap_cut(work.ink, tight)):
        if found is not None and len(parts := _pieces(tight, found[0], [found[1]])) > 1:
            return [leaf for part in parts for leaf in _ink_cut(work, part, depth + 1)]
    return [tight]


def _components(paper: Mask, min_area: float) -> list[Box]:
    """Hộp bao các thành phần liên thông (8 hướng) của mặt nạ giấy, bỏ mảnh < `min_area` điểm."""
    _n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(paper.astype(np.uint8), connectivity=8)
    return [(int(x), int(y), int(x + w), int(y + h)) for x, y, w, h, a in stats[1:] if a >= min_area]


def _inter(a: Box, b: Box) -> int:
    """Diện tích giao của hai hộp (0 khi rời nhau)."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, width) * max(0, height)


def _area(box: Box) -> int:
    """Diện tích hộp."""
    return (box[2] - box[0]) * (box[3] - box[1])


def _lines(ink: Mask) -> Mask:
    """Mặt nạ đường kẻ dài ngang/dọc (mở hình thái theo `LINE_LEN`), nong 1 px để cạnh ô chạm tới."""
    height, width = ink.shape
    length = max(15, min(int(LINE_LEN * max(height, width)), int(0.4 * min(height, width))))
    mask = ink.astype(np.uint8)
    across = np.asarray(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, length), np.uint8)), dtype=np.uint8)
    down = np.asarray(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8)), dtype=np.uint8)
    return np.asarray(cv2.dilate(across | down, np.ones((3, 3), np.uint8))) > 0


def _closed(lines: Mask, x: int, y: int, width: int, height: int) -> bool:
    """Bốn cạnh của ô đều nằm trên đường kẻ >= `SIDE_COVER` chiều dài."""
    sides = (
        lines[max(0, y - CELL_PAD) : y + 1, x : x + width].any(0).mean(),
        lines[y + height - 1 : y + height + CELL_PAD, x : x + width].any(0).mean(),
        lines[y : y + height, max(0, x - CELL_PAD) : x + 1].any(1).mean(),
        lines[y : y + height, x + width - 1 : x + width + CELL_PAD].any(1).mean(),
    )
    return bool(min(sides) >= SIDE_COVER)


def _frame_cells(ink: Mask) -> list[Box]:
    """Các ô chữ nhật lớn nhất được đường kẻ dài bao kín (khung bản vẽ), chiếm `CELL_MIN`-`CELL_MAX` tờ."""
    lines = _lines(ink)
    total = ink.shape[0] * ink.shape[1]
    _n, _labels, stats, _c = cv2.connectedComponentsWithStats((~lines).astype(np.uint8), connectivity=4)
    cells = [
        (int(x) - CELL_PAD, int(y) - CELL_PAD, int(x + w) + CELL_PAD, int(y + h) + CELL_PAD)
        for x, y, w, h, _a in stats[1:]
        if CELL_MIN * total <= w * h <= CELL_MAX * total and _closed(lines, int(x), int(y), int(w), int(h))
    ]
    return _maximal(cells)


def _maximal(cells: list[Box]) -> list[Box]:
    """Bỏ ô nằm >= 90 % trong một ô lớn hơn."""
    keep: list[Box] = []
    for cell in sorted(cells, key=lambda c: -_area(c)):
        if not any(_inter(cell, kept) >= CELL_INSIDE * _area(cell) for kept in keep):
            keep.append(cell)
    return keep


def _refine(boxes: list[Box], cells: list[Box]) -> list[Box]:
    """Thay một hộp bằng các ô khung bên trong nó khi >= 2 ô lát kín >= `FRAME_COVER` hộp."""
    out: list[Box] = []
    for box in boxes:
        inside = [c for c in cells if _inter(c, box) >= FRAME_INSIDE * _area(c)]
        covered = sum(_area(c) for c in inside)
        out += inside if len(inside) >= 2 and covered >= FRAME_COVER * _area(box) else [box]
    return out


def _ink_cover(ink: Mask, boxes: list[Box]) -> float:
    """Phần điểm mực của tờ nằm trong ít nhất một hộp (nới `COVER_PAD`); tờ không mực → 1."""
    total = int(ink.sum())
    if total == 0:
        return 1.0
    pad = max(1, round(COVER_PAD * max(ink.shape)))
    inside = np.zeros_like(ink)
    for x0, y0, x1, y1 in boxes:
        inside[max(0, y0 - pad) : y1 + pad, max(0, x0 - pad) : x1 + pad] = True
    return int((ink & inside).sum()) / total


def _to_input(box: Box, scale: float, score: float, size: tuple[int, int]) -> ViewBox:
    """Hộp của bản làm việc → điểm ảnh ảnh vào, kẹp trong khổ ảnh `size` = (rộng, cao)."""
    x0, y0, x1, y1 = (round(v / scale) for v in box)
    return ViewBox(max(0, x0), max(0, y0), min(size[0], x1), min(size[1], y1), score)


def split_views(gray: Gray) -> list[ViewBox]:
    """Khung các hình trên một tờ (ảnh xám), theo thứ tự đọc (trên xuống, trái sang); tờ trống → `[]`.

    Hộp < `MIN_AREA` tờ bị bỏ. Hộp giữ lại phủ < `INK_COVER_MIN` mực của tờ (nền trắng tinh, mặt nạ giấy vụn)
    → cắt hộp bao mực theo đường khung/khe trắng (độ chắc `INK_SPLIT_SCORE`); không cắt được → một hộp bao mực cả
    tờ với độ chắc `WHOLE_SCORE`. Không ném lỗi với ảnh hợp lệ bất kỳ (khổ >= 1x1).
    """
    scale, work = _prepare(gray)
    boxes = [leaf for comp in _components(work.paper, COMPONENT_MIN * work.area) for leaf in _cut(work, comp)]
    boxes = [b for b in _refine(boxes, _frame_cells(work.ink)) if _area(b) >= MIN_AREA * work.area]
    size = (gray.shape[1], gray.shape[0])
    if boxes and _ink_cover(work.ink, boxes) >= INK_COVER_MIN:
        ordered = sorted(boxes, key=lambda b: (b[1], b[0]))
        return [_to_input(b, scale, VIEW_SCORE, size) for b in ordered]
    whole = _tight(work.ink | work.paper, (0, 0, work.grey.shape[1], work.grey.shape[0]))
    if whole is None:
        return []
    parts = [b for b in _ink_cut(work, whole) if _area(b) >= MIN_AREA * work.area]
    if len(parts) > 1:
        return [_to_input(b, scale, INK_SPLIT_SCORE, size) for b in sorted(parts, key=lambda b: (b[1], b[0]))]
    return [_to_input(whole, scale, WHOLE_SCORE, size)]
