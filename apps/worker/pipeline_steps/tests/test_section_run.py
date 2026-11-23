"""Lượt chạy của dòng mặt cắt chỉ đọc chữ (K-32c, NO-481): không tách tường, không dò đồ đạc, không dựng.

Dịch vụ thật (K23): Postgres, Redis, kho đĩa. Lượt mở bằng đúng đường sản xuất (`run_pipeline_start`) trên tầng
`is_section`; khẳng định đọc lại từ DB và từ hàng `ml.infer`/`pipeline.cpu`.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.spatial_read.documents import load_document
from apps.worker.pipeline_orchestrate.pins import load_pins
from apps.worker.pipeline_orchestrate.section import SKIPPED
from apps.worker.pipeline_steps.sweep import CPU_QUEUE, ML_QUEUE
from apps.worker.pipeline_steps.tests import helpers
from apps.worker.pipeline_steps.tests.helpers import Arranged, deliver_ml, open_run_at_ml, run_row, sweep
from packages.db.models.pipeline_orchestrate import PipelineRunModelsRow
from packages.messaging.redis import SyncRedis
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import queued_payloads

type Maker = async_sessionmaker[AsyncSession]

clean_queues = helpers.clean_queues
"""Fixture hai hàng của `helpers`, gán lại để pytest thấy nó trong module này (R-02)."""


async def _open_section(
    maker: Maker, storage: LocalDiskStorage, clock: FakeClock, broker: SyncRedis
) -> tuple[Arranged, list[dict[str, object]]]:
    """Lượt mặt cắt vừa qua `start`; trả kèm các thông điệp `ml.infer` mà `start` đã gửi (đọc trước khi xoá hàng)."""
    seen: list[dict[str, object]] = []
    arranged = await open_run_at_ml(maker, storage, clock, broker, is_section=True, sent=seen)
    return arranged, seen


@pytest.mark.asyncio(loop_scope="function")
async def test_section_start_queues_only_text_and_skips_walls_objects(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, clean_queues: SyncRedis
) -> None:
    """`start` trên tầng mặt cắt: chỉ `dimensionReading` được gửi; hai họ kia `skipped`, bước đã đóng."""
    arranged, sent = await _open_section(db_sessionmaker, local_storage, fake_clock, clean_queues)

    assert [message["step"] for message in sent] == ["dimensionReading"], sent
    async with db_sessionmaker() as db:
        pins = await load_pins(db, arranged.run_id)
    assert pins is not None
    assert dict(pins.used) == {"wallSegmentation": SKIPPED, "openingAndFurnitureDetection": SKIPPED}
    assert (await run_row(db_sessionmaker, arranged.run_id)).current_step == "dimensionReading"


@pytest.mark.asyncio(loop_scope="function")
async def test_section_text_done_completes_run_without_build_or_layer(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, clean_queues: SyncRedis
) -> None:
    """Chữ xong → lượt `completed` ngay: không `pipeline.build.run`, không tài liệu tầng, tóm tắt `sectionOnly`."""
    arranged, _ = await _open_section(db_sessionmaker, local_storage, fake_clock, clean_queues)

    await deliver_ml(db_sessionmaker, local_storage, arranged, "dimensionReading", fake_clock)

    row = await run_row(db_sessionmaker, arranged.run_id)
    assert (row.status, row.current_step, row.progress_percent) == ("completed", "qualityCheck", 100)
    assert queued_payloads(clean_queues, CPU_QUEUE) == []
    async with db_sessionmaker() as db:
        pins = await load_pins(db, arranged.run_id)
        assert await load_document(db, arranged.floor_pk) is None
    assert pins is not None
    assert pins.quality_summary is not None
    assert pins.quality_summary["sectionOnly"] is True
    assert pins.quality_summary["hasCritical"] is False
    assert pins.persisted_revision is None


@pytest.mark.asyncio(loop_scope="function")
async def test_floor_run_still_builds_after_text_done(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, clean_queues: SyncRedis
) -> None:
    """Đối chứng: tầng thường (không `is_section`) vẫn đi đủ ba bước ML rồi xếp `pipeline.build.run`."""
    arranged = await open_run_at_ml(db_sessionmaker, local_storage, fake_clock, clean_queues)
    for family in ("wallSegmentation", "openingAndFurnitureDetection", "dimensionReading"):
        await deliver_ml(db_sessionmaker, local_storage, arranged, family, fake_clock)

    assert len(queued_payloads(clean_queues, CPU_QUEUE)) == 1
    async with db_sessionmaker() as db:
        used = (
            await db.execute(select(PipelineRunModelsRow.used).where(PipelineRunModelsRow.run_id == arranged.run_id))
        ).scalar_one()
    assert SKIPPED not in used.values()


@pytest.mark.asyncio(loop_scope="function")
async def test_section_sweep_resends_only_text(
    db_sessionmaker: Maker, local_storage: LocalDiskStorage, fake_clock: FakeClock, clean_queues: SyncRedis
) -> None:
    """Quét bù lượt mặt cắt kẹt ở `dimensionReading`: gửi lại chỉ `dimensionReading` (họ `skipped` bị bỏ qua)."""
    arranged, _ = await _open_section(db_sessionmaker, local_storage, fake_clock, clean_queues)
    await helpers.set_idle(db_sessionmaker, arranged.run_id, fake_clock, seconds=helpers.REQUEUE_AFTER_S + 1)
    clean_queues.delete(ML_QUEUE, CPU_QUEUE)

    await sweep(db_sessionmaker, fake_clock)

    assert [message["step"] for message in queued_payloads(clean_queues, ML_QUEUE)] == ["dimensionReading"]
