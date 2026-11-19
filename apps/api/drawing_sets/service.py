"""Đề xuất tầng, cốt và chiều cao cho cả nhà từ chữ OCR đã có của từng tầng (HS-02, chỉ đọc).

Một câu SQL lấy mọi tầng của dự án theo `floor_order` kèm lượt `completed` mới nhất chưa bị thay
(cùng luật `spatial_read.detected.latest_run`) và cỡ trang bản vẽ; `text.json` của các lượt ấy đọc
song song qua cổng kho, rồi `solve_house` (thuần, tốn CPU) chạy ngoài vòng sự kiện (R-23).

Không ghi DB, không gọi ML. Tầng chưa có lượt, artifact vắng, quá trần hay sai hợp đồng chỉ có nghĩa
"không có chữ" — miền trả `NO_TEXT` cho tầng đó, người gọi không nhận lỗi. Lỗi kho tạm thời
(`DEPENDENCY_UNAVAILABLE`) thì lan nguyên để client thử lại (R-16).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Select, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.detected import parse_texts, read_artifact
from packages.db.models.drawings import DrawingRow, PipelineRunRow
from packages.db.models.floors import FloorRow
from packages.domain.drawing_set import HouseProposal, HouseView, solve_house
from packages.ml_contracts.artifacts import TextPx
from packages.storage.port import ObjectStorage

MAX_FLOORS: Final = 1000
"""Trần số tầng đọc: `floor_order` của sản phẩm chỉ trong 0..999 (CHECK `floor_order_range`)."""
_READ_CONCURRENCY: Final = 4
"""Số artifact đọc cùng lúc: đủ gối đầu độ trễ kho mà không chiếm hết kết nối."""


@dataclass(frozen=True)
class HouseProposalRead:
    """Kết quả đọc: đề xuất theo thứ tự tầng, id lượt dùng chữ, và các tầng lệch thứ tự dự án."""

    proposal: HouseProposal
    run_ids: tuple[str, ...]
    order_differs: frozenset[str]


@dataclass(frozen=True)
class _FloorSource:
    """Một tầng của dự án cùng lượt mới nhất của nó (`None` khi chưa có) và cỡ trang bản vẽ (`None` khi chưa có)."""

    floor_id: str
    run: tuple[str, str] | None
    size: tuple[int, int] | None


@dataclass(frozen=True)
class _Ocr:
    """Một dòng chữ OCR theo `OcrText`: hộp (x0, y0, x1, y1)."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float


def _sources_stmt(project_id: str) -> Select[Any]:
    """Mọi tầng chưa xoá của dự án theo `(floor_order, pk)`; lượt mới nhất dựng bằng `LATERAL`, một câu cho cả bộ.

    Tầng chưa có lượt `completed` vẫn ra (cột lượt `NULL`). `drawings.floor_pk` unique nên join không nhân dòng.
    """
    latest = (
        select(PipelineRunRow.id.label("run_id"), PipelineRunRow.upload_id.label("upload_id"))
        .where(
            PipelineRunRow.floor_pk == FloorRow.pk,
            PipelineRunRow.status == "completed",
            PipelineRunRow.superseded_by.is_(None),
        )
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
        .lateral("latest_run")
    )
    return (
        select(
            FloorRow.level_id,
            latest.c.run_id,
            latest.c.upload_id,
            DrawingRow.width_px,
            DrawingRow.height_px,
        )
        .select_from(FloorRow)
        .outerjoin(latest, true())
        .outerjoin(DrawingRow, DrawingRow.floor_pk == FloorRow.pk)
        .where(FloorRow.project_id == project_id, FloorRow.deleted_at.is_(None))
        .order_by(FloorRow.floor_order, FloorRow.pk)
        .limit(MAX_FLOORS)
    )


async def _load_sources(db: AsyncSession, project_id: str) -> list[_FloorSource]:
    """Chạy câu ở `_sources_stmt` và gói dòng thành `_FloorSource`."""
    rows = (await db.execute(_sources_stmt(project_id))).all()
    return [
        _FloorSource(
            level_id,
            None if run_id is None else (run_id, upload_id),
            None if width is None else (width, height),
        )
        for level_id, run_id, upload_id, width, height in rows
    ]


async def _read_texts(
    storage: ObjectStorage, project_id: str, sources: Sequence[_FloorSource]
) -> list[tuple[TextPx, ...]]:
    """`text.json` của từng tầng theo thứ tự `sources`, tối đa `_READ_CONCURRENCY` lượt đọc cùng lúc."""
    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def one(source: _FloorSource) -> tuple[TextPx, ...]:
        """Chữ của một tầng; chưa có lượt hay artifact vắng/hỏng → rỗng."""
        if source.run is None:
            return ()
        async with gate:
            data = await read_artifact(
                storage, project_id=project_id, floor_id=source.floor_id, run=source.run, step="dimensionReading"
            )
        return parse_texts(data)

    return list(await asyncio.gather(*(one(source) for source in sources)))


def _view(source: _FloorSource, texts: tuple[TextPx, ...], position: int) -> HouseView:
    """`HouseView` của một tầng; thiếu cỡ trang thì lấy hộp bao của chữ (miền chia cho cỡ, không nhận 0)."""
    ocr = tuple(_Ocr(t.text, (t.box.x_min, t.box.y_min, t.box.x_max, t.box.y_max), t.confidence) for t in texts)
    if source.size is not None:
        size = source.size
    else:
        size = (max([1, *(round(o.box[2]) for o in ocr)]), max([1, *(round(o.box[3]) for o in ocr)]))
    return HouseView(source.floor_id, ocr, size, position)


def order_differs_of(storeys: Sequence[tuple[str, int | None]]) -> frozenset[str]:
    """Tầng mà số tầng đề xuất mâu thuẫn thứ tự dự án.

    `storeys` theo `floor_order`. Một tầng bị cờ khi có tầng đứng **trước** nó mang số tầng cao hơn, hoặc
    tầng đứng **sau** nó mang số thấp hơn — nên cả hai đầu của một cặp ngược đều bị cờ. Số bằng nhau
    không mâu thuẫn; tầng không có số (lửng, hình không rõ) bị bỏ qua. Duyệt một lượt theo
    max tiền tố / min hậu tố.
    """
    numbered = [(floor_id, number) for floor_id, number in storeys if number is not None]
    flagged: set[str] = set()
    highest = None
    for floor_id, number in numbered:
        if highest is not None and highest > number:
            flagged.add(floor_id)
        highest = number if highest is None else max(highest, number)
    lowest = None
    for floor_id, number in reversed(numbered):
        if lowest is not None and lowest < number:
            flagged.add(floor_id)
        lowest = number if lowest is None else min(lowest, number)
    return frozenset(flagged)


async def read_house_proposal(session: AsyncSession, storage: ObjectStorage, project_id: str) -> HouseProposalRead:
    """Đề xuất cho cả dự án từ `text.json` của lượt mới nhất của từng tầng.

    Dự án không có tầng → đề xuất rỗng. `solve_house` chạy trong `asyncio.to_thread` vì nó tốn CPU và
    chạy lại trên chữ cả trang của mọi tầng.
    """
    sources = await _load_sources(session, project_id)
    texts = await _read_texts(storage, project_id, sources)
    views = [_view(source, found, index) for index, (source, found) in enumerate(zip(sources, texts, strict=True))]
    proposal = await asyncio.to_thread(solve_house, views, order_known=False)
    return HouseProposalRead(
        proposal=proposal,
        run_ids=tuple(source.run[0] for source, found in zip(sources, texts, strict=True) if source.run and found),
        order_differs=order_differs_of([(floor.floor_id, floor.storey_no) for floor in proposal.floors]),
    )
