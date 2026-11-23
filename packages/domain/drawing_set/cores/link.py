"""Nối ô thang giữa các tầng thành lõi và chấm độ chắc (K-32e, từ `kernel/cores.py`: `stair_tracks`, `_core`).

Hai nguồn độc lập: bộ dò (một tầng) và sự lặp lại (cùng chỗ ở tầng kế sau dịch chồng tầng: giao ≥ `OVL` của hộp
nhỏ, hoặc tâm cách ≤ một bề rộng thang). Ô lặp lại trên ≥ 2 tầng là lõi; thiếu đúng một tầng thì cầu qua mặt bằng
đó (khe) bằng dịch cộng dồn. Độ chắc: không khe và mọi dịch ≥ 0,7 → 0,92 (2 tầng)/0,95 (≥ 3); khe hoặc dịch yếu
→ 0,8; cả hai → 0,7; hướng bậc/bước khác nhau trong chuỗi → ≤ 0,8; lõi chỉ gồm quạt cong ≤ 0,8. ≥ 0,9 còn cần
bằng chứng riêng trên ≥ 2 tầng quan sát (chữ thang/số bậc), hoặc lớp mô hình hỗ trợ trên ≥ 2 tầng ("confirm").
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from packages.domain.drawing_set.cores.extend import extend_chain, floor_confidence
from packages.domain.drawing_set.cores.geometry import (
    centre,
    dist,
    move,
    overlap,
    shift_confidence,
    stair_width,
    to_lowest,
)
from packages.domain.drawing_set.cores.types import (
    Box,
    CorePlan,
    Point,
    StackShift,
    StairCore,
    UnlinkedWell,
    Well,
    validate_plans,
)

OVL: Final = 0.5  # giao / hộp nhỏ để coi là trùng
SHIFT_OK: Final = 0.7  # độ chắc dịch cần cho lõi ≥ 0,9
ANG_TOL: Final = 10.0  # độ, hướng bậc theo mod 180
PITCH_TOL: Final = 0.2  # sai khác tương đối của bước bậc
EVID_CAP: Final = 0.85  # trần khi không đủ bằng chứng riêng trên 2 tầng quan sát
CONFIRM_CAP: Final = 0.7  # hai bộ dò bất đồng: không tầng nào có mô hình hỗ trợ và không có số bậc
CURVED_CAP: Final = 0.8  # lõi chỉ gồm quạt cong chưa hiệu chỉnh cho ≥ 0,9

Chain = list[tuple[int, int]]


def _pair(wa: Sequence[Box], wb: Sequence[Box], t: Point) -> list[tuple[int, int]]:
    """Cặp một-một (i, j) giữa ô tầng a và ô tầng b dịch `t`, tốt nhất (giao lớn, rồi gần) trước."""
    cand: list[tuple[float, float, int, int]] = []
    for i, a in enumerate(wa):
        for j, b in enumerate(wb):
            bm = move(b, t)
            o = overlap(a, bm)
            d = dist(centre(a), centre(bm))
            if o >= OVL or d <= stair_width(a, bm):
                cand.append((-o, d, i, j))
    used_i: set[int] = set()
    used_j: set[int] = set()
    out: list[tuple[int, int]] = []
    for _, _, i, j in sorted(cand):
        if i not in used_i and j not in used_j:
            used_i.add(i)
            used_j.add(j)
            out.append((i, j))
    return out


def _links(plans: Sequence[CorePlan], shifts: Sequence[StackShift | None]) -> dict[tuple[int, int], tuple[int, int]]:
    """Liên kết (tầng k, ô i) → (tầng k', ô j): nối trực tiếp trước, rồi cầu qua một mặt bằng thiếu ô."""
    boxes = [[w.box for w in p.wells] for p in plans]
    nxt: dict[tuple[int, int], tuple[int, int]] = {}
    for step in (1, 2):
        for k in range(len(plans) - step):
            t = to_lowest(shifts, k, k + step)
            if t is None:
                continue
            has_in = set(nxt.values())
            fa = [i for i in range(len(boxes[k])) if (k, i) not in nxt]
            fb = [j for j in range(len(boxes[k + step])) if (k + step, j) not in has_in]
            for i, j in _pair([boxes[k][i] for i in fa], [boxes[k + step][j] for j in fb], t):
                nxt[(k, fa[i])] = (k + step, fb[j])
    return nxt


def _similar(a: Well, b: Well) -> bool:
    """Cùng hướng bậc (mod 180) và bước bậc lệch ≤ `PITCH_TOL`; giá trị chưa biết (None) coi là giống."""
    if a.angle_deg is None or b.angle_deg is None or a.pitch_mm is None or b.pitch_mm is None:
        return True
    d = abs(a.angle_deg - b.angle_deg) % 180
    return min(d, 180 - d) <= ANG_TOL and abs(a.pitch_mm - b.pitch_mm) <= PITCH_TOL * max(a.pitch_mm, b.pitch_mm)


@dataclass(frozen=True)
class _Score:
    """Độ chắc của lõi và những gì tầng mở rộng/nguồn cần biết."""

    confidence: float
    sources: list[str]
    evidenced: set[int]
    shift_conf: float
    shape_ok: bool


def _evidence_rule(conf: float, wells: Sequence[Well], floors: Sequence[int]) -> tuple[float, set[str], set[int]]:
    """Áp luật bằng chứng: có lớp mô hình ("confirm") hoặc chỉ chữ. Trả (độ chắc, bằng chứng, tầng có chứng cứ)."""
    ev: set[str] = set().union(*(w.evidence for w in wells))
    ev_floors = {f for f, w in zip(floors, wells, strict=True) if w.evidence}
    if any(w.yolo_ok is not None for w in wells):
        sup = {f for f, w in zip(floors, wells, strict=True) if w.yolo_ok}
        if len(sup) >= 2:
            ev_floors |= sup
            ev.add("yolo_confirm")
        elif len(ev_floors) < 2:
            conf = min(conf, EVID_CAP)
        if not sup and "tread_numbers" not in ev:
            conf = min(conf, CONFIRM_CAP)
            ev.add("yolo_disagrees")
    elif len(ev_floors) < 2:
        conf = min(conf, EVID_CAP)
    return conf, ev, ev_floors


def _score(plans: Sequence[CorePlan], shifts: Sequence[StackShift | None], chain: Chain) -> _Score:
    """Chấm một chuỗi: khe, dịch yếu, hình dạng, quạt cong, rồi bằng chứng (xem docstring module)."""
    k0, k1 = chain[0][0], chain[-1][0]
    floors = [plans[k].floor_no for k, _ in chain]
    gap = plans[k1].floor_no - plans[k0].floor_no + 1 != len(chain)  # mặt bằng bị cầu qua hoặc tầng không có mặt bằng
    sc = shift_confidence(shifts, k0, k1)
    weak = sc < SHIFT_OK
    wells = [plans[k].wells[i] for k, i in chain]
    shape = all(_similar(a, b) for a, b in pairwise(wells))
    conf = (0.95 if len(chain) >= 3 else 0.92) if not (gap or weak) else (0.7 if gap and weak else 0.8)
    if not shape:
        conf = min(conf, 0.8)
    if {w.kind for w in wells} == {"curved"}:
        conf = min(conf, CURVED_CAP)
    conf, ev, ev_floors = _evidence_rule(conf, wells, floors)
    flags = [
        (gap, "gap"),
        (weak, "weak_shift"),
        (not shape, "shape_differs"),
        (any(w.kind == "curved" for w in wells), "curved"),
    ]
    sources = ["image_stairs", "persistence"] + [name for on, name in flags if on]
    sources += sorted(ev) if ev else ["no_stair_evidence"]
    return _Score(conf, sources, ev_floors, sc, shape)


def _core(plans: Sequence[CorePlan], shifts: Sequence[StackShift | None], chain: Chain) -> StairCore:
    """Dựng lõi từ chuỗi: chấm điểm, tâm trung bình về khung tầng quan sát thấp nhất, rồi mở rộng tầng."""
    score = _score(plans, shifts, chain)
    k0 = chain[0][0]
    pts: list[Point] = []
    for k, i in chain:
        t = to_lowest(shifts, k0, k) or (0.0, 0.0)  # đã nối được thì dịch luôn có; `or` chỉ để thoả kiểu
        c = centre(plans[k].wells[i].box)
        pts.append((c[0] + t[0], c[1] + t[1]))
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    ext, observed = extend_chain(plans, shifts, chain)
    sources = score.sources + (["assumed"] if ext.assumed else [])
    return StairCore(
        floors=tuple(range(ext.low, ext.high + 1)),
        observed_floors=tuple(observed),
        assumed_floors=ext.assumed,
        centre_mm=(round(cx + ext.centre_shift[0]), round(cy + ext.centre_shift[1])),
        confidence=score.confidence,
        floor_confidence=floor_confidence(ext, observed, score.confidence, score.evidenced),
        sources=tuple(sources),
        shift_conf=round(score.shift_conf, 3),
        shape_ok=score.shape_ok,
        members=tuple((plans[k].view_id, i) for k, i in chain),
        assumed_boxes=ext.boxes,
    )


def stair_tracks(
    plans: Sequence[CorePlan], shifts: Sequence[StackShift | None]
) -> tuple[list[StairCore], list[UnlinkedWell]]:
    """(lõi, ô lẻ) từ các ô thang của mặt bằng đã sắp theo số tầng tăng dần; `shifts[k]` nối plans[k+1] về plans[k].

    Ném ValueError nếu mặt bằng không tăng dần hoặc số dịch sai (fail-closed). Ô chỉ thấy một tầng → ô lẻ 0,3.
    """
    validate_plans(plans, shifts)
    nxt = _links(plans, shifts)
    has_in = set(nxt.values())
    cores: list[StairCore] = []
    unlinked: list[UnlinkedWell] = []
    for k, plan in enumerate(plans):
        for i, well in enumerate(plan.wells):
            if (k, i) in has_in:
                continue
            chain: Chain = [(k, i)]
            while chain[-1] in nxt:
                chain.append(nxt[chain[-1]])
            if len(chain) > 1:
                cores.append(_core(plans, shifts, chain))
            else:
                b = well.box
                unlinked.append(
                    UnlinkedWell(plan.floor_no, plan.view_id, i, (int(b[0]), int(b[1]), int(b[2]), int(b[3])))
                )
    return cores, unlinked


def stair_cores(plans: Sequence[CorePlan], shifts: Sequence[StackShift | None]) -> list[StairCore]:
    """Chỉ phần lõi của `stair_tracks`."""
    return stair_tracks(plans, shifts)[0]
