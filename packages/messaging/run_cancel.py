"""Cờ huỷ hợp tác của một lượt pipeline (K-24): API cắm cờ, worker `ml` dừng ở điểm kiểm gần nhất.

Vì sao không `revoke`/`terminate` của Celery:

- `revoke` chỉ là danh sách **trong RAM** của từng worker (mất khi worker khởi động lại, cần
  pidbox, cần biết id task — `send_task` không giữ id), và không chạm task đang chạy.
- `terminate=True` giết tiến trình con prefork giữa lời gọi ONNX: mất cache model/trang của
  tiến trình, `define_task` thấy `WORKER_LOST` rồi báo hỏng — tức một lượt hỏng giả sinh ra
  từ một lượt đã huỷ.

Thay vào đó API ghi `pipeline:cancelled:{run_id}` vào DB `safe` của Redis (broker
`noeviction`, worker `ml` đã có URL) **sau commit**; `run_step` kiểm cờ trước khi nạp model,
trước khi đọc trang, giữa từng ô ảnh của bộ tách tường/dò đồ vật (qua `checkpoint()`), và trước
khi ghi artifact. Task còn trong hàng đợi gặp cờ ở điểm kiểm đầu tiên nên thoát ngay — tương
đương `revoke` mà sống qua cả lần khởi động lại worker.

Cờ chỉ là **tối ưu tài nguyên**: nguồn sự thật vẫn là `pipeline_runs` (lượt `failed`
`PIPELINE_CANCELLED`), nên Redis hỏng thì việc chạy nốt rồi bị `record_step` bỏ — đúng bất biến
"kết quả muộn bị bỏ". Vì vậy mọi lỗi đọc/ghi cờ đều **mở** (coi như chưa huỷ) và chỉ log.
"""

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

from packages.messaging.redis import ProcessLocal, SyncRedis, safe_redis_sync

__all__ = [
    "CANCEL_TTL_S",
    "CHECK_INTERVAL_S",
    "CancelProbe",
    "RunCancelledError",
    "cancel_key",
    "cancel_scope",
    "checkpoint",
    "is_run_cancelled",
    "mark_runs_cancelled",
    "reset_cancel_client",
]

_log: Final = logging.getLogger(__name__)

CANCEL_TTL_S: Final = 6 * 3600
"""Cờ sống 6 giờ: dài hơn mọi lượt chạy (trần quét bù tính bằng phút) cộng hàng đợi dồn sau sự cố."""

CHECK_INTERVAL_S: Final = 0.5
"""Hai lượt hỏi Redis trong vòng lặp ô ảnh cách nhau ít nhất chừng này (một `GET` mỗi nửa giây)."""

ERROR_BACKOFF_S: Final = 30.0
"""Đọc cờ lỗi (Redis chết, tiến trình không có URL) thì thôi hỏi chừng này giây — không làm chậm ML."""

_CLIENT: Final = ProcessLocal[SyncRedis](safe_redis_sync)


class RunCancelledError(Exception):
    """Lượt đã bị huỷ: thân task dừng ở điểm kiểm, không ghi artifact, không báo `step_done`."""


def cancel_key(run_id: str) -> str:
    """Khoá Redis của cờ huỷ một lượt chạy."""
    return f"pipeline:cancelled:{run_id}"


def reset_cancel_client() -> None:
    """Chỉ cho test: quên client đã dựng, lần sau đọc lại `REDIS_BROKER_URL`."""
    _CLIENT.reset()


def mark_runs_cancelled(run_ids: Iterable[str]) -> None:
    """Cắm cờ huỷ cho từng lượt (gọi **sau commit**); Redis hỏng → log cảnh báo, không ném."""
    ids = tuple(run_ids)
    if not ids:
        return
    try:
        pipe = _CLIENT.get().pipeline(transaction=False)
        for run_id in ids:
            pipe.set(cancel_key(run_id), "1", ex=CANCEL_TTL_S)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — cờ chỉ là tối ưu, DB đã huỷ lượt
        _log.warning("pipeline_cancel_flag_unwritten", extra={"run_ids": ids, "reason": type(exc).__name__})


def is_run_cancelled(run_id: str) -> bool:
    """Cờ huỷ của lượt có trong Redis không; lỗi nổi lên để `CancelProbe` lùi lại."""
    return bool(_CLIENT.get().exists(cancel_key(run_id)))


class CancelProbe:
    """Hỏi cờ huỷ của **một** lượt, thưa theo `interval_s`; lỗi đọc thì mở và thôi hỏi `ERROR_BACKOFF_S`."""

    def __init__(
        self,
        run_id: str,
        *,
        read: Callable[[str], bool] = is_run_cancelled,
        interval_s: float = CHECK_INTERVAL_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """`read` thay được trong test; `monotonic` để test không phải chờ thật."""
        self.run_id = run_id
        self._read = read
        self._interval_s = interval_s
        self._monotonic = monotonic
        self._next_at = float("-inf")

    def check(self, *, force: bool = False) -> None:
        """Ném `RunCancelledError` khi cờ đã cắm; `force` bỏ qua nhịp thưa (ranh giới giữa các pha)."""
        now = self._monotonic()
        if not force and now < self._next_at:
            return
        try:
            cancelled = self._read(self.run_id)
        except Exception as exc:  # noqa: BLE001 — cờ chỉ là tối ưu, mở khi không đọc được
            reason = type(exc).__name__
            _log.warning("pipeline_cancel_probe_unavailable", extra={"run_id": self.run_id, "reason": reason})
            self._next_at = now + ERROR_BACKOFF_S
            return
        self._next_at = now + self._interval_s
        if cancelled:
            raise RunCancelledError(self.run_id)


_ACTIVE: Final[ContextVar[CancelProbe | None]] = ContextVar("pipeline_cancel_probe", default=None)


@contextmanager
def cancel_scope(probe: CancelProbe) -> Iterator[CancelProbe]:
    """Đặt `probe` cho `checkpoint()` trong khối; `asyncio.to_thread` chép ngữ cảnh nên luồng phụ cũng thấy."""
    token = _ACTIVE.set(probe)
    try:
        yield probe
    finally:
        _ACTIVE.reset(token)


def checkpoint() -> None:
    """Điểm kiểm trong vòng lặp nặng (từng ô ảnh); ngoài `cancel_scope` thì không làm gì (eval, bench)."""
    probe = _ACTIVE.get()
    if probe is not None:
        probe.check()
