"""N60 — "Xác nhận và dựng": tạo tầng còn thiếu, một lượt tải `complete` mỗi hình có đích, xếp pipeline sau commit.

Máy **không bao giờ** tự tạo tầng (K-32a quyết định 3): chỉ lệnh này, do người duyệt bấm, mới tạo. Ba chặng (K36):

1. **Kiểm** (giao dịch đọc): trạng thái, `revision`, mọi luật đích (dưới), sinh trước id lượt tải và id dòng mặt cắt.
2. **Chép** tệp gốc sang khoá tầng sẵn có `projects/{p}/floors/{f}/uploads/{u}/original.<đuôi>` (ngoài giao dịch):
   pipeline, N51 chạy lại, N53 huỷ chạy không đổi. Chặng 3 hỏng thì bản chép thành rác của lịch dọn lượt tải mồ côi.
3. **Ghi** dưới khoá dòng bộ: kiểm lại đúng luật của chặng 1 với id đã sinh, tạo tầng qua `floors.service.create_floor`
   (#10: `FLOOR_ID_TAKEN`, trần tầng, nhật ký), dòng `uploads`, `start_run` (xếp pipeline sau commit).

Bộ đã `confirmed` → 200 `DrawingSet` hiện tại, không tạo gì (C10 không khoá, C14 hai xác nhận song song: lượt thua
thấy `confirmed` sau khi chờ khoá). Mặt cắt (trước K-32c): dòng tầng `is_section`, `hidden_in_3d` (đường HS-03).
Thay bản vẽ tầng đã có chỉ khi `replace: true`; lượt mới thay lượt cũ như tải lại hôm nay, và tầng đã duyệt phải duyệt
lại theo luật L2-N03 (`drawings.upsert_drawing` khi lượt mới thành bản vẽ của tầng) — không cần mã riêng ở đây.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.access.activity import record_activity
from apps.api.access.kinds import ActivityKind
from apps.api.drawing_sets.errors import (
    DRAWING_SET_NO_PLAN,
    DRAWING_SET_NOT_READY,
    DRAWING_SET_REVISION_STALE,
    FLOOR_HAS_DRAWING,
)
from apps.api.drawing_sets.set_read import FULL_PAGE, load_set, set_wire
from apps.api.drawing_sets.set_schemas import DrawingSetOut
from apps.api.drawings.runs import start_run
from apps.api.drawings.schemas import CropRect
from apps.api.drawings.uploads import KIND_MIME, file_extension
from apps.api.floors.errors import FLOOR_ID_TAKEN
from apps.api.floors.lookup import get_floor, new_level_id
from apps.api.floors.schemas import ELEVATION_MAX, FloorCreateIn
from apps.api.floors.service import create_floor
from apps.api.projects.access import ProjectAccess
from packages.core.clock import Clock
from packages.core.error_codes import FORBIDDEN, VALIDATION
from packages.core.ids import new_id
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow, DrawingSetViewRow
from packages.db.models.drawings import UploadRow
from packages.db.models.floors import FloorRow
from packages.domain.permissions import can
from packages.storage.keys import upload_original
from packages.storage.port import ObjectStorage

PLANS_MAX: Final = 20
"""Trần hình có đích tầng mỗi lần xác nhận (K-32a §3); vượt → 422 `VALIDATION` `field:"views"`."""
NEW_FLOOR_HEIGHT_MM: Final = 3000
"""Chiều cao mặc định của tầng mới: đề xuất cho cả nhà (N52) sửa lại sau khi pipeline có chữ."""
_FLOOR_MODES: Final = frozenset({"floor", "newFloor"})


@dataclass(frozen=True, slots=True)
class _Placement:
    """Một hình sẽ thành một lượt tải: nguồn (tệp, trang, khung), đích (tầng), và id đã sinh ở chặng 1."""

    view_id: str
    ord: int
    mode: str
    level_id: str
    name: str | None
    replace: bool
    storey_no: int | None
    upload_id: str
    page_index: int
    crop: Mapping[str, float]
    file_name: str
    size_bytes: int
    declared_type: str
    chunk_count: int
    sniffed_kind: str
    content_sha256: str | None
    source_key: str


def _check_access(access: ProjectAccess) -> None:
    """Xác nhận tạo tầng nên đòi thêm `layer.edit` ngoài `floor.upload` của route (K-32a §3)."""
    if not can(access.role, "layer.edit"):
        raise FORBIDDEN.error()


def _check_ready(drawing_set: DrawingSetRow, revision: int) -> None:
    """Bộ phải `review` (409 `DRAWING_SET_NOT_READY`) và `revision` phải là bản mới nhất (409 `…_REVISION_STALE`)."""
    if drawing_set.status != "review":
        raise DRAWING_SET_NOT_READY.error()
    if revision != drawing_set.revision:
        raise DRAWING_SET_REVISION_STALE.error()


async def _view_rows(db: AsyncSession, set_id: str) -> Sequence[Any]:
    """Hình chưa loại của bộ theo `ord`, kèm tệp nguồn và tầng đích có sẵn (một câu SQL)."""
    has_drawing = exists().where(UploadRow.floor_pk == FloorRow.pk, UploadRow.status == "complete")
    stmt = (
        select(
            DrawingSetViewRow,
            DrawingSetFileRow,
            FloorRow.level_id.label("floor_level_id"),
            FloorRow.is_section.label("floor_is_section"),
            FloorRow.deleted_at.label("floor_deleted_at"),
            has_drawing.label("floor_has_drawing"),
        )
        .join(DrawingSetFileRow, DrawingSetFileRow.id == DrawingSetViewRow.file_id)
        .outerjoin(FloorRow, FloorRow.pk == DrawingSetViewRow.target_floor_pk)
        .where(DrawingSetViewRow.set_id == set_id, DrawingSetViewRow.excluded.is_(False))
        .order_by(DrawingSetViewRow.ord)
    )
    return (await db.execute(stmt)).all()


def _target_level(row: Any, field: str, active: frozenset[str]) -> str:
    """`level_id` đích của một hình có đích tầng; tầng có sẵn đã xoá/là mặt cắt → 422; id tầng mới đã dùng → 409."""
    view: DrawingSetViewRow = row[0]
    if view.target_mode == "newFloor":
        if view.new_floor_id in active:
            raise FLOOR_ID_TAKEN.error(field=field)
        return str(view.new_floor_id)
    if row.floor_level_id is None or row.floor_deleted_at is not None or row.floor_is_section:
        raise VALIDATION.error(field=field)
    if row.floor_has_drawing and not view.replace_drawing:
        raise FLOOR_HAS_DRAWING.error()
    return str(row.floor_level_id)


def _placement(row: Any, level_id: str, ids: tuple[str, str]) -> _Placement:
    """Một `_Placement` từ dòng hình + tệp; `ids` = (id lượt tải, id dòng mặt cắt) đã sinh."""
    view: DrawingSetViewRow = row[0]
    file: DrawingSetFileRow = row[1]
    return _Placement(
        view_id=view.id,
        ord=view.ord,
        mode=str(view.target_mode),
        level_id=ids[1] if view.target_mode == "section" else level_id,
        name=view.new_floor_name,
        replace=view.replace_drawing,
        storey_no=view.storey_no,
        upload_id=ids[0],
        page_index=view.page_index,
        crop=view.crop or FULL_PAGE,
        file_name=file.file_name,
        size_bytes=file.declared_size_bytes,
        declared_type=file.declared_type,
        chunk_count=file.chunk_count,
        sniffed_kind=str(file.sniffed_kind),
        content_sha256=file.content_sha256,
        source_key=str(file.original_key),
    )


async def _plan(
    db: AsyncSession, drawing_set: DrawingSetRow, ids: Mapping[str, tuple[str, str]], clock: Clock
) -> list[_Placement]:
    """Mọi luật đích của N60 trên bản duyệt hiện tại; `ids` của chặng 1 được dùng lại, hình mới (không có) thì sinh.

    422 `VALIDATION` `field:"views.<i>.target"`: hình mặt bằng chưa có đích, tầng đích đã xoá/là mặt cắt, hai hình cùng
    một tầng; 409 `FLOOR_HAS_DRAWING`, `FLOOR_ID_TAKEN`; 422 `DRAWING_SET_NO_PLAN`; quá `PLANS_MAX` → `field:"views"`.
    """
    active = frozenset(
        (
            await db.execute(
                select(FloorRow.level_id).where(
                    FloorRow.project_id == drawing_set.project_id, FloorRow.deleted_at.is_(None)
                )
            )
        ).scalars()
    )
    placements: list[_Placement] = []
    used: set[str] = set()
    for row in await _view_rows(db, drawing_set.id):
        view: DrawingSetViewRow = row[0]
        field = f"views.{view.ord}.target"
        if view.target_mode is None:
            if view.kind == "plan":
                raise VALIDATION.error(field=field)
            continue
        level_id = _target_level(row, field, active) if view.target_mode in _FLOOR_MODES else ""
        if level_id and level_id in used:
            raise VALIDATION.error(field=field)
        used.add(level_id)
        placement_ids = ids.get(view.id) or (new_id("upl", clock), new_level_id(clock))
        placements.append(_placement(row, level_id, placement_ids))
    floor_count = sum(placement.mode in _FLOOR_MODES for placement in placements)
    if floor_count == 0:
        raise DRAWING_SET_NO_PLAN.error()
    if floor_count > PLANS_MAX:
        raise VALIDATION.error(field="views")
    return placements


async def _copy_originals(storage: ObjectStorage, project_id: str, placements: Sequence[_Placement]) -> None:
    """Chặng 2: chép tệp gốc của bộ sang khoá tầng của từng lượt tải (luồng, có trần = kích thước khai)."""
    for placement in placements:
        await storage.put(
            upload_original(project_id, placement.level_id, placement.upload_id, file_extension(placement.file_name)),
            storage.open_read(placement.source_key),
            content_type=KIND_MIME[placement.sniffed_kind],
            max_bytes=placement.size_bytes,
        )


async def _stack_start(db: AsyncSession, project_id: str) -> tuple[int, int]:
    """`(số tầng chưa xoá, cốt đỉnh)` của dự án: tầng mới xếp sau cùng, cốt nối tiếp đỉnh tầng mặt bằng cao nhất."""
    count, top = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(
                    func.max(FloorRow.elevation_mm + FloorRow.height_mm).filter(FloorRow.is_section.is_(False)), 0
                ),
            ).where(FloorRow.project_id == project_id, FloorRow.deleted_at.is_(None))
        )
    ).one()
    return int(count), int(top)


async def _create_floors(
    db: AsyncSession, access: ProjectAccess, placements: Sequence[_Placement], clock: Clock
) -> None:
    """Tạo tầng mới (mặt bằng theo số tầng rồi `ord`, mặt cắt sau cùng) qua #10 — vòng lặp có trần `PLANS_MAX` + 60
    hình (R-20: `create_floor` khoá, đếm, ghi nhật ký theo từng tầng, không gom được)."""
    order, elevation = await _stack_start(db, access.project_id)
    fresh = [placement for placement in placements if placement.mode in {"newFloor", "section"}]
    fresh.sort(key=lambda p: (p.mode == "section", p.storey_no is None, p.storey_no or 0, p.ord))
    for placement in fresh:
        is_section = placement.mode == "section"
        body = FloorCreateIn(
            id=placement.level_id,
            name=str(placement.name),
            order=order,
            elevation_mm=0 if is_section else min(elevation, ELEVATION_MAX),
            height_mm=NEW_FLOOR_HEIGHT_MM,
        )
        await create_floor(db, access.project_id, body, access.principal, clock, app=None)
        order += 1
        if is_section:
            row = await get_floor(db, project_id=access.project_id, level_id=placement.level_id, for_update=True)
            if row is not None:
                row.is_section, row.hidden_in_3d = True, True
        else:
            elevation += NEW_FLOOR_HEIGHT_MM


def _crop_corners(crop: Mapping[str, float]) -> list[list[float]] | None:
    """4 góc như #5 (`CropRect.corners`); khung cả trang → `None` (pipeline xử lý cả trang)."""
    if dict(crop) == FULL_PAGE:
        return None
    return CropRect.model_validate(dict(crop)).corners()


async def _create_uploads(
    db: AsyncSession, access: ProjectAccess, placements: Sequence[_Placement], clock: Clock
) -> None:
    """Một dòng `uploads` `complete` mỗi hình + `start_run` (pipeline xếp sau commit); điền `views.upload_id`.

    Vòng lặp có trần (R-20): `start_run` khoá tầng và thay lượt dở của **từng** tầng (BE-00 §7), không gom được.
    """
    for placement in placements:
        floor = await get_floor(db, project_id=access.project_id, level_id=placement.level_id)
        if floor is None:
            raise VALIDATION.error(field=f"views.{placement.ord}.target")
        ext = file_extension(placement.file_name)
        db.add(
            UploadRow(
                id=placement.upload_id,
                project_id=access.project_id,
                floor_pk=floor.pk,
                file_name=placement.file_name,
                declared_size_bytes=placement.size_bytes,
                declared_type=placement.declared_type,
                sniffed_kind=placement.sniffed_kind,
                page_index=placement.page_index,
                chunk_count=placement.chunk_count,
                status="complete",
                original_key=upload_original(access.project_id, placement.level_id, placement.upload_id, ext),
                content_sha256=placement.content_sha256,
                crop_corners=_crop_corners(placement.crop),
                page_auto=False,
                created_by=access.principal.user_id,
            )
        )
        await db.flush()
        await start_run(db, upload_id=placement.upload_id, clock=clock)
        view = await db.get(DrawingSetViewRow, placement.view_id)
        if view is not None:
            view.upload_id = placement.upload_id


async def _phase_one(
    db: AsyncSession, access: ProjectAccess, set_id: str, revision: int, clock: Clock
) -> DrawingSetRow | list[_Placement]:
    """Chặng 1: bộ đã `confirmed` → trả chính dòng bộ (idempotent); không thì kiểm và trả kế hoạch."""
    drawing_set = await load_set(db, project_id=access.project_id, set_id=set_id)
    if drawing_set.status == "confirmed":
        return drawing_set
    _check_ready(drawing_set, revision)
    return await _plan(db, drawing_set, {}, clock)


async def confirm_set(
    db: AsyncSession, storage: ObjectStorage, *, access: ProjectAccess, set_id: str, revision: int, clock: Clock
) -> DrawingSetOut:
    """N60 — xác nhận bản duyệt `revision`: tầng, lượt tải, pipeline; bộ sang `confirmed` (một giao dịch ghi).

    Lỗi: 403 (thiếu `layer.edit`), 404, 409 `DRAWING_SET_NOT_READY`/`…_REVISION_STALE`/`FLOOR_HAS_DRAWING`/
    `FLOOR_ID_TAKEN`, 422 `DRAWING_SET_NO_PLAN`/`VALIDATION`, `FLOOR_LIMIT_REACHED` của #10, 5xx của kho.
    """
    _check_access(access)
    first = await _phase_one(db, access, set_id, revision, clock)
    if isinstance(first, DrawingSetRow):
        return await set_wire(db, storage, first)
    await db.rollback()
    await _copy_originals(storage, access.project_id, first)

    drawing_set = await load_set(db, project_id=access.project_id, set_id=set_id, for_update=True)
    if drawing_set.status == "confirmed":
        return await set_wire(db, storage, drawing_set)
    _check_ready(drawing_set, revision)
    ids = {placement.view_id: (placement.upload_id, placement.level_id) for placement in first}
    placements = await _plan(db, drawing_set, ids, clock)
    if {placement.view_id for placement in placements} != set(ids):
        raise DRAWING_SET_REVISION_STALE.error()
    await _create_floors(db, access, placements, clock)
    await _create_uploads(db, access, placements, clock)
    drawing_set.status, drawing_set.confirmed_at, drawing_set.confirmed_by = (
        "confirmed",
        clock.now(),
        access.principal.user_id,
    )
    await record_activity(
        db,
        actor_id=access.principal.user_id,
        kind=ActivityKind.DRAWING_SET_CONFIRM,
        object_code=set_id,
        object_label=drawing_set.name or set_id,
        clock=clock,
        project_id=access.project_id,
    )
    await db.flush()
    return await set_wire(db, storage, drawing_set)
