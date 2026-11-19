"""Cốt (level mark) trên bản vẽ: "+3.600", "±0.000", "-0.450" → mét, và cốt sàn chính của một mặt bằng.

Chuyển từ nhân nghiên cứu `read_levels.py`, phần chỉ cần chữ của `read_levels2.py`,
`norm_zero` của `solve_set.py` và luật "sàn hoàn thiện chính" của `heights3.py`. Phần cần ảnh
(đọc lại vùng cắt, dò dấu bằng điểm ảnh) không chuyển: mô-đun này chỉ dùng thư viện chuẩn.

Chữ OCR hay méo theo vài kiểu đã đếm được: thêm tiền tố ("FL:+2.900", "|+17.000"), thừa dấu đóng
("+0.000)"), ký hiệu bị đọc thành số ("1±0.000"), cốt viết bằng mét kèm "m" ("-0,25m") hoặc bằng
milimét có dấu ("+3600"). Một chuỗi không phải cốt (tên phòng, kích thước "3600") phải bị từ chối:
nhận nhầm tệ hơn bỏ sót, vì cốt sai kéo lệch cả chuỗi tầng.
"""

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

_LEVEL_MIN_M: Final = -10.0  # ngoài dải này không phải cốt nhà dân dụng
_LEVEL_MAX_M: Final = 60.0
_MINUS: Final = "-\N{MINUS SIGN}"  # gạch nối và dấu trừ Unicode; "±" và ký tự thay thế U+FFFD tính là dấu dương

_WHITESPACE: Final = re.compile(r"\s+")
_SIGNED: Final = re.compile(r"([+\-\N{MINUS SIGN}±\N{REPLACEMENT CHARACTER}])([0-9]{1,2})[.,]([0-9]{2,3})")
_UNSIGNED: Final = re.compile(r"([0-9]{1,2})[.,]([0-9]{3})")

# Đọc lỏng: sau số được thừa đúng một ký tự trong `| ) ] 1` hoặc hậu tố `m`; trước dấu không được
# là chữ số hay dấu thập phân (tránh cắt "1610.050" thành "0.050").
_LOOSE: Final = re.compile(
    r"(?<![0-9.,])([+\-\N{MINUS SIGN}±\N{REPLACEMENT CHARACTER}]?)([0-9]{1,2})[.,]([0-9]{2,3})(?:[|)\]1]|m)?$"
)
_DIGIT_BEFORE_SYMBOL: Final = re.compile(r"^[0-9](?=[±+])")  # "1±0.000": ký hiệu bị đọc thành số
_DIGIT_GLUED_TO_SIGN: Final = re.compile(r"(?<=[0-9])(?=[±+][0-9])")  # "FL1+8.950"
_PREFIX_MAX: Final = 4  # tiền tố dài hơn là tên phòng hay ghi chú, không phải cốt

# Mọi cách viết số 0 mà bộ đọc nghiêm ngặt bỏ sót: "+/-0.00", "+-0.000", "±000.00", "%%p0.000".
_ZERO: Final = re.compile(r"\W{0,2}(?:\+/-|\+-|\+/\N{MINUS SIGN}|±|%%p)0{1,3}[.,]0{2,3}", re.IGNORECASE)
_ZERO_TEXT: Final = "±0.000"

# Cốt viết bằng milimét có dấu. Dấu bắt buộc: số kích thước không bao giờ có dấu.
_MM: Final = re.compile(r"(?:[+\-\N{MINUS SIGN}±]|%%[pP])\s*([0-9]{3,5}|0)")
_MM_MAX: Final = 60000
_MM_MIN_DISTINCT: Final = 2  # ít hơn hai giá trị khác nhau thì cột "+3600" có thể chỉ là một kích thước
_ZERO_EPS_M: Final = 0.0005  # |giá trị| nhỏ hơn mức này coi là 0 khi xét "có cốt thập phân khác 0"

_CLUSTER_TOL_M: Final = 0.03  # hai cốt cách nhau không quá mức này là một
_MAIN_GAP_M: Final = 0.12  # cốt cách nhau dưới mức này trên một mặt bằng cùng thuộc một sàn
_ROUND_STEP_M: Final = 0.05  # cốt "tròn" là bội của 0,05 m


class TextLike(Protocol):
    """Chữ đọc được trên bản vẽ: `TextPx` của `packages.ml_contracts` khớp dạng này."""

    @property
    def text(self) -> str:
        """Chuỗi chữ thô."""
        ...

    @property
    def box(self) -> Sequence[float]:
        """Hộp `(x0, y0, x1, y1)` theo px của trang."""
        ...


@dataclass(frozen=True, slots=True)
class LevelMark:
    """Một cốt đọc được: giá trị (m), chữ gốc, tâm hộp (px) và dạng viết ("decimal" hay "mm")."""

    value_m: float
    text: str
    x: float
    y: float
    source: Literal["decimal", "mm"]


@dataclass(frozen=True, slots=True)
class MainLevel:
    """Cốt sàn chính của một mặt bằng: giá trị, số cốt thuộc nhóm đó, giá trị các nhóm còn lại."""

    value_m: float
    count: int
    alternatives: tuple[float, ...]


def _in_range(value: float) -> float | None:
    """Giá trị nếu nằm trong dải cốt, ngược lại `None`; `+ 0.0` đổi `-0.0` thành `0.0`."""
    return value + 0.0 if _LEVEL_MIN_M <= value <= _LEVEL_MAX_M else None


def parse_level(text: str) -> float | None:
    """Cốt dạng chặt → mét, hoặc `None`.

    Chấp nhận `[dấu]` + 1-2 chữ số + `.`/`,` + 2-3 chữ số (có dấu), hoặc đúng 3 chữ số thập phân khi
    không dấu ("3.600"). Từ chối "1200", "12.5", "FL:+2.900" và giá trị ngoài [-10, 60] m.
    """
    compact = _WHITESPACE.sub("", text)
    signed = _SIGNED.fullmatch(compact)
    if signed:
        value = float(f"{signed[2]}.{signed[3]}")
        value = -value if signed[1] in _MINUS else value
    else:
        unsigned = _UNSIGNED.fullmatch(compact)
        if unsigned is None:
            return None
        value = float(f"{unsigned[1]}.{unsigned[2]}")
    return _in_range(value)


def parse_level_loose(text: str) -> float | None:
    """Cốt trong chuỗi có rác quanh số → mét, hoặc `None` (đọc lỏng, "Pass A" của nhân nghiên cứu).

    Cho phép tiền tố ngắn ("FL:+2.900"), một ký tự thừa sau số, hậu tố `m` ("-0,25m"). Dấu bắt buộc:
    số trần ("0.450", "3,30m") có thể là chiều dài, nên trả `None` (nhân nghiên cứu chỉ nhận số trần
    khi dò thấy dấu trong điểm ảnh, phần này không chuyển). Tiền tố dài hơn 4 ký tự bị từ chối.
    """
    compact = _WHITESPACE.sub("", text)
    compact = _DIGIT_BEFORE_SYMBOL.sub("", compact)
    compact = _DIGIT_GLUED_TO_SIGN.sub("|", compact)
    found = _LOOSE.search(compact)
    if found is None or found.start() > _PREFIX_MAX or not found[1]:
        return None
    value = float(f"{found[2]}.{found[3]}")
    return _in_range(-value if found[1] in _MINUS else value)


def norm_zero(text: str) -> str:
    """Các cách viết cốt 0 mà `parse_level` bỏ sót ("+/-0.00", "+-0.000", "±000.00") → "±0.000".

    Chuỗi khác trả nguyên. Cốt 0 mất khỏi danh sách làm lệch cả chuỗi tầng một bậc, nên chuẩn hoá
    trước khi đọc.
    """
    return _ZERO_TEXT if _ZERO.fullmatch(_WHITESPACE.sub("", text)) else text


def parse_level_mm(text: str) -> float | None:
    """Cốt viết bằng milimét có dấu ("+3600", "+ 6900", "±0", "+0000") → mét, hoặc `None`.

    Phải có dấu, là bội của 10 và không quá 60000. Không dấu ("3600") là số kích thước, không phải cốt.
    """
    found = _MM.fullmatch(text.strip())
    if found is None:
        return None
    millimetres = int(found[1])
    if millimetres % 10 or millimetres > _MM_MAX:
        return None
    negative = text.strip()[0] in _MINUS
    return (-millimetres if negative else millimetres) / 1000 + 0.0


def _centre(item: TextLike) -> tuple[float, float]:
    """Tâm của hộp `(x0, y0, x1, y1)`."""
    x0, y0, x1, y1 = item.box
    return (x0 + x1) / 2, (y0 + y1) / 2


def _decimal_mark(item: TextLike) -> LevelMark | None:
    """Cốt dạng thập phân của một chữ: đọc chặt trước, không được thì đọc lỏng; `None` nếu không phải."""
    text = norm_zero(item.text)
    value = parse_level(text)
    if value is None:
        value = parse_level_loose(text)
    if value is None:
        return None
    x, y = _centre(item)
    return LevelMark(value, item.text, x, y, "decimal")


def _mm_mark(item: TextLike) -> LevelMark | None:
    """Cốt dạng milimét của một chữ, hoặc `None`."""
    value = parse_level_mm(item.text)
    if value is None:
        return None
    x, y = _centre(item)
    return LevelMark(value, item.text, x, y, "mm")


def levels_from_texts(texts: Iterable[TextLike]) -> tuple[LevelMark, ...]:
    """Cốt của một hình, từ chữ OCR: dạng thập phân trước, rồi dạng milimét khi qua đủ chốt chặn.

    Một bộ bản vẽ ghi cốt theo một kiểu, và số có dấu 4 chữ số rất giống kích thước, nên mark milimét
    chỉ nhận khi hình không có cốt thập phân khác 0 (cốt "±0.000" nằm trong cột milimét thì vẫn được)
    và có ít nhất hai giá trị milimét khác nhau.
    """
    items = tuple(texts)
    decimal = tuple(mark for item in items if (mark := _decimal_mark(item)) is not None)
    if any(abs(mark.value_m) > _ZERO_EPS_M for mark in decimal):
        return decimal
    millimetre = tuple(mark for item in items if (mark := _mm_mark(item)) is not None)
    if len({mark.value_m for mark in millimetre}) < _MM_MIN_DISTINCT:
        return decimal
    return decimal + millimetre


def _clusters(values: Iterable[float]) -> list[tuple[float, int]]:
    """Gom cốt cách nhau ≤ 0,03 m quanh giá trị hay gặp nhất → `(giá trị đại diện, số lần)`."""
    # Số lần của cốt còn lại không đổi khi gom, nên thứ tự hạt giống là thứ tự sắp một lần; thành viên tìm
    # bằng bisect trong cửa sổ rộng hơn ngưỡng một chút rồi lọc đúng điều kiện cũ (O(k log k), không O(k²)).
    left = Counter(round(value, 3) for value in values)
    ordered = sorted(left)
    out: list[tuple[float, int]] = []
    for seed in sorted(left, key=lambda value: (-left[value], value)):
        if seed not in left:
            continue
        lo = bisect_left(ordered, seed - _CLUSTER_TOL_M - 0.001)
        hi = bisect_right(ordered, seed + _CLUSTER_TOL_M + 0.001)
        members = [v for v in ordered[lo:hi] if v in left and round(abs(v - seed), 3) <= _CLUSTER_TOL_M]
        out.append((seed, sum(left.pop(value) for value in members)))
    return out


def _floor_groups(clusters: list[tuple[float, int]]) -> list[list[tuple[float, int]]]:
    """Chia các cụm đã sắp tăng dần thành nhóm: cụm cách cụm đầu nhóm dưới 0,12 m cùng một sàn."""
    groups: list[list[tuple[float, int]]] = []
    for cluster in sorted(clusters):
        if groups and round(cluster[0] - groups[-1][0][0], 3) < _MAIN_GAP_M:
            groups[-1].append(cluster)
        else:
            groups.append([cluster])
    return groups


def _is_round(value: float) -> bool:
    """`value` là bội của 0,05 m."""
    steps = value / _ROUND_STEP_M
    return abs(steps - round(steps)) < 1e-6


def level_groups(values: Iterable[float], *, prefer_zero: bool = False) -> list[tuple[float, int]]:
    """Các sàn của một mặt bằng, tăng dần: `(giá trị đại diện, tổng số lần của nhóm)`.

    Cốt cách nhau dưới 0,12 m là một sàn. Đại diện là giá trị tròn (bội 0,05 m) và cao hơn; với
    `prefer_zero` (mặt bằng tầng trệt) thì 0.000 thắng mọi giá trị khác trong nhóm. Rỗng khi không có cốt.
    """
    return [
        (
            max(
                (v for v, _ in group),
                key=lambda v: (prefer_zero and abs(v) <= _CLUSTER_TOL_M, _is_round(v), v),
            ),
            sum(count for _, count in group),
        )
        for group in _floor_groups(_clusters(values))
    ]


def main_floor_level(values: Iterable[float]) -> MainLevel | None:
    """Cốt sàn chính của một mặt bằng từ các cốt trên nó; `None` khi không có cốt nào.

    Cốt cách nhau dưới 0,12 m là một sàn (sàn hoàn thiện cùng WC, ban công thấp hơn một chút), đại
    diện là giá trị tròn (bội 0,05 m) và cao hơn: cốt WC trũng thua dù xuất hiện nhiều hơn. Sàn
    chính là nhóm đông nhất, hoà thì nhóm cao hơn; các nhóm còn lại trả ở `alternatives`.
    """
    ranked = sorted(level_groups(values), key=lambda item: (-item[1], -item[0]))
    if not ranked:
        return None
    (value, count), rest = ranked[0], ranked[1:]
    return MainLevel(value, count, tuple(v for v, _ in rest))
