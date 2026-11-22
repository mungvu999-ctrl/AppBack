"""Giải mã VNI/TCVN3: chép `selfcheck()` của `kernel/legacy_text.py` và các ca trong `legacy_text_report.md`."""

# ruff: noqa: RUF001  (bảng mã cũ chứa ký tự giống dấu câu, đó là dữ liệu)

from typing import Literal

import pytest

from packages.domain.drawing_set.legacy_text import VNI_PAIRS, VNI_SINGLE, decode_legacy, detect_encoding

_PAIRS = [
    ("MAËT BAÈNG LAÀU 1", "MẶT BẰNG LẦU 1"),
    ("MAËT BAÈNG TREÄT", "MẶT BẰNG TRỆT"),
    ("MAËT BAÈNG SAÂN THÖÔÏNG", "MẶT BẰNG SÂN THƯỢNG"),
    ("MAËT ÑÖÙNG CHÍNH", "MẶT ĐỨNG CHÍNH"),
    ("MAËT CAÉT A-A", "MẶT CẮT A-A"),
    ("TÆ LEÄ : 1/75", "TỈ LỆ : 1/75"),
    ("MAËT BAÈNG MAÙI", "MẶT BẰNG MÁI"),
    ("phoøng nguû", "phòng ngủ"),
    ("baøn beáp, vaän", "bàn bếp, vận"),
    ("mÆt b»ng tÇng 1", "mặt bằng tầng 1"),
    ("mÆt ®øng", "mặt đứng"),
    ("mÆt c¾t a-a", "mặt cắt a-a"),
    ("mÆt b»ng m¸i", "mặt bằng mái"),
    ("MÆT B»NG VËT DôNG", "MẶT BẰNG VẬT DỤNG"),
    ("MÆT B»NG m¸i", "MẶT BẰNG mái"),
    ("mÆt b»ng tÇng löng", "mặt bằng tầng lửng"),
    ("mÆt b»ng bè trÝ \ncöa tÇng 1", "mặt bằng bố trí \ncửa tầng 1"),
    ("mÆt c¾t trôc 5-1 - tØ lÖ: 1/75", "mặt cắt trục 5-1 - tỉ lệ: 1/75"),
    ("ph¹m vi s©n th­îng", "phạm vi sân thượng"),
    ("MAËT BAÈNG VẬT DỤNG LAÀU 1", "MẶT BẰNG VẬT DỤNG LẦU 1"),
    ("MẶT BẰNG KIẾN TRÚC", "MẶT BẰNG KIẾN TRÚC"),
    ("MẶT BẰNG ĐỊNH VỊ CÔNG TRÌNH\nTỈ LỆ: 1/200", "MẶT BẰNG ĐỊNH VỊ CÔNG TRÌNH\nTỈ LỆ: 1/200"),
    ("mặt đứng", "mặt đứng"),
    ("+3.600", "+3.600"),
    ("TL:1/50", "TL:1/50"),
    ("A", "A"),
    ("12", "12"),
    ("", ""),
    ("Ground floor plan", "Ground floor plan"),
    ("Elevation drawing", "Elevation drawing"),
    ("%%p0.15 %%c12", "%%p0.15 %%c12"),
    ("phßng ngñ 2", "phòng ngủ 2"),
    ("PHßNG ngñ 17m2", "PHÒNG ngủ 17m2"),
    ("Tên công trình", "Tên công trình"),
    ("PHÒNG KHÁCH", "PHÒNG KHÁCH"),
    ("GARAGE ÔTÔ", "GARAGE ÔTÔ"),
    ("Ghi Chó - Note", "Ghi Chó - Note"),
    ("PHÓ GIÁM ĐỐC", "PHÓ GIÁM ĐỐC"),
]


@pytest.mark.parametrize(("source", "expected"), _PAIRS)
def test_decode_legacy_kernel_cases(source: str, expected: str) -> None:
    """Các cặp vào-ra của selfcheck kernel: VNI, TCVN3, Unicode, ASCII, chuỗi trộn."""
    assert decode_legacy(source) == expected


@pytest.mark.parametrize(
    ("source", "prefer", "expected"),
    [
        ("Ghi Chó - Note", "tcvn3", "Ghi Chú - Note"),
        ("sè 1", "tcvn3", "số 1"),
        ("PHÓ GIÁM ĐỐC", "tcvn3", "PHÓ GIÁM ĐỐC"),
        ("tñ", "tcvn3", "tủ"),
        ("ÑK 73", "tcvn3", "ĐK 73"),
        ("VÏ", "vni", "VẼ"),
        ("+3.600", "tcvn3", "+3.600"),
        ("ÆåÅÆÊÒ", "tcvn3", "ÆåÅÆÊÒ"),
    ],
)
def test_decode_legacy_prefer_vote(source: str, prefer: Literal["vni", "tcvn3"], expected: str) -> None:
    """Một chữ Latin-1 yếu chỉ được giải mã khi tệp đã bỏ phiếu cho bảng mã đó và từ đổi là âm tiết hợp lệ."""
    assert decode_legacy(source, prefer=prefer) == expected


def test_decode_legacy_prefer_rejects_invalid_syllable() -> None:
    """Bảng mã ưa thích mà ra từ không phải âm tiết Việt thì bỏ, giữ đường đoán tự động."""
    assert decode_legacy("Zó", prefer="tcvn3") == "Zó"  # "Zú" không phải âm tiết
    assert decode_legacy("sè 1", prefer="vni") == "sè 1"  # VNI không đổi chữ nào


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MAËT BAÈNG LAÀU 1", "vni"),
        ("mÆt ®øng", "tcvn3"),
        ("MẶT BẰNG", "unicode"),
        ("GARAGE ÔTÔ", "unicode"),
        ("ÆåÅÆÊÒ", "unicode"),
        ("+3.600", "ascii"),
        ("", "ascii"),
        ("phßng ngñ", "tcvn3"),
        ("Chó", "unicode"),
    ],
)
def test_detect_encoding(text: str, expected: str) -> None:
    """Nhận bảng mã; ký hiệu phông và chữ Latin-1 yếu đơn lẻ không bị coi là chữ cũ."""
    assert detect_encoding(text) == expected


def test_detect_encoding_tie_prefers_vni_pair() -> None:
    """Cả hai bảng mã cùng khớp mà có cặp nền+dấu VNI ("AÈ") thì là VNI."""
    assert detect_encoding("ñAÈ ß") == "vni"


def test_detect_encoding_tie_breaks_on_syllables() -> None:
    """Không có cặp VNI: bên nào ra âm tiết hợp lệ hơn thắng ("phßng ngñ" là TCVN3, "ngđ" không phải âm tiết)."""
    assert detect_encoding("phßng ngñ") == "tcvn3"


def test_tables_cover_every_vietnamese_letter() -> None:
    """Mọi chữ nguyên âm + dấu của tiếng Việt với được từ bảng VNI (và 118 kết quả cặp riêng biệt)."""
    letters = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
    reachable = set(VNI_PAIRS.values()) | set(VNI_SINGLE.values()) | set("ìíý")
    assert letters <= reachable
    assert len(set(VNI_PAIRS.values())) >= 118
