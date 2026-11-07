"""plan.json shape (A → B interface, HOP-DONG-M1 §10) and its validator.

Coordinates: metres, X = x_mm/1000, Y = -y_mm/1000, Z up and level-local (from the level's floor;
the worker adds `levels[].elevation`). In assemblies, `boxes[].c`, `pivots[].at` and every `angle` are in the
LEVEL frame, not relative to the parent; `parent` only groups objects. `angle` turns about Z (radians).
Prisms may carry `caps`: CCW triangles [i, j, k] of the top/bottom face, indexing the vertices of all
`rings` concatenated in order (constrained Delaunay, no zero-area triangle; worker reverses for the bottom).
Optional `boxes[].pitch` (radians) turns the box about its own width axis, positive = the +X end rises
(stair stringers, balustrade glass, handrails). Boxes and pivots may carry a `props` dict (glTF extras).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

REQUIRED = (
    "schemaVersion",
    "units",
    "levels",
    "materials",
    "prisms",
    "cutters",
    "assemblies",
    "instances",
    "records",
    "stamps",
)
MATERIAL_KEYS = ("label", "editorRef", "color", "roughness", "metallic", "transmission", "sizeM", "maps", "prov")


class Plan(TypedDict):
    """Top-level plan.json (schemaVersion 1); nested items follow the contract example."""

    schemaVersion: int
    units: str
    levels: list[dict[str, Any]]
    materials: dict[str, dict[str, Any]]
    prisms: list[dict[str, Any]]
    cutters: list[dict[str, Any]]
    assemblies: list[dict[str, Any]]
    instances: list[dict[str, Any]]
    records: list[dict[str, Any]]
    stamps: dict[str, str]


def _material_refs(plan: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(owner name, material key) for every material reference in prisms and assembly boxes."""
    refs = []
    for p in plan["prisms"]:
        fm = p["faceMaterials"]
        refs += [(p["name"], fm[k]) for k in ("side", "top", "bottom")]
        refs += [(p["name"], s["material"]) for s in fm.get("sideByRoom", [])]
    for a in plan["assemblies"]:
        refs += [(b["name"], b["material"]) for b in a.get("boxes", [])]
    return refs


def _check_item(item: Mapping[str, Any]) -> None:
    """Optional `pitch` must be a number, optional `props` a dict."""
    pitch = item.get("pitch")
    if pitch is not None and (isinstance(pitch, bool) or not isinstance(pitch, (int, float))):
        raise ValueError(f"{item.get('name')}: pitch phải là số (radian)")
    if not isinstance(item.get("props", {}), dict):
        raise ValueError(f"{item.get('name')}: props phải là dict")


def _check_caps(prism: Mapping[str, Any]) -> None:
    """Optional `caps`: index triples into the concatenated `rings` vertices."""
    n = sum(len(r) for r in prism.get("rings", []))
    for tri in prism.get("caps") or []:
        if len(tri) != 3 or any(not isinstance(i, int) or not 0 <= i < n for i in tri):
            raise ValueError(f"{prism['name']}: caps có chỉ số ngoài phạm vi: {tri}")


def validate_plan(plan: Mapping[str, Any]) -> None:
    """Raise ValueError on missing keys, wrong schemaVersion, dangling material keys or cutter targets."""
    missing = [k for k in REQUIRED if k not in plan]
    if missing:
        raise ValueError(f"plan thiếu khoá: {', '.join(missing)}")
    if plan["schemaVersion"] != 1:
        raise ValueError(f"schemaVersion {plan['schemaVersion']} không hỗ trợ")
    for key, m in plan["materials"].items():
        lacking = [k for k in MATERIAL_KEYS if k not in m]
        if lacking:
            raise ValueError(f"vật liệu {key} thiếu: {', '.join(lacking)}")
    for owner, key in _material_refs(plan):
        if key not in plan["materials"]:
            raise ValueError(f"{owner} dùng vật liệu không có: {key}")
    for a in plan["assemblies"]:
        for item in [*a.get("boxes", []), *a.get("pivots", [])]:
            _check_item(item)
    for p in plan["prisms"]:
        _check_caps(p)
    names = {p["name"] for p in plan["prisms"]}
    for c in plan["cutters"]:
        if c["target"] not in names:
            raise ValueError(f"cutter {c.get('appId')} trỏ tới prism không có: {c['target']}")
