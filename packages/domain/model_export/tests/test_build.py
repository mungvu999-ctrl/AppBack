"""build_plan orchestration with the real plan/* modules (HOP-DONG-M1 §1, §10)."""

from types import SimpleNamespace
from typing import Any

import pytest
from shapely.geometry import box

from packages.domain.model_export import build as build_mod
from packages.domain.model_export.build import _fan_cutters, build_plan
from packages.domain.model_export.plan.schema import validate_plan
from packages.domain.model_export.quality import quality_of
from packages.domain.model_export.snapshot import build_snapshot

VERSIONS = {"compiler": "m1-01.test", "pack": "p1", "schema": 1}
PACK = {
    "materials": {"oak": {"label": "Gỗ", "color": "#a07850", "repeatX": 1, "maps": {}, "hasTexture": False}},
    "sceneMaterials": {"wood": {"color": "#886644"}},
    "models": {"sofa": {"sizeMm": [2000, 900, 800], "license": "CC0"}},
    "furnitureDefaults": {"other": "sofa"},
}


def _wall(wid: str, x0: int, y0: int, x1: int, y1: int, **kw: Any) -> dict[str, Any]:
    """Envelope wall dict, 200 mm thick."""
    return {
        "id": wid,
        "kind": "envelope",
        "thicknessMm": 200,
        "heightMm": 3000,
        "centreline": {"start": {"x": x0, "y": y0}, "end": {"x": x1, "y": y1}},
        **kw,
    }


def _box_layer(lid: str, **extra: Any) -> dict[str, Any]:
    """4x4 m room enclosed by four envelope walls, one door, one sofa."""
    walls = [
        _wall("W-1", 0, 0, 4000, 0, paint={"interior": "library:oak", "exterior": "scene:wood"}),
        _wall("W-2", 4000, 0, 4000, 4000),
        _wall("W-3", 4000, 4000, 0, 4000),
        _wall("W-4", 0, 4000, 0, 0),
    ]
    rooms = [
        {
            "id": "R-1",
            "outline": [{"x": 0, "y": 0}, {"x": 4000, "y": 0}, {"x": 4000, "y": 4000}, {"x": 0, "y": 4000}],
            "finish": "wood",
            "ceilingMaterial": "library:missing",
        }
    ]
    openings = [
        {
            "id": "D-1",
            "wallId": "W-1",
            "kind": "door",
            "offsetMm": 1000,
            "widthMm": 900,
            "heightMm": 2100,
            "sillHeightMm": 0,
            "swing": "left",
        }
    ]
    furniture = [
        {
            "id": "F-1",
            "kind": "other",
            "centre": {"x": 2000, "y": 2000},
            "rotationDeg": 0,
            "boundingBox": {"min": {"x": 0, "y": 0}, "max": {"x": 2000, "y": 900}},
        }
    ]
    return {"walls": walls, "openings": openings, "rooms": rooms, "furniture": furniture, "roofs": [], **extra}


def _floor(
    lid: str, order: int, elev: int, hidden: bool = False, layer: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Snapshot floor row."""
    return {
        "levelId": lid,
        "name": lid,
        "order": order,
        "elevationMm": elev,
        "heightMm": 3300,
        "hiddenIn3d": hidden,
        "revision": 1,
        "layer": layer if layer is not None else _box_layer(lid),
    }


def test_build_plan__full_snapshot_valid() -> None:
    """One built level + hidden level + wall-less level with a roof; plan validates; blocked quality."""
    stair = {
        "id": "F-9",
        "kind": "stair",
        "variant": "spiral",
        "centre": {"x": 1000, "y": 1000},
        "rotationDeg": 0,
        "boundingBox": {"min": {"x": 0, "y": 0}, "max": {"x": 900, "y": 2500}},
    }
    l1 = _box_layer("L-1")
    l1["furniture"] = [*l1["furniture"], stair]
    snap = build_snapshot(
        "prj_1",
        [
            _floor("L-1", 0, 0, layer=l1),
            _floor("L-2", 1, 3300, hidden=True),
            _floor("L-3", 2, 6600, layer={"walls": [], "roofs": [{"id": "RF-1"}]}),
        ],
        VERSIONS,
    )
    plan = build_plan(snap, PACK)
    validate_plan(plan)
    assert [lv["id"] for lv in plan["levels"]] == ["L-1", "L-2", "L-3"]
    assert plan["levels"][1]["hidden"]
    kinds = {p["kind"] for p in plan["prisms"]}
    assert kinds == {"wall", "slab", "floor", "ceiling"}
    slab = next(p for p in plan["prisms"] if p["kind"] == "slab")
    assert (slab["z0"], slab["z1"]) == (-0.15, 0.0)
    w1 = next(p for p in plan["prisms"] if p["name"] == "Wall_W-1")
    assert w1["faceMaterials"]["side"] == "scene:wood"
    assert {s["material"] for s in w1["faceMaterials"]["sideByRoom"]} == {"library:oak@R-1"}
    floor = next(p for p in plan["prisms"] if p["kind"] == "floor")
    assert floor["faceMaterials"]["top"] == "default:floor@R-1"
    assert plan["cutters"]
    assert plan["cutters"][0]["target"] == "Wall_W-1"
    assert plan["instances"][0]["model"] == "models/sofa.glb"
    reasons = {(r["level"], r["kind"], r["severity"], r["reason"]) for r in plan["records"]}
    assert ("L-2", "level", "info", "tầng đang ẩn trong 3D, không dựng") in reasons
    assert ("L-3", "level", "critical", "tầng không có tường") in reasons
    assert ("L-3", "roof", "major", "mái chưa hỗ trợ trong M1") in reasons
    assert any(r[1] == "stair" and r[2] == "major" for r in reasons)
    assert quality_of(plan["records"]) == "blocked"
    assert plan["stamps"]["compiler"] == "m1-01.test"
    assert len(plan["stamps"]["fingerprint"]) == 64


def test_build_plan__input_order_independent() -> None:
    """Same snapshot content in another floor order → identical plan."""
    floors = [_floor("L-1", 0, 0), _floor("L-2", 1, 3300)]
    a = build_plan(build_snapshot("p", floors, VERSIONS), PACK)
    b = build_plan(build_snapshot("p", list(reversed(floors)), VERSIONS), PACK)
    assert a == b
    assert quality_of(a["records"]) == "degraded"  # library:missing ceiling ref


def test_build_plan__stair_holes_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stair upper-slab holes cut the upper level's structural slab; own holes cut own ceilings."""
    calls = []

    def fake_stairs(furniture: Any, level: Any, upper: Any, materials: Any, book: Any) -> SimpleNamespace:
        calls.append((level["levelId"], upper and upper["levelId"]))
        return SimpleNamespace(
            assemblies=[{"name": "Stair_F-9", "boxes": []}],
            upper_slab_holes=[box(1, -3, 2, -1)],
            own_ceiling_holes=[box(1, -3, 2, -1)],
        )

    monkeypatch.setattr(build_mod, "stairs_plan", fake_stairs)
    stair = {"id": "F-9", "kind": "stair", "variant": "straight"}
    l1 = _box_layer("L-1")
    l1["furniture"] = [stair]
    plan = build_plan(build_snapshot("p", [_floor("L-1", 0, 0, layer=l1), _floor("L-2", 1, 3300)], VERSIONS), PACK)
    assert calls == [("L-1", "L-2")]
    upper_slab = next(p for p in plan["prisms"] if p["name"] == "Slab_L-2")
    own_ceiling = next(p for p in plan["prisms"] if p["name"] == "Ceiling_R-1" and p["level"] == "L-1")
    assert len(upper_slab["rings"]) == 2
    assert len(own_ceiling["rings"]) == 2
    assert any(a["name"] == "Stair_F-9" for a in plan["assemblies"])
    lone = build_plan(build_snapshot("p", [_floor("L-1", 0, 0, layer=l1)], VERSIONS), PACK)
    assert calls[-1] == ("L-1", None)
    assert lone["prisms"]


def test_fan_cutters__split_wall_parts() -> None:
    """A cutter on a split wall is copied to every part; unknown target kept as is."""
    prisms = [{"name": "Wall_W-1"}, {"name": "Wall_W-1_1"}, {"name": "Wall_W-10"}]
    out = _fan_cutters([{"target": "Wall_W-1", "appId": "D"}, {"target": "Wall_X", "appId": "E"}], prisms)
    assert [c["target"] for c in out] == ["Wall_W-1", "Wall_W-1_1", "Wall_X"]


ED = {
    "defaultStyle": "am-ap",
    "usageFinish": {"bedroom": "wood", "loggia": "decking"},
    "outdoorFinishNoCeiling": "decking",
    "window": {"frame": "library:oak", "glass": "scene:wood"},
    "stairSide": "library:oak",
    "styles": {
        "am-ap": {
            "floor": {"wood": "library:oak", "decking": "scene:wood"},
            "wall": {"interior": "scene:wood", "exterior": "library:oak"},
            "partition": "library:oak",
            "ceiling": "scene:wood",
            "stairTread": "library:oak",
        },
        "go-am": {"floor": {}, "wall": {}},
    },
}


def _ed_layer(usage: str, finish: str | None = None) -> dict[str, Any]:
    """Box layer: unpainted walls (one partition), room with `usage`/`finish`, no door/sofa."""
    layer = _box_layer("L-1")
    layer["walls"] = [{k: v for k, v in w.items() if k != "paint"} for w in layer["walls"]]
    layer["walls"][1]["kind"] = "partition"
    layer["rooms"][0] = {**layer["rooms"][0], "usage": usage, "finish": finish, "ceilingMaterial": None}
    layer["openings"], layer["furniture"] = [], []
    return layer


def test_build_plan__editor_defaults_default_style() -> None:
    """No sceneStyle → defaultStyle + info record; floor by usageFinish; partition/exterior/ceiling refs."""
    snap = build_snapshot("p", [_floor("L-1", 0, 0, layer=_ed_layer("bedroom"))], VERSIONS)
    plan = build_plan(snap, {**PACK, "editorDefaults": ED})
    validate_plan(plan)
    by_name = {p["name"]: p for p in plan["prisms"]}
    assert by_name["Floor_R-1"]["faceMaterials"]["top"] == "library:oak@R-1"
    assert by_name["Ceiling_R-1"]["faceMaterials"]["top"] == "scene:wood"
    assert by_name["Wall_W-1"]["faceMaterials"]["side"] == "library:oak"
    assert by_name["Wall_W-2"]["faceMaterials"]["side"] == "library:oak"
    assert plan["materials"]["library:oak"]["prov"] == "editor-default"
    style = [r for r in plan["records"] if r["kind"] == "style"]
    assert style == [
        {
            "level": "",
            "kind": "style",
            "id": "am-ap",
            "status": "built",
            "severity": "info",
            "reason": "dùng phong cách mặc định am-ap (dự án chưa chọn phong cách)",
        }
    ]
    assert quality_of(plan["records"]) == "ready"


def test_build_plan__outdoor_no_ceiling_and_style_choice() -> None:
    """Outdoor finish → no ceiling (info); chosen style used; unknown style → minor + default; style in fingerprint."""
    floors = [_floor("L-1", 0, 0, layer=_ed_layer("loggia"))]
    pack = {**PACK, "editorDefaults": ED}
    plan = build_plan(build_snapshot("p", floors, VERSIONS, scene_style="go-am"), pack)
    assert not any(p["kind"] == "ceiling" for p in plan["prisms"])
    assert any(r["kind"] == "ceiling" and r["status"] == "skipped" for r in plan["records"])
    assert not any(r["kind"] == "style" for r in plan["records"])
    floor = next(p for p in plan["prisms"] if p["kind"] == "floor")
    assert floor["faceMaterials"]["top"] == "default:floor@R-1"
    other = build_plan(build_snapshot("p", floors, VERSIONS, scene_style="nope"), pack)
    rec = next(r for r in other["records"] if r["kind"] == "style")
    assert rec["severity"] == "minor"
    assert rec["reason"] == "phong cách nope không có trong gói, dùng phong cách mặc định am-ap"
    assert other["stamps"]["fingerprint"] != plan["stamps"]["fingerprint"]
