"""J-case và M-case của task `ml.infer.walls.segment` (BE-00 §7 "Test task", cổng case).

Dịch vụ **thật**: Redis của Testcontainers, worker Celery thật nghe `ml.infer`, kho đĩa
`local_storage`, `onnxruntime` chạy model tí hon thật (K23) — không mock broker, kho hay
phiên ONNX. `step_done` đi tới `pipeline.cpu` mà không worker nào nghe, nên đọc lại được
bằng `LRANGE` và lọc theo `run_id` riêng của từng test.
"""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from apps.ml.runtime import loader
from apps.ml.runtime.loader import clear_session_cache
from apps.ml.runtime.settings import MlSettings
from apps.ml.runtime.tasks_util import InferContext
from apps.ml.walls import tasks
from apps.ml.walls.tests.helpers import STEP, classic_ref, infer_payload, onnx_ref, put_sync, read_sync
from packages.messaging.celery_app import producer_app, send_task
from packages.messaging.redis import SyncRedis, broker_redis_sync
from packages.ml_contracts.artifacts import decode_mask, walls_from_json
from packages.ml_contracts.payloads import InferStepPayload
from packages.ml_contracts.synthetic import render_plan
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.messaging import WorkerFactory, queued_payloads

TASK = "ml.infer.walls.segment"
LISTEN, OUTBOX = "ml.infer", "pipeline.cpu"
WAIT_S = 180.0
PLAN = render_plan(101)
PAGE_H, PAGE_W = PLAN.walls_mask.shape


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Client broker thật, hai hàng của test sạch ở đầu và cuối."""
    client = broker_redis_sync()
    client.delete(LISTEN, OUTBOX)
    clear_session_cache()
    yield client
    client.delete(LISTEN, OUTBOX)
    clear_session_cache()
    client.close()


def context_of(monkeypatch: pytest.MonkeyPatch, storage: LocalDiskStorage, settings: MlSettings) -> LocalDiskStorage:
    """Trỏ ngữ cảnh worker vào kho và cấu hình của test.

    Chỉ vá `tasks.infer_context`: `run_step` nhận kho qua tham số từ chính thân task, và
    `step.segment_page` nhận `backend` qua tham số nên không đọc ngữ cảnh lần nào.
    """
    monkeypatch.setattr(tasks, "infer_context", lambda: InferContext(storage=storage, settings=settings))
    return storage


@pytest.fixture
def onnx_context(local_storage: LocalDiskStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalDiskStorage:
    """Ngữ cảnh chạy ONNX thật (`ML_BACKEND=onnx`), thư mục model ghim rỗng."""
    settings = MlSettings(ml_backend="onnx", ml_models_dir=str(tmp_path / "models"), ml_ort_threads=1)
    return context_of(monkeypatch, local_storage, settings)


@pytest.fixture
def fake_context(local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch) -> LocalDiskStorage:
    """Ngữ cảnh bộ giả (`ML_BACKEND=fake`): không model nào được nạp."""
    return context_of(monkeypatch, local_storage, MlSettings(ml_backend="fake"))


def results(client: SyncRedis, run_id: str) -> list[dict[str, object]]:
    """Mọi `step_done` của một lượt chạy, đã bóc vỏ kombu."""
    return [item for item in queued_payloads(client, OUTBOX) if item.get("run_id") == run_id]


def wait_for(predicate: Callable[[], bool], what: str) -> None:
    """Chờ tới `WAIT_S` cho một điều kiện; quá hạn là hỏng, không im lặng bỏ qua."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"quá {WAIT_S} giây mà chưa: {what}")


def with_page(storage: LocalDiskStorage, payload: InferStepPayload) -> InferStepPayload:
    """Đặt trang tổng hợp của test vào kho dưới `page_key` của payload."""
    put_sync(storage, payload.page_key, PLAN.image_png, "image/png")
    return payload


def classic_payload(storage: LocalDiskStorage) -> InferStepPayload:
    """Payload đường lùi cổ điển trên trang `render_plan(101)`."""
    return with_page(storage, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=classic_ref()))


def run_task(factory: WorkerFactory, broker: SyncRedis, payload: InferStepPayload, count: int = 1) -> None:
    """Gửi task rồi chờ đủ `count` thông điệp `step_done` của lượt chạy ấy."""
    with factory([LISTEN]):
        for _ in range(count):
            send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) >= count, f"{count} step_done")


def keys_of(payload: InferStepPayload) -> list[str]:
    """Hai khoá artifact của bước, đúng thứ tự tăng mà `run_step` gửi đi."""
    return [f"{payload.artifact_prefix}walls.json", f"{payload.artifact_prefix}walls.png"]


def test_segment_walls__J01(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """`ModelRef` chưa kích hoạt: hai artifact ở `artifact_prefix`, một `step_done` `completed`."""
    payload = classic_payload(onnx_context)
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["model_version_id"]) == ("completed", None)
    assert result["artifact_keys"] == keys_of(payload)
    written = read_sync(onnx_context, keys_of(payload)[0])
    assert written is not None
    assert walls_from_json(written).walls, "đường lùi cổ điển không thấy tường nào trên trang tổng hợp"
    png = read_sync(onnx_context, keys_of(payload)[1])
    assert png is not None
    assert decode_mask(png, width_px=PAGE_W, height_px=PAGE_H).shape == (PAGE_H, PAGE_W)


def test_segment_walls__J01_onnx(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Model SegFormer tí hon trong kho, checksum đúng: `completed`, `model_version_id` là id bản."""
    ref = onnx_ref(onnx_context)
    payload = with_page(onnx_context, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["model_version_id"]) == ("completed", ref.version_id)
    assert result["artifact_keys"] == keys_of(payload)
    written = read_sync(onnx_context, keys_of(payload)[0])
    assert written is not None
    assert walls_from_json(written).walls


def test_segment_walls__J06(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Giao lặp: cùng khoá, bytes bằng nhau, hai `step_done` giống nhau trừ `duration_ms`."""
    payload = classic_payload(onnx_context)
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt giao đầu")
        first = [read_sync(onnx_context, key) for key in keys_of(payload)]
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 2, "lượt giao lặp")
    assert [read_sync(onnx_context, key) for key in keys_of(payload)] == first
    one, two = results(broker, payload.run_id)
    assert {k: v for k, v in one.items() if k != "duration_ms"} == {k: v for k, v in two.items() if k != "duration_ms"}


def test_segment_walls__J03(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Model ra `[1, 3, 256, 256]`: đúng một `failed` `MODEL_FORMAT_UNSUPPORTED`, không artifact."""
    ref = onnx_ref(onnx_context, wrong_output=True)
    payload = with_page(onnx_context, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"], result["artifact_keys"]) == (
        "failed",
        "MODEL_FORMAT_UNSUPPORTED",
        [],
    )
    assert [read_sync(onnx_context, key) for key in keys_of(payload)] == [None, None]


def test_segment_walls__J08(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Thông điệp độc (`schema_version: 2`) không chạy và không gửi gì; payload tốt sau vẫn chạy."""
    good = classic_payload(fake_context)
    poison = {"schema_version": 2, "bad": 1}
    with celery_worker_factory([LISTEN]):
        producer_app().send_task(TASK, args=[poison], queue=LISTEN)
        send_task(TASK, good)
        wait_for(lambda: bool(results(broker, good.run_id)), "thông điệp tốt sau thông điệp độc")
    assert [item["status"] for item in results(broker, good.run_id)] == ["completed"]
    assert len(queued_payloads(broker, OUTBOX)) == 1, "thông điệp độc không được sinh step_done nào"
    assert broker.llen(LISTEN) == 0


def test_segment_walls__M01(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """`ML_BACKEND=fake`: hai lượt ra `walls.json` bằng nhau từng byte, và có tường của đáp án."""
    payload = with_page(fake_context, infer_payload(width=PAGE_W, height=PAGE_H))
    key = keys_of(payload)[0]
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt đầu")
        first = read_sync(fake_context, key)
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 2, "lượt hai")
    assert first is not None
    assert read_sync(fake_context, key) == first
    assert walls_from_json(first).walls, "bộ giả phải trả đúng mặt nạ đáp án của trang"


def test_segment_walls__M02(
    broker: SyncRedis,
    onnx_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Checksum lệch: `failed` `MODEL_CHECKSUM_MISMATCH` và **không** phiên ORT nào được dựng."""
    built: list[int] = []
    monkeypatch.setattr(loader, "_session", lambda *args: built.append(1))
    ref = onnx_ref(onnx_context, checksum="0" * 64)
    payload = with_page(onnx_context, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_CHECKSUM_MISMATCH")
    assert built == []


def test_segment_walls_rejects_another_family(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Payload của bước khác: `failed` `MODEL_VERSION_FAMILY_MISMATCH`, trang không cần có trong kho."""
    payload = infer_payload(width=PAGE_W, height=PAGE_H, step="dimensionReading")
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", tasks.MODEL_VERSION_FAMILY_MISMATCH)
    assert result["step"] != STEP
