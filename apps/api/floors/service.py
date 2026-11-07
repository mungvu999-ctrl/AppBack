"""Nghiệp vụ 5 thao tác tầng (B2-03 [6]).

Thứ tự khoá của mọi route ghi (BE-00 §7, B2-03 [6]): `lock_project_floors` → dòng `floors`
cần ghi `FOR UPDATE` theo `pk` → `summaries.*` → `touch_project` cuối cùng. Mọi bất biến
(trùng id, trần tầng, tập tầng) kiểm **sau** khoá tư vấn, trong giao dịch ghi.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.access.activity import record_activity
from apps.api.access.kinds import ActivityKind
from apps.api.core.auth import Principal
from apps.api.drawings.runs import restore_window_elapsed
from apps.api.floors.errors import (
    FLOOR_HEIGHT_BELOW_OPENING,
    FLOOR_ID_TAKEN,
    FLOOR_LIMIT_REACHED,
    FLOOR_REORDER_MISMATCH,
)
from apps.api.floors.lookup import floor_outs, get_floor, lock_project_floors
from apps.api.floors.schemas import FloorCreateIn, FloorPatchIn
from apps.api.floors.settings import get_floors_settings
from apps.api.projects.summaries import register_floor, set_floor_orders, touch_project, unregister_floor
from apps.api.projects.wire import FloorOut
from apps.api.spatial_read.documents import load_document
from apps.api.spatial_write.writer import LayerWrite, actor_name, write_layer
from packages.core.clock import Clock
from packages.core.error_codes import NOT_FOUND, PATH_BODY_MISMATCH
from packages.db.errors import unique_violation
from packages.db.models.floors import FloorRow
from packages.domain.spatial import SpatialLayer


async def _locked_rows(db: AsyncSession, project_id: str, level_id: str) -> list[FloorRow]:
    """Mọi dòng (kể cả đã xoá) của `(project_id, level_id)`, khoá `FOR UPDATE` theo `pk` (bước 1 của
    #10) — thứ tự khoá ổn định (BE-00 §7), như `_active_rows_locked` của #13. `_restorable` tự chọn
    dòng xoá **gần nhất** trong Python, không dựa vào thứ tự khoá này (LOG-02)."""
    stmt = (
        select(FloorRow)
        .where(FloorRow.project_id == project_id, FloorRow.level_id == level_id)
        .order_by(FloorRow.pk)
        .with_for_update()
    )
    return list((await db.execute(stmt)).scalars().all())


async def _active_count(db: AsyncSession, project_id: str) -> int:
    """Số tầng chưa xoá của dự án (bước 3 của #10)."""
    stmt = (
        select(func.count())
        .select_from(FloorRow)
        .where(FloorRow.project_id == project_id, FloorRow.deleted_at.is_(None))
    )
    return (await db.execute(stmt)).scalar_one()


def _restorable(rows: Sequence[FloorRow], clock: Clock) -> FloorRow | None:
    """Dòng xoá mềm **gần nhất** (`deleted_at` lớn nhất) còn trong cửa sổ khôi phục, nếu có (bước 4
    của #10, LOG-02) — `rows` tới từ `_locked_rows` khoá theo `pk`, không theo `deleted_at`, nên
    chọn ở đây thay vì tin thứ tự của `rows`."""
    candidates = [
        (row.deleted_at, row)
        for row in rows
        if row.deleted_at is not None and not restore_window_elapsed(row.deleted_at, clock)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _apply_restore(row: FloorRow, body: FloorCreateIn) -> None:
    """Khôi phục một dòng xoá mềm: bỏ ẩn (cả ẩn 3D, như tầng mới), ghi lại nội dung theo thân, giữ `pk`."""
    row.deleted_at = None
    row.hidden_in_3d = False
    row.name = body.name
    row.floor_order = body.order
    row.elevation_mm = body.elevation_mm
    row.height_mm = body.height_mm


def _check_not_taken(rows: Sequence[FloorRow]) -> None:
    """Bước 2 của #10: có dòng chưa xoá cùng `(project_id, level_id)` → 409.

    Hàm đồng bộ riêng, gọi ngay sau `await` trong `create_floor`: `coverage.py` không đo đúng
    nhánh nằm ngay sau một `await` của SQLAlchemy async (greenlet, B2-01 `service.py`).
    """
    if any(row.deleted_at is None for row in rows):
        raise FLOOR_ID_TAKEN.error(field="id")


def _check_not_over_limit(active_count: int, floors_max: int) -> None:
    """Bước 3 của #10: đã đạt trần `FLOORS_MAX` → 422 (tách vì lý do như `_check_not_taken`)."""
    if active_count >= floors_max:
        raise FLOOR_LIMIT_REACHED.error()


def _raise_taken_if_unique_violation(exc: IntegrityError) -> None:
    """Vi phạm `uq_floors_level` đua ngoài khoá tư vấn → 409; lỗi khác → ném lại nguyên."""
    if unique_violation(exc) is None:
        raise exc
    raise FLOOR_ID_TAKEN.error(field="id") from exc


async def _insert_floor(db: AsyncSession, project_id: str, body: FloorCreateIn, principal: Principal) -> FloorRow:
    """Chèn dòng tầng mới (bước 5 của #10); vi phạm unique đua với lượt khác → 409 `FLOOR_ID_TAKEN`."""
    row = FloorRow(
        project_id=project_id,
        level_id=body.id,
        name=body.name,
        floor_order=body.order,
        elevation_mm=body.elevation_mm,
        height_mm=body.height_mm,
        created_by=principal.user_id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        _raise_taken_if_unique_violation(exc)
    return row


async def _resolved_row(
    db: AsyncSession,
    project_id: str,
    rows: Sequence[FloorRow],
    clock: Clock,
    body: FloorCreateIn,
    principal: Principal,
) -> tuple[FloorRow, bool]:
    """Dòng để dựng response của #10: khôi phục tại chỗ nếu có, không thì chèn mới; `(row, restored)`."""
    restorable = _restorable(rows, clock)
    if restorable is not None:
        _apply_restore(restorable, body)
        return restorable, True
    return await _insert_floor(db, project_id, body, principal), False


async def create_floor(
    db: AsyncSession, project_id: str, body: FloorCreateIn, principal: Principal, clock: Clock, *, app: object | None
) -> FloorOut:
    """#10: khôi phục tầng xoá mềm trong cửa sổ hoặc chèn mới; 201 (B2-03 [6])."""
    settings = get_floors_settings()
    await lock_project_floors(db, project_id)
    rows = await _locked_rows(db, project_id, body.id)
    _check_not_taken(rows)
    active_count = await _active_count(db, project_id)
    _check_not_over_limit(active_count, settings.floors_max)
    row, restored = await _resolved_row(db, project_id, rows, clock, body, principal)
    await register_floor(db, project_id=project_id, floor_level_id=body.id, floor_order=body.order, restored=restored)
    await touch_project(db, project_id=project_id, clock=clock)
    await record_activity(
        db,
        actor_id=principal.user_id,
        kind=ActivityKind.FLOOR_CREATE,
        object_code=body.id,
        object_label=body.name,
        clock=clock,
        project_id=project_id,
    )
    return (await floor_outs(db, project_id=project_id, floor_pks=[row.pk], app=app))[0]


def _checked_floor(row: FloorRow | None) -> FloorRow:
    """`row` phải tồn tại (dò trước khi ghi của #11, #34) → 404 `resource:"floor"`.

    Tách khỏi hàm gọi (đồng bộ, ngay sau `await`) cùng lý do `_check_not_taken` ở trên.
    """
    if row is None:
        raise NOT_FOUND.error(resource="floor")
    return row


def _mark_deleted(row: FloorRow, clock: Clock) -> None:
    """Đặt `deleted_at` — tách khỏi `delete_floor` (đồng bộ, ngay sau `await`, lý do như trên)."""
    row.deleted_at = clock.now()


async def delete_floor(
    db: AsyncSession, project_id: str, level_id: str, principal: Principal, clock: Clock, *, app: object | None
) -> FloorOut:
    """#11: xoá mềm; thân trả về là ảnh chụp **ngay trước** khi xoá (B2-03 [6])."""
    await lock_project_floors(db, project_id)
    found = await get_floor(db, project_id=project_id, level_id=level_id, for_update=True)
    row = _checked_floor(found)
    snapshot = (await floor_outs(db, project_id=project_id, floor_pks=[row.pk], app=app))[0]
    _mark_deleted(row, clock)
    await unregister_floor(db, project_id=project_id, floor_level_id=level_id)
    await touch_project(db, project_id=project_id, clock=clock)
    await record_activity(
        db,
        actor_id=principal.user_id,
        kind=ActivityKind.FLOOR_DELETE,
        object_code=level_id,
        object_label=snapshot.name,
        clock=clock,
        project_id=project_id,
    )
    return snapshot


async def list_floors(db: AsyncSession, project_id: str, *, app: object | None) -> list[FloorOut]:
    """#12: mọi tầng chưa xoá của dự án, `(floor_order, pk)` (B2-03 [6])."""
    return await floor_outs(db, project_id=project_id, app=app)


async def _active_rows_locked(db: AsyncSession, project_id: str) -> list[FloorRow]:
    """Mọi tầng chưa xoá của dự án, khoá `FOR UPDATE` theo `pk` (bước khoá của #13)."""
    stmt = (
        select(FloorRow)
        .where(FloorRow.project_id == project_id, FloorRow.deleted_at.is_(None))
        .order_by(FloorRow.pk)
        .with_for_update()
    )
    return list((await db.execute(stmt)).scalars().all())


def _changed_orders(rows: Sequence[FloorRow], floor_ids: Sequence[str]) -> dict[str, int]:
    """`level_id -> vị trí mới` chỉ cho những dòng có `floor_order` thật sự đổi."""
    positions = {level_id: i for i, level_id in enumerate(floor_ids)}
    return {row.level_id: positions[row.level_id] for row in rows if row.floor_order != positions[row.level_id]}


def _check_matches_set(rows: Sequence[FloorRow], floor_ids: Sequence[str]) -> None:
    """Tập `level_id` phải bằng đúng tập `floor_ids` → 422 (tách vì lý do như `_check_not_taken`)."""
    if {row.level_id for row in rows} != set(floor_ids):
        raise FLOOR_REORDER_MISMATCH.error(field="floorIds")


def _apply_orders(rows: Sequence[FloorRow], changed: dict[str, int]) -> None:
    """Ghi `floor_order` mới lên các dòng ORM đã khoá (đồng bộ, gọi ngay sau `await`)."""
    for row in rows:
        if row.level_id in changed:
            row.floor_order = changed[row.level_id]


async def reorder_floors(
    db: AsyncSession,
    project_id: str,
    project_name: str,
    floor_ids: Sequence[str],
    principal: Principal,
    clock: Clock,
    *,
    app: object | None,
) -> list[FloorOut]:
    """#13: `floor_order` = vị trí trong `floor_ids`; chỉ ghi khi có dòng đổi (B2-03 [6])."""
    await lock_project_floors(db, project_id)
    rows = await _active_rows_locked(db, project_id)
    _check_matches_set(rows, floor_ids)
    changed = _changed_orders(rows, floor_ids)
    if changed:
        _apply_orders(rows, changed)
        await set_floor_orders(db, project_id=project_id, orders=changed)
        await touch_project(db, project_id=project_id, clock=clock)
        await record_activity(
            db,
            actor_id=principal.user_id,
            kind=ActivityKind.FLOOR_REORDER,
            object_code=project_id,
            object_label=project_name,
            clock=clock,
            project_id=project_id,
        )
    return await floor_outs(db, project_id=project_id, app=app)


def _check_path_body_id(level_id: str, body_id: str | None) -> None:
    """`id` của thân #34 khác `{floor_id}` trên đường → 422 `PATH_BODY_MISMATCH`; bằng thì bỏ qua."""
    if body_id is not None and body_id != level_id:
        raise PATH_BODY_MISMATCH.error(field="id")


def _changed_fields(row: FloorRow, body: FloorPatchIn) -> dict[str, object]:
    """Khoá có mặt trong thân #34 mà khác hiện trạng; `{}` hay giá trị trùng → rỗng."""
    candidates = {
        "name": body.name,
        "order": body.order,
        "elevation_mm": body.elevation_mm,
        "height_mm": body.height_mm,
        "hidden_in_3d": body.hidden_in_3d,
    }
    current = {
        "name": row.name,
        "order": row.floor_order,
        "elevation_mm": row.elevation_mm,
        "height_mm": row.height_mm,
        "hidden_in_3d": row.hidden_in_3d,
    }
    return {field: value for field, value in candidates.items() if value is not None and value != current[field]}


def _apply_changes(row: FloorRow, changes: dict[str, object]) -> None:
    """Ghi các khoá đã đổi lên dòng; `order` dây ↔ `floor_order` của model."""
    for field, value in changes.items():
        setattr(row, "floor_order" if field == "order" else field, value)


@dataclass(frozen=True)
class _Retargeted:
    """`MergeOutcome` của `write_layer` cho H2: lớp đã đổi cao tường, không đổi id nào."""

    layer: SpatialLayer
    id_map: Mapping[str, str]


def _retargeted_heights(layer: SpatialLayer, old_mm: int, new_mm: int) -> SpatialLayer:
    """Lớp với mọi tường "theo tầng" (`heightMm == old_mm`) đổi sang `new_mm`; tường khác giữ nguyên.

    Một ô mở của tường theo tầng có `sill + height > new_mm` → 422 `FLOOR_HEIGHT_BELOW_OPENING`
    (`field="heightMm"`) trước khi đổi gì (H2, HOP-DONG-M1 §7). Không tường nào khớp → trả nguyên
    `layer`, `write_layer` khi đó thấy "không đổi" và không tăng `revision`.
    """
    inherited = {wall.id for wall in layer.walls if wall.height_mm == old_mm}
    if any(item.wall_id in inherited and item.sill_height_mm + item.height_mm > new_mm for item in layer.openings):
        raise FLOOR_HEIGHT_BELOW_OPENING.error(field="heightMm")
    walls = tuple(
        wall.model_copy(update={"height_mm": new_mm}) if wall.id in inherited else wall for wall in layer.walls
    )
    return layer.model_copy(update={"walls": walls})


async def _retarget_walls(db: AsyncSession, row: FloorRow, new_mm: int, principal: Principal, clock: Clock) -> None:
    """H2: đổi cao tầng kéo theo tường "theo tầng", qua `write_layer` (khoá, `revision += 1`, nhật ký `height_mm`).

    Đi đường `merge` của `write_layer` chứ không ghi tay: đường ấy khoá `floor_documents FOR UPDATE`
    rồi `floor_entity_ids` → `project_floor_summaries` → `projects` đúng BE-00 §7 (dòng `floors` đã
    `FOR UPDATE` ở `patch_floor`), ghi nhật ký theo trường để 409 W20 của FE cầm `baseVersion` cũ thấy
    `remoteChanges` `height_mm`, và không đánh `edited` cho tường AI (đây không phải người sửa tường).
    Không có lỗi xung đột: PATCH tầng vốn last-write-wins. Tầng chưa có tài liệu → không làm gì.

    Giới hạn đã biết: tường **tuỳ chỉnh** trùng đúng chiều cao cũ của tầng không phân biệt được với
    tường theo tầng, nên cũng bị đổi theo.
    """
    if await load_document(db, row.pk) is None:
        return
    old_mm = row.height_mm

    def retarget(layer: SpatialLayer, _scale: Decimal | None) -> _Retargeted:
        """Hàm `merge`: nền là lớp đang có, đã khoá."""
        return _Retargeted(_retargeted_heights(layer, old_mm, new_mm), {})

    await write_layer(
        db,
        floor_pk=row.pk,
        base_revision=0,
        body=LayerWrite(layer=None, scale_mm_per_px=None),
        actor_id=principal.user_id,
        actor_name=await actor_name(db, principal.user_id),
        clock=clock,
        merge=retarget,
    )


async def patch_floor(
    db: AsyncSession,
    project_id: str,
    level_id: str,
    body: FloorPatchIn,
    principal: Principal,
    clock: Clock,
    *,
    app: object | None,
) -> FloorOut:
    """#34: last-write-wins; `{}` hay giá trị không đổi → 200 không ghi, không nhật ký (B2-03 [6]).

    Đổi `heightMm` thì tường theo tầng đổi theo trong cùng giao dịch (H2, `_retarget_walls`), **trước**
    `set_floor_orders`/`touch_project` để giữ thứ tự khoá `floors` → `floor_documents` → … → `projects`.
    """
    _check_path_body_id(level_id, body.id)
    await lock_project_floors(db, project_id)
    found = await get_floor(db, project_id=project_id, level_id=level_id, for_update=True)
    row = _checked_floor(found)
    changes = _changed_fields(row, body)
    if "height_mm" in changes:
        await _retarget_walls(db, row, cast("int", changes["height_mm"]), principal, clock)
    if changes:
        _apply_changes(row, changes)
        if "order" in changes:
            await set_floor_orders(db, project_id=project_id, orders={level_id: row.floor_order})
        await touch_project(db, project_id=project_id, clock=clock)
        await record_activity(
            db,
            actor_id=principal.user_id,
            kind=ActivityKind.FLOOR_EDIT,
            object_code=level_id,
            object_label=row.name,
            clock=clock,
            project_id=project_id,
        )
    return (await floor_outs(db, project_id=project_id, floor_pks=[row.pk], app=app))[0]
