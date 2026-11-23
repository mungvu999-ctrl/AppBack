"""Cờ huỷ hợp tác (K-24): ghi/đọc trên Redis thật, nhịp thưa, mở khi lỗi, `checkpoint` qua luồng phụ."""

import asyncio
from collections.abc import Iterator

import pytest

from packages.messaging import safe_redis_sync
from packages.messaging.run_cancel import (
    CANCEL_TTL_S,
    ERROR_BACKOFF_S,
    CancelProbe,
    RunCancelledError,
    cancel_key,
    cancel_scope,
    checkpoint,
    is_run_cancelled,
    mark_runs_cancelled,
    reset_cancel_client,
)
from packages.messaging.settings import get_messaging_settings, reset_messaging_settings_cache


class Clock:
    """Đồng hồ đơn điệu giả: test đẩy giờ bằng tay."""

    def __init__(self) -> None:
        """Bắt đầu ở 0."""
        self.now = 0.0

    def __call__(self) -> float:
        """Giờ hiện tại."""
        return self.now


class Flag:
    """`read` giả: đếm số lần hỏi, trả `value` hay ném `error`."""

    def __init__(self, value: bool = False, error: Exception | None = None) -> None:
        """Mặc định chưa huỷ, không lỗi."""
        self.value = value
        self.error = error
        self.calls = 0

    def __call__(self, run_id: str) -> bool:
        """Một lần hỏi cờ."""
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.value


@pytest.fixture
def cancel_env(messaging_env: None) -> Iterator[None]:
    """Client của `run_cancel` dựng theo Redis của lượt test."""
    reset_cancel_client()
    yield
    reset_cancel_client()


def test_mark_then_read_on_real_redis(cancel_env: None) -> None:
    """Cờ cắm vào DB `safe`, có hạn sống; lượt khác không bị coi là đã huỷ."""
    mark_runs_cancelled(["run_a", "run_b"])
    client = safe_redis_sync(get_messaging_settings())
    try:
        assert is_run_cancelled("run_a")
        assert is_run_cancelled("run_b")
        assert not is_run_cancelled("run_c")
        assert 0 < client.ttl(cancel_key("run_a")) <= CANCEL_TTL_S
    finally:
        client.close()


def test_mark_with_nothing_or_without_redis_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Danh sách rỗng không chạm Redis; Redis không tới được → chỉ log, không ném (DB đã huỷ)."""
    mark_runs_cancelled([])
    monkeypatch.setenv("REDIS_BROKER_URL", "redis://127.0.0.1:1/0")
    reset_messaging_settings_cache()
    reset_cancel_client()
    try:
        mark_runs_cancelled(["run_x"])
    finally:
        reset_cancel_client()
        reset_messaging_settings_cache()
    assert any(record.getMessage() == "pipeline_cancel_flag_unwritten" for record in caplog.records)


def test_probe_is_throttled_but_force_always_asks() -> None:
    """Trong `interval_s` không hỏi lại; `force` (ranh giới pha) luôn hỏi; hết nhịp thì hỏi tiếp."""
    flag, clock = Flag(), Clock()
    probe = CancelProbe("run_1", read=flag, interval_s=0.5, monotonic=clock)
    probe.check()
    probe.check()
    assert flag.calls == 1
    probe.check(force=True)
    assert flag.calls == 2
    clock.now = 0.6
    flag.value = True
    with pytest.raises(RunCancelledError):
        probe.check()


def test_probe_fails_open_and_backs_off_on_errors(caplog: pytest.LogCaptureFixture) -> None:
    """Đọc cờ lỗi → coi như chưa huỷ, log một lần, thôi hỏi (nhịp thưa) trong `ERROR_BACKOFF_S` giây."""
    flag, clock = Flag(error=ConnectionError("down")), Clock()
    probe = CancelProbe("run_1", read=flag, monotonic=clock)
    probe.check()
    probe.check()
    assert flag.calls == 1
    assert [r.getMessage() for r in caplog.records].count("pipeline_cancel_probe_unavailable") == 1
    clock.now = ERROR_BACKOFF_S + 0.1
    flag.error, flag.value = None, True
    with pytest.raises(RunCancelledError):
        probe.check()


def test_checkpoint_is_a_no_op_outside_a_scope() -> None:
    """Bộ tách chạy ngoài pipeline (eval, bench) không có probe: `checkpoint` không làm gì."""
    checkpoint()


async def test_checkpoint_reaches_a_worker_thread() -> None:
    """`asyncio.to_thread` chép ngữ cảnh: vòng lặp ô ảnh ở luồng phụ thấy probe của task."""
    flag = Flag(value=True)
    with cancel_scope(CancelProbe("run_1", read=flag)), pytest.raises(RunCancelledError):
        await asyncio.to_thread(checkpoint)
    assert flag.calls == 1
    checkpoint()
