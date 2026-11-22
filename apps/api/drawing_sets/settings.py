"""Công tắc của đề xuất cho cả nhà (mẫu `apps/api/design_director/settings.py`).

Công tắc là biến môi trường của tiến trình, chưa theo dự án (HS-02, kế hoạch mục 1).
"""

from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DrawingSetSettings(BaseSettings):
    """`drawing_set_enabled` mặc định tắt: tắt thì `GET …/house-proposal` trả 200 `enabled: false`, rỗng."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    drawing_set_enabled: bool = False


@cache
def get_drawing_set_settings() -> DrawingSetSettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return DrawingSetSettings()


def reset_drawing_set_settings_cache() -> None:
    """Chỉ cho test: đọc lại biến môi trường ở lần gọi sau."""
    get_drawing_set_settings.cache_clear()
