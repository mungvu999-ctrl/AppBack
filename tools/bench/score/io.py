"""Đọc dữ liệu một lượt bench: tệp kết quả, đáp án (key), độ lệch trang. Không tính điểm.

Hệ toạ độ: điểm layer (mm của AI) → px ảnh gốc = mm / scaleAI + độ lệch trang (page.png dò khớp ảnh gốc).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from tools.bench.score.geometry import Arr

Json = dict[str, Any]

_NAME_TO_USAGE = (
    ("SÂN", "balcony"), ("GIẾNG", "balcony"), ("NGỦ", "bedroom"), ("WC", "wc"), ("LÔ GIA", "loggia"),
    ("BẾP", "kitchen"), ("SẢNH", "hall"), ("THAY ĐỒ", "storage"), ("KHO", "storage"), ("GIẶT", "laundry"),
    ("BAN CÔNG", "balcony"),
)  # fmt: skip
_DOOR_TYPES = ("door", "slide", "door2")
_PATCH = 48  # cạnh mẩu ảnh dò độ lệch (px)
_MATCH_SSD = 100  # sai số bình phương trung bình < 100 (≈ 10 mức xám) mới coi là khớp


def read_manifest(path: Path) -> dict[str, dict[str, str]]:
    """Đọc `MANIFEST.csv` (UTF-8 có BOM) → {id: dòng}."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def _scaled(k: Json, r: float) -> None:
    """Đổi các đại lượng pixel của đáp án theo hệ số r (ảnh đáp án rộng gấp r lần ảnh hệ thống dựng), tại chỗ."""
    k["mm_per_px"] = k["mm_per_px"] / r if k.get("mm_per_px") else None
    if k.get("mm_per_px_axes"):
        k["mm_per_px_axes"] = {a: v / r for a, v in k["mm_per_px_axes"].items()}
    k["plan_bbox_px"] = [v * r for v in k["plan_bbox_px"]] if k.get("plan_bbox_px") else None
    for o in (k.get("rooms") or []) + (k.get("openings") or []):
        if o.get("center_px"):
            o["center_px"] = [v * r for v in o["center_px"]]
    for ov in k.get("overall") or []:
        for a in ("px0", "px1"):
            if ov.get(a) is not None:
                ov[a] *= r


def _scaled_base(base: Json, r: float) -> None:
    """Nhóm C: đổi mm/px, khung và toạ độ tâm của đáp án bản gốc theo hệ số r, tại chỗ."""
    base["mm_per_px"] = base["mm_per_px"] / r if base.get("mm_per_px") else None
    base["plan_bbox_px"] = [v * r for v in base["plan_bbox_px"]] if base.get("plan_bbox_px") else None
    for o in base["rooms"] + base["openings"]:
        if o.get("center_px"):
            o["center_px"] = [v * r for v in o["center_px"]]


def _overall_refs(k: Json) -> None:
    """Đáp án "tổng" là một chuỗi không phủ hết căn → S3 so với khung căn x mm/px (`mm_ref`), tại chỗ."""
    bb = k.get("plan_bbox_px")
    for ov in k.get("overall") or []:
        mpp = (k.get("mm_per_px_axes") or {}).get(ov["axis"]) or k.get("mm_per_px")
        if bb and mpp and ov.get("px0") is not None and ov.get("px1") is not None:
            span = (bb[2] - bb[0]) if ov["axis"] == "x" else (bb[3] - bb[1])
            if abs(ov["px1"] - ov["px0"]) < 0.95 * span:
                ov["mm_ref"] = span * mpp


def _geometry_key(k: Json) -> Json:
    """Nhóm B: hình học chính xác (mm) → khung chung; giữ `truth` để chấm W5/W6/O2."""
    vb, px = k["viewBox"], k["px"]
    rooms = [
        {
            "name": r["name"],
            "usage": next((u for s, u in _NAME_TO_USAGE if s in r["name"]), "other"),
            "area_m2": r["area_m2"],
            "center_px": [
                ((r["rect_mm"][0] + r["rect_mm"][2]) / 2 - vb[0]) * px,
                ((r["rect_mm"][1] + r["rect_mm"][3]) / 2 - vb[1]) * px,
            ],
        }
        for r in k["rooms"]
    ]
    ops = k["openings"]
    counts = {
        "bedrooms": sum(r["usage"] == "bedroom" for r in rooms),
        "wc": sum(r["usage"] == "wc" for r in rooms),
        "doors": sum(o["type"] in _DOOR_TYPES for o in ops), "doors_confident": True,
        "windows": sum(o["type"] == "win" for o in ops), "windows_confident": True,
    }  # fmt: skip
    return {
        "mm_per_px": 1 / px, "overall": [{"axis": "x", "mm": k["W"]}, {"axis": "y", "mm": k["H"]}],
        "plan_bbox_px": None, "rooms": rooms, "rooms_extra": 1, "openings": [], "counts": counts,
        "truth": {"vb": vb, "walls": k["walls"], "openings": ops},
    }  # fmt: skip


def _vector_key(k: Json) -> Json:
    """Căn vẽ vector (có `overall_mm`): chỉ có kích thước tổng, phòng không toạ độ."""
    return {
        "mm_per_px": None,
        "overall": [{"axis": "x", "mm": k["overall_mm"][0]}, {"axis": "y", "mm": k["overall_mm"][1]}],
        "plan_bbox_px": None, "rooms": [dict(r, center_px=None) for r in k["rooms"]], "openings": [],
        "counts": dict(k["counts"], doors_confident=True, windows_confident=True), "vector": True,
    }  # fmt: skip


def _behaviour_key(k: Json) -> Json:
    """Nhóm D: đáp án là hành vi mong đợi; ca đối chứng "mo-hinh-day-du" chấm như mô hình thường, không đáp án."""
    return {
        "behaviour": None if k["expect"] == "mo-hinh-day-du" else k["expect"],
        "rooms": [],
        "openings": [],
        "counts": {},
    }


def match_offset(page: Arr, orig: Arr) -> tuple[int, int] | None:
    """Vị trí (dx, dy) của ảnh `page` trong ảnh gốc `orig` (thang xám); None nếu `page` lớn hơn hoặc không khớp."""
    (h, w), (big_h, big_w) = page.shape, orig.shape
    if h > big_h or w > big_w:
        return None
    if (h, w) == (big_h, big_w):
        return (0, 0)
    n = _PATCH
    _, py, px = max((page[y : y + n, x : x + n].std(), y, x) for y in range(0, h - n, n) for x in range(0, w - n, n))
    patch = page[py : py + n, px : px + n]
    best = (1e18, 0, 0)
    for dy in range(big_h - h + 1):
        win = np.lib.stride_tricks.sliding_window_view(orig[py + dy : py + dy + n, px : px + big_w - w + n], (n, n))[0]
        ssd = ((win - patch) ** 2).mean(axis=(1, 2))
        i = int(ssd.argmin())
        if ssd[i] < best[0]:
            best = (float(ssd[i]), i, dy)
    return (best[1], best[2]) if best[0] < _MATCH_SSD else None


def _image_width(p: Path) -> int:
    """Bề rộng ảnh tại `p` (px)."""
    with Image.open(p) as img:
        return int(img.width)


@dataclass
class Bench:
    """Một lượt chấm: gốc dữ liệu, thư mục lượt, độ lệch cho trước, manifest. Giữ cache độ lệch giữa các lần chấm."""

    root: Path
    run: Path
    offsets: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, dict[str, str]] = field(init=False)

    def __post_init__(self) -> None:
        """Đọc manifest của gốc dữ liệu."""
        self.manifest = read_manifest(self.root / "MANIFEST.csv")

    def load(self, cid: str, name: str, cal: bool = False) -> Json | None:
        """Đọc `<ca>/<name>.json` (hoặc `<ca>.<name>.json` của lượt cũ); `cal` chỉ đọc `calibrated/`; thiếu → None."""
        if cal:
            paths = [self.run / cid / "calibrated" / f"{name}.json"]
        else:
            paths = [self.run / cid / f"{name}.json", self.run / f"{cid}.{name}.json"]
        for p in paths:
            if p.exists():
                data: Json = json.loads(p.read_text(encoding="utf-8"))
                return data
        return None

    def key_of(self, cid: str) -> Json:
        """Đáp án chung cho mọi nhóm: mm_per_px, overall, plan_bbox_px, rooms, counts, openings, total_area.

        Đáp án có `pdf_raster_px`+`image_px` (PDF gói một ảnh raster) được đổi pixel sang `page.png` của hệ thống.
        Nhóm C (`derived_from`) lấy đáp án bản gốc, đổi theo tỉ lệ ảnh. Ghi độ lệch (0,0) vào cache cho ca PDF raster.
        """
        k: Json = json.loads((self.root / "answers" / f"{cid}.json").read_text(encoding="utf-8"))
        page = self.run / cid / "page.png"
        if k.get("pdf_raster_px") and k.get("image_px") and page.exists():
            _scaled(k, _image_width(page) / k["image_px"][0])
            self.offsets.setdefault(cid, (0, 0))
        _overall_refs(k)
        if "derived_from" in k:
            return self._derived_key(cid, k["derived_from"])
        if "viewBox" in k:
            return _geometry_key(k)
        if "overall_mm" in k:
            return _vector_key(k)
        if "expect" in k:
            return _behaviour_key(k)
        k.setdefault("openings", [])
        return k

    def _derived_key(self, cid: str, base_id: str) -> Json:
        """Nhóm C: đáp án bản gốc; đổi pixel theo tỉ lệ ảnh nếu chỉ phóng/thu/nén, bỏ toạ độ nếu chụp nghiêng/PDF."""
        base = self.key_of(base_id)
        w0 = _image_width(self.root / "plans" / self.manifest[base_id]["file"])
        f = self.manifest[cid]["file"]
        if f.endswith((".png", ".jpg")) and not f.startswith("photo"):
            _scaled_base(base, _image_width(self.root / "plans" / f) / w0)
        else:
            base.update(mm_per_px=None, plan_bbox_px=None, openings=[])
            for o in base["rooms"]:
                o["center_px"] = None
        return base

    def plan_or_page(self, cid: str, row: dict[str, str]) -> Path:
        """Ảnh dùng đo cỡ khung: `page.png` của lượt với PDF, ảnh gốc `plans/<file>` còn lại."""
        return self.run / cid / "page.png" if row["file"].endswith(".pdf") else self.root / "plans" / row["file"]

    def find_offset(self, cid: str) -> tuple[int, int] | None:
        """Độ lệch (dx, dy) px của page.png trong ảnh gốc: dò khớp một mẩu 48x48 nhiều chi tiết; None nếu không khớp."""
        if cid in self.offsets:
            dx, dy = self.offsets[cid]
            return (dx, dy)
        page_p, orig_p = self.run / cid / "page.png", self.root / "plans" / self.manifest[cid]["file"]
        if not page_p.exists() or orig_p.suffix == ".pdf":
            return None
        with Image.open(page_p) as a, Image.open(orig_p) as b:
            page_a = np.asarray(a.convert("L"), dtype=np.float32)
            orig_a = np.asarray(b.convert("L"), dtype=np.float32)
        return match_offset(page_a, orig_a)
