"""Material resolution: document ref → plan.json material key (HOP-DONG-M1 §3, revised 07/11).

No ref in the document → the editor's own default from the pack's `editor-defaults.json` for the
project style (`prov=editor-default`). Neutral colors only for roles the editor never sets (slab, wall
cap, door parts…; `prov=default`) and for unknown/missing refs (+ degraded major).
Known limit: the browser-only local style preview and AI concept are not in the snapshot, so not applied.
The editor has no glass/low wall kind in the document (`kind` is loadBearing|partition|envelope), so
`glassWall` is unused.

Pack format (`materials.json`, `scene-materials.json`):
`{id: {label, color: "#rrggbb", roughness, metalness, repeatX, maps: {base?, normal?, rough?}, hasTexture}}`.
Colors go out as sRGB floats 0..1.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

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


def hex_rgb(color: str) -> list[float]:
    """`#rrggbb` → [r, g, b] in 0..1, rounded to 4 decimals."""
    h = color.lstrip("#")
    return [round(int(h[i : i + 2], 16) / 255, 4) for i in (0, 2, 4)]


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
        return {
            "label": entry.get("label", ref),
            "editorRef": ref,
            "color": hex_rgb(entry.get("color", "#cccccc")),
            "roughness": float(entry.get("roughness", 0.9)),
            "metallic": float(entry.get("metalness", 0.0)),
            "transmission": float(entry.get("transmission", 0.0)),
            "sizeM": round(1 / repeat, 6),
            "maps": {k: (None if drop_maps else maps.get(k)) for k in ("base", "normal", "rough")},
            "prov": "document",
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

    def materials_table(self) -> dict[str, dict[str, Any]]:
        """Every key handed out so far, sorted."""
        return dict(sorted(self._table.items()))
