"""N19 `POST .../versions/{id}/restore` — GV: C01 C02 C03 C06 C07 C08 C09 C09b C14 C17 + C18 (B3-04 [8]).

Chỉ phần **nghiệp vụ phục hồi** đi qua HTTP thật: mọi trạng thái đầu vào dựng bằng `write_layer` +
`create_version` thật rồi `commit` (route mở session riêng), mọi khẳng định "đã lưu" đọc lại bằng
N16 hay một truy vấn mới — không mock session, không mock `write_layer` (K22, K23). Hai người
song song dùng **hai request thật** trên `api_client` (mỗi request một giao dịch Postgres).

Trạng thái mẫu: lớp `A` (tường dày 200) ở `revision 1` (bản v1), `B` (250) ở 2, `C` (300) ở 3.
"""

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.spatial_read.documents import load_document
from apps.api.spatial_write.tests._helpers import (
    WALL_ID,
    log_count,
    log_rows,
    make_dimension,
    make_wall,
    simple_layer,
)
from apps.api.spatial_write.tests._route_helpers import layer_path, wire
from apps.api.versions.messages import after_note, before_note
from apps.api.versions.tests._helpers import keep_snapshots, versions_of
from apps.api.versions.tests._route_helpers import (
    SNAPSHOT_LOGGER,
    Corruption,
    Stage,
    call,
    commit_snap,
    corrupt_snapshot,
    list_versions,
    make_stage,
    mismatch_logged,
    put_layer,
    relabel,
    restore,
    restore_body,
    versions_path,
)
from packages.core.clock import Clock
from packages.db.models.auth import User
from packages.db.models.floors import FloorRow
from packages.domain.spatial import Wall
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.access import activity_rows, assert_one_activity
from packages.testing.fixtures.clock import FakeClock

ABSENT_VERSION = "ver_01J00000000000000000000000"
"""Đúng mẫu `ver_<ULID>`, không có dòng nào."""


def _layer(stage: Stage, thickness: int) -> Any:
    """Lớp một tường AI chưa duyệt với bề dày `thickness` — ba giá trị 200/250/300 tạo ba trạng thái khác nhau."""
    return simple_layer(stage.level_id, walls=(make_wall(stage.level_id, thickness=thickness),))


async def _history(db: AsyncSession, stage: Stage, clock: Clock, thicknesses: tuple[int, ...]) -> list[str]:
    """Ghi lần lượt từng lớp (mỗi lớp một `revision`) và chụp bản sau mỗi lần; trả id các bản."""
    ids: list[str] = []
    for index, thickness in enumerate(thicknesses):
        await put_layer(db, stage, clock, _layer(stage, thickness), base=index)
        ids.append((await commit_snap(db, stage, clock)).id)
    return ids


async def _read_layer(client: httpx.AsyncClient, stage: Stage) -> dict[str, Any]:
    """N16 của tầng đầu: thân dây (`revision`, `layer`, `dimensions`, …)."""
    response = await call(client, "GET", layer_path(stage.project_id, stage.level_id), stage.owner)
    assert response.status_code == 200
    return dict(response.json())


async def test_versions_restore_version__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Tầng đã đổi từ bản mới nhất → 201, `sequence` tăng **2** (trước + sau); N16 đọc lại đúng lớp của ảnh chụp.
    Catalog: B11-ver-001.
    """
    stage = await make_stage(db_session)
    first, *_ = await _history(db_session, stage, fake_clock, (200,))
    await put_layer(db_session, stage, fake_clock, _layer(stage, 250), base=1)

    response = await restore(api_client, stage, first, 2)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sequence"] == 3
    assert body["floorRevision"] == 3
    assert body["note"] == after_note(1)
    assert body["creatorId"] == stage.owner.id
    assert body["creatorName"] == stage.owner.name
    assert body["hasSnapshot"] is True
    read = await _read_layer(api_client, stage)
    assert read["revision"] == 3
    assert read["layer"]["walls"][0]["thicknessMm"] == 200
    rows = await versions_of(db_sessionmaker, stage.floor.pk)
    assert [(row.sequence, row.floor_revision) for row in rows] == [(1, 1), (2, 2), (3, 3)]
    assert rows[1].note == before_note(1)
    assert [row.restored_from_id for row in rows] == [None, None, first]
    assert [row.restore_base_revision for row in rows] == [None, None, 2]


async def test_versions_restore_version__one_version_when_latest_is_current(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Bản mới nhất đã chụp đúng hiện trạng → không sinh bản "trước": `sequence` tăng **1**.

    Hàng catalog: [B06-G-170].
    """
    stage = await make_stage(db_session)
    first, _second = await _history(db_session, stage, fake_clock, (200, 250))

    response = await restore(api_client, stage, first, 2)

    assert response.status_code == 201
    assert response.json()["sequence"] == 3
    assert response.json()["floorRevision"] == 3


async def test_versions_restore_version__snapshot_equal_to_current(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Ảnh chụp trùng hiện trạng → 201 (không 500), trả bản mới nhất, không bản mới, không nhật ký."""
    stage = await make_stage(db_session)
    (only,) = await _history(db_session, stage, fake_clock, (200,))
    changes_before = await log_count(db_session, stage.floor.pk)

    response = await restore(api_client, stage, only, 1)

    assert response.status_code == 201, response.text
    assert response.json()["id"] == only
    assert len(await versions_of(db_sessionmaker, stage.floor.pk)) == 1
    assert await activity_rows(db_sessionmaker, actor_id=stage.owner.id, kind=ActivityKind.VERSION_RESTORE) == []
    await db_session.commit()
    assert await log_count(db_session, stage.floor.pk) == changes_before


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"baseVersion": 1, "body": {"floorId": ""}}, "body.floorId"),
        ({"baseVersion": -1, "body": {"floorId": "L-X"}}, "baseVersion"),
        ({"baseVersion": "1", "body": {"floorId": "L-X"}}, "baseVersion"),
        ("above_revision", "baseVersion"),
    ],
)
async def test_versions_restore_version__C02(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_clock: FakeClock,
    payload: dict[str, Any] | str,
    field: str,
) -> None:
    """`floorId` rỗng, `baseVersion` âm/chuỗi, `baseVersion = revision + 1` → 422 `VALIDATION` kèm `field`.

    Hàng catalog: [B06-G-154].
    """
    stage = await make_stage(db_session)
    (first,) = await _history(db_session, stage, fake_clock, (200,))
    body = restore_body(2, stage.level_id) if payload == "above_revision" else payload

    response = await call(
        api_client, "POST", versions_path(stage.project_id, first, "/restore"), stage.owner, json=body
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"
    assert response.json()["field"] == field


@pytest.mark.parametrize("where", ["body", "envelope"])
async def test_versions_restore_version__C03(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock, where: str
) -> None:
    """Khoá lạ trong `body` hay ở vỏ ngoài → 422 `VALIDATION`.

    Hàng catalog: [B06-G-157].
    """
    stage = await make_stage(db_session)
    (first,) = await _history(db_session, stage, fake_clock, (200,))
    payload = restore_body(1, stage.level_id)
    (payload["body"] if where == "body" else payload)["nonsense"] = 1

    response = await call(
        api_client, "POST", versions_path(stage.project_id, first, "/restore"), stage.owner, json=payload
    )

    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION")


async def test_versions_restore_version__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Admin hệ thống không phải thành viên → 404 `resource:"project"`, không 403 (K08).

    Hàng catalog: [B06-G-182].
    Catalog: B09-ver-012, B11-ver-008., B11-ver-008.
    """
    stage = await make_stage(db_session)
    (first,) = await _history(db_session, stage, fake_clock, (200,))
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await restore(api_client, stage, first, 1, outsider)

    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_versions_restore_version__C07(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Viewer là thành viên nhưng thiếu `layer.edit` → 403 `FORBIDDEN`.
    Catalog: B09-ver-011, B11-ver-007., B11-ver-007.
    """
    viewer = await make_user(db_session, role="viewer")
    stage = await make_stage(db_session, members=[viewer])
    (first,) = await _history(db_session, stage, fake_clock, (200,))

    response = await restore(api_client, stage, first, 1, viewer)

    assert (response.status_code, response.json()["code"]) == (403, "FORBIDDEN")


@pytest.mark.parametrize("missing", ["absent", "malformed", "other_project", "soft_deleted_floor"])
async def test_versions_restore_version__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock, missing: str
) -> None:
    """Phiên bản không có, sai mẫu, của dự án khác, hay của tầng xoá mềm → 404 `resource:"version"`.

    Hàng catalog: [B06-G-159, B06-G-184].
    Catalog: B11-ver-009.
    """
    stage = await make_stage(db_session)
    other = await make_stage(db_session)
    version_id = {"absent": ABSENT_VERSION, "malformed": "ver_nope"}.get(missing, "")
    if missing == "other_project":
        (version_id,) = await _history(db_session, other, fake_clock, (200,))
    elif missing == "soft_deleted_floor":
        (version_id,) = await _history(db_session, stage, fake_clock, (200,))
        deleted = update(FloorRow).where(FloorRow.pk == stage.floor.pk).values(deleted_at=fake_clock.now())
        await db_session.execute(deleted)
        await db_session.commit()

    response = await restore(api_client, stage, version_id, 0)

    assert (response.status_code, response.json()["resource"]) == (404, "version")


async def test_versions_restore_version__C09_stale(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Người khác ghi thêm sau khi mình đọc → 409 W20 có `remoteChanges` ≥ 1; bản "trước" rollback theo.
    Catalog: B11-ver-004.
    """
    stage = await make_stage(db_session)
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))
    await put_layer(db_session, stage, fake_clock, _layer(stage, 300), base=2)

    response = await restore(api_client, stage, first, 2)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["currentVersion"] == 3
    assert len(body["remoteChanges"]) >= 1
    assert len(await versions_of(db_sessionmaker, stage.floor.pk)) == 2


async def test_versions_restore_version__C09_missing(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Thân không có `baseVersion` → 428 `PRECONDITION_REQUIRED`, trước cả Pydantic.
    Catalog: B11-ver-005.
    """
    stage = await make_stage(db_session)
    (first,) = await _history(db_session, stage, fake_clock, (200,))

    response = await call(
        api_client,
        "POST",
        versions_path(stage.project_id, first, "/restore"),
        stage.owner,
        json={"body": {"floorId": stage.level_id}},
    )

    assert (response.status_code, response.json()["code"]) == (428, "PRECONDITION_REQUIRED")


async def test_versions_restore_version__C09b(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Gửi lại lượt vừa thành công → 200 cùng `id`, `sequence` không tăng; người khác cùng thân không được phát lại.
    Catalog: B11-ver-006.
    """
    second = await make_user(db_session, name="Người thứ hai")
    stage = await make_stage(db_session, members=[second])
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))
    created = await restore(api_client, stage, first, 2)
    assert created.status_code == 201

    repeat = await restore(api_client, stage, first, 2)
    stranger = await restore(api_client, stage, first, 2, second)

    assert repeat.status_code == 200
    assert repeat.json() == created.json()
    assert stranger.status_code == 409
    assert len(await versions_of(db_sessionmaker, stage.floor.pk)) == 3


async def test_versions_restore_version__C09b_after_source_is_pushed_out(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trần 3: lượt phục hồi tự đẩy bản nguồn khỏi ba bản còn nội dung; gửi lại vẫn 200 (C09b trước kiểm ảnh).

    Hàng catalog: [B06-G-167].
    """
    keep_snapshots(monkeypatch, 3)
    stage = await make_stage(db_session)
    first, *_ = await _history(db_session, stage, fake_clock, (200, 250, 300))
    created = await restore(api_client, stage, first, 3)
    assert created.status_code == 201
    assert (await versions_of(db_sessionmaker, stage.floor.pk))[0].snapshot is None

    repeat = await restore(api_client, stage, first, 3)
    fresh = await restore(api_client, stage, first, 4)

    assert (repeat.status_code, repeat.json()["id"]) == (200, created.json()["id"])
    assert (fresh.status_code, fresh.json()["code"]) == (422, "VERSION_SNAPSHOT_PURGED")


async def test_versions_restore_version__C09b_after_empty_diff_branch(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """`revision` bị tăng bằng SQL (không đổi nội dung) → W20 nhánh "diff rỗng" vẫn ghi; gửi lại → 200 C09b.

    Hàng catalog: [B06-G-168].
    """
    stage = await make_stage(db_session)
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))
    await db_session.execute(
        text("UPDATE floor_documents SET revision = revision + 1 WHERE floor_pk = :pk"), {"pk": stage.floor.pk}
    )
    await db_session.commit()

    created = await restore(api_client, stage, first, 2)
    repeat = await restore(api_client, stage, first, 2)

    assert created.status_code == 201, created.text
    assert created.json()["floorRevision"] == 4
    assert (repeat.status_code, repeat.json()["id"]) == (200, created.json()["id"])


async def test_versions_restore_version__C14(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Hai người, hai client thật, hai bản khác nhau, cùng `baseVersion` → một 201, một 409; `revision` +1.

    Hàng catalog: [B06-G-177].
    """
    second = await make_user(db_session, name="Người thứ hai")
    stage = await make_stage(db_session, members=[second])
    first, middle = await _history(db_session, stage, fake_clock, (200, 250))
    await put_layer(db_session, stage, fake_clock, _layer(stage, 300), base=2)
    revision_before = 3
    versions_before = len(await versions_of(db_sessionmaker, stage.floor.pk))

    results = await asyncio.gather(
        restore(api_client, stage, first, revision_before, stage.owner),
        restore(api_client, stage, middle, revision_before, second),
    )

    statuses = [response.status_code for response in results]
    rows = await versions_of(db_sessionmaker, stage.floor.pk)
    async with db_sessionmaker() as session:
        document = await load_document(session, stage.floor.pk)
    assert document is not None
    loser = next(response for response in results if response.status_code == 409)
    after = [row for row in rows if row.restored_from_id is not None]
    print(
        f"C14 versions_restore_version: status = {statuses}, revision {revision_before} → {document.revision}, "
        f"bản mới = {len(rows) - versions_before} (bản sau = {len(after)})"
    )
    assert sorted(statuses) == [201, 409]
    assert document.revision == revision_before + 1
    assert len(after) == 1
    assert len(loser.json()["remoteChanges"]) >= 1
    assert loser.json()["currentVersion"] == document.revision


async def test_versions_restore_version__C17(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """201 không có `label` → khoá `label` vắng (W2); `note` có mặt.

    Hàng catalog: [B06-G-179].
    """
    stage = await make_stage(db_session)
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))

    body = (await restore(api_client, stage, first, 2)).json()

    assert "label" not in body
    assert body["note"] == after_note(1)


async def test_versions_restore_version__C18(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Một dòng `version.restore`, `object_code` = id bản nguồn, nhãn là tên tầng; gửi lại (200) không thêm dòng.

    Hàng catalog: [B06-G-165].
    """
    stage = await make_stage(db_session)
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))

    assert (await restore(api_client, stage, first, 2)).status_code == 201
    assert (await restore(api_client, stage, first, 2)).status_code == 200

    row = await assert_one_activity(
        db_sessionmaker, actor_id=stage.owner.id, kind=ActivityKind.VERSION_RESTORE, object_code=first
    )
    assert row.object_label == stage.floor.name
    assert row.project_id == stage.project_id


async def test_versions_restore_version__floor_mismatch(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """`floorId` của tầng khác → 422 `VERSION_FLOOR_MISMATCH` `field:"body.floorId"`, chưa ghi gì.

    Hàng catalog: [B06-G-158].
    Catalog: B11-ver-010.
    """
    stage = await make_stage(db_session, floors=2)
    (first,) = await _history(db_session, stage, fake_clock, (200,))

    response = await restore(api_client, stage, first, 1, floor_id=stage.other_level_id)

    assert response.status_code == 422
    assert response.json()["code"] == "VERSION_FLOOR_MISMATCH"
    assert response.json()["field"] == "body.floorId"


@pytest.mark.parametrize("how", ["purged", "schema", "alien_key"])
async def test_versions_restore_version__purged(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
    how: Corruption,
) -> None:
    """Mất ảnh chụp, `schemaVersion: 2`, khoá lạ trong `document` → 422 `VERSION_SNAPSHOT_PURGED`; không ghi gì.

    Hàng catalog: [B06-G-178].
    """
    stage = await make_stage(db_session)
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))
    await corrupt_snapshot(db_session, first, how)
    caplog.set_level("WARNING", logger=SNAPSHOT_LOGGER)

    response = await restore(api_client, stage, first, 2)

    assert response.status_code == 422
    assert response.json()["code"] == "VERSION_SNAPSHOT_PURGED"
    assert mismatch_logged(caplog) is (how != "purged")
    assert len(await versions_of(db_sessionmaker, stage.floor.pk)) == 2


async def test_versions_restore_version__restore_rescales_to_current_scale(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Chụp ở tỉ lệ 1, hiệu chỉnh 10, phục hồi → mục AI và `line` kích thước dài gấp 10, mục đã duyệt giữ nguyên.

    Hàng catalog: [B06-G-172].
    """
    stage = await make_stage(db_session)
    human = Wall.model_validate(
        wire(
            make_wall(
                stage.level_id, entity_id="W-HUMANWALL1", length=2000, thickness=50, source="human", reviewed=True
            )
        )
        | {"centreline": {"start": {"x": 0, "y": 3000}, "end": {"x": 2000, "y": 3000}}}
    )
    ai = make_wall(stage.level_id, length=1000, thickness=50)
    dimension = make_dimension(stage.level_id, refs=[WALL_ID], length=1000)
    both = simple_layer(stage.level_id, walls=(ai, human))
    await put_layer(
        db_session, stage, fake_clock, both, base=0, scale=Decimal(1), dimensions=[dimension], scale_source="pipeline"
    )
    first = (await commit_snap(db_session, stage, fake_clock)).id
    await put_layer(db_session, stage, fake_clock, None, base=1, scale=Decimal(10))
    await put_layer(db_session, stage, fake_clock, simple_layer(stage.level_id, walls=(human,)), base=2, dimensions=[])

    response = await restore(api_client, stage, first, 3)

    assert response.status_code == 201, response.text
    read = await _read_layer(api_client, stage)
    walls = {wall["id"]: wall for wall in read["layer"]["walls"]}
    assert walls[WALL_ID]["centreline"]["end"]["x"] == 10_000
    assert walls["W-HUMANWALL1"]["centreline"]["end"]["x"] == 2000
    assert [item["line"]["end"]["x"] for item in read["dimensions"]] == [10_000]
    assert read["level"]["scaleMillimetresPerPixel"] == 10


async def test_versions_restore_version__rescale_that_breaks_the_model_is_422(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    fake_clock: FakeClock,
) -> None:
    """Đổi tỉ lệ làm tường dài 4 mm co về 0 (x0,1) → 422 `VALIDATION`, giao dịch rollback, chưa ghi gì.

    Hàng catalog: [B06-G-173].
    Catalog: B11-ver-011.
    """
    stage = await make_stage(db_session)
    tiny = simple_layer(stage.level_id, walls=(make_wall(stage.level_id, length=4, thickness=50),))
    await put_layer(db_session, stage, fake_clock, tiny, base=0, scale=Decimal(10))
    first = (await commit_snap(db_session, stage, fake_clock)).id
    await db_session.execute(
        text("UPDATE floor_documents SET scale_mm_per_px = 1 WHERE floor_pk = :pk"), {"pk": stage.floor.pk}
    )
    await db_session.commit()

    response = await restore(api_client, stage, first, 1)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"
    assert "count" not in response.json()  # NO-244: không phải lỗi Pydantic nên không có số lỗi
    assert len(await versions_of(db_sessionmaker, stage.floor.pk)) == 1


def _wall_at(level_id: str, entity_id: str, y: int) -> Wall:
    """Tường AI ngang tại tung độ `y` — hai tường chồng lên nhau sẽ vướng toàn vẹn nên phải đặt lệch."""
    return Wall.model_validate(
        wire(make_wall(level_id, entity_id=entity_id))
        | {"centreline": {"start": {"x": 0, "y": y}, "end": {"x": 1000, "y": y}}}
    )


async def test_versions_restore_version__undo_brings_back_the_lost_wall(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Phục hồi làm mất tường W, rồi phục hồi bản "trước" vừa sinh → `layer`, `dimensions` về như cũ (có lại W).

    Hàng catalog: [B06-G-174].
    Catalog: B11-ver-002, B11-ver-003.
    """
    stage = await make_stage(db_session)
    kept, lost = _wall_at(stage.level_id, "W-KEPTWALL01", 0), _wall_at(stage.level_id, "W-LOSTWALL01", 2000)
    await put_layer(
        db_session,
        stage,
        fake_clock,
        simple_layer(stage.level_id, walls=(kept,)),
        base=0,
        dimensions=[make_dimension(stage.level_id, refs=[kept.id])],
    )
    first = (await commit_snap(db_session, stage, fake_clock)).id
    await put_layer(
        db_session,
        stage,
        fake_clock,
        simple_layer(stage.level_id, walls=(kept, lost)),
        base=1,
        dimensions=[make_dimension(stage.level_id, refs=[kept.id, lost.id])],
    )
    original = await _read_layer(api_client, stage)
    assert original["dimensions"][0]["referenceIds"] == [kept.id, lost.id]

    forward = await restore(api_client, stage, first, 2)
    assert forward.status_code == 201
    after_forward = await _read_layer(api_client, stage)
    assert [wall["id"] for wall in after_forward["layer"]["walls"]] == [kept.id]
    before_id = next(
        item["id"]
        for item in (await list_versions(api_client, stage)).json()["items"]
        if item["note"] == before_note(1)
    )

    undo = await restore(api_client, stage, before_id, 3)

    assert undo.status_code == 201, undo.text
    restored = await _read_layer(api_client, stage)
    assert restored["layer"] == original["layer"]
    assert restored["dimensions"] == original["dimensions"]
    assert restored["dimensions"][0]["referenceIds"] == [kept.id, lost.id]
    await db_session.commit()
    rows = [row for row in await log_rows(db_session, stage.floor.pk) if row.revision >= 3]
    assert rows
    assert {row.changed_by for row in rows} == {stage.owner.id}


async def test_versions_restore_version__creator_name_is_the_name_at_write_time(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Đổi `users.name` sau khi phục hồi → N17 vẫn trả tên cũ cho bản "sau" và cho lượt gắn nhãn sau đó.

    Hàng catalog: [B06-G-176].
    """
    stage = await make_stage(db_session)
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))
    old_name = stage.owner.name
    assert (await restore(api_client, stage, first, 2)).status_code == 201
    await db_session.execute(text("UPDATE users SET name = 'Tên mới' WHERE id = :id"), {"id": stage.owner.id})
    await db_session.commit()

    latest = (await list_versions(api_client, stage)).json()["items"][0]
    labelled = await relabel(api_client, stage, latest["id"], "chốt")

    assert latest["creatorName"] == old_name
    assert labelled.json()["creatorName"] == old_name


async def test_versions_restore_version__other_users_are_recorded_as_the_actor(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Người gọi (không phải người tạo bản nguồn) là `creatorId`/`creatorName` của bản "sau" — lấy từ `Principal`.

    Hàng catalog: [B06-G-175].
    """
    second: User = await make_user(db_session, name="Người thứ hai")
    stage = await make_stage(db_session, members=[second])
    first, _ = await _history(db_session, stage, fake_clock, (200, 250))

    body = (await restore(api_client, stage, first, 2, second)).json()

    assert (body["creatorId"], body["creatorName"]) == (second.id, "Người thứ hai")


async def test_versions_restore_version__keeps_scale_accepted(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """ML-ACC-R10 K4 #15: phục hồi không gửi tỉ lệ — số, nguồn `human` và cờ chấp nhận số máy giữ như hiện tại."""
    stage = await make_stage(db_session)
    first, *_ = await _history(db_session, stage, fake_clock, (200,))
    await put_layer(db_session, stage, fake_clock, None, base=1, scale=Decimal("12"), evidence="machineAccepted")

    response = await restore(api_client, stage, first, 2)

    assert response.status_code == 201, response.text
    document = await load_document(db_session, stage.floor.pk)
    assert document is not None
    assert (document.scale_mm_per_px, document.scale_source) == (Decimal("12.000000"), "human")
    assert document.scale_accepted is not None
    read = await _read_layer(api_client, stage)
    assert read["scaleReasons"] == ["basisUnknown", "userAccepted"]
