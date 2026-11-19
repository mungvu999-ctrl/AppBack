"""Bộ bản vẽ thuần: tên hình, loại hình, nhãn tầng và chọn tên hình từ chữ OCR (HS-01).

Chỉ thư viện chuẩn; chưa nối vào API (xem `changes/HS-01.md`).
"""

from packages.domain.drawing_set.legacy_text import decode_legacy, detect_encoding
from packages.domain.drawing_set.titles import (
    KindGuess,
    OcrText,
    PickedTitle,
    TitleInfo,
    fold,
    parse_title,
    pick_title,
    view_kind,
)

__all__ = [
    "KindGuess",
    "OcrText",
    "PickedTitle",
    "TitleInfo",
    "decode_legacy",
    "detect_encoding",
    "fold",
    "parse_title",
    "pick_title",
    "view_kind",
]
