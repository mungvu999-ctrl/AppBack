"""Mã lỗi riêng của bộ xuất GLB (HOP-DONG-M1 §6); mã hỏng của job ở `state.py`."""

from typing import Final

from packages.core.error_codes import NOT_FOUND
from packages.core.errors import ERRORS, AppError

MODEL_EXPORT_NOT_READY = ERRORS.define("MODEL_EXPORT_NOT_READY", 409)
"""Tệp xin tải chưa có: job chưa `done`, hoặc `model.glb` của kết quả `blocked`."""

MODEL_EXPORT_RESOURCE: Final = "modelExport"


def export_not_found() -> AppError:
    """404 `NOT_FOUND` `resource:"modelExport"` — chưa có lượt, id sai mẫu, lượt của dự án khác."""
    return NOT_FOUND.error(resource=MODEL_EXPORT_RESOURCE)
