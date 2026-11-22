"""Cốt đọc được trên một mặt cắt / mặt đứng, lọc ngoại lai bằng khớp dọc `y_px = y0 - k·cốt_m`.

Chuyển, chỉ dùng chữ OCR, từ nhân nghiên cứu: `register_v.py` (khớp dọc nội lai/ngoại lai),
`heights3.py` (`side_values`, `zero_marks`) và `_dedup` của `read_levels2.py`. Đọc cốt dùng lại
`levels_from_texts` của `levels.py`. Sản phẩm không có tỉ lệ bản vẽ (`mm_per_px`) như bản nghiên cứu,
nên mọi ngưỡng đo bằng mét quy về px bằng chính `k` đã khớp, và độ chắc không kiểm được tỉ lệ (xem
`_confidence`). Phần cần điểm ảnh (dò dấu cạnh hộp, đọc lại vùng cắt) và lượt OCR trang xoay 90°
không chuyển.

Trên mặt cắt, cốt cao hơn nằm cao hơn trên trang, nên mọi cốt đúng nằm trên một đường thẳng theo
trục dọc. Cốt đọc nhầm ("+0.600" thay "+3.600", số của hình bên cạnh) rơi khỏi đường đó và bị loại.
"""

import itertools
import math
import re
import statistics
from collections.abc import Sequence
from typing import Final, NamedTuple

from packages.domain.drawing_set.levels import LevelMark, levels_from_texts
from packages.domain.drawing_set.titles import OcrText

_INLIER_TOL_M: Final = 0.08  # TOL_MM = 80 mm của bản nghiên cứu: cốt cách đường khớp quá mức này là ngoại lai
_MIN_CONFIDENCE: Final = 0.7  # khớp dưới mức này thì không trả cốt nào
_CONF_TWO: Final = 0.7  # hai giá trị nội lai khác nhau
_CONF_MANY: Final = 0.8  # từ ba giá trị nội lai khác nhau
_MANY_DISTINCT: Final = 3
_VALUE_DECIMALS: Final = 3  # hai cốt bằng nhau khi trùng tới milimét
_ZERO_TOL_M: Final = 0.03  # cốt trong khoảng này coi là đã có cốt 0
_DEDUP_VALUE_M: Final = 0.005  # hai chữ cùng giá trị ...
_DEDUP_PX: Final = 60.0  # ... và tâm gần hơn mức này (px) là một ký hiệu đọc hai lần
_TEXT_MAX: Final = 32  # chữ dài hơn không thể là cốt; bỏ trước khi chạy biểu thức chính quy
# ponytail: phép khớp xét mọi cặp ký hiệu, O(n³) với n ký hiệu; trần 64 (~130 nghìn phép, vài chục ms).
# Nhiều hơn thì trả rỗng (dè dặt); cần nâng thì lấy mẫu cặp kiểu RANSAC thay vì vét cạn.
_MAX_MARKS: Final = 64

# Các cách viết cốt 0 mà `levels_from_texts` có thể bỏ sót ("0.00", "00.00", "+/-0.0"); chỉ nhận khi khớp đặt nó ở 0.
_ZERO_LIKE: Final = re.compile(r"(?:\+/-|\+-|%%p|±|\+|-)?0+[.,]0+", re.IGNORECASE)
_WHITESPACE: Final = re.compile(r"\s+")


class _Fit(NamedTuple):
    """Khớp dọc: `y_px = y0 - k·cốt_m`; `confidence` theo số giá trị nội lai khác nhau."""

    y0: float
    k: float
    confidence: float


def _distinct(values: Sequence[float]) -> int:
    """Số giá trị khác nhau (làm tròn tới milimét)."""
    return len({round(v, _VALUE_DECIMALS) for v in values})


def _confidence(distinct: int) -> float:
    """Độ chắc của khớp chỉ theo số giá trị nội lai khác nhau.

    Chỗ lệch khỏi `register_v._conf(d, ratio)`: không có tỉ lệ bản vẽ nên không kiểm được `ratio`. Lấy
    mức thấp nhất `_conf` cho cùng `d` trong các dải tỉ lệ còn hợp lệ (lệch ≤ 5 %): d ≥ 3 → 0,8,
    d = 2 → 0,7 (mức 0,5 của tỉ lệ lệch > 5 % không dùng vì sẽ luôn trả rỗng). Hệ quả: mọi khớp có từ
    hai giá trị nội lai đều qua ngưỡng 0,7, nên chốt chặn thực là số giá trị nội lai, `k > 0` và
    ngưỡng 80 mm.
    """
    if distinct >= _MANY_DISTINCT:
        return _CONF_MANY
    return _CONF_TWO if distinct == 2 else 0.0


def _errors_m(marks: Sequence[tuple[float, float]], y0: float, k: float) -> list[float]:
    """Khoảng cách (m) của từng ký hiệu `(cốt, y)` tới đường khớp."""
    return [abs(y0 - k * v - y) / k for v, y in marks]


def _least_squares(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Bình phương tối thiểu `y = y0 - k·v` trên các cặp `(v, y)` có ít nhất hai `v` khác nhau → `(y0, k)`."""
    mean_v = statistics.fmean(v for v, _ in points)
    mean_y = statistics.fmean(y for _, y in points)
    k = -sum((v - mean_v) * (y - mean_y) for v, y in points) / sum((v - mean_v) ** 2 for v, _ in points)
    return mean_y + k * mean_v, k


def _best_pair(marks: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """Giả thuyết `(y0, k)` tốt nhất qua mọi cặp ký hiệu: nhiều giá trị nội lai, nhiều nội lai, sai số nhỏ.

    Chỗ lệch: bản nghiên cứu xếp trước hết theo "tỉ lệ khớp lệch ≤ 5 %" và cộng sai số bằng px với
    ngưỡng px cố định; ở đây không có tỉ lệ, sai số và ngưỡng đo bằng mét qua `k` của từng giả thuyết.
    """
    best: tuple[tuple[int, int, float], float, float] | None = None
    for (va, ya), (vb, yb) in itertools.combinations(marks, 2):
        if abs(va - vb) < 1e-6:
            continue
        k = (ya - yb) / (vb - va)
        if k <= 0:  # cốt cao hơn phải nằm cao hơn trên trang
            continue
        y0 = ya + k * va
        errors = _errors_m(marks, y0, k)
        inliers = [(v, e) for (v, _), e in zip(marks, errors, strict=True) if e <= _INLIER_TOL_M]
        score = (_distinct([v for v, _ in inliers]), len(inliers), -sum(e for _, e in inliers))
        if best is None or score > best[0]:
            best = (score, y0, k)
    return None if best is None else (best[1], best[2])


def _register(marks: Sequence[tuple[float, float]]) -> _Fit | None:
    """Khớp dọc từ các ký hiệu `(cốt_m, y_px)`; `None` khi dưới hai giá trị khác nhau hoặc không cặp nào hợp lệ."""
    if _distinct([v for v, _ in marks]) < 2:
        return None
    hypothesis = _best_pair(marks)
    if hypothesis is None:
        return None
    y0, k = hypothesis
    inliers = [m for m, e in zip(marks, _errors_m(marks, y0, k), strict=True) if e <= _INLIER_TOL_M]
    if _distinct([v for v, _ in inliers]) >= 2:
        y0, k = _least_squares(inliers)
        if k <= 0:
            return None
        inliers = [m for m, e in zip(marks, _errors_m(marks, y0, k), strict=True) if e <= _INLIER_TOL_M]
    return _Fit(y0, k, _confidence(_distinct([v for v, _ in inliers])))


def _dedup(marks: Sequence[LevelMark]) -> list[LevelMark]:
    """Bỏ chữ đọc lặp: cùng giá trị (±5 mm) và tâm cách nhau dưới 60 px; giữ chữ đến trước."""
    keep: list[LevelMark] = []
    for mark in marks:
        if not any(
            abs(mark.value_m - kept.value_m) < _DEDUP_VALUE_M
            and math.hypot(mark.x - kept.x, mark.y - kept.y) < _DEDUP_PX
            for kept in keep
        ):
            keep.append(mark)
    return keep


def _on_page(item: OcrText, width: int, height: int) -> bool:
    """Chữ ngắn, hộp đủ bốn số và tâm nằm trong trang (loại cả NaN/vô cực)."""
    box = item.box
    if len(item.text) > _TEXT_MAX or len(box) != 4:
        return False
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return 0 <= x <= width and 0 <= y <= height


def _recovered_zero(texts: Sequence[OcrText], fit: _Fit) -> bool:
    """Có chữ dạng cốt 0 ("+/-0.00", "0.00", …) mà khớp đặt ở 0,0 m (±0,08 m) (`zero_marks` của `heights3`)."""
    return any(
        _ZERO_LIKE.fullmatch(_WHITESPACE.sub("", item.text))
        and abs((fit.y0 - (item.box[1] + item.box[3]) / 2) / fit.k) <= _INLIER_TOL_M
        for item in texts
    )


def side_levels(texts: Sequence[OcrText], size: tuple[int, int]) -> tuple[float, ...]:
    """Các cốt (m) đọc được trên một mặt cắt, đã lọc ngoại lai bằng khớp dọc; rỗng khi khớp không đủ tin.

    `size` là `(rộng, cao)` của trang theo px; chữ có tâm ngoài trang bị bỏ. Trả các giá trị nội lai khác
    nhau, tăng dần, làm tròn tới milimét (bản nghiên cứu trả danh sách có lặp theo thứ tự chữ; chuỗi
    tầng chỉ dùng tập giá trị). Khi chưa có cốt 0, một chữ dạng cốt 0 mà khớp đặt ở 0,0 m thì thêm 0.0.
    Rỗng khi dưới hai giá trị khác nhau, độ chắc dưới 0,7, hoặc quá 64 ký hiệu cốt.
    """
    width, height = size
    usable = [item for item in texts if _on_page(item, width, height)]
    marks = levels_from_texts(usable)
    if len(marks) > _MAX_MARKS:
        return ()
    unique = _dedup(marks)
    points = [(mark.value_m, mark.y) for mark in unique]
    fit = _register(points)
    if fit is None or fit.confidence < _MIN_CONFIDENCE:
        return ()
    errors = _errors_m(points, fit.y0, fit.k)
    values = {round(v, _VALUE_DECIMALS) + 0.0 for (v, _), e in zip(points, errors, strict=True) if e <= _INLIER_TOL_M}
    if not any(abs(v) <= _ZERO_TOL_M for v in values) and _recovered_zero(usable, fit):
        values.add(0.0)
    return tuple(sorted(values))
