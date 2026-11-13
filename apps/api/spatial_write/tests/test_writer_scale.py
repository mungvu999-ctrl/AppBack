"""Tỉ lệ, `merge` và `dimensions` của `write_layer` (B3-03 [6] bước 6-9, [8]).

Ba luật dễ sai nằm cả ở đây: hạng nguồn (`human > pipeline > project_default > none`,
K19), luật theo trang của #35 (tỉ lệ của **trang khác** không nói gì về lớp đang có), và
"chỉ mục chưa duyệt mới đổi tỉ lệ".
"""

from collections.abc import Sequence
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.tests._helpers import make_scene
from apps.api.spatial_write.tests._helpers import (
    WALL_ID,
    RecordingMerge,
    log_rows,
    make_dimension,
    make_wall,
    reread,
    simple_layer,
    write,
)
from packages.core.errors import AppError
from packages.db.models.floors import FloorRow
from packages.domain.spatial import Dimension, SpatialLayer
from packages.storage.port import ObjectStorage
from packages.testing.factories.drawings import make_drawing, make_upload, png_bytes
from packages.testing.factories.spatial import make_floor_document
from packages.testing.fixtures.clock import FakeClock

PAGE_1 = "P1"
PAGE_2 = "P2"


def _wall_length(layer: SpatialLayer, index: int = 0) -> int:
    """Chiều dài đường tim của một tường - số duy nhất các test tỉ lệ cần đọc."""
    return layer.walls[index].centreline.end.x


async def _prepared(
    db: AsyncSession,
    floor: FloorRow,
    clock: FakeClock,
    *,
    scale: Decimal | None = None,
    scale_source: str = "none",
    scale_page_key: str | None = None,
    length: int = 1000,
    reviewed_wall: bool = False,
    dimensions: Sequence[Dimension] = (),
) -> None:
    """Tầng đã có tài liệu ở bản ghi 1: một tường AI dài `length`, tuỳ chọn thêm tường đã duyệt."""
    walls = [make_wall(floor.level_id, length=length)]
    if reviewed_wall:
        walls.append(make_wall(floor.level_id, entity_id="W-TESTWALL02", length=length, source="human", reviewed=True))
    await make_floor_document(
        db,
        floor_pk=floor.pk,
        layer=simple_layer(floor.level_id, walls=walls),
        dimensions=dimensions,
        revision=1,
        scale=scale,
        scale_source=scale_source,
        scale_page_key=scale_page_key,
        clock=clock,
    )


async def test_scale_without_drawing_leaves_page_key_null(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """PUT tỉ lệ khi tầng chưa có bản vẽ: `scale_page_key` là NULL, không phải chuỗi rỗng."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]

    await write(db_session, floor=floor, actor=scene.owner, clock=fake_clock, scale=Decimal("10"))

    document = await reread(db_session, floor.pk)
    assert (document.scale_mm_per_px, document.scale_source, document.scale_page_key) == (
        Decimal("10.000000"),
        "human",
        None,
    )


async def test_scale_takes_page_key_of_current_drawing(
    db_session: AsyncSession, local_storage: ObjectStorage, fake_clock: FakeClock
) -> None:
    """Chưa biết trang của lớp thì tỉ lệ mới thuộc về **bản vẽ đang dùng** của tầng."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    upload = await make_upload(db_session, project=scene.project, floor=floor)
    drawing = await make_drawing(db_session, local_storage, upload=upload, png=png_bytes(40, 30))

    await write(db_session, floor=floor, actor=scene.owner, clock=fake_clock, scale=Decimal("10"))

    assert (await reread(db_session, floor.pk)).scale_page_key == drawing.page_key


async def test_scale_keeps_page_key_of_existing_layer(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lớp đang có đã gắn trang P1 thì tỉ lệ mới vẫn thuộc P1, dù bản vẽ hiện tại đã sang trang khác."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human", scale_page_key=PAGE_1)

    await write(db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("20"))

    assert (await reread(db_session, floor.pk)).scale_page_key == PAGE_1


async def test_scale_rescales_only_unreviewed_entities(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """10 → 20 mm/px: tường chưa duyệt dài gấp đôi, tường đã duyệt nguyên vẹn, nhật ký có đỉnh."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(
        db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human", reviewed_wall=True, length=1000
    )

    result = await write(
        db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("20")
    )

    assert (_wall_length(result.layer, 0), _wall_length(result.layer, 1)) == (2000, 1000)
    assert (await reread(db_session, floor.pk)).scale_source == "human"
    vertices = [row for row in await log_rows(db_session, floor.pk) if row.entity_type == "vertex"]
    assert ("x", 2000) in [(row.field, row.value) for row in vertices]


async def test_human_scale_over_measured_rescales_reviewed_too(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """OCR-SCALE: tỉ lệ `measured` do máy, chưa ai duyệt số mm → người đổi tỉ lệ thì co giãn cả mục đã duyệt."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(
        db_session, floor, fake_clock, scale=Decimal("10"), scale_source="measured", reviewed_wall=True, length=1000
    )

    result = await write(
        db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("20")
    )

    assert (_wall_length(result.layer, 0), _wall_length(result.layer, 1)) == (2000, 2000)
    assert (await reread(db_session, floor.pk)).scale_source == "human"


async def test_scale_from_null_does_not_rescale(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Tỉ lệ cũ NULL nghĩa là chưa hiệu chỉnh: hình cũ không ở hệ nào để mà quy đổi (K19)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock)

    result = await write(
        db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("20")
    )

    assert _wall_length(result.layer) == 1000


async def test_layer_only_write_keeps_scale(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Thân chỉ mang `layer` thì tỉ lệ, nguồn và trang giữ nguyên."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human", scale_page_key=PAGE_1)
    layer = simple_layer(floor.level_id, walls=(make_wall(floor.level_id, length=1500),))

    await write(db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, layer=layer)

    document = await reread(db_session, floor.pk)
    assert (document.scale_mm_per_px, document.scale_source, document.scale_page_key) == (
        Decimal("10.000000"),
        "human",
        PAGE_1,
    )
    assert _wall_length(document.layer) == 1500


async def test_layer_sent_with_new_scale_is_rescaled(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Thân mang cả `layer` lẫn tỉ lệ: lớp gửi lên ở hệ **cũ**, nên cũng phải quy đổi."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human")
    layer = simple_layer(floor.level_id, walls=(make_wall(floor.level_id, length=1500),))

    result = await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        base_revision=1,
        layer=layer,
        scale=Decimal("20"),
    )

    assert _wall_length(result.layer) == 3000


async def test_pipeline_scale_does_not_override_human(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Hạng thấp không đè hạng cao: tỉ lệ người đặt thắng tỉ lệ pipeline suy ra (K19)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human")

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        base_revision=1,
        scale=Decimal("12"),
        scale_source="pipeline",
        layer=simple_layer(floor.level_id, walls=(make_wall(floor.level_id, length=1500),)),
    )

    document = await reread(db_session, floor.pk)
    assert (document.scale_mm_per_px, document.scale_source) == (Decimal("10.000000"), "human")


async def test_pipeline_scale_overrides_project_default(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """`pipeline` (2) đè được `project_default` (1): tỉ lệ đoán theo dự án là phỏng đoán yếu nhất."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("1"), scale_source="project_default")

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        base_revision=1,
        scale=Decimal("12"),
        scale_source="pipeline",
    )

    document = await reread(db_session, floor.pk)
    assert (document.scale_mm_per_px, document.scale_source) == (Decimal("12.000000"), "pipeline")


async def test_merge_writes_its_own_layer_without_rescaling(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lớp của `merge` đã ở hệ tỉ lệ đích: ghi thẳng, không nhân thêm lần nữa."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("1"), scale_source="project_default")
    merged = simple_layer(floor.level_id, walls=(make_wall(floor.level_id, length=12000),))
    merge = RecordingMerge(lambda _: merged)

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        scale=Decimal("12"),
        scale_source="pipeline",
        page_key=PAGE_1,
        merge=merge,
    )

    document = await reread(db_session, floor.pk)
    assert _wall_length(document.layer) == 12000
    assert merge.target == Decimal("12")
    assert merge.base is not None
    assert _wall_length(merge.base) == 12000


async def test_merge_on_other_page_rescales_only_unreviewed(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Trang khác: hạng hiện tại tính là `none`, mục chưa duyệt về tỉ lệ mới, mục đã duyệt giữ mm (BENCH3D-27)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(
        db_session,
        floor,
        fake_clock,
        scale=Decimal("10"),
        scale_source="human",
        scale_page_key=PAGE_1,
        reviewed_wall=True,
    )
    merge = RecordingMerge(lambda base: base)

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        scale=Decimal("12"),
        scale_source="pipeline",
        page_key=PAGE_2,
        merge=merge,
    )

    document = await reread(db_session, floor.pk)
    assert (document.scale_mm_per_px, document.scale_source, document.scale_page_key) == (
        Decimal("12.000000"),
        "pipeline",
        PAGE_2,
    )
    assert merge.target == Decimal("12")
    assert [_wall_length(document.layer, index) for index in range(2)] == [1200, 1000]


async def test_merge_on_other_page_keeps_base_for_project_default(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Tỉ lệ mới chỉ là mặc định dự án, không phải số đo: nền giữ mm, nguồn `project_default` để FE báo đỏ."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human", scale_page_key=PAGE_1)
    merge = RecordingMerge(lambda base: base)

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        scale=Decimal("1"),
        scale_source="project_default",
        page_key=PAGE_2,
        merge=merge,
    )

    document = await reread(db_session, floor.pk)
    assert (document.scale_mm_per_px, document.scale_source) == (Decimal("1.000000"), "project_default")
    assert _wall_length(document.layer) == 1000


async def test_merge_on_same_page_keeps_higher_ranked_scale(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Cùng trang thì hạng vẫn xử: `merge` nhận tỉ lệ **đang giữ**, và kích thước gửi kèm quy về hệ ấy."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="human", scale_page_key=PAGE_1)
    merge = RecordingMerge(lambda base: base)

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        scale=Decimal("12"),
        scale_source="pipeline",
        page_key=PAGE_1,
        merge=merge,
        dimensions=(make_dimension(floor.level_id, refs=(WALL_ID,), length=1200),),
    )

    assert merge.target == Decimal("10")
    document = await reread(db_session, floor.pk)
    assert document.scale_mm_per_px == Decimal("10.000000")
    assert document.dimensions[0].line.end.x == 1000


async def test_merge_remaps_dimension_references(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """`id_map` của `merge` áp lên `referenceIds`; id không còn trong lớp bị lọc hẳn."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock)
    dropped, kept = "W-TESTWALL77", WALL_ID
    merge = RecordingMerge(lambda base: base, id_map={dropped: kept})

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        merge=merge,
        dimensions=(
            make_dimension(floor.level_id, refs=(dropped,)),
            make_dimension(floor.level_id, entity_id="M-TESTDIM002", refs=("R-TESTROOM09",)),
        ),
    )

    document = await reread(db_session, floor.pk)
    assert [item.reference_ids for item in document.dimensions] == [(kept,), ()]


async def test_scale_change_breaking_a_wall_is_a_validation_error(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Quy đổi làm tường co về một điểm → 422 trỏ đúng trường tỉ lệ, không phải lỗi 500.

    Bề dày AI giờ bắt lại về bậc chuẩn (≥ 100 mm) khi đổi tỉ lệ (QT-SCALE), nên ca hỏng dùng đường tim:
    tường 400 mm ở hệ số 1/1000 còn 0,4 mm, làm tròn về 0.
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("1000"), scale_source="human", length=400)

    with pytest.raises(AppError) as caught:
        await write(db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("1"))

    assert caught.value.params["field"] == "body.scaleMillimetresPerPixel"


async def test_scale_change_breaking_a_dimension_is_a_validation_error(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Kích thước co về một điểm cũng là 422 của cùng trường: người dùng chỉ sửa được tỉ lệ."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await make_floor_document(
        db_session,
        floor_pk=floor.pk,
        layer=SpatialLayer(walls=(), openings=(), rooms=(), furniture=()),
        dimensions=(make_dimension(floor.level_id, length=100),),
        revision=1,
        scale=Decimal("1000"),
        scale_source="human",
        clock=fake_clock,
    )

    with pytest.raises(AppError) as caught:
        await write(db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("1"))

    assert caught.value.params["field"] == "body.scaleMillimetresPerPixel"


async def test_scale_from_project_default_rescales_reviewed(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Tỉ lệ cũ chỉ là mặc định dự án: đặt gấp 10 thì cả tường đã duyệt cũng thành 10.000 mm (H4)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(
        db_session, floor, fake_clock, scale=Decimal("1"), scale_source="project_default", reviewed_wall=True
    )

    result = await write(
        db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("10")
    )

    assert (_wall_length(result.layer, 0), _wall_length(result.layer, 1)) == (10000, 10000)


async def test_scale_from_pipeline_rescales_reviewed(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Tỉ lệ pipeline suy ra cũng là tạm (`scale_status` = `unresolved`): người đặt tỉ lệ thì co giãn hết."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, scale=Decimal("10"), scale_source="pipeline", reviewed_wall=True)

    result = await write(
        db_session, floor=floor, actor=scene.owner, clock=fake_clock, base_revision=1, scale=Decimal("20")
    )

    assert (_wall_length(result.layer, 0), _wall_length(result.layer, 1)) == (2000, 2000)


async def test_merge_over_project_default_rescales_reviewed(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt pipeline suy được tỉ lệ trên tầng `project_default`: nền đưa cho `merge` co giãn cả mục đã duyệt."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(
        db_session, floor, fake_clock, scale=Decimal("1"), scale_source="project_default", reviewed_wall=True
    )
    merge = RecordingMerge(lambda base: base)

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        scale=Decimal("12"),
        scale_source="pipeline",
        page_key=PAGE_1,
        merge=merge,
    )

    assert merge.base is not None
    assert (_wall_length(merge.base, 0), _wall_length(merge.base, 1)) == (12000, 12000)
    assert [_wall_length((await reread(db_session, floor.pk)).layer, i) for i in (0, 1)] == [12000, 12000]


async def test_merge_keeps_overridden_dimension(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt pipeline không thay kích thước có `overrideValueMm`; mục AI mới không trùng vẫn được thêm (H3)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    overridden = make_dimension(floor.level_id).model_copy(update={"override_value_mm": 1234})
    await _prepared(db_session, floor, fake_clock, dimensions=(overridden,))
    fresh = make_dimension(floor.level_id, entity_id="M-TESTDIM002", length=3000)

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        merge=RecordingMerge(lambda base: base),
        dimensions=(fresh,),
    )

    dimensions = (await reread(db_session, floor.pk)).dimensions
    assert [(item.id, item.override_value_mm) for item in dimensions] == [
        ("M-TESTDIM001", 1234),
        ("M-TESTDIM002", None),
    ]


async def test_merge_replaces_plain_ai_dimensions(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Kích thước AI chưa ai chạm thì lượt pipeline mới thay hẳn (H3)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _prepared(db_session, floor, fake_clock, dimensions=(make_dimension(floor.level_id),))

    await write(
        db_session,
        floor=floor,
        actor=scene.owner,
        clock=fake_clock,
        merge=RecordingMerge(lambda base: base),
        dimensions=(make_dimension(floor.level_id, entity_id="M-TESTDIM002", length=3000),),
    )

    assert [item.id for item in (await reread(db_session, floor.pk)).dimensions] == ["M-TESTDIM002"]
