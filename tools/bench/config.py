"""Cấu hình thước bench: mọi đường dẫn, địa chỉ và tên ảnh lấy từ biến môi trường (K-17a, Q8 a).

Ảnh bench, đáp án và `ml-cache` ở ngoài repo nên không có mặc định cho `BENCH_ROOT`: thiếu thì báo lỗi rõ
thay vì đoán một đường dẫn của riêng máy nào.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_API = "http://localhost:18081/api"
DEFAULT_ORIGIN = "http://localhost:18080"
DEFAULT_WORKER_IMAGE = "appback-worker:f14"


class ConfigError(RuntimeError):
    """Thiếu cấu hình bắt buộc (vd `BENCH_ROOT`); thông điệp nói rõ biến cần đặt."""


def bench_root(env: Mapping[str, str] | None = None) -> Path:
    """Thư mục gốc dữ liệu bench (`MANIFEST.csv`, `plans/`, `answers/`, `runs/`, `ml-cache/`); `BENCH_ROOT`.

    Tập F dùng một gốc khác: đặt `BENCH_ROOT` trỏ vào thư mục của tập đó. Ném `ConfigError` khi chưa đặt.
    """
    value = (os.environ if env is None else env).get("BENCH_ROOT", "").strip()
    if not value:
        raise ConfigError("chưa đặt BENCH_ROOT (thư mục chứa MANIFEST.csv, plans/, answers/, runs/, ml-cache/)")
    return Path(value)


def bench_api(env: Mapping[str, str] | None = None) -> str:
    """Địa chỉ API của stack f14 (`BENCH_API`, mặc định `http://localhost:18081/api`)."""
    return (os.environ if env is None else env).get("BENCH_API") or DEFAULT_API


def bench_origin(env: Mapping[str, str] | None = None) -> str:
    """Giá trị header `Origin` gửi kèm (`BENCH_ORIGIN`, mặc định `http://localhost:18080`)."""
    return (os.environ if env is None else env).get("BENCH_ORIGIN") or DEFAULT_ORIGIN


def bench_secrets(env: Mapping[str, str] | None = None) -> Path:
    """Tệp `.secrets.env` của stack f14 (`BENCH_SECRETS`, có `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD`); thiếu → ném."""
    value = (os.environ if env is None else env).get("BENCH_SECRETS", "").strip()
    if not value:
        raise ConfigError("chưa đặt BENCH_SECRETS (tệp .secrets.env của stack f14)")
    return Path(value)


def bench_worker_image(env: Mapping[str, str] | None = None) -> str:
    """Ảnh worker chạy replay (`BENCH_WORKER_IMAGE`, mặc định `appback-worker:f14`)."""
    return (os.environ if env is None else env).get("BENCH_WORKER_IMAGE") or DEFAULT_WORKER_IMAGE


def ml_cache(root: Path) -> Path:
    """Thư mục `ml-cache/` (đầu ra ML + dữ kiện DB của từng ca, tạo bởi `replay.dump`) dưới gốc dữ liệu."""
    return root / "ml-cache"


def replay_sets(root: Path, env: Mapping[str, str] | None = None) -> dict[str, tuple[Path, Path]]:
    """Hai tập replay: {tên: (thư mục lượt gốc, gốc chấm)}.

    `bench`: lượt `BENCH_RUN_BENCH` (mặc định `runs/r4b`) chấm với gốc `root`. `F`: lượt `BENCH_RUN_F` (mặc định
    `tap-f/runs/f2`) chấm với gốc `BENCH_ROOT_F` (mặc định `root/tap-f`, nơi có MANIFEST.csv của tập F).
    """
    e = os.environ if env is None else env
    return {
        "bench": (root / (e.get("BENCH_RUN_BENCH") or "runs/r4b"), root),
        "F": (root / (e.get("BENCH_RUN_F") or "tap-f/runs/f2"), Path(e.get("BENCH_ROOT_F") or root / "tap-f")),
    }
