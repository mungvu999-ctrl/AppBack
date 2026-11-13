"""Ghi và gộp bảng đếm theo tầng `project_floor_summaries` (B2-01 [2], [6]).

Bảng này là **nguồn duy nhất** của `floorCount`, `areaM2`, `status`, `defaultFloorId` ở N1
và `status` ở #23-#27: `project_rollups` gộp bằng **một** `GROUP BY`, không đọc jsonb của
tầng (N+1). Người ghi là B2-03 (tầng), B2-04 (bản vẽ), B3-03 và B5-06a/b (tường, pipeline)
— nên module này không nhập `fastapi`, `starlette`, `jwt`, `argon2` (BE-00 §7).

Hai luật dễ quên:

- **dòng ẩn vẫn ghi được.** `unregister_floor` chỉ đặt `hidden=true` và giữ số, để khôi
  phục tầng không mất đếm; mọi hàm ghi vẫn nhận dòng ẩn, chỉ `project_rollups` bỏ.
- **không `commit`.** Người gọi ghi trong giao dịch của mình; thứ tự khoá BE-00 §7 đặt
  `project_floor_summaries` **trước** `projects`, nên `touch_project` luôn gọi sau cùng.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Final, cast

from sqlalchemy import ARRAY, ColumnElement, Text, and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.projects.wire import ProjectRollup, ProjectStatus, SummaryStatus
from packages.core.clock import Clock
from packages.db.models.floors import FloorRow
from packages.db.models.projects import PIPELINE_STATES, Project, ProjectFloorSummary
from packages.db.models.spatial import FloorDocumentRow

_RUNNING_STATES: Final = ("pending", "running")
"""Lượt pipeline còn dở — `legacy_status` xét nó **trước** `failed` (B2-01 [6])."""

_REAL_SCALE_SOURCES: Final = ("human", "measured", "pipeline")
"""Nguồn tỉ lệ cho diện tích tin được; `project_default` chỉ là số mặc định, `none` là chưa có (U07)."""

_EMPTY_ROLLUP: Final = ProjectRollup(
    floor_count=0,
    area_m2=Decimal(0),
    walls_total=0,
    walls_reviewed=0,
    status="processing",
    legacy_status="draft",
    default_floor_id=None,
    has_scale=True,
    job_active=False,
)
"""Dự án không có dòng đếm nào (chưa có tầng, hay mọi tầng đã ẩn)."""

_DEFAULTS: Final[dict[str, Any]] = {
    "walls_total": 0,
    "walls_reviewed": 0,
    "area_m2": None,
    "has_upload": False,
    "pipeline_state": "none",
    "hidden": False,
}
"""Giá trị của một tầng vừa đăng ký — cũng là giá trị `register_floor(restored=False)` ghi đè."""


def _row(project_id: str, floor_level_id: str) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """Điều kiện khoá chính của một dòng đếm (dùng chung cho mọi lệnh ghi một tầng)."""
    return (
        ProjectFloorSummary.project_id == project_id,
        ProjectFloorSummary.floor_level_id == floor_level_id,
    )


async def _write_one(db: AsyncSession, project_id: str, floor_level_id: str, values: Mapping[str, Any]) -> None:
    """Ghi một dòng đếm (kể cả dòng ẩn); không có dòng nào → `ValueError` theo số dòng `RETURNING`."""
    stmt = (
        update(ProjectFloorSummary)
        .where(*_row(project_id, floor_level_id))
        .values(**values, updated_at=func.now())
        .returning(ProjectFloorSummary.floor_level_id)
    )
    if (await db.execute(stmt)).first() is None:
        raise ValueError(f"dự án {project_id} không có dòng đếm của tầng {floor_level_id!r}")


async def register_floor(
    db: AsyncSession, *, project_id: str, floor_level_id: str, floor_order: int, restored: bool = False
) -> None:
    """Tạo (hay bỏ ẩn) dòng đếm của một tầng.

    `restored=False`: ghi **mặc định** (số về 0, chưa upload, `pipeline_state='none'`) —
    tầng mới, hay tầng cũ dựng lại từ đầu. `restored=True`: chỉ bỏ ẩn và đặt lại thứ tự,
    **giữ nguyên số** (khôi phục tầng đã gỡ); chưa có dòng thì chèn dòng mặc định.
    """
    updated: dict[str, Any] = {"floor_order": floor_order, "hidden": False, "updated_at": func.now()}
    if not restored:
        updated |= _DEFAULTS
    stmt = (
        pg_insert(ProjectFloorSummary)
        .values(project_id=project_id, floor_level_id=floor_level_id, floor_order=floor_order, **_DEFAULTS)
        .on_conflict_do_update(
            index_elements=[ProjectFloorSummary.project_id, ProjectFloorSummary.floor_level_id], set_=updated
        )
    )
    await db.execute(stmt)


async def unregister_floor(db: AsyncSession, *, project_id: str, floor_level_id: str) -> None:
    """Ẩn dòng đếm của một tầng vừa gỡ, **giữ số** để `register_floor(restored=True)` trả lại."""
    await _write_one(db, project_id, floor_level_id, {"hidden": True})


async def purge_floor(db: AsyncSession, *, project_id: str, floor_level_id: str) -> None:
    """Xoá hẳn dòng đếm (lịch dọn của B2-03); không có dòng cũng **không** lỗi."""
    await db.execute(delete(ProjectFloorSummary).where(*_row(project_id, floor_level_id)))


async def set_floor_orders(db: AsyncSession, *, project_id: str, orders: Mapping[str, int]) -> None:
    """Đặt lại `floor_order` của nhiều tầng bằng **một** lệnh; thiếu bất kỳ tầng nào → `ValueError`."""
    if not orders:
        return
    stmt = (
        update(ProjectFloorSummary)
        .where(
            ProjectFloorSummary.project_id == project_id,
            ProjectFloorSummary.floor_level_id.in_(list(orders)),
        )
        .values(floor_order=case(dict(orders), value=ProjectFloorSummary.floor_level_id), updated_at=func.now())
        .returning(ProjectFloorSummary.floor_level_id)
    )
    touched = (await db.execute(stmt)).scalars().all()
    if len(touched) != len(orders):
        raise ValueError(f"dự án {project_id} thiếu dòng đếm của tầng {sorted(set(orders) - set(touched))}")


async def set_upload_state(
    db: AsyncSession, *, project_id: str, floor_level_id: str, has_upload: bool, pipeline_state: str
) -> None:
    """Ghi trạng thái bản vẽ và lượt pipeline mới nhất của một tầng; `pipeline_state` lạ → `ValueError`."""
    if pipeline_state not in PIPELINE_STATES:
        raise ValueError(f"pipeline_state {pipeline_state!r} không thuộc {PIPELINE_STATES}")
    await _write_one(db, project_id, floor_level_id, {"has_upload": has_upload, "pipeline_state": pipeline_state})


async def set_layer_counts(
    db: AsyncSession,
    *,
    project_id: str,
    floor_level_id: str,
    walls_total: int,
    walls_reviewed: int,
    area_m2: Decimal | None,
) -> None:
    """Ghi số tường và diện tích của một tầng; số âm hay `walls_reviewed > walls_total` → `ValueError`.

    Kiểm **trước** khi ghi chứ không để `CHECK` của DB ném: `IntegrityError` làm hỏng cả
    giao dịch của người gọi, còn `ValueError` để họ trả 422 và làm tiếp việc khác.
    """
    if walls_total < 0 or walls_reviewed < 0 or (area_m2 is not None and area_m2 < 0):
        raise ValueError(f"số đếm âm: walls_total={walls_total}, walls_reviewed={walls_reviewed}, area_m2={area_m2}")
    if walls_reviewed > walls_total:
        raise ValueError(f"walls_reviewed={walls_reviewed} lớn hơn walls_total={walls_total}")
    await _write_one(
        db,
        project_id,
        floor_level_id,
        {"walls_total": walls_total, "walls_reviewed": walls_reviewed, "area_m2": area_m2},
    )


async def touch_projects(db: AsyncSession, *, project_ids: Sequence[str], clock: Clock) -> None:
    """Đẩy `projects.updated_at` của **nhiều** dự án bằng một câu; danh sách rỗng → không câu nào.

    Người gỡ một người khỏi mọi dự án của họ chạm hàng chục dự án một lúc; một `UPDATE` cho
    mỗi dự án là N vòng DB cho một việc mà `IN (...)` làm trọn trong một vòng.

    `UPDATE … WHERE id IN (...)` khoá theo thứ tự kế hoạch của Postgres, không theo `id` tăng
    dần — hai giao dịch gỡ người dùng cùng lúc, có ≥ 2 dự án chung, có thể khoá chéo (NO-142).
    `SELECT … ORDER BY id FOR NO KEY UPDATE` trước, với danh sách id đã sắp, ép mọi giao dịch xin
    khoá theo cùng một thứ tự nên không thể khoá chéo.

    `NO KEY UPDATE` chứ không `UPDATE`: mọi lượt ghi chèn dòng con trỏ `projects.id` (phiên bản,
    thông báo…) trước khi chạm dự án, và khoá ngoại để lại `KEY SHARE` trên dòng dự án. `FOR
    UPDATE` xung đột với `KEY SHARE`, nên hai lượt dựng hai tầng của cùng một dự án khoá chéo
    nhau (MULTI-FLOOR-02). `NO KEY UPDATE` — đúng khoá mà `UPDATE updated_at` tự xin — vẫn tuần
    tự hoá các lượt chạm, nhưng không chờ khoá ngoại.
    """
    if not project_ids:
        return
    ids = sorted(set(project_ids))
    lock = select(Project.id).where(Project.id.in_(ids)).order_by(Project.id).with_for_update(key_share=True)
    await db.execute(lock)
    await db.execute(update(Project).where(Project.id.in_(ids)).values(updated_at=clock.now()))


async def touch_project(db: AsyncSession, *, project_id: str, clock: Clock) -> None:
    """Đẩy `projects.updated_at` — lời khoá **mới** cuối cùng của mọi lượt ghi (BE-00 §7)."""
    await touch_projects(db, project_ids=(project_id,), clock=clock)


def _derive_status(
    floor_count: int, walls_total: int, walls_reviewed: int, *, no_upload: bool, running: bool, failed: bool
) -> tuple[SummaryStatus, ProjectStatus]:
    """Suy `(status, legacy_status)` từ bộ cờ của một dự án — một chỗ, đúng thứ tự xét ở [6].

    Đã có tường thì chỉ lượt đang chạy mới giữ `processing`: tầng trống hay lượt mới nhất
    hỏng không làm tường sẵn có hết cần duyệt (U10, H-15 ghi "Đang xử lý" khi chưa chạy).
    """
    all_reviewed = walls_total > 0 and walls_reviewed == walls_total
    status: SummaryStatus = "done"
    if not all_reviewed:
        not_started = walls_total == 0 and (floor_count == 0 or no_upload or failed)
        status = "processing" if running or not_started else "qc"
    legacy: ProjectStatus = "draft"
    if running:
        legacy = "processing"
    elif failed:
        legacy = "error"
    elif floor_count >= 1 and all_reviewed:
        legacy = "approved"
    return status, legacy


async def project_rollups(db: AsyncSession, project_ids: Sequence[str]) -> dict[str, ProjectRollup]:
    """Số gộp của từng dự án bằng **một** `GROUP BY` (bỏ dòng ẩn); dự án không có dòng → 0 tầng.

    Lô rỗng trả `{}` **không** chạy câu nào: một trang danh sách rỗng không đáng một vòng DB
    cho `IN ()`.

    Ba cờ (`no_upload`, `running`, `failed`) tính bằng `bool_or` trong SQL, còn hai trạng
    thái suy ở Python (`_derive_status`) — một chỗ duy nhất cho luật, thay vì hai cây `CASE`
    dài phải sửa song song mỗi lần hợp đồng đổi.
    """
    if not project_ids:
        return {}
    summary = ProjectFloorSummary
    reviewed_all = summary.walls_reviewed >= summary.walls_total
    # Tầng đã chạy pipeline mà tài liệu chưa có tỉ lệ thật → diện tích là số px, không phải m².
    scale_source = func.coalesce(FloorDocumentRow.scale_source, "none")
    no_scale = and_(summary.has_upload, scale_source.not_in(_REAL_SCALE_SOURCES))
    stmt = (
        select(
            summary.project_id,
            func.count().label("floor_count"),
            func.coalesce(func.sum(summary.area_m2), 0).label("area_m2"),
            func.sum(summary.walls_total).label("walls_total"),
            func.sum(summary.walls_reviewed).label("walls_reviewed"),
            func.bool_or(~summary.has_upload).label("no_upload"),
            func.bool_or(summary.pipeline_state.in_(_RUNNING_STATES)).label("running"),
            func.bool_or(summary.pipeline_state == "failed").label("failed"),
            func.bool_or(no_scale).label("no_scale"),
            func.array_agg(
                aggregate_order_by(summary.floor_level_id, reviewed_all, summary.floor_order, summary.floor_level_id),
                type_=ARRAY(Text),
            )[1].label("default_floor_id"),
        )
        .outerjoin(
            FloorRow,
            and_(
                FloorRow.project_id == summary.project_id,
                FloorRow.level_id == summary.floor_level_id,
                FloorRow.deleted_at.is_(None),
            ),
        )
        .outerjoin(FloorDocumentRow, FloorDocumentRow.floor_pk == FloorRow.pk)
        .where(summary.project_id.in_(list(project_ids)), summary.hidden.is_(False))
        .group_by(summary.project_id)
    )
    rollups: dict[str, ProjectRollup] = {project_id: _EMPTY_ROLLUP for project_id in project_ids}
    for row in (await db.execute(stmt)).all():
        status, legacy = _derive_status(
            row.floor_count,
            row.walls_total,
            row.walls_reviewed,
            no_upload=row.no_upload,
            running=row.running,
            failed=row.failed,
        )
        rollups[row.project_id] = ProjectRollup(
            floor_count=row.floor_count,
            area_m2=Decimal(cast("int | Decimal", row.area_m2)),
            walls_total=row.walls_total,
            walls_reviewed=row.walls_reviewed,
            status=status,
            legacy_status=legacy,
            default_floor_id=row.default_floor_id,
            has_scale=not row.no_scale,
            job_active=row.running,
        )
    return rollups
