"""Fixture vàng của mái (NHA-02): node do BE phát ra nguyên văn cho test hợp đồng của AppFront.

AppFront chép `data/roof_golden.json` vào `src/pascal-page/__tests__/`, nạp `nodes` vào Pascal bằng `setScene` thật rồi
đọc ngược: phải y nguyên (0 lệnh), và `ridgeHeightMm` phải khớp `getActiveRoofHeight` của vendor. Đổi `roof_rects` hay
`roof_nodes` làm test này đỏ; sinh lại tệp bằng lệnh dưới (trong container) rồi chép lại sang AppFront:

    python -c "import json; from packages.vision.roofs.tests.test_golden import golden_cases; \
        print(json.dumps(golden_cases(), ensure_ascii=False, indent=2))" \
        > packages/vision/roofs/tests/data/roof_golden.json
"""

import json
from pathlib import Path
from typing import cast

from packages.domain.spatial import Roof
from packages.domain.spatial.roof_build import RoofForm, RoofRects, ridge_height_mm, roof_nodes
from packages.vision.roofs.rects import XY, roof_rects
from packages.vision.roofs.tests.test_rects import L_SHAPE, RECT, T_SHAPE, U_SHAPE, turned

GOLDEN = Path(__file__).parent / "data" / "roof_golden.json"

# tên, đường bao (mm), dạng, độ dốc (°), phần tư vòng, cốt diềm trên sàn (mm), độ đua ngang (mm), bề dày tường (mm)
CASES: list[tuple[str, list[XY], RoofForm, float, int, float, float, float]] = [
    (
        "rect_gable_turned",
        turned([(x + 2000, y + 1000) for x, y in RECT], 20.0),
        "gable",
        30.0,
        0,
        3300.0,
        300.0,
        220.0,
    ),
    ("l_hip", L_SHAPE, "hip", 30.0, 0, 3000.0, 450.0, 220.0),
    ("t_gable", T_SHAPE, "gable", 35.0, 0, 3300.0, 300.0, 200.0),
    ("u_hip", U_SHAPE, "hip", 25.0, 0, 3300.0, 300.0, 220.0),
    ("rect_flat", RECT, "flat", 0.0, 0, 3300.0, 0.0, 220.0),
    ("rect_shed_turned", RECT, "shed", 10.0, 1, 2700.0, 300.0, 110.0),
]


def golden_cases() -> list[dict[str, object]]:
    """Mỗi ca: tham số thô, node BE phát, và chiều cao đỉnh từng mặt để đối chiếu với vendor."""
    cases: list[dict[str, object]] = []
    for index, (name, outline, form, pitch, turns, eave, overhang, wall) in enumerate(CASES):
        outline_mm = [[round(x, 3), round(y, 3)] for x, y in outline]
        rects = roof_rects([(x, y) for x, y in outline_mm])
        assert isinstance(rects, RoofRects), name
        nodes = roof_nodes(
            rects,
            form=form,
            pitch_deg=pitch,
            quarter_turns=turns,
            eave_above_floor_mm=eave,
            overhang_mm=overhang,
            wall_thickness_mm=wall,
            roof_id=f"RF-GOLDEN{index:04d}",
        )
        ridges = [
            round(ridge_height_mm(form, cast("float", s["width"]) * 1000, cast("float", s["depth"]) * 1000, pitch), 3)
            for s in nodes[1:]
        ]
        cases.append(
            {
                "name": name,
                "outlineMm": outline_mm,
                "form": form,
                "pitchDeg": pitch,
                "quarterTurns": turns,
                "eaveAboveFloorMm": eave,
                "overhangMm": overhang,
                "wallThicknessMm": wall,
                "ridgeHeightMm": ridges,
                "nodes": list(nodes),
            }
        )
    return cases


def test_golden_fixture_is_what_the_backend_emits() -> None:
    """Tệp đã commit trùng đầu ra hiện tại của `roof_rects` + `roof_nodes`."""
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == golden_cases()


def test_golden_nodes_fit_the_wire_roof() -> None:
    """Mọi ca đi lọt model `Roof` trên dây (số node, cỡ JSON)."""
    for case in golden_cases():
        roof_id = cast("list[dict[str, str]]", case["nodes"])[0]["id"].removeprefix("roof_")
        wire = {"id": roof_id, "levelId": "L-LEVEL000000", "nodes": case["nodes"], "confidence": 0.6, "source": "ai"}
        assert Roof.model_validate({**wire, "reviewed": False}).id == roof_id
