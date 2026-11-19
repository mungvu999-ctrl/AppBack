"""Đề xuất tầng, cốt và chiều cao cho cả nhà từ chữ OCR đã có của từng tầng (HS-02, chỉ đọc).

Một câu SQL lấy các tầng của dự án theo `floor_order` kèm lượt `completed` mới nhất chưa bị thay
(cùng luật `spatial_read.detected.latest_run`) và cỡ trang của bản vẽ **cùng lượt tải** với lượt ấy. Xong câu
SQL là trả kết nối (`rollback`, K36: route không ghi gì). `text.json` của các lượt đọc song song qua cổng kho
(`TaskGroup`: lỗi kho đầu tiên huỷ các lượt đọc còn lại), rồi phân tích JSON + dựng hình + `solve_house` chạy
trong **một** hàm đồng bộ ở `asyncio.to_thread` (R-23).

Trần mỗi request: `MAX_FLOORS` tầng đầu theo `(floor_order, pk)` — tầng thứ 201 trở đi bị **bỏ khỏi** phản hồi
(trùng trần `_MAX_VIEWS` của bộ giải; nhà thật không có ngần ấy tầng); mỗi artifact tối đa
`ARTIFACT_JSON_MAX_BYTES` (bộ đọc dừng khi vượt); cả phần đọc + giải trong `DEADLINE_S` (dưới trần 15 s của
W11), quá hạn → 503 `DEPENDENCY_UNAVAILABLE`, không trả nửa kết quả.

Không ghi DB, không gọi ML. Tầng chưa có lượt, artifact vắng, quá trần hay sai hợp đồng chỉ có nghĩa
"không có chữ" — miền trả `NO_TEXT` cho tầng đó (ghi log một dòng mỗi tầng, không kèm nội dung), người gọi
không nhận lỗi. Lỗi kho tạm thời (`DEPENDENCY_UNAVAILABLE`) thì lan nguyên để client thử lại (R-16).
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Select, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.detected import read_artifact
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.db.models.drawings import DrawingRow, PipelineRunRow
from packages.db.models.floors import FloorRow
from packages.domain.drawing_set import HouseProposal, HouseView, solve_house
from packages.ml_contracts.artifacts import TextPx, text_from_json
from packages.storage.port import ObjectStorage

MAX_FLOORS: Final = 200
"""Trần số tầng một request xét (= `_MAX_VIEWS` của bộ giải); tầng sau đó theo `floor_order` bị bỏ khỏi phản hồi."""
DEADLINE_S: Final = 12.0
"""Trần thời gian đọc + giải của một request (W11: < 15 s)."""
RETRY_AFTER_S: Final = 5
"""`Retry-After` của 503 khi quá `DEADLINE_S` (W9: ≤ 10 s)."""
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


_log: Final = logging.getLogger(__name__)


def _sources_stmt(project_id: str) -> Select[Any]:
    """Mọi tầng chưa xoá của dự án theo `(floor_order, pk)`; lượt mới nhất dựng bằng `LATERAL`, một câu cho cả bộ.

    Tầng chưa có lượt `completed` vẫn ra (cột lượt `NULL`). `drawings.floor_pk` unique nên join không nhân dòng;
    bản vẽ chỉ khớp khi cùng lượt tải với lượt chạy (như `drawings/latest.py`), lệch thì cỡ lấy từ hộp bao chữ.
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
        .outerjoin(DrawingRow, (DrawingRow.floor_pk == FloorRow.pk) & (DrawingRow.upload_id == latest.c.upload_id))
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


async def _read_blobs(storage: ObjectStorage, project_id: str, sources: Sequence[_FloorSource]) -> list[bytes | None]:
    """Bytes `text.json` của từng tầng theo thứ tự `sources` (`None`: chưa có lượt, vắng hay quá trần).

    Tối đa `_READ_CONCURRENCY` lượt đọc cùng lúc. `TaskGroup` huỷ các lượt còn lại ngay khi một lượt lỗi
    (`gather` thì không); lỗi đầu tiên được ném lại nguyên dạng thay vì `ExceptionGroup`.
    """
    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def one(source: _FloorSource) -> bytes | None:
        """Bytes của một tầng."""
        if source.run is None:
            return None
        async with gate:
            return await read_artifact(
                storage, project_id=project_id, floor_id=source.floor_id, run=source.run, step="dimensionReading"
            )

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(source)) for source in sources]
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return [task.result() for task in tasks]


def _texts_of(source: _FloorSource, data: bytes | None) -> tuple[TextPx, ...]:
    """Chữ của một tầng; artifact vắng, quá trần hay sai hợp đồng → rỗng và một dòng log (không nội dung)."""
    if source.run is None:
        return ()
    if data is None:
        _log.warning("house_proposal_text_missing", extra={"floor_id": source.floor_id, "run_id": source.run[0]})
        return ()
    try:
        return text_from_json(data).items
    except ValueError:
        _log.warning("house_proposal_text_invalid", extra={"floor_id": source.floor_id, "run_id": source.run[0]})
        return ()


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


def _solve(sources: Sequence[_FloorSource], blobs: Sequence[bytes | None]) -> HouseProposalRead:
    """Phần tốn CPU, đồng bộ (chạy trong `to_thread`): phân tích JSON, dựng hình, `solve_house`."""
    texts = [_texts_of(source, data) for source, data in zip(sources, blobs, strict=True)]
    views = [_view(source, found, index) for index, (source, found) in enumerate(zip(sources, texts, strict=True))]
    proposal = solve_house(views, order_known=False)
    return HouseProposalRead(
        proposal=proposal,
        run_ids=tuple(source.run[0] for source, found in zip(sources, texts, strict=True) if source.run and found),
        order_differs=order_differs_of([(floor.floor_id, floor.storey_no) for floor in proposal.floors]),
    )


async def read_house_proposal(session: AsyncSession, storage: ObjectStorage, project_id: str) -> HouseProposalRead:
    """Đề xuất cho cả dự án từ `text.json` của lượt mới nhất của từng tầng (tối đa `MAX_FLOORS` tầng đầu).

    Dự án không có tầng → đề xuất rỗng. Trả kết nối DB ngay sau câu SQL (K36). Đọc + giải quá `DEADLINE_S`
    → 503 `DEPENDENCY_UNAVAILABLE`.
    """
    sources = await _load_sources(session, project_id)
    await session.rollback()
    try:
        async with asyncio.timeout(DEADLINE_S):
            blobs = await _read_blobs(storage, project_id, sources)
            # ponytail: quá hạn không dừng được luồng giải đang chạy; nó tự xong (bộ giải có trần `_MAX_VIEWS`).
            return await asyncio.to_thread(_solve, sources, blobs)
    except TimeoutError as exc:
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=RETRY_AFTER_S) from exc
