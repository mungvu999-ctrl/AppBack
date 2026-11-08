"""Bước chữ nhật `rect_pass` trên mặt nạ dựng bằng OpenCV (WALLS-RECT).

Mặt nạ vẽ tay nên đáp án biết trước: tim, bề dày và phần còn lại so với hình đã vẽ.
"""

import math

import cv2
import numpy as np
from numpy.typing import NDArray

from packages.vision.walls.rect_pass import MIN_RUN_PX, _dominant_tilt, rect_walls
from packages.vision.walls.types import WallSegment
from packages.vision.walls.vectorize import vectorize_with_stats

SHAPE = (300, 400)
THICK = 12


def _blank() -> NDArray[np.bool_]:
    """Mặt nạ rỗng `(cao, rộng)`."""
    return np.zeros(SHAPE, bool)


def _box(mask: NDArray[np.bool_], x0: int, y0: int, x1: int, y1: int) -> None:
    """Tô đặc `[y0, y1) x [x0, x1)`."""
    mask[y0:y1, x0:x1] = True


def _wall_at(walls: list[WallSegment], point: tuple[float, float], tol: float = 2.0) -> WallSegment:
    """Đoạn có tim đi qua `point` (sai số `tol` px); không có thì `AssertionError`."""
    for wall in walls:
        (ax, ay), (bx, by) = wall.start, wall.end
        length = math.dist(wall.start, wall.end)
        t = max(0.0, min(1.0, ((point[0] - ax) * (bx - ax) + (point[1] - ay) * (by - ay)) / (length * length)))
        if math.dist(point, (ax + t * (bx - ax), ay + t * (by - ay))) <= tol:
            return wall
    raise AssertionError(f"không đoạn nào qua {point}: {walls}")


def test_rect_walls__horizontal_bar_gives_one_wall_with_measured_thickness() -> None:
    """Thanh ngang 300x12: một đoạn, tim giữa thanh, bề dày đúng 12, không còn lại gì."""
    mask = _blank()
    _box(mask, 50, 100, 350, 100 + THICK)
    walls, residual = rect_walls(mask)
    assert len(walls) == 1
    assert walls[0].start == (50.0, 106.0)
    assert walls[0].end == (350.0, 106.0)
    assert walls[0].thickness_px == THICK
    assert not residual.any()


def test_rect_walls__thick_slab_on_a_wall_keeps_the_wall_centreline() -> None:
    """Tường dính khối dày (sàn WC): tim tường vẫn ở giữa thanh 12 px, khối thành đoạn riêng dày 50."""
    mask = _blank()
    _box(mask, 50, 100, 350, 100 + THICK)
    _box(mask, 200, 100 + THICK, 320, 160)
    walls, _ = rect_walls(mask)
    bar = _wall_at(walls, (100.0, 106.0))
    assert bar.thickness_px == THICK
    assert abs(bar.start[1] - 106.0) <= 0.5
    slab = next(w for w in walls if w is not bar)
    assert slab.thickness_px >= 40
    assert (slab.start[0], slab.end[0]) == (200.0, 320.0)


def test_rect_walls__tee_gives_two_walls() -> None:
    """Chữ T: thanh ngang và thanh dọc là hai đoạn, không có ô vuông giao thừa."""
    mask = _blank()
    _box(mask, 50, 100, 350, 100 + THICK)
    _box(mask, 150, 100 + THICK, 150 + THICK, 220)
    walls, residual = rect_walls(mask)
    assert len(walls) == 2
    assert {w.thickness_px for w in walls} == {float(THICK)}
    assert not residual.any()


def test_rect_walls__ragged_edge_pieces_are_fused_into_one_wall() -> None:
    """Mép răng cưa cắt thanh thành nhiều dải lệch mép 1 px: gộp lại thành một đoạn."""
    mask = _blank()
    for x in range(50, 350, 10):
        _box(mask, x, 100 + (x // 10) % 2, x + 10, 100 + THICK)
    walls, _ = rect_walls(mask)
    assert len(walls) == 1
    assert abs(walls[0].start[0] - 50) <= 1
    assert abs(walls[0].end[0] - 350) <= 1


def test_rect_walls__lone_diagonal_bar_is_read_after_rotation() -> None:
    """Thanh chéo 39° một mình: góc trội là 39°, xoay về trục rồi đọc thành một đoạn đúng góc, bề dày ≈ 12."""
    canvas = np.zeros(SHAPE, np.uint8)
    cv2.line(canvas, (50, 50), (300, 250), 1, THICK)
    mask = canvas.astype(bool)
    walls, residual = rect_walls(mask)
    assert len(walls) == 1
    angle = math.degrees(math.atan2(walls[0].end[1] - walls[0].start[1], walls[0].end[0] - walls[0].start[0]))
    assert abs(angle - math.degrees(math.atan2(200, 250))) <= 1.5
    assert abs(walls[0].thickness_px - THICK) <= 3
    assert not residual.any()


def test_rect_walls__curve_goes_to_the_residual() -> None:
    """Cung tròn: không dải nào đủ thẳng, thân cung nằm trong phần còn lại cho xương."""
    canvas = np.zeros(SHAPE, np.uint8)
    cv2.ellipse(canvas, (200, 150), (120, 120), 0, 200, 340, 1, THICK)
    mask = canvas.astype(bool)
    walls, residual = rect_walls(mask)
    assert sum(math.dist(w.start, w.end) for w in walls) <= 0.3 * mask.sum() / THICK
    assert residual.sum() >= 0.6 * mask.sum()


def test_rect_walls__rotated_frame_is_read_in_its_own_axes() -> None:
    """Khung chữ nhật xoay 20°: bốn đoạn theo trục bản vẽ, tim lệch góc đúng 20° so với ảnh."""
    canvas = np.zeros(SHAPE, np.uint8)
    canvas[60:72, 80:320] = 1
    canvas[228:240, 80:320] = 1
    canvas[60:240, 80:92] = 1
    canvas[60:240, 308:320] = 1
    turn = cv2.getRotationMatrix2D((200.0, 150.0), 20.0, 1.0)
    mask = cv2.warpAffine(canvas, turn, (SHAPE[1], SHAPE[0]), flags=cv2.INTER_NEAREST).astype(bool)
    assert abs(abs(_dominant_tilt(mask)) - 20.0) <= 1.0
    walls, _ = rect_walls(mask)
    assert len(walls) == 4
    angles = [math.degrees(math.atan2(w.end[1] - w.start[1], w.end[0] - w.start[0])) % 90.0 for w in walls]
    assert all(min(abs(a - 20.0), abs(a - 70.0)) <= 1.5 for a in angles), angles
    assert all(abs(w.thickness_px - THICK) <= 2 for w in walls)


def test_rect_walls__short_runs_are_not_walls() -> None:
    """Chấm nhỏ hơn `MIN_RUN_PX`: không đoạn nào, và cũng quá nhỏ để đưa cho xương."""
    mask = _blank()
    _box(mask, 100, 100, 100 + MIN_RUN_PX - 1, 100 + MIN_RUN_PX - 1)
    walls, residual = rect_walls(mask)
    assert not walls
    assert not residual.any()


def test_rect_walls__ink_moves_the_centreline_onto_the_stroke() -> None:
    """Có ảnh xám: tim dời về giữa nét mực khi mặt nạ phình lệch một bên."""
    mask = _blank()
    _box(mask, 50, 100, 350, 100 + THICK + 6)  # mặt nạ phình 6 px xuống dưới nét
    gray = np.full(SHAPE, 255, np.uint8)
    gray[100 : 100 + THICK, 50:350] = 0  # nét mực đúng 12 px
    walls, _ = rect_walls(mask, gray)
    assert len(walls) == 1
    assert abs(walls[0].start[1] - 106.0) <= 1.0
    assert abs(walls[0].thickness_px - THICK) <= 1.0


def test_vectorize_with_stats__rect_true_counts_boxes_and_keeps_the_diagonal() -> None:
    """`rect=True`: khung vuông là 4 đoạn chữ nhật, thanh chéo rời vẫn ra từ xương; `dropped["rect"]` = 4."""
    canvas = np.zeros(SHAPE, np.uint8)
    canvas[60:72, 80:320] = 1
    canvas[228:240, 80:320] = 1
    canvas[60:240, 80:92] = 1
    canvas[60:240, 308:320] = 1
    cv2.line(canvas, (120, 100), (280, 200), 1, THICK)
    result = vectorize_with_stats(canvas.astype(bool), rect=True)
    assert result.dropped["rect"] == 4
    assert {"spur", "short"} <= set(result.dropped)
    diagonal = [w for w in result.walls if abs(w.end[0] - w.start[0]) > 5 and abs(w.end[1] - w.start[1]) > 5]
    assert len(diagonal) >= 1
    assert len(result.walls) >= 5


def test_vectorize_with_stats__rect_true_on_an_empty_mask() -> None:
    """Mặt nạ rỗng với `rect=True`: không đoạn, đủ ba khoá `dropped`."""
    result = vectorize_with_stats(_blank(), rect=True)
    assert result.walls == ()
    assert result.dropped == {"spur": 0, "short": 0, "rect": 0}


def test_rect_walls__collinear_bars_of_different_thickness_stay_apart() -> None:
    """Vách 12 nối tường 30 trên cùng một đường: bề dày lệch > 40 % nên không gộp."""
    mask = _blank()
    _box(mask, 50, 100, 200, 100 + THICK)
    _box(mask, 200, 91, 350, 121)
    walls, _ = rect_walls(mask)
    assert sorted(round(w.thickness_px) for w in walls) == [THICK, 30]


def test_rect_walls__sliver_on_the_face_of_a_wall_is_absorbed() -> None:
    """Dải 2 px dài 40 px áp mặt dưới tường 12 px (răng cưa scan): không thành tường riêng."""
    mask = _blank()
    _box(mask, 50, 100, 350, 100 + THICK)
    _box(mask, 120, 100 + THICK + 1, 160, 100 + THICK + 3)  # hở 1 px để run-length không chập vào thân
    walls, _ = rect_walls(mask)
    assert len(walls) == 1
    assert walls[0].thickness_px == THICK


def test_rect_walls__blank_ink_keeps_the_mask_thickness() -> None:
    """Ảnh xám trắng tinh (không nét): `refine_line` không có bằng chứng, tim và bề dày giữ theo mặt nạ."""
    mask = _blank()
    _box(mask, 50, 100, 350, 100 + THICK)
    walls, _ = rect_walls(mask, np.full(SHAPE, 255, np.uint8))
    assert walls[0].thickness_px == THICK
    assert walls[0].start[1] == 106.0


def test_rect_walls__rotated_frame_with_ink_reads_the_stroke() -> None:
    """Khung xoay 20° kèm ảnh xám cùng xoay: bốn đoạn, bề dày theo nét mực ≈ 12."""
    canvas = np.zeros(SHAPE, np.uint8)
    canvas[60:72, 80:320] = 1
    canvas[228:240, 80:320] = 1
    canvas[60:240, 80:92] = 1
    canvas[60:240, 308:320] = 1
    turn = cv2.getRotationMatrix2D((200.0, 150.0), 20.0, 1.0)
    mask = cv2.warpAffine(canvas, turn, (SHAPE[1], SHAPE[0]), flags=cv2.INTER_NEAREST).astype(bool)
    gray = np.where(mask, 0, 255).astype(np.uint8)
    walls, _ = rect_walls(mask, gray)
    assert len(walls) == 4
    assert all(abs(w.thickness_px - THICK) <= 2 for w in walls)


def test_rect_walls__short_partition_stub_at_a_door_is_kept() -> None:
    """Mẩu vách 11 px dài 33 px thọc vào tường 22 px rồi dừng ở khe cửa: là tường thật, không phải lát cắt."""
    mask = _blank()
    _box(mask, 50, 100, 350, 122)  # tường dày 22
    _box(mask, 200, 122, 211, 144)  # mẩu vách 11 px, 22 px ngoài tường (44 px tính cả phần trong tường)
    _box(mask, 200, 223, 211, 290)  # phần vách bên kia khe cửa
    walls, _ = rect_walls(mask)
    stub = _wall_at(walls, (205.5, 133.0), tol=1.0)
    assert abs(stub.thickness_px - 11) <= 1


def test_rect_walls__corner_ends_stop_at_the_other_wall_centreline() -> None:
    """Khung chữ nhật: mỗi đoạn kết thúc ở tim tường vuông góc (nút góc), không kéo tới mặt ngoài."""
    mask = _blank()
    _box(mask, 80, 60, 320, 60 + THICK)
    _box(mask, 80, 228, 320, 240)
    _box(mask, 80, 60, 80 + THICK, 240)
    _box(mask, 308, 60, 320, 240)
    walls, _ = rect_walls(mask)
    assert len(walls) == 4
    ends = {tuple(round(v, 1) for v in p) for w in walls for p in (w.start, w.end)}
    assert ends == {(86.0, 66.0), (314.0, 66.0), (86.0, 234.0), (314.0, 234.0)}
