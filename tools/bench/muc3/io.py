"""Đọc JSON đáp án/dự đoán: tệp thiếu hay hỏng trả `None` để bộ chấm đếm thành "không dự đoán"."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any] | None:
    """Nội dung JSON (một object) của `path`; không đọc được, không phải JSON hay không phải object thì `None`."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
