"""Tác vụ ráp nhà `drawing_set.assemble` (NHA-07a, thiết kế §2.E, luật M-01/04/11/12/15/16) — J01, J06.

Postgres thật: gọi lõi `assemble_sets` với sessionmaker của test. Bộ `confirmed` được dựng thẳng bằng factory (bản
đóng băng `building.confirmed` đúng hình dạng N60 ghi), pipeline không chạy: tài liệu tầng và dòng lượt chạy được
đặt sẵn như sau khi pipeline xong. Người dùng ghi bằng `write_layer` thật (đường #35).
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets import assembly, jobs
from apps.api.drawing_sets.assembly import assemble_sets, build_roof, frozen_proposal
from apps.api.spatial_read.documents import FloorDocument, load_document
from apps.api.spatial_write.tests._helpers import log_rows, make_wall, write
from packages.core.clock import Clock, SystemClock
from packages.core.errors import SYSTEM_PIPELINE, VersionConflictError
from packages.core.ids import new_id
from packages.db.engine import GATE_CONNECT_TIMEOUT_S, create_engine, create_sessionmaker
from packages.db.models.auth import User
from packages.db.models.drawing_sets import DrawingSetRow
from packages.db.models.drawings import PipelineRunRow
from packages.db.models.floors import FloorRow
from packages.db.models.projects import Project
from packages.db.settings import DatabaseSettings, reset_database_settings_cache
from packages.domain.drawing_set.assembly import roof_id
from packages.domain.drawing_set.building import BuildingProposal, LevelSpec, Measured, RoofSpec
from packages.domain.spatial.model import Roof, Room, SpatialLayer
from packages.messaging.schedules import schedule_entries
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawing_sets import make_drawing_set, make_set_file, make_set_view
from packages.testing.factories.drawings import make_upload
from packages.testing.factories.floors import make_floor
from packages.testing.factories.pipeline_orchestrate import make_run_pins
from packages.testing.factories.projects import make_project
from packages.testing.factories.spatial import make_floor_document
from packages.testing.fixtures.clock import FakeClock

Maker = async_sessionmaker[AsyncSession]
HUMAN_ROOF = "RF-HUMAN00001"
LEVEL = "L-0000000001"
RECT = ((0, 0), (8000, 0), (8000, 6000), (0, 6000))
WIDER = ((0, 0), (9500, 0), (9500, 6000), (0, 6000))
L_SHAPE = ((0, 0), (8000, 0), (8000, 3000), (4000, 3000), (4000, 9000), (0, 9000))


@dataclass(frozen=True)
class _Plan:
    """Một tầng của bộ thử: đường bao phòng, dạng mái đã duyệt, trạng thái và mã quality của lượt chạy."""

    outline: Sequence[tuple[int, int]] = RECT
    form: str = "gable"
    run_status: str = "completed"
    checks: tuple[str, ...] = ()
    roof: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Floor:
    """Tầng đã dựng: hình mặt bằng đại diện (`levelRef`) và dòng tầng."""

    view_id: str
    row: FloorRow


@dataclass(frozen=True)
class _House:
    """Bộ `confirmed` đã commit, chủ dự án và các tầng theo thứ tự `plans`."""

    set_id: str
    project: Project
    user: User
    floors: tuple[_Floor, ...]


def _measured(value: float | str, source: str = "plan") -> Measured:
    """Một giá trị đo, độ tin 0,8."""
    return Measured.model_validate({"value": value, "confidence": 0.8, "source": source})


def _room(
    level_id: str,
    outline: Sequence[tuple[int, int]],
    *,
    usage: str = "bedroom",
    suffix: str = "1",
    name: str = "Phòng",
) -> Room:
    """Một phòng AI chưa duyệt theo đường bao cho trước."""
    return Room.model_validate(
        {
            "id": f"R-TESTROOM0{suffix}",
            "levelId": level_id,
            "name": name,
            "usage": usage,
            "outline": [{"x": x, "y": y} for x, y in outline],
            "areaM2": 0.0,
            "wallIds": [],
            "confidence": 0.8,
            "source": "ai",
            "reviewed": False,
        }
    )


def _layer(level_id: str, *rooms: Room, wall_id: str = "W-TESTWALL01") -> SpatialLayer:
    """Lớp một tường dày 200 mm và các phòng cho trước."""
    return SpatialLayer(walls=(make_wall(level_id, entity_id=wall_id),), openings=(), rooms=rooms, furniture=())


def _spec(view_id: str, plan: _Plan) -> RoofSpec:
    """`RoofSpec` đã duyệt của một tầng: 30°, diềm ở đỉnh tường 3000, độ đua 300 (mái bằng: 0)."""
    values: dict[str, Any] = {
        "level_ref": view_id,
        "form": _measured(plan.form),
        "pitch_deg": _measured(30.0),
        "quarter_turns": _measured(0, "convention"),
        "eave_elevation_mm": _measured(3000),
        "overhang_mm": _measured(0 if plan.form == "flat" else 300, "convention"),
    }
    return RoofSpec(**(values | dict(plan.roof)))


def _frozen(specs: Sequence[RoofSpec]) -> dict[str, Any]:
    """`building` như N60 ghi: bản đề xuất đóng băng mang các mái `specs`."""
    proposal = BuildingProposal(
        unit_system=_measured("metric"),
        floors=(),
        levels=tuple(
            LevelSpec(level_ref=spec.level_ref, elevation_mm=_measured(0), height_mm=_measured(3000)) for spec in specs
        ),
        roofs=tuple(specs),
        conflicts=(),
        violations=(),
    )
    return {"confirmed": {"schemaVersion": 1, "proposal": proposal.model_dump(mode="json", by_alias=True)}}


async def _add_run(db: AsyncSession, upload_id: str, floor_pk: int, plan: _Plan) -> str:
    """Một lượt chạy của tầng ở trạng thái `plan.run_status`, kèm mã quality nếu có."""
    run = PipelineRunRow(
        id=new_id("run", SystemClock()),
        upload_id=upload_id,
        floor_pk=floor_pk,
        status=plan.run_status,
        current_step="qualityCheck",
        progress_percent=100,
    )
    db.add(run)
    await db.flush()
    if plan.checks:
        await make_run_pins(db, run_id=run.id, quality_summary={"criticalChecks": list(plan.checks)})
    return run.id


async def _house(maker: Maker, clock: Clock, *plans: _Plan, **set_overrides: Any) -> _House:
    """Bộ `confirmed` với mỗi `plan` một tầng: tài liệu tầng (phòng theo `outline`), lượt tải, lượt chạy, hình có đích.

    `set_overrides` ghi đè cột của bộ (trạng thái, `building`, `deleted_at`…).
    """
    plans = plans or (_Plan(),)
    async with maker() as db:
        user = await make_user(db)
        project = await make_project(db, owner=user)
        drawing_set = await make_drawing_set(db, project=project, status="confirmed")
        file = await make_set_file(db, drawing_set=drawing_set, status="complete")
        floors: list[_Floor] = []
        for ord_, plan in enumerate(plans):
            floor = await make_floor(db, project=project)
            # id thực thể là duy nhất trong cả dự án (`floor_entity_ids`), nên mỗi tầng một hậu tố
            room = _room(floor.level_id, plan.outline, suffix=str(ord_ + 1))
            layer = _layer(floor.level_id, room, wall_id=f"W-TESTWALL1{ord_}")
            await make_floor_document(db, floor_pk=floor.pk, layer=layer, revision=1, clock=clock)
            upload = await make_upload(db, project=project, floor=floor, status="complete")
            await _add_run(db, upload.id, floor.pk, plan)
            view = await make_set_view(
                db,
                file=file,
                ord=ord_,
                kind="plan",
                kind_confidence=1.0,
                target_mode="newFloor",
                new_floor_id=floor.level_id,
                new_floor_name="Tầng",
                upload_id=upload.id,
            )
            floors.append(_Floor(view.id, floor))
        drawing_set.building = _frozen([_spec(item.view_id, plan) for item, plan in zip(floors, plans, strict=True)])
        for column, value in set_overrides.items():
            setattr(drawing_set, column, value)
        await db.commit()
        return _House(drawing_set.id, project, user, tuple(floors))


async def _document(maker: Maker, floor: _Floor) -> FloorDocument:
    """Tài liệu tầng đọc lại bằng session mới."""
    async with maker() as db:
        document = await load_document(db, floor.row.pk)
    assert document is not None
    return document


async def _assembly(maker: Maker, house: _House) -> dict[str, Any]:
    """`building.assembly` của bộ đọc lại bằng session mới (rỗng khi chưa ráp)."""
    async with maker() as db:
        building = (await db.execute(select(DrawingSetRow.building).where(DrawingSetRow.id == house.set_id))).scalar()
    return dict((building or {}).get("assembly") or {})


async def _human_write(maker: Maker, house: _House, floor: _Floor, clock: FakeClock, **changes: Any) -> None:
    """Người dùng ghi lớp của tầng (đường #35) với các danh sách trong `changes` thay cho lớp đang có."""
    document = await _document(maker, floor)
    async with maker() as db:
        await write(
            db,
            floor=floor.row,
            actor=house.user,
            clock=clock,
            base_revision=document.revision,
            layer=document.layer.model_copy(update=changes),
        )
        await db.commit()


def _hand_drawn(level_id: str) -> Roof:
    """Một mái do người vẽ, chưa duyệt."""
    return Roof(
        id=HUMAN_ROOF, level_id=level_id, nodes=({"type": "roof"},), confidence=1.0, source="human", reviewed=False
    )


# ---------------------------------------------------------------- drawing_set.assemble
async def test_assemble_drawing_sets__J01(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """Pipeline của tầng xong → tầng có đúng một mái AI chưa duyệt, id tất định, và kết quả ráp `BUILT` được lưu."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})

    document = await _document(db_sessionmaker, floor)
    (roof,) = document.layer.roofs
    assert (roof.id, roof.source, roof.reviewed, roof.edited) == (roof_id(floor.row.pk), "ai", False, False)
    assert (roof.level_id, roof.confidence) == (floor.row.level_id, 0.8)
    kinds = [(node["type"], node.get("roofType")) for node in roof.nodes]
    assert kinds == [("roof", None), ("roof-segment", "gable")]
    assert roof.nodes[0]["position"] == [4.0, 3.0, 3.0]  # tâm đường bao, diềm ở đỉnh tường 3000 mm
    assert (roof.nodes[1]["width"], roof.nodes[1]["depth"], roof.nodes[1]["pitch"]) == (8.2, 6.2, 30.0)
    entry = (await _assembly(db_sessionmaker, house))[floor.view_id]
    assert entry["code"] == "BUILT"
    assert (entry["levelId"], entry["revision"], entry["heightMm"]) == (floor.row.level_id, document.revision, 3000)
    assert (entry["roofIds"], entry["userRemoved"], entry["hits"]) == ([roof.id], False, [])
    async with db_sessionmaker() as db:
        assert {row.changed_by for row in await log_rows(db, floor.row.pk)} == {SYSTEM_PIPELINE}
        building = (await db.execute(select(DrawingSetRow.building).where(DrawingSetRow.id == house.set_id))).scalar()
    assert frozen_proposal(building, house.set_id) is not None  # bản đóng băng còn nguyên cạnh `assembly`


async def test_assemble_drawing_sets__J06(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """Chạy lại không đổi gì; bộ chưa xác nhận, đã xoá, không có bản đóng băng hay lượt chạy chưa xong → no-op."""
    house = await _house(db_sessionmaker, fake_clock)
    await assemble_sets(db_sessionmaker, fake_clock)
    before = await _document(db_sessionmaker, house.floors[0])
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()
    assert (await _document(db_sessionmaker, house.floors[0])).revision == before.revision

    idle = [
        await _house(db_sessionmaker, fake_clock, status="review"),
        await _house(db_sessionmaker, fake_clock, deleted_at=fake_clock.now()),
        await _house(db_sessionmaker, fake_clock, building=None),
        await _house(db_sessionmaker, fake_clock, building={"decisions": {}}),
        await _house(db_sessionmaker, fake_clock, _Plan(run_status="running")),
    ]
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()
    for other in idle:
        assert (await _document(db_sessionmaker, other.floors[0])).layer.roofs == ()
        assert await _assembly(db_sessionmaker, other) == {}


async def test_assemble_skips_floors_without_a_reviewed_roof_and_excluded_views(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """Chỉ tầng có `RoofSpec` trong bản đóng băng mới vào lô; tầng không mang mái không bị đọc hay ghi."""
    house = await _house(db_sessionmaker, fake_clock, _Plan(), _Plan())
    lower, upper = house.floors
    async with db_sessionmaker() as db:
        row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == house.set_id))).scalar_one()
        row.building = _frozen([_spec(upper.view_id, _Plan())])
        await db.commit()
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})
    assert (await _document(db_sessionmaker, lower)).layer.roofs == ()
    assert list(await _assembly(db_sessionmaker, house)) == [upper.view_id]


async def test_assemble_rebuilds_an_untouched_roof_when_walls_or_height_change(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """M-16: tường đổi (lớp ghi lại) hay chiều cao tầng đổi → mái AI chưa sửa được dựng lại, cùng id, không thêm mái."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    await assemble_sets(db_sessionmaker, fake_clock)
    first = (await _document(db_sessionmaker, floor)).layer.roofs[0]

    await _human_write(db_sessionmaker, house, floor, fake_clock, rooms=(_room(floor.row.level_id, WIDER),))
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})
    (second,) = (await _document(db_sessionmaker, floor)).layer.roofs
    assert (second.id, second.nodes[1]["width"], second.edited) == (first.id, 9.7, False)

    async with db_sessionmaker() as db:
        row = (await db.execute(select(FloorRow).where(FloorRow.pk == floor.row.pk))).scalar_one()
        row.height_mm = 3300
        await db.commit()
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})
    (third,) = (await _document(db_sessionmaker, floor)).layer.roofs
    assert third.nodes[0]["position"] == [4.75, 3.3, 3.0]
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()


async def test_assemble_never_overwrites_an_approved_roof(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """M-11 + M-16: mái máy dựng đã được người duyệt giữ nguyên khi tường đổi; kết quả chỉ ghi `ROOF_STALE`."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    await assemble_sets(db_sessionmaker, fake_clock)
    built = (await _document(db_sessionmaker, floor)).layer.roofs[0]
    approved = built.model_copy(update={"source": "human", "reviewed": True})
    await _human_write(db_sessionmaker, house, floor, fake_clock, roofs=(approved,))
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})  # chỉ lưu bộ ba mới

    await _human_write(db_sessionmaker, house, floor, fake_clock, rooms=(_room(floor.row.level_id, WIDER),))
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"ROOF_STALE": 1})
    (kept,) = (await _document(db_sessionmaker, floor)).layer.roofs
    assert (kept.nodes, kept.source, kept.reviewed) == (built.nodes, "human", True)
    entry = (await _assembly(db_sessionmaker, house))[floor.view_id]
    assert (entry["code"], entry["roofIds"]) == ("ROOF_STALE", [built.id])


async def test_assemble_leaves_a_floor_with_a_hand_drawn_roof_alone(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """M-11: tầng đã có mái người vẽ → không thêm mái AI, kết quả `ROOF_KEPT`, tài liệu tầng không tăng `revision`."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    drawn = _hand_drawn(floor.row.level_id)
    await _human_write(db_sessionmaker, house, floor, fake_clock, roofs=(drawn,))
    before = await _document(db_sessionmaker, floor)
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"ROOF_KEPT": 1})
    after = await _document(db_sessionmaker, floor)
    assert (after.layer.roofs, after.revision) == (before.layer.roofs, before.revision)


async def test_assemble_never_recreates_a_roof_the_user_removed(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """M-11: người xoá mái AI → `userRemoved`, không tạo lại — kể cả khi pipeline chạy lại trước lượt quét kế tiếp."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    await assemble_sets(db_sessionmaker, fake_clock)
    await _human_write(db_sessionmaker, house, floor, fake_clock, roofs=())
    async with db_sessionmaker() as db:  # pipeline chạy lại ngay sau khi người xoá
        runs = select(PipelineRunRow.upload_id).where(PipelineRunRow.floor_pk == floor.row.pk)
        upload_id = (await db.execute(runs)).scalars().first()
        assert upload_id is not None
        await _add_run(db, upload_id, floor.row.pk, _Plan())
        await db.commit()

    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"USER_REMOVED": 1})
    assert (await _document(db_sessionmaker, floor)).layer.roofs == ()
    entry = (await _assembly(db_sessionmaker, house))[floor.view_id]
    assert (entry["code"], entry["userRemoved"], entry["roofIds"]) == ("USER_REMOVED", True, [])

    await _human_write(db_sessionmaker, house, floor, fake_clock, rooms=(_room(floor.row.level_id, WIDER),))
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"USER_REMOVED": 1})
    assert (await _document(db_sessionmaker, floor)).layer.roofs == ()


async def test_assemble_one_bad_floor_does_not_block_the_others(
    db_sessionmaker: Maker, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """M-15: lượt mang `OPEN_ENVELOPE` → `ROOF_SHAPE_COMPLEX`; lượt chạy hỏng trên tầng còn lớp tốt vẫn có mái; tầng có
    kết quả lưu hỏng chỉ bị đếm `FAILED` kèm log; tầng lành cuối danh sách vẫn có mái."""
    house = await _house(
        db_sessionmaker,
        fake_clock,
        _Plan(run_status="failed"),
        _Plan(checks=("ROOMS_WITHOUT_DOOR", "OPEN_ENVELOPE")),
        _Plan(),
        _Plan(),
    )
    failed, leaky, broken, good = house.floors
    async with db_sessionmaker() as db:
        row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == house.set_id))).scalar_one()
        row.building = row.building | {"assembly": {broken.view_id: {"code": "KHONG-CO-MA-NAY"}}}
        await db.commit()

    done = await assemble_sets(db_sessionmaker, fake_clock)
    assert done == Counter({"BUILT": 2, "ROOF_SHAPE_COMPLEX": 1, "FAILED": 1})
    assert "drawing_set_assemble_floor_failed" in caplog.text
    roofs = [len((await _document(db_sessionmaker, floor)).layer.roofs) for floor in house.floors]
    assert roofs == [1, 0, 0, 1]
    stored = await _assembly(db_sessionmaker, house)
    assert (stored[failed.view_id]["code"], stored[leaky.view_id]["code"]) == ("BUILT", "ROOF_SHAPE_COMPLEX")
    assert (stored[broken.view_id], stored[good.view_id]["code"]) == ({"code": "KHONG-CO-MA-NAY"}, "BUILT")
    # tầng lỗi được thử lại ở chu kỳ sau; ba tầng đã có kết quả thì không
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"FAILED": 1})


async def test_assemble_keeps_the_roof_when_a_rerun_fails(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """Lượt chạy lại bị huỷ hay hỏng không ghi lớp: mái đang khớp tường giữ nguyên, tài liệu tầng không tăng `revision`;
    tầng chưa từng có tường thì `NO_WALLS`."""
    house = await _house(db_sessionmaker, fake_clock, _Plan(), _Plan(run_status="failed"))
    roofed, bare = house.floors
    await _human_write(db_sessionmaker, house, bare, fake_clock, walls=(), rooms=())  # lượt đầu hỏng: tầng trống
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1, "NO_WALLS": 1})
    before = await _document(db_sessionmaker, roofed)
    async with db_sessionmaker() as db:
        runs = select(PipelineRunRow.upload_id).where(PipelineRunRow.floor_pk == roofed.row.pk)
        upload_id = (await db.execute(runs)).scalars().one()
        await _add_run(db, upload_id, roofed.row.pk, _Plan(run_status="failed"))
        await db.commit()

    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})
    after = await _document(db_sessionmaker, roofed)
    assert (after.layer.roofs, after.revision) == (before.layer.roofs, before.revision)
    assert (await _document(db_sessionmaker, bare)).layer.roofs == ()


async def test_assemble_ignores_a_frozen_snapshot_of_another_version(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """Bản đóng băng khác `FROZEN_VERSION` không vào lô: không khoá, không ghi, không lặp lại mỗi phút."""
    house = await _house(db_sessionmaker, fake_clock)
    async with db_sessionmaker() as db:
        row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == house.set_id))).scalar_one()
        row.building = {"confirmed": row.building["confirmed"] | {"schemaVersion": 2}}
        await db.commit()
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()
    assert await _assembly(db_sessionmaker, house) == {}


async def test_assemble_respects_the_batch_limit(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """Lô có trần: phần còn lại sang lượt quét sau, không tầng nào bị bỏ đói."""
    house = await _house(db_sessionmaker, fake_clock, _Plan(), _Plan(), _Plan())
    assert await assemble_sets(db_sessionmaker, fake_clock, batch=2) == Counter({"BUILT": 2})
    assert await assemble_sets(db_sessionmaker, fake_clock, batch=2) == Counter({"BUILT": 1})
    assert len(await _assembly(db_sessionmaker, house)) == 3


async def test_assemble_only_the_newest_confirmed_set_roofs_a_shared_floor(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """Bộ mới nhắm tầng của bộ cũ với dạng mái khác: chỉ bộ mới ráp, thay mái AI chưa sửa của bộ cũ tại chỗ; hai bộ
    không thay mái của nhau mãi (lượt quét kế tiếp không còn việc)."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    await assemble_sets(db_sessionmaker, fake_clock)
    async with db_sessionmaker() as db:
        newer = await make_drawing_set(db, project=house.project, status="confirmed")
        file = await make_set_file(db, drawing_set=newer, status="complete")
        upload = await make_upload(db, project=house.project, floor=floor.row, status="complete")
        view = await make_set_view(
            db,
            file=file,
            kind="plan",
            kind_confidence=1.0,
            target_mode="floor",
            target_floor_pk=floor.row.pk,
            upload_id=upload.id,
        )
        newer.building = _frozen([_spec(view.id, _Plan(form="hip"))])
        await db.commit()
        newer_id = newer.id
    assert newer_id > house.set_id

    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})
    (roof,) = (await _document(db_sessionmaker, floor)).layer.roofs
    assert (roof.id, roof.nodes[1]["roofType"]) == (roof_id(floor.row.pk), "hip")
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()
    stored = await _assembly(db_sessionmaker, _House(newer_id, house.project, house.user, ()))
    assert stored[view.id]["code"] == "BUILT"


async def test_assemble_a_newer_set_without_a_roof_does_not_take_the_floor(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """Merge-review PR 121: bộ mới hơn cũng nhắm tầng nhưng không mang mái cho tầng đó (xác nhận không có `building`)
    không che bộ cũ: tường đổi thì mái AI của bộ cũ vẫn được dựng lại theo tường, không nằm lại mãi."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    await assemble_sets(db_sessionmaker, fake_clock)
    async with db_sessionmaker() as db:
        newer = await make_drawing_set(db, project=house.project, status="confirmed")
        file = await make_set_file(db, drawing_set=newer, status="complete")
        upload = await make_upload(db, project=house.project, floor=floor.row, status="complete")
        await make_set_view(
            db,
            file=file,
            kind="plan",
            kind_confidence=1.0,
            target_mode="floor",
            target_floor_pk=floor.row.pk,
            upload_id=upload.id,
        )
        await db.commit()
        assert newer.id > house.set_id

    await _human_write(db_sessionmaker, house, floor, fake_clock, rooms=(_room(floor.row.level_id, WIDER),))
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})
    (roof,) = (await _document(db_sessionmaker, floor)).layer.roofs
    assert roof.nodes[1]["width"] == 9.7
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()


async def test_assemble_two_sweeps_at_once_write_one_roof(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """Hai lượt quét chồng nhau (beat gửi lặp): lượt sau chờ khoá dòng bộ rồi thấy mái đã có — một mái, một lần ghi."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    first, second = await asyncio.gather(
        assemble_sets(db_sessionmaker, fake_clock), assemble_sets(db_sessionmaker, fake_clock)
    )
    assert first == second == Counter({"BUILT": 1})
    document = await _document(db_sessionmaker, floor)
    assert (len(document.layer.roofs), document.revision) == (1, 2)


async def test_assemble_waits_for_a_concurrent_human_write_and_keeps_their_roof(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """Ghi đồng thời với người dùng: lượt quét chờ khoá tài liệu tầng, rồi thấy mái người vừa vẽ và không ghi đè."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    document = await _document(db_sessionmaker, floor)
    drawn = _hand_drawn(floor.row.level_id)
    async with db_sessionmaker() as human:
        layer = document.layer.model_copy(update={"roofs": (drawn,)})
        await write(
            human, floor=floor.row, actor=house.user, clock=fake_clock, base_revision=document.revision, layer=layer
        )
        sweep = asyncio.create_task(assemble_sets(db_sessionmaker, fake_clock))
        await asyncio.sleep(0.2)
        assert not sweep.done()  # đang chờ khoá dòng tài liệu mà giao dịch của người giữ
        await human.commit()
    assert await sweep == Counter({"ROOF_KEPT": 1})
    assert (await _document(db_sessionmaker, floor)).layer.roofs == (drawn,)


async def test_a_stale_human_save_after_assembly_gets_the_usual_conflict(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """Người dùng lưu bằng bản cũ sau khi mái được ráp: 409 kèm thay đổi của máy (W20), mái AI không bị mất lặng lẽ."""
    house = await _house(db_sessionmaker, fake_clock)
    (floor,) = house.floors
    stale = await _document(db_sessionmaker, floor)
    await assemble_sets(db_sessionmaker, fake_clock)
    async with db_sessionmaker() as db:
        with pytest.raises(VersionConflictError) as caught:
            await write(
                db, floor=floor.row, actor=house.user, clock=fake_clock, base_revision=stale.revision, layer=stale.layer
            )
    assert {change.entity_type for change in caught.value.remote_changes} == {"roof"}


async def test_assemble_floor_skips_work_that_vanished_after_selection(
    db_sessionmaker: Maker, fake_clock: FakeClock
) -> None:
    """J06: giữa lúc chọn việc và lúc khoá, hình không còn mái, tầng mất, lượt chạy mới chưa xong hay bộ bị mở lại →
    `SKIPPED`, không ghi gì."""
    house = await _house(db_sessionmaker, fake_clock)
    running = await _house(db_sessionmaker, fake_clock, _Plan(run_status="running"))
    (floor,) = house.floors
    due = assembly._Due(house.set_id, floor.view_id, floor.row.pk)
    vanished = [
        replace(due, view_id="dsv_khong_co_mai"),
        replace(due, floor_pk=-1),
        assembly._Due(running.set_id, running.floors[0].view_id, running.floors[0].row.pk),
    ]
    async with db_sessionmaker() as db:
        for item in vanished:
            assert await assembly._assemble_floor(db, item, fake_clock) == "SKIPPED"
        await db.rollback()
        row = (await db.execute(select(DrawingSetRow).where(DrawingSetRow.id == house.set_id))).scalar_one()
        row.status = "review"
        await db.commit()
        assert await assembly._assemble_floor(db, due, fake_clock) == "SKIPPED"
    assert (await _document(db_sessionmaker, floor)).layer.roofs == ()
    assert await _assembly(db_sessionmaker, house) == {}


# ---------------------------------------------------------------- build_roof (không DB)
def _built(layer: SpatialLayer, plan: _Plan) -> tuple[Roof | str, tuple[Any, ...]]:
    """`build_roof` cho tầng `L-0000000001` cao 3000 với `RoofSpec` của `plan`."""
    level = LevelSpec(level_ref="V1", elevation_mm=_measured(0), height_mm=_measured(3000))
    return build_roof(layer, _spec("V1", plan), level, own_id=roof_id(1), level_id="L-0000000001", height_mm=3000)


def test_build_roof_covers_an_l_shape_with_two_gable_faces() -> None:
    """Đường bao chữ L: hai mặt mái hai dốc trong một mái; phòng loggia không tính vào đường bao."""
    loggia = _room(LEVEL, ((8000, 0), (12000, 0), (12000, 3000), (8000, 3000)), usage="loggia", suffix="2")
    roof, hits = _built(_layer(LEVEL, _room(LEVEL, L_SHAPE), loggia), _Plan())
    assert isinstance(roof, Roof)
    assert [node["type"] for node in roof.nodes] == ["roof", "roof-segment", "roof-segment"]
    assert hits == ()


@pytest.mark.parametrize(
    ("name", "width_m"),
    [
        ("Sân thượng", 8.2),
        ("BAN CÔNG 2", 8.2),
        ("Roof terrace", 8.2),
        ("Balcony", 8.2),
        ("Patio", 8.2),
        ("Phòng ngủ", 12.2),
        ("Sân", 12.2),  # thiết kế chỉ nêu năm tên trên; "sân" một mình còn là sân trong có mái
    ],
)
def test_build_roof_leaves_terraces_and_balconies_unroofed(name: str, width_m: float) -> None:
    """Thiết kế §2.C Câu 5 bước 1: phòng tên sân thượng/ban công không có mái che dù `usage` không phải loggia."""
    beside = _room(LEVEL, ((8000, 0), (12000, 0), (12000, 6000), (8000, 6000)), name=name, suffix="2")
    roof, _ = _built(_layer(LEVEL, _room(LEVEL, RECT), beside), _Plan())
    assert isinstance(roof, Roof)
    assert roof.nodes[1]["width"] == width_m


@pytest.mark.parametrize(
    ("layer", "plan", "expected"),
    [
        (SpatialLayer(walls=(), openings=(), rooms=(_room(LEVEL, RECT),), furniture=()), _Plan(), "NO_WALLS"),
        (_layer(LEVEL), _Plan(), "NO_WALLS"),
        (_layer(LEVEL, _room(LEVEL, RECT, usage="loggia")), _Plan(), "NO_WALLS"),
        (_layer(LEVEL, _room(LEVEL, L_SHAPE)), _Plan(form="flat"), "ROOF_SHAPE_COMPLEX"),
        (_layer(LEVEL, _room(LEVEL, L_SHAPE)), _Plan(form="shed"), "ROOF_SHAPE_COMPLEX"),
        (
            _layer(LEVEL, _room(LEVEL, RECT), _room(LEVEL, ((20000, 0), (24000, 0), (24000, 4000)), suffix="2")),
            _Plan(),
            "ROOF_SHAPE_COMPLEX",
        ),
        (_layer(LEVEL, _room(LEVEL, ((0, 0), (9000, 0), (0, 7000)))), _Plan(), "ROOF_SHAPE_COMPLEX"),
        (_layer(LEVEL, _room(LEVEL, RECT)), _Plan(form="mansard"), "ROOF_SHAPE_COMPLEX"),
        (_layer(LEVEL, _room(LEVEL, RECT)), _Plan(roof={"pitch_deg": _measured(89.0)}), "ROOF_SHAPE_COMPLEX"),
        (_layer(LEVEL, _room(LEVEL, RECT)), _Plan(roof={"pitch_deg": _measured("dốc")}), "ROOF_SHAPE_COMPLEX"),
    ],
    ids=[
        "khong-tuong",
        "khong-phong",
        "chi-loggia",
        "mai-bang-chu-L",
        "mot-doc-chu-L",
        "phong-roi",
        "tam-giac",
        "dang-la",
        "doc-qua-tran",
        "doc-khong-phai-so",
    ],
)
def test_build_roof_refuses_floors_it_cannot_roof(layer: SpatialLayer, plan: _Plan, expected: str) -> None:
    """M-01/M-15: không tường, không phòng có mái che → `NO_WALLS`; hình hay thông số không dựng nổi → vẽ tay."""
    assert _built(layer, plan) == (expected, ())


def test_build_roof_uses_the_envelope_wall_thickness_and_a_flat_roof_has_no_overhang() -> None:
    """Đường bao nới nửa bề dày tường **bao** (300 → 150 mm mỗi phía), không phải tường ngăn; mái bằng không đua."""
    thick = make_wall(LEVEL, entity_id="W-TESTWALL02", thickness=300).model_copy(update={"kind": "envelope"})
    layer = SpatialLayer(walls=(make_wall(LEVEL), thick), openings=(), rooms=(_room(LEVEL, RECT),), furniture=())
    roof, _ = _built(layer, _Plan(form="flat"))
    assert isinstance(roof, Roof)
    segment = roof.nodes[1]
    assert (segment["roofType"], segment["width"], segment["depth"]) == ("flat", 8.3, 6.3)
    assert (segment["overhang"], segment["wallThickness"]) == (0.0, 0.3)


@pytest.mark.parametrize(
    ("ridge_mm", "hits"),
    [
        (4790, 0),  # tính được: 3000 + 3100·tan30° = 4790
        (5090, 0),  # lệch đúng 300 mm: còn trong dung sai
        (5200, 1),
    ],
)
def test_m04_records_a_ridge_that_disagrees_with_the_pitch(ridge_mm: int, hits: int) -> None:
    """M-04: cốt đỉnh người duyệt nhập lệch quá 300 mm so với đỉnh tính từ độ dốc → vẫn dựng, ghi vi phạm kèm hai số."""
    plan = _Plan(roof={"ridge_elevation_mm": _measured(ridge_mm, "reviewer")})
    roof, found = _built(_layer(LEVEL, _room(LEVEL, RECT)), plan)
    assert isinstance(roof, Roof)
    assert len(found) == hits
    if found:
        assert (found[0].code, found[0].level, found[0].values) == ("M-04", "warning", (5200, 4790))


def test_m04_is_silent_without_a_ridge_or_on_a_flat_roof() -> None:
    """Không có cốt đỉnh, không có cốt diềm, mái bằng, hoặc cốt đỉnh không phải số: không có gì để so."""
    layer = _layer(LEVEL, _room(LEVEL, RECT))
    ridge = {"ridge_elevation_mm": _measured(9000, "reviewer")}
    assert _built(layer, _Plan())[1] == ()
    assert _built(layer, _Plan(roof=ridge | {"eave_elevation_mm": None}))[1] == ()
    assert _built(layer, _Plan(form="flat", roof=ridge))[1] == ()
    unread, found = _built(layer, _Plan(roof={"ridge_elevation_mm": _measured("cao", "reviewer")}))
    assert isinstance(unread, Roof)  # cốt không phải số chỉ mất phép so, mái vẫn dựng
    assert found == ()


async def test_m04_hit_is_stored_with_the_assembly_result(db_sessionmaker: Maker, fake_clock: FakeClock) -> None:
    """Vi phạm lúc ráp ra `building.assembly[…].hits` dạng `RuleHit` (mã, mức, hình, số liệu)."""
    plan = _Plan(roof={"ridge_elevation_mm": _measured(5200, "reviewer")})
    house = await _house(db_sessionmaker, fake_clock, plan)
    await assemble_sets(db_sessionmaker, fake_clock)
    view_id = house.floors[0].view_id
    entry = (await _assembly(db_sessionmaker, house))[view_id]
    assert entry["hits"] == [{"code": "M-04", "level": "warning", "floorRefs": [view_id], "values": [5200, 4790]}]


# ---------------------------------------------------------------- phần phụ
@pytest.mark.parametrize(
    "building",
    [
        None,
        {},
        {"confirmed": {"schemaVersion": 2, "proposal": {}}},
        {"confirmed": {"proposal": {}}},
        {"confirmed": "rac"},
        {"confirmed": {"schemaVersion": 1, "proposal": {"roofs": "rac"}}},
    ],
)
def test_frozen_proposal_treats_an_unreadable_snapshot_as_absent(building: Any) -> None:
    """Không có, sai `schemaVersion`, hay không còn khớp mô hình → `None` (tác vụ bỏ qua bộ, route đọc tính lại)."""
    assert frozen_proposal(building, "dws_x") is None


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (None, None),
        ({}, None),
        ({"criticalChecks": ["ROOMS_WITHOUT_DOOR"]}, None),
        ({"criticalChecks": ["WALL_FRAGMENTS"]}, "ROOF_SHAPE_COMPLEX"),
        ({"criticalChecks": "OPEN_ENVELOPE"}, None),  # hỏng dạng: không phải danh sách
        ({"criticalChecks": [1, None, "OPEN_ENVELOPE"]}, "ROOF_SHAPE_COMPLEX"),
    ],
)
def test_envelope_refusal_reads_the_quality_checks_of_the_run(summary: Any, expected: str | None) -> None:
    """M-15: hai mã kiểm bao che làm phòng không tin được; mọi thứ khác để hình học tự quyết."""
    assert assembly._envelope_refusal(summary) == expected


def test_schedule_is_registered_once_a_minute() -> None:
    """Lịch `default.drawing_set.assemble` chạy mỗi 60 giây (sàn của `periodic`) trên hàng `default`."""
    entry = next(item for item in schedule_entries() if item.name == assembly.ASSEMBLE_TASK)
    assert (entry.every.total_seconds(), entry.function) == (60, jobs.assemble_drawing_sets.__name__)


async def _on_fresh_engine[ResultT](db_url: str, work: Callable[[Maker], Awaitable[ResultT]]) -> ResultT:
    """Chạy `work` trên engine riêng của vòng `asyncio.run` hiện tại (test khói là test đồng bộ)."""
    engine = create_engine(DatabaseSettings(database_url=db_url, db_connect_timeout_s=int(GATE_CONNECT_TIMEOUT_S)))
    try:
        return await work(create_sessionmaker(engine))
    finally:
        await engine.dispose()


def test_assemble_drawing_sets_smoke(
    db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test khói: gọi chính hàm lịch (giờ thật, `worker_sessionmaker()`); lượt đầu dựng mái và ghi log, lượt hai im."""
    house = asyncio.run(_on_fresh_engine(db_url, lambda maker: _house(maker, SystemClock())))
    monkeypatch.setenv("DATABASE_URL", db_url)
    reset_database_settings_cache()
    caplog.set_level(logging.INFO, logger="apps.api.drawing_sets.jobs")
    try:
        jobs.assemble_drawing_sets()
        jobs.assemble_drawing_sets()
    finally:
        reset_database_settings_cache()
    document = asyncio.run(_on_fresh_engine(db_url, lambda maker: _document(maker, house.floors[0])))
    assert len(document.layer.roofs) == 1
    logged = [record.__dict__["floors"] for record in caplog.records if record.msg == "drawing_sets_assembled"]
    assert logged == [{"BUILT": 1}]
