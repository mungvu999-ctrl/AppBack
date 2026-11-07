"""Unit/axis conversion: editor mm (Y down) → export metres (Y up)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient

GRID = 1e-4  # metres; snapping grid for exported coordinates


def to_m(point_mm: Mapping[str, int]) -> tuple[float, float]:
    return (point_mm["x"] / 1000, -point_mm["y"] / 1000)


def polygons_of(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]


COLLINEAR_EPS = 1e-10
"""|cross| (m²) under which three ring points count as collinear. On the 0.1 mm grid every non-zero
cross product is ≥ 1e-8 m² (one grid cell), so this only catches exact collinearity/duplicates."""
AREA_EPS = 1e-10
"""Cap triangles smaller than this (m²) are dropped; the cap must still sum to the polygon area ±1e-8."""


def _cross(a: list[float], b: list[float], c: list[float]) -> float:
    """z of (b - a) x (c - b)."""
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def clean_ring(pts: list[list[float]]) -> list[list[float]]:
    """Drop duplicate and collinear consecutive points (incl. back-turning spikes) until stable.

    Collinear points (wall junction nodes from polygonize) make Blender fill zero-area triangles.
    """
    ring = list(pts)
    changed = True
    while changed and len(ring) >= 3:  # ponytail: O(n²) restart scan; rings are tens of points
        changed = False
        for i in range(len(ring)):
            prev, cur, nxt = ring[i - 1], ring[i], ring[(i + 1) % len(ring)]
            if cur == prev or abs(_cross(prev, cur, nxt)) < COLLINEAR_EPS:
                del ring[i]
                changed = True
                break
    return ring


def _ring(coords: Any) -> list[list[float]]:
    """Rounded open ring (no closing point), cleaned."""
    pts = [[round(float(x), 4), round(float(y), 4)] for x, y, *_ in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return clean_ring(pts)


def polygon_rings(geom: Any) -> list[list[list[float]]]:
    """Rings of a single Polygon: outer CCW first, then holes CW; no closing point, no duplicate or
    collinear points. Holes that collapse below 3 points are dropped; a collapsed outer ring raises.

    MultiPolygon → caller splits via polygons_of(); raises instead of mixing parts.
    """
    if isinstance(geom, MultiPolygon):
        raise ValueError("MultiPolygon: tách bằng polygons_of() trước")
    p = orient(geom, 1.0)
    outer = _ring(p.exterior.coords)
    if len(outer) < 3:
        raise ValueError("vòng ngoài suy biến sau khi bỏ điểm thẳng hàng")
    holes = [r for r in (_ring(h.coords) for h in p.interiors) if len(r) >= 3]
    return [outer, *holes]


def _area(a: list[float], b: list[float], c: list[float]) -> float:
    """Signed area of triangle abc."""
    return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2


def cap_triangles(rings: list[list[list[float]]]) -> list[list[int]]:
    """Cap (top/bottom) triangles for prism `rings` by constrained Delaunay, as CCW index triples into
    the vertices of all rings concatenated in order. Raises ValueError when a triangle vertex is not a
    ring vertex, the area doesn't match ±1e-8 m², or a ring edge isn't in exactly one triangle."""
    verts = [v for ring in rings for v in ring]
    index: dict[tuple[float, float], int] = {}
    for i, v in enumerate(verts):
        index.setdefault((v[0], v[1]), i)
    poly = Polygon(rings[0], rings[1:])
    tris: list[list[int]] = []
    for tri in shapely.constrained_delaunay_triangles(poly).geoms:
        ids = []
        for x, y in shapely.get_coordinates(tri)[:3]:
            key = (round(x, 4), round(y, 4))
            if key not in index:
                raise ValueError(f"tam giác nắp có đỉnh {key} không thuộc vòng")
            ids.append(index[key])
        area = _area(*(verts[i] for i in ids))
        if abs(area) < AREA_EPS:
            continue
        tris.append(ids if area > 0 else [ids[0], ids[2], ids[1]])
    total = sum(_area(*(verts[i] for i in t)) for t in tris)
    if abs(total - poly.area) > 1e-8:
        raise ValueError(f"diện tích nắp {total:.8f} khác đa giác {poly.area:.8f}")
    edges: Counter[tuple[int, ...]] = Counter()
    for a, b, c in tris:
        edges.update(tuple(sorted(e)) for e in ((a, b), (b, c), (c, a)))
    start = 0
    for ring in rings:
        n = len(ring)
        for k in range(n):
            e = tuple(sorted((start + k, start + (k + 1) % n)))
            if edges[e] != 1:
                raise ValueError(f"cạnh vòng {e} thuộc {edges[e]} tam giác nắp")
        start += n
    return tris
