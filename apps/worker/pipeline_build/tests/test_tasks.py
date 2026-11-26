"""Task `pipeline.build.run` (B5-05 [6] "Task", [8] "Task"; case J01, J03, J06, J08, J10).

Task cùng hàng `pipeline.cpu` với thông điệp nó gửi (`pipeline.orchestrate.step_done`) nên
kiểm bằng `task.apply(args=[…])` (BE-00 §7 "Test task"), không dựng worker thật; hàng đọc
lại bằng `LRANGE` qua `queued_payloads`. Kho là `local_storage` thật (K23).
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from typing import cast

import pytest

from apps.worker.pipeline_build import tasks
from apps.worker.pipeline_build.artifacts import input_key, layer_key, walls_mask_key
from apps.worker.pipeline_build.build import BuiltLayer
from apps.worker.pipeline_build.tasks import BuildContext, build_pipeline_layer
from packages.core.clock import SystemClock
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.core.ids import new_id
from packages.messaging.payloads.pipeline import BuildStepPayload
from packages.messaging.redis import SyncRedis, broker_redis_sync
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.artifacts import (
    ObjectsResult,
    TextResult,
    WallsResult,
    encode_mask,
    encode_rgb_png,
    objects_to_json,
    text_to_json,
    walls_to_json,
)
from packages.ml_contracts.families import ModelFamily
from packages.ml_contracts.synthetic import render_plan
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.messaging import queued_payloads

OUTBOX = "pipeline.cpu"
_LEVEL = "L-ABCDEFGHIJ"


def _some_id(prefix: str) -> str:
    """Id mới đúng tiền tố, cho id không đi qua `is_spatial_id`."""
    return new_id(prefix, SystemClock())  # type: ignore[arg-type]  # prefix luôn hợp `IdPrefix` ở test này


def _payload(**overrides: object) -> BuildStepPayload:
    """`BuildStepPayload` hợp lệ; ảnh 1600x1200 mặc định (B5-05 dữ kiện đã tra)."""
    run_id = str(overrides.pop("run_id", _some_id("run")))
    run_prefix = f"projects/{_some_id('prj')}/floors/{_LEVEL}/uploads/{_some_id('upl')}/runs/{run_id}/"
    base: dict[str, object] = {
        "run_id": run_id,
        "level_id": _LEVEL,
        "run_prefix": run_prefix,
        "width_px": 1600,
        "height_px": 1200,
        "fallback_mm_per_px": "10",
    }
    base.update(overrides)
    return BuildStepPayload.model_validate(base)


def _seed_inputs(storage: LocalDiskStorage, payload: BuildStepPayload, seed: int = 100) -> None:
    """Ghi ba artifact ML seed `seed` (`render_plan`) dưới `run_prefix` của `payload`."""
    plan = render_plan(seed, width_px=payload.width_px, height_px=payload.height_px)
    steps: tuple[tuple[ModelFamily, bytes], ...] = (
        ("wallSegmentation", walls_to_json(WallsResult(walls=plan.walls))),
        ("openingAndFurnitureDetection", objects_to_json(ObjectsResult(detections=plan.detections))),
        ("dimensionReading", text_to_json(TextResult(items=plan.texts))),
    )
    for step, data in steps:
        key = input_key(payload.run_prefix, step)
        asyncio.run(storage.put(key, data, content_type="application/json", max_bytes=len(data) + 1))


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Client broker thật, hàng `pipeline.cpu` sạch ở đầu và cuối test."""
    client = broker_redis_sync()
    client.delete(OUTBOX)
    yield client
    client.delete(OUTBOX)
    client.close()


@pytest.fixture
def context(local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch) -> LocalDiskStorage:
    """Trỏ `tasks.build_context` vào kho tạm của test (task nhập `build_context` vào chính nó)."""
    monkeypatch.setattr(tasks, "build_context", lambda: BuildContext(storage=local_storage))
    return local_storage


def _apply(payload: BuildStepPayload) -> None:
    """Chạy task tại chỗ, không `task_always_eager` (BE-00 §12)."""
    build_pipeline_layer.apply(args=[payload.model_dump(mode="json")])


def _results(client: SyncRedis, run_id: str) -> list[dict[str, object]]:
    """Mọi `step_done` của một lượt chạy trên `pipeline.cpu`."""
    return [item for item in queued_payloads(client, OUTBOX) if item.get("run_id") == run_id]


def test_build_pipeline_layer__J01(broker: SyncRedis, context: LocalDiskStorage) -> None:
    """Ba artifact seed 100 → `layer.json` giải được, đúng một `step_done` `completed`."""
    payload = _payload()
    _seed_inputs(context, payload)
    key = layer_key(payload.run_prefix)
    _apply(payload)
    (result,) = _results(broker, payload.run_id)
    assert (result["status"], result["artifact_keys"]) == ("completed", [key])
    written = asyncio.run(_read(context, key))
    assert written is not None
    BuiltLayer.from_json(written)


def test_build_pipeline_layer__J06(broker: SyncRedis, context: LocalDiskStorage) -> None:
    """Giao hai lần: byte `layer.json` giữ nguyên, hai `step_done` cùng khoá."""
    payload = _payload()
    _seed_inputs(context, payload)
    key = layer_key(payload.run_prefix)
    _apply(payload)
    first = asyncio.run(_read(context, key))
    _apply(payload)
    second = asyncio.run(_read(context, key))
    assert first == second
    results = _results(broker, payload.run_id)
    assert len(results) == 2
    assert all(result["artifact_keys"] == [key] for result in results)


def test_build_pipeline_layer__J10(
    broker: SyncRedis, context: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lượt đầu vá `send_task` thành không làm gì (ghi xong, chết trước khi gửi) → giao lại → không dựng lại."""
    payload = _payload()
    _seed_inputs(context, payload)
    key = layer_key(payload.run_prefix)
    with monkeypatch.context() as first_delivery:
        first_delivery.setattr(tasks, "send_task", lambda *args, **kwargs: None)
        _apply(payload)
    first = asyncio.run(_read(context, key))
    assert first is not None
    assert broker.llen(OUTBOX) == 0
    _apply(payload)
    second = asyncio.run(_read(context, key))
    assert first == second
    (result,) = _results(broker, payload.run_id)
    assert result["status"] == "completed"


def test_build_pipeline_layer__J03(broker: SyncRedis, context: LocalDiskStorage) -> None:
    """J03 gộp một tên (`case_gate` chỉ nhận đúng hậu tố `__J03`): artifact thiếu rồi artifact hỏng.

    Thiếu `walls.json` → `failed` `PIPELINE_ARTIFACT_MISSING`, không `layer.json`; `objects.json`
    khoá lạ → `failed` `PIPELINE_ARTIFACT_INVALID`. Hai lượt chạy khác `run_id` nên không lẫn nhau.
    """
    missing = _payload()
    _apply(missing)
    (result,) = _results(broker, missing.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "PIPELINE_ARTIFACT_MISSING")
    assert asyncio.run(_read(context, layer_key(missing.run_prefix))) is None

    payload = _payload()
    plan = render_plan(100, width_px=payload.width_px, height_px=payload.height_px)
    asyncio.run(
        context.put(
            input_key(payload.run_prefix, "wallSegmentation"),
            walls_to_json(WallsResult(walls=plan.walls)),
            content_type="application/json",
            max_bytes=1 << 20,
        )
    )
    asyncio.run(
        context.put(
            input_key(payload.run_prefix, "openingAndFurnitureDetection"),
            b'{"schemaVersion": 1, "khoaLa": []}',
            content_type="application/json",
            max_bytes=1 << 20,
        )
    )
    asyncio.run(
        context.put(
            input_key(payload.run_prefix, "dimensionReading"),
            text_to_json(TextResult(items=plan.texts)),
            content_type="application/json",
            max_bytes=1 << 20,
        )
    )
    _apply(payload)
    (result,) = _results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "PIPELINE_ARTIFACT_INVALID")


def test_check_run_prefix_rejects_mismatched_run_id() -> None:
    """`_check_run_prefix` (phòng thủ kép, B5-05 [6] bước 1): `run_prefix` không khớp `run_id` của chính payload.

    `BuildStepPayload.model_validate` đã chặn tổ hợp này ở lớp thông điệp (poison, J08) — hàm
    này kiểm hàm nội bộ trực tiếp, không qua hàng đợi, để đường phòng thủ không phải mã chết.
    """
    payload = _payload()
    other_run = _some_id("run")
    bad = BuildStepPayload.model_construct(
        schema_version=1,
        run_id=payload.run_id,
        level_id=payload.level_id,
        run_prefix=f"projects/prj_x/runs/{other_run}/",
        width_px=payload.width_px,
        height_px=payload.height_px,
        fallback_mm_per_px=payload.fallback_mm_per_px,
    )
    with pytest.raises(PermanentError) as excinfo:
        tasks._check_run_prefix(bad)
    assert excinfo.value.code == "PIPELINE_ARTIFACT_INVALID"


def test_read_artifact_rejects_over_max_bytes(context: LocalDiskStorage) -> None:
    """Gom bytes vượt `max_bytes` giữa chừng (dù bản đọc trọn vẹn nhỏ hơn) → `PIPELINE_ARTIFACT_INVALID`."""
    key = "projects/p/floors/L-ABCDEFGHIJ/uploads/u/runs/r/wallSegmentation/walls.json"
    asyncio.run(context.put(key, b"0123456789", content_type="application/json", max_bytes=1 << 20))
    with pytest.raises(PermanentError) as excinfo:
        asyncio.run(tasks._read_artifact(context, key, max_bytes=5))
    assert excinfo.value.code == "PIPELINE_ARTIFACT_INVALID"


def test_read_artifact_reraises_non_missing_app_error(context: LocalDiskStorage) -> None:
    """`AppError` khác `NOT_FOUND` (vd `DEPENDENCY_UNAVAILABLE`) nổi nguyên trạng, cho `define_task` thử lại (J02)."""

    async def failing_open_read(key: str, *, chunk_size: int = 0) -> AsyncIterator[bytes]:
        """Thay `open_read`: luôn báo kho bận, không bao giờ yield (đủ để là async generator)."""
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=5)
        yield b""  # không bao giờ chạy tới; chỉ để hàm là async generator thật

    context.open_read = failing_open_read  # type: ignore[method-assign]  # vá thẳng instance cho test này
    with pytest.raises(AppError) as excinfo:
        asyncio.run(tasks._read_artifact(context, "projects/p/runs/r/wallSegmentation/walls.json", max_bytes=100))
    assert excinfo.value.code is DEPENDENCY_UNAVAILABLE


def test_build_context_builds_real_storage_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build_context`/`_build_context`/`reset_build_context`: dựng lười, nhớ theo tiến trình (K22)."""
    from packages.storage import factory as storage_factory
    from packages.storage import settings as storage_settings_module

    monkeypatch.setattr(storage_settings_module, "get_storage_settings", lambda: object())
    monkeypatch.setattr(storage_factory, "create_storage", lambda *args, **kwargs: "fake-storage")
    tasks.reset_build_context()
    try:
        assert cast(object, tasks.build_context().storage) == "fake-storage"
        assert cast(object, tasks.build_context().storage) == "fake-storage"
    finally:
        tasks.reset_build_context()


def test_build_pipeline_layer_wraps_build_layer_value_error(
    broker: SyncRedis, context: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_layer` (việc C) ném `ValueError` → `failed` `PIPELINE_BUILD_INVALID`, không `layer.json` (B5-05 [6])."""

    def _broken(**kwargs: object) -> None:
        """Thay `build_layer`: mô phỏng đầu vào mà bước C từ chối."""
        raise ValueError("đầu vào xấu")

    # `tasks` nhập `build_layer` vào không gian tên của nó, nên vá ở đây chứ không ở module `build`.
    monkeypatch.setattr(tasks, "build_layer", _broken)
    payload = _payload()
    _seed_inputs(context, payload)
    _apply(payload)
    (result,) = _results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "PIPELINE_BUILD_INVALID")
    assert asyncio.run(_read(context, layer_key(payload.run_prefix))) is None


def test_build_pipeline_layer_rejects_perspective_view(
    broker: SyncRedis, context: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BENCH3D-19: lớp dựng ra phần lớn tường xiên (ảnh phối cảnh) → `failed` `NOT_A_FLOOR_PLAN`, không `layer.json`;
    mặt bằng tổng hợp thường (các test J01…) vẫn qua `not_a_floor_plan`."""
    monkeypatch.setattr(tasks, "not_a_floor_plan", lambda layer: 0.5)
    payload = _payload()
    _seed_inputs(context, payload)
    _apply(payload)
    (result,) = _results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "NOT_A_FLOOR_PLAN")
    assert asyncio.run(_read(context, layer_key(payload.run_prefix))) is None


def test_reset_pipeline_build_settings_cache_forces_reread() -> None:
    """`reset_pipeline_build_settings_cache`: đọc lại `PipelineBuildSettings` ở lần gọi sau."""
    from apps.worker.pipeline_build.settings import get_pipeline_build_settings, reset_pipeline_build_settings_cache

    first = get_pipeline_build_settings()
    reset_pipeline_build_settings_cache()
    second = get_pipeline_build_settings()
    assert first is not second
    assert second.PIPELINE_ARTIFACT_MAX_BYTES == 16_777_216


def test_build_pipeline_layer__J08(
    broker: SyncRedis, context: LocalDiskStorage, caplog: pytest.LogCaptureFixture
) -> None:
    """Hai thông điệp độc của [8] trong một hàm (`case_gate` chỉ nhận hậu tố `__J08`, không `[…]`).

    `schema_version` 2 (bản khác) và `fallback_mm_per_px: "0"` (sai hợp đồng trường) đều bị
    lớp giải chặn: log `poison_message`, task không chạy, hàng `pipeline.cpu` rỗng.
    """
    poisons: list[dict[str, object]] = [
        {"schema_version": 2, "run_id": _some_id("run")},
        {**_payload().model_dump(mode="json", by_alias=False), "fallback_mm_per_px": "0"},
    ]
    for poison in poisons:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            build_pipeline_layer.apply(args=[poison])
        assert any(record.getMessage() == "poison_message" for record in caplog.records), poison
        assert _results(broker, str(poison["run_id"])) == []
    assert broker.llen(OUTBOX) == 0


def test_build_pipeline_layer_reads_page_for_suggestions(
    broker: SyncRedis, context: LocalDiskStorage, caplog: pytest.LogCaptureFixture
) -> None:
    """`page_key` có trang + `walls.png` → dựng xong không cảnh báo; trang vắng → vẫn dựng, log `page_unavailable`."""
    page_key = f"projects/{_some_id('prj')}/floors/{_LEVEL}/uploads/{_some_id('upl')}/pages/0.png"
    payload = _payload(page_key=page_key)
    _seed_inputs(context, payload)
    plan = render_plan(100, width_px=payload.width_px, height_px=payload.height_px)
    for key, data in (
        (page_key, encode_rgb_png(plan.pixels)),
        (walls_mask_key(payload.run_prefix), encode_mask(plan.walls_mask)),
    ):
        asyncio.run(context.put(key, data, content_type="image/png", max_bytes=len(data) + 1))
    missing = _payload(page_key=f"{page_key}.vang")
    _seed_inputs(context, missing)
    with caplog.at_level(logging.WARNING):
        _apply(payload)
        assert not any(r.getMessage() == "pipeline_build_page_unavailable" for r in caplog.records)
        _apply(missing)
    assert any(r.getMessage() == "pipeline_build_page_unavailable" for r in caplog.records)
    for item in (payload, missing):
        (result,) = _results(broker, item.run_id)
        assert result["status"] == "completed"
    # L2-N02: trang vắng → lớp dựng mang cờ suy giảm cho quality; đọc được trang hay thông điệp không có trang → không.
    flags = []
    for item in (payload, missing):
        raw = asyncio.run(_read(context, layer_key(item.run_prefix)))
        assert raw is not None
        flags.append(BuiltLayer.from_json(raw).page_unavailable)
    assert flags == [False, True]


def test_build_without_page_key_has_no_degraded_flag(broker: SyncRedis, context: LocalDiskStorage) -> None:
    """Thông điệp cũ không có `page_key`: không có trang để mất → không gắn `page_unavailable` (L2-N02)."""
    payload = _payload()
    _seed_inputs(context, payload)
    _apply(payload)
    raw = asyncio.run(_read(context, layer_key(payload.run_prefix)))
    assert raw is not None
    assert BuiltLayer.from_json(raw).page_unavailable is False


def test_read_page_reraises_transient_storage_error(context: LocalDiskStorage) -> None:
    """L2-N02 B: kho bận (`DEPENDENCY_UNAVAILABLE`) khi đọc trang không bị nuốt thành `None` mà nổi lên cho
    `define_task` thử lại; chỉ lỗi vĩnh viễn (vắng, quá trần, hỏng) mới thành chế độ suy giảm."""

    async def failing_open_read(key: str, *, chunk_size: int = 0) -> AsyncIterator[bytes]:
        """Thay `open_read`: luôn báo kho bận."""
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=5)
        yield b""  # không bao giờ chạy tới; chỉ để hàm là async generator thật

    context.open_read = failing_open_read  # type: ignore[method-assign]  # vá thẳng instance cho test này
    payload = _payload(page_key=f"projects/{_some_id('prj')}/floors/{_LEVEL}/uploads/{_some_id('upl')}/pages/0.png")
    with pytest.raises(AppError) as excinfo:
        asyncio.run(tasks._read_page(context, payload, max_bytes=100))
    assert excinfo.value.code is DEPENDENCY_UNAVAILABLE


async def _read(storage: LocalDiskStorage, key: str) -> bytes | None:
    """Nội dung object, hay `None` khi chưa có."""
    if await storage.stat(key) is None:
        return None
    return b"".join([chunk async for chunk in storage.open_read(key)])


def test_vector_page_reads_preprocess_vectors(local_storage: LocalDiskStorage) -> None:
    """Không `px_per_paper_mm` → `True` khỏi đọc kho; có thì `True` chỉ khi `preprocess/vectors.json` có mặt."""
    paper = _payload(px_per_paper_mm=5.9)
    assert asyncio.run(tasks._vector_page(local_storage, _payload()))
    assert not asyncio.run(tasks._vector_page(local_storage, paper))
    key = f"{paper.run_prefix}preprocess/vectors.json"
    asyncio.run(local_storage.put(key, b"{}", content_type="application/json", max_bytes=3))
    assert asyncio.run(tasks._vector_page(local_storage, paper))
