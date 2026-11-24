"""Task `ml.infer.drawing_set.read`: tách khung + OCR từng trang của một bộ hồ sơ (K-32a PR(3)).

`drawing_set.analyze` (worker default) dựng ảnh trang `…/files/{f}/pages/{i}.png` rồi gửi task này. Mỗi trang:
gợi ý khung (`packages.vision.preprocess.views.split_views`), đọc chữ cả trang bằng **đúng** bộ đọc của bước
`dimensionReading` (`apps.ml.text.tasks.open_text_reader`, model đang kích hoạt), ghi `PageReadingDoc` vào
`…/files/{f}/readings/{i}.json` (ghi đè, nên giao lặp an toàn — J06). Xong (hay hỏng hẳn) → gửi
`drawing_set.publish`: bộ luôn đi tới màn duyệt, trang thiếu kết quả chỉ mất gợi ý của máy.

Chuỗi chặng (NO-566): một task chỉ đọc `READ_PAGES_PER_TASK` trang đầu của payload rồi gửi task kế (cùng tên,
các trang còn lại, cùng hạn) xuống **cuối** hàng `ml.infer` — khe worker được trả sau mỗi chặng (~10-60 s) thay
vì giữ tới 8 phút, việc ML khác chen vào được. Chặng cuối gửi `drawing_set.publish`. Không dùng chord/chain của
Celery (K34): mỗi chặng tự gửi chặng kế bằng `send_task`.

Không chạm DB (worker `ml` không được nhập `packages.db`). Giới hạn: OCR chỉ chạy trong `READ_BUDGET_S` kể từ
lúc chặng đầu bắt đầu (đồng hồ tường, chuyền qua `deadline_epoch_s`, nên thời gian chờ hàng giữa các chặng cũng
tính); trang sau hạn vẫn có khung, `read=False` (J05), và mọi trang còn lại làm luôn trong một chặng (không OCR
nên nhanh). Lỗi một trang (ảnh hỏng/vắng, bộ đọc ném lỗi có mã) chỉ làm trang đó thiếu kết quả hay thiếu chữ,
không dừng lượt. Lỗi kho tạm thời lan ra để hàng đợi thử lại chặng đó (ghi đè, an toàn); chặng hết lần thử →
`on_failed` gửi `publish` như trước: các trang đã đọc giữ gợi ý, trang chưa đọc thành hình cả trang.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Final

import cv2
import numpy as np

from apps.ml.runtime.tasks_util import PAGE_MAX_BYTES, infer_context
from apps.ml.text.tasks import open_text_reader
from packages.messaging import PermanentError, define_task, send_task
from packages.messaging.payloads.drawing_sets import DRAWING_SET_PUBLISH_TASK, DrawingSetPublishPayload
from packages.ml_contracts.artifacts import MASK_MAX_PIXELS, TextPx
from packages.ml_contracts.drawing_sets import (
    BOXES_PER_PAGE_MAX,
    DRAWING_SET_READ_TASK,
    READ_BUDGET_S,
    DrawingSetReadPayload,
    PageReadingDoc,
    PageRef,
    ReadingBox,
    reading_to_json,
)
from packages.ml_contracts.ports import TextReader
from packages.storage.keys import drawing_set_page, drawing_set_reading
from packages.storage.port import ObjectStorage, read_all_capped
from packages.vision.preprocess import VisionError, load_raster
from packages.vision.preprocess.views import split_views

__all__ = ["READ_BUDGET_S", "READ_PAGES_PER_TASK", "read_chunk", "read_drawing_set", "read_pages"]

_log: Final = logging.getLogger(__name__)

READ_PAGES_PER_TASK: Final = 2
"""Số trang một chặng đọc: OCR 4-32 s/tờ 24 Mpx (`changes/K-32a.md`) → một chặng giữ khe `ml.infer` ~10-60 s."""


class _MissingPageError(PermanentError):
    """Ảnh trang vắng hay quá trần: trang này không có kết quả, các trang khác vẫn đọc."""


def _page_missing() -> Exception:
    """Ngoại lệ của `read_all_capped` khi ảnh trang vắng hay vượt `PAGE_MAX_BYTES`."""
    return _MissingPageError("DRAWING_SET_PAGE_MISSING")


def _where(page: PageRef, exc: Exception) -> dict[str, object]:
    """Trường log của một trang hỏng."""
    return {"file_id": page.file_id, "page": page.page_index, "error": str(exc)}


def _gray(rgb: np.ndarray) -> np.ndarray:
    """Ảnh xám 8-bit cho bộ tách khung."""
    return np.asarray(cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY), dtype=np.uint8)


def _boxes(gray: np.ndarray) -> tuple[ReadingBox, ...]:
    """Khung gợi ý của trang, tối đa `BOXES_PER_PAGE_MAX` khung lớn nhất (giữ thứ tự đọc)."""
    found = split_views(gray)
    if len(found) > BOXES_PER_PAGE_MAX:
        largest = sorted(found, key=lambda b: -(b.x1 - b.x0) * (b.y1 - b.y0))[:BOXES_PER_PAGE_MAX]
        found = [b for b in found if b in largest]
    return tuple(ReadingBox(x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1, score=b.score) for b in found)


def _inside(texts: tuple[TextPx, ...], width: int, height: int) -> tuple[TextPx, ...]:
    """Bỏ chữ có hộp vượt khổ ảnh (đầu ra lệch của bộ đọc không được làm hỏng cả kết quả trang)."""
    return tuple(t for t in texts if t.box.x_max <= width and t.box.y_max <= height)


def _texts(reader: TextReader, rgb: np.ndarray, page: PageRef) -> tuple[TextPx, ...] | None:
    """Chữ cả trang; bộ đọc ném lỗi có mã (model hỏng, ORT) → `None` (trang thiếu chữ, ghi log)."""
    try:
        return reader.read(rgb)
    except (PermanentError, ValueError) as exc:
        _log.warning("drawing_set_page_ocr_failed", extra=_where(page, exc))
        return None


def _read_one(data: bytes, reader: TextReader | None, page: PageRef) -> PageReadingDoc:
    """Kết quả một trang từ bytes PNG (đồng bộ, chạy trên luồng phụ — R-23); `reader=None` → chỉ khung."""
    rgb = load_raster(data, max_pixels=MASK_MAX_PIXELS).pixels
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    texts = None if reader is None else _texts(reader, rgb, page)
    return PageReadingDoc(
        width_px=width,
        height_px=height,
        boxes=_boxes(_gray(rgb)),
        texts=() if texts is None else _inside(texts, width, height),
        read=texts is not None,
    )


async def _read_page(
    storage: ObjectStorage, payload: DrawingSetReadPayload, page: PageRef, reader: TextReader | None
) -> bool:
    """Đọc và ghi kết quả một trang; ảnh vắng/hỏng → `False` (không ghi gì, bộ ghi hình dùng hình cả trang)."""
    key = drawing_set_page(payload.project_id, payload.set_id, page.file_id, page.page_index)
    try:
        data = await read_all_capped(
            storage, key, max_bytes=PAGE_MAX_BYTES, too_large=_page_missing, on_missing=_page_missing
        )
        doc = await asyncio.to_thread(_read_one, data, reader, page)
    except (_MissingPageError, VisionError) as exc:
        _log.warning("drawing_set_page_unreadable", extra=_where(page, exc))
        return False
    body = reading_to_json(doc)
    out = drawing_set_reading(payload.project_id, payload.set_id, page.file_id, page.page_index)
    await storage.put(out, body, content_type="application/json", max_bytes=len(body))
    return True


async def read_pages(
    storage: ObjectStorage,
    payload: DrawingSetReadPayload,
    reader: TextReader | None,
    *,
    deadline: float = math.inf,
    clock: Callable[[], float] = time.time,
) -> int:
    """Đọc từng trang của `payload.pages` theo thứ tự, trả số trang đã ghi kết quả.

    Trang bắt đầu từ `deadline` (giây epoch theo `clock`) trở đi không chạy OCR (`read=False`) nhưng vẫn có khung.
    Lặp tuần tự có chủ ý: OCR đã dùng hết luồng ORT của tiến trình, chạy song song chỉ tranh CPU.
    """
    written = 0
    for page in payload.pages:
        written += await _read_page(storage, payload, page, reader if clock() < deadline else None)
    return written


def _deadline(payload: DrawingSetReadPayload, budget_s: float, clock: Callable[[], float]) -> float:
    """Hạn OCR của cả lượt: chặng đầu đặt `bây giờ + budget_s`, chặng sau dùng hạn được chuyền."""
    return clock() + budget_s if payload.deadline_epoch_s is None else payload.deadline_epoch_s


async def read_chunk(
    storage: ObjectStorage,
    payload: DrawingSetReadPayload,
    reader: TextReader | None,
    *,
    budget_s: float = READ_BUDGET_S,
    clock: Callable[[], float] = time.time,
) -> DrawingSetReadPayload | None:
    """Lõi một chặng (tiêm được cho test): đọc `READ_PAGES_PER_TASK` trang đầu, trả payload chặng kế hay `None`.

    Đã quá hạn lúc bắt đầu chặng → đọc luôn mọi trang còn lại không OCR (chỉ khung) và kết thúc chuỗi.
    """
    deadline = _deadline(payload, budget_s, clock)
    take = READ_PAGES_PER_TASK if clock() < deadline else len(payload.pages)
    head, rest = payload.pages[:take], payload.pages[take:]
    await read_pages(storage, payload.model_copy(update={"pages": head}), reader, deadline=deadline, clock=clock)
    if not rest:
        return None
    return payload.model_copy(update={"pages": rest, "deadline_epoch_s": deadline})


async def _reader_or_none(payload: DrawingSetReadPayload) -> TextReader | None:
    """Bộ đọc của model ghim; model hỏng (lỗi có mã) → `None`: vẫn tách khung, chỉ thiếu chữ."""
    try:
        return await open_text_reader(payload.model)
    except PermanentError as exc:
        _log.warning("drawing_set_reader_unavailable", extra={"set_id": payload.set_id, "code": exc.code})
        return None


def _send_publish(set_id: str) -> None:
    """Báo worker default ghi hình cho bộ (`drawing_set.publish`)."""
    send_task(DRAWING_SET_PUBLISH_TASK, DrawingSetPublishPayload(set_id=set_id))


def _on_failed(payload: DrawingSetReadPayload, code: str) -> None:
    """`on_failed`: lượt đọc hỏng hẳn (hết lần thử, worker chết) vẫn gửi `publish` — bộ ra màn duyệt không gợi ý."""
    _log.error("drawing_set_read_failed", extra={"set_id": payload.set_id, "code": code})
    _send_publish(payload.set_id)


@define_task(name=DRAWING_SET_READ_TASK, payload=DrawingSetReadPayload, on_failed=_on_failed)
async def read_drawing_set(payload: DrawingSetReadPayload) -> None:
    """Hàm task mỏng: bộ đọc + kho của tiến trình, đọc một chặng rồi gửi chặng kế hay `drawing_set.publish`."""
    storage = infer_context().storage
    following = await read_chunk(storage, payload, await _reader_or_none(payload))
    if following is None:
        _send_publish(payload.set_id)
    else:
        send_task(DRAWING_SET_READ_TASK, following)
