"""App Celery: định tuyến theo tiền tố, cấu hình theo hiến chương, và đường gửi task."""

import json
import logging
import os
import socket
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from celery import _state as celery_state
from celery.signals import worker_process_init, worker_process_shutdown

from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.messaging import celery_app as celery_app_module
from packages.messaging.celery_app import (
    AFTER_COMMIT_INLINE_ENV,
    DEFAULT_QUEUE,
    QUEUES,
    create_celery,
    metrics_port_for_process,
    producer_app,
    queue_for,
    reset_producer_app,
    route_task,
    send_task,
    start_metrics_exporter,
    stop_metrics_exporter,
)
from packages.messaging.redis import broker_redis_sync
from packages.messaging.settings import MessagingSettings, get_messaging_settings, reset_messaging_settings_cache
from packages.messaging.tasks import TaskPayload
from packages.observability import exporter as exporter_module
from packages.observability.settings import reset_observability_settings_cache
from packages.testing.fixtures.messaging import WorkerFactory, queued_payloads, queued_tasks
from packages.testing.fixtures.services import refused_url

REPO_ROOT = Path(__file__).resolve().parents[3]


def _free_port() -> int:
    """Một cổng localhost còn rỗi — exporter phải nghe cổng cấu hình, không cổng ngẫu nhiên."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _stop_exporters() -> None:
    """Đóng hẳn exporter của tiến trình test, kể cả khi nhiều lượt gọi đã tăng đếm tham chiếu."""
    stop_metrics_exporter()
    while exporter_module._instance is not None:
        exporter_module._instance.stop()


class Ping(TaskPayload):
    """Payload nhỏ nhất của test gửi task."""

    schema_version: int = 1
    run_id: str


def conf() -> MessagingSettings:
    """Cấu hình messaging cố định, không đọc biến môi trường."""
    return MessagingSettings(redis_broker_url="redis://broker:6379/0", redis_cache_url="redis://cache:6379/0")


@pytest.mark.parametrize(
    ("name", "queue"),
    [
        ("pipeline.drawings.render", "pipeline.cpu"),
        ("ml.infer.walls.segment", "ml.infer"),
        ("ml.training.yolo.launch", "ml.training"),
        ("bake.lighting.run", "bake"),
        ("bake", DEFAULT_QUEUE),
        ("notifications.fanout", DEFAULT_QUEUE),
        ("pipeline", DEFAULT_QUEUE),
        ("ml.inference.walls", DEFAULT_QUEUE),
    ],
)
def test_queue_is_derived_from_the_task_name_prefix(name: str, queue: str) -> None:
    """Tên task → hàng theo tiền tố."""
    assert queue_for(name) == queue
    assert route_task(name, [], {}, {}) == {"queue": queue}


def test_every_derived_queue_is_a_declared_queue() -> None:
    """Mọi hàng suy ra từ tiền tố đều nằm trong `QUEUES`."""
    assert {queue_for(name) for name in ("pipeline.a", "ml.infer.a", "ml.training.a", "bake.a", "a")} == set(QUEUES)


def test_create_celery_applies_the_charter_configuration() -> None:
    """`create_celery` áp đúng cấu hình hiến chương."""
    settings = conf()
    app = create_celery("worker", settings)

    assert app.conf.task_serializer == app.conf.result_serializer == "json"
    assert app.conf.accept_content == app.conf.result_accept_content == ["json"]
    assert app.conf.task_ignore_result is True
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_default_queue == DEFAULT_QUEUE
    assert app.conf.task_time_limit == settings.task_time_limit_s
    assert app.conf.task_soft_time_limit == settings.task_soft_time_limit_s
    assert app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert app.conf.broker_connection_retry_on_startup is True
    assert app.conf.broker_url == settings.redis_broker_url


def test_visibility_timeout_is_set_in_exactly_one_place() -> None:
    """kombu không nhận `visibility_timeout` theo hàng; hai nơi khai là hai giá trị lệch nhau."""
    app = create_celery("worker", conf())

    assert app.conf.broker_transport_options == {"visibility_timeout": 7200}
    assert not app.conf.task_queues


def test_no_result_backend_is_configured() -> None:
    """Có `result_backend` là mở đường cho chord/group, thứ hiến chương cấm (K34)."""
    assert not create_celery("worker", conf()).conf.result_backend


def test_create_celery_turns_on_inline_after_commit_callbacks() -> None:
    """App chạy task bật cờ callback after-commit chạy tại chỗ."""
    create_celery("worker", conf())

    assert os.environ[AFTER_COMMIT_INLINE_ENV] == "1"


def test_after_commit_flag_does_not_leak_between_tests() -> None:
    """Test ngay sau một test gọi `create_celery`: cờ phải vắng mặt lại (BE-00 §7)."""
    assert AFTER_COMMIT_INLINE_ENV not in os.environ


def test_producer_app_is_built_once_and_never_becomes_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """App gửi dựng một lần và không thành app hiện hành."""
    monkeypatch.setenv("REDIS_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.setenv("REDIS_CACHE_URL", "redis://cache:6379/0")
    reset_messaging_settings_cache()
    reset_producer_app()

    app = producer_app()
    assert producer_app() is app
    # `set_as_current=False`: app gửi không được thành app "hiện hành" của tiến trình.
    assert celery_state.get_current_app() is not app

    reset_producer_app()
    assert producer_app() is not app
    reset_messaging_settings_cache()


def test_producer_app_does_not_wait_on_a_hung_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gửi task nằm trên đường trả response: broker treo phải hỏng ngay, không giữ luồng."""
    monkeypatch.setenv("REDIS_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.setenv("REDIS_CACHE_URL", "redis://cache:6379/0")
    reset_messaging_settings_cache()
    reset_producer_app()

    options = producer_app().conf.broker_transport_options
    assert options["socket_timeout"] == options["socket_connect_timeout"] == 1
    assert producer_app().conf.broker_connection_retry is False
    assert producer_app().conf.broker_connection_max_retries == 0
    reset_messaging_settings_cache()


def test_producer_app_does_not_set_the_inline_flag(messaging_env: None) -> None:
    """API giữ executor riêng cho callback sau commit; chỉ worker chạy chúng tại chỗ."""
    producer_app()

    assert AFTER_COMMIT_INLINE_ENV not in os.environ


def test_queued_tasks__names_every_message_on_a_shared_queue_newest_first(messaging_env: None) -> None:
    """Hai task khác nhau trên cùng hàng: `queued_tasks` trả tên cả hai (`LPUSH` → mới nhất trước)."""
    client = broker_redis_sync(get_messaging_settings())
    try:
        client.delete(*QUEUES)
        send_task("pipeline.drawings.render", Ping(run_id="run_1"))
        send_task("pipeline.build.run", Ping(run_id="run_2"))

        assert queued_tasks(client, "pipeline.cpu") == ["pipeline.build.run", "pipeline.drawings.render"]
    finally:
        client.delete(*QUEUES)
        client.close()


def test_send_task_puts_one_message_on_the_queue_named_by_the_prefix(messaging_env: None) -> None:
    """`send_task` đặt đúng một thông điệp lên hàng theo tiền tố."""
    client = broker_redis_sync(get_messaging_settings())
    try:
        client.delete(*QUEUES)
        send_task("pipeline.drawings.render", Ping(run_id="run_1"))

        assert queued_payloads(client, "pipeline.cpu") == [{"schema_version": 1, "run_id": "run_1"}]
        assert client.llen(DEFAULT_QUEUE) == 0
    finally:
        client.delete(*QUEUES)
        client.close()


def test_send_task_countdown_sets_an_eta_and_default_sends_none(messaging_env: None) -> None:
    """NO-565: `countdown` đặt ETA (header `eta`) cách lúc gửi ngần ấy giây; không truyền thì không có ETA."""
    client = broker_redis_sync(get_messaging_settings())
    try:
        client.delete(*QUEUES)
        before = datetime.now(UTC)
        send_task("pipeline.drawings.render", Ping(run_id="run_1"), countdown=600)
        send_task("pipeline.drawings.render", Ping(run_id="run_2"))

        newest, oldest = (json.loads(raw)["headers"] for raw in client.lrange("pipeline.cpu", 0, -1))
        assert newest["eta"] is None
        delay = datetime.fromisoformat(oldest["eta"]) - before
        assert timedelta(seconds=599) <= delay <= timedelta(seconds=660)
    finally:
        client.delete(*QUEUES)
        client.close()


def test_send_task_reports_an_unreachable_broker_as_dependency_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C13: broker không nối được → 503 + `retry_after`, task gọi nó thử lại như lỗi tạm."""
    monkeypatch.setenv("REDIS_BROKER_URL", refused_url("redis"))
    monkeypatch.setenv("REDIS_CACHE_URL", refused_url("redis"))
    reset_messaging_settings_cache()
    reset_producer_app()

    with pytest.raises(AppError) as caught:
        send_task("pipeline.drawings.render", Ping(run_id="run_1"))

    assert caught.value.code is DEPENDENCY_UNAVAILABLE
    assert caught.value.retry_after == 5
    reset_messaging_settings_cache()


def test_every_celery_process_starts_the_metrics_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-161: `worker` và `ml` đều mở `/metrics` vì cả hai dựng app bằng `create_celery`.

    Gắn vào `worker_process_init` ở đây chứ không ở từng điểm vào: một cài đặt cho cả hai
    ảnh, không bản chép (R-07). `METRICS_PORT=0` là "tắt", như `metrics_lifespan` của API.
    """
    port = _free_port()
    monkeypatch.setenv("METRICS_PORT", str(port))
    monkeypatch.setenv("METRICS_HOST", "127.0.0.1")
    reset_observability_settings_cache()
    try:
        # Bắn chính tín hiệu Celery gửi ở mỗi tiến trình con: chứng minh cả **dây nối** lẫn
        # hành vi, thay vì soi sổ receiver riêng của `celery.utils.dispatch.Signal`.
        assert worker_process_init.has_listeners()
        worker_process_init.send(sender=None)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            body = response.read().decode("utf-8")
    finally:
        _stop_exporters()
        reset_observability_settings_cache()

    assert "appback_metrics_series_dropped_total" in body


def test_a_celery_process_shutdown_closes_the_metrics_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tín hiệu tắt tiến trình con phải trả tham chiếu exporter (review DEBT-01 finding 1).

    Không có receiver `worker_process_shutdown` thì mọi tiến trình đã nhận
    `worker_process_init` giữ cổng mở tới hết đời tiến trình.
    """
    port = _free_port()
    monkeypatch.setenv("METRICS_PORT", str(port))
    monkeypatch.setenv("METRICS_HOST", "127.0.0.1")
    reset_observability_settings_cache()
    try:
        assert worker_process_shutdown.has_listeners()
        worker_process_init.send(sender=None)
        assert exporter_module._instance is not None
        worker_process_shutdown.send(sender=None)
        assert exporter_module._instance is None
    finally:
        _stop_exporters()
        reset_observability_settings_cache()


def test_a_test_worker_leaves_no_metrics_exporter_in_the_pytest_process(
    celery_worker_factory: WorkerFactory,
) -> None:
    """Pool `solo` bắn `worker_process_init` trong **tiến trình gọi** và không bắn tín hiệu tắt.

    Nên `messaging_env` phải để `METRICS_PORT=0` (NO-159): không thế thì từ test này trở đi
    tiến trình pytest giữ một exporter thật sống, làm đỏ teardown của
    `packages/observability/tests/test_exporter.py` (review DEBT-01 finding 1).
    """
    with celery_worker_factory([DEFAULT_QUEUE]):
        pass

    assert exporter_module._instance is None


@pytest.mark.parametrize(("index", "expected"), [(None, 9464), (0, 9464), (1, 9465), (3, 9467)])
def test_metrics_port_for_process_gives_each_prefork_child_its_own_port(
    index: int | None, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mỗi con prefork một cổng, con đầu và tiến trình chính giữ cổng gốc (finding 3).

    Hai con bind cùng cổng thì con sau chỉ log `metrics_exporter_unavailable`, nên
    `/metrics` của container `worker` báo số của một con ngẫu nhiên. `None` là tiến trình
    chính (pool `solo`, beat): billiard không gắn `index` ở đó.
    """
    monkeypatch.setattr(celery_app_module, "current_process_index", lambda **_: index)

    assert metrics_port_for_process(9464) == expected


def test_the_metrics_exporter_stays_off_when_the_port_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """`METRICS_PORT=0` (mặc định của test, NO-159) không được mở cổng nào."""
    monkeypatch.setenv("METRICS_PORT", "0")
    reset_observability_settings_cache()
    try:
        start_metrics_exporter()
        assert exporter_module._instance is None
    finally:
        reset_observability_settings_cache()


def test_a_busy_metrics_port_is_only_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Cổng đã có người nghe không được làm hỏng tiến trình worker: không có metric vẫn chạy việc."""
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        monkeypatch.setenv("METRICS_PORT", str(taken.getsockname()[1]))
        monkeypatch.setenv("METRICS_HOST", "127.0.0.1")
        reset_observability_settings_cache()
        try:
            with caplog.at_level(logging.WARNING):
                start_metrics_exporter()
        finally:
            _stop_exporters()
            reset_observability_settings_cache()

    assert "metrics_exporter_unavailable" in caplog.text
    assert exporter_module._instance is None


def test_register_tasks__module_not_imported_before() -> None:
    """Tiến trình mới chưa nhập `jobs` nào: `register_tasks` vẫn đưa task mail của B1-03 vào sổ (NO-211)."""
    probe = (
        "import sys\n"
        "from celery import Celery\n"
        "from packages.testing.fixtures.messaging import register_tasks\n"
        "assert 'apps.api.auth_recovery.jobs' not in sys.modules\n"
        "print('default.auth_recovery.send_token_mail' in register_tasks(Celery('probe')).tasks)\n"
    )
    result = subprocess.run(  # noqa: S603 — lệnh cố định: python của môi trường + đoạn mã trong test
        [sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == "True"
