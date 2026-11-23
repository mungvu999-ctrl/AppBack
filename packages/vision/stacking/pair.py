"""Chồng một cặp tầng: đề xuất rồi kiểm — dịch (mm) đưa mặt bằng tầng trên B lên mặt bằng tầng dưới A (K-32d).

Chuyển từ nhân nghiên cứu `stacking2.py` (`stack_pair2`, `proposals`, `independent`, `confidence`, `_merge`, `_gap`).

1. Đề xuất: từng đầu mối của `cues.py` tự đề xuất (lưới một trục lấy trục kia từ tìm 1-D theo điểm kiểm), dịch 0, và
   các đỉnh tương quan tường/mực trong ±4 m.
2. Kiểm: điểm trùng khớp của `layers.verify`; mỗi ứng viên tinh chỉnh ±150 mm; ứng viên cách nhau ≤ 100 mm gộp lại.
3. Đáp án = ứng viên kiểm tốt nhất. Độ tin theo luật hai nguồn: ≥ 0,9 chỉ khi điểm tốt VÀ nhãn lưới hai trục hay cột
   cùng đề xuất trong 100 mm VÀ đối thủ (cách > 300 mm) kém rõ ràng; thiếu một → 0,7-0,9; điểm yếu hay hoà → < 0,7.
   Thang và dịch 0 không bao giờ xác nhận. Đầu mối độc lập dùng được (nhãn lưới, lưới một trục trên trục của nó, cột)
   cách đáp án > 150 mm là XUNG ĐỘT: liệt kê trong `conflict`, độ tin kẹp ≤ 0,65 (lỗi im lặng: cột xác nhận trong khi
   nhãn lưới lệch 1 m ở cả hai trục từng cho 0,965). Có đầu mối đủ hai trục mà không cái nào xác nhận: ≤ 0,8 khi điểm
   tốt và đối thủ kém rõ ràng, không thì < 0,7.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from packages.vision.stacking.cues import CUES, Prepared, prepare
from packages.vision.stacking.layers import Layer, corr_peaks, layer, refine, search_axis
from packages.vision.stacking.model import (
    Basis,
    Candidate,
    Conflict,
    FloorObservation,
    PartialShift,
    Shift,
    StackResult,
)

CONFIRM: Final = 100.0  # mm: đầu mối độc lập xác nhận một ứng viên khi cách nó không quá mức này
RIVAL: Final = 300.0  # mm: ứng viên nhì phải cách đáp án xa hơn mức này mới là đối thủ
CONFLICT: Final = 150.0  # mm: đầu mối độc lập (trên trục nó biết) cách đáp án xa hơn mức này là mâu thuẫn
CAP_CONFLICT: Final = 0.65
CAP_UNCONFIRMED: Final = 0.8
CAP_NO_CLEAR: Final = 0.69
GOOD: Final = 0.55  # điểm kiểm: tốt rõ (chỉnh trên bộ dev của nghiên cứu)
WEAK: Final = 0.35  # điểm kiểm: yếu
CLEAR: Final = 0.85  # đối thủ / nhất dưới mức này là kém rõ ràng
TIE: Final = 0.95  # trên mức này là hoà
_INDEPENDENT: Final = ("grid", "columns")  # đầu mối độc lập; thang và dịch 0 không bao giờ độc lập

_NO_ANSWER: Final = StackResult(shift_mm=None, confidence=0.0)


@dataclass(slots=True)
class _Cand:
    """Ứng viên đã tinh chỉnh: điểm kiểm, dịch, tên các đề xuất gộp vào nó."""

    score: float
    shift: Shift
    names: list[str]


def confidence(score: float, ratio: float, confirmed: bool) -> float:
    """Luật hai nguồn trên điểm kiểm, tỉ lệ đối thủ và việc có đầu mối độc lập xác nhận (chưa tính xung đột)."""
    if score < WEAK or ratio > TIE:
        return round(0.4 + 0.25 * min(1.0, score / WEAK) * (1 - max(0.0, ratio - TIE) * 5), 3)
    if score >= GOOD and ratio <= CLEAR and confirmed:
        return round(min(0.98, 0.9 + 0.1 * (score - GOOD)), 3)
    return round(0.7 + 0.15 * min(1.0, (score - WEAK) / (GOOD - WEAK)) * (1.2 - ratio), 3)


def _complete(r: PartialShift, la: Layer, lb: Layer) -> tuple[Shift, str] | None:
    """Dịch đủ hai trục: lưới một trục lấy trục kia từ tìm 1-D (hậu tố "(1 axis)"); không trục nào → None."""
    dx, dy = r
    if dx is not None and dy is not None:
        return (dx, dy), ""
    if dy is not None:
        return (search_axis(la, lb, dy, 0), dy), "(1 axis)"
    if dx is not None:
        return (dx, search_axis(la, lb, dx, 1)), "(1 axis)"
    return None


def proposals(cues: Mapping[str, PartialShift | None], la: Layer, lb: Layer) -> list[tuple[str, Shift]]:
    """[(tên, dịch)] từ dịch 0, từng đầu mối đã lên tiếng và các đỉnh tương quan."""
    out: list[tuple[str, Shift]] = [("zero", (0.0, 0.0))]
    for name, r in cues.items():
        full = None if r is None else _complete(r, la, lb)
        if full is not None:
            out.append((name + full[1], full[0]))
    out += [(f"corr{i + 1}", p) for i, p in enumerate(corr_peaks(la, lb))]
    return out


def independent(cues: Mapping[str, PartialShift | None]) -> list[tuple[str, PartialShift]]:
    """Đầu mối độc lập đã lên tiếng: lưới ("grid", hay "grid(1 axis)" khi thiếu một trục) và cột."""
    out: list[tuple[str, PartialShift]] = []
    for name in _INDEPENDENT:
        r = cues.get(name)
        if r is not None:
            out.append(("grid(1 axis)" if name == "grid" and None in r else name, r))
    return out


def _gap(sh: PartialShift, t: Shift) -> float:
    """Lệch lớn nhất (mm) trên từng trục giữa đầu mối `sh` (None = trục chưa biết) và đáp án t; 0 nếu chẳng biết gì."""
    return max([abs(v - u) for v, u in zip(sh, t, strict=True) if v is not None] or [0.0])


def _merge(cands: list[tuple[float, Shift, list[str]]]) -> list[_Cand]:
    """Ứng viên đã tinh chỉnh cách ứng viên tốt hơn ≤ CONFIRM thì gộp vào nó (nối tên); kết quả giảm dần theo điểm."""
    out: list[_Cand] = []
    for score, shift, names in sorted(cands, key=lambda c: -c[0]):
        hit = next((o for o in out if math.dist(o.shift, shift) <= CONFIRM), None)
        if hit:
            hit.names.extend(n for n in names if n not in hit.names)
        else:
            out.append(_Cand(score, shift, list(names)))
    return out


def _pick_layers(
    a: FloorObservation, b: FloorObservation, mm_a: float, mm_b: float
) -> tuple[Layer, Layer, Basis] | None:
    """Lớp kiểm của cả hai tầng: từ mặt nạ tường; thiếu/thưa ở một bên thì CẢ HAI lùi về mực. None khi vẫn thiếu."""
    la, lb = layer(a, mm_a), layer(b, mm_b)
    if la is not None and lb is not None:
        return la, lb, "mask"
    la, lb = layer(a, mm_a, use_ink=True), layer(b, mm_b, use_ink=True)
    return (la, lb, "ink") if la is not None and lb is not None else None


def _full(sh: PartialShift) -> Shift | None:
    """Dịch đủ hai trục, hoặc None khi một trục chưa biết."""
    return None if sh[0] is None or sh[1] is None else (sh[0], sh[1])


def _audit(ind: Sequence[tuple[str, PartialShift]], best: Shift) -> tuple[list[str], list[Conflict]]:
    """Đầu mối độc lập đủ hai trục cách đáp án ≤ CONFIRM là xác nhận; đầu mối nào cách > CONFLICT là xung đột."""
    confirmed = [n for n, sh in ind if (f := _full(sh)) is not None and math.dist(f, best) <= CONFIRM]
    conflict = [
        Conflict(n, (None if sh[0] is None else round(sh[0]), None if sh[1] is None else round(sh[1])))
        for n, sh in ind
        if _gap(sh, best) > CONFLICT
    ]
    return confirmed, conflict


def _final_confidence(
    score: float, ratio: float, ind: Sequence[tuple[str, PartialShift]], confirmed: list[str], conflict: list[Conflict]
) -> float:
    """Độ tin sau luật hai nguồn, kẹp theo xung đột / có đầu mối đủ hai trục mà không cái nào xác nhận."""
    conf = confidence(score, ratio, bool(confirmed))
    if conflict:
        return min(conf, CAP_CONFLICT)
    if any(_full(sh) is not None for _, sh in ind) and not confirmed:
        return min(conf, CAP_UNCONFIRMED if score >= GOOD and ratio <= CLEAR else CAP_NO_CLEAR)
    return conf


def stack_pair(obs_a: FloorObservation, obs_b: FloorObservation) -> StackResult:
    """Dịch (mm) đưa B lên A: đề xuất, kiểm, đáp án kèm độ tin hai nguồn (xem docstring module).

    Không trả lời (`shift_mm` None, độ tin 0) khi thiếu tỉ lệ ở một tầng hoặc không dựng được lớp kiểm cho cả hai.
    Hai tầng nên đứng thẳng và cùng chiều; quay 90° chưa xử lý (nợ NO-520).
    """
    mm_a, mm_b = obs_a.mm_per_px, obs_b.mm_per_px
    if not mm_a or not mm_b:
        return _NO_ANSWER
    picked = _pick_layers(obs_a, obs_b, mm_a, mm_b)
    if picked is None:
        return _NO_ANSWER
    la, lb, basis = picked
    if basis == "ink":  # lớp mực đã thay mặt nạ: đầu mối tường/cột cũng không dùng mặt nạ nữa
        obs_a, obs_b = replace(obs_a, wall_mask=None, col_mask=None), replace(obs_b, wall_mask=None, col_mask=None)
    pa, pb = prepare(obs_a, mm_a), prepare(obs_b, mm_b)
    return _answer(pa, pb, la, lb, basis)


def _answer(pa: Prepared, pb: Prepared, la: Layer, lb: Layer, basis: Basis) -> StackResult:
    """Từ hai quan sát đã chuẩn bị và hai lớp kiểm: đề xuất → tinh chỉnh → gộp → đáp án, độ tin, xác nhận, xung đột."""
    cues = {name: fn(pa, pb) for name, fn in CUES}
    props = proposals(cues, la, lb)
    cands = _merge([(*refine(la, lb, sh), [n]) for n, sh in props])
    best = cands[0]
    rival = next((c for c in cands[1:] if math.dist(c.shift, best.shift) > RIVAL), None)
    ratio = rival.score / best.score if rival and best.score > 0 else 0.0
    ind = independent(cues)
    confirmed, conflict = _audit(ind, best.shift)
    return StackResult(
        shift_mm=(round(best.shift[0]), round(best.shift[1])),
        confidence=float(_final_confidence(best.score, ratio, ind, confirmed, conflict)),
        basis=basis,
        score=round(best.score, 3),
        ratio=round(ratio, 3),
        cues={n: (round(sh[0]), round(sh[1])) for n, sh in props},
        candidates=tuple(
            Candidate(tuple(c.names), (round(c.shift[0]), round(c.shift[1])), round(c.score, 3)) for c in cands
        ),
        confirmed_by=tuple(confirmed),
        conflict=tuple(conflict),
    )
