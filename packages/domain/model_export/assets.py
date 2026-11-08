"""Furniture model resolution against the export pack (HOP-DONG-M1 §1 "Đồ đạc", §8).

Pack: `models.json` `{id: {file, license, author, source, sizeMm: {w, d, h} | [x, y, z]}}` (`{x, y, z}` cũ vẫn đọc),
`furniture-defaults.json` `{"kind|variant" | "kind": modelId}`.
Assumptions: `boundingBox` is the footprint before rotation (width along x, depth along y, mm);
`rotationDeg` turns in the editor's Y-down frame, so it flips sign in the export's Y-up frame;
`at` z is level-local (`liftMm`).

F0/F4 (M2-QL, KET-QUA-LOI §4-5): every instance is anchored by its model bbox (`anchor: "bbox"`, the worker puts the
bbox bottom centre on `at`): bottom on the finish floor + `liftMm`, centre on the box centre. The model's long side
follows the box's long side (turned 90° with its back towards the nearer wall), uniform scale = footprint ratio
(`suggestions.rules.placement.footprint_ratio`, the rules' own fit) clamped to ±10 %; a box needing more →
`degraded minor`, the model stays at the clamp (never shrunk further).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from packages.domain.model_export.defaults import FINISH_FLOOR_M, SUGGESTION_SCALE_TOL
from packages.domain.model_export.records import RecordBook, Status
from packages.domain.model_export.units import to_m
from packages.domain.suggestions.rules.placement import footprint_ratio

_SRC = re.compile(r"^/items/([a-z0-9_-]+)/model\.glb$")
ROUND_SLACK_MM = 0.5
"""Box sides are whole mm (rules store `js_round(model * scale)`): each side may have lost this much."""
LONG_AXIS_RATIO = 1.2
"""Model and box both at least this elongated, long sides crossed → turn the model 90° (B2 F4)."""


def model_size_m(meta: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """`sizeMm` of a pack model (list `[x, y, z]` or dict `w/d/h`, else legacy `x/y/z`) → metres; missing → None.

    The one size reader of the compiler (M1's `_size_mm` w/d/h fix merged into it, M2-QL)."""
    s = meta.get("sizeMm")
    if isinstance(s, Mapping):
        vals = (s.get("w", s.get("x", 0)), s.get("d", s.get("y", 0)), s.get("h", s.get("z", 0)))
    elif isinstance(s, (list, tuple)) and len(s) == 3:
        vals = tuple(s)
    else:
        return None
    out = tuple(float(v) / 1000 for v in vals)
    return (out[0], out[1], out[2]) if all(v > 0 for v in out) else None


def fit_scale(
    box_mm: Sequence[float], model_mm: Sequence[float], tol: float = SUGGESTION_SCALE_TOL
) -> tuple[float, bool]:
    """(scale, fits): footprint ratio at rule precision (3 decimals) clamped to ±`tol`; `fits` is False when the box
    needs more. The test grants each box side `ROUND_SLACK_MM`, so a model the rules shrank by `tol` fits back."""
    ratio = footprint_ratio(box_mm, model_mm)
    padded = footprint_ratio([v + ROUND_SLACK_MM for v in box_mm[:2]], model_mm)
    lo, hi = 1 - tol, 1 + tol
    return min(max(round(ratio, 3), lo), hi), padded >= lo - 1e-9 and ratio <= hi + 1e-9


def _front_mm(rotation_deg: float) -> tuple[float, float]:
    """Unit direction (layer mm, y down) a model front (local +y) faces at `rotation_deg`."""
    th = math.radians(rotation_deg)
    return -math.sin(th), math.cos(th)


def long_axis_rotation(
    f: Mapping[str, Any], box: tuple[float, float], model: tuple[float, float], walls: BaseGeometry | None
) -> float | None:
    """New `rotationDeg` when the model's long side crosses the box's long side, else None. Of the two quarter turns
    the one whose back (half the box's short side behind the centre) is nearer a wall wins; no walls → +90."""
    (bw, bd), (mw, md) = box, model
    if min(bw, bd) <= 0 or max(bw, bd) / min(bw, bd) < LONG_AXIS_RATIO or max(mw, md) / min(mw, md) < LONG_AXIS_RATIO:
        return None
    if (mw >= md) == (bw >= bd):
        return None
    rot, c, half = float(f.get("rotationDeg") or 0), f["centre"], min(bw, bd) / 2
    cands = ((rot + 90) % 360, (rot - 90) % 360)
    if walls is None or walls.is_empty:
        return cands[0]
    near = walls

    def back_gap(cand: float) -> float:
        nx, ny = _front_mm(cand)
        return float(near.distance(Point((c["x"] - nx * half) / 1000, -(c["y"] - ny * half) / 1000)))

    return min(cands, key=back_gap)


class AssetResolver:
    """Maps document furniture to plan.json instances."""

    def __init__(self, models_json: Mapping[str, Mapping[str, Any]], furniture_defaults: Mapping[str, str]) -> None:
        """Pack tables."""
        self._models = models_json
        self._defaults = furniture_defaults

    def has_model(self, mid: str) -> bool:
        """`mid` is in the pack's `models.json`."""
        return mid in self._models

    def model(self, mid: str) -> Mapping[str, Any]:
        """Pack metadata of `mid` (empty when absent)."""
        return self._models.get(mid) or {}

    def _pick(self, f: Mapping[str, Any], pick: str | None = None) -> tuple[str | None, str]:
        """(model id, prov_model) from `model.src`, else a `modelPick` suggestion, else editor default by
        kind|variant then kind (contract M2 §3: `model.src` > modelPick > editor default)."""
        src = (f.get("model") or {}).get("src", "")
        m = _SRC.match(src)
        if m:
            return m.group(1), "document"
        if pick:
            return pick, "suggested"
        kind = f.get("kind")
        mid = self._defaults.get(f"{kind}|{f.get('variant')}") or self._defaults.get(str(kind))
        return mid, "editor-default"

    def resolve_furniture(
        self,
        f: Mapping[str, Any],
        level: str,
        book: RecordBook,
        pick: str | None = None,
        walls: BaseGeometry | None = None,
    ) -> dict[str, Any] | None:
        """Instance dict, or None (recorded) when it can't be placed with its own model; `pick` = modelPick model;
        `walls` (metres) choose the back side when the model is turned to the box's long axis."""
        fid = f["id"]
        if f.get("suggested"):
            book.add(level, "furniture", fid, "skipped", "info", "gợi ý chưa được nhận, không dựng")
            return None
        mid, prov = self._pick(f, pick)
        if mid is None:
            book.add(level, "furniture", fid, "skipped", "major", f"chưa có mẫu mặc định cho loại {f.get('kind')}")
            return None
        meta = self._models.get(mid)
        if meta is None:
            book.add(level, "furniture", fid, "skipped", "major", f"mẫu {mid} chưa có trong gói xuất")
            return None
        bb = f["boundingBox"]
        box = (bb["max"]["x"] - bb["min"]["x"], bb["max"]["y"] - bb["min"]["y"])
        size = model_size_m(meta)
        rot, scale, notes = float(f.get("rotationDeg") or 0), 1.0, []
        status: Status = "built"
        if size is not None and min(box) > 0:
            model = (size[0] * 1000, size[1] * 1000)
            turned = long_axis_rotation(f, box, model, walls)
            if turned is not None:
                rot = turned
                notes.append("xoay trục dài mẫu theo hộp bao")
            scale, fits = fit_scale(box, model)
            if not fits:
                status = "degraded"
                need = f"{footprint_ratio(box, model) * 100:.0f}"
                notes.append(f"mẫu {mid} cần co giãn {need} % để vừa hộp bao, chỉ cho {scale * 100:.0f} %")
        book.add(level, "furniture", fid, status, "minor" if status == "degraded" else "info", "; ".join(notes))
        x, y = to_m(f["centre"])
        return {
            "name": f"Furniture_{fid}",
            "level": level,
            "model": f"models/{mid}.glb",
            "anchor": "bbox",
            "at": [x, y, round(FINISH_FLOOR_M + (f.get("liftMm") or 0) / 1000, 4)],
            "rotZ": round(-math.radians(rot), 6),
            "scale": [scale, scale, scale],
            "props": {
                "license": meta.get("license", ""),
                "author": meta.get("author", ""),
                "source": meta.get("source", ""),
                "prov_model": prov,
                "status": status,
            },
        }
