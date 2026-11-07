"""`EXECUTOR_SHA` của domain phải khớp `compile_plan.bpy` (M1-01)."""

import hashlib

from apps.worker.model_export.blender import COMPILE_SCRIPT
from packages.domain.model_export.version import EXECUTOR_SHA, normalized_bytes


def test_compile_plan__matches_executor_sha() -> None:
    """Sửa `compile_plan.bpy` mà quên cập nhật `EXECUTOR_SHA` → cache bản xuất cũ không bị loại: test này chặn."""
    assert hashlib.sha256(normalized_bytes(COMPILE_SCRIPT)).hexdigest() == EXECUTOR_SHA
