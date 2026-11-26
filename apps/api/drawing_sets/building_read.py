"""Đọc đề xuất ngôi nhà của một bộ hồ sơ (NHA-06a, thiết kế §2.E; chỉ đọc).

Đề xuất tính ngay lúc đọc bằng `propose_building`, không lưu DB. Đầu vào là thứ **người duyệt đã chốt** ở N59: loại
hình và khung của từng dòng `drawing_set_views` chưa bị loại, cộng chữ OCR của trang (`PageReadingDoc` worker `ml`
ghi lúc phân tích). Hình `plan` là tầng; hình `roof` và `section` chỉ thành nguồn khi loại đã chắc (máy xếp từ
`NOT_PLAN_SURE`, hoặc người duyệt tự đặt — N59 ghi độ tin 1).

Hai câu SQL (bộ, hình) rồi trả kết nối (`rollback`, K36). Kết quả đọc của các trang lấy song song qua cổng kho; phần
dựng hình + giải chạy trong nhóm luồng riêng `_BUILDERS` (R-23). Cả phần đọc + giải trong `DEADLINE_S`, quá hạn → 503
`DEPENDENCY_UNAVAILABLE`, không trả nửa kết quả (mẫu N52, `service.py`). Trang thiếu hay hỏng kết quả đọc chỉ có
nghĩa "hình không có chữ".

shortcut: số tầng người duyệt sửa ở N59 (`storey_no`) chưa vào đề xuất, và thứ tự hình không được coi là thứ tự tầng
(`order_known=False`, như lúc máy phân tích); nối khi NHA-06b lưu quyết định của người duyệt.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawing_sets.analysis import page_readings
from apps.api.drawing_sets.assembly import FROZEN_VERSION, frozen_proposal
from apps.api.drawing_sets.service import DEADLINE_S, RETRY_AFTER_S
from apps.api.drawing_sets.set_read import FULL_PAGE, load_set
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.db.models.drawing_sets import DrawingSetRow, DrawingSetViewRow
from packages.domain.drawing_set import HouseView, SectionView
from packages.domain.drawing_set.building import BuildingProposal
from packages.domain.drawing_set.decisions import Decisions, decide
from packages.domain.drawing_set.set_analysis import (
    NOT_PLAN_SURE,
    Box,
    PageReading,
    Text,
    assign_texts,
    is_ladder_section,
)
from packages.storage.port import ObjectStorage

__all__ = [
    "DEADLINE_S",
    "FROZEN_VERSION",
    "RETRY_AFTER_S",
    "BuildingInputs",
    "load_inputs",
    "propose",
    "read_building",
]

_log: Final = logging.getLogger(__name__)

_ANALYSED: Final = frozenset({"review", "confirmed"})
"""Trạng thái mà bộ đã có hình và kết quả đọc trang."""
_READ_CONCURRENCY: Final = 4
"""Số kết quả đọc lấy cùng lúc (như N52)."""
_BUILDERS: Final = ThreadPoolExecutor(max_workers=2, thread_name_prefix="set-building")
"""Luồng giải riêng: quá hạn không dừng được luồng đang chạy, nên số luồng là trần CPU của route này."""

type _Page = tuple[str, int]


@dataclass(frozen=True)
class _ViewSource:
    """Một hình chưa bị loại của bộ: mã, trang, khung theo tỉ lệ trang, loại và độ tin của loại."""

    view_id: str
    page: _Page
    crop: Mapping[str, float]
    kind: str
    kind_confidence: float


@dataclass(frozen=True)
class BuildingInputs:
    """Đầu vào của một lần tính đề xuất: hình chưa bị loại theo `ord` và quyết định của người duyệt (nếu có)."""

    sources: tuple[_ViewSource, ...]
    decisions: Decisions | None


async def _load_views(db: AsyncSession, set_id: str) -> list[_ViewSource]:
    """Hình chưa bị loại của bộ theo `ord`, chép ra dữ liệu thường (dòng ORM hết hạn sau `rollback`)."""
    rows = (
        await db.execute(
            select(DrawingSetViewRow)
            .where(DrawingSetViewRow.set_id == set_id, DrawingSetViewRow.excluded.is_(False))
            .order_by(DrawingSetViewRow.ord)
        )
    ).scalars()
    return [
        _ViewSource(row.id, (row.file_id, row.page_index), row.crop or FULL_PAGE, row.kind, row.kind_confidence)
        for row in rows
    ]


async def _read_pages(
    storage: ObjectStorage, project_id: str, set_id: str, pages: Sequence[_Page]
) -> dict[_Page, PageReading]:
    """Kết quả đọc của từng trang, tối đa `_READ_CONCURRENCY` lượt cùng lúc; lỗi kho đầu tiên huỷ các lượt còn lại."""
    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def one(page: _Page) -> PageReading:
        """Kết quả đọc một trang."""
        async with gate:
            return (await page_readings(storage, project_id, set_id, [page]))[0]

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(page)) for page in pages]
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return {page: task.result() for page, task in zip(pages, tasks, strict=True)}


def _box(crop: Mapping[str, float], size: tuple[int, int]) -> Box:
    """Khung theo tỉ lệ trang → hộp điểm ảnh `(x0, y0, x1, y1)`."""
    width, height = size
    x0, y0 = crop["x_ratio"] * width, crop["y_ratio"] * height
    return x0, y0, x0 + crop["width_ratio"] * width, y0 + crop["height_ratio"] * height


def _propose(inputs: BuildingInputs, readings: Mapping[_Page, PageReading]) -> BuildingProposal:
    """Phần tốn CPU, đồng bộ (chạy trong `_BUILDERS`): chia chữ của trang cho từng hình rồi `decide`.

    Chữ thuộc hình nhỏ nhất chứa tâm hộp chữ (`assign_texts`, như lúc máy phân tích), nên hình lồng nhau trên một
    trang không nhận trùng chữ.
    """
    sources = inputs.sources
    drawn: dict[str, tuple[tuple[Text, ...], tuple[int, int]]] = {}
    for page, reading in readings.items():
        group = [source for source in sources if source.page == page]
        boxes = [_box(source.crop, reading.size) for source in group]
        for source, box, texts in zip(group, boxes, assign_texts(boxes, reading.texts), strict=True):
            drawn[source.view_id] = texts, (max(1, round(box[2] - box[0])), max(1, round(box[3] - box[1])))
    plans = [source for source in sources if source.kind == "plan"]
    sure = [source for source in sources if source.kind_confidence >= NOT_PLAN_SURE]
    return decide(
        inputs.decisions,
        [HouseView(source.view_id, *drawn[source.view_id], index) for index, source in enumerate(plans)],
        [
            SectionView(*drawn[source.view_id])
            for source in sources
            if is_ladder_section(source.kind, source.kind_confidence, *drawn[source.view_id])
        ],
        roofs=[HouseView(source.view_id, *drawn[source.view_id], 0) for source in sure if source.kind == "roof"],
    )


def stored_decisions(drawing_set: DrawingSetRow) -> Decisions | None:
    """Quyết định của người duyệt lưu trong `building.decisions`; `None` khi chưa ai gửi.

    JSONB không còn đúng hình dạng thì ném `ValidationError` (500): thà hỏng ồn ào còn hơn xác nhận bằng quyết định
    đọc thiếu.
    """
    raw = (drawing_set.building or {}).get("decisions")
    return None if raw is None else Decisions.model_validate(raw)


def _frozen(drawing_set: DrawingSetRow) -> BuildingProposal | None:
    """Bản đề xuất đóng băng của bộ đã xác nhận; `None` khi bộ chưa xác nhận hoặc chưa từng đóng băng.

    Bản lưu khác `FROZEN_VERSION`, hoặc không còn khớp mô hình hiện tại (mã mới thêm trường), cũng trả `None` kèm một
    dòng log: route đọc khi đó tính lại, chứ không trả 500 mãi mãi cho một bộ đã xác nhận.
    """
    return frozen_proposal(drawing_set.building, drawing_set.id) if drawing_set.status == "confirmed" else None


async def load_inputs(db: AsyncSession, drawing_set: DrawingSetRow) -> BuildingInputs:
    """Mọi thứ của DB mà đề xuất cần (hình chưa bị loại, quyết định đã lưu), chép ra trước khi trả kết nối."""
    return BuildingInputs(tuple(await _load_views(db, drawing_set.id)), stored_decisions(drawing_set))


async def propose(storage: ObjectStorage, project_id: str, set_id: str, inputs: BuildingInputs) -> BuildingProposal:
    """Đọc kết quả đọc của các trang rồi tính đề xuất kèm quyết định; không đụng DB.

    Đọc + giải quá `DEADLINE_S` → 503 `DEPENDENCY_UNAVAILABLE`, không trả nửa kết quả.
    """
    pages = sorted({source.page for source in inputs.sources})
    try:
        async with asyncio.timeout(DEADLINE_S):
            readings = await _read_pages(storage, project_id, set_id, pages)
            # quá hạn không dừng được luồng giải đang chạy; nó tự xong và chỉ chiếm một chỗ của `_BUILDERS`.
            return await asyncio.get_running_loop().run_in_executor(_BUILDERS, _propose, inputs, readings)
    except TimeoutError as exc:
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=RETRY_AFTER_S) from exc


async def read_building(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, set_id: str
) -> tuple[BuildingProposal, Decisions | None] | None:
    """Đề xuất ngôi nhà của một bộ kèm quyết định đã lưu; `None` khi bộ chưa qua phân tích.

    Bộ đã xác nhận và có bản đóng băng (`building.confirmed`, N60 ghi) → trả đúng bản đó, không tính lại: mã mới
    triển khai sau lúc xác nhận không được làm đề xuất khác cái người dùng đã nhận. Bộ không có, đã xoá hay của dự án
    khác → 404 `resource:"drawingSet"`.
    """
    drawing_set = await load_set(db, project_id=project_id, set_id=set_id)
    if drawing_set.status not in _ANALYSED:
        await db.rollback()
        return None
    frozen = _frozen(drawing_set)
    if frozen is not None:
        found = frozen, stored_decisions(drawing_set)
        await db.rollback()
        return found
    inputs = await load_inputs(db, drawing_set)
    await db.rollback()
    return await propose(storage, project_id, set_id, inputs), inputs.decisions
