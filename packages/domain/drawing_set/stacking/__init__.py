"""Chồng tầng thuần (K-32d): căn mặt bằng nhiều tầng lên cùng một hệ toạ độ, và căn mặt cắt vào mặt bằng.

Chuyển từ nhân nghiên cứu `muc3/kernel/{stacking,stacking2,register_h,register_v}.py`; chưa nối vào pipeline hay API
(xem `changes/K-32d.md`). Khác phần còn lại của `drawing_set` (chỉ thư viện chuẩn), gói con này cần `numpy` và OpenCV
(`cv2`) cho mặt nạ, FFT và bản đồ khoảng cách (nợ NO-521: khai trong `packages/domain/pyproject.toml`).

- `stack_pair(a, b)` → dịch mm đưa tầng B lên tầng A kèm độ tin hai nguồn.
- `register_h(side, plan_axes(plan))`, `register_v(marks, mm_per_px)` → căn ngang/dọc một mặt cắt.
"""

from packages.domain.drawing_set.stacking.model import (
    Basis,
    Box,
    Bubble,
    Candidate,
    Conflict,
    FloorObservation,
    StackResult,
)
from packages.domain.drawing_set.stacking.pair import confidence, stack_pair
from packages.domain.drawing_set.stacking.register_h import HorizontalFit, fit_line, plan_axes, register_h, row_labels
from packages.domain.drawing_set.stacking.register_v import LevelPoint, VerticalFit, level_rows, register_v

__all__ = [
    "Basis",
    "Box",
    "Bubble",
    "Candidate",
    "Conflict",
    "FloorObservation",
    "HorizontalFit",
    "LevelPoint",
    "StackResult",
    "VerticalFit",
    "confidence",
    "fit_line",
    "level_rows",
    "plan_axes",
    "register_h",
    "register_v",
    "row_labels",
    "stack_pair",
]
