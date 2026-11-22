"""Giải mã chữ tiếng Việt kiểu cũ trong bản vẽ CAD (VNI-Windows, TCVN3/ABC) về Unicode NFC.

Chuỗi trong DWG/DXF thường được đọc thành ký tự Latin-1: dòng VNI hiện ra "MAËT BAÈNG", dòng
TCVN3 hiện ra "mÆt b»ng". Trên giấy, phông CAD ánh xạ các byte đó về chữ Việt; ở đây ta làm
việc ấy bằng bảng tra. Gương `F:/runs/dwglab/muc3/kernel/legacy_text.py` (HS-01), giữ nguyên
hành vi, chỉ tách hàm cho đủ ngưỡng độ phức tạp.

Giới hạn (heuristic, không phải mô hình ngôn ngữ):
 * Chỉ dùng cho chữ đã biết là của bản vẽ tiếng Việt: "où", "été" của Pháp sẽ bị đọc thành VNI.
 * TCVN3 (phông ABC) không có mã chữ hoa có dấu: từ có mọi chữ ASCII hoa thì đọc thành hoa.
 * Chuỗi trộn Unicode + VNI ("MAËT BAÈNG VẬT DỤNG LAÀU 1") được giải mã từng đoạn.
"""

# ruff: noqa: RUF001, RUF003  (bảng mã cũ chứa ký tự giống dấu câu, đó là dữ liệu)

import re
import unicodedata
from collections.abc import Callable
from typing import Literal

Encoding = Literal["unicode", "vni", "tcvn3", "ascii"]

# ---------------------------------------------------------------- VNI-Windows
# Chữ = nền + một ký tự dấu. Dấu thanh: huyền, sắc, hỏi, ngã, nặng.
_TONES = ("\u0300", "\u0301", "\u0309", "\u0303", "\u0323")
_VNI_PLAIN = "øùûõï"  # dấu thanh sau nguyên âm thường: aø=à aù=á aû=ả aõ=ã aï=ạ
_VNI_CIRC = "àáåãä"  # sau nguyên âm mũ (â ê ô): aá=ấ ...
_VNI_BREVE = "èéúüë"  # sau nguyên âm trăng (ă): aé=ắ ...


def _nfc(text: str) -> str:
    """Chuẩn hoá NFC (chuỗi lưu trong hệ thống luôn là NFC, K20)."""
    return unicodedata.normalize("NFC", text)


def _build_vni() -> dict[str, str]:
    """Bảng cặp VNI (nền + dấu) -> chữ Unicode, cả hoa lẫn thường."""
    table: dict[str, str] = {}
    for vowel in "aeouy":
        for mark, tone in zip(_VNI_PLAIN, _TONES, strict=True):
            table[vowel + mark] = _nfc(vowel + tone)
    for vowel, circ in (("a", "â"), ("e", "ê"), ("o", "ô")):
        table[vowel + "â"] = circ
        for mark, tone in zip(_VNI_CIRC, _TONES, strict=True):
            table[vowel + mark] = _nfc(circ + tone)
    table["aê"] = "ă"
    for mark, tone in zip(_VNI_BREVE, _TONES, strict=True):
        table["a" + mark] = _nfc("ă" + tone)
    for vni_base, uni in (("ô", "ơ"), ("ö", "ư")):
        for mark, tone in zip(_VNI_PLAIN, _TONES, strict=True):
            table[vni_base + mark] = _nfc(uni + tone)
    upper = {key.upper(): value.upper() for key, value in table.items()}
    return {**table, **upper}


VNI_PAIRS = _build_vni()
VNI_SINGLE = {"ñ": "đ", "ö": "ư", "ô": "ơ", "æ": "ỉ", "ó": "ĩ", "ò": "ị", "î": "ỵ"}
VNI_SINGLE.update({key.upper(): value.upper() for key, value in list(VNI_SINGLE.items())})
_VNI_EVIDENCE = frozenset("ñÑöÖæÆîÎ")  # ô ó ò cũng là chữ Unicode thường: không tính bằng chứng

# ---------------------------------------------------------------- TCVN3 (ABC), mã chữ thường
_T3 = (
    "µà ¸á ¶ả ·ã ¹ạ "
    "¨ă »ằ ¾ắ ¼ẳ ½ẵ Æặ "
    "©â Çầ Êấ Èẩ Éẫ Ëậ "
    "®đ "
    "Ìè Ðé Îẻ Ïẽ Ñẹ "
    "ªê Òề Õế Óể Ôễ Öệ "
    "×ì Ýí Øỉ Üĩ Þị "
    "ßò ãó áỏ âõ äọ "
    "«ô åồ èố æổ çỗ éộ "
    "¬ơ êờ íớ ëở ìỡ îợ "
    "ïù óú ñủ òũ ôụ "
    "\xadư õừ øứ öử ÷ữ ùự "  # \xad (dấu gạch nối mềm) là mã TCVN3 của "ư"
    "úỳ ýý ûỷ üỹ þỵ"
)
TCVN3 = {word[0]: word[1] for word in _T3.split(" ")}
TCVN3_UPPER_ONLY = {"¡": "Ă", "¢": "Â", "£": "Ê", "¤": "Ô", "¥": "Ơ", "¦": "Ư", "§": "Đ"}
# Bằng chứng mạnh = mã TCVN3 không phải chữ Latin-1 của Unicode Việt: "ß ñ ö ø Ç Æ ¸ ®" chỉ có thể
# là chữ cũ, còn "ê ô ì Ó" có thể là cả hai.
_VN_LATIN1 = frozenset("àáâãèéêìíòóôõùúýÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝ")
_T3_STRONG = frozenset(c for c in TCVN3 if c not in _VN_LATIN1) | frozenset(TCVN3_UPPER_ONLY)
_ASCII = re.compile(r"^[\x00-\x7f]*$")
_UNI_VN = re.compile("[ĂăĐđĨĩŨũƠơƯưẠ-ỹ]")
_SYLLABLE = re.compile(
    "^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdđghklmnpqrstvx])?[aăâeêioôơuưy]{1,3}(ch|ng|nh|[cmnpt])?$"
)
_WORD = re.compile(r"[^\W\d_]+")
_CONSONANTS_ONLY = re.compile("[b-df-hj-np-tv-xzđ]+")


def _is_letter(char: str) -> bool:
    """Ký tự là chữ cái ASCII hoặc mã TCVN3 (để cắt từ khi giải mã TCVN3)."""
    return (char.isascii() and char.isalpha()) or char in TCVN3 or char in TCVN3_UPPER_ONLY


def _ascii_upper(text: str) -> bool | None:
    """True/False khi mọi chữ ASCII của `text` là hoa / không phải; None khi không có chữ ASCII nào."""
    letters = [c for c in text if c.isascii() and c.isalpha()]
    if not letters:
        return None
    return all(c.isupper() for c in letters)


def _decode_vni(text: str) -> tuple[str, int]:
    """Giải mã VNI -> (chuỗi Unicode, điểm bằng chứng)."""
    out: list[str] = []
    hits = 0
    i = 0
    while i < len(text):
        pair = VNI_PAIRS.get(text[i : i + 2])
        if pair:
            out.append(pair)
            hits += 2
            i += 2
            continue
        char = text[i]
        i += 1
        out.append(VNI_SINGLE.get(char, char))
        if char in _VNI_EVIDENCE:
            hits += 2
    return "".join(out), hits


def _mixed_case_inside(word: str, k: int) -> bool:
    """Chữ Latin-1 hoa nằm giữa từ có chữ thường (không phải chữ châu Âu) -> thêm bằng chứng TCVN3."""
    before = k > 0 and word[k - 1].islower()
    after = k + 1 < len(word) and word[k + 1].islower()
    return word[k].isupper() and (before or after)


def _decode_tcvn3_word(word: str, upper: bool) -> tuple[str, int]:
    """Giải mã một từ TCVN3; `upper` quyết định chữ có dấu thành hoa hay thường."""
    out: list[str] = []
    hits = 0
    for k, char in enumerate(word):
        if char in TCVN3_UPPER_ONLY:
            out.append(TCVN3_UPPER_ONLY[char])
            hits += 2
        elif char in TCVN3:
            plain = TCVN3[char]
            out.append(plain.upper() if upper else plain)
            hits += 2 if char in _T3_STRONG else 1
            hits += 2 if _mixed_case_inside(word, k) else 0
        else:
            out.append(char)
    return "".join(out), hits


def _decode_tcvn3(text: str) -> tuple[str, int]:
    """Giải mã TCVN3 -> (chuỗi Unicode, điểm bằng chứng); hoa/thường theo từng từ (từ không chữ ASCII theo cả chuỗi)."""
    whole_upper = _ascii_upper(text)
    out: list[str] = []
    hits = 0
    i = 0
    while i < len(text):
        if not _is_letter(text[i]):
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < len(text) and _is_letter(text[j]):
            j += 1
        word_upper = _ascii_upper(text[i:j])
        decoded, word_hits = _decode_tcvn3_word(text[i:j], bool(whole_upper if word_upper is None else word_upper))
        out.append(decoded)
        hits += word_hits
        i = j
    return "".join(out), hits


_DECODERS: dict[str, Callable[[str], tuple[str, int]]] = {"vni": _decode_vni, "tcvn3": _decode_tcvn3}


def _mixed(text: str, char: str) -> bool:
    """`char` (lần đầu xuất hiện) đứng cạnh chữ thường."""
    i = text.index(char)
    return bool((i and text[i - 1].islower()) or (i + 1 < len(text) and text[i + 1].islower()))


def _strip_tones(word: str) -> str:
    """Bỏ dấu thanh, giữ dấu mũ/trăng/râu (để so với mẫu âm tiết)."""
    decomposed = unicodedata.normalize("NFD", word.lower())
    return _nfc("".join(c for c in decomposed if c not in _TONES))


def _valid_syllable(word: str) -> bool:
    """Từ có dạng một âm tiết tiếng Việt (sau khi bỏ dấu thanh)."""
    return bool(_SYLLABLE.match(_strip_tones(word)))


def _junk(text: str) -> bool:
    """Chuỗi phông ký hiệu ('ÆåÅÆÊÒ'): không có chữ ASCII và >= 3 chữ cao -> không phải chữ, để nguyên."""
    if any(c.isascii() and c.isalpha() for c in text):
        return False
    return sum(c in TCVN3 or c in VNI_SINGLE for c in text) >= 3


def _invalid(decoded: str) -> int:
    """Số từ có chữ ngoài ASCII mà không phải âm tiết Việt (từ viết tắt ĐT, MĐ không tính); chỉ dùng để xếp hạng."""
    words = [w for w in _WORD.findall(decoded) if not w.isascii()]
    return sum(not _valid_syllable(w) for w in words if not _CONSONANTS_ONLY.fullmatch(_strip_tones(w)))


def _evidence(text: str) -> tuple[int, int]:
    """Điểm bằng chứng (VNI, TCVN3); TCVN3 cần mã mạnh hoặc chữ hoa Latin-1 giữa từ thường."""
    vni, tcvn3 = _decode_vni(text)[1], _decode_tcvn3(text)[1]
    if not any(c in _T3_STRONG or (c.isupper() and c in TCVN3 and _mixed(text, c)) for c in text):
        tcvn3 = 0
    if _UNI_VN.search(text):  # đã có Unicode Việt thật: TCVN3 chỉ khi bằng chứng mạnh
        tcvn3 = sum(2 for c in text if c in _T3_STRONG)
    return vni, tcvn3


def _break_tie(text: str, vni: int, tcvn3: int) -> Encoding:
    """Cả hai đều khớp ("phßng ngñ"): cặp nền+dấu là của VNI; không thì bên nào viết ra âm tiết hợp lệ hơn thắng."""
    if any(text[i : i + 2] in VNI_PAIRS for i in range(len(text) - 1)):
        return "vni"
    invalid_vni = _invalid(_decode_vni(text)[0])
    invalid_tcvn3 = _invalid(_decode_tcvn3(text)[0])
    return "vni" if (invalid_vni, -vni) <= (invalid_tcvn3, -tcvn3) else "tcvn3"


def detect_encoding(text: str) -> Encoding:
    """Đoán bảng mã của `text`: "ascii", "unicode" (kể cả chuỗi ký hiệu), "vni" hoặc "tcvn3"."""
    if _ASCII.match(text):
        return "ascii"
    if _junk(text):
        return "unicode"
    vni, tcvn3 = _evidence(text)
    if max(vni, tcvn3) < 2:  # một chữ Latin-1 yếu không chứng minh gì
        return "unicode"
    if vni >= 2 and tcvn3 >= 2:
        return _break_tie(text, vni, tcvn3)
    return "vni" if vni >= tcvn3 else "tcvn3"


def _prefer_decoding(text: str, enc: Encoding, prefer: str) -> str | None:
    """Giải mã theo bảng mã ưa thích của cả tệp; None khi có chữ nào đổi mà không thành âm tiết Việt."""
    if enc == "ascii" or _UNI_VN.search(text) or _junk(text):
        return None
    decoded = _DECODERS[prefer](text)[0]
    changed = [w for w in _WORD.findall(decoded) if w not in text and any(ord(c) > 127 for c in w)]
    if decoded != text and changed and all(_valid_syllable(w) for w in changed) and (enc == "unicode" or enc != prefer):
        return decoded
    return None


def decode_legacy(text: str, prefer: Literal["vni", "tcvn3"] | None = None) -> str:
    """Chữ VNI/TCVN3 -> Unicode NFC; chuỗi không phải chữ cũ giữ nguyên (chỉ chuẩn hoá NFC).

    `prefer` = bảng mã chiếm đa số của cả tệp: một chữ Latin-1 yếu đơn lẻ ("Chó" = Chú, "sè" = số)
    chỉ được giải mã khi có phiếu này và mọi từ bị đổi đều là âm tiết Việt hợp lệ.
    """
    enc = detect_encoding(text)
    if prefer:
        preferred = _prefer_decoding(text, enc, prefer)
        if preferred is not None:
            return _nfc(preferred)
    if enc in _DECODERS:
        text = _DECODERS[enc](text)[0]
    return _nfc(text)
