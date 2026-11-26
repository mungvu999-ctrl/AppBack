"""Độ dốc mái ghi bằng chữ (NHA-01): phần trăm, độ, x:12, và dạng a:b nhập nhằng."""

import math

import pytest

from packages.domain.drawing_set.slope import parse_slope


@pytest.mark.parametrize(
    ("text", "ratio", "notation"),
    [
        ("30%", 0.30, "percent"),
        ("i=30%", 0.30, "percent"),
        ("I = 12,5 %", 0.125, "percent"),
        ("i=2%", 0.02, "percent"),
        ("0%", 0.0, "percent"),
        ("30°", math.tan(math.radians(30)), "degree"),
        ("22.5º", math.tan(math.radians(22.5)), "degree"),
        ("6:12", 0.5, "in_12"),
        ("6/12", 0.5, "in_12"),
        ("4.5 : 12", 0.375, "in_12"),
        ("12:12", 1.0, "in_12"),
        ("24:12", 2.0, "in_12"),
        ("999%", 9.99, "percent"),  # 84,3°: phần trăm ba chữ số không bao giờ chạm trần 85°
        ("84.9°", math.tan(math.radians(84.9)), "degree"),
    ],
)
def test_parse_slope_reads_explicit_notations(text: str, ratio: float, notation: str) -> None:
    """Ba kiểu ghi rõ ra tỉ số cao/ngang, giữ chữ gốc và kiểu ghi, không nhập nhằng."""
    slope = parse_slope(text)
    assert slope is not None
    assert slope.rise_over_run == pytest.approx(ratio)
    assert (slope.text, slope.notation, slope.ambiguous) == (text, notation, False)


@pytest.mark.parametrize(("text", "ratio"), [("1:2", 0.5), ("i=1:3", 1 / 3), ("1:1.5", 1 / 1.5), ("2:9", 2 / 9)])
def test_ratio_without_twelve_is_ambiguous(text: str, ratio: float) -> None:
    """`a:b` mẫu số khác 12: lưu theo cách đọc cao:ngang và đánh dấu nhập nhằng để lớp trên hỏi (luật M-19)."""
    slope = parse_slope(text)
    assert slope is not None
    assert slope.rise_over_run == pytest.approx(ratio)
    assert (slope.text, slope.notation, slope.ambiguous) == (text, "ratio", True)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "MÁI NGÓI",
        "1:50",  # tỉ lệ bản vẽ
        "1:100",
        "1:10",  # tỉ lệ chi tiết: mẫu số từ 10 không phải độ dốc viết trần
        "1/2",  # phân số inch, không phải độ dốc
        "1/50",
        "30",  # số trần là kích thước
        "85°",  # từ 85° là tường, không phải mái (luật M-17)
        "90°",
        "1200%",  # bốn chữ số
        "200°",  # tan dương nhưng không phải góc mái
        "99:1",  # tỉ số từ tan 85° = 11,43 trở lên
        "25:12",  # quá 24:12
        "120/12",
        "9:05",  # giờ
        "05%",
        "1 0%",  # hai mẩu chữ, không dán thành "10%"
        "10 5:12",
        "0:0",
        "1:0",
        "TL 30%",
        "30% x 2",
        "i=",
        "-30%",
    ],
)
def test_parse_slope_rejects_non_slopes(text: str) -> None:
    """Chữ rác, tỉ lệ bản vẽ, phân số inch, số trần và trị số ngoài dải mái: không đọc được."""
    assert parse_slope(text) is None
