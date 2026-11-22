"""Đọc và khoá tầng (B2-03 [2], [6] "Khoá", "Dựng `Floor`"); tên công khai chốt ở
`dinh-chinh.md` §9.

`lock_project_floors` được re-export từ `apps.api.floors.locks` (không định nghĩa lại ở
đây): module này nhập `apps.api.projects.parts` cho cổng `floor.drawings`, mà `parts.py`
nhập `apps.api.core.auth` (dù chỉ dưới `TYPE_CHECKING`) — `jobs.py` (worker) không được
nhập module này vì thế, nên nhập thẳng `apps.api.floors.locks` (BE-00 §7 "Hàm worker nhập").
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.floors.locks import lock_project_floors as lock_project_floors
from apps.api.projects.parts import FLOOR_DRAWINGS, view_part
from apps.api.projects.wire import DrawingOut, FloorOut
from packages.core.clock import Clock
from packages.core.ids import new_ulid
from packages.db.models.floors import FloorRow
from packages.db.models.projects import ProjectFloorSummary


async def _drawings_of(
    db: AsyncSession, pks: Sequence[int], *, app: object | None
) -> Mapping[str, Sequence[DrawingOut]]:
    """Một lượt `view_part(FLOOR_DRAWINGS)` cho cả lô, khoá `str(pk)`; chưa ai cài → rỗng."""
    part = view_part(FLOOR_DRAWINGS, app=app)
    if part is None or not pks:
        return {}
    # `ViewPart` của `FLOOR_DRAWINGS` khai `DrawingOut` (parts.py); sai kiểu hỏng ngay ở
    # test của B2-04, như `_project_outs` (B2-01 service.py) làm với `PROJECT_FLOORS`.
    loaded = await part.load(db, [str(pk) for pk in pks])
    return cast("Mapping[str, Sequence[DrawingOut]]", loaded)


def _floor_out(row: FloorRow, area_m2: Decimal | None, drawings: Mapping[str, Sequence[DrawingOut]]) -> FloorOut:
    """`FloorOut` của một dòng; `area_m2` `Decimal` → `float` làm tròn 2 chữ số, `NULL` → vắng."""
    return FloorOut(
        id=row.level_id,
        name=row.name,
        order=row.floor_order,
        elevation_mm=row.elevation_mm,
        height_mm=row.height_mm,
        area_m2=float(round(area_m2, 2)) if area_m2 is not None else None,
        drawings=list(drawings.get(str(row.pk), ())),
        hidden_in_3d=row.hidden_in_3d,
        is_section=True if row.is_section else None,
    )


async def _load_floor_outs(
    db: AsyncSession, project_ids: Sequence[str], *, floor_pks: Sequence[int] | None = None, app: object | None = None
) -> dict[str, list[tuple[int, FloorOut]]]:
    """Lõi dùng chung của `floor_outs`, `floor_outs_with_pk` và `floor_outs_by_project` (dinh-chinh §9, R-07).

    Mỗi phần tử là `(FloorRow.pk, FloorOut)`: `FloorOut` chỉ mang `level_id`, còn người gọi cần
    `pk` (khoá của bảng không gian) thì lấy từ cùng câu SQL, không đọc `floors` lần hai.

    Một truy vấn `floors` ⟕ `project_floor_summaries` cho cả lô `project_ids`, chỉ tầng
    chưa xoá, sắp `(project_id, floor_order, pk)`; số câu SQL không đổi theo số tầng hay
    số dự án. Trả **mọi** id được hỏi, kể cả dự án không tầng nào (`[]`).
    """
    result: dict[str, list[tuple[int, FloorOut]]] = {project_id: [] for project_id in project_ids}
    if not project_ids:
        return result
    stmt = (
        select(FloorRow, ProjectFloorSummary.area_m2)
        .outerjoin(
            ProjectFloorSummary,
            and_(
                ProjectFloorSummary.project_id == FloorRow.project_id,
                ProjectFloorSummary.floor_level_id == FloorRow.level_id,
            ),
        )
        .where(FloorRow.project_id.in_(project_ids), FloorRow.deleted_at.is_(None))
        .order_by(FloorRow.project_id, FloorRow.floor_order, FloorRow.pk)
    )
    if floor_pks is not None:
        stmt = stmt.where(FloorRow.pk.in_(floor_pks))
    rows = (await db.execute(stmt)).all()
    drawings = await _drawings_of(db, [row.FloorRow.pk for row in rows], app=app)
    for row in rows:
        floor_row: FloorRow = row.FloorRow
        result[floor_row.project_id].append((floor_row.pk, _floor_out(floor_row, row.area_m2, drawings)))
    return result


async def floor_outs_by_project(
    db: AsyncSession, project_ids: Sequence[str], *, app: object | None = None
) -> dict[str, list[FloorOut]]:
    """`FloorOut` chưa xoá của mỗi dự án trong `project_ids` (cổng `project.floors` của B2-01)."""
    loaded = await _load_floor_outs(db, list(project_ids), app=app)
    return {project_id: [out for _, out in pairs] for project_id, pairs in loaded.items()}


async def floor_outs(
    db: AsyncSession, *, project_id: str, floor_pks: Sequence[int] | None = None, app: object | None = None
) -> list[FloorOut]:
    """`FloorOut` chưa xoá của một dự án, lọc `floor_pks` nếu có (lọc trong SQL, không ở Python)."""
    return [out for _, out in await floor_outs_with_pk(db, project_id=project_id, floor_pks=floor_pks, app=app)]


async def floor_outs_with_pk(
    db: AsyncSession, *, project_id: str, floor_pks: Sequence[int] | None = None, app: object | None = None
) -> list[tuple[int, FloorOut]]:
    """Như `floor_outs` nhưng mỗi tầng đi kèm `FloorRow.pk`, cùng thứ tự và cùng một câu SQL."""
    return (await _load_floor_outs(db, [project_id], floor_pks=floor_pks, app=app))[project_id]


async def get_floor(db: AsyncSession, *, project_id: str, level_id: str, for_update: bool = False) -> FloorRow | None:
    """Tầng chưa xoá của dự án theo `level_id`; `for_update=True` khoá dòng (`FOR UPDATE`)."""
    stmt = select(FloorRow).where(
        FloorRow.project_id == project_id, FloorRow.level_id == level_id, FloorRow.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def new_level_id(clock: Clock) -> str:
    """`level_id` mới, `is_spatial_id("level", …)` đúng (dinh-chinh §7): `L-` + `new_ulid`."""
    return "L-" + new_ulid(clock)
