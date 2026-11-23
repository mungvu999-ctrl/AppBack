"""`apps/api/drawings/drawings.py` — bản vẽ đang dùng của một tầng (B2-04 [8] "Bản vẽ").

Mọi test chạy trong **một** giao dịch không commit: `upsert_drawing` không có tác dụng
ngoài nào, nên Redis và hàng đợi không phải vào cuộc.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.drawings import current_drawing, drawing_url, drawing_urls, new_page_key, upsert_drawing
from apps.api.drawings.runs import start_run
from apps.api.drawings.tests._helpers import Scene, make_scene
from apps.api.drawings.urls import signer, use_signer
from packages.core.object_keys import project_prefix
from packages.core.settings import reset_settings_cache
from packages.db.models.drawings import DrawingRow, UploadRow
from packages.db.models.spatial import FloorDocumentRow
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.drawings import make_complete_upload, png_bytes
from packages.testing.factories.spatial import make_floor_document
from packages.testing.fixtures.clock import FakeClock

PNG = png_bytes(800, 600)


async def _upload_with_run(
    db: AsyncSession, storage: LocalDiskStorage, scene: Scene, clock: FakeClock
) -> tuple[UploadRow, str]:
    """Một lượt tải `complete` cộng lượt chạy `pending` của nó (`start_run` thay lượt cũ)."""
    upload = await make_complete_upload(db, storage, project=scene.project, floor=scene.floor, data=PNG)
    run = await start_run(db, upload_id=upload.id, clock=clock)
    return upload, run.id


def _page_key(scene: Scene, upload: UploadRow, clock: FakeClock) -> str:
    """Khoá trang đã nắn hợp lệ của `upload`."""
    return new_page_key(
        project_id=scene.project.id,
        level_id=scene.floor.level_id,
        upload_id=upload.id,
        page_index=0,
        clock=clock,
    )


async def _count_drawings(db: AsyncSession) -> int:
    """Số dòng `drawings` còn lại — bằng chứng "xoá cũ chèn mới" chứ không phải "chèn thêm"."""
    return (await db.execute(select(func.count()).select_from(DrawingRow))).scalar_one()


async def test_new_page_key_is_new_every_time(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Mỗi lượt gọi một khoá khác: URL ký của trang cũ chết cùng object cũ (W23)."""
    scene = await make_scene(db_session)
    upload_id = "upl_" + "0" * 26
    first = new_page_key(
        project_id=scene.project.id,
        level_id=scene.floor.level_id,
        upload_id=upload_id,
        page_index=3,
        clock=fake_clock,
    )
    second = new_page_key(
        project_id=scene.project.id,
        level_id=scene.floor.level_id,
        upload_id=upload_id,
        page_index=3,
        clock=fake_clock,
    )
    assert first != second
    assert first.endswith(".png")
    assert "/pages/3-" in first


async def test_new_page_key_rejects_negative_index(fake_clock: FakeClock) -> None:
    """`page_index` âm là lỗi lập trình, không phải một trang hợp lệ."""
    with pytest.raises(ValueError, match="page_index"):
        new_page_key(
            project_id="prj_" + "0" * 26,
            level_id="L-ABCDEFGHIJ",
            upload_id="upl_" + "0" * 26,
            page_index=-1,
            clock=fake_clock,
        )


async def test_upsert_drawing_same_upload_keeps_id(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Chạy lại pipeline trên cùng lượt tải → cập nhật tại chỗ, `id` không đổi."""
    scene = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    first = await upsert_drawing(
        db_session,
        run_id=run_id,
        floor_pk=scene.floor.pk,
        upload_id=upload.id,
        page_key=_page_key(scene, upload, fake_clock),
        width_px=800,
        height_px=600,
        clock=fake_clock,
    )
    assert first is not None
    new_key = _page_key(scene, upload, fake_clock)
    second = await upsert_drawing(
        db_session,
        run_id=run_id,
        floor_pk=scene.floor.pk,
        upload_id=upload.id,
        page_key=new_key,
        width_px=801,
        height_px=601,
        clock=fake_clock,
    )
    assert second is not None
    assert second.id == first.id
    assert (second.page_key, second.width_px, second.height_px) == (new_key, 801, 601)
    assert second.name == upload.file_name
    assert second.uploader_id == upload.created_by
    assert await _count_drawings(db_session) == 1


async def test_upsert_drawing_new_upload_replaces_row(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Lượt tải khác là một bản vẽ khác: dòng cũ bị xoá, `id` mới được cấp."""
    scene = await make_scene(db_session)
    first_upload, first_run = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    first = await upsert_drawing(
        db_session,
        run_id=first_run,
        floor_pk=scene.floor.pk,
        upload_id=first_upload.id,
        page_key=_page_key(scene, first_upload, fake_clock),
        width_px=800,
        height_px=600,
        clock=fake_clock,
    )
    assert first is not None
    second_upload, second_run = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    second = await upsert_drawing(
        db_session,
        run_id=second_run,
        floor_pk=scene.floor.pk,
        upload_id=second_upload.id,
        page_key=_page_key(scene, second_upload, fake_clock),
        width_px=10,
        height_px=20,
        clock=fake_clock,
    )
    assert second is not None
    assert second.id != first.id
    assert second.upload_id == second_upload.id
    assert await _count_drawings(db_session) == 1


async def test_upsert_drawing_superseded_run_writes_nothing(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Kết quả muộn của lượt chạy đã bị thay → `None`, bảng `drawings` vẫn rỗng (BE-00 §7)."""
    scene = await make_scene(db_session)
    upload, stale_run = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    await _upload_with_run(db_session, local_storage, scene, fake_clock)
    result = await upsert_drawing(
        db_session,
        run_id=stale_run,
        floor_pk=scene.floor.pk,
        upload_id=upload.id,
        page_key=_page_key(scene, upload, fake_clock),
        width_px=800,
        height_px=600,
        clock=fake_clock,
    )
    assert result is None
    assert await current_drawing(db_session, scene.floor.pk) is None


async def test_upsert_drawing_unknown_run_writes_nothing(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Lượt chạy không tồn tại → `None` (lịch dọn có thể đã xoá dòng)."""
    scene = await make_scene(db_session)
    upload, _ = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    result = await upsert_drawing(
        db_session,
        run_id="run_" + "0" * 26,
        floor_pk=scene.floor.pk,
        upload_id=upload.id,
        page_key=_page_key(scene, upload, fake_clock),
        width_px=8,
        height_px=6,
        clock=fake_clock,
    )
    assert result is None


async def test_upsert_drawing_other_upload_of_same_run_writes_nothing(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Lượt chạy thuộc lượt tải khác → `None`: kết quả không ghép nhầm sang lượt kia."""
    scene = await make_scene(db_session)
    other = await make_complete_upload(db_session, local_storage, project=scene.project, floor=scene.floor, data=PNG)
    _, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    result = await upsert_drawing(
        db_session,
        run_id=run_id,
        floor_pk=scene.floor.pk,
        upload_id=other.id,
        page_key=_page_key(scene, other, fake_clock),
        width_px=8,
        height_px=6,
        clock=fake_clock,
    )
    assert result is None


@pytest.mark.parametrize(("width", "height"), [(0, 600), (800, 0)])
async def test_upsert_drawing_rejects_empty_size(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock, width: int, height: int
) -> None:
    """Kích thước < 1 là lỗi của người gọi (CHECK của bảng cũng chặn, nhưng muộn hơn)."""
    scene = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    with pytest.raises(ValueError, match="kích thước"):
        await upsert_drawing(
            db_session,
            run_id=run_id,
            floor_pk=scene.floor.pk,
            upload_id=upload.id,
            page_key=_page_key(scene, upload, fake_clock),
            width_px=width,
            height_px=height,
            clock=fake_clock,
        )


async def test_upsert_drawing_rejects_foreign_page_key(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Khoá trang của lượt tải khác → `ValueError`, dù nó là một khoá đúng mẫu."""
    scene = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    other = await make_complete_upload(db_session, local_storage, project=scene.project, floor=scene.floor, data=PNG)
    with pytest.raises(ValueError, match="khoá trang"):
        await upsert_drawing(
            db_session,
            run_id=run_id,
            floor_pk=scene.floor.pk,
            upload_id=upload.id,
            page_key=_page_key(scene, other, fake_clock),
            width_px=8,
            height_px=6,
            clock=fake_clock,
        )


async def test_upsert_drawing_rejects_key_outside_pages(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Khoá đúng lượt tải nhưng không dưới `pages/` (ví dụ `original.png`) → `ValueError`."""
    scene = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    assert upload.original_key is not None
    with pytest.raises(ValueError, match="khoá trang"):
        await upsert_drawing(
            db_session,
            run_id=run_id,
            floor_pk=scene.floor.pk,
            upload_id=upload.id,
            page_key=upload.original_key,
            width_px=8,
            height_px=6,
            clock=fake_clock,
        )


async def test_upsert_drawing_rejects_garbage_page_key(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Khoá không nằm dưới một lượt tải nào → `ValueError` của `upload_prefix_of` (NO-079)."""
    scene = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    with pytest.raises(ValueError, match="lượt tải"):
        await upsert_drawing(
            db_session,
            run_id=run_id,
            floor_pk=scene.floor.pk,
            upload_id=upload.id,
            page_key="users/avatar.png",
            width_px=8,
            height_px=6,
            clock=fake_clock,
        )


async def test_upsert_drawing_rejects_upload_still_receiving(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """Lượt tải chưa `complete` thì chưa có tệp gốc, nên chưa thể có trang đã nắn."""
    scene = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    page_key = _page_key(scene, upload, fake_clock)
    upload.status = "receiving"
    await db_session.flush()
    with pytest.raises(ValueError, match="chưa có tệp gốc"):
        await upsert_drawing(
            db_session,
            run_id=run_id,
            floor_pk=scene.floor.pk,
            upload_id=upload.id,
            page_key=page_key,
            width_px=8,
            height_px=6,
            clock=fake_clock,
        )


async def test_upsert_drawing_rejects_other_floor(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """`floor_pk` không khớp lượt tải → `ValueError` (người gọi đã tra sai tầng)."""
    scene = await make_scene(db_session)
    other = await make_scene(db_session)
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    with pytest.raises(ValueError, match="tầng khác"):
        await upsert_drawing(
            db_session,
            run_id=run_id,
            floor_pk=other.floor.pk,
            upload_id=upload.id,
            page_key=_page_key(scene, upload, fake_clock),
            width_px=8,
            height_px=6,
            clock=fake_clock,
        )


async def test_current_drawing_locks_row_when_asked(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """`for_update=True` trả đúng dòng đó; không có bản vẽ → `None`."""
    scene = await make_scene(db_session)
    assert await current_drawing(db_session, scene.floor.pk, for_update=True) is None
    upload, run_id = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    written = await upsert_drawing(
        db_session,
        run_id=run_id,
        floor_pk=scene.floor.pk,
        upload_id=upload.id,
        page_key=_page_key(scene, upload, fake_clock),
        width_px=8,
        height_px=6,
        clock=fake_clock,
    )
    assert written is not None
    locked = await current_drawing(db_session, scene.floor.pk, for_update=True)
    assert locked is not None
    assert locked.id == written.id


async def test_drawing_url__foreign_key_is_signed_attachment(local_storage: LocalDiskStorage) -> None:
    """NO-217: khoá không do server đặt tên không có `kind` tin được → `attachment`, không cần object tồn tại."""
    key = f"{project_prefix('prj_' + '0' * 26)}floors/L-ABCDEFGHIJ/uploads/x/a.png"
    grant = local_storage.verify_token((await drawing_url(local_storage, key)).rsplit("/", 1)[-1])
    assert grant.key == key
    assert grant.disposition == "attachment"


async def test_signer_builds_storage_from_environment(api_env: None) -> None:
    """Chưa ai gọi `use_signer` → `signer()` dựng kho theo biến môi trường (`create_storage`)."""
    use_signer(None)
    built = signer()
    assert await drawing_url(built, f"{project_prefix('prj_' + '0' * 26)}floors/L-ABCDEFGHIJ/uploads/x/a.png")


async def test_signer__default_store_is_not_inherited_from_a_previous_test(
    api_env: None, local_storage: LocalDiskStorage
) -> None:
    """Chặn tái phát NO-265: kho mặc định của `signer()` trỏ vào thư mục của **test này**, không phải bản cache cũ."""
    use_signer(None)
    key = f"{project_prefix('prj_' + '0' * 26)}floors/L-ABCDEFGHIJ/uploads/x/b.png"
    await local_storage.put(key, b"png", content_type="image/png", max_bytes=10)
    assert await signer().stat(key) is not None


async def test_use_signer_refuses_outside_test_env(
    api_env: None, monkeypatch: pytest.MonkeyPatch, local_storage: LocalDiskStorage
) -> None:
    """Ngoài `APP_ENV=test`, cài kho giả là lỗ hổng chứ không phải tiện ích → `RuntimeError`."""
    monkeypatch.setenv("APP_ENV", "dev")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        use_signer(local_storage)


async def test_drawing_urls__mixed_keys_follow_drawing_url_rules_in_order(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """NO-207: lô trộn khoá trang (`inline`) và khoá ngoài (`attachment`) đúng thứ tự, từng URL y hệt `drawing_url`."""
    scene = await make_scene(db_session)
    upload, _ = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    page = _page_key(scene, upload, fake_clock)
    foreign = f"{project_prefix('prj_' + '0' * 26)}floors/L-ABCDEFGHIJ/uploads/x/a.png"

    batch = await drawing_urls(local_storage, [foreign, page, foreign])

    assert batch == [await drawing_url(local_storage, key) for key in (foreign, page, foreign)]
    assert await drawing_urls(local_storage, []) == []


async def test_drawing_url__page_is_signed_inline_png(
    db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """NO-217: `Drawing.url` là ảnh FE hiển thị — ký `inline` (kind `png` truyền sẵn, không `stat`) như ảnh đại diện."""
    scene = await make_scene(db_session)
    upload, _ = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    key = _page_key(scene, upload, fake_clock)
    grant = local_storage.verify_token((await drawing_url(local_storage, key)).rsplit("/", 1)[-1])
    assert grant.key == key
    assert grant.disposition == "inline"


@pytest.mark.parametrize("unbound", [False, True], ids=["bound", "unbound"])
@pytest.mark.parametrize(
    ("same_file", "width_px", "carried"),
    [(True, 800, True), (False, 800, False), (True, 900, False)],
    ids=["same_file", "other_file", "same_file_other_size"],
)
async def test_upsert_drawing_carries_scale_page_only_for_same_page(
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    same_file: bool,
    width_px: int,
    carried: bool,
    unbound: bool,
) -> None:
    """Tải lại đúng tệp, ra trang cùng khổ: tỉ lệ tầng theo sang khoá mới (BENCH3D-27); khác đi thì khoá giữ nguyên.

    L2-N03 (NO-505): tỉ lệ chưa gắn trang (`unbound`) + bản vẽ khác → gắn vào trang cũ, để tỉ lệ người đặt hết hiệu
    lực; cùng bản vẽ thì vẫn vắng.
    """
    scene = await make_scene(db_session)
    old_upload, old_run = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    old_upload.content_sha256 = "a" * 64
    old_key = _page_key(scene, old_upload, fake_clock)
    await upsert_drawing(
        db_session,
        run_id=old_run,
        floor_pk=scene.floor.pk,
        upload_id=old_upload.id,
        page_key=old_key,
        width_px=800,
        height_px=600,
        clock=fake_clock,
    )
    await make_floor_document(
        db_session,
        floor_pk=scene.floor.pk,
        scale=Decimal("10"),
        scale_source="human",
        scale_page_key=None if unbound else old_key,
        clock=fake_clock,
    )
    new_upload, new_run = await _upload_with_run(db_session, local_storage, scene, fake_clock)
    new_upload.content_sha256 = ("a" if same_file else "b") * 64
    new_key = _page_key(scene, new_upload, fake_clock)

    await upsert_drawing(
        db_session,
        run_id=new_run,
        floor_pk=scene.floor.pk,
        upload_id=new_upload.id,
        page_key=new_key,
        width_px=width_px,
        height_px=600,
        clock=fake_clock,
    )

    stored = await db_session.scalar(
        select(FloorDocumentRow.scale_page_key).where(FloorDocumentRow.floor_pk == scene.floor.pk)
    )
    if carried:
        assert stored == (None if unbound else new_key)
    else:
        assert stored == old_key
