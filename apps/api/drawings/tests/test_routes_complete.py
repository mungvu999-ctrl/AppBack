"""Case của #7 `drawings_complete_upload` (T-complete: C01 C06 C08 C14 C21 U07 U10 U11 + C18).

C10 là case "+ngoài" nên phải có test **riêng** của op (cổng case): hai test cuối đếm
thông điệp `pipeline.cpu` trên Redis thật — lặp có khoá và lặp không khoá (như FE) đều chỉ
được xếp **một** lượt `pipeline.orchestrate.start`.
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterable, Iterator
from typing import Any, Final, cast

import httpx
import pytest
from fastapi import FastAPI
from redis import Redis as SyncRedis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.access.kinds import ActivityKind
from apps.api.drawings import complete
from apps.api.drawings.settings import reset_drawings_settings_cache
from apps.api.drawings.tests._helpers import sync_bus_reset as sync_bus_reset
from apps.api.drawings.tests._upload_helpers import (
    SMALL_CHUNK_BYTES,
    GatedStorage,
    Stage,
    broken_pdf_bytes,
    chunk_body,
    chunks_path,
    complete_path,
    headers_of,
    init_body,
    init_path,
    make_stage,
    pdf_bytes,
    send_chunks,
    split,
    upload_png,
    upload_through,
)
from apps.api.drawings.uploads import UploadFacts
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.core.errors import AppError
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.db.models.floors import FloorRow
from packages.messaging import broker_redis_sync
from packages.messaging.settings import get_messaging_settings
from packages.storage.keys import upload_prefix
from packages.storage.port import ObjectInfo, ObjectStorage
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.access import activity_rows
from packages.testing.fixtures.api import make_api_client
from packages.testing.fixtures.messaging import queued_payloads
from packages.vision.preprocess import VisionError

CPU_QUEUE: Final = "pipeline.cpu"
GATE_WAIT_S: Final = 10.0


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`UPLOAD_CHUNK_BYTES = 1 KiB`: các case của #7 cần nhiều khúc, không cần nhiều byte."""
    monkeypatch.setenv("UPLOAD_CHUNK_BYTES", str(SMALL_CHUNK_BYTES))
    reset_drawings_settings_cache()
    yield
    reset_drawings_settings_cache()


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Client Redis của broker; hàng `pipeline.cpu` dọn trước và sau vì broker là session-scope."""
    client = broker_redis_sync(get_messaging_settings())
    client.delete(CPU_QUEUE)
    yield client
    client.delete(CPU_QUEUE)
    client.close()


async def _start(client: httpx.AsyncClient, stage: Stage, data: bytes, **overrides: Any) -> str:
    """#5 cho một tệp; trả `upload_id`."""
    body = init_body(stage.project_id, stage.level_id, size_bytes=len(data), **overrides)
    response = await client.post(init_path(stage.project_id, stage.level_id), json=body, headers=stage.headers)
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


async def _upload_row(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str) -> UploadRow:
    """Dòng `uploads` đọc lại bằng session mới (K22)."""
    async with sessionmaker() as session:
        return (await session.execute(select(UploadRow).where(UploadRow.id == upload_id))).scalar_one()


async def _run_count(sessionmaker: async_sessionmaker[AsyncSession], upload_id: str) -> int:
    """Số lượt chạy của một lượt tải — C14 đòi đúng một."""
    async with sessionmaker() as session:
        stmt = select(func.count()).select_from(PipelineRunRow).where(PipelineRunRow.upload_id == upload_id)
        return int((await session.execute(stmt)).scalar_one())


async def test_drawings_complete_upload__C01(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: ObjectStorage,
    sync_bus_reset: None,
) -> None:
    """Tệp đủ khúc → 200 `Progress` `pending`, dòng `complete` và `original.png` trong kho."""
    stage = await make_stage(db_session)
    data = upload_png(2500)

    upload_id, response = await upload_through(api_client, stage, data)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire.pop("queueAhead") >= 0  # lượt vừa mở đã có chỗ trong hàng đợi (UX41 U02)
    assert wire == {"id": upload_id, "status": "pending", "step": "preprocess", "progressPercent": 0}
    row = await _upload_row(db_sessionmaker, upload_id)
    assert row.status == "complete"
    assert row.sniffed_kind == "png"
    assert row.content_sha256 == hashlib.sha256(data).hexdigest()  # UX41 P01: băm cả tệp gốc
    assert row.original_key is not None
    stored = await local_storage.stat(row.original_key)
    assert stored is not None
    assert stored.size == len(data)
    assert await _run_count(db_sessionmaker, upload_id) == 1


async def test_drawings_complete_upload__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Người ngoài dự án → 404 `resource:"project"`.
    Catalog: B09-drw-009.
    """
    stage = await make_stage(db_session)
    upload_id = await _start(api_client, stage, upload_png(600))
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await api_client.post(
        complete_path(stage.project_id, upload_id), json={"uploadId": upload_id}, headers=headers_of(outsider)
    )

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_drawings_complete_upload__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Lượt tải không tồn tại → 404 `resource:"upload"`."""
    stage = await make_stage(db_session)
    missing = "upl_00000000000000000000000000"

    response = await api_client.post(
        complete_path(stage.project_id, missing), json={"uploadId": missing}, headers=stage.headers
    )

    assert response.status_code == 404
    assert response.json()["resource"] == "upload"


async def test_drawings_complete_upload__C14(
    api_app: FastAPI,
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Hai #7 song song → cả hai 200, **một** lượt chạy và **một** dòng nhật ký."""
    stage = await make_stage(db_session)
    data = upload_png(1500)
    upload_id = await _start(api_client, stage, data)
    for response in await send_chunks(api_client, stage, upload_id, split(data)):
        assert response.status_code == 200, response.text

    path = complete_path(stage.project_id, upload_id)
    body = {"uploadId": upload_id}
    async with make_api_client(api_app) as client_a, make_api_client(api_app) as client_b:
        first, second = await asyncio.gather(
            client_a.post(path, json=body, headers=stage.headers),
            client_b.post(path, json=body, headers=stage.headers),
        )

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert await _run_count(db_sessionmaker, upload_id) == 1
    rows = await activity_rows(db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.FLOOR_UPLOAD_COMPLETE)
    assert len(rows) == 1


async def test_drawings_complete_upload__C21(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """`uploadId` trong thân khác đường → 422 `PATH_BODY_MISMATCH`."""
    stage = await make_stage(db_session)
    upload_id = await _start(api_client, stage, upload_png(600))

    response = await api_client.post(
        complete_path(stage.project_id, upload_id),
        json={"uploadId": "upl_00000000000000000000000000"},
        headers=stage.headers,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "PATH_BODY_MISMATCH"


async def test_drawings_complete_upload__U07(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: ObjectStorage,
    sync_bus_reset: None,
) -> None:
    """PNG thiếu `IEND` → 422 `FILE_CORRUPT`, dòng `rejected` và **không còn** `original.*`."""
    stage = await make_stage(db_session)
    data = upload_png(1500, truncated=True)

    upload_id, response = await upload_through(api_client, stage, data)

    assert response.status_code == 422
    assert response.json()["code"] == "FILE_CORRUPT"
    row = await _upload_row(db_sessionmaker, upload_id)
    assert row.status == "rejected"
    assert row.rejected_code == "FILE_CORRUPT"
    assert row.original_key is None
    assert await local_storage.stat(f"{_prefix(stage, upload_id)}original.png") is None


def _prefix(stage: Stage, upload_id: str) -> str:
    """Tiền tố object của một lượt tải (`keys.upload_prefix`)."""
    return upload_prefix(stage.project_id, stage.level_id, upload_id)


async def test_drawings_complete_upload__U10(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Thiếu khúc → 422 `UPLOAD_INCOMPLETE` kèm `count`, lượt tải vẫn `receiving`."""
    stage = await make_stage(db_session)
    data = upload_png(SMALL_CHUNK_BYTES * 3)
    upload_id = await _start(api_client, stage, data)
    pieces = split(data)
    assert len(pieces) == 3
    for response in await send_chunks(api_client, stage, upload_id, pieces[:1]):
        assert response.status_code == 200, response.text

    result = await api_client.post(
        complete_path(stage.project_id, upload_id), json={"uploadId": upload_id}, headers=stage.headers
    )

    assert result.status_code == 422
    assert result.json()["code"] == "UPLOAD_INCOMPLETE"
    assert result.json()["count"] == 2
    assert (await _upload_row(db_sessionmaker, upload_id)).status == "receiving"


async def test_drawings_complete_upload__U11(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Tổng byte thật khác `sizeBytes` khai → 422 `UPLOAD_SIZE_MISMATCH`, giữ `receiving`."""
    stage = await make_stage(db_session)
    upload_id = await _start(api_client, stage, b"x" * 900)
    for response in await send_chunks(api_client, stage, upload_id, [upload_png(500)[:500]]):
        assert response.status_code == 200, response.text

    result = await api_client.post(
        complete_path(stage.project_id, upload_id), json={"uploadId": upload_id}, headers=stage.headers
    )

    assert result.status_code == 422
    assert result.json()["code"] == "UPLOAD_SIZE_MISMATCH"
    assert (await _upload_row(db_sessionmaker, upload_id)).status == "receiving"


async def test_drawings_complete_upload__C18(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """Một dòng `activity_log` `floor.upload_complete`, actor lấy từ token."""
    stage = await make_stage(db_session)

    _, response = await upload_through(api_client, stage, upload_png(900))

    assert response.status_code == 200, response.text
    rows = await activity_rows(db_sessionmaker, actor_id=stage.scene.user.id, kind=ActivityKind.FLOOR_UPLOAD_COMPLETE)
    assert len(rows) == 1
    assert rows[0].object_code == stage.level_id


async def test_drawings_complete_upload__C10_queue_once(
    api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis, sync_bus_reset: None
) -> None:
    """Lặp `Idempotency-Key` → cùng response và **một** thông điệp `pipeline.cpu`."""
    stage = await make_stage(db_session)
    data = upload_png(900)
    upload_id = await _start(api_client, stage, data)
    for response in await send_chunks(api_client, stage, upload_id, split(data)):
        assert response.status_code == 200, response.text

    headers = {**stage.headers, "Idempotency-Key": "b2-04-complete-once"}
    path = complete_path(stage.project_id, upload_id)
    first = await api_client.post(path, json={"uploadId": upload_id}, headers=headers)
    second = await api_client.post(path, json={"uploadId": upload_id}, headers=headers)

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json()
    assert len(queued_payloads(broker, CPU_QUEUE)) == 1


async def test_drawings_complete_upload__C10_no_key_replay(
    api_client: httpx.AsyncClient, db_session: AsyncSession, broker: SyncRedis, sync_bus_reset: None
) -> None:
    """FE gửi lại #7 **không** `Idempotency-Key` → vẫn đúng một thông điệp (bước 1 của [6])."""
    stage = await make_stage(db_session)
    data = upload_png(900)
    upload_id = await _start(api_client, stage, data)
    for response in await send_chunks(api_client, stage, upload_id, split(data)):
        assert response.status_code == 200, response.text

    path = complete_path(stage.project_id, upload_id)
    first = await api_client.post(path, json={"uploadId": upload_id}, headers=stage.headers)
    second = await api_client.post(path, json={"uploadId": upload_id}, headers=stage.headers)

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json() == second.json()
    assert len(queued_payloads(broker, CPU_QUEUE)) == 1


# ---------------------------------------------------------------------------
# Nhánh lỗi của bước 3-7 (PDF không đọc được, và dữ liệu đổi giữa chừng)
# ---------------------------------------------------------------------------


async def test_complete_rejects_unreadable_pdf(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sync_bus_reset: None,
) -> None:
    """PDF có mật khẩu người dùng → 422 `PDF_ENCRYPTED` (mã của gói ảnh đi thẳng ra dây, UX41 U29)."""
    stage = await make_stage(db_session)

    upload_id, response = await upload_through(
        api_client, stage, pdf_bytes(1, encrypt="user_password"), file_name="ho-so.pdf", mime_type="application/pdf"
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "PDF_ENCRYPTED"
    assert (await _upload_row(db_sessionmaker, upload_id)).rejected_code == "PDF_ENCRYPTED"


async def test_complete_logs_pdfium_cause_for_corrupt_pdf(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    sync_bus_reset: None,
) -> None:
    """NO-124: `FILE_CORRUPT` do `PdfiumError` ghi `__cause__` mức warning trước khi trả W7."""
    caplog.set_level(logging.WARNING, logger="apps.api.drawings.complete")
    stage = await make_stage(db_session)

    _, response = await upload_through(
        api_client, stage, broken_pdf_bytes(), file_name="ho-so.pdf", mime_type="application/pdf"
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "FILE_CORRUPT"
    causes = [record.__dict__["cause"] for record in caplog.records if record.msg == "pdf_page_count_failed"]
    assert len(causes) == 1
    assert "PdfiumError" in causes[0]


async def _gated_complete(
    client: httpx.AsyncClient, app: FastAPI, stage: Stage, upload_id: str
) -> tuple[GatedStorage, "asyncio.Task[httpx.Response]"]:
    """Bắt đầu #7 và dừng nó lại đúng lúc đang ghi `original.*` (ngoài giao dịch)."""
    gated = GatedStorage(app.state.storage, only="original")
    app.state.storage = gated
    task = asyncio.create_task(
        client.post(complete_path(stage.project_id, upload_id), json={"uploadId": upload_id}, headers=stage.headers)
    )
    await asyncio.wait_for(gated.entered.wait(), GATE_WAIT_S)
    return gated, task


async def _ready_upload(client: httpx.AsyncClient, stage: Stage, data: bytes) -> str:
    """#5 + mọi #6 cho một tệp; lượt tải sẵn sàng cho #7."""
    upload_id = await _start(client, stage, data)
    for response in await send_chunks(client, stage, upload_id, split(data)):
        assert response.status_code == 200, response.text
    return upload_id


async def test_complete_conflicts_when_a_chunk_is_replaced(
    api_app: FastAPI, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Khúc bị ghi đè trong lúc #7 đang nối tệp → 409 `UPLOAD_CHUNKS_CHANGED` (FE thử lại)."""
    stage = await make_stage(db_session)
    data = upload_png(900)
    async with make_api_client(api_app) as client:
        upload_id = await _ready_upload(client, stage, data)
        gated, task = await _gated_complete(client, api_app, stage, upload_id)
        replaced = await client.post(
            chunks_path(stage.project_id, upload_id), json=chunk_body(data[:-1] + b"Z", 0), headers=stage.headers
        )
        gated.release.set()
        response = await asyncio.wait_for(task, GATE_WAIT_S)

    assert replaced.status_code == 200, replaced.text
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "UPLOAD_CHUNKS_CHANGED"


async def test_complete_404_when_floor_is_deleted_midway(
    api_app: FastAPI, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Tầng bị xoá mềm trong lúc #7 đang ghi kho → 404 `resource:"floor"`, không ghi gì."""
    stage = await make_stage(db_session)
    data = upload_png(900)
    async with make_api_client(api_app) as client:
        upload_id = await _ready_upload(client, stage, data)
        gated, task = await _gated_complete(client, api_app, stage, upload_id)
        await db_session.execute(
            update(FloorRow).where(FloorRow.pk == stage.scene.floor.pk).values(deleted_at=func.now())
        )
        await db_session.commit()
        gated.release.set()
        response = await asyncio.wait_for(task, GATE_WAIT_S)

    assert response.status_code == 404, response.text
    assert response.json()["resource"] == "floor"


async def test_complete_404_when_upload_is_deleted_midway(
    api_app: FastAPI, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Lượt tải bị lịch dọn xoá trong lúc #7 đang ghi kho → 404 `resource:"upload"`."""
    stage = await make_stage(db_session)
    data = upload_png(900)
    async with make_api_client(api_app) as client:
        upload_id = await _ready_upload(client, stage, data)
        gated, task = await _gated_complete(client, api_app, stage, upload_id)
        await db_session.execute(delete(UploadRow).where(UploadRow.id == upload_id))
        await db_session.commit()
        gated.release.set()
        response = await asyncio.wait_for(task, GATE_WAIT_S)

    assert response.status_code == 404, response.text
    assert response.json()["resource"] == "upload"


async def test_reject_survives_upload_deleted_midway(
    api_app: FastAPI, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Tệp hỏng **và** lượt tải biến mất giữa chừng → vẫn trả 422 W7, không 500."""
    stage = await make_stage(db_session)
    data = upload_png(900, truncated=True)
    async with make_api_client(api_app) as client:
        upload_id = await _ready_upload(client, stage, data)
        gated, task = await _gated_complete(client, api_app, stage, upload_id)
        await db_session.execute(delete(UploadRow).where(UploadRow.id == upload_id))
        await db_session.commit()
        gated.release.set()
        response = await asyncio.wait_for(task, GATE_WAIT_S)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "FILE_CORRUPT"


async def test_complete__storage_error_503_keeps_upload_receiving(
    api_app: FastAPI,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sync_bus_reset: None,
) -> None:
    """QA-03d BE2-G-083: kho chập chờn đúng lúc #7 ghi `original.*` → 503, lượt tải **vẫn** `receiving`.

    Lỗi kho là tạm thời (503 `DEPENDENCY_UNAVAILABLE` của `packages/storage/s3.py` `_s3_errors`),
    không phải tệp hỏng: không được thành dòng `rejected` vĩnh viễn, và #7 gửi lại sau khi kho
    hồi phải xong 200 `pending`. Chỉ lượt ghi `original.*` đầu tiên hỏng; mọi lượt khác ghi thật.
    """
    stage = await make_stage(db_session)
    async with make_api_client(api_app) as client:
        upload_id = await _ready_upload(client, stage, upload_png(900))
        storage = cast("ObjectStorage", api_app.state.storage)
        real_put = storage.put
        blips = [DEPENDENCY_UNAVAILABLE.error(retry_after=1)]

        async def blip_once(
            key: str, data: bytes | AsyncIterable[bytes], *, content_type: str, max_bytes: int
        ) -> ObjectInfo:
            """Lượt ghi `original.*` đầu tiên gặp kho mất kết nối; còn lại đi thẳng xuống kho thật."""
            if "original" in key and blips:
                raise blips.pop()
            return await real_put(key, data, content_type=content_type, max_bytes=max_bytes)

        monkeypatch.setattr(storage, "put", blip_once)
        path = complete_path(stage.project_id, upload_id)
        first = await client.post(path, json={"uploadId": upload_id}, headers=stage.headers)
        row = await _upload_row(db_sessionmaker, upload_id)
        retry = await client.post(path, json={"uploadId": upload_id}, headers=stage.headers)

    assert first.status_code == 503, first.text
    assert first.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert (row.status, row.rejected_code) == ("receiving", None)
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "pending"


def test_check_pages__plan_page_pick_failure_is_app_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """BENCH3D-07: `pick_plan_page` hỏng giữa chừng → `AppError` cùng mã (thành dòng `rejected`), không 500."""

    def broken(data: bytes, max_pages: int) -> int:
        raise VisionError("FILE_CORRUPT")

    monkeypatch.setattr(complete, "pick_plan_page", broken)
    facts = UploadFacts(
        id="u",
        project_id="p",
        floor_pk=1,
        level_id="L",
        file_name="ho-so.pdf",
        declared_size_bytes=1,
        page_index=0,
        chunk_count=1,
        status="receiving",
        auto_page=True,
    )
    with pytest.raises(AppError) as caught:
        complete._check_pages(pdf_bytes(2), facts)
    assert caught.value.code.code == "FILE_CORRUPT"
