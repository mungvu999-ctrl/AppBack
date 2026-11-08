"""Snapshot → plan.json orchestration (HOP-DONG-M1 §1, §4, §10). Pure: no IO, no Blender.

Z in prisms/cutters/assemblies/instances is level-local (metres above the level's floor);
the worker adds `levels[].elevation`. The structural slab sits at z ∈ [-0.15, 0].
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import shapely
from shapely.geometry import Point, Polygon

from packages.domain.model_export.assets import AssetResolver
from packages.domain.model_export.defaults import FINISH_FLOOR_M, STRUCTURAL_SLAB_M
from packages.domain.model_export.materials import MaterialResolver, Role
from packages.domain.model_export.plan.openings import openings_plan
from packages.domain.model_export.plan.slabs import slab_plan
from packages.domain.model_export.plan.stairs import stairs_plan
from packages.domain.model_export.plan.suggestions import (
    Summary,
    effective,
    model_picks,
    node_props,
    replaced_furniture,
    suggestions_plan,
)
from packages.domain.model_export.plan.walls import wall_plan
from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.snapshot import fingerprint, snapshot_sha
from packages.domain.model_export.units import GRID, cap_triangles, polygon_rings, polygons_of, to_m

SCHEMA_VERSION = 1
CEILING_T_M = FINISH_FLOOR_M
"""Trần dựng như tấm 20 mm (cùng bề dày sàn hoàn thiện; hợp đồng không nêu riêng)."""


def _prisms(
    geom: Any, name: str, level: str, group: str, kind: str, app_id: str, z0: float, z1: float, faces: dict[str, Any]
) -> list[dict[str, Any]]:
    """One prism per polygon part; parts after the first get `_<k>` suffixes. `caps` = CDT triangles."""
    out: list[dict[str, Any]] = []
    for k, poly in enumerate(polygons_of(geom)):
        rings = polygon_rings(poly)
        out.append(
            {
                "name": name if k == 0 else f"{name}_{k}",
                "level": level,
                "group": group,
                "kind": kind,
                "appId": app_id,
                "rings": rings,
                "caps": cap_triangles(rings),
                "z0": round(z0, 4),
                "z1": round(z1, 4),
                "faceMaterials": {**faces, "sideByRoom": []},
                "props": {"prov_geometry": "document"},
            }
        )
    return out


def _side_by_room(
    ring: list[Any], room_polys: list[tuple[str, Polygon]], material: str, mats: MaterialResolver
) -> list[dict[str, Any]]:
    """Outer-ring edges whose outside neighbour (5 cm probe) is a room get that room's own instance of `material`
    (`<key>@<roomId>`, M2: recolour one room without touching the others)."""
    out: list[dict[str, Any]] = []
    for i, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1], strict=False)):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        probe = Point((a[0] + b[0]) / 2 + dy / length * 0.05, (a[1] + b[1]) / 2 - dx / length * 0.05)
        rid = next((rid for rid, p in room_polys if p.contains(probe)), None)
        if rid is not None:
            out.append({"edgeIndex": i, "ring": 0, "material": mats.room_instance(material, rid)})
    return out


def _wall_prisms(
    wp: Any,
    walls: list[dict[str, Any]],
    room_polys: list[tuple[str, Polygon]],
    level: str,
    mats: MaterialResolver,
    book: RecordBook,
) -> list[dict[str, Any]]:
    """Wall prisms; envelope walls take exterior paint; room-facing edges of every wall take the room's own
    instance of the interior paint."""
    by_id = {w["id"]: w for w in walls}
    out: list[dict[str, Any]] = []
    for piece in wp.pieces:
        wid = piece["id"]
        paint = by_id.get(wid, {}).get("paint") or {}
        role: Role = "wall_partition" if piece["kind"] == "partition" else "wall_interior"
        inner = mats.resolve(paint.get("interior"), role, level, wid, book)
        cap = mats.resolve(None, "wall_cap", level, wid, book)
        envelope = piece["kind"] == "envelope"
        side = mats.resolve(paint.get("exterior"), "wall_exterior", level, wid, book) if envelope else inner
        prisms = _prisms(
            piece["own"],
            f"Wall_{wid}",
            level,
            "Walls",
            "wall",
            wid,
            0.0,
            piece["heightM"],
            {"side": side, "top": cap, "bottom": cap},
        )
        for p in prisms:
            p["faceMaterials"]["sideByRoom"] = _side_by_room(p["rings"][0], room_polys, inner, mats)
        if prisms:
            book.add(level, "wall", wid, "built", "info")
        out.extend(prisms)
    return out


def _fan_cutters(cutters: list[dict[str, Any]], prisms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A wall split into several parts gets the cutter on every part (`Wall_X`, `Wall_X_1`, …)."""
    names = [p["name"] for p in prisms]
    out: list[dict[str, Any]] = []
    for c in cutters:
        targets = [n for n in names if n == c["target"] or n.startswith(c["target"] + "_")] or [c["target"]]
        out.extend({**c, "target": t} for t in targets)
    return out


def _minus(geom: Any, holes: list[Any]) -> Any:
    """`geom - holes` on the 0.1 mm grid."""
    return shapely.difference(geom, shapely.union_all(holes), grid_size=GRID) if holes else geom


def _stairs(
    visible: list[Mapping[str, Any]], mats: MaterialResolver, book: RecordBook
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stair plans per level + slab holes per upper level (stairs_plan decides the ±10 mm link)."""
    plans: dict[str, Any] = {}
    upper_holes: dict[str, list[Any]] = {}
    for i, f in enumerate(visible):
        stairs = [x for x in f["layer"].get("furniture", []) if x.get("kind") == "stair" and not x.get("suggested")]
        if not stairs:
            continue
        upper = visible[i + 1] if i + 1 < len(visible) else None
        sp = stairs_plan(stairs, dict(f), dict(upper) if upper else None, mats, book)
        plans[f["levelId"]] = sp
        if upper is not None and sp.upper_slab_holes:
            upper_holes.setdefault(upper["levelId"], []).extend(sp.upper_slab_holes)
    return plans, upper_holes


def _level(
    f: Mapping[str, Any],
    plan: dict[str, Any],
    mats: MaterialResolver,
    assets: AssetResolver,
    book: RecordBook,
    sp: Any,
    holes: list[Any],
    summary: Summary,
) -> None:
    """Append one visible level's prisms/cutters/assemblies/instances to `plan`."""
    lid, layer, height = f["levelId"], f["layer"], f["heightMm"] / 1000
    for roof in layer.get("roofs", []):
        book.add(lid, "roof", roof.get("id", lid), "skipped", "major", "mái chưa hỗ trợ trong M1")
    walls, rooms = layer.get("walls", []), layer.get("rooms", [])
    wp = wall_plan(walls, lid, height, book)
    if not wp.pieces:
        book.add(lid, "level", lid, "skipped", "critical", "tầng không có tường")
        return
    room_polys = [
        (r["id"], Polygon([to_m(p) for p in r["outline"]])) for r in rooms if len(r.get("outline") or ()) >= 3
    ]
    wall_prisms = _wall_prisms(wp, walls, room_polys, lid, mats, book)
    sl = slab_plan(rooms, wp.union, lid, height, book, holes)
    slab = mats.resolve(None, "slab", lid, lid, book)
    prisms = wall_prisms + _prisms(
        _minus(sl.structural, holes),
        f"Slab_{lid}",
        lid,
        "Slabs",
        "slab",
        lid,
        -STRUCTURAL_SLAB_M,
        0.0,
        {"side": slab, "top": slab, "bottom": slab},
    )
    by_room = {r["id"]: r for r in rooms}
    for fin in sl.finishes:
        rid = fin["roomId"]
        room = by_room[rid]
        key = mats.resolve(room.get("floorMaterial"), "floor", lid, rid, book, fallback=mats.floor_default(room))
        key = mats.room_instance(key, rid)
        prisms += _prisms(
            fin["geom"],
            f"Floor_{rid}",
            lid,
            "Floors",
            "floor",
            rid,
            0.0,
            FINISH_FLOOR_M,
            {"side": key, "top": key, "bottom": key},
        )
        book.add(lid, "room", rid, "built", "info")
    own_holes = list(sp.own_ceiling_holes) if sp else []
    for c in sl.ceilings:
        rid = c["roomId"]
        if mats.is_outdoor(by_room[rid]):
            book.add(lid, "ceiling", rid, "skipped", "info", "sàn ngoài trời, editor không dựng trần")
            continue
        key = mats.resolve(by_room[rid].get("ceilingMaterial"), "ceiling", lid, rid, book)
        prisms += _prisms(
            _minus(c["geom"], own_holes),
            f"Ceiling_{rid}",
            lid,
            "Ceilings",
            "ceiling",
            rid,
            c["z"],
            c["z"] + CEILING_T_M,
            {"side": key, "top": key, "bottom": key},
        )
    cutters, assemblies = openings_plan(layer.get("openings", []), wp, rooms, lid, mats, book)
    plan["prisms"] += prisms
    plan["cutters"] += _fan_cutters(cutters, wall_prisms)
    plan["assemblies"] += assemblies + (list(sp.assemblies) if sp else [])
    _furniture(f, plan, assets, book, summary)
    suggestions_plan(f, wp, sl, mats, assets, book, plan, summary)


def _furniture(
    f: Mapping[str, Any], plan: dict[str, Any], assets: AssetResolver, book: RecordBook, summary: Summary
) -> None:
    """Document furniture; a live base cabinetRun replaces its host box(es), a live modelPick picks the model
    of a host without `model` (records `suggestion:<rule>`, never above `minor`)."""
    lid = f["levelId"]
    picks, replaced = model_picks(f), replaced_furniture(f)
    for item in f["layer"].get("furniture", []):
        fid = item.get("id")
        if item.get("kind") == "stair":
            continue
        if fid in replaced:
            book.add(lid, "furniture", fid, "skipped", "info", f"thay bằng dãy tủ đề xuất {replaced[fid]}")
            continue
        s = picks.get(fid)
        pick = None
        if s is not None:
            p, kind = effective(s), f"suggestion:{s['rule']}"
            mid = p.get("modelId")
            if (item.get("model") or {}).get("src"):
                summary.counts["skipped"] += 1
                book.add(lid, kind, s["id"], "skipped", "info", "đồ chủ đã có mẫu người chọn")
            elif not mid or not assets.has_model(str(mid)):
                summary.counts["skipped"] += 1
                book.add(lid, kind, s["id"], "skipped", "minor", f"mẫu {mid} chưa có trong gói xuất")
            else:
                pick = str(mid)
                summary.counts["built"] += 1
                book.add(lid, kind, s["id"], "built", "info", str(p.get("reasonVi") or ""))
        inst = assets.resolve_furniture(item, lid, book, pick)
        if inst is None:
            continue
        if pick is not None and s is not None:
            inst["props"].update(node_props(s))
        plan["instances"].append(inst)


def build_plan(snapshot: Mapping[str, Any], pack: Mapping[str, Any]) -> dict[str, Any]:
    """Full plan.json for a snapshot. `pack` keys: materials, sceneMaterials, models, furnitureDefaults,
    editorDefaults. No-ref materials follow the editor defaults of `snapshot["sceneStyle"]`."""
    book = RecordBook()
    style = snapshot.get("sceneStyle")
    mats = MaterialResolver(
        pack.get("materials", {}), pack.get("sceneMaterials", {}), pack.get("editorDefaults"), style
    )
    if mats.style_fallback:
        reason = (
            f"phong cách {style} không có trong gói, dùng phong cách mặc định {mats.style_id}"
            if style
            else f"dùng phong cách mặc định {mats.style_id} (dự án chưa chọn phong cách)"
        )
        book.add("", "style", str(mats.style_id), "built", "minor" if style else "info", reason)
    assets = AssetResolver(pack.get("models", {}), pack.get("furnitureDefaults", {}))
    floors = sorted(snapshot["floors"], key=lambda f: (f["order"], f["levelId"]))
    summary = Summary()
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "units": "m",
        "levels": [],
        "materials": {},
        "prisms": [],
        "cutters": [],
        "assemblies": [],
        "instances": [],
        "records": [],
    }
    visible = [f for f in floors if not f["hiddenIn3d"]]
    stair_plans, upper_holes = _stairs(visible, mats, book)
    for f in floors:
        lid = f["levelId"]
        plan["levels"].append(
            {
                "id": lid,
                "name": f.get("name", ""),
                "elevation": f["elevationMm"] / 1000,
                "height": f["heightMm"] / 1000,
                "hidden": bool(f["hiddenIn3d"]),
            }
        )
        if f["hiddenIn3d"]:
            book.add(lid, "level", lid, "skipped", "info", "tầng đang ẩn trong 3D, không dựng")
        else:
            _level(f, plan, mats, assets, book, stair_plans.get(lid), upper_holes.get(lid, []), summary)
    versions = snapshot["versions"]
    plan["materials"] = mats.materials_table()
    plan["records"] = book.to_dicts()
    extras = summary.extras()
    if extras is not None:
        plan["appSuggestions"] = extras
    plan["stamps"] = {
        "snapshotSha": snapshot_sha(snapshot),
        "fingerprint": fingerprint(floors, versions, scene_style=style),
        "compiler": versions.get("compiler", ""),
        "pack": versions.get("pack", ""),
    }
    return plan
