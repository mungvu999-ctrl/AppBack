"""Dependency quyền **cấp hệ thống** (BE-00 §5): vai của người gọi, không theo dự án.

Dùng dự kiến: `require_permission("project.create")` cho #25 (B2-01),
`require_permission("user.manage")` cho nhóm `users` (B1-05), `require_admin` cho
`admin/ml` (B6-*). Tài nguyên trong dự án đi qua `require_project` của B2-01 (404 cho
người ngoài trước mọi 403, BE-00 §4, K08), không qua module này.

Vai chỉ lấy từ `current_principal` (ảnh chụp DB/cache của B1-01), không từ claim,
header hay thân request (K05, K34). Mỗi cổng phơi `permission_key` qua
`permission_dependency` để quét route so với `lock` ở `cases.toml`.
"""

from collections.abc import Callable
from functools import cache
from typing import Final

from apps.api.core.auth import Principal
from apps.api.core.deps import CurrentPrincipal
from apps.api.core.permissions import ADMIN, permission_dependency
from packages.core.error_codes import FORBIDDEN
from packages.domain.permissions import ROLES, SYSTEM_SCOPED_KEYS, can

ROLE_KEY_PREFIX: Final = "role:"
"""Khoá của `require_role` khác admin: ngoài từ vựng `lock` ở `cases.toml`, nên route
có dòng hợp đồng dùng nó làm `test_permission_keys_match_bind_rows` hỏng."""

type RoleGate = Callable[[Principal], None]


@cache
def _role_gate(allowed: frozenset[str], key: str) -> RoleGate:
    """Cổng 403 cho vai ngoài `allowed`; nhớ đệm để cùng khoá → cùng dependency.

    Cùng một đối tượng thì `registered()` không ghi trùng và FastAPI gộp được
    dependency trùng trên một route. Trả `None`: test chung C10/C22 ghi đè cổng
    bằng `lambda: None`, handler lấy `Principal` qua `CurrentPrincipal` riêng.
    """

    @permission_dependency(key)
    def gate(principal: CurrentPrincipal) -> None:
        """403 `FORBIDDEN` không `resource` khi vai của `Principal` không được phép."""
        if principal.role not in allowed:
            raise FORBIDDEN.error()

    return gate


def require_role(*roles: str) -> RoleGate:
    """Cổng cho đúng các vai `roles`; không dùng cho tài nguyên trong dự án (K08).

    Không đối số hay vai lạ → `ValueError` lúc khai (fail-closed). Thứ tự vai không
    quan trọng. `require_role("admin")` mang khoá `admin`; tập khác mang
    `role:<vai sắp xếp, nối bằng ,>` — không khớp dòng hợp đồng nào.
    """
    allowed = frozenset(roles)
    if not allowed:
        raise ValueError("require_role cần ít nhất một vai")
    unknown = allowed - set(ROLES)
    if unknown:
        raise ValueError(f"vai lạ: {sorted(unknown)!r}")
    key = ADMIN if allowed == {ADMIN} else ROLE_KEY_PREFIX + ",".join(sorted(allowed))
    return _role_gate(allowed, key)


def require_permission(key: str) -> RoleGate:
    """Cổng theo khoá quyền cấp hệ thống: 403 khi `can(role, key)` sai.

    `key` ngoài `SYSTEM_SCOPED_KEYS` (khoá dự án như `layer.edit`, hay khoá lạ) →
    `ValueError` lúc khai: khoá dự án chỉ kiểm qua `require_project` (K08). Tập vai
    được phép tính một lần từ ma trận lúc khai, không mỗi request.
    """
    if key not in SYSTEM_SCOPED_KEYS:
        raise ValueError(f"khoá không phải cấp hệ thống: {key!r}")
    return _role_gate(frozenset(role for role in ROLES if can(role, key)), key)


require_admin: Final = require_role(ADMIN)
"""Cổng chỉ cho `admin`, khoá `admin`."""
