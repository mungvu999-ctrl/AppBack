"""Nhóm W: tường — vỏ kín (W1), bậc bề dày (W2), chiều cao (W3), mảnh vụn/trùng (W4), khớp hình học thật (W5, W6)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from tools.bench.score.case import Case
from tools.bench.score.geometry import Arr, band, sample, seg_dist

TIERS = (100, 150, 200, 220, 300, 400)  # các bậc bề dày tường chuẩn (mm)
_CELL = 50.0  # ô lưới tô vỏ (mm)
_MAX_CELLS = 4e7
_OUTDOOR = ("loggia", "balcony")
_THICK_LIMIT = 450  # dày hơn mức này không còn là tường "thật" khi xét bậc (vách giả, cột)


@dataclass
class WallFacts:
    """Số đo tường cho báo cáo và cho tiêu chí khác: vỏ kín, bậc dày, mảnh vụn/trùng, vùng ngoài vỏ (cho O5)."""

    enclosed: float | None
    tier_ok: float | None
    outside: tuple[Arr, Arr, float] | None
    leaked: list[list[float]]
    frag: int
    over: int


def _envelope_grid(case: Case) -> tuple[Arr, Arr] | None:
    """Tô tường lên lưới 50 mm rồi "đổ lũ" từ góc ngoài (128 = ngoài vỏ). Trả (lưới, gốc); None nếu lưới quá lớn."""
    segs, k = case.wsegs, case.k
    lo = segs.reshape(-1, 2).min(0) - 1000
    hi = segs.reshape(-1, 2).max(0) + 1000
    nx, ny = (int(v) for v in np.ceil((hi - lo) / _CELL))
    size = (nx, ny)
    if nx * ny >= _MAX_CELLS:
        return None
    im = Image.new("L", size, 0)
    d = ImageDraw.Draw(im)
    for w, s in zip(case.walls, segs, strict=True):
        width = math.ceil(max(w["thicknessMm"] * k, 100) / _CELL) + 1
        d.line([tuple((s[:2] - lo) / _CELL), tuple((s[2:] - lo) / _CELL)], fill=255, width=width)
    ImageDraw.floodfill(im, (0, 0), 128)
    return np.asarray(im), lo


def _room_points(case: Case) -> list[Arr]:
    """Điểm giữa mỗi phòng trong nhà (nhãn đáp án nếu có, không thì tâm phòng AI): bị "lũ" từ ngoài tràn tới = vỏ hở.

    Không dùng tỉ lệ diện tích kín/khung bao vì sân, giếng trời làm tỉ lệ thấp dù vỏ kín.
    """
    pts: list[Arr] = []
    if case.off is not None:
        pts = [
            np.array(x["center_px"]) * case.tm
            for x in case.key.get("rooms") or []
            if x.get("center_px") and x.get("usage") not in _OUTDOOR
        ]
    if not pts:
        pts = [np.mean([case.mm(q) for q in r["outline"]], axis=0) for r in case.rooms if r["usage"] != "loggia"]
    return pts


def _envelope(case: Case, c: dict[str, str]) -> tuple[float | None, tuple[Arr, Arr, float] | None, list[list[float]]]:
    """W1: tỉ lệ phòng nằm trong vỏ kín (≥ 90 % Đ). Trả (tỉ lệ, vùng ngoài vỏ, điểm bị lũ); lưới quá lớn → "?"."""
    grid = _envelope_grid(case)
    if grid is None:
        c["W1"] = "?"
        return None, None, []
    a, lo = grid
    pts = _room_points(case)
    cells = [(q, tuple(int(v) for v in (q - lo) / _CELL)) for q in pts]
    on_grid = [(q, (x, y)) for q, (x, y) in cells if 0 <= x < a.shape[1] and 0 <= y < a.shape[0]]
    # Giữ điểm đi cùng ô của nó khi lọc ô ngoài lưới: ghép lại bằng zip sau khi lọc làm lệch điểm (NO-466)
    enclosed = sum(a[y, x] != 128 for _, (x, y) in on_grid) / len(on_grid) if on_grid else None
    leaked = [list(q) for q, (x, y) in on_grid if a[y, x] == 128]
    c["W1"] = "—" if enclosed is None else "Đ" if enclosed >= 0.9 else "C" if enclosed >= 0.75 else "T"
    return enclosed, (a == 128, lo, _CELL), leaked


def _tier_mark(case: Case, c: dict[str, str]) -> float | None:
    """W2: tường "thật" (≤ 450 mm) có bề dày đúng một bậc chuẩn (±10 mm); cần tỉ lệ đã biết."""
    scale = case.k if not case.scale_known else 1
    real = [w for w in case.walls if w["thicknessMm"] * scale <= _THICK_LIMIT]
    if case.scale_known and real:
        ok = sum(any(abs(w["thicknessMm"] - t) <= 10 for t in TIERS) for w in real) / len(real)
        c["W2"] = "Đ" if ok >= 0.95 else "C" if ok >= 0.9 else "T"
        return float(ok)
    c["W2"] = "—"
    return None


def _fragments(segs: Arr, lengths: Arr) -> int:
    """Số đoạn tường ngắn (< 200 mm) lẻ loi: không có đoạn nào khác trong 50 mm."""
    frag = 0
    for i, s in enumerate(segs):
        if lengths[i] < 200 and seg_dist(np.array([s[:2], s[2:]]), np.delete(segs, i, 0)).min() > 50:
            frag += 1
    return frag


def _overlaps(segs: Arr, lengths: Arr) -> int:
    """Số cặp tường trùng nhau: hơn nửa đoạn ngắn hơn nằm trong 30 mm của đoạn dài hơn."""
    over = 0
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if min(lengths[i], lengths[j]) < 1:
                continue
            short, long_ = (j, i) if lengths[j] <= lengths[i] else (i, j)
            if (seg_dist(sample(segs[[short]], 25), segs[[long_]]) < 30).mean() > 0.5:
                over += 1
    return over


def wall_marks(case: Case, c: dict[str, str]) -> WallFacts:
    """W1-W4 (đặt vào `c`); không có tường thì cả bốn là T."""
    segs = case.wsegs
    if not len(segs):
        for name in ("W1", "W2", "W3", "W4"):
            c[name] = "T"
        return WallFacts(None, None, None, [], 0, 0)
    enclosed, outside, leaked = _envelope(case, c)
    ok = _tier_mark(case, c)
    c["W3"] = "Đ" if all(w["heightMm"] == case.level.get("heightMm") for w in case.walls) else "T"
    lengths = np.linalg.norm(segs[:, 2:] - segs[:, :2], axis=1)
    frag, over = _fragments(segs, lengths), _overlaps(segs, lengths)
    c["W4"] = band(frag + over, 0, 2)
    return WallFacts(enclosed, ok, outside, leaked, frag, over)


def _truth_segment(x: dict[str, Any], vb: list[float]) -> list[float]:
    """Đoạn tường đáp án (ngang `h`/dọc `v`) theo mm trong khung chung."""
    if x["o"] == "h":
        return [x["a"] - vb[0], x["c"] - vb[1], x["b"] - vb[0], x["c"] - vb[1]]
    return [x["c"] - vb[0], x["a"] - vb[1], x["c"] - vb[0], x["b"] - vb[1]]


def _angle(v: Arr) -> float:
    """Góc đoạn (0..π)."""
    return float(math.atan2(v[3] - v[1], v[2] - v[0]) % math.pi)


def _tier(v: float) -> int:
    """Chỉ số bậc bề dày gần nhất."""
    return min(range(len(TIERS)), key=lambda i: abs(TIERS[i] - v))


def _w5(case: Case, tw: Arr) -> tuple[float, float, str]:
    """W5: độ phủ (recall) và độ chính xác (precision) của tường AI so với tường đáp án trong 60 mm."""
    segs = case.wsegs
    rec = (seg_dist(sample(tw), segs) <= 60).mean() if len(segs) else 0.0
    pre = (seg_dist(sample(segs), tw) <= 60).mean() if len(segs) else 0.0
    mark = "Đ" if rec >= 0.9 and pre >= 0.9 else "T" if min(rec, pre) < 0.8 else "C"
    return rec, pre, mark


def _w6(case: Case, tw: Arr, truth_walls: list[dict[str, Any]]) -> tuple[str, list[list[Any]]]:
    """W6: bề dày mỗi tường AI cùng bậc (±1) với tường đáp án song song trùng nó.

    Ghép theo đoạn song song, không theo điểm giữa (giao chữ T ghép nhầm tường kề).
    """
    diffs: list[int] = []
    bad: list[list[Any]] = []
    for w, s in zip(case.walls, case.wsegs, strict=True):
        pts = sample(s[None], 25)
        cover = [
            ((seg_dist(pts, tw[[i]]) <= 60).mean() if abs(math.sin(_angle(s) - _angle(tw[i]))) < 0.1 else 0.0)
            for i in range(len(tw))
        ]
        i = int(np.argmax(cover))
        if cover[i] >= 0.5:
            diffs.append(abs(_tier(w["thicknessMm"] * case.k) - _tier(truth_walls[i]["t"])))
            if diffs[-1] > 1:
                bad.append([w["id"], round(w["thicknessMm"] * case.k), truth_walls[i]["t"]])
    return ("Đ" if diffs and max(diffs) <= 1 else "T"), bad


def _o2(case: Case, truth: dict[str, Any], vb: list[float]) -> float:
    """O2: tỉ lệ ô mở đáp án có ô mở AI cùng loại trong 300 mm (mỗi ô AI ghép một lần)."""
    targets = []
    for x in truth["openings"]:
        if x["type"] != "gap":
            seg = _truth_segment(x, vb)
            kind = "door" if x["type"] in ("door", "slide", "door2") else "window"
            targets.append(((np.array(seg[:2]) + np.array(seg[2:])) / 2, kind))
    actual = [(np.array(case.op_center(x)), x["kind"]) for x in case.ops if case.op_center(x)]
    used: set[int] = set()
    hit = 0
    for p, kind in targets:
        cand = [(np.linalg.norm(p - q), i) for i, (q, k3) in enumerate(actual) if k3 == kind and i not in used]
        if cand and min(cand)[0] <= 300:
            used.add(min(cand)[1])
            hit += 1
    return hit / max(len(targets), 1)


def truth_marks(case: Case, c: dict[str, str]) -> tuple[Any, Any, Any, list[list[Any]]]:
    """W5, W6, O2 với đáp án hình học (nhóm B); không có `truth` thì "—". Trả (recall, precision, o2, w6_bad)."""
    truth = case.key.get("truth")
    if not truth:
        c["W5"] = c["W6"] = c["O2"] = "—"
        return None, None, None, []
    vb = truth["vb"]
    tw = np.array([_truth_segment(x, vb) for x in truth["walls"]], dtype=float)
    rec, pre, c["W5"] = _w5(case, tw)
    c["W6"], bad = _w6(case, tw, truth["walls"])
    o2 = _o2(case, truth, vb)
    c["O2"] = "Đ" if o2 >= 0.85 else "C" if o2 >= 0.7 else "T"
    return rec, pre, o2, bad
