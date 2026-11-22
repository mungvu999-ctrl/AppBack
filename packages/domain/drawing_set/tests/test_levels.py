"""Cốt trên bản vẽ: chép các `selfcheck` của `read_levels.py`, `read_levels2.py`, `solve_set.py`, `heights3.py`."""

import dataclasses
import math
from dataclasses import dataclass

import pytest

from packages.domain.drawing_set.levels import (
    LevelMark,
    MainLevel,
    level_groups,
    levels_from_texts,
    main_floor_level,
    norm_zero,
    parse_level,
    parse_level_loose,
    parse_level_mm,
)


@dataclass(frozen=True)
class _Text:
    """Chữ giả có đúng ba trường mà `TextPx` có."""

    text: str
    box: tuple[float, float, float, float] = (10.0, 10.0, 100.0, 30.0)
    confidence: float = 0.9


def _column(*texts: str) -> list[_Text]:
    """Một cột chữ xếp dọc, mỗi chữ một hộp riêng."""
    return [_Text(t, (10, 10 + 30 * i, 100, 30 + 30 * i)) for i, t in enumerate(texts)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+3.600", 3.6),
        ("±0.000", 0.0),
        ("-0,450", -0.45),
        ("3.600", 3.6),
        ("+12.900", 12.9),
        ("+3.60", 3.6),
        ("\N{MINUS SIGN}0.450", -0.45),  # dấu trừ Unicode
        ("\N{REPLACEMENT CHARACTER}3.600", 3.6),  # ký tự thay thế = dấu bị hỏng, tính là dương
        ("+ 3.600", 3.6),  # khoảng trắng bị bỏ
        ("-10.000", -10.0),
        ("+60.000", 60.0),
    ],
)
def test_parse_level_accepts_level_marks(text: str, expected: float) -> None:
    """Dạng chặt: dấu + 1-2 chữ số + 2-3 chữ số thập phân, hoặc số trần 3 chữ số thập phân."""
    assert parse_level(text) == expected


@pytest.mark.parametrize(
    "text",
    ["A", "", "1200", "3600", "12.5", "99.000", "-10.001", "+60.001", "FL:+2.900", "+0.000)", "1.5m", "-0,25m"],
)
def test_parse_level_rejects_everything_else(text: str) -> None:
    """Chữ không phải cốt: không chữ số thập phân, ngoài dải, hay có rác quanh số."""
    assert parse_level(text) is None


def test_parse_level_negative_zero_is_plain_zero() -> None:
    """Chuỗi `-0.000` ra 0.0 dương, không phải -0.0."""
    value = parse_level("-0.000")
    assert value == 0.0
    assert math.copysign(1.0, value) == 1.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FL:+2.900", 2.9),
        ("|+17.000", 17.0),
        ("1±0.000", 0.0),  # ký hiệu đọc thành "1"
        ("< +6.900", 6.9),
        ("+0.000)", 0.0),
        ("+6.9001", 6.9),  # thừa "1" cuối
        ("+2.900|", 2.9),
        ("+2.900]", 2.9),
        ("FL +2.900", 2.9),
        ("- 0.02", -0.02),
        ("FL1 +8.950", 8.95),  # chữ số dính dấu cộng
        ("1+8.950", 8.95),
        ("-0,25m", -0.25),
        ("+3,30m", 3.3),
        ("\N{MINUS SIGN}0.450", -0.45),
        ("ABCD+2.900", 2.9),  # tiền tố dài đúng 4 ký tự
        ("+3.600", 3.6),
    ],
)
def test_parse_level_loose_tolerates_junk_around_the_number(text: str, expected: float) -> None:
    """Đọc lỏng nhận tiền tố ngắn, một ký tự thừa sau số, hậu tố `m`, nhưng luôn cần dấu."""
    assert parse_level_loose(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hop kt < +11.75",  # tiền tố là tên phòng
        "P.NGU 3 4± 8.300",
        "ABCDE+2.900",  # tiền tố 5 ký tự
        "+13.600+50",  # rác sau số
        "+13.26p",
        "3,30m",  # chiều dài, không dấu
        "0.450",  # không dấu: có thể là chiều dài
        "1610.050",
        "2850",
        "3600",
        "2.5m2",
        "20 m2",
        "+61.000",  # ngoài dải
        "-10.500",
    ],
)
def test_parse_level_loose_rejects_notes_lengths_and_unsigned(text: str) -> None:
    """Tên phòng, chiều dài, số trần, ngoài dải đều bị từ chối: nhận nhầm tệ hơn bỏ sót."""
    assert parse_level_loose(text) is None


@pytest.mark.parametrize(
    "text", ["+/-0.00", "+-0.000", "±000.00", "%%p0.000", "%%P000.00", "+ / - 0.00", "+-0,00", "|+/-0.00"]
)
def test_norm_zero_rewrites_zero_spellings(text: str) -> None:
    """Mọi cách viết số 0 mà bộ đọc chặt bỏ sót đổi thành "±0.000"."""
    assert norm_zero(text) == "±0.000"
    assert parse_level(norm_zero(text)) == 0.0


@pytest.mark.parametrize("text", ["+3.600", "0.000", "+0.000", "-0.000", "±0000.00", "±0.0", "+/-0.50", "", "A"])
def test_norm_zero_keeps_other_texts(text: str) -> None:
    """Chữ không phải cốt 0 trả nguyên, kể cả cốt 0 viết thường ("+0.000" đã đọc được)."""
    assert norm_zero(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+3600", 3.6),
        ("+ 6900", 6.9),
        ("+10200", 10.2),
        ("±0", 0.0),
        ("+0000", 0.0),
        ("-450", -0.45),
        ("\N{MINUS SIGN}450", -0.45),
        ("%%p0", 0.0),
        ("-0", 0.0),
        (" +3600 ", 3.6),
        ("+60000", 60.0),
    ],
)
def test_parse_level_mm_accepts_signed_millimetres(text: str, expected: float) -> None:
    """Cốt milimét: dấu bắt buộc, bội của 10, không quá 60000."""
    assert parse_level_mm(text) == expected


@pytest.mark.parametrize("text", ["3600", "1200", "+3605", "+70000", "+60010", "+12", "+3.600", "", "A+3600"])
def test_parse_level_mm_rejects_dimensions_and_garbage(text: str) -> None:
    """Không dấu, không bội của 10, quá lớn, quá ngắn hay có dấu thập phân đều không phải cốt milimét."""
    assert parse_level_mm(text) is None


def test_levels_from_texts_reads_strict_and_loose_marks() -> None:
    """Cốt chặt và cốt lỏng đều vào danh sách; tâm là tâm hộp, chữ giữ nguyên bản gốc."""
    marks = levels_from_texts(
        [_Text("FL:+2.900", (10, 10, 100, 30)), _Text("+1.000", (10, 50, 100, 70)), _Text("Phòng khách")]
    )
    assert marks == (
        LevelMark(2.9, "FL:+2.900", 55.0, 20.0, "decimal"),
        LevelMark(1.0, "+1.000", 55.0, 60.0, "decimal"),
    )


def test_levels_from_texts_normalises_zero_spellings_but_keeps_raw_text() -> None:
    """Chuỗi `+/-0.00` thành cốt 0 nhưng `text` vẫn là chữ OCR gốc."""
    (mark,) = levels_from_texts([_Text("+/-0.00")])
    assert (mark.value_m, mark.text, mark.source) == (0.0, "+/-0.00", "decimal")


def test_levels_from_texts_accepts_any_iterable_and_empty_input() -> None:
    """Bộ sinh cũng được; rỗng thì rỗng."""
    assert levels_from_texts(_Text(t) for t in ("+3.600",))[0].value_m == 3.6
    assert levels_from_texts([]) == ()


def test_levels_from_texts_ignores_unsigned_dimensions() -> None:
    """Số `3600` không dấu và "2850" là kích thước, không phải cốt, kể cả có hai giá trị."""
    assert levels_from_texts(_column("3600", "2850", "1200")) == ()


def test_levels_from_texts_reads_a_millimetre_column() -> None:
    """Cột ghi bằng milimét có từ hai giá trị khác nhau: nhận, cùng cốt "±0.000" thập phân."""
    marks = levels_from_texts(_column("+10200", "+6900", "+3600", "±0.000"))
    assert sorted(m.value_m for m in marks) == [0.0, 3.6, 6.9, 10.2]
    assert {m.source for m in marks} == {"mm", "decimal"}
    assert next(m.source for m in marks) == "decimal"  # thập phân trước, milimét sau


def test_levels_from_texts_needs_two_distinct_millimetre_values() -> None:
    """Một giá trị lẻ loi (hoặc hai lần cùng một giá trị) có thể là kích thước: bỏ."""
    assert levels_from_texts(_column("+3600")) == ()
    assert levels_from_texts(_column("+3600", "+3600")) == ()
    assert [m.value_m for m in levels_from_texts(_column("±0.000", "+3600"))] == [0.0]


def test_levels_from_texts_never_mixes_decimal_and_millimetre() -> None:
    """Hình đã có cốt thập phân khác 0 thì bỏ qua mọi cốt milimét."""
    marks = levels_from_texts(_column("+3.600", "+6900", "+10200"))
    assert [m.value_m for m in marks] == [3.6]


def test_levels_from_texts_zero_decimal_does_not_block_millimetre() -> None:
    """Cốt thập phân đúng 0 không chặn cột milimét; khác 0 dù rất nhỏ (0.001) thì chặn."""
    assert len(levels_from_texts(_column("+0.000", "+3600", "+6900"))) == 3
    assert [m.value_m for m in levels_from_texts(_column("+0.001", "+3600", "+6900"))] == [0.001]


def test_level_mark_is_frozen() -> None:
    """Kết quả bất biến."""
    mark = LevelMark(1.0, "+1.000", 0.0, 0.0, "decimal")
    with pytest.raises(dataclasses.FrozenInstanceError):
        mark.value_m = 2.0  # type: ignore[misc]


def test_main_floor_level_of_nothing() -> None:
    """Không có cốt nào thì không có sàn chính."""
    assert main_floor_level([]) is None


def test_main_floor_level_single_value() -> None:
    """Một cốt, lặp lại: đếm đủ, không phương án khác."""
    assert main_floor_level([3.6, 3.6, 3.6]) == MainLevel(3.6, 3, ())


def test_main_floor_level_merges_values_within_cluster_tolerance() -> None:
    """3.6 và 3.63 (cách đúng 0,03 m) là một cụm, giá trị đại diện là giá trị hay gặp hơn."""
    assert main_floor_level([3.6, 3.6, 3.63]) == MainLevel(3.6, 3, ())


def test_main_floor_level_groups_sunken_area_with_main_floor() -> None:
    """WC trũng 0,07 m dưới sàn chính cùng một nhóm; đại diện là giá trị tròn, không phải giá trị đông hơn."""
    assert main_floor_level([3.62, 3.62, 3.62, 3.55]) == MainLevel(3.55, 4, ())


def test_main_floor_level_prefers_higher_when_both_round() -> None:
    """Cả hai đều tròn thì lấy giá trị cao hơn."""
    assert main_floor_level([0.0, 0.0, -0.1]) == MainLevel(0.0, 3, ())


def test_main_floor_level_prefers_higher_when_neither_round() -> None:
    """Không giá trị nào tròn thì cũng lấy giá trị cao hơn."""
    assert main_floor_level([3.612, 3.612, 3.531]) == MainLevel(3.612, 3, ())


def test_main_floor_level_keeps_far_marks_as_alternatives() -> None:
    """Cốt cách nhau từ 0,12 m trở lên là sàn khác: nhóm đông nhất là sàn chính, còn lại xếp theo số lần."""
    assert main_floor_level([0.0, 0.0, 0.0, 0.15, 0.45, 0.45]) == MainLevel(0.0, 3, (0.45, 0.15))


def test_main_floor_level_tie_goes_to_higher_group() -> None:
    """Hai nhóm đông bằng nhau: nhóm cao hơn là sàn chính."""
    assert main_floor_level([1.0, 2.0]) == MainLevel(2.0, 1, (1.0,))


def test_main_floor_level_gap_boundary() -> None:
    """Cách đúng 0,12 m là hai sàn (kể cả khi phép trừ số thực lệch), dưới 0,12 m là một."""
    assert main_floor_level([0.45, 0.57]) == MainLevel(0.57, 1, (0.45,))
    assert main_floor_level([0.0, 0.119]) == MainLevel(0.0, 2, ())


def test_main_floor_level_is_order_independent() -> None:
    """Thứ tự đầu vào không đổi kết quả."""
    values = [0.0, 3.6, 0.0, 3.55, 0.0, 3.6]
    assert main_floor_level(values) == main_floor_level(values[::-1])


def test_level_groups_returns_ascending_floors_with_counts() -> None:
    assert level_groups([3.6, -0.45, 0.0, 0.0, 3.55, 3.6]) == [(-0.45, 1), (0.0, 2), (3.6, 3)]
    assert level_groups([]) == []


def test_level_groups_prefer_zero_only_when_asked() -> None:
    values = [0.0, 0.1, 0.1]  # một sàn: 0.000 và 0.100 cách nhau dưới 0,12 m
    assert level_groups(values) == [(0.1, 3)]
    assert level_groups(values, prefer_zero=True) == [(0.0, 3)]
