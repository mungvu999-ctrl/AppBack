"""Mở rộng lõi sang tầng máy dò sót: khe giữa chuỗi, mọi tầng bên dưới, tầng bên trên (K-32e, `cores._extend`).

Luật (đo trên 20 bộ, cores_report): thang phải bắt đầu ở đâu đó nên tầng dưới được thêm tới mặt bằng thấp nhất trừ
khi giếng chiếu xuống bị chiếm (tên phòng ≥ 3 chữ, hoặc tường phủ > `WALL_VETO`); tầng trên chỉ thêm khi mặt
bằng kế là tầng kế (số tầng + 1) và không có tên phòng đè lên giếng chiếu. Tầng thêm vào độ chắc thấp: dưới 0,6,
trên 0,5, khe 0,65; tầng quan sát không có chứng cứ riêng bị kẹp ở `OWN_CAP`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from packages.domain.drawing_set.cores.evidence import has_room_label
from packages.domain.drawing_set.cores.geometry import move
from packages.domain.drawing_set.cores.types import Box, CorePlan, Point, StackShift

WALL_VETO: Final = 0.4  # tầng dưới dừng khi tường phủ hơn mức này của giếng chiếu
OWN_CAP: Final = 0.75  # tầng quan sát chưa có bằng chứng riêng
BELOW_CONF: Final = 0.6
ABOVE_CONF: Final = 0.5
GAP_CONF: Final = 0.65
_ROOM_LETTERS_BELOW: Final = 3  # phòng dưới thang cần tên ≥ 3 chữ (không phải "WC", "KHO")
# ponytail: không có veto "ngoài toà nhà" - hộp toà nhà ở tầng dưới hay suy biến (DS-248/254/266) và từng chặn
# nhầm 3 lần mở rộng thật; thêm lại khi hộp toà nhà đáng tin (đường nâng cấp: đường bao từ K-32d).


@dataclass(frozen=True)
class Extension:
    """Kết quả mở rộng: dải tầng, tầng giả định, hộp chiếu, tổng dịch tâm khi mở xuống."""

    low: int
    high: int
    assumed: tuple[int, ...]
    boxes: tuple[tuple[str, Box], ...]
    centre_shift: Point


def _occupied(plan: CorePlan, box: Box) -> bool:
    """Giếng chiếu xuống tầng dưới bị chiếm: tên phòng ≥ 3 chữ hoặc tường phủ quá `WALL_VETO`."""
    walls = plan.wall_cover(box) if plan.wall_cover else 0.0
    return has_room_label(plan.texts, box, _ROOM_LETTERS_BELOW) or walls > WALL_VETO


def _extend_down(
    plans: Sequence[CorePlan], shifts: Sequence[StackShift | None], k: int, box: Box
) -> tuple[int, list[tuple[str, Box]], Point]:
    """Chiếu giếng xuống các mặt bằng dưới tới khi thiếu dịch hoặc bị chiếm; trả (chỉ số thấp nhất, hộp, dịch tâm)."""
    projected: list[tuple[str, Box]] = []
    cx = cy = 0.0
    while k > 0 and (s := shifts[k - 1]) is not None and s.shift_mm is not None:
        nb = move(box, s.shift_mm)
        if _occupied(plans[k - 1], nb):
            break
        box, k = nb, k - 1
        projected.append((plans[k].view_id, box))
        cx, cy = cx + s.shift_mm[0], cy + s.shift_mm[1]
    return k, projected, (cx, cy)


def _extend_up(
    plans: Sequence[CorePlan], shifts: Sequence[StackShift | None], k: int, box: Box
) -> tuple[int, list[tuple[str, Box]]]:
    """Chiếu giếng lên khi mặt bằng kế đúng là tầng kế và không có tên phòng đè lên giếng chiếu."""
    projected: list[tuple[str, Box]] = []
    while k < len(plans) - 1 and plans[k + 1].floor_no == plans[k].floor_no + 1:
        s = shifts[k]
        if s is None or s.shift_mm is None:
            break
        nb = move(box, (-s.shift_mm[0], -s.shift_mm[1]))
        if has_room_label(plans[k + 1].texts, nb):
            break
        box, k = nb, k + 1
        projected.append((plans[k].view_id, box))
    return k, projected


def extend_chain(
    plans: Sequence[CorePlan], shifts: Sequence[StackShift | None], chain: Sequence[tuple[int, int]]
) -> tuple[Extension, list[int]]:
    """Mở rộng một chuỗi (chỉ số mặt bằng, chỉ số ô) tăng dần; trả (mở rộng, số tầng quan sát đã có)."""
    have = sorted({plans[k].floor_no for k, _ in chain})
    first = plans[chain[0][0]].wells[chain[0][1]].box
    last = plans[chain[-1][0]].wells[chain[-1][1]].box
    k_low, down, shift = _extend_down(plans, shifts, chain[0][0], first)
    k_high, up = _extend_up(plans, shifts, chain[-1][0], last)
    low, high = plans[k_low].floor_no, plans[k_high].floor_no
    assumed = tuple(f for f in range(low, high + 1) if f not in have)
    return Extension(low, high, assumed, tuple(down + up), shift), have


def floor_confidence(
    ext: Extension, observed: Sequence[int], confidence: float, evidenced: set[int]
) -> dict[int, float]:
    """Độ chắc từng tầng: quan sát có chứng cứ riêng = độ chắc lõi, thiếu chứng cứ ≤ `OWN_CAP`; giả định theo vị trí."""
    lo, hi = min(observed), max(observed)
    out: dict[int, float] = {}
    for f in range(ext.low, ext.high + 1):
        if f in observed:
            out[f] = confidence if f in evidenced else min(confidence, OWN_CAP)
        else:
            out[f] = BELOW_CONF if f < lo else ABOVE_CONF if f > hi else GAP_CONF
    return out
