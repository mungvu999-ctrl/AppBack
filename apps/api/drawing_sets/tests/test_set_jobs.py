"""Tác vụ `drawing_set.analyze` bản tạm (K-32a): J01, J06, lỗi vĩnh viễn, `on_failed`, xếp hàng sau commit (J09).

Postgres + kho local thật; gọi lõi `analyze_set` với sessionmaker/kho của test (mẫu `run_design_concept`).
"""

from typing import Final

import numpy as np
import pytest
from redis import Redis as SyncRedis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets import jobs
from apps.api.drawing_sets.jobs import analyze_set, fail_analysis, send_analyze_after_commit
from apps.api.drawing_sets.tests._set_helpers import DEFAULT_QUEUE
from apps.api.drawing_sets.tests._set_helpers import broker as broker
from apps.api.drawings.tests._upload_helpers import pdf_bytes
from packages.db.hooks import after_commit_idle
from packages.db.models.drawing_sets import DrawingSetRow, DrawingSetViewRow
from packages.messaging import PermanentError
from packages.messaging.payloads.drawing_sets import DrawingSetAnalyzePayload
from packages.storage.keys import drawing_set_original, drawing_set_page
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawing_sets import make_drawing_set, make_set_file
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import queued_payloads
from packages.vision.preprocess import RgbImage, encode_png

Maker = async_sessionmaker[AsyncSession]
PDF_PAGES: Final = 2


async def _analyzing_set(
    maker: Maker, storage: LocalDiskStorage, *, store: bool = True, **overrides: object
) -> tuple[str, str]:
    """Bộ `analyzing` với PDF 2 trang + PNG `complete` (gốc trong kho khi `store`); trả `(project_id, set_id)`."""
    real_png = encode_png(RgbImage(np.full((8, 8, 3), 255, dtype=np.uint8)))
    files = [("ho-so.pdf", pdf_bytes(PDF_PAGES), "pdf", PDF_PAGES), ("anh.png", real_png, "png", 1)]
    async with maker() as db:
        project = await make_project(db, owner=await make_user(db))
        drawing_set = await make_drawing_set(db, project=project, status="analyzing", **overrides)
        for ord_, (name, data, kind, pages) in enumerate(files):
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
            if store:
                await storage.put(file.original_key, data, content_type="application/octet-stream", max_bytes=len(data))
        await db.commit()
        return project.id, drawing_set.id


async def _state(maker: Maker, set_id: str) -> tuple[str, int, str | None, list[tuple[str, int]]]:
    """`(status, progress, errorCode, [(file_id, page_index)…] theo ord)` đọc lại bằng session mới."""
    async with maker() as db:
        row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id))).scalar_one()
        views = (
            await db.execute(
                select(DrawingSetViewRow.file_id, DrawingSetViewRow.page_index)
                .where(DrawingSetViewRow.set_id == set_id)
                .order_by(DrawingSetViewRow.ord)
            )
        ).all()
    return row.status, row.progress_percent, row.error_code, [(f, p) for f, p in views]


async def test_analyze_drawing_set__J01(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """`analyzing` → `review` 100 %, một hình `ai` cả trang mỗi trang (3), ảnh PNG từng trang trong kho."""
    project_id, set_id = await _analyzing_set(db_sessionmaker, local_storage)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    status, progress, error, views = await _state(db_sessionmaker, set_id)
    assert (status, progress, error, [page for _, page in views]) == ("review", 100, None, [0, 1, 0])
    for file_id, page in views:
        info = await local_storage.stat(drawing_set_page(project_id, set_id, file_id, page))
        assert info is not None
    async with db_sessionmaker() as db:
        machine = (
            (await db.execute(select(DrawingSetViewRow.machine).where(DrawingSetViewRow.set_id == set_id)))
            .scalars()
            .first()
        )
    assert machine == jobs.PLACEHOLDER_MACHINE


async def test_analyze_drawing_set__J06(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Giao lặp sau khi xong → no-op (không nhân đôi hình); bộ đã xoá mềm hay không có → no-op."""
    _, set_id = await _analyzing_set(db_sessionmaker, local_storage)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is True
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is False
    assert len((await _state(db_sessionmaker, set_id))[3]) == PDF_PAGES + 1
    _, deleted = await _analyzing_set(db_sessionmaker, local_storage, deleted_at=fake_clock.now())
    assert await analyze_set(db_sessionmaker, local_storage, deleted, fake_clock) is False
    assert await analyze_set(db_sessionmaker, local_storage, "dws_" + "0" * 26, fake_clock) is False


async def test_analyze_stops_when_set_changed_midway(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bộ bị xoá mềm giữa lúc dựng trang → chặng cuối không ghi hình (người dùng đã "Bỏ bộ này")."""
    _, set_id = await _analyzing_set(db_sessionmaker, local_storage)
    real = jobs._write_pages

    async def delete_then_write(*args: object) -> None:
        """Xoá mềm bộ rồi dựng trang như thật."""
        async with db_sessionmaker() as db:
            row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id))).scalar_one()
            row.deleted_at = fake_clock.now()
            await db.commit()
        await real(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(jobs, "_write_pages", delete_then_write)
    assert await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock) is False
    assert (await _state(db_sessionmaker, set_id))[3] == []


@pytest.mark.parametrize("broken", ["missing", "corrupt"])
async def test_analyze_drawing_set__J03(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, broken: str
) -> None:
    """Tệp gốc vắng hay không dựng được → `PermanentError` `DRAWING_SET_ANALYSIS_FAILED`; `fail_analysis` chốt."""
    project_id, set_id = await _analyzing_set(db_sessionmaker, local_storage, store=broken == "corrupt")
    if broken == "corrupt":
        async for info in local_storage.list_prefix(f"projects/{project_id}/drawing-sets/{set_id}/"):
            await local_storage.put(info.key, b"%PDF-1.4 rac %%EOF", content_type="application/pdf", max_bytes=64)
    with pytest.raises(PermanentError) as caught:
        await analyze_set(db_sessionmaker, local_storage, set_id, fake_clock)
    assert caught.value.code == "DRAWING_SET_ANALYSIS_FAILED"
    await fail_analysis(db_sessionmaker, set_id)
    await fail_analysis(db_sessionmaker, set_id)  # lần hai: bộ đã `failed`, không đổi gì
    assert (await _state(db_sessionmaker, set_id))[:3] == ("failed", 0, "DRAWING_SET_ANALYSIS_FAILED")


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
