"""Đề xuất tầng, cốt và chiều cao cho cả nhà từ chữ OCR đã có của từng tầng (HS-02, chỉ đọc).

Một câu SQL lấy các tầng của dự án theo `floor_order` kèm lượt mới nhất chưa bị thay có trạng thái `completed`
hoặc `failed`, lượt `completed` mới nhất (như `spatial_read.detected.latest_run`) khi nó khác lượt kia, và cỡ trang
của bản vẽ **cùng lượt tải** với lượt mới nhất. Lượt `failed` chỉ góp chữ (L4-N01'): ba bước ML chạy song song nên
lượt hỏng ở `wallSegmentation`/`spatialDataBuild` (mặt cắt bị `NOT_A_FLOOR_PLAN`) vẫn có `text.json`; lượt ấy không
có tệp thì đọc lượt `completed` (tối đa hai lần đọc kho mỗi tầng). Xong câu
SQL là trả kết nối (`rollback`, K36: route không ghi gì). `text.json` của các lượt đọc song song qua cổng kho
(`TaskGroup`: lỗi kho đầu tiên huỷ các lượt đọc còn lại), rồi phân tích JSON + dựng hình + `solve_house` chạy
trong **một** hàm đồng bộ ở nhóm luồng riêng `_SOLVERS` (R-23).

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Select, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.detected import read_artifact
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.db.models.drawings import DrawingRow, PipelineRunRow
from packages.db.models.floors import FloorRow
from packages.domain.drawing_set import FloorProposal, HouseProposal, HouseView, SectionView, solve_house
from packages.domain.drawing_set.proposal import NO_TEXT
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
_SOLVERS: Final = ThreadPoolExecutor(max_workers=2, thread_name_prefix="house-proposal")
"""Luồng giải riêng: quá hạn không dừng được luồng đang chạy, nên số luồng là trần CPU của cả tính năng; request
tới sau xếp hàng và bị bỏ khỏi hàng khi quá `DEADLINE_S`."""


@dataclass(frozen=True)
class HouseProposalRead:
    """Kết quả đọc: đề xuất theo thứ tự tầng của dự án, id lượt dùng chữ, và các dòng người dùng đánh dấu mặt cắt."""

    proposal: HouseProposal
    run_ids: tuple[str, ...]
    section_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _FloorSource:
    """Một tầng của dự án cùng lượt mới nhất `completed|failed` của nó (`None` khi chưa có), cỡ trang bản vẽ của lượt
    tải ấy (`None` khi chưa có), dấu "bản vẽ này là mặt cắt" (`floors.is_section`) và lượt `completed` mới nhất
    khi nó khác `run` (đọc khi `run` không có `text.json`)."""

    floor_id: str
    run: tuple[str, str] | None
    size: tuple[int, int] | None
    section: bool = False
    fallback: tuple[str, str] | None = None


@dataclass(frozen=True)
class _Ocr:
    """Một dòng chữ OCR theo `OcrText`: hộp (x0, y0, x1, y1)."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float


_log: Final = logging.getLogger(__name__)


def _latest_run(name: str, *statuses: str) -> Any:
    """Lượt mới nhất chưa bị thay của tầng (dòng ngoài `FloorRow`) có trạng thái thuộc `statuses`, dạng `LATERAL`.

    Lượt bị lượt mới thay (`superseded_by`, `PIPELINE_SUPERSEDED`) không bao giờ được đọc: kết quả của nó đã bị bỏ.
    """
    return (
        select(PipelineRunRow.id.label("run_id"), PipelineRunRow.upload_id.label("upload_id"))
        .where(
            PipelineRunRow.floor_pk == FloorRow.pk,
            PipelineRunRow.status.in_(statuses),
            PipelineRunRow.superseded_by.is_(None),
        )
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
        .lateral(name)
    )


def _sources_stmt(project_id: str) -> Select[Any]:
    """Mọi tầng chưa xoá của dự án theo `(floor_order, pk)`; hai lượt (mới nhất `completed|failed`, mới nhất
    `completed`) dựng bằng `LATERAL`, một câu cho cả bộ.

    Tầng chưa có lượt vẫn ra (cột lượt `NULL`). `drawings.floor_pk` unique nên join không nhân dòng; bản vẽ chỉ khớp
    khi cùng lượt tải với lượt mới nhất (như `drawings/latest.py`), lệch thì cỡ lấy từ hộp bao chữ.
    """
    latest = _latest_run("latest_run", "completed", "failed")
    done = _latest_run("latest_done", "completed")
    return (
        select(
            FloorRow.level_id,
            latest.c.run_id,
            latest.c.upload_id,
            done.c.run_id,
            done.c.upload_id,
            DrawingRow.width_px,
            DrawingRow.height_px,
            FloorRow.is_section,
        )
        .select_from(FloorRow)
        .outerjoin(latest, true())
        .outerjoin(done, true())
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
            section,
            None if done_id in (None, run_id) else (done_id, done_upload),
        )
        for level_id, run_id, upload_id, done_id, done_upload, width, height, section in rows
    ]


_Blob = tuple[tuple[str, str] | None, bytes | None]
"""(lượt đã đọc, bytes `text.json` của nó); bytes `None`: chưa có lượt, vắng hay quá trần."""


async def _read_blobs(storage: ObjectStorage, project_id: str, sources: Sequence[_FloorSource]) -> list[_Blob]:
    """`text.json` của từng tầng theo thứ tự `sources`: của `run`, vắng thì của `fallback`; kèm lượt đã cho bytes
    (không lượt nào có thì `run` với `None`).

    Tối đa `_READ_CONCURRENCY` lượt đọc cùng lúc. `TaskGroup` huỷ các lượt còn lại ngay khi một lượt lỗi
    (`gather` thì không); lỗi đầu tiên được ném lại nguyên dạng thay vì `ExceptionGroup`.
    """
    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def one(source: _FloorSource) -> _Blob:
        """Lượt và bytes của một tầng."""
        for run in (source.run, source.fallback):
            if run is None:
                continue
            async with gate:
                data = await read_artifact(
                    storage, project_id=project_id, floor_id=source.floor_id, run=run, step="dimensionReading"
                )
            if data is not None:
                return run, data
        return source.run, None

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(source)) for source in sources]
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return [task.result() for task in tasks]


def _texts_of(source: _FloorSource, blob: _Blob) -> tuple[TextPx, ...]:
    """Chữ của một tầng; artifact vắng, quá trần hay sai hợp đồng → rỗng và một dòng log (không nội dung)."""
    run, data = blob
    if run is None:
        return ()
    if data is None:
        _log.warning("house_proposal_text_missing", extra={"floor_id": source.floor_id, "run_id": run[0]})
        return ()
    try:
        return text_from_json(data).items
    except ValueError:
        _log.warning("house_proposal_text_invalid", extra={"floor_id": source.floor_id, "run_id": run[0]})
        return ()


def _view(source: _FloorSource, run: tuple[str, str] | None, texts: tuple[TextPx, ...], position: int) -> HouseView:
    """`HouseView` của một tầng; cỡ trang chỉ dùng khi chữ đến từ cùng lượt tải với bản vẽ, không thì lấy hộp bao
    của chữ (miền chia cho cỡ, không nhận 0)."""
    ocr = tuple(_Ocr(t.text, (t.box.x_min, t.box.y_min, t.box.x_max, t.box.y_max), t.confidence) for t in texts)
    same_upload = run is not None and source.run is not None and run[1] == source.run[1]
    if source.size is not None and same_upload:
        size = source.size
    else:
        size = (max([1, *(round(o.box[2]) for o in ocr)]), max([1, *(round(o.box[3]) for o in ocr)]))
    return HouseView(source.floor_id, ocr, size, position)


def _solve(sources: Sequence[_FloorSource], blobs: Sequence[_Blob]) -> HouseProposalRead:
    """Phần tốn CPU, đồng bộ (chạy trong `_SOLVERS`): phân tích JSON, dựng hình, `solve_house`.

    `sources` theo `(floor_order, pk)` nên vị trí của hình là thứ tự tầng của dự án: số tầng đi theo nó
    (`order_known=True`), chữ trên bản vẽ nói khác thì miền hỏi `ORDER_TEXT_CONFLICT` (HS-03). Dòng người dùng
    đánh dấu mặt cắt không phải một sàn: chữ của nó vào `sections` (nguồn cốt thứ hai), đề xuất của dòng đó chỉ
    còn loại "section" (kèm `NO_TEXT` khi chưa đọc được chữ), không bao giờ `NOT_A_PLAN`.
    """
    texts = [_texts_of(source, blob) for source, blob in zip(sources, blobs, strict=True)]
    views = [
        _view(source, blob[0], found, index)
        for index, (source, blob, found) in enumerate(zip(sources, blobs, texts, strict=True))
    ]
    marked = {view.floor_id: view for source, view in zip(sources, views, strict=True) if source.section}
    solved = solve_house(
        [view for view in views if view.floor_id not in marked],
        order_known=True,
        sections=[SectionView(view.texts, view.size) for view in marked.values()],
    )
    by_id = {floor.floor_id: floor for floor in solved.floors}
    by_id |= {
        floor_id: FloorProposal(floor_id, "section", 1.0, questions=() if view.texts else (NO_TEXT,))
        for floor_id, view in marked.items()
    }
    return HouseProposalRead(
        proposal=HouseProposal(tuple(by_id[source.floor_id] for source in sources)),
        run_ids=tuple(run[0] for (run, _), found in zip(blobs, texts, strict=True) if run and found),
        section_ids=frozenset(marked),
    )


async def read_house_proposal(session: AsyncSession, storage: ObjectStorage, project_id: str) -> HouseProposalRead:
    """Đề xuất cho cả dự án từ `text.json` của lượt mới nhất (`completed` hay `failed`) của từng tầng (tối đa
    `MAX_FLOORS` tầng đầu).

    Dự án không có tầng → đề xuất rỗng. Trả kết nối DB ngay sau câu SQL (K36). Đọc + giải quá `DEADLINE_S`
    → 503 `DEPENDENCY_UNAVAILABLE`.
    """
    sources = await _load_sources(session, project_id)
    await session.rollback()
    try:
        async with asyncio.timeout(DEADLINE_S):
            blobs = await _read_blobs(storage, project_id, sources)
            # ponytail: quá hạn không dừng được luồng giải đang chạy; nó tự xong và chỉ chiếm một chỗ của `_SOLVERS`.
            return await asyncio.get_running_loop().run_in_executor(_SOLVERS, _solve, sources, blobs)
    except TimeoutError as exc:
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=RETRY_AFTER_S) from exc
