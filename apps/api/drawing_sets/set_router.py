"""Route "Tải cả bộ hồ sơ" N54-N61 dưới `/api/projects/{project_id}/drawing-sets` (K-32a §3).

Router mỏng như `apps/api/drawings/router.py`: `require_project()` ở `dependencies=` chạy **trước** mọi thứ (người
ngoài 404 trước 403, trước cả công tắc — K08), quyền trước hạn mức. Năm lệnh ghi N54, N55, N56, N59, N60 kiểm công tắc
`DRAWING_SET_ENABLED` trong thân handler (sau mọi dependency): tắt → 503 `DRAWING_SET_DISABLED` (mẫu N42). N57, N58,
N61 luôn chạy — không bao giờ tắt tính năng bằng 404 (L4-N04). Không route cũ nào đổi.

`route_options`: N55 mang một khúc 5 MiB base64 → trần thân 8 MiB, `idempotency="off"`; N59 là ghi có version
(thiếu `baseVersion` → 428 của khung); N54, N56, N60, N61 giữ `"auto"` (tác dụng ngoài giao dịch: nhật ký, hàng đợi).
"""

from typing import Annotated, Final

from fastapi import Depends, Query

from apps.api.core.deps import ClockDep, CurrentPrincipal, DbSession, Storage
from apps.api.core.pagination import CursorPage
from apps.api.core.ratelimit import key_user, rate_limit
from apps.api.core.routing import protected_router, route_options
from apps.api.drawing_sets import building_read, confirm, set_delete, set_read, set_review, set_upload
from apps.api.drawing_sets.building_schemas import BUILDING_DISABLED, SetBuildingOut, set_building_out
from apps.api.drawing_sets.errors import DRAWING_SET_DISABLED
from apps.api.drawing_sets.set_schemas import (
    CompleteSetBody,
    ConfirmSetBody,
    CreateSetBody,
    DrawingSetFileOut,
    DrawingSetOut,
    DrawingSetSummaryOut,
    ReplaceViewsBody,
    SetChunkBody,
)
from apps.api.drawing_sets.settings import DrawingSetSettings, get_drawing_set_settings
from apps.api.projects.access import ProjectAccess, require_project

router = protected_router(tags=["drawing_sets"])

CHUNK_BODY_LIMIT: Final = 8 * 1024 * 1024
"""Trần thân N55 (như #6): 5 MiB nhị phân nở ~6,7 MiB khi base64, cộng bao JSON."""
DISABLED_RETRY_AFTER_S: Final = 10
"""`Retry-After` của 503 khi công tắc tắt (W9: ≤ 10 s; mẫu N42)."""
_BASE: Final = "/projects/{project_id}/drawing-sets"

_upload_gate: Final = Depends(require_project("floor.upload"))
_member_gate: Final = Depends(require_project())
_create_limit: Final = rate_limit(
    "drawing_sets_create",
    limit=lambda: get_drawing_set_settings().drawing_set_create_rate_limit,
    window_s=lambda: get_drawing_set_settings().drawing_set_create_rate_window_s,
    key=key_user,
    store="cache",
    on_error="open",
)
"""Hạn mức N54 (C11): `store="cache"`, `on_error="open"` — Redis chết không chặn người tải (mẫu #5)."""

Settings = Annotated[DrawingSetSettings, Depends(get_drawing_set_settings)]
UploadAccess = Annotated[ProjectAccess, _upload_gate]


def _require_enabled(settings: DrawingSetSettings) -> None:
    """Công tắc tắt → 503 `DRAWING_SET_DISABLED` cho lệnh ghi."""
    if not settings.drawing_set_enabled:
        raise DRAWING_SET_DISABLED.error(retry_after=DISABLED_RETRY_AFTER_S)


@router.post(_BASE, dependencies=[_upload_gate, Depends(_create_limit)], status_code=201)
@route_options(idempotency="auto")
async def drawing_sets_create_set(
    project_id: str,
    body: CreateSetBody,
    db: DbSession,
    storage: Storage,
    principal: CurrentPrincipal,
    clock: ClockDep,
    settings: Settings,
) -> DrawingSetOut:
    """N54 — khai 1-20 tệp, mở bộ `receiving`; 201."""
    _require_enabled(settings)
    return await set_upload.create_set(db, storage, project_id=project_id, body=body, principal=principal, clock=clock)


@router.post(f"{_BASE}/{{set_id}}/files/{{file_id}}/chunks", dependencies=[_upload_gate])
@route_options(body_limit=CHUNK_BODY_LIMIT, idempotency="off")
async def drawing_sets_upload_chunk(
    project_id: str, set_id: str, file_id: str, body: SetChunkBody, db: DbSession, storage: Storage, settings: Settings
) -> DrawingSetFileOut:
    """N55 — nhận một khúc của một tệp; gửi lại cùng chỉ số là ghi đè (U10)."""
    _require_enabled(settings)
    return await set_upload.upload_chunk(db, storage, project_id=project_id, set_id=set_id, file_id=file_id, body=body)


@router.post(f"{_BASE}/{{set_id}}/complete", dependencies=[_upload_gate])
@route_options(idempotency="auto")
async def drawing_sets_complete_set(
    project_id: str,
    set_id: str,
    body: CompleteSetBody,
    db: DbSession,
    storage: Storage,
    principal: CurrentPrincipal,
    clock: ClockDep,
    settings: Settings,
) -> DrawingSetOut:
    """N56 — chốt bộ: kiểm từng tệp, sang `analyzing`, xếp tác vụ phân tích sau commit; gọi lại → bộ hiện tại."""
    _require_enabled(settings)
    return await set_upload.complete_set(
        db, storage, project_id=project_id, set_id=set_id, principal=principal, clock=clock
    )


@router.get(f"{_BASE}/{{set_id}}", dependencies=[_member_gate])
async def drawing_sets_read_set(project_id: str, set_id: str, db: DbSession, storage: Storage) -> DrawingSetOut:
    """N57 — một bộ đầy đủ tệp và hình (FE thăm dò 2 s khi `analyzing`)."""
    return await set_read.read_set(db, storage, project_id=project_id, set_id=set_id)


@router.get(_BASE, dependencies=[_member_gate])
async def drawing_sets_list_sets(
    project_id: str, db: DbSession, cursor: Annotated[str | None, Query()] = None
) -> CursorPage[DrawingSetSummaryOut]:
    """N58 — bộ của dự án, mới trước, trần một trang theo cấu hình (W22)."""
    return await set_read.list_sets(db, project_id=project_id, cursor=cursor)


@router.get(f"{_BASE}/{{set_id}}/building", dependencies=[_member_gate])
async def drawing_sets_read_building(
    project_id: str, set_id: str, db: DbSession, storage: Storage, settings: Settings
) -> SetBuildingOut:
    """NHA-06a — đề xuất ngôi nhà của bộ (tầng, mái, mâu thuẫn, vi phạm) tính ngay lúc đọc, không ghi gì.

    Công tắc tắt → 200 `{"enabled": false}` (mẫu N52: "tắt" là trạng thái thường, service không được gọi). Tách khỏi
    N57 vì N57 được thăm dò mỗi 2 s, còn route này đọc lại kết quả của mọi trang từ kho.
    """
    if not settings.drawing_set_enabled:
        return BUILDING_DISABLED
    return set_building_out(await building_read.read_building(db, storage, project_id=project_id, set_id=set_id))


@router.put(f"{_BASE}/{{set_id}}/views")
@route_options(versioned=True)
async def drawing_sets_replace_views(
    set_id: str,
    body: ReplaceViewsBody,
    access: UploadAccess,
    db: DbSession,
    storage: Storage,
    clock: ClockDep,
    settings: Settings,
) -> DrawingSetOut:
    """N59 — lưu bản duyệt (thay cả danh sách hình); base cũ → 409, lượt lặp của chính mình → 200."""
    _require_enabled(settings)
    return await set_review.replace_views(
        db,
        storage,
        project_id=access.project_id,
        set_id=set_id,
        base_version=body.base_version,
        body=body.body,
        principal=access.principal,
        clock=clock,
    )


@router.post(f"{_BASE}/{{set_id}}/confirm")
@route_options(idempotency="auto")
async def drawing_sets_confirm_set(
    set_id: str,
    body: ConfirmSetBody,
    access: UploadAccess,
    db: DbSession,
    storage: Storage,
    clock: ClockDep,
    settings: Settings,
) -> DrawingSetOut:
    """N60 — "Xác nhận và dựng": tạo tầng + lượt tải, xếp pipeline sau commit; gọi lại → bộ hiện tại."""
    _require_enabled(settings)
    return await confirm.confirm_set(db, storage, access=access, set_id=set_id, revision=body.revision, clock=clock)


@router.delete(f"{_BASE}/{{set_id}}", dependencies=[_upload_gate])
@route_options(idempotency="auto")
async def drawing_sets_delete_set(
    project_id: str, set_id: str, db: DbSession, storage: Storage, principal: CurrentPrincipal, clock: ClockDep
) -> DrawingSetOut:
    """N61 — xoá mềm; tầng đã tạo giữ nguyên."""
    return await set_delete.delete_set(
        db, storage, project_id=project_id, set_id=set_id, principal=principal, clock=clock
    )
