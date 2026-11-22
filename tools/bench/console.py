"""In tiến độ của thước ra stdout/stderr (ruff `T20` cấm `print`; ghi thẳng vào luồng và xả ngay)."""

from __future__ import annotations

import sys


def say(msg: str) -> None:
    """Một dòng tiến độ ra stdout, xả ngay (lượt chạy dài, người xem cần thấy từng ca)."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def warn(msg: str) -> None:
    """Một dòng cảnh báo/lỗi ra stderr."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
