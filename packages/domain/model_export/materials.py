"""Material resolution: document ref → plan.json material key (HOP-DONG-M1 §3, revised 07/11).

No ref in the document → the editor's own default from the pack's `editor-defaults.json` for the
project style (`prov=editor-default`). Neutral colors only for roles the editor never sets (slab, wall
cap, door parts…; `prov=default`) and for unknown/missing refs (+ degraded major).
Known limit: the browser-only local style preview and AI concept are not in the snapshot, so not applied.
The editor has no glass/low wall kind in the document (`kind` is loadBearing|partition|envelope), so
`glassWall` is unused.

Pack format (`materials.json`, `scene-materials.json`):
`{id: {label, color: "#rrggbb", roughness, metalness, repeatX, repeatM?, maps: {base?, normal?, rough?}, hasTexture}}`.
Colors go out as sRGB floats 0..1. `repeatM` (real-world metres per texture tile) wins over `1 / repeatX`.

M2 (global defaults, versioned by `compiler_version`): `room_instance` gives a room its own copy `<key>@<roomId>`
of a floor/wall finish (same image files, so recipients recolour one room); `tune_materials` turns glass into
transmission, fabrics get sheen and flat non-metals a roughness-noise flag (`roughVar`) the worker draws.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from packages.domain.model_export import defaults
from packages.domain.model_export.defaults import NEUTRAL_COLORS, NEUTRAL_LABELS, NEUTRAL_PBR
from packages.domain.model_export.records import RecordBook

Role = Literal[
    "wall_interior",
    "wall_exterior",
    "wall_cap",
    "floor",
    "ceiling",
    "slab",
    "frame",
    "glass",
    "metal",
    "door_leaf",
    "stone",
    "wall_partition",
    "window_frame",
    "window_glass",
    "stair_tread",
    "stair_side",
]


GLASS_ROLES = ("glass", "window_glass")
FABRIC = re.compile(r"fabric|linen|velvet|wool|cotton|textile|curtain|carpet|rug|cloth")
"""Vật liệu vải nhận diện theo khoá/ref/nhãn (cùng tinh thần regex mẫu đồ của worker)."""


def hex_rgb(color: str) -> list[float]:
    """`#rrggbb` → [r, g, b] in 0..1, rounded to 4 decimals."""
    h = color.lstrip("#")
    return [round(int(h[i : i + 2], 16) / 255, 4) for i in (0, 2, 4)]


def _transparent(entry: Mapping[str, Any]) -> bool:
    """Pack entry drawn see-through by the editor (`transparent` or `opacity < 1`)."""
    return bool(entry.get("transparent")) or float(entry.get("opacity", 1) or 1) < 1


class MaterialResolver:
    """Resolves refs once per key; collects the plan.json `materials` table."""

    def __init__(
        self,
        pack_materials: Mapping[str, Mapping[str, Any]],
        scene_materials: Mapping[str, Mapping[str, Any]],
        editor_defaults: Mapping[str, Any] | None = None,
        scene_style: str | None = None,
    ) -> None:
        """Pack tables keyed by id; `editor_defaults` = editor-defaults.json, `scene_style` = project style."""
        ed = editor_defaults or {}
        styles = ed.get("styles") or {}
        self.style_id = scene_style if scene_style in styles else ed.get("defaultStyle")
        self.style_fallback = bool(styles) and scene_style not in styles
        style = styles.get(self.style_id) or {}
        self._ed = ed
        self._style = style
        self._role_refs: dict[str, str | None] = {
            "wall_interior": (style.get("wall") or {}).get("interior"),
            "wall_exterior": (style.get("wall") or {}).get("exterior"),
            "wall_partition": style.get("partition"),
            "ceiling": style.get("ceiling"),
            "window_frame": (ed.get("window") or {}).get("frame"),
            "window_glass": (ed.get("window") or {}).get("glass"),
            "stair_tread": style.get("stairTread"),
            "stair_side": ed.get("stairSide"),
        }
        self._library = pack_materials
        self._scene = scene_materials
        self._table: dict[str, dict[str, Any]] = {}

    def _neutral(self, role: str, key: str, label: str, editor_ref: str | None) -> dict[str, Any]:
        """Neutral flat entry for `role`."""
        rough, metal, trans = NEUTRAL_PBR.get(role, (0.9, 0.0, 0.0))
        return {
            "label": label,
            "editorRef": editor_ref,
            "color": hex_rgb(NEUTRAL_COLORS[role]),
            "roughness": rough,
            "metallic": metal,
            "transmission": trans,
            "sizeM": 1.0,
            "maps": {"base": None, "normal": None, "rough": None},
            "prov": "default",
        }

    @staticmethod
    def _from_pack(entry: Mapping[str, Any], ref: str, drop_maps: bool) -> dict[str, Any]:
        """plan.json entry from a pack entry; `drop_maps` → flat color."""
        maps = entry.get("maps") or {}
        repeat = entry.get("repeatX") or 1.0
        size = float(entry["repeatM"]) if entry.get("repeatM") else round(1 / repeat, 6)
        return {
            "label": entry.get("label", ref),
            "editorRef": ref,
            "color": hex_rgb(entry.get("color", "#cccccc")),
            "roughness": float(entry.get("roughness", 0.9)),
            "metallic": float(entry.get("metalness", 0.0)),
            "transmission": float(entry.get("transmission", 0.0)),
            "sizeM": size,
            "maps": {k: (None if drop_maps else maps.get(k)) for k in ("base", "normal", "rough")},
            "prov": "document",
            **({"transparent": True} if _transparent(entry) else {}),
        }

    def floor_finish(self, room: Mapping[str, Any]) -> str | None:
        """Effective finish as the editor picks it: `finish ?? usageFinish[usage]`."""
        return room.get("finish") or (self._ed.get("usageFinish") or {}).get(room.get("usage"))

    def is_outdoor(self, room: Mapping[str, Any]) -> bool:
        """Editor draws no ceiling over the outdoor finish (`outdoorFinishNoCeiling`)."""
        outdoor = self._ed.get("outdoorFinishNoCeiling")
        return outdoor is not None and self.floor_finish(room) == outdoor

    def floor_default(self, room: Mapping[str, Any]) -> str | None:
        """Editor default floor ref: `styles.floor[finish ?? usageFinish[usage]]`."""
        return (self._style.get("floor") or {}).get(self.floor_finish(room) or "")

    def resolve(
        self,
        ref: str | None,
        role: Role,
        level: str,
        entity_id: str,
        book: RecordBook,
        fallback: str | None = None,
    ) -> str:
        """Material key for `ref` on `role`.

        No ref → `fallback` or the editor default for `role` (prov editor-default), else neutral (prov default).
        A ref the pack can't honour → neutral color + degraded major.
        """
        if not ref:
            editor_ref = fallback or self._role_refs.get(role)
            if editor_ref:
                new = editor_ref not in self._table
                key = self._resolve_ref(editor_ref, role, level, entity_id, book)
                if new and self._table[key]["prov"] == "document":
                    self._table[key]["prov"] = "editor-default"
                return key
            key = f"default:{role}"
            self._table.setdefault(key, self._neutral(role, key, NEUTRAL_LABELS[role], None))
            return key
        return self._resolve_ref(ref, role, level, entity_id, book)

    def _resolve_ref(self, ref: str, role: str, level: str, entity_id: str, book: RecordBook) -> str:
        """Pack lookup for an explicit ref (document or editor default)."""
        prefix, _, mid = ref.partition(":")
        table = {"library": self._library, "scene": self._scene}.get(prefix)
        entry = table.get(mid) if table is not None else None
        if entry is None:
            reason = (
                f"vật liệu {ref} không có trong gói xuất, dùng màu trung tính"
                if table is not None
                else f"tham chiếu vật liệu lạ {ref}, dùng màu trung tính"
            )
            book.add(level, "material", entity_id, "degraded", "major", reason)
            self._table.setdefault(ref, self._neutral(role, ref, ref, ref))
            return ref
        missing_maps = bool(entry.get("hasTexture")) and not (entry.get("maps") or {}).get("base")
        if missing_maps:
            book.add(level, "material", entity_id, "degraded", "major", f"thiếu ảnh vật liệu {mid}")
        self._table.setdefault(ref, self._from_pack(entry, ref, missing_maps))
        return ref

    def family(self, name: str) -> str | None:
        """Ref of a style material family (`styles[style].families[name]`: cabinetry, countertop, rug…)."""
        return (self._style.get("families") or {}).get(name)

    def room_instance(self, key: str, room_id: str) -> str:
        """Own material `<key>@<roomId>` for one room's finish: same entry (same image files), distinct material."""
        inst = f"{key}@{room_id}"
        if inst not in self._table:
            self._table[inst] = {**self._table[key], "instanceOf": key, "roomId": room_id}
        return inst

    def materials_table(self) -> dict[str, dict[str, Any]]:
        """Every key handed out so far, sorted, with the global M1-M3 defaults applied."""
        return tune_materials(dict(sorted(self._table.items())))


def _is_glass(key: str, spec: Mapping[str, Any]) -> bool:
    """Glass: a transparent pack entry, any transmission already, a glass role default, or `glass` in the key/ref."""
    ref = str(spec.get("editorRef") or "")
    if spec.get("transparent") or spec.get("transmission", 0) > 0 or "glass" in key + ref:
        return True
    return any(key == f"default:{r}" for r in GLASS_ROLES)


def tune_materials(table: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """M1 glass → transmission 1, roughness ≤ 0.05; M2 fabric → sheen; M3 flat non-metal → `roughVar` (new dicts)."""
    out: dict[str, dict[str, Any]] = {}
    for key, spec in table.items():
        spec = dict(spec)
        if _is_glass(key, spec):
            spec["transmission"] = defaults.GLASS_TRANSMISSION
            spec["roughness"] = min(spec["roughness"], defaults.GLASS_MAX_ROUGHNESS)
        else:
            if FABRIC.search(f"{key} {spec.get('editorRef') or ''} {spec.get('label') or ''}".lower()):
                spec["sheen"] = defaults.FABRIC_SHEEN
            flat = not (spec.get("maps") or {}).get("rough")
            if flat and spec["metallic"] < 0.5 and spec["roughness"] >= defaults.ROUGHNESS_NOISE_MIN:
                spec["roughVar"] = defaults.ROUGHNESS_NOISE
        out[key] = spec
    return out
