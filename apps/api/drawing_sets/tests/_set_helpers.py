"""Hạ tầng dùng chung cho test N54-N61 (K-32a): đường, thân, sân khấu đã commit, bộ dựng sẵn ở `review`.

Không phải file test. Tệp mẫu sinh trong bộ nhớ (PNG/PDF của `apps/api/drawings/tests/_upload_helpers.py`, R-07);
bộ `review` dựng thẳng bằng factory + ghi tệp gốc vào `local_storage` (cùng gốc kho với app thật của `api_env`).
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Final

import httpx
import pytest
from redis import Redis as SyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.drawings.settings import reset_drawings_settings_cache
from apps.api.drawings.tests._upload_helpers import SMALL_CHUNK_BYTES, Stage, chunk_body, make_stage, split, upload_png
from packages.db.models.drawing_sets import DrawingSetFileRow, DrawingSetRow, DrawingSetViewRow
from packages.messaging import broker_redis_sync
from packages.messaging.settings import get_messaging_settings
from packages.storage.keys import drawing_set_original
from packages.storage.port import ObjectStorage
from packages.testing.factories.drawing_sets import make_drawing_set, make_set_file, make_set_view

DEFAULT_QUEUE: Final = "default"
CPU_QUEUE: Final = "pipeline.cpu"
MISSING_SET: Final = "dws_" + "0" * 26
MISSING_FILE: Final = "dwf_" + "0" * 26
NEW_LEVEL: Final = "L-0000000000NEW1"
CROP_FULL: Final = {"xRatio": 0.0, "yRatio": 0.0, "widthRatio": 1.0, "heightRatio": 1.0}

__all__ = ["SMALL_CHUNK_BYTES", "Stage", "make_stage"]


@pytest.fixture
def drawing_set_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Công tắc bật + khúc 1 KiB cho một test; cache cấu hình đọc lại đầu và cuối."""
    monkeypatch.setenv("DRAWING_SET_ENABLED", "true")
    monkeypatch.setenv("UPLOAD_CHUNK_BYTES", str(SMALL_CHUNK_BYTES))
    reset_drawing_set_settings_cache()
    reset_drawings_settings_cache()
    yield
    reset_drawing_set_settings_cache()
    reset_drawings_settings_cache()


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Client Redis của broker; hai hàng `default` và `pipeline.cpu` dọn trước và sau (broker là session-scope)."""
    client = broker_redis_sync(get_messaging_settings())
    client.delete(DEFAULT_QUEUE, CPU_QUEUE)
    yield client
    client.delete(DEFAULT_QUEUE, CPU_QUEUE)
    client.close()


def sets_path(project_id: str) -> str:
    """Đường N54/N58."""
    return f"/api/projects/{project_id}/drawing-sets"


def set_path(project_id: str, set_id: str) -> str:
    """Đường N57/N61."""
    return f"{sets_path(project_id)}/{set_id}"


def chunk_path(project_id: str, set_id: str, file_id: str) -> str:
    """Đường N55."""
    return f"{set_path(project_id, set_id)}/files/{file_id}/chunks"


def complete_path(project_id: str, set_id: str) -> str:
    """Đường N56."""
    return f"{set_path(project_id, set_id)}/complete"


def views_path(project_id: str, set_id: str) -> str:
    """Đường N59."""
    return f"{set_path(project_id, set_id)}/views"


def confirm_path(project_id: str, set_id: str) -> str:
    """Đường N60."""
    return f"{set_path(project_id, set_id)}/confirm"


def create_body(*files: tuple[str, int, str], **extra: Any) -> dict[str, Any]:
    """Thân N54 từ `(tên, kích thước, MIME)`; `extra` thêm khoá (tên bộ, khoá lạ cho C03)."""
    return {"files": [{"fileName": n, "sizeBytes": s, "mimeType": m} for n, s, m in files], **extra}


async def create_set(client: httpx.AsyncClient, stage: Stage, *files: tuple[str, bytes, str]) -> dict[str, Any]:
    """N54 cho các tệp `(tên, byte, MIME)`; khẳng định 201, trả thân."""
    body = create_body(*((name, len(data), mime) for name, data, mime in files))
    response = await client.post(sets_path(stage.project_id), json=body, headers=stage.headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def send_file(client: httpx.AsyncClient, stage: Stage, set_id: str, file_id: str, data: bytes) -> None:
    """N55 cho mọi khúc của một tệp; khẳng định từng khúc 200."""
    for index, piece in enumerate(split(data)):
        response = await client.post(
            chunk_path(stage.project_id, set_id, file_id), json=chunk_body(piece, index), headers=stage.headers
        )
        assert response.status_code == 200, response.text


async def upload_set(
    client: httpx.AsyncClient, stage: Stage, *files: tuple[str, bytes, str], headers: dict[str, str] | None = None
) -> tuple[str, httpx.Response]:
    """N54 → N55 (mọi khúc mọi tệp) → N56; trả `(set_id, response N56)`."""
    created = await create_set(client, stage, *files)
    for declared, (_, data, _) in zip(created["files"], files, strict=True):
        await send_file(client, stage, created["id"], declared["id"], data)
    response = await client.post(
        complete_path(stage.project_id, created["id"]), json={"setId": created["id"]}, headers=headers or stage.headers
    )
    return str(created["id"]), response


@dataclass(frozen=True, slots=True)
class ReviewSet:
    """Bộ `review` dựng sẵn: một tệp PNG lành (gốc đã ở kho), các hình máy cả trang theo `ord`."""

    drawing_set: DrawingSetRow
    file: DrawingSetFileRow
    views: Sequence[DrawingSetViewRow]


async def seed_review_set(
    db: AsyncSession, storage: ObjectStorage, stage: Stage, *, views: int = 1, pages: int = 1, **overrides: Any
) -> ReviewSet:
    """Bộ `review` của dự án sân khấu (đã commit): tệp `complete` `pages` trang, `views` hình `ai`, gốc trong kho."""
    data = upload_png(200)
    drawing_set = await make_drawing_set(db, project=stage.scene.project, status=overrides.pop("status", "review"))
    file = await make_set_file(
        db,
        drawing_set=drawing_set,
        status="complete",
        sniffed_kind="png",
        page_count=pages,
        declared_size_bytes=len(data),
        content_sha256="b" * 64,
    )
    key = drawing_set_original(stage.project_id, drawing_set.id, file.id, "png")
    file.original_key = key
    rows = [
        await make_set_view(
            db,
            file=file,
            ord=index,
            page_index=index % pages,
            crop={"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0},
            machine={"questions": ["NO_TEXT", "UNKNOWN_CODE"]},
            **overrides,
        )
        for index in range(views)
    ]
    await db.commit()
    await storage.put(key, data, content_type="image/png", max_bytes=len(data))
    return ReviewSet(drawing_set=drawing_set, file=file, views=rows)


def edit_of(view: DrawingSetViewRow, **changes: Any) -> dict[str, Any]:
    """Một `ViewEdit` giữ nguyên hình `view`; `changes` ghi đè khoá dây (`kind`, `target`, `excluded`…)."""
    edit: dict[str, Any] = {
        "id": view.id,
        "fileId": view.file_id,
        "pageIndex": view.page_index,
        "crop": CROP_FULL,
        "excluded": False,
        "kind": view.kind,
    }
    return edit | changes


def views_body(base_version: int, *edits: dict[str, Any]) -> dict[str, Any]:
    """Thân N59 `{baseVersion, body: {views}}`."""
    return {"baseVersion": base_version, "body": {"views": list(edits)}}
