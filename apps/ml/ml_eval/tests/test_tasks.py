"""J-case và M-case của task `ml.infer.ml_eval.evaluate_version` (BE-00 §7 "Test task", cổng case).

Dịch vụ **thật**: Redis của Testcontainers, worker Celery thật nghe `ml.infer`, kho đĩa
`local_storage`, `onnxruntime` thật trong hộp cát thật (K23). `evaluation_done` đi tới
hàng `default` mà không worker nào nghe, nên đọc lại được bằng `LRANGE` và lọc theo
`version_id` riêng của từng test.
"""

import functools
import pickle
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from apps.ml.ml_eval import tasks
from apps.ml.ml_eval.tests.helpers import (
    NUM_CLASSES,
    OBJECTS,
    SEEDS,
    const_yolo,
    context,
    eval_payload,
    pinned_ref,
    pinned_yolo_ref,
    stored_model,
)
from apps.ml.objects.labels import labels_for
from apps.ml.objects.tests.helpers import FlakyReads, put_sync
from apps.ml.objects.tests.onnx_models import load_session, make_const_yolo, storage_ref, yolo_output
from apps.ml.runtime import gpu
from apps.ml.runtime.errors import MODEL_CHECKSUM_MISMATCH, MODEL_FORMAT_UNSUPPORTED
from apps.ml.runtime.loader import clear_session_cache
from apps.ml.runtime.tasks_util import reset_infer_context
from apps.ml.training_yolo.tests.support import TRAIN_SEEDS, VALIDATION_SEEDS
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.messaging.celery_app import producer_app, send_task
from packages.messaging.redis import SyncRedis, broker_redis_sync
from packages.messaging.tasks import TASK_TIMEOUT
from packages.ml_contracts import synthetic
from packages.ml_contracts.families import FAMILY_METRIC
from packages.ml_contracts.payloads import EvaluateVersionPayload
from packages.ml_contracts.synthetic import EVAL_SET_SEEDS
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.messaging import WorkerFactory, queued_payloads

TASK = "ml.infer.ml_eval.evaluate_version"
LISTEN, OUTBOX = "ml.infer", "default"
WAIT_S = 180.0


@pytest.fixture(autouse=True, scope="module")
def _cached_render_plan() -> Iterator[None]:
    """Cache `synthetic.render_plan` theo seed trong cả tệp: M01 (x3 họ) và M06 cùng dựng lại
    đúng 40 ảnh của `EVAL_SET_SEEDS` — `render_plan` tất định theo seed (BE-00 §9), nên dựng
    một lần là đủ; `evaluate_family` vẫn gọi `render_plan` thật cho mỗi seed (K23 giữ render
    thật), chỉ bỏ phần vẽ lại ảnh giống hệt đã có. Trần bộ nhớ: 40 kế hoạch ~1600x1200 (~300 MB) trong
    một worker, nhả khi hết tệp (fixture `scope="module"` trả lại hàm gốc, cache bị thu gom).
    """
    import packages.ml_contracts.synthetic as synthetic_module

    original = synthetic_module.render_plan
    synthetic_module.render_plan = functools.lru_cache(maxsize=None)(original)
    try:
        yield
    finally:
        synthetic_module.render_plan = original


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Client broker thật; hai hàng và cache phiên/ngữ cảnh sạch ở đầu và cuối test."""
    client = broker_redis_sync()
    client.delete(LISTEN, OUTBOX)
    clear_session_cache()
    reset_infer_context()
    yield client
    client.delete(LISTEN, OUTBOX)
    clear_session_cache()
    reset_infer_context()
    client.close()


def context_of(monkeypatch: pytest.MonkeyPatch, storage: LocalDiskStorage, models_dir: Path, backend: str) -> None:
    """Trỏ ngữ cảnh worker vào kho và cấu hình của test (task nhập `infer_context` vào chính nó)."""
    ctx = context(storage, models_dir, backend=backend)
    monkeypatch.setattr(tasks, "infer_context", lambda: ctx)


def results(client: SyncRedis, version_id: str) -> list[dict[str, object]]:
    """Mọi `evaluation_done` của một bản model, đã bóc vỏ kombu."""
    return [item for item in queued_payloads(client, OUTBOX) if item.get("version_id") == version_id]


def wait_for(predicate: Callable[[], bool], what: str) -> None:
    """Chờ tới `WAIT_S` cho một điều kiện; quá hạn là hỏng, không im lặng bỏ qua."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"quá {WAIT_S} giây mà chưa: {what}")


def run_task(
    factory: WorkerFactory, broker: SyncRedis, payload: EvaluateVersionPayload, count: int = 1
) -> list[dict[str, object]]:
    """Gửi task `count` lần rồi chờ đủ `count` thông điệp `evaluation_done` của bản model ấy."""
    with factory([LISTEN]):
        for _ in range(count):
            send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.version_id)) >= count, f"{count} evaluation_done")
    return results(broker, payload.version_id)


@pytest.fixture
def few_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hạ tập kiểm xuống ba seed (thuộc tính module, task đọc lúc chạy)."""
    monkeypatch.setattr(tasks, "EVAL_SEEDS", SEEDS)


def test_ml_eval_evaluate_version__J01(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Họ dò vật, model storage, ba seed: `completed` với đúng khoá `map50` ∈ [0, 1]."""
    context_of(monkeypatch, local_storage, tmp_path / "models", "onnx")
    payload = stored_model(local_storage, const_yolo())
    (result,) = run_task(celery_worker_factory, broker, payload)
    assert (result["status"], result["error_code"]) == ("completed", None)
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    assert list(metrics) == [FAMILY_METRIC[OBJECTS]]
    assert 0.0 <= float(metrics["map50"]) <= 1.0


def test_ml_eval_evaluate_version__J02(
    broker: SyncRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Kho hỏng tạm hai lượt khi đọc trọng số rồi đạt: task thử lại, đúng một `completed`."""
    flaky = FlakyReads(tmp_path / "flaky", DEPENDENCY_UNAVAILABLE.error(retry_after=5), failures=2)
    context_of(monkeypatch, flaky, tmp_path / "models", "onnx")
    payload = stored_model(flaky, const_yolo())
    done = run_task(celery_worker_factory, broker, payload)
    assert [item["status"] for item in done] == ["completed"]
    assert flaky.attempts == 3


def test_ml_eval_evaluate_version__J03(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Đầu ra ONNX sai số kênh: `failed` `MODEL_FORMAT_UNSUPPORTED`, không số đo."""
    context_of(monkeypatch, local_storage, tmp_path / "models", "onnx")
    payload = stored_model(local_storage, const_yolo(channels=4 + NUM_CLASSES + 1))
    (result,) = run_task(celery_worker_factory, broker, payload)
    assert (result["status"], result["error_code"], result["metrics"]) == ("failed", MODEL_FORMAT_UNSUPPORTED, None)


def test_ml_eval_evaluate_version__J05(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Quá hạn mềm giữa vòng đánh giá: `failed` `TASK_TIMEOUT` đúng một lần."""

    def timing_out(*args: object, **kwargs: object) -> dict[str, float]:
        """Thay `evaluate_family` bằng một lượt quá hạn mềm ngay lập tức."""
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(tasks, "evaluate_family", timing_out)
    context_of(monkeypatch, local_storage, tmp_path / "models", "fake")
    payload = eval_payload(pinned_ref(OBJECTS))
    (result,) = run_task(celery_worker_factory, broker, payload)
    assert (result["status"], result["error_code"]) == ("failed", TASK_TIMEOUT)


def test_ml_eval_evaluate_version__J06(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Giao hai lần: hai `evaluation_done` giống hệt nhau (đánh giá tất định)."""
    context_of(monkeypatch, local_storage, tmp_path / "models", "fake")
    payload = eval_payload(pinned_ref(OBJECTS))
    first, second = run_task(celery_worker_factory, broker, payload, count=2)
    assert first == second


def test_ml_eval_evaluate_version__J08(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Thông điệp độc không chạy và không gửi gì; worker vẫn làm thông điệp hợp lệ ngay sau."""
    context_of(monkeypatch, local_storage, tmp_path / "models", "fake")
    good = eval_payload(pinned_ref(OBJECTS))
    poison = {"schema_version": 1, "version_id": good.version_id}
    with celery_worker_factory([LISTEN]):
        producer_app().send_task(TASK, args=[poison], queue=LISTEN)
        send_task(TASK, good)
        wait_for(lambda: bool(results(broker, good.version_id)), "thông điệp tốt sau thông điệp độc")
    assert [item["status"] for item in results(broker, good.version_id)] == ["completed"]
    assert broker.llen(LISTEN) == 0


@pytest.mark.parametrize(
    ("family", "metric", "expected"),
    [
        ("wallSegmentation", "iou", 1.0),
        ("openingAndFurnitureDetection", "map50", 0.995),
        ("dimensionReading", "cer", 0.0),
    ],
)
def test_ml_eval_evaluate_version__M01(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
    family: str,
    metric: str,
    expected: float,
) -> None:
    """`ML_BACKEND=fake`, cả `EVAL_SET_SEEDS`: bộ giả hoàn hảo cho đúng số đo của họ, lặp lại bằng nhau."""
    context_of(monkeypatch, local_storage, tmp_path / "models", "fake")
    payload = eval_payload(pinned_ref(family))
    first, second = run_task(celery_worker_factory, broker, payload, count=2)
    assert first["metrics"] == {metric: expected}
    assert first == second


def test_ml_eval_evaluate_version__M02(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Checksum khai lệch với bytes thật: `failed` `MODEL_CHECKSUM_MISMATCH` (hộp cát kiểm)."""
    context_of(monkeypatch, local_storage, tmp_path / "models", "onnx")
    data = const_yolo()
    ref, key = storage_ref(data, checksum="1" * 64)
    put_sync(local_storage, key, data)
    (result,) = run_task(celery_worker_factory, broker, eval_payload(ref))
    assert (result["status"], result["error_code"]) == ("failed", MODEL_CHECKSUM_MISMATCH)


def test_ml_eval_evaluate_version__M03(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Bytes kiểu `.pt` (pickle): `failed` mã bộ nạp; không byte nào tới `pickle` (K12)."""
    calls: list[object] = []
    monkeypatch.setattr(pickle, "loads", lambda *args: calls.append(args))
    context_of(monkeypatch, local_storage, tmp_path / "models", "onnx")
    payload = stored_model(local_storage, pickle.dumps({"model": "yolo"}))
    (result,) = run_task(celery_worker_factory, broker, payload)
    assert (result["status"], result["error_code"]) == ("failed", MODEL_FORMAT_UNSUPPORTED)
    assert calls == []


def test_ml_eval_evaluate_version__M04(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """`ML_DEVICE=cuda` không đổi gì: đánh giá luôn CPU và không giữ khoá GPU (M04)."""
    monkeypatch.setenv("ML_DEVICE", "cuda")
    slots: list[object] = []
    monkeypatch.setattr(gpu, "gpu_slot", lambda *args: slots.append(args))
    context_of(monkeypatch, local_storage, tmp_path / "models", "onnx")
    payload = stored_model(local_storage, const_yolo())
    (result,) = run_task(celery_worker_factory, broker, payload)
    assert result["status"] == "completed"
    assert slots == []


def test_ml_eval_evaluate_version__M06(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Tập kiểm là đúng `range(100, 140)` theo thứ tự, rời hẳn seed dataset vi mô của huấn luyện."""
    seen: list[int] = []
    real = synthetic.render_plan

    def spy(seed: int, **kwargs: int) -> object:
        """Ghi lại seed rồi gọi `render_plan` thật."""
        seen.append(seed)
        return real(seed, **kwargs)

    monkeypatch.setattr(synthetic, "render_plan", spy)
    context_of(monkeypatch, local_storage, tmp_path / "models", "fake")
    payload = eval_payload(pinned_ref(OBJECTS))
    run_task(celery_worker_factory, broker, payload)
    assert seen == list(EVAL_SET_SEEDS)
    assert set(EVAL_SET_SEEDS).isdisjoint({*TRAIN_SEEDS, *VALIDATION_SEEDS})


def test_ml_eval_pinned_model_loads_in_process(
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    few_seeds: None,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Bản **ghim** nạp tại chỗ (không hộp cát) và dùng bảng nhãn COCO của chính bản ghim.

    `load_onnx` được thay bằng một phiên `onnxruntime` **thật** dựng từ model tí hon: tệp
    ONNX ghim thật không có trong worktree, mà checksum của bản ghim thì không giả được.
    """
    coco_labels = labels_for(pinned_yolo_ref())
    data = make_const_yolo(yolo_output([], nc=len(coco_labels)), input_shape=(1, 3, 640, 640))
    session = load_session(local_storage, data)

    async def fixed_session(*args: object, **kwargs: object) -> object:
        """Trả đúng phiên thật của test thay cho lượt nạp bản ghim."""
        return session

    monkeypatch.setattr(tasks, "load_onnx", fixed_session)
    context_of(monkeypatch, local_storage, tmp_path / "models", "onnx")
    (result,) = run_task(celery_worker_factory, broker, eval_payload(pinned_yolo_ref()))
    assert (result["status"], result["metrics"]) == ("completed", {"map50": 0.0})
