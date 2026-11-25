"""Luật ghép của bộ hồ sơ viết dạng dữ liệu (NHA-05a, thiết kế §2.E và §3): bảng `RULES` và mỗi mã một hàm kiểm thuần.

Nhóm T (tầng) kiểm lúc đề xuất, khi mới có chữ và cốt. Hàm kiểm nhận số liệu trần, trả `RuleHit` (hoặc không gì); FE
viết câu tiếng Việt theo mã. Mức `error` chặn xác nhận; `warning` chặn cho tới khi người duyệt chọn nguồn hoặc bấm
"Đã xem". Dung sai là số đề xuất của thiết kế, chưa hiệu chỉnh bằng số đo. Nhóm M (mái) thêm ở NHA-05b.
"""

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from packages.domain.spatial.model import DomainModel

type RuleLevel = Literal["error", "warning", "hint"]

HEIGHT_OK_MM: Final = (2400, 4600)  # chiều cao một tầng "còn hợp lý"; cùng dải với `heights._LOOSE`
_ASSUMED_SOURCES: Final = frozenset({"default", "convention"})


@dataclass(frozen=True, slots=True)
class Rule:
    """Một luật ghép: mã, mức khi vi phạm, dung sai (mm) nếu luật có."""

    code: str
    level: RuleLevel
    tolerance: int | None = None


class RuleHit(DomainModel):
    """Một lần vi phạm: mã luật, mức, các hình liên quan và số liệu để FE viết câu."""

    code: str
    level: RuleLevel
    floor_refs: tuple[str, ...]
    values: tuple[int | str, ...]


RULES: Final[Mapping[str, Rule]] = {
    rule.code: rule
    for rule in (
        Rule("T-01", "error", 50),  # chồng lấn là lỗi; hở cùng dung sai chỉ là cảnh báo
        Rule("T-02", "warning"),
        Rule("T-03", "warning"),
        Rule("T-04", "error"),
        Rule("T-05", "warning", 250),
        Rule("T-06", "warning"),
        Rule("T-07", "warning"),
    )
}


def _hit(code: str, refs: Sequence[str], values: Sequence[int | str], level: RuleLevel | None = None) -> RuleHit:
    """`RuleHit` của một mã, mức lấy từ bảng trừ khi luật có hai mức."""
    return RuleHit(code=code, level=level or RULES[code].level, floor_refs=tuple(refs), values=tuple(values))


def t01_stack(floors: Sequence[tuple[str, int, int]]) -> list[RuleHit]:
    """T-01: cốt tầng trên = cốt tầng dưới + chiều cao tầng dưới. `floors`: `(hình, cốt, chiều cao)` mm, từ dưới lên.

    Mỗi cặp liền nhau lệch quá dung sai sinh một vi phạm kèm độ lệch (âm = tầng trên thấp hơn đỉnh tầng dưới: lỗi;
    dương = hở: cảnh báo). Không tự sửa số nào.
    """
    tolerance = RULES["T-01"].tolerance or 0
    hits: list[RuleHit] = []
    for (lower, base, height), (upper, level, _) in itertools.pairwise(floors):
        gap = level - (base + height)
        if abs(gap) > tolerance:
            hits.append(_hit("T-01", (lower, upper), (gap,), "error" if gap < 0 else "warning"))
    return hits


def t02_storey_count(plans: int, sections: int | None) -> RuleHit | None:
    """T-02: số tầng có mặt bằng khớp số tầng đọc trên thang cốt của mặt cắt (`None` = mặt cắt không nói gì)."""
    return None if sections is None or sections == plans else _hit("T-02", (), (plans, sections))


def t03_height(ref: str, height_mm: int) -> RuleHit | None:
    """T-03: chiều cao tầng nằm trong 2,4-4,6 m."""
    low, high = HEIGHT_OK_MM
    return None if low <= height_mm <= high else _hit("T-03", (ref,), (height_mm,))


def t04_one_plan_per_storey(targets: Mapping[str, str]) -> list[RuleHit]:
    """T-04: mỗi tầng một mặt bằng đại diện. `targets`: hình → đích tầng người duyệt gán; hai hình một đích là lỗi."""
    views: dict[str, list[str]] = {}
    for view, target in targets.items():
        views.setdefault(target, []).append(view)
    return [_hit("T-04", refs, (target,)) for target, refs in views.items() if len(refs) > 1]


def t05_sources(ref: str, values_mm: Sequence[int]) -> RuleHit | None:
    """T-05: cốt của cùng một tầng từ các nguồn cách nhau không quá 250 mm (không có giá trị nào thì không vi phạm)."""
    tolerance = RULES["T-05"].tolerance or 0
    spread = max(values_mm, default=0) - min(values_mm, default=0)
    return None if spread <= tolerance else _hit("T-05", (ref,), values_mm)


def t06_units(metric: int, imperial: int) -> RuleHit | None:
    """T-06: hệ đơn vị xác định và nhất quán. Nhận số chữ đọc được của mỗi hệ; lẫn hai hệ hoặc không có gì thì hỏi."""
    return None if (metric > 0) != (imperial > 0) else _hit("T-06", (), (metric, imperial))


def t07_assumed(ref: str, field: str, source: str | None) -> RuleHit | None:
    """T-07: cốt hoặc chiều cao lấy từ mặc định/quy ước phải được người duyệt xem."""
    if source is None or source not in _ASSUMED_SOURCES:
        return None
    return _hit("T-07", (ref,), (field, source))
