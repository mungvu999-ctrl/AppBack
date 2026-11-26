"""Đường bao phòng → đường bao mái → chữ nhật mái (NHA-02)."""

import math

import pytest
import shapely

from packages.domain.spatial.roof_build import RoofRect, RoofRects
from packages.vision.roofs import rects as module
from packages.vision.roofs.rects import XY, roof_outline, roof_rects

RECT: list[XY] = [(0, 0), (10000, 0), (10000, 6000), (0, 6000)]
L_SHAPE: list[XY] = [(0, 0), (10000, 0), (10000, 4000), (4000, 4000), (4000, 9000), (0, 9000)]
T_SHAPE: list[XY] = [
    (0, 0),
    (12000, 0),
    (12000, 4000),
    (8000, 4000),
    (8000, 10000),
    (4000, 10000),
    (4000, 4000),
    (0, 4000),
]
U_SHAPE: list[XY] = [
    (0, 0),
    (12000, 0),
    (12000, 9000),
    (8000, 9000),
    (8000, 4000),
    (4000, 4000),
    (4000, 9000),
    (0, 9000),
]
ONE_RECT = RoofRects(0.0, (RoofRect(5000.0, 3000.0, 10000.0, 6000.0),))


def turned(outline: list[XY], degrees: float) -> list[XY]:
    """Đường bao quay quanh gốc, ngược chiều kim đồng hồ."""
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in outline]


def room(x0: float, y0: float, x1: float, y1: float) -> list[XY]:
    """Phòng chữ nhật theo mặt trong tường."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# 1. Đường bao phòng → đường bao mái


@pytest.mark.parametrize("gap", [200, 300])  # tường trong dày bằng hoặc hơn tường ngoài
def test_outline_joins_rooms_across_the_wall_between_them(gap: float) -> None:
    """Hai phòng cách nhau một tường: nới nửa bề dày tường ngoài rồi khép khe, ra một chữ nhật."""
    outline = roof_outline([room(0, 0, 4000, 5000), room(4000 + gap, 0, 8000, 5000)], 100)
    assert not isinstance(outline, str)
    assert len(outline) == 4
    assert shapely.Polygon(outline).bounds == pytest.approx((-100, -100, 8100, 5100), abs=1.0)


def test_outline_keeps_an_l_shape() -> None:
    """Ba phòng xếp chữ L: đường bao sáu đỉnh, không bị nắn thành chữ nhật."""
    rooms = [room(0, 0, 4000, 4000), room(4200, 0, 10000, 4000), room(0, 4200, 4000, 9000)]
    outline = roof_outline(rooms, 100)
    assert not isinstance(outline, str)
    assert len(outline) == 6
    assert shapely.Polygon(outline).area == pytest.approx(10200 * 4200 + 4200 * 5000, rel=1e-3)


@pytest.mark.parametrize(
    ("rooms", "reason"),
    [
        ([], "no_rooms"),
        ([[(0, 0), (1000, 0)]], "no_rooms"),  # chưa đủ ba đỉnh
        ([[(0, 0), (1000, 0), (2000, 0)]], "no_rooms"),  # ba đỉnh thẳng hàng: không có diện tích
        ([room(0, 0, 4000, 4000), room(9000, 0, 12000, 4000)], "split"),
        (
            [
                room(0, 0, 9000, 3000),
                room(0, 6000, 9000, 9000),
                room(0, 3200, 3000, 5800),
                room(6000, 3200, 9000, 5800),
            ],
            "hole",  # bốn phòng quanh sân trong
        ),
    ],
)
def test_outline_refuses_what_one_roof_cannot_cover(rooms: list[list[XY]], reason: str) -> None:
    """Không phòng, hai khối rời, có sân trong: không dựng, trả lý do để hỏi người dùng."""
    assert roof_outline(rooms, 100) == reason


def test_outline_keeps_both_lobes_of_a_self_crossing_room() -> None:
    """Phòng tự cắt hình nơ: sửa thành hai múi, không lặng lẽ bỏ mất một nửa diện tích."""
    outline = roof_outline([[(0, 0), (4000, 4000), (4000, 0), (0, 4000)]], 100)
    assert not isinstance(outline, str)
    assert shapely.Polygon(outline).area > 8_000_000


def test_outline_rejects_a_negative_grow() -> None:
    """Nới âm sẽ co đường bao tới rỗng: lỗi của nơi gọi, không phải lý do từ chối."""
    with pytest.raises(ValueError, match="grow_mm"):
        roof_outline([room(0, 0, 4000, 5000)], -1)


# 2. Đường bao mái → chữ nhật


@pytest.mark.parametrize(
    "outline",
    [
        L_SHAPE[::-1],  # thuận chiều kim đồng hồ
        [*L_SHAPE, L_SHAPE[0]],  # vòng khép: điểm đầu lặp ở cuối
        [L_SHAPE[0], *L_SHAPE],  # điểm lặp liền nhau
        [(0, 0), (5000, 0), *L_SHAPE[1:]],  # điểm thẳng hàng trên cạnh
    ],
)
def test_ring_spelling_does_not_change_the_rects(outline: list[XY]) -> None:
    """Chiều vòng, điểm lặp, điểm thẳng hàng: cùng một hình thì cùng các chữ nhật."""
    assert roof_rects(outline) == roof_rects(L_SHAPE)


def test_grid_lines_do_not_chain_past_the_snap_distance() -> None:
    """Gom theo đầu nhóm: 0, 90, 180 không dính thành một đường lệch 90 mm."""
    assert module._lines([180, 0, 5000, 90]) == [45.0, 180.0, 5000.0]


def test_rectangle_is_one_rect() -> None:
    """Chữ nhật → một mặt, trục chính dọc cạnh dài."""
    assert roof_rects(RECT) == ONE_RECT


def test_tall_rectangle_puts_the_main_axis_on_the_long_side() -> None:
    """Chữ nhật đứng: khung trục chính quay 90° để cạnh dọc trục vẫn là cạnh dài."""
    assert roof_rects(room(0, 0, 6000, 10000)) == RoofRects(-90.0, (RoofRect(3000.0, 5000.0, 10000.0, 6000.0),))


def test_near_rectangle_is_one_rect() -> None:
    """Khuyết góc 1,7 % (>= 0,96 diện tích khung bao, ngưỡng công cụ mái của Pascal): vẫn một mặt phủ cả khung."""
    notched: list[XY] = [(0, 0), (10000, 0), (10000, 5000), (9000, 5000), (9000, 6000), (0, 6000)]
    assert roof_rects(notched) == ONE_RECT


@pytest.mark.parametrize(
    ("outline", "expected"),
    [
        (L_SHAPE, (RoofRect(5000.0, 2000.0, 10000.0, 4000.0), RoofRect(2000.0, 4500.0, 4000.0, 9000.0))),
        (T_SHAPE, (RoofRect(6000.0, 2000.0, 12000.0, 4000.0), RoofRect(6000.0, 5000.0, 4000.0, 10000.0))),
        (
            U_SHAPE,
            (
                RoofRect(6000.0, 2000.0, 12000.0, 4000.0),
                RoofRect(2000.0, 4500.0, 4000.0, 9000.0),
                RoofRect(10000.0, 4500.0, 4000.0, 9000.0),
            ),
        ),
    ],
)
def test_orthogonal_shapes_become_maximal_rectangles(outline: list[XY], expected: tuple[RoofRect, ...]) -> None:
    """L/T/U: mỗi cánh là chữ nhật cực đại xuyên hết thân (Pascal chỉ cắt mặt nhỏ trong ruột mặt lớn); lớn trước."""
    assert roof_rects(outline) == RoofRects(0.0, expected)


def test_turned_l_shape_keeps_one_frame() -> None:
    """Chữ L quay 30°: cả mái chung một góc trục chính, tâm chữ nhật trả về hệ mặt bằng."""
    found = roof_rects(turned(L_SHAPE, 30.0))
    assert isinstance(found, RoofRects)
    assert found.angle_deg == pytest.approx(30.0)
    (body_x, body_y), (wing_x, wing_y) = turned([(5000, 2000), (2000, 4500)], 30.0)
    body, wing = found.rects
    assert (body.cx_mm, body.cy_mm, body.along_mm, body.across_mm) == pytest.approx(
        (body_x, body_y, 10000, 4000), abs=0.01
    )
    assert (wing.cx_mm, wing.cy_mm, wing.along_mm, wing.across_mm) == pytest.approx(
        (wing_x, wing_y, 4000, 9000), abs=0.01
    )


def test_jitter_is_snapped_to_one_line() -> None:
    """Cạnh lệch vài mm (làm tròn toạ độ phòng) không đẻ thêm mặt mái gần trùng."""
    jittered: list[XY] = [
        (0, 0),
        (6000, 0),
        (6000, 5),
        (10000, 5),
        (10000, 4000),
        (4000, 4000),
        (4000, 9000),
        (0, 9000),
    ]
    found = roof_rects(jittered)
    assert isinstance(found, RoofRects)
    sizes = [size for rect in found.rects for size in (rect.along_mm, rect.across_mm)]
    assert sizes == pytest.approx([10000, 3997.5, 4000, 8997.5])


def test_small_bump_is_left_uncovered() -> None:
    """Mấu chéo nhỏ (0,8 % diện tích): bỏ, mái vẫn dựng trên thân chữ nhật."""
    bumped: list[XY] = [(0, 0), (10000, 0), (10000, 6000), (5500, 6000), (5000, 7000), (4500, 6000), (0, 6000)]
    assert roof_rects(bumped) == ONE_RECT


@pytest.mark.parametrize(
    "outline",
    [
        [(0, 0), (10000, 0)],  # chưa thành đa giác
        [(0, 0), (10000, 6000), (10000, 0), (0, 6000)],  # tự cắt
        [(0, 0), (10000, 0), (0, 10000)],  # tam giác: chữ nhật nào cũng phủ thiếu
        [(0, 0), (10000, 0), (10000, 6000), (6000, 6000), (5000, 8000), (4000, 6000), (0, 6000)],  # mấu chéo 3,2 %
        [  # bốn khe hẹp 90 mm bị nắn mất: mái phủ thừa 2,1 %
            (0, 0),
            *[point for x in (5000, 6000, 7000, 8000) for point in ((x, 0), (x, 3500), (x + 90, 3500), (x + 90, 0))],
            (10000, 0),
            (10000, 4000),
            (4000, 4000),
            (4000, 9000),
            (0, 9000),
        ],
        [  # M-08: hai khối 10 m nối bằng cổ dày 80 mm; lưới nuốt cổ, hai chữ nhật rời nhau dù phủ thiếu chỉ 0,5 %
            (0, 0),
            (22000, 0),
            (22000, 10000),
            (12000, 10000),
            (12000, 80),
            (10000, 80),
            (10000, 10000),
            (0, 10000),
        ],
    ],
)
def test_shapes_rectangles_cannot_cover_are_refused(outline: list[XY]) -> None:
    """Phủ thiếu hoặc thừa quá 2 % diện tích, hoặc các mặt không liền một khối → `poor_fit` ("hãy vẽ tay")."""
    assert roof_rects(outline) == "poor_fit"


def test_staircase_outline_is_too_complex() -> None:
    """Đường bao bậc thang tám bậc: quá nhiều đường lưới, không thử ghép chữ nhật."""
    steps = [point for i in range(8) for point in ((10000 - 1000 * i, 1000 * i), (10000 - 1000 * i, 1000 * (i + 1)))]
    assert roof_rects([(0, 0), *steps, (0, 8000)]) == "complex"


def test_outline_with_too_many_vertices_is_too_complex() -> None:
    """Merge-review PR 121: quá 500 đỉnh thì từ chối ngay, trước phép tìm trục chính (bậc hai theo số đỉnh).

    Đỉnh thừa nằm thẳng hàng trên cạnh đáy của chữ nhật: đủ 500 đỉnh vẫn ra một mặt, 501 đỉnh thì `complex`.
    """
    along = [(10000 * i / 497, 0.0) for i in range(498)]
    assert roof_rects([*along, (10000, 6000), (0, 6000)]) == ONE_RECT
    assert roof_rects([*along, (10000, 3000), (10000, 6000), (0, 6000)]) == "complex"


def test_more_faces_than_one_roof_holds_is_too_complex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Số chữ nhật vượt trần mặt mái của một `Roof` (32) → từ chối; hạ trần để kiểm bằng chữ L."""
    monkeypatch.setattr(module, "_MAX_RECTS", 1)
    assert roof_rects(L_SHAPE) == "complex"
