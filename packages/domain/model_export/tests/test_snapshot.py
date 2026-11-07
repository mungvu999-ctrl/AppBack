"""Snapshot, fingerprint and units (HOP-DONG-M1 §2)."""

import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box

from packages.domain.model_export.snapshot import build_snapshot, canonical_json, fingerprint, snapshot_sha
from packages.domain.model_export.units import polygon_rings, polygons_of, to_m

META = [
    {"levelId": "L-2", "order": 1, "elevationMm": 3300, "heightMm": 3000, "hiddenIn3d": False, "revision": 2},
    {"levelId": "L-1", "order": 0, "elevationMm": 0, "heightMm": 3300, "hiddenIn3d": False, "revision": 7},
]
VERSIONS = {"compiler": "m1-01.abc", "pack": "p1", "schema": 1}


def test_canonical_json__sorted_compact_utf8() -> None:
    """Keys sorted, no spaces, Vietnamese kept as UTF-8."""
    assert canonical_json({"b": 1, "a": "Tầng"}) == '{"a":"Tầng","b":1}'.encode()


def test_fingerprint__order_independent_and_sensitive() -> None:
    """Input order irrelevant; revision, versions and format change it."""
    fp = fingerprint(META, VERSIONS)
    assert fp == fingerprint(list(reversed(META)), VERSIONS)
    assert len(fp) == 64
    bumped = [dict(META[0], revision=3), META[1]]
    assert fingerprint(bumped, VERSIONS) != fp
    assert fingerprint(META, {**VERSIONS, "pack": "p2"}) != fp
    assert fingerprint(META, VERSIONS, fmt="usdz") != fp


def test_build_snapshot__sorted_and_sha_stable() -> None:
    """Floors sorted by order; sha equal for equal content."""
    floors = [dict(m, name="T", layer={"walls": []}) for m in META]
    snap = build_snapshot("prj_1", floors, VERSIONS)
    assert snap["schemaVersion"] == 1
    assert snap["projectId"] == "prj_1"
    assert [f["levelId"] for f in snap["floors"]] == ["L-1", "L-2"]
    assert snapshot_sha(snap) == snapshot_sha(build_snapshot("prj_1", reversed(floors), VERSIONS))


def test_to_m__flips_y() -> None:
    """mm Y-down → m Y-up."""
    assert to_m({"x": 1500, "y": 2000}) == (1.5, -2.0)


def test_polygon_rings__ccw_outer_cw_holes_open() -> None:
    """Outer CCW, hole CW, no repeated closing point."""
    poly = Polygon([(0, 0), (0, 4), (4, 4), (4, 0)], [[(1, 1), (2, 1), (2, 2), (1, 2)]])
    outer, hole = polygon_rings(poly)
    assert len(outer) == 4
    assert len(hole) == 4

    def area(r: list[list[float]]) -> float:
        return float(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:] + r[:1], strict=False)) / 2)

    assert area(outer) > 0 > area(hole)
    with pytest.raises(ValueError, match="MultiPolygon"):
        polygon_rings(MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]))


def test_polygons_of__all_shapes() -> None:
    """None/empty → [], Polygon, MultiPolygon, collection keeps polygons only."""
    assert polygons_of(None) == []
    assert polygons_of(Polygon()) == []
    assert len(polygons_of(box(0, 0, 1, 1))) == 1
    assert len(polygons_of(MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]))) == 2
    gc = GeometryCollection([box(0, 0, 1, 1), LineString([(0, 0), (1, 1)])])
    assert len(polygons_of(gc)) == 1


def test_scene_style__snapshot_and_fingerprint() -> None:
    """sceneStyle stored in snapshot; None keeps the old fingerprint, a style changes it."""
    assert build_snapshot("p", [], VERSIONS)["sceneStyle"] is None
    assert build_snapshot("p", [], VERSIONS, scene_style="go-am")["sceneStyle"] == "go-am"
    assert fingerprint(META, VERSIONS, scene_style=None) == fingerprint(META, VERSIONS)
    assert fingerprint(META, VERSIONS, scene_style="go-am") != fingerprint(META, VERSIONS)
