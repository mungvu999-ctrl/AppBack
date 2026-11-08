"""Placement core and collision classifier (`rules/placement.py`, KET-QUA-LOI §4-6)."""

import json

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from packages.domain.suggestions import messages
from packages.domain.suggestions.model import HostRef, ItemParams, Stale, Suggestion, params_dict
from packages.domain.suggestions.reconcile import flag_collisions
from packages.domain.suggestions.rules.placement import (
    BBOX_PATH,
    Site,
    _lines,
    check_collisions,
    facing,
    footprint_ratio,
    front,
    known_model,
    model_size,
    place,
    polygons_of,
    rect,
    run_rect,
)
from packages.domain.suggestions.ruleset import load_ruleset
from packages.testing.factories.suggestions import flat_layer, flat_parts, furniture, layer, opening, room, sid, wall

RS = load_ruleset()
KW = {"gap": 30, "solid_margin": 100, "scale_tol_pct": 10, "step": 50}


def item(
    n: int, centre: tuple[int, int], size: tuple[int, int, int], rot: float = 0, lift: int = 0, **over: object
) -> Suggestion:
    """An effective P1-like item suggestion."""
    params = ItemParams.model_validate(
        {"furniture_kind": "other", "model_id": "plant-ph", "centre": {"x": centre[0], "y": centre[1]},
         "size_mm": size, "rotation_deg": rot, "lift_mm": lift}
    )  # fmt: skip
    return Suggestion.model_validate(
        {"id": f"DRESS-D5-x{n}", "rule": "D5", "rule_version": 1, "kind": "item", "host_ref": HostRef(room=sid("R", 1)),
         "params": params_dict(params), "created_revision": 1, **over}
    )  # fmt: skip


def test_bbox_table__provenance_and_every_rule_model_known() -> None:
    """The copied table names its source; every model id a rule places is in it."""
    raw = json.loads(BBOX_PATH.read_text(encoding="utf-8"))
    assert "model_bbox.py" in raw["source"]
    assert raw["unit"] == "m"
    p = {k: s.params for k, s in RS.rules.items()}
    ids = {slot["modelId"] for slots in p["P1"]["roles"].values() for slot in slots}
    ids |= {p[k]["modelId"] for k in ("P2", "P3", "D3", "D4", "D5")} | set(p["F1"]["models"]) | {p["L1"]["lampModelId"]}
    assert all(known_model(m) for m in ids), sorted(m for m in ids if not known_model(m))
    assert model_size("bed-sf") == (1800, 2252, 938)


def test_frame_helpers__facing_front_rect_run() -> None:
    """Front faces local +y; `facing` inverts `front`; rect and run footprints have the asked size."""
    for rot in (0.0, 90.0, 180.0, 270.0):
        assert facing(*front(rot)) == rot
    assert front(0) == pytest.approx((0, 1))
    assert rect((0, 0), 400, 200, 90).bounds == pytest.approx((-100, -200, 100, 200))
    run = run_rect((0, 0), (1000, 0), 600)
    assert run.area == pytest.approx(600_000)
    assert run.bounds == pytest.approx((0, 0, 1000, 600))
    assert polygons_of(GeometryCollection([Polygon([(0, 0), (1, 0), (1, 1)]), LineString([(0, 0), (1, 1)])]))
    assert polygons_of(Polygon()) == []
    assert list(_lines(LineString())) == []
    assert len(list(_lines(GeometryCollection([LineString([(0, 0), (1, 0)]), Point(0, 0)])))) == 1


def test_wall_edges__only_the_part_with_a_wall_behind() -> None:
    """A long room edge with a 1 m wall stub at its middle yields a ~1 m wall-backed edge, not the full 6 m."""
    outline = [(0, 0), (6000, 0), (6000, 3000), (0, 3000)]
    lay = layer(walls=[wall(1, (2500, 0), (3500, 0))], rooms=[room(1, "bedroom", outline)])
    site = Site(lay)
    edges = site.wall_edges(sid("R", 1))
    assert len(edges) == 1
    assert edges[0].length == pytest.approx(1000, abs=1)
    assert (edges[0].nx, edges[0].ny) == pytest.approx((0, 1))
    assert place(site, sid("R", 1), "bed-sf", prefer="centre", solid=True, **KW) is None


def test_place__slides_shrinks_to_tol_then_gives_up() -> None:
    """Free spot at full size; a tight room takes the -10 % step; tighter still → None (never shrinks further)."""
    full = place(Site(flat_layer()), sid("R", 2), "sofa-ph", prefer="centre", solid=False, **KW)
    assert full is not None
    assert (full.scale, full.size) == (1.0, model_size("sofa-ph"))
    tight = layer(
        walls=[wall(1, (0, 0), (1900, 0)), wall(2, (1900, 0), (1900, 2300)), wall(3, (1900, 2300), (0, 2300)),
               wall(4, (0, 2300), (0, 0))],
        rooms=[room(1, "bedroom", [(0, 0), (1900, 0), (1900, 2300), (0, 2300)])],
    )  # fmt: skip
    shrunk = place(Site(tight), sid("R", 1), "bed-sf", prefer="centre", solid=True, **KW)
    assert shrunk is not None
    assert shrunk.scale == pytest.approx(0.9)
    assert shrunk.size == (1620, 2027, 844)
    assert place(Site(tight), sid("R", 1), "bed-sf", prefer="centre", solid=True, **{**KW, "scale_tol_pct": 4}) is None


def test_place__ends_hugs_a_corner_and_avoids_obstacles_doors_windows() -> None:
    """`ends` starts at a stretch end (a corner); the result touches no obstacle, door or tall-item window zone."""
    lay = flat_layer(furn=[furniture(1, "table", (6500, 1500), (1000, 1000), roomId=sid("R", 2))])
    site = Site(lay)
    got = place(site, sid("R", 2), "plant-ph", prefer="ends", solid=False, **KW)
    assert got is not None
    minx, miny, maxx, maxy = got.poly.bounds
    assert min(minx - 4050, 8950 - maxx) <= 11
    assert min(miny - 50, 2950 - maxy) <= 11
    assert not got.poly.intersects(site.doors)
    assert site.fits(sid("R", 2), got.poly, got.size[2])


def test_check_collisions__doc_types_logged_rule_ones_flagged() -> None:
    """Document boxes: wall, item-item, door, window, outside-room → `doc`. A suggestion overlapping a box → `rule`;
    disabled suggestions and lifted items are ignored; opening on an unknown wall is skipped."""
    parts = flat_parts()
    parts["openings"].append(opening(9, 99, "window", 0, 500))
    furn = [
        furniture(1, "wardrobe", (3000, 60), (1200, 600)),  # into W1 (and the window zone, tall)
        furniture(2, "table", (1000, 1500), (800, 800)),
        furniture(3, "chair", (1300, 1500), (600, 600)),  # overlaps 2
        furniture(4, "table", (3700, 1450), (500, 500)),  # door zone of D1
        furniture(5, "other", (4000, 2500), (1200, 400), heightMm=900),  # across W5 into the living room
        furniture(6, "other", (6000, 1000), (400, 400), suggested=True),
        furniture(7, "stair", (6000, 2000), (400, 400)),
        furniture(8, "other", (3000, 1200), (30, 30)),  # thinner than the touch tolerance: never collides
    ]
    lay = layer(**{**parts, "furn": furn})
    hit = item(1, (700, 1200), (500, 500, 800))
    off = item(2, (1000, 1500), (500, 500, 800), state="disabled")
    high = item(3, (1000, 1500), (500, 500, 800), lift=1500)
    notes = check_collisions(lay, [hit, off, high])
    doc = {(n.item, n.type, n.other) for n in notes if n.label == "doc"}
    assert (sid("F", 1), "wall", None) in doc
    assert (sid("F", 1), "window", None) in doc
    assert (sid("F", 2), "item-item", sid("F", 3)) in doc
    assert (sid("F", 4), "opening-clearance", None) in doc
    assert (sid("F", 5), "outside-room", None) in doc
    assert not any({sid("F", 6), sid("F", 7), sid("F", 8)} & {n.item, n.other} for n in notes)
    rule = [(n.item, n.type, n.other) for n in notes if n.label == "rule"]
    assert rule == [("DRESS-D5-x1", "item-item", sid("F", 2))]
    assert notes == sorted(notes, key=lambda n: (n.label, n.item, n.type, n.other or ""))


def test_footprint_ratio__long_on_long_height_ignored() -> None:
    """Long side on long side whatever the order; a third (height) value is ignored."""
    assert footprint_ratio((900, 1800), (2000, 900)) == pytest.approx(0.9)
    assert footprint_ratio((1800, 900, 10), (900, 2000, 5000)) == pytest.approx(0.9)
    assert footprint_ratio((2000, 450), (2000, 900)) == pytest.approx(0.5)


def test_flag_collisions__switched_on_record_goes_stale_with_reason() -> None:
    """Review F6: two effective items overlap → the one switched on by this write (`first`) goes stale with the reason
    and a log note; without `first` the pair's first item is blamed; no `rule` collision → list unchanged."""
    lay = flat_layer()
    a, b = item(1, (700, 1200), (500, 500, 800)), item(2, (800, 1200), (500, 500, 800))

    out, notes = flag_collisions([a, b], lay, 7, frozenset({b.id}))

    reason = messages.collides_stale(["item-item"], [a.id])
    assert [s.stale for s in out] == [None, Stale(since=7, reason_vi=reason)]
    assert [(n.rule, n.host, n.status, n.reason_vi) for n in notes] == [("D5", b.id, "log", reason)]
    default, _ = flag_collisions([a, b], lay, 7)
    assert (default[0].stale is not None, default[1].stale) == (True, None)
    assert flag_collisions([a], lay, 7) == ([a], [])
