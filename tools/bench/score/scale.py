"""Nhóm S: tỉ lệ (S1, S2), kích thước tổng (S3, S5), khung bản vẽ (S4)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image

from tools.bench.score.case import Case
from tools.bench.score.geometry import band


def _extent(case: Case) -> dict[str, tuple[float, float]] | None:
    """Kích thước tổng của tường theo mỗi trục (tim, và tính cả bề dày) theo mm AI; None khi chưa có tường/tỉ lệ."""
    segs, k = case.wsegs, case.k
    if not (len(segs) and case.scale_known):
        return None
    cl = (segs[:, [0, 2]].min(), segs[:, [1, 3]].min(), segs[:, [0, 2]].max(), segs[:, [1, 3]].max())
    tmax = max(x["thicknessMm"] for x in case.walls) * k / 2
    return {
        "x": ((cl[2] - cl[0]) / k, (cl[2] - cl[0] + 2 * tmax) / k),
        "y": ((cl[3] - cl[1]) / k, (cl[3] - cl[1] + 2 * tmax) / k),
    }


def _worst_overall_error(case: Case) -> Any:
    """Sai số lớn nhất (%) giữa kích thước tổng của AI và các số tổng đáp án; None khi không đo được."""
    ext = _extent(case)
    errs = [
        min(abs(e / ov.get("mm_ref", ov["mm"]) - 1) for e in ext[ov["axis"]]) * 100
        for ov in case.key.get("overall") or []
        if ext and ov.get("mm")
    ]
    return max(errs) if errs else None


def _frame_mark(case: Case) -> str:
    """S4: tường/phòng của AI nằm trong khung căn của đáp án (+2 % cạnh dài ảnh). Tường ngoài khung > 500 mm → T."""
    key = case.key
    if not (key.get("plan_bbox_px") and case.off is not None):
        return "—"
    x0, y0, x1, y1 = key["plan_bbox_px"]
    with Image.open(case.bench.plan_or_page(case.cid, case.row)) as img:
        m = 0.02 * max(img.size)

    def outside(cx: Any, cy: Any) -> bool:
        return not (x0 - m <= cx <= x1 + m and y0 - m <= cy <= y1 + m)

    px = case.px
    out_len = 0.0
    for w in case.walls:
        a, b = px(w["centreline"]["start"]), px(w["centreline"]["end"])
        if outside((a[0] + b[0]) / 2, (a[1] + b[1]) / 2):
            out_len += math.dist(a, b) * case.tm
    room_out = any(
        outside(np.mean([px(p)[0] for p in r["outline"]]), np.mean([px(p)[1] for p in r["outline"]]))
        for r in case.rooms
    )
    return "T" if room_out or out_len > 500 else ("C" if out_len > 0 else "Đ")


def scale_marks(case: Case, c: dict[str, str]) -> Any:
    """S1 (tỉ lệ so đáp án), S2 (không đáp án: phải khai chưa rõ), S3, S5 (kích thước tổng), S4. Trả sai số lớn nhất."""
    key, sc = case.key, case.sc
    if key.get("mm_per_px"):
        c["S1"] = "T" if not case.scale_known else band(abs(sc / key["mm_per_px"] - 1) * 100, 2, 5)
    else:
        c["S1"] = "—"
    if not key.get("mm_per_px") and not key.get("vector"):
        c["S2"] = "Đ" if case.body.get("scaleStatus", "unresolved") == "unresolved" else "T"
    else:
        c["S2"] = "—"
    worst = _worst_overall_error(case)
    c["S3"] = band(worst, 3, 6) if key.get("overall") and not key.get("vector") else "—"
    c["S5"] = (band(worst, 1, 3) if worst is not None else "T") if key.get("vector") else "—"
    c["S4"] = _frame_mark(case)
    return worst
