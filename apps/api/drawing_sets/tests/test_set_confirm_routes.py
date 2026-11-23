"""Case của N60 `drawing_sets_confirm_set` (G +ngoài, C10 riêng, C14 thêm; C16 miễn) — K-32a.

Đích được đặt qua N59 thật (bản duyệt `revision` 1) rồi mới xác nhận, đúng luồng người dùng. Pipeline không chạy
trong test: chỉ đếm thông điệp `pipeline.orchestrate.start` trên hàng `pipeline.cpu` của Redis thật.
"""

import asyncio
from typing import Any, Final

import httpx
import pytest
from fastapi import FastAPI
from redis import Redis as SyncRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.core.auth import Principal
from apps.api.drawing_sets.confirm import _check_access
from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.drawing_sets.tests._set_helpers import (
    CPU_QUEUE,
    MISSING_SET,
    NEW_LEVEL,
    ReviewSet,
    confirm_path,
    edit_of,
    seed_review_set,
    views_body,
    views_path,
)
from apps.api.drawing_sets.tests._set_helpers import (
    broker as broker,
)
from apps.api.drawing_sets.tests._set_helpers import (
    drawing_set_on as drawing_set_on,
)
from apps.api.drawings.tests._upload_helpers import Stage, headers_of, make_stage, upload_png
from apps.api.projects.access import ProjectAccess
from packages.core.errors import AppError
from packages.db.models.drawing_sets import DrawingSetViewRow
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.db.models.floors import FloorRow
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawings import make_complete_upload
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.access import activity_rows, assert_one_activity
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.messaging import queued_payloads

SECOND_LEVEL: Final = "L-0000000000NEW2"

pytestmark = pytest.mark.usefixtures("drawing_set_on", "broker")


async def _review(client: httpx.AsyncClient, stage: Stage, seeded: ReviewSet, *edits: dict[str, Any]) -> None:
    """Lưu bản duyệt (N59, base 0) với các hình `edits`; khẳng định 200."""
    response = await client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=views_body(0, *edits), headers=stage.headers
    )
    assert response.status_code == 200, response.text


async def _confirm(
    client: httpx.AsyncClient, stage: Stage, set_id: str, revision: int = 1, extra: dict[str, str] | None = None
) -> httpx.Response:
    """N60 cho bộ `set_id` với `revision`."""
    return await client.post(
        confirm_path(stage.project_id, set_id),
        json={"setId": set_id, "revision": revision},
        headers=stage.headers | (extra or {}),
    )


def _new_floor(level_id: str = NEW_LEVEL, name: str = "Tầng 1") -> dict[str, str]:
    """Đích tầng mới."""
    return {"mode": "newFloor", "floorId": level_id, "name": name}


async def _count(sessionmaker: async_sessionmaker[AsyncSession], stmt: Any) -> int:
    """Một câu đếm trên session mới."""
    async with sessionmaker() as session:
        return int((await session.execute(stmt)).scalar_one())


async def _seeded(db: AsyncSession, storage: LocalDiskStorage, views: int = 3) -> tuple[Stage, ReviewSet]:
    """Sân khấu (một tầng có sẵn chưa bản vẽ) + bộ `review` ba hình máy trên ba trang."""
    stage = await make_stage(db)
    return stage, await seed_review_set(db, storage, stage, views=views, pages=views)


async def test_drawing_sets_confirm_set__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    broker: SyncRedis,
) -> None:
    """Tầng mới + tầng có sẵn + mặt cắt → `confirmed`; 2 tầng mới (mặt cắt ẩn, `isSection`), 3 lượt tải `complete` có
    gốc chép sang khoá tầng, 3 lượt chạy xếp sau commit, mỗi hình có `uploadId`."""
    stage, seeded = await _seeded(db_session, local_storage)
    v0, v1, v2 = seeded.views
    await _review(
        api_client,
        stage,
        seeded,
        edit_of(v0, kind="plan", storeyNo=2, target=_new_floor()),
        edit_of(v1, kind="plan", target={"mode": "floor", "floorId": stage.level_id}),
        edit_of(v2, kind="section", target={"mode": "section", "name": "Mặt cắt A-A"}),
    )
    response = await _confirm(api_client, stage, seeded.drawing_set.id)
    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire["status"] == "confirmed"
    assert "confirmedAt" in wire
    assert all("uploadId" in view for view in wire["views"])
    async with db_sessionmaker() as session:
        floors = (
            (
                await session.execute(
                    select(FloorRow).where(FloorRow.project_id == stage.project_id).order_by(FloorRow.floor_order)
                )
            )
            .scalars()
            .all()
        )
        uploads = (
            (await session.execute(select(UploadRow).where(UploadRow.project_id == stage.project_id))).scalars().all()
        )
    assert [(f.level_id == NEW_LEVEL, f.is_section, f.hidden_in_3d) for f in floors] == [
        (False, False, False),
        (True, False, False),
        (False, True, True),
    ]
    assert floors[1].elevation_mm == floors[0].elevation_mm + floors[0].height_mm
    assert {(u.status, u.page_auto, u.crop_corners) for u in uploads} == {("complete", False, None)}
    assert sorted(u.page_index for u in uploads) == [0, 1, 2]
    for upload in uploads:
        assert upload.original_key is not None
        assert (await local_storage.stat(upload.original_key)) is not None
    assert len(queued_payloads(broker, CPU_QUEUE)) == 3


async def test_confirm_keeps_crop_of_partial_view(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Khung nửa trang → lượt tải mang `crop_corners` 4 góc như #5."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    half = {"xRatio": 0.5, "yRatio": 0.0, "widthRatio": 0.5, "heightRatio": 1.0}
    await _review(api_client, stage, seeded, edit_of(seeded.views[0], kind="plan", crop=half, target=_new_floor()))
    assert (await _confirm(api_client, stage, seeded.drawing_set.id)).status_code == 200
    async with db_sessionmaker() as session:
        corners = (
            await session.execute(select(UploadRow.crop_corners).where(UploadRow.project_id == stage.project_id))
        ).scalar_one()
    assert corners == [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


async def test_drawing_sets_confirm_set__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """`revision` không phải số nguyên → 422 `VALIDATION` `field:"revision"`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    response = await api_client.post(
        confirm_path(stage.project_id, seeded.drawing_set.id),
        json={"setId": seeded.drawing_set.id, "revision": "1"},
        headers=stage.headers,
    )
    assert (response.status_code, response.json()["field"]) == (422, "revision")


async def test_drawing_sets_confirm_set__C03(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Khoá lạ → 422 `VALIDATION`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    body = {"setId": seeded.drawing_set.id, "revision": 0, "dungLuon": True}
    response = await api_client.post(
        confirm_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION")


async def test_drawing_sets_confirm_set__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Người ngoài dự án → 404 `resource:"project"`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.post(
        confirm_path(stage.project_id, seeded.drawing_set.id),
        json={"setId": seeded.drawing_set.id, "revision": 0},
        headers=headers_of(outsider),
    )
    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_drawing_sets_confirm_set__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`viewer` → 403."""
    owner = await make_user(db_session)
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    await db_session.commit()
    response = await api_client.post(
        confirm_path(project.id, MISSING_SET),
        json={"setId": MISSING_SET, "revision": 0},
        headers=headers_of(viewer, role="viewer"),
    )
    assert response.status_code == 403


def test_confirm_requires_layer_edit(fake_principal: Principal) -> None:
    """Vai không có `layer.edit` (chặn trước cả `floor.upload` ở route) → 403 ở service."""
    access = ProjectAccess(project_id="prj_" + "0" * 26, project_name="x", principal=fake_principal, role="viewer")
    with pytest.raises(AppError) as caught:
        _check_access(access)
    assert caught.value.code.code == "FORBIDDEN"


async def test_drawing_sets_confirm_set__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bộ không có → 404 `resource:"drawingSet"`."""
    stage = await make_stage(db_session)
    response = await _confirm(api_client, stage, MISSING_SET, 0)
    assert (response.status_code, response.json()["resource"]) == (404, "drawingSet")


async def test_drawing_sets_confirm_set__C10_queue_once(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage, broker: SyncRedis
) -> None:
    """Lặp `Idempotency-Key` → cùng response, một lượt chạy."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await _review(api_client, stage, seeded, edit_of(seeded.views[0], kind="plan", target=_new_floor()))
    first = await _confirm(api_client, stage, seeded.drawing_set.id, extra={"Idempotency-Key": "k32a-confirm"})
    second = await _confirm(api_client, stage, seeded.drawing_set.id, extra={"Idempotency-Key": "k32a-confirm"})
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert len(queued_payloads(broker, CPU_QUEUE)) == 1


async def test_drawing_sets_confirm_set__C10_no_key_replay(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    broker: SyncRedis,
) -> None:
    """Gửi lại **không** khoá sau khi đã xác nhận → 200 bộ hiện tại, không tầng/lượt mới, một dòng nhật ký."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await _review(api_client, stage, seeded, edit_of(seeded.views[0], kind="plan", target=_new_floor()))
    first = await _confirm(api_client, stage, seeded.drawing_set.id)
    second = await _confirm(api_client, stage, seeded.drawing_set.id, revision=0)
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["status"] == "confirmed"
    assert len(queued_payloads(broker, CPU_QUEUE)) == 1
    assert (
        await _count(
            db_sessionmaker, select(func.count()).select_from(UploadRow).where(UploadRow.project_id == stage.project_id)
        )
        == 1
    )
    rows = await activity_rows(db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.DRAWING_SET_CONFIRM)
    assert len(rows) == 1


async def test_drawing_sets_confirm_set__C14(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    broker: SyncRedis,
) -> None:
    """Hai xác nhận song song → cả hai 200, đúng một bộ tầng và một bộ lượt chạy."""
    stage, seeded = await _seeded(db_session, local_storage, views=2)
    await _review(
        api_client,
        stage,
        seeded,
        edit_of(seeded.views[0], kind="plan", target=_new_floor()),
        edit_of(seeded.views[1], kind="plan", target=_new_floor(SECOND_LEVEL, "Tầng 2")),
    )
    path, body = confirm_path(stage.project_id, seeded.drawing_set.id), {"setId": seeded.drawing_set.id, "revision": 1}
    async with make_api_client(api_app) as client_a, make_api_client(api_app) as client_b:
        first, second = await asyncio.gather(
            client_a.post(path, json=body, headers=stage.headers), client_b.post(path, json=body, headers=stage.headers)
        )
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    floors = select(func.count()).select_from(FloorRow).where(FloorRow.project_id == stage.project_id)
    runs = (
        select(func.count()).select_from(PipelineRunRow).join(UploadRow).where(UploadRow.project_id == stage.project_id)
    )
    assert (await _count(db_sessionmaker, floors), await _count(db_sessionmaker, runs)) == (3, 2)
    assert len(queued_payloads(broker, CPU_QUEUE)) == 2


async def test_drawing_sets_confirm_set__C17(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Bộ chưa tên đã xác nhận → `name`, `errorCode` vắng khoá; hình không đích (đã loại) không có `uploadId`."""
    stage, seeded = await _seeded(db_session, local_storage, views=2)
    await _review(
        api_client,
        stage,
        seeded,
        edit_of(seeded.views[0], kind="plan", target=_new_floor()),
        edit_of(seeded.views[1], excluded=True),
    )
    wire = (await _confirm(api_client, stage, seeded.drawing_set.id)).json()
    assert {"name", "errorCode"}.isdisjoint(wire)
    assert "uploadId" not in wire["views"][1]


async def test_drawing_sets_confirm_set__C18(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Một dòng nhật ký `drawingset.confirm` (và `floor.create` cho tầng mới, của #10)."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await _review(api_client, stage, seeded, edit_of(seeded.views[0], kind="plan", target=_new_floor()))
    await _confirm(api_client, stage, seeded.drawing_set.id)
    await assert_one_activity(
        db_sessionmaker,
        actor_id=stage.scene.user.id,
        kind=ActivityKind.DRAWING_SET_CONFIRM,
        object_code=seeded.drawing_set.id,
    )
    await assert_one_activity(
        db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.FLOOR_CREATE, object_code=NEW_LEVEL
    )


async def test_drawing_sets_confirm_set__C21(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """`setId` khác đường → 422 `PATH_BODY_MISMATCH`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    response = await api_client.post(
        confirm_path(stage.project_id, seeded.drawing_set.id),
        json={"setId": MISSING_SET, "revision": 0},
        headers=stage.headers,
    )
    assert (response.status_code, response.json()["code"]) == (422, "PATH_BODY_MISMATCH")


async def test_confirm_rejects_existing_drawing_unless_replace(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    broker: SyncRedis,
) -> None:
    """Tầng đích đã có bản vẽ: không tick → 409 `FLOOR_HAS_DRAWING`; tick `replace` → lượt tải mới trên đúng tầng đó."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await make_complete_upload(
        db_session, local_storage, project=stage.scene.project, floor=stage.scene.floor, data=upload_png(300)
    )
    await db_session.commit()
    target = {"mode": "floor", "floorId": stage.level_id}
    await _review(api_client, stage, seeded, edit_of(seeded.views[0], kind="plan", target=target))
    refused = await _confirm(api_client, stage, seeded.drawing_set.id)
    assert (refused.status_code, refused.json()["code"]) == (409, "FLOOR_HAS_DRAWING")
    path = views_path(stage.project_id, seeded.drawing_set.id)
    edit = edit_of(seeded.views[0], kind="plan", target=target | {"replace": True})
    assert (await api_client.put(path, json=views_body(1, edit), headers=stage.headers)).status_code == 200
    assert (await _confirm(api_client, stage, seeded.drawing_set.id, revision=2)).status_code == 200
    stmt = (
        select(func.count())
        .select_from(UploadRow)
        .where(UploadRow.floor_pk == stage.scene.floor.pk, UploadRow.status == "complete")
    )
    assert await _count(db_sessionmaker, stmt) == 2


@pytest.mark.parametrize("case", ["plan_without_target", "same_floor_twice", "section_floor", "deleted_floor"])
async def test_confirm_rejects_bad_targets(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    case: str,
) -> None:
    """Hình mặt bằng chưa đích, hai hình cùng tầng, tầng đích là mặt cắt hay đã xoá → 422 `field:"views.<i>.target"`."""
    stage, seeded = await _seeded(db_session, local_storage, views=2)
    floor_target = {"mode": "floor", "floorId": stage.level_id}
    edits = {
        "plan_without_target": [
            edit_of(seeded.views[0], kind="plan", target=_new_floor()),
            edit_of(seeded.views[1], kind="plan"),
        ],
        "same_floor_twice": [
            edit_of(seeded.views[0], target=_new_floor()),
            edit_of(seeded.views[1], target=_new_floor()),
        ],
        "section_floor": [edit_of(seeded.views[0], target=_new_floor()), edit_of(seeded.views[1], target=floor_target)],
        "deleted_floor": [edit_of(seeded.views[0], target=_new_floor()), edit_of(seeded.views[1], target=floor_target)],
    }[case]
    await _review(api_client, stage, seeded, *edits)
    if case in {"section_floor", "deleted_floor"}:
        async with db_sessionmaker() as session:
            floor = await session.get(FloorRow, stage.scene.floor.pk)
            assert floor is not None
            floor.is_section = case == "section_floor"
            floor.deleted_at = floor.created_at if case == "deleted_floor" else None
            await session.commit()
    response = await _confirm(api_client, stage, seeded.drawing_set.id)
    assert (response.status_code, response.json()["field"]) == (422, "views.1.target")


async def test_confirm_rejects_taken_new_floor_id(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Id tầng mới đã có trong dự án → 409 `FLOOR_ID_TAKEN`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await _review(api_client, stage, seeded, edit_of(seeded.views[0], kind="plan", target=_new_floor(stage.level_id)))
    response = await _confirm(api_client, stage, seeded.drawing_set.id)
    assert (response.status_code, response.json()["code"]) == (409, "FLOOR_ID_TAKEN")


async def test_confirm_state_and_plan_guards(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Không hình nào có đích tầng → 422 `DRAWING_SET_NO_PLAN`; `revision` cũ → 409 `…_REVISION_STALE`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await _review(
        api_client, stage, seeded, edit_of(seeded.views[0], kind="section", target={"mode": "section", "name": "MC"})
    )
    no_plan = await _confirm(api_client, stage, seeded.drawing_set.id)
    stale = await _confirm(api_client, stage, seeded.drawing_set.id, revision=0)
    assert [(r.status_code, r.json()["code"]) for r in (no_plan, stale)] == [
        (422, "DRAWING_SET_NO_PLAN"),
        (409, "DRAWING_SET_REVISION_STALE"),
    ]


async def test_confirm_requires_review(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Bộ còn `analyzing` → 409 `DRAWING_SET_NOT_READY`."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, status="analyzing")
    response = await _confirm(api_client, stage, seeded.drawing_set.id, revision=0)
    assert (response.status_code, response.json()["code"]) == (409, "DRAWING_SET_NOT_READY")


async def test_confirm_counts_plans_against_the_cap(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quá `PLANS_MAX` hình có đích tầng → 422 `field:"views"` (trần đặt 1 cho test)."""
    monkeypatch.setattr("apps.api.drawing_sets.confirm.PLANS_MAX", 1)
    stage, seeded = await _seeded(db_session, local_storage, views=2)
    await _review(
        api_client,
        stage,
        seeded,
        edit_of(seeded.views[0], target=_new_floor()),
        edit_of(seeded.views[1], target=_new_floor(SECOND_LEVEL)),
    )
    response = await _confirm(api_client, stage, seeded.drawing_set.id)
    assert (response.status_code, response.json()["field"]) == (422, "views")


async def test_drawing_sets_confirm_set__disabled(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Công tắc tắt → 503 `DRAWING_SET_DISABLED`."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    monkeypatch.setenv("DRAWING_SET_ENABLED", "false")
    reset_drawing_set_settings_cache()
    response = await _confirm(api_client, stage, seeded.drawing_set.id, revision=0)
    assert (response.status_code, response.json()["code"]) == (503, "DRAWING_SET_DISABLED")


async def test_confirmed_views_link_to_uploads(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """`drawing_set_views.upload_id` trỏ đúng lượt tải trên tầng đích (đường K-32d/K-32e tra ngược)."""
    stage, seeded = await _seeded(db_session, local_storage, views=1)
    await _review(
        api_client,
        stage,
        seeded,
        edit_of(seeded.views[0], kind="plan", target={"mode": "floor", "floorId": stage.level_id}),
    )
    await _confirm(api_client, stage, seeded.drawing_set.id)
    async with db_sessionmaker() as session:
        floor_pk = (
            await session.execute(
                select(UploadRow.floor_pk).join(DrawingSetViewRow, DrawingSetViewRow.upload_id == UploadRow.id)
            )
        ).scalar_one()
    assert floor_pk == stage.scene.floor.pk
