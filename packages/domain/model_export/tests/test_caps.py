"""Ring cleaning + cap triangulation (blocked GLB from collinear ring points, H-02 e2e 07/11)."""

import json
from pathlib import Path
from typing import Any

import pytest
import shapely
from shapely.geometry import GeometryCollection, Polygon

from packages.domain.model_export import units
from packages.domain.model_export.plan.slabs import slab_plan
from packages.domain.model_export.plan.walls import wall_plan
from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.units import cap_triangles, clean_ring, polygon_rings, polygons_of

DATA = Path(__file__).parent / "data"
U_SHAPE = [[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [2.0, 2.0], [2.0, 1.0], [1.0, 1.0], [1.0, 2.0], [0.0, 2.0]]
"""(3,2), (2,2), (1,2), (0,2) lie on one line without being consecutive (the slab case)."""


def _area(verts: list[list[float]], t: list[int]) -> float:
    """Signed triangle area."""
    a, b, c = (verts[i] for i in t)
    return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2


def _check(rings: list[list[list[float]]]) -> list[list[int]]:
    """Caps are CCW, non-degenerate and sum to the polygon area."""
    caps = cap_triangles(rings)
    verts = [v for r in rings for v in r]
    assert all(_area(verts, t) >= 1e-10 for t in caps)
    assert sum(_area(verts, t) for t in caps) == pytest.approx(Polygon(rings[0], rings[1:]).area, abs=1e-8)
    return caps


def test_clean_ring__duplicates_collinear_spike() -> None:
    """Duplicate, collinear and back-turning points go; corners stay."""
    ring = [[0.0, 0.0], [0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [2.0, 1.0], [2.0, 1.5], [2.0, 1.2], [2.0, 2.0], [0.0, 2.0]]
    assert clean_ring(ring) == [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
    assert len(clean_ring([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])) < 3


def test_polygon_rings__cleaned_and_collapsed() -> None:
    """Junction nodes removed; a degenerate hole is dropped."""
    poly = Polygon([(0, 0), (1, 0), (2, 0), (2, 2), (0, 2)])
    assert polygon_rings(poly) == [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]]
    tiny_hole = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (1.00001, 1), (1, 1.00001)]])
    assert len(polygon_rings(tiny_hole)) == 1


def test_polygon_rings__collapsed_outer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outer ring that collapses below 3 points → clear error."""
    monkeypatch.setattr(units, "clean_ring", lambda pts: pts[:2])
    with pytest.raises(ValueError, match="vòng ngoài suy biến"):
        polygon_rings(Polygon([(0, 0), (1, 0), (1, 1)]))


def test_cap_triangles__non_adjacent_collinear_and_hole() -> None:
    """U shape with non-adjacent collinear vertices; square with a hole."""
    assert len(_check([U_SHAPE])) == 6
    holed = polygon_rings(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (2, 1), (2, 2), (1, 2)]]))
    assert len(_check(holed)) == 8


def _fake(tris: Any) -> Any:
    """Stand-in for shapely.constrained_delaunay_triangles."""
    return lambda _poly: GeometryCollection([Polygon(t) for t in tris])


SQUARE = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]


@pytest.mark.parametrize(
    ("tris", "msg"),
    [
        ([[(0, 0), (2, 0), (1, 1)]], "không thuộc vòng"),
        ([[(0, 0), (2, 0), (2, 2)]], "diện tích nắp"),
        ([[(0, 0), (2, 0), (2, 2)], [(0, 0), (2, 0), (0, 2)]], "cạnh vòng"),
    ],
)
def test_cap_triangles__checks(
    monkeypatch: pytest.MonkeyPatch, tris: list[list[tuple[float, float]]], msg: str
) -> None:
    """Foreign vertex, area mismatch, ring edge not in exactly one triangle → ValueError."""
    monkeypatch.setattr(shapely, "constrained_delaunay_triangles", _fake(tris))
    with pytest.raises(ValueError, match=msg):
        cap_triangles([SQUARE])


def test_cap_triangles__drops_zero_area_and_orients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-area triangles dropped; clockwise ones reversed."""
    tris = [[(0, 0), (2, 2), (2, 0)], [(0, 0), (2, 2), (0, 2)], [(0, 0), (2, 0), (2, 0)]]
    monkeypatch.setattr(shapely, "constrained_delaunay_triangles", _fake(tris))
    assert cap_triangles([SQUARE]) == [[0, 1, 2], [0, 2, 3]]


@pytest.mark.parametrize("name", ["ch-08", "nt-02"])
def test_caps__bench_fixtures(name: str) -> None:
    """Every wall/slab/finish polygon of the real plans: no collinear ring points, valid caps."""
    data = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    height = data["heightMm"] / 1000
    book = RecordBook()
    wp = wall_plan(data["walls"], data["levelId"], height, book)
    sl = slab_plan(data["rooms"], wp.union, data["levelId"], height, book, [])
    geoms = [p["own"] for p in wp.pieces] + [sl.structural] + [f["geom"] for f in sl.finishes]
    count = 0
    for poly in (p for g in geoms for p in polygons_of(g)):
        rings = polygon_rings(poly)
        for ring in rings:
            n = len(ring)
            assert all(abs(units._cross(ring[i - 1], ring[i], ring[(i + 1) % n])) >= 1e-10 for i in range(n))
        _check(rings)
        count += 1
    assert count > 10
