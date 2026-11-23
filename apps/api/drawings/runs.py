"""Lượt chạy pipeline: nơi **duy nhất** (cùng lõi quét bù của `jobs.py`) ghi `pipeline_runs`.

Hai luật giữ cả module đứng vững:

- **Thứ tự khoá** (BE-00 §7): `floors` → `uploads`/`pipeline_runs`. Mọi hàm ghi ở đây mở
  bằng `_lock_floor`, kể cả khi chỉ đụng một dòng lượt chạy; `touch_project` là lời khoá
  **mới** cuối cùng.
- **Không tin worker** (BE-00 §7): lượt đã kết thúc, đã bị thay, hay bước lùi thì hàm trả
  `None` và **không ghi gì** — `apps/ml` không có cách nào ép một kết quả cũ vào DB.

Tác dụng ngoài (xếp task, phát `Progress`) luôn đi qua `on_after_commit` (K17, J09):
rollback thì hàng đợi và stream không thấy gì.

Module này là "hàm worker nhập" (BE-00 §7): không `fastapi`/`starlette`, trả `dict` khoá
dây chứ không `WireModel`.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Final
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.errors import FLOOR_DELETED
from apps.api.drawings.progress import FIRST_STEP, progress_wire
from apps.api.floors.settings import get_floors_settings
from apps.api.notifications.messages import ai_failed
from apps.api.notifications.service import notify
from apps.api.projects.memberships import is_member
from apps.api.projects.summaries import set_upload_state, touch_project
from apps.api.streams.lifecycle import finalize_upload_stream_sync
from packages.core.clock import Clock
from packages.core.ids import new_id
from packages.core.pipeline import PIPELINE_STEPS, PipelineCode
from packages.db.hooks import on_after_commit
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.db.models.floors import FloorRow
from packages.db.models.projects import Project
from packages.messaging.celery_app import send_task
from packages.messaging.payloads.drawings import PipelineStartPayload
from packages.messaging.redis import streams_redis_sync
from packages.messaging.run_cancel import mark_runs_cancelled
from packages.messaging.streams import SyncEventBus, upload_stream

START_TASK: Final = "pipeline.orchestrate.start"
"""Task điều phối pipeline; **khai** ở B5-06a, B2-04 chỉ gửi ([12])."""

STEP_STATUSES: Final = ("running", "completed", "failed")
"""`status` mà `record_step` nhận — trạng thái của **bước**, không phải của lượt chạy."""

FINAL_RUN_STATUSES: Final = ("completed", "failed")
LIVE_RUN_STATUSES: Final = ("pending", "running")
PROGRESS_DEDUPE_TTL_S: Final = 300
"""Khoá chống trùng của một khung `Progress` sống 5 phút: đủ cho lượt thử lại của redis-py (mili giây)."""

_STEPS: Final = tuple(step for step, _ in PIPELINE_STEPS)
_STEP_INDEX: Final = {step: index for index, step in enumerate(_STEPS)}
_WEIGHT_BEFORE: Final = tuple(sum(w for _, w in PIPELINE_STEPS[:index]) for index in range(len(PIPELINE_STEPS)))
_WEIGHT_THROUGH: Final = tuple(sum(w for _, w in PIPELINE_STEPS[: index + 1]) for index in range(len(PIPELINE_STEPS)))
_UPPER_SNAKE: Final = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_SILENT_FAILURES: Final = frozenset(
    {PipelineCode.PIPELINE_SUPERSEDED.value, PipelineCode.PIPELINE_CANCELLED.value, FLOOR_DELETED}
)
"""Lượt hỏng vì lượt mới thay, người dùng huỷ (K-24) hay tầng/dự án đã xoá: tự gây ra, không báo (FIX-P0-12)."""


@dataclass(frozen=True, slots=True)
class RunRow:
    """Ảnh chụp một lượt chạy cho người gọi ngoài module; đông cứng để không ai ghi qua nó."""

    id: str
    upload_id: str
    floor_pk: int
    status: str
    current_step: str
    superseded_by: str | None


@dataclass(frozen=True, slots=True)
class _FloorFacts:
    """Tầng đã khoá + trạng thái xoá mềm của nó và của dự án (BE-00 §7)."""

    pk: int
    project_id: str
    level_id: str
    deleted_at: datetime | None
    project_deleted_at: datetime | None


def _snapshot(row: PipelineRunRow) -> RunRow:
    """Bản chép bất biến của dòng lượt chạy, an toàn để dùng sau khi session đóng."""
    return RunRow(
        id=row.id,
        upload_id=row.upload_id,
        floor_pk=row.floor_pk,
        status=row.status,
        current_step=row.current_step,
        superseded_by=row.superseded_by,
    )


@cache
def _sync_bus() -> SyncEventBus:
    """Bus đồng bộ dựng **lười** một lần mỗi tiến trình: callback sau commit chạy ngoài vòng sự kiện.

    Dựng client Redis mới cho mỗi sự kiện `Progress` là một lượt bắt tay TCP cho mỗi bước
    pipeline của mỗi tầng; client đồng bộ của redis-py dùng pool nên chia được cho các luồng
    của executor sau commit.
    """
    return SyncEventBus(streams_redis_sync())


def reset_sync_bus_cache() -> None:
    """Chỉ cho test: đọc lại `REDIS_BROKER_URL` ở lần phát sau."""
    _sync_bus.cache_clear()


# ---------------------------------------------------------------------------
# Tác dụng ngoài sau commit
# ---------------------------------------------------------------------------


def _publish(upload_id: str, data: dict[str, object], dedupe_key: str) -> None:
    """XADD một khung `Progress` **đúng một lần**; trạng thái cuối thì hẹn giờ xoá stream (BE-00 §7).

    `publish_once` chứ không `publish`: lượt thử lại của redis-py sau `ConnectionError` ở pha đọc
    phản hồi (lệnh đã chạy ở máy chủ) gửi lại cùng `dedupe_key`, script trả id cũ thay vì ghi
    thêm một khung — FE không thấy sự kiện hai lần (NO-187).
    """
    bus = _sync_bus()
    bus.publish_once(upload_stream(upload_id), dedupe_key, data, ttl_s=PROGRESS_DEDUPE_TTL_S)
    if data.get("status") in FINAL_RUN_STATUSES:
        finalize_upload_stream_sync(bus, upload_id)


async def publish_progress_after_commit(db: AsyncSession, upload_id: str) -> None:
    """Chụp `Progress` **trong** giao dịch, phát **sau** commit (K17).

    Chụp sớm là cố ý: callback chạy ngoài vòng sự kiện, không còn session để đọc DB.
    """
    data = await progress_wire(db, upload_id)
    # Một khoá cho mỗi khung đã chụp: hai commit là hai khung, lượt thử lại của cùng khung trùng khoá.
    dedupe_key = f"progress:{upload_id}:{uuid4().hex}"
    on_after_commit(db, lambda: _publish(upload_id, data, dedupe_key))


def send_start_after_commit(db: AsyncSession, *, run_id: str, upload_id: str) -> None:
    """Xếp `pipeline.orchestrate.start` sau commit (J09); lõi quét bù gửi lại bằng chính hàm này."""
    payload = PipelineStartPayload(run_id=run_id, upload_id=upload_id)
    on_after_commit(db, lambda: send_task(START_TASK, payload))


# ---------------------------------------------------------------------------
# Khoá
# ---------------------------------------------------------------------------


async def _lock_floor(db: AsyncSession, floor_pk: int) -> _FloorFacts | None:
    """Khoá dòng `floors` rồi đọc `deleted_at` của dự án; không có tầng → `None`.

    Dự án chỉ **đọc**: khoá `projects` sau `floors` là chiều đúng, nhưng lượt ghi duy nhất
    lên nó (`touch_project`) phải là lời khoá mới cuối cùng nên ở đây không khoá.
    """
    stmt = (
        select(FloorRow.pk, FloorRow.project_id, FloorRow.level_id, FloorRow.deleted_at)
        .where(FloorRow.pk == floor_pk)
        .with_for_update()
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    project_deleted_at = (
        await db.execute(select(Project.deleted_at).where(Project.id == row.project_id))
    ).scalar_one_or_none()
    return _FloorFacts(
        pk=row.pk,
        project_id=row.project_id,
        level_id=row.level_id,
        deleted_at=row.deleted_at,
        project_deleted_at=project_deleted_at,
    )


async def _lock_run(db: AsyncSession, run_id: str) -> tuple[_FloorFacts, PipelineRunRow] | None:
    """Khoá tầng rồi lượt chạy, đúng thứ tự BE-00 §7; thiếu dòng nào → `None`."""
    floor_pk = (
        await db.execute(select(PipelineRunRow.floor_pk).where(PipelineRunRow.id == run_id))
    ).scalar_one_or_none()
    if floor_pk is None:
        return None
    floor = await _lock_floor(db, floor_pk)
    if floor is None:
        return None
    stmt = select(PipelineRunRow).where(PipelineRunRow.id == run_id).with_for_update()
    run = (await db.execute(stmt)).scalar_one_or_none()
    return None if run is None else (floor, run)


def _usable(run: PipelineRunRow) -> bool:
    """Lượt còn nhận kết quả: chưa kết thúc và chưa bị thay (BE-00 §7)."""
    return run.status not in FINAL_RUN_STATUSES and run.superseded_by is None


async def lock_run(db: AsyncSession, *, run_id: str) -> RunRow | None:
    """Khoá `floors` → lượt chạy và trả ảnh chụp; lượt đã kết thúc hay bị thay → `None`.

    Người gọi (B5-06a, B2-05b) giữ khoá tới hết giao dịch của mình, nên kết quả họ ghi tiếp
    không thể chen vào giữa một lượt tải lại.
    """
    locked = await _lock_run(db, run_id)
    if locked is None:
        return None
    _, run = locked
    return _snapshot(run) if _usable(run) else None


# ---------------------------------------------------------------------------
# Bắt đầu một lượt
# ---------------------------------------------------------------------------


async def start_run(db: AsyncSession, *, upload_id: str, clock: Clock) -> RunRow:
    """Mở lượt chạy mới cho một lượt tải đã `complete`, thay mọi lượt còn dở của tầng.

    Lượt cũ thành `failed` `PIPELINE_SUPERSEDED` với `superseded_by` = id lượt mới, nên mọi
    kết quả muộn của chúng bị `lock_run`/`record_step` từ chối. Không có dòng upload hay
    tầng → `ValueError` (lỗi lập trình: #7 đã tra cả hai dưới khoá).
    """
    floor_pk = (await db.execute(select(UploadRow.floor_pk).where(UploadRow.id == upload_id))).scalar_one_or_none()
    if floor_pk is None:
        raise ValueError(f"không có lượt tải {upload_id!r}")
    floor = await _lock_floor(db, floor_pk)
    if floor is None:
        raise ValueError(f"không có tầng pk={floor_pk} của lượt tải {upload_id!r}")

    run_id = new_id("run", clock)
    superseded = await _supersede_live_runs(db, floor_pk=floor.pk, run_id=run_id, clock=clock)
    row = PipelineRunRow(
        id=run_id,
        upload_id=upload_id,
        floor_pk=floor.pk,
        status="pending",
        current_step=FIRST_STEP,
        progress_percent=0,
    )
    db.add(row)
    await db.flush()

    await set_upload_state(
        db,
        project_id=floor.project_id,
        floor_level_id=floor.level_id,
        has_upload=True,
        pipeline_state="pending",
    )
    await touch_project(db, project_id=floor.project_id, clock=clock)
    send_start_after_commit(db, run_id=run_id, upload_id=upload_id)
    for affected in (upload_id, *sorted(superseded - {upload_id})):
        await publish_progress_after_commit(db, affected)
    return _snapshot(row)


async def _supersede_live_runs(db: AsyncSession, *, floor_pk: int, run_id: str, clock: Clock) -> set[str]:
    """Đánh hỏng mọi lượt `pending|running` của tầng; trả tập `upload_id` của chúng.

    Khoá theo `id` tăng dần để hai lượt `#7` song song trên cùng tầng không khoá chéo.
    """
    stmt = (
        select(PipelineRunRow.id, PipelineRunRow.upload_id)
        .where(PipelineRunRow.floor_pk == floor_pk, PipelineRunRow.status.in_(LIVE_RUN_STATUSES))
        .order_by(PipelineRunRow.id)
        .with_for_update()
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return set()
    await db.execute(
        update(PipelineRunRow)
        .where(PipelineRunRow.id.in_([row.id for row in rows]))
        .values(
            status="failed",
            error_code=PipelineCode.PIPELINE_SUPERSEDED.value,
            superseded_by=run_id,
            updated_at=clock.now(),
        )
    )
    return {row.upload_id for row in rows}


# ---------------------------------------------------------------------------
# Huỷ (K-24)
# ---------------------------------------------------------------------------


async def _floor_has_completed_run(db: AsyncSession, floor_pk: int) -> bool:
    """Tầng còn một lượt `completed` nào (của bất kỳ lượt tải) — tức vẫn có mô hình dùng được."""
    stmt = (
        select(PipelineRunRow.id)
        .where(PipelineRunRow.floor_pk == floor_pk, PipelineRunRow.status == "completed")
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def cancel_upload_runs(db: AsyncSession, *, upload_id: str, clock: Clock) -> tuple[str, ...]:
    """Huỷ mọi lượt `pending|running` của **một** lượt tải; trả id các lượt đã huỷ (rỗng = không có gì để huỷ).

    Cùng khoá với `start_run`/`record_step` (`floors` → `pipeline_runs`, id tăng dần), nên một
    `record_step` đang ghi dở xong trước rồi mới tới lượt huỷ, và mọi kết quả tới sau thấy lượt
    `failed` → `_usable` từ chối (bất biến "kết quả muộn bị bỏ"). Không đặt `superseded_by`: không
    có lượt nào thay. Dòng đếm của tầng về `completed` nếu tầng còn lượt xong trước đó (mô hình cũ
    vẫn dùng được), ngược lại `none` — tầng không bao giờ kẹt "đang xử lý" (`PIPELINE_STATES` không có
    "cancelled"). Sau commit: cắm cờ huỷ cho worker `ml` (`packages.messaging.run_cancel`) và phát
    `Progress` `failed` `PIPELINE_CANCELLED` (đóng stream). Không báo người tải (`_SILENT_FAILURES`).
    """
    floor_pk = (await db.execute(select(UploadRow.floor_pk).where(UploadRow.id == upload_id))).scalar_one_or_none()
    if floor_pk is None:
        return ()
    floor = await _lock_floor(db, floor_pk)
    if floor is None:
        return ()
    stmt = (
        select(PipelineRunRow.id)
        .where(PipelineRunRow.upload_id == upload_id, PipelineRunRow.status.in_(LIVE_RUN_STATUSES))
        .order_by(PipelineRunRow.id)
        .with_for_update()
    )
    run_ids = tuple((await db.execute(stmt)).scalars())
    if not run_ids:
        return ()
    await db.execute(
        update(PipelineRunRow)
        .where(PipelineRunRow.id.in_(run_ids))
        .values(status="failed", error_code=PipelineCode.PIPELINE_CANCELLED.value, updated_at=clock.now())
    )
    if not await _floor_was_recreated(db, floor):
        has_model = await _floor_has_completed_run(db, floor.pk)
        await set_upload_state(
            db,
            project_id=floor.project_id,
            floor_level_id=floor.level_id,
            has_upload=has_model,
            pipeline_state="completed" if has_model else "none",
        )
    await touch_project(db, project_id=floor.project_id, clock=clock)
    on_after_commit(db, lambda: mark_runs_cancelled(run_ids))
    await publish_progress_after_commit(db, upload_id)
    return run_ids


# ---------------------------------------------------------------------------
# Ghi một bước
# ---------------------------------------------------------------------------


def _check_args(step: str, status: str, error_code: str | None) -> None:
    """Đối số của `record_step`; sai → `ValueError` **trước** khi chạm DB (BE-00 §7)."""
    if step not in _STEP_INDEX:
        raise ValueError(f"bước lạ: {step!r}, phải thuộc {_STEPS}")
    if status not in STEP_STATUSES:
        raise ValueError(f"status lạ: {status!r}, phải thuộc {STEP_STATUSES}")
    if status == "failed" and (error_code is None or not _UPPER_SNAKE.fullmatch(error_code)):
        raise ValueError(f"bước hỏng phải kèm mã UPPER_SNAKE, nhận {error_code!r}")


def _apply(run: PipelineRunRow, *, step: str, status: str, error_code: str | None, clock: Clock) -> bool:
    """Chuyển trạng thái theo [6]; trả `True` khi có gì đó thật sự đổi.

    `completed` **không** tự đặt lượt sang `running` (đúng chữ [6]): chỉ `running` mở lượt.
    Phần trăm lấy `max` nên không bao giờ lùi, kể cả khi hai bước về không đúng thứ tự.
    """
    before = (run.status, run.current_step, run.progress_percent, run.error_code, run.started_at, run.ended_at)
    index = _STEP_INDEX[step]
    if status == "running":
        run.status = "running"
        run.started_at = run.started_at or clock.now()
        run.current_step = step
        run.progress_percent = max(run.progress_percent, _WEIGHT_BEFORE[index])
    elif status == "completed":
        run.progress_percent = max(run.progress_percent, _WEIGHT_THROUGH[index])
        if index == len(_STEPS) - 1:
            run.status = "completed"
            run.progress_percent = 100
            run.ended_at = clock.now()
            run.current_step = step
        else:
            run.current_step = _STEPS[index + 1]
    else:
        run.status = "failed"
        run.error_code = error_code
        run.current_step = step
    return (run.status, run.current_step, run.progress_percent, run.error_code, run.started_at, run.ended_at) != before


def restore_window_elapsed(deleted_at: datetime | None, clock: Clock) -> bool:
    """Tầng xoá mềm đã qua `FLOOR_RESTORE_WINDOW_S` chưa (BE-00 §7, A8): chưa xoá → `False`, đúng mốc → `True`.

    Luật duy nhất của cửa sổ khôi phục (nửa mở `[0, window)`: BE-00 §7 "còn trong" cửa sổ là còn, đúng
    mốc là hết): `_out_of_window`, `pipeline_persist`, `floors.create_floor` và `claim_entity_ids` cùng gọi.
    """
    if deleted_at is None:
        return False
    return (clock.now() - deleted_at).total_seconds() >= get_floors_settings().floor_restore_window_s


def _out_of_window(floor: _FloorFacts, clock: Clock) -> bool:
    """Tầng/dự án xoá mềm tới mức lượt chạy phải hỏng hẳn (BE-00 §7).

    Tầng vừa gỡ mà còn trong `FLOOR_RESTORE_WINDOW_S` được ghi **như tầng sống**: người dùng
    hoàn tác trong cửa sổ đó phải thấy đúng trạng thái pipeline lúc gỡ (A8).
    """
    return floor.project_deleted_at is not None or restore_window_elapsed(floor.deleted_at, clock)


async def _floor_was_recreated(db: AsyncSession, floor: _FloorFacts) -> bool:
    """Có tầng chưa xoá **khác** cùng `(project_id, level_id)` không — tức tầng đã được tạo lại.

    Dòng đếm dùng chung theo `level_id` (BE-00 §7), nên lượt chạy của tầng cũ **không** được
    ghi đè lên số của tầng mới. Loại chính `floor.pk` ra khỏi câu là phần làm cho luật có
    nghĩa: `uq_floors_level` chỉ cho một tầng sống mỗi `level_id`, nên hàng trả về (nếu có)
    luôn là tầng thay thế, không bao giờ là chính nó.
    """
    stmt = (
        select(FloorRow.pk)
        .where(
            FloorRow.project_id == floor.project_id,
            FloorRow.level_id == floor.level_id,
            FloorRow.pk != floor.pk,
            FloorRow.deleted_at.is_(None),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def _notify_failed(db: AsyncSession, run: PipelineRunRow, floor: _FloorFacts, clock: Clock) -> None:
    """Báo người tải lên rằng lượt hỏng (FIX-P0-12); người tải đã rời dự án → không báo ai.

    Một câu đọc người tải, tên dự án, tên tầng. `_settle` có thể chạy lại cho cùng lượt: `dedupe_key`
    theo lượt + người nhận nên `notify` không sinh dòng thứ hai.
    """
    stmt = (
        select(UploadRow.created_by, Project.name, FloorRow.name)
        .join(FloorRow, FloorRow.pk == UploadRow.floor_pk)
        .join(Project, Project.id == FloorRow.project_id)
        .where(UploadRow.id == run.upload_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None or not await is_member(db, floor.project_id, row.created_by):
        return
    uploader_id, project_name, floor_name = row
    await notify(
        db,
        user_id=uploader_id,
        kind="aiFailed",
        place="floors",
        project_id=floor.project_id,
        project_name=project_name,
        object_label=floor_name,
        message=ai_failed(project_name),
        dedupe_key=f"aiFailed:{run.id}:{uploader_id}",
        clock=clock,
        floor_level_id=floor.level_id,
    )


async def _settle(db: AsyncSession, run: PipelineRunRow, floor: _FloorFacts, clock: Clock) -> None:
    """Đồng bộ dòng đếm của tầng, báo người tải nếu lượt hỏng, rồi hẹn phát `Progress` sau commit (BE-00 §7)."""
    await db.flush()
    if run.status == "failed" and run.error_code not in _SILENT_FAILURES:
        await _notify_failed(db, run, floor, clock)
    if not await _floor_was_recreated(db, floor):
        await set_upload_state(
            db,
            project_id=floor.project_id,
            floor_level_id=floor.level_id,
            has_upload=True,
            pipeline_state=run.status,
        )
    await publish_progress_after_commit(db, run.upload_id)


async def _abandon(db: AsyncSession, run: PipelineRunRow, floor: _FloorFacts, error_code: str, clock: Clock) -> None:
    """Đánh hỏng một lượt vì lý do ngoài pipeline; không `ended_at` (K33)."""
    run.status = "failed"
    run.error_code = error_code
    run.updated_at = clock.now()
    await _settle(db, run, floor, clock)


async def fail_run(db: AsyncSession, *, run_id: str, error_code: str, clock: Clock) -> bool:
    """Đánh hỏng một lượt từ bên ngoài (lõi quét bù: `PIPELINE_STALLED`); trả `False` nếu không ghi.

    Cùng khoá, cùng luật dòng đếm với nhánh `FLOOR_DELETED` của `record_step` — lõi quét bù
    gọi hàm này thay vì tự `UPDATE pipeline_runs` ([9]).
    """
    if not _UPPER_SNAKE.fullmatch(error_code):
        raise ValueError(f"mã lỗi phải là UPPER_SNAKE, nhận {error_code!r}")
    locked = await _lock_run(db, run_id)
    if locked is None:
        return False
    floor, run = locked
    if not _usable(run):
        return False
    await _abandon(db, run, floor, error_code, clock)
    return True


async def record_step(
    db: AsyncSession,
    *,
    run_id: str,
    step: str,
    status: str,
    clock: Clock,
    error_code: str | None = None,
) -> dict[str, object] | None:
    """Ghi kết quả **một bước** vào lượt chạy; trả `Progress` mới hay `None` khi không ghi gì.

    `None` (không phải lỗi) cho mọi lượt giao muộn: lượt đã kết thúc, đã bị thay, bước lùi
    (giao lặp J06), tầng hay dự án xoá mềm quá cửa sổ. `ValueError` chỉ dành cho đối số sai —
    đó là lỗi của người gọi, không phải của thời điểm.
    """
    _check_args(step, status, error_code)
    locked = await _lock_run(db, run_id)
    if locked is None:
        return None
    floor, run = locked
    if not _usable(run) or _STEP_INDEX[step] < _STEP_INDEX[run.current_step]:
        return None
    if _out_of_window(floor, clock):
        await _abandon(db, run, floor, FLOOR_DELETED, clock)
        return None
    if not _apply(run, step=step, status=status, error_code=error_code, clock=clock):
        return None
    run.updated_at = clock.now()
    await _settle(db, run, floor, clock)
    return await progress_wire(db, run.upload_id)


__all__ = [
    "FINAL_RUN_STATUSES",
    "LIVE_RUN_STATUSES",
    "START_TASK",
    "STEP_STATUSES",
    "RunRow",
    "cancel_upload_runs",
    "fail_run",
    "lock_run",
    "publish_progress_after_commit",
    "record_step",
    "reset_sync_bus_cache",
    "restore_window_elapsed",
    "send_start_after_commit",
    "start_run",
]
