"""Phiên bản bộ biên dịch suy từ mã (M1-01)."""

import re
from pathlib import Path

from packages.domain.model_export import version


def test_compiler_version__format_and_stable() -> None:
    """Dạng `m1-01.<12 hex>`, gọi lại cho cùng kết quả."""
    first = version.compiler_version()
    assert re.fullmatch(r"m1-01\.[0-9a-f]{12}", first)
    assert version.compiler_version() == first


def test_normalized_bytes__crlf_becomes_lf(tmp_path: Path) -> None:
    """CRLF và LF cho cùng nội dung băm."""
    crlf, lf = tmp_path / "a.py", tmp_path / "b.py"
    crlf.write_bytes(b"x = 1\r\ny = 2\r\n")
    lf.write_bytes(b"x = 1\ny = 2\n")
    assert version.normalized_bytes(crlf) == version.normalized_bytes(lf)
