"""Tách tên test case theo cổng case (NO-337): nguồn duy nhất cho `tools/case_gate.py` và bộ ghi
golden `packages/testing/golden/recorder.py` (R-07).

Thuần `re`, không nhập gì nội bộ — nằm ở tầng thấp để gói test lẫn công cụ nhập xuôi chiều, không
còn `packages.*` nhập ngược `tools.*` (cùng họ NO-184, `packages/core/pinned_images.py`).
"""

from __future__ import annotations

import re
from typing import NamedTuple

# hậu tố `_<việc>` hoặc id tham số `[...]` sau mã case đều được
_TEST_OP_CASE_RE = re.compile(r"^test_(?P<op>.+?)__(?P<case>[A-Z]\d{2}[a-z]?)(?:[_\[].*)?$")
_TEST_COMMON_RE = re.compile(r"^test_common__(?P<case>[A-Z]\d{2}[a-z]?)\[(?P<op>.+)\]$")


class CaseTestName(NamedTuple):
    """Tên test case đã tách: `tail` là phần sau mã case (`_missing`, `[tham-số]`), rỗng ở dạng chung."""

    op: str
    case: str
    tail: str
    common: bool


def split_case_test_name(name: str) -> CaseTestName | None:
    """Tách `test_<op>__<case>[_…|[…]]` hay `test_common__<case>[<op>]` (cổng case); `None` khi không khớp.

    Mẫu chung xét trước: `test_common__C04[op]` cũng khớp mẫu riêng với op="common".
    """
    m = _TEST_COMMON_RE.match(name)
    if m:
        return CaseTestName(m.group("op"), m.group("case"), "", common=True)
    m = _TEST_OP_CASE_RE.match(name)
    if m is None:
        return None
    return CaseTestName(m.group("op"), m.group("case"), name[m.end("case") :], common=False)
