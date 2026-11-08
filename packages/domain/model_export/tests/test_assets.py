"""Furniture model resolution (HOP-DONG-M1 §1 "Đồ đạc")."""

import logging
import math
from typing import Any

import pytest

from packages.domain.model_export.assets import AssetResolver
from packages.domain.model_export.records import RecordBook

MODELS: dict[str, dict[str, Any]] = {
    "sofa": {"file": "models/sofa.glb", "license": "CC0", "author": "A", "source": "s", "sizeMm": [2000, 900, 800]},
    "bed": {"sizeMm": {"x": 1600, "y": 2000, "z": 500}},
    "odd": {"sizeMm": "?"},
    "pack-bed": {"file": "models/pack-bed.glb", "license": "MIT (Pascal)", "sizeMm": {"w": 1520, "d": 2000, "h": 710}},
}
DEFAULTS = {"bed|bed": "bed", "table": "sofa", "chair": "ghost"}


def _f(**kw: Any) -> dict[str, Any]:
    """Furniture dict with a 2000x900 footprint at (1000, 2000) mm."""
    base = {
        "id": "F-1",
        "kind": "other",
        "centre": {"x": 1000, "y": 2000},
        "rotationDeg": 90,
        "boundingBox": {"min": {"x": 0, "y": 0}, "max": {"x": 2000, "y": 900}},
    }
    return {**base, **kw}


def test_resolve__document_model_exact_fit() -> None:
    """model.src → id, scale 1, Y flipped, rotation sign flipped, licence props."""
    book = RecordBook()
    inst = AssetResolver(MODELS, DEFAULTS).resolve_furniture(
        _f(model={"id": "sofa", "src": "/items/sofa/model.glb"}, heightMm=800, liftMm=100), "L", book
    )
    assert inst is not None
    assert inst["model"] == "models/sofa.glb"
    assert inst["at"] == [1.0, -2.0, 0.1]
    assert inst["rotZ"] == round(-math.pi / 2, 6)
    assert inst["scale"] == [1.0, 1.0, 1.0]
    assert inst["props"]["prov_model"] == "document"
    assert inst["props"]["license"] == "CC0"
    assert book.counts() == {"furniture:built": 1}


def test_resolve__editor_default_kind_variant_then_kind() -> None:
    """No model → kind|variant default, then kind."""
    r, book = AssetResolver(MODELS, DEFAULTS), RecordBook()
    a = r.resolve_furniture(
        _f(kind="bed", variant="bed", boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 1600, "y": 2000}}), "L", book
    )
    assert a is not None
    assert a["model"] == "models/bed.glb"
    assert a["props"]["prov_model"] == "editor-default"
    b = r.resolve_furniture(_f(kind="table", variant="x"), "L", book)
    assert b is not None
    assert b["model"] == "models/sofa.glb"


def test_resolve__scale_off_records_minor() -> None:
    """>±30 % → degraded minor with the number, never clamped."""
    book = RecordBook()
    inst = AssetResolver(MODELS, DEFAULTS).resolve_furniture(
        _f(model={"src": "/items/sofa/model.glb"}, boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 1000, "y": 450}}),
        "L",
        book,
    )
    assert inst is not None
    assert inst["scale"] == [0.5, 0.5, 0.5]
    assert book.records[0].status == "degraded"
    assert "50 %" in book.records[0].reason


def test_resolve__skips() -> None:
    """Suggested → info; no default → major; model not in pack → major; bad size → scale 1."""
    r, book = AssetResolver(MODELS, DEFAULTS), RecordBook()
    assert r.resolve_furniture(_f(suggested=True), "L", book) is None
    assert r.resolve_furniture(_f(kind="wardrobe"), "L", book) is None
    assert r.resolve_furniture(_f(kind="chair"), "L", book) is None
    assert [x.severity for x in book.records] == ["info", "major", "major"]
    assert book.records[2].reason == "mẫu ghost chưa có trong gói xuất"
    odd = r.resolve_furniture(_f(model={"src": "/items/odd/model.glb"}), "L", book)
    assert odd is not None
    assert odd["scale"] == [1.0, 1.0, 1.0]


def test_resolve__pack_size_keys_w_d_h(caplog: pytest.LogCaptureFixture) -> None:
    """Gói xuất ghi `sizeMm` `{w, d, h}`: hộp bao khác cỡ mẫu → co giãn theo hộp, lệch > 30 % → degraded."""
    r, book = AssetResolver(MODELS, DEFAULTS), RecordBook()
    fits = r.resolve_furniture(
        _f(
            model={"src": "/items/pack-bed/model.glb"},
            boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 1520, "y": 2000}},
            heightMm=710,
        ),
        "L",
        book,
    )
    small = r.resolve_furniture(
        _f(
            model={"src": "/items/pack-bed/model.glb"},
            boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 760, "y": 1000}},
        ),
        "L",
        book,
    )
    assert fits is not None
    assert fits["scale"] == [1.0, 1.0, 1.0]
    assert small is not None
    assert small["scale"] == [0.5, 0.5, 0.5]
    assert [x.status for x in book.records] == ["built", "degraded"]
    with caplog.at_level(logging.WARNING):
        legacy = r.resolve_furniture(
            _f(kind="bed", variant="bed", boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 800, "y": 1000}}),
            "L",
            book,
        )
    assert legacy is not None
    assert legacy["scale"] == [0.5, 0.5, 0.5]
    assert [rec.message for rec in caplog.records] == ["model_export_pack_size_xyz"]
