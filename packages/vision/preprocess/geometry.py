"""Góc, khung, nắn phối cảnh và nắn nghiêng; bản làm việc dùng chung với `quality`.

Bản làm việc (`working_gray`) và ước lượng nghiêng (`estimate_skew`) nằm ở đây,
không ở `quality`, vì `deskew` cần chúng và `quality` đã nhập `preprocess`
(đặt ngược lại sinh vòng import).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray

from packages.vision.preprocess.errors import VisionError
from packages.vision.preprocess.types import (
    DEFAULT_MAX_PIXELS,
    Homography,
    Point,
    Quad,
    RectifyResult,
    RgbImage,
)

WORKING_LONG_EDGE_PX: Final = 2000

_WHITE: Final = (255.0, 255.0, 255.0)
_MIN_EDGE_PX: Final = 16.0
_MIN_AREA_RATIO: Final = 0.01
_SKEW_MIN_SEGMENTS: Final = 4
_PERSPECTIVE_AXIS_DEG: Final = 20.0
_DESKEW_MIN_DEG: Final = 0.05
_FRAME_MIN_AREA_RATIO: Final = 0.25
_FRAME_INK_RATIO: Final = 0.97
_FRAME_EXPAND: Final = 1.01
_BORDER_TOLERANCE_PX: Final = 2.0
_INK_BLOCK_PX: Final = 15
_HOUGH_THETA_STEPS: Final = 900
_AXIS_TOLERANCE_PX: Final = 2.0


def js_round(value: float) -> int:
    """`Math.round` của JS (`floor(x + 0.5)`): nửa luôn làm tròn lên, khác `round` của Python."""
    return math.floor(value + 0.5)


@dataclass(frozen=True, slots=True)
class WorkingImage:
    """Ảnh xám BT.601 thu nhỏ `INTER_AREA` để cạnh dài ≤ 2.000 px (ảnh nhỏ hơn giữ nguyên).

    `scale` = kích thước bản làm việc / ảnh gốc (≤ 1); toạ độ gốc = toạ độ làm việc / `scale`.
    """

    gray: NDArray[np.uint8]
    scale: float


def working_gray(img: RgbImage) -> WorkingImage:
    """Dựng bản làm việc cho mọi số đo trừ độ phân giải (khối [6] "Số đo")."""
    gray = cv2.cvtColor(img.pixels, cv2.COLOR_RGB2GRAY)
    long_edge = max(img.width_px, img.height_px)
    if long_edge <= WORKING_LONG_EDGE_PX:
        return WorkingImage(np.asarray(gray, dtype=np.uint8), 1.0)
    scale = WORKING_LONG_EDGE_PX / long_edge
    size = (max(1, round(img.width_px * scale)), max(1, round(img.height_px * scale)))
    small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    return WorkingImage(np.asarray(small, dtype=np.uint8), scale)


def fit_size(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    """Kích thước lớn nhất ≤ `max_pixels` giữ gần đúng tỉ lệ; không vượt thì giữ nguyên.

    Tính bằng số nguyên (`isqrt`, chia lấy nguyên) nên `rộng · cao ≤ max_pixels` là bất
    biến chắc chắn, không phụ thuộc sai số làm tròn của `sqrt` — K13 không cho phép trả
    ảnh vượt trần dù chỉ một điểm ảnh. Không bao giờ phóng to.
    """
    if width * height <= max_pixels:
        return width, height
    new_w = max(1, min(width, max_pixels, math.isqrt(max_pixels * width // height)))
    new_h = max(1, min(height, max_pixels // new_w))
    return new_w, new_h


def cap_pixels(pixels: NDArray[np.uint8], max_pixels: int) -> tuple[NDArray[np.uint8], float, float]:
    """Trần đầu ra (U06, K13): co đều để `rộng x cao ≤ max_pixels`.

    Trả `(ảnh, sx, sy)` với `sx = rộng mới / rộng cũ`, `sy` tương tự, để người gọi
    ghép phép co vào homography. Không vượt trần → trả nguyên mảng vào, `1.0, 1.0`.
    """
    height, width = int(pixels.shape[0]), int(pixels.shape[1])
    new_w, new_h = fit_size(width, height, max_pixels)
    if (new_w, new_h) == (width, height):
        return pixels, 1.0, 1.0
    resized = cv2.resize(pixels, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return np.asarray(resized, dtype=np.uint8), new_w / width, new_h / height


@dataclass(frozen=True, slots=True)
class SkewEstimate:
    """Góc nghiêng có dấu `(-45, 45]` độ, chưa làm tròn, và hộp bao các đoạn đã dùng.

    `segments_bbox` = `(x0, y0, x1, y1)` theo pixel **ảnh gốc**; `None` khi < 4 đoạn
    (lúc đó `angle_deg == 0.0` chỉ là giá trị giữ chỗ, xem `is_measured`). Dấu: ảnh
    thẳng xoay bằng `cv2.getRotationMatrix2D(c, +3.4, 1)` đo ra ≈ +3.4.

    `perspective_deg` (G3-35) = độ hội tụ, tức chênh góc lớn nhất giữa nửa trái và
    nửa phải của các đoạn gần dọc (hoặc nửa trên và nửa dưới của các đoạn gần ngang).
    Xoay đều không làm chênh (mọi đoạn nghiêng cùng một góc), còn méo hình thang thì
    có: hai mép dọc nghiêng ngược chiều nhau. `None` khi không nhóm nào có đủ hai nửa.
    """

    angle_deg: float
    segments_bbox: tuple[float, float, float, float] | None
    perspective_deg: float | None = None

    @property
    def is_measured(self) -> bool:
        """Đủ đoạn Hough để đo; `False` thì `angle_deg` không mang nghĩa "thẳng"."""
        return self.segments_bbox is not None


def _weighted_median(values: NDArray[np.float64], weights: NDArray[np.float64]) -> float:
    """Giá trị đầu tiên có luỹ kế trọng số ≥ nửa tổng — trung vị chịu nhiễu tốt hơn trung bình.

    Đoạn dài đáng tin hơn đoạn ngắn nên trọng số là độ dài đoạn.
    """
    order = np.argsort(values)
    sorted_values, sorted_weights = values[order], weights[order]
    cutoff = float(sorted_weights.sum()) / 2.0
    index = int(np.searchsorted(np.cumsum(sorted_weights), cutoff))
    return float(sorted_values[min(index, sorted_values.size - 1)])


def _half_spread(
    deviation: NDArray[np.float64], position: NDArray[np.float64], weights: NDArray[np.float64]
) -> float | None:
    """Chênh trung vị có trọng số của độ lệch trục giữa hai nửa (chia ở giữa dải `position`).

    Một nửa rỗng → `None`: không có gì để so, không kết luận được hội tụ.
    """
    if position.size == 0:
        return None
    first = position < (float(position.min()) + float(position.max())) / 2.0
    if not first.any() or first.all():
        return None
    low = _weighted_median(deviation[first], weights[first])
    high = _weighted_median(deviation[~first], weights[~first])
    return abs(low - high)


def _perspective_spread(points: NDArray[np.float64]) -> float | None:
    """Độ hội tụ (độ) của các đoạn gần trục; lớn nhất của hai nhóm dọc/ngang, `None` nếu cả hai rỗng.

    Chỉ lấy đoạn lệch trục ≤ 20° để nét xiên 45° (gạch chéo, mũi tên) không nhảy
    qua lại giữa hai nhóm. Đoạn dọc chia trái/phải theo hoành độ trung điểm, đoạn
    ngang chia trên/dưới theo tung độ.
    """
    delta_x, delta_y = points[:, 2] - points[:, 0], points[:, 3] - points[:, 1]
    weights = np.hypot(delta_x, delta_y)
    raw = np.degrees(np.arctan2(-delta_y, delta_x)) % 180.0
    horizontal = np.where(raw > 90.0, raw - 180.0, raw)
    vertical = raw - 90.0
    mid_x, mid_y = (points[:, 0] + points[:, 2]) / 2.0, (points[:, 1] + points[:, 3]) / 2.0
    is_h = np.abs(horizontal) <= _PERSPECTIVE_AXIS_DEG
    is_v = np.abs(vertical) <= _PERSPECTIVE_AXIS_DEG
    spreads = [
        spread
        for spread in (
            _half_spread(vertical[is_v], mid_x[is_v], weights[is_v]),
            _half_spread(horizontal[is_h], mid_y[is_h], weights[is_h]),
        )
        if spread is not None
    ]
    return max(spreads) if spreads else None


def estimate_skew(work: WorkingImage) -> SkewEstimate:
    """Trung vị có trọng số độ dài của các đoạn Hough dài, gấp về gần trục nhất.

    Ngưỡng Canny 50/150 và ngưỡng Hough theo cạnh ngắn bản làm việc (xem hằng dưới):
    bản vẽ kỹ thuật là nét đen trên nền trắng nên tương phản luôn cao, không cần
    ngưỡng thích nghi ở đây. Góc lấy theo `-atan2(dy, dx)` rồi mới gấp về `(-45, 45]`
    — đảo dấu trước khi gấp giữ miền giá trị đúng mà không cần nhánh chỉnh biên.

    Bước góc Hough là 0,2° chứ không phải 1° mặc định: với 1° đầu mút đoạn bị bắt
    vào bội số của 1° nên ảnh nghiêng 3,4° đo ra 2,99°, ngoài dải test. 0,2° cho
    3,40° và tốn ~0,1 s trên bản làm việc 2.000 px (trần đã có sẵn).

    Cùng lượt đoạn Hough còn cho `perspective_deg` (`_perspective_spread`): trung vị
    góc không thấy méo hình thang vì hai mép dọc ±θ triệt nhau (G3-35).
    """
    short_edge = min(int(work.gray.shape[0]), int(work.gray.shape[1]))
    edges = cv2.Canny(cv2.GaussianBlur(work.gray, (5, 5), 0), 50, 150)
    segments = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / _HOUGH_THETA_STEPS,
        threshold=max(50, short_edge // 20),
        minLineLength=max(40.0, short_edge / 10),
        maxLineGap=max(4.0, short_edge / 200),
    )
    if segments is None or len(segments) < _SKEW_MIN_SEGMENTS:
        return SkewEstimate(0.0, None)
    points = segments.reshape(-1, 4).astype(np.float64)
    delta_x, delta_y = points[:, 2] - points[:, 0], points[:, 3] - points[:, 1]
    folded = np.degrees(np.arctan2(-delta_y, delta_x)) % 90.0
    folded = np.where(folded > 45.0, folded - 90.0, folded)
    angle = _weighted_median(folded, np.hypot(delta_x, delta_y))
    xs, ys = points[:, 0::2], points[:, 1::2]
    bbox = (
        float(xs.min()) / work.scale,
        float(ys.min()) / work.scale,
        float(xs.max()) / work.scale,
        float(ys.max()) / work.scale,
    )
    return SkewEstimate(angle, bbox, _perspective_spread(points))


def order_corners(points: Sequence[Point]) -> Quad:
    """Sắp 4 điểm chiều kim đồng hồ (y hướng xuống) từ điểm có `x + y` nhỏ nhất.

    Trong hệ ảnh (y hướng xuống) góc `atan2` tăng dần chính là chiều kim đồng hồ,
    nên chỉ cần sắp theo góc quanh trọng tâm rồi xoay danh sách về điểm bắt đầu.
    Hoà `x + y` thì lấy `x` nhỏ hơn. Không đúng 4 điểm → `VALIDATION`, `field="corners"`.
    """
    if len(points) != 4:
        raise VisionError("VALIDATION", field="corners")
    pixels = [(float(x), float(y)) for x, y in points]
    center_x = sum(p[0] for p in pixels) / 4.0
    center_y = sum(p[1] for p in pixels) / 4.0
    clockwise = sorted(pixels, key=lambda p: math.atan2(p[1] - center_y, p[0] - center_x))
    start = min(range(4), key=lambda i: (clockwise[i][0] + clockwise[i][1], clockwise[i][0]))
    rotated = clockwise[start:] + clockwise[:start]
    return Quad((rotated[0], rotated[1], rotated[2], rotated[3]))


def _edge_lengths(points: Sequence[Point]) -> list[float]:
    """Độ dài 4 cạnh theo thứ tự đã cho (cạnh cuối nối điểm 4 về điểm 1)."""
    return [math.dist(points[i], points[(i + 1) % 4]) for i in range(4)]


def _is_convex_clockwise(points: Sequence[Point]) -> bool:
    """Lồi, không tự cắt và đi chiều kim đồng hồ (y hướng xuống): 4 tích có hướng đều > 0.

    Tứ giác tự cắt (hình nơ) luôn có hai tích trái dấu, ba điểm thẳng hàng cho tích 0,
    ngược chiều kim đồng hồ (sẽ nắn ra ảnh lật gương) cho cả 4 tích âm — một phép kiểm
    phủ cả ba luật của khối [6].
    """
    crosses = []
    for i in range(4):
        ax, ay = points[i]
        bx, by = points[(i + 1) % 4]
        cx, cy = points[(i + 2) % 4]
        crosses.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    return all(c > 0 for c in crosses)


def _polygon_area(points: Sequence[Point]) -> float:
    """Diện tích tứ giác theo công thức dây giày (không dấu)."""
    total = sum(points[i][0] * points[(i + 1) % 4][1] - points[(i + 1) % 4][0] * points[i][1] for i in range(4))
    return abs(total) / 2.0


def validate_quad(quad: Quad, width_px: int, height_px: int) -> None:
    """Luật góc người chọn → `VisionError("VALIDATION", field="corners")`.

    Loại: góc ngoài `[0, w] x [0, h]`, không lồi hoặc tự cắt, ngược chiều kim đồng
    hồ, cạnh < 16 px, diện tích < 1 % ảnh. Một mã lỗi duy nhất vì FE chỉ tô lại vùng
    chọn, không phân biệt lý do (`quality.ts:139-159`). Điểm đầu **không** bị ép về
    `order_corners`: nó là TL của ảnh nắn, do người gọi chọn (#31 kiểm luật TL trên
    tỉ lệ trang đang hiển thị, NO-227) — so `x + y` pixel ở đây sai hệ toạ độ (ảnh
    không vuông, trang đã nắn) và loại nhầm hình thoi bắt đầu ở đỉnh trên (BUG-576).
    """
    points = quad.points
    valid = (
        all(0.0 <= x <= width_px and 0.0 <= y <= height_px for x, y in points)
        and _is_convex_clockwise(points)
        and min(_edge_lengths(points)) >= _MIN_EDGE_PX
        and _polygon_area(points) >= _MIN_AREA_RATIO * width_px * height_px
    )
    if not valid:
        raise VisionError("VALIDATION", field="corners")


def quad_to_ratios(quad: Quad, width_px: int, height_px: int) -> tuple[Point, Point, Point, Point]:
    """Góc pixel → tỉ lệ `[0, 1]` theo kích thước ảnh (dạng FE lưu, độc lập độ phân giải)."""
    ratios = [(x / width_px, y / height_px) for x, y in quad.points]
    return (ratios[0], ratios[1], ratios[2], ratios[3])


def quad_from_ratios(ratios: Sequence[Point], width_px: int, height_px: int) -> Quad:
    """Tỉ lệ `[0, 1]` → góc pixel; không đúng 4 tỉ lệ → `VALIDATION`, `field="corners"`."""
    if len(ratios) != 4:
        raise VisionError("VALIDATION", field="corners")
    points = [(x * width_px, y * height_px) for x, y in ratios]
    return Quad((points[0], points[1], points[2], points[3]))


def _scale_quad(quad: Quad, factor: float) -> Quad:
    """Nhân toạ độ 4 góc với `factor` (đổi giữa bản làm việc và ảnh gốc)."""
    points = [(x * factor, y * factor) for x, y in quad.points]
    return Quad((points[0], points[1], points[2], points[3]))


def _ink_mask(gray: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Mặt nạ nét bằng ngưỡng thích nghi: mặt bàn hay giấy đồng màu không bị tính là nét.

    Ô cửa sổ 15 px ≈ 2-3 lần bề dày nét trên bản làm việc. Cửa sổ rộng hơn (≈ 1/40
    cạnh ngắn) biến cả dải mặt bàn sát mép giấy thành "nét" giả, đủ để đẩy tỉ lệ
    bao phủ của mép giấy xuống dưới 97 % — đã đo: 0,91 với ô 31 px, 1,00 với ô 15 px.
    """
    mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, _INK_BLOCK_PX, 10)
    return np.asarray(mask, dtype=np.uint8)


def _is_image_border(quad: Quad, width: int, height: int) -> bool:
    """Tứ giác trùng chính viền ảnh (cả 4 góc lệch ≤ 2 px) — không phải khung bản vẽ."""
    corners = ((0.0, 0.0), (width - 1.0, 0.0), (width - 1.0, height - 1.0), (0.0, height - 1.0))
    return all(math.dist(q, c) <= _BORDER_TOLERANCE_PX for q, c in zip(quad.points, corners, strict=True))


def _quad_candidates(gray: NDArray[np.uint8]) -> list[Quad]:
    """Tứ giác lồi ≥ 25 % diện tích ảnh từ đường viền Canny, đã loại tứ giác trùng viền ảnh.

    Dùng viền Canny chứ không viền của mặt nạ nét: nét dày cho hai viền lồng nhau,
    còn mép giấy trên mặt bàn chỉ có một viền mảnh đúng chỗ. Vòng lặp chạy theo
    đường viền (vài chục), không theo điểm ảnh (K28).
    """
    height, width = int(gray.shape[0]), int(gray.shape[1])
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = _FRAME_MIN_AREA_RATIO * width * height
    candidates = []
    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if cv2.contourArea(approx) < min_area:
            continue
        quad = order_corners([(float(p[0][0]), float(p[0][1])) for p in approx])
        if not _is_image_border(quad, width, height):
            candidates.append(quad)
    return candidates


def _ink_inside_ratio(ink: NDArray[np.uint8], quad: Quad) -> float:
    """Tỉ lệ điểm nét nằm trong tứ giác nở 1 % — khung thật bao gần hết nét của bản vẽ.

    Nở 1 % để nét nằm ngay trên đường khung (chính khung bản vẽ) vẫn được tính.
    Đếm bằng mặt nạ OpenCV, không duyệt điểm ảnh trong Python (K28). Mẫu số chặn
    dưới ở 1 nên ảnh không có nét nào cho tỉ lệ 0 thay vì chia cho 0.
    """
    total = max(1, cv2.countNonZero(ink))
    corners = np.array(quad.points, dtype=np.float64)
    expanded = corners.mean(axis=0) + (corners - corners.mean(axis=0)) * _FRAME_EXPAND
    mask = np.zeros(ink.shape, dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(expanded).astype(np.int32), 255)
    return cv2.countNonZero(cv2.bitwise_and(ink, mask)) / total


def find_frame(img: RgbImage) -> Quad | None:
    """Khung bản vẽ lớn nhất (≥ 25 % ảnh, chứa ≥ 97 % nét) hoặc `None`; toạ độ ảnh gốc."""
    return find_frame_working(working_gray(img))


def find_frame_working(work: WorkingImage) -> Quad | None:
    """Như `find_frame` nhưng nhận bản làm việc đã dựng sẵn (`quality.assess` dùng lại).

    Tách ra để một lượt `assess` chỉ dựng bản làm việc **một lần** thay vì hai
    (`measure` một, `find_frame` một) — bước tốn nhất trên ảnh 40 MP.
    """
    ink = _ink_mask(work.gray)
    best: Quad | None = None
    best_area = 0.0
    for quad in _quad_candidates(work.gray):
        area = _polygon_area(quad.points)
        if area <= best_area or _ink_inside_ratio(ink, quad) < _FRAME_INK_RATIO:
            continue
        best, best_area = quad, area
    return None if best is None else _scale_quad(best, 1.0 / work.scale)


def _warp_matrix_to_cap(
    matrix: NDArray[np.float64], width: int, height: int, max_pixels: int
) -> tuple[NDArray[np.float64], int, int]:
    """Co ma trận nắn về kích thước lọt trần **trước** khi warp (K13 + tránh cấp phát khổng lồ).

    Nhân trái bằng `diag(sx, sy, 1)` tương đương `compose` với phép co, nhưng không
    phải dựng `Homography` trung gian chỉ để vứt đi.
    """
    out_w, out_h = fit_size(width, height, max_pixels)
    if (out_w, out_h) == (width, height):
        return matrix, width, height
    shrink = np.diag([out_w / width, out_h / height, 1.0])
    return np.asarray(shrink @ matrix, dtype=np.float64), out_w, out_h


def _axis_box(quad: Quad, width_px: int, height_px: int) -> tuple[int, int, int, int] | None:
    """Hộp nguyên `(x0, y0, x1, y1)` khi `quad` gần như chữ nhật thẳng trục (cặp góc cùng cạnh lệch
    <= `_AXIS_TOLERANCE_PX`), không thì `None`.

    Khung tìm trên ảnh xuất số hay lệch 1 px (bench căn hộ ch-03/05/06); cắt thay vì nắn khi đó sai
    hình <= 2 px, còn nắn thì làm nhạt mọi nét 1 px. Toạ độ kẹp vào ảnh vì góc được nằm đúng trên mép.
    """
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = quad.points
    if max(abs(tlx - blx), abs(trx - brx), abs(tly - try_), abs(bly - bry)) > _AXIS_TOLERANCE_PX:
        return None
    left, right = (min(max(js_round((a + b) / 2), 0), width_px) for a, b in ((tlx, blx), (trx, brx)))
    top, bottom = (min(max(js_round((a + b) / 2), 0), height_px) for a, b in ((tly, try_), (bly, bry)))
    return (left, top, right, bottom) if left < right and top < bottom else None


def crop(img: RgbImage, box: tuple[int, int, int, int]) -> RectifyResult:
    """Cắt nguyên pixel hộp `(x0, y0, x1, y1)` (đã nằm trong ảnh); homography là phép tịnh tiến.

    Không nội suy: nét 1 px của bản vẽ CAD giữ nguyên độ đậm. Nắn bằng `warpPerspective` một khung
    vốn đã thẳng làm đường kích thước nhạt còn ~9 mức so với nền (đo ở bench căn hộ ch-06), nên
    đường kích thước không còn đo được.
    """
    x0, y0, x1, y1 = box
    pixels = np.ascontiguousarray(img.pixels[y0:y1, x0:x1])
    shift = np.array([[1.0, 0.0, -x0], [0.0, 1.0, -y0], [0.0, 0.0, 1.0]])
    homography = Homography.from_array(shift, (img.width_px, img.height_px), (x1 - x0, y1 - y0))
    return RectifyResult(RgbImage(pixels), homography)


def rectify(img: RgbImage, quad: Quad, *, max_pixels: int = DEFAULT_MAX_PIXELS) -> RectifyResult:
    """Nắn phối cảnh giữ tỉ lệ khung, nền trắng, qua trần đầu ra.

    `quad` là chữ nhật thẳng trục và lọt trần → `crop`, không nội suy (khung bản vẽ xuất số hầu
    hết đã thẳng). Còn lại: kích thước đích lấy cạnh dài nhất của mỗi hướng (`js_round`) để không
    nén nét; vượt trần thì co ma trận trước `warpPerspective`. Homography trả về đưa đúng 4 góc
    nguồn về `(0,0), (w,0), (w,h), (0,h)` của ảnh kết quả.
    """
    validate_quad(quad, img.width_px, img.height_px)
    box = _axis_box(quad, img.width_px, img.height_px)
    if box is not None and (box[2] - box[0]) * (box[3] - box[1]) <= max_pixels:
        return crop(img, box)
    top_left, top_right, bottom_right, bottom_left = quad.points
    target_w = max(1, js_round(max(math.dist(top_right, top_left), math.dist(bottom_right, bottom_left))))
    target_h = max(1, js_round(max(math.dist(bottom_left, top_left), math.dist(bottom_right, top_right))))
    source = np.array(quad.points, dtype=np.float32)
    dest = np.array([(0.0, 0.0), (target_w, 0.0), (target_w, target_h), (0.0, target_h)], dtype=np.float32)
    perspective = np.asarray(cv2.getPerspectiveTransform(source, dest), dtype=np.float64)
    matrix, width, height = _warp_matrix_to_cap(perspective, target_w, target_h, max_pixels)
    warped = cv2.warpPerspective(
        img.pixels,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=_WHITE,
    )
    homography = Homography.from_array(matrix, (img.width_px, img.height_px), (width, height))
    return RectifyResult(RgbImage(np.asarray(warped, dtype=np.uint8)), homography)


def _capped_unchanged(img: RgbImage, max_pixels: int) -> RectifyResult:
    """Nhánh ảnh đã thẳng của `deskew`: giữ nguyên hình, chỉ đi qua trần đầu ra."""
    pixels, scale_x, scale_y = cap_pixels(img.pixels, max_pixels)
    source = (img.width_px, img.height_px)
    image = RgbImage(pixels)
    if source == (image.width_px, image.height_px):
        return RectifyResult(image, Homography.identity(*source))
    matrix = np.diag([scale_x, scale_y, 1.0])
    target = (image.width_px, image.height_px)
    return RectifyResult(image, Homography.from_array(matrix, source, target))


def deskew(img: RgbImage, *, max_pixels: int = DEFAULT_MAX_PIXELS) -> RectifyResult:
    """Xoay `-skew` mở rộng khung, nền trắng; `|skew| < 0,05°` → giữ nguyên.

    `skew` làm tròn 2 chữ số như `quality` để hai nơi báo cùng một con số. Khung mở
    rộng theo hộp bao của ảnh đã xoay nên không nét nào bị cắt; phần thêm ra là nền
    trắng. Cả hai nhánh đều qua trần đầu ra (K13).
    """
    skew = round(estimate_skew(working_gray(img)).angle_deg, 2)
    if abs(skew) < _DESKEW_MIN_DEG:
        return _capped_unchanged(img, max_pixels)
    width, height = img.width_px, img.height_px
    center = (width / 2.0, height / 2.0)
    affine = cv2.getRotationMatrix2D(center, -skew, 1.0)
    cos_a, sin_a = abs(float(affine[0, 0])), abs(float(affine[0, 1]))
    grown_w = math.ceil(height * sin_a + width * cos_a)
    grown_h = math.ceil(height * cos_a + width * sin_a)
    affine[0, 2] += grown_w / 2.0 - center[0]
    affine[1, 2] += grown_h / 2.0 - center[1]
    square = np.vstack([affine, np.array([0.0, 0.0, 1.0])])
    matrix, out_w, out_h = _warp_matrix_to_cap(square, grown_w, grown_h, max_pixels)
    rotated = cv2.warpAffine(
        img.pixels,
        np.ascontiguousarray(matrix[:2]),
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=_WHITE,
    )
    homography = Homography.from_array(matrix, (width, height), (out_w, out_h))
    return RectifyResult(RgbImage(np.asarray(rotated, dtype=np.uint8)), homography)
