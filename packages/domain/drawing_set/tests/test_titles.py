"""Tên hình: chép `selfcheck()` của `kernel/classify.py` và `kernel/ocr_views.py` (phần chỉ-dùng-chữ).

Số tầng ở đây là số **như viết** (lầu 1 -> 1, "2ND FLOOR" -> 2); kernel cộng một cho lầu và "Nth floor".
"""

# ruff: noqa: RUF001  (bảng mã cũ chứa ký tự giống dấu câu, đó là dữ liệu)

from dataclasses import dataclass

import pytest

from packages.domain.drawing_set import KindGuess, PickedTitle, fold, parse_title, pick_title, titles, view_kind

SIZE = (2000.0, 1200.0)


@dataclass(frozen=True)
class Ocr:
    """Dòng OCR giả: đủ ba thuộc tính của `OcrText`."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float = 1.0


def line(text: str, x0: float, y0: float, x1: float, y1: float) -> Ocr:
    """Dòng OCR giả theo thứ tự hộp (x0, y0, x1, y1)."""
    return Ocr(text, (x0, y0, x1, y1))


SMALL = [line(f"P{i}", 100 + 90 * i, 100, 160 + 90 * i, 130) for i in range(6)]  # chữ nhỏ làm nền trung vị


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mặt bằng  tầng\n1", "MAT BANG TANG 1"),
        ("MẶT ĐỨNG TRỤC 1-6", "MAT DUNG TRUC 1-6"),
        ("đường Đồng", "DUONG DONG"),
        ("MAËT BAÈNG LAÀU 1", "MAT BANG LAU 1"),
        ("mÆt b»ng tÇng 1", "MAT BANG TANG 1"),
        ("日本 plan", "PLAN"),
        ("", ""),
    ],
)
def test_fold(text: str, expected: str) -> None:
    """Bỏ dấu, Đ -> D, giải mã VNI/TCVN3, hoa, một khoảng trắng; ký tự lạ không quy được bị bỏ."""
    assert fold(text) == expected


# (tên, loại hình, nhãn tầng như viết, cách đánh số, biến thể)
_TITLES = [
    ("MẶT BẰNG TẦNG 1", "plan", 1, "tang", "plain"),
    ("MAT BANG LAU 2", "plan", 2, "lau", "plain"),
    ("MẶT BẰNG TẦNG TRỆT", "plan", "ground", None, "plain"),
    ("TẦNG TRỆT", "plan", "ground", None, "plain"),
    ("MẶT CẮT A-A", "section", None, None, "plain"),
    ("MẶT ĐỨNG TRỤC 1-6", "elevation", None, None, "plain"),
    ("MẶT BẰNG MÁI", "roof", None, None, "plain"),
    ("ROOF PLAN", "roof", None, None, "plain"),
    ("MẶT BẰNG BỐ TRÍ NỘI THẤT TẦNG 3", "plan", 3, "tang", "furniture"),
    ("MẶT BẰNG TẦNG LỬNG", "plan", "mezzanine", None, "plain"),
    ("TẦNG LỬNG", "plan", "mezzanine", None, "plain"),
    ("MẶT BẰNG TẦNG HẦM", "plan", "basement", None, "plain"),
    ("TẦNG HẦM", "plan", "basement", None, "plain"),
    ("Basement Floor Plan", "plan", "basement", None, "plain"),
    ("Underground floor plan", "plan", "basement", None, "plain"),
    ("MẶT BẰNG NỘI THẤT TẦNG HẦM", "plan", "basement", None, "furniture"),
    ("TUM", "plan", "attic", None, "plain"),
    ("mÆt b»ng tÇng tum", "plan", "attic", None, "plain"),
    ("SÂN THƯỢNG", "plan", "roof_terrace", None, "plain"),
    ("GROUND FLOOR PLAN", "plan", "ground", None, "plain"),
    ("GROUNDT FLOOR PLAN", "plan", "ground", None, "plain"),
    ("Groudn Floor Plan", "plan", "ground", None, "plain"),
    ("FIRST FLOOR PLAN", "plan", 1, "floor_ordinal", "plain"),
    ("Second Floor Plan", "plan", 2, "floor_ordinal", "plain"),
    ("2ND FLOOR PLAN", "plan", 2, "floor_ordinal", "plain"),
    ("2ND FLOOR", "plan", 2, "floor_ordinal", "plain"),
    ("TẦNG 2", "plan", 2, "tang", "plain"),
    ("MẶT BẰNG KẾT CẤU TẦNG 2", "plan", 2, "tang", "structure"),
    ("MẶT BẰNG MÓNG", "plan", None, None, "structure"),
    ("MẶT BẰNG TRẦN TẦNG 1", "plan", 1, "tang", "ceiling"),
    ("MẶT BẰNG ĐIỆN TẦNG 1", "plan", 1, "tang", "electrical"),
    ("MẶT BẰNG CẤP NƯỚC TẦNG 1", "plan", 1, "tang", "plumbing"),
    ("MAËT BAÈNG LAÙT SAØN TAÀNG 1", "plan", 1, "tang", "other"),
    ("MAËT BAÈNG KÍCH THÖÔÙC CHI TIEÁT TAÀNG 1", "plan", 1, "tang", "plain"),
    ("MAËT BAÈNG TAÀNG 2\nTL:1/100", "plan", 2, "tang", "plain"),
    ("MAËT BAÈNG LAÀU 1", "plan", 1, "lau", "plain"),
    ("MAËT BAÈNG TREÄT", "plan", "ground", None, "plain"),
    ("MAËT CAÉT B - B", "section", None, None, "plain"),
    ("MAËT ÑÖÙNG", "elevation", None, None, "plain"),
    ("MAËT BAÈNG MAÙI", "roof", None, None, "plain"),
    ("mÆt ®øng", "elevation", None, None, "plain"),
    ("mÆt c¾t", "section", None, None, "plain"),
    ("mÆt b»ng tÇng m¸i", "roof", None, None, "plain"),
    ("MÆT §øNG TRôC  d - d", "elevation", None, None, "plain"),
    ("mÆt b»ng néi thÊt tÇng 1", "plan", 1, "tang", "furniture"),
    ("SECTION A-A", "section", None, None, "plain"),
    ("NORTH ELEVATION", "elevation", None, None, "plain"),
    ("1_FRONT ELEVATION", "elevation", None, None, "plain"),
    ("MAËT ÑÖÙNG CHÍNH ( COÙ SAÂN THÖÔÏNG )", "elevation", None, None, "plain"),
    ("SITE PLAN", "site", None, None, "plain"),
    ("MẶT BẰNG TỔNG THỂ", "site", None, None, "plain"),
    ("CHI TIẾT CỬA ĐI D1", "detail", None, None, "other"),
    ("CHI TIẾT CẦU THANG", "detail", None, None, "other"),
    ("CHI TIẾT CỬA", "detail", None, None, "other"),
    # Như kernel: "MẶT BẰNG" thắng, "CHI TIẾT" chỉ hạ độ ưu tiên tầng (storey_cues.NONSTOREY): mặt bằng, chưa rõ tầng.
    ("MẶT BẰNG CHI TIẾT WC", "plan", None, None, "plain"),
    ("PERSPECTIVE VIEW", "other", None, None, "other"),
    ("PHỐI CẢNH 3D", "other", None, None, "other"),
]


@pytest.mark.parametrize(("text", "kind", "storey", "numbering", "variant"), _TITLES)
def test_parse_title(text: str, kind: str, storey: object, numbering: object, variant: str) -> None:
    """Loại hình, nhãn tầng như viết, cách đánh số và biến thể của từng tên hình trong báo cáo kernel."""
    info = parse_title(text)
    assert info is not None
    assert (info.kind, info.storey, info.numbering, info.variant) == (kind, storey, numbering, variant)
    assert info.norm == fold(text)


@pytest.mark.parametrize("text", ["", "   ", "TL:1/50", "KT-02", "A", "+3.600", "nhà 3 gian"])
def test_parse_title_returns_none_without_a_drawing_word(text: str) -> None:
    """Chữ không nêu loại bản vẽ (tỉ lệ, mã tờ, cốt, rỗng) -> None."""
    assert parse_title(text) is None


# ---------------------------------------------------------------- pick_title
def test_pick_title_merges_words_on_one_baseline() -> None:
    """ "MẶT BẰNG" + "TẦNG 2" cạnh nhau nối thành một tên; ghi chú tỉ lệ bên phải kéo điểm lên."""
    texts = [
        *SMALL,
        line("MẶT BẰNG", 500, 900, 900, 980),
        line("TẦNG 2", 930, 902, 1130, 982),
        line("TL: 1/100", 1160, 920, 1300, 970),
    ]
    picked = pick_title(texts, SIZE)
    assert picked.source == "under_drawing"
    assert "MẶT BẰNG" in picked.text
    info = parse_title(picked.text)
    assert info is not None
    assert info.storey == 2


def test_pick_title_block_and_under_drawing_agree() -> None:
    """Khung tên "TEN BAN VE" + chữ rớt nguyên âm "MTBNG TNG 3" được sửa; chữ xoay bị bỏ; hai nguồn khớp -> 0,9."""
    texts = [
        line("TEN BAN VE:", 100, 300, 300, 330),
        line("MTBNG TNG 3", 320, 300, 560, 330),
        line("MAT CAT A-A", 700, 500, 740, 700),
        *SMALL,
    ]
    assert pick_title(texts, SIZE) == PickedTitle("MAT BANG TANG 3", "under_drawing", 0.9)


def test_pick_title_two_line_title_gets_storey_from_the_line_below() -> None:
    """ "MẶT BẰNG" không tầng + dòng "TẦNG 2" ngay dưới -> nối thành tên hai dòng."""
    texts = [*SMALL, line("MẶT BẰNG", 500, 900, 900, 980), line("TẦNG 2", 500, 1000, 700, 1080)]
    assert pick_title(texts, SIZE).text == "MẶT BẰNG TẦNG 2"


def test_pick_title_storey_line_on_the_right_is_joined() -> None:
    """Dòng tầng ở bên phải cùng đường cơ sở, cách xa quá ngưỡng gộp, vẫn được nối vào tên."""
    texts = [*SMALL, line("MẶT BẰNG", 500, 900, 900, 980), line("LẦU 1", 1000, 905, 1200, 982)]
    assert pick_title(texts, SIZE).text.endswith("LẦU 1")


def test_pick_title_only_under_drawing() -> None:
    """Không có khung tên: nguồn duy nhất, tin cậy 0,8."""
    texts = [*SMALL, line("MẶT CẮT A-A", 500, 900, 900, 980)]
    assert pick_title(texts, SIZE) == PickedTitle("MẶT CẮT A-A", "under_drawing", 0.8)


def test_pick_title_sources_disagree() -> None:
    """Khung tên nói tầng 3, tên dưới hình nói tầng 1 -> lấy tên dưới hình, tin cậy thấp 0,6."""
    texts = [
        line("TEN BAN VE:", 100, 300, 300, 330),
        line("MAT BANG TANG 3", 320, 300, 700, 330),
        line("MAT BANG TANG 1", 700, 900, 1100, 980),
        *SMALL,
    ]
    assert pick_title(texts, SIZE) == PickedTitle("MAT BANG TANG 1", "under_drawing", 0.6)


def test_pick_title_block_only_when_the_title_sits_in_a_note() -> None:
    """Tên nằm giữa hai dòng ghi chú cùng cỡ chữ (đoạn ghi chú) không được chọn dưới hình; khung tên cứu."""
    texts = [
        line("TEN BAN VE:", 100, 300, 300, 330),
        line("MAT BANG TANG 3", 320, 300, 700, 330),
        line("ghi chu a", 320, 260, 700, 290),
        line("ghi chu b", 320, 340, 700, 370),
    ]
    assert pick_title(texts, SIZE) == PickedTitle("MAT BANG TANG 3", "title_block", 0.7)


def test_pick_title_none_without_a_recognisable_title() -> None:
    """Chữ không nêu loại hình, hoặc không có chữ, hoặc chỉ có chữ xoay -> `none`, không đoán."""
    nothing = PickedTitle("", "none", 0.0)
    assert pick_title([*SMALL, line("XYZ", 50, 700, 300, 800), line("ABC", 50, 300, 200, 360)], SIZE) == nothing
    assert pick_title([], SIZE) == nothing
    assert pick_title([line("  ", 0, 0, 10, 10), line("TALL", 0, 0, 10, 100)], SIZE) == nothing


def _block(extra: list[Ocr]) -> str:
    """Tên khung tên mà `_block_title` lấy từ `SMALL + extra`."""
    items = titles._lines(SMALL + extra)
    return titles._block_title(items, titles._short_merges(items))


def test_block_title_value_below_the_label() -> None:
    """Giá trị nằm trong ô dưới nhãn "Tén ban ve - drawlng name:" (OCR sai chữ); mã tờ "KT-02" không phải tên."""
    extra = [
        line("Tén ban ve - drawlng name:", 1300, 1130, 1430, 1146),
        line("mat bang tang 1", 1320, 1167, 1580, 1194),
        line("KT-02", 1320, 1210, 1400, 1240),
    ]
    assert _block(extra) == "mat bang tang 1"


def test_block_title_value_right_of_the_label() -> None:
    """Giá trị cùng dòng, bên phải nhãn; nhãn kế tiếp ("ti le") chặn ô."""
    extra = [
        line("ten ban ve:", 1300, 1130, 1400, 1150),
        line("mat cat 2-2", 1420, 1128, 1600, 1152),
        line("ti le", 1700, 1130, 1760, 1150),
    ]
    assert _block(extra) == "mat cat 2-2"


def test_block_title_text_after_the_label_in_the_same_line() -> None:
    """Tên nằm ngay sau nhãn trong cùng một dòng OCR."""
    assert _block([line("TEN BAN VE: MAT BANG TANG 2", 1300, 1130, 1900, 1160)]) == "MAT BANG TANG 2"


def test_block_title_empty_without_a_label_or_value() -> None:
    """Không có nhãn -> rỗng; nhãn mà ô giá trị không nhận ra tên -> rỗng; chữ chồng lên nhãn bị loại."""
    assert _block([line("MAT BANG TANG 1", 500, 900, 900, 980)]) == ""
    assert _block([line("TEN BAN VE:", 100, 300, 300, 330), line("TL 1/100", 320, 300, 500, 330)]) == ""
    assert _block([line("TEN BAN VE:", 100, 300, 300, 330), line("MAT BANG", 120, 300, 280, 330)]) == ""


def test_block_title_prefers_a_value_naming_a_kind() -> None:
    """Hai ô giá trị: tên có từ chỉ loại hình thắng "TANG 3" trơn."""
    extra = [
        line("ten ban ve:", 1300, 1130, 1400, 1150),
        line("TANG 3", 1420, 1128, 1600, 1152),
        line("mat bang tang 1", 1300, 1180, 1580, 1204),
    ]
    assert _block(extra) == "mat bang tang 1"


# ---------------------------------------------------------------- view_kind
def test_view_kind_title_and_weak_content_agree() -> None:
    """Tên "mặt cắt" + hình gần như trống (nghiêng về đứng) cùng ý -> loại hình 0,85, nguồn tên 0,8 nên 0,8."""
    assert view_kind([line("MAT CAT A-A", 500, 900, 900, 980)], SIZE) == KindGuess(
        "section", 0.8, "TITLE_AND_CONTENT_AGREE"
    )


def test_view_kind_title_only_when_content_has_no_cue() -> None:
    """Số dòng chữ vừa phải: nội dung không có dấu hiệu -> chỉ dựa vào tên."""
    texts = [*SMALL[:4], line("MẶT BẰNG", 500, 900, 900, 980), line("TẦNG 2", 930, 902, 1130, 982)]
    assert view_kind(texts, SIZE) == KindGuess("plan", 0.8, "TITLE_ONLY")


def test_view_kind_weak_content_disagrees() -> None:
    """Tên "mặt bằng" nhưng hình gần như trống (nghiêng đứng): vẫn theo tên, ghi lý do lệch."""
    guess = view_kind([line("MẶT BẰNG TẦNG 1", 500, 900, 900, 980)], SIZE)
    assert guess == KindGuess("plan", 0.8, "TITLE_CONTENT_DISAGREE_WEAK")


def test_view_kind_title_confidence_caps_the_result() -> None:
    """Hai nguồn tên lệch nhau (0,6) kéo tin cậy loại hình xuống 0,6."""
    texts = [
        line("TEN BAN VE:", 100, 300, 300, 330),
        line("MAT BANG TANG 3", 320, 300, 700, 330),
        line("MAT BANG TANG 1", 700, 900, 1100, 980),
        *SMALL,
    ]
    assert view_kind(texts, SIZE) == KindGuess("plan", 0.6, "TITLE_ONLY")


def test_view_kind_other_titles_have_no_content_expectation() -> None:
    """Loại `other` (phối cảnh) không so với nội dung và không quá 0,8."""
    assert view_kind([line("PERSPECTIVE", 500, 900, 900, 980)], SIZE) == KindGuess("other", 0.8, "TITLE_ONLY")


def test_view_kind_without_title() -> None:
    """Không tên: nhiều chữ -> mặt bằng 0,5; gần như trống -> mặt đứng 0,3; ở giữa -> `other` 0,1."""
    assert view_kind(SMALL * 4, SIZE) == KindGuess("plan", 0.5, "CONTENT_ONLY_MANY_TEXTS")
    assert view_kind([], SIZE) == KindGuess("elevation", 0.3, "CONTENT_ONLY_EMPTY_VIEW")
    assert view_kind(SMALL[:4], SIZE) == KindGuess("other", 0.1, "NO_TITLE_NO_CONTENT")


# ---------------------------------------------------------------- phần phụ trợ chữ OCR
@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("TANG", "TANG", True),
        ("TANG", "TAN", True),
        ("TAN", "TANG", True),
        ("TANG", "TUNG", True),
        ("TANG", "TONG X", False),
    ],
)
def test_near_by_one_edit(a: str, b: str, expected: bool) -> None:
    """Cách nhau tối đa một lần thay/chèn/xoá; lệch độ dài quá một hay hai ký tự khác nhau thì không."""
    assert titles._near_by_one_edit(a, b) is expected
    assert not titles._near_by_one_edit("ABC", "XYZ")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MAT BANG TANG 1", "MAT BANG TANG 1"),
        ("MAT BANG TAN 1", "MAT BANG TANG 1"),
        ("MAT BNG TANG 2", "MAT BNG TANG 2"),  # đã có tầng: không sửa (như kernel)
        ("MAT CAT A-A", "MAT CAT A-A"),
        ("MAT CT A-A", "MAT CAT A-A"),
        ("MAT DUNG", "MAT DUNG"),
        ("XYZ", "XYZ"),
    ],
)
def test_better_repairs_key_words(text: str, expected: str) -> None:
    """Sửa từ khoá lệch một ký tự khi bản sửa nêu được loại hình/tầng; chữ khác giữ nguyên."""
    assert titles._better(text) == expected


def test_better_keeps_text_when_repair_adds_nothing() -> None:
    """Bản sửa không thêm loại hình hay tầng thì giữ nguyên chữ gốc, kể cả dấu."""
    assert titles._better("Tầng trệt") == "Tầng trệt"
    assert titles._better("xin chào") == "xin chào"


def test_fix_vowel_drops() -> None:
    """Ba dạng rớt nguyên âm đã gặp ở bộ DS-571."""
    assert titles._fix_vowel_drops("MTBNG TNG 01") == "MAT BANG TANG 01"
    assert titles._fix_vowel_drops("MT DNG TRC") == "MAT DUNG TRUC"
    assert titles._fix_vowel_drops("khong doi") == "khong doi"


def test_same_ignores_numbering_convention() -> None:
    """ "LAU 1" và "TANG 2" là một tầng (lầu N = tầng N+1); loại hình khác hay không đọc được thì không."""
    assert titles._same("MAT BANG LAU 1", "MAT BANG TANG 2")
    assert titles._same("2ND FLOOR PLAN", "MAT BANG TANG 3")
    assert not titles._same("MAT BANG LAU 1", "MAT BANG TANG 1")
    assert not titles._same("MAT CAT A-A", "MAT BANG TANG 1")
    assert not titles._same("xyz", "MAT BANG TANG 1")


def test_is_note_needs_two_stacked_neighbours() -> None:
    """Một dòng giữa hai dòng cùng cỡ xếp sát là ghi chú; một dòng bên cạnh thôi thì chưa."""
    mid = titles._lines([line("MAT BANG TANG 1", 320, 300, 700, 330)])[0]
    above = titles._lines([line("ghi chu a", 320, 260, 700, 290)])[0]
    below = titles._lines([line("ghi chu b", 320, 340, 700, 370)])[0]
    assert titles._is_note(mid, [mid, above, below])
    assert not titles._is_note(mid, [mid, above])


def test_lines_drop_empty_and_rotated_boxes() -> None:
    """Dòng rỗng và hộp cao hơn 1,5 lần rộng (chữ xoay) không thành dòng; còn lại sắp từ trên xuống."""
    out = titles._lines(
        [line("B", 10, 50, 100, 80), line(" ", 0, 0, 5, 5), line("ROT", 0, 0, 10, 100), line("A", 10, 10, 100, 40)]
    )
    assert [ln.text for ln in out] == ["A", "B"]


# ---------------------------------------------------------------- từ thật không bị sửa (NO-441)
# Mọi cặp (từ thật của bộ đọc, từ khoá sửa lỗi) cách nhau một ký tự; từ thật phải sống sót.
_COLLISIONS = [
    ("TIET", "TRET"),
    ("TIET", "TIEN"),
    ("DIEN", "TIEN"),
    ("LUNG", "DUNG"),
    ("TONG", "TANG"),
    ("CAU", "CAT"),
    ("DAT", "MAT"),
    ("LAT", "MAT"),
    ("MAI", "MAT"),
]


def _snap_collisions() -> set[tuple[str, str]]:
    """Mọi cặp (từ thật, từ khoá bị sửa tới) cách một ký tự, dò trên cả tập từ."""
    targets = {*titles._SNAP_WORDS, "MAT", "CAT"}
    return {
        (w, k)
        for w in titles._VOCAB
        for k in targets
        if len(w) >= 3 and w != k and w not in targets and titles._near_by_one_edit(w, k)
    }


def test_collision_list_is_complete() -> None:
    """Danh sách va chạm trong test khớp với phép dò toàn tập từ (thêm từ mới sẽ làm test này đỏ)."""
    found = _snap_collisions()
    assert set(_COLLISIONS) <= found
    assert {w for w, _ in found} == {w for w, _ in _COLLISIONS}


@pytest.mark.parametrize(("word", "key"), _COLLISIONS)
def test_snap_keeps_real_words(word: str, key: str) -> None:
    """Từ thật sát một từ khoá giữ nguyên, kể cả đứng sau MAT/trước số (nơi từ khoá được phép sửa)."""
    assert titles._snap(word) == word
    assert titles._snap(f"MAT {word} 1") == f"MAT {word} 1"
    assert titles._snap(f"{word} 1") == f"{word} 1"
    assert titles._snap(f"{word} BANG") == f"{word} BANG"


def test_snap_leaves_every_vocabulary_word_alone() -> None:
    """Không từ nào trong tập từ bị `_snap` đổi, dù đứng ở đâu."""
    for word in titles._VOCAB:
        assert titles._snap(word) == word
        assert titles._snap(f"MAT {word} 1") == f"MAT {word} 1"


@pytest.mark.parametrize(
    ("text", "kind", "storey"),
    [
        ("CHI TIẾT CẦU THANG", "detail", None),
        ("CHI TIẾT CỬA", "detail", None),
        ("MẶT BẰNG CHI TIẾT WC", "plan", None),
    ],
)
def test_pick_title_keeps_vietnamese_detail(text: str, kind: str, storey: object) -> None:
    """ "CHI TIẾT" không bị bẻ thành "CHI TRET": loại đúng, không thành tầng trệt."""
    picked = pick_title([*SMALL, line(text, 300, 500, 900, 560)], SIZE)
    assert picked.text == text
    info = parse_title(picked.text)
    assert info is not None
    assert (info.kind, info.storey) == (kind, storey)
    assert view_kind([*SMALL, line(text, 300, 500, 900, 560)], SIZE).kind == kind


def test_snap_still_repairs_ocr_damage() -> None:
    """Chữ hỏng (không phải từ thật) vẫn được sửa."""
    assert titles._fix_vowel_drops("MTBNG TNG 3") == "MAT BANG TANG 3"
    assert titles._snap("MAT BANG TAN 1") == "MAT BANG TANG 1"
    assert titles._snap("MAT BANG TRAT") == "MAT BANG TRET"
