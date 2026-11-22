"""Hạn mức của nướng ánh sáng (mẫu `apps/api/admin_ml_registry/settings.py`).

"Hàm worker nhập": lịch `sweep_lighting_bakes` đọc module này, nên chỉ `pydantic_settings`.
"""

from functools import cache
from typing import Final

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

UPLOAD_BODY_LIMIT: Final = 128 * 1024 * 1024
"""128 MiB — trần thân N42; `route_options` chốt lúc nạp module nên là hằng riêng."""


class LightingBakeSettings(BaseSettings):
    """Công tắc `lighting_bake_enabled` (bật cùng profile `bake`), trần `storage.put` N42, mốc tuổi lịch quét."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    # ponytail: cờ tay, không dò worker sống (heartbeat). Cờ bật mà worker `bake` chết thì lượt vẫn kẹt
    # `queued` đến `queued_max_age_s` rồi lịch quét chốt `failed`; nâng lên heartbeat khi cần.
    lighting_bake_enabled: bool = False
    lighting_bake_upload_max_bytes: PositiveInt = UPLOAD_BODY_LIMIT
    lighting_bake_resend_after_s: PositiveInt = 600
    lighting_bake_queued_max_age_s: PositiveInt = 6 * 3600
    lighting_bake_running_max_age_s: PositiveInt = 70 * 60


@cache
def get_lighting_bake_settings() -> LightingBakeSettings:
    """Cấu hình của tiến trình, đọc biến môi trường một lần."""
    return LightingBakeSettings()


def reset_lighting_bake_settings_cache() -> None:
    """Chỉ cho test: đọc lại biến môi trường ở lần gọi sau."""
    get_lighting_bake_settings.cache_clear()
