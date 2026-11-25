"""Schema dây của quản trị người dùng (B1-05 [2], `src/api/schemas/users.ts`, hợp đồng #38-#46).

Response dựng từ `WireModel` (vắng khoá khi `None`, riêng `lastActiveAt` là `KeepNull`, K01/K02).
Request kế thừa `WireRequest` (`extra="forbid"`, C03). Email trong `emails` đi qua
`validate_wire_email` **ở mức cả danh sách** để lỗi luôn mang `field:"emails"` chứ không
`emails.0` (K37); bỏ trùng theo `normalize_email`, giữ thứ tự đầu (K20).
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from apps.api.auth.emails import validate_wire_email
from apps.api.core.wire import KeepNull, WireDatetime, WireModel, WireRequest

type UserRole = Literal["admin", "engineer", "viewer"]
type UserStatus = Literal["active", "pending", "disabled"]


class AdminUserOut(WireModel):
    """`AdminUser`: một người nhìn từ màn quản trị; ba trường tuỳ chọn vắng khoá khi không có."""

    avatar_url: str | None = None
    email: str
    id: str
    invited_at: WireDatetime | None = None
    invite_expires_at: WireDatetime | None = None
    last_active_at: KeepNull[WireDatetime | None]
    name: str
    project_count: int
    role: UserRole
    status: UserStatus


class AdminUserListOut(WireModel):
    """#38: `total` là số người chưa xoá mềm, có thể lớn hơn `len(users)` khi chạm trần."""

    total: int
    users: list[AdminUserOut]


class UserMembershipOut(WireModel):
    """#39: một dự án chưa xoá mềm của người này, kèm vai hệ thống hiện tại."""

    project_id: str
    project_name: str
    role: UserRole


class UserActivityOut(WireModel):
    """#40: một dòng `activity_log` có `actor_id` là người này; `id` là chuỗi thập phân."""

    id: str
    at: WireDatetime
    kind: str
    object_code: str
    object_label: str


class EmptyBody(WireRequest):
    """Thân `{}` của #42, #43, #45: không nhận khoá nào."""


class RoleChangeBody(WireRequest):
    """#41: `userId` chỉ để guard W21 so với đường (K09); người thực hiện lấy từ `Principal`."""

    role: UserRole
    user_id: str


class DeleteUserBody(WireRequest):
    """#46: thân có `userId` (W13, guard W21) và email gõ lại để xác nhận."""

    confirm_email: str
    user_id: str

    @field_validator("confirm_email")
    @classmethod
    def _wire_email(cls, value: str) -> str:
        """`confirmEmail` cũng qua `validate_wire_email` (K37): chuỗi ngoài ASCII không được chuẩn hoá thành trùng."""
        return validate_wire_email(value)


class InviteBody(WireRequest):
    """#44: ≥ 1 email đã kiểm; trần lô và bỏ trùng theo `normalize_email` do `service.invite_users` làm."""

    emails: Annotated[list[str], Field(min_length=1)]
    role: UserRole

    @field_validator("emails")
    @classmethod
    def _validate_emails(cls, values: list[str]) -> list[str]:
        """Mỗi email qua `validate_wire_email` (K37); không bỏ trùng ở đây để trần lô đếm số phần tử gốc."""
        return [validate_wire_email(value) for value in values]
