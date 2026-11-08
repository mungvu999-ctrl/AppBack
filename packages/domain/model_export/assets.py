"""Furniture model resolution against the export pack (HOP-DONG-M1 §1 "Đồ đạc", §8).

Pack: `models.json` `{id: {file, license, author, source, sizeMm: [x, y, z] | {x, y, z}}}`,
`furniture-defaults.json` `{"kind|variant" | "kind": modelId}`.
Assumptions: `boundingBox` is the footprint before rotation (width along x, depth along y, mm);
`rotationDeg` turns in the editor's Y-down frame, so it flips sign in the export's Y-up frame;
`at` z is level-local (`liftMm`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.units import to_m

_SRC = re.compile(r"^/items/([a-z0-9_-]+)/model\.glb$")
SCALE_TOLERANCE = 0.3
"""Uniform scale off by more than ±30 % → degraded minor (never clamped silently)."""


def _size_mm(size: Any) -> tuple[float, float, float] | None:
    """`sizeMm` as (x, y, z); list or dict accepted."""
    if isinstance(size, Mapping):
        return (size.get("x", 0), size.get("y", 0), size.get("z", 0))
    if isinstance(size, (list, tuple)) and len(size) == 3:
        return tuple(size)
    return None


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
        self, f: Mapping[str, Any], level: str, book: RecordBook, pick: str | None = None
    ) -> dict[str, Any] | None:
        """Instance dict, or None (recorded) when it can't be placed with its own model; `pick` = modelPick model."""
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
        target = (bb["max"]["x"] - bb["min"]["x"], bb["max"]["y"] - bb["min"]["y"], f.get("heightMm") or 0)
        size = _size_mm(meta.get("sizeMm")) or (0, 0, 0)
        ratios = [t / s for t, s in zip(target, size, strict=False) if t > 0 and s > 0]
        scale = round(min(ratios), 4) if ratios else 1.0
        status = "built"
        if abs(scale - 1) > SCALE_TOLERANCE:
            status = "degraded"
            pct = f"{scale * 100:.0f}".replace(".", ",")
            book.add(level, "furniture", fid, "degraded", "minor", f"mẫu {mid} phải co giãn {pct} % để vừa hộp bao")
        else:
            book.add(level, "furniture", fid, "built", "info")
        x, y = to_m(f["centre"])
        return {
            "name": f"Furniture_{fid}",
            "level": level,
            "model": f"models/{mid}.glb",
            "at": [x, y, (f.get("liftMm") or 0) / 1000],
            "rotZ": round(-math.radians(f.get("rotationDeg") or 0), 6),
            "scale": [scale, scale, scale],
            "props": {
                "license": meta.get("license", ""),
                "author": meta.get("author", ""),
                "source": meta.get("source", ""),
                "prov_model": prov,
                "status": status,
            },
        }
