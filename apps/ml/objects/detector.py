"""Bộ nhận ô mở và đồ đạc chạy qua ONNX YOLO xuất kiểu ultralytics (B5-03 §B, khối [2]).

Lát trang bằng `tiles.tile_windows` (A), decode mỗi lát, bỏ mảnh bị lát cắt khi lát khác đã thấy
trọn vật, NMS, rồi nối hộp vắt qua ranh lát (khối [6], luật nối sửa 2026-10-19). Không vòng Python
theo điểm ảnh hay theo anchor (K28): decode/NMS/ghép hộp là thao tác mảng numpy trên toàn bộ ứng
viên một lượt, vòng Python còn lại chỉ theo lát (vài chục) hay theo phát hiện cuối
(≤ `MAX_DETECTIONS`). Suy luận luôn CPU qua phiên `load_onnx` đã nạp sẵn (M04): module này
không gọi `resolve_device`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, cast

import cv2
import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]  # onnxruntime 1.30 không có py.typed
from numpy.typing import NDArray

from apps.ml.objects.labels import COCO_LABELS, check_labels
from apps.ml.objects.tiles import Window, overlap_for, tile_windows
from apps.ml.runtime.errors import MODEL_FORMAT_UNSUPPORTED, ORT_ERRORS
from packages.messaging.run_cancel import checkpoint
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.artifacts import MASK_MAX_PIXELS, BoxPx, DetectionPx
from packages.ml_contracts.labels import DetectionLabel
from packages.ml_contracts.ports import RgbImage

__all__ = [
    "COCO_CONFIDENCE_MIN",
    "CONFIDENCE_MIN",
    "CONTAIN_MAX",
    "CROSS_AREA_RATIO",
    "CROSS_CONTAIN",
    "CROSS_IOU",
    "CUT_COVER_MIN",
    "CUT_SLIVER_AREA_RATIO",
    "JOIN_SPAN_MIN",
    "LABEL_CONFIDENCE_MIN",
    "MAX_DETECTIONS",
    "MERGE_OVERLAP",
    "NMS_IOU",
    "UPSCALE_FALLBACK",
    "UPSCALE_MAX",
    "UPSCALE_MIN",
    "UPSCALE_SKIP_LABELS",
    "YoloOnnxDetector",
    "decode_tile",
    "label_thresholds",
    "merge_candidates",
    "upscale_factor",
]

CONFIDENCE_MIN: Final = 0.25
COCO_CONFIDENCE_MIN: Final = 0.5
LABEL_CONFIDENCE_MIN: Final[Mapping[str, float]] = MappingProxyType(
    {
        "bed": 0.5,
        "chair": 0.25,
        "door": 0.4,
        "kitchen_cabinet": 0.45,
        "other": 0.4,
        "sanitary_fixture": 0.3,
        "table": 0.55,
        "wardrobe": 0.6,
        "window": 0.4,
        # ponytail: 0,45 tạm cho hai lớp mới của model 13 lớp; chỉnh lại theo F1 val lệch miền (như B2) sau khi train v4
        "washing_machine": 0.45,
        "shower": 0.45,
    }
)
"""Ngưỡng hiển thị riêng từng nhãn của model miền (yolo11m-v3): F1 cao nhất trên 300 trang validation
CubiCasa thu nhỏ x1, x1/1,5, x1/2,2 kèm nén JPEG và phủ màu — mô phỏng bản căn hộ VN vật nhỏ hơn lúc
train (B2 vật sót 2026-10-28, `F:/runs/bench-can-ho/b2/thresholds.json`, `sweep.txt`), làm tròn 0,05,
không chỉnh trên bench. Ngưỡng cũ (giường 0,8, bàn 0,6) chỉnh trên CubiCasa cỡ gốc nên bỏ sót giường
trên bản thật. Nhãn vắng ở đây dùng `CONFIDENCE_MIN`; model COCO dùng `COCO_CONFIDENCE_MIN` cho mọi
nhãn. `stair` tắt ở bước pipeline (`labels.pipeline_labels`), không qua ngưỡng."""
NMS_IOU: Final = 0.5
MERGE_OVERLAP: Final = 0.7
CUT_COVER_MIN: Final = 0.7
CUT_SLIVER_AREA_RATIO: Final = 2.0
"""Mảnh chạm mép trong bị bỏ khi một hộp cùng nhãn (kể cả hộp cũng chạm mép, cùng lát hay khác lát)
lớn ≥ chừng này lần, phủ ≥ `CUT_COVER_MIN` và điểm ≥ mảnh: vật rộng hơn độ chồng lát không lát nào
thấy trọn, mảnh mỏng còn sót qua NMS vì IoU với hộp lớn chỉ ~3% (BUG-IT-002, CubiCasa G5)."""
JOIN_SPAN_MIN: Final = 0.5
MAX_DETECTIONS: Final = 2000

DOOR_TRAIN_PX: Final = 70.0
"""Cạnh dài hộp cửa (px) ở cỡ vật lúc train: hệ số phóng = `DOOR_TRAIN_PX / trung vị cạnh dài cửa`.

Bước này chạy song song với tường và đọc kích thước nên chưa có mm/px; cỡ cửa của chính lượt gốc
thay cho nó. Đo 2026-10-28 trên 11 bản căn hộ có tỉ lệ: `(mm/px ÷ 11) x cửa_px` trung vị 69,5
(61 đến 77), tức hệ số theo cửa bám hệ số theo mm/px trong ±12%."""
UPSCALE_MIN: Final = 1.2
UPSCALE_MAX: Final = 4.0
UPSCALE_FALLBACK: Final = 1.5
"""Lượt phóng thêm: hệ số kẹp `[1, UPSCALE_MAX]`, dưới `UPSCALE_MIN` thì bỏ lượt; trang có < 2 cửa
(không ước được cỡ) dùng `UPSCALE_FALLBACK` (vật trên bản căn hộ VN nhỏ hơn lúc train ~1,5 lần)."""
CONTAIN_MAX: Final = 0.6
"""Hộp chỉ lượt phóng mới thấy bị bỏ khi ≥ chừng này diện tích nằm trong một hộp lượt gốc (hộp con
lồng trong sofa/giường): 6 bản căn hộ chấm tay, bỏ 7 hộp sai, không bỏ hộp đúng nào (2026-10-28)."""
UPSCALE_SKIP_LABELS: Final = frozenset({"other"})
"""Nhãn lượt phóng không được thêm: trên 14 bản bench (đáp án mù), 18 hộp `other` mới chỉ 5 đúng,
8 hộp sai nằm trên nền màu đậm ch-07; bỏ chúng đưa độ đúng hộp mới 61% → 71% (2026-10-28)."""
_NEW_IOU_MAX: Final = 0.3
CROSS_IOU: Final = 0.6
CROSS_CONTAIN: Final = 0.85
CROSS_AREA_RATIO: Final = 1.5
"""Khử trùng khác nhãn (`_cross_label_dedupe`): IoU ≥ 0,6, hoặc hộp nhỏ nằm ≥ 85 % trong hộp lớn mà diện tích
chênh < 1,5 lần → cùng một vật bị hai lớp bắn (giường + sofa, tủ bếp + bàn), giữ hộp điểm cao hơn."""
_OPENING_LABELS: Final = frozenset({"door", "double_door", "window"})
_ALLOWED_OVERLAP: Final = frozenset(
    {
        frozenset({"chair", "table"}),
        frozenset({"sanitary_fixture", "shower"}),
        frozenset({"table", "bed"}),
        frozenset({"sanitary_fixture", "kitchen_cabinet"}),
    }
)
"""Cặp nhãn chồng nhau thật: ghế quanh bàn, bồn trong buồng tắm, tủ đầu giường, chậu trên mặt bếp."""

_PAD_VALUE: Final = 114
_MIN_INPUT_PX: Final = 32
_MIN_BOX_SIDE_PX: Final = 2.0
_EDGE_TOUCH_PX: Final = 2.0
_EDGE_EPS: Final = 1e-6
"""Lát của lượt phóng đổi về toạ độ gốc là số thực: mép trang chỉ lệch sai số làm tròn, không phải mép cắt."""

Labels = tuple[DetectionLabel | None, ...]


def _unsupported() -> PermanentError:
    """Lỗi chung cho mọi model/lỗi chạy không khớp hợp đồng (không tiết lộ chi tiết)."""
    return PermanentError(MODEL_FORMAT_UNSUPPORTED)


@dataclass(frozen=True, slots=True)
class _Candidates:
    """Ứng viên sau decode, theo cột: `boxes (n,4)` x1y1x2y2, `scores (n,)`, `classes (n,)`.

    `tiles (n,4)` là phần thật `x, y, w, h` của lát nguồn mỗi ứng viên — giữ để bước nối
    hộp bị lát cắt biết "khác lát" và "mép trong" mà không cần vòng Python theo ứng viên.
    """

    boxes: NDArray[np.float64]
    scores: NDArray[np.float64]
    classes: NDArray[np.int64]
    tiles: NDArray[np.float64]

    def __len__(self) -> int:
        """Số ứng viên (bằng số hàng của `boxes`)."""
        return int(self.boxes.shape[0])


@dataclass(frozen=True, slots=True)
class _Detections:
    """Phát hiện cuối sau nối/NMS: chỉ còn hộp/điểm/nhãn, không còn lát nguồn."""

    boxes: NDArray[np.float64]
    scores: NDArray[np.float64]
    classes: NDArray[np.int64]

    def __len__(self) -> int:
        """Số phát hiện."""
        return int(self.boxes.shape[0])


_EMPTY_CANDIDATES: Final = _Candidates(
    boxes=np.zeros((0, 4), dtype=np.float64),
    scores=np.zeros(0, dtype=np.float64),
    classes=np.zeros(0, dtype=np.int64),
    tiles=np.zeros((0, 4), dtype=np.float64),
)


def _checked_input_shape(inputs: Sequence[Any]) -> int:
    """Đúng 1 đầu vào `(1, 3, S, S)`, `S` số nguyên tĩnh ≥ 32 chia hết 32 (khối [2])."""
    if len(inputs) != 1:
        raise _unsupported()
    shape = list(inputs[0].shape)
    if len(shape) != 4 or shape[0] != 1 or shape[1] != 3:
        raise _unsupported()
    size = shape[2]
    if not isinstance(size, int) or shape[3] != size or size < _MIN_INPUT_PX or size % 32 != 0:
        raise _unsupported()
    return size


def _checked_output_shape(outputs: Sequence[Any], *, expected_channels: int) -> None:
    """Đúng 1 đầu ra `(1, 4+len(labels), A)`, `A` tĩnh hay động đều nhận (khối [2])."""
    if len(outputs) != 1:
        raise _unsupported()
    shape = list(outputs[0].shape)
    if len(shape) != 3 or shape[0] != 1 or shape[1] != expected_channels:
        raise _unsupported()


def _preprocess_tile(image: RgbImage, window: Window, input_px: int) -> NDArray[np.float32]:
    """Cắt phần thật của lát, đệm 114 tới `input_px²`, `float32/255` HWC→NCHW (khối [6], không co giãn)."""
    tile = np.full((input_px, input_px, 3), _PAD_VALUE, dtype=np.uint8)
    tile[: window.height, : window.width] = image[
        window.y : window.y + window.height, window.x : window.x + window.width
    ]
    chw = np.ascontiguousarray(tile.transpose(2, 0, 1).astype(np.float32) / 255.0)
    return chw[np.newaxis, ...]


def label_thresholds(labels: Labels, min_confidence: float | Mapping[str, float] | None = None) -> NDArray[np.float64]:
    """Ngưỡng điểm theo chỉ số lớp của `labels`: số → mọi nhãn; dict → đè lên bảng mặc định.

    Mặc định: `COCO_CONFIDENCE_MIN` cho mọi nhãn khi `labels is COCO_LABELS`, khác thì
    `LABEL_CONFIDENCE_MIN` (vắng → `CONFIDENCE_MIN`). Đánh giá mAP truyền ngưỡng thấp (0,05)
    để tính trên gần trọn đường precision-recall. Ngưỡng ngoài `[0, 1]` → `ValueError`.
    """
    coco = labels is COCO_LABELS
    default = COCO_CONFIDENCE_MIN if coco else CONFIDENCE_MIN
    if isinstance(min_confidence, (int, float)):
        table: Mapping[str, float] = {}
        default = float(min_confidence)
    else:
        table = {**({} if coco else LABEL_CONFIDENCE_MIN), **(min_confidence or {})}
    values = np.array([table.get(label, default) if label is not None else 1.0 for label in labels], dtype=np.float64)
    if not np.all((values >= 0.0) & (values <= 1.0)):
        raise ValueError(f"ngưỡng điểm ngoài [0, 1]: {min_confidence}")
    return values


def decode_tile(
    output: NDArray[np.float32],
    window: Window,
    labels: Labels,
    input_px: int,
    thresholds: NDArray[np.float64] | None = None,
) -> _Candidates:
    """`(1, 4+nc, A)` ultralytics → ứng viên trang, mảng hoá toàn bộ anchor cùng lúc (khối [6] bước 1-2, K28).

    `score = max` lớp, `cls = argmax` (hoà chỉ số nhỏ, `np.argmax` đã vậy); ngưỡng theo lớp
    `thresholds` (vắng → `label_thresholds(labels)` mặc định). Lớp `None` của model miền (nhãn tắt) bị che
    trước argmax; lớp `None` của COCO,
    NaN/vô cực, cạnh < 2px sau kẹp vào phần thật của lát đều bị bỏ (mặt nạ boolean, không vòng anchor).

    `input_px` giữ trong chữ ký vì khối [5] khai vậy cho mọi hàm hậu xử lý của bước, nhưng
    không dùng ở đây: vùng đệm bị trừ bằng `window` (phần thật của lát, luôn ≤ `input_px`),
    không phải bằng `input_px` trực tiếp — hàm này không cần biết kích cỡ đệm.
    """
    arr = output[0].T.astype(np.float64)
    class_scores = arr[:, 4:]
    none_mask = np.array([label is None for label in labels], dtype=bool)
    if tuple(labels) != COCO_LABELS:
        # Lớp `None` của model miền chỉ là `DISABLED_LABELS` (stair): che trước argmax để anchor sofa mà stair
        # nhỉnh điểm vẫn ra lớp kế tiếp, không mất hộp. Lớp `None` của COCO giữ cách cũ (bỏ sau argmax).
        class_scores = np.where(none_mask[None, :], -np.inf, class_scores)
    classes = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(classes.shape[0]), classes]
    per_class = label_thresholds(labels) if thresholds is None else thresholds
    keep = (scores >= per_class[classes]) & ~none_mask[classes]
    if not np.any(keep):
        return _EMPTY_CANDIDATES
    cx, cy, w, h = arr[keep, 0], arr[keep, 1], arr[keep, 2], arr[keep, 3]
    x1, y1 = cx - w / 2 + window.x, cy - h / 2 + window.y
    x2, y2 = cx + w / 2 + window.x, cy + h / 2 + window.y
    finite = np.isfinite(x1) & np.isfinite(y1) & np.isfinite(x2) & np.isfinite(y2)
    inner_x1, inner_y1 = float(window.x), float(window.y)
    inner_x2, inner_y2 = float(window.x + window.width), float(window.y + window.height)
    x1c, x2c = np.clip(x1, inner_x1, inner_x2), np.clip(x2, inner_x1, inner_x2)
    y1c, y2c = np.clip(y1, inner_y1, inner_y2), np.clip(y2, inner_y1, inner_y2)
    valid = finite & (x2c - x1c >= _MIN_BOX_SIDE_PX) & (y2c - y1c >= _MIN_BOX_SIDE_PX)
    if not np.any(valid):
        return _EMPTY_CANDIDATES
    boxes = np.stack([x1c, y1c, x2c, y2c], axis=1)[valid]
    n = boxes.shape[0]
    tile_row = np.array([window.x, window.y, window.width, window.height], dtype=np.float64)
    return _Candidates(
        boxes=boxes,
        scores=scores[keep][valid],
        classes=classes[keep][valid].astype(np.int64),
        tiles=np.tile(tile_row, (n, 1)),
    )


def _concat_candidates(parts: Sequence[_Candidates]) -> _Candidates:
    """Nối ứng viên mọi lát bằng `np.concatenate` (khối [6]: sinh lát tuần tự, gộp mảng)."""
    non_empty = [part for part in parts if len(part) > 0]
    if not non_empty:
        return _EMPTY_CANDIDATES
    return _Candidates(
        boxes=np.concatenate([p.boxes for p in non_empty]),
        scores=np.concatenate([p.scores for p in non_empty]),
        classes=np.concatenate([p.classes for p in non_empty]),
        tiles=np.concatenate([p.tiles for p in non_empty]),
    )


def _iou_against(box: NDArray[np.float64], boxes: NDArray[np.float64]) -> NDArray[np.float64]:
    """IoU của một hộp so với nhiều hộp, vector hoá (dùng cho NMS)."""
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (box[2] - box[0]) * (box[3] - box[1])
    area_b = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    union = area_a + area_b - inter
    return cast(NDArray[np.float64], np.where(union > 0, inter / union, 0.0))


def _nms(candidates: _Candidates) -> _Candidates:
    """NMS tham lam theo nhãn (khối [6] bước 3): `np.lexsort` một lần; vòng chỉ lặp trên hộp còn giữ.

    Không vòng lồng: mỗi bước so IoU vector hoá với các hộp **cùng nhãn đã giữ**, không so
    lại toàn bộ cặp (n, n).
    """
    n = len(candidates)
    if n == 0:
        return candidates
    x1 = candidates.boxes[:, 0]
    y1 = candidates.boxes[:, 1]
    order = np.lexsort((y1, x1, candidates.classes, -candidates.scores))
    keep_mask = np.zeros(n, dtype=bool)
    kept_by_label: dict[int, NDArray[np.float64]] = {}
    for i in order:
        label = int(candidates.classes[i])
        box = candidates.boxes[i]
        existing = kept_by_label.get(label)
        if existing is not None and np.any(_iou_against(box, existing) >= NMS_IOU):
            continue
        keep_mask[i] = True
        kept_by_label[label] = box[None, :] if existing is None else np.vstack([existing, box[None, :]])
    idx = np.nonzero(keep_mask)[0]
    return _Candidates(
        boxes=candidates.boxes[idx],
        scores=candidates.scores[idx],
        classes=candidates.classes[idx],
        tiles=candidates.tiles[idx],
    )


def _select(candidates: _Candidates, mask: NDArray[np.bool_]) -> _Candidates:
    """Ứng viên theo mặt nạ boolean (giữ nguyên lát nguồn)."""
    return _Candidates(
        boxes=candidates.boxes[mask],
        scores=candidates.scores[mask],
        classes=candidates.classes[mask],
        tiles=candidates.tiles[mask],
    )


def _pairwise_area_overlap(boxes: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ma trận `(n,n)` giao/diện tích hộp nhỏ hơn của mỗi cặp (một lượt numpy)."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    ix1 = np.maximum(x1[:, None], x1[None, :])
    iy1 = np.maximum(y1[:, None], y1[None, :])
    ix2 = np.minimum(x2[:, None], x2[None, :])
    iy2 = np.minimum(y2[:, None], y2[None, :])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area = (x2 - x1) * (y2 - y1)
    smaller = np.minimum(area[:, None], area[None, :])
    with np.errstate(divide="ignore", invalid="ignore"):
        return cast(NDArray[np.float64], np.where(smaller > 0, inter / smaller, 0.0))


def _interval_iou_matrix(lo: NDArray[np.float64], hi: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ma trận `(n,n)` IoU một chiều của khoảng chiếu `[lo, hi]` (điều kiện (b), trục song song mép cắt)."""
    ilo = np.maximum(lo[:, None], lo[None, :])
    ihi = np.minimum(hi[:, None], hi[None, :])
    inter = np.clip(ihi - ilo, 0, None)
    union = (hi[:, None] - lo[:, None]) + (hi[None, :] - lo[None, :]) - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return cast(NDArray[np.float64], np.where(union > 0, inter / union, 0.0))


def _cut_edge_touches(
    boxes: NDArray[np.float64], tiles: NDArray[np.float64], page_w: int, page_h: int
) -> dict[str, NDArray[np.bool_]]:
    """Mỗi ứng viên có chạm mép trong (không trùng mép trang) của lát mình, vector hoá (điều kiện (b))."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    tile_x, tile_y, tile_w, tile_h = tiles[:, 0], tiles[:, 1], tiles[:, 2], tiles[:, 3]
    right, bottom = tile_x + tile_w, tile_y + tile_h
    return {
        "left": (tile_x > _EDGE_EPS) & (np.abs(x1 - tile_x) <= _EDGE_TOUCH_PX),
        "right": (right < page_w - _EDGE_EPS) & (np.abs(x2 - right) <= _EDGE_TOUCH_PX),
        "top": (tile_y > _EDGE_EPS) & (np.abs(y1 - tile_y) <= _EDGE_TOUCH_PX),
        "bottom": (bottom < page_h - _EDGE_EPS) & (np.abs(y2 - bottom) <= _EDGE_TOUCH_PX),
    }


def _drop_cut_fragments(candidates: _Candidates, *, page_w: int, page_h: int) -> _Candidates:
    """Bỏ mảnh bị lát cắt khi lát khác thấy trọn vật hay hộp lớn hơn hẳn phủ nó — chạy **trước** NMS.

    Mảnh = ứng viên chạm mép trong của lát mình (`_cut_edge_touches`). Mảnh `i` bị bỏ khi có `j`
    cùng nhãn, khác lát, không chạm mép trong, diện tích ≥ `i` và phủ ≥ `CUT_COVER_MIN` diện tích
    `i`. Vật ≤ độ chồng lát (160 px ở 640) luôn có một lát chứa trọn, nên mảnh của nó không còn
    đè (NMS) hay ghép nhầm với hộp trọn. Vật rộng hơn thì mọi hộp phủ đều chạm mép, nên mảnh `i`
    cũng bị bỏ khi có `j` cùng nhãn (lát bất kỳ, chạm mép cũng được) lớn ≥ `CUT_SLIVER_AREA_RATIO`
    lần, phủ ≥ `CUT_COVER_MIN` diện tích `i` và điểm ≥ `i`; tỉ lệ diện tích > 1 nên không cặp nào
    bỏ lẫn nhau. Hai vật riêng chỉ chạm nhau không phủ đủ nên giữ cả hai. Ma trận `(m,m)` theo từng
    nhãn, như NMS.
    """
    touch = _cut_edge_touches(candidates.boxes, candidates.tiles, page_w, page_h)
    cut = touch["left"] | touch["right"] | touch["top"] | touch["bottom"]
    keep = np.ones(len(candidates), dtype=bool)
    for label in np.unique(candidates.classes[cut]):
        idx = np.nonzero(candidates.classes == label)[0]
        boxes, tiles, scores = candidates.boxes[idx], candidates.tiles[idx], candidates.scores[idx]
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        diff_tile = np.any(tiles[:, None, :] != tiles[None, :, :], axis=2)
        overlaps = _pairwise_area_overlap(boxes) >= CUT_COVER_MIN
        whole = diff_tile & overlaps & (area[None, :] >= area[:, None]) & ~cut[idx][None, :]
        larger = overlaps & (area[None, :] >= CUT_SLIVER_AREA_RATIO * area[:, None])
        cover = whole | (larger & (scores[None, :] >= scores[:, None]))
        keep[idx] = ~(cut[idx] & cover.any(axis=1))
    return _select(candidates, keep)


def _cut_pairs(
    boxes: NDArray[np.float64],
    tiles: NDArray[np.float64],
    end_cut: NDArray[np.bool_],
    start_cut: NDArray[np.bool_],
    axis: int,
) -> NDArray[np.bool_]:
    """Ma trận `(m,m)` cặp nối theo trục `axis` (0 = x, 1 = y): hai nửa của một vật vắt qua ranh lát.

    Hàng `i` chạm mép cuối lát mình, cột `j` chạm mép đầu lát mình; lát `j` bắt đầu **bên trong**
    lát `i` (hai lát kề, chồng nhau); hai hộp cùng phủ ≥ `JOIN_SPAN_MIN` dải chồng của hai lát;
    khoảng chiếu trên trục kia IoU ≥ `MERGE_OVERLAP`. Hai hộp ở xa nhau không bao giờ đạt.
    """
    lo, hi = boxes[:, axis], boxes[:, axis + 2]
    start, end = tiles[:, axis], tiles[:, axis] + tiles[:, axis + 2]
    inside = (start[None, :] > start[:, None]) & (start[None, :] < end[:, None])
    strip = end[:, None] - start[None, :]
    span = np.minimum(hi[:, None], hi[None, :]) - np.maximum(lo[:, None], lo[None, :])
    other = 1 - axis
    cross = _interval_iou_matrix(boxes[:, other], boxes[:, other + 2]) >= MERGE_OVERLAP
    return end_cut[:, None] & start_cut[None, :] & inside & (span >= JOIN_SPAN_MIN * strip) & cross


def _connected_components(n: int, edges: NDArray[np.int64]) -> NDArray[np.int64]:
    """Nhãn thành phần liên thông của `n` đỉnh theo `edges (m,2)` — union-find, chỉ lặp trên cạnh thật.

    Bao đóng bắc cầu của các cặp nối (`_join_cut_boxes`): vật dài vắt qua nhiều ranh lát liền
    nhau thành một nhóm. Chỉ một lượt — hộp đã nối không được nối tiếp, nên không còn xâu chuỗi.
    """
    parent = np.arange(n)

    def find(x: int) -> int:
        """Gốc của `x` theo path-halving (nén một nửa đường mỗi bước, không đệ quy)."""
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return int(x)

    for a, b in edges:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(n)])


def _union_groups(candidates: _Candidates, groups: NDArray[np.int64]) -> _Detections:
    """Gộp mỗi nhóm thành một phát hiện: hộp bao, điểm max, nhãn chung (lát nguồn bỏ đi)."""
    _, inverse = np.unique(groups, return_inverse=True)
    g = int(inverse.max()) + 1
    merged_x1 = np.full(g, np.inf)
    merged_y1 = np.full(g, np.inf)
    merged_x2 = np.full(g, -np.inf)
    merged_y2 = np.full(g, -np.inf)
    merged_score = np.full(g, -np.inf)
    merged_label = np.zeros(g, dtype=np.int64)
    np.minimum.at(merged_x1, inverse, candidates.boxes[:, 0])
    np.minimum.at(merged_y1, inverse, candidates.boxes[:, 1])
    np.maximum.at(merged_x2, inverse, candidates.boxes[:, 2])
    np.maximum.at(merged_y2, inverse, candidates.boxes[:, 3])
    np.maximum.at(merged_score, inverse, candidates.scores)
    merged_label[inverse] = candidates.classes
    boxes = np.stack([merged_x1, merged_y1, merged_x2, merged_y2], axis=1)
    return _Detections(boxes=boxes, scores=merged_score, classes=merged_label)


def _join_cut_boxes(candidates: _Candidates, *, page_w: int, page_h: int) -> _Detections:
    """Nối hai nửa của vật dài hơn độ chồng lát (`_cut_pairs` ngang + dọc), theo từng nhãn, một lượt.

    Thay luật cũ (khối [6] bước 4): luật cũ nối cả hai hộp chạm mép ở hai lát xa nhau, rồi điều
    kiện "chứa" nuốt tiếp các hộp nằm giữa — dãy cửa sổ thành một hộp dài (đo 2026-10-19).
    """
    if len(candidates) == 0:
        return _Detections(boxes=candidates.boxes, scores=candidates.scores, classes=candidates.classes)
    touch = _cut_edge_touches(candidates.boxes, candidates.tiles, page_w, page_h)
    edges = [np.zeros((0, 2), dtype=np.int64)]
    for label in np.unique(candidates.classes):
        idx = np.nonzero(candidates.classes == label)[0]
        boxes, tiles = candidates.boxes[idx], candidates.tiles[idx]
        horizontal = _cut_pairs(boxes, tiles, touch["right"][idx], touch["left"][idx], 0)
        vertical = _cut_pairs(boxes, tiles, touch["bottom"][idx], touch["top"][idx], 1)
        edges.append(idx[np.argwhere(horizontal | vertical)])
    all_edges = np.concatenate(edges)
    if all_edges.shape[0] == 0:
        return _Detections(boxes=candidates.boxes, scores=candidates.scores, classes=candidates.classes)
    return _union_groups(candidates, _connected_components(len(candidates), all_edges))


def _cap_and_sort(detections: _Detections) -> _Detections:
    """Giữ tối đa `MAX_DETECTIONS` theo điểm, sắp `(y1, x1, nhãn)` — `np.argpartition`/`np.lexsort` (bước 5)."""
    n = len(detections)
    if n > MAX_DETECTIONS:
        top = np.argpartition(-detections.scores, MAX_DETECTIONS - 1)[:MAX_DETECTIONS]
        detections = _Detections(
            boxes=detections.boxes[top], scores=detections.scores[top], classes=detections.classes[top]
        )
    order = np.lexsort((detections.classes, detections.boxes[:, 0], detections.boxes[:, 1]))
    return _Detections(
        boxes=detections.boxes[order], scores=detections.scores[order], classes=detections.classes[order]
    )


def merge_candidates(candidates: _Candidates, *, page_w: int, page_h: int, labels: Labels | None = None) -> _Detections:
    """Bỏ mảnh bị lát cắt, NMS theo nhãn, nối vật vắt ranh lát, giữ tối đa `MAX_DETECTIONS` theo điểm.

    Bỏ mảnh **trước** NMS là cố ý: mảnh điểm cao hơn hộp trọn sẽ khiến NMS bỏ hộp trọn (đo trên
    400 trang test và 400 trang validation CubiCasa, 2026-10-19).

    Lệch khỏi prompt: khổ trang (`page_w`, `page_h`) truyền tường minh (kích thước ảnh thật ở
    `detect`) thay vì suy từ hợp các lát, để không phụ thuộc việc lát cuối có còn trong ứng
    viên hay không. `labels` có → thêm lượt khử trùng khác nhãn `_cross_label_dedupe` sau NMS.
    """
    kept = _nms(_drop_cut_fragments(candidates, page_w=page_w, page_h=page_h))
    if labels is not None:
        kept = _cross_label_dedupe(kept, labels)
    return _cap_and_sort(_join_cut_boxes(kept, page_w=page_w, page_h=page_h))


def _overlap_allowed(labels: Labels) -> NDArray[np.bool_]:
    """Ma trận `(nc, nc)` cặp nhãn được chồng nhau (`_ALLOWED_OVERLAP`, mọi cặp có ô mở, cùng nhãn)."""
    names = list(labels)
    allowed = np.eye(len(names), dtype=bool)
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if a in _OPENING_LABELS or b in _OPENING_LABELS or frozenset((a, b)) in _ALLOWED_OVERLAP:
                allowed[i, j] = True
    return allowed


def _cross_label_dedupe(candidates: _Candidates, labels: Labels) -> _Candidates:
    """Hai hộp khác nhãn cùng một vật → giữ hộp điểm cao hơn (sau NMS theo nhãn).

    "Cùng vật" = IoU ≥ `CROSS_IOU`, hoặc hộp nhỏ nằm ≥ `CROSS_CONTAIN` trong hộp lớn mà diện tích chênh <
    `CROSS_AREA_RATIO` lần. Cặp được chồng thật (`_overlap_allowed`: ghế-bàn, tủ đầu giường, chậu trên mặt bếp,
    ô mở) không xét. Tham lam theo điểm giảm dần như `_nms`; vòng chỉ lặp trên hộp đã giữ.
    """
    n = len(candidates)
    if n < 2:
        return candidates
    allowed = _overlap_allowed(labels)
    boxes = candidates.boxes
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    for i in np.lexsort((boxes[:, 1], boxes[:, 0], -candidates.scores)):
        if keep:
            kept = np.asarray(keep)
            check = ~allowed[candidates.classes[i], candidates.classes[kept]]
            iou = _iou_against(boxes[i], boxes[kept])
            small = np.minimum(areas[i], areas[kept])
            ratio = np.maximum(areas[i], areas[kept]) / np.maximum(small, 1e-9)
            inside = iou * (areas[i] + areas[kept]) / (1.0 + iou) / np.maximum(small, 1e-9)
            same = (iou >= CROSS_IOU) | ((inside >= CROSS_CONTAIN) & (ratio < CROSS_AREA_RATIO))
            if np.any(check & same):
                continue
        keep.append(int(i))
    return _select(candidates, np.isin(np.arange(n), keep))


def _finalize(detections: _Detections, labels: Labels) -> tuple[DetectionPx, ...]:
    """Làm tròn toạ độ 2 chữ số, điểm 4 chữ số `[0, 1]`; vòng Python chỉ để dựng `DetectionPx` (≤ `MAX_DETECTIONS`)."""
    boxes = np.round(detections.boxes, 2)
    confidences = np.round(np.clip(detections.scores, 0.0, 1.0), 4)
    results: list[DetectionPx] = []
    for i in range(len(detections)):
        label = cast(DetectionLabel, labels[int(detections.classes[i])])
        box = BoxPx(
            x_min=float(boxes[i, 0]), y_min=float(boxes[i, 1]), x_max=float(boxes[i, 2]), y_max=float(boxes[i, 3])
        )
        results.append(DetectionPx(label=label, box=box, confidence=float(confidences[i])))
    return tuple(results)


def upscale_factor(door_long_sides: NDArray[np.float64], *, page_pixels: int) -> float:
    """Hệ số phóng của lượt thêm: `DOOR_TRAIN_PX / trung vị cạnh dài cửa`, kẹp `[1, UPSCALE_MAX]`.

    < 2 cửa → `UPSCALE_FALLBACK`. Trang phóng không vượt `MASK_MAX_PIXELS` (trần khổ trang của hợp
    đồng), để lượt phóng không ngốn bộ nhớ hơn trang lớn nhất được nhận. Kết quả < `UPSCALE_MIN`
    nghĩa là bỏ lượt phóng (người gọi quyết).
    """
    if door_long_sides.shape[0] < 2:
        factor = UPSCALE_FALLBACK
    else:
        factor = float(np.clip(DOOR_TRAIN_PX / float(np.median(door_long_sides)), 1.0, UPSCALE_MAX))
    return min(factor, float(np.sqrt(MASK_MAX_PIXELS / page_pixels)))


def _to_page(candidates: _Candidates, sx: float, sy: float, *, page_w: int, page_h: int) -> _Candidates:
    """Ứng viên trên trang phóng `(sx, sy)` → toạ độ trang gốc: hộp **và** lát nguồn cùng chia, kẹp vào trang.

    Lát cũng đổi để `merge_candidates` vẫn nhận ra mảnh bị lát của lượt phóng cắt (mép trong).
    """
    scale = np.array([sx, sy, sx, sy], dtype=np.float64)
    limit = np.array([page_w, page_h, page_w, page_h], dtype=np.float64)
    boxes = np.minimum(candidates.boxes / scale, limit)
    tiles = candidates.tiles / scale
    return _Candidates(boxes=boxes, scores=candidates.scores, classes=candidates.classes, tiles=tiles)


def _area_inside(boxes: NDArray[np.float64], others: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ma trận `(n, m)`: phần diện tích hộp `boxes[i]` nằm trong `others[j]`."""
    a, b = boxes[:, None, :], others[None, :, :]
    ix = np.clip(np.minimum(a[..., 2], b[..., 2]) - np.maximum(a[..., 0], b[..., 0]), 0, None)
    iy = np.clip(np.minimum(a[..., 3], b[..., 3]) - np.maximum(a[..., 1], b[..., 1]), 0, None)
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return cast(NDArray[np.float64], ix * iy / area[:, None])


def _add_new(native: _Detections, merged: _Detections, skip_classes: NDArray[np.int64]) -> _Detections:
    """Hộp gốc giữ nguyên hết, cộng hộp của lượt gộp mọi cỡ mà lượt gốc không có.

    "Không có" = IoU < 0,3 với mọi hộp gốc (bất kể nhãn) và < `CONTAIN_MAX` diện tích nằm trong
    một hộp gốc. Hộp gộp mà NMS đã thay hộp gốc bằng bản phóng tương đương (IoU ≥ 0,3) bị coi là
    trùng, nên hộp gốc không bao giờ mất hay đổi. Hộp mới thuộc lớp `skip_classes` bị bỏ.
    """
    fresh = ~np.isin(merged.classes, skip_classes)
    if len(native) > 0 and len(merged) > 0:
        iou = np.stack([_iou_against(box, native.boxes) for box in merged.boxes])
        fresh &= ~np.any((iou >= _NEW_IOU_MAX) | (_area_inside(merged.boxes, native.boxes) >= CONTAIN_MAX), axis=1)
    return _cap_and_sort(
        _Detections(
            boxes=np.concatenate([native.boxes, merged.boxes[fresh]]),
            scores=np.concatenate([native.scores, merged.scores[fresh]]),
            classes=np.concatenate([native.classes, merged.classes[fresh]]),
        )
    )


class YoloOnnxDetector:
    """`ObjectDetector` (B5-01) chạy trên phiên ONNX YOLO xuất kiểu ultralytics (khối [1])."""

    def __init__(
        self,
        session: ort.InferenceSession,
        labels: Labels,
        *,
        min_confidence: float | Mapping[str, float] | None = None,
        multiscale: bool = False,
    ) -> None:
        """Kiểm hình dạng và nhãn ngay lúc dựng (K12, M02); sai → `PermanentError(MODEL_FORMAT_UNSUPPORTED)`.

        Không đọc `names`/metadata khác của ONNX (khối [2]); nhãn ngoài `ARTIFACT_LABELS`
        và khác `None` → `ValueError` qua `check_labels`. `min_confidence`: xem `label_thresholds`
        (vắng → ngưỡng hiển thị mặc định; đánh giá mAP truyền 0,05). `multiscale`: thêm lượt phóng
        (`detect`); bước pipeline bật, đánh giá model tắt để số đo so được với lúc train.
        """
        check_labels(labels)
        self._thresholds = label_thresholds(labels, min_confidence)
        inputs, outputs = session.get_inputs(), session.get_outputs()
        self.input_px = _checked_input_shape(inputs)
        _checked_output_shape(outputs, expected_channels=4 + len(labels))
        self._session = session
        self._labels = labels
        self._input_name = inputs[0].name
        self._output_name = outputs[0].name
        self._door = labels.index("door") if multiscale and "door" in labels else None
        self._skip = np.array([i for i, label in enumerate(labels) if label in UPSCALE_SKIP_LABELS], dtype=np.int64)

    def _candidates(self, image: RgbImage) -> _Candidates:
        """Ứng viên mọi lát của một ảnh (toạ độ ảnh ấy): lát tuần tự → suy luận CPU → decode."""
        height, width = image.shape[:2]
        windows = tile_windows(width, height, tile_px=self.input_px, overlap_px=overlap_for(self.input_px))
        per_tile: list[_Candidates] = []
        for window in windows:
            checkpoint()  # K-24: lượt bị huỷ thì dừng giữa hai lát, không chạy hết trang
            tile_input = _preprocess_tile(image, window, self.input_px)
            try:
                output = self._session.run([self._output_name], {self._input_name: tile_input})[0]
            except ORT_ERRORS as exc:
                raise _unsupported() from exc
            per_tile.append(decode_tile(output, window, self._labels, self.input_px, self._thresholds))
        return _concat_candidates(per_tile)

    def detect(self, image: RgbImage) -> tuple[DetectionPx, ...]:
        """Ảnh trang → phát hiện theo pixel trang: lượt gốc, rồi (nếu bật) một lượt phóng gộp chung.

        Lượt phóng (vật trên bản vẽ thật nhỏ hơn lúc train): phóng trang `upscale_factor` lần theo cỡ
        cửa của lượt gốc, đổi ứng viên về toạ độ gốc rồi cho **chung** `merge_candidates` với ứng viên
        gốc — mảnh bị lát phóng cắt rơi ở bước bỏ mảnh/NMS như mảnh gốc. Chỉ cộng hộp mới (`_add_new`).
        Model không có nhãn `door` (COCO) không phóng: không ước được cỡ vật.

        Lỗi `onnxruntime` lúc `session.run` (`ORT_ERRORS`) → `PermanentError(MODEL_FORMAT_UNSUPPORTED)`,
        như `apps/ml/walls/segformer.py:93` (M04: không `resolve_device`, luôn CPU qua phiên đã nạp).
        """
        height, width = image.shape[:2]
        native_candidates = self._candidates(image)
        native = merge_candidates(native_candidates, page_w=width, page_h=height, labels=self._labels)
        if self._door is None:
            return _finalize(native, self._labels)
        doors = native.boxes[native.classes == self._door]
        factor = upscale_factor(
            np.maximum(doors[:, 2] - doors[:, 0], doors[:, 3] - doors[:, 1]), page_pixels=width * height
        )
        if factor < UPSCALE_MIN:
            return _finalize(native, self._labels)
        up_w, up_h = round(width * factor), round(height * factor)
        upscaled = np.asarray(cv2.resize(image, (up_w, up_h), interpolation=cv2.INTER_CUBIC), dtype=np.uint8)
        mapped = _to_page(self._candidates(upscaled), up_w / width, up_h / height, page_w=width, page_h=height)
        merged = merge_candidates(
            _concat_candidates([native_candidates, mapped]), page_w=width, page_h=height, labels=self._labels
        )
        # ponytail: chưa bỏ hộp mới ngoài căn hộ (sơ đồ vị trí, khung tên) — bước này chạy song song với
        # tường nên chưa có mặt nạ trong căn hộ; lọc ở spatialDataBuild khi cần.
        return _finalize(_add_new(native, merged, self._skip), self._labels)
