"""Nguồn duy nhất của `Progress` (bảng "Nguồn trạng thái").

Luật nằm ở **một** hàm thuần `progress_of`: #8 và luồng S1 gọi `progress_wire` (một lượt
tải, một truy vấn), còn N7 dựng cả trang bằng **một** truy vấn rồi gọi thẳng `progress_of`
cho từng dòng — không bản sao thứ hai của bảng §4.

Module này là "hàm worker nhập" (BE-00 §7): không nhập `fastapi`/`starlette`, trả `dict`
khoá dây chứ không `WireModel`.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.instants import to_wire
from packages.db.models.drawings import PipelineRunRow, UploadRow

LAST_STEP: Final = "qualityCheck"
"""Bước hiển thị của lượt đã xong: FE vẽ mọi bước "xong" khi `completed`."""

FIRST_STEP: Final = "preprocess"
"""Bước hiển thị khi chưa có gì chạy: `pending` và upload `rejected` đều đứng ở đây."""

QUEUED_RUN_STATUSES: Final = ("pending", "running")
"""Lượt chạy còn chiếm hàng đợi ML — cùng bộ với `runs.LIVE_RUN_STATUSES` (module này không nhập `runs`)."""


def _pending(upload_id: str) -> dict[str, object]:
    """Hàng 1 của bảng §4: upload `receiving`, hoặc lượt mới nhất còn `pending`."""
    return {"id": upload_id, "status": "pending", "step": FIRST_STEP, "progressPercent": 0}


def progress_of(
    upload_id: str,
    *,
    upload_status: str,
    rejected_code: str | None,
    run_status: str | None,
    current_step: str | None,
    progress_percent: int | None,
    error_code: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    queue_ahead: int | None = None,
) -> dict[str, object]:
    """Bảng "Nguồn trạng thái", từ giá trị cột sang dict khoá dây.

    Hàm thuần: người gọi đã có sẵn các cột (một truy vấn cho cả trang ở N7) thì không phải
    vào DB lần nữa. `run_*` là `None` khi lượt tải chưa có lượt chạy nào.

    Ba bất biến của FE: `endedAt` **chỉ** khi `completed` (lượt hỏng không có, K33); `error`
    luôn là mã UPPER_SNAKE; trường vắng thì vắng hẳn, không `null` (W2).

    `queueAhead` (UX41 U02): số lượt chạy khác đứng trước trong hàng đợi, chỉ khi lượt còn
    sống (`pending`/`running`) và người gọi đã đếm; 0 = đang tới lượt mình.
    """
    if upload_status == "rejected":
        return _pending(upload_id) | {"status": "failed", "error": rejected_code or ""}
    queue = {"queueAhead": queue_ahead} if queue_ahead is not None and run_status in QUEUED_RUN_STATUSES else {}
    if upload_status != "complete" or run_status is None or run_status == "pending":
        return _pending(upload_id) | (queue if upload_status == "complete" else {})

    wire: dict[str, object] = {
        "id": upload_id,
        "status": run_status,
        "step": LAST_STEP if run_status == "completed" else current_step,
        "progressPercent": 100 if run_status == "completed" else progress_percent,
    }
    if started_at is not None:
        wire["startedAt"] = to_wire(started_at)
    if run_status == "completed" and ended_at is not None:
        wire["endedAt"] = to_wire(ended_at)
    if run_status == "failed":
        wire["error"] = error_code or ""
    return wire | queue


async def _queue_ahead(db: AsyncSession, run_id: str, created_at: datetime) -> int:
    """Số lượt chạy còn sống, chưa bị thay, mở **trước** lượt `run_id` — mọi dự án dùng chung hàng ML.

    ponytail: đếm như thể ML chạy từng lượt một; worker ML song song N lượt thì số này là cận trên.
    """
    stmt = select(func.count()).where(
        PipelineRunRow.status.in_(QUEUED_RUN_STATUSES),
        PipelineRunRow.superseded_by.is_(None),
        tuple_(PipelineRunRow.created_at, PipelineRunRow.id) < tuple_(literal(created_at), literal(run_id)),
    )
    return int((await db.execute(stmt)).scalar_one())


async def progress_wire(db: AsyncSession, upload_id: str) -> dict[str, object]:
    """`Progress` của một lượt tải và lượt chạy **mới nhất** của nó; thêm một câu đếm hàng đợi khi lượt còn sống.

    Không có dòng upload → `ValueError`: mọi người gọi (#5-#8, S1, `record_step`) đã tra
    upload trước đó, nên đây là lỗi lập trình chứ không phải 404 của người dùng.
    """
    latest = (
        select(PipelineRunRow.id)
        .where(PipelineRunRow.upload_id == UploadRow.id)
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
        .correlate(UploadRow)
        .scalar_subquery()
    )
    stmt = (
        select(
            UploadRow.status,
            UploadRow.rejected_code,
            PipelineRunRow.status,
            PipelineRunRow.current_step,
            PipelineRunRow.progress_percent,
            PipelineRunRow.error_code,
            PipelineRunRow.started_at,
            PipelineRunRow.ended_at,
            PipelineRunRow.id,
            PipelineRunRow.created_at,
        )
        .select_from(UploadRow)
        .outerjoin(PipelineRunRow, PipelineRunRow.id == latest)
        .where(UploadRow.id == upload_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise ValueError(f"không có lượt tải {upload_id!r}")
    upload_status, rejected_code, run_status, step, percent, error_code, started_at, ended_at, run_id, run_at = row
    live = upload_status == "complete" and run_status in QUEUED_RUN_STATUSES
    return progress_of(
        upload_id,
        upload_status=upload_status,
        rejected_code=rejected_code,
        run_status=run_status,
        current_step=step,
        progress_percent=percent,
        error_code=error_code,
        started_at=started_at,
        ended_at=ended_at,
        queue_ahead=await _queue_ahead(db, run_id, run_at) if live else None,
    )
