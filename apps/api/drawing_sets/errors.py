"""Mã lỗi riêng của đề xuất cho cả nhà; mã lõi (`NOT_FOUND`, …) ở `packages.core.error_codes`.

Câu chữ cho người dùng (FE): `DRAWING_SET_DISABLED` — FE ẩn bảng đề xuất, không hiện lỗi.
"""

from typing import Final

from packages.core.errors import ERRORS

DRAWING_SET_DISABLED = ERRORS.define("DRAWING_SET_DISABLED", 503)
"""`DRAWING_SET_ENABLED` đang tắt."""

RETRY_AFTER_S: Final = 10
"""`Retry-After` của 503 (W9: 1-10 s); FE không tự thử lại, công tắc chỉ đổi khi quản trị khởi động lại."""
