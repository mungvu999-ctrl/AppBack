"""Cấu hình thước Mức 3: gốc dữ liệu lấy từ biến môi trường, không có mặc định theo máy (K-33, Q8 a)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


class ConfigError(RuntimeError):
    """Thiếu cấu hình bắt buộc; thông điệp nói rõ biến cần đặt."""


def muc3_root(env: Mapping[str, str] | None = None) -> Path:
    """Gốc dữ liệu Mức 3 (`BENCH_MUC3_ROOT`): chứa `split.json`, `truth*/`, `sheets/`; thiếu thì ném `ConfigError`.

    Không dùng chung `BENCH_ROOT` của thước bench mặt bằng vì hai kho đáp án khác nhau, đặt ở hai nơi.
    """
    value = (os.environ if env is None else env).get("BENCH_MUC3_ROOT", "").strip()
    if not value:
        raise ConfigError("chưa đặt BENCH_MUC3_ROOT (thư mục chứa split.json, truth/, truth_v2/, truth_v3/, sheets/)")
    return Path(value)
