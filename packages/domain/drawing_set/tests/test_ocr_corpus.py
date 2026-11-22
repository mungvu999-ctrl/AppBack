"""Bộ kiểm chữ OCR thật (K-30c): `solve_house` trên `text.json` của pipeline f14, không phải chữ DXF.

Mỗi tệp `data/ocr/<bộ>.json` là một bộ DM- (bộ kiểm thứ ba): mặt bằng theo thứ tự sàn, mặt cắt, chữ OCR từng hình
([chữ, x0, y0, x1, y1, độ tin]) và đáp án rút gọn. Chữ đọc bằng đúng đường của pipeline (`prepare_page` đường iii +
`apps.ml.text.tasks.read_page`, model ghim `rapidocrRec`), trùng từng byte với `text.json` của lượt thật (DN-602).
Bộ đầy đủ (20 bộ) nằm ngoài repo (`F:/runs/dwglab/muc3/ocr_corpus`, `changes/K-30c.md`); ở đây vài bộ nhỏ làm
hồi quy: đổi bộ OCR, phông, bước nắn trang mà tụt thì test đỏ.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest

from packages.domain.drawing_set import HouseProposal, HouseView, SectionView, solve_house

_DATA: Final = Path(__file__).parent / "data" / "ocr"
_SETS: Final = tuple(sorted(p.stem for p in _DATA.glob("*.json")))
_HIGH: Final = 0.9  # nhóm "Nhận tất cả" (muc-3-ke-hoach-trien-khai §7: sai trong nhóm >= 0,9 <= 5 %)
_TOL_MM: Final = 50  # lệch cốt/chiều cao tối đa coi là đúng (cùng bộ chấm `score_all.py` của nghiên cứu)
_MIN_STOREY_RATE: Final = 0.9  # tiêu chí K-30c: số tầng đúng >= 90 % trên mặt bằng có đáp án


class _Text:
    """Một dòng chữ OCR theo `OcrText`."""

    def __init__(self, row: list[Any]) -> None:
        self.text, self.box, self.confidence = row[0], (row[1], row[2], row[3], row[4]), row[5]


def _views(rows: list[dict[str, Any]]) -> list[tuple[str, tuple[_Text, ...], tuple[int, int]]]:
    """(mã, chữ, khổ) của các hình theo thứ tự trong tệp."""
    return [(r["id"], tuple(_Text(t) for t in r["texts"]), (r["size"][0], r["size"][1])) for r in rows]


@cache
def _solved(name: str) -> tuple[dict[str, Any], HouseProposal]:
    """(đáp án, đề xuất) của một bộ; giải một lần cho mọi test."""
    doc = json.loads((_DATA / f"{name}.json").read_text(encoding="utf-8"))
    plans = [HouseView(vid, texts, size, i) for i, (vid, texts, size) in enumerate(_views(doc["plans"]))]
    sections = [SectionView(texts, size) for _, texts, size in _views(doc["sections"])]
    return doc["truth"], solve_house(plans, order_known=True, sections=sections)


def test_corpus_present() -> None:
    """Bộ kiểm có dữ liệu (glob rỗng thì các test tham số hoá dưới đây không chạy gì)."""
    assert len(_SETS) >= 3


@pytest.mark.parametrize("name", _SETS)
def test_no_silent_error_in_high_band(name: str) -> None:
    """Không số tầng, cốt hay chiều cao nào ở >= 0,9 mà sai (lỗi im lặng: người dùng bấm "Nhận tất cả")."""
    truth, got = _solved(name)
    floors = {f.floor_id: f for f in got.floors}
    for vid, number in truth["storeys"].items():
        f = floors[vid]
        assert not (f.storey_confidence >= _HIGH and f.storey_no != number), (vid, f.storey_no, number)
    for vid, mm in truth["levels_mm"].items():
        f = floors[vid]
        if f.elevation_mm is not None and (f.elevation_confidence or 0) >= _HIGH:
            assert abs(f.elevation_mm - mm) <= _TOL_MM, (vid, f.elevation_mm, mm)
    by_no = {f.storey_no: f for f in got.floors if f.kind == "plan" and f.storey_no is not None}
    for low, mm in truth["heights_mm"].items():
        plan = by_no.get(int(low))
        if plan is not None and plan.height_mm is not None and (plan.height_confidence or 0) >= _HIGH:
            assert abs(plan.height_mm - mm) <= _TOL_MM, (low, plan.height_mm, mm)


def test_storey_numbers_mostly_right() -> None:
    """Số tầng đúng >= `_MIN_STOREY_RATE` trên mọi mặt bằng có đáp án của cả bộ kiểm."""
    right = total = 0
    for name in _SETS:
        truth, got = _solved(name)
        floors = {f.floor_id: f for f in got.floors}
        total += len(truth["storeys"])
        right += sum(floors[vid].storey_no == n for vid, n in truth["storeys"].items())
    assert right >= _MIN_STOREY_RATE * total, (right, total)
