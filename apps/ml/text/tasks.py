"""Task `ml.infer.text.read`: bước `dimensionReading` của lượt pipeline (BE-00 §7, §9).

Thân task chỉ là khuôn `run_step` của B5-01 — đọc trang, chạy bộ đọc, ghi `text.json`,
gửi `step_done` — nên ba luật riêng của bước nằm hết ở `prepare`: họ model phải đúng,
`ML_BACKEND=fake` không nạp ONNX nào, và `ModelRef` cổ điển (chưa có bản kích hoạt cho
họ đọc chữ) trả kết quả rỗng + `completed` thay vì hỏng lượt — B5-05 khi ấy lùi về tỉ
lệ khai ở dự án chứ không đoán (K19).

Luôn CPU: bước này không gọi `resolve_device` và không bao giờ giữ khoá `gpu:0`.
"""

import logging
from pathlib import Path
from typing import Final, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from apps.ml.runtime.errors import MODEL_VERSION_FAMILY_MISMATCH
from apps.ml.runtime.loader import load_onnx
from apps.ml.runtime.tasks_util import StepOutput, infer_context, run_step, step_failed
from apps.ml.text.reader import RapidOcrReader
from packages.domain.scale import ScaleSample, classify_scale_range, infer_scale
from packages.messaging.tasks import PermanentError, define_task
from packages.ml_contracts.artifacts import BoxPx, TextPx, TextResult, text_to_json
from packages.ml_contracts.fakes import FakeTextReader
from packages.ml_contracts.payloads import InferStepPayload, ModelRef
from packages.ml_contracts.ports import RgbImage, TextReader
from packages.vision.dimensions.chains import densest_cluster, dimension_spans
from packages.vision.dimensions.lengths import parse_length_mm

__all__ = ["MODEL_VERSION_FAMILY_MISMATCH", "STEP", "open_text_reader", "text_read"]

_log: Final = logging.getLogger(__name__)

STEP: Final = "dimensionReading"

REREAD_MAX_SIDE_PX: Final = 2000
"""Trang cạnh dài từ mức này không đọc lại: chữ đã đủ px cho bộ dò, còn phóng 2 lần thì vượt 16 Mpx mỗi lượt."""

REREAD_ZOOM: Final = 2

VERTICAL_TEXT_RATIO: Final = 1.3
"""Hộp cao hơn chừng này lần bề rộng là chữ dọc — bằng `_VERTICAL_TEXT_RATIO` của bước dựng (`pipeline_build`)."""

STRETCH_MIN_RANGE: Final = 10
"""Khoảng mức xám nền-mực tối thiểu để giãn; hẹp hơn là ảnh gần trống, giãn chỉ khuếch nhiễu."""


class _InactiveReader:
    """Bộ đọc của họ chưa có bản kích hoạt: trang nào cũng không có chữ nào."""

    def read(self, image: RgbImage) -> tuple[TextPx, ...]:
        """Luôn rỗng; bước vẫn `completed` để lượt pipeline chạy tiếp."""
        return ()


async def open_text_reader(model: ModelRef) -> TextReader:
    """Bộ đọc cho một model họ `dimensionReading`: bộ giả, bộ rỗng, hay `RapidOcrReader` trên model đã kiểm.

    Dùng chung cho bước pipeline và lượt đọc bộ hồ sơ (K-32a, `apps/ml/drawing_set_read`). Model chỉ tới được
    `onnxruntime` qua `load_onnx` (checksum, luật ONNX không tin, M02/M03); mọi lỗi của nó đã là `PermanentError`
    có mã nên không bọc thêm gì. Sai họ → `PermanentError(MODEL_VERSION_FAMILY_MISMATCH)`.
    """
    if model.family != STEP:
        raise PermanentError(MODEL_VERSION_FAMILY_MISMATCH)
    context = infer_context()
    if context.settings.ml_backend == "fake":
        return FakeTextReader()
    if model.is_classic:
        _log.info("text_model_inactive")
        return _InactiveReader()
    models_dir = Path(context.settings.ml_models_dir)
    session = await load_onnx(context.storage, model, models_dir=models_dir)
    return RapidOcrReader.from_session(session, pinned_name=model.pinned_name)


async def _prepare(payload: InferStepPayload) -> TextReader:
    """Bộ đọc của một bước pipeline: bước phải là `dimensionReading` (cùng họ model), rồi `open_text_reader`."""
    if payload.step != STEP:
        raise PermanentError(MODEL_VERSION_FAMILY_MISMATCH)
    return await open_text_reader(payload.model)


def _read(image: NDArray[np.uint8], payload: InferStepPayload, reader: TextReader | None) -> StepOutput:
    """Artifact `text.json` của bước.

    `reader` khai `| None` vì chữ ký chung của `run_step` cho phép bước không có
    `prepare`; bước này luôn có nên `cast` thay cho một nhánh chết không test nổi.
    """
    items = read_page(image, cast("TextReader", reader))
    return StepOutput({"text.json": text_to_json(TextResult(items=items))})


def read_page(image: NDArray[np.uint8], reader: TextReader) -> tuple[TextPx, ...]:
    """Chữ có `span_px` của trang; chưa ra tỉ lệ từ chuỗi kích thước thì đọc lại một lượt trên ảnh phóng.

    Lượt lại chỉ cho trang cạnh dài < `REREAD_MAX_SIDE_PX` (chữ kích thước của ảnh nhỏ cao vài px, dưới tầm bộ dò),
    trên ảnh phóng `REREAD_ZOOM` lần + giãn mức xám (`_stretch`); hộp chia lại về toạ độ trang, `span_px` vẫn đo
    trên ảnh gốc. Nhận lượt lại **chỉ** khi nó ra tỉ lệ chuỗi, không thì giữ lượt thường. Không phóng mọi trang:
    hộp chữ hẹp đi đổi đoạn đo được và làm ca chuỗi đang đúng xấu đi (bench ch-14 sai thêm 4,7 %).
    """
    items = with_spans(image, reader.read(image))
    if max(image.shape[:2]) >= REREAD_MAX_SIDE_PX or _chain_scale_found(items):
        return items
    height, width = image.shape[:2]
    zoomed = cv2.resize(image, (width * REREAD_ZOOM, height * REREAD_ZOOM), interpolation=cv2.INTER_CUBIC)
    reread = tuple(
        item.model_copy(
            update={
                "box": BoxPx(
                    x_min=item.box.x_min / REREAD_ZOOM,
                    y_min=item.box.y_min / REREAD_ZOOM,
                    x_max=item.box.x_max / REREAD_ZOOM,
                    y_max=item.box.y_max / REREAD_ZOOM,
                )
            }
        )
        for item in reader.read(_stretch(np.asarray(zoomed, dtype=np.uint8)))
    )
    spanned = with_spans(image, reread)
    adopted = _chain_scale_found(spanned)
    _log.info("text_reread", extra={"adopted": adopted})
    return spanned if adopted else items


def _chain_scale_found(items: tuple[TextPx, ...]) -> bool:
    """Chữ có `span_px` đọc ra độ dài, tỉ số trong dải, và cụm đông nhất suy được tỉ lệ — cùng phép của bước dựng
    (`pipeline_build.build._chain_samples` + `densest_cluster` + `infer_scale`; bước ML không nhập được apps.worker) —
    **hoặc** riêng chữ ngang và riêng chữ dọc mỗi bên suy được: ảnh giãn không đều tách hai cụm, trang đã đủ chữ và
    bước dựng xử lý theo trục; lượt phóng chỉ đổi hộp chữ, không chữa được (bench f-23)."""
    samples: list[ScaleSample] = []
    across: list[ScaleSample] = []
    down: list[ScaleSample] = []
    for index, item in enumerate(items):
        value = None if not item.span_px else parse_length_mm(item.text)
        if value is not None and item.span_px and classify_scale_range(value / item.span_px) == "inRange":
            sample = ScaleSample(f"t{index}-chain", item.span_px, float(value))
            box = item.box
            samples.append(sample)
            (down if box.y_max - box.y_min > VERTICAL_TEXT_RATIO * (box.x_max - box.x_min) else across).append(sample)
    if infer_scale(densest_cluster(samples)).mm_per_px is not None:
        return True
    return all(infer_scale(densest_cluster(axis)).mm_per_px is not None for axis in (across, down))


def _stretch(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Giãn mức xám theo nền: mức xám phổ biến nhất (nền giấy) → 255, phân vị 1 % (mực đậm nhất) → 0, mọi kênh
    cùng phép; chữ xám nhạt thành đậm cho bộ dò. Nền cách mực < `STRETCH_MIN_RANGE` (ảnh gần trống) → giữ nguyên."""
    gray = np.asarray(cv2.cvtColor(np.ascontiguousarray(image[..., :3]), cv2.COLOR_RGB2GRAY), dtype=np.uint8)
    background = int(np.argmax(np.bincount(gray.ravel(), minlength=256)))
    ink = float(np.percentile(gray, 1))
    if background - ink < STRETCH_MIN_RANGE:
        return image
    stretched = (image.astype(np.float32) - ink) * (255.0 / (background - ink))
    return np.asarray(np.clip(stretched, 0, 255), dtype=np.uint8)


def with_spans(image: NDArray[np.uint8], items: tuple[TextPx, ...]) -> tuple[TextPx, ...]:
    """Gắn `span_px` (đoạn kích thước đo trên ảnh) cho từng chữ; bước dựng dùng làm mẫu tỉ lệ.

    Ảnh xám theo trọng số BT.601, **làm tròn** như `cv2.COLOR_RGB2GRAY` (cắt phần lẻ biến nền 255 thành 254
    và lệch ngưỡng mực một mức: đo được trên ch-07); không có chữ thì khỏi dựng mặt nạ. Tính bằng số nguyên
    (`+500 // 1000` là làm tròn), không ép cả ảnh sang float64 (~960 MB ở trần 40 Mpx).
    """
    if not items:
        return items
    weighted = (image[..., :3].astype(np.uint32) * np.array([299, 587, 114], dtype=np.uint32)).sum(-1)
    gray = ((weighted + 500) // 1000).astype(np.uint8)
    spans = dimension_spans(gray, [item.box for item in items])
    return tuple(item.model_copy(update={"span_px": span}) for item, span in zip(items, spans, strict=True))


@define_task(name="ml.infer.text.read", payload=InferStepPayload, on_failed=step_failed)
async def text_read(payload: InferStepPayload) -> None:
    """Đọc chữ kích thước và nhãn phòng của một trang đã nắn, ghi `text.json` (J01, J06)."""
    await run_step(payload, _read, storage=infer_context().storage, prepare=_prepare)
