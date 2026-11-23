"""Bộ tách tường SegFormer chạy qua ONNX: ghép lát và `WallSegmenter` (B5-02 §C, khối [2]).

`stitch_mask` ở đây, không ở `spec.py`, vì nó cần `cv2.resize` để phóng hiệu logit 256/512→1024
(`spec.py` chỉ `numpy`, khối [2]). Không vòng Python theo điểm ảnh (K28): mọi vòng chỉ theo
lát, phần phóng và cộng dồn đều là thao tác mảng numpy/`cv2`.
"""

from collections.abc import Callable
from typing import Final, cast

import cv2
import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]  # onnxruntime 1.30 không có py.typed
from numpy.typing import NDArray

from apps.ml.runtime.errors import MODEL_FORMAT_UNSUPPORTED, ORT_ERRORS
from apps.ml.walls.spec import LOGITS_STRIDE, PAD_VALUE, TILE_PX, WALL_CLASS, tile_origins, to_model_input
from packages.messaging.run_cancel import checkpoint
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.ports import RgbImage

__all__ = ["SegformerOnnxSegmenter", "stitch_mask"]

_INPUT_SHAPE: Final = [1, 3, TILE_PX, TILE_PX]
# Bước 4 (SegFormer gốc) hoặc 2 (decoder có nhánh chi tiết, docs/seg-kien-truc gói A); ghép lát phóng về TILE_PX
_OUTPUT_SHAPES: Final = tuple([1, 2, TILE_PX // stride, TILE_PX // stride] for stride in (LOGITS_STRIDE, 2))

type RunTile = Callable[[NDArray[np.float32]], NDArray[np.float32]]


def _unsupported() -> PermanentError:
    """Lỗi chung của mọi model không khớp hợp đồng suy luận (không tiết lộ chi tiết)."""
    return PermanentError(MODEL_FORMAT_UNSUPPORTED)


def stitch_mask(pixels: RgbImage, run_tile: RunTile) -> NDArray[np.bool_]:
    """Ghép mặt nạ tường cỡ ảnh từ các lát `TILE_PX` chồng mép (khối [2] "Ghép lát").

    Mỗi lát: đệm `PAD_VALUE` nếu chạm mép ảnh → `to_model_input` → `run_tile` → hiệu logit
    (lớp `WALL_CLASS` trừ lớp còn lại) phóng về `TILE_PX` bằng nội suy song tuyến →
    cộng dồn vào mảng cỡ ảnh, kèm bộ đếm lát phủ mỗi điểm. Mask cuối = trung bình hiệu > 0.
    """
    height, width = pixels.shape[:2]
    other_class = 1 - WALL_CLASS
    accum = np.zeros((height, width), dtype=np.float32)
    counts = np.zeros((height, width), dtype=np.float32)
    for y0 in tile_origins(height):
        for x0 in tile_origins(width):
            checkpoint()  # K-24: lượt bị huỷ thì dừng giữa hai lát, không chạy hết trang
            tile_h = min(TILE_PX, height - y0)
            tile_w = min(TILE_PX, width - x0)
            tile = np.full((TILE_PX, TILE_PX, 3), PAD_VALUE, dtype=np.uint8)
            tile[:tile_h, :tile_w] = pixels[y0 : y0 + tile_h, x0 : x0 + tile_w]
            logits = run_tile(to_model_input(tile))
            diff = logits[0, WALL_CLASS] - logits[0, other_class]
            diff_full = cv2.resize(diff, (TILE_PX, TILE_PX), interpolation=cv2.INTER_LINEAR)
            accum[y0 : y0 + tile_h, x0 : x0 + tile_w] += diff_full[:tile_h, :tile_w]
            counts[y0 : y0 + tile_h, x0 : x0 + tile_w] += 1.0
    return (accum / counts) > 0.0


class SegformerOnnxSegmenter:
    """`WallSegmenter` (`packages.ml_contracts.ports`) chạy trên phiên ONNX SegFormer.

    Bất biến: hình vào/ra của phiên đã kiểm **lúc dựng**, nên `segment` không bao giờ gặp
    hình lạ; mọi lỗi `onnxruntime` lúc chạy (`ORT_ERRORS`, B5-01) là `PermanentError` có mã,
    không `except Exception` (R-16). Không dựa vào tên vào/ra (khối [2]).
    """

    def __init__(self, session: ort.InferenceSession) -> None:
        """Đúng 1 vào `float32` hình `[1, 3, 1024, 1024]`, đúng 1 ra hình `[1, 2, 256, 256]` hay `[1, 2, 512, 512]`.

        Sai một trong hai → `MODEL_FORMAT_UNSUPPORTED` (khối [2]), không suy đoán thêm.
        """
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].type != "tensor(float)"
            or list(inputs[0].shape) != _INPUT_SHAPE
            or len(outputs) != 1
            or outputs[0].type != "tensor(float)"
            or list(outputs[0].shape) not in _OUTPUT_SHAPES
        ):
            raise _unsupported()
        self._session = session
        self._input = inputs[0].name
        self._output = outputs[0].name

    def segment(self, image: RgbImage) -> NDArray[np.bool_]:
        """Ảnh trang → mặt nạ tường cùng khổ, qua `stitch_mask` quanh `session.run`."""

        def run_tile(x: NDArray[np.float32]) -> NDArray[np.float32]:
            try:
                return cast(NDArray[np.float32], self._session.run([self._output], {self._input: x})[0])
            except ORT_ERRORS as exc:
                raise _unsupported() from exc

        return stitch_mask(image, run_tile)
