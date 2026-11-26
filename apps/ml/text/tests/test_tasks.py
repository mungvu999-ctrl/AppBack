"""J-case và M-case của task `ml.infer.text.read` (BE-00 §7 "Test task", cổng case).

Dịch vụ **thật**: Redis của Testcontainers, worker Celery thật nghe `ml.infer`, kho đĩa
`local_storage`, bộ dò của wheel và model rec tí hon chạy thật (K23). `step_done` đi tới
`pipeline.cpu` mà không worker nào nghe, nên đọc lại được bằng `LRANGE` và lọc theo
`run_id` riêng của từng test.
"""

import logging
import pickle
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]  # onnxruntime 1.30 không có py.typed
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from numpy.typing import NDArray

from apps.ml.runtime import device, gpu, loader
from apps.ml.runtime.loader import clear_session_cache, load_onnx
from apps.ml.runtime.settings import MlSettings
from apps.ml.runtime.tasks_util import InferContext, StepOutput
from apps.ml.text import tasks
from apps.ml.text.tests.helpers import (
    CONSTANT_TEXT,
    STEP,
    FlakyReads,
    infer_payload,
    put_sync,
    read_sync,
    rec_model,
    storage_ref,
)
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.messaging.celery_app import producer_app, send_task
from packages.messaging.redis import SyncRedis, broker_redis_sync
from packages.messaging.tasks import RETRY_EXHAUSTED, TASK_TIMEOUT
from packages.ml_contracts.artifacts import BoxPx, TextResult, text_from_json, text_to_json
from packages.ml_contracts.payloads import InferStepPayload
from packages.ml_contracts.synthetic import render_plan
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.messaging import WorkerFactory, queued_payloads

_log = logging.getLogger(__name__)

TASK = "ml.infer.text.read"
LISTEN, OUTBOX = "ml.infer", "pipeline.cpu"
WAIT_S = 90.0
PAGE_W, PAGE_H = 800, 600
PLAN = render_plan(7, width_px=PAGE_W, height_px=PAGE_H)


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
    """Trỏ ngữ cảnh worker vào kho và cấu hình của test (task nhập `infer_context` vào chính nó)."""
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


def onnx_payload(storage: LocalDiskStorage, **kwargs: object) -> InferStepPayload:
    """Payload trỏ model rec tí hon đã nằm trong kho dạng storage."""
    data = rec_model(**kwargs).SerializeToString()  # type: ignore[arg-type]  # kwargs của `rec_model`
    ref, key = storage_ref(data)
    put_sync(storage, key, data)
    return with_page(storage, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=ref))


def run_task(factory: WorkerFactory, broker: SyncRedis, payload: InferStepPayload, count: int = 1) -> None:
    """Gửi task rồi chờ đủ `count` thông điệp `step_done` của lượt chạy ấy."""
    with factory([LISTEN]):
        for _ in range(count):
            send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) >= count, f"{count} step_done")


def test_text_read__J01(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Chạy đúng: `text.json` giải được, có mục `3.600` trùng vùng chữ của đáp án."""
    payload = onnx_payload(onnx_context)
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["model_version_id"]) == ("completed", payload.model.version_id)
    assert result["artifact_keys"] == [f"{payload.artifact_prefix}text.json"]
    written = read_sync(onnx_context, f"{payload.artifact_prefix}text.json")
    assert written is not None
    items = text_from_json(written).items
    assert items, "bộ dò thật không thấy chữ nào trên trang tổng hợp"
    assert {item.text for item in items} == {CONSTANT_TEXT}
    assert any(_overlaps(item.box, answer.box) for item in items for answer in PLAN.texts)


def _overlaps(one: BoxPx, two: BoxPx) -> bool:
    """Hai hộp có phần chung dương (đủ để nói mục nằm đúng vùng chữ của đáp án)."""
    return min(one.x_max, two.x_max) > max(one.x_min, two.x_min) and min(one.y_max, two.y_max) > max(
        one.y_min, two.y_min
    )


def test_text_read__J06(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Giao lặp: cùng khoá, bytes bằng nhau, mỗi lượt một `step_done` giống nhau."""
    payload = onnx_payload(onnx_context)
    key = f"{payload.artifact_prefix}text.json"
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt giao đầu")
        first = read_sync(onnx_context, key)
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 2, "lượt giao lặp")
    assert read_sync(onnx_context, key) == first
    one, two = results(broker, payload.run_id)
    assert {k: v for k, v in one.items() if k != "duration_ms"} == {k: v for k, v in two.items() if k != "duration_ms"}


def test_text_read__J02(
    broker: SyncRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Kho hỏng tạm hai lượt rồi đạt: task thử lại và kết thúc `completed`, đúng một thông điệp."""
    flaky = FlakyReads(tmp_path / "flaky", DEPENDENCY_UNAVAILABLE.error(retry_after=5), failures=2)
    context_of(monkeypatch, flaky, MlSettings(ml_backend="fake"))
    payload = with_page(flaky, infer_payload(width=PAGE_W, height=PAGE_H))
    run_task(celery_worker_factory, broker, payload)
    assert [item["status"] for item in results(broker, payload.run_id)] == ["completed"]
    assert flaky.attempts == 3


def test_text_read__J02_exhausted(
    broker: SyncRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Kho hỏng mãi: hết bậc thử lại → `failed` `RETRY_EXHAUSTED`, đúng một thông điệp."""
    broken = FlakyReads(tmp_path / "flaky", DEPENDENCY_UNAVAILABLE.error(retry_after=5), failures=-1)
    context_of(monkeypatch, broken, MlSettings(ml_backend="fake"))
    payload = with_page(broken, infer_payload(width=PAGE_W, height=PAGE_H))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"], result["artifact_keys"]) == ("failed", RETRY_EXHAUSTED, [])


def test_text_read__J03(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Model rec thiếu metadata `character`: đúng một `failed` `MODEL_FORMAT_UNSUPPORTED`, không artifact."""
    payload = onnx_payload(onnx_context, characters=None)
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_FORMAT_UNSUPPORTED")
    assert read_sync(onnx_context, f"{payload.artifact_prefix}text.json") is None


def test_text_read__J05(
    broker: SyncRedis,
    fake_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Quá hạn mềm giữa bước: `failed` `TASK_TIMEOUT` đúng một lần, không thử lại."""

    def timing_out(image: NDArray[np.uint8], payload: InferStepPayload, reader: object) -> StepOutput:
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(tasks, "_read", timing_out)
    payload = with_page(fake_context, infer_payload(width=PAGE_W, height=PAGE_H))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", TASK_TIMEOUT)


def test_text_read__J08(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Thông điệp độc không chạy và không gửi gì; worker vẫn làm thông điệp hợp lệ ngay sau."""
    good = with_page(fake_context, infer_payload(width=PAGE_W, height=PAGE_H))
    poison = {"schema_version": 1, "run_id": good.run_id, "step": STEP}
    with celery_worker_factory([LISTEN]):
        producer_app().send_task(TASK, args=[poison], queue=LISTEN)
        send_task(TASK, good)
        wait_for(lambda: bool(results(broker, good.run_id)), "thông điệp tốt sau thông điệp độc")
    assert [item["status"] for item in results(broker, good.run_id)] == ["completed"]
    assert broker.llen(LISTEN) == 0


def test_text_read__M01(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """`ML_BACKEND=fake`: hai lượt ra `text.json` bằng nhau từng byte và khớp đáp án."""
    payload = with_page(fake_context, infer_payload(width=PAGE_W, height=PAGE_H))
    key = f"{payload.artifact_prefix}text.json"
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt đầu")
        first = read_sync(fake_context, key)
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 2, "lượt hai")
    second = read_sync(fake_context, key)
    assert first == second == text_to_json(TextResult(items=PLAN.texts))


def test_text_read__M02(
    broker: SyncRedis,
    onnx_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Checksum lệch: `failed` `MODEL_CHECKSUM_MISMATCH` và **không** phiên ORT nào được dựng."""
    built: list[int] = []
    monkeypatch.setattr(loader, "_session", lambda *args: built.append(1))
    data = rec_model().SerializeToString()
    ref, key = storage_ref(data, checksum="0" * 64)
    put_sync(onnx_context, key, data)
    payload = with_page(onnx_context, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_CHECKSUM_MISMATCH")
    assert built == []


def test_text_read__M03(
    broker: SyncRedis,
    onnx_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Trọng số kiểu `.pt` (zip pickle): `failed` mã bộ nạp, `pickle.loads` không được gọi."""
    monkeypatch.setattr(pickle, "loads", _forbidden)
    data = b"PK\x03\x04" + pickle.dumps({"state_dict": [1, 2, 3]})
    ref, key = storage_ref(data)
    put_sync(onnx_context, key, data)
    payload = with_page(onnx_context, infer_payload(width=PAGE_W, height=PAGE_H, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_FORMAT_UNSUPPORTED")


def _forbidden(*args: object, **kwargs: object) -> object:
    """Thay `pickle.loads` trong lượt test M03: gọi tới đây là vi phạm K12."""
    raise AssertionError("pickle.loads bị cấm trên đường nạp trọng số (K12)")


def test_importing_the_step_does_not_pull_torch() -> None:
    """Nhập `apps.ml.text` trong tiến trình sạch không kéo `torch` theo (M03, BE-00 §12)."""
    code = "import apps.ml.text, sys; assert 'torch' not in sys.modules"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr.decode()


def test_text_read__M04(
    broker: SyncRedis,
    onnx_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """`ML_DEVICE=cuda` và máy "có" GPU: phiên vẫn chỉ chạy CPU, `gpu_slot` không được gọi."""
    monkeypatch.setenv("ML_DEVICE", "cuda")
    monkeypatch.setattr(gpu, "gpu_slot", _forbidden_slot)
    monkeypatch.setattr(device, "resolve_device", _forbidden_device)
    sessions: list[ort.InferenceSession] = []

    async def spying(*args: object, **kwargs: object) -> ort.InferenceSession:
        session = await load_onnx(*args, **kwargs)  # type: ignore[arg-type]  # chuyển tiếp nguyên tham số
        sessions.append(session)
        return session

    monkeypatch.setattr(tasks, "load_onnx", spying)
    payload = onnx_payload(onnx_context)
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert result["status"] == "completed"
    assert [session.get_providers() for session in sessions] == [["CPUExecutionProvider"]]


def _forbidden_slot(**kwargs: object) -> object:
    """Thay `gpu_slot` trong lượt test M04: bước đọc chữ không bao giờ giữ khoá GPU."""
    raise AssertionError("bước dimensionReading không được lấy khoá gpu:0")


def _forbidden_device(setting: str) -> str:
    """Thay `resolve_device` trong lượt test M04: suy luận luôn CPU, không hỏi `torch.cuda`."""
    raise AssertionError("bước suy luận không được chọn thiết bị (BE-00 §9)")


def test_text_read_rejects_another_family(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Payload của bước khác: `failed` `MODEL_VERSION_FAMILY_MISMATCH`, không đọc trang."""
    payload = with_page(fake_context, infer_payload(width=PAGE_W, height=PAGE_H, step="wallSegmentation"))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", tasks.MODEL_VERSION_FAMILY_MISMATCH)


def test_text_read_with_inactive_model_writes_no_items(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """`ModelRef` cổ điển (chưa có bản kích hoạt): `TextResult` rỗng và bước vẫn `completed` (K19)."""
    payload = with_page(onnx_context, infer_payload(width=PAGE_W, height=PAGE_H))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert result["status"] == "completed"
    written = read_sync(onnx_context, f"{payload.artifact_prefix}text.json")
    assert written == text_to_json(TextResult(items=()))
