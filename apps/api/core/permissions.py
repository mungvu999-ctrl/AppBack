"""Sổ dependency quyền (BE-00 §5, §2.2).

Khung **không** biết luật quyền — `require_role`/`require_admin` là của B1-02,
`require_project` của B2-01. Nó chỉ đòi hai thứ ở mỗi dependency quyền:

- phơi `permission_key` để `Operation.permission_key` (cổng case) so được với
  `lock` ở `cases.toml`; quét route hỏng khi lệch;
- có mặt trong `registered()` để test case chung ghi đè được (C10, C22 phải đi qua
  được dependency quyền mới tới bước nhận việc idempotency).
"""

from collections.abc import Callable
from typing import Any, Final

PERMISSION_ATTR: Final = "permission_key"

ANY_MEMBER: Final = "thành viên"
ANY_ROLE: Final = "—"
ADMIN: Final = "admin"

_registered: Final[list[Callable[..., Any]]] = []


def permission_dependency[DependencyT: Callable[..., Any]](key: str) -> Callable[[DependencyT], DependencyT]:
    """Đánh dấu một dependency là cổng quyền mang khoá `key` (`—`, `thành viên`, `admin`, hay tên khoá)."""
    if not key:
        raise ValueError("khoá quyền không được rỗng")

    def decorate(dependency: DependencyT) -> DependencyT:
        """Gắn khoá lên dependency và ghi nó vào sổ."""
        setattr(dependency, PERMISSION_ATTR, key)
        _registered.append(dependency)
        return dependency

    return decorate


def registered() -> tuple[Callable[..., Any], ...]:
    """Mọi dependency quyền đã khai, theo thứ tự khai (test case chung ghi đè hết)."""
    return tuple(_registered)


def permission_key_of(call: object) -> str | None:
    """Khoá quyền của một callable, hay `None` nếu nó không phải cổng quyền."""
    key = getattr(call, PERMISSION_ATTR, None)
    return key if isinstance(key, str) else None
