"""`AppError` và sổ đăng ký mã lỗi (BE-00 §4, W7-W10).

Mọi module khai mã riêng bằng `ERRORS.define(code, status)` ở mức module; luật sai
thì hỏng ngay lúc nhập. Handler của `apps/api` (B0-06) biến `AppError` thành thân
W7. `AppError` không bao giờ mang `message`, `detail` hay stacktrace: chuỗi hiển
thị do FE giữ.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal, cast, get_args

from packages.core.ids import is_id
from packages.core.pipeline import PIPELINE_STEPS

_CODE_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_STATUSES: Final = frozenset({400, 401, 403, 404, 409, 413, 422, 428, 429, 503})
# W10/K30: 401 chỉ dành cho nhóm Phiên của lõi.
_SESSION_CODES: Final = frozenset(
    {"UNAUTHENTICATED", "INVALID_CREDENTIALS", "SESSION_REVOKED", "ACCOUNT_DISABLED", "ORIGIN_MISMATCH"}
)
_RETRY_STATUSES: Final = frozenset({429, 503})
_MAX_RETRY_AFTER: Final = 10  # W9

RESOURCES: Final = frozenset(
    {
        "project",
        "floor",
        "member",
        "version",
        "upload",
        "measurement",
        "template",
        "libraryItem",
        "modelFamily",
        "modelVersion",
        "dataset",
        "lightingBake",
        "modelExport",
        "datasetVersion",
        "trainingJob",
        "notification",
        "user",
        "shareLink",
    }
)
_STEP_IDS: Final = frozenset(step for step, _ in PIPELINE_STEPS)
_FIELD_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]*(\.[A-Za-z0-9]+)*")


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# tên Python → (khoá dây, kiểm giá trị)
_PARAMS: Final[Mapping[str, tuple[str, Callable[[object], bool]]]] = {
    "step": ("step", lambda v: isinstance(v, str) and v in _STEP_IDS),
    "floor": ("floor", _non_empty_str),
    "count": ("count", _non_negative_int),
    "field": ("field", lambda v: isinstance(v, str) and _FIELD_RE.fullmatch(v) is not None),
    "resource": ("resource", lambda v: isinstance(v, str) and v in RESOURCES),
    "file_name": ("fileName", _non_empty_str),
    "layer": ("layer", _non_empty_str),
    "reason": ("reason", lambda v: v in ("revoked", "expired")),  # 404 liên kết chia sẻ (U59)
}


@dataclass(frozen=True, slots=True)
class ErrorCode:
    """Chỉ tạo qua `ErrorRegistry.define`."""

    code: str
    status: int

    def error(self, *, retry_after: int | None = None, **params: object) -> "AppError":
        return AppError(self, retry_after=retry_after, **params)


class ErrorRegistry:
    def __init__(self) -> None:
        self._codes: dict[str, ErrorCode] = {}

    def define(self, code: str, status: int) -> ErrorCode:
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"mã lỗi sai mẫu ^[A-Z][A-Z0-9_]{{2,63}}$: {code!r}")
        if code in self._codes:
            raise ValueError(f"mã lỗi đã khai: {code}")
        if status not in _STATUSES and not (code == "INTERNAL" and status == 500):
            raise ValueError(f"status {status} không dùng cho mã {code} (W8)")
        if status == 401 and code not in _SESSION_CODES:
            raise ValueError(f"401 chỉ dành cho nhóm Phiên của lõi (W10), không cho {code}")
        error_code = ErrorCode(code, status)
        self._codes[code] = error_code
        return error_code

    def all(self) -> tuple[ErrorCode, ...]:
        return tuple(self._codes.values())


ERRORS: Final = ErrorRegistry()


class AppError(Exception):
    """Lỗi có mã; `params` đã đổi sang khoá dây."""

    def __init__(self, code: ErrorCode, *, retry_after: int | None = None, **params: object) -> None:
        wire: dict[str, str | int] = {}
        for name, value in params.items():
            if name not in _PARAMS:
                raise ValueError(f"tham số lỗi lạ: {name}")
            key, valid = _PARAMS[name]
            if not valid(value):
                raise ValueError(f"giá trị sai cho tham số lỗi {name}")
            wire[key] = cast("str | int", value)  # `valid` đã kiểm kiểu
        if code.status in _RETRY_STATUSES:
            if not (_non_negative_int(retry_after) and 1 <= cast("int", retry_after) <= _MAX_RETRY_AFTER):
                raise ValueError(f"{code.code} ({code.status}) cần retry_after nguyên 1-{_MAX_RETRY_AFTER} (W9)")
        elif retry_after is not None:
            raise ValueError(f"retry_after chỉ dùng cho 429/503, không cho {code.code}")
        super().__init__(code.code)
        self.code: Final = code
        self.params: Final[Mapping[str, str | int]] = MappingProxyType(wire)
        self.retry_after: Final = retry_after

    def wire_params(self) -> dict[str, str | int]:
        return dict(self.params)


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()
"""Giá trị của `RemoteFieldChange.value` khi trường bị gỡ (trên dây: vắng khoá `value`)."""

VersionEntityType = Literal["vertex", "wall", "door", "window", "furniture", "room", "dimension", "roof"]
_ENTITY_TYPES: Final = frozenset(get_args(VersionEntityType))
SYSTEM_PIPELINE: Final = "system:pipeline"


@dataclass(frozen=True, slots=True)
class RemoteFieldChange:
    entity_id: str
    entity_type: VersionEntityType
    field: str
    value: object
    changed_at: datetime
    changed_by: str
    changed_by_name: str

    def __post_init__(self) -> None:
        for name in ("entity_id", "field", "changed_by_name"):
            if not _non_empty_str(getattr(self, name)):
                raise ValueError(f"{name} phải là chuỗi không rỗng")
        if self.entity_type not in _ENTITY_TYPES:
            raise ValueError(f"entity_type lạ: {self.entity_type!r}")
        if self.value is None:
            raise ValueError("value không được là None; trường bị gỡ dùng MISSING")
        if self.changed_at.utcoffset() is None:
            raise ValueError("changed_at phải có múi giờ")
        if self.changed_by != SYSTEM_PIPELINE and not is_id("usr", self.changed_by):
            raise ValueError("changed_by phải là usr_<ULID> hoặc system:pipeline")


class VersionConflictError(AppError):
    """409 `VERSION_CONFLICT` (W20). Luật "rỗng hay ≥ 1" của `remote_changes` do chủ route kiểm."""

    def __init__(self, *, current_version: int, remote_changes: Iterable[RemoteFieldChange]) -> None:
        # Nhập tại chỗ: error_codes nhập module này để dùng ERRORS.
        from packages.core.error_codes import VERSION_CONFLICT

        if not _non_negative_int(current_version):
            raise ValueError("current_version phải là int ≥ 0")
        changes = tuple(remote_changes)
        if not all(isinstance(change, RemoteFieldChange) for change in changes):
            raise ValueError("remote_changes chỉ chứa RemoteFieldChange")
        super().__init__(VERSION_CONFLICT)
        self.current_version: Final = current_version
        self.remote_changes: Final = changes
