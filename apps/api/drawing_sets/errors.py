"""Mã lỗi của bộ hồ sơ nhiều bản vẽ (K-32a, BE-00 §4 "Bộ hồ sơ"); mã lõi và U-case ở `packages.core.error_codes`,
`FLOOR_ID_TAKEN` dùng chung của `apps.api.floors.errors`.
"""

from typing import Final

from packages.core.error_codes import NOT_FOUND
from packages.core.errors import ERRORS, AppError

DRAWING_SET_DISABLED = ERRORS.define("DRAWING_SET_DISABLED", 503)
"""Công tắc `DRAWING_SET_ENABLED` tắt: N54, N55, N56, N59, N60 từ chối (mẫu N42); đọc và xoá vẫn chạy."""

DRAWING_SET_NOT_RECEIVING = ERRORS.define("DRAWING_SET_NOT_RECEIVING", 409)
"""N55: bộ đã chốt (không còn `receiving`) — không nhận thêm khúc."""

DRAWING_SET_NOT_READY = ERRORS.define("DRAWING_SET_NOT_READY", 409)
"""N59/N60: bộ chưa tới bước duyệt (`receiving`, `analyzing`) hay đã hỏng."""

DRAWING_SET_CONFIRMED = ERRORS.define("DRAWING_SET_CONFIRMED", 409)
"""N59: bộ đã xác nhận — bản duyệt không đổi được nữa."""

DRAWING_SET_REVISION_STALE = ERRORS.define("DRAWING_SET_REVISION_STALE", 409)
"""N60: `revision` gửi lên không phải bản duyệt mới nhất (người khác vừa lưu)."""

FLOOR_HAS_DRAWING = ERRORS.define("FLOOR_HAS_DRAWING", 409)
"""N60: tầng đích đã có bản vẽ mà người duyệt không tick "Thay bản vẽ cũ" (`replace: true`)."""

DRAWING_SET_NO_PLAN = ERRORS.define("DRAWING_SET_NO_PLAN", 422)
"""N60: không hình nào có đích tầng (tầng có sẵn hay tầng mới) — không có gì để dựng."""

DRAWING_SET_TOO_MANY_PAGES = ERRORS.define("DRAWING_SET_TOO_MANY_PAGES", 422)
"""N56: một PDF quá `PDF_PAGES_MAX` trang hay cả bộ quá `SET_PAGES_MAX` trang."""

ALL_FILES_REJECTED: Final = "DRAWING_SET_ALL_FILES_REJECTED"
"""`errorCode` của bộ `failed` khi mọi tệp đều hỏng ở N56 — chuỗi trên dây, không phải mã HTTP."""

ANALYSIS_FAILED: Final = "DRAWING_SET_ANALYSIS_FAILED"
"""`errorCode` của bộ `failed` khi lượt phân tích hỏng hay treo quá hạn — chuỗi trên dây, không phải mã HTTP."""

SET_RESOURCE: Final = "drawingSet"
FILE_RESOURCE: Final = "drawingSetFile"


def set_not_found() -> AppError:
    """404 `NOT_FOUND` `resource:"drawingSet"` — bộ không có, của dự án khác, hay đã xoá mềm."""
    return NOT_FOUND.error(resource=SET_RESOURCE)


def file_not_found() -> AppError:
    """404 `NOT_FOUND` `resource:"drawingSetFile"` — tệp không thuộc bộ trên đường."""
    return NOT_FOUND.error(resource=FILE_RESOURCE)
