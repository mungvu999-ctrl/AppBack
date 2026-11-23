"""Tái hiện số đo của `cores_report.md` (STAIRS = "confirm") trên 20 bộ DS- đã duyệt bằng mắt (K-32e).

Dữ liệu `data/replay_20_sets.json`: chi tiết bộ dò, hộp mô hình, chữ OCR, hộp toà nhà và đáp án `summary.json` của
nhân (đã in ra từ chính `kernel/cores.py`). Module thuần phải cho đúng từng lõi: tầng, độ chắc, nguồn, thành viên.
Số đo báo cáo: 20 lõi, 12 lõi ≥ 0,9 (cả 12 đều thang thật theo duyệt mắt), 3 lõi kẹp 0,7, 0 sai ở ≥ 0,9.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from packages.domain.drawing_set.cores import (
    CorePlan,
    Detection,
    PlanText,
    StackShift,
    StairCore,
    YoloBox,
    build_wells,
    combine,
    stair_tracks,
)

_DATA = json.loads((Path(__file__).parent / "data" / "replay_20_sets.json").read_text(encoding="utf-8"))["sets"]
_SIZE_LIMIT = 1_000_000


def _run(case: dict[str, Any]) -> tuple[list[StairCore], int]:
    """Dựng mặt bằng từ dữ liệu bộ dò thô như đường sản phẩm sẽ làm, trả (lõi, số ô lẻ)."""
    plans = []
    for p in case["plans"]:
        straight = [
            Detection(tuple(x["box"]), x["n_treads"], "stair", x["angle_deg"], x["pitch_mm"], x["reason"])
            for x in p["straight"]
        ]
        curved = [Detection(tuple(x["box"]), x["n_treads"]) for x in p["curved"]]
        building = tuple(p["building"]) if p["building"] else None
        texts = tuple(PlanText(text, (cx, cy)) for text, cx, cy in p["texts"])
        yolo = None if p["yolo"] is None else [YoloBox(tuple(y["box"]), y["score"]) for y in p["yolo"]]
        wells = build_wells(combine(straight, curved, building), texts, yolo)
        plans.append(CorePlan(p["floor_no"], p["view_id"], wells, texts))
    shifts = [StackShift(tuple(shift), conf) for shift, conf in case["shifts"]]
    cores, unlinked = stair_tracks(plans, shifts)
    return cores, len(unlinked)


def _summary(core: StairCore) -> dict[str, Any]:
    """Dạng so sánh với `summary.json` của nhân."""
    return {
        "floors": list(core.floors),
        "confidence": core.confidence,
        "sources": list(core.sources),
        "assumed_floors": list(core.assumed_floors),
        "observed_floors": list(core.observed_floors),
        "floor_confidence": {str(f): c for f, c in core.floor_confidence.items()},
        "shape_ok": core.shape_ok,
        "members": [list(m) for m in core.members],
    }


def test_fixture_stays_under_one_megabyte() -> None:
    assert (Path(__file__).parent / "data" / "replay_20_sets.json").stat().st_size < _SIZE_LIMIT


@pytest.mark.parametrize("set_id", sorted(_DATA))
def test_set_matches_research_kernel(set_id: str) -> None:
    case = _DATA[set_id]
    cores, unlinked = _run(case)
    expected = [{k: v for k, v in e.items() if k != "id"} for e in case["expected"]]
    assert [_summary(c) for c in cores] == expected
    assert unlinked == len(case["unlinked"])


def test_report_totals_on_twenty_sets() -> None:
    cores = [c for case in _DATA.values() for c in _run(case)[0]]
    assert len(_DATA) == 20
    assert len(cores) == 20
    assert sum(c.confidence >= 0.9 for c in cores) == 12
    assert sum(c.confidence == 0.7 and "yolo_disagrees" in c.sources for c in cores) == 3


@pytest.mark.parametrize(
    ("set_id", "floor_confidence"),
    [
        ("DS-254", {1: 0.6, 2: 0.95, 3: 0.95, 4: 0.95}),  # thang thẳng tầng 2-4, tầng trệt giả định
        ("DS-266", {1: 0.6, 2: 0.92, 3: 0.92, 4: 0.5, 5: 0.5, 6: 0.5}),
    ],
)
def test_report_examples_floor_confidence(set_id: str, floor_confidence: dict[int, float]) -> None:
    (core,) = _run(_DATA[set_id])[0]
    assert dict(core.floor_confidence) == floor_confidence


def test_report_example_gap_core_ds466() -> None:
    first = _run(_DATA["DS-466"])[0][0]
    assert first.observed_floors == (1, 3)
    assert dict(first.floor_confidence)[2] == 0.65
