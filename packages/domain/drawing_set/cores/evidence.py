"""Bằng chứng chữ quanh ô thang, phủ quyết tên phòng và chữ chỉ ô trống (K-32e, từ `kernel/cores.py`).

Chữ được gấp bằng `titles.fold` (bỏ dấu, hoa, giải mã VNI/TCVN3); nhân nghiên cứu chỉ bỏ dấu, nên chữ phông
cũ sau OCR được đọc thêm. Bản gốc xoá chữ khỏi ảnh trước khi dò thang (`erase_texts`): bước ảnh đó thuộc bộ dò,
không có ở đây.
"""

import re
from collections.abc import Sequence
from typing import Final

from packages.domain.drawing_set.cores.types import Box, PlanText
from packages.domain.drawing_set.titles import fold

EVID_PAD_MM: Final = 600.0  # lề tìm bằng chứng quanh giếng thang
STAIR_WORDS: Final = re.compile(r"LEN|XUONG|THANG|\bUP\b|\bDN\b|\bDOWN\b|STAIR")
UNDER_STAIR: Final = re.compile(r"^\W*(WC|KHO|VS)\b")  # phòng hay nằm dưới thang: không phủ quyết
VOID_WORDS: Final = re.compile(r"THONG\s*TANG|GIENG\s*TROI|\bVOID\b|\bO\s*TRONG\b|SAN\s*TROI")
_STEP_MAX = 40  # số bậc ghi trên thang nằm trong 1..40


def is_wordy(text: str) -> bool:
    """Chữ đáng kể: ≥ 3 ký tự không tính khoảng trắng và không phải số trần (số bậc nằm trên bậc thang)."""
    s = re.sub(r"\s", "", text or "")
    return len(s) >= 3 and not s.isdigit()


def evidence_near(texts: Sequence[PlanText], box: Box) -> frozenset[str]:
    """Bằng chứng thang từ chữ có tâm trong hộp + `EVID_PAD_MM`.

    "stair_word" = LEN/XUONG/THANG/UP/DN/DOWN/STAIR; "tread_numbers" = ≥ 3 số bậc 1..40 cách đều 1 hoặc 2 (bản vẽ
    hay đánh số cách một bậc). Nhân có thêm "down" nhưng nó bị bỏ ngay khi tính độ chắc nên không giữ ở đây.
    """
    x0, y0, x1, y1 = box[0] - EVID_PAD_MM, box[1] - EVID_PAD_MM, box[2] + EVID_PAD_MM, box[3] + EVID_PAD_MM
    out: set[str] = set()
    nums: set[int] = set()
    for t in texts:
        cx, cy = t.centre_mm
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        s = fold(t.text)
        if STAIR_WORDS.search(s):
            out.add("stair_word")
        if s.isascii() and s.isdigit() and 1 <= int(s) <= _STEP_MAX:
            nums.add(int(s))
    if any({n, n + d, n + 2 * d} <= nums for n in nums for d in (1, 2)):
        out.add("tread_numbers")
    return frozenset(out)


def has_room_label(texts: Sequence[PlanText], box: Box, letters: int = 2) -> bool:
    """Có chữ tên phòng (≥ `letters` chữ cái liền, không phải chữ thang, không phải WC/KHO/VS) có tâm trong hộp."""
    pat = re.compile(rf"[A-Z]{{{letters}}}")
    for t in texts:
        s = fold(t.text)
        if not (is_wordy(t.text) and pat.search(s)):
            continue
        if STAIR_WORDS.search(s) or UNDER_STAIR.search(s):
            continue
        cx, cy = t.centre_mm
        if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
            return True
    return False


def void_texts(texts: Sequence[PlanText]) -> list[PlanText]:
    """Chữ nói về ô trống/giếng trời/thông tầng (đã gấp dấu)."""
    return [t for t in texts if VOID_WORDS.search(fold(t.text))]
