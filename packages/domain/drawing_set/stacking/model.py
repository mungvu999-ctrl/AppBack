"""Kiểu dữ liệu của chồng tầng (K-32d): quan sát một mặt bằng vào, kết quả chồng cặp tầng ra.

Quan sát là thứ các bộ dò/OCR bàn giao cho một mặt bằng: bong bóng trục, ô thang, hộp chữ, mặt nạ tường và cột,
và (khi thiếu mặt nạ) ảnh xám của bản vẽ. Toạ độ điểm ảnh tính trên ảnh dựng 300 dpi của hình; mặt nạ ở
`mask_scale` lần ảnh đó. Mọi dịch chuyển trả ra tính bằng mm, theo chiều "đưa tầng B lên tầng A": `A = B + dịch`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Mask = NDArray[np.uint8]  # mặt nạ nhị phân: điểm đặt là 255 (> 127), 0 là nền
Gray = NDArray[np.uint8]  # ảnh xám: nét mực tối (< 128)
Shift = tuple[float, float]  # (dx, dy) mm
PartialShift = tuple[float | None, float | None]  # một trục chưa biết là None (lưới chỉ có một hướng)
Basis = Literal["mask", "ink"]


@dataclass(frozen=True, slots=True)
class Bubble:
    """Bong bóng đánh số trục lưới: nhãn, tâm và bán kính (px ảnh dựng)."""

    label: str
    x: float
    y: float
    r: float = 55.0


@dataclass(frozen=True, slots=True)
class Box:
    """Hộp chữ nhật (px ảnh dựng): ô thang hay hộp chữ cần xoá khỏi mực."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class FloorObservation:
    """Quan sát một mặt bằng. `mm_per_px` None = chưa biết tỉ lệ: không chồng được.

    `wall_mask`/`col_mask` ở `mask_scale` lần ảnh dựng. `ink` là ảnh xám để lùi về khi mặt nạ tường thiếu hoặc
    thưa, ở `ink_scale` lần ảnh dựng; `texts` là hộp chữ (px ảnh dựng) bị xoá khỏi mực trước khi chấm.
    """

    mm_per_px: float | None
    bubbles: Sequence[Bubble] = ()
    stairs: Sequence[Box] = ()
    texts: Sequence[Box] = ()
    wall_mask: Mask | None = None
    col_mask: Mask | None = None
    mask_scale: float = 0.5
    ink: Gray | None = None
    ink_scale: float = 0.5


@dataclass(frozen=True, slots=True)
class Candidate:
    """Một ứng viên dịch chuyển sau gộp: tên các đầu mối cùng đề xuất nó, dịch (mm, làm tròn) và điểm kiểm."""

    names: tuple[str, ...]
    shift_mm: tuple[int, int]
    score: float


@dataclass(frozen=True, slots=True)
class Conflict:
    """Đầu mối độc lập chỉ chỗ khác xa đáp án (> 150 mm trên trục nó biết); trục chưa biết là None."""

    cue: str
    shift_mm: tuple[int | None, int | None]


@dataclass(frozen=True, slots=True)
class StackResult:
    """Kết quả chồng một cặp tầng.

    `shift_mm` None = không trả lời (thiếu tỉ lệ hay lớp kiểm). `confidence` theo luật hai nguồn: ≥ 0,9 chỉ khi
    điểm kiểm tốt, đầu mối độc lập (nhãn lưới hai trục, cột) xác nhận trong 100 mm và ứng viên đứng nhì kém rõ
    ràng; xung đột đầu mối độc lập kẹp ≤ 0,65. `ratio` = điểm nhì / điểm nhất (0 khi không có đối thủ xa).
    """

    shift_mm: tuple[int, int] | None
    confidence: float
    basis: Basis | None = None
    score: float = 0.0
    ratio: float = 0.0
    cues: dict[str, tuple[int, int]] = field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()
    confirmed_by: tuple[str, ...] = ()
    conflict: tuple[Conflict, ...] = ()
