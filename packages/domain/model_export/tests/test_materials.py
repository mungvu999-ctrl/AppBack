"""Material resolution (HOP-DONG-M1 §3)."""

from packages.domain.model_export.materials import MaterialResolver, hex_rgb
from packages.domain.model_export.records import RecordBook

PACK = {
    "oak": {
        "label": "Gỗ sồi",
        "color": "#a07850",
        "roughness": 0.6,
        "metalness": 0.0,
        "repeatX": 2,
        "maps": {"base": "textures/oak.jpg", "normal": "textures/oak_n.png"},
        "hasTexture": True,
    },
    "tile": {"label": "Gạch", "color": "#ffffff", "repeatX": 0, "maps": {}, "hasTexture": True},
    "paint": {"color": "#eeeeee", "hasTexture": False},
}
SCENE = {"wood": {"label": "Sàn gỗ", "color": "#886644", "roughness": 0.7}}


def test_hex_rgb() -> None:
    """#rrggbb → 0..1 floats."""
    assert hex_rgb("#ff0000") == [1.0, 0.0, 0.0]


def test_resolve__no_ref_is_neutral_default() -> None:
    """No ref → default:<role>, prov default, no record."""
    mr, book = MaterialResolver(PACK, SCENE), RecordBook()
    key = mr.resolve(None, "glass", "L", "D-1", book)
    assert key == "default:glass"
    assert not book.records
    entry = mr.materials_table()[key]
    assert entry["prov"] == "default"
    assert entry["transmission"] == 1.0  # M1 (M2-QL): glass transmits fully
    assert entry["editorRef"] is None


def test_resolve__library_with_maps() -> None:
    """Library hit: maps, sizeM = 1/repeatX, prov document."""
    mr, book = MaterialResolver(PACK, SCENE), RecordBook()
    assert mr.resolve("library:oak", "floor", "L", "R-1", book) == "library:oak"
    e = mr.materials_table()["library:oak"]
    assert e["sizeM"] == 0.5
    assert e["maps"] == {
        "base": "textures/oak.jpg",
        "normal": "textures/oak_n.png",
        "rough": None,
    }
    assert e["prov"] == "document"
    assert e["label"] == "Gỗ sồi"
    assert not book.records


def test_resolve__library_missing_texture_degrades() -> None:
    """Catalogue says textured but pack lacks the image → flat color + degraded major."""
    mr, book = MaterialResolver(PACK, SCENE), RecordBook()
    mr.resolve("library:tile", "floor", "L", "R-1", book)
    assert book.records[0].severity == "major"
    assert "thiếu ảnh vật liệu tile" in book.records[0].reason
    assert mr.materials_table()["library:tile"]["maps"]["base"] is None
    assert mr.materials_table()["library:tile"]["sizeM"] == 1.0
    mr.resolve("library:paint", "wall_interior", "L", "W-1", book)
    assert len(book.records) == 1


def test_resolve__scene_and_unknown() -> None:
    """Scene hit ok; scene miss / unknown prefix → neutral + degraded major."""
    mr, book = MaterialResolver(PACK, SCENE), RecordBook()
    assert mr.resolve("scene:wood", "floor", "L", "R-1", book) == "scene:wood"
    assert not book.records
    mr.resolve("scene:marble", "floor", "L", "R-2", book)
    mr.resolve("weird:x", "ceiling", "L", "R-3", book)
    assert [r.severity for r in book.records] == ["major", "major"]
    assert "không có trong gói" in book.records[0].reason
    assert "lạ" in book.records[1].reason
    table = mr.materials_table()
    assert table["weird:x"]["prov"] == "default"
    assert table["weird:x"]["editorRef"] == "weird:x"
    assert list(table) == sorted(table)


def test_resolve__stone_role_neutral() -> None:
    """`stone` (threshold, sill, tread) → light neutral stone, prov default."""
    mr = MaterialResolver(PACK, SCENE)
    key = mr.resolve(None, "stone", "L", "D-1", RecordBook())
    entry = mr.materials_table()[key]
    assert key == "default:stone"
    assert entry["prov"] == "default"
    assert entry["roughness"] == 0.5


ED = {
    "defaultStyle": "s1",
    "usageFinish": {"bedroom": "wood"},
    "outdoorFinishNoCeiling": "decking",
    "window": {"frame": "library:oak", "glass": "scene:nope"},
    "styles": {"s1": {"floor": {"wood": "library:oak"}, "ceiling": "scene:wood"}},
}


def test_resolve__editor_defaults() -> None:
    """No ref → editor default ref (prov editor-default); missing default ref → major; unset role → neutral."""
    mr, book = MaterialResolver(PACK, SCENE, ED, None), RecordBook()
    assert mr.style_id == "s1"
    assert mr.style_fallback
    assert mr.resolve(None, "window_frame", "L", "W-1", book) == "library:oak"
    assert mr.materials_table()["library:oak"]["prov"] == "editor-default"
    assert mr.resolve(None, "window_glass", "L", "W-1", book) == "scene:nope"
    assert [r.severity for r in book.records] == ["major"]
    assert mr.resolve(None, "stair_side", "L", "F-1", book) == "default:stair_side"
    assert mr.resolve("scene:wood", "floor", "L", "R-1", book) == "scene:wood"
    assert mr.resolve(None, "ceiling", "L", "R-1", book) == "scene:wood"
    assert mr.materials_table()["scene:wood"]["prov"] == "document"


def test_floor_helpers() -> None:
    """finish ?? usageFinish[usage]; outdoor finish detection; style floor lookup."""
    mr = MaterialResolver(PACK, SCENE, ED, "s1")
    assert not mr.style_fallback
    assert mr.floor_default({"usage": "bedroom"}) == "library:oak"
    assert mr.floor_default({"usage": "kitchen"}) is None
    assert mr.is_outdoor({"finish": "decking"})
    assert not mr.is_outdoor({"usage": "bedroom"})
    plain = MaterialResolver(PACK, SCENE)
    assert not plain.is_outdoor({"finish": "decking"})
    assert not plain.style_fallback
