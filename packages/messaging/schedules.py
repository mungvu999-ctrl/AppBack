"""Sổ lịch nền cho Celery beat (BE-00 §7 "Dọn rác").

Mỗi chủ module tự đăng ký việc dọn rác của mình bằng `@periodic`; beat của
`apps/worker` đọc `beat_schedule()`. Hàm lịch **không tham số** và mang tên duy
nhất toàn repo, vì cổng case gắn `test_<tên hàm>__J01` vào đúng một hàm.

Dò module bằng `pkgutil` một cấp thay vì bảng khai tay: thêm module mới không phải
sửa file của gói này.
"""

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from celery import shared_task

from packages.messaging.celery_app import queue_for
from packages.messaging.tasks import register_task, run_maybe_async

MIN_PERIOD_S: Final = 60
_SCHEDULES: Final[dict[str, "ScheduleEntry"]] = {}


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """Một dòng lịch: tên task, chu kỳ, và tên hàm để cổng case truy ra test."""

    name: str
    every: timedelta
    function: str


def periodic(name: str, every: timedelta) -> Callable[[Callable[[], Any]], Any]:
    """Khai một việc chạy theo chu kỳ.

    Chu kỳ dưới `MIN_PERIOD_S` bị từ chối: beat gửi lại trước khi lượt trước kịp
    xong sẽ chất đống trên hàng. Trùng tên bị từ chối ngay lúc nhập module, vì hai
    lịch cùng tên thì beat chỉ chạy một cái mà không ai biết.
    """
    if every.total_seconds() < MIN_PERIOD_S:
        raise ValueError(f"chu kỳ phải ≥ {MIN_PERIOD_S} giây, nhận {every.total_seconds()}")

    def decorator(fn: Callable[[], Any]) -> Any:
        """Đăng ký `fn` thành task lịch tên `name` trên hàng suy từ tên, ghi vào sổ lịch."""
        register_task(name, fn)
        _SCHEDULES[name] = ScheduleEntry(name=name, every=every, function=fn.__name__)

        def body() -> None:
            """Thân task Celery: chạy hàm lịch, kể cả hàm `async` trên vòng sự kiện dùng chung."""
            run_maybe_async(fn)

        body.__name__ = fn.__name__
        body.__doc__ = fn.__doc__
        return shared_task(name=name, queue=queue_for(name))(body)

    return decorator


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Lịch cho `app.conf.beat_schedule`, khoá là tên task."""
    return {
        entry.name: {
            "task": entry.name,
            "schedule": entry.every.total_seconds(),
            "options": {"queue": queue_for(entry.name)},
        }
        for entry in schedule_entries()
    }


def schedule_entries() -> list[ScheduleEntry]:
    """Bản sao sổ lịch, sắp theo tên — để test và công cụ soi mà không sửa được sổ."""
    return sorted(_SCHEDULES.values(), key=lambda entry: entry.name)


def discover_submodules(package: str, submodule: str) -> None:
    """Nhập `<package>.<mỗi gói con một cấp>.<submodule>` theo thứ tự tên.

    Gói cha chưa có, hay gói con không có `<submodule>` → bỏ qua. Nhưng module có
    thật mà **nhập lỗi** thì ném: im lặng ở đây nghĩa là một task biến mất khỏi sổ
    và cổng case không hề biết.
    """
    try:
        root = importlib.import_module(package)
    except ModuleNotFoundError:
        return
    for info in sorted(pkgutil.iter_modules(root.__path__), key=lambda module: module.name):
        if not info.ispkg:
            continue
        name = f"{package}.{info.name}.{submodule}"
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise


def discover_jobs() -> None:
    """Nhập mọi `apps.api.*.jobs` và `apps.worker.*.jobs` để sổ lịch đầy đủ."""
    discover_submodules("apps.api", "jobs")
    discover_submodules("apps.worker", "jobs")


def discover_worker_tasks() -> None:
    """Nhập mọi `apps.worker.*.tasks` rồi mọi `*.jobs`: sổ task đầy đủ như worker thật.

    Một chỗ duy nhất cho `apps/worker/celery_main.py` và fixture test, để hai nơi không lệch nhau.
    """
    discover_submodules("apps.worker", "tasks")
    discover_jobs()
