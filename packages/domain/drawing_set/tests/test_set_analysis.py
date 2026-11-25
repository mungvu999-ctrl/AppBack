"""K-32a PR(3): đề xuất cho từng hình của bộ hồ sơ — ghép K-14, tên hình, tầng của cả bộ, khung tên K-65.

Ca thật: 11 hình của bộ DM-457 (chữ OCR + đáp án loại trong `data/page_kind/views.json`, dữ liệu của K-14) ghép
lên MỘT tờ, mỗi hình một khung; cộng ca tổng hợp cho từng luật (ngưỡng 0,8, không chữ, bìa, khung nhỏ, khung tên).
"""

import json
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest

from packages.domain.drawing_set import classify_page
from packages.domain.drawing_set.set_analysis import (
    NOT_PLAN_SURE,
    VIEW_MIN_AREA,
    PageReading,
    Text,
    ViewGuess,
    assign_texts,
    guess_views,
    solve_set,
)

_DATA: Final = Path(__file__).parent / "data" / "page_kind" / "views.json"
GAP: Final = 200


@cache
def _set(prefix: str) -> list[dict[str, Any]]:
    """Các hình thật của một bộ DM- (theo thứ tự id)."""
    doc = json.loads(_DATA.read_text(encoding="utf-8"))
    return [v for v in doc["views"] if v["id"].startswith(prefix)]


def _texts(view: dict[str, Any], dx: float = 0.0) -> list[Text]:
    """Chữ OCR của một hình, dời ngang `dx` px."""
    return [Text(r[0], (r[1] + dx, r[2], r[3] + dx, r[4])) for r in view["texts"]]


def _sheet(views: list[dict[str, Any]]) -> PageReading:
    """Ghép các hình thành một hàng ngang trên một tờ, mỗi hình một khung (độ chắc 0,9)."""
    boxes, texts, x = [], [], 0.0
    for view in views:
        width, height = view["size"]
        boxes.append(((x, 0.0, x + width, float(height)), 0.9))
        texts += _texts(view, x)
        x += width + GAP
    return PageReading((int(x), max(v["size"][1] for v in views)), boxes, texts)


def line(text: str, box: tuple[float, float, float, float]) -> Text:
    """Một dòng chữ tổng hợp."""
    return Text(text, box)


def plan_page(title: str, size: tuple[int, int] = (2000, 1200)) -> PageReading:
    """Trang một hình có tên lớn ở giữa dưới (kiểu tên hình dưới bản vẽ) và vài nhãn phòng."""
    width, height = size
    rooms = [line(f"P{i}", (200 + 150 * i, 300, 260 + 150 * i, 330)) for i in range(6)]
    title_line = line(title, (width * 0.3, height * 0.85, width * 0.7, height * 0.92))
    return PageReading(size, (), [*rooms, title_line])


# ---------------------------------------------------------------- ca thật
def test_real_sheet_matches_page_classifier_view_by_view() -> None:
    """Mỗi hình trên tờ ghép ra đúng loại và độ tin `classify_page` cho hình đó đứng riêng (chữ đúng khung)."""
    views = _set("DM-457")
    guesses = guess_views([_sheet(views)])
    assert len(guesses) == len(views)
    for view, guess in zip(views, guesses, strict=True):
        alone = classify_page(_texts(view), tuple(view["size"]))
        assert (guess.kind, guess.kind_confidence) == (alone.kind, alone.confidence), view["id"]
        assert guess.machine["cues"] == list(alone.cues)


def test_real_sheet_kinds_and_storeys() -> None:
    """DM-457 trên một tờ: 6/6 mặt bằng ra mặt bằng, 5/5 mặt cắt/mặt đứng ra hình đứng (ba mặt cắt không bong bóng
    trục ra mặt đứng — như K-14 chỉ chữ); ba mặt bằng tầng khác nhau được đề xuất ba số tầng khác nhau."""
    views = _set("DM-457")
    guesses = guess_views([_sheet(views)])
    for view, guess in zip(views, guesses, strict=True):
        expected = {"plan"} if view["kind"] == "plan" else {"section", "elevation"}
        assert guess.kind in expected, view["id"]
    storeys = [g.storey_no for g in guesses if g.kind == "plan"]
    assert all(s is not None for s in storeys[:3])
    assert len({s for s in storeys if s is not None}) >= 3
    assert all(g.storey_no is None for g in guesses if g.kind != "plan")


def test_crop_is_page_ratio_and_ordered() -> None:
    """Khung ra theo tỉ lệ trang (x, y, rộng, cao) trong [0, 1], theo thứ tự đầu vào, `page` trỏ trang."""
    views = _set("DM-445")[:2]
    page = _sheet(views)
    first, second = guess_views([page, page])[:2]
    width = page.size[0]
    assert first.page == 0
    assert second.page == 0
    assert first.crop == pytest.approx((0.0, 0.0, views[0]["size"][0] / width, 1.0))
    assert second.crop[0] == pytest.approx((views[0]["size"][0] + GAP) / width)


# ---------------------------------------------------------------- luật
def test_no_text_view_is_other_with_no_text_question() -> None:
    """Hình không chữ (hay trang chưa đọc) → "other", 0, `NO_TEXT`, không tên, không tầng."""
    for page in (PageReading((800, 600)), PageReading((800, 600), read=False)):
        (guess,) = guess_views([page])
        assert (guess.kind, guess.kind_confidence, guess.title_text, guess.storey_no) == ("other", 0.0, None, None)
        assert guess.machine["questions"] == ["NO_TEXT"]
        assert guess.crop == (0.0, 0.0, 1.0, 1.0)


def test_unread_page_has_no_title_block() -> None:
    """Trang chưa đọc (`read=False`) không tìm khung tên; trang đã đọc có khoá `titleBlock` (rỗng khi không có)."""
    (unread,) = guess_views([PageReading((800, 600), read=False)])
    assert unread.machine["read"] is False
    assert unread.machine["titleBlock"] == {}


def test_title_block_lands_in_machine() -> None:
    """Khung tên K-65 đọc từ chữ CẢ trang (kể cả chữ ngoài khung hình) vào `machine.titleBlock` của mọi hình."""
    page = plan_page("MẶT BẰNG TẦNG 1")
    owner = [line("CHỦ ĐẦU TƯ:", (1600, 1000, 1700, 1020)), line("ÔNG NGUYỄN VĂN AN", (1710, 1000, 1900, 1020))]
    (guess,) = guess_views([PageReading(page.size, (), [*page.texts, *owner])])
    assert guess.machine["titleBlock"]["owner"]["value"] == "ÔNG NGUYỄN VĂN AN"


def test_plan_titles_give_storeys() -> None:
    """Ba trang "MẶT BẰNG TẦNG 1/2/3" → mặt bằng, tầng 1/2/3; tên hình lên `titleText`."""
    pages = [plan_page(f"MẶT BẰNG TẦNG {n}") for n in (1, 2, 3)]
    guesses = guess_views(pages)
    assert [g.kind for g in guesses] == ["plan"] * 3
    assert [g.storey_no for g in guesses] == [1, 2, 3]
    assert all(g.storey_confidence is not None for g in guesses)
    assert guesses[1].title_text == "MẶT BẰNG TẦNG 2"
    assert [g.page for g in guesses] == [0, 1, 2]


def test_solve_set_returns_house_floors_of_plans_only() -> None:
    """`solve_set` trả đúng đề xuất của `guess_views`, kèm đề xuất cả nhà chỉ cho hình máy xếp mặt bằng."""
    pages = [plan_page("MẶT BẰNG TẦNG 1"), PageReading((800, 600)), plan_page("MẶT BẰNG TẦNG 2")]
    guesses, floors = solve_set(pages)
    assert guesses == guess_views(pages)
    assert sorted(floors) == [0, 2]
    assert [floors[i].storey_no for i in (0, 2)] == [1, 2]
    assert solve_set([PageReading((800, 600))])[1] == {}


def test_plan_without_storey_in_title_is_asked() -> None:
    """Mặt bằng mà tên không nói tầng ("MẶT BẰNG BỐ TRÍ NỘI THẤT"): tầng chỉ là đoán (<= 0,7) và hỏi `STOREY_UNSURE`."""
    (guess,) = guess_views([plan_page("MẶT BẰNG BỐ TRÍ NỘI THẤT")])
    assert guess.kind == "plan"
    assert guess.storey_confidence is not None
    assert guess.storey_confidence <= 0.7
    assert "STOREY_UNSURE" in guess.machine["questions"]


def test_duplicate_storey_asks_conflict_once() -> None:
    """Hai mặt bằng cùng tên tầng: câu hỏi tầng thêm vào, mã không lặp; `storeyQuestions` giữ câu hỏi gốc."""
    guesses = guess_views([plan_page("MẶT BẰNG TẦNG 2"), plan_page("MẶT BẰNG TẦNG 2")])
    for guess in guesses:
        questions = guess.machine["questions"]
        assert len(questions) == len(set(questions))
        assert set(questions) <= {"STOREY_UNSURE", "STOREY_CONFLICT", "NOT_A_PLAN"}
        assert "storeyQuestions" in guess.machine


@pytest.mark.parametrize(
    ("confidence", "asked"),
    [(NOT_PLAN_SURE, False), (NOT_PLAN_SURE - 0.01, True)],
)
def test_not_plan_threshold(confidence: float, asked: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Khác mặt bằng ở đúng 0,8 → không hỏi; dưới 0,8 → `NOT_A_PLAN` (ngưỡng K-14 khuyến nghị)."""
    from packages.domain.drawing_set import set_analysis
    from packages.domain.drawing_set.page_kind import PageKindResult

    monkeypatch.setattr(
        set_analysis, "classify_page", lambda *a, **k: PageKindResult("section", confidence, "TITLE_ONLY", ())
    )
    (guess,) = guess_views([plan_page("MẶT CẮT A-A")])
    assert guess.kind == "section"
    assert guess.storey_no is None
    assert (guess.machine["questions"] == ["NOT_A_PLAN"]) is asked


def test_cover_is_other_on_the_wire() -> None:
    """Trang mục lục: K-14 nói `cover` (giữ trong `machine.kind`), dây chỉ có "other" — và hỏi (0,75 < 0,8)."""
    texts = [line("DANH MỤC BẢN VẼ", (800, 100, 1200, 160)), line("KT-01 MẶT BẰNG", (800, 300, 1100, 330))]
    (guess,) = guess_views([PageReading((2000, 1200), (), texts)])
    assert guess.kind == "other"
    assert guess.machine["kind"] == "cover"
    assert guess.machine["questions"] == ["NOT_A_PLAN"]


def test_small_boxes_fall_back_to_whole_page() -> None:
    """Khung < 1 % trang bị bỏ (N59 không lưu lại được); không còn khung nào → một khung cả trang, độ chắc 0."""
    tiny = ((0.0, 0.0, 10.0, 10.0), 0.9)
    (guess,) = guess_views([PageReading((1000, 1000), [tiny])])
    assert guess.crop == (0.0, 0.0, 1.0, 1.0)
    assert guess.machine["split"] == {"score": 0.0}
    assert VIEW_MIN_AREA == 0.01


def test_box_outside_page_is_clamped() -> None:
    """Khung vượt trang (đầu vào không tin) bị kẹp về [0, 1]."""
    (guess,) = guess_views([PageReading((100, 100), [((-50.0, -50.0, 150.0, 80.0), 0.5)])])
    assert guess.crop == (0.0, 0.0, 1.0, 0.8)


def test_assign_texts_smallest_box_wins_and_outside_dropped() -> None:
    """Chữ về khung nhỏ nhất chứa tâm nó, toạ độ dời về góc khung; chữ ngoài mọi khung bị bỏ."""
    outer, inner = (0.0, 0.0, 100.0, 100.0), (50.0, 50.0, 90.0, 90.0)
    texts = [line("A", (10, 10, 20, 20)), line("B", (60, 60, 70, 70)), line("C", (200, 200, 210, 210))]
    got = assign_texts([outer, inner], texts)
    assert [t.text for t in got[0]] == ["A"]
    assert [(t.text, t.box) for t in got[1]] == [("B", (10.0, 10.0, 20.0, 20.0))]


def test_empty_input() -> None:
    """Không trang nào → không hình nào."""
    assert guess_views([]) == []


def test_view_guess_defaults() -> None:
    """Đề xuất mặc định không có tầng và `machine` rỗng."""
    guess = ViewGuess(0, (0.0, 0.0, 1.0, 1.0), "other", 0.0)
    assert guess.storey_no is None
    assert guess.machine == {}
