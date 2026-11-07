"""Mã lỗi riêng của module tầng (B2-03 [2]); mã lõi (`NOT_FOUND`, `VALIDATION`, …) ở
`packages.core.error_codes`.
"""

from packages.core.errors import ERRORS

FLOOR_ID_TAKEN = ERRORS.define("FLOOR_ID_TAKEN", 409)
"""#10: `id` trùng tầng chưa xoá của cùng dự án; ném kèm `field="id"`."""

FLOOR_ID_AMBIGUOUS = ERRORS.define("FLOOR_ID_AMBIGUOUS", 409)
"""#11, #13: id tầng khớp tầng ở hơn một dự án người gọi là thành viên."""

FLOOR_LIMIT_REACHED = ERRORS.define("FLOOR_LIMIT_REACHED", 422)
"""#10 (và khôi phục): dự án đã có `FLOORS_MAX` tầng chưa xoá."""

FLOOR_REORDER_MISMATCH = ERRORS.define("FLOOR_REORDER_MISMATCH", 422)
"""#13: `floorIds` không bằng đúng tập tầng chưa xoá của một dự án; ném kèm `field="floorIds"`."""

FLOOR_HEIGHT_BELOW_OPENING = ERRORS.define("FLOOR_HEIGHT_BELOW_OPENING", 422)
"""#34 (H2): hạ `heightMm` làm một tường theo tầng thấp hơn đỉnh ô mở của nó; ném kèm `field="heightMm"`."""
