"""Furniture model resolution (HOP-DONG-M1 §1 "Đồ đạc")."""

import math
from typing import Any

from packages.domain.model_export.assets import AssetResolver, fit_scale, model_size_m
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
    """model.src → id, scale 1, Y flipped, rotation sign flipped, licence props; F0: bbox anchor, bottom on the
    finish floor (+20 mm) + lift."""
    book = RecordBook()
    inst = AssetResolver(MODELS, DEFAULTS).resolve_furniture(
        _f(model={"id": "sofa", "src": "/items/sofa/model.glb"}, heightMm=800, liftMm=100), "L", book
    )
    assert inst is not None
    assert inst["model"] == "models/sofa.glb"
    assert inst["anchor"] == "bbox"
    assert inst["at"] == [1.0, -2.0, 0.12]
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


def test_resolve__scale_clamped_records_minor() -> None:
    """F4: footprint needs 50 % → clamped to 90 %, degraded minor with both numbers; never shrunk further."""
    book = RecordBook()
    inst = AssetResolver(MODELS, DEFAULTS).resolve_furniture(
        _f(model={"src": "/items/sofa/model.glb"}, boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 1000, "y": 450}}),
        "L",
        book,
    )
    assert inst is not None
    assert inst["scale"] == [0.9, 0.9, 0.9]
    assert inst["props"]["status"] == "degraded"
    assert (book.records[0].status, book.records[0].severity) == ("degraded", "minor")
    assert "cần co giãn 50 %" in book.records[0].reason
    assert "chỉ cho 90 %" in book.records[0].reason


def test_resolve__scale_within_tolerance_and_height_ignored() -> None:
    """Box 1900x855 (95 %) → scale 0.95, built; `heightMm` does not enter the fit."""
    book = RecordBook()
    inst = AssetResolver(MODELS, DEFAULTS).resolve_furniture(
        _f(
            model={"src": "/items/sofa/model.glb"},
            heightMm=100,
            boundingBox={"min": {"x": 0, "y": 0}, "max": {"x": 1900, "y": 855}},
        ),
        "L",
        book,
    )
    assert inst is not None
    assert inst["scale"] == [0.95, 0.95, 0.95]
    assert book.counts() == {"furniture:built": 1}


def test_fit_scale__rules_rounding_and_bounds() -> None:
    """Rules store whole mm: vanity 917x636 at 90 % = 825x572 fits back at 0.9; 88 % does not; growth clamps at 1.1."""
    assert fit_scale((825, 572), (917, 636)) == (0.9, True)
    assert fit_scale((807, 560), (917, 636)) == (0.9, False)
    assert fit_scale((1100, 700), (917, 636)) == (1.1, False)
    assert fit_scale((917, 636), (917, 636)) == (1.0, True)


def test_fit_scale__model_crossing_the_box_does_not_fit() -> None:
    """Review F12: width on width, depth on depth. Long sides crossed (box 572 x 825, model 917 x 636) needs 62 %, not
    90 %; the 1000 x 900 box with an 880 x 1000 model (too little elongated to have been turned) needs 90 % — fits
    at the clamp instead of the old "1.0, lấn 100 mm"."""
    assert fit_scale((572, 825), (917, 636)) == (0.9, False)
    assert fit_scale((1000, 900), (880, 1000)) == (0.9, True)
    assert fit_scale((1000, 890), (880, 1000)) == (0.9, False)


def test_resolve__document_furniture_never_turned() -> None:
    """Review F13: box 900 wide x 2000 deep, sofa 2000 x 900 → built at the document's rotation (as the editor), scale
    clamped at 0.9 and `degraded minor` with the scale it needed; no "xoay trục dài" turn any more."""
    box = {"min": {"x": 0, "y": 0}, "max": {"x": 900, "y": 2000}}
    item = _f(model={"src": "/items/sofa/model.glb"}, rotationDeg=0, boundingBox=box)
    book = RecordBook()
    inst = AssetResolver(MODELS, DEFAULTS).resolve_furniture(item, "L", book)
    assert inst is not None
    assert (inst["rotZ"], inst["scale"]) == (0.0, [0.9, 0.9, 0.9])
    (rec,) = book.records
    assert (rec.status, rec.severity) == ("degraded", "minor")
    assert rec.reason == "mẫu sofa cần co giãn 45 % để vừa hộp bao, chỉ cho 90 %"


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


def test_model_size_forms() -> None:
    """List, `w/d/h` and `x/y/z` dicts → metres; a zero side or no size → None."""
    assert model_size_m({"sizeMm": [1000, 500, 250]}) == (1.0, 0.5, 0.25)
    assert model_size_m({"sizeMm": {"x": 1000, "y": 500, "z": 250}}) == (1.0, 0.5, 0.25)
    assert model_size_m({"sizeMm": {"w": 1000, "d": 0, "h": 1}}) is None
    assert model_size_m({}) is None


def test_resolve__pack_size_keys_w_d_h() -> None:
    """Gói xuất ghi `sizeMm` `{w, d, h}`: hộp bao vừa mẫu → built, scale 1; hộp chỉ bằng nửa → degraded (đọc cỡ)."""
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
    assert small["scale"][0] < 1
    assert [x.status for x in book.records] == ["built", "degraded"]
