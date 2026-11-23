"""Tác vụ phân tích bộ hồ sơ (K-32a PR(3)): `drawing_set.analyze` và `drawing_set.publish` (hàng `default`).

Worker nhập module này (`discover_jobs`), nên chỉ nhập `packages.*` và module không web của `apps.api` (không
`apps.api.core`, `fastapi`). Chuỗi ba task, session ngắn, không giữ kết nối khi chạm kho (K36):

1. `drawing_set.analyze`: đọc bộ `analyzing` chưa xoá và các tệp `complete` (vắng → no-op, J06); dựng từng trang
   thành PNG `…/files/{f}/pages/{i}.png` (ghi đè nên chạy lại an toàn); tệp gốc vắng hay một trang không dựng được
   chỉ bỏ trang đó, mọi trang hỏng → `DRAWING_SET_ANALYSIS_FAILED`; xếp `ml.infer.drawing_set.read` sau commit
   (J09) với model đọc chữ đang kích hoạt, tiến độ 40 %.
2. `ml.infer.drawing_set.read` (worker `ml`, `apps/ml/drawing_set_read`): tách khung + OCR, ghi kết quả từng trang.
3. `drawing_set.publish`: phân loại, đề xuất tầng, ghi hình `ai` (`analysis.py`), bộ sang `review`.

Máy chỉ đề xuất (loại, tên, tầng, câu hỏi); không hình nào có đích — người duyệt chọn ở N59 (K-32a §1).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.admin_ml_registry.registry import active_versions
from apps.api.drawing_sets.analysis import publish_set
from apps.api.drawing_sets.errors import ANALYSIS_FAILED
from packages.core.clock import Clock, SystemClock
from packages.db.engine import session_scope, worker_sessionmaker
from packages.db.hooks import on_after_commit
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow
from packages.messaging import PermanentError, define_task, runner, send_task
from packages.messaging.payloads.drawing_sets import (
    DRAWING_SET_ANALYZE_TASK,
    DRAWING_SET_PUBLISH_TASK,
    DrawingSetAnalyzePayload,
    DrawingSetPublishPayload,
)
from packages.ml_contracts.drawing_sets import DRAWING_SET_READ_TASK, DrawingSetReadPayload, PageRef
from packages.storage.keys import drawing_set_page
from packages.storage.port import ObjectStorage, read_all_capped
from packages.vision.preprocess import RgbImage, VisionError, encode_png, load_raster, render_pdf_page
from packages.vision.preprocess.geometry import cap_pixels

_log: Final = logging.getLogger(__name__)

ORIGINAL_MAX_BYTES: Final = 100 * 1024 * 1024
"""Trần byte đọc một tệp gốc (= `UPLOAD_MAX_BYTES` mặc định; N54 đã chặn tệp lớn hơn)."""
PAGE_MAX_PIXELS: Final = 24_000_000
"""Ảnh trang cho màn duyệt **và** OCR: 24 Mpx ≈ A1 ở 170 dpi. Đo trên 10 tờ DS- (khung đáp án): đúng loại 49/104 ở
16 Mpx, 57/104 ở cả khổ gốc ~25 Mpx; OCR 4-32 s/tờ ở 24 Mpx (`changes/K-32a.md`). Nâng thì ảnh màn duyệt nặng hơn
(đường nâng cấp: ảnh xem trước riêng cỡ nhỏ, NO-546)."""
READ_PROGRESS: Final = 40
"""Tiến độ khi đã dựng xong trang và đang chờ worker `ml` đọc (N57 hiện cho người dùng)."""
TEXT_FAMILY: Final = "dimensionReading"


@dataclass(frozen=True, slots=True)
class _File:
    """Ảnh chụp một tệp `complete` của bộ, đọc trước khi thả kết nối."""

    id: str
    sniffed_kind: str
    original_key: str
    page_count: int


class _UnreadableSetError(PermanentError):
    """Không trang nào của bộ dựng được: lượt phân tích không thể xong, thử lại vô ích."""


class _MissingOriginalError(Exception):
    """Tệp gốc vắng hay quá trần: bỏ các trang của tệp đó, các tệp khác vẫn đi tiếp."""


def send_analyze_after_commit(db: AsyncSession, set_id: str) -> None:
    """Xếp `drawing_set.analyze` cho `set_id` **sau** commit của `db` (K17, J09)."""
    payload = DrawingSetAnalyzePayload(set_id=set_id)
    on_after_commit(db, lambda: send_task(DRAWING_SET_ANALYZE_TASK, payload))


async def _snapshot(sessionmaker: async_sessionmaker[AsyncSession], set_id: str) -> tuple[str, list[_File]] | None:
    """`(project_id, tệp complete theo thứ tự)` của bộ còn `analyzing` và chưa xoá; khác → `None` (no-op)."""
    async with sessionmaker() as db:
        project_id = (
            await db.execute(
                select(DrawingSetRow.project_id).where(
                    DrawingSetRow.id == set_id, DrawingSetRow.status == "analyzing", DrawingSetRow.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if project_id is None:
            return None
        rows = (
            await db.execute(
                select(
                    DrawingSetFileRow.id,
                    DrawingSetFileRow.sniffed_kind,
                    DrawingSetFileRow.original_key,
                    DrawingSetFileRow.page_count,
                )
                .where(DrawingSetFileRow.set_id == set_id, DrawingSetFileRow.status == "complete")
                .order_by(DrawingSetFileRow.ord)
            )
        ).all()
    return project_id, [_File(*row) for row in rows]


def _render_page(kind: str, data: bytes, index: int) -> bytes:
    """PNG một trang ≤ `PAGE_MAX_PIXELS` (đồng bộ, PDFium/PIL — người gọi chạy trên luồng phụ, R-23).

    Ảnh PNG/JPEG là một trang: nạp với trần mặc định của tiền xử lý (40 Mpx, như #7) rồi co về trần trang — nạp
    thẳng với trần trang thì ảnh quét lớn hơn bị từ chối (`IMAGE_TOO_LARGE`) thay vì được co.
    """
    if kind != "pdf":
        pixels, _sx, _sy = cap_pixels(load_raster(data).pixels, PAGE_MAX_PIXELS)
        return encode_png(RgbImage(pixels))
    return encode_png(render_pdf_page(data, index, max_pixels=PAGE_MAX_PIXELS))


def _missing() -> Exception:
    """Ngoại lệ của `read_all_capped` khi tệp gốc biến mất khỏi kho sau N56 hay vượt trần."""
    return _MissingOriginalError()


async def _write_pages(storage: ObjectStorage, project_id: str, set_id: str, file: _File) -> list[int]:
    """Dựng và ghi ảnh từng trang của một tệp; trả chỉ số các trang đã ghi (tệp gốc vắng → `[]`, trang hỏng bị bỏ)."""
    try:
        data = await read_all_capped(
            storage, file.original_key, max_bytes=ORIGINAL_MAX_BYTES, too_large=_missing, on_missing=_missing
        )
    except _MissingOriginalError:
        _log.warning("drawing_set_original_missing", extra={"set_id": set_id, "file_id": file.id})
        return []
    written: list[int] = []
    for index in range(file.page_count if file.sniffed_kind == "pdf" else 1):
        try:
            png = await asyncio.to_thread(_render_page, file.sniffed_kind, data, index)
        except VisionError as exc:
            _log.warning("drawing_set_page_unrenderable", extra={"file_id": file.id, "page": index, "code": exc.code})
            continue
        key = drawing_set_page(project_id, set_id, file.id, index)
        await storage.put(key, png, content_type="image/png", max_bytes=len(png))
        written.append(index)
    return written


async def _queue_read(
    sessionmaker: async_sessionmaker[AsyncSession], project_id: str, set_id: str, pages: list[PageRef]
) -> bool:
    """Dưới khoá dòng bộ: còn `analyzing` thì đặt tiến độ `READ_PROGRESS` và xếp lượt đọc sau commit (J09)."""
    async with session_scope(sessionmaker) as db:
        drawing_set = (
            await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id).with_for_update())
        ).scalar_one_or_none()
        if drawing_set is None or drawing_set.status != "analyzing" or drawing_set.deleted_at is not None:
            return False
        model = (await active_versions(db))[TEXT_FAMILY]
        payload = DrawingSetReadPayload(project_id=project_id, set_id=set_id, pages=tuple(pages), model=model)
        drawing_set.progress_percent = READ_PROGRESS
        on_after_commit(db, lambda: send_task(DRAWING_SET_READ_TASK, payload))
    return True


async def analyze_set(
    sessionmaker: async_sessionmaker[AsyncSession], storage: ObjectStorage, set_id: str, clock: Clock
) -> bool:
    """Lõi của `drawing_set.analyze` (tiêm được cho test); trả `True` khi đã xếp lượt đọc.

    Không trang nào dựng được → `_UnreadableSetError` (`DRAWING_SET_ANALYSIS_FAILED`). `clock` giữ chữ ký chung
    của các lõi task (không dùng ở chặng này).
    """
    snapshot = await _snapshot(sessionmaker, set_id)
    if snapshot is None:
        return False
    project_id, files = snapshot
    pages = [
        PageRef(file_id=file.id, page_index=index)
        for file in files
        for index in await _write_pages(storage, project_id, set_id, file)
    ]
    if not pages:
        raise _UnreadableSetError(ANALYSIS_FAILED)
    return await _queue_read(sessionmaker, project_id, set_id, pages)


async def publish(
    sessionmaker: async_sessionmaker[AsyncSession], storage: ObjectStorage, set_id: str, clock: Clock
) -> bool:
    """Lõi của `drawing_set.publish`: mọi trang của các tệp `complete` (kể cả trang không dựng được — hình cả trang
    không gợi ý) → hình máy đề xuất; trả `True` khi bộ đã sang `review`. Bộ không còn `analyzing` → no-op (J06)."""
    snapshot = await _snapshot(sessionmaker, set_id)
    if snapshot is None:
        return False
    project_id, files = snapshot
    pages = [(f.id, i) for f in files for i in range(f.page_count if f.sniffed_kind == "pdf" else 1)]
    return await publish_set(sessionmaker, storage, project_id, set_id, pages, clock)


async def fail_analysis(sessionmaker: async_sessionmaker[AsyncSession], set_id: str) -> None:
    """Chốt `failed` `DRAWING_SET_ANALYSIS_FAILED` cho bộ còn `analyzing` (lượt hết lần thử hay lỗi vĩnh viễn)."""
    async with session_scope(sessionmaker) as db:
        drawing_set = (
            await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id).with_for_update())
        ).scalar_one_or_none()
        if drawing_set is not None and drawing_set.status == "analyzing":
            drawing_set.status, drawing_set.error_code = "failed", ANALYSIS_FAILED


def _open_storage(clock: Clock) -> ObjectStorage:
    """Kho thật của tiến trình worker, nhập trễ (khuôn `apps/worker/design_director/tasks.py`)."""
    from packages.storage.factory import create_storage
    from packages.storage.settings import get_storage_settings

    return create_storage(get_storage_settings(), None, clock)


def _on_failed(payload: DrawingSetAnalyzePayload | DrawingSetPublishPayload, code: str) -> None:
    """`on_failed` của hai task: mọi mã hỏng của khung → bộ `failed` `DRAWING_SET_ANALYSIS_FAILED` (J03)."""
    _log.error("drawing_set_analysis_failed", extra={"set_id": payload.set_id, "code": code})
    runner().run(fail_analysis(worker_sessionmaker(), payload.set_id))


@define_task(name=DRAWING_SET_ANALYZE_TASK, payload=DrawingSetAnalyzePayload, on_failed=_on_failed)
async def analyze_drawing_set(payload: DrawingSetAnalyzePayload) -> None:
    """Hàm task mỏng: dựng tài nguyên thật rồi gọi `analyze_set`."""
    clock = SystemClock()
    await analyze_set(worker_sessionmaker(), _open_storage(clock), payload.set_id, clock)


@define_task(name=DRAWING_SET_PUBLISH_TASK, payload=DrawingSetPublishPayload, on_failed=_on_failed)
async def publish_drawing_set(payload: DrawingSetPublishPayload) -> None:
    """Hàm task mỏng: dựng tài nguyên thật rồi gọi `publish`."""
    clock = SystemClock()
    await publish(worker_sessionmaker(), _open_storage(clock), payload.set_id, clock)
