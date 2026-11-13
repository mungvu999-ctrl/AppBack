"""Nền dựng dùng chung cho mọi test của `apps.api.spatial_write` (B3-03: việc W, R, J).

Bốn thứ lặp lại ở gần như mọi file: gọi `write_layer` mà không phải viết lại `LayerWrite`
và `actor_id`/`actor_name` mỗi lần, một hàm `merge` giả nhớ được đối số nó nhận, đọc lại
nhật ký của một tầng, và đọc lại tài liệu **bằng session khác** (test đồng thời chỉ tin
những gì đã `commit`).

Dựng dự án, tầng và session thứ hai thì dùng `make_scene`, `other_session`, `race` của
`apps/api/spatial_read/tests/_helpers.py` (B3-02) — không có bản thứ hai ở đây.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.spatial_read.documents import FloorDocument, load_document
from apps.api.spatial_read.tests._helpers import other_session
from apps.api.spatial_write.writer import LayerWrite, ScaleEvidence, WriteResult, write_layer
from packages.core.clock import Clock
from packages.db.models.auth import User
from packages.db.models.floors import FloorRow
from packages.db.models.spatial import FloorChangeLogRow, FloorEntityIdRow
from packages.domain.spatial import Dimension, Furniture, Room, SpatialLayer, Wall


@dataclass(frozen=True)
class Merged:
    """`MergeOutcome` giả: lớp đã gộp và bảng id AI bị bỏ → id được giữ."""

    layer: SpatialLayer
    id_map: Mapping[str, str] = field(default_factory=dict)


class RecordingMerge:
    """Hàm `merge` giả nhớ `(nền, tỉ lệ đích)` nó nhận — đó là nửa hợp đồng test phải kiểm."""

    def __init__(self, build: Callable[[SpatialLayer], SpatialLayer], id_map: Mapping[str, str] | None = None) -> None:
        """`build` dựng lớp gộp **từ nền** mà `write_layer` truyền vào (bước 7)."""
        self.build = build
        self.id_map = dict(id_map or {})
        self.base: SpatialLayer | None = None
        self.target: Decimal | None = None
        self.calls = 0

    def __call__(self, base: SpatialLayer, target: Decimal | None) -> Merged:
        """Ghi lại đối số rồi trả kết quả gộp."""
        self.base, self.target = base, target
        self.calls += 1
        return Merged(self.build(base), self.id_map)


async def write(
    db: AsyncSession,
    *,
    floor: FloorRow,
    actor: User,
    clock: Clock,
    base_revision: int = 0,
    layer: SpatialLayer | None = None,
    scale: Decimal | None = None,
    dimensions: Sequence[Dimension] | None = None,
    may_approve: bool = True,
    evidence: ScaleEvidence | None = None,
    **options: Any,
) -> WriteResult:
    """Gọi `write_layer` cho một tầng với người ghi là `actor`; `options` đi thẳng vào hàm thật.

    `may_approve=True` mặc định: người ghi của các test dựng trạng thái là engineer, có `qc.approve`.
    `evidence` là `scaleEvidence` của thân (ML-ACC-R10 K4), `None` = thân cũ.
    """
    return await write_layer(
        db,
        floor_pk=floor.pk,
        base_revision=base_revision,
        body=LayerWrite(
            layer=layer,
            scale_mm_per_px=scale,
            dimensions=None if dimensions is None else tuple(dimensions),
            scale_evidence=evidence,
        ),
        actor_id=actor.id,
        actor_name=actor.name,
        clock=clock,
        may_approve=may_approve,
        **options,
    )


async def log_rows(db: AsyncSession, floor_pk: int) -> list[FloorChangeLogRow]:
    """Mọi dòng nhật ký của một tầng theo thứ tự chèn."""
    stmt = select(FloorChangeLogRow).where(FloorChangeLogRow.floor_pk == floor_pk).order_by(FloorChangeLogRow.id)
    return list((await db.execute(stmt)).scalars().all())


async def log_count(db: AsyncSession, floor_pk: int) -> int:
    """Số dòng nhật ký của một tầng (đếm trong DB, không kéo dòng về)."""
    stmt = select(func.count()).select_from(FloorChangeLogRow).where(FloorChangeLogRow.floor_pk == floor_pk)
    return int((await db.execute(stmt)).scalar_one())


async def owned_ids(db: AsyncSession, project_id: str) -> set[str]:
    """Tập id thực thể dự án đang giữ, để kiểm bước 12 không để lại rác."""
    stmt = select(FloorEntityIdRow.entity_id).where(FloorEntityIdRow.project_id == project_id)
    return set((await db.execute(stmt)).scalars().all())


async def reread(db: AsyncSession, floor_pk: int) -> FloorDocument:
    """Tài liệu đọc lại (thường bằng một session khác); tầng phải đã có dòng."""
    document = await load_document(db, floor_pk)
    assert document is not None
    return document


WALL_ID = "W-TESTWALL01"
"""Id tường mặc định của lớp nhỏ; thân 10-64 ký tự `[0-9A-Z]` theo W4."""
ROOM_ID = "R-TESTROOM01"
OPENING_ID = "D-TESTOPEN01"
FURNITURE_ID = "F-TESTFURN01"
DIMENSION_ID = "M-TESTDIM001"


def review(*, source: str = "ai", reviewed: bool = False) -> dict[str, Any]:
    """Ba trường duyệt của một thực thể; `source="ai"` kèm `reviewed=True` là mẫu vi phạm A5."""
    return {"confidence": 1.0 if source == "human" else 0.8, "source": source, "reviewed": reviewed}


def make_wall(
    level_id: str,
    *,
    entity_id: str = WALL_ID,
    length: int = 1000,
    thickness: int = 200,
    source: str = "ai",
    reviewed: bool = False,
) -> Wall:
    """Một bức tường nằm ngang dài `length` mm, mặc định là mục AI chưa duyệt."""
    return Wall.model_validate(
        {
            "id": entity_id,
            "levelId": level_id,
            "centreline": {"start": {"x": 0, "y": 0}, "end": {"x": length, "y": 0}},
            "thicknessMm": thickness,
            "heightMm": 3000,
            "kind": "partition",
            "openingIds": [],
            **review(source=source, reviewed=reviewed),
        }
    )


def make_room(
    level_id: str,
    *,
    entity_id: str = ROOM_ID,
    name: str = "Bếp",
    area_m2: float = 0.0,
    source: str = "ai",
    reviewed: bool = False,
) -> Room:
    """Phòng 4000 x 4250 mm (= 17,00 m² thật) với `areaM2` do người gọi đặt, để kiểm W18."""
    return Room.model_validate(
        {
            "id": entity_id,
            "levelId": level_id,
            "name": name,
            "usage": "kitchen",
            "outline": [{"x": 0, "y": 0}, {"x": 4000, "y": 0}, {"x": 4000, "y": 4250}, {"x": 0, "y": 4250}],
            "areaM2": area_m2,
            "wallIds": [],
            **review(source=source, reviewed=reviewed),
        }
    )


def simple_layer(
    level_id: str,
    *,
    walls: Sequence[Wall] | None = None,
    rooms: Sequence[Room] = (),
    furniture: Sequence[Furniture] = (),
) -> SpatialLayer:
    """Lớp nhỏ nhất đủ dùng: một tường AI chưa duyệt, không ô mở; phòng và đồ đạc do người gọi đưa vào."""
    return SpatialLayer(
        walls=tuple(walls) if walls is not None else (make_wall(level_id),),
        openings=(),
        rooms=tuple(rooms),
        furniture=tuple(furniture),
    )


def reordered(layer: SpatialLayer) -> SpatialLayer:
    """Lớp cùng nội dung, đảo thứ tự tường: đổi jsonb mà `diff_layers` không thấy gì.

    Dùng cho mọi test cần một lượt ghi "thắng" mà **không** để lại dòng nhật ký — thứ làm bên
    kia thành cũ. Thực thể khớp nhau theo id chứ không theo vị trí, nên không mục nào đổi.
    """
    return layer.model_copy(update={"walls": layer.walls[::-1]})


def slipping_load(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    floor: FloorRow,
    actor: User,
    clock: Clock,
) -> Callable[..., Awaitable[FloorDocument | None]]:
    """Bản thay `writer.load_document` để một lượt ghi **thật** chen vào giữa bước 3 và 11.

    Trả về một hàm đọc **thật** (không mock session, không mock `write_layer` — K22/K23) rồi
    `commit` một lượt ghi khác ở session riêng trước khi người gọi kịp `UPDATE`, nên lượt của
    người gọi luôn thua đua ở bước 11. Lượt chen vào chỉ `reordered` nên không sinh dòng nhật
    ký: người gọi vì thế đi qua được bước 5 rồi mới chết ở `UPDATE`, đúng cảnh 503 mô tả.
    Cờ `running` chặn đệ quy — chính lượt ghi chen vào cũng gọi hàm này.
    """
    original = load_document
    running = {"rival": False}

    async def loader(db: AsyncSession, floor_pk: int, *, for_update: bool = False) -> FloorDocument | None:
        """Đọc thật, rồi nhường cho một lượt ghi khác `commit` trước khi trả kết quả."""
        document = await original(db, floor_pk, for_update=for_update)
        if running["rival"] or document is None:
            return document
        running["rival"] = True
        async with other_session(sessionmaker) as rival:
            current = await original(rival, floor_pk)
            assert current is not None
            await write(
                rival,
                floor=floor,
                actor=actor,
                clock=clock,
                base_revision=current.revision,
                layer=reordered(current.layer),
            )
            await rival.commit()
        running["rival"] = False
        return document

    return loader


def make_dimension(
    level_id: str, *, entity_id: str = DIMENSION_ID, refs: Sequence[str] = (), length: int = 1000
) -> Dimension:
    """Chuỗi kích thước AI chưa duyệt - dạng duy nhất `write_layer` nhận qua `body.dimensions`."""
    return Dimension.model_validate(
        {
            "id": entity_id,
            "levelId": level_id,
            "kind": "linear",
            "referenceIds": list(refs),
            "line": {"start": {"x": 0, "y": 0}, "end": {"x": length, "y": 0}},
            "valueMm": length,
            **review(),
        }
    )
