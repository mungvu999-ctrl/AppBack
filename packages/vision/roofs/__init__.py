"""Hình học mái thuần (NHA-02): đường bao phòng → đường bao mái → chữ nhật mái cho `roof_nodes`.

- `roof_outline(rooms_mm, grow_mm)` → đường bao phần nhà mái che, hoặc mã từ chối.
- `roof_rects(outline_mm)` → `RoofRects` cho `packages.domain.spatial.roof_nodes`, hoặc mã từ chối.
"""

from packages.vision.roofs.rects import XY, RoofRefusal, roof_outline, roof_rects

__all__ = ["XY", "RoofRefusal", "roof_outline", "roof_rects"]
