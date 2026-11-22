"""Nhóm P: phòng — số phòng (P1), ngủ/WC (P2), nhãn trong phòng (P3), công năng (P4), diện tích (P5-P6), hợp lệ (P7)."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from tools.bench.score.case import Case
from tools.bench.score.geometry import Point, band, inside, self_cross
from tools.bench.score.io import Json

# Công năng đáp án → các công năng AI chấp nhận
USAGE = {
    "bedroom": {"bedroom"}, "living": {"livingRoom"}, "kitchen": {"kitchen"}, "wc": {"bathroom"},
    "living_kitchen": {"livingRoom", "kitchen"}, "dining": {"livingRoom", "kitchen"},
    "loggia": {"loggia"}, "balcony": {"loggia"}, "hall": {"corridor", "livingRoom"},
    "storage": {"utility", "other"}, "laundry": {"utility", "loggia", "other"},
}  # fmt: skip
_OVERLAP_CELL = 100.0  # ô lưới tô chồng lấn (mm)


@dataclass
class RoomFacts:
    """Số đo phòng cho báo cáo và cho U3: số phòng mong đợi, tỉ lệ P3/P4, trung vị sai số P5, diện tích, lỗi P7."""

    want: int | None
    p3: float | None
    p4: float | None
    p5: float | None
    sum_m2: float
    key_m2: float | None
    self_cross: int
    under_1m2: int
    overlap: Any


def clear_m2(case: Case, r: Json) -> float:
    """Diện tích thông thủy ước của phòng AI: tim tường - chu vi x nửa bề dày trung bình tường bao phòng (mm thật)."""
    ts = [case.wall_by[i][0]["thicknessMm"] * case.k for i in r.get("wallIds", []) if i in case.wall_by] or [110]
    pts = [case.mm(q) for q in r["outline"]]
    per = sum(math.dist(a, b) for a, b in zip(pts, pts[1:] + pts[:1], strict=True))
    area: float = max(r["areaM2"] * case.k**2 - per * (sum(ts) / len(ts)) / 2 / 1e6, 0)
    return area


def _count_marks(case: Case, c: dict[str, str]) -> int | None:
    """P1 (số phòng so đáp án), P2 (số phòng ngủ + WC). Trả số phòng mong đợi (None khi đáp án không có phòng)."""
    krooms = case.key.get("rooms") or []
    want = len(krooms) + case.key.get("rooms_extra", 0)
    c["P1"] = band(abs(len(case.rooms) - want), 1, 2) if krooms else "—"
    kc = case.key.get("counts") or {}
    if kc.get("bedrooms") is not None:
        beds = sum(r["usage"] == "bedroom" for r in case.rooms)
        wcs = sum(r["usage"] == "bathroom" for r in case.rooms)
        c["P2"] = band(abs(beds - kc["bedrooms"]) + abs(wcs - kc.get("wc", 0)), 0, 1)
    else:
        c["P2"] = "—"
    return want if krooms else None


def _label_marks(case: Case, c: dict[str, str]) -> tuple[float | None, float | None, Any]:
    """P3 (nhãn đáp án rơi đúng một phòng AI), P4 (công năng khớp), P5 (trung vị sai số diện tích thông thủy)."""
    lab = [x for x in case.key.get("rooms") or [] if x.get("center_px")]
    if not (lab and case.off is not None):
        c["P3"] = c["P4"] = c["P5"] = "—"
        return None, None, None
    polys = [[case.px(p) for p in r["outline"]] for r in case.rooms]
    hits = [[i for i, poly in enumerate(polys) if inside((x["center_px"][0], x["center_px"][1]), poly)] for x in lab]
    p3 = sum(len(h) == 1 for h in hits) / len(lab)
    good = [(x, case.rooms[h[0]]) for x, h in zip(lab, hits, strict=True) if len(h) == 1]
    p4 = sum(r["usage"] in USAGE.get(x["usage"], {r["usage"]}) for x, r in good) / len(good) if good else 0
    c["P3"] = "Đ" if p3 >= 0.9 else "C" if p3 >= 0.75 else "T"
    c["P4"] = "Đ" if p4 >= 0.85 else "C" if p4 >= 0.7 else "T"
    errs = [abs(clear_m2(case, r) / x["area_m2"] - 1) * 100 for x, r in good if x.get("area_m2")]
    p5 = statistics.median(errs) if errs else None
    c["P5"] = band(p5, 8, 15)
    return p3, p4, p5


def _area_mark(case: Case, c: dict[str, str]) -> tuple[float, float | None]:
    """P6: tổng diện tích phòng so tổng diện tích đáp án (thông thủy nếu có, không thì tim tường)."""
    ta = case.key.get("total_area_m2") or {}
    tot = ta.get("thong_thuy") or ta.get("tim_tuong")
    if ta.get("thong_thuy"):
        s_area = sum(clear_m2(case, r) for r in case.rooms)
    else:
        s_area = sum(r["areaM2"] for r in case.rooms) * case.k**2
    c["P6"] = band(abs(s_area / tot - 1) * 100, 5, 10) if tot else "—"
    return s_area, tot


def _overlap_ratio(polys: list[list[Point]]) -> Any:
    """Tỉ lệ diện tích chồng lấn có bề rộng giữa các phòng (bỏ dải 1-2 ô ở cạnh chung của hai phòng kề)."""
    allp = np.array([q for p in polys for q in p])
    lo = allp.min(0)
    nx, ny = (int(v) + 1 for v in np.ceil((allp.max(0) - lo) / _OVERLAP_CELL))
    size = (nx, ny)
    acc = np.zeros((ny, nx), dtype=np.int16)
    for p in polys:
        im = Image.new("L", size, 0)
        ImageDraw.Draw(im).polygon([tuple((np.array(q) - lo) / _OVERLAP_CELL) for q in p], fill=1)
        acc += np.asarray(im, dtype=np.int16)
    two = acc >= 2
    core = two[1:-1, 1:-1] & two[:-2, 1:-1] & two[2:, 1:-1] & two[1:-1, :-2] & two[1:-1, 2:]
    return core.sum() / max((acc >= 1).sum(), 1)


def _validity_mark(case: Case, c: dict[str, str]) -> tuple[int, int, Any]:
    """P7: phòng không tự cắt, không dưới 1 m², không chồng lấn đáng kể (> 1 %). Trả (tự cắt, < 1 m², chồng lấn)."""
    polys = [[case.mm(p) for p in r["outline"]] for r in case.rooms]
    cross = sum(self_cross(p) for p in polys)
    small = sum(r["areaM2"] * case.k**2 < 1 for r in case.rooms)
    overlap = None
    p7 = cross + small
    if polys:
        overlap = _overlap_ratio(polys)
        p7 += overlap > 0.01
    c["P7"] = "Đ" if case.rooms and p7 == 0 else "T"
    return cross, small, overlap


def room_marks(case: Case, c: dict[str, str]) -> RoomFacts:
    """P1-P7 (theo thứ tự đó) vào `c`; trả số đo cho báo cáo."""
    want = _count_marks(case, c)
    p3, p4, p5 = _label_marks(case, c)
    s_area, tot = _area_mark(case, c)
    cross, small, overlap = _validity_mark(case, c)
    return RoomFacts(want, p3, p4, p5, s_area, tot, cross, small, overlap)
