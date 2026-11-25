"""Chiều dài và cốt ghi feet-inch (`10'-6"`, `EL. 100'-0"`) → milimét (NHA-01, thiết kế §2.D mục 3).

Dấu feet bắt buộc: inch trần (`6"`) và số trần không đủ chắc là feet-inch, mà nhận nhầm tệ hơn bỏ sót. Cả chuỗi
phải là một trị số; `10'-6" x 8'-0"` bị từ chối. Chưa nối vào luồng nào; nơi dùng đầu tiên là NHA-05a/05b.
"""

import re
from typing import Final

_MM_PER_INCH: Final = 25.4
_INCHES_PER_FOOT: Final = 12
_FRACTION_DENOMINATORS: Final = frozenset({2, 4, 8, 16})  # thước inch chia đôi dần; mẫu khác là chữ rác
_MINUS: Final = "-\N{MINUS SIGN}\N{EN DASH}"  # gạch nối đứng đầu: viết `\{_MINUS}` trong lớp ký tự để không tạo dải
_FOOT: Final = "['\N{RIGHT SINGLE QUOTATION MARK}\N{PRIME}]"
_INCH: Final = f'(?:"|{_FOOT}{{2}}|\N{RIGHT DOUBLE QUOTATION MARK}|\N{DOUBLE PRIME})'
_FRACTION: Final = r"(\d{1,2})/(\d{1,2})"
# Nhóm: 1 dấu, 2 feet (tối đa 999' = 304 m), 3 inch nguyên, 4/5 phân số sau inch nguyên, 6/7 phân số đứng một mình.
_FEET_INCH: Final = re.compile(
    rf"(?:EL(?:EV)?\.?\s*)?([+\{_MINUS}])?\s*(\d{{1,3}})\s*{_FOOT}"
    rf"(?:[\s\{_MINUS}]*(?:(\d{{1,2}})(?:[\s\{_MINUS}]+{_FRACTION})?|{_FRACTION})\s*{_INCH}?)?",
    re.IGNORECASE | re.ASCII,
)


def _fraction(numerator: str | None, denominator: str | None) -> float | None:
    """Phân số inch nhỏ hơn 1 với mẫu 2/4/8/16; không viết phân số là 0; sai dạng là `None`."""
    if numerator is None or denominator is None:
        return 0.0
    top, bottom = int(numerator), int(denominator)
    return top / bottom if bottom in _FRACTION_DENOMINATORS and top < bottom else None


def parse_feet_inch(text: str) -> float | None:
    """Trị số feet-inch → milimét (có dấu), hoặc `None` khi không đọc được.

    Nhận `10'`, `10'-6"`, `10' 6 1/2"`, `8'-3/4"`, dấu `+`/`-` đứng đầu, tiền tố `EL.`/`ELEV.`, và các kiểu nháy
    OCR hay trả (nháy cong, dấu phẩy trên, hai nháy đơn thay nháy kép). Dấu inch được thiếu (`10'-6`). Từ chối inch
    từ 12, phân số từ 1.
    """
    found = _FEET_INCH.fullmatch(text.strip())
    if found is None:
        return None
    fraction = _fraction(found[4] or found[6], found[5] or found[7])
    inches = int(found[3] or 0)
    if fraction is None or inches >= _INCHES_PER_FOOT:
        return None
    millimetres = (int(found[2]) * _INCHES_PER_FOOT + inches + fraction) * _MM_PER_INCH
    return round(-millimetres if found[1] and found[1] in _MINUS else millimetres, 3) + 0.0
