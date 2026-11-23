"""Lịch dọn tầng xoá mềm (BE-00 §7 "khuôn lịch nền", "dọn rác"; B2-03 [6] "Lịch dọn").

`apps/worker` nhập module này để chạy beat, nên nó chỉ nhập `packages.core`, `packages.db`,
`packages.storage`, `packages.messaging` và `apps.api.projects.summaries` — không `fastapi`,
`starlette`, `jwt`, `argon2` (BE-00 §2.1, K36). Khoá tư vấn dùng chung với `lookup.py` qua
`apps.api.floors.locks` (không nhập thẳng `apps.api.floors.lookup`: module đó nhập
`apps.api.projects.parts`, mà `parts.py` nhập `apps.api.core.auth` dưới `TYPE_CHECKING` —
`lint-imports` (grimp) tính cả import có điều kiện là cạnh thật, nên nhập `lookup` sẽ kéo
`starlette` vào `apps.api.floors.jobs` và vỡ ranh giới worker). `locks.py` chỉ nhập
`sqlalchemy` nên cả hai module dùng chung đúng một hàm, một chuỗi khoá (R-07).

Mỗi tầng đi qua ba bước session **ngắn** (R-20: không giữ giao dịch DB lúc gọi kho qua
mạng, và mỗi tầng một giao dịch riêng thay vì ôm cả lô): (1) chọn ứng viên quá hạn kèm cờ
"còn dòng `floors` khác cùng `(project_id, level_id)`" bằng một câu, đóng session ngay;
(2) gọi lưu trữ ngoài mọi giao dịch, chỉ khi cờ tắt; (3) khoá tư vấn rồi `DELETE` có điều
kiện `deleted_at < cutoff` — tầng vừa được khôi phục giữa bước 1 và 3 không khớp điều kiện
này nên không bị xoá — rồi tính lại cờ **trong cùng giao dịch** để quyết có xoá dòng đếm.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, CursorResult, MetaData, delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from apps.api.floors.locks import lock_project_floors
from apps.api.floors.settings import get_floors_settings
from apps.api.projects.summaries import purge_floor
from packages.core.clock import Clock, SystemClock
from packages.core.errors import AppError
from packages.core.object_keys import project_prefix
from packages.db.engine import worker_sessionmaker
from packages.db.models.floors import FloorRow
from packages.messaging import periodic
from packages.storage.port import ObjectStorage

_log: Final = logging.getLogger(__name__)

TASK_NAME: Final = "default.floors.purge_deleted"
EVERY: Final = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Một tầng quá hạn của lô: khoá chính, dự án/level và cờ "còn dòng khác cùng level_id"."""

    pk: int
    project_id: str
    level_id: str
    has_sibling: bool


def _sibling_flag() -> ColumnElement[bool]:
    """Cờ "có dòng `floors` khác (sống hoặc xoá mềm) cùng `(project_id, level_id)`", cột phụ."""
    sibling = aliased(FloorRow)
    return exists(
        select(1).where(
            sibling.project_id == FloorRow.project_id,
            sibling.level_id == FloorRow.level_id,
            sibling.pk != FloorRow.pk,
        )
    )


async def _select_batch(
    sessionmaker: async_sessionmaker[AsyncSession], cutoff: datetime, batch: int, after: int | None
) -> list[_Candidate]:
    """`batch` tầng xoá mềm quá hạn nhất, `pk ASC`, kèm cờ; session đóng ngay khi trả (K36).

    Cờ và ứng viên đến từ **một** câu: tách hai câu là race giữa lúc đọc cờ và lúc một dòng
    khác biến mất hay xuất hiện. `after` (keyset theo `pk`) cho lô sau, như B2-01
    `run_project_purge`: chọn lại từ đầu mỗi lô sẽ lặp vô hạn khi cả một lô hỏng (RES-02).
    """
    stmt = (
        select(FloorRow.pk, FloorRow.project_id, FloorRow.level_id, _sibling_flag())
        .where(FloorRow.deleted_at < cutoff)
        .order_by(FloorRow.pk)
        .limit(batch)
    )
    if after is not None:
        stmt = stmt.where(FloorRow.pk > after)
    async with sessionmaker() as session:
        rows = await session.execute(stmt)
        return [
            _Candidate(pk=pk, project_id=project_id, level_id=level_id, has_sibling=bool(sibling))
            for pk, project_id, level_id, sibling in rows
        ]


async def _delete_objects(storage: ObjectStorage, candidate: _Candidate) -> bool:
    """Xoá mọi object của tầng; kho hỏng (`AppError` `DEPENDENCY_UNAVAILABLE`) → log, `False`."""
    try:
        await storage.delete_prefix(f"{project_prefix(candidate.project_id)}floors/{candidate.level_id}/")
    except AppError:
        _log.warning("floor_purge_failed", extra={"pk": candidate.pk})
        return False
    return True


async def _has_sibling(session: AsyncSession, candidate: _Candidate) -> bool:
    """Cờ tính lại **trong** giao dịch xoá dòng (khác câu `_select_batch`, cùng luật)."""
    stmt = select(FloorRow.project_id).where(
        FloorRow.project_id == candidate.project_id, FloorRow.level_id == candidate.level_id
    )
    return (await session.execute(stmt)).first() is not None


async def _delete_row(sessionmaker: async_sessionmaker[AsyncSession], candidate: _Candidate, cutoff: datetime) -> bool:
    """Khoá tư vấn, xoá dòng nếu còn quá hạn, rồi xoá dòng đếm nếu không còn dòng khác cùng level_id.

    `DELETE … WHERE pk = :pk AND deleted_at < :cutoff`: tầng khôi phục giữa bước 1 và bước
    này (`deleted_at` về `NULL`) không khớp, `rowcount == 0`, không đụng dòng đếm — trả
    `False` để `run_floor_purge` không đếm dòng chưa từng bị xoá (J06). `ON DELETE CASCADE`
    của FK `floors.pk` (`check_floor_fk_cascade`) đưa bảng con của prompt sau theo `DELETE` này.
    """
    async with sessionmaker() as session:
        await lock_project_floors(session, candidate.project_id)
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(FloorRow).where(FloorRow.pk == candidate.pk, FloorRow.deleted_at < cutoff)),
        )
        deleted = result.rowcount > 0
        if deleted and not await _has_sibling(session, candidate):
            await purge_floor(session, project_id=candidate.project_id, floor_level_id=candidate.level_id)
        await session.commit()
        return deleted


async def run_floor_purge(
    sessionmaker: async_sessionmaker[AsyncSession], storage: ObjectStorage, clock: Clock, *, batch: int
) -> int:
    """Xoá cứng mọi tầng xoá mềm quá hạn, theo lô; trả tổng số dòng `floors` đã xoá.

    Phân trang keyset (`pk > after`, như B2-01 `run_project_purge`): tầng mà lưu trữ vẫn
    hỏng **ở lại** trong bảng, nên chọn lại từ đầu mỗi lô sẽ lấy đúng lô cũ và không bao giờ
    tiến (RES-02). Mỗi tầng được thử đúng một lần mỗi lượt gọi.
    """
    settings = get_floors_settings()
    cutoff = clock.now() - timedelta(seconds=settings.floor_restore_window_s, days=settings.floor_purge_after_d)
    purged = 0
    after: int | None = None
    while True:
        candidates = await _select_batch(sessionmaker, cutoff, batch, after)
        for candidate in candidates:
            if (candidate.has_sibling or await _delete_objects(storage, candidate)) and await _delete_row(
                sessionmaker, candidate, cutoff
            ):
                purged += 1
        if len(candidates) < batch:
            break
        after = candidates[-1].pk
    _log.info("floor_purge_completed", extra={"purged": purged})
    return purged


_PURGE_SAFE: Final = frozenset({"CASCADE", "SET NULL"})
"""`ondelete` của FK tới `floors.pk` mà `DELETE` của lịch dọn đi qua được, không để mồ côi."""


def check_floor_fk_cascade(metadata: MetaData) -> None:
    """Mọi FK trỏ `floors.pk` phải `ondelete="CASCADE"` hay `"SET NULL"`, không thì `RuntimeError` nêu bảng/cột.

    Lịch dọn ở trên xoá dòng `floors` bằng một `DELETE` và trông cậy CASCADE để mọi bảng con
    của prompt sau (bản vẽ, lớp không gian, …) tự đi theo — thiếu CASCADE ở một FK nghĩa là
    bảng con ấy mồ côi, không được lịch nào dọn, phải hỏng ngay lúc kiểm chứ không đợi tới
    khi dữ liệu thật rò rỉ. `SET NULL` cũng không chặn `DELETE` và không để mồ côi: dòng ấy thuộc
    về cha khác, chỉ tham chiếu tầng (đích của hình trong bộ hồ sơ, K-32a `drawing_set_views`).
    """
    for table in metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "floors" and fk.column.name == "pk" and fk.ondelete not in _PURGE_SAFE:
                raise RuntimeError(f"{table.name}.{fk.parent.name} trỏ floors.pk phải ondelete='CASCADE'")


@periodic(TASK_NAME, every=EVERY)
async def purge_deleted_floors() -> None:
    """Hàm lịch: chỉ dựng tài nguyên thật rồi gọi lõi (BE-00 §7 "khuôn lịch nền").

    `create_storage` nhập trễ như B2-01 `purge_deleted_projects`: `minio` tự nhập `argon2`
    ngay ở `__init__.py`, nên nhập nó ở đầu module sẽ phá ranh giới "`apps.api.*.jobs` không
    nhập `argon2`" (BE-00 §2.1) dù mã của module này không hề chạm `argon2`.
    """
    from packages.core.settings import get_core_settings
    from packages.storage.factory import create_storage
    from packages.storage.settings import get_storage_settings

    clock = SystemClock()
    storage = create_storage(get_storage_settings(), get_core_settings(), clock)
    await run_floor_purge(worker_sessionmaker(), storage, clock, batch=get_floors_settings().floor_purge_batch)
