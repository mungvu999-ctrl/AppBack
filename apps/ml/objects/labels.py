"""Nhãn artifact và bảng nhãn theo model (B5-03 khối [1] "Nhãn", [6] "Model").

`ARTIFACT_LABELS` là mọi nhãn hợp lệ trong `objects.json`. Model ghim `yolov8n`/`yolov8s`
là YOLO COCO gốc (chưa huấn luyện lại): đầu ra của chúng là chỉ số lớp COCO, không phải
`ObjectLabel` — `COCO_LABELS` dịch **thẳng tại suy luận**, không sinh nhãn miền.

Lệch khỏi prompt: `COCO_TO_LABEL` khoá bằng tên lớp COCO (`"chair"`, …), không theo chỉ
số; bảng chỉ số → tên cho 6 lớp mượn của B5-03 khối [6] đứng ở đây rồi tra `COCO_TO_LABEL`.
Model miền N lớp đọc N nhãn đầu của `ARTIFACT_LABELS` (lớp mới luôn nối cuối): v3 11 lớp,
v4 13 lớp (thêm `washing_machine`, `shower`).
"""

from collections.abc import Sequence
from typing import Any, Final

from packages.ml_contracts.labels import COCO_TO_LABEL, DETECTION_LABELS, OPENING_BLIND_PINNED, DetectionLabel
from packages.ml_contracts.payloads import ModelRef

ARTIFACT_LABELS: Final[tuple[DetectionLabel, ...]] = DETECTION_LABELS

_COCO_CLASS_NAMES: Final[dict[int, str]] = {
    56: "chair",
    57: "couch",
    59: "bed",
    60: "dining table",
    61: "toilet",
    71: "sink",
}
"""Chỉ số lớp COCO gốc → tên lớp, chỉ 6 lớp bảng khối [6] giữ lại; còn lại bỏ (`None`)."""

COCO_LABELS: Final[tuple[DetectionLabel | None, ...]] = tuple(
    COCO_TO_LABEL.get(_COCO_CLASS_NAMES.get(i, "")) for i in range(80)
)

PINNED_COCO_MODELS: Final = OPENING_BLIND_PINNED
"""Bí danh giữ tương thích; nguồn là `packages.ml_contracts.labels.OPENING_BLIND_PINNED`."""

DISABLED_LABELS: Final = frozenset({"stair"})
"""Nhãn bước pipeline bỏ hẳn: sản phẩm chỉ làm căn hộ một tầng, còn `stair` (F1 ≈ 0,70) bắn nhầm
sofa (B2 vật sót 2026-10-28). Đánh giá model vẫn giữ để số đo so được với lúc train."""


def class_count(session: Any) -> int | None:
    """Số lớp của phiên ONNX YOLO kiểu ultralytics (đầu ra `(1, 4+N, A)`); hình dạng lạ → `None`
    (detector tự từ chối khi dựng)."""
    outputs = session.get_outputs()
    channels = outputs[0].shape[1] if len(outputs) == 1 and len(outputs[0].shape) == 3 else None
    return channels - 4 if isinstance(channels, int) and channels > 4 else None


def labels_for(ref: ModelRef, n_classes: int | None = None) -> tuple[DetectionLabel | None, ...]:
    """Bảng nhãn theo chỉ số lớp của model: COCO gốc cho model ghim, artifact cho model đã huấn luyện.

    Model miền `n_classes` lớp (≤ số nhãn) đọc `n_classes` nhãn đầu: v3 11 lớp không thấy lớp mới;
    vắng → đủ `ARTIFACT_LABELS`.
    """
    if ref.pinned_name in PINNED_COCO_MODELS:
        return COCO_LABELS
    if n_classes is not None and 0 < n_classes < len(ARTIFACT_LABELS):
        return ARTIFACT_LABELS[:n_classes]
    return ARTIFACT_LABELS


def pipeline_labels(ref: ModelRef, n_classes: int | None = None) -> tuple[DetectionLabel | None, ...]:
    """`labels_for` với nhãn `DISABLED_LABELS` đổi thành `None` (detector bỏ lớp `None` lúc decode)."""
    return tuple(None if label in DISABLED_LABELS else label for label in labels_for(ref, n_classes))


def check_labels(labels: Sequence[str | None]) -> None:
    """Mọi nhãn ngoài `ARTIFACT_LABELS` (và khác `None`) là lỗi cấu hình model → `ValueError`.

    Lệch khỏi prompt: prompt đặt luật này ở khối [6] "Model" (gọi trong
    `YoloOnnxDetector.__init__`); để ở `labels.py` để B nhập được mà không lặp lại tập nhãn.
    """
    valid = set(ARTIFACT_LABELS)
    bad = [label for label in labels if label is not None and label not in valid]
    if bad:
        raise ValueError(f"nhãn ngoài ARTIFACT_LABELS: {bad}")
