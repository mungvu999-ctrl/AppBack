"""Route của lượt tải bản vẽ: #5 init, #6 khúc, #7 complete, #8 progress (B2-04 [2], [7]).

Router **mỏng** như `apps/api/floors/router.py`: quyền và hạn mức nằm ở `dependencies=`
(quyền **trước** hạn mức, mẫu `apps/api/project_members/router.py`), nghiệp vụ ở
`uploads.py`/`complete.py`. Ba route ghi đòi `floor.upload` (viewer → 403), #8 chỉ đòi là
thành viên. `project_id` lấy từ **đường**: `require_project` đã chốt người gọi là thành
viên của đúng dự án đó, còn id trong thân chỉ để guard W21 so (K09).

`route_options`: #6 mang một khúc 5 MiB base64 nên trần thân 8 MiB, và trần > 1 MiB bắt
buộc `idempotency="off"` lúc nạp module; #5 và #7 giữ `"auto"` vì chúng có tác dụng ngoài
giao dịch (nhật ký, hàng đợi) mà FE có thể gửi lại.
"""

from typing import Final

from fastapi import Depends
from starlette.requests import Request
from starlette.responses import Response

from apps.api.core.deps import ClockDep, CurrentPrincipal, DbSession, Storage
from apps.api.core.ratelimit import key_user, rate_limit
from apps.api.core.routing import protected_router, route_options
from apps.api.drawings import complete, scan_results, thumbnails, uploads
from apps.api.drawings.latest import router as latest_router
from apps.api.drawings.progress import progress_wire
from apps.api.drawings.run_history import router as run_history_router
from apps.api.drawings.schemas import CompleteUploadBody, InitUploadBody, ProgressOut, UploadChunkBody
from apps.api.drawings.settings import get_drawings_settings
from apps.api.projects.access import require_project

router = protected_router(tags=["drawings"])
ROUTERS: Final = (router, latest_router, run_history_router)
"""N7 (`latest.py`) và N7b (`run_history.py`) có router riêng; `discover_routers()` chỉ đọc bộ này."""

CHUNK_BODY_LIMIT: Final = 8 * 1024 * 1024
"""Trần thân của #6 (BE-00 §11): 5 MiB nhị phân nở ~6,7 MiB khi base64, cộng bao JSON."""

_upload_gate: Final = Depends(require_project("floor.upload"))
_member_gate: Final = Depends(require_project())

_init_limit: Final = rate_limit(
    "drawings_init",
    limit=lambda: get_drawings_settings().drawings_init_rate_limit,
    window_s=lambda: get_drawings_settings().drawings_init_rate_window_s,
    key=key_user,
    store="cache",
    on_error="open",
)
"""Hạn mức #5 ([6]): `store="cache"`, `on_error="open"` — Redis chết không chặn người tải."""


@router.post(
    "/projects/{project_id}/floors/{floor_id}/drawings/uploads",
    dependencies=[_upload_gate, Depends(_init_limit)],
)
@route_options(idempotency="auto")
async def drawings_init_upload(
    project_id: str,
    floor_id: str,
    body: InitUploadBody,
    db: DbSession,
    principal: CurrentPrincipal,
    clock: ClockDep,
) -> ProgressOut:
    """#5 — mở lượt tải; lượt init giống hệt trong cửa sổ khử trùng trả lại lượt cũ."""
    wire = await uploads.init_upload(
        db, project_id=project_id, level_id=floor_id, body=body, principal=principal, clock=clock
    )
    return ProgressOut.model_validate(wire)


@router.post("/projects/{project_id}/drawings/uploads/{upload_id}/chunks", dependencies=[_upload_gate])
@route_options(body_limit=CHUNK_BODY_LIMIT, idempotency="off")
async def drawings_upload_chunk(
    project_id: str, upload_id: str, body: UploadChunkBody, db: DbSession, storage: Storage
) -> ProgressOut:
    """#6 — nhận một khúc; gửi lại cùng chỉ số là ghi đè (U10)."""
    wire = await uploads.upload_chunk(db, storage, project_id=project_id, upload_id=upload_id, body=body)
    return ProgressOut.model_validate(wire)


@router.post(
    "/projects/{project_id}/drawings/uploads/{upload_id}/complete",
    dependencies=[_upload_gate],
    response_model=ProgressOut,
)
@route_options(idempotency="auto")
async def drawings_complete_upload(
    project_id: str,
    upload_id: str,
    body: CompleteUploadBody,
    db: DbSession,
    storage: Storage,
    principal: CurrentPrincipal,
    clock: ClockDep,
) -> ProgressOut | Response:
    """#7 — chốt lượt tải và mở lượt chạy; tệp hỏng trả 422 W7 **và** giữ dòng `rejected`."""
    result = await complete.complete_upload(
        db, storage, project_id=project_id, upload_id=upload_id, principal=principal, clock=clock
    )
    return result if isinstance(result, Response) else ProgressOut.model_validate(result)


@router.get("/projects/{project_id}/drawings/uploads/{upload_id}/progress", dependencies=[_member_gate])
async def drawings_read_progress(project_id: str, upload_id: str, db: DbSession) -> ProgressOut:
    """#8 — `Progress` hiện tại của một lượt tải, mọi trạng thái (BE-BIND §4)."""
    facts = await uploads.load_upload(db, project_id=project_id, upload_id=upload_id)
    return ProgressOut.model_validate(await progress_wire(db, facts.id))


@router.get("/projects/{project_id}/drawings/uploads/{upload_id}/scan-results", dependencies=[_member_gate])
async def drawings_read_scan_results(
    project_id: str, upload_id: str, db: DbSession, storage: Storage
) -> scan_results.ScanResultsOut:
    """Kết quả mô hình đã có của lượt chạy mới nhất, cho hiệu ứng vẽ trên màn Xử lý."""
    facts = await uploads.load_upload(db, project_id=project_id, upload_id=upload_id)
    return await scan_results.read_scan_results(db, storage, facts)


@router.post("/projects/{project_id}/drawings/page-thumbnails", dependencies=[_upload_gate])
@route_options(body_limit=thumbnails.THUMBNAIL_BODY_LIMIT, idempotency="off")
async def drawings_page_thumbnails(request: Request) -> thumbnails.PageThumbnailsOut:
    """N41 — thân là byte PDF thô; trả ảnh nhỏ từng trang cho ô chọn trang, không ghi gì (UX41 U05).

    Cùng khoá `floor.upload` với #5: chỉ người được tải lên mới cần chọn trang.
    """
    return await thumbnails.page_thumbnails(await request.body())
