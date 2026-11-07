"""validate_plan (HOP-DONG-M1 §10)."""

import copy
from collections.abc import Callable
from typing import Any

import pytest

from packages.domain.model_export.plan.schema import validate_plan

MAT = {
    "label": "x",
    "editorRef": None,
    "color": [1, 1, 1],
    "roughness": 0.9,
    "metallic": 0.0,
    "transmission": 0.0,
    "sizeM": 1.0,
    "maps": {"base": None, "normal": None, "rough": None},
    "prov": "default",
}
OK: dict[str, Any] = {
    "schemaVersion": 1,
    "units": "m",
    "levels": [],
    "materials": {"m": MAT},
    "prisms": [
        {
            "name": "Wall_W-1",
            "rings": [[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]],
            "caps": [[0, 1, 2]],
            "faceMaterials": {
                "side": "m",
                "top": "m",
                "bottom": "m",
                "sideByRoom": [{"edgeIndex": 0, "ring": 0, "material": "m"}],
            },
        }
    ],
    "cutters": [{"target": "Wall_W-1", "appId": "D-1"}],
    "assemblies": [
        {
            "name": "Door_D-1",
            "boxes": [{"name": "Door_D-1_Leaf", "material": "m", "pitch": 0.5, "props": {"a": 1}}],
            "pivots": [{"name": "Door_D-1_Leaf0", "at": [0, 0, 0], "angle": 0.0, "props": {}}],
        }
    ],
    "instances": [],
    "records": [],
    "stamps": {},
}


def _bad(mutate: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    """Copy of OK after `mutate`."""
    p = copy.deepcopy(OK)
    mutate(p)
    return p


def test_validate_plan__ok() -> None:
    """Valid plan passes."""
    validate_plan(OK)


@pytest.mark.parametrize(
    ("mutate", "msg"),
    [
        (lambda p: p.pop("stamps"), "thiếu khoá"),
        (lambda p: p.update(schemaVersion=2), "schemaVersion"),
        (lambda p: p["materials"]["m"].pop("prov"), "thiếu: prov"),
        (lambda p: p["prisms"][0]["faceMaterials"].update(top="nope"), "nope"),
        (lambda p: p["prisms"][0]["faceMaterials"]["sideByRoom"][0].update(material="sbr"), "sbr"),
        (lambda p: p["assemblies"][0]["boxes"][0].update(material="box"), "box"),
        (lambda p: p["cutters"][0].update(target="Wall_X"), "Wall_X"),
        (lambda p: p["prisms"][0].update(caps=[[0, 1, 3]]), "caps"),
        (lambda p: p["prisms"][0].update(caps=[[0, 1]]), "caps"),
        (lambda p: p["assemblies"][0]["boxes"][0].update(pitch="1"), "pitch"),
        (lambda p: p["assemblies"][0]["boxes"][0].update(pitch=True), "pitch"),
        (lambda p: p["assemblies"][0]["pivots"][0].update(props=[]), "props"),
    ],
)
def test_validate_plan__rejects(mutate: Callable[[dict[str, Any]], object], msg: str) -> None:
    """Each broken invariant raises ValueError naming it."""
    with pytest.raises(ValueError, match=msg):
        validate_plan(_bad(mutate))
