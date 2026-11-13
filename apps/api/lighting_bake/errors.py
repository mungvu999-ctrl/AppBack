"""Mã lỗi riêng của nướng ánh sáng; mã lõi (`FILE_TYPE_MISMATCH`, `NOT_FOUND`) ở `packages.core.error_codes`."""

from typing import Final

from packages.core.error_codes import NOT_FOUND
from packages.core.errors import ERRORS, AppError

LIGHTING_BAKE_IN_PROGRESS = ERRORS.define("LIGHTING_BAKE_IN_PROGRESS", 409)
"""Dự án đã có một lượt `queued|running` (unique partial `uq_lighting_bakes_active`)."""

LIGHTING_BAKE_DISABLED = ERRORS.define("LIGHTING_BAKE_DISABLED", 503)
"""Công tắc `LIGHTING_BAKE_ENABLED` tắt: không có worker `bake` để nhận lượt. FE: "Nướng ánh sáng chưa được bật."."""

LIGHTING_BAKE_RESOURCE: Final = "lightingBake"


def bake_not_found() -> AppError:
    """404 `NOT_FOUND` `resource:"lightingBake"` — chưa có lượt, id sai mẫu, tệp chưa sẵn sàng."""
    return NOT_FOUND.error(resource=LIGHTING_BAKE_RESOURCE)
