"""Xuất mô hình + liên kết chia sẻ (BE-BIND #47-#49) — quyền kiểm ở máy chủ, không chỉ ở FE.

- `POST /projects/{id}/exports`: `model.export` (admin, engineer); trả đồ thị N15 để FE đóng gói.
- `GET|POST /projects/{id}/share-links`, `DELETE .../{link_id}`: `share.create`. Liên kết MỚI bị giới hạn (L6-N05):
  hạn mặc định +7 ngày, trần +90 ngày, tối đa 50 liên kết đang hiệu lực mỗi dự án, 20 lượt tạo/10 phút mỗi người,
  quyền mới chỉ `view`. Liên kết cũ (`expires_at` NULL, quyền `comment`) giữ nguyên và vẫn mở được.
- `GET /share/{token}`: công khai, chỉ đọc; token lạ/hết hạn/thu hồi → cùng một 404 (không hé lộ).
404 (không phải thành viên) luôn thắng 403 (vai không đủ) nhờ `require_project` (K08).
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal

from fastapi import Depends
from pydantic import Field
from sqlalchemy import func, or_, select
from starlette.requests import Request

from apps.api.core.deps import ClockDep, DbSession
from apps.api.core.origin import reject_foreign_origin
from apps.api.core.ratelimit import key_ip, key_user, rate_limit
from apps.api.core.routing import protected_router, public_router
from apps.api.core.wire import NfcStr, WireDatetime, WireModel, WireRequest
from apps.api.projects.access import ProjectAccess, require_project
from apps.api.share_links.errors import SHARE_LINK_LIMIT_REACHED
from apps.api.spatial_read.graph import spatial_graph
from apps.api.spatial_read.wire import SpatialGraphDocumentOut
from packages.core.error_codes import NOT_FOUND, VALIDATION
from packages.core.ids import new_id
from packages.core.settings import get_core_settings
from packages.db.models.projects import Project
from packages.db.models.share_links import ShareLinkRow

router = protected_router(tags=["share_links"])
public = public_router(tags=["share_links"])
ROUTERS: Final = (router, public)

SHARE_PATH: Final = "/share/"
"""Đường FE mở liên kết; `url` trả về là `<origin><SHARE_PATH><token>`."""

OPEN_RATE_LIMIT: Final = 60
OPEN_RATE_WINDOW_S: Final = 60
_open_limit: Final = rate_limit(
    "share_links_open",
    limit=OPEN_RATE_LIMIT,
    window_s=OPEN_RATE_WINDOW_S,
    key=key_ip,
    store="cache",
    on_error="open",
)
"""Mở liên kết công khai: 60 lượt/phút mỗi IP chặn dò token (C11); Redis hỏng không chặn người xem (chỉ đọc)."""

CREATE_RATE_LIMIT: Final = 20
CREATE_RATE_WINDOW_S: Final = 600
_create_gate: Final = Depends(require_project("share.create"))
_create_limit: Final = rate_limit(
    "share_links_create",
    limit=CREATE_RATE_LIMIT,
    window_s=CREATE_RATE_WINDOW_S,
    key=key_user,
    store="cache",
    on_error="open",
)
"""Tạo liên kết: 20 lượt/10 phút mỗi người (mẫu `_init_limit` của bản vẽ); Redis hỏng không chặn người dùng."""

DEFAULT_EXPIRY: Final = timedelta(days=7)
"""`expiresAt` vắng -> hạn này tính từ lúc tạo."""
MAX_EXPIRY: Final = timedelta(days=90)
"""Trần `expiresAt` của liên kết mới; vượt -> 422 `VALIDATION` `field: "expiresAt"`."""
MAX_ACTIVE_LINKS: Final = 50
"""Số liên kết đang hiệu lực (chưa thu hồi, chưa hết hạn, kể cả liên kết cũ không hạn) tối đa mỗi dự án."""


class EmbedIn(WireRequest):
    """Bốn khoá embed FE gửi kèm (chỉ lưu ý định, không ảnh hưởng quyền)."""

    level: str | None = None
    color: str | None = None
    toolbar: bool = True


class ShareLinkCreateIn(WireRequest):
    """Thân `POST share-links`; mật khẩu chưa hỗ trợ: `password` không rỗng → 422 (không nhận rồi bỏ qua).

    `permission` mới chỉ nhận `view` (`comment` -> 422; liên kết cũ mang `comment` vẫn đọc được).
    Hạn: xem `_resolve_expiry`.
    """

    permission: Literal["view"] = "view"
    expires_at: datetime | None = None
    viewpoint_code: NfcStr | None = Field(default=None, max_length=512)
    label: NfcStr | None = Field(default=None, max_length=200)
    embed: EmbedIn | None = None
    password: Annotated[str | None, Field(max_length=0)] = None


class ShareLinkOut(WireModel):
    """`ShareLinkWire` của FE (`src/lib/export/shareLink.ts`)."""

    id: str
    url: str
    permission: str
    status: Literal["active", "expired", "revoked"]
    created_at: WireDatetime
    expires_at: WireDatetime | None
    revoked_at: WireDatetime | None
    password_protected: bool = False
    viewpoint_code: str | None
    label: str | None


class SharedModelOut(WireModel):
    """Trả lời tuyến công khai: liên kết + đồ thị chỉ đọc."""

    link: ShareLinkOut
    project_name: str
    graph: SpatialGraphDocumentOut


def _status(row: ShareLinkRow, now: datetime) -> Literal["active", "expired", "revoked"]:
    """Phán quyết của máy chủ: thu hồi thắng hết hạn."""
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= now:
        return "expired"
    return "active"


def _out(row: ShareLinkRow, now: datetime) -> ShareLinkOut:
    """Dòng → dây; `url` dựng từ `PUBLIC_BASE_URL`, không từ `Host` (L1-N06)."""
    return ShareLinkOut(
        id=row.id,
        url=f"{get_core_settings().public_base_url}{SHARE_PATH}{row.token}",
        permission=row.permission,
        status=_status(row, now),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        viewpoint_code=row.viewpoint_code,
        label=row.label,
    )


@router.post("/projects/{project_id}/exports")
async def share_links_export(
    access: Annotated[ProjectAccess, Depends(require_project("model.export"))], request: Request, db: DbSession
) -> SpatialGraphDocumentOut:
    """Xuất mô hình: kiểm `model.export` rồi trả đồ thị N15; FE đóng gói tệp ở client."""
    return await spatial_graph(db, access.project_id, app=request.app)


@router.get("/projects/{project_id}/share-links")
async def share_links_list(
    access: Annotated[ProjectAccess, Depends(require_project("share.create"))],
    db: DbSession,
    clock: ClockDep,
) -> list[ShareLinkOut]:
    """#47 — mọi liên kết của dự án, mới trước."""
    rows = await db.scalars(
        select(ShareLinkRow).where(ShareLinkRow.project_id == access.project_id).order_by(ShareLinkRow.id.desc())
    )
    now = clock.now()
    return [_out(row, now) for row in rows]


def _resolve_expiry(requested: datetime | None, now: datetime) -> datetime:
    """Hạn của liên kết mới: vắng -> +7 ngày; quá khứ/hiện tại hoặc quá +90 ngày -> 422 `field: "expiresAt"`."""
    if requested is None:
        return now + DEFAULT_EXPIRY
    expires_at = requested if requested.tzinfo is not None else requested.replace(tzinfo=UTC)
    if expires_at <= now or expires_at > now + MAX_EXPIRY:
        raise VALIDATION.error(field="expiresAt")
    return expires_at


@router.post("/projects/{project_id}/share-links", status_code=201, dependencies=[_create_gate, Depends(_create_limit)])
async def share_links_create(
    body: ShareLinkCreateIn,
    access: Annotated[ProjectAccess, _create_gate],
    db: DbSession,
    clock: ClockDep,
) -> ShareLinkOut:
    """#48 — tạo liên kết với token ngẫu nhiên 256 bit, hạn <= 90 ngày (mặc định 7), <= 50 liên kết hiệu lực."""
    now = clock.now()
    expires_at = _resolve_expiry(body.expires_at, now)
    active = await db.scalar(
        select(func.count())
        .select_from(ShareLinkRow)
        .where(
            ShareLinkRow.project_id == access.project_id,
            ShareLinkRow.revoked_at.is_(None),
            or_(ShareLinkRow.expires_at.is_(None), ShareLinkRow.expires_at > now),
        )
    )
    if (active or 0) >= MAX_ACTIVE_LINKS:
        raise SHARE_LINK_LIMIT_REACHED.error()
    row = ShareLinkRow(
        id=new_id("shl", clock),
        project_id=access.project_id,
        token=secrets.token_urlsafe(32),
        permission=body.permission,
        created_by=access.principal.user_id,
        expires_at=expires_at,
        viewpoint_code=body.viewpoint_code,
        label=body.label,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(row, now)


@router.delete("/projects/{project_id}/share-links/{link_id}")
async def share_links_revoke(
    link_id: str,
    access: Annotated[ProjectAccess, Depends(require_project("share.create"))],
    db: DbSession,
    clock: ClockDep,
) -> ShareLinkOut:
    """#49 — thu hồi (giữ dòng, đặt `revoked_at`); lặp lại vẫn trả `revoked`."""
    row = await db.scalar(
        select(ShareLinkRow).where(ShareLinkRow.id == link_id, ShareLinkRow.project_id == access.project_id)
    )
    if row is None:
        raise NOT_FOUND.error(resource="shareLink")
    if row.revoked_at is None:
        row.revoked_at = clock.now()
        await db.commit()
        await db.refresh(row)
    return _out(row, clock.now())


@public.get("/share/{token}", dependencies=[Depends(reject_foreign_origin), Depends(_open_limit)])
async def share_links_open(token: str, request: Request, db: DbSession, clock: ClockDep) -> SharedModelOut:
    """Công khai, chỉ đọc: token còn hiệu lực và dự án chưa xoá → đồ thị; còn lại 404.

    `Origin` lệch → 403 trước mọi thứ (C24; điều hướng thẳng không gửi `Origin` nên vẫn mở được);
    quá hạn mức theo IP → 429.
    """
    found = (
        await db.execute(
            select(ShareLinkRow, Project.name)
            .join(Project, Project.id == ShareLinkRow.project_id)
            .where(ShareLinkRow.token == token, Project.deleted_at.is_(None))
        )
    ).first()
    now = clock.now()
    if found is None:
        raise NOT_FOUND.error(resource="shareLink")
    status = _status(found[0], now)
    if status != "active":
        raise NOT_FOUND.error(resource="shareLink", reason=status)  # U59: nói đúng lý do, vẫn 404
    row, name = found
    document = await spatial_graph(db, row.project_id, app=request.app)
    # L6-N06: người nhận liên kết không thấy địa chỉ công trình (None -> vắng khoá theo W2). Tầng
    # vẫn trả đủ (lọc theo tầng là phương án A của F2, chưa làm).
    building = document.graph.building.model_copy(update={"address": None})
    graph = document.graph.model_copy(update={"building": building})
    return SharedModelOut(
        link=_out(row, now),
        project_name=name,
        graph=document.model_copy(update={"graph": graph}),
    )
