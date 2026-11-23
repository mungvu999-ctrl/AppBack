"""`run_step` gọi trực tiếp: mọi nhánh đọc trang, ghi artifact, báo `step_done` (không cần worker)."""

import logging
import os
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import BaseModel

from apps.ml.runtime import tasks_util
from apps.ml.runtime.errors import FILE_CORRUPT, IMAGE_TOO_LARGE, PIPELINE_ARTIFACT_MISSING
from apps.ml.runtime.settings import MlSettings
from apps.ml.runtime.tasks_util import (
    STEP_DONE_TASK,
    InferContext,
    StepOutput,
    infer_context,
    reset_infer_context,
    run_step,
    step_failed,
)
from apps.ml.runtime.tests.helpers import FailingReads, infer_payload, some_id
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.messaging import run_cancel
from packages.messaging.tasks import PermanentError
from packages.ml_contracts._png import PNG_SIGNATURE, _chunk
from packages.ml_contracts.artifacts import WallsResult, decode_mask, encode_mask, walls_from_json, walls_to_json
from packages.ml_contracts.fakes import FakeWallSegmenter
from packages.ml_contracts.payloads import InferStepPayload, ModelRef, StepResultPayload
from packages.ml_contracts.pinned import PINNED
from packages.ml_contracts.synthetic import render_plan
from packages.storage.local import LocalDiskStorage
from packages.storage.port import ObjectStorage

PLAN = render_plan(55)


class Sent:
    """`send` ghi lại: thay broker trong test gọi trực tiếp."""

    def __init__(self, error: AppError | None = None) -> None:
        """`error` khác `None` thì mọi lần gửi ném nó (broker hỏng)."""
        self.calls: list[tuple[str, StepResultPayload]] = []
        self.error = error

    def __call__(self, name: str, payload: BaseModel) -> None:
        """Ghi `(tên task, payload)`, hay ném `error` đã cài."""
        if self.error is not None:
            raise self.error
        assert isinstance(payload, StepResultPayload)
        self.calls.append((name, payload))


def walls_step(image: NDArray[np.uint8], payload: InferStepPayload, prepared: object) -> StepOutput:
    """Bước tường giả: mặt nạ của bộ giả, không có đường tim."""
    mask = FakeWallSegmenter().segment(image)
    return StepOutput({"walls.png": encode_mask(mask), "walls.json": walls_to_json(WallsResult(walls=()))})


async def put_page(storage: LocalDiskStorage, payload: InferStepPayload, data: bytes = PLAN.image_png) -> None:
    """Đặt trang vào kho tại `payload.page_key` (mặc định PNG của bản vẽ tổng hợp)."""
    await storage.put(payload.page_key, data, content_type="image/png", max_bytes=len(data) + 1)


async def test_run_step_writes_then_reports(local_storage: LocalDiskStorage) -> None:
    """Bước đạt: ghi hai artifact đúng nội dung và kiểu rồi gửi đúng một `completed`."""
    payload = infer_payload()
    await put_page(local_storage, payload)
    sent = Sent()
    await run_step(payload, walls_step, storage=local_storage, send=sent)
    ((name, result),) = sent.calls
    assert name == STEP_DONE_TASK
    assert result.status == "completed"
    assert result.artifact_keys == (f"{payload.artifact_prefix}walls.json", f"{payload.artifact_prefix}walls.png")
    assert result.model_version_id is None
    assert result.duration_ms >= 0
    chunks = [chunk async for chunk in local_storage.open_read(result.artifact_keys[1])]
    assert np.array_equal(decode_mask(b"".join(chunks), width_px=1600, height_px=1200), PLAN.walls_mask)
    info = await local_storage.stat(result.artifact_keys[0])
    assert info is not None
    assert info.content_type == "application/json"
    assert (
        walls_from_json(b"".join([chunk async for chunk in local_storage.open_read(result.artifact_keys[0])])).walls
        == ()
    )


class CountingReads:
    """Bọc `_read_page`: đếm số lần thật sự tải trang từ kho."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Thay `tasks_util._read_page` bằng bản đếm, vẫn gọi bản gốc."""
        self.keys: list[str] = []
        original = tasks_util._read_page

        async def counted(storage: ObjectStorage, key: str) -> bytes:
            """Ghi khoá rồi đọc như cũ."""
            self.keys.append(key)
            return await original(storage, key)

        monkeypatch.setattr(tasks_util, "_read_page", counted)


async def test_run_step_reads_and_decodes_a_page_once_per_process(
    local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P21: ba bước của một lượt cùng trang → tải + giải một lần; mỗi bước nhận bản sao ghi được."""
    payload = infer_payload()
    await put_page(local_storage, payload)
    reads = CountingReads(monkeypatch)
    seen: list[NDArray[np.uint8]] = []

    def mutating_step(image: NDArray[np.uint8], item: InferStepPayload, prepared: None) -> StepOutput:
        """Bước sửa ảnh tại chỗ: task sau vẫn phải thấy trang gốc."""
        seen.append(image.copy())
        image[:] = 0
        return walls_step(seen[-1], item, prepared)

    sent = Sent()
    for _ in range(3):
        await run_step(payload, mutating_step, storage=local_storage, send=sent)
    assert reads.keys == [payload.page_key]
    assert [result.status for _, result in sent.calls] == ["completed"] * 3
    assert all(np.array_equal(item, seen[0]) for item in seen)
    assert seen[0].any()


async def test_run_step_page_cache_is_bounded_lru(
    local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quá `PAGE_CACHE_SIZE` trang → trang cũ nhất bị bỏ và lần sau tải lại."""
    payloads = [infer_payload() for _ in range(tasks_util.PAGE_CACHE_SIZE + 1)]
    for item in payloads:
        await put_page(local_storage, item)
    reads = CountingReads(monkeypatch)
    sent = Sent()
    for item in [*payloads, payloads[-1], payloads[0]]:
        await run_step(item, walls_step, storage=local_storage, send=sent)
    assert reads.keys == [item.page_key for item in payloads] + [payloads[0].page_key]


async def test_run_step_does_not_cache_page_failures(
    local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trang thiếu → `failed`, không vào cache: trang tới sau thì lần sau đọc được."""
    payload = infer_payload()
    reads = CountingReads(monkeypatch)
    sent = Sent()
    await run_step(payload, walls_step, storage=local_storage, send=sent)
    await put_page(local_storage, payload)
    await run_step(payload, walls_step, storage=local_storage, send=sent)
    assert [result.status for _, result in sent.calls] == ["failed", "completed"]
    assert reads.keys == [payload.page_key, payload.page_key]


async def test_run_step_logs_step_timing(local_storage: LocalDiskStorage, caplog: pytest.LogCaptureFixture) -> None:
    """P26: mỗi bước xong (đạt hay hỏng) → một dòng `pipeline_step_timing` có run, bước, `duration_ms`."""
    payload = infer_payload()
    sent = Sent()
    with caplog.at_level(logging.INFO, logger="apps.ml.runtime.tasks_util"):
        await run_step(payload, walls_step, storage=local_storage, send=sent)
        await put_page(local_storage, payload)
        await run_step(payload, walls_step, storage=local_storage, send=sent)
    records = [record for record in caplog.records if record.getMessage() == "pipeline_step_timing"]
    assert [(r.__dict__["run_id"], r.__dict__["step"], r.__dict__["status"]) for r in records] == [
        (payload.run_id, payload.step, "failed"),
        (payload.run_id, payload.step, "completed"),
    ]
    assert [r.__dict__["duration_ms"] for r in records] == [result.duration_ms for _, result in sent.calls]


async def test_run_step_reports_the_model_version_on_every_path(local_storage: LocalDiskStorage) -> None:
    """`model_version_id` có mặt ở cả lượt đạt lẫn lượt `prepare` từ chối."""
    pin = PINNED["rapidocrRec"]
    ref = ModelRef(
        version_id=some_id("mdl"),
        family="dimensionReading",
        weights_key=None,
        pinned_name="rapidocrRec",
        checksum_sha256=str(pin.onnx_sha256),
    )
    payload = infer_payload(step="dimensionReading", model_ref=ref)
    await put_page(local_storage, payload)
    sent = Sent()

    async def prepare(item: InferStepPayload) -> str:
        """Chuẩn bị giả: trả tên model ghim."""
        return item.model.pinned_name or ""

    def text_step(image: NDArray[np.uint8], item: InferStepPayload, prepared: str | None) -> StepOutput:
        """Bước chữ giả: nhận đúng kết quả `prepare`, trả `text.json` rỗng."""
        assert prepared == "rapidocrRec"
        return StepOutput({"text.json": b'{"schema_version":1,"items":[]}'})

    await run_step(payload, text_step, storage=local_storage, prepare=prepare, send=sent)

    async def refuse(item: InferStepPayload) -> str:
        """Chuẩn bị từ chối: model không có."""
        raise PermanentError("MODEL_NOT_FOUND")

    await run_step(payload, text_step, storage=local_storage, prepare=refuse, send=sent)
    assert [(result.status, result.error_code, result.model_version_id) for _, result in sent.calls] == [
        ("completed", None, ref.version_id),
        ("failed", "MODEL_NOT_FOUND", ref.version_id),
    ]


def _png_header_only(width: int, height: int) -> bytes:
    """PNG chỉ có chữ ký + `IHDR` của khổ đã cho (không dữ liệu ảnh)."""
    return PNG_SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))


@pytest.mark.parametrize(
    ("page", "code"),
    [
        (None, PIPELINE_ARTIFACT_MISSING),
        (b"GIF89a not a png", FILE_CORRUPT),
        (_png_header_only(1600, 1199), FILE_CORRUPT),
        (_png_header_only(10_000, 5_000), IMAGE_TOO_LARGE),
        (
            _png_header_only(1600, 1200) + _chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + _chunk(b"IEND", b""),
            FILE_CORRUPT,
        ),
    ],
)
async def test_run_step_page_failures(local_storage: LocalDiskStorage, page: bytes | None, code: str) -> None:
    """Trang thiếu, không phải PNG, lệch khổ, quá trần, hỏng → một `failed` có mã, không artifact."""
    payload = infer_payload()
    if page is not None:
        await put_page(local_storage, payload, page)
    sent = Sent()
    await run_step(payload, walls_step, storage=local_storage, send=sent)
    assert [(result.status, result.error_code) for _, result in sent.calls] == [("failed", code)]
    assert await local_storage.stat(f"{payload.artifact_prefix}walls.png") is None


async def test_run_step_page_byte_cap(local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trang vượt `PAGE_MAX_BYTES` → `failed` `IMAGE_TOO_LARGE`."""
    payload = infer_payload()
    await put_page(local_storage, payload)
    monkeypatch.setattr(tasks_util, "PAGE_MAX_BYTES", 1000)
    sent = Sent()
    await run_step(payload, walls_step, storage=local_storage, send=sent)
    assert sent.calls[0][1].error_code == IMAGE_TOO_LARGE


async def test_run_step_step_failure_and_contract_errors(local_storage: LocalDiskStorage) -> None:
    """Bước ném `PermanentError` → `failed` có mã; bước trả artifact lạ → `ValueError`, không gửi thêm."""
    payload = infer_payload()
    await put_page(local_storage, payload)
    sent = Sent()

    def gpu_lost(image: NDArray[np.uint8], item: InferStepPayload, prepared: object) -> StepOutput:
        """Bước giả mất khoá GPU giữa chừng."""
        raise PermanentError("GPU_LOCK_LOST")

    await run_step(payload, gpu_lost, storage=local_storage, send=sent)
    assert sent.calls[0][1].error_code == "GPU_LOCK_LOST"

    def stray(image: NDArray[np.uint8], item: InferStepPayload, prepared: object) -> StepOutput:
        """Bước giả trả artifact không thuộc bước này."""
        return StepOutput({"objects.json": b"{}"})

    with pytest.raises(ValueError, match="artifact lạ"):
        await run_step(payload, stray, storage=local_storage, send=sent)
    assert len(sent.calls) == 1


async def test_run_step_transient_errors_propagate(local_storage: LocalDiskStorage, tmp_path: Path) -> None:
    """Lỗi tạm của kho hay broker nổi lên (task thử lại), không gửi `failed`; artifact đã ghi giữ nguyên."""
    payload = infer_payload()
    sent = Sent()
    broken = FailingReads(tmp_path / "broken", DEPENDENCY_UNAVAILABLE.error(retry_after=5))
    with pytest.raises(AppError):
        await run_step(payload, walls_step, storage=broken, send=sent)
    assert sent.calls == []
    await put_page(local_storage, payload)
    with pytest.raises(AppError):
        await run_step(
            payload, walls_step, storage=local_storage, send=Sent(DEPENDENCY_UNAVAILABLE.error(retry_after=5))
        )
    assert await local_storage.stat(f"{payload.artifact_prefix}walls.png") is not None


async def test_run_step_stops_before_loading_anything_when_the_run_is_cancelled(
    local_storage: LocalDiskStorage, caplog: pytest.LogCaptureFixture
) -> None:
    """K-24: task còn xếp hàng của lượt đã huỷ thoát ở điểm kiểm đầu — không nạp model, không gửi `step_done`."""
    payload = infer_payload()
    await put_page(local_storage, payload)
    sent = Sent()
    prepared: list[str] = []

    async def prepare(p: InferStepPayload) -> None:
        prepared.append(p.run_id)

    with caplog.at_level(logging.INFO, logger="apps.ml.runtime.tasks_util"):
        await run_step(
            payload, walls_step, storage=local_storage, send=sent, prepare=prepare, cancelled=lambda run: True
        )

    assert (prepared, sent.calls) == ([], [])
    assert await local_storage.stat(f"{payload.artifact_prefix}walls.png") is None
    timings = [r.__dict__["status"] for r in caplog.records if r.getMessage() == "pipeline_step_timing"]
    assert timings == ["cancelled"]


async def test_run_step_stops_between_tiles_when_cancelled_mid_inference(local_storage: LocalDiskStorage) -> None:
    """K-24: cờ cắm giữa lúc `fn` chạy (luồng phụ) → dừng ở `checkpoint()` kế tiếp, không ghi artifact."""
    payload = infer_payload()
    await put_page(local_storage, payload)
    sent = Sent()
    flag = {"cancelled": False}
    tiles_done: list[int] = []

    def tiled_step(image: NDArray[np.uint8], p: InferStepPayload, prepared: object) -> StepOutput:
        for tile in range(10):
            run_cancel.checkpoint()
            tiles_done.append(tile)
            if tile == 2:
                flag["cancelled"] = True
                time.sleep(run_cancel.CHECK_INTERVAL_S + 0.05)  # qua nhịp thưa để lần hỏi kế tiếp thật sự hỏi
        return walls_step(image, p, prepared)

    await run_step(payload, tiled_step, storage=local_storage, send=sent, cancelled=lambda run: flag["cancelled"])

    assert tiles_done == [0, 1, 2]
    assert sent.calls == []
    assert await local_storage.stat(f"{payload.artifact_prefix}walls.png") is None


def test_step_failed_is_a_define_task_on_failed() -> None:
    """`step_failed` gửi một `failed` mang mã, không artifact, thời lượng 0."""
    payload = infer_payload()
    sent = Sent()
    step_failed(payload, "RETRY_EXHAUSTED", send=sent)
    ((_, result),) = sent.calls
    assert (result.status, result.error_code, result.artifact_keys, result.duration_ms) == (
        "failed",
        "RETRY_EXHAUSTED",
        (),
        0,
    )


@pytest.fixture
def ml_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, storage_env: None) -> Iterator[None]:
    """Môi trường `ml` kho local + backend giả; dọn cache cấu hình và ngữ cảnh trước và sau."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "ml-objects"))
    monkeypatch.setenv("ML_BACKEND", "fake")
    from packages.storage.settings import reset_storage_settings_cache

    reset_storage_settings_cache()
    reset_infer_context()
    yield
    reset_storage_settings_cache()
    reset_infer_context()


def test_infer_context_is_built_once_from_the_environment(ml_env: None) -> None:
    """`infer_context()` dựng một lần từ môi trường rồi trả lại đúng đối tượng ấy."""
    context = infer_context()
    assert isinstance(context, InferContext)
    assert isinstance(context.storage, LocalDiskStorage)
    assert context.settings == MlSettings(ml_backend="fake")
    assert infer_context() is context
    reset_infer_context()
    assert infer_context() is not context


# Môi trường của dịch vụ `ml` ở production (`deploy/compose/base.yml`): khoá MinIO riêng,
# **không** `SECRET_KEY`/`PUBLIC_BASE_URL`/`REDIS_CACHE_URL` (NO-085, BE-00 §2.1/§9).
_ML_PROD_ENV = {
    "APP_ENV": "production",
    "REDIS_BROKER_URL": "redis://redis-broker:6379/0",
    "STORAGE_BACKEND": "s3",
    "S3_ENDPOINT": "http://minio:9000",
    "S3_PUBLIC_ENDPOINT": "https://files.appback.test",
    "S3_BUCKET": "appback",
    "S3_ACCESS_KEY": "ml-key",
    "S3_SECRET_KEY": "ml-secret",
    "ML_BACKEND": "fake",
}
_API_ONLY = ("SECRET_KEY", "SECRET_KEY_PREVIOUS", "PUBLIC_BASE_URL", "REDIS_CACHE_URL")


def test_infer_context_builds_without_the_api_secrets() -> None:
    """Tiến trình mới với môi trường `ml` production dựng được kho S3 (FIX-091, NO-085).

    Tiến trình riêng vì cache cấu hình của tiến trình test đã giữ `SECRET_KEY`. Dựng kho
    không mở kết nối nào tới MinIO, nên không cần dịch vụ thật. Tiến trình không `CoreSettings`
    nhận lớp con không ký URL của `S3Storage` (`packages/storage/factory.py`, 45b5ece) — so `isinstance`.
    """
    env = {name: value for name, value in os.environ.items() if name not in _API_ONLY} | _ML_PROD_ENV
    code = (
        "from apps.ml.runtime.tasks_util import infer_context; from packages.storage.s3 import S3Storage; "
        "print(isinstance(infer_context().storage, S3Storage))"
    )
    result = subprocess.run(  # noqa: S603 — trình thông dịch của chính tiến trình test, mã cố định
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[4],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
