"""K-14: phân loại trang bản vẽ. Ca thật từ bộ DM- (chữ OCR + bong bóng trục) và ca tổng hợp cho từng dấu hiệu."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest

from packages.domain.drawing_set import PageGeometry, PageKindResult, PickedTitle, classify_page

_DATA: Final = Path(__file__).parent / "data" / "page_kind" / "views.json"
SIZE: Final = (2000.0, 1200.0)
_HIGH: Final = 0.9  # ngưỡng cảnh báo `PAGE_NOT_PLAN` của K-14


@dataclass(frozen=True)
class Ocr:
    """Dòng OCR giả: đủ ba thuộc tính của `OcrText`."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float = 1.0


def line(text: str, x0: float = 100, y0: float = 100, x1: float = 160, y1: float = 130) -> Ocr:
    """Dòng OCR giả theo thứ tự hộp (x0, y0, x1, y1)."""
    return Ocr(text, (x0, y0, x1, y1))


def filler(n: int) -> list[Ocr]:
    """`n` dòng chữ nhỏ không mang nghĩa, rải đều để đủ số chữ."""
    return [
        line(f"P{i}", 50 + 30 * (i % 60), 50 + 40 * (i // 60), 80 + 30 * (i % 60), 70 + 40 * (i // 60))
        for i in range(n)
    ]


def stack(*values: str) -> list[Ocr]:
    """Các cốt xếp thành cột dọc (đúng kiểu mặt cắt)."""
    return [line(v, 1800, 100 + 220 * i, 1860, 130 + 220 * i) for i, v in enumerate(values)]


# ---------------------------------------------------------------- ca thật
class _Text:
    """Một dòng chữ OCR theo `OcrText`."""

    def __init__(self, row: list[Any]) -> None:
        self.text, self.box, self.confidence = row[0], (row[1], row[2], row[3], row[4]), 1.0


@cache
def _views() -> dict[str, dict[str, Any]]:
    doc = json.loads(_DATA.read_text(encoding="utf-8"))
    return {v["id"]: v for v in doc["views"]}


def _classify(view_id: str, *, geometry: bool) -> PageKindResult:
    v = _views()[view_id]
    geo = PageGeometry(bubbles=[tuple(b) for b in v["bubbles"]]) if geometry else None
    return classify_page([_Text(r) for r in v["texts"]], (v["size"][0], v["size"][1]), geo)


def _collapse(kind: str) -> str:
    return "plan" if kind in ("roof", "site", "detail") else kind  # đáp án gọi mái/khu đất bản Việt là mặt bằng


@pytest.mark.parametrize(("geometry", "min_rate"), [(False, 0.85), (True, 0.97)])
def test_real_views_accuracy_and_no_silent_error(geometry: bool, min_rate: float) -> None:
    """Loại đúng >= 85 % (chỉ chữ) / 97 % (thêm bong bóng) trên 34 hình thật; không hình nào sai mà độ tin >= 0,9.

    Bộ nhỏ này nhặt gồm các ca khó của DM-457 (chữ ghi chú bị chọn làm tên, mặt cắt không bong bóng), nên chỉ chữ
    thấp hơn số 97,2 % của cả 142 hình (`changes/K-14.md`).
    """
    views = _views()
    right = 0
    for vid, v in views.items():
        got = _classify(vid, geometry=geometry)
        kind = _collapse(got.kind)
        right += kind == v["kind"]
        assert not (got.confidence >= _HIGH and kind != v["kind"]), (vid, got)
        if v["kind"] == "plan":
            assert not (got.confidence >= _HIGH and got.kind in ("section", "elevation", "site")), (vid, got)
    assert right >= min_rate * len(views), (right, len(views))


@pytest.mark.parametrize("vid", ["DM-457__f08_section", "DM-457__f09_section", "DM-457__f10_section"])
def test_misread_note_title_is_overridden_by_level_stack(vid: str) -> None:
    """Mặt cắt không tên: "SIKA FLOOR" bị chọn làm tên (FLOOR -> mặt bằng) nhưng chồng cốt đứng thắng."""
    for geometry in (False, True):
        got = _classify(vid, geometry=geometry)
        assert got.kind in ("section", "elevation")
        assert got.reason == "TITLE_OVERRIDDEN_BY_LEVELS"
        assert got.confidence < 0.7


def test_titled_section_with_level_stack_is_confident() -> None:
    """Có tên "MẶT CẮT" và chồng cốt cao: mặt cắt, độ tin 0,92 (đủ để cảnh báo)."""
    got = classify_page(
        [line("MAT CAT A-A", 800, 1000, 1100, 1040), *filler(8), *stack("+0.000", "+3.600", "+7.200", "+10.800")], SIZE
    )
    assert (got.kind, got.confidence, got.reason) == ("section", 0.92, "TITLE_AND_CONTENT_AGREE")
    assert "CONTENT:LEVELS_VERTICAL" in got.cues


# ---------------------------------------------------------------- tên hình
@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("MẶT BẰNG TẦNG 2", "plan"),
        ("MAËT CAÉT B - B", "section"),  # VNI
        ("mÆt ®øng trôc 1-5", "elevation"),  # TCVN3
        ("MẶT BẰNG MÁI", "roof"),
        ("MẶT BẰNG KHU ĐẤT", "site"),
        ("CHI TIẾT CẦU THANG", "detail"),
        ("PHỐI CẢNH 3D", "other"),
        ("NORTH ELEVATION", "elevation"),
    ],
)
def test_title_decides_kind_without_content(title: str, kind: str) -> None:
    """Chỉ có tên: loại theo tên, độ tin 0,8 (hoặc thấp hơn nếu chỉ khung tên)."""
    got = classify_page([line(title, 800, 1000, 1200, 1040), *filler(10)], SIZE)
    assert got.kind == kind
    assert got.confidence == 0.8
    assert got.reason == "TITLE_ONLY"


def test_title_conflict_keeps_title_but_drops_confidence() -> None:
    """Tên "MẶT CẮT" nhưng lưới trục hai chiều: giữ loại theo tên, độ tin 0,55 (không cảnh báo)."""
    grid = PageGeometry(bubbles=[(100.0 + 450 * i, 100.0 + 300 * j) for i in range(4) for j in range(3)])
    got = classify_page([line("MAT CAT A-A", 800, 1000, 1100, 1040), *filler(8)], SIZE, grid)
    assert (got.kind, got.confidence, got.reason) == ("section", 0.55, "TITLE_CONTENT_CONFLICT")


def test_weak_agreement_and_plan_grid_agreement() -> None:
    """Tên mặt bằng + nhiều chữ: 0,85; thêm lưới trục hai chiều: 0,92; thêm phòng/ô mở: 0,92."""
    title = line("MAT BANG TANG 1", 800, 1000, 1200, 1040)
    assert classify_page([title, *filler(30)], SIZE).confidence == 0.85
    grid = PageGeometry(bubbles=[(100.0 + 450 * i, 100.0 + 300 * j) for i in range(4) for j in range(3)])
    assert classify_page([title, *filler(5)], SIZE, grid).confidence == 0.92
    rooms = PageGeometry(n_rooms=5, n_openings=7, n_walls=40)
    got = classify_page([title, *filler(5)], SIZE, rooms)
    assert (got.kind, got.confidence, "CONTENT:OBJECTS_5R_7O" in got.cues) == ("plan", 0.92, True)


def test_weak_title_source_caps_confidence() -> None:
    """Tên chỉ đọc ra từ khung tên (0,7) hay hai nguồn lệch (0,6): độ tin không vượt độ tin của tên."""
    picked = PickedTitle("MAT CAT A-A", "title_block", 0.7)
    got = classify_page([*filler(8), *stack("+0.000", "+3.600", "+7.200")], SIZE, picked=picked)
    assert (got.kind, got.confidence) == ("section", 0.7)


def test_given_title_skips_ocr_pick() -> None:
    """`picked` truyền vào được dùng, không chọn lại từ chữ."""
    got = classify_page(filler(30), SIZE, picked=PickedTitle("MAT DUNG CHINH", "under_drawing", 0.8))
    assert got.kind == "elevation"


# ---------------------------------------------------------------- dấu hiệu nội dung, không tên
def test_content_only_confidence_never_reaches_alert_threshold() -> None:
    """Không tên hình: mọi kết luận từ nội dung <= 0,75 (bìa), còn lại <= 0,65."""
    cases = [
        classify_page(filler(40), SIZE, PageGeometry(n_rooms=4, n_openings=6)),
        classify_page([*filler(8), *stack("+0.000", "+3.600", "+7.200")], SIZE),
        classify_page(filler(40), SIZE),
        classify_page(filler(1), SIZE),
        classify_page(filler(10), SIZE),
    ]
    assert all(r.confidence < 0.7 for r in cases)


def test_content_only_plan_from_objects_and_many_texts() -> None:
    strong = classify_page(filler(5), SIZE, PageGeometry(n_rooms=3))
    assert (strong.kind, strong.confidence, strong.reason) == ("plan", 0.65, "CONTENT_ONLY")
    weak = classify_page(filler(40), SIZE)
    assert (weak.kind, weak.confidence) == ("plan", 0.5)
    stairs = classify_page(filler(5), SIZE, PageGeometry(n_stairs=1))
    assert (stairs.kind, stairs.confidence) == ("plan", 0.5)


def test_content_only_vertical_guess() -> None:
    """Chồng cốt: hình đứng, nghiêng về mặt đứng; có hàng bong bóng trục thì nghiêng về mặt cắt."""
    texts = [*filler(8), *stack("+0.000", "+3.600", "+7.200")]
    assert classify_page(texts, SIZE).kind == "elevation"
    row = PageGeometry(bubbles=[(100.0 + 300 * i, 600.0) for i in range(5)])
    got = classify_page(texts, SIZE, row)
    assert (got.kind, got.confidence) == ("section", 0.45)
    only_row = classify_page(filler(10), SIZE, row)
    assert (only_row.kind, only_row.confidence) == ("section", 0.3)


def test_empty_and_nothing() -> None:
    """Trang không chữ: gần trống -> đứng yếu 0,3; vài chữ không dấu hiệu -> `other` 0,1."""
    assert classify_page([], SIZE).confidence == 0.3
    got = classify_page(filler(10), SIZE)
    assert (got.kind, got.confidence, got.reason) == ("other", 0.1, "NO_TITLE_NO_CONTENT")


def test_perspective_by_low_axis_share() -> None:
    """Không tên, nét theo trục thấp, không dấu hiệu mặt bằng: phối cảnh -> `other`; có vật thể mặt bằng thì không."""
    geo = PageGeometry(axis_share=0.3)
    got = classify_page(filler(30), SIZE, geo)
    assert (got.kind, got.reason) == ("other", "LOW_AXIS_SHARE")
    plan = classify_page(filler(30), SIZE, PageGeometry(axis_share=0.3, n_rooms=4))
    assert plan.kind == "plan"
    assert classify_page(filler(30), SIZE, PageGeometry(axis_share=0.9)).kind == "plan"


# ---------------------------------------------------------------- bìa
@pytest.mark.parametrize("words", ["MỤC LỤC", "DANH MỤC BẢN VẼ", "TRANG BÌA", "BÌA", "DRAWING LIST", "COVER SHEET"])
def test_cover_words(words: str) -> None:
    got = classify_page([line(words, 700, 100, 1300, 160), *filler(10)], SIZE)
    assert (got.kind, got.confidence, got.reason) == ("cover", 0.75, "COVER_WORDS")


def test_cover_does_not_override_confirmed_title_or_crowded_page() -> None:
    """Tên được khung tên xác nhận thì thắng; trang đông chữ như bản vẽ thật không phải bìa dù có chữ "mục lục"."""
    confirmed = PickedTitle("MAT BANG TANG 1", "under_drawing", 0.9)
    assert classify_page([line("DANH MUC BAN VE"), *filler(10)], SIZE, picked=confirmed).kind == "plan"
    assert classify_page([line("MUC LUC"), *filler(80)], SIZE).kind != "cover"


# ---------------------------------------------------------------- cốt không đủ làm dấu hiệu đứng
@pytest.mark.parametrize(
    "values",
    [("±0.000", "±0.000", "±0.000", "-0.050"), ("+0.000", "+0.450", "+0.900"), ("+0.000", "+3.600")],
)
def test_scattered_or_close_levels_are_not_vertical(values: tuple[str, ...]) -> None:
    """Mặt bằng có vài dấu cốt gần nhau (hoặc chỉ hai cốt) không bị coi là mặt cắt."""
    texts = [*filler(30), *stack(*values)]
    got = classify_page(texts, SIZE)
    assert got.kind == "plan"
    assert "CONTENT:LEVELS_VERTICAL" not in got.cues
