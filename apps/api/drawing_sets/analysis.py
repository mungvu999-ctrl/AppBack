"""Chặng cuối của lượt phân tích bộ hồ sơ (`drawing_set.publish`, K-32a PR(3)): kết quả đọc → hình `ai`.

Đọc `PageReadingDoc` worker `ml` ghi cho từng trang (có trần byte, kiểm hợp đồng — worker `ml` không được tin),
gọi `packages.domain.drawing_set.set_analysis.guess_views` (loại K-14, tên, tầng của cả bộ, khung tên K-65), rồi
dưới khoá dòng bộ thay mọi hình bằng hình máy đề xuất và chuyển bộ sang `review`. Trang thiếu/hỏng kết quả → một
hình cả trang `other` + `NO_TEXT` (như bản tạm PR(2)): lỗi một trang không làm hỏng cả bộ.

Worker nhập module này qua `jobs.py`, nên chỉ nhập `packages.*` (không `fastapi`, không `apps.api.core`).
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.clock import Clock
from packages.core.ids import new_id
from packages.db.engine import session_scope
from packages.db.models.drawing_sets import DrawingSetRow, DrawingSetViewRow
from packages.domain.drawing_set.set_analysis import PageReading, Text, ViewGuess, guess_views
from packages.ml_contracts.artifacts import ARTIFACT_JSON_MAX_BYTES
from packages.ml_contracts.drawing_sets import PageReadingDoc, reading_from_json
from packages.storage.keys import drawing_set_reading
from packages.storage.port import ObjectStorage, read_all_capped

_log: Final = logging.getLogger(__name__)

UNREAD_PAGE: Final = PageReading((1, 1), read=False)
"""Trang không có kết quả đọc dùng được: một hình cả trang, không chữ."""
CROP_DIGITS: Final = 6


class _NoReadingError(Exception):
    """Kết quả đọc vắng hay quá trần: trang đó dùng `UNREAD_PAGE`."""


def _no_reading() -> Exception:
    """Ngoại lệ của `read_all_capped` khi kết quả đọc vắng hay vượt trần."""
    return _NoReadingError()


def _to_domain(doc: PageReadingDoc) -> PageReading:
    """Kết quả đọc trên kho → đầu vào của gói domain (toạ độ điểm ảnh trang)."""
    return PageReading(
        size=(doc.width_px, doc.height_px),
        boxes=tuple(((b.x0, b.y0, b.x1, b.y1), b.score) for b in doc.boxes),
        texts=tuple(
            Text(t.text, (t.box.x_min, t.box.y_min, t.box.x_max, t.box.y_max), t.confidence) for t in doc.texts
        ),
        read=doc.read,
    )


async def _reading(storage: ObjectStorage, key: str) -> PageReading:
    """Kết quả đọc một trang; vắng, quá trần hay sai hợp đồng → `UNREAD_PAGE` (ghi log). Lỗi kho tạm thời lan ra."""
    try:
        data = await read_all_capped(
            storage, key, max_bytes=ARTIFACT_JSON_MAX_BYTES, too_large=_no_reading, on_missing=_no_reading
        )
        return _to_domain(reading_from_json(data))
    except (_NoReadingError, ValueError) as exc:
        _log.warning("drawing_set_reading_unusable", extra={"key": key, "error": type(exc).__name__})
        return UNREAD_PAGE


async def page_readings(
    storage: ObjectStorage, project_id: str, set_id: str, pages: Sequence[tuple[str, int]]
) -> list[PageReading]:
    """Kết quả đọc của mọi trang `(file_id, page_index)`, cùng thứ tự.

    Đọc tuần tự (≤ 40 trang, kho không có đọc lô) trong việc nền — không phải N+1 của request (R-20).
    """
    return [await _reading(storage, drawing_set_reading(project_id, set_id, f, i)) for f, i in pages]


def _crop(guess: ViewGuess) -> dict[str, float]:
    """Khung theo tỉ lệ trang ở dạng cột `crop` (khoá như `CropRect` #5)."""
    x, y, width, height = (round(v, CROP_DIGITS) for v in guess.crop)
    return {"x_ratio": x, "y_ratio": y, "width_ratio": width, "height_ratio": height}


def view_rows(
    set_id: str, pages: Sequence[tuple[str, int]], guesses: Sequence[ViewGuess], clock: Clock
) -> list[DrawingSetViewRow]:
    """Dòng `drawing_set_views` nguồn `ai` cho từng đề xuất, `ord` theo thứ tự đề xuất (trang rồi khung)."""
    return [
        DrawingSetViewRow(
            id=new_id("dwv", clock),
            set_id=set_id,
            file_id=pages[guess.page][0],
            ord=ord_,
            page_index=pages[guess.page][1],
            crop=_crop(guess),
            source="ai",
            kind=guess.kind,
            kind_confidence=guess.kind_confidence,
            title_text=guess.title_text,
            storey_label=guess.storey_label,
            storey_no=guess.storey_no,
            storey_confidence=guess.storey_confidence,
            machine=guess.machine,
        )
        for ord_, guess in enumerate(guesses)
    ]


async def publish_views(
    sessionmaker: async_sessionmaker[AsyncSession], set_id: str, rows: Sequence[DrawingSetViewRow]
) -> bool:
    """Dưới khoá dòng bộ: còn `analyzing` (chưa xoá) thì thay mọi hình bằng `rows`, bộ sang `review` 100 %.

    Xoá hình cũ trước khi chèn: lượt giao lặp tới sau một lượt dở không nhân đôi hình (J06). Trả đã ghi.
    """
    async with session_scope(sessionmaker) as db:
        drawing_set = (
            await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id).with_for_update())
        ).scalar_one_or_none()
        if drawing_set is None or drawing_set.status != "analyzing" or drawing_set.deleted_at is not None:
            return False
        await db.execute(delete(DrawingSetViewRow).where(DrawingSetViewRow.set_id == set_id))
        db.add_all(rows)
        drawing_set.status, drawing_set.progress_percent = "review", 100
    return True


async def publish_set(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    project_id: str,
    set_id: str,
    pages: Sequence[tuple[str, int]],
    clock: Clock,
) -> bool:
    """Đọc kết quả mọi trang, đề xuất hình và ghi; trả `True` khi bộ đã sang `review`."""
    readings = await page_readings(storage, project_id, set_id, pages)
    guesses = await asyncio.to_thread(guess_views, readings)  # luật chữ thuần Python, vài giây cho 40 trang (R-23)
    return await publish_views(sessionmaker, set_id, view_rows(set_id, pages, guesses, clock))
