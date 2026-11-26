"""Tác vụ ráp nhà `drawing_set.assemble` (NHA-07a, thiết kế §2.E): dựng mái AI cho tầng của bộ đã xác nhận.

Một lượt quét định kỳ (`jobs.assemble_drawing_sets`). Worker nhập module này, nên không nhập `fastapi` hay
`apps.api.core`. Mỗi lượt:

1. **Chọn việc** (`_due`, một câu SQL): hình có đích tầng của bộ `confirmed` chưa xoá mà bản đóng băng
   (`building.confirmed`, N60 ghi) có mái cho hình đó; lượt chạy mới nhất của tầng đã ở trạng thái cuối; không có bộ
   `confirmed` nào mới hơn cũng nhắm tầng đó; và bộ ba dấu vân (id lượt chạy, `revision` tài liệu tầng, chiều cao
   tầng) khác bộ ba đã lưu ở `building.assembly`. Không đổi thì tầng không được đụng tới, kể cả đọc lớp.
2. **Ráp từng tầng** (`_assemble_floor`), mỗi tầng một giao dịch: khoá dòng bộ → tầng → tài liệu tầng (thứ tự của
   N60 và BE-00 §7), dựng mái từ đường bao phòng hiện tại, áp luật M-11/M-16 (`settle`) dưới khoá, ghi qua
   `write_layer(merge=…)` rồi lưu kết quả vào `building.assembly` trong cùng giao dịch.

Mái ghi ra mang `source="ai"`, `reviewed=False`: `readiness` báo `roofUnreviewed` cho tới khi người dùng duyệt (M-10).
Thông số mái lấy từ bản đóng băng, không tính lại đề xuất: mã mới triển khai sau lúc xác nhận không được cho mái khác
cái người dùng đã nhận. Bộ xác nhận mà chưa từng có quyết định (không có bản đóng băng) thì không có mái.

shortcut: mỗi chu kỳ quét mọi bộ đã xác nhận bằng một truy vấn so bộ ba; khi số bộ lên hàng chục nghìn thì chuyển
sang chỉ quét bộ có tầng vừa đổi. Tầng lỗi lặp lại (tài liệu hỏng) được thử lại mỗi chu kỳ và chiếm một chỗ của lô.
"""

import logging
import statistics
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final, cast

from pydantic import JsonValue, ValidationError
from sqlalchemy import ColumnElement, Text, func, or_, select, true
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from apps.api.drawings.runs import FINAL_RUN_STATUSES
from apps.api.spatial_read.codec import DocumentCorruptError
from apps.api.spatial_read.documents import FloorDocument, ensure_document, load_document
from apps.api.spatial_write.writer import LayerWrite, write_layer
from packages.core.clock import Clock
from packages.core.errors import SYSTEM_PIPELINE, AppError
from packages.db.engine import session_scope
from packages.db.models.drawing_sets import DrawingSetRow, DrawingSetViewRow
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.db.models.floors import FloorRow
from packages.db.models.pipeline_orchestrate import PipelineRunModelsRow
from packages.db.models.projects import Project
from packages.db.models.spatial import FloorDocumentRow
from packages.domain.drawing_set.assembly import (
    FloorAssembly,
    Refusal,
    eave_above_floor_mm,
    roof_id,
    settle,
)
from packages.domain.drawing_set.building import BuildingProposal, LevelSpec, RoofSpec
from packages.domain.drawing_set.rules import RuleHit, m04_ridge
from packages.domain.spatial.model import Roof, SpatialLayer
from packages.domain.spatial.roof_build import RoofForm, ridge_height_mm, roof_nodes
from packages.vision.roofs import roof_outline, roof_rects

_log: Final = logging.getLogger(__name__)

ASSEMBLE_TASK: Final = "default.drawing_set.assemble"
ASSEMBLE_EVERY: Final = timedelta(seconds=60)
"""Chu kỳ quét = độ trễ tối đa từ lúc pipeline xong tới lúc có mái; 60 giây là sàn của `periodic` (`MIN_PERIOD_S`)."""
SWEEP_BATCH: Final = 200
"""Số tầng tối đa mỗi lượt quét; phần còn lại sang lượt sau (R-21)."""
FROZEN_VERSION: Final = 1
"""`schemaVersion` của bản đề xuất đóng băng trong `building.confirmed` (N60 ghi; route đọc và tác vụ này kiểm)."""

_ACTOR_NAME: Final = "hệ thống AI"  # tên hiển thị của pipeline (`apps.worker`; `apps.api` không được nhập worker)
_FLOOR_MODES: Final = ("floor", "newFloor")
_UNROOFED: Final = frozenset({"loggia"})  # phòng không có mái che; mô hình chưa có sân thượng/ban công riêng
_FORMS: Final = frozenset({"flat", "shed", "gable", "hip"})
_SINGLE_FACE: Final = frozenset({"flat", "shed"})  # `roof_nodes` chỉ dựng hai dạng này trên một chữ nhật
_BAD_ENVELOPE: Final = frozenset({"OPEN_ENVELOPE", "WALL_FRAGMENTS"})  # M-15: phòng của lượt này không tin được


@dataclass(frozen=True, slots=True)
class _Due:
    """Một tầng cần ráp: bộ, hình mặt bằng đại diện (`levelRef`) và tầng đích của nó."""

    set_id: str
    view_id: str
    floor_pk: int


@dataclass(frozen=True, slots=True)
class _Merged:
    """Kết quả của hàm `merge` đưa cho `write_layer` (`MergeOutcome`): lớp mới, không đổi id nào."""

    layer: SpatialLayer
    id_map: Mapping[str, str]


def frozen_proposal(building: Mapping[str, Any] | None, set_id: str) -> BuildingProposal | None:
    """Bản đề xuất đóng băng trong `building.confirmed`; `None` khi chưa từng đóng băng.

    Bản lưu khác `FROZEN_VERSION`, hoặc không còn khớp mô hình hiện tại, cũng trả `None` kèm một dòng log: người gọi
    coi như không có bản đóng băng, chứ không hỏng mãi mãi vì một bộ đã xác nhận.
    """
    stored = (building or {}).get("confirmed")
    if stored is None:
        return None
    try:
        if stored["schemaVersion"] != FROZEN_VERSION:
            raise ValueError(f"schemaVersion {stored['schemaVersion']}")
        return BuildingProposal.model_validate(stored["proposal"])
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        _log.warning("set_building_frozen_unreadable", extra={"set_id": set_id, "error": type(exc).__name__})
        return None


def _stored(key: str) -> Any:
    """Một trường (dạng chữ) của kết quả ráp đã lưu cho hình đang xét; SQL NULL khi chưa lưu."""
    return func.jsonb_extract_path_text(DrawingSetRow.building, "assembly", DrawingSetViewRow.id, key)


def _newer_set_on_floor() -> ColumnElement[bool]:
    """Có bộ `confirmed` tạo sau bộ đang xét cũng nhắm tầng này (câu con tương quan với bộ và tầng của `_due`).

    Chỉ bộ mới nhất được ráp một tầng: hai bộ mang `RoofSpec` khác nhau sẽ thay mái của nhau mãi, vì mỗi lượt ghi làm
    lệch `revision` trong bộ ba của bộ kia. Id bộ là ULID nên so id là so thời điểm tạo.
    """
    other_set, other_view, other_upload = aliased(DrawingSetRow), aliased(DrawingSetViewRow), aliased(UploadRow)
    return (
        select(other_set.id)
        .join(other_view, other_view.set_id == other_set.id)
        .join(other_upload, other_upload.id == other_view.upload_id)
        .where(
            other_upload.floor_pk == FloorRow.pk,
            other_set.id > DrawingSetRow.id,
            other_set.status == "confirmed",
            other_set.deleted_at.is_(None),
            other_view.excluded.is_(False),
            other_view.target_mode.in_(_FLOOR_MODES),
        )
        .exists()
    )


async def _due(db: AsyncSession, batch: int) -> list[_Due]:
    """Các tầng tới lượt ráp, tối đa `batch` (xem bước 1 ở docstring module).

    Lượt chạy mới nhất tính theo **tầng** chứ không theo lượt tải của bộ: tầng được tải lại bản vẽ khác vẫn có mái
    bám theo tường mới. Điều kiện "bản đóng băng có mái cho hình này" là phép chứa JSONB, nên tầng không mang mái
    không bao giờ vào lô. Tầng mà một bộ mới hơn đã nhắm thì thuộc về bộ đó (`_newer_set_on_floor`).
    """
    run = (
        select(PipelineRunRow.id, PipelineRunRow.status)
        .where(PipelineRunRow.floor_pk == FloorRow.pk, PipelineRunRow.superseded_by.is_(None))
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
        .lateral("run")
    )
    roof_of_view = func.jsonb_build_array(func.jsonb_build_object("levelRef", DrawingSetViewRow.id))
    # Kèm `schemaVersion`: bản đóng băng `frozen_proposal` không đọc được thì không vào lô, thay vì bị khoá mỗi phút.
    frozen = func.jsonb_build_object(
        "schemaVersion", FROZEN_VERSION, "proposal", func.jsonb_build_object("roofs", roof_of_view)
    )
    has_roof = func.jsonb_build_object("confirmed", frozen)
    stmt = (
        select(DrawingSetRow.id, DrawingSetViewRow.id, FloorRow.pk)
        .select_from(DrawingSetRow)
        .join(Project, Project.id == DrawingSetRow.project_id)
        .join(DrawingSetViewRow, DrawingSetViewRow.set_id == DrawingSetRow.id)
        .join(UploadRow, UploadRow.id == DrawingSetViewRow.upload_id)
        .join(FloorRow, FloorRow.pk == UploadRow.floor_pk)
        .join(run, true())
        .outerjoin(FloorDocumentRow, FloorDocumentRow.floor_pk == FloorRow.pk)
        .where(
            DrawingSetRow.status == "confirmed",
            DrawingSetRow.deleted_at.is_(None),
            Project.deleted_at.is_(None),
            DrawingSetRow.building.contains(has_roof),
            DrawingSetViewRow.excluded.is_(False),
            DrawingSetViewRow.target_mode.in_(_FLOOR_MODES),
            FloorRow.deleted_at.is_(None),
            run.c.status.in_(FINAL_RUN_STATUSES),
            ~_newer_set_on_floor(),
            or_(
                _stored("runId").is_distinct_from(run.c.id),
                _stored("revision").is_distinct_from(sql_cast(func.coalesce(FloorDocumentRow.revision, 0), Text)),
                _stored("heightMm").is_distinct_from(sql_cast(FloorRow.height_mm, Text)),
            ),
        )
        .order_by(DrawingSetRow.id, DrawingSetViewRow.ord)
        .limit(batch)
    )
    return [_Due(*row) for row in (await db.execute(stmt)).all()]


def _envelope_refusal(summary: Mapping[str, object] | None) -> Refusal | None:
    """M-15: lượt chạy mang `OPEN_ENVELOPE`/`WALL_FRAGMENTS` → `ROOF_SHAPE_COMPLEX` (phòng của lượt đó không tin được).

    Lượt chạy **hỏng** không tự nó là lý do từ chối: nó không ghi lớp, nên tầng chưa từng có lớp ra `NO_WALLS` ở
    `build_roof` (không tường), còn tầng đang mang lớp tốt của lượt trước thì giữ mái — một lượt chạy lại bị huỷ hay
    quá hạn không được làm mất mái đang khớp tường.
    """
    checks = (summary or {}).get("criticalChecks")
    if isinstance(checks, list) and not _BAD_ENVELOPE.isdisjoint(code for code in checks if isinstance(code, str)):
        return "ROOF_SHAPE_COMPLEX"
    return None


def _millimetres(metres: JsonValue) -> float:
    """Một số đo (m) trong node Pascal → mm."""
    return float(cast("float", metres)) * 1000


def _ridge_hits(spec: RoofSpec, form: RoofForm, nodes: tuple[dict[str, JsonValue], ...]) -> tuple[RuleHit, ...]:
    """M-04: cốt đỉnh người duyệt nhập so với cốt đỉnh tính từ độ dốc trên hình vừa dựng (mặt cao nhất).

    Chỉ kiểm khi có cả cốt đỉnh và cốt diềm là số, và mái có dốc. Vẫn dựng theo độ dốc; vi phạm chỉ ghi vào kết quả
    ráp, nên một cốt không đọc nổi thành số chỉ làm mất phép so chứ không làm mất mái.
    """
    ridge, eave = spec.ridge_elevation_mm, spec.eave_elevation_mm
    if ridge is None or eave is None or form == "flat":
        return ()
    try:
        ridge_mm, eave_mm = float(ridge.value), float(eave.value)
    except ValueError:
        return ()
    pitch = float(spec.pitch_deg.value)
    rise = max(
        ridge_height_mm(form, _millimetres(node["width"]), _millimetres(node["depth"]), pitch) for node in nodes[1:]
    )
    hit = m04_ridge(spec.level_ref, round(ridge_mm), round(eave_mm + rise))
    return () if hit is None else (hit,)


def build_roof(
    layer: SpatialLayer, spec: RoofSpec, level: LevelSpec | None, *, own_id: str, level_id: str, height_mm: int
) -> tuple[Roof | Refusal, tuple[RuleHit, ...]]:
    """Mái AI của một tầng từ tường/phòng hiện tại và `RoofSpec` đã duyệt, hoặc lý do không dựng (M-01, M-12, M-15).

    Đường bao lấy từ các phòng có mái che, nới ra nửa bề dày tường bao (trung vị; không có tường bao thì mọi tường).
    Không tường hay không phòng → `NO_WALLS`. Phòng rời nhau, có lỗ, hình không phủ nổi bằng chữ nhật, mái bằng/một
    dốc trên nhiều chữ nhật, dạng lạ, hay thông số mà `roof_nodes`/`Roof` từ chối → `ROOF_SHAPE_COMPLEX`.
    Phần tính hình chạy đồng bộ: vài đa giác, cỡ mili giây (dưới ngưỡng cần `to_thread` của R-23).
    """
    envelope = [wall.thickness_mm for wall in layer.walls if wall.kind == "envelope"]
    thicknesses = envelope or [wall.thickness_mm for wall in layer.walls]
    if not thicknesses:
        return "NO_WALLS", ()
    thickness = statistics.median_low(thicknesses)
    rooms = [[(point.x, point.y) for point in room.outline] for room in layer.rooms if room.usage not in _UNROOFED]
    outline = roof_outline(rooms, thickness / 2)
    if isinstance(outline, str):
        return ("NO_WALLS" if outline == "no_rooms" else "ROOF_SHAPE_COMPLEX"), ()
    rects = roof_rects(outline)
    form = str(spec.form.value)
    if isinstance(rects, str) or form not in _FORMS or (form in _SINGLE_FACE and len(rects.rects) > 1):
        return "ROOF_SHAPE_COMPLEX", ()
    try:
        nodes = roof_nodes(
            rects,
            form=cast("RoofForm", form),
            pitch_deg=float(spec.pitch_deg.value),
            quarter_turns=int(spec.quarter_turns.value),
            eave_above_floor_mm=eave_above_floor_mm(spec, level, height_mm),
            overhang_mm=float(spec.overhang_mm.value),
            wall_thickness_mm=thickness,
            roof_id=own_id,
        )
        confidence = min(spec.form.confidence, spec.pitch_deg.confidence, spec.quarter_turns.confidence)
        roof = Roof(id=own_id, level_id=level_id, nodes=nodes, confidence=confidence, source="ai", reviewed=False)
    except ValueError as exc:
        _log.warning("drawing_set_roof_refused", extra={"level_id": level_id, "error": str(exc)[:200]})
        return "ROOF_SHAPE_COMPLEX", ()
    return roof, _ridge_hits(spec, cast("RoofForm", form), nodes)


async def _latest_run(db: AsyncSession, floor_pk: int) -> tuple[str, str, Mapping[str, object] | None] | None:
    """`(id, trạng thái, tóm tắt quality)` của lượt chạy mới nhất chưa bị thay của tầng; chưa có lượt nào → `None`."""
    stmt = (
        select(PipelineRunRow.id, PipelineRunRow.status, PipelineRunModelsRow.quality_summary)
        .outerjoin(PipelineRunModelsRow, PipelineRunModelsRow.run_id == PipelineRunRow.id)
        .where(PipelineRunRow.floor_pk == floor_pk, PipelineRunRow.superseded_by.is_(None))
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


async def _locked(db: AsyncSession, due: _Due) -> tuple[DrawingSetRow, FloorRow] | None:
    """Dòng bộ (`FOR UPDATE`, cũng là khoá của `building`) rồi dòng tầng (`FOR SHARE`); `None` khi bộ không còn
    `confirmed`, đã xoá, hoặc tầng đã xoá."""
    drawing_set = (
        await db.execute(
            select(DrawingSetRow)
            .where(
                DrawingSetRow.id == due.set_id,
                DrawingSetRow.status == "confirmed",
                DrawingSetRow.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    floor = (
        await db.execute(
            select(FloorRow)
            .where(FloorRow.pk == due.floor_pk, FloorRow.deleted_at.is_(None))
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    return None if drawing_set is None or floor is None else (drawing_set, floor)


async def _write_roofs(
    db: AsyncSession, floor_pk: int, document: FloorDocument, roofs: tuple[Roof, ...], clock: Clock
) -> int:
    """Ghi danh sách mái đã quyết lên lớp của tầng (đang giữ khoá tài liệu); trả `revision` sau lượt này.

    Danh sách không đổi thì không ghi, `revision` giữ nguyên (J06). `page_key` truyền đúng trang đang gắn tỉ lệ để
    `write_layer` không coi đây là lượt của bản vẽ khác (nó sẽ gỡ hết dấu duyệt của tầng).
    """
    if roofs == document.layer.roofs:
        return document.revision

    def merge(base: SpatialLayer, _scale: Decimal | None) -> _Merged:
        """Lớp đang có với danh sách mái đã quyết dưới khoá."""
        return _Merged(base.model_copy(update={"roofs": roofs}), {})

    written = await write_layer(
        db,
        floor_pk=floor_pk,
        base_revision=0,
        body=LayerWrite(None, None),
        actor_id=SYSTEM_PIPELINE,
        actor_name=_ACTOR_NAME,
        clock=clock,
        merge=merge,
        page_key=document.scale_page_key,
    )
    return written.revision


async def _assemble_floor(db: AsyncSession, due: _Due, clock: Clock) -> str:
    """Ráp một tầng trong giao dịch của `db`; trả mã kết quả, hoặc `SKIPPED` khi việc không còn (J06).

    Mọi thứ đọc lại **dưới khoá**: bộ, tầng (`_locked`), lượt chạy mới nhất đã xong, tài liệu tầng (`FOR UPDATE`). Giữ
    khoá tài liệu từ đây nên lớp mà `settle` xét cũng là lớp `_write_roofs` ghi lên: người dùng không ghi xen giữa được
    (M-11).
    """
    locked = await _locked(db, due)
    run = await _latest_run(db, due.floor_pk)
    if locked is None or run is None or run[1] not in FINAL_RUN_STATUSES:
        return "SKIPPED"
    drawing_set, floor = locked
    building: Mapping[str, Any] = drawing_set.building or {}
    frozen = frozen_proposal(building, drawing_set.id)
    spec = next((roof for roof in frozen.roofs if roof.level_ref == due.view_id), None) if frozen else None
    if frozen is None or spec is None:
        return "SKIPPED"
    stored: Mapping[str, Any] = building.get("assembly") or {}
    previous = FloorAssembly.model_validate(stored[due.view_id]) if due.view_id in stored else None

    created = await ensure_document(db, floor_pk=floor.pk, clock=clock)
    # `or created` chỉ là cầu kiểu tĩnh: tầng đang bị khoá FOR SHARE nên dòng tài liệu không biến mất giữa hai câu.
    document = await load_document(db, floor.pk, for_update=True) or created
    own_id = roof_id(floor.pk)
    level = next((item for item in frozen.levels if item.level_ref == due.view_id), None)
    refusal = _envelope_refusal(run[2])
    built, hits = (
        build_roof(document.layer, spec, level, own_id=own_id, level_id=floor.level_id, height_mm=floor.height_mm)
        if refusal is None
        else (refusal, ())
    )
    settled = settle(document.layer.roofs, own_id, built, previous)
    entry = FloorAssembly(
        level_id=floor.level_id,
        run_id=run[0],
        revision=await _write_roofs(db, floor.pk, document, settled.roofs, clock),
        height_mm=floor.height_mm,
        code=settled.code,
        fingerprint=settled.fingerprint,
        roof_ids=settled.roof_ids,
        user_removed=settled.user_removed,
        hits=hits if settled.code == "BUILT" else (),
    )
    assembly = {**stored, due.view_id: entry.model_dump(mode="json", by_alias=True)}
    drawing_set.building = {**building, "assembly": assembly}
    return settled.code


async def assemble_sets(
    sessionmaker: async_sessionmaker[AsyncSession], clock: Clock, *, batch: int = SWEEP_BATCH
) -> Counter[str]:
    """Lõi của `drawing_set.assemble` (tiêm được cho test): một lượt quét; trả số tầng theo mã kết quả.

    Mỗi tầng một giao dịch riêng (R-20: khoá và `write_layer` theo từng tầng, không gom được). Một tầng hỏng — lỗi
    có mã của `write_layer`, tài liệu tầng hỏng, kết quả lưu không đọc được — chỉ bị ghi log kèm vết rồi đếm `FAILED`;
    các tầng khác vẫn ráp, và tầng đó được thử lại ở chu kỳ sau vì bộ ba của nó chưa được lưu. Lỗi hạ tầng (DB) không
    bị bắt: lượt quét dừng, chu kỳ sau làm tiếp (R-16).
    """
    async with sessionmaker() as db:
        due = await _due(db, batch)
    done: Counter[str] = Counter()
    for item in due:
        try:
            async with session_scope(sessionmaker) as db:
                code = await _assemble_floor(db, item, clock)
        except (AppError, ValueError, DocumentCorruptError):
            _log.exception(
                "drawing_set_assemble_floor_failed", extra={"set_id": item.set_id, "floor_pk": item.floor_pk}
            )
            code = "FAILED"
        done[code] += 1
    return done
