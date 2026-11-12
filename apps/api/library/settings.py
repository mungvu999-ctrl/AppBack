"""Hạn mức của module thư viện .glb (B2-06 [5], theo mẫu `apps/api/drawings/settings.py`).

Đọc **lười** qua `get_library_settings()`; test đổi trần bằng `monkeypatch.setenv` rồi
`reset_library_settings_cache()`.
"""

from functools import cache

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class LibrarySettings(BaseSettings):
    """Trần số mục của #14 và tuổi tối đa của lần kiểm object trước khi lịch kiểm lại."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    # Trần phải lớn hơn cả danh mục: 978 mẫu từ tệp + 16 mẫu dựng sẵn (12/11/2026). Trần cũ 500 cắt mất các mẫu xếp sau
    # (sắp theo `sort_order`) — editor không bao giờ thấy chúng. `test_list_cap.py` giữ danh mục dưới trần.
    library_list_max: PositiveInt = 2000
    library_verify_after_s: PositiveInt = 3600


@cache
def get_library_settings() -> LibrarySettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return LibrarySettings()


def reset_library_settings_cache() -> None:
    """Chỉ cho test và CLI: đọc lại biến môi trường ở lần gọi sau."""
    get_library_settings.cache_clear()
