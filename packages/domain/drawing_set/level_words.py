"""Từ khoá đi kèm một cốt trên mặt cắt/mặt đứng → vai của cốt đó (NHA-01, thiết kế §2.D mục 3).

Vai giúp phân biệt cốt sàn với cốt diềm, đỉnh mái, parapet khi dựng `RoofSpec`. "ROOF", "PLATE", "PARAPET" trần
là tên cấu kiện chứ không phải cốt, nên phải có "T.O."/"T/O"/"TOP OF" đứng trước. Chưa nối vào luồng nào; nơi dùng
đầu tiên là NHA-05b.
"""

import re
from typing import Final, Literal

from packages.domain.drawing_set.titles import fold

type LevelRole = Literal["floor", "plate", "roof_top", "ridge", "parapet", "roof_slab", "gutter"]

_TOP_OF: Final = r"(?:\bT[./] ?O\.? ?|\bTOP OF )"
# Thứ tự là thứ tự ưu tiên: "CỐT SÀN MÁI" là sàn mái chứ không phải sàn tầng.
_ROLES: Final[tuple[tuple[LevelRole, re.Pattern[str]], ...]] = (
    ("parapet", re.compile(_TOP_OF + r"PARAPET\b")),
    ("ridge", re.compile(r"\bRIDGE\b|\bDINH MAI\b")),
    ("roof_slab", re.compile(r"\bSAN MAI\b")),
    ("gutter", re.compile(r"\bSE ?NO\b")),
    ("roof_top", re.compile(_TOP_OF + r"ROOF\b")),
    ("plate", re.compile(_TOP_OF + r"PLATE\b")),
    ("floor", re.compile(r"\bF\.?F\.?L\b|\bFIN(?:ISH(?:ED)?)?\.? ?FL(?:OO)?R\b|\bCOT SAN\b")),
)


def level_role(text: str) -> LevelRole | None:
    """Vai của cốt theo từ khoá trong chữ (đã bỏ dấu, hoa hết); `None` khi không có từ khoá nào.

    Chỉ gọi cho chữ đứng cạnh một ký hiệu cốt. Hàm không biết ngữ cảnh: bỏ dấu thì "cột sàn" trùng "cốt sàn",
    "RIDGE VENT" vẫn ra `ridge`, và chữ có hai từ khoá trả vai đứng trước trong `_ROLES`.
    """
    folded = fold(text)
    return next((role for role, pattern in _ROLES if pattern.search(folded)), None)
