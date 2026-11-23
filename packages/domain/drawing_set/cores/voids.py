"""Gợi ý ô trống/giếng trời theo tầng từ chữ (K-32e, `cores.voids`).

Chữ THONG TANG / GIENG TROI / VOID / O TRONG / SAN TROI trên mặt bằng. Có ô tường kín quanh chữ (`CorePlan.cell_of`)
thì lấy hộp ô, độ chắc 0,75; không thì chỉ điểm tâm chữ, 0,6 (một nguồn). Cổ chai: mặt nạ tường luôn rò khi đổ
loang nên nhân chỉ ra được điểm; vì vậy đây là gợi ý, không phải lỗ sàn.
"""

from collections.abc import Sequence
from typing import Final

from packages.domain.drawing_set.cores.evidence import void_texts
from packages.domain.drawing_set.cores.types import CorePlan, VoidHint

CELL_CONF: Final = 0.75
TEXT_CONF: Final = 0.6


def voids(plans: Sequence[CorePlan]) -> list[VoidHint]:
    """Gợi ý ô trống của mọi mặt bằng, theo thứ tự mặt bằng rồi thứ tự chữ."""
    out: list[VoidHint] = []
    for plan in plans:
        for t in void_texts(plan.texts):
            cell = plan.cell_of(t.centre_mm) if plan.cell_of else None
            if cell is not None:
                box = (int(cell[0]), int(cell[1]), int(cell[2]), int(cell[3]))
                out.append(VoidHint(plan.floor_no, plan.view_id, t.text, CELL_CONF, "text+cell", box_mm=box))
            else:
                centre = (int(t.centre_mm[0]), int(t.centre_mm[1]))
                out.append(VoidHint(plan.floor_no, plan.view_id, t.text, TEXT_CONF, "text", centre_mm=centre))
    return out
