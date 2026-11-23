"""Khung tên chung của cả bộ hồ sơ và số hiệu từng hình, từ khung tên từng trang đã lưu (K-65 đợt 2).

`set_analysis` lưu khung tên đọc được của mỗi trang ở `machine["titleBlock"]` dạng
`{"owner": {"value": ..., "confidence": ...}, ..., "sheetNo": {...}}` (chỉ trường đọc chắc). Ở đây chỉ **đọc lại** dạng
đó, không đọc chữ OCR lần nữa. Hàm thuần, chỉ thư viện chuẩn, nhận dữ liệu lỏng (JSON từ DB) và không bao giờ ném lỗi
với dạng lạ: trường sai dạng coi như vắng.

Luật khung tên chung của bộ (mỗi trường quyết riêng):
 * Mỗi TRANG tính một lần (nhiều hình cùng trang không làm giá trị "nhiều phiếu hơn").
 * Trang không đọc được trường đó thì không bỏ phiếu; không trang nào đọc được thì trường vắng.
 * Các trang có giá trị phải KHỚP: chủ đầu tư/công trình/địa điểm khớp gần đúng (OCR nhiễu một hai ký tự, cùng ngưỡng
   `title_block.SAME_RATIO` với luật trong một trang); ngày phải khớp đúng từng ký tự sau khi gấp chữ (ngày lệch một
   chữ số là ngày khác). Có hai trang lệch nhau thì BỎ trường đó, không chọn bên nào.
 * Giá trị trả về là của trang có độ tin cao nhất (hoà: trang đầu), nguyên chữ đã lưu.
"""

from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from typing import Final

from packages.domain.drawing_set.title_block import SAME_RATIO
from packages.domain.drawing_set.titles import fold

type PageKey = tuple[str, int]

COMMON_FIELDS: Final = ("owner", "project", "location", "date")
"""Khoá dây của khung tên chung (không có số hiệu: số hiệu là của từng hình)."""
_EXACT: Final = frozenset({"date"})
"""Trường so đúng từng ký tự (sau gấp chữ); các trường còn lại so gần đúng."""


def _field(machine: object, name: str) -> tuple[str, float] | None:
    """`(value, confidence)` của một trường khung tên trong `machine`; sai dạng hoặc rỗng thì `None`."""
    block = machine.get("titleBlock") if isinstance(machine, Mapping) else None
    item = block.get(name) if isinstance(block, Mapping) else None
    value = item.get("value") if isinstance(item, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        return None
    confidence = item.get("confidence") if isinstance(item, Mapping) else None
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        return value.strip(), 0.0
    return value.strip(), float(confidence)


def sheet_no_of(machine: object) -> str | None:
    """Số hiệu bản vẽ của hình (khung tên của trang chứa nó); vắng khi máy không đọc chắc."""
    found = _field(machine, "sheetNo")
    return None if found is None else found[0]


def _agree(name: str, left: str, right: str) -> bool:
    """Hai giá trị của cùng một trường có phải một giá trị không."""
    a, b = fold(left), fold(right)
    if name in _EXACT:
        return a == b
    return a == b or SequenceMatcher(None, a, b).ratio() >= SAME_RATIO


def common_title_block(pages: Iterable[tuple[PageKey, object]]) -> dict[str, str]:
    """Khung tên chung của bộ từ `(khoá trang, machine)` của các hình dùng được; trường lệch/vắng thì không có khoá.

    Khoá trang là `(file_id, page_index)`: trang nhiều hình chỉ tính một lần (hình đầu tiên của trang).
    """
    per_page: dict[PageKey, object] = {}
    for key, machine in pages:
        per_page.setdefault(key, machine)
    common: dict[str, str] = {}
    for name in COMMON_FIELDS:
        votes = [found for machine in per_page.values() if (found := _field(machine, name)) is not None]
        if not votes:
            continue
        values = [value for value, _ in votes]
        if all(_agree(name, values[0], other) for other in values[1:]) and all(
            _agree(name, a, b) for i, a in enumerate(values) for b in values[i + 1 :]
        ):
            common[name] = max(votes, key=lambda vote: vote[1])[0]
    return common
