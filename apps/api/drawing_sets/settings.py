"""Công tắc và hạn mức của bộ hồ sơ (mẫu `apps/api/design_director/settings.py`).

Công tắc là biến môi trường của tiến trình, chưa theo dự án (HS-02 kế hoạch mục 1; K-32a quyết định 2). Cùng một
công tắc `DRAWING_SET_ENABLED` bật đề xuất cho cả nhà (N52) và lệnh ghi của "Tải cả bộ hồ sơ" (N54-N61).
"""

from functools import cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DrawingSetSettings(BaseSettings):
    """`drawing_set_enabled` mặc định tắt: N52 trả `enabled: false`, lệnh ghi N54/N55/N56/N59/N60 trả 503.

    `drawing_set_create_rate_*`: hạn mức N54 theo người (C11, mặc định 10 bộ / 10 phút). `drawing_set_list_limit`:
    trần một trang N58 (C15 đặt 3 trong test). `drawing_set_analysis_stall_s`: lượt phân tích quá ngần này giây
    coi như treo → `failed` `DRAWING_SET_ANALYSIS_FAILED`.
    """

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    drawing_set_enabled: bool = False
    drawing_set_create_rate_limit: int = Field(default=10, ge=1)
    drawing_set_create_rate_window_s: int = Field(default=600, ge=1)
    drawing_set_list_limit: int = Field(default=20, ge=1, le=200)
    drawing_set_analysis_stall_s: int = Field(default=900, ge=60)


@cache
def get_drawing_set_settings() -> DrawingSetSettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return DrawingSetSettings()


def reset_drawing_set_settings_cache() -> None:
    """Chỉ cho test: đọc lại biến môi trường ở lần gọi sau."""
    get_drawing_set_settings.cache_clear()
