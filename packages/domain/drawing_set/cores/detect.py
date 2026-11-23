"""Từ phát hiện thô tới ô thang giữ lại (K-32e, từ `cores.combine`/`dropped`/`apply_mode` chế độ geom|confirm).

Bộ dò ảnh (`stairs_geom`, `stairs_filters`, `stairs_curved`, YOLO) chạy ngoài gói này và chỉ giao `Detection`
thuần. Chế độ "both"/"yolo" của nhân (nguồn thứ hai làm tăng hoặc thay ô) KHÔNG chuyển: đo trên 20 bộ cho thấy
"both" thêm một lõi sai và mất 1-2 thang thật, nhân chọn "confirm" làm mặc định (cores_report, y2-stairs).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from packages.domain.drawing_set.cores.evidence import evidence_near
from packages.domain.drawing_set.cores.geometry import centre, overlap
from packages.domain.drawing_set.cores.types import Box, PlanText, Well

MIN_TREADS: Final = 5  # dưới 5 bậc phần lớn là nhiễu (stairs_geom_report)
CURVED_MIN: Final = 5  # quạt cong n = 4 là nhiễu
YOLO_THR: Final = 0.3  # hộp mô hình dưới điểm này không tính là hỗ trợ
YOLO_OVL: Final = 0.3  # hộp mô hình trùng ô khi giao ≥ mức này của hộp nhỏ
ACTIVE_NEG: Final = ("grid", "hatch", "table")  # "continues"/"wide" làm rớt thang thật nên bị bỏ qua


@dataclass(frozen=True)
class Detection:
    """Một thang dò được trên ảnh mặt bằng, đã gộp các vế (`merge_flights`).

    `reason` là chuỗi lý do của bộ lọc: các mục cách nhau ";", mỗi mục dạng "grid: ..." ("ok" khi không có).
    Hộp cùng đơn vị với hộp toà nhà truyền vào `combine`.
    """

    box: Box
    n_treads: int
    kind: str = "stair"  # "stair" | "curved"
    angle_deg: float | None = None
    pitch_mm: float | None = None
    reason: str = "ok"


@dataclass(frozen=True)
class YoloBox:
    """Hộp của lớp mô hình thang (điểm 0..1)."""

    box: Box
    score: float


def is_dropped(det: Detection) -> bool:
    """Lý do bộ lọc có một trong `ACTIVE_NEG` (lưới/gạch/bảng) thì bỏ ô; "continues"/"wide" không bỏ."""
    return any(r.split(":")[0].strip() in ACTIVE_NEG for r in (det.reason or "ok").split(";"))


def combine(straight: Sequence[Detection], curved: Sequence[Detection], building: Box | None = None) -> list[Detection]:
    """Ô giữ lại để nối tầng: thang thẳng ≥ `MIN_TREADS` không bị lọc + quạt cong ≥ `CURVED_MIN` không đè lên ô thẳng
    nào (đè = cùng giếng thang); tâm phải nằm trong `building` nếu có. Quạt cong thành kind "curved", không góc/bước."""

    def inside(d: Detection) -> bool:
        cx, cy = centre(d.box)
        return building is None or (building[0] <= cx <= building[2] and building[1] <= cy <= building[3])

    keep = [d for d in straight if d.n_treads >= MIN_TREADS and not is_dropped(d) and inside(d)]
    for c in curved:
        if c.n_treads >= CURVED_MIN and inside(c) and not any(overlap(c.box, k.box) > 0 for k in keep):
            keep.append(Detection(c.box, c.n_treads, "curved"))
    return keep


def _yolo_best(box: Box, yolo: Sequence[YoloBox]) -> float:
    """Điểm cao nhất của hộp mô hình trùng `box` (≥ `YOLO_OVL` của hộp nhỏ), 0 khi không có."""
    return max((y.score for y in yolo if overlap(box, y.box) >= YOLO_OVL), default=0.0)


def build_wells(
    detections: Sequence[Detection], texts: Sequence[PlanText] = (), yolo: Sequence[YoloBox] | None = None
) -> tuple[Well, ...]:
    """Ô thang cho `CorePlan`: bằng chứng chữ quanh từng ô; `yolo_ok` đánh dấu có hộp mô hình ≥ `YOLO_THR` trùng ô
    (chế độ "confirm": không bỏ ô, không thêm ô). `yolo` None = mặt bằng không có lớp mô hình → `yolo_ok` None.

    Hộp `detections` phải ở mm (đổi px → mm ở nơi gọi, nhân: `px * mm_per_px`).
    """
    return tuple(
        Well(
            box=d.box,
            kind="curved" if d.kind == "curved" else "stair",
            angle_deg=d.angle_deg,
            pitch_mm=d.pitch_mm,
            evidence=evidence_near(texts, d.box),
            yolo_ok=None if yolo is None else _yolo_best(d.box, yolo) >= YOLO_THR,
        )
        for d in detections
    )
