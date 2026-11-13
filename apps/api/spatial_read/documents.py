"""Đọc `floor_documents` và biến một dòng thành `FloorDocument` (B3-02 [2], [6]).

Mọi người đọc — ba route, B3-03, B3-04, B5-06b, lịch đối chiếu, CLI — đi qua đây, nên
luật "tài liệu hỏng" chỉ có **một** bản: `schema_version` khác `DOCUMENT_SCHEMA_VERSION`
là hỏng ngay, kiểm trên **cột** trước khi gọi `codec` (một tài liệu v2 giải bằng luật v1
sẽ ra mô hình sai chứ không ném lỗi); rồi `codec.document_from_json` cho phần còn lại.

Module này nhập được trong ngữ cảnh worker (BE-00 §7): không `fastapi`, không phần HTTP
của `apps.api.core`.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from sqlalchemy import Select, cast, literal, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.codec import DocumentCorruptError, decode_document, document_to_json
from packages.core.clock import Clock
from packages.db.models.spatial import FloorDocumentRow
from packages.domain.spatial import Axis, Dimension, Point, SpatialLayer

_log: Final = logging.getLogger(__name__)

DOCUMENT_SCHEMA_VERSION: Final = 1
"""Lược đồ của cột `document`; nâng theo FIX.md luật 7 (migration đọc-ghi, không đọc thầm)."""

ScaleSource = Literal["human", "measured", "pipeline", "project_default", "none"]

EMPTY_LAYER: Final = SpatialLayer(walls=(), openings=(), rooms=(), furniture=())
"""Lớp rỗng dùng chung: mô hình bất biến nên một bản là đủ cho mọi tầng chưa có tài liệu."""

_HUMAN_GEOMETRY_PATH: Final = '$.layer.*[*] ? (@.reviewed == true || @.source == "human")'
"""Jsonpath của "có dấu tay người trong lớp" ([6], `src/lib/commands/business/shared.ts:118-129`)."""


@dataclass(frozen=True)
class FloorDocument:
    """Tài liệu của một tầng đã giải mã; bất biến để người gọi truyền đi mà không sợ bị sửa."""

    floor_pk: int
    revision: int
    layer: SpatialLayer
    axes: tuple[Axis, ...]
    dimensions: tuple[Dimension, ...]
    scale_mm_per_px: Decimal | None
    scale_source: ScaleSource
    scale_page_key: str | None
    last_writer_id: str | None
    last_body_sha256: str | None
    last_base_revision: int | None
    updated_at: datetime | None
    origin: Point | None = None
    """Gốc toạ độ màn Trục (mm, G4b-17); `None` = chưa đặt."""


def empty_document(floor_pk: int) -> FloorDocument:
    """Tài liệu của một tầng **chưa có dòng**: bản ghi 0, lớp rỗng, chưa hiệu chỉnh tỉ lệ.

    Route đọc trả cái này thay vì ghi một dòng mặc định ([6]: đọc không bao giờ ghi);
    `updated_at=None` để người gọi phân biệt "chưa từng ghi" với "vừa ghi xong".
    """
    return FloorDocument(
        floor_pk=floor_pk,
        revision=0,
        layer=EMPTY_LAYER,
        axes=(),
        dimensions=(),
        scale_mm_per_px=None,
        scale_source="none",
        scale_page_key=None,
        last_writer_id=None,
        last_body_sha256=None,
        last_base_revision=None,
        updated_at=None,
    )


def document_from_row(row: FloorDocumentRow) -> FloorDocument:
    """Một dòng → `FloorDocument`; lược đồ lạ hay jsonb hỏng → `DocumentCorruptError` (đã log `floor_pk`).

    Đây là **đường giải mã duy nhất** của dòng `floor_documents`: `load_document`,
    `load_documents`, lịch đối chiếu và CLI `check-documents` đều gọi nó, nên không ai
    có thể quên kiểm `schema_version`.
    """
    if row.schema_version != DOCUMENT_SCHEMA_VERSION:
        _log.error("tài liệu không gian sai lược đồ", extra={"floor_pk": row.floor_pk})
        raise DocumentCorruptError(f"floor_pk={row.floor_pk} có schema_version={row.schema_version}")
    try:
        envelope = decode_document(row.document)
    except DocumentCorruptError:
        _log.error("tài liệu không gian hỏng", extra={"floor_pk": row.floor_pk})
        raise
    return FloorDocument(
        floor_pk=row.floor_pk,
        revision=row.revision,
        layer=envelope.layer,
        axes=envelope.axes,
        dimensions=envelope.dimensions,
        origin=envelope.origin,
        scale_mm_per_px=row.scale_mm_per_px,
        scale_source=scale_source_of(row.scale_source),
        scale_page_key=row.scale_page_key,
        last_writer_id=row.last_writer_id,
        last_body_sha256=row.last_body_sha256,
        last_base_revision=row.last_base_revision,
        updated_at=row.updated_at,
    )


def scale_source_of(value: str) -> ScaleSource:
    """Cột `scale_source` → `ScaleSource`. CHECK của DB đã chặn giá trị lạ, đây chỉ là cầu kiểu tĩnh."""
    return value  # type: ignore[return-value]  # ck_floor_documents_scale_source giữ tập giá trị


def _rows(*floor_pks: int) -> Select[tuple[FloorDocumentRow]]:
    """Câu `SELECT` một hay nhiều dòng — cùng một hình dạng cho mọi người đọc."""
    return select(FloorDocumentRow).where(FloorDocumentRow.floor_pk.in_(floor_pks))


async def load_document(db: AsyncSession, floor_pk: int, *, for_update: bool = False) -> FloorDocument | None:
    """Tài liệu của một tầng, `None` khi chưa có dòng; `for_update=True` khoá dòng cho người ghi."""
    stmt = _rows(floor_pk)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    return None if row is None else document_from_row(row)


async def load_documents(db: AsyncSession, floor_pks: Sequence[int]) -> dict[int, FloorDocument]:
    """Tài liệu của nhiều tầng bằng **một** truy vấn; tầng chưa có dòng vắng khoá (N15 không N+1).

    Lô rỗng trả `{}` không chạy câu nào — một dự án chưa có tầng nào không đáng một vòng DB.
    """
    if not floor_pks:
        return {}
    rows = (await db.execute(_rows(*floor_pks))).scalars().all()
    return {row.floor_pk: document_from_row(row) for row in rows}


async def ensure_document(db: AsyncSession, *, floor_pk: int, clock: Clock) -> FloorDocument:
    """Bảo đảm tầng có dòng tài liệu rồi trả nó; hai lượt song song → một dòng, không lỗi.

    `ON CONFLICT (floor_pk) DO NOTHING` chứ không "đọc rồi ghi nếu thiếu": hai giao dịch
    đọc cùng lúc đều thấy thiếu và lượt sau sẽ vỡ PK. Không `commit` — người gọi đang ở
    giữa giao dịch ghi của họ (K22).
    """
    now = clock.now()
    stmt = pg_insert(FloorDocumentRow).values(
        floor_pk=floor_pk,
        revision=0,
        schema_version=DOCUMENT_SCHEMA_VERSION,
        document=document_to_json(EMPTY_LAYER, (), ()),
        scale_source="none",
        created_at=now,
        updated_at=now,
    )
    await db.execute(stmt.on_conflict_do_nothing(index_elements=[FloorDocumentRow.floor_pk]))
    return document_from_row((await db.execute(_rows(floor_pk))).scalar_one())


async def has_human_geometry(db: AsyncSession, floor_pk: int) -> bool:
    """Tầng có dấu tay người: tỉ lệ do người đặt, **hoặc** một mục vẽ tay / đã duyệt trong lớp.

    Một truy vấn, **không** giải `codec`: câu này chạy trên mọi tầng của một dự án, còn
    giải jsonb 99 thực thể chỉ để hỏi một `bool` là phí. `@?` dùng được index GIN về sau
    mà không phải đổi mã gọi. Chưa có dòng → `False`.
    """
    # `@?` nhận `jsonpath`, không nhận `text`: thiếu `cast` là Postgres báo "operator does not exist".
    marked = FloorDocumentRow.document.op("@?")(cast(literal(_HUMAN_GEOMETRY_PATH), JSONPATH))
    condition = (FloorDocumentRow.scale_source == "human") | marked
    stmt = select(select(1).where(FloorDocumentRow.floor_pk == floor_pk, condition).exists())
    return bool((await db.execute(stmt)).scalar_one())


async def scan_corrupt(db: AsyncSession, *, after_pk: int, limit: int) -> tuple[list[int], int | None]:
    """Một lô cho CLI `check-documents`: `(floor_pk hỏng, con trỏ lô sau)`.

    Dùng **cùng** `document_from_row` với đường đọc thường, nên CLI không thể lệch luật
    kiểm khỏi route. Con trỏ là `floor_pk` lớn nhất đã xét, `None` khi lô này là lô cuối.
    """
    stmt = (
        select(FloorDocumentRow)
        .where(FloorDocumentRow.floor_pk > after_pk)
        .order_by(FloorDocumentRow.floor_pk)
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    corrupt: list[int] = []
    for row in rows:
        try:
            document_from_row(row)
        except DocumentCorruptError:
            corrupt.append(row.floor_pk)
    return corrupt, rows[-1].floor_pk if len(rows) == limit else None
