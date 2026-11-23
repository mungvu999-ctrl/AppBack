"""Case của N54 `drawing_sets_create_set` (G + C11 U08 U09 U12), N55 `drawing_sets_upload_chunk` (T-chunk), N56
`drawing_sets_complete_set` (T-complete +ngoài, C10 riêng, C18) — K-32a.

Postgres, Redis và kho local thật qua app thật (`api_client`); mọi khẳng định "đã ghi" đọc lại bằng session mới (K22).
"""

import asyncio
import hashlib
import unicodedata
from typing import Final

import httpx
import pytest
from fastapi import FastAPI
from redis import Redis as SyncRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.drawing_sets.tests._set_helpers import (
    DEFAULT_QUEUE,
    MISSING_FILE,
    MISSING_SET,
    chunk_path,
    complete_path,
    create_body,
    create_set,
    send_file,
    sets_path,
    upload_set,
)
from apps.api.drawing_sets.tests._set_helpers import (
    broker as broker,
)
from apps.api.drawing_sets.tests._set_helpers import (
    drawing_set_on as drawing_set_on,
)
from apps.api.drawings.tests._upload_helpers import (
    Stage,
    chunk_body,
    headers_of,
    jpeg_bytes,
    make_stage,
    pdf_bytes,
    upload_png,
)
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.access import activity_rows, assert_one_activity
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import queued_payloads

PNG_MIME: Final = "image/png"
PDF_MIME: Final = "application/pdf"
NFD_NAME: Final = unicodedata.normalize("NFD", "Bộ hồ sơ nhà phố")

pytestmark = pytest.mark.usefixtures("drawing_set_on")


async def _set_row(sessionmaker: async_sessionmaker[AsyncSession], set_id: str) -> DrawingSetRow:
    """Dòng bộ đọc lại bằng session mới (K22)."""
    async with sessionmaker() as session:
        return (await session.execute(select(DrawingSetRow).where(DrawingSetRow.id == set_id))).scalar_one()


async def _file_rows(sessionmaker: async_sessionmaker[AsyncSession], set_id: str) -> list[DrawingSetFileRow]:
    """Tệp của bộ theo thứ tự khai, đọc lại bằng session mới."""
    async with sessionmaker() as session:
        stmt = select(DrawingSetFileRow).where(DrawingSetFileRow.set_id == set_id).order_by(DrawingSetFileRow.ord)
        return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------------- N54


async def test_drawing_sets_create_set__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Hai tệp hợp lệ → 201 `DrawingSet` `receiving`, tệp đúng thứ tự, số khúc theo `UPLOAD_CHUNK_BYTES` (1 KiB)."""
    stage = await make_stage(db_session)
    body = create_body(("mat-bang.pdf", 2500, PDF_MIME), ("mat-cat.png", 900, PNG_MIME), name="Nhà phố")

    response = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)

    assert response.status_code == 201, response.text
    wire = response.json()
    assert (wire["status"], wire["revision"], wire["progressPercent"], wire["name"]) == ("receiving", 0, 0, "Nhà phố")
    assert [(f["fileName"], f["chunkCount"], f["status"]) for f in wire["files"]] == [
        ("mat-bang.pdf", 3, "receiving"),
        ("mat-cat.png", 1, "receiving"),
    ]
    assert wire["views"] == []
    assert (await _set_row(db_sessionmaker, wire["id"])).created_by == stage.scene.user.id


async def test_drawing_sets_create_set__C02(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Không tệp nào → 422 `VALIDATION` `field:"files"`."""
    stage = await make_stage(db_session)
    response = await api_client.post(sets_path(stage.project_id), json={"files": []}, headers=stage.headers)
    assert response.status_code == 422
    assert (response.json()["code"], response.json()["field"]) == ("VALIDATION", "files")


@pytest.mark.parametrize(
    ("files", "field"),
    [
        ([("a.png", 101 * 1024 * 1024, PNG_MIME)], "files.0.sizeBytes"),
        ([("a.pdf", 90 * 1024 * 1024, PDF_MIME)] * 3, "files"),
    ],
)
async def test_drawing_sets_create_set__C02_size_limits(
    api_client: httpx.AsyncClient, db_session: AsyncSession, files: list[tuple[str, int, str]], field: str
) -> None:
    """Một tệp quá `UPLOAD_MAX_BYTES` → `files.<i>.sizeBytes`; cả bộ quá 200 MiB → `files`."""
    stage = await make_stage(db_session)
    response = await api_client.post(sets_path(stage.project_id), json=create_body(*files), headers=stage.headers)
    assert response.status_code == 422
    assert response.json()["field"] == field


async def test_drawing_sets_create_set__C02_too_many_files(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """21 tệp → 422 `field:"files"` (trần 20)."""
    stage = await make_stage(db_session)
    body = create_body(*[(f"t{i}.png", 10, PNG_MIME) for i in range(21)])
    response = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    assert (response.status_code, response.json()["field"]) == (422, "files")


async def test_drawing_sets_create_set__C03(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khoá lạ → 422 `VALIDATION`."""
    stage = await make_stage(db_session)
    body = create_body(("a.png", 10, PNG_MIME), khoaLa=1)
    response = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION")


async def test_drawing_sets_create_set__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án (vai `admin`) → 404 `resource:"project"`, kể cả khi công tắc tắt thì vẫn 404 trước."""
    stage = await make_stage(db_session)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.post(
        sets_path(stage.project_id), json=create_body(("a.png", 10, PNG_MIME)), headers=headers_of(outsider)
    )
    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_drawing_sets_create_set__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thành viên `viewer` (không `floor.upload`) → 403."""
    owner = await make_user(db_session)
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    await db_session.commit()
    response = await api_client.post(
        sets_path(project.id), json=create_body(("a.png", 10, PNG_MIME)), headers=headers_of(viewer, role="viewer")
    )
    assert response.status_code == 403


async def test_drawing_sets_create_set__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    owner = await make_user(db_session)
    project = await make_project(db_session, owner=owner, deleted_at=fake_clock.now())
    await db_session.commit()
    response = await api_client.post(
        sets_path(project.id), json=create_body(("a.png", 10, PNG_MIME)), headers=headers_of(owner)
    )
    assert response.status_code == 404


async def test_drawing_sets_create_set__C11(
    api_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quá `DRAWING_SET_CREATE_RATE_LIMIT` → 429 `RATE_LIMITED` + `Retry-After`."""
    monkeypatch.setenv("DRAWING_SET_CREATE_RATE_LIMIT", "1")
    reset_drawing_set_settings_cache()
    stage = await make_stage(db_session)
    body = create_body(("a.png", 10, PNG_MIME))
    first = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    second = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    assert first.status_code == 201
    assert (second.status_code, second.json()["code"]) == (429, "RATE_LIMITED")
    assert "retry-after" in second.headers


async def test_drawing_sets_create_set__C16(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Tên bộ và tên tệp NFD → lưu và trả NFC."""
    stage = await make_stage(db_session)
    file_name = unicodedata.normalize("NFD", "mặt-bằng.png")
    body = create_body((file_name, 10, PNG_MIME), name=NFD_NAME)
    response = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    assert response.status_code == 201
    nfc_name = unicodedata.normalize("NFC", NFD_NAME)
    assert (response.json()["name"], response.json()["files"][0]["fileName"]) == (nfc_name, "mặt-bằng.png")
    assert (await _set_row(db_sessionmaker, response.json()["id"])).name == nfc_name


async def test_drawing_sets_create_set__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Không tên, chưa hỏng, chưa xác nhận → `name`, `errorCode`, `confirmedAt` vắng khoá; tệp không `rejectedCode`."""
    stage = await make_stage(db_session)
    response = await api_client.post(
        sets_path(stage.project_id), json=create_body(("a.png", 10, PNG_MIME)), headers=stage.headers
    )
    wire = response.json()
    assert {"name", "errorCode", "confirmedAt"}.isdisjoint(wire)
    assert {"rejectedCode", "pageCount"}.isdisjoint(wire["files"][0])
    assert None not in wire.values()


async def test_drawing_sets_create_set__C18(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Một dòng nhật ký `drawingset.create`, actor từ token, mã = id bộ."""
    stage = await make_stage(db_session)
    created = await create_set(api_client, stage, ("a.png", b"x" * 10, PNG_MIME))
    await assert_one_activity(
        db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.DRAWING_SET_CREATE, object_code=created["id"]
    )


@pytest.mark.parametrize(("name", "mime"), [("a.png", PDF_MIME), ("a.gif", "image/gif")])
async def test_drawing_sets_create_set__U08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, name: str, mime: str
) -> None:
    """MIME lệch đuôi hay đuôi lạ → 422 `FILE_TYPE_MISMATCH` ngay ở N54."""
    stage = await make_stage(db_session)
    response = await api_client.post(
        sets_path(stage.project_id), json=create_body((name, 10, mime)), headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (422, "FILE_TYPE_MISMATCH")


async def test_drawing_sets_create_set__U09(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`File.type` rỗng → nhận (magic bytes quyết ở N56)."""
    stage = await make_stage(db_session)
    response = await api_client.post(
        sets_path(stage.project_id), json=create_body(("a.png", 10, "")), headers=stage.headers
    )
    assert response.status_code == 201


async def test_drawing_sets_create_set__U12(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tệp `.dwg` → 422 `CAD_NOT_SUPPORTED`."""
    stage = await make_stage(db_session)
    body = create_body(("a.png", 10, PNG_MIME), ("nha.dwg", 10, ""))
    response = await api_client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    assert (response.status_code, response.json()["code"]) == (422, "CAD_NOT_SUPPORTED")


async def test_drawing_sets_create_set__disabled(
    api_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Công tắc tắt → 503 `DRAWING_SET_DISABLED` + `Retry-After` (thành viên có quyền)."""
    monkeypatch.setenv("DRAWING_SET_ENABLED", "false")
    reset_drawing_set_settings_cache()
    stage = await make_stage(db_session)
    response = await api_client.post(
        sets_path(stage.project_id), json=create_body(("a.png", 10, PNG_MIME)), headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (503, "DRAWING_SET_DISABLED")
    assert response.headers["retry-after"] == "10"


# --------------------------------------------------------------------------- N55


async def _one_file(client: httpx.AsyncClient, stage: Stage, data: bytes) -> tuple[str, str]:
    """N54 cho một PNG; trả `(set_id, file_id)`."""
    created = await create_set(client, stage, ("a.png", data, PNG_MIME))
    return created["id"], created["files"][0]["id"]


async def test_drawing_sets_upload_chunk__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Một khúc → 200 `DrawingSetFile` `receiving`; object khúc nằm dưới tiền tố bộ."""
    stage = await make_stage(db_session)
    data = upload_png(600)
    set_id, file_id = await _one_file(api_client, stage, data)
    response = await api_client.post(
        chunk_path(stage.project_id, set_id, file_id), json=chunk_body(data, 0), headers=stage.headers
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": file_id,
        "fileName": "a.png",
        "sizeBytes": 600,
        "chunkCount": 1,
        "status": "receiving",
    }
    keys = [info.key async for info in local_storage.list_prefix(f"projects/{stage.project_id}/drawing-sets/{set_id}/")]
    assert keys == [
        f"projects/{stage.project_id}/drawing-sets/{set_id}/files/{file_id}/chunks/0/{hashlib.sha256(data).hexdigest()}"
    ]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"chunk": "", "chunkIndex": 0}, "chunk"),
        ({"chunk": "QQ==", "chunkIndex": 5}, "chunkIndex"),
        ({"chunk": "%%%", "chunkIndex": 0}, "chunk"),
    ],
)
async def test_drawing_sets_upload_chunk__C02(
    api_client: httpx.AsyncClient, db_session: AsyncSession, body: dict[str, object], field: str
) -> None:
    """Khúc rỗng, chỉ số ngoài số khúc, base64 hỏng → 422 `VALIDATION` kèm đúng `field`."""
    stage = await make_stage(db_session)
    set_id, file_id = await _one_file(api_client, stage, upload_png(600))
    response = await api_client.post(chunk_path(stage.project_id, set_id, file_id), json=body, headers=stage.headers)
    assert (response.status_code, response.json()["field"]) == (422, field)


async def test_drawing_sets_upload_chunk__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án → 404 `resource:"project"`."""
    stage = await make_stage(db_session)
    set_id, file_id = await _one_file(api_client, stage, upload_png(600))
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.post(
        chunk_path(stage.project_id, set_id, file_id), json=chunk_body(b"x", 0), headers=headers_of(outsider)
    )
    assert (response.status_code, response.json()["resource"]) == (404, "project")


@pytest.mark.parametrize(("which", "resource"), [("set", "drawingSet"), ("file", "drawingSetFile")])
async def test_drawing_sets_upload_chunk__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, which: str, resource: str
) -> None:
    """Bộ không có → 404 `drawingSet`; tệp không thuộc bộ → 404 `drawingSetFile`."""
    stage = await make_stage(db_session)
    set_id, file_id = await _one_file(api_client, stage, upload_png(600))
    path = (
        chunk_path(stage.project_id, MISSING_SET, file_id)
        if which == "set"
        else chunk_path(stage.project_id, set_id, MISSING_FILE)
    )
    response = await api_client.post(path, json=chunk_body(b"x", 0), headers=stage.headers)
    assert (response.status_code, response.json()["resource"]) == (404, resource)


async def test_drawing_sets_upload_chunk__U10(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Gửi lại cùng chỉ số với nội dung khác → ghi đè (một dòng khúc, khoá mới)."""
    stage = await make_stage(db_session)
    data = upload_png(600)
    set_id, file_id = await _one_file(api_client, stage, data)
    path = chunk_path(stage.project_id, set_id, file_id)
    for payload in (b"y" * 600, data):
        assert (await api_client.post(path, json=chunk_body(payload, 0), headers=stage.headers)).status_code == 200
    response = await api_client.post(
        complete_path(stage.project_id, set_id), json={"setId": set_id}, headers=stage.headers
    )
    assert response.json()["files"][0]["status"] == "complete"


async def test_drawing_sets_upload_chunk__not_receiving(
    api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis
) -> None:
    """Bộ đã chốt → 409 `DRAWING_SET_NOT_RECEIVING`."""
    stage = await make_stage(db_session)
    data = upload_png(600)
    set_id, response = await upload_set(api_client, stage, ("a.png", data, PNG_MIME))
    assert response.status_code == 200
    file_id = response.json()["files"][0]["id"]
    again = await api_client.post(
        chunk_path(stage.project_id, set_id, file_id), json=chunk_body(data, 0), headers=stage.headers
    )
    assert (again.status_code, again.json()["code"]) == (409, "DRAWING_SET_NOT_RECEIVING")


# --------------------------------------------------------------------------- N56


async def test_drawing_sets_complete_set__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
) -> None:
    """PDF 3 trang + JPEG → 200 `analyzing`, tệp `complete` có số trang, gốc trong kho, một tác vụ phân tích."""
    stage = await make_stage(db_session)
    pdf, jpeg = pdf_bytes(3), jpeg_bytes(700)
    set_id, response = await upload_set(
        api_client, stage, ("ho-so.pdf", pdf, PDF_MIME), ("anh.jpg", jpeg, "image/jpeg")
    )
    assert response.status_code == 200, response.text
    wire = response.json()
    assert (wire["status"], wire["progressPercent"]) == ("analyzing", 0)
    assert [(f["status"], f["pageCount"]) for f in wire["files"]] == [("complete", 3), ("complete", 1)]
    rows = await _file_rows(db_sessionmaker, set_id)
    assert rows[0].content_sha256 == hashlib.sha256(pdf).hexdigest()
    assert rows[0].original_key is not None
    assert (await local_storage.stat(rows[0].original_key)) is not None
    assert (await _set_row(db_sessionmaker, set_id)).analysis_started_at is not None
    assert queued_payloads(broker, DEFAULT_QUEUE) == [{"schema_version": 1, "set_id": set_id}]


async def test_drawing_sets_complete_set__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án → 404 `resource:"project"`."""
    stage = await make_stage(db_session)
    set_id, _ = await _one_file(api_client, stage, upload_png(600))
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    response = await api_client.post(
        complete_path(stage.project_id, set_id), json={"setId": set_id}, headers=headers_of(outsider)
    )
    assert (response.status_code, response.json()["resource"]) == (404, "project")


async def test_drawing_sets_complete_set__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Bộ không có → 404 `resource:"drawingSet"`."""
    stage = await make_stage(db_session)
    response = await api_client.post(
        complete_path(stage.project_id, MISSING_SET), json={"setId": MISSING_SET}, headers=stage.headers
    )
    assert (response.status_code, response.json()["resource"]) == (404, "drawingSet")


async def test_drawing_sets_complete_set__C14(
    api_app: FastAPI, api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis
) -> None:
    """Hai N56 song song → cả hai 200 `analyzing`, đúng **một** tác vụ phân tích."""
    stage = await make_stage(db_session)
    data = upload_png(900)
    set_id, file_id = await _one_file(api_client, stage, data)
    await send_file(api_client, stage, set_id, file_id, data)
    path, body = complete_path(stage.project_id, set_id), {"setId": set_id}
    async with make_api_client(api_app) as client_a, make_api_client(api_app) as client_b:
        first, second = await asyncio.gather(
            client_a.post(path, json=body, headers=stage.headers), client_b.post(path, json=body, headers=stage.headers)
        )
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert {first.json()["status"], second.json()["status"]} == {"analyzing"}
    assert len(queued_payloads(broker, DEFAULT_QUEUE)) == 1


async def test_drawing_sets_complete_set__C21(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`setId` trong thân khác đường → 422 `PATH_BODY_MISMATCH`."""
    stage = await make_stage(db_session)
    set_id, _ = await _one_file(api_client, stage, upload_png(600))
    response = await api_client.post(
        complete_path(stage.project_id, set_id), json={"setId": MISSING_SET}, headers=stage.headers
    )
    assert (response.status_code, response.json()["code"]) == (422, "PATH_BODY_MISMATCH")


async def test_drawing_sets_complete_set__U07(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
    local_storage: LocalDiskStorage,
) -> None:
    """PNG cụt bên cạnh PNG lành → tệp cụt `rejected` `FILE_CORRUPT` (gốc dở bị xoá), bộ vẫn `analyzing`."""
    stage = await make_stage(db_session)
    _, response = await upload_set(
        api_client,
        stage,
        ("hong.png", upload_png(600, truncated=True), PNG_MIME),
        ("lanh.png", upload_png(600), PNG_MIME),
    )
    wire = response.json()
    assert wire["status"] == "analyzing"
    assert [(f["status"], f.get("rejectedCode")) for f in wire["files"]] == [
        ("rejected", "FILE_CORRUPT"),
        ("complete", None),
    ]
    rows = await _file_rows(db_sessionmaker, wire["id"])
    assert rows[0].original_key is None


async def test_drawing_sets_complete_set__U10(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
) -> None:
    """Tệp thiếu khúc là tệp duy nhất → `rejected` `UPLOAD_INCOMPLETE`, bộ `failed` `DRAWING_SET_ALL_FILES_REJECTED`."""
    stage = await make_stage(db_session)
    set_id, _ = await _one_file(api_client, stage, upload_png(1500))
    response = await api_client.post(
        complete_path(stage.project_id, set_id), json={"setId": set_id}, headers=stage.headers
    )
    wire = response.json()
    assert (wire["status"], wire["errorCode"]) == ("failed", "DRAWING_SET_ALL_FILES_REJECTED")
    assert wire["files"][0]["rejectedCode"] == "UPLOAD_INCOMPLETE"
    assert queued_payloads(broker, DEFAULT_QUEUE) == []


async def test_drawing_sets_complete_set__U11(
    api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis
) -> None:
    """Tổng byte khác `sizeBytes` khai (khúc cuối ngắn hơn) → `rejected` `UPLOAD_SIZE_MISMATCH`."""
    stage = await make_stage(db_session)
    data = upload_png(600)
    set_id, file_id = await _one_file(api_client, stage, data)
    await api_client.post(
        chunk_path(stage.project_id, set_id, file_id), json=chunk_body(data[:500], 0), headers=stage.headers
    )
    response = await api_client.post(
        complete_path(stage.project_id, set_id), json={"setId": set_id}, headers=stage.headers
    )
    assert response.json()["files"][0]["rejectedCode"] == "UPLOAD_SIZE_MISMATCH"


@pytest.mark.parametrize(
    ("name", "data", "mime", "code"),
    [
        ("a.png", jpeg_bytes(600), PNG_MIME, "FILE_TYPE_MISMATCH"),
        ("a.png", b"AC1032" + b"\x00" * 600, PNG_MIME, "CAD_NOT_SUPPORTED"),
        ("a.pdf", pdf_bytes(1, encrypt="user_password"), PDF_MIME, "PDF_ENCRYPTED"),
    ],
)
async def test_complete_set_rejects_bad_content(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    broker: SyncRedis,
    name: str,
    data: bytes,
    mime: str,
    code: str,
) -> None:
    """Magic bytes lệch (U08), DWG đổi tên (U12 lần hai), PDF mã hoá (U04) → tệp `rejected` đúng mã."""
    stage = await make_stage(db_session)
    _, response = await upload_set(api_client, stage, (name, data, mime))
    assert response.json()["files"][0]["rejectedCode"] == code


async def test_complete_set_too_many_pages(
    api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis
) -> None:
    """PDF 21 trang → 422 `DRAWING_SET_TOO_MANY_PAGES`, bộ giữ `receiving`, không tác vụ."""
    stage = await make_stage(db_session)
    _, response = await upload_set(api_client, stage, ("dai.pdf", pdf_bytes(21), PDF_MIME))
    assert (response.status_code, response.json()["code"]) == (422, "DRAWING_SET_TOO_MANY_PAGES")
    assert queued_payloads(broker, DEFAULT_QUEUE) == []


async def test_drawing_sets_complete_set__C18(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
) -> None:
    """Một dòng nhật ký `drawingset.submit` cho bộ vừa chốt."""
    stage = await make_stage(db_session)
    set_id, _ = await upload_set(api_client, stage, ("a.png", upload_png(600), PNG_MIME))
    await assert_one_activity(
        db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.DRAWING_SET_SUBMIT, object_code=set_id
    )


async def test_drawing_sets_complete_set__C10_queue_once(
    api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis
) -> None:
    """Lặp `Idempotency-Key` → cùng response, **một** tác vụ phân tích."""
    stage = await make_stage(db_session)
    data = upload_png(600)
    set_id, file_id = await _one_file(api_client, stage, data)
    await send_file(api_client, stage, set_id, file_id, data)
    headers = {**stage.headers, "Idempotency-Key": "k32a-complete-once"}
    path = complete_path(stage.project_id, set_id)
    first = await api_client.post(path, json={"setId": set_id}, headers=headers)
    second = await api_client.post(path, json={"setId": set_id}, headers=headers)
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert len(queued_payloads(broker, DEFAULT_QUEUE)) == 1


async def test_drawing_sets_complete_set__C10_no_key_replay(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    broker: SyncRedis,
) -> None:
    """Gửi lại N56 **không** khoá → bộ hiện tại, vẫn một tác vụ và một dòng nhật ký."""
    stage = await make_stage(db_session)
    set_id, first = await upload_set(api_client, stage, ("a.png", upload_png(600), PNG_MIME))
    second = await api_client.post(
        complete_path(stage.project_id, set_id), json={"setId": set_id}, headers=stage.headers
    )
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["status"] == "analyzing"
    assert len(queued_payloads(broker, DEFAULT_QUEUE)) == 1
    rows = await activity_rows(db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.DRAWING_SET_SUBMIT)
    assert len(rows) == 1


async def test_drawing_sets_complete_set__disabled(
    api_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Công tắc tắt giữa chừng → N55 và N56 đều 503 `DRAWING_SET_DISABLED`."""
    stage = await make_stage(db_session)
    set_id, file_id = await _one_file(api_client, stage, upload_png(600))
    monkeypatch.setenv("DRAWING_SET_ENABLED", "false")
    reset_drawing_set_settings_cache()
    chunk = await api_client.post(
        chunk_path(stage.project_id, set_id, file_id), json=chunk_body(b"x", 0), headers=stage.headers
    )
    done = await api_client.post(complete_path(stage.project_id, set_id), json={"setId": set_id}, headers=stage.headers)
    assert [(r.status_code, r.json()["code"]) for r in (chunk, done)] == [(503, "DRAWING_SET_DISABLED")] * 2
