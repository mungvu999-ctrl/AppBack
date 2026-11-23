"""Lõi thang/thang máy xuyên tầng, thuần, từ nhân nghiên cứu `kernel/cores.py` (K-32e).

Vào: `CorePlan` mỗi tầng (ô thang `Well` từ bộ dò ảnh, chữ, mặt nạ tường tuỳ chọn) và `StackShift` giữa các tầng
liền kề (K-32d điền). Ra: `StairCore` (tầng quan sát + giả định, độ chắc từng tầng), ô lẻ `UnlinkedWell`,
gợi ý ô trống `VoidHint`. Chỉ thư viện chuẩn; chưa nối pipeline (xem `changes/K-32e.md`).
"""

from packages.domain.drawing_set.cores.detect import Detection, YoloBox, build_wells, combine, is_dropped
from packages.domain.drawing_set.cores.evidence import evidence_near, has_room_label, void_texts
from packages.domain.drawing_set.cores.link import stair_cores, stair_tracks
from packages.domain.drawing_set.cores.types import (
    Box,
    CorePlan,
    PlanText,
    StackShift,
    StairCore,
    UnlinkedWell,
    VoidHint,
    Well,
)
from packages.domain.drawing_set.cores.voids import voids

__all__ = [
    "Box",
    "CorePlan",
    "Detection",
    "PlanText",
    "StackShift",
    "StairCore",
    "UnlinkedWell",
    "VoidHint",
    "Well",
    "YoloBox",
    "build_wells",
    "combine",
    "evidence_near",
    "has_room_label",
    "is_dropped",
    "stair_cores",
    "stair_tracks",
    "void_texts",
    "voids",
]
