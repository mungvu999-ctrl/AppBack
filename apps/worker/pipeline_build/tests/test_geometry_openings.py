"""Bước 3b và 4 của `build_walls`: nối khe ô mở, gắn ô mở (B5-05 [8] "Ô mở").

Mỗi ca dựng tay `WallPx`/`DetectionPx` với `Scaling(1.0, 1.0)` nên px = mm và số đo đọc thẳng
ra mong đợi; tường đặt ở `y = 1000` để mọi toạ độ không âm.
"""

import math
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np
import pytest

from apps.worker.pipeline_build.geometry import (
    DOOR_KIND_UNREAD,
    Scaling,
    WallSet,
    _bridge_hosts,
    _gap_door,
    build_walls,
    close_envelope_gaps,
)
from apps.worker.pipeline_build.ids import new_spatial_id
from apps.worker.pipeline_build.residual import PageRaster
from apps.worker.pipeline_build.rooms import build_rooms
from apps.worker.pipeline_build.tests.helpers import build_wrapped
from apps.worker.pipeline_build.tests.test_geometry_walls import wall_px
from packages.domain.spatial import Point, Segment
from packages.ml_contracts.artifacts import BoxPx, DetectionPx, WallPx
from packages.ml_contracts.labels import DetectionLabel
from packages.testing.fixtures.clock import FakeClock


def det(label: DetectionLabel, x0: float, y0: float, x1: float, y1: float, confidence: float = 0.8) -> DetectionPx:
    """`DetectionPx` gọn cho test: nhãn và hộp px."""
    return DetectionPx(label=label, box=BoxPx(x_min=x0, y_min=y0, x_max=x1, y_max=y1), confidence=confidence)


def run(walls: list[WallPx], detections: list[DetectionPx], clock: FakeClock, dropped: Counter[str]) -> WallSet:
    """Gọi `build_walls` với `level_id` hợp mẫu W4 và tỉ lệ 1 mm/px."""
    return build_walls(
        walls,
        detections,
        level_id=new_spatial_id("level", clock),
        scaling=Scaling(1.0, 1.0),
        clock=clock,
        dropped=dropped,
    )


def test_build_walls__door_on_wall_gets_offset_and_defaults(fake_clock: FakeClock) -> None:
    """Cửa đi trên tường: 900 x 2200, bậu 0, `left`, `offsetMm` tới mép trái, có trong `openingIds`."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("door", 1000, 950, 1900, 1050)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.kind, opening.width_mm, opening.height_mm, opening.sill_height_mm) == ("door", 900, 2200, 0)
    assert (opening.swing, opening.offset_mm, opening.source, opening.reviewed) == ("left", 1000, "ai", False)
    assert result.walls[0].opening_ids == (opening.id,)
    assert opening.wall_id == result.walls[0].id


def test_build_walls__window_uses_label_defaults(fake_clock: FakeClock) -> None:
    """Cửa sổ: 1200 x 1400, bậu 900, `sliding`, `kind` `window`."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("window", 1000, 950, 2200, 1050)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.kind, opening.width_mm, opening.height_mm) == ("window", 1200, 1400)
    assert (opening.sill_height_mm, opening.swing) == (900, "sliding")


def test_build_walls__double_door_width_from_box(fake_clock: FakeClock) -> None:
    """`double_door` không có rộng mặc định: lấy `js_round(b - a)` của hộp chiếu lên tim."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("double_door", 1000, 950, 2600, 1050)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.kind, opening.width_mm, opening.swing, opening.offset_mm) == ("door", 1600, "double", 1000)


def test_build_walls__narrow_double_door_kept_as_single_door(fake_clock: FakeClock) -> None:
    """Cửa đôi hẹp `b - a < 600` là cửa đơn dò nhầm lớp: giữ thành cửa đơn (rộng mặc định, kẹp trong tường).

    Trước B11 hộp này bị bỏ (`openingUnattached`), căn hộ mất cửa.
    """
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("double_door", 3900, 950, 4100, 1050)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.kind, opening.width_mm, opening.swing, opening.offset_mm) == ("door", 900, "left", 3100)
    assert dropped["openingUnattached"] == 0


def test_build_walls__wide_box_reaches_further(fake_clock: FakeClock) -> None:
    """Hộp cửa sổ rộng 2.000 cách tim 400 mm: quá ½ dày + 150 nhưng ≤ 0,25 x 2.000 → vẫn gắn tường."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("window", 1000, 1400, 3000, 1500)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.kind, opening.width_mm, opening.offset_mm) == ("window", 2000, 1000)
    assert dropped["openingUnattached"] == 0


def test_build_walls__gap_with_door_box_bridged(fake_clock: FakeClock) -> None:
    """Tường 4.000 bị khe cửa 900 chia hai đoạn, khe có hộp → một tường và một cửa (3b-a)."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1550, 1000), wall_px(2450, 1000, 4000, 1000)],
        [det("door", 1550, 950, 2450, 1050)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 1
    assert dropped["wallGapBridged"] == 1
    assert result.input_to_wall == {0: 0, 1: 0}
    (opening,) = result.openings
    assert abs(opening.offset_mm - 1550) <= 20


def test_build_walls__three_segments_reach_fixed_point(fake_clock: FakeClock) -> None:
    """Tường bị **hai** khe cửa chia ba đoạn → một tường (hàng đợi ứng viên lặp tới điểm bất động)."""
    dropped: Counter[str] = Counter()
    result = run(
        [
            wall_px(0, 1000, 1550, 1000),
            wall_px(2450, 1000, 4000, 1000),
            wall_px(4900, 1000, 6500, 1000),
        ],
        [det("door", 1550, 950, 2450, 1050), det("door", 4000, 950, 4900, 1050)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 1
    assert dropped["wallGapBridged"] == 2
    assert len(result.openings) == 2
    assert result.input_to_wall == {0: 0, 1: 0, 2: 0}


def test_build_walls__endpoint_pulled_to_perpendicular_centreline(fake_clock: FakeClock) -> None:
    """3b-b: khe 100 giữa đầu mút và tim vách vuông góc có hộp → kéo đầu mút tới tim vách."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1900, 1000), wall_px(2000, 1000, 2000, 4000)],
        [det("door", 1900, 950, 2000, 1050)],
        fake_clock,
        dropped,
    )
    assert dropped["wallGapBridged"] == 1
    assert result.walls[0].centreline.end == Point(x=2000, y=1000)


def test_build_walls__aligned_gap_without_box_becomes_bridge(fake_clock: FakeClock) -> None:
    """3b-c: cặp thẳng hàng có khe mà không hộp → giữ cả hai, đoạn nối vào `WallSet.bridges`."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 1550, 1000), wall_px(2450, 1000, 4000, 1000)], [], fake_clock, dropped)
    assert len(result.walls) == 2
    assert dropped["wallGapBridged"] == 0
    assert result.bridges == ((Point(x=1550, y=1000), Point(x=2450, y=1000)),)


def test_build_walls__door_near_perpendicular_partition_picks_right_wall(fake_clock: FakeClock) -> None:
    """Cửa 900 cách mặt vách vuông góc 50 mm vẫn gắn vào tường nó nằm trên (tâm hộp gần tim hơn)."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000), wall_px(2000, 1000, 2000, 4000)],
        [det("door", 2100, 950, 3000, 1050)],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert opening.wall_id == result.walls[0].id
    assert result.walls[1].opening_ids == ()


def test_build_walls__box_far_from_wall_unattached(fake_clock: FakeClock) -> None:
    """Hộp cách tim 350 mm > max(`thicknessMm / 2 + OPENING_WALL_REACH_MM`, ¼ x 900) → `openingUnattached`."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("door", 1000, 1350, 1900, 1450)], fake_clock, dropped)
    assert result.openings == ()
    assert dropped["openingUnattached"] == 1


def test_build_walls__short_wall_keeps_door_clamped_to_length(fake_clock: FakeClock) -> None:
    """Tường 800 mm, hộp cửa 1.000 phủ quá hai đầu: rộng = hình chiếu kẹp về 800, không `openingTooWide`."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(100, 1000, 900, 1000)], [det("door", 0, 950, 1000, 1050)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.width_mm, opening.offset_mm) == (800, 0)
    assert dropped["openingTooWide"] == 0


@pytest.mark.parametrize("length", [800, 500, 300])
def test_build_walls__degenerate_projection_clamped_to_short_wall(length: int, fake_clock: FakeClock) -> None:
    """BUG-IT-009: hộp lố đầu tường 250 (< 300, không dựng tường qua khe): chiếu 100 < 300 → mặc định 900 → kẹp về L."""
    dropped: Counter[str] = Counter()
    box = det("door", length - 100, 950, length + 250, 1050)
    result = run([wall_px(0, 1000, length, 1000)], [box], fake_clock, dropped)
    (opening,) = result.openings
    assert len(result.walls) == 1
    assert (opening.width_mm, opening.offset_mm) == (length, 0)
    assert dropped["openingTooWide"] == 0


@pytest.mark.parametrize("length", [800, 250])
def test_build_walls__box_past_wall_end_gets_gap_wall(length: int, fake_clock: FakeClock) -> None:
    """OPEN-ATTACH: hộp lố đầu tường 900 → tường mới chỉ trên phần lố `[L, L + 900]`, cửa 900 trên đó, không chồng."""
    dropped: Counter[str] = Counter()
    box = det("door", length - 100, 950, length + 900, 1050)
    result = run([wall_px(0, 1000, length, 1000)], [box], fake_clock, dropped)
    gap = result.walls[1]
    (opening,) = result.openings
    assert (gap.centreline.start, gap.centreline.end) == (Point(x=length, y=1000), Point(x=length + 900, y=1000))
    assert (opening.wall_id, opening.width_mm, opening.offset_mm) == (gap.id, 900, 0)
    assert dropped["openingTooWide"] == 0


def test_build_walls__clamped_opening_stays_inside_diagonal_wall(fake_clock: FakeClock) -> None:
    """Review 2 P2: tường chéo L ≈ 476,6 → rộng kẹp 476; `offsetMm` trần `floor(L - 476) = 0` để không lố đầu tường."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 0, 337, 337)], [det("door", 300, 300, 700, 700)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.width_mm, opening.offset_mm) == (476, 0)
    assert opening.offset_mm + opening.width_mm <= math.hypot(337, 337)


def test_build_walls__wall_under_min_width_too_wide(fake_clock: FakeClock) -> None:
    """Tường 250 < `OPENING_MIN_WIDTH_MM`, hộp lố 250 (không dựng tường qua khe): không đủ chỗ → `openingTooWide`."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 250, 1000)], [det("door", 150, 950, 500, 1050)], fake_clock, dropped)
    assert result.openings == ()
    assert dropped["openingTooWide"] == 1


def test_build_walls__clamped_openings_on_short_wall_overlap(fake_clock: FakeClock) -> None:
    """Hai hộp suy biến ở hai đầu tường 500: cả hai kẹp về 500 → chồng nhau, giữ `confidence` cao, `openingOverlap`."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(1000, 1000, 1500, 1000)],
        [det("door", 1400, 950, 1650, 1050, 0.7), det("door", 850, 950, 1100, 1050, 0.9)],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert (opening.width_mm, opening.offset_mm) == (500, 0)
    assert dropped["openingOverlap"] == 1
    assert dropped["openingTooWide"] == 0


def test_build_walls__width_from_box_projection(fake_clock: FakeClock) -> None:
    """Rộng ô mở lấy từ hộp chiếu lên tim, không phải `LABEL_TARGETS`: cửa 800, cửa sổ 600."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000)],
        [det("door", 1000, 950, 1800, 1050), det("window", 2500, 950, 3100, 1050)],
        fake_clock,
        dropped,
    )
    door, window = result.openings
    assert (door.width_mm, door.offset_mm, door.height_mm) == (800, 1000, 2200)
    assert (window.width_mm, window.offset_mm, window.sill_height_mm) == (600, 2500, 900)


def test_build_walls__adjacent_windows_do_not_overlap(fake_clock: FakeClock) -> None:
    """Hai cửa sổ 600 cách nhau 100 mm: rộng theo hộp nên không chồng (trước đây 1.200 mỗi cái → `openingOverlap`)."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000)],
        [det("window", 1000, 950, 1600, 1050), det("window", 1700, 950, 2300, 1050)],
        fake_clock,
        dropped,
    )
    assert [(o.offset_mm, o.width_mm) for o in result.openings] == [(1000, 600), (1700, 600)]
    assert dropped["openingOverlap"] == 0


@pytest.mark.parametrize(
    ("cross", "expected_cross"),
    [
        ((3855, 2900, 7000, 2900), (Point(x=3826, y=2900), Point(x=7000, y=2900))),
        ((1000, 2900, 3797, 2900), (Point(x=1000, y=2900), Point(x=3826, y=2900))),
    ],
)
def test_build_walls__corner_gap_closed_when_crossing_just_outside(
    cross: tuple[float, float, float, float], expected_cross: tuple[Point, Point], fake_clock: FakeClock
) -> None:
    """BUG-IT-001 (G2): chỗ giao lố đầu vách ngang 29 mm (≤ 150) → kéo tường tới chân, vách kéo theo, cửa gắn."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(3826, 0, 3826, 2000), wall_px(*cross)],
        [det("door", 3776, 2000, 3876, 2900)],
        fake_clock,
        dropped,
    )
    assert dropped["wallGapBridged"] == 1
    assert result.walls[0].centreline.end == Point(x=3826, y=2900)
    assert (result.walls[1].centreline.start, result.walls[1].centreline.end) == expected_cross
    (opening,) = result.openings
    assert (opening.wall_id, opening.offset_mm, opening.width_mm) == (result.walls[0].id, 2000, 900)


def test_build_walls__crossing_far_outside_cross_wall_not_pulled(fake_clock: FakeClock) -> None:
    """Chỗ giao cách đầu vách ngang 274 mm > `max(bề dày, 150)` → không kéo, cả hai tường giữ nguyên."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(3826, 0, 3826, 2000), wall_px(4100, 2900, 7000, 2900)],
        [det("door", 3776, 2000, 3876, 2900)],
        fake_clock,
        dropped,
    )
    assert dropped["wallGapBridged"] == 0
    assert result.walls[0].centreline.end == Point(x=3826, y=2000)
    assert result.walls[1].centreline.start == Point(x=4100, y=2900)


def test_build_walls__diagonal_wall_attaches_axis_aligned_box(fake_clock: FakeClock) -> None:
    """BUG-IT-003: tường 45°, hộp thẳng trục có tâm cách tim 354 mm > 200 nhưng tim cắt hộp → gắn.

    Hình chiếu 4 góc lên tim chéo: `(5400 - 3600) / √2 ≈ 1272,8`, trừ phần bề dày chiếu thêm `100 · |sin 90°|`
    → rộng 1173; `offsetMm` = tâm hình chiếu 3181,9 - 586,5 ≈ 2595.
    """
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 0, 4000, 4000)], [det("door", 1700, 1900, 2300, 3100)], fake_clock, dropped)
    (opening,) = result.openings
    assert (opening.wall_id, opening.width_mm, opening.offset_mm) == (result.walls[0].id, 1173, 2595)
    assert dropped["openingUnattached"] == 0


def test_build_walls__diagonal_adjacent_windows_keep_true_width(fake_clock: FakeClock) -> None:
    """Review P2 LOG-02: hai cửa sổ thật 900 cách 100 mm trên tường 45° (bề dày 100).

    Hộp thẳng trục của mỗi cửa sổ là tâm ± 500/√2; hình chiếu dài 1.000 = 900 + bề dày → trừ lại còn 900,
    nên không chồng nhau (trước đó 1.000 mỗi cái → cái sau `openingOverlap`).
    """
    half = 500 / math.sqrt(2)
    centres = [2000 / math.sqrt(2), 3000 / math.sqrt(2)]
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 0, 4000, 4000)],
        [det("window", c - half, c - half, c + half, c + half) for c in centres],
        fake_clock,
        dropped,
    )
    assert [(o.offset_mm, o.width_mm) for o in result.openings] == [(1550, 900), (2550, 900)]
    assert dropped["openingOverlap"] == 0


def test_build_walls__corner_box_touching_two_walls_picks_centre_nearest(fake_clock: FakeClock) -> None:
    """Hộp cửa sát góc chạm cả vách vuông góc (đứng trước): cùng khoảng cách hộp 0 → chọn tường gần tâm."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(2000, 1000, 2000, 4000), wall_px(0, 1000, 4000, 1000)],
        [det("door", 2000, 950, 2900, 1050)],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert (opening.wall_id, opening.offset_mm, opening.width_mm) == (result.walls[1].id, 2000, 900)
    assert result.walls[0].opening_ids == ()


@pytest.mark.parametrize(
    ("stub_end_y", "box_y1"),
    [(1250, 1250), (1400, 1100)],
    ids=["stub_under_min_width", "stub_across_box"],
)
def test_build_walls__door_skips_jamb_stub_nearer_centre(
    stub_end_y: float, box_y1: float, fake_clock: FakeClock
) -> None:
    """OPEN-ATTACH: mẩu má cửa vuông góc gần tâm hộp hơn (ngắn < 300 hoặc chiếu chỉ 100 mm) → bỏ qua, gắn tường dài."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000), wall_px(1500, 1000, 1500, stub_end_y)],
        [det("door", 1100, 1000, 1900, box_y1)],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert (opening.wall_id, opening.offset_mm, opening.width_mm) == (result.walls[0].id, 1100, 800)
    assert dropped["openingTooWide"] == 0


def test_build_walls__short_box_off_axis_falls_back_to_nearest(fake_clock: FakeClock) -> None:
    """OPEN-ATTACH: hộp đứng 100 x 250 (< 300, không dựng tường qua khe), không tường nằm dọc → tường gần tâm nhất."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("door", 1000, 1000, 1100, 1250)], fake_clock, dropped)
    (opening,) = result.openings
    assert len(result.walls) == 1
    assert opening.wall_id == result.walls[0].id


def test_build_walls__door_between_perpendicular_walls_gets_gap_wall(fake_clock: FakeClock) -> None:
    """OPEN-ATTACH bước 3c: khe chỉ có tường vuông góc → tường mới qua tim hộp, bề dày tường gần tâm, cửa rộng 900."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(1000, 0, 1000, 2000, 200), wall_px(1900, 0, 1900, 2000)],
        [det("door", 1000, 950, 1900, 1050)],
        fake_clock,
        dropped,
    )
    gap = result.walls[2]
    (opening,) = result.openings
    assert (gap.centreline.start, gap.centreline.end) == (Point(x=1000, y=1000), Point(x=1900, y=1000))
    assert gap.thickness_mm == result.walls[0].thickness_mm
    assert (opening.wall_id, opening.offset_mm, opening.width_mm) == (gap.id, 0, 900)
    assert 2 not in result.input_to_wall.values()
    assert dropped["openingTooWide"] == 0


def test_build_walls__overlapping_gap_boxes_share_one_wall(fake_clock: FakeClock) -> None:
    """OPEN-ATTACH: hai hộp trùng một khe đứng → một tường qua khe, ô mở thứ hai rơi `openingOverlap`."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 2000, 1000), wall_px(0, 1900, 2000, 1900)],
        [det("door", 950, 1000, 1050, 1900, 0.9), det("door", 960, 1010, 1060, 1880, 0.6)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 3
    (opening,) = result.openings
    assert opening.wall_id == result.walls[2].id
    assert dropped["openingOverlap"] == 1


def test_build_walls__overlapping_openings_keep_higher_confidence(fake_clock: FakeClock) -> None:
    """Hai hộp chồng khoảng trên cùng tường → giữ `confidence` cao hơn, còn lại `openingOverlap`."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000)],
        [
            det("door", 1000, 950, 1900, 1050, 0.6),
            det("door", 1100, 950, 2000, 1050, 0.9),
            det("door", 1200, 950, 2100, 1050, 0.5),
        ],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert opening.confidence == 0.9
    assert dropped["openingOverlap"] == 2


def test_build_walls__furniture_labels_ignored_without_counting(fake_clock: FakeClock) -> None:
    """Nhãn đồ đạc không thành ô mở và **không** vào `dropped` (việc của `build_furniture`)."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 4000, 1000)], [det("bed", 1000, 950, 1900, 1050)], fake_clock, dropped)
    assert result.openings == ()
    assert dropped == Counter()


def test_build_walls__openings_without_walls_all_unattached(fake_clock: FakeClock) -> None:
    """Không tường nào còn lại: mọi hộp ô mở thành `openingUnattached`, không ném."""
    dropped: Counter[str] = Counter()
    result = run([], [det("door", 1000, 950, 1900, 1050)], fake_clock, dropped)
    assert (result.walls, result.openings) == ((), ())
    assert dropped["openingUnattached"] == 1


def test_build_walls__gap_crossed_by_other_wall_not_merged(fake_clock: FakeClock) -> None:
    """Khe cắt tim vách vuông góc → không gộp; hai đầu được kéo tới tim vách đó (3b-a chặn, 3b-b nhận)."""
    dropped: Counter[str] = Counter()
    result = run(
        [
            wall_px(0, 1000, 1550, 1000),
            wall_px(2450, 1000, 4000, 1000),
            wall_px(2000, 500, 2000, 1500),
        ],
        [det("door", 1550, 950, 2450, 1050)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 3
    assert dropped["wallGapBridged"] == 2
    assert result.walls[0].centreline.end == Point(x=2000, y=1000)
    assert result.walls[1].centreline.start == Point(x=2000, y=1000)


def test_build_walls__perpendicular_gap_without_box_not_pulled(fake_clock: FakeClock) -> None:
    """3b-b chỉ kéo khi khe có hộp: không hộp thì đầu mút giữ nguyên."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 1900, 1000), wall_px(2000, 1000, 2000, 4000)], [], fake_clock, dropped)
    assert dropped["wallGapBridged"] == 0
    assert result.walls[0].centreline.end == Point(x=1900, y=1000)


def test_build_walls__perpendicular_wall_too_short_not_pulled(fake_clock: FakeClock) -> None:
    """Chân vuông góc rơi ngoài đoạn vách (vách không vươn tới) → không kéo đầu mút."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(0, 1000, 1550, 1000), wall_px(2000, 2000, 2000, 3000)], [], fake_clock, dropped)
    assert dropped["wallGapBridged"] == 0
    assert result.walls[0].centreline.end == Point(x=1550, y=1000)


def test_build_walls__equidistant_walls_pick_lowest_index(fake_clock: FakeClock) -> None:
    """Tâm hộp cách hai tường song song đúng bằng nhau → hoà lấy tường đứng trước."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000), wall_px(0, 1400, 4000, 1400)],
        [det("door", 1000, 1150, 1900, 1250)],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert opening.wall_id == result.walls[0].id
    assert result.walls[1].opening_ids == ()


def _on_diagonal(t: float, angle_deg: float, length: float) -> tuple[float, float, float, float]:
    """Tường dài `length` theo góc `angle_deg`, bắt đầu tại điểm cách (1000, 1000) một đoạn `t` trên đường 45°."""
    x0, y0 = 1000 + t / math.sqrt(2), 1000 + t / math.sqrt(2)
    rad = math.radians(angle_deg)
    return (x0, y0, x0 + length * math.cos(rad), y0 + length * math.sin(rad))


def _reversed(wall: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Đảo chiều tim tường (đầu ↔ cuối)."""
    return (wall[2], wall[3], wall[0], wall[1])


@pytest.mark.parametrize(("label", "gap", "flip"), [("door", 900, False), ("window", 1200, False), ("door", 900, True)])
def test_build_walls__diagonal_noisy_gap_joined(
    label: DetectionLabel, gap: int, flip: bool, fake_clock: FakeClock
) -> None:
    """BUG-IT-007 (G5 xoay 45°): hai tường 43° và 47° hai bên khe ô mở trên đường 45° → gộp một tường.

    Lệch nhau 4° > 2° nên trước đây không gộp; hai đầu mút gần cùng nằm trên đường 45° (lệch ngang 0
    theo hướng trung bình). Hộp thẳng trục của ô mở rộng `gap`, bề dày 100: nửa cạnh `(gap/2 + 50)/√2`;
    rộng ô mở ≈ `gap` (hình chiếu trừ phần bề dày), `flip` = tường sau vẽ ngược chiều.
    """
    a = _reversed(_on_diagonal(1500, 43 + 180, 1500))
    b = _on_diagonal(1500 + gap, 47, 1500)
    centre = 1000 + (1500 + gap / 2) / math.sqrt(2)
    half = (gap / 2 + 50) / math.sqrt(2)
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(*a), wall_px(*(_reversed(b) if flip else b))],
        [det(label, centre - half, centre - half, centre + half, centre + half)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 1
    assert dropped["wallGapBridged"] == 1
    (opening,) = result.openings
    assert abs(opening.width_mm - gap) <= 30
    assert abs(opening.offset_mm - 1500) <= 30
    assert dropped["openingTooWide"] == 0


def test_build_walls__orthogonal_three_degrees_not_joined(fake_clock: FakeClock) -> None:
    """Bản vẽ thẳng trục giữ 2° của B5-05 [6] 3b: tường 0° và 3° có hộp cửa ở khe → không gộp; cửa nằm trên tường
    qua khe của bước 3c (OPEN-ATTACH), hai tường cũ giữ nguyên."""
    rad = math.radians(3)
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1550, 1000), wall_px(2450, 1000, 2450 + 1550 * math.cos(rad), 1000 + 1550 * math.sin(rad))],
        [det("door", 1550, 950, 2450, 1050)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 3
    assert result.walls[0].centreline.end == Point(x=1550, y=1000)
    assert result.walls[2].opening_ids == tuple(opening.id for opening in result.openings)
    assert dropped["wallGapBridged"] == 0


def test_build_walls__diagonal_side_shifted_gap_not_joined(fake_clock: FakeClock) -> None:
    """Hai tường 45° song song lệch ngang 200 mm (> ½ bề dày) qua khe có hộp → không gộp, không đoạn nối."""
    shift = 200 / math.sqrt(2)
    b = _on_diagonal(2400, 45, 1500)
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(*_on_diagonal(0, 45, 1500)), wall_px(b[0] + shift, b[1] - shift, b[2] + shift, b[3] - shift)],
        [det("door", 1700, 1700, 2500, 2500)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 2
    assert dropped["wallGapBridged"] == 0
    assert result.bridges == ()


def test_build_walls__diagonal_touching_walls_no_bridge(fake_clock: FakeClock) -> None:
    """Hai tường 45° nối đầu chạm đầu (khe 0): không phải cặp khe → không gộp, không đoạn nối."""
    dropped: Counter[str] = Counter()
    result = run([wall_px(1000, 1000, 2000, 2000), wall_px(2000, 2000, 3000, 3000)], [], fake_clock, dropped)
    assert len(result.walls) == 2
    assert result.bridges == ()


def test_build_walls__diagonal_endpoint_pulled_to_near_perpendicular(fake_clock: FakeClock) -> None:
    """3b-b chéo: tường 42° hụt 100 mm trước vách 135° (lệch 93°, ngoài 2° nhưng trong 5°) có hộp → kéo tới tim vách."""
    rad = math.radians(42)
    end = (3000 - 100 * math.cos(rad), 3000 - 100 * math.sin(rad))
    start = (end[0] - 2000 * math.cos(rad), end[1] - 2000 * math.sin(rad))
    reach = 1500 / math.sqrt(2)
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(*start, *end), wall_px(3000 + reach, 3000 - reach, 3000 - reach, 3000 + reach)],
        [det("door", 2900, 2900, 3050, 3050)],
        fake_clock,
        dropped,
    )
    assert dropped["wallGapBridged"] == 1
    pulled = result.walls[0].centreline.end
    assert abs(pulled.x - 3000) <= 2
    assert abs(pulled.y - 3000) <= 2


def test_build_walls__gap_merge_keeps_the_longer_walls_line(fake_clock: FakeClock) -> None:
    """UX41-A: hai đoạn lệch tim 30 mm qua khe cửa → tường gộp nằm trên tim đoạn dài hơn, không nghiêng."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1500, 1000), wall_px(2400, 1030, 4500, 1030)],
        [det("door", 1500, 950, 2400, 1080)],
        fake_clock,
        dropped,
    )
    (wall,) = result.walls
    assert (wall.centreline.start, wall.centreline.end) == (Point(x=0, y=1030), Point(x=4500, y=1030))


def test_build_walls__gap_merge_takes_the_longer_walls_thickness(fake_clock: FakeClock) -> None:
    """Vách 2,8 m dày 100 nối qua khe cửa với mẩu 500 dày 200 → tường gộp dày 100, không bị nâng lên 200."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 2800, 1000, 100.0), wall_px(3700, 1000, 4200, 1000, 200.0)],
        [det("door", 2800, 900, 3700, 1100)],
        fake_clock,
        dropped,
    )
    (wall,) = result.walls
    assert (wall.centreline.start.x, wall.centreline.end.x, wall.thickness_mm) == (0, 4200, 100)


def test_build_walls__dangling_end_with_door_in_800_gap_reaches_wall(fake_clock: FakeClock) -> None:
    """3b-d: khe 800 mm có tâm cửa (hộp hẹp, không phủ ½ khe) → tường kéo tới tim vách, cửa nằm trên nó."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1150, 1000), wall_px(2000, 0, 2000, 3000)],
        [det("door", 1500, 950, 1700, 1050)],
        fake_clock,
        dropped,
    )
    assert result.walls[0].centreline.end == Point(x=2000, y=1000)
    assert len(result.openings) == 1
    assert result.openings[0].wall_id == result.walls[0].id


def test_build_walls__dangling_end_without_door_in_800_gap_joins_thick_wall(fake_clock: FakeClock) -> None:
    """3b-d: khe 800 mm không cửa, tường 300 (trần 3 x 300 = 900) → kéo tới chạm vách."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1150, 1000, thickness_px=300), wall_px(2000, 0, 2000, 3000)], [], fake_clock, dropped
    )
    assert result.walls[0].centreline.end == Point(x=2000, y=1000)
    assert dropped["wallGapBridged"] == 1


def test_build_walls__dangling_end_2m_gap_kept(fake_clock: FakeClock) -> None:
    """3b-d: khe 2 m (kể cả có tâm cửa) vượt mọi trần → giữ nguyên đầu tường."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1000, 1000), wall_px(3050, 0, 3050, 3000)],
        [det("door", 1900, 950, 2100, 1050)],
        fake_clock,
        dropped,
    )
    assert result.walls[0].centreline.end == Point(x=1000, y=1000)


def test_build_walls__dangling_end_not_pulled_through_other_wall(fake_clock: FakeClock) -> None:
    """3b-d: tường thẳng hàng nằm trong khe tới vách → không kéo xuyên, giữ đầu tường."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 1500, 1000), wall_px(1650, 1000, 1850, 1000), wall_px(2000, 0, 2000, 3000)],
        [],
        fake_clock,
        dropped,
    )
    assert result.walls[0].centreline.end == Point(x=1500, y=1000)


def test_build_walls__short_stub_hugging_thick_wall_dropped(fake_clock: FakeClock) -> None:
    """3b-e (H-04 #W-013): mẩu 300 mm song song, mặt cách tường 300 là 100 mm, một đầu tự do → bỏ."""
    dropped: Counter[str] = Counter()
    result = run(
        [
            wall_px(0, 1000, 6000, 1000, thickness_px=300),
            wall_px(3000, 1000, 3000, 3000, thickness_px=200),
            wall_px(2700, 1300, 3000, 1300, thickness_px=100),
        ],
        [],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 2
    assert 2 not in result.input_to_wall


def test_build_walls__hugging_stub_with_opening_or_long_kept(fake_clock: FakeClock) -> None:
    """3b-e: mẩu mang hộp cửa sổ, hay dài 900 mm → giữ (có thể là bậu/tường thật)."""
    walls = [wall_px(0, 1000, 6000, 1000, thickness_px=300), wall_px(2700, 1300, 3000, 1300, thickness_px=100)]
    kept = run(walls, [det("window", 2750, 1250, 2950, 1350)], fake_clock, Counter())
    long = run([walls[0], wall_px(2100, 1300, 3000, 1300, thickness_px=100)], [], fake_clock, Counter())
    assert len(kept.walls) == 2
    assert len(long.walls) == 2


def test_build_walls__hugging_stub_closed_both_ends_or_thin_host_kept(fake_clock: FakeClock) -> None:
    """3b-e: mẩu có hai đầu chạm tường thứ ba, hay tường chủ không dày hơn mẩu → giữ."""
    host = wall_px(0, 1000, 6000, 1000, thickness_px=300)
    stub = wall_px(2700, 1300, 3000, 1300, thickness_px=100)
    closed = run(
        [
            host,
            wall_px(3000, 1000, 3000, 3000, thickness_px=200),
            wall_px(2700, 1000, 2700, 3000, thickness_px=200),
            stub,
        ],
        [],
        fake_clock,
        Counter(),
    )
    thin = run(
        [wall_px(0, 1000, 6000, 1000, thickness_px=100), wall_px(2700, 1200, 3000, 1200, thickness_px=100)],
        [],
        fake_clock,
        Counter(),
    )
    assert len(closed.walls) == 4
    assert len(thin.walls) == 2


def test_build_walls__gap_wall_keeps_narrow_double_door(fake_clock: FakeClock) -> None:
    """Cửa đôi 500 < 600 trên tường qua khe: nay giữ thành cửa đơn (B11) nên tường qua khe mang cửa, không bị bỏ.

    Trước B11 (OPEN-ATTACH review P2) cửa rơi `openingUnattached` và tường qua khe bị bỏ để không bịt lối.
    """
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(1000, 0, 1000, 2000), wall_px(1500, 0, 1500, 2000)],
        [det("double_door", 1000, 950, 1500, 1050)],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 3
    (opening,) = result.openings
    assert (opening.kind, opening.swing) == ("door", "left")
    assert dropped["openingUnattached"] == 0


def test_build_layer__rooms_ignore_gap_walls() -> None:
    """OPEN-ATTACH (P3): vách lệch 120 mm hai bên khe cửa → tường qua khe mang cửa, `_door_closers` chia hai phòng
    6 m². Dựng phòng cả tường qua khe (không lọc) thì tường lơ lửng đó chặn đường khép → một phòng 12 m²."""
    room = [
        wall_px(0, 0, 400, 0, 10),
        wall_px(400, 0, 400, 300, 10),
        wall_px(400, 300, 0, 300, 10),
        wall_px(0, 300, 0, 0, 10),
        wall_px(200, 0, 200, 130, 10),
        wall_px(212, 230, 212, 300, 10),
    ]
    built = build_wrapped(
        walls=room,
        detections=[det("door", 194, 130, 218, 230)],
        width_px=400,
        height_px=300,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    (opening,) = built.layer.openings
    gap = next(wall for wall in built.layer.walls if wall.id == opening.wall_id)
    assert gap.centreline.start.y == 1300
    assert all(gap.id not in room.wall_ids for room in built.layer.rooms)
    assert sorted(room.area_m2 for room in built.layer.rooms) == [6.0, 6.0]


@pytest.mark.parametrize("thickness_px", [pytest.param(20.0, id="hairline"), pytest.param(60.0, id="inside-body")])
def test_build_walls__glazing_stroke_inside_thick_wall_dropped(thickness_px: float, fake_clock: FakeClock) -> None:
    """C2 (syn-can-ho-150): nét kính 1,3 m nằm trong thân tường 220 mang hộp cửa sổ → bỏ, cửa sổ về tường chủ."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000, thickness_px=220), wall_px(1500, 1040, 2800, 1040, thickness_px=thickness_px)],
        [det("window", 1500, 900, 2800, 1100)],
        fake_clock,
        dropped,
    )
    (wall,) = result.walls
    (opening,) = result.openings
    assert (wall.thickness_mm, opening.wall_id, result.input_to_wall) == (220, wall.id, {0: 0})
    assert dropped["isolatedBlock"] == 1


def test_build_walls__thinner_wall_beside_thick_wall_body_kept(fake_clock: FakeClock) -> None:
    """C2: vách 100 song song ngoài thân tường 220 (tim cách 200 > 110), hay nối tiếp quá đầu tường → giữ."""
    dropped: Counter[str] = Counter()
    result = run(
        [
            wall_px(0, 1000, 4000, 1000, thickness_px=220),
            wall_px(1500, 1200, 2800, 1200),
            wall_px(4000, 1000, 6000, 1000),
        ],
        [],
        fake_clock,
        dropped,
    )
    assert len(result.walls) == 3


def test_build_walls__window_split_20mm_apart_merges_into_one(fake_clock: FakeClock) -> None:
    """C4 (cn-10/cn-11): cửa sổ 1400 + 600 cùng tường cách 20 mm là một cửa sổ bị cắt đôi → một khoảng 2020."""
    dropped: Counter[str] = Counter()
    result = run(
        [wall_px(0, 1000, 4000, 1000)],
        [det("window", 1000, 950, 2400, 1050, 0.6), det("window", 2420, 950, 3020, 1050, 0.9)],
        fake_clock,
        dropped,
    )
    (opening,) = result.openings
    assert (opening.kind, opening.offset_mm, opening.width_mm, opening.confidence) == ("window", 1000, 2020, 0.9)
    assert dropped["openingOverlap"] == 1


def _door_page(width_px: int, *, arc: bool) -> PageRaster:
    """Trang 10 mm/px: tường ngang y=100 dày 10 px có khe [130, 130 + rộng], cánh vuông tường + cung 1/4 (nếu `arc`)."""
    gray = np.full((300, 400), 255, np.uint8)
    walls = np.zeros((300, 400), np.uint8)
    cv2.line(walls, (0, 100), (130, 100), 1, 10)
    cv2.line(walls, (130 + width_px, 100), (399, 100), 1, 10)
    gray[walls > 0] = 60
    cv2.line(gray, (130, 105), (130, 105 + width_px), 0, 2)
    if arc:
        pts = [(130 + width_px * math.cos(q), 105 + width_px * math.sin(q)) for q in np.linspace(0, math.pi / 2, 60)]
        cv2.polylines(gray, [np.rint(pts).astype(np.int32)], False, 0, 1)
    return PageRaster(gray=gray, walls=walls.astype(bool))


@pytest.mark.parametrize(
    ("width_px", "arc", "swing"),
    [
        pytest.param(160, False, "sliding", id="wide-no-arc"),
        pytest.param(140, True, "double", id="1.4m-with-arc"),
        pytest.param(140, False, "left", id="1.4m-no-arc"),
        pytest.param(90, True, "left", id="single-leaf"),
    ],
)
def test_build_walls__door_swing_from_width_and_arc(
    width_px: int, arc: bool, swing: str, fake_clock: FakeClock
) -> None:
    """C3: cửa đi không cung mà rộng ≥ 1,5 m → lùa; có cung mà rộng > 1,2 m → hai cánh; còn lại cánh đơn."""
    result = build_walls(
        [wall_px(0, 100, 400, 100, thickness_px=10)],
        [det("door", 130, 90, 130 + width_px, 110)],
        level_id=new_spatial_id("level", fake_clock),
        scaling=Scaling(10.0, 10.0),
        clock=fake_clock,
        dropped=Counter(),
        page=_door_page(width_px, arc=arc),
    )
    (opening,) = result.openings
    assert (opening.width_mm, opening.swing, opening.door_style) == (width_px * 10, swing, None)


def _kind_page(width_px: int, kind: str) -> PageRaster:
    """Trang 10 mm/px như `_door_page`, khe [130, 130 + rộng]: `double` = hai cánh rộng/2 + hai cung về tâm khe;
    `sliding` = hai tấm mảnh song song tường trong bề dày tường, so le, mỗi tấm 60 % khe từ một đầu."""
    gray = np.full((300, 400), 255, np.uint8)
    walls = np.zeros((300, 400), np.uint8)
    cv2.line(walls, (0, 100), (130, 100), 1, 10)
    cv2.line(walls, (130 + width_px, 100), (399, 100), 1, 10)
    gray[walls > 0] = 60
    half = width_px / 2
    if kind == "double":
        for hinge, toward in ((130, 1), (130 + width_px, -1)):
            cv2.line(gray, (hinge, 105), (hinge, round(105 + half)), 0, 2)
            pts = [
                (hinge + toward * half * math.cos(q), 105 + half * math.sin(q)) for q in np.linspace(0, math.pi / 2, 60)
            ]
            cv2.polylines(gray, [np.rint(pts).astype(np.int32)], False, 0, 1)
    else:
        cv2.line(gray, (130, 98), (round(130 + 0.6 * width_px), 98), 0, 1)
        cv2.line(gray, (round(130 + 0.4 * width_px), 102), (130 + width_px, 102), 0, 1)
    return PageRaster(gray=gray, walls=walls.astype(bool))


@pytest.mark.parametrize(
    ("width_px", "kind", "swing"),
    [
        pytest.param(120, "double", "double", id="double-1.2m"),
        pytest.param(180, "double", "double", id="double-1.8m"),
        pytest.param(120, "sliding", "sliding", id="sliding-1.2m"),
    ],
)
def test_build_walls__door_kind_from_symbol(width_px: int, kind: str, swing: str, fake_clock: FakeClock) -> None:
    """K-13a: ký hiệu hai cánh → `double` kể cả khi rộng ≤ 1,2 m; ray so le → `sliding` kể cả khi hẹp hơn 1,5 m."""
    dropped: Counter[str] = Counter()
    result = build_walls(
        [wall_px(0, 100, 400, 100, thickness_px=10)],
        [det("door", 130, 90, 130 + width_px, 110)],
        level_id=new_spatial_id("level", fake_clock),
        scaling=Scaling(10.0, 10.0),
        clock=fake_clock,
        dropped=dropped,
        page=_kind_page(width_px, kind),
    )
    (opening,) = result.openings
    assert (opening.width_mm, opening.swing) == (width_px * 10, swing)
    assert dropped[DOOR_KIND_UNREAD] == 0


def test_build_walls__wide_unread_door_counted(fake_clock: FakeClock) -> None:
    """K-13a: cửa 1,4 m không đọc được kiểu → giữ `left` (C3) và đếm `doorKindUnread`; cửa 0,9 m không đếm."""
    dropped: Counter[str] = Counter()
    build_walls(
        [wall_px(0, 100, 400, 100, thickness_px=10)],
        [det("door", 130, 90, 270, 110)],
        level_id=new_spatial_id("level", fake_clock),
        scaling=Scaling(10.0, 10.0),
        clock=fake_clock,
        dropped=dropped,
        page=_door_page(140, arc=False),
    )
    assert dropped[DOOR_KIND_UNREAD] == 1
    narrow: Counter[str] = Counter()
    run([wall_px(0, 1000, 4000, 1000)], [det("door", 1000, 950, 1900, 1050)], fake_clock, narrow)
    assert narrow[DOOR_KIND_UNREAD] == 0


@pytest.mark.parametrize(("kind", "swing", "leaf"), [("double", "double", (0.0, 1.0)), ("sliding", "sliding", None)])
def test_gap_door__reads_kind(kind: str, swing: str, leaf: tuple[float, float] | None) -> None:
    """K-13a: cửa đoán trên khe (C5) dùng cùng kiểu đọc được: hai cánh → `double` có hướng cánh, ray → `sliding`."""
    got = _gap_door(Point(x=1300, y=1000), Point(x=2500, y=1000), 100, _kind_page(120, kind), Scaling(10.0, 10.0))
    assert got[0] == swing
    assert got[1] == 0.5
    assert got[2] == leaf


def test_build_walls__wide_door_without_page_is_sliding(fake_clock: FakeClock) -> None:
    """C3: không có trang để đọc cung, cửa đi 1,6 m vẫn thành cửa lùa."""
    result = run([wall_px(0, 1000, 4000, 1000)], [det("door", 1000, 950, 2600, 1050)], fake_clock, Counter())
    assert result.openings[0].swing == "sliding"


def _closed(walls: list[WallPx], clock: FakeClock, scaling: Scaling, page: PageRaster | None = None) -> WallSet:
    """`build_walls` → phòng (qua `bridges`) → `close_envelope_gaps`, như `build._assembled`."""
    shared: dict[str, Any] = {"level_id": new_spatial_id("level", clock), "scaling": scaling, "clock": clock}
    wall_set = build_walls(walls, [], dropped=Counter(), page=page, **shared)
    rooms = build_rooms(wall_set.walls, wall_set.bridges, (), dropped=Counter(), **shared)
    # Phòng ở: khe ngoài vẫn đoán cửa đi (R2' chỉ bỏ cửa đoán vào lô gia/kỹ thuật/khác).
    rooms = tuple(room.model_copy(update={"usage": "bedroom"}) for room in rooms)
    return close_envelope_gaps(wall_set, rooms, page=page, **shared)


def _box_with_top_gap(gap_from: int, gap_to: int, *, scale: float = 1.0) -> list[WallPx]:
    """Phòng 6 x 4 m (đơn vị px = mm / `scale`), tường trên đứt khe `[gap_from, gap_to]`."""
    k = 1 / scale
    return [
        wall_px(0, 0, gap_from * k, 0),
        wall_px(gap_to * k, 0, 6000 * k, 0),
        wall_px(6000 * k, 0, 6000 * k, 4000 * k),
        wall_px(6000 * k, 4000 * k, 0, 4000 * k),
        wall_px(0, 4000 * k, 0, 0),
    ]


@pytest.mark.parametrize(
    ("gap_to", "swing", "confidence"),
    [pytest.param(2900, "left", 0.3, id="900-guessed"), pytest.param(3600, "sliding", 0.5, id="1600-sliding")],
)
def test_close_envelope_gaps__exterior_gap_becomes_wall_with_door(
    gap_to: int, swing: str, confidence: float, fake_clock: FakeClock
) -> None:
    """C5: khe 900/1600 trên tường ngoài, không hộp cửa → tường qua khe mang cửa đi phủ cả khe."""
    result = _closed(_box_with_top_gap(2000, gap_to), fake_clock, Scaling(1.0, 1.0))
    gap = result.walls[-1]
    (opening,) = result.openings
    assert (gap.centreline.start, gap.centreline.end) == (Point(x=2000, y=0), Point(x=gap_to, y=0))
    assert gap.opening_ids == (opening.id,)
    assert (opening.offset_mm, opening.width_mm, opening.swing, opening.confidence) == (
        0,
        gap_to - 2000,
        swing,
        confidence,
    )
    assert len(result.walls) == 6


def test_close_envelope_gaps__narrow_exterior_gap_extends_a_host(fake_clock: FakeClock) -> None:
    """C5: khe 500 < 600 trên tường ngoài → tường có cuối ở khe kéo tới đầu kia, không thêm tường, không ô mở."""
    result = _closed(_box_with_top_gap(2000, 2500), fake_clock, Scaling(1.0, 1.0))
    assert len(result.walls) == 5
    assert (result.walls[0].centreline.start, result.walls[0].centreline.end) == (Point(x=0, y=0), Point(x=2500, y=0))
    assert result.openings == ()


def test_close_envelope_gaps__narrow_gap_skips_perpendicular_partition(fake_clock: FakeClock) -> None:
    """C5: vách vuông góc chạm mép khe (chữ T) không phải tường chủ — chỉ tường thẳng hàng với khe được kéo."""
    shared: dict[str, Any] = {
        "level_id": new_spatial_id("level", fake_clock),
        "scaling": Scaling(1.0, 1.0),
        "clock": fake_clock,
    }
    walls = [wall_px(2000, 4000, 2000, 0), *_box_with_top_gap(2000, 2500)]
    wall_set = build_walls(walls, [], dropped=Counter(), **shared)
    gap = (Point(x=2000, y=0), Point(x=2500, y=0))
    hosts = _bridge_hosts(wall_set.walls, gap)
    assert hosts
    assert all(host.centreline.start.y == host.centreline.end.y == 0 for host in hosts)


def test_close_envelope_gaps__narrow_gap_moves_start_and_shifts_openings(fake_clock: FakeClock) -> None:
    """C5: hai tường đều có **đầu** ở khe 500 → dời đầu tường thứ nhất, cửa sổ trên nó lùi theo 500 mm."""
    walls = [wall_px(2000, 0, 0, 0), wall_px(2500, 0, 6000, 0), *_box_with_top_gap(2000, 2500)[2:]]
    shared: dict[str, Any] = {
        "level_id": new_spatial_id("level", fake_clock),
        "scaling": Scaling(1.0, 1.0),
        "clock": fake_clock,
    }
    wall_set = build_walls(walls, [det("window", 500, 0, 1500, 50)], dropped=Counter(), **shared)
    rooms = build_rooms(wall_set.walls, wall_set.bridges, (), dropped=Counter(), **shared)
    result = close_envelope_gaps(wall_set, rooms, **shared)
    (opening,) = result.openings
    assert result.walls[0].centreline.start == Point(x=2500, y=0)
    assert (opening.wall_id, opening.offset_mm) == (result.walls[0].id, 1000)


def test_close_envelope_gaps__interior_gap_stays_open(fake_clock: FakeClock) -> None:
    """C5: khe 900 trên vách giữa hai phòng (cả hai bên là phòng) → giữ hở như cũ, chỉ còn đoạn nối."""
    walls = [
        *_box_with_top_gap(2000, 2000)[2:],
        wall_px(0, 0, 6000, 0),
        wall_px(3000, 0, 3000, 1500),
        wall_px(3000, 2400, 3000, 4000),
    ]
    result = _closed(walls, fake_clock, Scaling(1.0, 1.0))
    assert len(result.walls) == 6
    assert result.openings == ()
    assert result.bridges


def test_close_envelope_gaps__door_leaf_read_from_page(fake_clock: FakeClock) -> None:
    """C5: khe 900 có cánh + cung trên trang → cửa bản lề đọc được, tin 0,5, hướng cánh vào `leaves`."""
    page = _door_page(90, arc=True)
    walls = [wall_px(0, 100, 130, 100, 10), wall_px(220, 100, 399, 100, 10)]
    walls += [wall_px(399, 100, 399, 290, 10), wall_px(399, 290, 0, 290, 10), wall_px(0, 290, 0, 100, 10)]
    result = _closed(walls, fake_clock, Scaling(10.0, 10.0), page)
    (opening,) = result.openings
    assert (opening.width_mm, opening.confidence) == (900, 0.5)
    assert opening.swing in ("left", "right")
    assert opening.id in result.leaves


def test_close_envelope_gaps__fanned_bridge_along_wall_skipped(fake_clock: FakeClock) -> None:
    """W4: tường trái đứt thành hai mảnh → đoạn nối xoè từ mảnh xa chạy dọc mảnh gần; chỉ khe thật thành tường cửa."""
    shared: dict[str, Any] = {
        "level_id": new_spatial_id("level", fake_clock),
        "scaling": Scaling(1.0, 1.0),
        "clock": fake_clock,
    }
    wall_set = build_walls(_box_with_top_gap(2000, 2900), [], dropped=Counter(), **shared)
    rooms = build_rooms(wall_set.walls, wall_set.bridges, (), dropped=Counter(), **shared)
    rooms = tuple(room.model_copy(update={"usage": "bedroom"}) for room in rooms)
    left = next(i for i, w in enumerate(wall_set.walls) if w.centreline.end == Point(x=2000, y=0))
    right = next(i for i, w in enumerate(wall_set.walls) if w.centreline.start == Point(x=2900, y=0))
    walls = list(wall_set.walls)
    far, near = Point(x=1000, y=0), Point(x=2900, y=20)
    walls[left] = walls[left].model_copy(update={"centreline": Segment(start=Point(x=0, y=0), end=far)})
    walls[right] = walls[right].model_copy(update={"centreline": Segment(start=near, end=Point(x=6000, y=0))})
    piece = walls[left].model_copy(update={"id": new_spatial_id("wall", fake_clock)})
    walls.append(piece.model_copy(update={"centreline": Segment(start=far, end=Point(x=2000, y=0))}))
    fanned = replace(wall_set, walls=tuple(walls), bridges=((far, near), (Point(x=2000, y=0), near)))
    result = close_envelope_gaps(fanned, rooms, **shared)
    (opening,) = result.openings
    gap = result.walls[-1]
    assert (gap.centreline.start, gap.centreline.end) == (Point(x=2000, y=0), near)
    assert gap.opening_ids == (opening.id,)
    assert len(result.walls) == len(walls) + 1


def test_close_envelope_gaps__extended_host_not_reread_from_input(fake_clock: FakeClock) -> None:
    """Khe hẹp thứ hai chung đầu (2000, 0) với khe đã khép: tường chủ đọc từ danh sách đang sửa, không kéo lùi tường."""
    shared: dict[str, Any] = {
        "level_id": new_spatial_id("level", fake_clock),
        "scaling": Scaling(1.0, 1.0),
        "clock": fake_clock,
    }
    wall_set = build_walls(_box_with_top_gap(2000, 2400), [], dropped=Counter(), **shared)
    rooms = build_rooms(wall_set.walls, wall_set.bridges, (), dropped=Counter(), **shared)
    left = next(i for i, w in enumerate(wall_set.walls) if w.centreline.end == Point(x=2000, y=0))
    tip, far = Point(x=2495, y=-70), Point(x=3485, y=-209)
    slanted = wall_set.walls[left].model_copy(
        update={"id": new_spatial_id("wall", fake_clock), "centreline": Segment(start=tip, end=far)}
    )
    gap = (Point(x=2000, y=0), Point(x=2400, y=0))
    fanned = replace(wall_set, walls=(*wall_set.walls, slanted), bridges=(gap, (Point(x=2000, y=0), tip)))
    result = close_envelope_gaps(fanned, rooms, **shared)
    assert result.walls[left].centreline.end == Point(x=2400, y=0)
    assert result.walls[-1].centreline == Segment(start=Point(x=2000, y=0), end=far)
