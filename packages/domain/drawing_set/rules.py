"""Luật ghép của bộ hồ sơ viết dạng dữ liệu (NHA-05a, thiết kế §2.E và §3): bảng `RULES` và mỗi mã một hàm kiểm thuần.

Nhóm T (tầng) kiểm lúc đề xuất, khi mới có chữ và cốt. Hàm kiểm nhận số liệu trần, trả `RuleHit` (hoặc không gì); FE
viết câu tiếng Việt theo mã. Mức `error` chặn xác nhận; `warning` chặn cho tới khi người duyệt chọn nguồn hoặc bấm
"Đã xem"; `hint` chỉ hiển thị. Dung sai là số đề xuất của thiết kế, chưa hiệu chỉnh bằng số đo.

Nhóm M (mái, NHA-05b) chỉ gồm các luật kiểm lúc đề xuất; các luật M kiểm lúc ráp và lúc đọc thuộc NHA-07a/08.
"""

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from packages.domain.spatial.model import DomainModel

type RuleLevel = Literal["error", "warning", "hint"]

HEIGHT_OK_MM: Final = (2400, 4600)  # chiều cao một tầng "còn hợp lý"; cùng dải với `heights._LOOSE`
_ASSUMED_SOURCES: Final = frozenset({"default", "convention"})
FLAT_MAX_DEG: Final = math.degrees(math.atan(0.05))  # dưới 5 % là dốc thoát nước của mái bằng, không phải mái dốc
_MAX_PITCH_DEG: Final = 85.0  # từ 85° là tường; cùng trần với `slope._MAX_DEG`
# Độ dốc tối thiểu theo vật liệu (cao/ngang), IRC chương 9; tôn chưa có ngưỡng xác minh được nên không kiểm.
_MIN_SLOPE: Final[Mapping[str, float]] = {"tile": 2.5 / 12, "shingle": 2 / 12}
TUM_LABELS: Final = frozenset({"tum", "attic"})


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
    values: tuple[int | float | str, ...]  # mm giữ nguyên số nguyên; số thực chỉ dùng cho độ dốc (độ)


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
        Rule("M-02", "warning", 100),
        Rule("M-03", "warning", 3),  # độ
        Rule("M-05", "warning"),
        Rule("M-06", "warning"),
        Rule("M-07", "hint"),
        Rule("M-09", "hint"),
        Rule("M-17", "error"),
        Rule("M-18", "warning"),
        Rule("M-19", "warning"),
    )
}


def _hit(
    code: str, refs: Sequence[str], values: Sequence[int | float | str], level: RuleLevel | None = None
) -> RuleHit:
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
    """T-07: giá trị lấy từ mặc định/quy ước (cốt, chiều cao tầng, dạng mái, độ dốc) phải được người duyệt xem."""
    if source is None or source not in _ASSUMED_SOURCES:
        return None
    return _hit("T-07", (ref,), (field, source))


def m02_eave(ref: str, eave_mm: int, wall_top_mm: int) -> RuleHit | None:
    """M-02: cốt diềm mái = đỉnh tường của tầng nó che (± 100 mm). Vi phạm kèm cốt diềm rồi đỉnh tường."""
    tolerance = RULES["M-02"].tolerance or 0
    return None if abs(eave_mm - wall_top_mm) <= tolerance else _hit("M-02", (ref,), (eave_mm, wall_top_mm))


def m03_pitch(ref: str, degs: Sequence[float]) -> RuleHit | None:
    """M-03: độ dốc từ các nguồn (mặt bằng mái, mặt cắt) cách nhau không quá 3°."""
    tolerance = RULES["M-03"].tolerance or 0
    spread = round(max(degs, default=0) - min(degs, default=0), 1)  # 4,4 trừ 1,4 ra 3,0000000000000004
    return None if spread <= tolerance else _hit("M-03", (ref,), degs)


def m05_form(ref: str, forms: Sequence[str]) -> RuleHit | None:
    """M-05: các nguồn nói cùng một dạng mái."""
    return None if len(set(forms)) <= 1 else _hit("M-05", (ref,), forms)


def m06_rooftop(refs: Sequence[str], label: str | None, *, sloped: bool = False) -> RuleHit | None:
    """M-06: tầng cao nhất là tum/áp mái thì hỏi về mái của nó và tấm phủ tầng dưới. `refs`: tum, rồi tầng dưới.

    `sloped`: chữ ghi mái dốc trên nhà có tum nên không mái nào được đề xuất; vi phạm kèm thêm chữ `sloped`.
    """
    if label is None or label not in TUM_LABELS:
        return None
    return _hit("M-06", refs, (label, "sloped") if sloped else (label,))


def m07_flat_drain(ref: str, form: str, *, drains: bool) -> RuleHit | None:
    """M-07: mái bằng nên có parapet hoặc thoát nước; `drains` = bộ có chữ sê nô, parapet, drain hay scupper."""
    return None if form != "flat" or drains else _hit("M-07", (ref,), ())


def m09_min_pitch(ref: str, pitch_deg: float, material: str | None) -> RuleHit | None:
    """M-09: độ dốc không thấp hơn ngưỡng tối thiểu của vật liệu. Vi phạm kèm độ dốc, ngưỡng (độ) và vật liệu."""
    minimum = _MIN_SLOPE.get(material or "")
    if material is None or minimum is None or math.tan(math.radians(pitch_deg)) >= minimum:
        return None
    return _hit("M-09", (ref,), (pitch_deg, round(math.degrees(math.atan(minimum)), 1), material))


def m17_consistent(ref: str, form: str, pitch_deg: float, eave_mm: int | None, ridge_mm: int | None) -> RuleHit | None:
    """M-17: thông số mái tự nhất quán. Vi phạm kèm tên từng điều sai.

    `ridgeBelowEave`: mái dốc mà cốt đỉnh không cao hơn cốt diềm. `pitchTooLow`: mái dốc mà độ dốc dưới 5 %.
    `pitchTooHigh`: độ dốc từ 85°.
    """
    sloped = form != "flat"
    checks = (
        ("ridgeBelowEave", sloped and eave_mm is not None and ridge_mm is not None and ridge_mm <= eave_mm),
        ("pitchTooLow", sloped and pitch_deg < FLAT_MAX_DEG),
        ("pitchTooHigh", pitch_deg >= _MAX_PITCH_DEG),
    )
    failed = [name for name, bad in checks if bad]
    return _hit("M-17", (ref,), failed) if failed else None


def m18_roof_plan(roof_views: Sequence[str], form_source: str | None) -> RuleHit | None:
    """M-18: bộ có hình mặt bằng mái thì mái không được vắng (`form_source` rỗng) hay mang dạng giả định."""
    if not roof_views or (form_source is not None and form_source not in _ASSUMED_SOURCES):
        return None
    return _hit("M-18", roof_views, (form_source or "none",))


def m19_ambiguous(ref: str, text: str | None) -> RuleHit | None:
    """M-19: độ dốc ghi dạng `a:b` nhập nhằng phải được người duyệt chọn cách đọc. `text`: chữ gốc, rỗng = ghi rõ."""
    return None if text is None else _hit("M-19", (ref,), (text,))
