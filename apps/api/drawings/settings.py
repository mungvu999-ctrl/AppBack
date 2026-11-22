"""Hạn mức của module bản vẽ (B2-04 [5], theo mẫu `apps/api/floors/settings.py`).

Đọc **lười** qua `get_drawings_settings()`; test đổi trần bằng `monkeypatch.setenv` rồi
`reset_drawings_settings_cache()`.
"""

from functools import cache

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class DrawingsSettings(BaseSettings):
    """Trần tệp, cửa sổ khử trùng, tham số quét bù và hạn mức gọi `#5`, N51."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    upload_max_bytes: PositiveInt = 104_857_600
    upload_chunk_bytes: PositiveInt = 5_242_880
    upload_init_dedupe_s: PositiveInt = 120
    upload_abandon_after_h: PositiveInt = 24
    pipeline_requeue_after_s: PositiveInt = 600
    drawings_init_rate_limit: PositiveInt = 60
    drawings_init_rate_window_s: PositiveInt = 600
    drawings_rerun_rate_limit: PositiveInt = 10
    drawings_rerun_rate_window_s: PositiveInt = 600


@cache
def get_drawings_settings() -> DrawingsSettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return DrawingsSettings()


def reset_drawings_settings_cache() -> None:
    """Chỉ cho test và CLI: đọc lại biến môi trường ở lần gọi sau."""
    get_drawings_settings.cache_clear()
