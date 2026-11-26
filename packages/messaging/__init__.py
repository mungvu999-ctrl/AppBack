"""packages.messaging — task Celery, EventBus, khoá Redis an toàn.

Mặt công khai của gói gom ở đây vì hai bên gọi nó theo hai cách: mã nghiệp vụ nhập
`packages.messaging.<module>`, còn `tools/case_gate.py` đọc **thuộc tính của gói**
`packages.messaging.registered_tasks` để dựng sổ task (cổng case).
"""

from packages.messaging.celery_app import (
    QUEUES,
    create_celery,
    producer_app,
    queue_for,
    reset_producer_app,
    send_task,
)
from packages.messaging.locks import LockBusy, LockLost, SafeLock
from packages.messaging.redis import (
    assert_broker_policy,
    broker_redis,
    broker_redis_sync,
    cache_redis,
    cache_redis_sync,
    safe_redis,
    safe_redis_sync,
    streams_redis,
    streams_redis_sync,
    translate_redis_error,
)
from packages.messaging.schedules import beat_schedule, discover_jobs, periodic
from packages.messaging.streams import Event, EventBus, SyncEventBus, is_event_id, upload_stream, user_stream
from packages.messaging.tasks import (
    PermanentError,
    TaskPayload,
    TransientError,
    define_task,
    registered_tasks,
    runner,
    task_entries,
)

__all__ = [
    "QUEUES",
    "Event",
    "EventBus",
    "LockBusy",
    "LockLost",
    "PermanentError",
    "SafeLock",
    "SyncEventBus",
    "TaskPayload",
    "TransientError",
    "assert_broker_policy",
    "beat_schedule",
    "broker_redis",
    "broker_redis_sync",
    "cache_redis",
    "cache_redis_sync",
    "create_celery",
    "define_task",
    "discover_jobs",
    "is_event_id",
    "periodic",
    "producer_app",
    "queue_for",
    "registered_tasks",
    "reset_producer_app",
    "runner",
    "safe_redis",
    "safe_redis_sync",
    "send_task",
    "streams_redis",
    "streams_redis_sync",
    "task_entries",
    "translate_redis_error",
    "upload_stream",
    "user_stream",
]
