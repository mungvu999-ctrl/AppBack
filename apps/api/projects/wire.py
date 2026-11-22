"""Model **response** của module dự án và kiểu tổng hợp `ProjectRollup` (B2-01 [2]).

Mọi lớp ở đây soi gương `src/api/schemas/index.ts` ở commit ghim của AppFront và phải
`.strict()` đúng nó (K01): thừa một trường là zod của FE ném cả response. Hai hệ quả hay
quên:

- **không có** `currentVersion`, `progress`, `deletedAt` trên dây — FE khai `optional`
  nhưng v1 không sinh chúng, gửi ra là mở rộng hợp đồng ngầm;
- trường tuỳ chọn **vắng**, không `null` (W2, K02): `WireModel` tự bỏ `None`.

`ProjectRollup` ở đây (chứ không ở `summaries.py`) để `summaries.project_rollups` — nơi
tính — và `service.py` — nơi dựng dây — cùng nhập một kiểu mà không tạo vòng nhập.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, cast

from pydantic import Field

from apps.api.core.wire import WireDatetime, WireModel
from apps.api.me.avatar import avatar_urls as sign_avatar_urls
from packages.db.models.auth import User
from packages.db.models.projects import Project
from packages.domain.permissions import Role
from packages.storage.port import ObjectStorage

type MemberRole = Literal["engineer", "viewer"]
"""Vai trong một dự án (U24): sửa hoặc xem."""
type ProjectStatus = Literal["draft", "processing", "approved", "error"]
"""`ProjectSchema.status` của #23-#27 (`wireProjectStatusSchema`) — `legacy_status` của rollup."""
type SummaryStatus = Literal["processing", "qc", "done"]
"""`ProjectSummarySchema.status` của N1 (HOP-DONG-MOI §2 N1)."""

_AREA_QUANTUM = Decimal("0.01")
"""N1 gửi `areaM2` làm tròn 2 chữ số; `numeric(12,2)` đã đúng, quantize là chốt chặn."""


class UserOut(WireModel):
    """`UserSchema`: vai **hệ thống** của người dùng, không phải vai trong dự án.

    `avatarUrl` ký qua `apps.api.me.avatar.avatar_url` (B1-04, NO-135); vắng khi người dùng
    chưa có `avatar_key` hoặc lúc gọi không có kho (test gọi thẳng service, không qua app).
    """

    id: str
    email: str
    name: str
    role: Role
    avatar_url: str | None = None
    # U24: vai trong dự án — chỉ ở danh sách thành viên và khi đã đặt; vắng = theo vai hệ thống.
    project_role: MemberRole | None = None


class DrawingOut(WireModel):
    """`DrawingSchema` — chỉ khai ở đây, B2-04 là nơi dựng (cổng `floor.drawings`)."""

    id: str
    name: str
    url: str
    width_mm: int
    height_mm: int
    scale: float | None = None
    uploaded_at: WireDatetime
    uploader_id: str
    content_sha256: str | None = None
    """SHA-256 hex của tệp gốc (UX41 P01); vắng khoá với lượt tải trước khi có cột."""


class FloorOut(WireModel):
    """`FloorSchema` — chỉ khai ở đây, B2-03 là nơi dựng (cổng `project.floors`).

    `hiddenIn3d` (FE3D-B3) luôn có trên dây; mặc định `false` chỉ cho chỗ dựng tay (test, fake).
    `isSection` (K-31, HS-03) chỉ có khi `true` (dòng là mặt cắt), vắng khi không (W2): FE cũ strict vẫn đọc được
    mọi tầng thường; FE phải khai khoá tuỳ chọn này trước khi BE lên `main`.
    """

    id: str
    name: str
    order: int
    elevation_mm: int
    height_mm: int
    area_m2: float | None = None
    drawings: list[DrawingOut]
    hidden_in_3d: bool = Field(default=False, alias="hiddenIn3d")
    is_section: bool | None = None


class ProjectOut(WireModel):
    """`ProjectSchema` của #23-#27; `status` là `legacy_status` của rollup."""

    id: str
    name: str
    code: str | None = None
    address: str | None = None
    created_at: WireDatetime
    updated_at: WireDatetime
    status: ProjectStatus
    floors: list[FloorOut]
    members: list[UserOut]
    owner_id: str | None = None
    """`projects.created_by` — người tạo là chủ sở hữu; FE so với `members[].id` (U49)."""


class SummaryMemberOut(WireModel):
    """`ProjectSummarySchema.members[]`: N1 chỉ cần tên để dựng chữ cái đầu, không lộ email."""

    id: str
    name: str


class ProjectSummaryOut(WireModel):
    """`ProjectSummarySchema` của N1 (HOP-DONG-MOI §2 N1).

    `areaM2` là **số** JSON (`z.number()`), nên `Decimal` phải đổi sang `float` ở biên chứ
    không để Pydantic in ra chuỗi. `defaultFloorId` vắng khi dự án chưa có tầng nào.
    """

    id: str
    name: str
    floor_count: Annotated[int, Field(ge=0)]
    area_m2: Annotated[float, Field(ge=0)]
    status: SummaryStatus
    walls_reviewed_count: Annotated[int, Field(ge=0)]
    walls_total_count: Annotated[int, Field(ge=0)]
    updated_at: WireDatetime
    members: list[SummaryMemberOut]
    default_floor_id: str | None = None
    has_scale: bool | None = None
    job_active: bool | None = None


@dataclass(frozen=True, slots=True)
class ProjectRollup:
    """Số đếm của **một** dự án, gộp từ `project_floor_summaries` (bỏ dòng `hidden`).

    Bất biến: `walls_reviewed <= walls_total`, `area_m2 >= 0`, `default_floor_id` là `None`
    khi và chỉ khi `floor_count == 0`. `status` và `legacy_status` là hai luật suy khác
    nhau trên cùng bộ số (B2-01 [6]), nên giữ cả hai chứ không đổi qua lại lúc đọc.
    """

    floor_count: int
    area_m2: Decimal
    walls_total: int
    walls_reviewed: int
    status: SummaryStatus
    legacy_status: ProjectStatus
    default_floor_id: str | None
    has_scale: bool = True
    """`False` khi một tầng đã chạy pipeline chưa có tỉ lệ `human`/`pipeline` (U07)."""
    job_active: bool = False
    """Có tầng đang có lượt pipeline `pending|running` (U10)."""


async def user_outs(
    rows: Sequence[User], storage: ObjectStorage | None, project_roles: Sequence[str | None] | None = None
) -> list[UserOut]:
    """Nhiều dòng `users` → `UserSchema`, `avatarUrl` ký cả lô một lần qua kho khi có (NO-135, NO-207).

    `project_roles` (cùng thứ tự `rows`) điền `projectRole` cho danh sách thành viên dự án (U24).

    `storage=None` (test gọi thẳng service, không qua app) → `avatarUrl` vắng, không ném lỗi:
    chữ ký URL không phải luật nghiệp vụ, chỉ là trình bày.
    """
    keys = [row.avatar_key for row in rows]
    avatars = await sign_avatar_urls(storage, keys) if storage is not None else [None] * len(rows)
    roles = project_roles if project_roles is not None else [None] * len(rows)
    return [
        UserOut(
            id=row.id,
            email=row.email,
            name=row.name,
            role=cast("Role", row.role),
            avatar_url=avatar,
            project_role=cast("MemberRole | None", project_role),
        )
        for row, avatar, project_role in zip(rows, avatars, roles, strict=True)
    ]


async def user_out(row: User, storage: ObjectStorage | None, project_role: str | None = None) -> UserOut:
    """Một dòng `users` → `UserSchema`; cùng đường ký với `user_outs`."""
    return (await user_outs([row], storage, [project_role]))[0]


def summary_member_out(row: User) -> SummaryMemberOut:
    """Một dòng `users` → `members[]` của N1."""
    return SummaryMemberOut(id=row.id, name=row.name)


def project_summary_out(project: Project, rollup: ProjectRollup, members: Sequence[User]) -> ProjectSummaryOut:
    """Dự án + rollup + thành viên → một mục của `CursorPage` N1.

    `members` đã được người gọi tải theo lô và sắp sẵn (`user_id ASC`, bỏ người xoá mềm) —
    hàm này không truy vấn, để N1 giữ đúng một lượt đọc thành viên cho cả trang (R-20).
    """
    return ProjectSummaryOut(
        id=project.id,
        name=project.name,
        floor_count=rollup.floor_count,
        area_m2=float(rollup.area_m2.quantize(_AREA_QUANTUM, rounding=ROUND_HALF_UP)),
        status=rollup.status,
        walls_reviewed_count=rollup.walls_reviewed,
        walls_total_count=rollup.walls_total,
        updated_at=project.updated_at,
        members=[summary_member_out(row) for row in members],
        default_floor_id=rollup.default_floor_id,
        has_scale=rollup.has_scale,
        job_active=rollup.job_active,
    )
