"""Ba bước tải của bộ hồ sơ: N54 khai tệp, N55 nhận khúc, N56 chốt (K-32a §3).

Cùng ba luật của lượt tải từng tầng (`apps/api/drawings/uploads.py`, `complete.py`), dùng lại đúng mã của nó:

- **Tin magic bytes, không tin đuôi** (K14): N54 chỉ từ chối sớm `.dwg`/MIME lệch; N56 nhận diện bằng `sniff_kind`.
- **Không giữ kết nối DB khi chờ việc chậm** (K36): N55 và N56 `rollback` trước khi giải mã, băm, đọc/ghi kho; giao
  dịch cuối mở lại chỉ để ghi dòng, dưới khoá dòng bộ.
- **Tệp hỏng không làm hỏng cả bộ**: N56 ghi `rejected` + mã U-case cho tệp đó và đi tiếp; mọi tệp hỏng → bộ `failed`
  `DRAWING_SET_ALL_FILES_REJECTED`. Lỗi 5xx của kho ném tiếp, tệp giữ `receiving` để gọi lại N56.
"""

import asyncio
import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.access.activity import record_activity
from apps.api.access.kinds import ActivityKind
from apps.api.core.auth import Principal
from apps.api.drawing_sets.errors import (
    ALL_FILES_REJECTED,
    DRAWING_SET_NOT_RECEIVING,
    DRAWING_SET_TOO_MANY_PAGES,
    file_not_found,
    set_not_found,
)
from apps.api.drawing_sets.jobs import send_analyze_after_commit
from apps.api.drawing_sets.set_read import load_set, set_wire
from apps.api.drawing_sets.set_schemas import CreateSetBody, DrawingSetFileOut, DrawingSetOut, SetChunkBody
from apps.api.drawings.complete import _Chunk, _concat, _EndMarker, _page_count, _read_head
from apps.api.drawings.errors import UPLOAD_CHUNKS_CHANGED
from apps.api.drawings.settings import get_drawings_settings
from apps.api.drawings.uploads import (
    EXT_KIND,
    KIND_MIME,
    OCTET_STREAM,
    _decode_chunk,
    _digest,
    declared_kind,
    file_extension,
)
from packages.core.clock import Clock
from packages.core.error_codes import (
    CAD_NOT_SUPPORTED,
    FILE_CORRUPT,
    FILE_TYPE_MISMATCH,
    UPLOAD_INCOMPLETE,
    UPLOAD_SIZE_MISMATCH,
    VALIDATION,
)
from packages.core.errors import AppError
from packages.core.ids import new_id
from packages.db.models.drawing_sets import DrawingSetFileChunkRow, DrawingSetFileRow, DrawingSetRow
from packages.storage.keys import drawing_set_chunk, drawing_set_original
from packages.storage.port import ObjectStorage
from packages.vision.preprocess import sniff_kind

SET_BYTES_MAX: Final = 200 * 1024 * 1024
"""Trần tổng kích thước khai của một bộ (K-32a §3); vượt → 422 `VALIDATION` `field:"files"`."""
PDF_PAGES_MAX: Final = 20
"""Trần trang mỗi PDF (khớp `PAGE_INDEX_MAX = 19` của #5)."""
SET_PAGES_MAX: Final = 40
"""Trần trang cả bộ (K-32a quyết định 1)."""


@dataclass(frozen=True, slots=True)
class _FileFacts:
    """Ảnh chụp một tệp `receiving` trước khi thả kết nối (K36); dataclass đông cứng, không giữ đối tượng ORM."""

    id: str
    file_name: str
    declared_size_bytes: int
    chunk_count: int

    @property
    def expected_kind(self) -> str:
        """Loại tệp mà đuôi hứa hẹn; N56 so với `sniff_kind` (U08)."""
        return EXT_KIND.get(file_extension(self.file_name), "unknown")


@dataclass(frozen=True, slots=True)
class _Built:
    """Kết quả nối + nhận diện một tệp lành ở N56."""

    sniffed: str
    sha256: str
    original_key: str
    page_count: int


async def create_set(
    db: AsyncSession,
    storage: ObjectStorage,
    *,
    project_id: str,
    body: CreateSetBody,
    principal: Principal,
    clock: Clock,
) -> DrawingSetOut:
    """N54 — mở một bộ `receiving` với các tệp đã khai; 201.

    Lỗi: `CAD_NOT_SUPPORTED` (U12), `FILE_TYPE_MISMATCH` (U08; MIME rỗng được nhận — U09), 422 `field`
    `files.<i>.sizeBytes` (tệp quá `UPLOAD_MAX_BYTES`) hay `files` (cả bộ quá `SET_BYTES_MAX`).
    """
    settings = get_drawings_settings()
    for index, declared in enumerate(body.files):
        declared_kind(declared.file_name, declared.mime_type)
        if declared.size_bytes > settings.upload_max_bytes:
            raise VALIDATION.error(field=f"files.{index}.sizeBytes")
    if sum(declared.size_bytes for declared in body.files) > SET_BYTES_MAX:
        raise VALIDATION.error(field="files")
    drawing_set = DrawingSetRow(
        id=new_id("dws", clock), project_id=project_id, name=body.name, status="receiving", created_by=principal.user_id
    )
    db.add(drawing_set)
    await db.flush()
    db.add_all(
        DrawingSetFileRow(
            id=new_id("dwf", clock),
            set_id=drawing_set.id,
            ord=index,
            file_name=declared.file_name,
            declared_size_bytes=declared.size_bytes,
            declared_type=declared.mime_type,
            chunk_count=-(-declared.size_bytes // settings.upload_chunk_bytes),
            status="receiving",
        )
        for index, declared in enumerate(body.files)
    )
    await db.flush()
    await record_activity(
        db,
        actor_id=principal.user_id,
        kind=ActivityKind.DRAWING_SET_CREATE,
        object_code=drawing_set.id,
        object_label=body.name or body.files[0].file_name,
        clock=clock,
        project_id=project_id,
    )
    await db.refresh(drawing_set)
    return await set_wire(db, storage, drawing_set)


async def _load_file(db: AsyncSession, *, project_id: str, set_id: str, file_id: str) -> tuple[str, _FileFacts]:
    """`(trạng thái bộ, tệp)` của **đúng** dự án và bộ trên đường; bộ vắng/đã xoá → 404 bộ, tệp lạ → 404 tệp."""
    row = (
        await db.execute(
            select(DrawingSetRow.status, DrawingSetFileRow.id)
            .outerjoin(
                DrawingSetFileRow, (DrawingSetFileRow.set_id == DrawingSetRow.id) & (DrawingSetFileRow.id == file_id)
            )
            .where(
                DrawingSetRow.id == set_id, DrawingSetRow.project_id == project_id, DrawingSetRow.deleted_at.is_(None)
            )
        )
    ).first()
    if row is None:
        raise set_not_found()
    if row.id is None:
        raise file_not_found()
    file = (await db.execute(select(DrawingSetFileRow).where(DrawingSetFileRow.id == file_id))).scalar_one()
    return row.status, _FileFacts(file.id, file.file_name, file.declared_size_bytes, file.chunk_count)


async def _save_chunk(
    db: AsyncSession, *, set_id: str, file_id: str, index: int, size: int, sha256: str, key: str
) -> None:
    """Giao dịch ngắn của N55: khoá **chia sẻ** dòng bộ (khúc song song không chặn nhau, N56 chặn chúng), còn
    `receiving` thì upsert khúc — gửi lại cùng chỉ số là ghi đè (U10)."""
    status = (
        await db.execute(select(DrawingSetRow.status).where(DrawingSetRow.id == set_id).with_for_update(read=True))
    ).scalar_one_or_none()
    if status is None:
        raise set_not_found()
    if status != "receiving":
        raise DRAWING_SET_NOT_RECEIVING.error()
    stmt = pg_insert(DrawingSetFileChunkRow).values(
        file_id=file_id, chunk_index=index, size_bytes=size, sha256=sha256, object_key=key
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[DrawingSetFileChunkRow.file_id, DrawingSetFileChunkRow.chunk_index],
            set_={"size_bytes": size, "sha256": sha256, "object_key": key, "updated_at": func.now()},
        )
    )


async def upload_chunk(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, set_id: str, file_id: str, body: SetChunkBody
) -> DrawingSetFileOut:
    """N55 — nhận một khúc: kiểm dưới giao dịch, ghi kho **ngoài** giao dịch, rồi ghi dòng (như #6).

    Lỗi: 404 bộ/tệp, 409 `DRAWING_SET_NOT_RECEIVING`, 422 `field:"chunkIndex"`/`"chunk"`.
    """
    status, facts = await _load_file(db, project_id=project_id, set_id=set_id, file_id=file_id)
    if status != "receiving":
        raise DRAWING_SET_NOT_RECEIVING.error()
    if body.chunk_index >= facts.chunk_count:
        raise VALIDATION.error(field="chunkIndex")
    chunk_bytes = get_drawings_settings().upload_chunk_bytes

    await db.rollback()
    data = _decode_chunk(body.chunk, index=body.chunk_index, chunk_count=facts.chunk_count, chunk_bytes=chunk_bytes)
    sha256 = await asyncio.to_thread(_digest, data)
    key = drawing_set_chunk(project_id, set_id, file_id, body.chunk_index, sha256)
    await storage.put(key, data, content_type=OCTET_STREAM, max_bytes=chunk_bytes)

    await _save_chunk(
        db, set_id=set_id, file_id=file_id, index=body.chunk_index, size=len(data), sha256=sha256, key=key
    )
    file = (await db.execute(select(DrawingSetFileRow).where(DrawingSetFileRow.id == file_id))).scalar_one()
    return DrawingSetFileOut.model_validate(
        {
            "id": file.id,
            "file_name": file.file_name,
            "size_bytes": file.declared_size_bytes,
            "chunk_count": file.chunk_count,
            "status": file.status,
        }
    )


async def _receiving_files(db: AsyncSession, set_id: str) -> tuple[list[_FileFacts], dict[str, list[_Chunk]]]:
    """Tệp `receiving` của bộ theo thứ tự khai và mọi khúc của chúng theo chỉ số — hai câu SQL (không N+1)."""
    files = [
        _FileFacts(row.id, row.file_name, row.declared_size_bytes, row.chunk_count)
        for row in (
            await db.execute(
                select(DrawingSetFileRow)
                .where(DrawingSetFileRow.set_id == set_id, DrawingSetFileRow.status == "receiving")
                .order_by(DrawingSetFileRow.ord)
            )
        ).scalars()
    ]
    chunks: dict[str, list[_Chunk]] = defaultdict(list)
    stmt = (
        select(
            DrawingSetFileChunkRow.file_id,
            DrawingSetFileChunkRow.chunk_index,
            DrawingSetFileChunkRow.size_bytes,
            DrawingSetFileChunkRow.object_key,
        )
        .where(DrawingSetFileChunkRow.file_id.in_([file.id for file in files]))
        .order_by(DrawingSetFileChunkRow.file_id, DrawingSetFileChunkRow.chunk_index)
    )
    for file_id, index, size, key in (await db.execute(stmt)).all():
        chunks[file_id].append(_Chunk(index, size, key))
    return files, chunks


def _check_chunks(facts: _FileFacts, chunks: Sequence[_Chunk]) -> None:
    """Đủ khúc (U10 → `UPLOAD_INCOMPLETE`) và tổng byte đúng khai (U11 → `UPLOAD_SIZE_MISMATCH`)."""
    if set(range(facts.chunk_count)) - {chunk.index for chunk in chunks}:
        raise UPLOAD_INCOMPLETE.error()
    if sum(chunk.size_bytes for chunk in chunks) != facts.declared_size_bytes:
        raise UPLOAD_SIZE_MISMATCH.error()


async def _build(
    storage: ObjectStorage, project_id: str, set_id: str, facts: _FileFacts, chunks: Sequence[_Chunk]
) -> _Built:
    """Nhận diện (U08, U12 lần hai), nối khúc thành `original.*` trên luồng (băm + dò dấu kết thúc U07), đếm trang PDF.

    Chỉ PDF nằm trọn trong RAM (PDFium nhận `bytes`; trần = `UPLOAD_MAX_BYTES`, như #7). Lỗi tệp → `AppError` 4xx
    (người gọi biến thành `rejected`); lỗi kho 5xx ném tiếp.
    """
    _check_chunks(facts, chunks)
    sniffed = sniff_kind(await _read_head(storage, chunks[0].object_key))
    if sniffed == "dwg":
        raise CAD_NOT_SUPPORTED.error()
    if sniffed != facts.expected_kind:
        raise FILE_TYPE_MISMATCH.error()
    marker = _EndMarker(sniffed)
    buffer = bytearray() if sniffed == "pdf" else None
    digest = hashlib.sha256()
    key = drawing_set_original(project_id, set_id, facts.id, file_extension(facts.file_name))
    await storage.put(
        key,
        _concat(storage, chunks, marker, buffer, digest.update),
        content_type=KIND_MIME[sniffed],
        max_bytes=facts.declared_size_bytes,
    )
    if not marker.complete:
        raise FILE_CORRUPT.error()
    pages = 1 if buffer is None else await asyncio.to_thread(_page_count, bytes(buffer))
    return _Built(sniffed=sniffed, sha256=digest.hexdigest(), original_key=key, page_count=pages)


async def _build_all(
    storage: ObjectStorage,
    project_id: str,
    set_id: str,
    files: Sequence[_FileFacts],
    chunks: Mapping[str, list[_Chunk]],
) -> dict[str, _Built | AppError]:
    """Từng tệp tuần tự (trần RAM = một PDF); tệp hỏng → `AppError` 4xx của nó, lỗi 5xx ném tiếp (R-16)."""
    results: dict[str, _Built | AppError] = {}
    for facts in files:
        try:
            results[facts.id] = await _build(storage, project_id, set_id, facts, chunks.get(facts.id, []))
        except AppError as exc:
            if exc.code.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                raise
            await storage.delete(drawing_set_original(project_id, set_id, facts.id, file_extension(facts.file_name)))
            results[facts.id] = exc
    return results


def _check_page_limits(results: Mapping[str, _Built | AppError], known_pages: int) -> None:
    """Một PDF quá `PDF_PAGES_MAX` hay cả bộ quá `SET_PAGES_MAX` → 422 `DRAWING_SET_TOO_MANY_PAGES`."""
    pages = [result.page_count for result in results.values() if isinstance(result, _Built)]
    if any(count > PDF_PAGES_MAX for count in pages) or known_pages + sum(pages) > SET_PAGES_MAX:
        raise DRAWING_SET_TOO_MANY_PAGES.error()


async def _apply_results(
    db: AsyncSession, set_id: str, results: Mapping[str, _Built | AppError], chunks: Mapping[str, list[_Chunk]]
) -> None:
    """Ghi kết quả từng tệp dưới khoá dòng bộ; khúc đổi giữa lúc kiểm và lúc nối → 409 `UPLOAD_CHUNKS_CHANGED`."""
    _, fresh = await _receiving_files(db, set_id)
    files = (await db.execute(select(DrawingSetFileRow).where(DrawingSetFileRow.id.in_(list(results))))).scalars().all()
    for file in files:
        if [chunk.object_key for chunk in fresh.get(file.id, [])] != [
            chunk.object_key for chunk in chunks.get(file.id, [])
        ]:
            raise UPLOAD_CHUNKS_CHANGED.error()
        result = results[file.id]
        if isinstance(result, AppError):
            file.status, file.rejected_code = "rejected", result.code.code
            continue
        file.status, file.sniffed_kind, file.content_sha256 = "complete", result.sniffed, result.sha256
        file.original_key, file.page_count = result.original_key, result.page_count
    await db.flush()


async def complete_set(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, set_id: str, principal: Principal, clock: Clock
) -> DrawingSetOut:
    """N56 — chốt bộ: kiểm từng tệp, bộ sang `analyzing` và xếp `drawing_set.analyze` **sau commit** (K17).

    Bộ không còn `receiving` (gọi lại, hai N56 song song — C10/C14) → `DrawingSet` hiện tại, không xếp tác vụ lần hai.
    Mọi tệp hỏng → bộ `failed` `DRAWING_SET_ALL_FILES_REJECTED`. Lỗi: 404, 422 `DRAWING_SET_TOO_MANY_PAGES` (bộ giữ
    `receiving`), 409 `UPLOAD_CHUNKS_CHANGED`, 5xx của kho.
    """
    drawing_set = await load_set(db, project_id=project_id, set_id=set_id)
    if drawing_set.status != "receiving":
        return await set_wire(db, storage, drawing_set)
    files, chunks = await _receiving_files(db, set_id)
    known_pages = (
        await db.execute(
            select(func.coalesce(func.sum(DrawingSetFileRow.page_count), 0)).where(DrawingSetFileRow.set_id == set_id)
        )
    ).scalar_one()

    await db.rollback()
    results = await _build_all(storage, project_id, set_id, files, chunks)
    _check_page_limits(results, int(known_pages or 0))

    drawing_set = await load_set(db, project_id=project_id, set_id=set_id, for_update=True)
    if drawing_set.status != "receiving":
        return await set_wire(db, storage, drawing_set)
    await _apply_results(db, set_id, results, chunks)
    accepted = (
        await db.execute(
            select(func.count()).where(DrawingSetFileRow.set_id == set_id, DrawingSetFileRow.status == "complete")
        )
    ).scalar_one()
    values: dict[str, object] = {"status": "failed", "error_code": ALL_FILES_REJECTED}
    if accepted:
        # `now()` của Postgres: cùng đồng hồ với `expire_stalled` khi so hạn treo.
        values = {"status": "analyzing", "progress_percent": 0, "analysis_started_at": func.now()}
        send_analyze_after_commit(db, set_id)
    await db.execute(update(DrawingSetRow).where(DrawingSetRow.id == set_id).values(**values))
    await record_activity(
        db,
        actor_id=principal.user_id,
        kind=ActivityKind.DRAWING_SET_SUBMIT,
        object_code=set_id,
        object_label=drawing_set.name or set_id,
        clock=clock,
        project_id=project_id,
    )
    await db.flush()
    await db.refresh(drawing_set)
    return await set_wire(db, storage, drawing_set)
