"""N59 — lưu bản duyệt của bộ hồ sơ (GV, W20): thay **cả** danh sách hình, `revision` tăng một.

Luật thay thế (K-32a §3): hình máy (`ai`) không bao giờ mất — vắng khỏi danh sách thì giữ nguyên, có mặt thì chỉ
được sửa khung/loại/tầng/đích/`excluded`, không đổi tệp hay trang; hình người vẽ (`user`) vắng khỏi danh sách thì xoá.
Thứ tự hình = thứ tự trong danh sách, hình máy vắng đứng sau.

Thắng thua quyết bằng **một** câu `UPDATE … WHERE revision = :base RETURNING` (K07, mẫu `project_settings`), khoá luôn
dòng bộ tới hết giao dịch: hai lượt lưu song song cùng base → đúng một bên thắng, bên kia 409 (C14). Câu đó trả 0 dòng
thì mới đọc hiện trạng để tách lượt gửi lặp của chính người ghi (C09b) khỏi xung đột thật.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import Principal
from apps.api.drawing_sets.errors import DRAWING_SET_CONFIRMED, DRAWING_SET_NOT_READY
from apps.api.drawing_sets.set_read import load_set, set_wire
from apps.api.drawing_sets.set_schemas import DrawingSetOut, FloorTargetIn, NewFloorTargetIn, ViewEditIn, ViewsBodyIn
from packages.core.clock import Clock
from packages.core.error_codes import VALIDATION
from packages.core.errors import VersionConflictError
from packages.core.ids import new_id
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow, DrawingSetViewRow
from packages.db.models.floors import FloorRow
from packages.storage.port import ObjectStorage

ORD_SHIFT: Final = 100_000
"""Dời `ord` của mọi hình trước khi đánh lại 0..n-1: tránh vấp UNIQUE `(set_id, ord)` giữa chừng."""
USER_CONFIDENCE: Final = 1.0
"""Độ tin của giá trị người duyệt gõ (loại, số tầng) — người là nguồn chắc nhất."""


@dataclass(frozen=True, slots=True)
class _Context:
    """Những gì cần để kiểm một bản duyệt: tệp lành (số trang), hình hiện có, tầng chưa xoá của dự án."""

    pages: Mapping[str, int]
    views: Mapping[str, DrawingSetViewRow]
    floors: Mapping[str, int]


def body_digest(body: ViewsBodyIn) -> str:
    """SHA-256 của thân đã chuẩn hoá (JSON khoá sắp): cùng thân sau NFC luôn cùng băm (C09b, C16)."""
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_status(drawing_set: DrawingSetRow) -> None:
    """Chỉ bộ `review` nhận bản duyệt: đã xác nhận → 409 `DRAWING_SET_CONFIRMED`, khác → 409 `DRAWING_SET_NOT_READY`."""
    if drawing_set.status == "confirmed":
        raise DRAWING_SET_CONFIRMED.error()
    if drawing_set.status != "review":
        raise DRAWING_SET_NOT_READY.error()


async def _context(db: AsyncSession, drawing_set: DrawingSetRow) -> _Context:
    """Ba câu SQL cố định: tệp `complete` + số trang, hình của bộ, `level_id → pk` của tầng chưa xoá trong dự án."""
    pages = dict(
        (
            await db.execute(
                select(DrawingSetFileRow.id, DrawingSetFileRow.page_count).where(
                    DrawingSetFileRow.set_id == drawing_set.id, DrawingSetFileRow.status == "complete"
                )
            )
        )
        .tuples()
        .all()
    )
    views = {
        row.id: row
        for row in (
            await db.execute(select(DrawingSetViewRow).where(DrawingSetViewRow.set_id == drawing_set.id))
        ).scalars()
    }
    floors = dict(
        (
            await db.execute(
                select(FloorRow.level_id, FloorRow.pk).where(
                    FloorRow.project_id == drawing_set.project_id, FloorRow.deleted_at.is_(None)
                )
            )
        )
        .tuples()
        .all()
    )
    return _Context(pages={key: value or 0 for key, value in pages.items()}, views=views, floors=floors)


def _check_edit(index: int, edit: ViewEditIn, context: _Context, seen: set[str]) -> None:
    """Một hình hợp lệ với bộ: id có thật, không lặp; hình máy giữ tệp/trang; tệp lành; trang có; tầng có thật."""
    field = f"body.views.{index}"
    if edit.id is not None:
        current = context.views.get(edit.id)
        if current is None or edit.id in seen:
            raise VALIDATION.error(field=f"{field}.id")
        seen.add(edit.id)
        if current.source == "ai" and current.file_id != edit.file_id:
            raise VALIDATION.error(field=f"{field}.fileId")
        if current.source == "ai" and current.page_index != edit.page_index:
            raise VALIDATION.error(field=f"{field}.pageIndex")
    if edit.file_id not in context.pages:
        raise VALIDATION.error(field=f"{field}.fileId")
    if edit.page_index >= context.pages[edit.file_id]:
        raise VALIDATION.error(field=f"{field}.pageIndex")
    if isinstance(edit.target, FloorTargetIn) and edit.target.floor_id not in context.floors:
        raise VALIDATION.error(field=f"{field}.target")


def _target_columns(edit: ViewEditIn, floors: Mapping[str, int]) -> dict[str, Any]:
    """Cột đích của một hình theo `target` (vắng = xoá đích)."""
    target = edit.target
    columns: dict[str, Any] = {
        "target_mode": None if target is None else target.mode,
        "target_floor_pk": None,
        "new_floor_id": None,
        "new_floor_name": None,
        "replace_drawing": False,
    }
    if isinstance(target, FloorTargetIn):
        columns |= {"target_floor_pk": floors[target.floor_id], "replace_drawing": bool(target.replace)}
    elif isinstance(target, NewFloorTargetIn):
        columns |= {"new_floor_id": target.floor_id, "new_floor_name": target.name}
    elif target is not None:
        columns["new_floor_name"] = target.name
    return columns


def _apply_edit(view: DrawingSetViewRow, edit: ViewEditIn, ord_: int, floors: Mapping[str, int]) -> None:
    """Ghi một hình: khung, loại, số tầng, đích, `excluded`; loại/số tầng người đổi → độ tin 1, `machine` giữ nguyên."""
    if edit.kind != view.kind:
        view.kind, view.kind_confidence = edit.kind, USER_CONFIDENCE
    if edit.storey_no != view.storey_no:
        view.storey_no, view.storey_confidence = edit.storey_no, None if edit.storey_no is None else USER_CONFIDENCE
    view.ord, view.file_id, view.page_index = ord_, edit.file_id, edit.page_index
    view.crop, view.excluded = edit.crop.model_dump(), edit.excluded
    for column, value in _target_columns(edit, floors).items():
        setattr(view, column, value)


async def _rewrite_views(
    db: AsyncSession, drawing_set: DrawingSetRow, edits: Sequence[ViewEditIn], context: _Context, clock: Clock
) -> None:
    """Thay danh sách hình theo luật ở docstring module (đã kiểm hợp lệ trước đó)."""
    listed = {edit.id for edit in edits if edit.id is not None}
    dropped = [view_id for view_id, view in context.views.items() if view.source == "user" and view_id not in listed]
    if dropped:
        await db.execute(delete(DrawingSetViewRow).where(DrawingSetViewRow.id.in_(dropped)))
    await db.execute(
        update(DrawingSetViewRow)
        .where(DrawingSetViewRow.set_id == drawing_set.id, DrawingSetViewRow.id.not_in(dropped))
        .values(ord=DrawingSetViewRow.ord + ORD_SHIFT)
    )
    await db.flush()
    for ord_, edit in enumerate(edits):
        view = context.views.get(edit.id) if edit.id is not None else None
        if view is None:
            view = DrawingSetViewRow(
                id=new_id("dwv", clock), set_id=drawing_set.id, source="user", kind=edit.kind, kind_confidence=1.0
            )
            db.add(view)
        _apply_edit(view, edit, ord_, context.floors)
    kept = sorted(
        (view for view in context.views.values() if view.source == "ai" and view.id not in listed), key=lambda v: v.ord
    )
    for offset, view in enumerate(kept, start=len(edits)):
        view.ord = offset
    await db.flush()


async def _claim_revision(
    db: AsyncSession, set_id: str, base_version: int, writer: str, digest: str
) -> DrawingSetRow | None:
    """Câu quyết thắng thua (K07): tăng `revision` khi base còn khớp và bộ còn `review`; `None` khi không."""
    stmt = (
        update(DrawingSetRow)
        .where(
            DrawingSetRow.id == set_id,
            DrawingSetRow.revision == base_version,
            DrawingSetRow.status == "review",
            DrawingSetRow.deleted_at.is_(None),
        )
        .values(revision=DrawingSetRow.revision + 1, last_writer_id=writer, last_body_sha256=digest)
        .returning(DrawingSetRow)
    )
    return (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()


async def replace_views(
    db: AsyncSession,
    storage: ObjectStorage,
    *,
    project_id: str,
    set_id: str,
    base_version: int,
    body: ViewsBodyIn,
    principal: Principal,
    clock: Clock,
) -> DrawingSetOut:
    """N59 — lưu bản duyệt nếu `base_version` còn khớp; lượt lặp của chính người ghi → 200 không đổi gì (C09b).

    Lỗi: 404 bộ; 409 `DRAWING_SET_CONFIRMED`/`DRAWING_SET_NOT_READY`; 422 `VALIDATION` `field` `body.views.<i>.<khoá>`;
    409 `VERSION_CONFLICT` (`remoteChanges: []`) khi base cũ. Không ghi nhật ký (revision là dấu vết, BE-BIND).
    """
    drawing_set = await load_set(db, project_id=project_id, set_id=set_id)
    _check_status(drawing_set)
    context = await _context(db, drawing_set)
    seen: set[str] = set()
    for index, edit in enumerate(body.views):
        _check_edit(index, edit, context, seen)
    digest = body_digest(body)
    claimed = await _claim_revision(db, set_id, base_version, principal.user_id, digest)
    if claimed is None:
        current = await load_set(db, project_id=project_id, set_id=set_id)
        _check_status(current)
        if (current.revision, current.last_writer_id, current.last_body_sha256) == (
            base_version + 1,
            principal.user_id,
            digest,
        ):
            return await set_wire(db, storage, current)
        raise VersionConflictError(current_version=current.revision, remote_changes=[])
    await _rewrite_views(db, claimed, body.views, context, clock)
    return await set_wire(db, storage, claimed)
