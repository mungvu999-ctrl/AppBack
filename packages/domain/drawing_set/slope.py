"""Độ dốc mái ghi bằng chữ trên mặt bằng mái/mặt cắt → tỉ số cao/ngang (NHA-01, thiết kế §2.D mục 3).

Bốn kiểu ghi: phần trăm ("i=30%"), độ ("30°"), số inch trên 12 ("6:12", "6/12") và tỉ số `a:b` mẫu khác 12
("1:2"). Kiểu cuối nhập nhằng (cao:ngang hay ngang:cao) nên chỉ được đánh dấu, lớp trên hỏi người duyệt (luật
M-19). Nhận nhầm tệ hơn bỏ sót: cả chuỗi phải là độ dốc; "1:50" (tỉ lệ bản vẽ), "1/2" (phân số inch) và số trần
bị từ chối. Chưa nối vào luồng nào; nơi dùng đầu tiên là NHA-05b.
"""

import math
import re
from dataclasses import dataclass
from typing import Final, Literal

type SlopeNotation = Literal["percent", "degree", "in_12", "ratio"]

_NUMBER: Final = r"((?!0\d)\d{1,3}(?:[.,]\d{1,2})?)"  # "05" là phút của giờ hay ngày tháng, không phải trị số dốc
# Khoảng trắng chỉ được quanh dấu: bỏ hết khoảng trắng sẽ dán "1 0%" thành "10%".
_SLOPE: Final = re.compile(rf"(?:I\s*=\s*)?{_NUMBER}\s*(?:(%)|([°º])|([:/])\s*{_NUMBER})", re.ASCII)
_MAX_DEG: Final = 85.0  # từ 85° trở lên là tường, không còn là mái (luật M-17)
_MAX_RATIO: Final = math.tan(math.radians(_MAX_DEG))
_IN_12: Final = 12.0  # "x:12" là quy ước ghi số inch cao trên 12 inch ngang
_IN_12_MAX_RISE: Final = 24.0  # 24:12 (63°) là mái dốc nhất còn ghi kiểu này; "120/12" là số khác
_RATIO_MAX_RUN: Final = 10.0  # "1:10", "1:50" viết trần là tỉ lệ bản vẽ, không phải độ dốc


@dataclass(frozen=True, slots=True)
class Slope:
    """Một độ dốc đọc được: tỉ số cao/ngang, chữ gốc, kiểu ghi, và cờ nhập nhằng của dạng `a:b` mẫu khác 12."""

    rise_over_run: float
    text: str
    notation: SlopeNotation
    ambiguous: bool = False


def _number(digits: str) -> float:
    """Số thập phân viết bằng dấu chấm hoặc dấu phẩy."""
    return float(digits.replace(",", "."))


def _slope(ratio: float, text: str, notation: SlopeNotation, *, ambiguous: bool = False) -> Slope | None:
    """`Slope` nếu tỉ số dưới tan 85° (tỉ số không bao giờ âm: mẫu chữ không có dấu), ngược lại `None`."""
    return Slope(ratio, text, notation, ambiguous) if ratio < _MAX_RATIO else None


def parse_slope(text: str) -> Slope | None:
    """Chữ độ dốc → `Slope`, hoặc `None` khi không đọc được.

    Nhận "30%", "i=30%", "30°", "6:12", "6/12" (ghi rõ, x không quá 24) và "1:2" (mẫu số dưới 10, đánh dấu nhập
    nhằng, lưu theo cách đọc cao:ngang). Khoảng trắng quanh dấu và hoa/thường không tính. Từ chối chuỗi có chữ
    thừa, "a/b" mẫu khác 12, "a:b" mẫu từ 10 trở lên, số có số 0 đứng đầu ("9:05"), và độ dốc từ 85°.

    Chỉ gọi cho chữ trên hình mái/mặt cắt: "1:2", "1:5" trần cũng là tỉ lệ bản vẽ và "3:12" cũng là giờ — hàm
    không phân biệt được, nên dạng `a:b` luôn mang cờ nhập nhằng để lớp trên hỏi.
    """
    found = _SLOPE.fullmatch(text.strip().upper())
    if found is None:
        return None
    first = _number(found[1])
    if found[2]:
        return _slope(first / 100, text, "percent")
    if found[3]:
        return _slope(math.tan(math.radians(first)), text, "degree") if first < _MAX_DEG else None
    run = _number(found[5])
    if run == _IN_12:
        return _slope(first / _IN_12, text, "in_12") if first <= _IN_12_MAX_RISE else None
    if found[4] == ":" and 0 < run < _RATIO_MAX_RUN:
        return _slope(first / run, text, "ratio", ambiguous=True)
    return None
