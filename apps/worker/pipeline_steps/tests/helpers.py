"""Dựng sân khấu và đọc lại trạng thái dùng chung cho **mọi** test B5-06c ([8] "Cách dựng").

Một bản duy nhất cho ba lõi (`step_done`, `sweep`, `purge`): không module test nào nhập helper
từ module test khác (R-02). Ba nhóm: lượt đang ở bước ML (`open_run_at_ml`), lượt dựng bằng SQL
cho quét bù (`arrange_run`, `set_idle`), cảnh dọn artifact (`build_scene`).

Dịch vụ thật (K23): Postgres, Redis, kho đĩa `local_storage`. Lượt chạy mở bằng đúng đường
sản xuất (`start_run` → lõi `run_pipeline_start` của B5-06a) thay vì `INSERT` tay, nên bản vẽ,
dòng ghim và tiền tố artifact đều đúng hình dạng mà lõi `step_done` sẽ kiểm lại dưới khoá.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

import pytest
from PIL import Image
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.runs import reset_sync_bus_cache, start_run
from apps.api.drawings.tests._helpers import make_scene
from apps.worker.pipeline_build.artifacts import input_key
from apps.worker.pipeline_orchestrate.keys import run_prefix
from apps.worker.pipeline_orchestrate.start import run_pipeline_start
from apps.worker.pipeline_steps import sweep as sweep_core
from apps.worker.pipeline_steps.settings import reset_steps_settings_cache
from apps.worker.pipeline_steps.step_done import run_pipeline_step_done
from apps.worker.pipeline_steps.sweep import CPU_QUEUE, ML_QUEUE, run_stuck_pipeline_sweep
from packages.core.clock import Clock
from packages.core.object_keys import run_prefix as artifact_run_prefix
from packages.core.object_keys import upload_prefix
from packages.db.hooks import after_commit_idle
from packages.db.models.drawings import DrawingRow, PipelineRunRow
from packages.db.models.pipeline_orchestrate import PipelineRunModelsRow
from packages.db.settings import reset_database_settings_cache
from packages.messaging.payloads.drawings import PipelineStartPayload
from packages.messaging.redis import SyncRedis, broker_redis, broker_redis_sync
from packages.messaging.streams import EventBus, upload_stream
from packages.ml_contracts.artifacts import (
    ObjectsResult,
    TextResult,
    WallsResult,
    objects_to_json,
    text_to_json,
    walls_to_json,
)
from packages.ml_contracts.families import FAMILY_STEP, ModelFamily
from packages.ml_contracts.payloads import StepResultPayload
from packages.ml_contracts.synthetic import render_plan
from packages.storage.keys import upload_original, upload_page
from packages.storage.local import LocalDiskStorage
from packages.storage.port import ObjectStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawings import make_complete_upload, make_drawing
from packages.testing.factories.floors import make_floor
from packages.testing.factories.pipeline_orchestrate import make_run_pins
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.db import drop_after_commit
from packages.testing.fixtures.messaging import queued_payloads
from packages.vision.preprocess.tests.synthetic import encode

type Maker = async_sessionmaker[AsyncSession]

_log = logging.getLogger(__name__)

PLAN_SEED: Final = 7
"""Seed trang tổng hợp; cố định để khổ trang (và `width_px` của `BuildStepPayload`) tất định."""

_ARTIFACT_BODY: Final = {
    "wallSegmentation": lambda: walls_to_json(WallsResult(walls=())),
    "openingAndFurnitureDetection": lambda: objects_to_json(ObjectsResult(detections=())),
    "dimensionReading": lambda: text_to_json(TextResult(items=())),
}
"""Thân JSON **rỗng nhưng hợp hợp đồng** của từng họ; `step_done` chỉ so khoá, không đọc nội dung."""


@dataclass(frozen=True, slots=True)
class Arranged:
    """Mọi id mà test cần sau khi lượt chạy đã vào bước ML; đông cứng để không test nào sửa."""

    run_id: str
    upload_id: str
    floor_pk: int
    project_id: str
    level_id: str
    run_prefix: str
    page_key: str
    width_px: int
    height_px: int


async def open_run_at_ml(
    maker: Maker,
    storage: ObjectStorage,
    clock: FakeClock,
    broker_client: SyncRedis,
    *,
    is_section: bool = False,
    sent: list[dict[str, object]] | None = None,
) -> Arranged:
    """Lượt `running` ở `wallSegmentation`, ba họ đã ghim, `used` rỗng, hàng `ml.infer` đã xoá.

    `is_section=True` (K-32c): tầng là mặt cắt nên lượt vào `dimensionReading` với `used` đã có hai họ `skipped`.
    `sent` (nếu có) nhận các thông điệp `ml.infer` mà `start` vừa gửi, chụp trước khi xoá hàng.

    Xoá hàng ở cuối là phần quan trọng: `run_pipeline_start` gửi ba `ml.infer.*`, mà mọi test
    sau đó đếm thông điệp của **mình** bằng `LLEN`/`LRANGE` trên hàng dùng chung cả phiên.
    """
    plan = render_plan(PLAN_SEED, width_px=800, height_px=600)
    async with maker() as db:
        scene = await make_scene(db)
        scene.floor.is_section = is_section
        upload = await make_complete_upload(
            db, storage, project=scene.project, floor=scene.floor, data=plan.image_png, file_name="plan.png"
        )
        await db.commit()
    async with maker() as db:
        run = await start_run(db, upload_id=upload.id, clock=clock)
        await db.commit()
    await after_commit_idle(db)
    payload = PipelineStartPayload(run_id=run.id, upload_id=run.upload_id)
    await run_pipeline_start(payload, sessionmaker=maker, storage=storage, clock=clock)
    if sent is not None:
        sent.extend(queued_payloads(broker_client, ML_QUEUE))
    broker_client.delete(ML_QUEUE, CPU_QUEUE)
    async with maker() as db:
        drawing = (await db.execute(select(DrawingRow).where(DrawingRow.floor_pk == scene.floor.pk))).scalar_one()
        return Arranged(
            run_id=run.id,
            upload_id=upload.id,
            floor_pk=scene.floor.pk,
            project_id=scene.project.id,
            level_id=scene.floor.level_id,
            run_prefix=run_prefix(
                project_id=scene.project.id,
                level_id=scene.floor.level_id,
                upload_id=upload.id,
                run_id=run.id,
            ),
            page_key=drawing.page_key,
            width_px=drawing.width_px,
            height_px=drawing.height_px,
        )


async def put_ml_artifacts(storage: ObjectStorage, arranged: Arranged, family: ModelFamily) -> tuple[str, ...]:
    """Ghi artifact JSON đầu vào của một bước ML vào kho, trả đúng `artifact_keys` của bước đó."""
    body = _ARTIFACT_BODY[family]()
    key = input_key(arranged.run_prefix, family)
    await storage.put(key, body, content_type="application/json", max_bytes=len(body))
    return (key,)


def step_result(arranged: Arranged, step: str, **kw: object) -> StepResultPayload:
    """`StepResultPayload` của một bước; `kw` sai hợp đồng (mã lỗi xấu) → `model_construct`.

    `model_construct` là lệch khỏi prompt có chủ đích: `error_code` đã có `pattern` nên payload
    sai mẫu không qua nổi `model_validate`, mà [8] vẫn đòi kiểm nhánh phòng thủ của lõi.
    """
    fields: dict[str, object] = {"run_id": arranged.run_id, "step": step, "duration_ms": 10, **kw}
    fields.setdefault("status", "completed")
    fields.setdefault("artifact_keys", ())
    try:
        return StepResultPayload.model_validate(fields)
    except ValueError:
        return StepResultPayload.model_construct(schema_version=1, **cast("Any", fields))


async def deliver_ml(
    maker: Maker, storage: ObjectStorage, arranged: Arranged, family: ModelFamily, clock: FakeClock
) -> None:
    """Một lượt giao `step_done` `completed` cho bước ML của `family`, artifact đã nằm trong kho."""
    keys = await put_ml_artifacts(storage, arranged, family)
    payload = step_result(arranged, FAMILY_STEP[family], artifact_keys=keys)
    await run_pipeline_step_done(payload, sessionmaker=maker, clock=clock)


async def steps_seen(bus: EventBus, upload_id: str, *, after: int) -> list[tuple[object, object, object]]:
    """Khung `Progress` mới kể từ sự kiện thứ `after`, dạng `(status, step, progressPercent)`."""
    events = await bus.read_after(upload_stream(upload_id), "0-0")
    return [(e.data["status"], e.data["step"], e.data["progressPercent"]) for e in events[after:]]


async def run_row(maker: Maker, run_id: str) -> PipelineRunRow:
    """Dòng `pipeline_runs` đọc lại trên session mới — trạng thái đã commit, không phải cache."""
    async with maker() as db:
        return (await db.execute(select(PipelineRunRow).where(PipelineRunRow.id == run_id))).scalar_one()


REQUEUE_AFTER_S: Final = 600
"""`PIPELINE_STEP_REQUEUE_AFTER_S` mặc định của `StepsSettings`; test đặt mốc im quanh số này."""
BATCH: Final = 10
REAL_LLEN_TIMEOUT_S: Final = sweep_core.LLEN_TIMEOUT_S
"""Trần `LLEN` thật của lõi, chụp lúc nạp module (trước mọi vá) — hai test hàng treo ghim lại số này."""
LLEN_WAIT_FACTOR: Final = 30
"""Hệ số nới `sweep.LLEN_TIMEOUT_S` trong `clean_queues` (NO-404): hạn chờ chống treo, không phải trần hiệu năng."""


@dataclass(frozen=True, slots=True)
class SweepRun:
    """Một lượt `running` đã ghim, có bản vẽ hiện hành — điểm xuất phát mọi test quét bù."""

    run_id: str
    upload_id: str
    floor_pk: int


def png(width: int = 48, height: int = 32) -> bytes:
    """Một PNG thật nhỏ nhất đủ để `make_drawing` đọc được kích thước."""
    return encode(Image.new("RGB", (width, height), "white"), "PNG")


async def arrange_run(
    maker: Maker,
    storage: LocalDiskStorage,
    clock: FakeClock,
    *,
    step: str = "wallSegmentation",
    status: str = "running",
    used: dict[ModelFamily, str] | None = None,
    requeue_count: int = 0,
    with_drawing: bool = True,
    level_id: str | None = None,
    progress_percent: int = 5,
) -> SweepRun:
    """Lượt chạy ở `step` với `status`, dòng ghim và (tuỳ chọn) bản vẽ hiện hành đã commit.

    `start_run` mở lượt `pending` ở `preprocess`; bước, trạng thái và `progress_percent` đặt bằng
    `UPDATE` vì không có lõi nào đẩy lượt tới giữa chuỗi mà không gửi thông điệp. Mặc định 5 là
    trọng số của `preprocess` — đúng số `Progress` của một lượt vừa vào `wallSegmentation`.

    Cả giao dịch nằm trong `drop_after_commit()`: `start_run` tự hẹn một `pipeline.orchestrate.start`
    sau commit (`runs.py:132`), mà thông điệp đó vừa làm `pipeline.cpu` "còn việc" (lõi giữ mọi
    lượt) vừa lẫn vào phần đếm hàng của test. Bỏ nó đi **là** cảnh cần dựng: thông điệp đã mất.
    """
    with drop_after_commit():
        return await _arrange(
            maker,
            storage,
            clock,
            step=step,
            status=status,
            used=used,
            requeue_count=requeue_count,
            with_drawing=with_drawing,
            level_id=level_id,
            progress_percent=progress_percent,
        )


async def _arrange(
    maker: Maker,
    storage: LocalDiskStorage,
    clock: FakeClock,
    *,
    step: str,
    status: str,
    used: dict[ModelFamily, str] | None,
    requeue_count: int,
    with_drawing: bool,
    level_id: str | None,
    progress_percent: int,
) -> SweepRun:
    """Thân của `arrange_run`, tách ra để `drop_after_commit()` bọc đúng một khối `async with`."""
    async with maker() as db:
        scene = await make_scene(db, level_id=level_id)
        upload = await make_complete_upload(
            db, storage, project=scene.project, floor=scene.floor, data=png(), file_name="plan.png"
        )
        if with_drawing:
            await make_drawing(db, storage, upload=upload, png=png())
        run = await start_run(db, upload_id=upload.id, clock=clock)
        await make_run_pins(db, run_id=run.id, used=used, step_requeue_count=requeue_count)
        await db.execute(
            update(PipelineRunRow)
            .where(PipelineRunRow.id == run.id)
            .values(status=status, current_step=step, started_at=clock.now(), progress_percent=progress_percent)
        )
        await db.commit()
    await after_commit_idle(db)
    placed = await run_row(maker, run.id)
    assert (placed.status, placed.current_step) == (status, step), "bản dựng không đặt được lượt vào đúng bước"
    return SweepRun(run_id=run.id, upload_id=upload.id, floor_pk=scene.floor.pk)


async def set_idle(maker: Maker, run_id: str, clock: FakeClock, *, seconds: float) -> datetime:
    """Đặt hai `updated_at`, `last_used_at` **và** `started_at` về một mốc rồi `fake_clock.set` tới `mốc + seconds`.

    Trả mốc im đã **đọc lại** từ DB: giờ của Postgres và của `fake_clock` không cùng nguồn, nên
    chỉ giá trị đọc lại mới dùng được để tính ngưỡng ([8]). `started_at` đi cùng mốc vì nếu không,
    `now - started_at` vượt `PIPELINE_RUN_MAX_S` và mọi luật giữ theo hàng bị bỏ qua.

    `last_used_at` cũng phải lùi: mốc im của lõi là `greatest(r.updated_at, m.updated_at,
    m.last_used_at)`, nên để cột đó ở giờ thật là dựng một lượt **không** im.
    """
    mark = datetime(2026, 3, 1, 12, tzinfo=UTC)
    async with maker() as db:
        await db.execute(
            update(PipelineRunRow).where(PipelineRunRow.id == run_id).values(updated_at=mark, started_at=mark)
        )
        await db.execute(
            update(PipelineRunModelsRow)
            .where(PipelineRunModelsRow.run_id == run_id)
            .values(updated_at=mark, last_used_at=mark)
        )
        await db.commit()
        stmt = text(
            "SELECT greatest(r.updated_at, m.updated_at) FROM pipeline_runs r"
            " JOIN pipeline_run_models m ON m.run_id = r.id WHERE r.id = :run_id"
        )
        read_back = cast("datetime", (await db.execute(stmt, {"run_id": run_id})).scalar_one())
    clock.set(read_back + timedelta(seconds=seconds))
    _log.info("sweep_test_idle", extra={"run_id": run_id, "idle_s": seconds, "mark": read_back.isoformat()})
    return read_back


async def read_count(maker: Maker, run_id: str) -> int:
    """`step_requeue_count` hiện tại của lượt."""
    async with maker() as db:
        stmt = select(PipelineRunModelsRow.step_requeue_count).where(PipelineRunModelsRow.run_id == run_id)
        return (await db.execute(stmt)).scalar_one()


async def sweep(maker: Maker, clock: FakeClock, *, batch: int = BATCH) -> None:
    """Một lượt quét với client Redis riêng, đóng lại sau (lõi không đóng `broker` của người gọi)."""
    broker = broker_redis()
    try:
        await run_stuck_pipeline_sweep(maker, broker, clock, batch=batch)
    finally:
        await broker.aclose()


@pytest.fixture
def clean_queues(messaging_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[SyncRedis]:
    """Hai hàng dùng chung cả phiên; `DEL` trước **và** sau mỗi test (BE-00 §12, test song song).

    Trần `LLEN` của lõi nhân `LLEN_WAIT_FACTOR` (NO-404): dưới tải `pytest -n` hai `LLEN` trên client mới
    (nối nguội) có lúc quá 1 s, lõi coi hai hàng "còn việc" và test luật thấy hàng rỗng. Test này kiểm
    **luật**, không kiểm trần; trần thật do hai test hàng treo ở `test_sweep_rules.py` kiểm (ghim
    `REAL_LLEN_TIMEOUT_S`). Nhân chứ không thay, để hằng lõi về 0 vẫn làm đỏ test luật.
    """
    monkeypatch.setattr(sweep_core, "LLEN_TIMEOUT_S", REAL_LLEN_TIMEOUT_S * LLEN_WAIT_FACTOR)
    client = broker_redis_sync()
    client.delete(ML_QUEUE, CPU_QUEUE)
    yield client
    client.delete(ML_QUEUE, CPU_QUEUE)


@pytest.fixture
def sweep_env(
    clean_queues: SyncRedis, db_url: str, monkeypatch: pytest.MonkeyPatch, local_storage: LocalDiskStorage
) -> Iterator[SyncRedis]:
    """Biến môi trường để hàm lịch dựng đúng DB/broker của test; cache đọc lại hai đầu."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    reset_database_settings_cache()
    reset_steps_settings_cache()
    reset_sync_bus_cache()
    yield clean_queues
    reset_database_settings_cache()
    reset_steps_settings_cache()
    reset_sync_bus_cache()


PAGE_BYTES: Final = b"%PDF-1.4 fake page for purge tests"
"""Nội dung tệp gốc tối thiểu — không ai đọc lại nó, chỉ cần `make_complete_upload` chấp nhận."""

RETENTION_S: Final = 604800
"""Bản sao hằng mặc định của `PIPELINE_ARTIFACT_RETENTION_S` (7 ngày) — chỉ để tính `age` test."""


@dataclass(frozen=True, slots=True)
class Scene:
    """Mọi id + khoá kho của một cảnh dọn artifact đã dựng."""

    run_id: str
    run_prefix: str
    run_artifact_key: str
    page_key: str
    original_key: str


async def build_scene(maker: Maker, storage: LocalDiskStorage, clock: Clock, *, status: str, age: timedelta) -> Scene:
    """Dựng lượt tải + lượt chạy pipeline, ghi một artifact dưới `runs/`, một trang, một gốc.

    `status`/`updated_at` đặt thẳng bằng SQL sau `start_run` (spec: không chờ `tests/helpers.py`
    của nhánh A — SQL tay + factory đã có của B5-06a/B2-04 đủ dựng cảnh).
    """
    async with maker() as db:
        owner = await make_user(db)
        project = await make_project(db, owner=owner)
        floor = await make_floor(db, project=project)
        upload = await make_complete_upload(
            db, storage, project=project, floor=floor, data=PAGE_BYTES, file_name="plan.pdf"
        )
        await db.commit()
    async with maker() as db:
        run = await start_run(db, upload_id=upload.id, clock=clock)
        await make_run_pins(db, run_id=run.id)
        await db.commit()

    upload_root = upload_prefix(project.id, floor.level_id, upload.id)
    run_artifact_key = f"{artifact_run_prefix(upload_root, run.id, 'preprocess')}out.json"
    await storage.put(run_artifact_key, b"{}", content_type="application/json", max_bytes=16)
    page_key = upload_page(project.id, floor.level_id, upload.id, 0)
    await storage.put(page_key, b"\x89PNG", content_type="image/png", max_bytes=16)

    async with maker() as db:
        await db.execute(
            text("UPDATE pipeline_runs SET status = :status, updated_at = :updated_at WHERE id = :run_id"),
            {"status": status, "updated_at": clock.now() - age, "run_id": run.id},
        )
        await db.commit()

    return Scene(
        run_id=run.id,
        run_prefix=run_prefix(project_id=project.id, level_id=floor.level_id, upload_id=upload.id, run_id=run.id),
        run_artifact_key=run_artifact_key,
        page_key=page_key,
        original_key=upload_original(project.id, floor.level_id, upload.id, "pdf"),
    )
