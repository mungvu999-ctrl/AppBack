"""Nhóm O: ô mở — số cửa/cửa sổ (O1, O1b), vị trí (O1c), kích thước hợp lệ (O3), toàn vẹn (O4), lối vào (O5)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from tools.bench.score.case import Case
from tools.bench.score.geometry import Arr, band, count_crit, inside, seg_dist
from tools.bench.score.io import Json

_ENTRY_RADIUS_MM = 400  # cửa ra ngoài = tâm cửa cách vùng ngoài vỏ ≤ mức này
_DEFAULT_HEIGHT_MM = 3300


@dataclass
class OpeningFacts:
    """Số đo ô mở cho báo cáo và tiêu chí khác: số cửa, cửa sổ, tỉ lệ khớp vị trí, vi phạm, phòng thiếu cửa."""

    doors: int
    windows: int
    o1c: float | None
    violations: int
    missing: int


def _match_by_distance(targets: list[tuple[Arr, str]], actual: list[tuple[Arr, str]], tol: float) -> int:
    """Số mục tiêu có ô mở thật cùng loại trong `tol` (mỗi ô thật ghép một lần, ưu tiên gần nhất)."""
    used: set[int] = set()
    hit = 0
    for p, kind in targets:
        cand = [(np.linalg.norm(p - q), i) for i, (q, k3) in enumerate(actual) if k3 == kind and i not in used]
        if cand and min(cand)[0] <= tol:
            used.add(min(cand)[1])
            hit += 1
    return hit


def _position_mark(case: Case, c: dict[str, str]) -> float | None:
    """O1c: ô mở đáp án (có toạ độ px) khớp ô mở AI cùng loại trong 3 % cạnh dài ảnh."""
    kops = [x for x in case.key.get("openings") or [] if x.get("center_px")]
    if not (kops and case.off is not None):
        c["O1c"] = "—"
        return None
    with Image.open(case.bench.plan_or_page(case.cid, case.row)) as img:
        tol = 0.03 * max(img.size)
    actual = [(np.array(case.op_center(x, case.px)), x["kind"]) for x in case.ops if case.op_center(x, case.px)]
    targets = [(np.array(x["center_px"]), x["kind"]) for x in kops]
    o1c = _match_by_distance(targets, actual, tol) / len(kops)
    c["O1c"] = "Đ" if o1c >= 0.8 else "C" if o1c >= 0.65 else "T"
    return o1c


def _host_length(case: Case, op: Json) -> float:
    """Chiều dài tường chủ của ô mở theo mm thật; 0 khi không có tường chủ."""
    hw = case.wall_by.get(op["wallId"])
    if not hw:
        return 0.0
    s, e = hw[0]["centreline"]["start"], hw[0]["centreline"]["end"]
    return math.dist((s["x"], s["y"]), (e["x"], e["y"])) * case.k


def _size_violations(case: Case) -> int:
    """O3: số ô mở sai kích thước (cửa 600-1800 mm, cao 1900-2500; cửa sổ ≥ 300 mm, bệ ≤ 1500, không vượt trần)."""
    viol = 0
    height = case.level.get("heightMm") or _DEFAULT_HEIGHT_MM
    for x in case.ops:
        wd = x["widthMm"] * case.k
        if x["kind"] == "door":
            wide_ok = 600 <= wd <= 1800 or (wd <= 4000 and x["swing"] in ("sliding", "double"))
            viol += not (wide_ok and 1900 <= x["heightMm"] <= 2500 and x["sillHeightMm"] <= 50)
        else:  # dải kính dài có thật: cửa sổ hợp lệ tới chiều dài tường chủ
            host = _host_length(case, x)
            viol += not (
                300 <= wd <= max(3600, host + 1)
                and x["sillHeightMm"] <= 1500
                and x["sillHeightMm"] + x["heightMm"] <= height
            )
    return viol


def _integrity_issues(case: Case) -> int:
    """O4: ô mở vượt hết chiều dài tường chủ, hoặc hai ô mở chồng nhau trên cùng một tường."""
    o4 = 0
    for x in case.ops:
        w = case.wall_by.get(x["wallId"])
        if w:
            s, e = w[0]["centreline"]["start"], w[0]["centreline"]["end"]
            o4 += x["offsetMm"] + x["widthMm"] > math.dist((s["x"], s["y"]), (e["x"], e["y"])) + 1
    for i, x in enumerate(case.ops):
        for y in case.ops[i + 1 :]:
            same_wall = x["wallId"] == y["wallId"]
            o4 += (
                same_wall
                and x["offsetMm"] < y["offsetMm"] + y["widthMm"]
                and y["offsetMm"] < x["offsetMm"] + x["widthMm"]
            )
    return o4


def _rooms_without_door(case: Case, doors: list[Arr]) -> int:
    """Số phòng ngủ/WC không có cửa nào trong phòng hoặc cách viền ≤ 300 mm."""
    missing = 0
    for r in case.rooms:
        if r["usage"] in ("bedroom", "bathroom"):
            poly = [case.mm(p) for p in r["outline"]]
            segs = np.array([[*a, *b] for a, b in zip(poly, poly[1:] + poly[:1], strict=True)])
            missing += not any(
                inside((float(d[0]), float(d[1])), poly) or seg_dist(d[None], segs)[0] <= 300 for d in doors
            )
    return missing


def _has_entry(doors: list[Arr], outside: tuple[Arr, Arr, float] | None) -> bool:
    """Có cửa ra ngoài không: tâm cửa cách vùng ngoài vỏ (W1) ≤ 400 mm.

    Không dựa nhãn `envelope` vì AI gán nhãn này rất thưa.
    """
    if outside is None:
        return False
    mask, lo, cell = outside
    r = int(_ENTRY_RADIUS_MM / cell)
    main = False
    for d in doors:
        cx, cy = (int(v) for v in (d - lo) / cell)
        main |= bool(mask[max(cy - r, 0) : cy + r + 1, max(cx - r, 0) : cx + r + 1].any())
    return main


def opening_marks(case: Case, c: dict[str, str], outside: tuple[Arr, Arr, float] | None) -> OpeningFacts:
    """O1, O1b, O1c, O3, O4, O5 (theo thứ tự đó) vào `c`; `outside` là vùng ngoài vỏ do `wall_marks` trả."""
    kc = case.key.get("counts") or {}
    nd = sum(x["kind"] == "door" for x in case.ops)
    nw = sum(x["kind"] == "window" for x in case.ops)
    c["O1"] = count_crit(nd, kc.get("doors"), kc.get("doors_confident", False))
    c["O1b"] = count_crit(nw, kc.get("windows"), kc.get("windows_confident", False))
    o1c = _position_mark(case, c)
    viol = _size_violations(case)
    c["O3"] = band(viol, 0, 1)
    c["O4"] = "Đ" if _integrity_issues(case) == 0 else "T"
    doors = case.centers("door")
    missing = _rooms_without_door(case, doors)
    c["O5"] = ("T" if not _has_entry(doors, outside) else band(missing, 0, 1)) if case.rooms else "T"
    return OpeningFacts(nd, nw, o1c, viol, missing)
