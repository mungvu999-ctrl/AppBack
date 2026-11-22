"""Nhãn phát hiện ô mở và đồ đạc, và đích miền của từng nhãn (B5-03 dùng, B5-05 dựng).

`DETECTION_LABELS` theo **đúng thứ tự id lớp YOLO** của model huấn luyện: đổi thứ tự là
đọc sai mọi model đã huấn luyện. `other` (id 10) là lớp gom đồ không có đích riêng.
`ADDED_LABELS` (máy giặt, buồng tắm đứng; model 13 lớp, B5 2026-10-28) đứng **sau** `other`
để model 11 lớp (v3) giữ nguyên id 0..10.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, get_args

ObjectLabel = Literal[
    "door",
    "double_door",
    "window",
    "table",
    "chair",
    "bed",
    "wardrobe",
    "kitchen_cabinet",
    "sanitary_fixture",
    "stair",
]
AddedLabel = Literal["washing_machine", "shower"]
DetectionLabel = ObjectLabel | Literal["other"] | AddedLabel
SwingDirection = Literal["left", "right", "double", "sliding", "fixed"]

OBJECT_LABELS: Final[tuple[ObjectLabel, ...]] = get_args(ObjectLabel)
ADDED_LABELS: Final[tuple[AddedLabel, ...]] = get_args(AddedLabel)
DETECTION_LABELS: Final[tuple[DetectionLabel, ...]] = (*OBJECT_LABELS, "other", *ADDED_LABELS)

COCO_TO_LABEL: Final[Mapping[str, DetectionLabel]] = MappingProxyType(
    {
        "bed": "bed",
        "chair": "chair",
        "couch": "other",
        "dining table": "table",
        "toilet": "sanitary_fixture",
        "sink": "sanitary_fixture",
    }
)
"""Tên lớp COCO của `yolov8n` gốc → nhãn; lớp COCO khác bị bỏ."""

OPENING_BLIND_PINNED: Final = frozenset({"yolov8n", "yolov8s"})
"""Tên model ghim (`ModelRef.pinned_name`) là detector COCO gốc: không có lớp cửa/cửa sổ nên không thấy ô mở."""


@dataclass(frozen=True, slots=True)
class LabelTarget:
    """Thực thể miền mà một nhãn dựng thành (`src/domain/spatial/types.ts:135-163`).

    Ô mở mang kích thước mặc định (mm) khi ảnh không cho đủ thông tin; `None` nghĩa là
    lấy theo hộp phát hiện. Đồ đạc không có trường số.
    """

    category: Literal["opening", "furniture"]
    domain_kind: str
    width_mm: int | None = None
    height_mm: int | None = None
    sill_mm: int | None = None
    swing: SwingDirection | None = None


def _furniture(kind: str) -> LabelTarget:
    """Đích đồ đạc: chỉ có loại camel của FE (`FurnitureKind`)."""
    return LabelTarget(category="furniture", domain_kind=kind)


# Kích thước ô mở theo bộ mẫu FE (`__fixtures__/sampleBuilding.ts:49-56,113,125`).
LABEL_TARGETS: Final[Mapping[DetectionLabel, LabelTarget]] = MappingProxyType(
    {
        "door": LabelTarget("opening", "door", 900, 2200, 0, "left"),
        "double_door": LabelTarget("opening", "door", None, 2200, 0, "double"),
        "window": LabelTarget("opening", "window", 1200, 1400, 900, "sliding"),
        "table": _furniture("table"),
        "chair": _furniture("chair"),
        "bed": _furniture("bed"),
        "wardrobe": _furniture("wardrobe"),
        "kitchen_cabinet": _furniture("kitchenCabinet"),
        "sanitary_fixture": _furniture("sanitaryFixture"),
        "stair": _furniture("stair"),
        "other": _furniture("other"),
        "washing_machine": _furniture("washingMachine"),
        "shower": _furniture("shower"),
    }
)
