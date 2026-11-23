"""Tác vụ `drawing_set.analyze` — bản **tạm** của lát cắt 2 (K-32a §5 PR(2)): rã trang, một hình mỗi trang.

Worker nhập module này (`discover_jobs`), nên chỉ nhập `packages.*` và `errors`/`settings` của module (không
`apps.api.core`, `fastapi`). Ba chặng, session ngắn, không giữ kết nối khi chạm kho (K36):

1. đọc bộ `analyzing` chưa xoá và các tệp `complete` (vắng → no-op: giao lặp, bộ đã xoá/đổi trạng thái — J06);
2. dựng từng trang thành PNG `…/files/{f}/pages/{i}.png` (ghi đè cùng khoá nên chạy lại an toàn);
3. dưới khoá dòng bộ, còn `analyzing` thì thay mọi hình `ai` bằng một hình cả trang mỗi trang, bộ sang `review`.

Chưa đọc chữ: mọi hình `kind: "other"`, độ tin 0, câu hỏi `NO_TEXT` — người duyệt đặt loại và đích. Chỗ nối của
việc sau (K-32a §5): K-14 thay `_machine_guess` bằng `page_kind(...)`; K-32a(3) thay "một hình/trang" bằng gợi ý khung
`packages/vision/preprocess/views.py`; K-32c đọc chữ từng hình. Dây không đổi.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets.errors import ANALYSIS_FAILED
from packages.core.clock import Clock, SystemClock
from packages.core.ids import new_id
from packages.db.engine import session_scope, worker_sessionmaker
from packages.db.hooks import on_after_commit
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow, DrawingSetViewRow
from packages.messaging import PermanentError, define_task, runner, send_task
from packages.messaging.payloads.drawing_sets import DRAWING_SET_ANALYZE_TASK, DrawingSetAnalyzePayload
from packages.storage.keys import drawing_set_page
from packages.storage.port import ObjectStorage, read_all_capped
from packages.vision.preprocess import VisionError, encode_png, load_raster, render_pdf_page

_log: Final = logging.getLogger(__name__)

ORIGINAL_MAX_BYTES: Final = 100 * 1024 * 1024
"""Trần byte đọc một tệp gốc (= `UPLOAD_MAX_BYTES` mặc định; N54 đã chặn tệp lớn hơn)."""
PAGE_MAX_PIXELS: Final = 4_000_000
"""Ảnh trang cho màn duyệt: ~4 MP đủ đọc tên hình và kéo khung, nhỏ hơn nhiều trần dựng của pipeline."""
PLACEHOLDER_MACHINE: Final = {"kind": "other", "kindConfidence": 0.0, "questions": ["NO_TEXT"], "cues": []}
"""Đề xuất gốc của bản tạm (lưu `machine` để K-32g đo được bản thật hơn bản này bao nhiêu)."""


@dataclass(frozen=True, slots=True)
class _File:
    """Ảnh chụp một tệp `complete` của bộ, đọc trước khi thả kết nối."""

    id: str
    sniffed_kind: str
    original_key: str
    page_count: int


class _UnreadableOriginalError(PermanentError):
    """Tệp gốc vắng hay quá trần: lượt phân tích không thể xong, thử lại vô ích."""


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


def _render_pages(kind: str, data: bytes, page_count: int) -> list[bytes]:
    """PNG từng trang (đồng bộ, PDFium/PIL — người gọi chạy trên luồng phụ, R-23); ảnh PNG/JPEG là một trang."""
    if kind != "pdf":
        return [encode_png(load_raster(data, max_pixels=PAGE_MAX_PIXELS))]
    return [encode_png(render_pdf_page(data, index, max_pixels=PAGE_MAX_PIXELS)) for index in range(page_count)]


async def _write_pages(storage: ObjectStorage, project_id: str, set_id: str, file: _File) -> None:
    """Đọc tệp gốc (có trần), dựng và ghi ảnh từng trang; vắng/quá trần/không dựng được → `_UnreadableOriginalError`."""

    def missing() -> Exception:
        """Tệp gốc biến mất khỏi kho sau N56."""
        return _UnreadableOriginalError(ANALYSIS_FAILED)

    data = await read_all_capped(
        storage, file.original_key, max_bytes=ORIGINAL_MAX_BYTES, too_large=missing, on_missing=missing
    )
    try:
        pages = await asyncio.to_thread(_render_pages, file.sniffed_kind, data, file.page_count)
    except VisionError as exc:
        raise _UnreadableOriginalError(ANALYSIS_FAILED) from exc
    for index, png in enumerate(pages):
        await storage.put(
            drawing_set_page(project_id, set_id, file.id, index), png, content_type="image/png", max_bytes=len(png)
        )


def _machine_guess() -> dict[str, Any]:
    """Đề xuất của máy cho một trang — chỗ nối K-14 (`page_kind`); bản tạm luôn "khác, chưa có chữ"."""
    return dict(PLACEHOLDER_MACHINE)


async def _publish_views(
    sessionmaker: async_sessionmaker[AsyncSession], set_id: str, files: list[_File], clock: Clock
) -> bool:
    """Dưới khoá dòng bộ: còn `analyzing` thì thay hình `ai` bằng một hình mỗi trang, bộ sang `review`; trả đã ghi.

    Xoá hình `ai` cũ trước khi chèn: lượt giao lặp tới sau một lượt dở không nhân đôi hình (J06).
    """
    async with session_scope(sessionmaker) as db:
        drawing_set = (
            await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id).with_for_update())
        ).scalar_one_or_none()
        if drawing_set is None or drawing_set.status != "analyzing" or drawing_set.deleted_at is not None:
            return False
        await db.execute(delete(DrawingSetViewRow).where(DrawingSetViewRow.set_id == set_id))
        pages = [(file.id, index) for file in files for index in range(file.page_count)]
        db.add_all(
            DrawingSetViewRow(
                id=new_id("dwv", clock),
                set_id=set_id,
                file_id=file_id,
                ord=ord_,
                page_index=page_index,
                crop={"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0},
                source="ai",
                kind="other",
                kind_confidence=0.0,
                machine=_machine_guess(),
            )
            for ord_, (file_id, page_index) in enumerate(pages)
        )
        drawing_set.status, drawing_set.progress_percent = "review", 100
    return True


async def analyze_set(
    sessionmaker: async_sessionmaker[AsyncSession], storage: ObjectStorage, set_id: str, clock: Clock
) -> bool:
    """Lõi của task (tiêm được cho test): ba chặng ở docstring module; trả `True` khi bộ đã sang `review`."""
    snapshot = await _snapshot(sessionmaker, set_id)
    if snapshot is None:
        return False
    project_id, files = snapshot
    for file in files:
        await _write_pages(storage, project_id, set_id, file)
    return await _publish_views(sessionmaker, set_id, files, clock)


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


def _on_failed(payload: DrawingSetAnalyzePayload, code: str) -> None:
    """`on_failed` của `define_task`: mọi mã hỏng của khung → bộ `failed` `DRAWING_SET_ANALYSIS_FAILED` (J03)."""
    _log.error("drawing_set_analysis_failed", extra={"set_id": payload.set_id, "code": code})
    runner().run(fail_analysis(worker_sessionmaker(), payload.set_id))


@define_task(name=DRAWING_SET_ANALYZE_TASK, payload=DrawingSetAnalyzePayload, on_failed=_on_failed)
async def analyze_drawing_set(payload: DrawingSetAnalyzePayload) -> None:
    """Hàm task mỏng: dựng tài nguyên thật rồi gọi `analyze_set`."""
    clock = SystemClock()
    await analyze_set(worker_sessionmaker(), _open_storage(clock), payload.set_id, clock)
