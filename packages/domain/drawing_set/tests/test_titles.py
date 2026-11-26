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
        ("MAET BAENG LAAU 1 TL: 1/100", "MAT BANG LAU 1 TL: 1/100"),  # VNI sau OCR trên ảnh
        ("MAET BAENG TREAT", "MAT BANG TRET"),
        ("MAET CAET A-A", "MAT CAT A-A"),
        ("MAET NOUNG TRUC 1-6", "MAT DUNG TRUC 1-6"),
        ("TOAN KHOA LAAUX", "TOAN KHOA LAAUX"),  # chỉ thay nguyên từ trong bộ từ vựng
        ("MET b>ng tCng 1", "MAT BANG TANG 1"),  # TCVN3 sau OCR trên ảnh (DN-602 trên f14)
        (".mAEt c3/4t a-a", ".MAT CAT A-A"),
        ("MET b>ng tCng p m.i", "MAT BANG TANG AP MAI"),
        ("DAI 5 MET", "DAI 5 MET"),  # "MET" đứng một mình là mét: giữ
        ("MAET BAENG LAAU2", "MAT BANG LAU2"),  # số dính liền sau từ dư VNI (DM-481, K-30a)
        ("mAEt b>ng tCng1", "MAT BANG TANG1"),  # số dính liền sau từ dư TCVN3 (DM-528)
        ("MAET B>NG m,i", "MAT BANG MAI"),  # "m<U+00B8>i" ra "m,i" (DM-445)
        ("TCNGA LAAUX2", "TCNGA LAAUX2"),  # chữ dính sau từ thì không phải từ đó: giữ
        ("日本 plan", "PLAN"),
        ("", ""),
    ],
)
def test_fold(text: str, expected: str) -> None:
    """Bỏ dấu, Đ -> D, giải mã VNI/TCVN3, hoa, một khoảng trắng; ký tự lạ không quy được bị bỏ."""
    assert fold(text) == expected


@pytest.mark.parametrize(
    ("text", "storey", "numbering"),
    [
        ("MAET BAENG TREAT TL: 1/100", "ground", None),
        ("MAET BAENG LAAU 1 TL: 1/100", 1, "lau"),
        ("MAET BAENG LAAU 3 TL: 1/100", 3, "lau"),
        ("MET b>ng tCng 2", 2, "tang"),
        ("MET b>ng tCng p m.i", "attic", None),
        ("MAET BAENG LAAU2", 2, "lau"),
        ("mAEt b>ng tCng1", 1, "tang"),
    ],
)
def test_parse_title_reads_ocr_vni_residue(text: str, storey: object, numbering: str | None) -> None:
    """Tên hình phông VNI đọc bằng OCR (bộ DN-244 trên f14, 22/11): đọc ra đúng tầng như chữ Unicode."""
    info = parse_title(text)
    assert info is not None
    assert (info.kind, info.storey, info.numbering) == ("plan", storey, numbering)


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
    ("TUM", "plan", "tum", None, "plain"),
    ("MẶT BẰNG TẦNG ÁP MÁI", "plan", "attic", None, "plain"),
    ("mÆt b»ng tÇng tum", "plan", "tum", None, "plain"),
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
    # NHA-03 — tên hình kiểu Anh/Mỹ/Canada (thiết kế §2.A mục 2).
    ("MAIN FLOOR PLAN", "plan", "ground", None, "plain"),
    ("MAIN LEVEL FLOOR PLAN", "plan", "ground", None, "plain"),
    # UPPER/LOWER không nói tầng mấy (nhà hai tầng hay bán hầm): mặt bằng, để người duyệt chọn tầng.
    ("UPPER FLOOR PLAN", "plan", None, None, "plain"),
    ("LOWER FLOOR PLAN", "plan", None, None, "plain"),
    # LEVEL n chưa được đọc thành số tầng (NO-597): cùng chữ đó là nhãn cốt ("FINISHED FLOOR LEVEL 2",
    # "LEVEL 10'-0") và `storeys.py` chưa biết cách đếm này. Mặt bằng, để người duyệt chọn tầng.
    ("LEVEL 2 FLOOR PLAN", "plan", None, None, "plain"),
    ("FINISHED FLOOR LEVEL 2", "plan", None, None, "plain"),
    ("SECOND FLOOR PLAN LEVEL 3", "plan", 2, "floor_ordinal", "plain"),
    ("ATTIC FLOOR PLAN", "plan", "attic", None, "plain"),
    ("BUILDING SECTIONS", "section", None, None, "plain"),
    ("CROSS SECTION", "section", None, None, "plain"),
    ("LONGITUDINAL SECTION B", "section", None, None, "plain"),
    ("LEFT SIDE ELEVATION", "elevation", None, None, "plain"),
    ("EXTERIOR ELEVATIONS", "elevation", None, None, "plain"),
    # Tiêu đề lẫn hai ngôn ngữ: phần tiếng Việt quyết định số tầng (TẦNG 2 và SECOND FLOOR lệch nhau một tầng).
    ("MẶT BẰNG TẦNG 2 / SECOND FLOOR PLAN", "plan", 2, "tang", "plain"),
    ("MẶT BẰNG TẦNG TRỆT - MAIN FLOOR PLAN", "plan", "ground", None, "plain"),
    ("MẶT CẮT A-A / SECTION A-A", "section", None, None, "plain"),
    ("MẶT ĐỨNG CHÍNH - FRONT ELEVATION", "elevation", None, None, "plain"),
    # Ghim NO-597: nhãn cốt `MAIN FLOOR` đứng một mình và ghi chú `SEE SECTIONS` vẫn thành tên hình, cùng lớp nhầm
    # sẵn có với `GROUND FLOOR` và `SEE SECTION A-A`.
    ("MAIN FLOOR", "plan", "ground", None, "plain"),
    ("SEE SECTIONS", "section", None, None, "plain"),
]


@pytest.mark.parametrize(("text", "kind", "storey", "numbering", "variant"), _TITLES)
def test_parse_title(text: str, kind: str, storey: object, numbering: object, variant: str) -> None:
    """Loại hình, nhãn tầng như viết, cách đánh số và biến thể của từng tên hình trong báo cáo kernel."""
    info = parse_title(text)
    assert info is not None
    assert (info.kind, info.storey, info.numbering, info.variant) == (kind, storey, numbering, variant)
    assert info.norm == fold(text)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "TL:1/50", "KT-02", "A", "+3.600", "nhà 3 gian", "LEVEL 2", "MAIN LEVEL", "BASEMENT", "ATTIC", "UPPER"],
)
def test_parse_title_returns_none_without_a_drawing_word(text: str) -> None:
    """Chữ không nêu loại bản vẽ (tỉ lệ, mã tờ, cốt, rỗng) -> None.

    NHA-03: `LEVEL 2`, `BASEMENT`, `ATTIC` đứng một mình cũng là nhãn cốt trên mặt cắt và tên phòng trong mặt bằng,
    nên không được thành tên hình.
    """
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
    assert pick_title(texts, SIZE) == PickedTitle("MAT BANG TANG 1", "under_drawing", 0.6, sources_disagree=True)


def test_pick_title_block_missing_storey_is_unknown_not_different() -> None:
    """Khung tên rớt số tầng ("... Tang" so với "... Tang 1"): khớp yếu 0,8, không phải bất đồng."""
    texts = [
        line("TEN BAN VE:", 100, 300, 300, 330),
        line("mat bang noi that Tang", 320, 300, 700, 330),
        line("mat bang noi that Tang 1", 700, 900, 1100, 980),
        *SMALL,
    ]
    assert pick_title(texts, SIZE) == PickedTitle("mat bang noi that Tang 1", "under_drawing", 0.8)


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


def test_agreement_ignores_numbering_convention() -> None:
    """ "LAU 1" và "TANG 2" là một tầng (lầu N = tầng N+1); thiếu tầng một bên là khớp yếu; tầng khác,
    loại hình khác hay không đọc được thì lệch."""
    assert titles._agreement("MAT BANG LAU 1", "MAT BANG TANG 2") == 0.9
    assert titles._agreement("2ND FLOOR PLAN", "MAT BANG TANG 3") == 0.9
    assert titles._agreement("MAT BANG TANG", "MAT BANG TANG 1") == 0.8
    assert titles._agreement("MAT BANG TANG 1", "MAT BANG") == 0.8
    assert titles._agreement("MAT BANG LAU 1", "MAT BANG TANG 1") is None
    assert titles._agreement("MAT BANG TRET", "MAT BANG TANG 2") is None
    assert titles._agreement("MAT CAT A-A", "MAT BANG TANG 1") is None
    assert titles._agreement("MAT CAT A-A", "MAT BANG") is None
    assert titles._agreement("xyz", "MAT BANG TANG 1") is None


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


# ---------------------------------------------------------------- số tầng lạ (soát mã, lỗi 3)
@pytest.mark.parametrize(
    ("text", "storey"),
    [
        ("MAT BANG TANG 1", 1),
        ("MAT BANG TANG 80", 80),
        ("MAT BANG TANG 81", None),
        ("MAT BANG TANG 0", None),
        ("MAT BANG TANG 999999999999", None),
        ("MAT BANG LAU " + "9" * 5000, None),
        ("MAT BANG TANG 999 TRET", None),
    ],
)
def test_parse_title_rejects_implausible_storey_numbers(text: str, storey: object) -> None:
    """Số tầng ngoài 1..80 (kể cả chuỗi số rất dài của OCR) -> giữ loại mặt bằng, không có tầng."""
    info = parse_title(text)
    assert info is not None
    assert (info.kind, info.storey) == ("plan", storey)


# ---------------------------------------------------------------- lửng hỏng OCR
@pytest.mark.parametrize(
    "text",
    [
        "mat bang lurng tang 1",
        "MAT BANG LUNGG TANG 1",
        "MAT BANG XLUNG TANG 1",
        "MAT BANG LUN TANG 1",
        "MAT BANG LUNGTANG 1",
        "MAT BANG LURNG1",
        "MAT BANG TANG LURNG",
        "MẶT BẰNG LƯNG TẦNG 1",
        "MẶT BẰNG LỬNG TẦNG 1",
    ],
)
def test_damaged_mezzanine_is_repaired(text: str) -> None:
    """LUNG hỏng (thêm/thiếu/sai một chữ, dính từ kế) sau MAT BANG/TANG -> lửng, không số, dù có số tầng sau."""
    picked = pick_title([*SMALL, line(text, 300, 500, 900, 560)], SIZE)
    info = parse_title(picked.text)
    assert info is not None
    assert (info.kind, info.storey, info.numbering) == ("plan", "mezzanine", None)


@pytest.mark.parametrize(
    ("text", "storey"),
    [("KHOI LUONG TANG 1", 1), ("MAT BANG TUNG 1", 1), ("MAT BANG TANG 1", 1), ("MAT BANG BANGTANG 1", None)],
)
def test_mezzanine_repair_needs_the_title_context(text: str, storey: object) -> None:
    """Chữ cách LUNG một ký tự ở chỗ khác (KHOI LUONG) giữ nguyên; TUNG gần TANG hơn nên thành TANG."""
    info = parse_title(titles._better(text))
    assert info is not None
    assert info.storey == storey


# ---------------------------------------------------------------- chữ OCR hỏng (soát A-F5)
@dataclass(frozen=True)
class RawOcr:
    """Dòng OCR không theo hợp đồng (hộp sai độ dài, chữ None ...)."""

    text: object
    box: object
    confidence: float = 1.0


@pytest.mark.parametrize(
    "bad",
    [
        RawOcr("MẶT BẰNG TẦNG 1", (0, 5, 10, 5)),
        RawOcr("MẶT BẰNG TẦNG 1", (10, 0, 0, 10)),
        RawOcr("MẶT BẰNG TẦNG 1", (0, 0, float("nan"), 10)),
        RawOcr("MẶT BẰNG TẦNG 1", (0, 0, float("inf"), 10)),
        RawOcr("MẶT BẰNG TẦNG 1", (0, 0, 10)),
        RawOcr("MẶT BẰNG TẦNG 1", None),
        RawOcr("MẶT BẰNG TẦNG 1", ("a", "b", "c", "d")),
        RawOcr(None, (0, 0, 100, 20)),
        RawOcr(12, (0, 0, 100, 20)),
    ],
)
def test_pick_title_drops_degenerate_texts(bad: RawOcr) -> None:
    """Hộp không đủ bốn số hữu hạn, rộng/cao <= 0, hay chữ không phải chuỗi: bỏ dòng đó, không ném lỗi."""
    assert pick_title([bad], SIZE) == PickedTitle("", "none", 0.0)  # type: ignore[list-item]
    kept = pick_title([*SMALL, bad, line("MAT CAT A-A", 500, 900, 900, 980)], SIZE)  # type: ignore[list-item]
    assert kept.text == "MAT CAT A-A"
    assert view_kind([bad], SIZE).kind == "elevation"  # type: ignore[list-item]


@pytest.mark.parametrize("size", [(0.0, 0.0), (-5.0, 100.0), (float("nan"), 1.0), (1.0, float("inf"))])
def test_pick_title_survives_a_bad_page_size(size: tuple[float, float]) -> None:
    """Cỡ trang không dùng được -> lấy hộp bao của chữ; tên vẫn đọc ra."""
    picked = pick_title([*SMALL, line("MẶT BẰNG TẦNG 1", 500, 900, 900, 980)], size)
    assert picked.text == "MẶT BẰNG TẦNG 1"
    assert titles._page((0.0, 0.0), []) == (1.0, 1.0)


def test_pick_title_cost_is_bounded() -> None:
    """3000 chữ (lưới + một hàng dài cùng đường cơ sở) xong dưới 2 giây; tên hình vẫn tìm ra."""
    import time

    grid = [
        line(f"P{i}", 10 + 30 * (i % 60), 10 + 20 * (i // 60), 30 + 30 * (i % 60), 25 + 20 * (i // 60))
        for i in range(2000)
    ]
    row = [line("TẦNG", 10 + 30 * i, 1050, 38 + 30 * i, 1070) for i in range(999)]
    title = line("MẶT BẰNG TẦNG 2", 700, 1100, 1300, 1180)
    start = time.perf_counter()
    picked = pick_title([*grid, *row, title, line("x" * 100_000, 0, 0, 10, 5)], (2000.0, 1200.0))
    assert time.perf_counter() - start < 2.0
    assert picked.text == "MẶT BẰNG TẦNG 2"


def test_lines_cap_count_and_length() -> None:
    """Quá MAX_TEXTS dòng: giữ nửa cao nhất và nửa thấp nhất trang; một dòng tối đa MAX_TEXT_LEN ký tự."""
    many = [line(f"P{i}", 0, i, 10, i + 1 + (i % 7)) for i in range(titles.MAX_TEXTS + 50)]
    kept = titles._lines([*many, line("y" * 500, 0, 0, 10, 2)])
    assert len(kept) <= titles.MAX_TEXTS
    assert all(len(ln.text) <= titles.MAX_TEXT_LEN for ln in kept)


# ---------------------------------------------------------------- hai nguồn lệch tầng (Codex 1, 4)
@pytest.mark.parametrize(
    ("under", "block", "disagree"),
    [
        ("MAT BANG TANG 1", "MAT BANG TANG 81", True),
        ("MAT BANG TANG 81", "MAT BANG TANG 1", True),
        ("MAT BANG TANG 81", "MAT BANG TANG 81", False),
        ("MAT BANG TANG 81", "MAT BANG", False),
        ("MAT BANG TANG 81", "MAT BANG HAM", True),
        ("MAT BANG HAM B1", "MAT BANG HAM B2", True),
        ("MAT BANG HAM B1", "MAT BANG HAM", False),
        ("MAT BANG HAM 2", "MAT BANG HAM B2", False),
    ],
)
def test_agreement_sees_rejected_numbers_and_basement_depth(under: str, block: str, disagree: bool) -> None:
    """Số bị loại ở một bên mà bên kia có tầng là lệch; thiếu hẳn số vẫn là 'chưa biết'; hầm so cả độ sâu."""
    assert (titles._agreement(under, block) is None) is disagree


def test_pick_title_reports_rejected_storey_disagreement() -> None:
    """Tên dưới hình TẦNG 1, khung tên TẦNG 81 -> sources_disagree, tin cậy thấp."""
    texts = [
        *SMALL,
        line("MAT BANG TANG 1", 700, 900, 1100, 980),
        line("TEN BAN VE:", 1500, 1100, 1650, 1120),
        line("MAT BANG TANG 81", 1660, 1100, 1800, 1120),
    ]
    assert pick_title(texts, SIZE) == PickedTitle("MAT BANG TANG 1", "under_drawing", 0.6, sources_disagree=True)


@pytest.mark.parametrize(
    ("text", "storey", "rejected"),
    [
        ("MAT BANG TANG 80", 80, False),
        ("MAT BANG TANG 81", None, True),
        ("MAT BANG LAU 79", 79, False),
        ("MAT BANG LAU 80", None, True),
        ("MAT BANG 79TH FLOOR", 79, False),
        ("MAT BANG 80TH FLOOR", None, True),
        ("MAT BANG TANG 0", None, True),
        ("MAT BANG", None, False),
    ],
)
def test_storey_bound_applies_to_the_resulting_storey(text: str, storey: object, rejected: bool) -> None:
    """Giới hạn 1..MAX_FLOOR áp lên tầng sau khi tính lầu N = tầng N+1; số bị loại được đánh dấu."""
    info = parse_title(text)
    assert info is not None
    assert (info.storey, info.storey_rejected) == (storey, rejected)


# ---------------------------------------------------------------- lửng: từ thật không bị sửa (Codex 5)
@pytest.mark.parametrize(
    ("text", "storey"),
    [
        ("MAT BANG LUONG GIAO THONG TANG 2", 2),
        ("MAT BANG LONG TANG 2", 2),
        ("MAT BANG LANG TANG 2", 2),
        ("MAT BANG LUON TANG 2", 2),
        ("MAT BANG HUNG TANG 2", 2),
        ("MAT BANG LURNG GIAO THONG TANG 2", 2),
    ],
)
def test_mezzanine_repair_spares_real_words(text: str, storey: object) -> None:
    """Từ thật cách LUNG một chữ (LƯỢNG, LONG, LANG ...) và chữ hỏng không đứng ở chỗ nhãn tầng giữ nguyên."""
    picked = pick_title([*SMALL, line(text, 300, 500, 900, 560)], SIZE)
    info = parse_title(picked.text)
    assert info is not None
    assert info.storey == storey


def test_far_scale_note_does_not_lift_a_candidate() -> None:
    """Ghi chú tỉ lệ ở xa (khác dòng, xa ngang) không cộng điểm: tên to hơn vẫn thắng."""
    texts = [
        *SMALL,
        line("MAT BANG TANG 1", 100, 300, 500, 360),
        line("MAT CAT A-A", 1200, 900, 1500, 940),
        line("TL 1:100", 1510, 905, 1600, 935),
        line("TL 1:50", 100, 1150, 200, 1180),
    ]
    assert titles._scale_near(titles._lines([texts[6]])[0], titles._lines(texts[-1:])) == 0.0
    assert pick_title(texts, SIZE).text.startswith("MAT CAT A-A")  # có thể kèm "TL 1:100" nối cùng dòng


def test_keyword_index_matches_the_plain_comparison() -> None:
    """Bảng tra từ khoá cho đúng kết quả của phép so từng từ khoá, kể cả với chữ rác."""
    words = {
        "TANG",
        "TNG",
        "TAANG",
        "TUNG",
        "BANH",
        "FLOR",
        "SECTON",
        "GROUNDS",
        "ELEVATIONS",
        "NGAT",
        "XYZ",
        "ABCDEFGH",
    }
    for key in titles._SNAP_WORDS:
        words |= titles._one_off(key) | {key[:i] + "X" + key[i:] for i in range(len(key) + 1)}
    for word in words:
        plain = tuple(key for key in titles._SNAP_WORDS if titles._near_by_one_edit(word, key))
        assert titles._near_keys(word) == plain, word


def test_repair_budget_serves_short_lines_first() -> None:
    """Hình quá nhiều chữ: tên ngắn vẫn được sửa lỗi OCR, các dòng dài nhất mới bị bỏ qua."""
    noise = [Ocr("GHI CHU SO " * 16, (10.0, 10.0 + 12 * i, 900.0, 20.0 + 12 * i)) for i in range(90)]
    assert sum(len(n.text.split()) for n in noise) > titles._REPAIR_WORDS
    picked = pick_title([*noise, Ocr("MAT BANH TUNG 2", (600.0, 1130.0, 1200.0, 1170.0))], SIZE)
    assert picked.text == "MAT BANG TANG 2"
