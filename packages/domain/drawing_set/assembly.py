"""Kết quả ráp nhà của từng tầng và luật không ghi đè mái (NHA-07a, thiết kế §2.E; luật M-11, M-16).

Phần thuần của tác vụ `drawing_set.assemble`: hình dạng lưu ở `drawing_sets.building.assembly`, id tất định của mái
máy dựng, dấu vân của nó, và `settle` — quyết định danh sách mái của tầng sau một lượt ráp. Tầng API
(`apps/api/drawing_sets/assembly.py`) gọi `settle` dưới khoá dòng tài liệu tầng, nên mọi điều kiện ở đây đọc đúng
trạng thái đang ghi. Dựng hình (đường bao, chữ nhật) không nằm ở đây vì cần shapely.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import JsonValue

from packages.domain.drawing_set.building import LevelSpec, RoofSpec
from packages.domain.drawing_set.rules import RuleHit
from packages.domain.drawing_set.titles import fold
from packages.domain.spatial.model import DomainModel, Roof

type AssemblyCode = Literal["BUILT", "NO_WALLS", "ROOF_SHAPE_COMPLEX", "ROOF_KEPT", "ROOF_STALE", "USER_REMOVED"]
"""`BUILT`: tầng mang mái máy dựng còn khớp tường. `NO_WALLS`: lượt chạy hỏng hoặc tầng chưa có phòng/tường.
`ROOF_SHAPE_COMPLEX`: hình không dựng nổi, mời vẽ tay. `ROOF_KEPT`: tầng có mái của người nên máy không ghi (M-11).
`ROOF_STALE`: mái máy dựng đã được sửa/duyệt mà tường đổi sau đó (M-16). `USER_REMOVED`: người đã xoá mái máy dựng."""
type Refusal = Literal["NO_WALLS", "ROOF_SHAPE_COMPLEX"]

_BASE36: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ID_CHARS: Final = 16
_ID_BYTES: Final = 10  # 80 bit < 36^16, nên 16 ký tự base36 luôn chứa đủ
# Thiết kế §2.C Câu 5 bước 1. Chỉ khớp danh từ chính của tên: tiếng Việt đứng đầu ("Ban công 2"), tiếng Anh đứng cuối
# ("Roof terrace"); "Phòng ngủ có ban công" vẫn là phòng trong nhà.
_OPEN_AIR: Final = re.compile(r"^\s*(?:SAN THUONG|BAN CONG)\b|\b(?:TERRACE|BALCONY|PATIO)(?:\s*\d+)?\s*$")


class FloorAssembly(DomainModel):
    """Kết quả ráp của một tầng, lưu ở `building.assembly[<levelRef>]`.

    `run_id`, `revision`, `height_mm` là bộ ba dấu vân của lượt ráp: cả ba không đổi thì lượt quét sau bỏ qua tầng mà
    không đọc lớp. `fingerprint` là dấu vân của mái máy dựng **lần gần nhất nó ghi**; mái đã sửa/duyệt lệch dấu vân này
    là `ROOF_STALE`. `hits` là vi phạm luật kiểm lúc ráp (M-04), chỉ có khi `code` là `BUILT`.
    """

    level_id: str
    run_id: str
    revision: int
    height_mm: int
    code: AssemblyCode
    fingerprint: str | None = None
    roof_ids: tuple[str, ...] = ()
    user_removed: bool = False
    hits: tuple[RuleHit, ...] = ()


@dataclass(frozen=True, slots=True)
class Settled:
    """Danh sách mái của tầng sau một lượt ráp (bằng đầu vào khi máy không được ghi) và phần kết quả để lưu."""

    roofs: tuple[Roof, ...]
    code: AssemblyCode
    fingerprint: str | None
    roof_ids: tuple[str, ...]
    user_removed: bool


def roof_id(floor_pk: int) -> str:
    """Id mái máy dựng của một tầng: `RF-` + 16 ký tự base36 của băm, tất định theo **tầng** chứ không theo bộ.

    Nhờ vậy bộ mới thay được mái AI chưa sửa của bộ cũ trên cùng tầng thay vì chồng thêm mái, và mái vắng khỏi lớp
    nhận ra được là "người đã xoá". Đợt đầu mỗi tầng một mái nên không có số thứ tự trong băm.
    """
    value = int.from_bytes(hashlib.sha256(f"drawing_set.assemble:{floor_pk}".encode()).digest()[:_ID_BYTES], "big")
    chars: list[str] = []
    for _ in range(_ID_CHARS):
        value, digit = divmod(value, 36)
        chars.append(_BASE36[digit])
    return "RF-" + "".join(reversed(chars))


def open_air_room(name: str) -> bool:
    """Phòng mang tên sân thượng/ban công: không có mái che dù `usage` không phải `loggia` (so trên tên đã gấp chữ).

    Tên chỉ nhắc tới ban công ("Phòng ngủ có ban công", "Balcony door lobby") không tính: bỏ nhầm một phòng trong nhà
    làm mái co lại, nặng hơn lợp thừa một ban công.
    """
    return _OPEN_AIR.search(fold(name)) is not None


def fingerprint(nodes: Sequence[dict[str, JsonValue]]) -> str:
    """Dấu vân của một mái: băm JSON chuẩn của các node. Đường bao, chiều cao tầng hay thông số mái đổi thì nó đổi."""
    canonical = json.dumps(list(nodes), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def eave_above_floor_mm(spec: RoofSpec, level: LevelSpec | None, floor_height_mm: int) -> int:
    """Cốt diềm so với sàn của tầng mái che.

    Diềm mặc định nằm ở đỉnh tường, tức chiều cao **hiện tại** của tầng: người dùng sửa chiều cao tầng thì mái đi theo
    tường (M-16). Chỉ khi người duyệt tự nhập cốt diềm mới lấy khoảng cách từ cốt tầng đã duyệt tới cốt đó.

    Nguồn `reviewer` chưa đủ để nói "tự nhập": `building._wall_top` cho diềm thừa kế nguồn của chiều cao, nên người
    duyệt chỉ nhập chiều cao tầng thì diềm cũng mang `reviewer`. Diềm trùng đỉnh tường đã duyệt (cốt + chiều cao đang
    hiệu lực) là diềm suy ra, vẫn đi theo tường hiện tại.
    """
    eave = spec.eave_elevation_mm
    if eave is None or eave.source != "reviewer" or level is None or level.elevation_mm is None:
        return floor_height_mm
    above = int(eave.value) - int(level.elevation_mm.value)
    derived = level.height_mm is not None and above == int(level.height_mm.value)
    return floor_height_mm if derived else above


def settle(roofs: Sequence[Roof], own_id: str, built: Roof | Refusal, previous: FloorAssembly | None) -> Settled:
    """Danh sách mái của tầng sau lượt ráp này, theo luật M-11 (không ghi đè) và M-16 (mái lệch tường).

    `roofs`: mái đang có trong lớp (đọc dưới khoá). `own_id`: `roof_id` của tầng. `built`: mái vừa dựng từ tường hiện
    tại, hoặc lý do không dựng được. `previous`: kết quả lượt ráp trước của tầng. Theo thứ tự:

    1. Mái máy dựng từng có mà nay vắng → người đã xoá: không bao giờ tạo lại (cờ dính, kể cả khi tường đổi sau đó).
    2. Tầng có bất kỳ mái nào do người vẽ, đã duyệt hoặc đã sửa → không ghi gì. Mái của máy còn đó mà dấu vân lệch
       mái vừa dựng thì `ROOF_STALE`; còn khớp thì `BUILT`; máy chưa từng có mái ở tầng này thì `ROOF_KEPT`.
    3. Còn lại: thay mái máy dựng chưa sửa bằng mái mới; không dựng được thì gỡ nó (mái cũ không còn khớp tường).
    """
    current = tuple(roofs)
    present = any(roof.id == own_id for roof in current)
    last = None if previous is None else previous.fingerprint
    if previous is not None and (previous.user_removed or (own_id in previous.roof_ids and not present)):
        return Settled(current, "USER_REMOVED", last, (), True)
    mark = fingerprint(built.nodes) if isinstance(built, Roof) else None
    if any(roof.source != "ai" or roof.reviewed or roof.edited for roof in current):
        if not present:
            return Settled(current, "ROOF_KEPT", None, (), False)
        return Settled(current, "BUILT" if mark is not None and mark == last else "ROOF_STALE", last, (own_id,), False)
    if not isinstance(built, Roof):
        return Settled(tuple(roof for roof in current if roof.id != own_id), built, None, (), False)
    replaced = tuple(built if roof.id == own_id else roof for roof in current) if present else (*current, built)
    return Settled(replaced, "BUILT", mark, (own_id,), False)
