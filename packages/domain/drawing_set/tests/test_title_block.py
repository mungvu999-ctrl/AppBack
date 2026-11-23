"""K-65: khung tên bản vẽ. Ca thật từ bộ DM- (chữ OCR, đáp án đọc tay) và ca tổng hợp cho từng cách bố trí."""

import json
import math
import re
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest

from packages.domain.drawing_set import TitleBlock, TitleBlockField, fold, read_title_block

_DATA: Final = Path(__file__).parent / "data" / "title_block" / "views.json"
_FIELDS: Final = ("owner", "project", "location", "sheet_no", "date")


@dataclass(frozen=True)
class Ocr:
    """Dòng OCR giả: đủ ba thuộc tính của `OcrText`."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float = 1.0


def at(text: str, x: float, y: float, w: float = 160, h: float = 24) -> Ocr:
    """Dòng OCR giả với góc trên trái (x, y), rộng w, cao h."""
    return Ocr(text, (x, y, x + w, y + h))


def _val(field: TitleBlockField | None) -> str | None:
    """Giá trị của trường, hoặc None."""
    return field.value if field else None


# ---------------------------------------------------------------- ca thật
@cache
def _views() -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = json.loads(_DATA.read_text(encoding="utf-8"))["views"]
    return views


def _read(view: dict[str, Any]) -> TitleBlock:
    return read_title_block([Ocr(r[0], (r[1], r[2], r[3], r[4])) for r in view["texts"]])


def _squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", fold(text))


def _matches(expected: str, got: str) -> bool:
    """Đáp án là tiền tố hoặc nằm trong chữ đọc được (đáp án đọc tay bỏ phần OCR nhiễu ở cuối)."""
    exp, value = _squash(expected), _squash(got)
    return value.startswith(exp) or exp in value


def test_real_views_precision_and_recall() -> None:
    """Trên 13 khung tên thật: trường trả ra đúng >= 95 %, không trường nào trả ra khi đáp án là None
    (không nhãn/không giá trị), và đọc được >= 80 % số trường có đáp án."""
    right = wrong = expected_total = spurious = 0
    for view in _views():
        block = read_title_block([Ocr(r[0], (r[1], r[2], r[3], r[4])) for r in view["texts"]])
        for name in _FIELDS:
            exp, got = view["expected"][name], getattr(block, name)
            expected_total += exp is not None
            spurious += exp is None and got is not None
            if exp is not None and got is not None:
                right += _matches(exp, got.value)
                wrong += not _matches(exp, got.value)
    assert spurious == 0
    assert right / (right + wrong) >= 0.95, (right, wrong)
    assert right / expected_total >= 0.8, (right, expected_total)


@pytest.mark.parametrize(
    ("view_id", "field", "value"),
    [
        ("DM-448__f04_plan", "owner", "MR MINH"),
        ("DM-448__f04_plan", "location", "HA dONG - HA NOI"),
        ("DM-448__f04_plan", "date", "10/2018"),  # nhãn "Ti LE HON THANH TIME", giá trị dưới nhãn
        ("DM-457__f00_plan", "date", "10/2009"),  # dòng dịch "1st issue of current phase" nằm giữa nhãn và giá trị
        ("DM-479__f02_plan", "date", "03/2015"),  # "thong 03/2015": chỉ lấy phần ngày
        ("DM-528__f00_plan", "date", "30.03.2001"),  # cùng dòng sau nhãn, khoảng trắng thừa
        ("DM-528__f00_plan", "sheet_no", "a2 - 06"),
        ("DM-3__f00_section", "project", "BIET THU' GIA DINH"),  # nhãn "CONG TRiNH", giá trị dưới
        ("DM-531__f00_plan", "project", "khu nha 0 gia dinh 4 tang"),  # nhãn sai chính tả "cung tronh:"
    ],
)
def test_real_field(view_id: str, field: str, value: str) -> None:
    view = next(v for v in _views() if v["id"] == view_id)
    got = getattr(_read(view), field)
    assert _val(got) == value


def test_real_views_stay_none_when_unsure() -> None:
    """Nhãn có nhưng giá trị không có trong OCR, hoặc giá trị trùng nhãn khác: None, không đoán."""
    by_id = {v["id"]: _read(v) for v in _views()}
    assert by_id["DM-445__f01_plan"].owner is None  # "chu dau tur :" không có giá trị
    assert by_id["DM-445__f01_plan"].location is None
    assert by_id["DM-430__f00_plan"].owner is None  # "ONG ....." là chỗ trống
    assert by_id["DM-457__f00_plan"].project is None  # "TEN DU AN_PROJECT NAME" không có giá trị
    assert by_id["DM-4__f00_plan"].sheet_no is None  # "KT : 01" không có nhãn
    assert by_id["DM-447__f00_plan"].owner is None  # giá trị dưới "chu NHA" bị nhãn "cung tronh" chắn


# ---------------------------------------------------------------- bố trí tổng hợp
def test_inline_right_below_sources() -> None:
    """Giá trị cùng dòng sau dấu hai chấm, bên phải nhãn, và dưới nhãn."""
    texts = [
        at("Chu dau tu: Nguyen Van An", 100, 100, 300),
        at("Cong trinh", 100, 200, 120),
        at("Nha pho 3 tang", 260, 200, 200),
        at("Dia diem", 100, 300, 100),
        at("Phuong Lang Ha, Dong Da, Ha Noi", 110, 340, 300),
    ]
    block = read_title_block(texts)
    assert block.owner == TitleBlockField("Nguyen Van An", 0.9, "same_line")
    assert block.project == TitleBlockField("Nha pho 3 tang", 0.85, "right")
    assert block.location == TitleBlockField("Phuong Lang Ha, Dong Da, Ha Noi", 0.8, "below")
    assert (block.date, block.sheet_no) == (None, None)


def test_multi_line_value_joins_aligned_lines() -> None:
    """Công trình xuống dòng: nối tối đa ba dòng cùng cột, độ tin giảm; dừng ở nhãn kế tiếp."""
    texts = [
        at("TEN CONG TRINH", 100, 100, 200),
        at("NHA O KET HOP", 100, 140, 200),
        at("KINH DOANH 4 TANG", 100, 172, 200),
        at("DIA DIEM", 100, 230, 100),
        at("Cau Giay", 100, 270, 100),
    ]
    block = read_title_block(texts)
    assert _val(block.project) == "NHA O KET HOP KINH DOANH 4 TANG"
    assert block.project is not None
    assert block.project.confidence == 0.75
    assert _val(block.location) == "Cau Giay"


def test_label_between_blocks_the_value() -> None:
    """Ô nhãn khác chen giữa: giá trị bên dưới thuộc nhãn kia, không phải nhãn này."""
    texts = [at("CHU DAU TU", 100, 100, 150), at("DIA DIEM", 100, 160, 100), at("Quan 7", 100, 200, 100)]
    block = read_title_block(texts)
    assert block.owner is None
    assert _val(block.location) == "Quan 7"


def test_label_on_the_same_line_blocks_the_value() -> None:
    """Nhãn thứ hai cùng dòng, ở giữa nhãn đầu và giá trị: giá trị thuộc nhãn thứ hai."""
    block = read_title_block([at("CHU DAU TU", 100, 100, 120), at("DIA CHI", 260, 100, 80), at("Quan 7", 360, 100, 80)])
    assert block.owner is None
    assert _val(block.location) == "Quan 7"


def test_combined_project_location_label_is_ambiguous() -> None:
    """ "CÔNG TRÌNH - ĐỊA ĐIỂM:" ghép hai trường: không tách được nên cả hai là None."""
    block = read_title_block([at("CONG TRINH - DIA DIEM:", 100, 100, 260), at("Nha mau", 100, 140, 120)])
    assert (block.project, block.location) == (None, None)


def test_two_different_values_tie_gives_none_but_noisy_copies_agree() -> None:
    """Hai ô nhãn cùng loại, hai giá trị khác hẳn, cùng điểm: None. Hai bản chỉ khác do OCR nhiễu: lấy được."""
    base = [at("DIA CHI:", 100, 100, 100), at("So 5 ngo 6 Cau Dien Ha Noi", 100, 140, 300)]
    other = [at("ADDRESS", 900, 100, 100), at("45 Tran Phu Da Nang", 900, 140, 300)]
    assert read_title_block([*base, *other]).location is None
    noisy = [at("ADDRESS", 900, 100, 100), at("So 5 ngo 6 Cau Di&n Ha Noi", 900, 140, 300)]
    assert read_title_block([*base, *noisy]).location is not None


# ---------------------------------------------------------------- ngày và số hiệu
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("31/10/2020", "31/10/2020"),
        ("10/2009", "10/2009"),
        ("24-03-20", "24-03-20"),
        ("30.03. 2001", "30.03.2001"),
        ("ngay 5 thang 3 nam 2020", "5 thang 3 nam 2020"),
    ],
)
def test_date_formats(text: str, expected: str) -> None:
    block = read_title_block([at("NGAY PHAT HANH", 100, 100, 160), at(text, 100, 140, 200)])
    assert _val(block.date) == expected


@pytest.mark.parametrize("text", ["1/50", "1/100", "A3", "12", "TL 1/75", "ngay"])
def test_not_a_date(text: str) -> None:
    """Tỉ lệ bản vẽ, khổ giấy, số trần không phải ngày."""
    assert read_title_block([at("NGAY", 100, 100, 60), at(text, 100, 140, 120)]).date is None


def test_revision_table_date_is_rejected() -> None:
    """Ngày của bảng sửa đổi không phải ngày phát hành: nhãn "NGÀY" dưới tiêu đề SỬA ĐỔI, hay tự nói sửa đổi."""
    table = [at("SUA DOI - REVISION", 100, 60, 200), at("NGAY - DATE", 100, 100, 120), at("05/2019", 100, 140, 100)]
    assert read_title_block(table).date is None
    assert read_title_block([at("Ngay sua doi ban ve", 100, 100, 200), at("05/2019", 100, 140, 100)]).date is None


@pytest.mark.parametrize("text", ["KT-01", "A-02", "KT05", "NT - 01", "A2 - 06", "KT24", "AR-101"])
def test_sheet_number_formats(text: str) -> None:
    block = read_title_block([at("SO BAN VE", 100, 100, 140), at(text, 100, 140, 120)])
    assert _val(block.sheet_no) == text


@pytest.mark.parametrize("text", ["MAT BANG TANG 1", "1/100", "2024", "A", ""])
def test_not_a_sheet_number(text: str) -> None:
    assert read_title_block([at("KY HIEU BAN VE", 100, 100, 160), at(text or "-", 100, 140, 120)]).sheet_no is None


def test_legend_label_is_not_sheet_number() -> None:
    """ "KÝ HIỆU GHI CHÚ" (bảng chú giải) không phải nhãn số bản vẽ."""
    assert read_title_block([at("KY HIEU GHI CHU", 100, 100, 160), at("KT-01", 100, 140, 120)]).sheet_no is None


# ---------------------------------------------------------------- chữ cũ, giá trị xấu, đầu vào thù địch
def test_legacy_encodings_label_and_value() -> None:
    """Nhãn VNI ("CHỦ ĐẦU TƯ") và giá trị TCVN3 được giải mã ra Unicode NFC."""
    block = read_title_block([at("CHUÛ ÑAÀU TÖ:", 100, 100, 160), at("ph¹m vi s©n th­îng", 100, 140, 200)])
    assert _val(block.owner) == "phạm vi sân thượng"


def test_value_must_look_like_a_name() -> None:
    """Giá trị toàn số, quá ngắn, là nhãn khác hay kết thúc bằng ":" không được nhận."""
    for junk in ("123456", "ab", "GIAM DOC", "dia dlbm xay dyng:"):
        assert read_title_block([at("CONG TRINH", 100, 100, 120), at(junk, 100, 140, 160)]).project is None


def test_rotated_text_is_ignored() -> None:
    """Chữ xoay 90 độ (hộp cao hơn rộng nhiều): hình học bên phải/bên dưới không còn đúng nên bỏ."""
    rotated = [Ocr("CHU DAU TU", (100, 100, 124, 260)), Ocr("Nguyen Van An", (130, 100, 154, 300))]
    assert read_title_block(rotated) == TitleBlock()


def test_empty_and_hostile_input() -> None:
    """Không chữ, chữ rỗng, hộp NaN/âm/thiếu, chuỗi dài, một vạn dòng: không ném lỗi, nhanh, mọi trường None."""
    assert read_title_block([]) == TitleBlock()
    bad = [
        Ocr("", (0, 0, 1, 1)),
        Ocr("CHU DAU TU", (math.nan, 0, 10, 10)),
        Ocr("CHU DAU TU", (10, 10, 5, 20)),
        Ocr("CONG TRINH " * 500, (0, 0, 100, 20)),
        Ocr(None, (0, 0, 1, 1)),  # type: ignore[arg-type]
        Ocr("NGAY", None),  # type: ignore[arg-type]
    ]
    assert read_title_block(bad) == TitleBlock()
    flood = [at(f"CHU DAU TU {i}", 10 + (i % 50), 10 + i, 100) for i in range(10_000)]
    start = time.perf_counter()
    read_title_block(flood)
    # Trần rộng: chỉ bắt lỗi bậc hai (10k dòng → hàng phút), không đo tốc độ — cổng song song chạy chậm 2–3×.
    assert time.perf_counter() - start < 10.0
