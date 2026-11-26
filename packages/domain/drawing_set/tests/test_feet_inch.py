"""Chiều dài và cốt ghi feet-inch (NHA-01) → milimét."""

import pytest

from packages.domain.drawing_set.feet_inch import parse_feet_inch


@pytest.mark.parametrize(
    ("text", "mm"),
    [
        ("10'-6\"", 3200.4),
        ("10' 6 1/2\"", 3213.1),
        ("EL. 100'-0\"", 30480.0),
        ("EL 9'-1 1/8\"", 2771.775),
        ("ELEV. +8'-0\"", 2438.4),
        ("10'", 3048.0),
        ("10'-6", 3200.4),  # OCR rơi dấu inch
        ("0'-6\"", 152.4),
        ("-2'-0\"", -609.6),
        ("\N{MINUS SIGN}2'-0\"", -609.6),
        ("8'-0 3/4\"", 2457.45),
        ("8'-3/4\"", 2457.45),  # chỉ có phân số inch
        ("10\N{RIGHT SINGLE QUOTATION MARK}-6\N{RIGHT DOUBLE QUOTATION MARK}", 3200.4),
        ("10\N{PRIME}-6\N{DOUBLE PRIME}", 3200.4),
        ("10'-6''", 3200.4),
        ("10'-6\N{RIGHT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}", 3200.4),
        ("10'\N{EN DASH}6\"", 3200.4),
        ("\N{EN DASH}2'-0\"", -609.6),
        ("999'-0\"", 304495.2),
        ("el. 12' - 11\"", 3937.0),
    ],
)
def test_parse_feet_inch_reads_lengths_and_levels(text: str, mm: float) -> None:
    """Feet bắt buộc; inch, phân số inch, dấu và tiền tố EL. tuỳ chọn; mọi kiểu nháy OCR hay trả."""
    assert parse_feet_inch(text) == pytest.approx(mm)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "BEDROOM",
        "10",
        '6"',  # không có feet: không đủ chắc là feet-inch
        "10'-12\"",  # inch phải < 12
        "10'-6 3/2\"",  # phân số phải < 1
        "10'-6 1/3\"",  # mẫu số inch là 2, 4, 8, 16
        "10'-6 1/0\"",
        "10'-6\" x 8'-0\"",
        "1000'-0\"",  # quá ba chữ số feet
        "10''",
        "3.600",
        "+3.600",
        "'-6\"",
    ],
)
def test_parse_feet_inch_rejects_garbage(text: str) -> None:
    """Chữ rác, cốt mét, inch trần và trị số sai dạng: không đọc được."""
    assert parse_feet_inch(text) is None
