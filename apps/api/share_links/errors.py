"""Mã lỗi riêng của liên kết chia sẻ (L6-N05); mã lõi (`VALIDATION`, `RATE_LIMITED`) ở `packages.core.error_codes`."""

from packages.core.errors import ERRORS

SHARE_LINK_LIMIT_REACHED = ERRORS.define("SHARE_LINK_LIMIT_REACHED", 422)
"""#48: dự án đã có đủ liên kết đang hiệu lực; thu hồi bớt rồi tạo lại."""
