"""Cài đặt worker xuất GLB: đường Blender, trần thời gian, trần byte, thư mục cache gói (M1-01).

Đọc lại mỗi lần gọi, không `cache` (khuôn `apps/worker/lighting_bake/settings.py`): rẻ, và test hạ được.
"""

import tempfile
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ModelExportWorkerSettings", "get_worker_settings"]

DEFAULT_BLENDER_BIN: Final = "/opt/blender/blender"
DEFAULT_TIMEOUT_S: Final = 270.0
"""Dưới soft timeout 300 s của hợp đồng §6, để worker tự giết Blender trước."""


class ModelExportWorkerSettings(BaseSettings):
    """`BLENDER_BIN`, `MODEL_EXPORT_TIMEOUT_S`, `MODEL_EXPORT_MAX_OUTPUT_BYTES`, `MODEL_EXPORT_PACK_CACHE_DIR`,
    `MODEL_EXPORT_PACK_CACHE_MAX_BYTES`."""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    blender_bin: str = DEFAULT_BLENDER_BIN
    model_export_timeout_s: Annotated[float, Field(gt=0)] = DEFAULT_TIMEOUT_S
    model_export_max_output_bytes: Annotated[int, Field(gt=0)] = 256 * 1024 * 1024
    model_export_pack_cache_dir: str = str(Path(tempfile.gettempdir()) / "model-export-packs")
    model_export_pack_cache_max_bytes: Annotated[int, Field(gt=0)] = 2 * 1024 * 1024 * 1024


def get_worker_settings() -> ModelExportWorkerSettings:
    """Cài đặt đọc từ môi trường lúc gọi."""
    return ModelExportWorkerSettings()
