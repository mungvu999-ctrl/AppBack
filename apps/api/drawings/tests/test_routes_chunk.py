"""Case của #6 `drawings_upload_chunk` (case T-chunk: C01 C02 C06 C08 U10).

Không C21: thân `{chunk, chunkIndex}` không lặp id nào của đường. Kho là `local_storage`
thật (cùng gốc với app nhờ `api_env`), nên mỗi test đọc lại object vừa ghi chứ không tin
vào response.
"""

import asyncio
import base64
from collections.abc import Iterator
from typing import Final

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawings.settings import reset_drawings_settings_cache
from apps.api.drawings.tests._upload_helpers import (
    SMALL_CHUNK_BYTES,
    GatedStorage,
    Stage,
    chunk_body,
    chunks_path,
    headers_of,
    init_body,
    init_path,
    make_stage,
    upload_png,
)
from packages.db.models.drawings import UploadChunkRow, UploadRow
from packages.storage.port import ObjectStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawings import make_upload
from packages.testing.fixtures.api import make_api_client

GATE_WAIT_S: Final = 10.0


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`UPLOAD_CHUNK_BYTES = 1 KiB`: mọi test ở đây cần nhiều khúc, không cần nhiều byte."""
    monkeypatch.setenv("UPLOAD_CHUNK_BYTES", str(SMALL_CHUNK_BYTES))
    reset_drawings_settings_cache()
    yield
    reset_drawings_settings_cache()


async def _start_upload(client: httpx.AsyncClient, stage: Stage, size: int) -> str:
    """Gọi #5 và trả `upload_id` — mọi test của #6 bắt đầu từ một lượt tải thật."""
    body = init_body(stage.project_id, stage.level_id, size_bytes=size)
    response = await client.post(init_path(stage.project_id, stage.level_id), json=body, headers=stage.headers)
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


async def _chunk_rows(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str) -> list[UploadChunkRow]:
    """Mọi dòng `upload_chunks` của một lượt tải, đọc bằng session mới (K22)."""
    async with sessionmaker() as session:
        stmt = select(UploadChunkRow).where(UploadChunkRow.upload_id == upload_id).order_by(UploadChunkRow.chunk_index)
        return list((await session.execute(stmt)).scalars())


async def test_drawings_upload_chunk__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: ObjectStorage,
) -> None:
    """Một khúc hợp lệ → 200 `Progress`, một dòng `upload_chunks` và object thật trong kho."""
    stage = await make_stage(db_session)
    upload_id = await _start_upload(api_client, stage, 600)
    piece = upload_png(600)[:600]

    response = await api_client.post(
        chunks_path(stage.project_id, upload_id), json=chunk_body(piece, 0), headers=stage.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    rows = await _chunk_rows(db_sessionmaker, upload_id)
    assert len(rows) == 1
    assert rows[0].size_bytes == 600
    stored = await local_storage.stat(rows[0].object_key)
    assert stored is not None
    assert stored.size == 600


async def test_drawings_upload_chunk__C02(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`chunkIndex` ngoài `0…chunk_count-1` → 422 `VALIDATION` `field:"chunkIndex"`."""
    stage = await make_stage(db_session)
    upload_id = await _start_upload(api_client, stage, 600)

    response = await api_client.post(
        chunks_path(stage.project_id, upload_id), json=chunk_body(b"x" * 10, 7), headers=stage.headers
    )

    assert response.status_code == 422
    assert response.json()["field"] == "chunkIndex"


async def test_drawings_upload_chunk__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án → 404 `resource:"project"`.
    Catalog: B09-drw-006.
    """
    stage = await make_stage(db_session)
    upload_id = await _start_upload(api_client, stage, 600)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await api_client.post(
        chunks_path(stage.project_id, upload_id), json=chunk_body(b"x" * 10, 0), headers=headers_of(outsider)
    )

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_drawings_upload_chunk__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Lượt tải không tồn tại → 404 `resource:"upload"`."""
    stage = await make_stage(db_session)

    response = await api_client.post(
        chunks_path(stage.project_id, "upl_00000000000000000000000000"),
        json=chunk_body(b"x" * 10, 0),
        headers=stage.headers,
    )

    assert response.status_code == 404
    assert response.json()["resource"] == "upload"


async def test_drawings_upload_chunk__U10(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Khúc sai thứ tự vẫn nhận; gửi lại cùng chỉ số là **ghi đè**, không thành dòng thứ hai."""
    stage = await make_stage(db_session)
    upload_id = await _start_upload(api_client, stage, SMALL_CHUNK_BYTES + 200)
    path = chunks_path(stage.project_id, upload_id)

    last = await api_client.post(path, json=chunk_body(b"z" * 200, 1), headers=stage.headers)
    first = await api_client.post(path, json=chunk_body(b"a" * SMALL_CHUNK_BYTES, 0), headers=stage.headers)
    again = await api_client.post(path, json=chunk_body(b"b" * SMALL_CHUNK_BYTES, 0), headers=stage.headers)

    assert [last.status_code, first.status_code, again.status_code] == [200, 200, 200]
    rows = await _chunk_rows(db_sessionmaker, upload_id)
    assert [row.chunk_index for row in rows] == [0, 1]
    assert rows[0].object_key.endswith(rows[0].sha256)
    assert rows[0].sha256 != rows[1].sha256


async def test_chunk_into_completed_upload_is_rejected(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khúc gửi vào lượt tải đã `complete` → 422 `UPLOAD_NOT_RECEIVING` (FE dừng ngay)."""
    stage = await make_stage(db_session)
    upload = await make_upload(
        db_session, project=stage.scene.project, floor=stage.scene.floor, status="complete", size_bytes=600
    )
    await db_session.commit()

    response = await api_client.post(
        chunks_path(stage.project_id, upload.id), json=chunk_body(b"x" * 10, 0), headers=stage.headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "UPLOAD_NOT_RECEIVING"


@pytest.mark.parametrize(
    ("raw", "label"),
    [("!!!not-base64!!!", "hỏng"), (base64.b64encode(b"x" * (SMALL_CHUNK_BYTES + 1)).decode(), "quá trần")],
)
async def test_chunk_payload_is_validated(
    api_client: httpx.AsyncClient, db_session: AsyncSession, raw: str, label: str
) -> None:
    """Base64 hỏng hay khúc quá `UPLOAD_CHUNK_BYTES` → 422 `field:"chunk"`."""
    stage = await make_stage(db_session)
    upload_id = await _start_upload(api_client, stage, SMALL_CHUNK_BYTES * 2)

    response = await api_client.post(
        chunks_path(stage.project_id, upload_id), json={"chunk": raw, "chunkIndex": 0}, headers=stage.headers
    )

    assert response.status_code == 422, label
    assert response.json()["field"] == "chunk"


async def test_non_final_chunk_must_be_full(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khúc không cuối mà thiếu byte → 422 `field:"chunk"`: nối lại sẽ ra tệp lệch."""
    stage = await make_stage(db_session)
    upload_id = await _start_upload(api_client, stage, SMALL_CHUNK_BYTES * 2)

    response = await api_client.post(
        chunks_path(stage.project_id, upload_id), json=chunk_body(b"a" * 10, 0), headers=stage.headers
    )

    assert response.status_code == 422
    assert response.json()["field"] == "chunk"


async def _gated_chunk(
    client: httpx.AsyncClient, app: FastAPI, stage: Stage, upload_id: str, data: bytes
) -> tuple[GatedStorage, "asyncio.Task[httpx.Response]"]:
    """Bắt đầu #6 và dừng nó lại đúng lúc đang ghi khúc lên kho (ngoài giao dịch, K36)."""
    gated = GatedStorage(app.state.storage, only="chunks")
    app.state.storage = gated
    task = asyncio.create_task(
        client.post(chunks_path(stage.project_id, upload_id), json=chunk_body(data, 0), headers=stage.headers)
    )
    await asyncio.wait_for(gated.entered.wait(), GATE_WAIT_S)
    return gated, task


async def test_chunk_404_when_upload_is_deleted_midway(api_app: FastAPI, db_session: AsyncSession) -> None:
    """Lượt tải biến mất trong lúc #6 đang ghi kho → 404, không dòng khúc mồ côi."""
    stage = await make_stage(db_session)
    async with make_api_client(api_app) as client:
        upload_id = await _start_upload(client, stage, 600)
        gated, task = await _gated_chunk(client, api_app, stage, upload_id, b"x" * 600)
        await db_session.execute(delete(UploadRow).where(UploadRow.id == upload_id))
        await db_session.commit()
        gated.release.set()
        response = await asyncio.wait_for(task, GATE_WAIT_S)

    assert response.status_code == 404, response.text
    assert response.json()["resource"] == "upload"


async def test_chunk_rejected_when_upload_completes_midway(api_app: FastAPI, db_session: AsyncSession) -> None:
    """Lượt tải chốt `complete` trong lúc #6 đang ghi kho → 422 `UPLOAD_NOT_RECEIVING`."""
    stage = await make_stage(db_session)
    async with make_api_client(api_app) as client:
        upload_id = await _start_upload(client, stage, 600)
        gated, task = await _gated_chunk(client, api_app, stage, upload_id, b"x" * 600)
        await db_session.execute(update(UploadRow).where(UploadRow.id == upload_id).values(status="complete"))
        await db_session.commit()
        gated.release.set()
        response = await asyncio.wait_for(task, GATE_WAIT_S)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "UPLOAD_NOT_RECEIVING"
