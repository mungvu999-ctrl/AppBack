"""Đọc chữ mái (NHA-05b): độ dốc, vật liệu và chữ thoát nước từ hình mặt bằng mái và mặt cắt."""

import pytest

from packages.domain.drawing_set.roof_reading import RoofReading, read_roof
from packages.domain.drawing_set.tests.test_proposal import _section, _Text


def _texts(*lines: str) -> list[_Text]:
    """Các dòng OCR giả, mỗi dòng một hộp."""
    return [_Text(line, (100, 100 + 40 * i, 300, 130 + 40 * i)) for i, line in enumerate(lines)]


def test_nothing_to_read() -> None:
    """Không hình mái, không mặt cắt: bản đọc rỗng."""
    assert read_roof({}, []) == RoofReading((), None, drains=False, roof_view_ids=())


def test_roof_plan_slope_carries_its_view() -> None:
    """Độ dốc ghi trên mặt bằng mái: nguồn `roofPlan`, kèm hình đọc ra; hình mái nào cũng được liệt kê."""
    reading = read_roof({"r0": _texts("MẶT BẰNG MÁI", "i=30%"), "r1": _texts("GHI CHÚ")}, [])
    (pitch,) = reading.pitches
    assert (pitch.source, pitch.view_ids, pitch.slope.text) == ("roofPlan", ("r0",), "i=30%")
    assert pitch.slope.rise_over_run == pytest.approx(0.3)
    assert reading.roof_view_ids == ("r0", "r1")


def test_section_slope_has_no_view() -> None:
    """Độ dốc trên mặt cắt: nguồn `section`, không có mã hình (mặt cắt chưa mang id)."""
    (pitch,) = read_roof({}, [_section(("6:12", 200.0), ("+3.300", 600.0))]).pitches
    assert (pitch.source, pitch.view_ids, pitch.slope.notation) == ("section", (), "in_12")


def test_one_reading_per_source_roof_plan_first() -> None:
    """Mỗi nguồn một trị số, mặt bằng mái đứng trước mặt cắt."""
    reading = read_roof({"r0": _texts("30°")}, [_section(("35°", 200.0))])
    assert [(p.source, p.slope.text) for p in reading.pitches] == [("roofPlan", "30°"), ("section", "35°")]


def test_steepest_reading_wins_within_a_source() -> None:
    """Mái dốc có sê nô ghi `i=2%`: lấy trị số dốc nhất của nguồn đó."""
    (pitch,) = read_roof({"r0": _texts("i=2%", "i=45%", "i=2%")}, []).pitches
    assert pitch.slope.text == "i=45%"


def test_clear_reading_beats_an_ambiguous_one() -> None:
    """Dạng `a:b` nhập nhằng chỉ được dùng khi nguồn đó không có trị số nào rõ, kể cả khi nó dốc hơn."""
    (pitch,) = read_roof({"r0": _texts("1:1", "i=30%")}, []).pitches
    assert (pitch.slope.text, pitch.slope.ambiguous) == ("i=30%", False)
    (alone,) = read_roof({"r0": _texts("1:2")}, []).pitches
    assert alone.slope.ambiguous


@pytest.mark.parametrize(
    ("line", "material"),
    [
        ("MÁI LỢP NGÓI", "tile"),
        ("ROOF TILES", "tile"),
        ("MÁI TÔN", "metal"),
        ("LỢP TÔN LẠNH", "metal"),
        ("METAL ROOFING", "metal"),
        ("ASPHALT SHINGLES", "shingle"),
    ],
)
def test_material_keywords(line: str, material: str) -> None:
    """Ba vật liệu mái đọc bằng từ khoá, không phân biệt hoa thường và dấu."""
    found = read_roof({"r0": _texts(line)}, []).material
    assert found is not None
    assert (found.material, found.source, found.view_ids) == (material, "roofPlan", ("r0",))


def test_material_from_a_section_and_none_without_keywords() -> None:
    """Mặt cắt cũng cho vật liệu (nguồn `section`); chữ không có từ khoá thì không có vật liệu."""
    found = read_roof({}, [_section(("MÁI NGÓI", 100.0))]).material
    assert found is not None
    assert (found.material, found.source, found.view_ids) == ("tile", "section", ())
    assert read_roof({"r0": _texts("CERAMIC TILE", "TỒN KHO", "+3.300")}, []).material is None
    assert read_roof({"r0": _texts("CERAMIC TILE ROOFTOP", "METAL ROOFTOP UNIT")}, []).material is None


@pytest.mark.parametrize("line", ["SÊ NÔ", "T.O. PARAPET", "ROOF DRAIN", "SCUPPER"])
def test_drain_words(line: str) -> None:
    """Chữ sê nô, parapet, drain, scupper trên hình mái hoặc mặt cắt: mái bằng có thoát nước."""
    assert read_roof({"r0": _texts(line)}, []).drains
    assert read_roof({}, [_section((line, 100.0))]).drains
    assert not read_roof({"r0": _texts("MẶT BẰNG MÁI")}, []).drains


def test_ambiguous_ratio_on_a_section_is_ignored() -> None:
    """`1:2`, `1:5` trên mặt cắt thường là tỉ lệ của chi tiết: không lấy làm độ dốc; trên hình mái thì vẫn lấy."""
    assert read_roof({}, [_section(("1:2", 100.0), ("1:5", 200.0))]).pitches == ()
    assert [p.slope.text for p in read_roof({"r0": _texts("1:2")}, [_section(("1:2", 100.0))]).pitches] == ["1:2"]
