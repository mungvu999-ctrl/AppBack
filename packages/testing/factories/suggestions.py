"""Small hand-built layers for the suggestion rules (`packages/domain/suggestions/tests`).

Ids are W4-valid (`W-0000000001`…); coordinates in mm. `flat_layer()` is the shared fixture plan:

    y=0     +---------W1---------+---------W2---------+
            |  R1 bedroom 4x3 m  D1 (door on W5)       |
            |  window D2 on W1   W5  R2 livingRoom 5x3 |
    y=3000  +---------W3---------+---------W4---------+
            |  R3 loggia: wall W6 on x=0 only, open south and east
    y=4500  +....................+
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions.ruleset import RULE_PARAMS_PATH, Ruleset, ruleset_from_text

LEVEL = "L-0000000001"
_REVIEW: dict[str, Any] = {"confidence": 0.9, "source": "ai", "reviewed": False}


def sid(prefix: str, n: int) -> str:
    """W4 id `<prefix>-<n padded to 10 digits>`."""
    return f"{prefix}-{n:010d}"


def wall(n: int, start: tuple[int, int], end: tuple[int, int], thickness: int = 100, **over: Any) -> dict[str, Any]:
    """Straight wall dict (wire form)."""
    return {
        **_REVIEW,
        "id": sid("W", n),
        "levelId": LEVEL,
        "centreline": {"start": {"x": start[0], "y": start[1]}, "end": {"x": end[0], "y": end[1]}},
        "thicknessMm": thickness,
        "heightMm": 3000,
        "kind": "partition",
        "openingIds": [],
        **over,
    }


def opening(n: int, wall_n: int, kind: str, offset: int, width: int, **over: Any) -> dict[str, Any]:
    """Door or window on wall `wall_n`."""
    return {
        **_REVIEW,
        "id": sid("D", n),
        "wallId": sid("W", wall_n),
        "kind": kind,
        "offsetMm": offset,
        "widthMm": width,
        "heightMm": 2100 if kind == "door" else 1400,
        "sillHeightMm": 0 if kind == "door" else 900,
        "swing": "left",
        **over,
    }


def room(n: int, usage: str, outline: list[tuple[int, int]], **over: Any) -> dict[str, Any]:
    """Room with its shoelace area."""
    pts = outline
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, [*pts[1:], pts[0]], strict=True))) / 2e6
    return {
        **_REVIEW,
        "id": sid("R", n),
        "levelId": LEVEL,
        "name": usage,
        "usage": usage,
        "outline": [{"x": x, "y": y} for x, y in outline],
        "areaM2": round(area, 2),
        "wallIds": [],
        **over,
    }


def furniture(n: int, kind: str, centre: tuple[int, int], size: tuple[int, int], **over: Any) -> dict[str, Any]:
    """Furniture box of `size` (w, d) around `centre`."""
    cx, cy = centre
    w, d = size
    return {
        **_REVIEW,
        "id": sid("F", n),
        "levelId": LEVEL,
        "kind": kind,
        "centre": {"x": cx, "y": cy},
        "boundingBox": {
            "min": {"x": cx - w // 2, "y": cy - d // 2},
            "max": {"x": cx + w - w // 2, "y": cy + d - d // 2},
        },
        "rotationDeg": 0.0,
        **over,
    }


def layer(
    walls: Sequence[dict[str, Any]] = (),
    openings: Sequence[dict[str, Any]] = (),
    rooms: Sequence[dict[str, Any]] = (),
    furn: Sequence[dict[str, Any]] = (),
) -> SpatialLayer:
    """Validated layer from wire dicts."""
    return SpatialLayer.model_validate(
        {"walls": list(walls), "openings": list(openings), "rooms": list(rooms), "furniture": list(furn)}
    )


def flat_parts() -> dict[str, list[dict[str, Any]]]:
    """Wire dicts of the fixture plan (see module docstring), editable before `layer(**parts)`."""
    return {
        "walls": [
            wall(1, (0, 0), (4000, 0)),
            wall(2, (4000, 0), (9000, 0)),
            wall(3, (0, 3000), (4000, 3000)),
            wall(4, (4000, 3000), (9000, 3000)),
            wall(5, (4000, 0), (4000, 3000)),
            wall(6, (0, 0), (0, 4500)),
            wall(7, (9000, 0), (9000, 3000)),
        ],
        "openings": [opening(1, 5, "door", 1000, 900), opening(2, 1, "window", 1500, 1000)],
        "rooms": [
            room(1, "bedroom", [(0, 0), (4000, 0), (4000, 3000), (0, 3000)]),
            room(2, "livingRoom", [(4000, 0), (9000, 0), (9000, 3000), (4000, 3000)]),
            room(3, "loggia", [(0, 3000), (4000, 3000), (4000, 4500), (0, 4500)]),
        ],
        "furn": [],
    }


def flat_layer(**extra: list[dict[str, Any]]) -> SpatialLayer:
    """The fixture plan; `extra` lists are appended (e.g. `furn=[furniture(...)]`)."""
    parts = flat_parts()
    for key, items in extra.items():
        parts[key] = [*parts[key], *items]
    return layer(**parts)


def ruleset_with(mutate: Callable[[dict[str, Any]], None]) -> Ruleset:
    """The shipped rule table after `mutate(raw)` edits its parsed JSON (new sha)."""
    raw = json.loads(RULE_PARAMS_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    return ruleset_from_text(json.dumps(raw))
