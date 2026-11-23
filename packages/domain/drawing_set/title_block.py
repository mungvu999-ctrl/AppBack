"""Khung tên bản vẽ: chủ đầu tư, công trình, địa điểm, số hiệu bản vẽ, ngày, đọc từ chữ OCR (K-65).

Hàm thuần, chỉ thư viện chuẩn. Nhãn của khung tên (CHỦ ĐẦU TƯ, CÔNG TRÌNH, ĐỊA ĐIỂM, SỐ BẢN VẼ, NGÀY...) tìm theo
chữ đã gấp (`titles.fold`: giải mã VNI/TCVN3, bỏ dấu, hoa) nên chạy cả với chữ OCR không dấu; giá trị là chữ gốc
đã giải mã (`decode_legacy`, NFC), không gấp. Giá trị nằm cùng dòng sau nhãn, bên phải nhãn, hoặc dưới nhãn
(một ô khung tên): tìm theo hình học hộp chữ, sau khi loại ô nhãn khác chắn giữa.

Nguyên tắc: trường không chắc thì `None`, không đoán. Một trường chỉ trả khi: có nhãn rõ, giá trị qua kiểm
hợp lệ của kiểu trường (ngày đúng dạng ngày, số hiệu đúng dạng mã, tên có chữ cái), và không có hai giá trị
khác nhau cùng điểm. Ngày của bảng sửa đổi (SỬA ĐỔI/REVISION) không phải ngày phát hành nên bị loại.
Tên bản vẽ (TÊN BẢN VẼ) là việc của `titles.pick_title`, không làm ở đây.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final, Literal

from packages.domain.drawing_set.legacy_text import decode_legacy
from packages.domain.drawing_set.titles import OcrText, fold

Source = Literal["same_line", "right", "below"]
Box = tuple[float, float, float, float]

CONF_SAME_LINE: Final = 0.9
CONF_RIGHT: Final = 0.85
CONF_BELOW: Final = 0.8
CONF_FAR_PENALTY: Final = 0.15  # giá trị xa nhãn quá `FAR_GAP` chiều cao dòng
CONF_MULTILINE_PENALTY: Final = 0.05
MIN_CONFIDENCE: Final = 0.6
SAME_RATIO: Final = 0.85  # giống nhau tối thiểu để coi hai chuỗi OCR là cùng một giá trị
TIE_EPS: Final = 0.05  # hai giá trị khác nhau mà điểm lệch dưới mức này: không chắc, trả None
FAR_GAP: Final = 3.0

RIGHT_MAX_GAP: Final = 15.0  # khoảng hở ngang tối đa tới giá trị cùng dòng, theo chiều cao dòng
SAME_LINE: Final = 0.6  # lệch tâm dọc tối đa của cùng một dòng
BELOW_MAX_GAP: Final = 7.0  # giá trị dưới nhãn: đỉnh hộp cách đáy nhãn tối đa
BELOW_X_SLACK: Final = 8.0  # giá trị lệch phải khỏi nhãn tối đa (ô khung tên thường thụt vào)
MULTILINE_GAP: Final = 1.8  # dòng nối tiếp cách dòng trước tối đa
MAX_LINES: Final = 3
MAX_ITEMS: Final = 600  # số dòng OCR xét tối đa (chặn chi phí bậc hai trên đầu vào thù địch)
MAX_TEXT_LEN: Final = 120
MAX_ASPECT: Final = 1.5  # hộp cao hơn rộng quá mức này là chữ xoay
MAX_LABEL_LEN: Final = 60  # nhãn dài hơn thế là câu ghi chú, không phải ô nhãn
MIN_LETTERS: Final = 4
MIN_LETTER_SHARE: Final = 0.5
REVISION_REACH: Final = (
    4.0  # chữ SỬA ĐỔI/REVISION cách nhãn ngày tối đa (chiều cao dòng) phía trên thì đó là bảng sửa đổi
)

Field = Literal["owner", "project", "location", "sheet_no", "date"]
_TEXT_FIELDS: Final = frozenset({"owner", "project", "location"})

_LABELS: Final[dict[Field, re.Pattern[str]]] = {
    "owner": re.compile(r"\bCHU DAU T\w{0,2}\b|\bCHU NHA\b|\bCHU HO\b|\bOWNER\b|\bCLIENT\b"),
    "project": re.compile(
        r"\bTEN (?:CONG TRINH|DU AN)\b|(?<!\bMA )\bC[OU]NG (?:TR|RL|TL)\w{2,3}\b|(?<!\bMA )\bDU AN\b"
        r"|\bPROJECT(?: TITLE| NAME)?\b(?! CODE)"
    ),
    "location": re.compile(r"\bD\S?A DIEM(?: XAY DUNG| CONG TRINH)?\b|\bDIA CHI\b|\bADDRESS\b|\bLOCATION\b"),
    "sheet_no": re.compile(
        r"\bSO BAN VE\b|\bBAN VE SO\b|\bKY HIEU(?: BAN VE)?(?: SO)?\b(?! GHI CHU)|\bSO HIEU\b"
        r"|\bDRAWING (?:NO|NUMBER)\b|\bSHEE\w (?:NO|NUMBER)\b|\bMA BAN VE\b"
    ),
    "date": re.compile(r"\bNGAY(?: PHAT HANH| NOP| LAP| HOAN THANH)?\b|\bDATE\b|\bHO?A?N THANH\b|\bISSUE DATE\b"),
}
# Ô nhãn khác của khung tên: không bao giờ là giá trị, và chắn đường từ nhãn tới giá trị ở ô dưới.
_CAPTION: Final = re.compile(
    r"GIAM DOC|CHU TRI|THIET KE|KIEM TRA|NGUOI VE|\bDRAWN\b|CHECKED|DESIGN|DIRECTOR|MANAGER|\bTEN BAN VE\b"
    r"|DRAWING TITLE|HANG MUC|GIAI DOAN|\bPHASE\b|TI LE|TY LE|\bSCALE\b|KHO GIAY|\bFORMAT\b|\bBO MON\b"
    r"|DISCIPLINE|CODE\b|\bMA \w+"
    r"|DON VI|TU VAN|HOAN THANH|PHAT HANH|\bISSUE|DESCRIPTION|DIEN GIAI|REVISION|SUA DOI|MODIFY|YEU CAU|REQUEST"
    r"|TRINH DUYET"
    r"|\bTHAM KHAO\b|\bTHI CONG\b|MUC DICH|\bSHEET SET\b|BO HO SO|\bKHOI NHA\b|\bBUILDING\b"
)
_REVISION: Final = re.compile(r"SUA DOI|REVISION|MODIFY|HIEU CHINH|DESCRIPTION|DIEN GIAI")
# Nhãn ngắn của khung tên bị tách mỗi từ một dòng ("ĐƠN / VỊ / THIẾT / KẾ"): từng dòng không phải giá trị.
_CAPTION_WORD: Final = re.compile(r"DON|VI|THIET|KE|CHU|TRI|GIAM|DOC|KIEM|TRA|NGUOI|VE|DAU|TU")
_SEPARATORS: Final = " :-_./|"
_DATE: Final = re.compile(
    r"(?<![\d/.-])\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*(?:20\d{2}|19\d{2}|\d{2})(?![\d/])"  # 31/10/2020, 10.09.20
    r"|(?<![\d/.-])\d{1,2}\s*[/.-]\s*(?:20\d{2}|19\d{2})(?![\d/])"  # 10/2009 (năm 4 chữ số: "1/50" là tỉ lệ)
    r"|(?<!\d)\d{1,2}\s*THANG\s*\d{1,2}\s*(?:NAM\s*)?\d{2,4}"
)
_SHEET: Final = re.compile(r"[A-Z]{1,4}\s?[-_.]?\s?\d{1,3}[A-Z]?(?:\s?[-_.]\s?\d{1,2})?")
_DIGITS: Final = re.compile(r"\d")


@dataclass(frozen=True)
class TitleBlockField:
    """Một trường đọc được: chữ gốc (NFC), độ tin trong [0, 1], và vị trí giá trị so với nhãn."""

    value: str
    confidence: float
    source: Source


@dataclass(frozen=True)
class TitleBlock:
    """Khung tên của một trang; trường không đọc chắc là `None`."""

    owner: TitleBlockField | None = None
    project: TitleBlockField | None = None
    location: TitleBlockField | None = None
    sheet_no: TitleBlockField | None = None
    date: TitleBlockField | None = None


@dataclass(frozen=True)
class _Item:
    """Một dòng OCR: chữ gốc, chữ đã gấp (cho so nhãn), hộp, chiều cao."""

    text: str
    norm: str
    box: Box
    h: float


@dataclass(frozen=True)
class _Cand:
    """Giá trị ứng viên của một trường: chữ, điểm, nguồn."""

    text: str
    score: float
    source: Source


def _items(texts: Sequence[OcrText]) -> list[_Item]:
    """Dòng OCR dùng được (chữ khác rỗng, hộp hữu hạn dương), cắt độ dài, tối đa `MAX_ITEMS`; không ném lỗi."""
    out: list[_Item] = []
    for t in texts:
        text, raw = getattr(t, "text", None), getattr(t, "box", None)
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in raw)  # type: ignore[union-attr]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
            continue
        if y1 - y0 > MAX_ASPECT * (x1 - x0):
            continue  # chữ xoay: hình học "bên phải/bên dưới" không còn đúng
        text = text.strip()[:MAX_TEXT_LEN]
        out.append(_Item(text, re.sub(r"[_/|]", " ", fold(text)), (x0, y0, x1, y1), y1 - y0))
        if len(out) >= MAX_ITEMS:
            break
    return out


def _clean(text: str) -> str:
    """Chữ giá trị: giải mã VNI/TCVN3 -> Unicode, một khoảng trắng, bỏ dấu phân cách đầu/cuối."""
    return re.sub(r"\s+", " ", decode_legacy(text)).strip(_SEPARATORS)


def _normalise(field: Field, text: str) -> str:
    """Giá trị trả về: ngày chỉ lấy phần ngày ("tháng 03/2015" -> "03/2015", "30.03. 2001" -> "30.03.2001").

    Trường khác giữ nguyên."""
    if field == "date":
        m = _DATE.search(text)
        if m is not None:
            return re.sub(r"\s+", "", m.group(0))
    return text


def _valid(field: Field, text: str) -> bool:
    """Giá trị đúng kiểu trường: ngày/mã số hiệu đúng dạng; tên thì đủ chữ cái, không phải nhãn hay số thuần."""
    norm = fold(text)
    if field == "date":
        return _DATE.search(norm) is not None
    if field == "sheet_no":
        return _SHEET.fullmatch(norm) is not None
    if _CAPTION.search(norm) or any(p.search(norm) for p in _LABELS.values()) or _CAPTION_WORD.fullmatch(norm):
        return False
    if text.rstrip().endswith(":"):
        return (
            False  # nhãn viết sai chính tả ("dia dlbm xay dyng:") không khớp mẫu nhưng luôn kết thúc bằng dấu hai chấm
        )
    letters = sum(c.isalpha() for c in norm)
    return (
        letters >= MIN_LETTERS
        and letters >= MIN_LETTER_SHARE * len(norm.replace(" ", ""))
        and _DATE.fullmatch(norm) is None
    )


def _is_label(item: _Item) -> bool:
    """Dòng là một ô nhãn của khung tên (nhãn trường hoặc nhãn khác), tức không thể là giá trị."""
    return len(item.norm) <= MAX_LABEL_LEN and (
        item.text.endswith(":")  # nhãn viết sai chính tả vẫn kết thúc bằng dấu hai chấm
        or _CAPTION.search(item.norm) is not None
        or any(p.search(item.norm) for p in _LABELS.values())
    )


def _inline(item: _Item, match: re.Match[str], field: Field) -> str | None:
    """Giá trị cùng dòng sau nhãn ("Ngày phát hành: 10/2009"): phần chữ sau chỗ nhãn kết thúc, nếu hợp lệ."""
    if match.start() != 0 or len(item.norm) != len(
        item.text
    ):  # nhãn đứng đầu dòng; gấp giữ độ dài thì cắt được chữ gốc
        return None
    raw = item.text[match.end() :]
    if field in _TEXT_FIELDS and not raw.lstrip(" ").startswith((":", "-", "_")):
        return None  # "ĐỊA ĐIỂM XÂY DỰNG" đọc sai thành "dia diem xay dyng": phần sau nhãn chưa chắc là giá trị
    rest = _clean(raw)
    return rest if rest and _valid(field, rest) else None


def _continues(label: _Item, other: _Item) -> bool:
    """`other` là dòng thứ hai của cùng ô nhãn: sát ngay dưới nhãn (không hở quá nửa dòng) và cùng cột."""
    lb, ob = label.box, other.box
    return -0.5 * label.h <= ob[1] - lb[3] <= 0.5 * label.h and ob[0] < lb[2] and ob[2] > lb[0]


def _blocked(label: _Item, cand: _Item, items: Sequence[_Item], *, bilingual: bool = False) -> bool:
    """Có ô nhãn khác nằm giữa nhãn và ứng viên (cùng cột ở ô dưới, hoặc cùng dòng ở bên phải).

    `bilingual`: `label` là ô nhãn thật (không phải dòng giá trị) nên dòng dịch ngay dưới nó không chắn."""
    lb, cb = label.box, cand.box
    for other in items:
        if other is label or other is cand or not _is_label(other):
            continue
        ob = other.box
        if bilingual and _continues(label, other):
            continue  # dòng dịch tiếng Anh ngay dưới nhãn ("Ngày phát hành" / "1st issue"): cùng một ô nhãn
        column = ob[0] < max(lb[2], cb[2]) and ob[2] > min(lb[0], cb[0])  # cùng cột với nhãn hay với giá trị (thụt vào)
        if column and ob[1] >= lb[3] - 0.3 * label.h and ob[3] <= cb[1] + 0.3 * label.h:
            return True  # ô khác chen vào giữa theo chiều dọc
        same = abs((ob[1] + ob[3]) / 2 - (lb[1] + lb[3]) / 2) <= SAME_LINE * label.h
        if same and lb[2] - 0.2 * label.h <= ob[0] < cb[0]:
            return True  # ô khác chen vào giữa theo chiều ngang
    return False


def _right_cands(label: _Item, items: Sequence[_Item]) -> list[_Item]:
    """Dòng cùng đường cơ sở với nhãn, ở bên phải trong `RIGHT_MAX_GAP`, gần nhất trước."""
    lb = label.box
    near = [
        i
        for i in items
        if i is not label
        and abs((i.box[1] + i.box[3]) / 2 - (lb[1] + lb[3]) / 2) <= SAME_LINE * label.h
        and lb[2] - 0.3 * label.h <= i.box[0] <= lb[2] + RIGHT_MAX_GAP * label.h
    ]
    return sorted(near, key=lambda i: i.box[0])


def _below_cands(label: _Item, items: Sequence[_Item]) -> list[_Item]:
    """Dòng dưới nhãn (đỉnh cách đáy nhãn <= `BELOW_MAX_GAP`, cùng cột cho phép thụt phải), gần nhất trước."""
    lb = label.box
    near = [
        i
        for i in items
        if i is not label
        and lb[3] - 0.3 * label.h <= i.box[1] <= lb[3] + BELOW_MAX_GAP * label.h
        and i.box[0] <= lb[2] + BELOW_X_SLACK * label.h
        and i.box[2] >= lb[0] - label.h
    ]
    return sorted(near, key=lambda i: (i.box[1], i.box[0]))


def _extend(first: _Item, items: Sequence[_Item], field: Field) -> tuple[str, int]:
    """Giá trị nhiều dòng của tên (công trình, địa điểm): nối các dòng ngay dưới, cùng cột, tối đa `MAX_LINES`."""
    parts, cur = [first.text], first
    while field in _TEXT_FIELDS and len(parts) < MAX_LINES:
        nxt = [
            i
            for i in items
            if i is not cur
            and 0 <= i.box[1] - cur.box[3] <= MULTILINE_GAP * cur.h
            and abs(i.box[0] - first.box[0]) <= 3 * cur.h
            and not _is_label(i)
            and _valid(field, i.text)
            and not _blocked(cur, i, items)
        ]
        if not nxt:
            break
        cur = min(nxt, key=lambda i: i.box[1])
        parts.append(cur.text)
    return " ".join(parts), len(parts) - 1


def _candidates(label: _Item, match: re.Match[str], field: Field, items: Sequence[_Item]) -> list[_Cand]:
    """Giá trị ứng viên cho một ô nhãn: cùng dòng, rồi bên phải, rồi bên dưới (mỗi nơi tối đa một, gần nhất hợp lệ)."""
    out: list[_Cand] = []
    inline = _inline(label, match, field)
    if inline is not None:
        out.append(_Cand(_normalise(field, inline), CONF_SAME_LINE, "same_line"))
    for source, found, base in (
        ("right", _right_cands(label, items), CONF_RIGHT),
        ("below", _below_cands(label, items), CONF_BELOW),
    ):
        for item in found:
            if _valid(field, item.text) and not _is_label(item) and not _blocked(label, item, items, bilingual=True):
                text, extra = _extend(item, items, field)
                far = _gap(label, item) > FAR_GAP * label.h
                score = base - (CONF_FAR_PENALTY if far else 0.0) - CONF_MULTILINE_PENALTY * extra
                out.append(_Cand(_normalise(field, text), score, source))  # type: ignore[arg-type]
                break
    return out


def _gap(label: _Item, cand: _Item) -> float:
    """Khoảng hở (px) từ nhãn tới ứng viên theo trục chính (ngang nếu cùng dòng, không thì dọc)."""
    dx = max(0.0, cand.box[0] - label.box[2])
    dy = max(0.0, cand.box[1] - label.box[3])
    return dx if dy == 0 else dy


def _revision_cell(label: _Item, items: Sequence[_Item]) -> bool:
    """Nhãn ngày thuộc bảng sửa đổi: chính nó nói SỬA ĐỔI/REVISION, hoặc có chữ đó ngay phía trên cùng cột."""
    if _REVISION.search(label.norm):
        return True
    lb = label.box
    return any(
        _REVISION.search(i.norm) is not None
        and i.box[0] < lb[2] + label.h
        and i.box[2] > lb[0] - label.h
        and 0 <= lb[1] - i.box[3] <= REVISION_REACH * label.h
        for i in items
        if i is not label
    )


def _same(a: str, b: str) -> bool:
    """Hai giá trị là một (sai khác chỉ do OCR đọc nhiễu): chữ gấp, bỏ ký tự lạ, giống nhau >= `SAME_RATIO`."""
    x, y = (re.sub(r"[^A-Z0-9]", "", fold(t)) for t in (a, b))
    return x == y or SequenceMatcher(None, x, y).ratio() >= SAME_RATIO


def _field(field: Field, items: Sequence[_Item]) -> TitleBlockField | None:
    """Trường tốt nhất của một loại: gom ứng viên từ mọi ô nhãn, chọn điểm cao nhất; hai giá trị khác nhau mà điểm
    sát nhau (< `TIE_EPS`), hoặc điểm dưới `MIN_CONFIDENCE`: không chắc -> None."""
    pattern = _LABELS[field]
    cands: list[_Cand] = []
    for item in items:
        m = pattern.search(item.norm) if len(item.norm) <= MAX_LABEL_LEN else None
        if m is None:
            continue
        if field == "date" and _revision_cell(item, items):
            continue
        if field in ("project", "location") and _both_project_and_location(item):
            continue
        cands.extend(_candidates(item, m, field, items))
    if not cands:
        return None
    cands.sort(key=lambda c: -c.score)
    best = cands[0]
    rival = next((c for c in cands[1:] if not _same(c.text, best.text)), None)
    if best.score < MIN_CONFIDENCE or (rival is not None and best.score - rival.score < TIE_EPS):
        return None
    return TitleBlockField(_clean(best.text), round(best.score, 2), best.source)


def _both_project_and_location(item: _Item) -> bool:
    """Nhãn ghép "CÔNG TRÌNH - ĐỊA ĐIỂM": giá trị là cả hai, không tách được -> bỏ cho cả hai trường."""
    return _LABELS["project"].search(item.norm) is not None and _LABELS["location"].search(item.norm) is not None


def read_title_block(texts: Sequence[OcrText]) -> TitleBlock:
    """Đọc khung tên từ chữ OCR cả trang (một trang một khung tên).

    Không ném lỗi với đầu vào bất kỳ (hộp hỏng, chữ không phải chuỗi, rất nhiều dòng); không chữ -> mọi trường `None`.
    """
    items = _items(texts)
    if not items:
        return TitleBlock()
    return TitleBlock(
        owner=_field("owner", items),
        project=_field("project", items),
        location=_field("location", items),
        sheet_no=_field("sheet_no", items),
        date=_field("date", items),
    )
