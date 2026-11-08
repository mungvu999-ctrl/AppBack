"""Lớp đề xuất trong compiler (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.5, §6) + vật liệu toàn cục M1-M3 (M2-QL)."""

import copy
import json
from typing import Any

import pytest
from shapely.geometry import Polygon

from packages.domain.model_export.build import build_plan
from packages.domain.model_export.materials import MaterialResolver, Role, tune_materials
from packages.domain.model_export.plan import suggestions as sg
from packages.domain.model_export.plan.schema import validate_plan
from packages.domain.model_export.quality import quality_of
from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.snapshot import build_snapshot, snapshot_sha, suggestion_keys
from packages.domain.model_export.tests.test_build import VERSIONS, _box_layer, _floor

SHA = "a" * 64
PACK: dict[str, Any] = {
    "materials": {
        "oak": {"label": "Gỗ", "color": "#a07850", "repeatX": 2, "maps": {"base": "t/oak.jpg"}, "hasTexture": True},
        "tile": {"label": "Gạch", "color": "#eeeeee", "repeatM": 0.6, "maps": {}, "hasTexture": False},
        "fabric-linen": {"label": "Vải lanh", "color": "#eeeadd", "roughness": 0.8},
        "metal-chrome": {"label": "Inox", "color": "#cccccc", "metalness": 1, "roughness": 0.2},
    },
    "sceneMaterials": {
        "mat_glass": {"label": "Kính", "color": "#ddeeff", "roughness": 0.3, "opacity": 0.3},
        "wood": {"color": "#886644"},
    },
    "models": {
        "sofa": {"sizeMm": {"w": 2000, "d": 900, "h": 800}, "license": "CC0", "author": "A", "source": "S"},
        "lamp": {"sizeMm": [400, 400, 900], "license": "CC-BY"},
        "nosize": {"license": "CC0"},
    },
    "furnitureDefaults": {"other": "sofa", "kitchenCabinet": "sofa"},
}


def _s(sid: str, rule: str, kind: str, host: dict[str, str], params: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """Stored suggestion record (wire form)."""
    return {
        "id": f"DRESS-{sid}",
        "rule": rule,
        "ruleVersion": 1,
        "kind": kind,
        "hostRef": host,
        "params": params,
        "state": "active",
        "createdRevision": 1,
        **kw,
    }


WINDOW = {
    "id": "O-1",
    "wallId": "W-3",
    "kind": "window",
    "offsetMm": 1500,
    "widthMm": 1000,
    "heightMm": 1400,
    "sillHeightMm": 900,
    "swing": "sliding",
}
CABINET = {
    "id": "F-2",
    "kind": "kitchenCabinet",
    "centre": {"x": 1400, "y": 3600},
    "rotationDeg": 0,
    "boundingBox": {"min": {"x": 400, "y": 3300}, "max": {"x": 2400, "y": 3900}},
}
BASE_RUN = {"gapMm": 4, "plinthMm": 100, "countertopMm": 40}
"""F2 params copied into every base run record (rule_params.json 2026.11-1)."""
LINE = {"start": {"x": 2400, "y": 3900}, "end": {"x": 400, "y": 3900}}
"""Back line of the kitchen run: direction -x, left normal (0, -1) points into the room."""


def suggestions() -> list[dict[str, Any]]:
    """One live suggestion of every buildable kind + modelPick + disabled/stale/adopted ones."""
    run = {"lengthMm": 2000, "moduleMm": 600, "material": "library:oak", **LINE}
    return [
        _s(
            "R1-R-1-W-2",
            "R1",
            "skirting",
            {"room": "R-1", "wall": "W-2"},
            {"side": "left", "spansMm": [[100, 3900]], "heightMm": 90, "thicknessMm": 12, "material": "library:oak"},
        ),
        _s(
            "R2-R-1-W-4",
            "R2",
            "wallTile",
            {"room": "R-1", "wall": "W-4"},
            {
                "side": "left",
                "spansMm": [[100, 1500]],
                "sillSpansMm": [[1500, 2500, 900]],
                "heightMm": 2400,
                "thicknessMm": 8,
                "tileMm": 300,
                "material": "library:tile",
            },
        ),
        _s("R3-R-1", "R3", "cornice", {"room": "R-1"}, {"heightMm": 50, "depthMm": 30}),
        _s("C1-O-1", "C1", "casing", {"opening": "O-1"}, {"widthMm": 70, "thicknessMm": 16, "material": "library:oak"}),
        _s("C2-O-1", "C2", "sash", {"opening": "O-1"}, {"frameWidthMm": 40, "frameDepthMm": 45}),
        _s(
            "R4-R-1-e0",
            "R4",
            "railing",
            {"room": "R-1"},
            {
                "edgeIndex": 0,
                "start": {"x": 0, "y": 0},
                "end": {"x": 4000, "y": 0},
                "spansMm": [[2200, 3800]],
                "heightMm": 1100,
                "handrailMm": 50,
                "glassMm": 12,
                "postSpacingMaxMm": 1200,
                "metalMaterial": "library:metal-chrome",
                "glassMaterial": "scene:mat_glass",
            },
        ),
        _s(
            "F2-F-2-base",
            "F2",
            "cabinetRun",
            {"furniture": "F-2"},
            {"slot": "base", "depthMm": 600, "bottomMm": 0, "topMm": 860, "mergedIds": ["F-3"], **run, **BASE_RUN},
        ),
        _s(
            "F3-F-2-upper",
            "F3",
            "cabinetRun",
            {"furniture": "F-2"},
            {"slot": "upper", "depthMm": 350, "bottomMm": 1450, "topMm": 2150, "gapMm": 4, **run},
        ),
        *[
            _s(
                f"D2-O-1-{side}",
                "D2",
                "curtain",
                {"opening": "O-1", "room": "R-1"},
                {
                    "side": side,
                    "widthMm": 450,
                    "extMm": 300,
                    "topMm": 2930,
                    "offsetMm": 120,
                    "pleatMm": 80,
                    "material": "library:fabric-linen",
                },
            )
            for side in ("L", "R")
        ],
        _s(
            "D1-R-1",
            "D1",
            "rug",
            {"room": "R-1"},
            {"centre": {"x": 2000, "y": 2000}, "sizeMm": [2300, 1600], "rotationDeg": 30, "thicknessMm": 12},
        ),
        _s(
            "L1-R-1",
            "L1",
            "ceilingLight",
            {"room": "R-1"},
            {"centre": {"x": 2000, "y": 2000}, "fixture": "panel", "sizeMm": [300, 300, 40]},
        ),
        _s(
            "L1-R-1-lamp",
            "L1",
            "ceilingLight",
            {"room": "R-1"},
            {"centre": {"x": 1000, "y": 1000}, "fixture": "pendant", "modelId": "lamp"},
        ),
        _s(
            "P1-R-1-sofa",
            "P1",
            "item",
            {"room": "R-1"},
            {
                "furnitureKind": "chair",
                "modelId": "sofa",
                "centre": {"x": 2000, "y": 1000},
                "sizeMm": [1900, 860, 760],
                "rotationDeg": 90,
                "liftMm": 0,
            },
        ),
        _s(
            "F1-F-1",
            "F1",
            "modelPick",
            {"furniture": "F-1"},
            {"modelId": "lamp", "scale": 1.0, "reasonVi": "hộp → đèn"},
        ),
        _s(
            "D4-R-1",
            "D4",
            "item",
            {"room": "R-1"},
            {
                "furnitureKind": "other",
                "modelId": "sofa",
                "centre": {"x": 1, "y": 1},
                "sizeMm": [1, 1, 1],
                "rotationDeg": 0,
            },
            state="disabled",
        ),
        _s(
            "D5-R-1",
            "D5",
            "item",
            {"room": "R-1"},
            {
                "furnitureKind": "other",
                "modelId": "sofa",
                "centre": {"x": 1, "y": 1},
                "sizeMm": [1, 1, 1],
                "rotationDeg": 0,
            },
            stale={"since": 3, "reasonVi": "phòng đã đổi"},
        ),
        _s(
            "P2-R-1-L",
            "P2",
            "item",
            {"room": "R-1"},
            {
                "furnitureKind": "other",
                "modelId": "sofa",
                "centre": {"x": 1, "y": 1},
                "sizeMm": [1, 1, 1],
                "rotationDeg": 0,
            },
            state="adopted",
            adoptedAs="F-9",
        ),
    ]


def _layer() -> dict[str, Any]:
    """Box room with a window, the kitchen box and its duplicate, no broken refs (quality `ready` before M2)."""
    layer = _box_layer("L-1")
    layer["rooms"][0]["ceilingMaterial"] = None
    layer["openings"] = [*layer["openings"], WINDOW]
    dup = {**CABINET, "id": "F-3"}
    layer["furniture"] = [*layer["furniture"], CABINET, dup]
    return layer


def _plan(items: list[dict[str, Any]] | None = None, pack: dict[str, Any] = PACK) -> dict[str, Any]:
    """Plan of one level carrying `items` (default: every kind)."""
    floor = _floor("L-1", 0, 0, layer=_layer())
    floor["suggestions"] = suggestions() if items is None else items
    floor["suggestionsMeta"] = {"ruleset": "2026.11-1", "rulesetSha": SHA, "basisRevision": 1}
    return build_plan(build_snapshot("p", [floor], VERSIONS), pack)


def _by_sid(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every node (prism, assembly, box, instance) carrying a suggestion id, grouped by it."""
    out: dict[str, list[dict[str, Any]]] = {}
    nodes = [*plan["prisms"], *plan["assemblies"], *plan["instances"]]
    nodes += [b for a in plan["assemblies"] for b in a["boxes"]]
    for n in nodes:
        sid = (n.get("props") or {}).get("suggestionId")
        if sid:
            out.setdefault(sid, []).append(n)
    return out


def _sug_records(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Suggestion records by id."""
    return {r["id"]: r for r in plan["records"] if r["kind"].startswith("suggestion:")}


def test_disabled_stale_adopted_not_built() -> None:
    """disabled/stale/adopted build nothing; disabled+stale listed in scene extras; counts per outcome."""
    plan = _plan()
    validate_plan(plan)
    built = _by_sid(plan)
    for sid in ("DRESS-D4-R-1", "DRESS-D5-R-1", "DRESS-P2-R-1-L"):
        assert sid not in built
        assert sid not in _sug_records(plan)
    extras = plan["appSuggestions"]
    assert extras["rulesetSha"] == SHA
    assert extras["disabled"] == ["DRESS-D4-R-1", "DRESS-D5-R-1"]
    assert extras["counts"] == {"adopted": 1, "built": 15, "disabled": 1, "stale": 1}
    kinds = {sid.split("-")[1] for sid in built}
    assert kinds == {"R1", "R2", "R3", "C1", "C2", "R4", "F2", "F3", "D2", "D1", "L1", "P1", "F1"}


def test_override_applied() -> None:
    """`{**params, **override}`: height and material of the override win."""
    items = suggestions()[:1]
    items[0] = {**items[0], "state": "overridden", "override": {"heightMm": 150, "material": "library:tile"}}
    (prism,) = _by_sid(_plan(items))["DRESS-R1-R-1-W-2"]
    assert (prism["z0"], prism["z1"]) == (0.02, 0.17)
    assert prism["faceMaterials"]["side"] == "library:tile"
    assert prism["props"]["state"] == "overridden"


def test_suggestion_failure_never_major() -> None:
    """Broken params, lost host, model/material missing from the pack: skipped/degraded minor, quality unchanged."""
    base = _plan([])
    assert quality_of(base["records"]) == "ready"
    bad = [
        _s("R1-x", "R1", "skirting", {"room": "R-1", "wall": "W-2"}, {"side": "left"}),
        _s(
            "R1-w",
            "R1",
            "skirting",
            {"room": "R-1", "wall": "W-2"},
            {"side": "left", "spansMm": [[0, 500]], "heightMm": "cao", "thicknessMm": 12},
        ),
        _s("R1-y", "R1", "skirting", {"room": "R-1", "wall": "W-404"}, {}),
        _s(
            "R1-z",
            "R1",
            "skirting",
            {"room": "R-1", "wall": "W-2"},
            {"side": "left", "spansMm": [[0, 500]], "heightMm": 90, "thicknessMm": 12, "material": "library:nope"},
        ),
        _s(
            "P1-x",
            "P1",
            "item",
            {"room": "R-1"},
            {"modelId": "ghost", "centre": {"x": 1, "y": 1}, "sizeMm": [1, 1, 1], "rotationDeg": 0},
        ),
        _s(
            "P1-y",
            "P1",
            "item",
            {"room": "R-1"},
            {"modelId": "sofa", "centre": {"x": 1, "y": 1}, "sizeMm": [1000, 450, 400], "rotationDeg": 0},
        ),
        _s("C2-x", "C2", "sash", {"opening": "O-404"}, {"frameWidthMm": 40, "frameDepthMm": 45}),
        _s("C2-y", "C2", "sash", {"opening": "O-1"}, {"frameWidthMm": 400, "frameDepthMm": 45}),
        _s("L1-x", "L1", "ceilingLight", {"room": "R-404"}, {"centre": {"x": 1, "y": 1}, "fixture": "panel"}),
        _s("Q-x", "R1", "mystery", {"room": "R-1"}, {}),
        _s("F1-x", "F1", "modelPick", {"furniture": "F-1"}, {"modelId": "ghost", "scale": 1, "reasonVi": "x"}),
        _s("R4-x", "R4", "railing", {"room": "R-1"}, {"start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 0}}),
    ]
    plan = _plan(bad)
    validate_plan(plan)
    recs = _sug_records(plan)
    assert len(recs) == len(bad)
    assert {r["severity"] for r in recs.values()} <= {"info", "minor"}
    assert recs["DRESS-R1-z"]["status"] == "degraded"
    assert "library:nope" in recs["DRESS-R1-z"]["reason"]
    assert recs["DRESS-R1-y"]["reason"] == "không còn tường chủ"
    assert "ngoài ±10 %" in recs["DRESS-P1-y"]["reason"]
    assert recs["DRESS-C2-y"]["reason"] == "khung cánh dày hơn ô kính"
    assert recs["DRESS-R1-x"]["reason"].startswith("tham số đề xuất không dựng được: thiếu ")
    assert recs["DRESS-R1-w"]["reason"] == "tham số đề xuất không dựng được: sai kiểu, giá trị hoặc hình học suy biến"
    assert quality_of(plan["records"]) == "ready"
    assert plan["appSuggestions"]["counts"] == {"built": 1, "skipped": 11}


def test_extras_carry_rule_host_state() -> None:
    """Every node a suggestion builds carries {prov, rule, ruleVersion, suggestionId, hostRef, state}."""
    plan = _plan()
    for sid, nodes in _by_sid(plan).items():
        for n in nodes:
            props = n["props"]
            assert props["prov"] == "suggested"
            assert props["suggestionId"] == sid
            assert props["rule"] == sid.split("-")[1]
            assert props["ruleVersion"] == 1
            assert props["state"] == "active"
            assert props["hostRef"]
    lamp = next(i for i in plan["instances"] if i["props"].get("suggestionId") == "DRESS-F1-F-1")
    assert lamp["name"] == "Furniture_F-1"
    assert lamp["model"] == "models/lamp.glb"
    assert lamp["props"]["prov_model"] == "suggested"


def test_snapshot_without_suggestions__sha_unchanged() -> None:
    """A document without suggestions adds no key: snapshotSha equals the M1 value (pinned from HEAD 1e707c04)."""
    floor = {
        "levelId": "L-1",
        "name": "Tầng 1",
        "order": 0,
        "elevationMm": 0,
        "heightMm": 3000,
        "hiddenIn3d": False,
        "revision": 7,
        "layer": {
            "walls": [
                {
                    "id": "W-1",
                    "kind": "envelope",
                    "thicknessMm": 200,
                    "heightMm": 3000,
                    "centreline": {"start": {"x": 0, "y": 0}, "end": {"x": 4000, "y": 0}},
                }
            ],
            "rooms": [],
            "openings": [],
            "furniture": [],
        },
    }
    document = {"layer": floor["layer"], "axes": [], "dimensions": []}
    assert suggestion_keys(document) == {}
    assert suggestion_keys(None) == {}
    assert suggestion_keys({**document, "suggestions": []}) == {}
    snap = build_snapshot(
        "prj_1",
        [{**floor, **suggestion_keys(document)}],
        {"compiler": "c", "pack": "p", "schema": 1},
        scene_style="am-ap",
    )
    assert snapshot_sha(snap) == "d35b7d065c7c1064b0d37e17701ab11eeb335dc198d13efe6b7d1a2f852cae68"
    meta = {"ruleset": "r", "rulesetSha": SHA, "basisRevision": 7}
    stored = {**document, "suggestions": [{"id": "DRESS-x"}], "suggestionsMeta": meta}
    assert suggestion_keys(stored) == {}
    assert suggestion_keys({**stored, "suggestionsEnabled": False}) == {}
    keys = suggestion_keys({**stored, "suggestionsEnabled": True})
    assert keys == {"suggestions": [{"id": "DRESS-x"}], "suggestionsMeta": meta}


def _cap_area(p: dict[str, Any]) -> float:
    """Total CCW area of a prism's caps."""
    verts = [v for r in p["rings"] for v in r]
    total = 0.0
    for t in p["caps"]:
        a, b, c = (verts[i] for i in t)
        area = ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
        assert area >= 1e-10
        total += area
    return total


@pytest.mark.parametrize(
    "kind",
    [
        "skirting",
        "wallTile",
        "cornice",
        "casing",
        "sash",
        "railing",
        "cabinetRun",
        "curtain",
        "rug",
        "ceilingLight",
        "item",
    ],
)
def test_each_kind__closed_and_non_degenerate(kind: str) -> None:
    """Prisms: caps CCW, no zero-area triangle, sum = polygon area (watertight extrusion); boxes: positive size."""
    items = [s for s in suggestions() if s["kind"] == kind and s.get("state") == "active" and not s.get("stale")]
    plan = _plan(items)
    validate_plan(plan)
    nodes = [n for sid, ns in _by_sid(plan).items() for n in ns]
    assert nodes
    for n in nodes:
        if "rings" in n:
            assert _cap_area(n) == pytest.approx(Polygon(n["rings"][0], n["rings"][1:]).area, abs=1e-8)
            assert n["z1"] > n["z0"]
        if "size" in n:
            assert min(n["size"]) > 0
    assert all(r["status"] == "built" for r in _sug_records(plan).values())


def test_plan_deterministic() -> None:
    """Same input twice → byte-identical plan JSON; stored order of suggestions does not matter."""
    a = json.dumps(_plan(), sort_keys=True)
    assert json.dumps(_plan(), sort_keys=True) == a
    first, other = _plan(), _plan(suggestions()[::-1])
    for plan in (first, other):
        plan["stamps"].pop("snapshotSha")  # the snapshot itself stores the list in another order
    assert json.dumps(other, sort_keys=True) == json.dumps(first, sort_keys=True)


def test_geometry_of_kinds() -> None:
    """Spot values: skirting on the inner face, tile below the sill, run replaces boxes, sash on pivots, item anchor."""
    plan = _plan()
    nodes = _by_sid(plan)
    (skirt,) = nodes["DRESS-R1-R-1-W-2"]
    xs = sorted({v[0] for v in skirt["rings"][0]})
    assert xs == [3.888, 3.9]
    tiles = nodes["DRESS-R2-R-1-W-4"]
    assert sorted((t["z0"], t["z1"]) for t in tiles) == [(0.02, 0.9), (0.02, 2.42)]
    (cornice,) = nodes["DRESS-R3-R-1"]
    assert (cornice["z0"], cornice["z1"]) == pytest.approx((2.6495, 2.6995))
    names = {i["name"] for i in plan["instances"]}
    assert "Furniture_F-2" not in names
    assert "Furniture_F-3" not in names
    skipped = {r["id"]: r["reason"] for r in plan["records"] if r["kind"] == "furniture" and r["status"] == "skipped"}
    assert skipped == {
        "F-2": "thay bằng dãy tủ đề xuất DRESS-F2-F-2-base",
        "F-3": "thay bằng dãy tủ đề xuất DRESS-F2-F-2-base",
    }
    base = next(n for n in nodes["DRESS-F2-F-2-base"] if n.get("boxes"))
    top = next(b for b in base["boxes"] if b["name"].endswith("_Top"))
    assert top["c"][2] == pytest.approx(0.86)
    assert top["c"][1] == pytest.approx(-3.9 + 0.31)
    assert len([b for b in base["boxes"] if "_Door" in b["name"]]) == 3
    window = next(a for a in plan["assemblies"] if a["name"] == "Window_O-1")
    sash = [b for b in window["boxes"] if b["props"].get("suggestionId") == "DRESS-C2-O-1"]
    assert len(sash) == 8
    assert {b["parent"] for b in sash} == {"Window_O-1_Sash0", "Window_O-1_Sash1"}
    (item,) = nodes["DRESS-P1-R-1-sofa"]
    assert item["anchor"] == "bbox"
    assert item["at"] == [2.0, -1.0, 0.02]
    assert item["scale"] == [0.95, 0.95, 0.95]
    assert item["props"]["license"] == "CC0"
    lamp = next(n for n in nodes["DRESS-L1-R-1-lamp"])
    assert lamp["at"][2] == pytest.approx(2.7 - 0.9)
    curtains = [n for n in nodes["DRESS-D2-O-1-L"] if "rings" in n]
    assert curtains
    assert curtains[0]["z1"] == pytest.approx(2.63)
    rail = next(n for n in nodes["DRESS-R4-R-1-e0"] if n.get("boxes"))
    assert len([b for b in rail["boxes"] if "_Post" in b["name"]]) == 3
    assert all(b["c"][1] == pytest.approx(-0.03) for b in rail["boxes"])


def test_cabinet_run_built_from_record_params_and_degraded_note() -> None:
    """Review F7: plinth, countertop and door gap come from the record (`plinthMm`, `countertopMm`, `gapMm`), not from
    compiler constants; a second stored run is never filtered out by the compiler; `degradedVi` → degraded minor."""
    items = [s for s in suggestions() if s["id"] == "DRESS-F2-F-2-base"]
    items[0]["params"].update(plinthMm=150, countertopMm=30, gapMm=10, degradedVi="dãy tủ ngắn lại")
    twin = copy.deepcopy(items[0])
    twin["id"] = "DRESS-F2-F-3-base"
    twin["hostRef"] = {"furniture": "F-3"}
    plan = _plan([*items, twin])
    recs = _sug_records(plan)
    assert (recs["DRESS-F2-F-2-base"]["status"], recs["DRESS-F2-F-2-base"]["reason"]) == ("degraded", "dãy tủ ngắn lại")
    assert recs["DRESS-F2-F-3-base"]["status"] == "degraded"
    base = next(n for n in _by_sid(plan)["DRESS-F2-F-2-base"] if n.get("boxes"))
    boxes = {b["name"].rsplit("_", 1)[1]: b for b in base["boxes"]}
    assert boxes["Plinth"]["size"][2] == pytest.approx(0.15)
    assert boxes["Top"]["size"][2] == pytest.approx(0.03)
    assert boxes["Door0"]["size"][0] == pytest.approx(2.0 / 3 - 0.01)


def test_item_without_size_and_pendant_without_size() -> None:
    """Pack model without sizeMm: item kept at true size (degraded note); pendant needs a size (skipped)."""
    items = [
        _s(
            "P1-n",
            "P1",
            "item",
            {"room": "R-1"},
            {"modelId": "nosize", "centre": {"x": 2000, "y": 2000}, "sizeMm": [500, 500, 500], "rotationDeg": 0},
        ),
        _s(
            "L1-n",
            "L1",
            "ceilingLight",
            {"room": "R-1"},
            {"centre": {"x": 2000, "y": 2000}, "fixture": "pendant", "modelId": "nosize"},
        ),
    ]
    recs = _sug_records(_plan(items))
    assert recs["DRESS-P1-n"]["status"] == "degraded"
    assert recs["DRESS-L1-n"]["reason"] == "mẫu nosize chưa có kích thước trong gói"


def test_outdoor_room_and_no_meta() -> None:
    """Room without a ceiling: cornice/light skipped; floors without suggestions → no `appSuggestions`."""
    assert "appSuggestions" not in build_plan(build_snapshot("p", [_floor("L-1", 0, 0)], VERSIONS), PACK)
    floor = _floor("L-1", 0, 0, layer=_layer())
    floor["layer"]["rooms"][0]["finish"] = "decking"
    floor["suggestions"] = [s for s in suggestions() if s["rule"] in ("R3", "L1")]
    ed = {"outdoorFinishNoCeiling": "decking", "styles": {}}
    plan = build_plan(build_snapshot("p", [floor], VERSIONS), {**PACK, "editorDefaults": ed})
    recs = _sug_records(plan)
    assert {r["reason"] for r in recs.values()} == {"phòng không có trần"}
    assert plan["appSuggestions"]["rulesetSha"] is None


def test_summary_mixed_rulesets() -> None:
    """Floors with different rulesets → sorted list of shas."""
    summary = sg.Summary(shas={"b", "a"}, seen=True)
    assert summary.extras() == {"rulesetSha": ["a", "b"], "counts": {}, "disabled": []}


def test_room_instances_share_images_distinct_materials() -> None:
    """Two rooms with the same floor ref: `<ref>@R-1`, `<ref>@R-2` share image files, are distinct materials;
    room-facing wall edges take the room's own wall instance."""
    layer = _layer()
    layer["walls"].append(
        {
            "id": "W-5",
            "kind": "partition",
            "thicknessMm": 100,
            "heightMm": 3000,
            "centreline": {"start": {"x": 2000, "y": 0}, "end": {"x": 2000, "y": 4000}},
        }
    )
    left = [{"x": 0, "y": 0}, {"x": 2000, "y": 0}, {"x": 2000, "y": 4000}, {"x": 0, "y": 4000}]
    right = [{"x": 2000, "y": 0}, {"x": 4000, "y": 0}, {"x": 4000, "y": 4000}, {"x": 2000, "y": 4000}]
    layer["rooms"] = [
        {"id": "R-1", "outline": left, "floorMaterial": "library:oak"},
        {"id": "R-2", "outline": right, "floorMaterial": "library:oak"},
    ]
    plan = build_plan(build_snapshot("p", [_floor("L-1", 0, 0, layer=layer)], VERSIONS), PACK)
    validate_plan(plan)
    m = plan["materials"]
    assert (
        m["library:oak@R-1"]["maps"]
        == m["library:oak@R-2"]["maps"]
        == {"base": "t/oak.jpg", "normal": None, "rough": None}
    )
    assert (m["library:oak@R-1"]["instanceOf"], m["library:oak@R-2"]["roomId"]) == ("library:oak", "R-2")
    floors = {p["appId"]: p["faceMaterials"]["top"] for p in plan["prisms"] if p["kind"] == "floor"}
    assert floors == {"R-1": "library:oak@R-1", "R-2": "library:oak@R-2"}
    w5 = next(p for p in plan["prisms"] if p["name"] == "Wall_W-5")
    assert {s["material"] for s in w5["faceMaterials"]["sideByRoom"]} == {
        "default:wall_partition@R-1",
        "default:wall_partition@R-2",
    }


def test_global_material_defaults() -> None:
    """M1 glass → transmission 1, roughness ≤ 0.05; M2 fabric sheen; M3 roughVar on flat non-metal; repeatM wins."""
    mats = MaterialResolver(PACK["materials"], PACK["sceneMaterials"])
    book = RecordBook()
    refs: list[tuple[str | None, Role]] = [
        ("scene:mat_glass", "glass"),
        ("library:fabric-linen", "frame"),
        ("library:metal-chrome", "metal"),
        ("library:tile", "wall_interior"),
        ("library:oak", "floor"),
        (None, "window_glass"),
    ]
    for ref, role in refs:
        mats.resolve(ref, role, "L-1", "x", book)
    t = mats.materials_table()
    assert (t["scene:mat_glass"]["transmission"], t["scene:mat_glass"]["roughness"]) == (1.0, 0.05)
    assert (t["default:window_glass"]["transmission"], t["default:window_glass"]["roughness"]) == (1.0, 0.05)
    assert t["library:fabric-linen"]["sheen"] == 0.6
    assert t["library:fabric-linen"]["roughVar"] == 0.06
    assert "roughVar" not in t["library:metal-chrome"]
    assert "sheen" not in t["library:metal-chrome"]
    assert t["library:tile"]["sizeM"] == 0.6
    assert t["library:oak"]["sizeM"] == 0.5
    rough_mapped = {k: v for k, v in t["library:oak"].items() if k != "roughVar"} | {"maps": {"rough": "r.jpg"}}
    assert "roughVar" not in tune_materials({"k": rough_mapped})["k"]


def test_skip_reasons() -> None:
    """Each guard gives its own minor reason: curved host, zero height/size, empty spans, unbuilt window, low base."""
    sk = {"side": "left", "spansMm": [[100, 900]], "heightMm": 90, "thicknessMm": 12}
    cab = {"slot": "base", "lengthMm": 2000, "depthMm": 600, "bottomMm": 0, "topMm": 100, "moduleMm": 600, **BASE_RUN}
    cab = {**cab, **LINE}
    rail = {
        "start": {"x": 0, "y": 0},
        "end": {"x": 4000, "y": 0},
        "spansMm": [],
        "heightMm": 1100,
        "handrailMm": 50,
        "glassMm": 12,
        "postSpacingMaxMm": 1200,
    }
    items = [
        _s("a", "R1", "skirting", {"wall": "W-2"}, {**sk, "heightMm": 0}),
        _s("b", "R1", "skirting", {"wall": "W-2"}, {**sk, "spansMm": [[100, 100]]}),
        _s("c", "C1", "casing", {"opening": "O-1"}, {"widthMm": 0, "thicknessMm": 16}),
        _s("d", "R2", "wallTile", {"wall": "W-4"}, {**sk, "spansMm": [], "tileMm": 300}),
        _s("e", "R4", "railing", {"room": "R-1"}, rail),
        _s("f", "F2", "cabinetRun", {"furniture": "F-2"}, cab),
        _s("g", "C2", "sash", {"opening": "O-2"}, {"frameWidthMm": 40, "frameDepthMm": 45}),
        _s("h", "R1", "skirting", {"wall": "W-1"}, sk),
        _s("i", "C1", "casing", {"opening": "O-3"}, {"widthMm": 70, "thicknessMm": 16}),
        _s("j", "F1", "modelPick", {"furniture": "F-5"}, {"modelId": "sofa", "scale": 1, "reasonVi": "x"}),
        _s(
            "k",
            "D2",
            "curtain",
            {"opening": "O-1", "room": "R-1"},
            {
                "side": "R",
                "widthMm": 450,
                "extMm": 100,
                "topMm": 2930,
                "offsetMm": 120,
                "pleatMm": 80,
                "degradedVi": "rèm hẹp",
            },
        ),
    ]
    floor = _floor("L-1", 0, 0, layer=_layer())
    layer = floor["layer"]
    layer["walls"][0]["curveOffsetMm"] = 300
    layer["openings"] += [
        {**WINDOW, "id": "O-2", "widthMm": 40},
        {**WINDOW, "id": "O-3", "wallId": "W-1", "offsetMm": 2500},
    ]
    layer["furniture"].append({**CABINET, "id": "F-5", "kind": "other", "model": {"src": "/items/sofa/model.glb"}})
    floor["suggestions"] = items
    recs = _sug_records(build_plan(build_snapshot("p", [floor], VERSIONS), PACK))
    assert {k[len("DRESS-") :]: (r["status"], r["reason"]) for k, r in recs.items()} == {
        "a": ("skipped", "chiều cao không dương"),
        "b": ("skipped", "hình rỗng sau khi cắt"),
        "c": ("skipped", "Casing_" + sg.short_name("", "DRESS-c")[1:] + "_L: kích thước không dương"),
        "d": ("skipped", "không có đoạn ốp nào"),
        "e": ("skipped", "cạnh không còn đoạn hở"),
        "f": ("skipped", "tủ dưới thấp hơn chân + mặt đá"),
        "g": ("skipped", "bộ cửa sổ không được dựng"),
        "h": ("skipped", "tường cong chưa hỗ trợ"),
        "i": ("skipped", "ô mở không có tường thẳng dựng được"),
        "j": ("skipped", "đồ chủ đã có mẫu người chọn"),
        "k": ("degraded", "rèm hẹp"),
    }


def test_item_scale_matches_rules_rounding() -> None:
    """ch-04 basin: rules shrank vanity-sf 917x636 by 10 % and stored whole mm (825x572); the compiler's footprint fit
    reads that back as 0.9 (built), not 89.9 % (skipped) — height is not part of the fit."""
    pack = {**PACK, "models": {**PACK["models"], "vanity": {"sizeMm": {"w": 917, "d": 636, "h": 1130}}}}
    item = _s(
        "P1-basin",
        "P1",
        "item",
        {"room": "R-1"},
        {"modelId": "vanity", "centre": {"x": 2000, "y": 1000}, "sizeMm": [825, 572, 900], "rotationDeg": 0},
    )
    plan = _plan([item], pack)
    (inst,) = _by_sid(plan)["DRESS-P1-basin"]
    assert inst["scale"] == [0.9, 0.9, 0.9]
    assert _sug_records(plan)["DRESS-P1-basin"]["status"] == "built"
