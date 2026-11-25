"""J-case và M-case của task `ml.infer.objects.detect` (BE-00 §7 "Test task", cổng case).

Dịch vụ **thật**: Redis của Testcontainers, worker Celery thật nghe `ml.infer`, kho đĩa
`local_storage`, `YoloOnnxDetector` và model YOLO tí hon chạy thật (K23). `step_done` đi
tới `pipeline.cpu` mà không worker nào nghe, nên đọc lại được bằng `LRANGE` và lọc theo
`run_id` riêng của từng test. Khuôn chép từ `apps/ml/text/tests/test_tasks.py`.
"""

import functools
import hashlib
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

from apps.ml.objects import tasks
from apps.ml.objects.labels import ARTIFACT_LABELS, COCO_LABELS
from apps.ml.objects.tests.helpers import STEP, FlakyReads, infer_payload, put_sync, read_sync, some_id
from apps.ml.objects.tests.onnx_models import make_const_yolo, make_runtime_broken_yolo, storage_ref, yolo_output
from apps.ml.runtime import device, gpu, loader
from apps.ml.runtime.loader import clear_session_cache, load_onnx
from apps.ml.runtime.settings import MlSettings, get_ml_settings
from apps.ml.runtime.tasks_util import InferContext, StepOutput
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.messaging.celery_app import producer_app, send_task
from packages.messaging.redis import SyncRedis, broker_redis_sync
from packages.messaging.tasks import RETRY_EXHAUSTED, TASK_TIMEOUT
from packages.ml_contracts.artifacts import ObjectsResult, objects_from_json, objects_to_json
from packages.ml_contracts.payloads import InferStepPayload, ModelRef
from packages.ml_contracts.pinned import PinnedWeights
from packages.ml_contracts.synthetic import render_plan
from packages.storage.local import LocalDiskStorage
from packages.testing.fixtures.messaging import WorkerFactory, queued_payloads

_log = logging.getLogger(__name__)

TASK = "ml.infer.objects.detect"
LISTEN, OUTBOX = "ml.infer", "pipeline.cpu"
WAIT_S = 90.0
PAGE_S = 640
PLAN = render_plan(1, width_px=PAGE_S, height_px=PAGE_S)


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


def _trained_model_bytes() -> bytes:
    """Model YOLO tí hon "đã huấn luyện" (`ARTIFACT_LABELS`) khớp `PLAN.detections`."""
    boxes = [
        (
            (det.box.x_min + det.box.x_max) / 2,
            (det.box.y_min + det.box.y_max) / 2,
            det.box.x_max - det.box.x_min,
            det.box.y_max - det.box.y_min,
            ARTIFACT_LABELS.index(det.label),
            det.confidence,
        )
        for det in PLAN.detections
    ]
    output = yolo_output(boxes, nc=len(ARTIFACT_LABELS))
    return make_const_yolo(output, input_shape=(1, 3, PAGE_S, PAGE_S))


def onnx_payload(storage: LocalDiskStorage, data: bytes | None = None, **kwargs: object) -> InferStepPayload:
    """Payload trỏ model YOLO tí hon (đã huấn luyện) đã nằm trong kho dạng storage."""
    model_bytes = data if data is not None else _trained_model_bytes()
    ref, key = storage_ref(model_bytes, **kwargs)  # type: ignore[arg-type]  # kwargs của `storage_ref`
    put_sync(storage, key, model_bytes)
    return with_page(storage, infer_payload(width=PAGE_S, height=PAGE_S, model_ref=ref))


def run_task(factory: WorkerFactory, broker: SyncRedis, payload: InferStepPayload, count: int = 1) -> None:
    """Gửi task rồi chờ đủ `count` thông điệp `step_done` của lượt chạy ấy."""
    with factory([LISTEN]):
        for _ in range(count):
            send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) >= count, f"{count} step_done")


def test_objects_detect__J01(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Chạy đúng: `objects.json` giải được, đúng các hộp mong đợi; `step_done` khớp `model_version_id`."""
    payload = onnx_payload(onnx_context)
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["model_version_id"]) == ("completed", payload.model.version_id)
    assert result["artifact_keys"] == [f"{payload.artifact_prefix}objects.json"]
    written = read_sync(onnx_context, f"{payload.artifact_prefix}objects.json")
    assert written is not None
    got = objects_from_json(written)
    assert {d.label for d in got.detections} == {d.label for d in PLAN.detections}


def test_objects_detect__J06(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Giao lặp: cùng khoá, bytes bằng nhau, mỗi lượt một `step_done` giống nhau."""
    payload = onnx_payload(onnx_context)
    key = f"{payload.artifact_prefix}objects.json"
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt giao đầu")
        first = read_sync(onnx_context, key)
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 2, "lượt giao lặp")
    assert read_sync(onnx_context, key) == first
    one, two = results(broker, payload.run_id)
    assert {k: v for k, v in one.items() if k != "duration_ms"} == {k: v for k, v in two.items() if k != "duration_ms"}


def test_objects_detect__J02(
    broker: SyncRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Kho hỏng tạm hai lượt rồi đạt: task thử lại và kết thúc `completed`, đúng một thông điệp."""
    flaky = FlakyReads(tmp_path / "flaky", DEPENDENCY_UNAVAILABLE.error(retry_after=5), failures=2)
    context_of(monkeypatch, flaky, MlSettings(ml_backend="fake"))
    payload = with_page(flaky, infer_payload(width=PAGE_S, height=PAGE_S))
    run_task(celery_worker_factory, broker, payload)
    assert [item["status"] for item in results(broker, payload.run_id)] == ["completed"]
    assert flaky.attempts == 3


def test_objects_detect__J02_exhausted(
    broker: SyncRedis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Kho hỏng mãi: hết bậc thử lại → `failed` `RETRY_EXHAUSTED`, đúng một thông điệp."""
    broken = FlakyReads(tmp_path / "flaky", DEPENDENCY_UNAVAILABLE.error(retry_after=5), failures=-1)
    context_of(monkeypatch, broken, MlSettings(ml_backend="fake"))
    payload = with_page(broken, infer_payload(width=PAGE_S, height=PAGE_S))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"], result["artifact_keys"]) == ("failed", RETRY_EXHAUSTED, [])


def test_objects_detect__J03(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Đầu ra sai hình dạng lúc chạy: đúng một `failed` `MODEL_FORMAT_UNSUPPORTED`, không artifact."""
    data = make_runtime_broken_yolo(size=PAGE_S, nc=len(ARTIFACT_LABELS))
    payload = onnx_payload(onnx_context, data)
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_FORMAT_UNSUPPORTED")
    assert read_sync(onnx_context, f"{payload.artifact_prefix}objects.json") is None


def test_objects_detect__J05(
    broker: SyncRedis,
    fake_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Quá hạn mềm giữa bước: `failed` `TASK_TIMEOUT` đúng một lần, không thử lại."""

    def timing_out(image: NDArray[np.uint8], payload: InferStepPayload, detector: object) -> StepOutput:
        """Thay `_detect`: luôn ném `SoftTimeLimitExceeded`, mô phỏng quá hạn mềm Celery."""
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(tasks, "_detect", timing_out)
    payload = with_page(fake_context, infer_payload(width=PAGE_S, height=PAGE_S))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", TASK_TIMEOUT)


def test_objects_detect__J08(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Thông điệp độc không chạy và không gửi gì; worker vẫn làm thông điệp hợp lệ ngay sau."""
    good = with_page(fake_context, infer_payload(width=PAGE_S, height=PAGE_S))
    poison = {"schema_version": 2, "run_id": good.run_id, "step": STEP}
    with celery_worker_factory([LISTEN]):
        producer_app().send_task(TASK, args=[poison], queue=LISTEN)
        send_task(TASK, good)
        wait_for(lambda: bool(results(broker, good.run_id)), "thông điệp tốt sau thông điệp độc")
    assert [item["status"] for item in results(broker, good.run_id)] == ["completed"]
    assert broker.llen(LISTEN) == 0


def test_objects_detect__M01(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """`ML_BACKEND=fake`: hai lượt ra `objects.json` bằng nhau từng byte và khớp đáp án."""
    payload = with_page(fake_context, infer_payload(width=PAGE_S, height=PAGE_S))
    key = f"{payload.artifact_prefix}objects.json"
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt đầu")
        first = read_sync(fake_context, key)
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 2, "lượt hai")
    second = read_sync(fake_context, key)
    assert first == second == objects_to_json(ObjectsResult(detections=PLAN.detections))


def test_objects_detect__M01_real(
    broker: SyncRedis, onnx_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Bộ thật chạy hai lượt cùng một model: `objects.json` bằng nhau từng byte (tất định)."""
    payload = onnx_payload(onnx_context)
    key = f"{payload.artifact_prefix}objects.json"
    with celery_worker_factory([LISTEN]):
        send_task(TASK, payload)
        wait_for(lambda: len(results(broker, payload.run_id)) == 1, "lượt đầu")
        first = read_sync(onnx_context, key)
        payload2 = onnx_payload(onnx_context, data=_trained_model_bytes())
        send_task(TASK, payload2)
        wait_for(lambda: len(results(broker, payload2.run_id)) == 1, "lượt hai")
    second = read_sync(onnx_context, f"{payload2.artifact_prefix}objects.json")
    assert first == second


def test_objects_detect__M02(
    broker: SyncRedis,
    onnx_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """Checksum lệch: `failed` `MODEL_CHECKSUM_MISMATCH` và **không** phiên ORT nào được dựng."""
    built: list[int] = []
    monkeypatch.setattr(loader, "_session", lambda *args: built.append(1))
    data = _trained_model_bytes()
    ref, key = storage_ref(data, checksum="0" * 64)
    put_sync(onnx_context, key, data)
    payload = with_page(onnx_context, infer_payload(width=PAGE_S, height=PAGE_S, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_CHECKSUM_MISMATCH")
    assert built == []


def test_objects_detect__M03(
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
    payload = with_page(onnx_context, infer_payload(width=PAGE_S, height=PAGE_S, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", "MODEL_FORMAT_UNSUPPORTED")


def _forbidden(*args: object, **kwargs: object) -> object:
    """Thay `pickle.loads` trong lượt test M03: gọi tới đây là vi phạm K12."""
    raise AssertionError("pickle.loads bị cấm trên đường nạp trọng số (K12)")


def test_objects_import_does_not_pull_torch_or_ultralytics() -> None:
    """Nhập `apps.ml.objects` trong tiến trình sạch không kéo `torch`/`ultralytics` theo (M03, BE-00 §12)."""
    code = "import apps.ml.objects, sys; assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules"
    done = subprocess.run(  # noqa: S603 — lệnh cố định, chạy chính Python của venv
        [sys.executable, "-c", code], capture_output=True, check=False
    )
    assert done.returncode == 0, done.stderr.decode()


def test_objects_detect__M03_pinned(
    broker: SyncRedis,
    tmp_path: Path,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """`pinned_name="yolov8n"` với model tí hon 80 lớp trong `models_dir` tạm: nạp được, dùng `COCO_LABELS`."""
    data = make_const_yolo(yolo_output([], nc=len(COCO_LABELS)), input_shape=(1, 3, PAGE_S, PAGE_S))
    digest = hashlib.sha256(data).hexdigest()
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "yolov8n.onnx").write_bytes(data)
    settings = MlSettings(ml_backend="onnx", ml_models_dir=str(models_dir), ml_ort_threads=1)
    context_of(monkeypatch, local_storage, settings)
    pin = PinnedWeights(
        name="yolov8n",
        family="openingAndFurnitureDetection",
        source_url="",
        source_sha256=digest,
        onnx_sha256=digest,
        license="AGPL-3.0",
    )
    monkeypatch.setattr(tasks, "load_onnx", functools.partial(load_onnx, pinned={"yolov8n": pin}))
    version = some_id("mdl")
    ref = ModelRef(version_id=version, family=STEP, weights_key=None, pinned_name="yolov8n", checksum_sha256=digest)
    payload = with_page(local_storage, infer_payload(width=PAGE_S, height=PAGE_S, model_ref=ref))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert result["status"] == "completed"


def test_objects_detect__M04(
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
        """Thay `load_onnx`: gọi bản thật rồi giữ lại phiên để kiểm `get_providers()` sau đó."""
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
    """Thay `gpu_slot` trong lượt test M04: bước phát hiện không bao giờ giữ khoá GPU."""
    raise AssertionError("bước openingAndFurnitureDetection không được lấy khoá gpu:0")


def _forbidden_device(setting: str) -> str:
    """Thay `resolve_device` trong lượt test M04: suy luận luôn CPU, không hỏi `torch.cuda`."""
    raise AssertionError("bước suy luận không được chọn thiết bị (BE-00 §9)")


def test_get_ml_settings__not_pinned_by_a_previous_test() -> None:
    """Chặn tái phát NO-312: sau test vá `ML_DEVICE=cuda` (ở đây và ở `apps/ml/text`), cache không còn `cuda`."""
    assert get_ml_settings().ml_device != "cuda"


def test_objects_detect_rejects_another_family(
    broker: SyncRedis, fake_context: LocalDiskStorage, celery_worker_factory: WorkerFactory
) -> None:
    """Payload của bước khác: `failed` `MODEL_VERSION_FAMILY_MISMATCH`, không đọc trang."""
    payload = with_page(fake_context, infer_payload(width=PAGE_S, height=PAGE_S, step="wallSegmentation"))
    run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert (result["status"], result["error_code"]) == ("failed", tasks.MODEL_VERSION_FAMILY_MISMATCH)


def test_objects_detect_with_no_model_writes_no_detections(
    broker: SyncRedis,
    onnx_context: LocalDiskStorage,
    celery_worker_factory: WorkerFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`ModelRef` cổ điển (không trỏ model nào): `ObjectsResult` rỗng, bước vẫn `completed` (K19),
    và log `objects_model_inactive` kèm `run_id` của chính lượt này (khối [6] bước 3, [8])."""
    payload = with_page(onnx_context, infer_payload(width=PAGE_S, height=PAGE_S))
    with caplog.at_level(logging.INFO, logger="apps.ml.objects.tasks"):
        run_task(celery_worker_factory, broker, payload)
    (result,) = results(broker, payload.run_id)
    assert result["status"] == "completed"
    written = read_sync(onnx_context, f"{payload.artifact_prefix}objects.json")
    assert written == objects_to_json(ObjectsResult(detections=()))
    inactive = [r for r in caplog.records if r.message == "objects_model_inactive"]
    assert inactive, "thiếu log objects_model_inactive"
    assert inactive[0].run_id == payload.run_id  # type: ignore[attr-defined]  # extra= của logging.info


def test_objects_detect_sends_one_failed_message_not_two(
    broker: SyncRedis,
    fake_context: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    celery_worker_factory: WorkerFactory,
) -> None:
    """`PermanentError` (họ sai) và `TASK_TIMEOUT` gửi đúng một `step_done` mỗi loại — không gửi hai lần.

    Đọc trực tiếp hàng đợi `pipeline.cpu` (không spy `send_task`): `run_step` nhận `send` qua
    tham số mặc định `send: Send = send_task` ràng buộc **lúc định nghĩa** `tasks_util.run_step`,
    nên `monkeypatch.setattr(tasks_util, "send_task", ...)` không đổi được giá trị đã ràng buộc —
    đếm số thông điệp thật trên hàng đợi là cách quan sát đúng. Chờ thêm sau khi thấy đủ một
    thông điệp để bắt được một bản gửi lặp tới muộn, nếu có.
    """
    family_payload = with_page(fake_context, infer_payload(width=PAGE_S, height=PAGE_S, step="wallSegmentation"))

    def timing_out(image: NDArray[np.uint8], payload: InferStepPayload, detector: object) -> StepOutput:
        """Thay `_detect`: luôn quá hạn mềm, cho nhánh `TASK_TIMEOUT` của test này."""
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(tasks, "_detect", timing_out)
    timeout_payload = with_page(fake_context, infer_payload(width=PAGE_S, height=PAGE_S))

    with celery_worker_factory([LISTEN]):
        send_task(TASK, family_payload)
        send_task(TASK, timeout_payload)
        wait_for(lambda: len(results(broker, family_payload.run_id)) >= 1, "family mismatch failed")
        wait_for(lambda: len(results(broker, timeout_payload.run_id)) >= 1, "timeout failed")
    family_results = results(broker, family_payload.run_id)
    timeout_results = results(broker, timeout_payload.run_id)
    assert len(family_results) == 1
    assert family_results[0]["error_code"] == tasks.MODEL_VERSION_FAMILY_MISMATCH
    assert len(timeout_results) == 1
    assert timeout_results[0]["error_code"] == TASK_TIMEOUT
