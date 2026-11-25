"""`define_task`: khai báo, phân loại lỗi, thử lại, thông điệp độc (J01, J02, J03, J05, J06, J08).

Task mẫu chạy trên worker Celery **thật** và Redis **thật** (K23). Đường không thử
lại được kiểm bằng `task.apply()` — BE-00 §12 cho phép, và nó tránh phải dựng worker
cho mỗi nhánh lỗi.
"""

import asyncio
import importlib
import logging
import os
import re
import sys
import time
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded
from celery.signals import worker_process_shutdown
from pydantic import BaseModel

from packages.core.error_codes import DEPENDENCY_UNAVAILABLE, VALIDATION
from packages.messaging import tasks as tasks_module
from packages.messaging.celery_app import AFTER_COMMIT_INLINE_ENV, QUEUES, producer_app, send_task
from packages.messaging.redis import AsyncRedis, broker_redis_sync, safe_redis, safe_redis_sync, sync_result
from packages.messaging.settings import get_messaging_settings
from packages.messaging.tasks import (
    DELIVERY_TTL_S,
    INTERNAL,
    MAX_DELIVERIES,
    RETRY_EXHAUSTED,
    TASK_TIMEOUT,
    WORKER_LOST,
    PermanentError,
    TaskPayload,
    TransientError,
    backoff_step,
    define_task,
    registered_tasks,
    runner,
    task_entries,
)
from packages.testing.fixtures.messaging import CELERY_PROCESS_ENV, WorkerFactory, ephemeral_broker, queued_payloads

PROBE_TTL_S = 300
WAIT_TIMEOUT_S = 20.0
SINK_TASK = "pipeline.tests.sink"

FAILURES: list[tuple[str, str]] = []
ASYNC_CLIENT: list[AsyncRedis] = []


class Job(TaskPayload):
    """Payload mẫu: `run_id` cho phép mỗi test đếm riêng phần của mình."""

    schema_version: int = 1
    run_id: str


def record_failure(payload: Job, code: str) -> None:
    """`on_failed` chung của các task thử: ghi `(run_id, mã lỗi)` vào `FAILURES` để test đọc lại."""
    FAILURES.append((payload.run_id, code))


def mark(run_id: str, step: str) -> int:
    """Ghi dấu một lượt chạy vào Redis an toàn và trả số lượt đã chạy."""
    client = safe_redis_sync()
    try:
        count = sync_result(client.incr(f"probe:{run_id}:{step}"), int)
        client.expire(f"probe:{run_id}:{step}", PROBE_TTL_S)
    finally:
        client.close()
    return count


def runs(run_id: str, step: str = "ok") -> int:
    """Số lần task đã chạy `step` cho `run_id`, đọc từ bộ đếm Redis do `mark` tăng."""
    client = safe_redis_sync()
    try:
        return sync_result(client.get(f"probe:{run_id}:{step}") or 0, int)
    finally:
        client.close()


def wait_until(predicate: Callable[[], bool], what: str) -> None:
    """Chờ worker thật làm xong; hết hạn thì hỏng với câu nói rõ đang chờ gì."""
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"quá {WAIT_TIMEOUT_S} giây mà chưa: {what}")


@define_task(name="tests.tasks.ok", payload=Job, on_failed=record_failure)
def run_ok(payload: Job) -> None:
    """Task thành công: chỉ đếm một lượt chạy, làm mốc cho các ca không retry."""
    mark(payload.run_id, "ok")


@define_task(name="tests.tasks.transient", payload=Job, on_failed=record_failure)
def run_transient(payload: Job) -> None:
    """Lỗi tạm hai lượt đầu rồi thành công: kiểm thang retry hồi phục được."""
    if mark(payload.run_id, "ok") <= 2:
        raise TransientError("phụ thuộc bận")


@define_task(name="tests.tasks.ladder", payload=Job, on_failed=record_failure, backoff=(10, 60, 300))
def run_ladder(payload: Job) -> None:
    """Luôn hỏng tạm, với bậc lùi mặc định của hiến chương thay vì bậc 0 của test."""
    raise TransientError("phụ thuộc bận mãi")


@define_task(name="tests.tasks.always_transient", payload=Job, on_failed=record_failure)
def run_always_transient(payload: Job) -> None:
    """Luôn lỗi tạm: đẩy task qua hết thang retry tới `RETRY_EXHAUSTED`."""
    mark(payload.run_id, "ok")
    raise TransientError("phụ thuộc bận mãi")


@define_task(name="tests.tasks.dependency", payload=Job, on_failed=record_failure)
def run_dependency(payload: Job) -> None:
    """`DEPENDENCY_UNAVAILABLE` kèm `retry_after=5`: retry phải tôn trọng thời gian chờ do lỗi chỉ định."""
    mark(payload.run_id, "ok")
    raise DEPENDENCY_UNAVAILABLE.error(retry_after=5)


@define_task(name="tests.tasks.permanent", payload=Job, on_failed=record_failure)
def run_permanent(payload: Job) -> None:
    """Lỗi vĩnh viễn: không retry, `on_failed` nhận đúng mã."""
    mark(payload.run_id, "ok")
    raise PermanentError("BAD_INPUT")


@define_task(name="tests.tasks.timeout", payload=Job, on_failed=record_failure)
def run_timeout(payload: Job) -> None:
    """Quá hạn mềm: phải thành `TASK_TIMEOUT` chứ không rơi vào nhánh lỗi lạ."""
    raise SoftTimeLimitExceeded


@define_task(name="tests.tasks.boom", payload=Job, on_failed=record_failure)
def run_boom(payload: Job) -> None:
    """Ngoại lệ ngoài hệ lỗi của dự án: phải thành `INTERNAL` và log, không lọt ra ngoài."""
    raise ZeroDivisionError("lỗi lạ")


@define_task(name="tests.tasks.other_app_error", payload=Job, on_failed=record_failure)
def run_other_app_error(payload: Job) -> None:
    """`AppError` của lớp API không thuộc hệ lỗi hàng đợi: task phải thành `INTERNAL` và vẫn được ack."""
    raise VALIDATION.error(field="name")


def explode(payload: Job, code: str) -> None:
    """`on_failed` luôn ném: kiểm lỗi trong `on_failed` không làm hỏng việc xác nhận task."""
    raise RuntimeError(f"on_failed hỏng với {code}")


@define_task(name="tests.tasks.on_failed_explodes", payload=Job, on_failed=explode)
def run_on_failed_explodes(payload: Job) -> None:
    """Task lỗi vĩnh viễn mà `on_failed` ném: chỉ gửi đúng một kết quả hỏng."""
    raise PermanentError("BAD_INPUT")


PRIVATE_TAIL = "du-lieu-nguoi-dung-o-cuoi"


def explode_verbosely(payload: Job, code: str) -> None:
    """`on_failed` của module khác ném kèm thông điệp dài, phần đuôi mang dữ liệu người dùng."""
    raise RuntimeError("x" * 300 + PRIVATE_TAIL)


@define_task(name="tests.tasks.on_failed_explodes_verbosely", payload=Job, on_failed=explode_verbosely)
def run_on_failed_explodes_verbosely(payload: Job) -> None:
    """Như trên nhưng thông điệp lỗi chứa dữ liệu người dùng: log không được in phần đó."""
    raise PermanentError("BAD_INPUT")


@define_task(name="tests.tasks.sender", payload=Job, on_failed=record_failure)
def run_sender(payload: Job) -> None:
    """Task gửi `SINK_TASK` qua `send_task`, để test đếm thông điệp thật xuất hiện trên hàng."""
    send_task(SINK_TASK, payload)


@define_task(name="tests.tasks.async_probe", payload=Job, on_failed=record_failure)
async def run_async_probe(payload: Job) -> None:
    """Dùng lại **một** client `redis.asyncio` giữa hai lượt chạy (BE-00 §7)."""
    if not ASYNC_CLIENT:
        ASYNC_CLIENT.append(safe_redis())
    await ASYNC_CLIENT[0].set(f"probe:{payload.run_id}:ok", "1", ex=PROBE_TTL_S)


def apply_task(task: Any, run_id: str, **options: Any) -> Any:
    """Chạy một task tại chỗ (không `task_always_eager`, BE-00 §12)."""
    return task.apply(args=[Job(run_id=run_id).model_dump(mode="json")], **options)


# ---------------------------------------------------------------------------
# Khai báo
# ---------------------------------------------------------------------------


def test_payload_without_a_schema_version_field_is_refused() -> None:
    """Payload không có trường `schema_version` bị từ chối khi khai task."""

    class NoVersion(BaseModel):
        """Payload thiếu `schema_version`."""

    with pytest.raises(TypeError, match="schema_version"):
        define_task(name="tests.tasks.never", payload=NoVersion, on_failed=record_failure)


def test_payload_whose_schema_version_has_no_default_is_refused() -> None:
    """Không có mặc định thì không có gì để so, và mọi thông điệp cũ đều lọt qua."""
    with pytest.raises(TypeError, match="schema_version"):
        define_task(name="tests.tasks.never", payload=TaskPayload, on_failed=record_failure)


def test_declaring_two_tasks_with_the_same_name_is_refused() -> None:
    """Khai hai task trùng tên bị từ chối ngay, để không có task nào âm thầm thay task kia."""
    with pytest.raises(ValueError, match=re.escape("tests.tasks.ok")):

        @define_task(name="tests.tasks.ok", payload=Job, on_failed=record_failure)
        def run_ok_again(payload: Job) -> None:
            """Trùng tên với task đã khai ở trên."""


@pytest.mark.parametrize("code", ["", "ab", "bad_input", "1BAD", "A" * 65])
def test_permanent_error_codes_follow_the_registry_pattern(code: str) -> None:
    """Mã `PermanentError` sai mẫu sổ mã lỗi (rỗng, quá ngắn, chữ thường, quá dài) bị từ chối khi tạo."""
    with pytest.raises(ValueError, match="mã lỗi sai mẫu"):
        PermanentError(code)


def test_registered_tasks_leaves_out_tasks_declared_in_tests() -> None:
    """Sổ task là đầu vào của cổng case: nó chỉ được phụ thuộc mã sản phẩm.

    Khẳng định **vắng mặt** task của module test, không khẳng định sổ rỗng: mọi prompt
    khai `define_task`/`@periodic` đều làm sổ thật khác rỗng (BE-00 §7 "Dọn rác").
    """
    assert "run_ok" not in registered_tasks()
    assert "run_ok" in {entry.function for entry in task_entries()}


def test_the_ledger_records_the_declaring_module() -> None:
    """Sổ task ghi đúng hàm và module khai, để tra ngược task tới mã nguồn của nó."""
    entry = next(entry for entry in task_entries() if entry.name == "tests.tasks.ok")

    assert (entry.function, entry.module) == ("run_ok", __name__)


def test_backoff_follows_the_charter_ladder() -> None:
    """J02: lùi 10, 60, 300 giây rồi hết lượt — đúng bậc của hiến chương."""
    assert [backoff_step(retries, (10, 60, 300)) for retries in range(4)] == [10, 60, 300, None]
    assert backoff_step(0, ()) is None


# ---------------------------------------------------------------------------
# Phân loại lỗi — chạy tại chỗ
# ---------------------------------------------------------------------------


def test_permanent_error_fails_once_without_retrying(messaging_env: None) -> None:
    """J03: lỗi vĩnh viễn ghi mã của module chủ và dừng hẳn."""
    FAILURES.clear()
    result = apply_task(run_permanent, "perm-1")

    assert result.state == "SUCCESS"
    assert FAILURES == [("perm-1", "BAD_INPUT")]
    assert runs("perm-1") == 1


def test_soft_time_limit_becomes_a_timeout_failure(messaging_env: None) -> None:
    """J05: quá thời gian là hỏng có mã, không phải treo."""
    FAILURES.clear()
    apply_task(run_timeout, "timeout-1")

    assert FAILURES == [("timeout-1", TASK_TIMEOUT)]


def test_an_unexpected_error_fails_as_internal(messaging_env: None, caplog: pytest.LogCaptureFixture) -> None:
    """Ngoại lệ lạ thành `INTERNAL` và được log, không làm worker chết."""
    FAILURES.clear()
    with caplog.at_level(logging.ERROR):
        apply_task(run_boom, "boom-1")

    assert FAILURES == [("boom-1", INTERNAL)]
    assert "task_failed" in caplog.text


def test_an_app_error_other_than_dependency_is_internal(messaging_env: None) -> None:
    """Chỉ `DEPENDENCY_UNAVAILABLE` là lỗi tạm; mã khác là lỗi lập trình, không thử lại."""
    FAILURES.clear()
    result = apply_task(run_other_app_error, "app-1")

    assert result.state == "SUCCESS"
    assert FAILURES == [("app-1", INTERNAL)]


def test_a_transient_error_climbs_the_whole_backoff_ladder(messaging_env: None) -> None:
    """J02: `task.apply()` chạy luôn cả các lượt thử lại, nên đếm được đủ bốn lượt."""
    FAILURES.clear()
    backoff = get_messaging_settings().task_retry_backoff_s
    apply_task(run_always_transient, "retry-1")

    assert runs("retry-1") == len(backoff) + 1
    assert FAILURES == [("retry-1", RETRY_EXHAUSTED)]


def test_a_task_that_starts_out_of_retries_fails_immediately(messaging_env: None) -> None:
    """J02: hết bậc lùi thì task hỏng có mã thay vì quay vòng mãi."""
    FAILURES.clear()
    backoff = get_messaging_settings().task_retry_backoff_s
    result = apply_task(run_always_transient, "retry-2", retries=len(backoff))

    assert result.state == "SUCCESS"
    assert runs("retry-2") == 1
    assert FAILURES == [("retry-2", RETRY_EXHAUSTED)]


def test_a_failing_on_failed_is_logged_and_swallowed(messaging_env: None, caplog: pytest.LogCaptureFixture) -> None:
    """`on_failed` là mã của module khác; nó hỏng thì worker vẫn phải sống."""
    with caplog.at_level(logging.ERROR):
        result = apply_task(run_on_failed_explodes, "explode-1")

    assert result.state == "SUCCESS"
    assert "on_failed_error" in caplog.text


def test_an_on_failed_error_is_logged_briefly(messaging_env: None, caplog: pytest.LogCaptureFixture) -> None:
    """Lỗi của `on_failed` (mã module khác) chỉ vào log bằng tên lớp + 200 ký tự đầu, không stack:
    thông điệp của nó có thể mang dữ liệu người dùng (NO-030)."""
    with caplog.at_level(logging.ERROR):
        apply_task(run_on_failed_explodes_verbosely, "explode-2")

    record = next(record for record in caplog.records if record.msg == "on_failed_error")
    assert getattr(record, "error", None) == "RuntimeError: " + "x" * 200
    assert record.exc_info is None
    assert PRIVATE_TAIL not in caplog.text


@pytest.mark.parametrize(
    "args",
    [[{"bad": 1}], ["chuỗi"], [{"schema_version": 2, "run_id": "poison"}], [42]],
)
def test_a_poison_message_is_dropped_and_logged(
    messaging_env: None, caplog: pytest.LogCaptureFixture, args: list[object]
) -> None:
    """J08: thông điệp sai schema bị loại và ack, không thử lại, không gọi `on_failed`."""
    FAILURES.clear()
    with caplog.at_level(logging.WARNING):
        result = run_ok.apply(args=args)

    assert result.state == "SUCCESS"
    assert FAILURES == []
    assert "poison_message" in caplog.text


def test_the_poison_log_never_prints_the_message_body(messaging_env: None, caplog: pytest.LogCaptureFixture) -> None:
    """Thông điệp hỏng schema chỉ log mô tả lỗi, không in thân (có thể chứa dữ liệu người dùng)."""
    with caplog.at_level(logging.WARNING):
        run_ok.apply(args=[{"run_id": "bi-mat", "schema_version": 1, "token": "khong-duoc-in"}])

    assert "khong-duoc-in" not in caplog.text


def test_too_many_redeliveries_end_as_worker_lost(messaging_env: None) -> None:
    """Giao lại mãi vì worker chết là mất việc; quá trần thì ack và báo hỏng."""
    FAILURES.clear()
    client = safe_redis_sync()
    try:
        client.set("delivery:tid-lost", MAX_DELIVERIES)
        apply_task(run_ok, "lost-1", task_id="tid-lost")
    finally:
        client.delete("delivery:tid-lost")
        client.close()

    assert FAILURES == [("lost-1", WORKER_LOST)]
    assert runs("lost-1") == 0


def test_the_delivery_count_and_its_ttl_are_one_atomic_command(
    messaging_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`INCR` rồi `EXPIRE` rời nhau: đứt kết nối giữa hai lệnh là bộ đếm sống mãi và một `task_id`
    dùng lại bị gán nhầm `WORKER_LOST` sớm (NO-023). Một lệnh duy nhất thì không có khe đó.

    Chỉ **quan sát** lệnh gửi qua client thật (Redis vẫn chạy lệnh), không mock Redis (K23).
    """
    client = tasks_module._delivery_client.get()
    sent: list[str] = []
    real: Callable[..., Any] = client.execute_command

    def spy(*args: Any, **options: Any) -> Any:
        """Ghi tên lệnh rồi gửi thật."""
        sent.append(str(args[0]).upper())
        return real(*args, **options)

    monkeypatch.setattr(client, "execute_command", spy)
    try:
        apply_task(run_ok, "atomic-1", task_id="tid-atomic")
        assert sent == ["EVAL"]
        assert 0 < sync_result(client.ttl("delivery:tid-atomic"), int) <= DELIVERY_TTL_S
        assert sync_result(client.get("delivery:tid-atomic"), int) == 1
    finally:
        client.delete("delivery:tid-atomic")
    assert runs("atomic-1") == 1


def test_a_retried_task_is_not_mistaken_for_a_lost_worker(messaging_env: None) -> None:
    """Mỗi lượt `retry` cũng là một lượt giao: không trừ đi thì J02 hoá thành `WORKER_LOST`."""
    FAILURES.clear()
    client = safe_redis_sync()
    try:
        client.set("delivery:tid-retried", MAX_DELIVERIES)
        apply_task(run_ok, "retried-1", task_id="tid-retried", retries=MAX_DELIVERIES)
    finally:
        client.delete("delivery:tid-retried")
        client.close()

    assert FAILURES == []
    assert runs("retried-1") == 1


def test_the_worker_process_closes_its_event_loop_on_shutdown() -> None:
    """Tiến trình con của worker tắt thì đóng vòng sự kiện đã mở lúc khởi động (NO-024);
    lần `runner()` sau (tiến trình khác, hay test sau) dựng vòng mới chạy được."""
    loop = runner().get_loop()

    worker_process_shutdown.send(sender=None, pid=os.getpid(), exitcode=0)

    assert loop.is_closed()
    assert runner().get_loop() is not loop
    assert runner().run(asyncio.sleep(0, result="chạy")) == "chạy"
    tasks_module.reset_runner()
    tasks_module.reset_runner()  # chưa có vòng nào: không có gì để đóng, không lỗi


def test_async_tasks_share_one_event_loop(messaging_env: None) -> None:
    """Hai lượt `async` liên tiếp dùng chung một client: `asyncio.run` mỗi task sẽ hỏng."""
    apply_task(run_async_probe, "async-1")
    apply_task(run_async_probe, "async-2")

    assert runs("async-1") == 1
    assert runs("async-2") == 1


# ---------------------------------------------------------------------------
# Worker thật
# ---------------------------------------------------------------------------


def test_a_valid_task_runs_on_a_real_worker(messaging_env: None, celery_worker_factory: WorkerFactory) -> None:
    """J01: hiệu ứng quan sát được là một khoá Redis có TTL."""
    FAILURES.clear()
    with celery_worker_factory(["default"]):
        send_task("tests.tasks.ok", Job(run_id="live-1"))
        wait_until(lambda: runs("live-1") == 1, "task chạy xong")

    client = safe_redis_sync()
    try:
        assert 0 < sync_result(client.ttl("probe:live-1:ok"), int) <= PROBE_TTL_S
    finally:
        client.close()
    assert FAILURES == []


def test_a_transient_failure_is_retried_until_it_succeeds(
    messaging_env: None, celery_worker_factory: WorkerFactory
) -> None:
    """J02: hỏng tạm hai lần rồi thành công → đúng ba lượt chạy, không gọi `on_failed`."""
    FAILURES.clear()
    with celery_worker_factory(["default"]):
        send_task("tests.tasks.transient", Job(run_id="live-2"))
        wait_until(lambda: runs("live-2") == 3, "task chạy đủ ba lượt")
        time.sleep(0.2)

    assert runs("live-2") == 3
    assert FAILURES == []


def test_a_task_that_never_recovers_reports_retry_exhausted(
    messaging_env: None, celery_worker_factory: WorkerFactory
) -> None:
    """Task lỗi tạm mãi qua worker thật: sau hết thang retry báo `RETRY_EXHAUSTED` một lần."""
    FAILURES.clear()
    backoff = get_messaging_settings().task_retry_backoff_s
    with celery_worker_factory(["default"]):
        send_task("tests.tasks.always_transient", Job(run_id="live-3"))
        wait_until(lambda: ("live-3", RETRY_EXHAUSTED) in FAILURES, "task báo hết lượt thử lại")

    assert runs("live-3") == len(backoff) + 1


def test_a_dependency_failure_inside_a_task_is_retried(
    messaging_env: None, celery_worker_factory: WorkerFactory
) -> None:
    """`DEPENDENCY_UNAVAILABLE` trong task là lỗi tạm (BE-00 §7)."""
    FAILURES.clear()
    with celery_worker_factory(["default"]):
        send_task("tests.tasks.dependency", Job(run_id="live-4"))
        wait_until(lambda: ("live-4", RETRY_EXHAUSTED) in FAILURES, "task báo hết lượt thử lại")

    assert runs("live-4") > 1


def test_a_worker_survives_a_poison_message(messaging_env: None, celery_worker_factory: WorkerFactory) -> None:
    """J08: thông điệp độc bị loại, thông điệp hợp lệ gửi sau đó vẫn chạy."""
    with celery_worker_factory(["default"]):
        producer_app().send_task("tests.tasks.ok", args=[{"bad": 1}], queue="default", retry=False)
        send_task("tests.tasks.ok", Job(run_id="live-5"))
        wait_until(lambda: runs("live-5") == 1, "task hợp lệ chạy sau thông điệp độc")


def test_a_task_can_queue_work_for_another_queue(messaging_env: None, celery_worker_factory: WorkerFactory) -> None:
    """Worker thử chỉ nghe `default`, nên thông điệp gửi sang `pipeline.cpu` còn nguyên để đếm."""
    client = broker_redis_sync(get_messaging_settings())
    try:
        client.delete(*QUEUES)
        with celery_worker_factory(["default"]):
            send_task("tests.tasks.sender", Job(run_id="live-6"))
            wait_until(lambda: client.llen("pipeline.cpu") == 1, "thông điệp sang pipeline.cpu")

        assert queued_payloads(client, "pipeline.cpu") == [{"schema_version": 1, "run_id": "live-6"}]
    finally:
        client.delete(*QUEUES)
        client.close()


def test_the_worker_factory_refuses_an_unknown_queue(celery_worker_factory: WorkerFactory) -> None:
    """Fixture dựng worker từ chối tên hàng không khai, thay vì dựng worker không nghe gì."""
    with pytest.raises(ValueError, match="hàng lạ"), celery_worker_factory(["khong-co-hang-nay"]):
        pytest.fail("không được dựng worker cho hàng không khai")


def test_the_worker_factory_leaves_the_root_logger_alone(
    messaging_env: None, celery_worker_factory: WorkerFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """NO-078: mức và handler của logger gốc trước, trong và sau worker thử như nhau.

    Celery từng đặt logger gốc ở ERROR và thay handler (gỡ cả handler bắt log của pytest):
    `WARNING` của test chạy sau không vào báo cáo đỏ nếu test không tự `caplog.at_level`.
    """
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    with celery_worker_factory(["default"]):
        during = (root.level, list(root.handlers))
    assert during == before
    assert (root.level, list(root.handlers)) == before
    logging.getLogger("apps.sau_worker").warning("rate_limit_open")
    assert [r.getMessage() for r in caplog.records if r.name == "apps.sau_worker"] == ["rate_limit_open"]


def _process_logging_state() -> tuple[dict[str, str], object, tuple[object, ...]]:
    """Dấu cấp tiến trình `app.log.setup` của Celery có thể để lại: `os.environ`, `showwarning`, bộ lọc `warnings`."""
    return dict(os.environ), warnings.showwarning, tuple(warnings.filters)


@pytest.mark.parametrize("preset", [False, True], ids=["clean", "preset"])
def test_the_worker_factory_leaves_no_process_logging_state(
    messaging_env: None, celery_worker_factory: WorkerFactory, monkeypatch: pytest.MonkeyPatch, preset: bool
) -> None:
    """NO-087: biến môi trường, `logging.captureWarnings` và bộ lọc `warnings` sau worker thử như trước nó.

    Trạng thái đầu đặt tường minh — khoá vắng và capture tắt, hoặc khoá có giá trị và capture bật — để test
    vẫn đỏ khi một worker thử chạy trước trong cùng tiến trình đã để dấu, và để capture bật từ trước không bị tắt.
    `catch_warnings` trả `showwarning` nhưng không trả hàm gốc `logging` tự nhớ, nên capture còn được kiểm bằng
    hành vi (NO-093): sau worker, đảo capture phải có tác dụng — ca clean bật được, ca preset tắt về đúng hàm gốc.
    """
    for key in CELERY_PROCESS_ENV:
        if preset:
            monkeypatch.setenv(key, "trước worker")
        else:
            monkeypatch.delenv(key, raising=False)
    logging.captureWarnings(False)
    original = warnings.showwarning
    logging.captureWarnings(preset)
    try:
        before = _process_logging_state()
        with celery_worker_factory(["default"]):
            during = _process_logging_state()
        after = _process_logging_state()
        logging.captureWarnings(not preset)
        toggled = warnings.showwarning
    finally:
        logging.captureWarnings(False)
    assert during != before
    assert after == before
    assert (toggled is original) == preset


def test_the_inline_flag_does_not_survive_the_worker_fixtures() -> None:
    """Test ngay sau các test dùng `celery_test_app`/`celery_worker_factory` (BE-00 §7)."""
    assert AFTER_COMMIT_INLINE_ENV not in os.environ


PROBE_TASKS = '''
"""Task mẫu của một module `apps.ml` giả, để kiểm việc dò module một cấp."""

from packages.messaging.tasks import TaskPayload, define_task


class ProbePayload(TaskPayload):
    """Payload của task thăm dò."""

    schema_version: int = 1


def probe_failed(payload: ProbePayload, code: str) -> None:
    """Task thăm dò không ghi trạng thái hỏng ở đâu cả."""


@define_task(name="ml.infer.probe.run", payload=ProbePayload, on_failed=probe_failed)
def probe_infer(payload: ProbePayload) -> None:
    """Thân rỗng: test chỉ kiểm việc dò module."""
'''


def _drop_new_ml_modules(before: set[str]) -> None:
    """Xoá khỏi `sys.modules` các tên `apps.ml`/`apps.ml.*` chưa có trong `before`.

    Bất biến: module `apps.ml.*` đã nạp từ trước khi fixture chạy (ví dụ do một test
    khác trong cùng tiến trình nhập trước) phải giữ nguyên danh tính; chỉ module do
    chính lượt test này nạp (gồm gói thăm dò) mới bị xoá, nếu không lần nhập lại sau
    sẽ chạy lại `define_task` và `register_task` ném `ValueError: task đã khai`.
    """

    def is_new_ml_module(name: str) -> bool:
        """True nếu `name` là `apps.ml`/`apps.ml.*` chưa có trong `before`."""
        return (name == "apps.ml" or name.startswith("apps.ml.")) and name not in before

    for name in [name for name in sys.modules if is_new_ml_module(name)]:
        del sys.modules[name]


@pytest.fixture
def ml_probe_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Một gói `apps.ml.probe.tasks` thật, nối vào `apps.__path__` (BE-00 §12).

    Trả sổ task về nguyên trạng khi xong: sổ là biến toàn tiến trình, để sót một dòng
    là test sau thấy một task lạ. Chỉ xoá khỏi `sys.modules` các module do chính lượt
    test này nạp (xem `_drop_new_ml_modules`), không đụng module `apps.ml.*` đã nạp
    từ trước.
    """
    import apps.ml
    import packages.messaging.tasks as tasks_module

    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "__init__.py").write_text("", encoding="utf-8")
    (probe / "tasks.py").write_text(PROBE_TASKS, encoding="utf-8")
    monkeypatch.setattr(apps.ml, "__path__", [*apps.ml.__path__, str(tmp_path)])
    # Gói vừa được tạo sau khi tiến trình khởi động; không dọn cache thì bộ tìm module
    # không bao giờ thấy nó (đây cũng là việc `monkeypatch.syspath_prepend` tự làm).
    importlib.invalidate_caches()
    before = set(sys.modules)
    saved = dict(tasks_module._TASKS)
    yield
    tasks_module._TASKS.clear()
    tasks_module._TASKS.update(saved)
    _drop_new_ml_modules(before)


def test_registered_tasks_finds_tasks_under_the_ml_app(ml_probe_package: None) -> None:
    """Sổ phải thấy task của `apps.ml` dù tiến trình worker không bao giờ nhập gói đó."""
    assert "probe_infer" in registered_tasks()


@pytest.fixture
def restored_task_ledger() -> Iterator[None]:
    """Trả sổ task toàn tiến trình về nguyên trạng khi test xong.

    Nhập một module `apps.ml.*` lần đầu chạy `define_task` và thêm dòng vào sổ; không trả lại thì
    test sau thấy task lạ.
    """
    import packages.messaging.tasks as tasks_module

    saved = dict(tasks_module._TASKS)
    yield
    tasks_module._TASKS.clear()
    tasks_module._TASKS.update(saved)


def test_drop_new_ml_modules_keeps_modules_loaded_before_the_snapshot(restored_task_ledger: None) -> None:
    """FIX-115: module `apps.ml.*` nạp trước `before` phải sống sót; module nạp sau bị xoá."""
    import apps.ml.objects.tasks as preloaded_module

    before = set(sys.modules)
    assert "apps.ml.objects.tasks" in before

    fake_name = "apps.ml.__fix_115_probe__"
    sys.modules[fake_name] = preloaded_module

    _drop_new_ml_modules(before)

    assert sys.modules["apps.ml.objects.tasks"] is preloaded_module
    assert fake_name not in sys.modules

    # Nhập lại module đã nạp từ trước không được ném `ValueError: task đã khai`
    # (vì nó vẫn còn trong `sys.modules`, không chạy lại `define_task`).
    assert importlib.import_module("apps.ml.objects.tasks") is preloaded_module


@pytest.mark.parametrize(("retries", "countdown"), [(0, 10), (1, 60), (2, 300)])
def test_the_retry_countdown_walks_the_charter_ladder(messaging_env: None, retries: int, countdown: int) -> None:
    """J02: `self.retry` của task đã bind nhận đúng 10, 60, 300 giây."""
    with pytest.raises(Retry) as caught:
        apply_task(run_ladder, "ladder-1", retries=retries, throw=True)

    assert caught.value.when == countdown


def test_the_ladder_ends_in_retry_exhausted(messaging_env: None) -> None:
    """Ở lượt retry cuối thang, task báo `RETRY_EXHAUSTED` chứ không retry tiếp."""
    FAILURES.clear()
    apply_task(run_ladder, "ladder-2", retries=3, throw=True)

    assert FAILURES == [("ladder-2", RETRY_EXHAUSTED)]


def test_a_full_safe_database_makes_a_task_retry_instead_of_dying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bộ đếm lượt giao hỏng vì Redis chạm `maxmemory` là **lỗi tạm**, không phải `INTERNAL`.

    Nếu nó rơi xuống nhánh "ngoại lệ lạ" thì mọi task đều bị ack và mất việc đúng lúc
    broker đang kẹt — hỏng nặng nhất đúng lúc tệ nhất.
    """
    FAILURES.clear()
    with ephemeral_broker(monkeypatch) as admin:
        admin.config_set("maxmemory", "1")
        apply_task(run_ok, "oom-1")

    assert FAILURES == [("oom-1", RETRY_EXHAUSTED)]


def test_every_failure_log_carries_the_task_id(messaging_env: None, caplog: pytest.LogCaptureFixture) -> None:
    """Thông điệp độc bị ack; không có `task_id` trong log thì không truy được là cái nào."""
    with caplog.at_level(logging.WARNING):
        result = run_ok.apply(args=[{"bad": 1}], task_id="tid-truy-vet")

    poison = [record for record in caplog.records if record.msg == "poison_message"]
    assert result.state == "SUCCESS"
    assert [getattr(record, "task_id", None) for record in poison] == ["tid-truy-vet"]
