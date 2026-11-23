"""Test thuần của `report.build_report`/`RunQualityReport.to_json_bytes` (B5-07 [8]).

Không DB, không kho: `build_report` chỉ nhận một `SpatialLayer` dựng tay trong bộ nhớ.
"""

import json
from decimal import Decimal

from apps.worker.pipeline_quality.report import PAGE_RASTER_UNAVAILABLE, build_report
from packages.domain.spatial.model import Opening, Point, Segment, SpatialLayer, Wall

_LEVEL = "L-LEVEL00000000001"
_AI = {"source": "ai", "reviewed": False}


def _wall(id_: str, *, confidence: float, extra: dict[str, object] | None = None) -> Wall:
    """Một tường tối thiểu hợp lệ, không ô mở, gắn `_LEVEL`."""
    return Wall(
        id=id_,
        level_id=_LEVEL,
        centreline=Segment(start=Point(x=0, y=0), end=Point(x=1000, y=0)),
        thickness_mm=100,
        height_mm=2500,
        kind="partition",
        opening_ids=(),
        confidence=confidence,
        **(extra or _AI),
    )


def test_build_report__ai_below_threshold_counts_as_low_confidence() -> None:
    """Ngưỡng 0,7, bốn tường ([8]): AI 0,5 và AI 0,9 (chỉ 0,5 dưới ngưỡng), `human` 0,3 và
    `human` `reviewed` 0,2 (cả hai bị loại dù dưới ngưỡng) → `lowConfidence.walls == 1`.

    Hai điều kiện loại trừ của `_low_confidence_count` (`source == "ai"`, `reviewed is False`)
    mỗi cái có một tường đi qua nhánh sai của nó (TEST-02): bỏ điều kiện `source` thì tường
    `human` 0,3 bị đếm nhầm; bỏ điều kiện `reviewed` thì tường `human reviewed` 0,2 bị đếm nhầm.
    """
    layer = SpatialLayer(
        walls=(
            _wall("W-WALL0000000001", confidence=0.5),
            _wall("W-WALL0000000002", confidence=0.9),
            _wall("W-WALL0000000003", confidence=0.3, extra={"source": "human", "reviewed": False}),
            _wall("W-WALL0000000004", confidence=0.2, extra={"source": "human", "reviewed": True}),
        ),
        openings=(),
        rooms=(),
        furniture=(),
    )
    report = build_report(run_id="run_01", revision=1, layer=layer, level_id=_LEVEL, threshold=Decimal("0.7"))
    assert report.low_confidence == {"walls": 1, "openings": 0, "rooms": 0, "furniture": 0}


def test_build_report__confidence_equal_threshold_is_not_counted() -> None:
    """`confidence == threshold` không tính (luật ngặt `<`)."""
    layer = SpatialLayer(walls=(_wall("W-WALL0000000001", confidence=0.7),), openings=(), rooms=(), furniture=())
    report = build_report(run_id="run_01", revision=1, layer=layer, level_id=_LEVEL, threshold=Decimal("0.7"))
    assert report.low_confidence["walls"] == 0


def test_build_report__empty_layer_has_no_issues_and_zero_counts() -> None:
    """Lớp rỗng: không lỗi toàn vẹn, bốn khoá `lowConfidence` đều 0."""
    layer = SpatialLayer(walls=(), openings=(), rooms=(), furniture=())
    report = build_report(run_id="run_01", revision=1, layer=layer, level_id=_LEVEL, threshold=Decimal("0.7"))
    assert report.issues == ()
    assert report.low_confidence == {"walls": 0, "openings": 0, "rooms": 0, "furniture": 0}
    assert report.has_critical is False


def test_build_report__issue_order_preserved_and_ref_id_absent_on_wire() -> None:
    """Thứ tự `issues` giữ nguyên thứ tự `check_integrity`; `refId` vắng hẳn khoá khi `None`.

    `_duplicate_ids` báo **một** lỗi cho mỗi id trùng (không phải một lỗi mỗi lần xuất hiện).
    """
    duplicated = _wall("W-WALL0000000001", confidence=0.9)
    layer = SpatialLayer(walls=(duplicated, duplicated), openings=(), rooms=(), furniture=())
    report = build_report(run_id="run_01", revision=1, layer=layer, level_id=_LEVEL, threshold=Decimal("0.7"))
    assert [issue.rule for issue in report.issues] == ["duplicateId"]
    wire = json.loads(report.to_json_bytes())
    assert all("refId" not in item for item in wire["integrity"])


def test_build_report__to_json_bytes_is_deterministic_and_threshold_is_float() -> None:
    """Hai lần gọi cùng tài liệu ra cùng byte; `confidenceThreshold` là `float` trên dây."""
    layer = SpatialLayer(walls=(_wall("W-WALL0000000001", confidence=0.5),), openings=(), rooms=(), furniture=())
    report = build_report(run_id="run_01", revision=3, layer=layer, level_id=_LEVEL, threshold=Decimal("0.75"))
    first = report.to_json_bytes()
    second = report.to_json_bytes()
    assert first == second
    wire = json.loads(first)
    assert isinstance(wire["confidenceThreshold"], float)
    assert wire["confidenceThreshold"] == 0.75


def test_build_report__missing_reference_issue_marks_critical() -> None:
    """Ô mở trỏ tường không tồn tại → lỗi `critical`, `has_critical` true."""
    opening = Opening(
        id="D-OPEN000000000001",
        wall_id="W-NOPE000000000001",
        kind="door",
        offset_mm=0,
        width_mm=800,
        height_mm=2000,
        sill_height_mm=0,
        swing="left",
        confidence=0.9,
        **_AI,
    )
    layer = SpatialLayer(walls=(), openings=(opening,), rooms=(), furniture=())
    report = build_report(run_id="run_01", revision=1, layer=layer, level_id=_LEVEL, threshold=Decimal("0.7"))
    assert report.has_critical is True
    assert report.issues[0].rule == "missingReference"


def test_build_report__page_unavailable_adds_critical_check_last() -> None:
    """L2-N02: lớp dựng ở chế độ suy giảm (mất raster trang) → kiểm `PAGE_RASTER_UNAVAILABLE` critical, đứng cuối,
    bật `has_critical`; không cờ → không có kiểm này."""
    layer = SpatialLayer(walls=(_wall("W-WALL0000000001", confidence=0.9),), openings=(), rooms=(), furniture=())
    plain, degraded = (
        build_report(
            run_id="run_01", revision=1, layer=layer, level_id=_LEVEL, threshold=Decimal("0.7"), page_unavailable=flag
        )
        for flag in (False, True)
    )
    assert PAGE_RASTER_UNAVAILABLE not in [check.code for check in plain.checks]
    assert degraded.checks[-1].code == PAGE_RASTER_UNAVAILABLE
    assert degraded.checks[-1].critical
    assert degraded.has_critical
    assert json.loads(degraded.to_json_bytes())["checks"][-1] == {
        "code": PAGE_RASTER_UNAVAILABLE,
        "critical": True,
        "detail": {},
    }
