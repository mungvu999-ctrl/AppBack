"""Số tầng theo thứ tự tầng của dự án (HS-03): mỗi sàn một bản vẽ, người dùng đã xếp thứ tự.

Dọc thứ tự dự án số tầng tăng dần; lửng là sàn riêng không số. Sàn không phải lửng có vị trí `s` (0, 1, 2…).
Số đọc từ chữ `n` hợp thứ tự khi `n - s` không giảm dọc dãy ("TẦNG 2" rồi "TẦNG 4" vẫn hợp); giữ tập nặng
nhất (tổng độ tin) các số như vậy làm neo. Sàn còn lại đếm từ neo gần nhất phía dưới (không có thì phía trên;
không neo nào thì sàn đầu là tầng 1). Chữ mâu thuẫn thứ tự (số bị loại, hầm nằm trên trệt, tum/sân thượng nằm
dưới một tầng thường, cốt riêng lệch thứ tự quá 0,25 m): GIỮ thứ tự, độ tin tầng <= 0,65, trả `OrderConflict`.

Độ tin (luật hai nguồn): số từ chữ được một neo kề xác nhận đúng khoảng cách trong thứ tự (cùng `n - s`) là
hai nguồn độc lập cùng ý -> 0,95; số chỉ suy từ thứ tự là một nguồn -> 0,8 khi kẹp đúng giữa hai neo, 0,75 khi
đếm từ một phía, 0,6 khi khoảng trống có nhiều cách điền hoặc không có neo.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Final, Literal

from packages.domain.drawing_set.storeys import StoreyChoice, StoreyLabel

OrderReason = Literal["title", "level"]
_Anchor = tuple[int, int]  # (vị trí s, số tầng n) của một sàn giữ số đọc từ chữ

_ROOFISH: Final = frozenset({"tum", "attic", "roof_terrace"})  # sàn trên cùng: số theo thứ tự, không theo chữ
_TEXT_NUMBERED: Final = frozenset({"ground", "floor", "basement"})
_BOTH: Final = 0.95  # chữ và thứ tự cùng ý
_PINNED: Final = 0.8  # chỉ thứ tự, kẹp đúng giữa hai neo
_COUNTED: Final = 0.75  # chỉ thứ tự, đếm từ một phía
_LOOSE: Final = 0.6  # chỉ thứ tự, khoảng trống nhiều cách điền hoặc không có neo
_BELOW_GROUND: Final = 0.5  # đếm xuống dưới tầng 1 mà chữ không nói là hầm
_CONFLICT: Final = 0.65
_LEVEL_TOL_MM: Final = 250  # cốt lệch thứ tự không quá mức này thì bỏ qua (như `_COPY_TOL_MM` của `proposal`)
_MIN_WEIGHT: Final = 0.1  # số đọc được với độ tin 0 vẫn hơn không có gì
_SAME_STEP: Final = 0.05  # thưởng khi hai neo kề cách nhau đúng số sàn giữa chúng (phá hoà giữa các tập ngang nhau)
_EVIDENCE: Final = frozenset({"text", "level"})  # họ dấu hiệu đọc từ bản vẽ; họ "order" không tự xác nhận thứ tự


@dataclass(frozen=True)
class Slot:
    """Một sàn của dự án theo thứ tự: mã, tầng đọc từ chữ (`None`: không có chữ, vẫn chiếm chỗ), cốt riêng."""

    view_id: str
    choice: StoreyChoice | None
    level_mm: int | None = None


@dataclass(frozen=True)
class OrderConflict:
    """Chữ trên bản vẽ mâu thuẫn thứ tự dự án: do tên hình ("title") hay do cốt ("level"); số tầng chữ ghi."""

    reason: OrderReason
    text_number: int | None = None


def _text_number(choice: StoreyChoice | None) -> int | None:
    """Số tầng mà bản vẽ nói (tên hình hoặc cốt); tum/sân thượng mang số "tầng cao nhất + 1" suy ra và số chỉ
    do phép loại trừ/thứ tự hình đưa ra đều không phải số đọc được."""
    if choice is None or choice.label not in _TEXT_NUMBERED or not _EVIDENCE & set(choice.sources):
        return None
    return choice.number


def _kept(items: Sequence[tuple[int, int, float]]) -> set[int]:
    """Chỉ số các sàn giữ số đọc từ chữ: dãy con nặng nhất có `n - s` không giảm. `items` = (s, n, độ tin).

    Quy hoạch động O(m²) với m <= số sàn của dự án (lớp trên chặn 200).
    """
    best: list[float] = []
    back: list[int | None] = []
    for i, (s, n, weight) in enumerate(items):
        step = n - s
        prev = {j: best[j] + _SAME_STEP * (items[j][1] - items[j][0] == step) for j in range(i)}
        link = max((j for j in prev if items[j][1] - items[j][0] <= step), key=prev.__getitem__, default=None)
        best.append(max(weight, _MIN_WEIGHT) + (prev[link] if link is not None else 0.0))
        back.append(link)
    out: set[int] = set()
    at = max(range(len(best)), key=best.__getitem__, default=None)
    while at is not None:
        out.add(at)
        at = back[at]
    return out


def _anchors(slots: Sequence[Slot], pos: dict[str, int]) -> dict[str, _Anchor]:
    """{mã: (s, n)} của các sàn giữ số đọc từ chữ."""
    texts = [
        (pos[slot.view_id], n, slot.choice.confidence if slot.choice else 0.0, slot.view_id)
        for slot in slots
        if slot.view_id in pos and (n := _text_number(slot.choice)) is not None
    ]
    return {texts[i][3]: (texts[i][0], texts[i][1]) for i in _kept([t[:3] for t in texts])}


def _label(choice: StoreyChoice, number: int) -> StoreyLabel:
    """Nhãn theo số tầng; tum/áp mái/sân thượng giữ nhãn của chữ."""
    if choice.label is not None and choice.label in _ROOFISH:
        return choice.label
    return "basement" if number <= 0 else "ground" if number == 1 else "floor"


def _level_clashes(slots: Sequence[Slot]) -> set[str]:
    """Sàn có cốt riêng thấp hơn một sàn đứng trước hoặc cao hơn một sàn đứng sau quá 0,25 m."""
    marked = [(s.view_id, s.level_mm) for s in slots if s.level_mm is not None]
    out: set[str] = set()
    highest: int | None = None
    for view_id, level in marked:
        if highest is not None and highest > level + _LEVEL_TOL_MM:
            out.add(view_id)
        highest = level if highest is None else max(highest, level)
    lowest: int | None = None
    for view_id, level in reversed(marked):
        if lowest is not None and lowest < level - _LEVEL_TOL_MM:
            out.add(view_id)
        lowest = level if lowest is None else min(lowest, level)
    return out


def _filled(s: int, below: _Anchor | None, above: _Anchor | None) -> tuple[int, float]:
    """(số, độ tin) của sàn ở vị trí `s` không giữ số riêng; `below`/`above` = neo gần nhất hai phía."""
    if below is not None:
        number = below[1] + (s - below[0])
        if above is None:
            return number, _COUNTED
        return number, _PINNED if above[1] - below[1] == above[0] - below[0] else _LOOSE
    if above is not None:
        return above[1] - (above[0] - s), _COUNTED
    return s + 1, _LOOSE


def _by_order(
    choice: StoreyChoice, s: int, kept: _Anchor | None, anchors: Sequence[_Anchor], exact: set[_Anchor]
) -> tuple[StoreyChoice, OrderConflict | None]:
    """Tầng của một sàn không phải lửng ở vị trí `s`, và mâu thuẫn khi số của chữ bị thứ tự loại hoặc hầm nằm
    trên trệt. `kept` = neo của chính nó (khi số của chữ được giữ), `exact` = neo được neo kề xác nhận."""
    text = _text_number(choice)
    conflict: OrderConflict | None = None
    if kept is not None:
        number = kept[1]
        confidence = max(choice.confidence, _BOTH) if kept in exact and not choice.conflict else choice.confidence
    else:
        below = max((a for a in anchors if a[0] < s), default=None)
        above = min((a for a in anchors if a[0] > s), default=None)
        number, confidence = _filled(s, below, above)
        if text is not None:
            conflict = OrderConflict("title" if "text" in choice.sources else "level", text)
        elif number <= 0 and choice.label != "basement":
            confidence = _BELOW_GROUND
    if conflict is None and choice.label == "basement" and number >= 1:
        conflict = OrderConflict("title", text)
    sources = tuple(sorted({*choice.sources, "order"}))
    return replace(
        choice, label=_label(choice, number), number=number, confidence=confidence, sources=sources
    ), conflict


def follow_order(slots: Sequence[Slot]) -> tuple[dict[str, StoreyChoice], dict[str, OrderConflict]]:
    """({mã: tầng theo thứ tự dự án}, {mã: mâu thuẫn}) cho các sàn có chữ; `slots` theo thứ tự dự án.

    Lửng giữ nguyên (không số). Sàn không có chữ chỉ chiếm chỗ trong thứ tự, không có trong kết quả.
    """
    pos: dict[str, int] = {}
    for slot in slots:
        if slot.choice is None or slot.choice.label != "mezzanine":
            pos[slot.view_id] = len(pos)
    kept = _anchors(slots, pos)
    anchors = sorted(kept.values())
    exact = {x for a, b in pairwise(anchors) if b[1] - a[1] == b[0] - a[0] for x in (a, b)}
    plain = [pos[s.view_id] for s in slots if s.view_id in pos and s.choice and s.choice.label not in _ROOFISH]
    clashes = _level_clashes(slots)
    out: dict[str, StoreyChoice] = {}
    conflicts: dict[str, OrderConflict] = {}
    for slot in slots:
        choice, vid = slot.choice, slot.view_id
        if choice is None:
            continue
        found: OrderConflict | None = None
        if vid in pos:
            choice, found = _by_order(choice, pos[vid], kept.get(vid), anchors, exact)
            if found is None and choice.label in _ROOFISH and pos[vid] < max(plain, default=-1):
                found = OrderConflict("title")
        if found is None and vid in clashes:
            found = OrderConflict("level")
        if found is not None:
            conflicts[vid] = found
            choice = replace(choice, confidence=min(choice.confidence, _CONFLICT))
        out[vid] = choice
    return out, conflicts
