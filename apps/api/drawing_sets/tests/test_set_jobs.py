"""Tác vụ phân tích bộ hồ sơ (K-32a PR(3)): `drawing_set.analyze` và `drawing_set.publish` — J01, J03, J06, J09.

Postgres + kho local thật; gọi lõi `analyze_set`/`publish` với sessionmaker/kho của test (mẫu `run_design_concept`).
Thông điệp gửi sau commit bắt tại chỗ gửi (`DB_AFTER_COMMIT_INLINE=1`: callback chạy ngay trong commit), trừ J09
chạy broker thật. Kết quả đọc của worker `ml` được ghi tay vào kho đúng khoá nó sẽ ghi.
"""

import io
import math
from collections.abc import Coroutine
from typing import Any, Final

import numpy as np
import pytest
from PIL import Image
from pydantic import BaseModel
from redis import Redis as SyncRedis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets import analysis, jobs
from apps.api.drawing_sets.jobs import analyze_set, fail_analysis, publish, send_analyze_after_commit
from apps.api.drawing_sets.settings import get_drawing_set_settings
from apps.api.drawing_sets.tests._set_helpers import DEFAULT_QUEUE
from apps.api.drawing_sets.tests._set_helpers import broker as broker
from apps.api.drawings.schemas import CROP_MIN_AREA
from apps.api.drawings.tests._upload_helpers import pdf_bytes
from packages.db.hooks import after_commit_idle
from packages.db.models.drawing_sets import DrawingSetRow, DrawingSetViewRow
from packages.domain.drawing_set.set_analysis import VIEW_MIN_AREA
from packages.messaging import PermanentError
from packages.messaging.payloads.drawing_sets import DrawingSetAnalyzePayload, DrawingSetPublishPayload
from packages.ml_contracts.artifacts import BoxPx, TextPx
from packages.ml_contracts.drawing_sets import READ_BUDGET_S, PageReadingDoc, ReadingBox, reading_to_json
from packages.storage.keys import drawing_set_original, drawing_set_page, drawing_set_reading
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawing_sets import make_drawing_set, make_set_file
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import queued_payloads
from packages.vision.preprocess import RgbImage, VisionError, encode_png, load_raster
from packages.vision.preprocess.tests.synthetic import png_declaring

Maker = async_sessionmaker[AsyncSession]
Sent = list[tuple[str, dict[str, Any], float | None]]
PDF_PAGES: Final = 2
PAGE_W: Final = 2000
PAGE_H: Final = 1200
FULL: Final = {"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    """Callback sau commit chạy ngay (như worker) và `send_task` của `jobs` ghi lại (tên, payload JSON, countdown)."""
    out: Sent = []

    def record(name: str, body: BaseModel, *, countdown: float | None = None) -> None:
        """Ghi một lần gửi."""
        out.append((name, body.model_dump(mode="json"), countdown))

    monkeypatch.setenv("DB_AFTER_COMMIT_INLINE", "1")
    monkeypatch.setattr(jobs, "send_task", record)
    return out


def _read_pages(sent: Sent) -> list[tuple[str, int]]:
    """`(file_id, page_index)` của lượt đọc duy nhất đã gửi."""
    (body,) = [b for name, b, _ in sent if name == "ml.infer.drawing_set.read"]
    return [(p["file_id"], p["page_index"]) for p in body["pages"]]


async def _analyzing_set(
    maker: Maker, storage: LocalDiskStorage, *, store: tuple[bool, bool] = (True, True), **overrides: object
) -> tuple[str, str, list[str]]:
    """Bộ `analyzing` với PDF 2 trang + PNG `complete`, gốc trong kho theo `store` (mỗi tệp một cờ).

    Trả `(project_id, set_id, [file_id PDF, file_id PNG])`.
    """
    real_png = encode_png(RgbImage(np.full((8, 8, 3), 255, dtype=np.uint8)))
    files = [("ho-so.pdf", pdf_bytes(PDF_PAGES), "pdf", PDF_PAGES), ("anh.png", real_png, "png", 1)]
    ids: list[str] = []
    async with maker() as db:
        project = await make_project(db, owner=await make_user(db))
        drawing_set = await make_drawing_set(db, project=project, **({"status": "analyzing"} | overrides))
        for ord_, ((name, data, kind, pages), keep) in enumerate(zip(files, store, strict=True)):
            file = await make_set_file(
                db,
                drawing_set=drawing_set,
                ord=ord_,
                file_name=name,
                status="complete",
                sniffed_kind=kind,
                page_count=pages,
                declared_size_bytes=len(data),
            )
            file.original_key = drawing_set_original(project.id, drawing_set.id, file.id, kind)
            ids.append(file.id)
            if keep:
                await storage.put(file.original_key, data, content_type="application/octet-stream", max_bytes=len(data))
        await db.commit()
        return project.id, drawing_set.id, ids


async def _state(maker: Maker, set_id: str) -> tuple[str, int, str | None, list[DrawingSetViewRow]]:
    """`(status, progress, errorCode, hình theo ord)` đọc lại bằng session mới."""
    async with maker() as db:
        row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id))).scalar_one()
        stmt = select(DrawingSetViewRow).where(DrawingSetViewRow.set_id == set_id).order_by(DrawingSetViewRow.ord)
        views = list((await db.execute(stmt)).scalars().all())
    return row.status, row.progress_percent, row.error_code, views


def _text(value: str, x0: float, y0: float, x1: float, y1: float) -> TextPx:
    """Một dòng chữ OCR."""
    return TextPx(text=value, box=BoxPx(x_min=x0, y_min=y0, x_max=x1, y_max=y1), confidence=0.9)


def _two_views(left_title: str, right_title: str) -> PageReadingDoc:
    """Kết quả đọc một tờ hai hình (trái/phải), mỗi hình vài nhãn phòng và một tên lớn ở giữa dưới hình."""
    half = PAGE_W // 2
    texts: list[TextPx] = []
    for x0, title in ((0, left_title), (half, right_title)):
        texts += [_text(f"P{i}", x0 + 100 + 120 * i, 300, x0 + 150 + 120 * i, 330) for i in range(5)]
        texts.append(_text(title, x0 + 300, 1020, x0 + 700, 1100))
    boxes = (
        ReadingBox(x0=0, y0=0, x1=half, y1=PAGE_H, score=0.9),
        ReadingBox(x0=half, y0=0, x1=PAGE_W, y1=PAGE_H, score=0.9),
    )
    return PageReadingDoc(width_px=PAGE_W, height_px=PAGE_H, boxes=boxes, texts=tuple(texts), read=True)


async def _store_reading(
    storage: LocalDiskStorage, project_id: str, set_id: str, file_id: str, index: int, body: bytes
) -> None:
    """Ghi kết quả đọc một trang đúng khoá worker `ml` ghi."""
    key = drawing_set_reading(project_id, set_id, file_id, index)
    await storage.put(key, body, content_type="application/json", max_bytes=len(body))


# ---------------------------------------------------------------- drawing_set.analyze
async def test_analyze_drawing_set__J01(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """Ảnh PNG từng trang (3) trong kho, tiến độ 40 %, còn `analyzing`; đúng một lượt đọc với ba trang và model
    đọc chữ (cổ điển khi chưa kích hoạt bản nào)."""
    project_id, set_id, (pdf, png) = await _analyzing_set(db_sessionmaker, local_storage)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    status, progress, error, views = await _state(db_sessionmaker, set_id)
    assert (status, progress, error, views) == ("analyzing", jobs.READ_PROGRESS, None, [])
    for file_id, page in ((pdf, 0), (pdf, 1), (png, 0)):
        assert await local_storage.stat(drawing_set_page(project_id, set_id, file_id, page)) is not None
    safety, (name, body, countdown) = sent
    assert (name, countdown) == ("ml.infer.drawing_set.read", None)
    assert (body["project_id"], body["set_id"]) == (project_id, set_id)
    assert _read_pages(sent) == [(pdf, 0), (pdf, 1), (png, 0)]
    assert body["model"]["family"] == "dimensionReading"
    # NO-565: publish trễ gửi trước lượt đọc, hạn = ngân sách OCR + 2 phút, dưới mốc treo 15 phút.
    assert safety == ("drawing_set.publish", {"schema_version": 1, "set_id": set_id}, READ_BUDGET_S + 120)
    stall_s = get_drawing_set_settings().drawing_set_analysis_stall_s
    assert stall_s > jobs.PUBLISH_SAFETY_DELAY_S


async def test_analyze_drawing_set__J06(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """Bộ đã sang `review`, đã xoá mềm hay không có → no-op, không gửi gì."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage, status="review")
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is False
    _, deleted, _ = await _analyzing_set(db_sessionmaker, local_storage, deleted_at=fake_clock.now())
    assert await analyze_set(db_sessionmaker, local_storage, deleted, fake_clock) is False
    assert await analyze_set(db_sessionmaker, local_storage, "dws_" + "0" * 26, fake_clock) is False
    assert sent == []


async def test_analyze_skips_unreadable_file(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """Tệp gốc vắng (PDF) → bỏ trang của tệp đó, tệp còn lại (PNG) vẫn đi tiếp: lỗi một tệp không hỏng cả bộ."""
    _, set_id, (_, png) = await _analyzing_set(db_sessionmaker, local_storage, store=(False, True))
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    assert _read_pages(sent) == [(png, 0)]


async def test_analyze_skips_unrenderable_page(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """PDF hỏng (không dựng được trang nào) → chỉ ảnh PNG được đọc."""
    project_id, set_id, (pdf, png) = await _analyzing_set(db_sessionmaker, local_storage)
    key = drawing_set_original(project_id, set_id, pdf, "pdf")
    await local_storage.put(key, b"%PDF-1.4 rac %%EOF", content_type="application/pdf", max_bytes=64)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    assert _read_pages(sent) == [(png, 0)]


async def test_analyze_stops_when_set_changed_midway(
    db_sessionmaker: Maker,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    sent: Sent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bộ bị xoá mềm giữa lúc dựng trang → không xếp lượt đọc (người dùng đã "Bỏ bộ này")."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage)
    real = jobs._write_pages

    async def delete_then_write(*args: Any) -> list[int]:
        """Xoá mềm bộ rồi dựng trang như thật."""
        async with db_sessionmaker() as db:
            row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id))).scalar_one()
            row.deleted_at = fake_clock.now()
            await db.commit()
        return await real(*args)

    monkeypatch.setattr(jobs, "_write_pages", delete_then_write)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is False
    assert sent == []


def test_render_page_downsamples_scan_over_decoder_default() -> None:
    """NO-567: tờ PNG 5832x7027 (~41 Mpx, khổ DS-391, hơn trần 40 Mpx của `load_raster`) được co về dưới
    `PAGE_MAX_PIXELS` giữ tỉ lệ thay vì `IMAGE_TOO_LARGE`; tờ khai quá `PAGE_DECODE_MAX_PIXELS` vẫn bị từ chối."""
    scan = Image.new("L", (5832, 7027), 255)
    scan.paste(0, (0, 3500, 5832, 3520))
    buffer = io.BytesIO()
    scan.save(buffer, format="PNG")
    with pytest.raises(VisionError, match="IMAGE_TOO_LARGE"):
        load_raster(buffer.getvalue())
    with Image.open(io.BytesIO(jobs._render_page("png", buffer.getvalue(), 0))) as page:
        width, height = page.size
    assert width * height <= jobs.PAGE_MAX_PIXELS
    assert width * height > 0.99 * jobs.PAGE_MAX_PIXELS
    assert abs(width / height - 5832 / 7027) < 0.001
    side = math.isqrt(jobs.PAGE_DECODE_MAX_PIXELS) + 1
    with pytest.raises(VisionError, match="IMAGE_TOO_LARGE"):
        jobs._render_page("png", png_declaring(side, side), 0)


async def test_analyze_drawing_set__J03(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """Mọi tệp gốc vắng → `PermanentError` `DRAWING_SET_ANALYSIS_FAILED`; `fail_analysis` chốt (gọi lại không đổi)."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage, store=(False, False))
    with pytest.raises(PermanentError) as caught:
        await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock)
    assert caught.value.code == "DRAWING_SET_ANALYSIS_FAILED"
    await fail_analysis(db_sessionmaker, set_id)
    await fail_analysis(db_sessionmaker, set_id)
    assert (await _state(db_sessionmaker, set_id))[:3] == ("failed", 0, "DRAWING_SET_ANALYSIS_FAILED")
    assert sent == []


async def test_send_analyze_after_commit__J09(db_session: AsyncSession, broker: SyncRedis) -> None:
    """Giao dịch rollback → không gửi; commit → đúng một thông điệp `drawing_set.analyze` trên hàng `default`."""
    set_id = "dws_" + "1" * 26
    await db_session.execute(text("SELECT 1"))  # mở giao dịch: callback gắn với nó, rollback bỏ callback
    send_analyze_after_commit(db_session, set_id)
    await db_session.rollback()
    await after_commit_idle(db_session)
    assert queued_payloads(broker, DEFAULT_QUEUE) == []
    await db_session.execute(text("SELECT 1"))
    send_analyze_after_commit(db_session, set_id)
    await db_session.commit()
    await after_commit_idle(db_session)
    assert queued_payloads(broker, DEFAULT_QUEUE) == [DrawingSetAnalyzePayload(set_id=set_id).model_dump()]


# ---------------------------------------------------------------- drawing_set.publish
async def test_publish_drawing_set__J01(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Trang 0 của PDF là tờ hai mặt bằng (tầng 1, tầng 2) → hai hình `ai` mặt bằng, khung trái/phải, tầng 1/2, tên
    hình; trang không có kết quả → hình cả trang `NO_TEXT`. Bộ sang `review` 100 %, không hình nào có đích."""
    project_id, set_id, (pdf, png) = await _analyzing_set(db_sessionmaker, local_storage)
    body = reading_to_json(_two_views("MẶT BẰNG TẦNG 1", "MẶT BẰNG TẦNG 2"))
    await _store_reading(local_storage, project_id, set_id, pdf, 0, body)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is True
    status, progress, error, views = await _state(db_sessionmaker, set_id)
    assert (status, progress, error) == ("review", 100, None)
    assert [(v.file_id, v.page_index, v.source) for v in views] == [
        (pdf, 0, "ai"),
        (pdf, 0, "ai"),
        (pdf, 1, "ai"),
        (png, 0, "ai"),
    ]
    left, right, unread, image = views
    assert (left.kind, left.storey_no, left.title_text) == ("plan", 1, "MẶT BẰNG TẦNG 1")
    assert (right.kind, right.storey_no) == ("plan", 2)
    assert left.crop == {"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 0.5, "height_ratio": 1.0}
    assert right.crop["x_ratio"] == 0.5
    for view in (unread, image):
        assert (view.kind, view.kind_confidence, view.machine["questions"]) == ("other", 0.0, ["NO_TEXT"])
        assert view.crop == FULL
    assert all(v.target_mode is None for v in views)


async def test_publish_drawing_set__J06(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Giao lặp sau khi xong → no-op (không nhân đôi hình); bộ đã xoá mềm hay không có → no-op."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is True
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is False
    assert len((await _state(db_sessionmaker, set_id))[3]) == PDF_PAGES + 1
    _, deleted, _ = await _analyzing_set(db_sessionmaker, local_storage, deleted_at=fake_clock.now())
    assert await publish(db_sessionmaker, local_storage, deleted, fake_clock) is False
    assert await publish(db_sessionmaker, local_storage, "dws_" + "0" * 26, fake_clock) is False


async def test_safety_net_publish_after_read_finished_changes_nothing(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """NO-565: lượt đọc xong gửi publish (bộ sang `review`, hình có gợi ý); publish trễ tới sau → no-op, hình và
    trạng thái y nguyên (không ghi đè gợi ý, không nhân đôi hình)."""
    project_id, set_id, (pdf, _) = await _analyzing_set(db_sessionmaker, local_storage)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    body = reading_to_json(_two_views("MẶT BẰNG TẦNG 1", "MẶT BẰNG TẦNG 2"))
    await _store_reading(local_storage, project_id, set_id, pdf, 0, body)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is True
    status, progress, _, views = await _state(db_sessionmaker, set_id)
    before = [(v.id, v.kind, v.storey_no, v.crop, v.machine) for v in views]
    assert (status, progress, len(before)) == ("review", 100, PDF_PAGES + 2)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is False
    status, progress, _, views = await _state(db_sessionmaker, set_id)
    assert (status, progress) == ("review", 100)
    assert [(v.id, v.kind, v.storey_no, v.crop, v.machine) for v in views] == before


async def test_safety_net_publish_when_read_lost_opens_review_without_suggestions(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, sent: Sent
) -> None:
    """NO-565/NO-547: lượt đọc mất hẳn (không kết quả đọc nào, không publish nào từ worker `ml`) → publish trễ đưa
    bộ đang kẹt `analyzing` ra màn duyệt: mỗi trang một hình cả trang `NO_TEXT`, không lỗi, không hình có đích."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    assert (await _state(db_sessionmaker, set_id))[:2] == ("analyzing", jobs.READ_PROGRESS)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is True
    status, progress, error, views = await _state(db_sessionmaker, set_id)
    assert (status, progress, error, len(views)) == ("review", 100, None, PDF_PAGES + 1)
    for view in views:
        assert (view.kind, view.machine["questions"], view.crop, view.target_mode) == ("other", ["NO_TEXT"], FULL, None)


async def test_publish_drawing_set__J03(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task hết lần thử → `on_failed` chốt bộ `failed` `DRAWING_SET_ANALYSIS_FAILED` (J03)."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage)
    pending: list[Coroutine[Any, Any, None]] = []

    class _Runner:
        """Giữ coroutine `on_failed` gửi cho vòng của worker để test tự chờ trên vòng của nó."""

        def run(self, coro: Coroutine[Any, Any, None]) -> None:
            """Ghi lại coroutine."""
            pending.append(coro)

    monkeypatch.setattr(jobs, "runner", _Runner)
    monkeypatch.setattr(jobs, "worker_sessionmaker", lambda: db_sessionmaker)
    jobs._on_failed(DrawingSetPublishPayload(set_id=set_id), "RETRY_EXHAUSTED")
    await pending[0]
    assert (await _state(db_sessionmaker, set_id))[:3] == ("failed", 0, "DRAWING_SET_ANALYSIS_FAILED")


async def test_publish_stops_when_set_changed_midway(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bộ bị xoá mềm trong lúc đọc kết quả → chặng ghi không ghi hình, bộ giữ nguyên."""
    _, set_id, _ = await _analyzing_set(db_sessionmaker, local_storage)
    real = analysis.page_readings

    async def delete_then_read(*args: Any) -> list[Any]:
        """Xoá mềm bộ rồi đọc như thật."""
        async with db_sessionmaker() as db:
            row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id))).scalar_one()
            row.deleted_at = fake_clock.now()
            await db.commit()
        return await real(*args)

    monkeypatch.setattr(analysis, "page_readings", delete_then_read)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is False
    assert (await _state(db_sessionmaker, set_id))[3] == []


@pytest.mark.parametrize("body", [b"{rac", b'{"schema_version": 1}'])
async def test_publish_bad_reading_falls_back(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, body: bytes
) -> None:
    """Kết quả đọc hỏng hay sai hợp đồng (worker `ml` không được tin) → trang đó một hình cả trang `NO_TEXT`."""
    project_id, set_id, (pdf, _) = await _analyzing_set(db_sessionmaker, local_storage)
    await _store_reading(local_storage, project_id, set_id, pdf, 0, body)
    assert await publish(db_sessionmaker, local_storage, set_id, fake_clock) is True
    first = (await _state(db_sessionmaker, set_id))[3][0]
    assert (first.kind, first.machine["questions"], first.crop) == ("other", ["NO_TEXT"], FULL)


def test_view_min_area_matches_crop_rule() -> None:
    """Khung máy nhỏ nhất (gói domain) bằng trần `CropRect` của #5: khung máy luôn lưu lại được qua N59."""
    assert VIEW_MIN_AREA == CROP_MIN_AREA
