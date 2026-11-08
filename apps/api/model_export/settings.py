"""Cấu hình bộ xuất GLB (mẫu `apps/api/lighting_bake/settings.py`).

"Hàm worker nhập": worker và lịch quét đọc module này, nên chỉ `pydantic_settings`.
"""

from functools import cache

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelExportSettings(BaseSettings):
    """Phiên bản gói/bộ biên dịch, trần, mốc tuổi của lịch quét."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    model_export_pack_version: str = "m1-01-pack-r1"
    """Gói đã kiểm cùng mã này (nạp bằng `cli import-pack`); đổi qua biến môi trường khi nạp gói mới."""
    model_export_compiler_version: str | None = None
    """`None` → suy từ mã (`packages.domain.model_export.version.compiler_version`); chỉ đặt khi cần ghim tay."""
    model_export_snapshot_max_bytes: PositiveInt = 64 * 1024 * 1024
    model_export_soft_timeout_s: PositiveInt = 300
    model_export_resend_after_s: PositiveInt = 600
    model_export_queued_max_age_s: PositiveInt = 2 * 3600
    model_export_running_max_age_s: PositiveInt = 15 * 60
    model_export_keep_done: PositiveInt = 5


@cache
def get_model_export_settings() -> ModelExportSettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return ModelExportSettings()


def reset_model_export_settings_cache() -> None:
    """Chỉ cho test: đọc lại biến môi trường ở lần gọi sau."""
    get_model_export_settings.cache_clear()
