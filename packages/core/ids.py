"""Id có tiền tố (W4, HOP-DONG-MOI §0.1).

- Tài nguyên: `<tiền tố>_<ULID>`. ULID = 48 bit mili giây từ `clock.now()` + 80 bit
  `secrets`, Crockford base32 HOA 26 ký tự.
- Thực thể không gian (client sinh): `<chữ>-<base36 HOA 10-64>` (mái: hai chữ `RF`) (`src/domain/spatial/ids.ts:15-43`).
- Phép đo (client sinh): `MS-<4-15 chữ số>`.

Mẫu dùng `[0-9]`, không `\\d` (`\\d` của `re` khớp cả chữ số Unicode), và `fullmatch`
(`$` của `re` nhận cả `\\n` cuối chuỗi).
"""

import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, get_args

from packages.core.clock import Clock

IdPrefix = Literal[
    "prj",
    "usr",
    "upl",
    "drw",
    "ver",
    "ntf",
    "tok",
    "mdl",
    "dst",
    "dsv",
    "job",
    "run",
    "tpl",
    "shl",
    "bak",
    "dcn",
    "mex",
]
SpatialKind = Literal["level", "wall", "opening", "furniture", "room", "axis", "dimension", "roof"]

SPATIAL_PREFIX: Final[Mapping[SpatialKind, str]] = {
    "level": "L",
    "wall": "W",
    "opening": "D",
    "furniture": "F",
    "room": "R",
    "axis": "A",
    "dimension": "M",
    "roof": "RF",
}

_PREFIXES: Final = frozenset(get_args(IdPrefix))
_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_RE: Final = re.compile("[0-9A-HJKMNP-TV-Z]{26}")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MEASUREMENT_RE: Final = re.compile(r"MS-[0-9]{4,15}")


def _check_prefix(prefix: str) -> None:
    """Tiền tố ngoài `IdPrefix` → `ValueError` (lỗi của người gọi)."""
    if prefix not in _PREFIXES:
        raise ValueError(f"tiền tố id lạ: {prefix!r}")


def new_ulid(clock: Clock) -> str:
    """Thân ULID trần (26 ký tự Crockford HOA): 48 bit mili giây của `clock.now()` + 80 bit `secrets`.

    Nguồn duy nhất của bộ sinh: `new_id` ghép tiền tố vào đây, còn tên object ảnh đại diện,
    `level_id` và khoá trang bản vẽ gọi thẳng (NO-168). Trước 1970 → `ValueError`.
    """
    ms = (clock.now() - _EPOCH) // timedelta(milliseconds=1)
    if ms < 0:  # trần 48 bit (năm 10889) nằm ngoài datetime.max
        raise ValueError("ULID không biểu diễn được thời điểm trước 1970")
    value = (ms << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def new_id(prefix: IdPrefix, clock: Clock) -> str:
    """`<prefix>_<ULID>` mới; tiền tố ngoài `IdPrefix` → `ValueError`."""
    _check_prefix(prefix)
    return f"{prefix}_{new_ulid(clock)}"


def is_ulid(value: str) -> bool:
    """Thân ULID trần, không tiền tố: Crockford base32 HOA đúng 26 ký tự.

    Nguồn duy nhất của luật thân ULID: `is_id` kiểm thân qua đây, `packages.storage.keys`
    dùng cho tên ảnh đại diện (ULID trần) thay vì chép mẫu (NO-076).
    """
    return _ULID_RE.fullmatch(value) is not None


def is_id(prefix: IdPrefix, value: str) -> bool:
    """`value` là `<prefix>_<ULID>`; tiền tố ngoài `IdPrefix` → `ValueError` (lỗi của người gọi)."""
    _check_prefix(prefix)
    head = f"{prefix}_"
    return value.startswith(head) and is_ulid(value.removeprefix(head))


def check_id(prefix: IdPrefix, value: str) -> str:
    """Id đúng `<prefix>_<ULID>` → trả lại chính nó; sai → `ValueError` nêu tiền tố cần.

    Dạng ném lỗi của `is_id` cho người dựng khoá object (`packages.core.object_keys`,
    `packages.storage.keys`): id sai bị chặn, kèm tên trường, trước khi thành khoá.
    """
    if not is_id(prefix, value):
        raise ValueError(f"id phải có dạng {prefix}_<ULID>: {value!r}")
    return value


def is_spatial_id(kind: SpatialKind, value: str) -> bool:
    """`value` là id không gian của `kind`: `<chữ>-<base36 HOA 10-64>`."""
    return re.fullmatch(f"{SPATIAL_PREFIX[kind]}-[0-9A-Z]{{10,64}}", value) is not None


def is_measurement_id(value: str) -> bool:
    """`value` là id phép đo `MS-<4-15 chữ số>`."""
    return _MEASUREMENT_RE.fullmatch(value) is not None
