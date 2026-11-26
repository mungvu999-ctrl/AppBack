"""Năm task cầu nối `default.training_bridge.*` của job huấn luyện (B6-03a [2], [6]).

Mọi task theo **một** khuôn: một giao dịch, `SELECT … FOR UPDATE` dòng job trước mọi lượt
ghi (K18), trạng thái không khớp → bỏ im lặng và log `training_message_ignored`. Payload do
`apps/ml` gửi là **không tin**: khoá object kiểm mẫu, checksum đo lại bằng kho, văn bản log
luôn dựng từ `LOG_TEMPLATES` chứ không lấy chuỗi của runner ([7]).

Hai bất biến còn lại:

1. **Mọi lần chuyển job sang `failed|cancelled`, và mọi thông điệp của job đã kết thúc, đặt
   `cancel_key` trước commit** — khoá Redis đó là tín hiệu dừng duy nhất của runner; Redis lỗi
   làm cả giao dịch rollback để lượt thử lại (J02) đặt lại được.
2. **Không giữ kết nối DB khi chạm kho** (K36): `finish_training_job` gọi `stat` xong rồi mới
   mở giao dịch; và không task nào tự `send_task` — `request_evaluation` đăng ký gửi sau commit.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.admin_ml_registry.registry import register_trained_version, request_evaluation, set_evaluation
from apps.worker.training_bridge.errors import MODEL_CHECKSUM_MISMATCH, TRAINING_METRICS_MISSING
from apps.worker.training_bridge.messages import trained_version_label
from apps.worker.training_bridge.settings import get_training_settings
from packages.core.clock import Clock, SystemClock
from packages.core.object_keys import model_prefix
from packages.db.engine import session_scope, worker_sessionmaker
from packages.db.models.admin_ml_jobs import FINISHED_STATUSES, TrainingJobRow, TrainingLogRow, TrainingMetricRow
from packages.messaging.payloads.training import WEIGHTS_NAME_RE, cancel_key, render_log, trained_version_id
from packages.messaging.redis import safe_redis
from packages.messaging.tasks import define_task
from packages.ml_contracts.families import FAMILY_METRIC, ModelFamily
from packages.ml_contracts.payloads import (
    EvaluationDonePayload,
    MetricPoint,
    TrainingFinishedPayload,
    TrainingHeartbeatPayload,
    TrainingLogPayload,
    TrainingMetricsPayload,
)
from packages.storage.port import ObjectInfo, ObjectStorage


async def arm_cancel_key(job_id: str) -> None:
    """Đặt `training:cancel:{job}` = "1" với TTL `TRAINING_CANCEL_TTL_S` (BE-00 §7).

    Gọi **trước** commit; Redis lỗi thì ngoại lệ lan lên để người gọi rollback (task: lỗi
    tạm J02, lịch: bỏ lượt). Gọi lại trên job đã kết thúc là bình thường (đặt lại khoá).

    `safe_redis()` dựng client **mới** mỗi lượt gọi, nên đóng trong `finally` (R-24): mỗi task và
    mỗi beat gọi hàm này, bỏ lại pool thì tiến trình worker rò kết nối dần.
    """
    client = safe_redis()
    try:
        await client.set(cancel_key(job_id), "1", ex=get_training_settings().training_cancel_ttl_s)
    finally:
        await client.aclose()


HEARTBEAT_TASK: Final = "default.training_bridge.heartbeat"
METRICS_TASK: Final = "default.training_bridge.metrics"
LOG_TASK: Final = "default.training_bridge.log"
FINISHED_TASK: Final = "default.training_bridge.finished"
EVALUATION_DONE_TASK: Final = "default.training_bridge.evaluation_done"

WEIGHTS_FORMAT: Final = "onnx"
_METRIC_FIELDS: Final = ("loss", "iou", "map50")
_IGNORED: Final = "training_message_ignored"
_log: Final = logging.getLogger(__name__)


def _ignore(task: str, job_id: str, reason: str, **extra: object) -> None:
    """Log một thông điệp bị bỏ (sự kiện máy chủ, **không** vào `training_logs`, không thân payload)."""
    _log.warning(_IGNORED, extra={"task": task, "job_id": job_id, "reason": reason, **extra})


async def _lock_job(db: AsyncSession, job_id: str) -> TrainingJobRow | None:
    """Job dưới `SELECT … FOR UPDATE` (K18); `None` khi job không còn (đã xoá, hay id lạ).

    Mọi task khoá dòng job **trước** mọi lượt ghi: `training_jobs` là bảng đầu trong thứ tự
    khoá (BE-00 §9), nên khoá ở đây rồi mới chạm `training_metrics`, `training_logs`, `model_*`.
    """
    stmt = select(TrainingJobRow).where(TrainingJobRow.id == job_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def _accept_progress(job: TrainingJobRow, clock: Clock, *, task: str) -> bool:
    """`metrics`/`log` có được nhận không: job chưa kết thúc, hay trong cửa sổ muộn.

    Job đã kết thúc nghĩa là runner vẫn còn chạy sau khi job chốt → đặt lại `cancel_key`
    (nó là tín hiệu dừng duy nhất) **trước** khi quyết định, dù điểm có bị bỏ hay không.
    """
    if job.status not in FINISHED_STATUSES:
        return True
    await arm_cancel_key(job.id)
    window = timedelta(seconds=get_training_settings().training_late_window_s)
    if job.ended_at is not None and clock.now() - job.ended_at <= window:
        return True
    _ignore(task, job.id, "late_window_passed", status=job.status)
    return False


async def run_training_heartbeat(
    sessionmaker: async_sessionmaker[AsyncSession], clock: Clock, payload: TrainingHeartbeatPayload
) -> None:
    """Một nhịp tim: `queued` → `running`, `running|cancelling` → mốc nhịp + `current_epoch`.

    `current_epoch` chỉ tiến (`max`): thứ tự giao không được bảo đảm (case J02) nên một
    nhịp cũ tới sau không được kéo tiến trình lùi. `epoch` ngoài `0…epochs` là payload hỏng.
    """
    async with session_scope(sessionmaker) as db:
        job = await _lock_job(db, payload.job_id)
        if job is None:
            _ignore(HEARTBEAT_TASK, payload.job_id, "job_missing")
            return
        if job.status in FINISHED_STATUSES:
            await arm_cancel_key(job.id)
            _ignore(HEARTBEAT_TASK, job.id, "job_finished", status=job.status)
            return
        if not 0 <= payload.epoch <= job.epochs:
            _ignore(HEARTBEAT_TASK, job.id, "epoch_out_of_range")
            return
        now = clock.now()
        if job.status == "queued":
            job.status, job.started_at = "running", now
        job.last_heartbeat_at = now
        job.current_epoch = max(job.current_epoch or 0, payload.epoch)
        job.updated_at = now


async def _max_steps(db: AsyncSession, job_id: str) -> dict[str, int]:
    """Bước lớn nhất đã ghi theo `split` — mốc nhận ra một lô tới muộn (`training_metric_late`)."""
    stmt = (
        select(TrainingMetricRow.split, func.max(TrainingMetricRow.step))
        .where(TrainingMetricRow.job_id == job_id)
        .group_by(TrainingMetricRow.split)
    )
    return {str(split): int(step) for split, step in (await db.execute(stmt)).all()}


def _point_values(point: MetricPoint, allowed: frozenset[str]) -> dict[str, float] | None:
    """Số đo của một điểm, hay `None` khi điểm mang số đo không thuộc họ (bỏ điểm).

    Họ chỉ có một số đo chất lượng (`FAMILY_METRIC`); một điểm `iou` gửi cho job `cer` là
    payload hỏng, ghi vào thì N36 trả về số đo FE không vẽ được.
    """
    values = {name: value for name in _METRIC_FIELDS if (value := getattr(point, name)) is not None}
    return values if set(values) <= allowed else None


async def run_training_metrics(
    sessionmaker: async_sessionmaker[AsyncSession], clock: Clock, payload: TrainingMetricsPayload
) -> None:
    """Một lô điểm số đo; `(job_id, split, step)` là lưới J06 cuối cùng (`DO NOTHING`).

    `recorded_at = min(mốc gửi, now)`: đồng hồ của runner có thể chạy trước, mà một điểm
    "tương lai" làm trục thời gian của FE nhảy (M05). Lô tới muộn vẫn chèn — N36 chỉ trả
    phần đủ cả hai `split` — chỉ log để thấy runner gửi lệch thứ tự.

    Trần `training_max_metric_points` kiểm theo **từng điểm**: một lô 500 điểm (`MAX_METRIC_POINTS`)
    không được vượt trần rồi mới bị chặn ở lô sau — chèn tới trần, phần dư bỏ và log một lần.
    """
    settings = get_training_settings()
    async with session_scope(sessionmaker) as db:
        job = await _lock_job(db, payload.job_id)
        if job is None:
            _ignore(METRICS_TASK, payload.job_id, "job_missing")
            return
        if not await _accept_progress(job, clock, task=METRICS_TASK):
            return
        total = await db.scalar(
            select(func.count()).select_from(TrainingMetricRow).where(TrainingMetricRow.job_id == job.id)
        )
        stored = total or 0
        allowed = frozenset({"loss", FAMILY_METRIC[cast("ModelFamily", job.family)]})
        max_steps = await _max_steps(db, job.id)
        now = clock.now()
        for point in payload.points:
            if stored >= settings.training_max_metric_points:
                _ignore(METRICS_TASK, job.id, "metric_points_exhausted", points=stored)
                return
            values = _point_values(point, allowed)
            if values is None:
                _ignore(METRICS_TASK, job.id, "metric_not_of_family", split=point.split, step=point.step)
                continue
            if point.step <= max_steps.get(point.split, -1):
                _log.warning("training_metric_late", extra={"job_id": job.id, "split": point.split, "step": point.step})
            sent = datetime.fromtimestamp(point.recorded_at_ms / 1000, UTC)
            await db.execute(
                insert(TrainingMetricRow)
                .values(
                    job_id=job.id,
                    split=point.split,
                    step=point.step,
                    epoch=point.epoch,
                    recorded_at=min(sent, now),
                    **values,
                )
                .on_conflict_do_nothing()
            )
            stored += 1


def dedupe_sha(payload: TrainingLogPayload) -> str:
    """SHA-256 của JSON sắp khoá của payload — khoá chống trùng của `training_logs` (J06).

    Băm **payload**, không băm câu đã dựng: hai mẫu khác nhau có thể cho cùng câu sau khi
    che dữ liệu, mà hai lượt giao cùng một thông điệp luôn có cùng JSON.
    """
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


async def run_training_log(
    sessionmaker: async_sessionmaker[AsyncSession], clock: Clock, payload: TrainingLogPayload
) -> None:
    """Một dòng log: dựng câu từ mẫu, `seq = max + 1` dưới khoá job, trùng `dedupe_sha` → bỏ.

    Câu luôn do `render_log` dựng từ khoá mẫu — văn bản của runner không bao giờ vào DB
    (nó có thể mang đường dẫn, biến môi trường). Mẫu lạ hay thiếu tham số → `None` → bỏ.
    """
    settings = get_training_settings()
    message = render_log(payload.template, payload.params)
    async with session_scope(sessionmaker) as db:
        job = await _lock_job(db, payload.job_id)
        if job is None:
            _ignore(LOG_TASK, payload.job_id, "job_missing")
            return
        if not await _accept_progress(job, clock, task=LOG_TASK):
            return
        if message is None:
            _ignore(LOG_TASK, job.id, "template_unknown", template=payload.template)
            return
        lines = await db.scalar(select(func.count()).select_from(TrainingLogRow).where(TrainingLogRow.job_id == job.id))
        if (lines or 0) >= settings.training_max_log_lines:
            _ignore(LOG_TASK, job.id, "log_lines_exhausted", lines=lines)
            return
        top = await db.scalar(select(func.max(TrainingLogRow.seq)).where(TrainingLogRow.job_id == job.id))
        await db.execute(
            insert(TrainingLogRow)
            .values(
                job_id=job.id,
                seq=(top if top is not None else -1) + 1,
                at=clock.now(),
                level=payload.level,
                message=message,
                dedupe_sha=dedupe_sha(payload),
            )
            .on_conflict_do_nothing()
        )


def _weights_failure(job: TrainingJobRow, payload: TrainingFinishedPayload, info: ObjectInfo | None) -> str | None:
    """Mã lỗi của một `finished(succeeded)` không tin được, hay `None` khi hợp lệ.

    Cầu nối không tin runner ([7]): khoá phải nằm đúng dưới tiền tố bản của **job này** và
    mang tên `weights-<32 hex>.onnx`, object phải có thật, trong trần, và `sha256` kho đo lại
    phải khớp `checksum_sha256` — nếu không thì bản model sẽ trỏ vào rác.
    """
    prefix = model_prefix(trained_version_id(job.id))
    key = payload.weights_key or ""
    if not key.startswith(prefix) or WEIGHTS_NAME_RE.fullmatch(key.removeprefix(prefix)) is None:
        return MODEL_CHECKSUM_MISMATCH
    if info is None or info.size > get_training_settings().training_weights_max_bytes:
        return MODEL_CHECKSUM_MISMATCH
    if info.sha256 != payload.checksum_sha256:
        return MODEL_CHECKSUM_MISMATCH
    if set(payload.metrics or {}) != {FAMILY_METRIC[cast("ModelFamily", job.family)]}:
        return TRAINING_METRICS_MISSING
    return None


def _close(job: TrainingJobRow, status: str, now: datetime, *, failure_code: str | None = None) -> None:
    """Chốt job sang một trạng thái cuối, tôn trọng CHECK của bảng (`started_at`, `ended_at`)."""
    if job.started_at is None:
        job.started_at = now
    job.status = status
    job.failure_code = failure_code
    job.ended_at = now
    job.updated_at = now


async def _register(db: AsyncSession, job: TrainingJobRow, payload: TrainingFinishedPayload, clock: Clock) -> str:
    """Ghi bản model của job rồi xin đánh giá **cùng giao dịch**; trả id bản.

    `request_evaluation` tự đăng ký `send_task` sau commit (K17), nên task này không bao giờ
    tự gửi: rollback ở bất kỳ dòng nào dưới đây cũng không để lại thông điệp mồ côi (J09).
    """
    version_id = trained_version_id(job.id)
    await register_trained_version(
        db,
        version_id=version_id,
        family=job.family,
        label=trained_version_label(base_model=job.base_model, epochs=job.epochs, finished_at=clock.now()),
        weights_key=payload.weights_key or "",
        weights_format=WEIGHTS_FORMAT,
        checksum_sha256=payload.checksum_sha256 or "",
        training_job_id=job.id,
        dataset_version_id=job.dataset_version_id,
        creator_id=job.creator_id,
        clock=clock,
    )
    await request_evaluation(db, version_id=version_id, clock=clock)
    return version_id


async def _finish_succeeded(
    db: AsyncSession, job: TrainingJobRow, payload: TrainingFinishedPayload, clock: Clock, info: ObjectInfo | None
) -> None:
    """`finished(succeeded)`: huỷ đang chờ thắng `succeeded`; trọng số không tin được → `failed`."""
    now = clock.now()
    if job.status == "cancelling":
        await arm_cancel_key(job.id)
        _close(job, "cancelled", now)
        return
    failure = _weights_failure(job, payload, info)
    if failure is not None:
        await arm_cancel_key(job.id)
        _close(job, "failed", now, failure_code=failure)
        return
    version_id = await _register(db, job, payload, clock)
    _close(job, "succeeded", now)
    job.result_model_version_id = version_id


async def run_finish_training_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
    payload: TrainingFinishedPayload,
) -> None:
    """Chốt một job; `stat` chạy **trước** giao dịch (K36: không giữ kết nối DB khi chạm kho).

    Job đã kết thúc → đặt lại `cancel_key` rồi bỏ: một `finished` tới hai lần không được ghi
    lại bản model hay đổi mã lỗi đã chốt (J06). `succeeded` khi job đang `cancelling` thành
    `cancelled` — người dùng đã huỷ, trọng số của lượt đó bị bỏ.
    """
    info = await storage.stat(payload.weights_key) if payload.status == "succeeded" and payload.weights_key else None
    async with session_scope(sessionmaker) as db:
        job = await _lock_job(db, payload.job_id)
        if job is None:
            _ignore(FINISHED_TASK, payload.job_id, "job_missing")
            return
        if job.status in FINISHED_STATUSES:
            await arm_cancel_key(job.id)
            _ignore(FINISHED_TASK, job.id, "job_finished", status=job.status)
            return
        if payload.status == "succeeded":
            await _finish_succeeded(db, job, payload, clock, info)
            return
        await arm_cancel_key(job.id)
        _close(job, payload.status, clock.now(), failure_code=payload.error_code)


async def run_record_model_evaluation(
    sessionmaker: async_sessionmaker[AsyncSession], clock: Clock, payload: EvaluationDonePayload
) -> None:
    """Kết quả đánh giá một bản model; `False` là bình thường (mã tạm, bản đã chốt) → log `info`."""
    async with session_scope(sessionmaker) as db:
        written = await set_evaluation(
            db,
            version_id=payload.version_id,
            status=payload.status,
            metrics=dict(payload.metrics) if payload.metrics is not None else None,
            error_code=payload.error_code,
            clock=clock,
        )
    if not written:
        _log.info("training_evaluation_skipped", extra={"version_id": payload.version_id, "status": payload.status})


def open_storage(clock: Clock) -> ObjectStorage:
    """Kho thật của tiến trình worker; `None` cho `CoreSettings` vì worker không ký URL (NO-085).

    `minio` nhập trễ: nó là phụ thuộc nặng mà mọi lượt dò module `apps.worker.*` phải nạp nếu
    nhập ở đầu file (khuôn `apps/worker/datasets/tasks.py`).
    """
    from packages.storage.factory import create_storage
    from packages.storage.settings import get_storage_settings

    return create_storage(get_storage_settings(), None, clock)


def _on_job_failed(payload: TrainingHeartbeatPayload | TrainingMetricsPayload | TrainingLogPayload, code: str) -> None:
    """`on_failed` của ba task tiến trình: **chỉ** log (lịch `sweep_lost_training_jobs` lo trạng thái).

    Không ghi `training_logs`: văn bản ngoại lệ thô không bao giờ vào log job ([9]), và một
    thông điệp tiến trình hỏng không được tự đánh `failed` một job có thể vẫn đang chạy.
    """
    _log.error("training_bridge_task_failed", extra={"job_id": payload.job_id, "failure": code})


def _on_finished_failed(payload: TrainingFinishedPayload, code: str) -> None:
    """`on_failed` của `finished`: chỉ log; lịch quét nhịp tim chốt job nếu runner đã chết."""
    _log.error("training_bridge_finish_failed", extra={"job_id": payload.job_id, "failure": code})


def _on_evaluation_failed(payload: EvaluationDonePayload, code: str) -> None:
    """`on_failed` của `evaluation_done`: chỉ log; lịch gửi lại của B6-01 lo bản còn `pending`."""
    _log.error("training_bridge_evaluation_failed", extra={"version_id": payload.version_id, "failure": code})


@define_task(name=HEARTBEAT_TASK, payload=TrainingHeartbeatPayload, on_failed=_on_job_failed)
async def record_training_heartbeat(payload: TrainingHeartbeatPayload) -> None:
    """Task mỏng: tài nguyên thật của tiến trình rồi gọi lõi (khuôn `apps/worker/datasets/tasks.py`)."""
    await run_training_heartbeat(worker_sessionmaker(), SystemClock(), payload)


@define_task(name=METRICS_TASK, payload=TrainingMetricsPayload, on_failed=_on_job_failed)
async def record_training_metrics(payload: TrainingMetricsPayload) -> None:
    """Task mỏng: tài nguyên thật của tiến trình rồi gọi lõi."""
    await run_training_metrics(worker_sessionmaker(), SystemClock(), payload)


@define_task(name=LOG_TASK, payload=TrainingLogPayload, on_failed=_on_job_failed)
async def record_training_log(payload: TrainingLogPayload) -> None:
    """Task mỏng: tài nguyên thật của tiến trình rồi gọi lõi."""
    await run_training_log(worker_sessionmaker(), SystemClock(), payload)


@define_task(name=FINISHED_TASK, payload=TrainingFinishedPayload, on_failed=_on_finished_failed)
async def finish_training_job(payload: TrainingFinishedPayload) -> None:
    """Task mỏng: kho thật + `worker_sessionmaker()` rồi gọi lõi."""
    clock = SystemClock()
    await run_finish_training_job(worker_sessionmaker(), open_storage(clock), clock, payload)


@define_task(name=EVALUATION_DONE_TASK, payload=EvaluationDonePayload, on_failed=_on_evaluation_failed)
async def record_model_evaluation(payload: EvaluationDonePayload) -> None:
    """Task mỏng: tài nguyên thật của tiến trình rồi gọi lõi."""
    await run_record_model_evaluation(worker_sessionmaker(), SystemClock(), payload)
