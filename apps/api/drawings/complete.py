"""#7 — chốt một lượt tải: nối khúc thành `original.*`, nhận diện tệp, mở lượt chạy.

Bảy bước của [6] chia làm ba chặng, và ranh giới giữa chúng là chỗ dễ sai nhất:

1. **Dưới giao dịch của request**: tra lượt tải, đếm khúc thiếu, so tổng kích thước.
2. **Ngoài giao dịch** (`await db.rollback()`, K36): một tệp 100 MiB đi qua MinIO mất hàng
   giây, không được giữ kết nối Postgres trong lúc ấy. Chặng này chỉ đụng kho object.
3. **Giao dịch cuối**: khoá tầng → khoá lượt tải, kiểm lại khúc, `complete`, `start_run`.

Tệp hỏng ở chặng 2 → hàm **trả** `Response` 422 chứ không ném: `AppRoute` rollback khi
handler ném, mà dòng `rejected` phải được giữ lại (BE-00 §7, [6] #7 bước 6).
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from apps.api.access.activity import record_activity
from apps.api.access.kinds import ActivityKind
from apps.api.core.auth import Principal
from apps.api.core.errors import error_response, request_id
from apps.api.drawings.errors import UPLOAD_CHUNKS_CHANGED
from apps.api.drawings.progress import progress_wire
from apps.api.drawings.runs import start_run
from apps.api.drawings.uploads import KIND_MIME, UploadFacts, load_upload
from apps.api.floors.lookup import get_floor
from packages.core.clock import Clock
from packages.core.error_codes import (
    CAD_NOT_SUPPORTED,
    FILE_CORRUPT,
    FILE_TYPE_MISMATCH,
    NOT_FOUND,
    UPLOAD_INCOMPLETE,
    UPLOAD_SIZE_MISMATCH,
    VALIDATION,
)
from packages.core.errors import AppError
from packages.db.models.drawings import UploadChunkRow, UploadRow
from packages.storage.port import ObjectStorage
from packages.vision.preprocess import VisionError, pdf_page_count, sniff_kind
from packages.vision.preprocess.pdf_vector import pick_plan_page

_log: Final = logging.getLogger(__name__)

SNIFF_BYTES: Final = 1024
"""`sniff_kind` chỉ đọc ngần này byte đầu (B2-05a) — đủ cho PNG/JPEG/DWG/PDF."""

PDF_PAGE_MAX: Final = 20
"""Trần số trang của một PDF bản vẽ (W14); quá trần → 422 `VALIDATION` **không** `field`."""

PDF_TAIL_BYTES: Final = 1024
"""`%%EOF` của PDF nằm trong 1 KiB cuối (case U07)."""

_END_MARKERS: Final = {"png": b"IEND", "jpeg": b"\xff\xd9", "pdf": b"%%EOF"}
_JPEG_SKIP: Final = 2
"""`FF D9` chỉ tính là dấu kết thúc khi nằm **sau** hai byte SOI (case U07)."""


@dataclass(frozen=True, slots=True)
class _Chunk:
    """Một dòng `upload_chunks` đã chốt ở bước 2 — bản chụp để so lại ở bước 7."""

    index: int
    size_bytes: int
    object_key: str


class _EndMarker:
    """Dò dấu kết thúc tệp **trên luồng** để bắt tệp cụt (U07) mà không nạp cả tệp vào RAM.

    PNG và JPEG: tìm dấu ở bất kỳ đâu, giữ lại `len(dấu) - 1` byte cuối mỗi lượt để dấu
    nằm vắt qua hai khúc vẫn thấy. PDF: `%%EOF` phải ở 1 KiB cuối nên chỉ giữ đuôi.
    Chỉ ba loại ấy tới được đây: bước 3 đã đổi mọi loại khác thành `FILE_TYPE_MISMATCH`.
    Mọi dấu dài ≥ 2 byte nên `len(dấu) - 1 ≥ 1` (`data[-0:]` sẽ trả cả `data`).
    """

    def __init__(self, kind: str) -> None:
        """Chuẩn bị bộ đệm theo loại tệp; `kind` ngoài ba loại nhận được → `KeyError`."""
        self._marker = _END_MARKERS[kind]
        self._tail_only = kind == "pdf"
        self._skip = _JPEG_SKIP if kind == "jpeg" else 0
        self._buf = b""
        self._start = 0
        self._found = False

    def feed(self, part: bytes) -> None:
        """Nạp một mảnh của luồng; gọi đúng một lần cho mỗi mảnh, đúng thứ tự."""
        if self._found:
            return
        if self._tail_only:
            self._buf = (self._buf + part)[-PDF_TAIL_BYTES:]
            return
        data = self._buf + part
        found_at = data.find(self._marker)
        if found_at >= 0 and self._start + found_at >= self._skip:
            self._found = True
            self._buf = b""
            return
        keep = len(self._marker) - 1
        self._start += max(0, len(data) - keep)
        self._buf = data[-keep:]

    @property
    def complete(self) -> bool:
        """Tệp đã thấy dấu kết thúc của loại nó."""
        return self._found or (self._tail_only and self._marker in self._buf)


async def _chunks_of(db: AsyncSession, facts: UploadFacts) -> list[_Chunk]:
    """Mọi khúc đã nhận, theo chỉ số ([6] #7 bước 2).

    Thiếu chỉ số → 422 `UPLOAD_INCOMPLETE` kèm `count` (U10); đủ khúc nhưng tổng byte khác
    `sizeBytes` khai → 422 `UPLOAD_SIZE_MISMATCH` (U11). Cả hai giữ lượt tải `receiving`:
    FE gửi bù khúc rồi gọi lại #7.
    """
    stmt = (
        select(UploadChunkRow.chunk_index, UploadChunkRow.size_bytes, UploadChunkRow.object_key)
        .where(UploadChunkRow.upload_id == facts.id)
        .order_by(UploadChunkRow.chunk_index)
    )
    chunks = [_Chunk(*row) for row in (await db.execute(stmt)).all()]
    missing = set(range(facts.chunk_count)) - {chunk.index for chunk in chunks}
    if missing:
        raise UPLOAD_INCOMPLETE.error(count=len(missing))
    if sum(chunk.size_bytes for chunk in chunks) != facts.declared_size_bytes:
        raise UPLOAD_SIZE_MISMATCH.error()
    return chunks


async def _read_head(storage: ObjectStorage, key: str) -> bytes:
    """`SNIFF_BYTES` byte đầu của khúc số 0 — đủ cho `sniff_kind`, không đọc cả khúc."""
    head = bytearray()
    async for part in storage.open_read(key):
        head.extend(part)
        if len(head) >= SNIFF_BYTES:
            break
    return bytes(head[:SNIFF_BYTES])


def _check_kind(sniffed: str, facts: UploadFacts) -> None:
    """Magic bytes phải khớp đuôi tệp ([6] #7 bước 3, K14, U08).

    `dwg` → `CAD_NOT_SUPPORTED` dù đuôi là gì (U12 lần hai: người dùng đổi tên `.dwg`
    thành `.png`); mọi lệch khác → `FILE_TYPE_MISMATCH`, kể cả `unknown`.
    """
    if sniffed == "dwg":
        raise CAD_NOT_SUPPORTED.error()
    if sniffed != facts.expected_kind:
        raise FILE_TYPE_MISMATCH.error()


async def _concat(
    storage: ObjectStorage,
    chunks: Sequence[_Chunk],
    marker: _EndMarker,
    buffer: bytearray | None,
    hash_part: Callable[[bytes], None],
) -> AsyncIterator[bytes]:
    """Nối object của các khúc thành **một** luồng, vừa chảy vừa dò dấu kết thúc và băm SHA-256.

    `buffer` chỉ khác `None` cho PDF: `pdf_page_count` nhận `bytes`, không nhận luồng, nên
    đó là lần duy nhất cả tệp nằm trong RAM (trần = `UPLOAD_MAX_BYTES`).
    """
    for chunk in chunks:
        async for part in storage.open_read(chunk.object_key):
            marker.feed(part)
            hash_part(part)
            if buffer is not None:
                buffer.extend(part)
            yield part


def _check_pages(data: bytes, facts: UploadFacts) -> int:
    """Số trang PDF ([6] #7 bước 5): quá trần → `VALIDATION`, `pageIndex` ngoài → có `field`; trả trang sẽ dựng.

    Đồng bộ (PDFium dưới `PDF_LOCK`): người gọi chạy nó trên `asyncio.to_thread`.
    `VisionError` đi tiếp nguyên mã (`PDF_UNREADABLE`, `FILE_CORRUPT`, …); riêng
    `FILE_CORRUPT` do `PdfiumError` thì nguyên nhân gốc chỉ có ở `__cause__` (NO-124) nên
    phải vào nhật ký trước khi thân W7 nuốt mất nó.

    BENCH3D-07: client vắng `pageIndex` và không cắt vùng (`facts.auto_page`) → trang bản vẽ đầu tiên
    (`pick_plan_page`, bỏ trang bìa). Trang client gửi, kể cả 0, luôn giữ nguyên.
    """
    pages = _page_count(data)
    if pages > PDF_PAGE_MAX:
        raise VALIDATION.error()
    if facts.page_index >= pages:
        raise VALIDATION.error(field="pageIndex")
    if not facts.auto_page or pages == 1:
        return facts.page_index
    try:
        picked = pick_plan_page(data, pages)
    except VisionError as exc:
        raise exc.to_app_error() from exc
    if picked != facts.page_index:
        _log.info("upload_page_auto_picked", extra={"upload_id": facts.id, "pages": pages, "picked": picked})
    return picked


def _page_count(data: bytes) -> int:
    """`pdf_page_count` + đổi `VisionError` sang `AppError`, có log NO-124."""
    try:
        return pdf_page_count(data)
    except VisionError as exc:
        if exc.code == "FILE_CORRUPT" and exc.__cause__ is not None:
            _log.warning("pdf_page_count_failed", extra={"cause": repr(exc.__cause__)})
        raise exc.to_app_error() from exc


async def _build_original(storage: ObjectStorage, facts: UploadFacts, chunks: Sequence[_Chunk]) -> tuple[str, str, int]:
    """Bước 3-5: nhận diện, ghi `original.*`, đếm trang; trả `(loại đã nhận diện, sha256 hex, trang sẽ dựng)`.

    SHA-256 của cả tệp gốc (UX41 P01) băm ngay trên luồng ghi, không đọc lại tệp: FE so
    nó với tệp người dùng vừa chọn để báo "giống hệt bản vẽ đang dùng".

    `max_bytes` = kích thước khai, nên khúc bị đánh tráo bằng nội dung dài hơn cũng không
    ghi được quá một tệp (`PAYLOAD_TOO_LARGE` từ chính kho). Lỗi 4xx ở đây là lỗi tệp và
    người gọi biến nó thành một dòng `rejected`; lỗi 5xx của kho (503 `DEPENDENCY_UNAVAILABLE`)
    người gọi ném tiếp, lượt tải giữ `receiving`.
    """
    sniffed = sniff_kind(await _read_head(storage, chunks[0].object_key))
    _check_kind(sniffed, facts)
    marker = _EndMarker(sniffed)
    buffer = bytearray() if sniffed == "pdf" else None
    digest = hashlib.sha256()
    await storage.put(
        facts.original_key,
        _concat(storage, chunks, marker, buffer, digest.update),
        content_type=KIND_MIME[sniffed],
        max_bytes=facts.declared_size_bytes,
    )
    if not marker.complete:
        raise FILE_CORRUPT.error()
    page_index = facts.page_index if buffer is None else await asyncio.to_thread(_check_pages, bytes(buffer), facts)
    return sniffed, digest.hexdigest(), page_index


async def _lock_upload(db: AsyncSession, upload_id: str) -> UploadRow | None:
    """Khoá dòng `uploads` trước khi chốt trạng thái cuối; `None` khi dòng đã biến mất."""
    stmt = select(UploadRow).where(UploadRow.id == upload_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def _reject(db: AsyncSession, storage: ObjectStorage, facts: UploadFacts, exc: AppError) -> Response:
    """Bước 6: xoá tệp gốc dở, ghi `rejected` + mã, **trả** thân W7 422 (không ném).

    Ném ở đây là mất dòng `rejected`: `AppRoute` chỉ commit khi handler trả về (BE-00 §7).
    Lượt tải đã sang trạng thái khác giữa chừng thì giữ nguyên trạng thái ấy.
    """
    await storage.delete(facts.original_key)
    row = await _lock_upload(db, facts.id)
    if row is not None and row.status == "receiving":
        row.status = "rejected"
        row.rejected_code = exc.code.code
        await db.flush()
    return error_response(exc, request_id())


async def _finish(
    db: AsyncSession,
    *,
    facts: UploadFacts,
    chunks: Sequence[_Chunk],
    sniffed: str,
    content_sha256: str,
    page_index: int,
    principal: Principal,
    clock: Clock,
) -> dict[str, object]:
    """Bước 7: khoá tầng → lượt tải, chốt `complete`, mở lượt chạy, ghi nhật ký.

    Lượt tải đã `complete` (hai #7 song song, C14) → 200 `Progress` cũ, không tác dụng
    thứ hai: đúng một lượt chạy và đúng một dòng nhật ký cho một lượt tải. Khoá object của
    khúc khác lúc bước 2 → 409 `UPLOAD_CHUNKS_CHANGED` (FE gửi lại #7). Tầng vừa bị xoá
    mềm → 404.
    """
    floor = await get_floor(db, project_id=facts.project_id, level_id=facts.level_id, for_update=True)
    if floor is None:
        raise NOT_FOUND.error(resource="floor")
    row = await _lock_upload(db, facts.id)
    if row is None:
        raise NOT_FOUND.error(resource="upload")
    if row.status != "receiving":
        return await progress_wire(db, facts.id)
    if [chunk.object_key for chunk in await _chunks_of(db, facts)] != [chunk.object_key for chunk in chunks]:
        raise UPLOAD_CHUNKS_CHANGED.error()

    row.status = "complete"
    row.sniffed_kind = sniffed
    row.content_sha256 = content_sha256
    row.original_key = facts.original_key
    row.page_index = page_index
    await db.flush()
    await start_run(db, upload_id=facts.id, clock=clock)
    await record_activity(
        db,
        actor_id=principal.user_id,
        kind=ActivityKind.FLOOR_UPLOAD_COMPLETE,
        object_code=floor.level_id,
        object_label=floor.name,
        clock=clock,
        project_id=facts.project_id,
    )
    return await progress_wire(db, facts.id)


async def complete_upload(
    db: AsyncSession,
    storage: ObjectStorage,
    *,
    project_id: str,
    upload_id: str,
    principal: Principal,
    clock: Clock,
) -> dict[str, object] | Response:
    """#7 — chốt lượt tải và mở lượt chạy pipeline; `Progress` `pending` khi xong.

    Lượt tải đã `complete`/`rejected` → 200 `Progress` hiện tại, không tác dụng (bước 1),
    nên FE thử lại bao nhiêu lần cũng an toàn. Tệp hỏng → `Response` 422 và dòng
    `rejected` (bước 6). Các lỗi còn lại ném như thường: 404, `UPLOAD_INCOMPLETE`,
    `UPLOAD_SIZE_MISMATCH`, 409 `UPLOAD_CHUNKS_CHANGED`, và 5xx của kho khi dựng `original.*`
    (lượt tải giữ `receiving`, `original.*` dở bị lượt sau ghi đè hoặc job dọn lượt bỏ dở xoá).
    """
    facts = await load_upload(db, project_id=project_id, upload_id=upload_id)
    if facts.status != "receiving":
        return await progress_wire(db, facts.id)
    chunks = await _chunks_of(db, facts)

    await db.rollback()
    try:
        sniffed, content_sha256, page_index = await _build_original(storage, facts, chunks)
    except AppError as exc:
        if exc.code.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise  # kho/phụ thuộc hỏng tạm thời: giữ `receiving` để #7 gửi lại được (NO-419)
        return await _reject(db, storage, facts, exc)
    return await _finish(
        db,
        facts=facts,
        chunks=chunks,
        sniffed=sniffed,
        content_sha256=content_sha256,
        page_index=page_index,
        principal=principal,
        clock=clock,
    )
