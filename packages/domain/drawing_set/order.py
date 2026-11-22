"""Số tầng theo thứ tự tầng của dự án (HS-03): mỗi sàn một bản vẽ, người dùng đã xếp thứ tự.

Dọc thứ tự dự án số tầng tăng dần; lửng là sàn riêng không số. Sàn không phải lửng có vị trí `s` (0, 1, 2…).
Số đọc từ chữ `n` hợp thứ tự khi `n - s` không giảm dọc dãy ("TẦNG 2" rồi "TẦNG 4" vẫn hợp); giữ tập nặng
nhất (tổng độ tin) các số như vậy làm neo. Sàn còn lại đếm từ neo gần nhất phía dưới (không có thì phía trên;
không neo nào thì sàn đầu là tầng 1). Chữ mâu thuẫn thứ tự (số bị loại, hầm nằm trên trệt, tum/sân thượng nằm
dưới một tầng thường, cốt riêng lệch thứ tự quá 0,25 m, số không thể đúng — lớn hơn số sàn + 1 hay nhảy so với
neo kề quá số sàn giữa chúng + 1): GIỮ thứ tự, độ tin tầng <= 0,65, trả `OrderConflict`.

Độ tin (luật hai nguồn): số từ chữ được một neo kề xác nhận đúng khoảng cách trong thứ tự (cùng `n - s`) là
hai nguồn độc lập cùng ý -> 0,95 (chỉ khi số đọc từ tên hình; số chỉ suy từ cốt tối đa 0,8); số chỉ suy từ thứ
tự là một nguồn -> 0,8 khi kẹp đúng giữa hai neo, 0,75 khi đếm từ một phía, 0,6 khi khoảng trống có nhiều cách điền
hoặc không có neo.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from statistics import median
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
_LEVEL_ONLY: Final = 0.8  # số chỉ suy từ cốt, không có tên hình: một nguồn
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


def _implausible_titles(kept: dict[str, _Anchor], floor_count: int) -> set[str]:
    """Mã các neo có số tầng không thể đúng với dự án: số > số sàn + 1, hoặc bước nhảy so với neo kề lớn hơn số
    sàn giữa chúng + 1 (bỏ một số như "TẦNG 2" rồi "TẦNG 4" vẫn hợp; lửng đã không chiếm vị trí).

    Vì sao: tên hình và thứ tự cùng đến từ MỘT lần OCR, nên một chữ số đọc sai ("TẦNG 8" ngay trên "TẦNG 1",
    không có cốt phản bác) vẫn qua `_kept` và các sàn sau đếm theo nó im lặng (K-30b). Trong cặp nhảy bất thường,
    neo có `n - s` xa trung vị hơn bị loại; ngang nhau thì loại cả hai (hỏi thừa còn hơn sai im lặng). Không bắt
    được số sai mà vẫn hợp lý (2↔3 đúng chỗ trống) — giới hạn đã biết, đường nâng cấp là mã câu hỏi riêng (K-31).
    """
    out = {vid for vid, (_, n) in kept.items() if n > floor_count + 1}
    rest = sorted((a, vid) for vid, a in kept.items() if vid not in out)
    if not rest:
        return out
    mid = median(a[1] - a[0] for a, _ in rest)
    for (a, low), (b, high) in pairwise(rest):
        if b[1] - a[1] <= b[0] - a[0] + 1:
            continue
        off_low, off_high = abs(a[1] - a[0] - mid), abs(b[1] - b[0] - mid)
        out |= {low} if off_low > off_high else {high} if off_high > off_low else {low, high}
    return out


def _label(choice: StoreyChoice, number: int) -> StoreyLabel:
    """Nhãn theo số tầng; tum/áp mái/sân thượng giữ nhãn của chữ."""
    if choice.label is not None and choice.label in _ROOFISH:
        return choice.label
    return "basement" if number <= 0 else "ground" if number == 1 else "floor"


def _chain_lengths(levels: Sequence[int]) -> list[int]:
    """Độ dài dãy con dài nhất kết thúc tại mỗi cốt, hai cốt kề hợp nhau khi cốt sau không thấp hơn quá 0,25 m."""
    out: list[int] = []
    for j, level in enumerate(levels):
        out.append(1 + max((out[i] for i in range(j) if level >= levels[i] - _LEVEL_TOL_MM), default=0))
    return out


def _level_clashes(slots: Sequence[Slot]) -> set[str]:
    """Sàn có cốt riêng không chắc nằm đúng thứ tự: không thuộc MỌI dãy con dài nhất các cốt hợp thứ tự.

    Một cốt đọc sai chỉ gắn cờ chính nó (`±0, +3.3, +6.6, +0.6` -> chỉ `+0.6`), không kéo theo các sàn đứng giữa.
    Hai cốt ngược nhau mà không có gì phân xử (`+3.3, ±0`) thì gắn cờ cả hai. Phần tử thứ `k` của một dãy dài nhất
    có độ dài-kết-thúc đúng bằng `k`, nên sàn nằm trong mọi dãy dài nhất khi nó là sàn duy nhất ở mức `k` đó.
    """
    marked = [(s.view_id, s.level_mm) for s in slots if s.level_mm is not None]
    levels = [level for _, level in marked]
    ends = _chain_lengths(levels)
    starts = list(reversed(_chain_lengths([-level for level in reversed(levels)])))  # dài nhất bắt đầu tại mỗi cốt
    longest = max(ends, default=0)
    on_longest = [ends[i] + starts[i] - 1 == longest for i in range(len(marked))]
    per_rank: dict[int, int] = {}
    for i, ok in enumerate(on_longest):
        if ok:
            per_rank[ends[i]] = per_rank.get(ends[i], 0) + 1
    return {vid for i, (vid, _) in enumerate(marked) if not on_longest[i] or per_rank[ends[i]] > 1}


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


def _kept_confidence(choice: StoreyChoice, confirmed: bool) -> float:
    """Độ tin của sàn giữ số đọc được. 0,95 chỉ khi số đến từ TÊN HÌNH và một neo kề xác nhận đúng khoảng cách:
    số chỉ suy từ cốt (họ "level") tối đa 0,8, vì khi tên hình không đọc được, các neo cốt có thể cùng lệch một tầng
    mà vẫn xác nhận lẫn nhau (bộ DN-244 trên f14, 22/11: "LẦU 3" ra tầng 3 ở 0,95)."""
    if "text" not in choice.sources:
        return min(choice.confidence, _LEVEL_ONLY)
    return max(choice.confidence, _BOTH) if confirmed and not choice.conflict else choice.confidence


def _by_order(
    choice: StoreyChoice, s: int, kept: _Anchor | None, anchors: Sequence[_Anchor], exact: set[_Anchor]
) -> tuple[StoreyChoice, OrderConflict | None]:
    """Tầng của một sàn không phải lửng ở vị trí `s`, và mâu thuẫn khi số của chữ bị thứ tự loại hoặc hầm nằm
    trên trệt. `kept` = neo của chính nó (khi số của chữ được giữ), `exact` = neo được neo kề xác nhận."""
    text = _text_number(choice)
    conflict: OrderConflict | None = None
    if kept is not None:
        number = kept[1]
        confidence = _kept_confidence(choice, kept in exact)
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
    for vid in _implausible_titles(kept, len(pos)):
        del kept[vid]  # sàn đó điền theo thứ tự và hỏi `OrderConflict` như số bị `_kept` loại
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
