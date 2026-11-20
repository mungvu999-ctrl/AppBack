"""`read_house_proposal` (HS-02): đề xuất cả nhà từ `text.json` của lượt mới nhất, một câu SQL, không lỗi cho client.

Dữ liệu dựng thật (DB + kho trên đĩa): tầng, lượt `completed`, bản vẽ và artifact `dimensionReading/text.json`.
"""

import asyncio
import logging
import threading
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawing_sets import service
from apps.api.drawing_sets.service import HouseProposalRead, order_differs_of, read_house_proposal
from apps.api.drawings.runs import start_run
from apps.api.projects.tests.sql_count import count_sql
from apps.api.spatial_read import detected
from apps.api.spatial_read.tests._helpers import Scene, make_scene
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.db.models.drawings import PipelineRunRow
from packages.db.models.floors import FloorRow
from packages.ml_contracts.artifacts import BoxPx, TextPx, TextResult, text_to_json
from packages.storage import keys
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.drawings import make_complete_upload, make_drawing, png_bytes
from packages.testing.fixtures.clock import FakeClock


def _box(x_min: float, y_min: float, x_max: float, y_max: float) -> BoxPx:
    return BoxPx(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)


def _texts(title: str, *marks: str) -> TextResult:
    """Chữ một mặt bằng: vài nhãn nhỏ làm nền trung vị, các cốt, và tên hình cao ở giữa dưới trang 2000 x 1200."""
    items = [TextPx(text=f"P{i}", box=_box(100 + 90 * i, 100, 160 + 90 * i, 130), confidence=0.9) for i in range(6)]
    items += [
        TextPx(text=m, box=_box(100 + 200 * i, 300, 220 + 200 * i, 330), confidence=0.9) for i, m in enumerate(marks)
    ]
    items.append(TextPx(text=title, box=_box(700, 1000, 1300, 1080), confidence=0.9))
    return TextResult(items=tuple(items))


async def _seed_run(
    db: AsyncSession,
    storage: LocalDiskStorage,
    clock: FakeClock,
    scene: Scene,
    floor: FloorRow,
    *,
    body: bytes | None,
    drawing: bool = True,
    status: str = "completed",
) -> str:
    """Lượt tải + lượt chạy `status` của tầng, bản vẽ 2000 x 1200 (tuỳ chọn), `text.json` = `body` (nếu có); đã commit.

    Mỗi tầng chỉ có một bản vẽ (`uq_drawings_floor_pk`), nên lượt thứ hai của cùng tầng phải đặt `drawing=False`.
    """
    upload = await make_complete_upload(db, storage, project=scene.project, floor=floor, data=png_bytes(10, 10))
    if drawing:
        await make_drawing(db, storage, upload=upload, png=png_bytes(2000, 1200))
    run = await start_run(db, upload_id=upload.id, clock=clock)
    await db.execute(update(PipelineRunRow).where(PipelineRunRow.id == run.id).values(status=status))
    if body is not None:
        key = keys.run_artifact(scene.project.id, floor.level_id, upload.id, run.id, "dimensionReading", "text.json")
        await storage.put(key, body, content_type="application/json", max_bytes=len(body))
    await db.commit()
    return run.id


async def _seed_titles(
    db: AsyncSession, storage: LocalDiskStorage, clock: FakeClock, scene: Scene, specs: list[tuple[str, str]]
) -> list[str]:
    """Mỗi tầng của `scene` (theo thứ tự) một lượt `completed` với (tên hình, cốt) tương ứng; trả id lượt."""
    return [
        await _seed_run(db, storage, clock, scene, floor, body=text_to_json(_texts(title, mark)))
        for floor, (title, mark) in zip(scene.floors, specs, strict=True)
    ]


async def test_drawing_sets_read_house_proposal__three_floors(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Ba tầng có tên và cốt → tầng 1-2-3, cốt 0/3300/6600, đúng thứ tự tầng, không lệch thứ tự."""
    scene = await make_scene(db_session, floors=3)
    run_ids = await _seed_titles(
        db_session,
        local_storage,
        fake_clock,
        scene,
        [("MẶT BẰNG TẦNG 1", "±0.000"), ("MẶT BẰNG TẦNG 2", "+3.300"), ("MẶT BẰNG TẦNG 3", "+6.600")],
    )
    ids = [f.level_id for f in scene.floors]  # `rollback` của service làm hết hạn đối tượng ORM
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert isinstance(got, HouseProposalRead)
    assert [f.floor_id for f in got.proposal.floors] == ids
    assert [f.storey_no for f in got.proposal.floors] == [1, 2, 3]
    assert [f.elevation_mm for f in got.proposal.floors] == [0, 3300, 6600]
    assert [f.height_mm for f in got.proposal.floors][:2] == [3300, 3300]
    assert got.run_ids == tuple(run_ids)
    assert got.order_differs == frozenset()


async def test_drawing_sets_read_house_proposal__floor_without_run(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Tầng chưa có lượt vẫn ra (NO_TEXT), không vào `run_ids`; lượt chưa `completed` cũng không tính."""
    scene = await make_scene(db_session, floors=3)
    body = text_to_json(_texts("MẶT BẰNG TẦNG 1", "±0.000"))
    first = await _seed_run(db_session, local_storage, fake_clock, scene, scene.floors[0], body=body)
    await _seed_run(db_session, local_storage, fake_clock, scene, scene.floors[2], body=body, status="running")
    ids = [f.level_id for f in scene.floors]  # `rollback` của service làm hết hạn đối tượng ORM
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert [f.floor_id for f in got.proposal.floors] == ids
    assert got.proposal.floors[1].questions == ("NO_TEXT",)
    assert got.proposal.floors[2].questions == ("NO_TEXT",)
    assert got.proposal.floors[0].storey_no == 1
    assert got.run_ids == (first,)


@pytest.mark.parametrize(
    "body",
    [None, b"{khong phai json", b'{"schemaVersion": 2, "items": []}'],
    ids=["missing", "broken", "wrong-contract"],
)
async def test_drawing_sets_read_house_proposal__unreadable_artifact(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock, body: bytes | None
) -> None:
    """Artifact vắng, hỏng hay sai hợp đồng: tầng đó NO_TEXT, tầng kia vẫn đọc được, không ném lỗi."""
    scene = await make_scene(db_session, floors=2)
    good = await _seed_run(
        db_session,
        local_storage,
        fake_clock,
        scene,
        scene.floors[0],
        body=text_to_json(_texts("MẶT BẰNG TẦNG 1", "±0.000")),
    )
    await _seed_run(db_session, local_storage, fake_clock, scene, scene.floors[1], body=body)
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert got.proposal.floors[1].questions == ("NO_TEXT",)
    assert got.proposal.floors[1].storey_no is None
    assert got.proposal.floors[0].storey_no == 1
    assert got.run_ids == (good,)


async def test_drawing_sets_read_house_proposal__no_drawing_row(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Chưa có bản vẽ (không biết cỡ trang): lấy hộp bao của chữ, không chia cho 0, vẫn ra tầng."""
    scene = await make_scene(db_session, floors=1)
    await _seed_run(
        db_session,
        local_storage,
        fake_clock,
        scene,
        scene.floors[0],
        body=text_to_json(_texts("MẶT BẰNG TẦNG 1", "±0.000")),
        drawing=False,
    )
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert got.proposal.floors[0].storey_no == 1


async def test_drawing_sets_read_house_proposal__empty_text_list(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """`text.json` hợp lệ nhưng không có dòng nào → NO_TEXT và lượt không vào `run_ids`."""
    scene = await make_scene(db_session, floors=1)
    await _seed_run(
        db_session, local_storage, fake_clock, scene, scene.floors[0], body=text_to_json(TextResult(items=()))
    )
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert got.proposal.floors[0].questions == ("NO_TEXT",)
    assert got.run_ids == ()


async def test_drawing_sets_read_house_proposal__newest_run_wins(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Hai lượt `completed` của một tầng: chữ của lượt mới hơn được dùng."""
    scene = await make_scene(db_session, floors=1)
    floor = scene.floors[0]
    old = text_to_json(_texts("MẶT BẰNG TẦNG 1", "±0.000"))
    await _seed_run(db_session, local_storage, fake_clock, scene, floor, body=old)
    fake_clock.advance(timedelta(minutes=5))
    new = text_to_json(_texts("MẶT BẰNG TẦNG 2", "+3.300"))
    newer = await _seed_run(db_session, local_storage, fake_clock, scene, floor, body=new, drawing=False)
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert got.run_ids == (newer,)
    assert got.proposal.floors[0].storey_no == 2


async def test_drawing_sets_read_house_proposal__order_differs(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Tầng tạo sai thứ tự (1, 3, 2): hai tầng của cặp ngược bị cờ, tầng 1 thì không."""
    scene = await make_scene(db_session, floors=3)
    await _seed_titles(
        db_session,
        local_storage,
        fake_clock,
        scene,
        [("MẶT BẰNG TẦNG 1", "±0.000"), ("MẶT BẰNG TẦNG 3", "+6.600"), ("MẶT BẰNG TẦNG 2", "+3.300")],
    )
    ids = [f.level_id for f in scene.floors]  # `rollback` của service làm hết hạn đối tượng ORM
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert [f.storey_no for f in got.proposal.floors] == [1, 3, 2]
    assert got.order_differs == {ids[1], ids[2]}


async def test_drawing_sets_read_house_proposal__empty_project(
    db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Dự án chưa có tầng: đề xuất rỗng, không lỗi."""
    scene = await make_scene(db_session, floors=0)
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert got.proposal.floors == ()
    assert (got.run_ids, got.order_differs) == ((), frozenset())


async def test_drawing_sets_read_house_proposal__deleted_floor_is_ignored(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Tầng đã xoá mềm không vào đề xuất."""
    scene = await make_scene(db_session, floors=2)
    await db_session.execute(
        update(FloorRow).where(FloorRow.pk == scene.floors[1].pk).values(deleted_at=fake_clock.now())
    )
    await db_session.commit()
    ids = [f.level_id for f in scene.floors]  # `rollback` của service làm hết hạn đối tượng ORM
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert [f.floor_id for f in got.proposal.floors] == [ids[0]]


async def test_drawing_sets_read_house_proposal__query_count_constant(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """R-20: số câu SQL không đổi khi số tầng tăng từ 2 lên 6 (một câu cho cả bộ)."""
    counts: list[int] = []
    for floors in (2, 6):
        scene = await make_scene(db_session, floors=floors)
        await _seed_titles(
            db_session,
            local_storage,
            fake_clock,
            scene,
            [(f"MẶT BẰNG TẦNG {i + 1}", f"+{3 * i}.000") for i in range(floors)],
        )
        with count_sql() as counter:
            await read_house_proposal(db_session, local_storage, scene.project.id)
        counts.append(counter.count)
    assert counts == [1, 1]


async def test_drawing_sets_read_house_proposal__connection_released_before_reads(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K36: lúc đọc artifact, phiên không còn giao dịch (kết nối đã trả pool)."""
    scene = await make_scene(db_session, floors=2)
    await _seed_titles(db_session, local_storage, fake_clock, scene, [("MẶT BẰNG TẦNG 1", "±0.000")] * 2)
    in_tx: list[bool] = []
    real_read = detected.read_artifact

    async def spy(*args: Any, **kwargs: Any) -> bytes | None:
        """Ghi trạng thái giao dịch rồi đọc thật."""
        in_tx.append(db_session.in_transaction())
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(service, "read_artifact", spy)
    await read_house_proposal(db_session, local_storage, scene.project.id)
    assert in_tx == [False, False]


async def test_drawing_sets_read_house_proposal__oversized_artifact(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Artifact vượt trần bộ đọc → tầng NO_TEXT, một dòng log cho tầng đó, không ném lỗi."""
    scene = await make_scene(db_session, floors=1)
    body = text_to_json(_texts("MẶT BẰNG TẦNG 1", "±0.000"))
    await _seed_run(db_session, local_storage, fake_clock, scene, scene.floors[0], body=body)
    monkeypatch.setattr(detected, "ARTIFACT_JSON_MAX_BYTES", len(body) - 1)
    with caplog.at_level(logging.WARNING, logger="apps.api.drawing_sets.service"):
        got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert got.proposal.floors[0].questions == ("NO_TEXT",)
    assert [r.message for r in caplog.records if r.name == service.__name__] == ["house_proposal_text_missing"]


async def test_drawing_sets_read_blobs__storage_failure_cancels_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lỗi kho đầu tiên lan nguyên (không `ExceptionGroup`) và các lượt đọc còn lại bị huỷ."""
    cancelled: list[str] = []

    async def fake_read(storage: object, *, floor_id: str, **_: object) -> bytes | None:
        """Tầng `bad` lỗi ngay; các tầng khác chờ mãi tới khi bị huỷ."""
        if floor_id == "bad":
            raise DEPENDENCY_UNAVAILABLE.error(retry_after=1)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(floor_id)
            raise
        return None

    monkeypatch.setattr(service, "read_artifact", fake_read)
    sources = [service._FloorSource(fid, ("run", "upl"), None) for fid in ("a", "b", "bad")]
    with pytest.raises(AppError) as caught:
        await service._read_blobs(object(), "prj", sources)  # type: ignore[arg-type]
    assert caught.value.code is DEPENDENCY_UNAVAILABLE
    assert sorted(cancelled) == ["a", "b"]


async def test_drawing_sets_read_house_proposal__deadline(
    db_session: AsyncSession, local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đọc + giải quá `DEADLINE_S` → 503 `DEPENDENCY_UNAVAILABLE` có `Retry-After`, không nửa kết quả."""
    scene = await make_scene(db_session, floors=1)

    async def slow_read(*_: object, **__: object) -> bytes | None:
        await asyncio.sleep(30)
        return None

    async def one_source(*_: object) -> list[Any]:
        return [service._FloorSource(scene.floors[0].level_id, ("run", "upl"), None)]

    monkeypatch.setattr(service, "_load_sources", one_source)
    monkeypatch.setattr(service, "read_artifact", slow_read)
    monkeypatch.setattr(service, "DEADLINE_S", 0.05)
    with pytest.raises(AppError) as caught:
        await read_house_proposal(db_session, local_storage, scene.project.id)
    assert (caught.value.code, caught.value.retry_after) == (DEPENDENCY_UNAVAILABLE, service.RETRY_AFTER_S)


async def test_drawing_sets_read_house_proposal__solver_slots(
    db_session: AsyncSession, local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hai luồng giải đang bận: request thứ ba quá hạn trong hàng chờ và không bao giờ được chạy."""
    scene = await make_scene(db_session, floors=1)
    project_id = scene.project.id  # `rollback` của service làm hết hạn đối tượng ORM
    release, started = threading.Event(), []

    def blocked_solve(*_: object) -> None:
        started.append(1)
        release.wait(10)

    monkeypatch.setattr(service, "_solve", blocked_solve)
    monkeypatch.setattr(service, "DEADLINE_S", 0.2)
    try:
        for _ in range(3):
            with pytest.raises(AppError) as caught:
                await read_house_proposal(db_session, local_storage, project_id)
            assert caught.value.code is DEPENDENCY_UNAVAILABLE
        assert len(started) == 2
    finally:
        release.set()


async def test_drawing_sets_read_house_proposal__max_floors(
    db_session: AsyncSession, local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quá `MAX_FLOORS`: chỉ các tầng đầu theo `floor_order` vào phản hồi, phần còn lại bị bỏ."""
    assert service.MAX_FLOORS == 200
    scene = await make_scene(db_session, floors=3)
    monkeypatch.setattr(service, "MAX_FLOORS", 2)
    ids = [f.level_id for f in scene.floors]  # `rollback` của service làm hết hạn đối tượng ORM
    got = await read_house_proposal(db_session, local_storage, scene.project.id)
    assert [f.floor_id for f in got.proposal.floors] == ids[:2]


@pytest.mark.parametrize(
    ("storeys", "expected"),
    [
        ([], set()),
        ([("a", 1), ("b", 2), ("c", 3)], set()),
        ([("a", 1), ("b", 1)], set()),
        ([("a", 2), ("b", 1)], {"a", "b"}),
        ([("a", 1), ("b", None), ("c", 3), ("d", 2)], {"c", "d"}),
        ([("a", None), ("b", None)], set()),
    ],
    ids=["empty", "ascending", "equal", "reversed", "unnumbered-skipped", "none-only"],
)
def test_drawing_sets_order_differs_of__cases(storeys: list[tuple[str, int | None]], expected: set[str]) -> None:
    """Cặp ngược cờ cả hai đầu; số bằng nhau và tầng không có số không mâu thuẫn."""
    assert order_differs_of(storeys) == frozenset(expected)
