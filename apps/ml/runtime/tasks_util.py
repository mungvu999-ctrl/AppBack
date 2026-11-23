"""Khuôn task suy luận của B5-02…B5-04: đọc trang → chạy bước → ghi artifact → báo `step_done`.

Thân task chỉ là `await run_step(payload, fn, storage=infer_context().storage, prepare=…)`,
khai bằng `define_task(..., on_failed=step_failed)` để mã hỏng của chính `define_task`
(`RETRY_EXHAUSTED`, `WORKER_LOST`…) cũng tới B5-06c. Artifact ghi **đè** cùng khoá nên
giao lặp chỉ ghi lại cùng bytes rồi gửi lại (J06); B5-06c kiểm lượt dưới khoá dòng.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from apps.ml.runtime.errors import FILE_CORRUPT, IMAGE_TOO_LARGE, PIPELINE_ARTIFACT_MISSING
from apps.ml.runtime.loader import clear_session_cache
from apps.ml.runtime.settings import MlSettings, get_ml_settings, reset_ml_settings_cache
from packages.core.clock import SystemClock
from packages.messaging.celery_app import send_task
from packages.messaging.redis import ProcessLocal
from packages.messaging.run_cancel import CancelProbe, RunCancelledError, cancel_scope, is_run_cancelled
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.artifacts import MASK_MAX_PIXELS, STEP_ARTIFACTS, read_png_header
from packages.ml_contracts.payloads import InferStepPayload, StepResultPayload
from packages.storage.factory import create_storage
from packages.storage.port import ObjectStorage, read_all_capped
from packages.storage.settings import get_storage_settings

__all__ = [
    "ARTIFACT_MAX_BYTES",
    "PAGE_MAX_BYTES",
    "STEP_DONE_TASK",
    "InferContext",
    "StepOutput",
    "clear_page_cache",
    "clear_session_cache",
    "infer_context",
    "reset_infer_context",
    "run_step",
    "step_failed",
]

STEP_DONE_TASK: Final = "pipeline.orchestrate.step_done"
PAGE_MAX_BYTES: Final = 256 * 1024 * 1024
ARTIFACT_MAX_BYTES: Final = 64 * 1024 * 1024
_CONTENT_TYPES: Final = {".json": "application/json", ".png": "image/png"}
PAGE_CACHE_SIZE: Final = 2
"""Số trang đã giải giữ trong tiến trình (PERF-03 P21): ba task ML của một lượt đọc cùng trang.

ponytail: trần cố định 2 trang (trang ≤ `MASK_MAX_PIXELS` RGB, cỡ vài trăm MB mỗi trang) thay vì
trần theo byte; đủ cho một lượt chạy ở mỗi tiến trình prefork, nâng khi có số đo RAM thật.
"""

_log: Final = logging.getLogger(__name__)
type _PageCacheKey = tuple[int, str, int, int]
_PAGE_CACHE: Final[OrderedDict[_PageCacheKey, tuple[ObjectStorage, NDArray[np.uint8]]]] = OrderedDict()

type Send = Callable[[str, BaseModel], None]


@dataclass(frozen=True, slots=True)
class StepOutput:
    """Artifact của một bước: tên tương đối (⊆ `STEP_ARTIFACTS[step]`) → bytes."""

    artifacts: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class InferContext:
    """Tài nguyên dùng chung của tiến trình worker `ml`."""

    storage: ObjectStorage
    settings: MlSettings


def _build_context() -> InferContext:
    """Dựng từ biến môi trường của tiến trình (kho theo `STORAGE_BACKEND`).

    Không truyền `CoreSettings`: `ml` không ký URL (không gọi `signed_url`) nên không cầm
    `SECRET_KEY`/`PUBLIC_BASE_URL` (NO-085, BE-00 §2.1/§9); luật khác origin thuộc API.
    """
    storage = create_storage(get_storage_settings(), None, SystemClock())
    return InferContext(storage=storage, settings=get_ml_settings())


_CONTEXT: Final = ProcessLocal[InferContext](_build_context)


def infer_context() -> InferContext:
    """Ngữ cảnh của tiến trình, dựng lười ở lần gọi đầu (và lại sau `fork`)."""
    return _CONTEXT.get()


def reset_infer_context() -> None:
    """Chỉ cho test: quên ngữ cảnh, cấu hình đã đọc và trang đã giải."""
    _CONTEXT.reset()
    reset_ml_settings_cache()
    clear_page_cache()


def clear_page_cache() -> None:
    """Quên mọi trang đã giải trong tiến trình (test, hay giải phóng RAM)."""
    _PAGE_CACHE.clear()


def _log_step_timing(payload: InferStepPayload, status: str, duration_ms: int) -> None:
    """Một dòng log có cấu trúc khi một bước ML xong (PERF-03 P26); không chứa khoá hay bí mật."""
    _log.info(
        "pipeline_step_timing",
        extra={"run_id": payload.run_id, "step": payload.step, "status": status, "duration_ms": duration_ms},
    )


def step_failed(payload: InferStepPayload, code: str, *, send: Send = send_task, duration_ms: int = 0) -> None:
    """Báo `failed` kèm mã cho B5-06c; cũng là `on_failed` của task (chữ ký `define_task`)."""
    result = StepResultPayload(
        run_id=payload.run_id,
        step=payload.step,
        status="failed",
        error_code=code,
        model_version_id=payload.model.version_id,
        duration_ms=duration_ms,
    )
    send(STEP_DONE_TASK, result)


async def _read_page(storage: ObjectStorage, key: str) -> bytes:
    """Trang PNG ≤ `PAGE_MAX_BYTES`; không còn trong kho → `PIPELINE_ARTIFACT_MISSING`."""
    return await read_all_capped(
        storage,
        key,
        max_bytes=PAGE_MAX_BYTES,
        too_large=lambda: PermanentError(IMAGE_TOO_LARGE),
        on_missing=lambda: PermanentError(PIPELINE_ARTIFACT_MISSING),
    )


def _decode_page(data: bytes, payload: InferStepPayload) -> NDArray[np.uint8]:
    """PNG → RGB `(H, W, 3)`; kiểm khổ từ IHDR **trước** khi giải (K13)."""
    try:
        header = read_png_header(data)
    except ValueError as exc:
        raise PermanentError(FILE_CORRUPT) from exc
    if header.width * header.height > MASK_MAX_PIXELS:
        raise PermanentError(IMAGE_TOO_LARGE)
    if (header.width, header.height) != (payload.width_px, payload.height_px):
        raise PermanentError(FILE_CORRUPT)
    bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise PermanentError(FILE_CORRUPT)
    return np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)


async def _load_page(storage: ObjectStorage, payload: InferStepPayload) -> NDArray[np.uint8]:
    """Trang đã giải, qua cache LRU `PAGE_CACHE_SIZE` mục theo (kho, `page_key`, khổ).

    Khoá trang của một lượt tải không bị ghi đè nên cache không cũ. Mục giữ chính `storage`
    để `id(storage)` không bị dùng lại khi mục còn sống. Mảng trong cache đặt chỉ-đọc và mỗi
    lần trả **bản sao**, nên `fn` của task sau không thấy chỉnh sửa tại chỗ của task trước.
    Lỗi (`PermanentError`, lỗi kho) không vào cache.
    """
    key = (id(storage), payload.page_key, payload.width_px, payload.height_px)
    hit = _PAGE_CACHE.get(key)
    if hit is not None:
        _PAGE_CACHE.move_to_end(key)
        return hit[1].copy()
    data = await _read_page(storage, payload.page_key)
    image = await asyncio.to_thread(_decode_page, data, payload)
    image.flags.writeable = False
    _PAGE_CACHE[key] = (storage, image)
    while len(_PAGE_CACHE) > PAGE_CACHE_SIZE:
        _PAGE_CACHE.popitem(last=False)
    return image.copy()


async def _write(storage: ObjectStorage, payload: InferStepPayload, output: StepOutput) -> tuple[str, ...]:
    """Ghi đè từng artifact dưới `artifact_prefix`; tên ngoài `STEP_ARTIFACTS[step]` → `ValueError`."""
    unknown = set(output.artifacts) - set(STEP_ARTIFACTS[payload.step])
    if unknown:
        raise ValueError(f"artifact lạ cho bước {payload.step}: {sorted(unknown)}")
    keys: list[str] = []
    for name in sorted(output.artifacts):
        key = payload.artifact_prefix + name
        content_type = _CONTENT_TYPES[name[name.rindex(".") :]]
        await storage.put(key, output.artifacts[name], content_type=content_type, max_bytes=ARTIFACT_MAX_BYTES)
        keys.append(key)
    return tuple(keys)


async def run_step[PreparedT](
    payload: InferStepPayload,
    fn: Callable[[NDArray[np.uint8], InferStepPayload, PreparedT | None], StepOutput],
    *,
    storage: ObjectStorage,
    prepare: Callable[[InferStepPayload], Awaitable[PreparedT]] | None = None,
    send: Send = send_task,
    cancelled: Callable[[str], bool] = is_run_cancelled,
) -> None:
    """Chạy một bước suy luận và báo kết quả cho B5-06c.

    0. `prepare` (nạp model, dựng bộ chạy) trên vòng sự kiện, trước khi đọc trang;
    1. đọc, kiểm, giải trang; 2. `fn` (đồng bộ, không gọi hàm `async`) ngoài vòng sự kiện;
    3. ghi artifact **rồi mới** gửi `completed`.
    `PermanentError` ở bất kỳ bước nào → gửi `failed` cùng mã, trả bình thường. Lỗi tạm
    (`TransientError`, kho hay broker 503) nổi lên để `define_task` thử lại.

    Huỷ hợp tác (K-24): cờ huỷ của lượt (`cancelled`, mặc định đọc Redis) được hỏi trước mỗi pha
    và giữa từng ô ảnh trong `fn` (`run_cancel.checkpoint()`, ngữ cảnh chép sang luồng phụ). Gặp
    cờ → dừng, không ghi artifact, **không** gửi `step_done` (lượt đã `failed` trong DB), trả bình
    thường để `define_task` không thử lại.
    """
    started = time.monotonic()
    probe = CancelProbe(payload.run_id, read=cancelled)
    try:
        with cancel_scope(probe):
            probe.check(force=True)
            prepared = None if prepare is None else await prepare(payload)
            probe.check(force=True)
            image = await _load_page(storage, payload)
            output = await asyncio.to_thread(fn, image, payload, prepared)
            probe.check(force=True)
            keys = await _write(storage, payload, output)
    except RunCancelledError:
        _log_step_timing(payload, "cancelled", int((time.monotonic() - started) * 1000))
        return
    except PermanentError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        _log_step_timing(payload, "failed", elapsed)
        await asyncio.to_thread(step_failed, payload, exc.code, send=send, duration_ms=elapsed)
        return
    elapsed = int((time.monotonic() - started) * 1000)
    _log_step_timing(payload, "completed", elapsed)
    result = StepResultPayload(
        run_id=payload.run_id,
        step=payload.step,
        status="completed",
        artifact_keys=keys,
        model_version_id=payload.model.version_id,
        duration_ms=elapsed,
    )
    await asyncio.to_thread(send, STEP_DONE_TASK, result)
