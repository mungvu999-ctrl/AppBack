"""Khai báo và chạy task Celery (BE-00 §7, cổng case).

`define_task` bọc một hàm thường thành task Celery và **quyết định thay module chủ**
cái gì là thử lại được: lỗi tạm (`TransientError`, `DEPENDENCY_UNAVAILABLE`) thì
lùi theo `TASK_RETRY_BACKOFF_S`, lỗi vĩnh viễn thì gọi `on_failed` một lần, thông
điệp sai schema thì loại và ack (J08). Nhờ vậy mọi module có cùng một cách hỏng.

**Gói này chỉ bảo đảm thông điệp tới ít nhất một lần.** Tính idempotent của task là
việc của module chủ: khoá nghiệp vụ duy nhất và kiểm "đã làm chưa" trong cùng giao
dịch (J06, K18). Giao lặp vì worker chết quá `MAX_DELIVERIES` lần → `WORKER_LOST`
và ack, để một thông điệp độc không quay vòng mãi.
"""

import asyncio
import inspect
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_process_init, worker_process_shutdown
from pydantic import BaseModel, ConfigDict, ValidationError

from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.messaging.celery_app import queue_for
from packages.messaging.redis import ProcessLocal, SyncRedis, redis_errors, safe_redis_sync, sync_result
from packages.messaging.settings import get_messaging_settings

_log: Final = logging.getLogger(__name__)

RETRY_EXHAUSTED: Final = "RETRY_EXHAUSTED"
TASK_TIMEOUT: Final = "TASK_TIMEOUT"
WORKER_LOST: Final = "WORKER_LOST"
INTERNAL: Final = "INTERNAL"

MAX_DELIVERIES: Final = 3
DELIVERY_TTL_S: Final = 86_400
ERROR_TEXT_LIMIT: Final = 200
_CODE_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
# `INCR` và `EXPIRE` trong **một** lệnh: hai lệnh rời thì đứt kết nối ở giữa là bộ đếm sống
# mãi, và một `task_id` dùng lại bị gán nhầm `WORKER_LOST` sớm (NO-023). Cùng cách `publish_once`.
_COUNT_DELIVERY: Final = """
local count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return count
"""

_delivery_client: Final = ProcessLocal[SyncRedis](safe_redis_sync)
_runner: Final = ProcessLocal[asyncio.Runner](asyncio.Runner)
# tên task → dòng sổ, cho `registered_tasks()` và cổng case
_TASKS: Final[dict[str, "TaskEntry"]] = {}

type OnFailed = Callable[[Any, str], None]


class TaskPayload(BaseModel):
    """Gốc của mọi payload task: chỉ JSON, khoá lạ bị từ chối, có số hiệu schema.

    Lớp con **phải** khai `schema_version: int = <n>`; đổi nghĩa một trường thì tăng
    số, thông điệp cũ còn trên hàng sẽ bị loại như thông điệp độc thay vì hiểu sai.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int


class TransientError(Exception):
    """Lỗi thử lại được: phụ thuộc bận, tranh chấp khoá, dữ liệu chưa sẵn sàng."""


class PermanentError(Exception):
    """Lỗi không thử lại: `code` được ghi vào trạng thái hỏng của module chủ (J03)."""

    def __init__(self, code: str) -> None:
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"mã lỗi sai mẫu ^[A-Z][A-Z0-9_]{{2,63}}$: {code!r}")
        super().__init__(code)
        self.code: Final = code


@dataclass(frozen=True, slots=True)
class TaskEntry:
    """Một dòng sổ task: tên task, tên hàm, và module đã khai nó."""

    name: str
    function: str
    module: str


@dataclass(frozen=True, slots=True)
class _TaskSpec:
    """Mọi thứ thân task cần để xử lý một thông điệp, gom lại để khỏi truyền 6 tham số."""

    name: str
    payload: type[BaseModel]
    version: int
    on_failed: OnFailed
    backoff: tuple[int, ...] | None
    fn: Callable[[Any], Any]


def runner() -> asyncio.Runner:
    """`asyncio.Runner` dùng chung của tiến trình, cho task khai bằng `async def`.

    Một vòng sự kiện cho cả tiến trình, không `asyncio.run` mỗi task: engine asyncpg
    và client Redis async gắn với vòng sự kiện đã tạo ra chúng (BE-00 §7).
    """
    return _runner.get()


@worker_process_init.connect
def _open_runner(**_: object) -> None:
    """Dựng sẵn vòng sự kiện ngay khi tiến trình con của worker khởi động."""
    runner()


def reset_runner() -> None:
    """Đóng vòng sự kiện của tiến trình hiện tại (nếu có) rồi quên nó; `runner()` sau dựng vòng mới.

    `Runner.close()` huỷ task còn treo và tắt executor mặc định, để engine async đóng đàng
    hoàng thay vì bị bỏ lại lúc tiến trình thoát (NO-024). Test gọi để về trạng thái sạch.
    """
    current = _runner.reset()
    if current is not None:
        current.close()


@worker_process_shutdown.connect
def _close_runner(**_: object) -> None:
    """Tiến trình con của worker tắt: đóng vòng sự kiện đã mở ở `worker_process_init`."""
    reset_runner()


def register_task(task_name: str, fn: Callable[..., Any]) -> None:
    """Ghi vào sổ task; trùng tên task là lỗi khai báo, phát hiện ngay lúc nhập."""
    existing = _TASKS.get(task_name)
    if existing is not None:
        raise ValueError(f"task đã khai: {task_name} (hàm {existing.function})")
    _TASKS[task_name] = TaskEntry(name=task_name, function=fn.__name__, module=fn.__module__)


def task_entries() -> list[TaskEntry]:
    """Toàn bộ sổ task, kể cả task mẫu của test — để test và công cụ soi."""
    return sorted(_TASKS.values(), key=lambda entry: entry.name)


def _declared_in_tests(module: str) -> bool:
    """Task khai trong một module test (`packages.x.tests.test_y`)."""
    return ".tests." in module


def registered_tasks() -> list[str]:
    """Tên **hàm** của mọi task và lịch của **mã sản phẩm**, sau khi dò một cấp các gói chủ.

    Trả tên hàm (không phải tên task) vì `tools/case_gate.py` đối chiếu chúng với
    `test_<tên hàm>__J01`. Chỉ `case_gate` và test gọi hàm này: nó nhập
    `apps.ml`, thứ mà tiến trình worker không có.

    Task mẫu khai trong module test bị loại: sổ là **đầu vào của cổng**, nên nó chỉ
    được phụ thuộc mã sản phẩm, không phụ thuộc việc lượt chạy đã nhập test nào.
    """
    # Nhập tại chỗ: `schedules` nhập module này lúc khởi động, vòng nhập ở mức module
    # sẽ hỏng.
    from packages.messaging.schedules import discover_jobs, discover_submodules

    discover_jobs()
    discover_submodules("apps.worker", "tasks")
    discover_submodules("apps.ml", "tasks")
    return sorted(entry.function for entry in _TASKS.values() if not _declared_in_tests(entry.module))


def _schema_version_of(payload: type[BaseModel]) -> int:
    """Số hiệu schema mà task này chấp nhận, lấy từ mặc định của trường `schema_version`."""
    field = payload.model_fields.get("schema_version")
    if field is None:
        raise TypeError(f"{payload.__name__} thiếu trường schema_version")
    default = field.get_default()
    if not isinstance(default, int) or isinstance(default, bool):
        raise TypeError(f"{payload.__name__}.schema_version phải có mặc định là int, nhận {default!r}")
    return default


def task_id_of(task: Any) -> str:
    """Id của lượt giao hiện tại — mọi dòng log hỏng phải mang nó để truy ra thông điệp."""
    return str(task.request.id)


def _parse(spec: _TaskSpec, task_id: str, raw: object) -> BaseModel | None:
    """Giải payload; thông điệp độc → log gọn (không in thân) rồi `None` để ack (J08)."""
    reason = ""
    parsed: BaseModel | None = None
    if not isinstance(raw, dict):
        reason = f"args[0] phải là dict, nhận {type(raw).__name__}"
    else:
        try:
            parsed = spec.payload.model_validate(raw)
        except ValidationError as exc:
            reason = f"{exc.error_count()} trường sai schema"
    # `getattr`: payload là `type[BaseModel]` bất kỳ; trường đã được `_schema_version_of`
    # bắt buộc phải có ngay lúc khai task.
    version = None if parsed is None else getattr(parsed, "schema_version", None)
    if parsed is not None and version != spec.version:
        reason = f"schema_version {version!r} ≠ {spec.version}"
        parsed = None
    if parsed is None:
        _log.warning("poison_message", extra={"task": spec.name, "task_id": task_id, "reason": reason})
    return parsed


def run_maybe_async(call: Callable[[], Any]) -> None:
    """Gọi một hàm task hay hàm lịch; bản `async` chạy trên vòng sự kiện của tiến trình.

    Dùng chung cho `define_task` và `periodic` — cả hai đều nhận hàm `async`.
    """
    result = call()
    if inspect.isawaitable(result):
        runner().run(_await(result))


async def _await(awaitable: Any) -> None:
    """Bọc một awaitable thành coroutine — `Runner.run` chỉ nhận coroutine."""
    await awaitable


def reset_delivery_client() -> None:
    """Chỉ cho test và CLI: quên client đếm lượt giao (URL Redis đổi giữa các lượt test)."""
    _delivery_client.reset()


def _deliveries(task: Any) -> int:
    """Số lượt giao **không** do `self.retry` sinh ra, đếm theo `task_id` trong DB an toàn.

    Mỗi lượt `retry` cũng là một lượt giao của cùng `task_id`, nên phải trừ đi, không
    thì một task thử lại đủ 3 lần sẽ bị gán nhầm `WORKER_LOST` thay vì
    `RETRY_EXHAUSTED`.

    Bọc `redis_errors()`: DB an toàn hỏng thì đây là **lỗi tạm**, phải thử lại. Không
    bọc thì lỗi Redis rơi xuống nhánh “ngoại lệ lạ” và task bị ack mất.
    """
    key = f"delivery:{task_id_of(task)}"
    with redis_errors():
        # `eval` chứ không `register_script`: luôn đúng một lượt đi-về, kể cả lần đầu (không NOSCRIPT).
        count = sync_result(_delivery_client.get().eval(_COUNT_DELIVERY, 1, key, str(DELIVERY_TTL_S)), int)
    return count - int(task.request.retries or 0)


def _fail(spec: _TaskSpec, task_id: str, payload: BaseModel, code: str) -> None:
    """Báo hỏng cho module chủ; `on_failed` tự ném thì log và nuốt, worker phải sống tiếp.

    Log chỉ mang tên lớp + `ERROR_TEXT_LIMIT` ký tự đầu, **không** stack: thông điệp của lỗi
    module khác có thể chứa dữ liệu người dùng, mà stack in lại nguyên thông điệp (NO-030).
    Module chủ muốn chi tiết thì tự log trong `on_failed` của mình.
    """
    try:
        spec.on_failed(payload, code)
    except Exception as exc:  # noqa: BLE001 — on_failed là mã của module khác; hỏng thì log, không ném lại
        brief = f"{type(exc).__name__}: {str(exc)[:ERROR_TEXT_LIMIT]}"
        _log.error(
            "on_failed_error",
            extra={"task": spec.name, "task_id": task_id, "failure": code, "error": brief},
        )


def backoff_step(retries: int, backoff: Sequence[int]) -> int | None:
    """Số giây chờ trước lượt thử lại kế tiếp, hay `None` khi đã hết bậc (J02)."""
    return backoff[retries] if retries < len(backoff) else None


def _retry_or_exhaust(spec: _TaskSpec, task: Any, payload: BaseModel) -> None:
    """Lùi theo bậc rồi thử lại; hết bậc → `RETRY_EXHAUSTED` (J02).

    Đọc `TASK_RETRY_BACKOFF_S` **lúc retry**, không lúc khai task, để đổi cấu hình
    không phải nhập lại module.
    """
    backoff = get_messaging_settings().task_retry_backoff_s if spec.backoff is None else spec.backoff
    countdown = backoff_step(int(task.request.retries or 0), backoff)
    if countdown is None:
        _fail(spec, task_id_of(task), payload, RETRY_EXHAUSTED)
        return
    raise task.retry(countdown=countdown, max_retries=len(backoff))


def _handle(spec: _TaskSpec, task: Any, raw: object) -> None:
    """Một lượt chạy task: giải payload, lọc giao lặp, gọi thân, phân loại lỗi."""
    task_id = task_id_of(task)
    payload = _parse(spec, task_id, raw)
    if payload is None:
        return
    try:
        if _deliveries(task) > MAX_DELIVERIES:
            _fail(spec, task_id, payload, WORKER_LOST)
            return
        run_maybe_async(lambda: spec.fn(payload))
    except TransientError:
        _retry_or_exhaust(spec, task, payload)
    except AppError as exc:
        if exc.code is DEPENDENCY_UNAVAILABLE:
            _retry_or_exhaust(spec, task, payload)
        else:
            _log.exception("task_failed", extra={"task": spec.name, "task_id": task_id, "error": exc.code.code})
            _fail(spec, task_id, payload, INTERNAL)
    except PermanentError as exc:
        _fail(spec, task_id, payload, exc.code)
    except SoftTimeLimitExceeded:
        _fail(spec, task_id, payload, TASK_TIMEOUT)
    except Exception:  # noqa: BLE001 — lỗi lạ của thân task: log kèm stack rồi đánh hỏng, không thử lại
        _log.exception("task_failed", extra={"task": spec.name, "task_id": task_id, "error": INTERNAL})
        _fail(spec, task_id, payload, INTERNAL)


def define_task(
    *,
    name: str,
    payload: type[BaseModel],
    on_failed: OnFailed,
    backoff: tuple[int, ...] | None = None,
    acks_late: bool = True,
) -> Callable[[Callable[[Any], Any]], Any]:
    """Khai một task Celery từ một hàm `fn(payload) -> None`.

    `on_failed(payload, code)` là **bắt buộc**: chỉ module chủ biết ghi trạng thái
    hỏng ở đâu. `acks_late=False` chỉ dành cho task chỉ khởi chạy tiến trình rồi ack
    (huấn luyện, BE-00 §7). Không nhận app Celery: `apps/api/*/jobs.py` khai được
    task mà không nhập mã worker.
    """
    version = _schema_version_of(payload)
    queue = queue_for(name)

    def decorator(fn: Callable[[Any], Any]) -> Any:
        spec = _TaskSpec(name=name, payload=payload, version=version, on_failed=on_failed, backoff=backoff, fn=fn)

        def body(task: Any, raw: object) -> None:
            _handle(spec, task, raw)

        body.__name__ = fn.__name__
        body.__doc__ = fn.__doc__
        register_task(name, fn)
        return shared_task(bind=True, name=name, queue=queue, acks_late=acks_late)(body)

    return decorator
