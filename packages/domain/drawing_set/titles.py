"""Tên hình trong bộ bản vẽ: gấp chữ, đọc loại hình + nhãn tầng, chọn tên hình từ chữ OCR.

Gương phần chỉ-dùng-chữ của `F:/runs/dwglab/muc3/kernel/{classify,legacy_text,ocr_views}.py` (HS-01).
Thuần thư viện chuẩn: không ảnh, không DB.

Thay đổi có chủ ý so với kernel (kernel gom mọi thứ vào số tầng `floor_no`):
 * Nhãn tầng giữ **như viết** (`storey`) cùng cách đánh số (`numbering`); đổi sang quy ước
   của bộ (lầu N = tầng N+1, "first floor" = 2) là việc của lớp tầng (T3), không làm ở đây.
 * Mái, khu đất là loại hình `roof`, `site` cho cả tiếng Việt lẫn Anh; kernel ép bản Việt về
   `plan` để khớp đáp án thử. Hầm, lửng, tum, sân thượng là nhãn tầng chứ không phải variant.
 * Mặt bằng kết cấu/trần/điện/nước/lát sàn cũng mang nhãn tầng (kernel để trống), variant cho
   người gọi chọn bản "plain" trước.
 * Thêm loại `detail` (chi tiết) và đổi ISOMETRIC/PHỐI CẢNH thành `other`.
"""

import bisect
import heapq
import math
import re
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Final, Literal, Protocol

from packages.domain.drawing_set.legacy_text import decode_legacy

Kind = Literal["plan", "section", "elevation", "roof", "site", "detail", "other"]
Variant = Literal["plain", "furniture", "ceiling", "electrical", "plumbing", "structure", "other"]
StoreyWord = Literal["ground", "basement", "mezzanine", "tum", "attic", "roof_terrace"]
Numbering = Literal["tang", "lau", "floor_ordinal"]
TitleSource = Literal["under_drawing", "title_block", "none"]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class TitleInfo:
    """Kết quả đọc một tên hình.

    `storey` là số như viết ("TẦNG 2" -> 2, "LẦU 1" -> 1, "2ND FLOOR" -> 2) hoặc một từ
    (`StoreyWord`), None khi không có; `numbering` cho biết số đó đếm theo kiểu nào (None với từ).
    `norm` là tên đã gấp chữ. `storey_rejected`: tên CÓ viết số tầng nhưng số đó bị loại (tầng suy ra
    ngoài 1..MAX_FLOOR) -> `storey=None`; khác với "không viết số" ở chỗ nó vẫn là một lời khai về tầng.
    """

    kind: Kind
    storey: int | StoreyWord | None
    numbering: Numbering | None
    variant: Variant
    norm: str
    storey_rejected: bool = False


@dataclass(frozen=True)
class PickedTitle:
    """Tên hình chọn được từ chữ OCR: `text` rỗng khi `source == "none"`; `confidence` trong [0, 1];
    `sources_disagree` khi tên dưới hình và tên khung tên nêu loại hình hoặc tầng khác nhau."""

    text: str
    source: TitleSource
    confidence: float
    sources_disagree: bool = False


@dataclass(frozen=True)
class KindGuess:
    """Đoán loại hình: `reason` là mã máy đọc (TITLE_AND_CONTENT_AGREE, TITLE_ONLY, ...)."""

    kind: Kind
    confidence: float
    reason: str


class OcrText(Protocol):
    """Một dòng chữ OCR: nội dung, hộp (x0, y0, x1, y1) theo pixel, độ tin cậy của OCR."""

    @property
    def text(self) -> str:
        """Nội dung dòng chữ."""
        ...

    @property
    def box(self) -> Sequence[float]:
        """Hộp (x0, y0, x1, y1)."""
        ...

    @property
    def confidence(self) -> float:
        """Độ tin cậy OCR (kernel không dùng)."""
        ...


# ---------------------------------------------------------------- gấp chữ
_SPACES = re.compile(r"\s+")


def _ascii_base(char: str) -> str:
    """Phần ASCII chữ-số của một ký tự (sau NFKD), rỗng nếu không có."""
    decomposed = unicodedata.normalize("NFKD", char)
    return "".join(c for c in decomposed if c.isascii() and c.isalnum())


def _fold_unicode(text: str) -> str:
    """Unicode -> ASCII hoa: bỏ dấu, đ/Đ -> D, ký tự lạ không quy được thành khoảng trắng."""
    if text.isascii():  # NFD và combining() không đổi ký tự ASCII: kết quả như vòng dưới, nhanh hơn nhiều
        return text.upper()
    text = text.replace("đ", "d").replace("Đ", "D")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return "".join(c if c.isascii() else (_ascii_base(c) or " ") for c in text).upper()


_CACHE = 1 << 15  # mục tối đa của mỗi bộ nhớ đệm hàm thuần str -> giá trị bất biến (chữ lặp lại trong và giữa các hình)


# Phần dư VNI sau OCR: tên hình viết bằng phông VNI, OCR trên ẢNH bỏ dấu nhưng giữ chữ của ký tự dấu ("MAËT BAÈNG
# LAÀU 1" in ra thành "MAET BAENG LAAU 1"). `decode_legacy` chỉ gặp được dạng còn byte dấu (chữ lấy từ DXF). Chỉ gấp
# bộ từ vựng tên hình/tầng, theo nguyên từ: gấp mọi cặp nguyên âm sẽ làm hỏng chữ thường ("TOAN", "KHOA").
_OCR_VNI_RESIDUE: Final = {
    "MAET": "MAT",  # MAËT
    "BAENG": "BANG",  # BAÈNG
    "TREAT": "TRET",  # TREÄT
    "LAAU": "LAU",  # LAÀU
    "TAANG": "TANG",  # TAÀNG
    "HAAM": "HAM",  # HAÀM
    "MAUI": "MAI",  # MAÙI
    "CAET": "CAT",  # CAÉT
    "LOUNG": "LUNG",  # LÖÛNG
    "SAAN": "SAN",  # SAÂN
    "THOOING": "THUONG",  # THÖÔÏNG
    "THOOONG": "THUONG",  # THÖÔÏNG, dấu nặng đọc thành O
    "NOUNG": "DUNG",  # ÑÖÙNG (Ñ = Đ)
    "DOUNG": "DUNG",
    "TIEAN": "TIEN",  # TIEÀN
    "BEAN": "BEN",  # BEÂN
    "NAAT": "DAT",  # ÑAÁT
    "NOA": "DO",  # ÑOÀ (BÌNH ĐỒ)
    "DOIC": "DOC",  # DOÏC
    "TIEAT": "TIET",  # TIEÁT
}
# Số dính liền sau từ ("LAAU2", bộ DM-481 qua OCR f14) vẫn là từ đó: `\b` không tách chữ khỏi số.
_OCR_VNI_WORD: Final = re.compile(r"\b(" + "|".join(_OCR_VNI_RESIDUE) + r")(?=\d|\b)")


# Phần dư TCVN3 sau OCR ("mÆt b»ng tÇng 1" in không phông ra ảnh, OCR đọc thành "MET b>ng tCng 1", "mAEt c3/4t a-a",
# "MET b>ng tCng p m.i"). Thay theo cụm có ngữ cảnh: "MET" đứng một mình là mét, chỉ đổi khi đứng trước BANG/CAT.
_OCR_TCVN3_RESIDUE: Final = (
    (re.compile(r"\bB>NG\b"), "BANG"),
    (re.compile(r"\bTCNG(?=\d|\b)"), "TANG"),  # "tCng1" (DM-528): số dính liền
    (re.compile(r"\bC3/4T\b"), "CAT"),
    (re.compile(r"\bM(?:A?E|EE)T (?=BANG\b|CAT\b)"), "MAT "),
    (re.compile(r"\bTANG P (?=M[.,]?I\b)"), "TANG AP "),
    (re.compile(r"\b(BANG|TANG|AP) M[.,]?I\b"), r"\1 MAI"),  # "m¸i" ra "m.i" hay "m,i" (DM-445)
)


def _fold_ocr_vni(text: str) -> str:
    """Thay các từ dư VNI/TCVN3 sau OCR (đã hoa, không dấu) bằng từ đúng; chữ khác giữ nguyên."""
    text = _OCR_VNI_WORD.sub(lambda m: _OCR_VNI_RESIDUE[m.group(1)], text)
    for pattern, repl in _OCR_TCVN3_RESIDUE:
        text = pattern.sub(repl, text)
    return text


@lru_cache(maxsize=_CACHE)
def fold(text: str) -> str:
    """Chữ bản vẽ -> ASCII hoa một khoảng trắng: giải mã VNI/TCVN3 (cả phần dư VNI sau OCR), bỏ dấu, Đ -> D,
    xuống dòng -> cách.

    Dùng cho mọi so khớp từ khoá; chuỗi rỗng hoặc None-like cho chuỗi rỗng.
    """
    return _fold_ocr_vni(_SPACES.sub(" ", _fold_unicode(decode_legacy(text or ""))).strip())


# ---------------------------------------------------------------- loại hình, biến thể, tầng
_SECTION = re.compile(r"MAT CAT|\bSECTION\b|CAT DOC|CAT NGANG")
_ELEVATION = re.compile(r"MAT DUNG|MAT TIEN|MAT BEN|ELEVATION|\bFACADE\b")
_THREE_D = re.compile(r"ISOMETRIC|AXONOMETRIC|PERSPECTIVE|PHOI CANH")
_VN_PLAN_WORD = re.compile(r"MAT BANG|BINH DO|\bTANG\b|\bLAU\b|\bTRET\b|\bTUM\b|SAN THUONG|\bMAI\b|KHU DAT")
_EN_PLAN_WORD = re.compile(r"\bPLAN\b|FLOOR|\bROOF\b")
_DETAIL = re.compile(r"CHI TIET|\bDETAIL")
_ROOF = re.compile(r"\bROOF|\bMAI\b")
_SITE = re.compile(r"\bSITE\b|LOCATION|KHU DAT|THUA DAT|TONG THE|TONG MAT BANG")
_BASEMENT = re.compile(r"\bHAM\b|BASEMENT|CELLAR|UNDERGROUND")
_TUM = re.compile(r"\bTUM\b")
_ATTIC = re.compile(r"\bAP MAI\b|ATTIC")
_TERRACE = re.compile(r"SAN THUONG|TERRACE")
_MEZZANINE = re.compile(r"\bLUNG\b|MEZZ")
_GROUND = re.compile(r"\bTRET\b|\bGRO\w{1,6}\s+FLOOR|\bGROUND\b")
_STOREY_WORDS: tuple[tuple[StoreyWord, re.Pattern[str]], ...] = (
    ("basement", _BASEMENT),
    ("tum", _TUM),
    ("attic", _ATTIC),
    ("roof_terrace", _TERRACE),
    ("mezzanine", _MEZZANINE),
)
_NUMBERED: tuple[tuple[re.Pattern[str], Numbering], ...] = (
    (re.compile(r"\bLAU\s*0?(\d+)"), "lau"),
    (re.compile(r"\bTANG\s*0?(\d+)"), "tang"),
    (re.compile(r"\b(\d+)\s*(?:ST|ND|RD|TH)\s+FLOOR"), "floor_ordinal"),
)
MAX_FLOOR = 80  # tầng suy ra (lầu N = N+1, "Nth floor" = N+1) phải trong 1..80; ngoài khoảng là lỗi OCR
_ORDINALS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6}
_ORDINAL_WORD = re.compile(r"\b(" + "|".join(_ORDINALS) + r")\s+FLOOR")
# Thứ tự = thứ tự kernel: nền móng trước, nội thất cuối.
_VARIANTS: tuple[tuple[Variant, re.Pattern[str]], ...] = (
    ("structure", re.compile(r"MONG\b|FOND?ATION")),
    ("ceiling", re.compile(r"\bTRAN\b|CEILING")),
    ("structure", re.compile(r"KET CAU|STRUCTUR")),
    ("electrical", re.compile(r"\bDIEN\b|ELECTR")),
    ("plumbing", re.compile(r"\bNUOC\b|PLUMB")),
    ("other", re.compile(r"\bLAT SAN|\bLAT\b")),
    ("furniture", re.compile(r"NOI THAT|FURNITURE")),
)


def _kind_of(n: str) -> Kind | None:
    """Loại hình từ tên đã gấp; None khi không có từ nào chỉ loại bản vẽ."""
    if _SECTION.search(n):
        return "section"
    if _ELEVATION.search(n):
        return "elevation"
    if _THREE_D.search(n):
        return "other"
    if not (_VN_PLAN_WORD.search(n) or _EN_PLAN_WORD.search(n)):
        return "detail" if _DETAIL.search(n) else None
    if _SITE.search(n):
        return "site"
    if _ROOF.search(n) and not (_TUM.search(n) or _ATTIC.search(n) or _TERRACE.search(n)):
        return "roof"
    return "plan"


def _variant_of(n: str) -> Variant:
    """Biến thể của mặt bằng: kết cấu, trần, điện, nước, lát sàn, nội thất; còn lại là plain."""
    for variant, pattern in _VARIANTS:
        if pattern.search(n):
            return variant
    return "plain"


def _storey_word(n: str) -> StoreyWord | None:
    """Nhãn tầng bằng chữ: hầm, tum (áp mái), sân thượng, lửng, trệt; None khi không có."""
    for word, pattern in _STOREY_WORDS:
        if pattern.search(n):
            return word
    return None


def _storey_number(n: str) -> tuple[int, Numbering] | None:
    """Số tầng như viết cùng cách đếm: LẦU N, TẦNG N, "2ND FLOOR", "FIRST FLOOR"."""
    for pattern, numbering in _NUMBERED:
        match = pattern.search(n)
        if match:
            digits = match.group(1)  # > 3 chữ số: không int() chuỗi dài vô hạn của OCR
            return (int(digits) if len(digits) <= 3 else 0), numbering
    match = _ORDINAL_WORD.search(n)
    return (_ORDINALS[match.group(1)], "floor_ordinal") if match else None


def _plausible(number: int, numbering: Numbering) -> bool:
    """Số viết >= 1 và tầng suy ra (cộng một với lầu/"Nth floor", như `_storey_key`) <= MAX_FLOOR.

    Giới hạn đặt lên tầng SUY RA để mọi cách viết cùng một trần: TẦNG 80 và LẦU 79 nhận, TẦNG 81 và LẦU 80 loại.
    """
    return number >= 1 and number + (numbering != "tang") <= MAX_FLOOR


def _storey_of(n: str) -> tuple[int | StoreyWord | None, Numbering | None, bool]:
    """Nhãn tầng của một mặt bằng: hầm/tum/sân thượng/lửng thắng số, số thắng "trệt".

    Phần tử thứ ba: có viết số tầng nhưng số bị loại (0, 81, 999999999999 do OCR) -> không tầng, đánh dấu loại.
    """
    word = _storey_word(n)
    if word:
        return word, None, False
    number = _storey_number(n)
    if number:
        return (*number, False) if _plausible(*number) else (None, None, True)
    return ("ground", None, False) if _GROUND.search(n) else (None, None, False)


@lru_cache(maxsize=_CACHE)  # TitleInfo bất biến (frozen, trường str/int/bool)
def parse_title(text: str) -> TitleInfo | None:
    """Đọc tên hình; None khi không nhận ra từ nào chỉ loại bản vẽ (kể cả chuỗi rỗng).

    Nhãn tầng và biến thể chỉ có nghĩa với mặt bằng (`kind == "plan"`); loại khác để
    `storey=None`, `variant="plain"`.
    """
    n = fold(text)
    kind = _kind_of(n) if n else None
    if kind is None:
        return None
    if kind == "plan":
        storey, numbering, rejected = _storey_of(n)
        return TitleInfo(kind, storey, numbering, _variant_of(n), n, rejected)
    return TitleInfo(kind, None, None, "other" if kind in ("detail", "other") else "plain", n)


# ---------------------------------------------------------------- dòng chữ OCR
TALL = 1.6  # dòng tên cao >= TALL lần trung vị chiều cao chữ
SAME_Y = 0.6  # sai số đường cơ sở, tính theo chiều cao dòng
GAP_X = 2.0  # khoảng hở ngang tối đa khi nối chữ, tính theo chiều cao dòng
MAX_LEN = 60  # tên hình dài tối đa (ký tự)
MAX_TEXTS = 400  # số dòng OCR xét tối đa mỗi hình (chặn chi phí bậc hai của nối dòng/ghi chú)
# ponytail: trần sửa lỗi OCR theo dòng. Dòng dài hơn 3 x MAX_LEN không bao giờ là tên (<= MAX_LEN) hay dòng nhãn
# khung tên (`_block_title` chỉ đọc dòng <= 3 x MAX_LEN) nên không sửa từ khoá/rớt nguyên âm: 400 dòng x 200
# hình x 200 ký tự sửa từng từ tốn hàng chục giây. Chỉ đổi kết quả khi không còn ứng viên nào và tên dự phòng là
# chính dòng dài đó. Nâng nếu thấy tên thật dài hơn.
_REPAIR_MAX = 3 * MAX_LEN
# ponytail: trần số từ được sửa lỗi OCR mỗi hình, dòng ngắn trước. Một mặt bằng thật có vài trăm dòng, mỗi dòng
# vài từ; quá trần thì các dòng dài nhất giữ nguyên chữ OCR. Nâng nếu gặp hình thật vượt trần.
_REPAIR_WORDS = 2000
MAX_TEXT_LEN = 200  # một dòng OCR dài hơn bị cắt: tên hình <= MAX_LEN, phần đuôi chỉ tốn regex
_SCALE = re.compile(r"\bTL\b|\bT\.?L\s*:|TI LE|\bSCALE|\b1\s*[/:]\s*\d{2,3}\b")
_LABEL = re.compile(r"TEN BAN VE|DRAW\w{0,4} ?NAM\w*|DRAW\w{0,4} ?TITL\w*|NOI DUNG|HANG MUC")
_BLOCK_LABEL = re.compile(
    r"\bTEN (?:BAN|HINH) VE|\w?EN (?:BAN|BEN) V[EA]\b"
    r"|(?:DR\w{2,6}|DWG|SHEET)\W{0,3}(?:NA\w{2,3}|TI\w?LE)|NOI DUNG|HANG MUC"
)
_FIELD = re.compile(
    r"TI LE|TY LE|SCALE|SO BAN VE|BAN VE S\w|DRAW\w* NO\b|\bNGAY\b|\bDATE\b|KY HIEU|CHU DAU TU|CHU TRI|CONG TRINH"
    r"|PROJECT|LOCATION|VI TRI|\bKTS\b|THIET KE|DESIGN|GIAM DOC|KHO GIAY|DIA DIEM|PRIMARY|ARCHITECT|GIAI DOAN"
    r"|HSTC|HOAN THANH|\bCHU DT\b"
)
_KIND_WORD = re.compile(r"MAT BANG|BINH DO|\bPLAN\b|FLOOR|MAT CAT|MAT DUNG|SECTION|ELEVATION|MAT TIEN|MAT BEN|CAT DOC")
_PLAN_OR_VIEW = re.compile(r"MAT BANG|BINH DO|\bPLAN\b|FLOOR|MAT CAT|MAT DUNG|SECTION|ELEVATION|MAT TIEN|MAT BEN")
_STOREY_LINE = re.compile(r"\b(?:TANG|LAU) ?0?\d+\b|\bTRET\b")
_SNAP_WORDS = ("TANG", "TRET", "PLAN", "FLOOR", "SECTION", "ELEVATION", "GROUND", "BANG", "DUNG", "TIEN")
_NEED_MAT = ("BANG", "DUNG", "TIEN")  # ngắn và phổ biến: chỉ sửa khi đứng ngay sau một từ giống MAT
# Từ bộ đọc hiểu (mọi từ trong các mẫu `_SECTION` ... `_VARIANTS`, từ thứ tự "FIRST FLOOR", "CHI TIET"):
# sửa lỗi OCR chỉ được động vào chữ NGOÀI tập này, vì chữ thật nằm cách một ký tự với từ khoá khác
# sẽ bị đọc thành nghĩa khác. Va chạm (cách một ký tự) đã dò hết tập, test giữ chúng:
#   TIET -> TRET (chi tiết thành trệt, lỗi NO-441), TIET -> TIEN, DIEN -> TIEN, LUNG -> DUNG, TONG -> TANG;
#   cặp với MAT/CAT: CAU, DAT, LAT, MAI. Cặp còn lại (BANG/TANG, MONG/TONG, TIEN/TIET, ...) cùng là từ thật.
_VOCAB_TEXT = (
    "MAT CAT BANG BINH DO DOC NGANG DUNG TIEN BEN TANG LAU TRET TUM SAN THUONG MAI KHU DAT CHI TIET DETAIL PLAN FLOOR"
    " ROOF SITE LOCATION THUA TONG THE HAM BASEMENT CELLAR UNDERGROUND AP ATTIC TERRACE LUNG MEZZ GROUND SECTION"
    " ELEVATION FACADE ISOMETRIC AXONOMETRIC PERSPECTIVE PHOI CANH MONG FOUNDATION FONDATION TRAN CEILING KET CAU"
    " STRUCTURE STRUCTURAL ELECTRICAL ELECTRIC DIEN NUOC PLUMBING LAT NOI THAT FURNITURE TRUC FIRST SECOND THIRD"
    " FOURTH FIFTH SIXTH"
)
_VOCAB = frozenset(_VOCAB_TEXT.split())
_VOWEL_DROPS = (
    (re.compile(r"\bM\w?T ?BNG"), "MAT BANG"),
    (re.compile(r"\bM\w?T ?D\w?NG ?TR\w?C?"), "MAT DUNG TRUC"),
    (re.compile(r"\bTNG\b"), "TANG"),
)


@dataclass(frozen=True)
class _Line:
    """Một dòng chữ đã sửa lỗi OCR: `n` là bản gấp chữ."""

    text: str
    box: Box
    n: str

    @property
    def h(self) -> float:
        """Chiều cao hộp."""
        return self.box[3] - self.box[1]


@lru_cache(maxsize=_CACHE)
def _near_by_one_edit(a: str, b: str) -> bool:
    """`a` và `b` hơn kém nhau tối đa một lần thay, chèn hoặc xoá ký tự."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


_SNAP_LEN = (min(map(len, _SNAP_WORDS)) - 1, max(map(len, _SNAP_WORDS)) + 1)


def _one_off(word: str) -> set[str]:
    """`word` và mọi bản bỏ một ký tự: hai chuỗi cách nhau tối đa một lần sửa luôn có chung một phần tử."""
    return {word, *(word[:i] + word[i + 1 :] for i in range(len(word)))}


_SNAP_INDEX: dict[str, set[str]] = {}
for _key in _SNAP_WORDS:
    for _variant in _one_off(_key):
        _SNAP_INDEX.setdefault(_variant, set()).add(_key)


@lru_cache(maxsize=_CACHE)
def _near_keys(word: str) -> tuple[str, ...]:
    """Các từ khoá `_SNAP_WORDS` cách `word` tối đa một lần sửa, theo thứ tự `_SNAP_WORDS`. Tra bảng
    `_SNAP_INDEX` trước (chung phần tử là điều kiện cần), rồi mới so kỹ: chữ rác không tốn một lượt so nào."""
    if not _SNAP_LEN[0] <= len(word) <= _SNAP_LEN[1]:
        return ()
    found = set().union(*(_SNAP_INDEX.get(v, ()) for v in _one_off(word)))
    return tuple(key for key in _SNAP_WORDS if key in found and _near_by_one_edit(word, key))


def _snap_word(word: str, prev: str, nxt: str) -> str:
    """Từ khoá cách `word` một lần sửa (từ >= 3 chữ), không thì giữ nguyên; `prev`/`nxt` là từ lân cận.

    Từ đã là từ thật của bộ đọc (`_VOCAB`, ví dụ TIET) không bao giờ bị sửa.
    """
    if len(word) < 3 or word in _VOCAB:
        return word
    for key in _near_keys(word):
        if len(word) == 3 and not nxt[:1].isdigit():
            continue
        after_mat = _near_by_one_edit(prev, "MAT") and len(prev) <= 4
        if key in _NEED_MAT and not after_mat and not (key == "BANG" and _near_by_one_edit(nxt, "TANG")):
            continue
        return key
    return _snap_mezzanine(word, prev, nxt)


# Âm tiết tiếng Việt thật (đã gấp) cách LUNG một chữ: không bao giờ là LỬNG hỏng (LƯỢNG giao thông, LONG, LÀNG ...).
_NEAR_LUNG_WORDS = frozenset(("LUONG", "LUON", "LONG", "LANG", "LUNA", "BUNG", "CUNG", "DUNG", "HUNG", "MUNG"))
_NEAR_LUNG_WORDS |= {"NUNG", "RUNG", "SUNG", "TUNG", "VUNG"}


def _storey_slot(nxt: str) -> bool:
    """Từ kế cho thấy chữ đang ở chỗ nhãn tầng: hết tên, một số, hoặc TẦNG/LẦU (kể cả hỏng một chữ)."""
    return not nxt or nxt[:1].isdigit() or nxt == "LAU" or _near_by_one_edit(nxt, "TANG")


def _snap_mezzanine(word: str, prev: str, nxt: str) -> str:
    """LUNG (lửng) hỏng OCR (LURNG, LUNGG, XLUNG, LUN) -> LUNG, chỉ khi đứng ngay sau BANG/TANG, ở chỗ nhãn
    tầng (cuối tên, trước số hay TẦNG/LẦU) và không phải một từ thật (`_NEAR_LUNG_WORDS`): "MAT BANG LUONG
    GIAO THONG" (luồng giao thông) hay "KHOI LUONG" giữ nguyên. Chạy sau các từ khoá khác (TUNG -> TANG)."""
    if word in _NEAR_LUNG_WORDS or prev not in ("BANG", "TANG") or not _storey_slot(nxt):
        return word
    return "LUNG" if _near_by_one_edit(word, "LUNG") else word


_GLUED = re.compile(r"\b([A-Z]{3,5}?)(TANG|LAU|\d{1,2})\b")


def _unglue(n: str) -> str:
    """Tách chữ giống LUNG dính vào từ/số kế ("LUNGTANG 1", "LURNG1") thành hai từ."""
    return _GLUED.sub(lambda m: f"{m[1]} {m[2]}" if _near_by_one_edit(m[1], "LUNG") else m[0], n)


_PUNCT_IN_WORD = str.maketrans("", "", "';`")  # = re.sub(r"[';`]", "", w), không qua regex mỗi từ


def _snap(n: str) -> str:
    """Tên đã gấp -> bỏ dấu câu trong từ, sửa từ khoá (MAT BANG TANG ..., LUNG) và MAT/CAT bên cạnh."""
    words = [w.translate(_PUNCT_IN_WORD) for w in _unglue(n).split()]
    out: list[str] = []
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        out.append(_snap_word(word, out[-1] if out else "", nxt) if word.isalpha() else word)
    for i, word in enumerate(out[:-1]):
        if not word.isalpha() or word in _VOCAB:
            continue
        if len(word) <= 4 and _near_by_one_edit(word, "MAT") and out[i + 1] in ("BANG", "DUNG", "TIEN", "CAT", "BEN"):
            out[i] = "MAT"
        elif _near_by_one_edit(word, "CAT") and i and out[i - 1] == "MAT":
            out[i] = "CAT"
    return " ".join(out)


def _better(text: str) -> str:
    """`text`, hoặc bản đã sửa từ khoá khi bản đó nêu được loại hình/tầng mà `text` chưa nêu, hoặc khi bản sửa
    là lửng ("LURNG TANG 1": lửng trên tầng 1, không phải tầng 1). Dòng dài hơn `_REPAIR_MAX` giữ nguyên."""
    if len(text) > _REPAIR_MAX:
        return text
    before = parse_title(text)
    n = fold(text)
    snapped = _snap(n)
    after = parse_title(snapped) if snapped != n else None
    if not after:
        return text
    if not before or (before.storey is None and after.storey is not None) or _became_mezzanine(before, after):
        return snapped
    return text


def _became_mezzanine(before: TitleInfo, after: TitleInfo) -> bool:
    """Bản sửa đọc ra lửng mà bản gốc không: lửng thắng số tầng đi sau nó."""
    return after.storey == "mezzanine" != before.storey


def _fix_vowel_drops(text: str) -> str:
    """Sửa lỗi OCR rớt nguyên âm đã gặp (MTBNG TNG 01 -> MAT BANG TANG 01)."""
    for pattern, replacement in _VOWEL_DROPS:
        text = pattern.sub(replacement, text)
    return text


def _usable(item: object) -> tuple[str, Box] | None:
    """(chữ đã cắt MAX_TEXT_LEN, hộp) của một dòng OCR; None khi chữ không phải chuỗi khác rỗng, hộp không
    phải bốn số hữu hạn, rộng/cao <= 0, hoặc là chữ xoay (cao hơn 1,5 lần rộng). Không bao giờ ném lỗi."""
    text, raw = getattr(item, "text", None), getattr(item, "box", None)
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or not 0 < y1 - y0 <= 1.5 * (x1 - x0):
        return None
    return text.strip()[:MAX_TEXT_LEN], (x0, y0, x1, y1)


def _capped(rows: list[tuple[str, Box]]) -> list[tuple[str, Box]]:
    """Quá MAX_TEXTS dòng: giữ nửa cao nhất (tên hình là chữ to) và nửa nằm thấp nhất trang (tên dưới hình,
    khung tên ở đáy trang); hai nửa trùng nhau thì ít hơn MAX_TEXTS. Thứ tự gốc giữ nguyên."""
    if len(rows) <= MAX_TEXTS:
        return rows
    half = MAX_TEXTS // 2
    order = range(len(rows))
    tall = sorted(order, key=lambda i: rows[i][1][1] - rows[i][1][3])[:half]
    low = sorted(order, key=lambda i: -rows[i][1][3])[:half]
    return [rows[i] for i in sorted({*tall, *low})]


def _lines(texts: Sequence[OcrText]) -> list[_Line]:
    """Dòng OCR dùng được (`_usable`, tối đa MAX_TEXTS theo `_capped`), đã sửa lỗi OCR, sắp từ trên xuống."""
    rows = _capped([row for row in map(_usable, texts) if row is not None])
    budget = _REPAIR_WORDS
    fixed = [text for text, _ in rows]
    for i in sorted(range(len(rows)), key=lambda i: len(rows[i][0])):  # dòng ngắn trước: tên hình là dòng ngắn
        if len(fixed[i]) > _REPAIR_MAX or budget <= 0:
            break
        budget -= fixed[i].count(" ") + 1
        fixed[i] = _better(_fix_vowel_drops(fixed[i]))
    out = [_Line(text, box, fold(text)) for text, (_, box) in zip(fixed, rows, strict=True)]
    return sorted(out, key=lambda ln: (ln.box[1], ln.box[0]))


def _merge(a: _Line, b: _Line) -> _Line:
    """Nối `b` vào `a` thành một dòng (hộp bao, chữ sửa lại từ khoá)."""
    box = (min(a.box[0], b.box[0]), min(a.box[1], b.box[1]), max(a.box[2], b.box[2]), max(a.box[3], b.box[3]))
    text = _better(a.text + " " + b.text)
    return _Line(text, box, fold(text))


def _short_merges(items: list[_Line]) -> list[_Line]:
    """Các dòng cùng các bản nối tối đa 3 dòng cùng đường cơ sở, cao gần nhau (30 %), hở <= GAP_X dòng.

    Chỉ xét dòng có đáy trong cửa sổ dọc +-SAME_Y dòng (tìm nhị phân trên danh sách sắp theo đáy).
    """
    out = list(items)
    by_bottom = sorted(range(len(items)), key=lambda k: items[k].box[3])
    bottoms = [items[k].box[3] for k in by_bottom]
    for i, first in enumerate(items):
        cur = first
        lo = bisect.bisect_left(bottoms, first.box[3] - SAME_Y * first.h)
        hi = bisect.bisect_right(bottoms, first.box[3] + SAME_Y * first.h)
        right = heapq.nsmallest(  # = sorted(...)[:2], ổn định như sorted, khỏi sắp cả cửa sổ cho mỗi dòng
            2,
            (items[k] for k in by_bottom[lo:hi] if k > i and items[k].box[0] >= first.box[2] - first.h),
            key=lambda x: x.box[0],
        )
        for nxt in right:
            if abs(nxt.h - cur.h) > 0.3 * max(nxt.h, cur.h) or nxt.box[0] - cur.box[2] > GAP_X * cur.h:
                break
            cur = _merge(cur, nxt)
            out.append(cur)
    return out


def _is_note(c: _Line, items: list[_Line]) -> bool:
    """`c` nằm trong đoạn ghi chú dày: >= 2 dòng khác cùng cỡ chữ xếp sát trên/dưới, chồng nhau theo x."""

    def inside(o: _Line) -> bool:
        return (
            o.box[0] >= c.box[0] - 1
            and o.box[2] <= c.box[2] + 1
            and o.box[1] >= c.box[1] - 1
            and o.box[3] <= c.box[3] + 1
        )

    def stacked(o: _Line) -> bool:
        dy = abs((o.box[1] + o.box[3]) - (c.box[1] + c.box[3])) / 2
        return abs(o.h - c.h) <= 0.25 * c.h and o.box[0] < c.box[2] and o.box[2] > c.box[0] and 0 < dy <= 2.2 * c.h

    return sum(1 for o in items if o is not c and not inside(o) and stacked(o)) >= 2


def _scale_near(c: _Line, items: list[_Line]) -> float:
    """1,0 khi ghi chú tỉ lệ nằm trong `c`; 0,7 khi ở cùng dòng hoặc dòng kế, sát bên; 0 nếu không."""
    if _SCALE.search(c.n):
        return 1.0
    cy = (c.box[1] + c.box[3]) / 2
    for o in items:
        below = -c.h <= (o.box[1] + o.box[3]) / 2 - cy <= 2.5 * c.h
        if _SCALE.search(o.n) and below and abs(o.box[0] - c.box[2]) <= 8 * c.h:
            return 0.7
    return 0.0


def _good(c: _Line, items: list[_Line]) -> bool:
    """Ứng viên tên dưới hình: ngắn, ít chữ số, nhận ra loại hình, không phải nhãn ô tên/ghi chú."""
    if len(c.text) > MAX_LEN:  # điều kiện rẻ nhất trước; các vế dưới đều thuần nên thứ tự không đổi kết quả
        return False
    digits = sum(ch.isdigit() for ch in c.n) / max(len(c.n), 1)
    weak = not _PLAN_OR_VIEW.search(c.n) and len(c.n) > 14  # "nha 3 tang", "ba mai"
    return (
        digits < 0.35
        and parse_title(c.text) is not None
        and not weak
        and not _LABEL.search(c.n)
        and not _is_note(c, items)
    )


def _rank(c: _Line, items: list[_Line], med: float, size: tuple[float, float]) -> float:
    """Điểm ứng viên: cao hơn trung vị, gần ghi chú tỉ lệ, nằm thấp và giữa trang, có từ chỉ loại hình."""
    cx = (c.box[0] + c.box[2]) / 2 / size[0]
    cy = (c.box[1] + c.box[3]) / 2 / size[1]
    bare = 0.0 if _KIND_WORD.search(c.n) else 1.5  # "TANG 1" đứng một mình là tên yếu
    return min(c.h / med, 4.0) + 1.5 * _scale_near(c, items) + 0.8 * cy - 0.4 * abs(cx - 0.5) + len(c.n) / 100 - bare


def _add_storey(best: _Line, items: list[_Line]) -> _Line:
    """Mặt bằng plain chưa có tầng: nối dòng tầng nằm sát dưới hoặc bên phải (tên hai dòng)."""
    info = parse_title(best.text)
    if not info or info.kind != "plan" or info.variant != "plain" or info.storey is not None:
        return best
    if not _KIND_WORD.search(best.n):
        return best
    b = best.box

    def below(o: _Line) -> bool:
        return 0 <= o.box[1] - b[3] <= 1.5 * best.h and o.box[0] < b[2] + best.h and o.box[2] > b[0] - best.h

    def right(o: _Line) -> bool:
        return abs(o.box[3] - b[3]) <= SAME_Y * best.h and b[0] < o.box[0] <= b[2] + GAP_X * best.h

    near = [
        o
        for o in items
        if _STOREY_LINE.search(o.n) and not _KIND_WORD.search(o.n) and len(o.text) <= 20 and (below(o) or right(o))
    ]
    if not near:
        return best
    return replace(best, text=best.text + " " + min(near, key=lambda o: (o.box[1], o.box[0])).text)


def _mid(o: _Line) -> float:
    """Tung độ tâm hộp."""
    return (o.box[1] + o.box[3]) / 2


def _stack_window(c: _Line, by_mid: list[_Line], mids: list[float]) -> list[_Line]:
    """Dòng có tâm cách tâm `c` trong (0, 2,2 dòng]: đúng tập `_is_note` có thể đếm (dòng cùng tâm bị bỏ,
    nên một hàng dài chữ cùng đường cơ sở không làm việc thành bậc hai)."""
    cy, reach = _mid(c), 2.2 * c.h
    lo, eq_lo = bisect.bisect_left(mids, cy - reach), bisect.bisect_left(mids, cy)
    eq_hi, hi = bisect.bisect_right(mids, cy), bisect.bisect_right(mids, cy + reach)
    return [*by_mid[lo:eq_lo], *by_mid[eq_hi:hi]]


def _page(size: tuple[float, float], items: list[_Line]) -> tuple[float, float]:
    """Cỡ trang dùng làm mẫu số: `size` khi hai số hữu hạn > 0; không thì góc dưới phải của hộp bao các
    dòng; vẫn <= 0 thì 1."""
    out = []
    for axis, value in enumerate(size):
        if not (math.isfinite(value) and value > 0):
            value = max((ln.box[axis + 2] for ln in items), default=1.0)
        out.append(value if value > 0 else 1.0)
    return out[0], out[1]


def _best_candidate(cands: list[_Line], items: list[_Line], size: tuple[float, float]) -> _Line:
    """Tên dưới hình: ứng viên nhận ra được điểm cao nhất; không có thì dòng ngắn cao nhất."""
    med = max(statistics.median(i.h for i in items), 1e-9)  # `_usable` đã bỏ hộp cao 0; chặn thêm cho chắc
    size = _page(size, items)
    by_mid = sorted(items, key=_mid)
    mids = [_mid(o) for o in by_mid]
    good = [c for c in cands if _good(c, _stack_window(c, by_mid, mids))]
    if good:
        scales = [o for o in items if _SCALE.search(o.n)]  # `_scale_near` chỉ nhìn các dòng tỉ lệ
        return max(good, key=lambda c: _rank(c, scales, med, size))
    tall = [c for c in cands if len(c.text) <= MAX_LEN and c.h >= TALL * med]
    return max(tall or cands, key=lambda c: c.h)


# ---------------------------------------------------------------- khung tên (title block)
def _is_field(c: _Line) -> bool:
    """Dòng là nhãn hoặc trường khác của khung tên (tỉ lệ, ngày, số bản vẽ, ...)."""
    return bool(_BLOCK_LABEL.search(c.n) or _FIELD.search(c.n))


def _value_ok(c: _Line) -> bool:
    """Dòng đọc như tên hình: ngắn, ít chữ số, không phải trường khung tên, nhận ra loại hình."""
    digits = sum(ch.isdigit() for ch in c.n) / max(len(c.n), 1)
    return len(c.text) <= MAX_LEN and digits < 0.35 and not _is_field(c) and parse_title(c.text) is not None


def _in_cell(lab: _Line, c: _Line, items: list[_Line]) -> bool:
    """`c` nằm trong ô giá trị của nhãn `lab`: bên dưới (<= 6 dòng nhãn, chồng theo x) hoặc bên phải cùng dòng."""
    b, cb, h = lab.box, c.box, lab.h
    same_line = abs(cb[3] - b[3]) <= SAME_Y * h
    if cb[0] < b[2] - 0.2 * h and cb[2] > b[0] + 0.2 * h and same_line:
        return False  # chồng lên chính nhãn
    others = [f for f in items if f is not lab and _is_field(f)]
    if same_line:
        stop = any(abs(f.box[3] - b[3]) <= SAME_Y * h and b[2] - 0.2 * h <= f.box[0] < cb[0] for f in others)
        return b[2] - 0.2 * h <= cb[0] <= b[2] + 8 * h and not stop
    if not (b[3] - 0.3 * h <= cb[1] <= b[3] + 6 * h and cb[0] < b[2] + h and cb[2] > b[0]):
        return False
    return not any(
        f.box[1] >= b[3] - 0.3 * h and f.box[3] <= cb[1] + 0.3 * h and f.box[0] < cb[2] and f.box[2] > cb[0]
        for f in others
    )


def _label_value(lab: _Line, cands: list[_Line], items: list[_Line]) -> tuple[tuple[bool, float, float], str] | None:
    """Giá trị tốt nhất trong ô của nhãn `lab`: (khoá xếp hạng, chữ); None khi không có."""
    best: tuple[tuple[bool, float, float], str] | None = None
    for c in cands:
        if c is lab or not _value_ok(c) or not _in_cell(lab, c, items):
            continue
        key = (bool(_KIND_WORD.search(c.n)), c.h, -abs(c.box[1] - lab.box[3]))
        if best is None or key > best[0]:
            best = (key, c.text)
    return best


def _block_title(items: list[_Line], cands: list[_Line]) -> str:
    """Tên hình trong khung tên: chữ sau nhãn (TEN BAN VE / DRAWING NAME ...) hoặc trong ô dưới/bên phải nhãn.

    Trong các ứng viên của mọi nhãn: có từ chỉ loại hình trước, rồi cao nhất, rồi gần nhất.
    """
    best: tuple[tuple[bool, float, float], str] | None = None
    for lab in items:
        match = _BLOCK_LABEL.search(lab.n)
        if not match:
            continue
        rest = re.sub(r"^[\s:\-]+", "", lab.n[match.end() :])
        if len(lab.text) <= 3 * MAX_LEN and parse_title(rest) and len(rest) <= MAX_LEN:
            return rest
        found = _label_value(lab, cands, items)
        if found and (best is None or found[0] > best[0]):
            best = found
    return best[1] if best else ""


_BASEMENT_DEPTH = re.compile(r"(?:\bHAM|BASEMENT)\s*B?\s*(\d+)")
StoreyKey = int | str | tuple[str, int] | None


def _basement_depth(n: str) -> int:
    """Độ sâu hầm như viết (HẦM B2, HẦM 2 -> 2); không ghi số = hầm 1; số dài vô lý -> 0."""
    match = _BASEMENT_DEPTH.search(n)
    digits = match.group(1) if match else "1"
    return int(digits) if len(digits) <= 3 else 0


def _storey_key(info: TitleInfo) -> StoreyKey:
    """Khoá so tầng khi hai nguồn tên cùng nói về một hình: lầu N và "Nth floor" đứng sau tầng trệt một bậc;
    hầm kèm độ sâu; số bị loại vẫn là một khoá (("rejected", số viết)), chỉ "không viết gì" mới là None.

    Chỉ dùng để xét hai tên có khớp nhau không; quy ước tầng chính thức là việc của T3.
    """
    if info.storey_rejected:
        number = _storey_number(info.norm)
        return "rejected", number[0] if number else 0
    if info.storey == "basement":
        return "basement", _basement_depth(info.norm)
    if isinstance(info.storey, int) and info.numbering in ("lau", "floor_ordinal"):
        return info.storey + 1
    return info.storey


# Độ tin cậy là hằng heuristic, chưa hiệu chỉnh trên dữ liệu sản phẩm.
# ponytail: hiệu chỉnh lại theo tỉ lệ sai của từng nhóm nguồn khi T0/T5 đo xong.
_CONF_BOTH_AGREE = 0.9
_CONF_UNDER_ONLY = 0.8  # cũng là độ tin khi hai nguồn khớp nhưng một bên thiếu tầng
_CONF_BLOCK_ONLY = 0.7
_CONF_DISAGREE = 0.6


def _agreement(a: str, b: str) -> float | None:
    """Độ tin khi hai tên khớp: cùng loại hình và cùng tầng 0,9; cùng loại, một bên thiếu tầng (OCR rớt số,
    "... Tang" so với "... Tang 1") 0,8 vì thiếu là "chưa biết" chứ không phải "khác". None khi lệch: khác
    loại hình hoặc hai khoá tầng có mặt mà khác nhau (số bị loại, độ sâu hầm cũng là khoá có mặt)."""
    pa, pb = parse_title(a), parse_title(b)
    if not (pa and pb and pa.kind == pb.kind):
        return None
    ka, kb = _storey_key(pa), _storey_key(pb)
    if ka == kb:
        return _CONF_BOTH_AGREE
    return _CONF_UNDER_ONLY if ka is None or kb is None else None


def pick_title(texts: Sequence[OcrText], size: tuple[float, float]) -> PickedTitle:
    """Chọn tên hình từ chữ OCR cả trang (`size` = rộng, cao của ảnh).

    Tên dưới hình = ứng viên nhận ra được điểm cao nhất (cao > gần ghi chú tỉ lệ > thấp, giữa trang);
    tên khung tên = chữ sau nhãn "TEN BAN VE". Cả hai nhận ra -> lấy tên dưới hình, tin cậy cao khi
    khớp nhau, thấp khi lệch; chỉ khung tên nhận ra -> lấy nó; không nhận ra gì -> `source="none"`,
    `text=""` (kernel trả dòng cao nhất dù không đọc ra loại hình; ở đây bỏ vì không dùng được).
    `OcrText.confidence` không được dùng, như kernel.
    """
    items = _lines(texts)
    if not items:
        return PickedTitle("", "none", 0.0)
    cands = _short_merges(items)
    under = _add_storey(_best_candidate(cands, items, size), items).text
    block = _block_title(items, cands)
    under_ok, block_ok = parse_title(under) is not None, parse_title(block) is not None
    if under_ok:
        if block_ok:
            agree = _agreement(under, block)
            if agree is None:
                return PickedTitle(under, "under_drawing", _CONF_DISAGREE, sources_disagree=True)
            return PickedTitle(under, "under_drawing", agree)
        return PickedTitle(under, "under_drawing", _CONF_UNDER_ONLY)
    if block_ok:
        return PickedTitle(block, "title_block", _CONF_BLOCK_ONLY)
    return PickedTitle("", "none", 0.0)


# ---------------------------------------------------------------- đoán loại hình
_EXPECTED_CONTENT: dict[str, str] = {"plan": "plan", "roof": "plan", "section": "vertical", "elevation": "vertical"}
_FEW_TEXTS = 2  # hình gần như trống: thường là mặt cắt/mặt đứng chưa đọc ra chữ
_MANY_TEXTS = 20  # nhiều chữ mà không có dấu hiệu đứng: nghiêng về mặt bằng


def _content_cue(n_texts: int) -> tuple[str | None, str]:
    """Dấu hiệu từ số dòng chữ: ("vertical"|"plan"|None, mã lý do). Chỉ còn dấu hiệu yếu."""
    if n_texts <= _FEW_TEXTS:
        return "vertical", "EMPTY_VIEW"
    if n_texts >= _MANY_TEXTS:
        return "plan", "MANY_TEXTS"
    return None, "NO_USABLE_CONTENT"


def _kind_from_title(kind: Kind, cue: str | None) -> tuple[float, str]:
    """Độ tin cậy loại hình từ tên: cùng ý với nội dung 0,85; không có/ngược ý yếu 0,8; loại `other` tối đa 0,8."""
    expected = _EXPECTED_CONTENT.get(kind)
    if expected is None or cue is None:
        return 0.8, "TITLE_ONLY"
    if cue == expected:
        return 0.85, "TITLE_AND_CONTENT_AGREE"
    return 0.8, "TITLE_CONTENT_DISAGREE_WEAK"


def view_kind(texts: Sequence[OcrText], size: tuple[float, float]) -> KindGuess:
    """Đoán loại hình từ chữ OCR: tên trước (`pick_title`), nội dung sau.

    Gương `classify()` của kernel khi không có ảnh. Bỏ các dấu hiệu cần mặt nạ/ảnh hay phát hiện
    đối tượng: cầu thang/cửa/cửa sổ, bong bóng trục, dấu cốt (cốt đọc ở `levels.py`), tỉ lệ khung
    hình, và vì thế không còn nhánh "mâu thuẫn mạnh" 0,55 và độ tin 0,92 của kernel. Còn lại dấu
    hiệu theo số dòng chữ (<= 2: gần trống -> đứng; >= 20 -> mặt bằng), đều yếu.
    Có tên: tin cậy = min(độ tin của tên, độ tin loại hình). Không tên: mặt bằng 0,5, đứng 0,3
    (không phân biệt được mặt cắt/mặt đứng nên đoán mặt đứng), còn lại `other` 0,1.
    """
    return kind_from_picked(texts, pick_title(texts, size))


def kind_from_picked(texts: Sequence[OcrText], picked: PickedTitle) -> KindGuess:
    """`view_kind` khi đã có `pick_title(texts, size)` (người gọi cần cả hai thì khỏi chọn tên hai lần)."""
    cue, cue_reason = _content_cue(len(texts))
    info = parse_title(picked.text) if picked.text else None
    if info is not None:
        kind_conf, reason = _kind_from_title(info.kind, cue)
        return KindGuess(info.kind, min(kind_conf, picked.confidence), reason)
    if cue == "plan":
        return KindGuess("plan", 0.5, "CONTENT_ONLY_" + cue_reason)
    if cue == "vertical":
        return KindGuess("elevation", 0.3, "CONTENT_ONLY_" + cue_reason)
    return KindGuess("other", 0.1, "NO_TITLE_NO_CONTENT")
