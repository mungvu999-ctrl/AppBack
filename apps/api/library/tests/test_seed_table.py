"""Seed `library` và ràng buộc của bảng `library_items` (B2-06 [5], [8]); sniff/khoá của danh mục."""

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.library.tests._helpers import LIST_PATH, headers_of, seed_and_publish
from packages.db.models.library import LibraryItemRow
from packages.db.seeds import SEEDS_DIR
from packages.db.seeds.library import ENVS, ORDER, seed, sync_catalogue
from packages.domain.library import CATALOGUE, FILE_CATALOGUE, CatalogueItem, FileItem, build_glb, build_preview_png
from packages.storage.keys import library_object
from packages.storage.local import LocalDiskStorage
from packages.storage.sniff import sniff
from packages.testing.factories.auth import make_user
from packages.testing.factories.library import make_library_item
from packages.testing.fixtures.clock import FakeClock


async def _rows(db: AsyncSession) -> dict[str, LibraryItemRow]:
    """Mọi dòng theo `id`, đọc lại từ DB (bỏ cache của session)."""
    db.expire_all()
    return {row.id: row for row in (await db.execute(select(LibraryItemRow))).scalars()}


def test_seed_module_contract() -> None:
    """Khuôn seed: `ORDER = 50`, đủ năm môi trường, tệp nằm trong thư mục seed."""
    assert ORDER == 50
    assert {"dev", "test", "ci", "staging", "production"} == ENVS
    assert (SEEDS_DIR / "library.py").is_file()


async def test_seed_twice_keeps_row_count_and_identity(db_session: AsyncSession) -> None:
    """Chạy hai lần → 16 + 978 dòng (mẫu từ tệp xếp sau), `sort_order` = chỉ số x 10, chỉ danh tính."""
    await seed(db_session)
    await db_session.commit()
    await seed(db_session)
    await db_session.commit()

    assert (await db_session.execute(select(func.count()).select_from(LibraryItemRow))).scalar_one() == len(
        CATALOGUE
    ) + len(FILE_CATALOGUE)
    rows = await _rows(db_session)
    items: tuple[CatalogueItem | FileItem, ...] = (*CATALOGUE, *FILE_CATALOGUE)
    for index, item in enumerate(items):
        row = rows[item.id]
        assert (row.name, row.item_group, row.sort_order) == (item.name, item.group, index * 10)
        assert (row.published_at, row.model_key, row.retired_at, row.owner_id) == (None, None, None, None)


async def test_seed_rerun_does_not_touch_unchanged_rows(db_session: AsyncSession) -> None:
    """`DO UPDATE … WHERE` chỉ chạm dòng có giá trị khác: `updated_at` không đổi khi chạy lại."""
    await seed(db_session)
    await db_session.commit()
    before = {i: r.updated_at for i, r in (await _rows(db_session)).items()}
    await seed(db_session)
    await db_session.commit()
    assert {i: r.updated_at for i, r in (await _rows(db_session)).items()} == before


async def test_seed_updates_changed_identity(db_session: AsyncSession) -> None:
    """Danh tính đổi (tên, nhóm) → dòng cập nhật theo danh mục; số dòng không đổi."""
    await seed(db_session)
    await db_session.commit()
    await db_session.execute(
        update(LibraryItemRow).where(LibraryItemRow.id == "table-desk").values(name="cũ", item_group="bed")
    )
    await db_session.commit()
    await seed(db_session)
    await db_session.commit()
    row = (await _rows(db_session))["table-desk"]
    assert (row.name, row.item_group) == ("bàn làm việc", "table")


async def test_seed_retires_missing_and_restores_returning(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
) -> None:
    """Danh mục thiếu một id → `retired_at` có, #14 không còn mục đó; thêm lại → hiện lại (không cần phát hành lại)."""
    user = await make_user(db_session)
    await seed_and_publish(db_session, db_sessionmaker, local_storage, fake_clock)
    headers = headers_of(user)

    await sync_catalogue(db_session, tuple(item for item in CATALOGUE if item.id != "chair-stool"))
    await db_session.commit()
    ids = [i["id"] for i in (await api_client.get(LIST_PATH, headers=headers)).json()]
    assert "chair-stool" not in ids
    assert len(ids) == len(CATALOGUE) - 1
    assert (await _rows(db_session))["chair-stool"].retired_at is not None

    await seed(db_session)
    await db_session.commit()
    assert "chair-stool" in [i["id"] for i in (await api_client.get(LIST_PATH, headers=headers)).json()]
    assert (await _rows(db_session))["chair-stool"].retired_at is None


async def test_seed_leaves_owned_items_and_empty_catalogue_alone(
    db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Mục có chủ không bị rút; danh mục rỗng là no-op (không rút hàng loạt do lỗi tiêm)."""
    owner = await make_user(db_session)
    await make_library_item(db_session, local_storage, id="mine-one", owner=owner)
    await make_library_item(db_session, local_storage, id="system-one")
    await sync_catalogue(db_session, ())
    await db_session.commit()
    assert all(row.retired_at is None for row in (await _rows(db_session)).values())

    await sync_catalogue(db_session, CATALOGUE[:1])
    await db_session.commit()
    rows = await _rows(db_session)
    assert rows["mine-one"].retired_at is None
    assert rows["system-one"].retired_at is not None


def test_generated_assets_are_recognised_by_sniff() -> None:
    """`sniff` nhận `build_glb(...)` là `glb` và `build_preview_png(...)` là `png` với mọi mục."""
    for item in CATALOGUE:
        assert sniff(build_glb(item).data[:64]) == "glb"
        assert sniff(build_preview_png(item)[:64]) == "png"


def test_every_catalogue_id_is_a_valid_object_key() -> None:
    """`keys.library_object` nhận mọi id của danh mục cho cả hai tên object."""
    for item in CATALOGUE:
        assert library_object(item.id, "model.glb") == f"library/{item.id}/model.glb"
        assert library_object(item.id, "preview.png") == f"library/{item.id}/preview.png"


BAD_ROWS: list[tuple[str, dict[str, Any]]] = [
    ("id_format", {"id": "A"}),
    ("id_format_underscore", {"id": "a_b"}),
    ("id_length", {"id": "a" * 65}),
    ("name_length", {"name": ""}),
    ("item_group", {"item_group": "lamp"}),
    ("published_without_numbers", {"published_at": func.now()}),
    (
        "published_without_key",
        {
            "published_at": func.now(),
            "width_mm": 1,
            "depth_mm": 1,
            "height_mm": 1,
            "triangle_count": 1,
            "file_size_bytes": 1,
        },
    ),
    ("preview_key_without_sha", {"preview_key": "library/x/preview.png"}),
    ("preview_sha_without_key", {"preview_sha256": "0" * 64}),
]


@pytest.mark.parametrize("overrides", [pytest.param(v, id=c) for c, v in BAD_ROWS])
async def test_library_items_check_constraints(db_session: AsyncSession, overrides: dict[str, Any]) -> None:
    """Mỗi CHECK của bảng chặn dòng sai bằng `IntegrityError` (id sai mẫu, phát hành thiếu số đo, ảnh lệch cặp)."""
    values: dict[str, Any] = {"id": "ok-item", "name": "mẫu", "item_group": "table", "sort_order": 0, **overrides}
    db_session.add(LibraryItemRow(**values))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
