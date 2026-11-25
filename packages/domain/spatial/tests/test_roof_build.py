"""Dựng node mái Pascal từ tham số thô (NHA-02): khoá node, chiều cao đỉnh, quy ước toạ độ."""

import itertools
import math
from typing import Any

import pytest

from packages.domain.spatial import Roof
from packages.domain.spatial.roof_build import RoofForm, RoofRect, RoofRects, ridge_height_mm, roof_nodes

ROOF_ID = "RF-ROOF0000000"
ONE_RECT = RoofRects(0.0, (RoofRect(4000.0, 3000.0, 8000.0, 6000.0),))


def build(rects: RoofRects = ONE_RECT, **changes: Any) -> list[dict[str, Any]]:
    """Node của mái hai dốc 30° mặc định, đè các tham số truyền vào."""
    params: dict[str, Any] = {
        "form": "gable",
        "pitch_deg": 30.0,
        "quarter_turns": 0,
        "eave_above_floor_mm": 3300.0,
        "overhang_mm": 300.0,
        "wall_thickness_mm": 200.0,
        "roof_id": ROOF_ID,
        **changes,
    }
    return [dict(node) for node in roof_nodes(rects, **params)]


def plan_bounds_mm(nodes: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    """Khung bao mặt mái trên mặt bằng, đúng công thức `getRoofPlanBounds` của vendor (`roof-overlap.ts:77-112`)."""
    roof, *segments = nodes
    roof_cos, roof_sin = math.cos(roof["rotation"]), math.sin(roof["rotation"])
    xs, zs = [], []
    for segment in segments:
        seg_cos, seg_sin = math.cos(segment["rotation"]), math.sin(segment["rotation"])
        half_width, half_depth = segment["width"] / 2, segment["depth"] / 2
        for x, z in itertools.product((-half_width, half_width), (-half_depth, half_depth)):
            local_x = segment["position"][0] + x * seg_cos + z * seg_sin
            local_z = segment["position"][2] - x * seg_sin + z * seg_cos
            xs.append(roof["position"][0] + local_x * roof_cos + local_z * roof_sin)
            zs.append(roof["position"][2] - local_x * roof_sin + local_z * roof_cos)
    return min(xs) * 1000, min(zs) * 1000, max(xs) * 1000, max(zs) * 1000


@pytest.mark.parametrize(
    ("form", "width", "depth", "run"),
    [
        ("gable", 8000.0, 6000.0, 3000.0),  # nửa `depth`
        ("hip", 8000.0, 6000.0, 3000.0),  # nửa cạnh ngắn
        ("hip", 4000.0, 6000.0, 2000.0),
        ("shed", 8000.0, 6000.0, 6000.0),  # cả `depth`
    ],
)
def test_ridge_height_uses_the_run_of_each_form(form: RoofForm, width: float, depth: float, run: float) -> None:
    """Chiều cao đỉnh = quãng dốc x tan(độ dốc), quãng dốc theo dạng như `getPrimarySlopeRun` của vendor."""
    assert ridge_height_mm(form, width, depth, 30.0) == pytest.approx(run * math.tan(math.radians(30.0)))


def test_flat_roof_has_no_ridge_height() -> None:
    """Mái bằng không có đỉnh, dù `pitch` ghi 40."""
    assert ridge_height_mm("flat", 8000.0, 6000.0, 40.0) == 0.0


def test_single_gable_writes_every_key_explicitly() -> None:
    """Một chữ nhật → node `roof` + một `roof-segment`, đủ khoá để Pascal không phải điền mặc định lúc nạp."""
    assert build() == [
        {
            "object": "node",
            "id": f"roof_{ROOF_ID}",
            "type": "roof",
            "visible": True,
            "metadata": {},
            "position": [4.0, 3.3, 3.0],
            "rotation": 0.0,
            "support": {"kind": "level"},
        },
        {
            "object": "node",
            "id": f"rseg_{ROOF_ID}_0",
            "type": "roof-segment",
            "visible": True,
            "metadata": {},
            "position": [0.0, 0.0, 0.0],
            "rotation": 0.0,
            "roofType": "gable",
            "width": 8.0,
            "depth": 6.0,
            "wallHeight": 0.0,
            "pitch": 30.0,
            "wallThickness": 0.2,
            "deckThickness": 0.1,
            "overhang": 0.34641,  # 300 mm ngang / cos 30°: Pascal đo `overhang` dọc mái dốc
            "shingleThickness": 0.05,
        },
    ]


def test_nodes_fit_the_wire_roof_and_carry_no_tree() -> None:
    """Node đi thẳng vào `Roof.nodes`: không `parentId`/`children`, không khoá FE tự bật rồi tự gỡ."""
    nodes = build()
    wire = {"id": ROOF_ID, "levelId": "L-LEVEL000000", "nodes": nodes, "confidence": 0.6, "source": "ai"}
    assert Roof.model_validate({**wire, "reviewed": False}).nodes == tuple(nodes)
    assert not any({"parentId", "children", "fascia"} & node.keys() for node in nodes)
    assert all(node["metadata"] == {} for node in nodes)


def test_flat_roof_keeps_a_positive_pitch() -> None:
    """Mái bằng ghi `pitch` 40: `migrateNodes` của Pascal đổi mọi `pitch` <= 0 thành 40 lúc nạp, sinh lệnh sửa."""
    segment = build(form="flat", pitch_deg=0.0, overhang_mm=0.0)[1]
    assert (segment["roofType"], segment["pitch"], segment["overhang"]) == ("flat", 40.0, 0.0)


def test_flat_roof_overhang_is_horizontal() -> None:
    """Mái bằng không dốc nên độ đua không chia cos."""
    assert build(form="flat", pitch_deg=0.0, overhang_mm=600.0)[1]["overhang"] == 0.6


@pytest.mark.parametrize(
    ("along", "across", "quarter_turns", "width", "depth", "turns"),
    [
        (8000.0, 6000.0, 0, 8.0, 6.0, 0),  # đỉnh dọc cạnh dài
        (8000.0, 6000.0, 1, 6.0, 8.0, 1),
        (8000.0, 6000.0, 2, 8.0, 6.0, 2),
        (8000.0, 6000.0, 3, 6.0, 8.0, 3),
        (4000.0, 9000.0, 0, 9.0, 4.0, 1),  # cánh vuông góc trục chính: tự quay để `width` là cạnh dài
        (4000.0, 9000.0, 1, 4.0, 9.0, 2),
    ],
)
def test_width_follows_the_ridge(
    along: float, across: float, quarter_turns: int, width: float, depth: float, turns: int
) -> None:
    """`width` là cạnh mang đỉnh mái; `quarter_turns` quay đỉnh quanh tâm chữ nhật, khung bao giữ nguyên."""
    nodes = build(RoofRects(0.0, (RoofRect(0.0, 0.0, along, across),)), quarter_turns=quarter_turns)
    segment = nodes[1]
    assert (segment["width"], segment["depth"]) == (width, depth)
    assert segment["rotation"] == pytest.approx(turns * math.pi / 2)
    assert plan_bounds_mm(nodes) == pytest.approx((-along / 2, -across / 2, along / 2, across / 2), abs=0.01)


@pytest.mark.parametrize(
    ("quarter_turns", "high_side"),
    [(0, (0.0, -1.0)), (1, (-1.0, 0.0)), (2, (0.0, 1.0)), (3, (1.0, 0.0))],
)
def test_shed_high_edge_turns_with_quarter_turns(quarter_turns: int, high_side: tuple[float, float]) -> None:
    """Mép cao của mái một dốc ở -z cục bộ (`getRoofSegmentSurfaceY`); mỗi phần tư vòng quay nó trên mặt bằng."""
    rotation = build(form="shed", pitch_deg=10.0, quarter_turns=quarter_turns)[1]["rotation"]
    assert (-math.sin(rotation), -math.cos(rotation)) == pytest.approx(high_side, abs=1e-9)


def test_l_shape_in_a_rotated_frame_matches_the_plan() -> None:
    """Hai chữ nhật trong khung xoay 30°: mặt bằng `(x, y)` mm ↔ Pascal `(x, z)` m, mặt bằng +θ = yaw -θ."""
    angle = math.radians(30.0)

    def to_plan(u: float, v: float) -> tuple[float, float]:
        """Điểm trong khung trục chính → mặt bằng."""
        return u * math.cos(angle) - v * math.sin(angle), u * math.sin(angle) + v * math.cos(angle)

    body = RoofRect(*to_plan(5000.0, 2000.0), 10000.0, 4000.0)
    wing = RoofRect(*to_plan(2000.0, 4500.0), 4000.0, 9000.0)
    nodes = build(RoofRects(30.0, (body, wing)), form="hip")
    roof, first, second = nodes

    assert roof["rotation"] == pytest.approx(-angle)
    assert (roof["position"][0] * 1000, roof["position"][2] * 1000) == pytest.approx(to_plan(5000.0, 4500.0), abs=0.01)
    assert roof["position"][1] == 3.3
    assert first["position"] == pytest.approx([0.0, 0.0, -2.5])
    assert second["position"] == pytest.approx([-3.0, 0.0, 0.0])
    assert [node["id"] for node in nodes] == [f"roof_{ROOF_ID}", f"rseg_{ROOF_ID}_0", f"rseg_{ROOF_ID}_1"]

    corners = [to_plan(u, v) for u, v in ((0, 0), (10000, 0), (10000, 4000), (0, 4000), (4000, 9000), (0, 9000))]
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    assert plan_bounds_mm(nodes) == pytest.approx((min(xs), min(ys), max(xs), max(ys)), abs=0.01)


@pytest.mark.parametrize(
    ("rects", "changes"),
    [
        (RoofRects(0.0, ()), {}),
        (ONE_RECT, {"pitch_deg": 0.0}),  # Pascal sẽ tự đổi thành 40
        (ONE_RECT, {"pitch_deg": 85.0}),  # trần của schema Pascal; từ đó trở lên là tường
        (ONE_RECT, {"quarter_turns": 4}),
        (ONE_RECT, {"quarter_turns": -1}),
        (RoofRects(0.0, (*ONE_RECT.rects, *ONE_RECT.rects)), {"form": "shed"}),  # shed không che được mặt anh em
        (RoofRects(0.0, (RoofRect(0.0, 0.0, 0.0, 6000.0),)), {}),  # Pascal thay cạnh 0 bằng mặc định 8 m
        (RoofRects(0.0, (RoofRect(0.0, 0.0, 8000.0, -6000.0),)), {}),
        (RoofRects(0.0, (RoofRect(0.0, 0.0, math.nan, 6000.0),)), {}),
        (ONE_RECT, {"overhang_mm": -1.0}),  # Pascal kẹp số âm về 0
        (ONE_RECT, {"wall_thickness_mm": math.inf}),
    ],
)
def test_bad_input_raises(rects: RoofRects, changes: dict[str, Any]) -> None:
    """Tham số mà Pascal sẽ tự sửa hoặc dựng sai hình: ném `ValueError`, không sinh node."""
    with pytest.raises(ValueError, match="mái"):
        build(rects, **changes)
