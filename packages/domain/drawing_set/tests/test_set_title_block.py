"""K-65 đợt 2: khung tên chung của bộ và số hiệu từng hình, từ `machine.titleBlock` đã lưu."""

from typing import Any

import pytest

from packages.domain.drawing_set.set_title_block import common_title_block, sheet_no_of


def machine(**fields: str | tuple[str, float]) -> dict[str, Any]:
    """`machine` có khung tên; giá trị là chữ (độ tin 0,9) hoặc `(chữ, độ tin)`."""
    block = {
        name: {"value": v[0], "confidence": v[1]} if isinstance(v, tuple) else {"value": v, "confidence": 0.9}
        for name, v in fields.items()
    }
    return {"titleBlock": block}


def test_sheet_no_of_reads_value_and_ignores_bad_shapes() -> None:
    bad: object
    assert sheet_no_of(machine(sheetNo="KT-01")) == "KT-01"
    assert sheet_no_of(machine(sheetNo="  KT-02 ")) == "KT-02"
    for bad in ({}, None, "x", {"titleBlock": []}, {"titleBlock": {"sheetNo": "KT-1"}}, machine(sheetNo="   ")):
        assert sheet_no_of(bad) is None


def test_common_value_when_pages_agree_noise_tolerated() -> None:
    pages = [
        (("f", 0), machine(owner="Anh Nguyễn Văn Tho", project="Nhà phố An", location="Cau Dien, Ha Noi")),
        (("f", 1), machine(owner="Anh Nguyễn Văn Thq", project="Nhà phố An", location="Cau Din, Ha Noi")),
    ]
    assert common_title_block(pages) == {
        "owner": "Anh Nguyễn Văn Tho",
        "project": "Nhà phố An",
        "location": "Cau Dien, Ha Noi",
    }


def test_conflicting_field_is_dropped_others_kept() -> None:
    pages = [
        (("f", 0), machine(owner="Anh An", project="Nhà phố An")),
        (("f", 1), machine(owner="Chị Bình", project="Nhà phố An")),
    ]
    assert common_title_block(pages) == {"project": "Nhà phố An"}


def test_date_needs_exact_match_one_digit_off_is_a_different_date() -> None:
    assert common_title_block([(("f", 0), machine(date="12/11/2026")), (("f", 1), machine(date="13/11/2026"))]) == {}
    assert common_title_block([(("f", 0), machine(date="12/11/2026")), (("f", 1), machine(date="12/11/2026"))]) == {
        "date": "12/11/2026"
    }


def test_pages_without_a_field_do_not_vote_and_one_page_is_enough() -> None:
    pages = [(("f", 0), machine(project="Nhà phố An")), (("f", 1), {}), (("f", 2), machine(owner="Anh An"))]
    assert common_title_block(pages) == {"project": "Nhà phố An", "owner": "Anh An"}


def test_each_page_counts_once_first_view_of_the_page_wins() -> None:
    pages = [(("f", 0), machine(project="Nhà phố An")), (("f", 0), machine(project="Biệt thự Lan"))]
    assert common_title_block(pages) == {"project": "Nhà phố An"}


def test_representative_is_highest_confidence_and_sheet_no_is_not_common() -> None:
    pages = [
        (("f", 0), machine(project=("Nha pho An", 0.7), sheetNo="KT-01")),
        (("f", 1), machine(project=("Nhà phố An", 0.95), sheetNo="KT-01")),
    ]
    assert common_title_block(pages) == {"project": "Nhà phố An"}


@pytest.mark.parametrize("bad", [None, "x", 5, {"titleBlock": None}, {"titleBlock": {"owner": {"value": 3}}}])
def test_bad_shapes_are_absent_not_errors(bad: object) -> None:
    assert common_title_block([(("f", 0), bad)]) == {}
