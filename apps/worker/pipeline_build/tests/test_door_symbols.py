"""R6-W: nét ký hiệu cửa (quạt, cánh) vector hoá thành tường bị bỏ, phòng vẫn khép (`geometry._drop_door_symbols`).

Mặt bằng chung: hộp 6 x 4 m, vách đứng `x = 3000` chia hai phòng, khe cửa trên vách; px = mm (`Scaling(1, 1)`).
Nét quạt dựng như mặt nạ thật vẽ ra: cánh vuông góc vách + dây cung, dày 60 mm (trên ngưỡng sợi tóc 40 mm).
"""

from collections import Counter
from typing import Any

import cv2
import numpy as np

from apps.worker.pipeline_build.geometry import Scaling, WallSet, build_walls
from apps.worker.pipeline_build.ids import new_spatial_id
from apps.worker.pipeline_build.residual import PageRaster
from apps.worker.pipeline_build.rooms import build_rooms
from apps.worker.pipeline_build.tests.test_geometry_openings import det
from apps.worker.pipeline_build.tests.test_geometry_walls import wall_px
from packages.domain.spatial import Room, Wall
from packages.ml_contracts.artifacts import DetectionPx, WallPx
from packages.testing.fixtures.clock import FakeClock

_FAN = 60.0


def _shell(gaps: list[tuple[float, float]]) -> list[WallPx]:
    """Vỏ 6 x 4 m + vách `x = 3000` đứt ở các khe `gaps` (khoảng `y`)."""
    walls = [wall_px(0, 0, 6000, 0), wall_px(6000, 0, 6000, 4000), wall_px(6000, 4000, 0, 4000), wall_px(0, 4000, 0, 0)]
    cursor = 0.0
    for low, high in gaps:
        if low > cursor:
            walls.append(wall_px(3000, cursor, 3000, low))
        cursor = high
    walls.append(wall_px(3000, cursor, 3000, 4000))
    return walls


def _layer(walls: list[WallPx], detections: list[DetectionPx], clock: FakeClock) -> tuple[WallSet, tuple[Room, ...]]:
    """`build_walls` → `build_rooms` như `build._assembled` (tường qua khe 3c không vào phòng)."""
    shared: dict[str, Any] = {"level_id": new_spatial_id("level", clock), "scaling": Scaling(1.0, 1.0), "clock": clock}
    wall_set = build_walls(walls, detections, dropped=Counter(), **shared)
    from_inputs = set(wall_set.input_to_wall.values())
    room_walls = [wall for k, wall in enumerate(wall_set.walls) if k in from_inputs]
    rooms = build_rooms(room_walls, wall_set.bridges, (), detections=detections, dropped=Counter(), **shared)
    return wall_set, rooms


def _inside_box(wall: Wall, box: tuple[float, float, float, float]) -> bool:
    """Tim tường nằm trọn trong hộp (mm)."""
    x0, y0, x1, y1 = box
    ends = (wall.centreline.start, wall.centreline.end)
    return all(x0 - 50 <= p.x <= x1 + 50 and y0 - 50 <= p.y <= y1 + 50 for p in ends)


def _spans(walls: tuple[Wall, ...], y: float, x0: float, x1: float) -> bool:
    """Có tường ngang ở `y` phủ trọn `[x0, x1]` (tường thật còn nguyên)."""
    return any(
        w.centreline.start.y == y == w.centreline.end.y
        and min(w.centreline.start.x, w.centreline.end.x) <= x0
        and max(w.centreline.start.x, w.centreline.end.x) >= x1
        for w in walls
    )


_SWING_BOX = (2950.0, 1500.0, 3900.0, 2400.0)
_SWING = [wall_px(3000, 1500, 3900, 1500, _FAN), wall_px(3900, 1500, 3000, 2400, _FAN)]


def test_door_symbols__missed_door_keeps_fan_as_before(fake_clock: FakeClock) -> None:
    """Detector bỏ sót cửa (quạt còn trong mặt nạ, không hộp): không có căn cứ → giữ nét quạt như baseline, 2 phòng."""
    wall_set, rooms = _layer([*_shell([(1500, 2400)]), *_SWING], [], fake_clock)
    assert any(_inside_box(w, _SWING_BOX) and w.centreline.start.x != w.centreline.end.x for w in wall_set.walls)
    assert len(rooms) == 2


def test_door_symbols__fan_dropped_rooms_stay_closed(fake_clock: FakeClock) -> None:
    """Cửa một cánh có hộp: cánh + dây cung bỏ khỏi tường 3D; ô mở trên vách; vẫn đúng 2 phòng (khe được khép)."""
    box = det("door", *_SWING_BOX)
    wall_set, rooms = _layer([*_shell([(1500, 2400)]), *_SWING], [box], fake_clock)
    (opening,) = wall_set.openings
    host = next(w for w in wall_set.walls if w.id == opening.wall_id)
    assert host.centreline.start.x == host.centreline.end.x == 3000
    assert not [w for w in wall_set.walls if w is not host and _inside_box(w, _SWING_BOX)]
    assert len(rooms) == 2


def test_door_symbols__corner_door_keeps_perpendicular_wall(fake_clock: FakeClock) -> None:
    """Cửa sát góc (khe 0..900 sát tường trên): bỏ quạt, tường trên đi qua mép hộp còn nguyên, 2 phòng."""
    box_xy = (2950.0, 0.0, 3900.0, 900.0)
    fan = [wall_px(3000, 900, 3900, 900, _FAN), wall_px(3900, 900, 3050, 50, _FAN)]
    wall_set, rooms = _layer([*_shell([(0, 900)]), *fan], [det("door", *box_xy)], fake_clock)
    assert _spans(wall_set.walls, 0, 0, 6000)
    assert not [w for w in wall_set.walls if _inside_box(w, box_xy) and w.centreline.start.y != 0]
    assert len(rooms) == 2


def test_door_symbols__double_door_drops_both_leaves(fake_clock: FakeClock) -> None:
    """Cửa đôi 1600: hai cánh + hai dây cung đều bỏ, một ô mở, 2 phòng."""
    box_xy = (2950.0, 1200.0, 3800.0, 2800.0)
    fan = [
        wall_px(3000, 1200, 3800, 1200, _FAN),
        wall_px(3800, 1200, 3000, 2000, _FAN),
        wall_px(3000, 2800, 3800, 2800, _FAN),
        wall_px(3800, 2800, 3000, 2000, _FAN),
    ]
    wall_set, rooms = _layer([*_shell([(1200, 2800)]), *fan], [det("double_door", *box_xy)], fake_clock)
    (opening,) = wall_set.openings
    assert not [w for w in wall_set.walls if w.id != opening.wall_id and _inside_box(w, box_xy)]
    assert len(rooms) == 2


def test_door_symbols__sliding_door_keeps_wall_and_closes(fake_clock: FakeClock) -> None:
    """Cửa lùa (không quạt, hai cánh song song trong bề dày vách): ô mở lùa, 2 phòng."""
    box_xy = (2950.0, 1000.0, 3050.0, 2600.0)
    panels = [wall_px(2980, 1000, 2980, 1850, _FAN), wall_px(3020, 1750, 3020, 2600, _FAN)]
    wall_set, rooms = _layer([*_shell([(1000, 2600)]), *panels], [det("door", *box_xy)], fake_clock)
    (opening,) = wall_set.openings
    assert opening.swing == "sliding"
    assert len(rooms) == 2


def test_door_symbols__false_box_does_not_cut_real_wall(fake_clock: FakeClock) -> None:
    """Detector gọi sai (hộp cửa trên vách liền, có vách ngang thật xuyên hộp): không tường thật nào bị bỏ."""
    box_xy = (2950.0, 1500.0, 3900.0, 2400.0)
    real = wall_px(3000, 1800, 6000, 1800)
    walls = [*_shell([]), real]
    baseline, _ = _layer(walls, [], fake_clock)
    wall_set, rooms = _layer(walls, [det("door", *box_xy)], fake_clock)
    assert _spans(wall_set.walls, 1800, 3000, 6000)
    assert len(wall_set.walls) == len(baseline.walls)
    assert len(rooms) == 3


_THIN_BOX = (2950.0, 1500.0, 3050.0, 2400.0)


def test_door_symbols__thin_box_fan_outside_box_dropped(fake_clock: FakeClock) -> None:
    """v2: hộp mảnh chỉ ôm khe, quạt vẽ ra ngoài hộp → vùng quạt nới một bán kính cánh, quạt vẫn bị bỏ, 2 phòng."""
    wall_set, rooms = _layer([*_shell([(1500, 2400)]), *_SWING], [det("door", *_THIN_BOX)], fake_clock)
    zone = (2050.0, 1500.0, 3950.0, 2400.0)
    (opening,) = wall_set.openings
    assert not [w for w in wall_set.walls if w.id != opening.wall_id and _inside_box(w, zone)]
    assert len(rooms) == 2


def test_door_symbols__thin_box_keeps_real_wall_from_jamb(fake_clock: FakeClock) -> None:
    """v2: vách thật 1,5 m mọc từ má cửa (dài hơn bán kính cánh) không nằm trọn vùng quạt → giữ."""
    real = wall_px(3000, 2400, 4500, 2400)
    wall_set, _ = _layer([*_shell([(1500, 2400)]), real], [det("door", *_THIN_BOX)], fake_clock)
    assert _spans(wall_set.walls, 2400, 3000, 4500)


def test_door_symbols__closet_wall_at_jamb_kept(fake_clock: FakeClock) -> None:
    """Sửa sau phản biện X2-C1 (cắt từ cvcfp-Ic_JN0702): vách tủ 800 mm (< bán kính cánh) mọc từ má cửa, đầu xa nối
    vách khác ra ngoài vùng quạt → vách thật, giữ; quạt cùng cửa vẫn bị bỏ.
    """
    closet = [wall_px(3000, 2400, 3800, 2400), wall_px(3800, 2400, 3800, 4000)]
    fan = [wall_px(3000, 1500, 3800, 1500, _FAN)]
    wall_set, _ = _layer([*_shell([(1500, 2400)]), *closet, *fan], [det("door", *_THIN_BOX)], fake_clock)
    assert _spans(wall_set.walls, 2400, 3000, 3800)
    assert not _spans(wall_set.walls, 1500, 3100, 3700)


def test_door_symbols__thin_box_without_ink_swing_keeps_stub(fake_clock: FakeClock) -> None:
    """Sửa sau phản biện X2-C1 (cvcfp-Ic_JN0702: hộp door mảnh trên vách placard, vách 89 px đầu tự do): trang không
    có cánh + cung → không nới vùng quạt, mẩu vách thật mọc từ má cửa được giữ.
    """
    blank = PageRaster(gray=np.full((4100, 6100), 255, np.uint8), walls=np.zeros((4100, 6100), bool))
    stub = wall_px(3000, 2400, 3890, 2400)
    shared: dict[str, Any] = {"level_id": new_spatial_id("level", fake_clock), "scaling": Scaling(1.0, 1.0)}
    walls = [*_shell([(1500, 2400)]), stub]
    wall_set = build_walls(walls, [det("door", *_THIN_BOX)], clock=fake_clock, dropped=Counter(), page=blank, **shared)
    assert _spans(wall_set.walls, 2400, 3000, 3890)


def test_door_symbols__ink_wedge_drops_fan_keeps_jamb_stub(fake_clock: FakeClock) -> None:
    """R8-W v5: trang vẽ cánh + cung (bản lề y = 1500, cánh 900 mm sang phải) → nét quạt trong quạt 1/4 tròn bị bỏ; vách
    890 mm mọc từ má kia (cvcfp-Ic_JN0702) nằm ngoài quạt → giữ. Mực tường tô đen dày không tính là cánh.
    """
    gray = np.full((4100, 6100), 255, np.uint8)
    cv2.line(gray, (3000, 0), (3000, 1500), 0, 100)
    cv2.line(gray, (3000, 2400), (3000, 4000), 0, 100)
    cv2.line(gray, (3050, 1500), (3950, 1500), 0, 3)
    cv2.ellipse(gray, (3050, 1500), (900, 900), 0, 0, 90, 0, 3)
    page = PageRaster(gray=gray, walls=np.zeros(gray.shape, bool))
    stub = wall_px(3000, 2400, 3890, 2400)
    shared: dict[str, Any] = {"level_id": new_spatial_id("level", fake_clock), "scaling": Scaling(1.0, 1.0)}
    walls = [*_shell([(1500, 2400)]), *_SWING, stub]
    wall_set = build_walls(walls, [det("door", *_THIN_BOX)], clock=fake_clock, dropped=Counter(), page=page, **shared)
    assert _spans(wall_set.walls, 2400, 3000, 3890)
    assert not _spans(wall_set.walls, 1500, 3100, 3800)
    ends = [(w.centreline.start, w.centreline.end) for w in wall_set.walls]
    assert not [(p, q) for p, q in ends if p.x != q.x and p.y != q.y]
