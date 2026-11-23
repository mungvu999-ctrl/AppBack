"""Bốn bảng bộ hồ sơ (K-32a) trên Postgres thật (K23): revision lên/xuống/idempotent, CHECK hai phía biên,
unique, cascade từ bộ xuống, `SET NULL` từ tầng và lượt tải."""

import asyncio
from typing import Any, Final

import pytest
from alembic import command
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from packages.db.migrate_check import alembic_config
from packages.db.models.drawing_sets import (
    DRAWING_SET_FILE_CHUNKS,
    DRAWING_SET_FILES,
    DRAWING_SET_VIEWS,
    DRAWING_SETS,
    DrawingSetFileChunkRow,
    DrawingSetFileRow,
    DrawingSetRow,
    DrawingSetViewRow,
)
from packages.db.models.drawings import UploadRow
from packages.db.models.floors import FloorRow
from packages.db.models.projects import Project
from packages.db.settings import reset_database_settings_cache
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawing_sets import make_drawing_set, make_set_file, make_set_view
from packages.testing.factories.drawings import make_upload
from packages.testing.factories.floors import make_floor
from packages.testing.factories.projects import make_project

HS_04: Final = "r20261123_hs_04"
PARENT: Final = "r20261121_hs_03"
TABLES: Final = (DRAWING_SETS, DRAWING_SET_FILES, DRAWING_SET_FILE_CHUNKS, DRAWING_SET_VIEWS)
SHA: Final = "a" * 64


async def _table_count(url: str) -> int:
    """Số bảng bộ hồ sơ đang có trong DB `url` (kết nối riêng, ngoài Alembic)."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_name = ANY(:names)"),
                {"names": list(TABLES)},
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


@pytest.fixture
def url(blank_db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """URL DB trống, đặt vào môi trường cho `env.py` của Alembic."""
    monkeypatch.setenv("DATABASE_URL", blank_db_url)
    return blank_db_url


def test_hs_04_upgrade_downgrade_and_rerun_over_existing_tables(url: str) -> None:
    """Lên tạo đủ bốn bảng; xuống bỏ cả bốn; lên lại khi bảng đã có (đóng dấu về cha) không lỗi (L4-N02)."""
    config = alembic_config()
    reset_database_settings_cache()  # env.py đọc `DatabaseSettings` có cache; `url` vừa đặt `DATABASE_URL`
    try:
        command.upgrade(config, HS_04)
        assert asyncio.run(_table_count(url)) == len(TABLES)
        command.downgrade(config, PARENT)
        assert asyncio.run(_table_count(url)) == 0
        command.upgrade(config, HS_04)
        command.stamp(config, PARENT)
        command.upgrade(config, HS_04)
        assert asyncio.run(_table_count(url)) == len(TABLES)
    finally:
        reset_database_settings_cache()


async def _project(db: AsyncSession) -> Project:
    """Một dự án mới của một người dùng mới."""
    return await make_project(db, owner=await make_user(db))


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("status", "draft", "status"),
        ("progress_percent", -1, "progress_percent_range"),
        ("progress_percent", 101, "progress_percent_range"),
        ("revision", -1, "revision_non_negative"),
        ("name", "", "name_length"),
        ("name", "x" * 121, "name_length"),
    ],
)
async def test_set_checks_reject_out_of_range(
    db_session: AsyncSession, field: str, value: Any, constraint: str
) -> None:
    """Mỗi CHECK của `drawing_sets` chặn phía ngoài biên."""
    project = await _project(db_session)
    with pytest.raises(IntegrityError, match=constraint):
        await make_drawing_set(db_session, project=project, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("progress_percent", 0), ("progress_percent", 100), ("revision", 0), ("name", "x"), ("name", "x" * 120)],
)
async def test_set_checks_accept_inner_edge(db_session: AsyncSession, field: str, value: Any) -> None:
    """Phía trong biên đi qua — CHECK không chặt hơn hợp đồng; mặc định `revision = 0`, `progress = 0`."""
    row = await make_drawing_set(db_session, project=await _project(db_session), **{field: value})
    await db_session.refresh(row)
    assert (row.revision, row.progress_percent, row.deleted_at) == (
        value if field == "revision" else 0,
        value if field == "progress_percent" else 0,
        None,
    )


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("status", "complete_", "status"),
        ("ord", -1, "ord_non_negative"),
        ("declared_size_bytes", 0, "declared_size_bytes_positive"),
        ("chunk_count", 0, "chunk_count_positive"),
        ("page_count", 0, "page_count_positive"),
        ("content_sha256", "A" * 64, "content_sha256_format"),
    ],
)
async def test_file_checks_reject_out_of_range(
    db_session: AsyncSession, field: str, value: Any, constraint: str
) -> None:
    """`drawing_set_files`: trạng thái như `uploads`, thứ tự không âm, kích thước/khúc/trang dương, sha hex thường."""
    drawing_set = await make_drawing_set(db_session, project=await _project(db_session))
    with pytest.raises(IntegrityError, match=constraint):
        await make_set_file(db_session, drawing_set=drawing_set, **{field: value})


async def test_file_ord_is_unique_within_a_set(db_session: AsyncSession) -> None:
    """Hai tệp cùng `ord` trong một bộ bị chặn; cùng `ord` ở bộ khác thì được."""
    project = await _project(db_session)
    first = await make_drawing_set(db_session, project=project)
    second = await make_drawing_set(db_session, project=project)
    await make_set_file(db_session, drawing_set=first, ord=0, page_count=1, content_sha256=SHA)
    await make_set_file(db_session, drawing_set=second, ord=0)
    with pytest.raises(IntegrityError, match="uq_drawing_set_files_set_id_ord"):
        await make_set_file(db_session, drawing_set=first, ord=0)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("chunk_index", -1, "chunk_index_non_negative"),
        ("size_bytes", 0, "size_bytes_positive"),
        ("sha256", "a" * 63, "sha256_format"),
    ],
)
async def test_chunk_checks_reject_out_of_range(
    db_session: AsyncSession, field: str, value: Any, constraint: str
) -> None:
    """`drawing_set_file_chunks`: như `upload_chunks` — chỉ số không âm, khúc có byte, sha 64 hex."""
    file = await make_set_file(
        db_session, drawing_set=await make_drawing_set(db_session, project=await _project(db_session))
    )
    values: dict[str, Any] = {"file_id": file.id, "chunk_index": 0, "size_bytes": 1, "sha256": SHA, "object_key": "k"}
    db_session.add(DrawingSetFileChunkRow(**(values | {field: value})))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("source", "machine", "source"),
        ("kind", "facade", "kind"),
        ("target_mode", "floors", "target_mode"),
        ("ord", -1, "ord_non_negative"),
        ("page_index", -1, "page_index_non_negative"),
        ("kind_confidence", 1.01, "kind_confidence_range"),
        ("kind_confidence", -0.01, "kind_confidence_range"),
        ("storey_confidence", 1.5, "storey_confidence"),
    ],
)
async def test_view_checks_reject_out_of_range(
    db_session: AsyncSession, field: str, value: Any, constraint: str
) -> None:
    """`drawing_set_views`: nguồn, 7 loại của N52, 3 chế độ đích, thứ tự/trang không âm, độ tin trong [0, 1]."""
    file = await make_set_file(
        db_session, drawing_set=await make_drawing_set(db_session, project=await _project(db_session))
    )
    with pytest.raises(IntegrityError, match=constraint):
        await make_set_view(db_session, file=file, **{field: value})


@pytest.mark.parametrize("mode", ["floor", "newFloor", "section", None])
async def test_view_accepts_every_target_mode(db_session: AsyncSession, mode: str | None) -> None:
    """Ba chế độ đích và "chưa có đích" đều hợp lệ; `crop`/`machine` JSONB đọc lại nguyên dạng."""
    file = await make_set_file(
        db_session, drawing_set=await make_drawing_set(db_session, project=await _project(db_session))
    )
    crop = {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.5}
    view = await make_set_view(
        db_session, file=file, target_mode=mode, crop=crop, machine={"cues": ["MẶT BẰNG"]}, kind_confidence=1.0
    )
    await db_session.refresh(view)
    assert (view.crop, view.machine, view.excluded, view.replace_drawing) == (
        crop,
        {"cues": ["MẶT BẰNG"]},
        False,
        False,
    )


async def test_view_ord_is_unique_within_a_set(db_session: AsyncSession) -> None:
    """Hai hình cùng `ord` trong một bộ bị chặn."""
    file = await make_set_file(
        db_session, drawing_set=await make_drawing_set(db_session, project=await _project(db_session))
    )
    await make_set_view(db_session, file=file, ord=3)
    with pytest.raises(IntegrityError, match="uq_drawing_set_views_set_id_ord"):
        await make_set_view(db_session, file=file, ord=3)


async def test_deleting_set_cascades_to_files_chunks_and_views(db_session: AsyncSession) -> None:
    """Xoá cứng một bộ (lịch dọn dự án) dọn tệp, khúc và hình bằng một lệnh."""
    drawing_set = await make_drawing_set(db_session, project=await _project(db_session))
    file = await make_set_file(db_session, drawing_set=drawing_set)
    db_session.add(DrawingSetFileChunkRow(file_id=file.id, chunk_index=0, size_bytes=1, sha256=SHA, object_key="k"))
    await make_set_view(db_session, file=file)
    await db_session.execute(delete(DrawingSetRow).where(DrawingSetRow.id == drawing_set.id))
    for model in (DrawingSetFileRow, DrawingSetViewRow):
        assert (await db_session.execute(select(model).where(model.set_id == drawing_set.id))).first() is None
    chunks = select(DrawingSetFileChunkRow).where(DrawingSetFileChunkRow.file_id == file.id)
    assert (await db_session.execute(chunks)).first() is None


async def test_deleting_floor_or_upload_nulls_view_links(db_session: AsyncSession) -> None:
    """Xoá cứng tầng đích hay lượt tải đã gắn chỉ gỡ liên kết của hình (`SET NULL`), không chặn, không xoá hình."""
    project = await _project(db_session)
    floor = await make_floor(db_session, project=project, order=0)
    upload = await make_upload(
        db_session, project=project, floor=await make_floor(db_session, project=project, order=1)
    )
    file = await make_set_file(db_session, drawing_set=await make_drawing_set(db_session, project=project))
    view = await make_set_view(
        db_session, file=file, target_mode="floor", target_floor_pk=floor.pk, upload_id=upload.id
    )
    await db_session.execute(delete(FloorRow).where(FloorRow.pk == floor.pk))
    await db_session.execute(delete(UploadRow).where(UploadRow.id == upload.id))
    await db_session.refresh(view)
    assert (view.target_floor_pk, view.upload_id) == (None, None)
