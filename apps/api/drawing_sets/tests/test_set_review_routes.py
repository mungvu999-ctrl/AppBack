"""Case của N59 `drawing_sets_replace_views` (GV: G bỏ C18, thêm C09 C09b C14) — K-32a."""

import asyncio
import unicodedata
from typing import Final

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.drawing_sets.tests._set_helpers import (
    CROP_FULL,
    MISSING_SET,
    NEW_LEVEL,
    ReviewSet,
    edit_of,
    seed_review_set,
    views_body,
    views_path,
)
from apps.api.drawing_sets.tests._set_helpers import (
    drawing_set_on as drawing_set_on,
)
from apps.api.drawings.tests._upload_helpers import Stage, headers_of, make_stage
from packages.db.models.drawing_sets import DrawingSetRow, DrawingSetViewRow
from packages.db.models.projects import ProjectMembership
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.api import make_api_client

NFD_FLOOR: Final = unicodedata.normalize("NFD", "Tầng trệt")
HALF: Final = {"xRatio": 0.0, "yRatio": 0.0, "widthRatio": 0.5, "heightRatio": 1.0}

pytestmark = pytest.mark.usefixtures("drawing_set_on")


async def _views(sessionmaker: async_sessionmaker[AsyncSession], set_id: str) -> list[DrawingSetViewRow]:
    """Hình của bộ theo `ord`, đọc lại bằng session mới."""
    async with sessionmaker() as session:
        stmt = select(DrawingSetViewRow).where(DrawingSetViewRow.set_id == set_id).order_by(DrawingSetViewRow.ord)
        return list((await session.execute(stmt)).scalars())


async def _seed(db: AsyncSession, storage: LocalDiskStorage, views: int = 2) -> tuple[Stage, ReviewSet]:
    """Sân khấu + bộ `review` hai trang, `views` hình máy."""
    stage = await make_stage(db)
    return stage, await seed_review_set(db, storage, stage, views=views, pages=2)


async def test_drawing_sets_replace_views__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Đổi thứ tự, sửa loại/khung/đích, loại một hình máy, vẽ thêm một hình → `revision` 1, dây phản ánh đủ."""
    stage, seeded = await _seed(db_session, local_storage)
    first, second = seeded.views
    body = views_body(
        0,
        edit_of(second, kind="plan", storeyNo=1, target={"mode": "floor", "floorId": stage.level_id, "replace": True}),
        edit_of(first, excluded=True),
        {
            "fileId": seeded.file.id,
            "pageIndex": 1,
            "crop": HALF,
            "excluded": False,
            "kind": "section",
            "target": {"mode": "section", "name": "Mặt cắt A-A"},
        },
    )
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers
    )
    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire["revision"] == 1
    assert [(v["id"], v["source"], v["kind"], v["excluded"]) for v in wire["views"][:2]] == [
        (second.id, "ai", "plan", False),
        (first.id, "ai", "other", True),
    ]
    assert wire["views"][0]["target"] == {"mode": "floor", "floorId": stage.level_id, "replace": True}
    assert (wire["views"][0]["kindConfidence"], wire["views"][0]["storeyConfidence"]) == (1.0, 1.0)
    drawn = wire["views"][2]
    assert (drawn["source"], drawn["crop"], drawn["target"]) == (
        "user",
        HALF,
        {"mode": "section", "name": "Mặt cắt A-A"},
    )
    rows = await _views(db_sessionmaker, seeded.drawing_set.id)
    assert rows[0].machine == {"questions": ["NO_TEXT", "UNKNOWN_CODE"]}  # đề xuất gốc của máy giữ nguyên


async def test_replace_views_keeps_absent_ai_views_and_drops_absent_user_views(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Hình máy vắng khỏi danh sách vẫn còn (xếp sau); hình người vẽ vắng ở lượt sau thì bị xoá."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    drawn = {"fileId": seeded.file.id, "pageIndex": 0, "crop": HALF, "excluded": False, "kind": "detail"}
    first = (await api_client.put(path, json=views_body(0, drawn), headers=stage.headers)).json()
    assert [v["source"] for v in first["views"]] == ["user", "ai", "ai"]
    second = (await api_client.put(path, json=views_body(1, edit_of(seeded.views[1])), headers=stage.headers)).json()
    assert [v["id"] for v in second["views"]] == [seeded.views[1].id, seeded.views[0].id]


async def test_drawing_sets_replace_views__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Loại ngoài tập → 422 `VALIDATION` `field` trỏ vào hình đó."""
    stage, seeded = await _seed(db_session, local_storage)
    body = views_body(0, edit_of(seeded.views[0], kind="facade"))
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers
    )
    assert (response.status_code, response.json()["field"]) == (422, "body.views.0.kind")


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"id": "dwv_" + "0" * 26}, "body.views.0.id"),
        ({"pageIndex": 1}, "body.views.0.pageIndex"),
        ({"fileId": "dwf_" + "0" * 26}, "body.views.0.fileId"),
        ({"target": {"mode": "floor", "floorId": "L-0000000000GONE"}}, "body.views.0.target"),
    ],
)
async def test_replace_views_rejects_edits_outside_the_set(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    change: dict[str, object],
    field: str,
) -> None:
    """Id hình lạ, hình máy đổi trang, tệp lạ, tầng đích không có → 422 đúng `field`, không đổi gì."""
    stage, seeded = await _seed(db_session, local_storage)
    body = views_body(0, edit_of(seeded.views[0], **change))
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers
    )
    assert (response.status_code, response.json()["field"]) == (422, field)


async def test_replace_views_rejects_duplicate_ids_and_pages_past_the_file(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Một id hai lần → `id`; hình người vẽ trên trang không có → `pageIndex`."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    twice = views_body(0, edit_of(seeded.views[0]), edit_of(seeded.views[0]))
    response = await api_client.put(path, json=twice, headers=stage.headers)
    assert response.json()["field"] == "body.views.1.id"
    past = views_body(
        0, {"fileId": seeded.file.id, "pageIndex": 7, "crop": CROP_FULL, "excluded": False, "kind": "plan"}
    )
    assert (await api_client.put(path, json=past, headers=stage.headers)).json()["field"] == "body.views.0.pageIndex"


async def test_drawing_sets_replace_views__C03(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Khoá lạ trong hình → 422 `VALIDATION`."""
    stage, seeded = await _seed(db_session, local_storage)
    body = views_body(0, edit_of(seeded.views[0], khoaLa=1))
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION")


async def test_drawing_sets_replace_views__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Người ngoài dự án → 404 `resource:"project"`."""
    stage, seeded = await _seed(db_session, local_storage)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=views_body(0), headers=headers_of(outsider)
    )
    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_drawing_sets_replace_views__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`viewer` → 403."""
    owner = await make_user(db_session)
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    await db_session.commit()
    response = await api_client.put(
        views_path(project.id, MISSING_SET), json=views_body(0), headers=headers_of(viewer, role="viewer")
    )
    assert response.status_code == 403


async def test_drawing_sets_replace_views__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bộ không có → 404 `resource:"drawingSet"`."""
    stage = await make_stage(db_session)
    response = await api_client.put(
        views_path(stage.project_id, MISSING_SET), json=views_body(0), headers=stage.headers
    )
    assert (response.status_code, response.json()["resource"]) == (404, "drawingSet")


async def test_drawing_sets_replace_views__C09_stale(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """`baseVersion` cũ → 409 `VERSION_CONFLICT` thân W20 (`currentVersion`, `remoteChanges: []`)."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    assert (
        await api_client.put(path, json=views_body(0, edit_of(seeded.views[0])), headers=stage.headers)
    ).status_code == 200
    response = await api_client.put(
        path, json=views_body(0, edit_of(seeded.views[0], excluded=True)), headers=stage.headers
    )
    assert response.status_code == 409
    assert (response.json()["code"], response.json()["currentVersion"], response.json()["remoteChanges"]) == (
        "VERSION_CONFLICT",
        1,
        [],
    )


async def test_drawing_sets_replace_views__C09_missing(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Thiếu `baseVersion` → 428 `PRECONDITION_REQUIRED`."""
    stage, seeded = await _seed(db_session, local_storage)
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json={"body": {"views": []}}, headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (428, "PRECONDITION_REQUIRED")


async def test_drawing_sets_replace_views__C09b(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Cùng người gửi lặp cùng thân với base đã lưu → 200 kết quả hiện tại, không revision mới; người khác → 409."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    body = views_body(0, edit_of(seeded.views[0], kind="plan"))
    first = await api_client.put(path, json=body, headers=stage.headers)
    again = await api_client.put(path, json=body, headers=stage.headers)
    assert (first.status_code, again.status_code) == (200, 200)
    assert again.json()["revision"] == 1
    other = await make_user(db_session, role="engineer")
    db_session.add(ProjectMembership(project_id=stage.project_id, user_id=other.id, added_by=stage.scene.user.id))
    await db_session.commit()
    conflict = await api_client.put(path, json=body, headers=headers_of(other))
    assert (conflict.status_code, conflict.json()["code"]) == (409, "VERSION_CONFLICT")


async def test_drawing_sets_replace_views__C14(
    api_app: FastAPI, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Hai lượt lưu song song cùng base → đúng một 200, bên kia 409 `VERSION_CONFLICT`."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    bodies = [views_body(0, edit_of(seeded.views[0], kind=kind)) for kind in ("plan", "roof")]
    async with make_api_client(api_app) as client_a, make_api_client(api_app) as client_b:
        responses = await asyncio.gather(
            client_a.put(path, json=bodies[0], headers=stage.headers),
            client_b.put(path, json=bodies[1], headers=stage.headers),
        )
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_drawing_sets_replace_views__C16(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Tên tầng mới NFD → lưu và trả NFC."""
    stage, seeded = await _seed(db_session, local_storage)
    target = {"mode": "newFloor", "floorId": NEW_LEVEL, "name": NFD_FLOOR}
    body = views_body(0, edit_of(seeded.views[0], kind="plan", target=target))
    wire = (
        await api_client.put(views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers)
    ).json()
    nfc = unicodedata.normalize("NFC", NFD_FLOOR)
    assert wire["views"][0]["target"] == {"mode": "newFloor", "floorId": NEW_LEVEL, "name": nfc}
    assert (await _views(db_sessionmaker, seeded.drawing_set.id))[0].new_floor_name == nfc


async def test_drawing_sets_replace_views__C17(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Hình chưa đích, chưa số tầng, chưa xác nhận → `target`, `storeyNo`, `uploadId`, `titleText` vắng khoá."""
    stage, seeded = await _seed(db_session, local_storage)
    wire = (
        await api_client.put(
            views_path(stage.project_id, seeded.drawing_set.id),
            json=views_body(0, edit_of(seeded.views[0])),
            headers=stage.headers,
        )
    ).json()
    assert {"target", "storeyNo", "uploadId", "titleText", "storeyLabel", "storeyConfidence"}.isdisjoint(
        wire["views"][0]
    )


@pytest.mark.parametrize(
    ("status", "code"), [("analyzing", "DRAWING_SET_NOT_READY"), ("confirmed", "DRAWING_SET_CONFIRMED")]
)
async def test_replace_views_only_while_in_review(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage, status: str, code: str
) -> None:
    """Bộ chưa tới bước duyệt → 409 `DRAWING_SET_NOT_READY`; đã xác nhận → 409 `DRAWING_SET_CONFIRMED`."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, status=status)
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=views_body(0), headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (409, code)


async def test_replace_views_conflict_after_confirm_reports_confirmed(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Bộ đã xác nhận sau một lượt lưu → 409 `DRAWING_SET_CONFIRMED`, không phải xung đột version."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    assert (await api_client.put(path, json=views_body(0), headers=stage.headers)).status_code == 200
    async with db_sessionmaker() as session:
        row = (
            await session.execute(select(DrawingSetRow).where(DrawingSetRow.id == seeded.drawing_set.id))
        ).scalar_one()
        row.status = "confirmed"
        await session.commit()
    response = await api_client.put(path, json=views_body(1), headers=stage.headers)
    assert response.json()["code"] == "DRAWING_SET_CONFIRMED"


async def test_drawing_sets_replace_views__disabled(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Công tắc tắt → 503 `DRAWING_SET_DISABLED`."""
    stage, seeded = await _seed(db_session, local_storage)
    monkeypatch.setenv("DRAWING_SET_ENABLED", "false")
    reset_drawing_set_settings_cache()
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=views_body(0), headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (503, "DRAWING_SET_DISABLED")


async def test_replace_views_renames_set_and_replay_is_idempotent(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """K-65: `body.name` đổi tên bộ (trim + NFC); gửi lại y hệt → 200 không đổi gì; thân không có `name` giữ tên."""
    stage, seeded = await _seed(db_session, local_storage)
    path = views_path(stage.project_id, seeded.drawing_set.id)
    body = views_body(0, edit_of(seeded.views[0]))
    body["body"]["name"] = f"  {unicodedata.normalize('NFD', 'Nhà phố An')} "
    first = await api_client.put(path, json=body, headers=stage.headers)
    assert (first.status_code, first.json()["name"], first.json()["revision"]) == (200, "Nhà phố An", 1)
    replay = await api_client.put(path, json=body, headers=stage.headers)
    assert (replay.status_code, replay.json()["name"], replay.json()["revision"]) == (200, "Nhà phố An", 1)
    async with db_sessionmaker() as session:
        stmt = select(DrawingSetRow).where(DrawingSetRow.id == seeded.drawing_set.id)
        row = (await session.execute(stmt)).scalar_one()
    assert row.name == "Nhà phố An"
    keep = await api_client.put(path, json=views_body(1, edit_of(seeded.views[0])), headers=stage.headers)
    assert (keep.status_code, keep.json()["name"], keep.json()["revision"]) == (200, "Nhà phố An", 2)
    again = views_body(2, edit_of(seeded.views[0]))
    again["body"]["name"] = "Tên khác"
    assert (await api_client.put(path, json=again, headers=stage.headers)).json()["name"] == "Tên khác"


@pytest.mark.parametrize("name", ["", "   ", "x" * 121, 5])
async def test_replace_views_rejects_bad_name(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage, name: object
) -> None:
    """K-65: tên rỗng, toàn khoảng trắng, quá 120 ký tự hay không phải chuỗi → 422 `field: "body.name"`."""
    stage, seeded = await _seed(db_session, local_storage)
    body = views_body(0, edit_of(seeded.views[0]))
    body["body"]["name"] = name
    response = await api_client.put(
        views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers
    )
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "body.name"
