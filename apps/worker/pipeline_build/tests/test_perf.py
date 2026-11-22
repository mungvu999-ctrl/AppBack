"""Hiệu năng `build_layer` trên trần B5-01: 20k tường, 5k hộp, 5k chữ (B5-05 việc E, khối [8]).

Nhân bản `render_plan(100)` bằng tịnh tiến để giữ hình học hợp lệ (tường có hướng, hộp `min<max`)
ở quy mô lớn thay vì sinh ngẫu nhiên có thể vi phạm mô hình. Marker `perf` (BE-00 §12); phần phủ
của đường mã này do các test không-`perf` khác (`test_e2e_wire.py`, …) gánh.
"""

import logging
import math
import os
import time
from collections import Counter
from unittest.mock import patch

import pytest

import apps.worker.pipeline_build.build as build_module
from apps.worker.pipeline_build.geometry import Scaling, build_walls
from apps.worker.pipeline_build.ids import new_spatial_id
from apps.worker.pipeline_build.tests.helpers import build_from_plan, build_wrapped
from apps.worker.pipeline_build.tests.test_geometry_walls import wall_px
from packages.domain.spatial.model import SpatialLayer
from packages.ml_contracts.artifacts import (
    MAX_DETECTIONS,
    MAX_TEXTS,
    MAX_WALLS,
    BoxPx,
    DetectionPx,
    TextPx,
    WallPx,
)
from packages.ml_contracts.synthetic import render_plan
from packages.testing.fixtures.clock import FakeClock

logger = logging.getLogger(__name__)

# Trần `build_layer` (K-56): 60 s, ghi đè bằng env `PERF_BUILD_LAYER_MAX_S` (máy dev chậm, runner khác).
# Số đo (thời lượng cả test = khởi động + dựng đầu vào + `build_layer`, junit-perf.xml của job `perf` CI, `main`,
# 13-15/11/2026, 5 lượt): 15,4 / 18,8 / 19,6 / 24,0 / 24,0 s — điển hình ~20 s, nên `elapsed` của riêng `build_layer`
# còn thấp hơn. Lịch sử review: CI tới 38,8 s ở PR nặng, máy dev đang tải 58–62 s. Trần 60 s >= 2x giá trị đo lớn nhất (24 s) và 3x điển hình: vẫn bắt hồi quy bậc hai, không đỏ vì
# runner chậm (trước đây 30 s, chỉ cách đo lớn nhất 6 s). Log `build_layer elapsed=` không vào log CI (mức INFO không
# hiện), nên số đo `elapsed` được ghi thêm vào junit qua `record_property`.
_BUDGET_S = float(os.environ.get("PERF_BUILD_LAYER_MAX_S", "60.0"))


def _shift_wall(wall: WallPx, dx: float, dy: float) -> WallPx:
    """Tường tịnh tiến `(dx, dy)` px; bề dày, tin cậy giữ nguyên."""
    start = wall.start.model_copy(update={"x": wall.start.x + dx, "y": wall.start.y + dy})
    end = wall.end.model_copy(update={"x": wall.end.x + dx, "y": wall.end.y + dy})
    return wall.model_copy(update={"start": start, "end": end})


def _shift_box(box: BoxPx, dx: float, dy: float) -> BoxPx:
    """Hộp tịnh tiến `(dx, dy)` px; `min < max` giữ nguyên vì cả hai cạnh cùng dịch."""
    return box.model_copy(
        update={"x_min": box.x_min + dx, "x_max": box.x_max + dx, "y_min": box.y_min + dy, "y_max": box.y_max + dy}
    )


def _tile_grid_size(base_counts: tuple[int, int, int], caps: tuple[int, int, int]) -> tuple[int, int]:
    """Số cột/hàng lưới tịnh tiến đủ để mọi danh sách đạt trần sau khi cắt ở caps."""
    replicas_needed = max(-(-cap // max(count, 1)) for count, cap in zip(base_counts, caps, strict=True))
    cols = math.ceil(math.sqrt(replicas_needed))
    rows = math.ceil(replicas_needed / cols)
    return cols, rows


def _oversized_inputs() -> tuple[tuple[WallPx, ...], tuple[DetectionPx, ...], tuple[TextPx, ...], int, int]:
    """Tường/hộp/chữ nhân bản từ `render_plan(100)` tới đúng trần B5-01, và kích thước ảnh lớn kèm theo."""
    plan = render_plan(100)
    base_h, base_w = plan.pixels.shape[0], plan.pixels.shape[1]
    cols, rows = _tile_grid_size(
        (len(plan.walls), len(plan.detections), len(plan.texts)), (MAX_WALLS, MAX_DETECTIONS, MAX_TEXTS)
    )
    walls: list[WallPx] = []
    detections: list[DetectionPx] = []
    texts: list[TextPx] = []
    for row in range(rows):
        for col in range(cols):
            dx, dy = col * base_w, row * base_h
            if len(walls) < MAX_WALLS:
                walls.extend(_shift_wall(wall, dx, dy) for wall in plan.walls)
            if len(detections) < MAX_DETECTIONS:
                detections.extend(
                    detection.model_copy(update={"box": _shift_box(detection.box, dx, dy)})
                    for detection in plan.detections
                )
            if len(texts) < MAX_TEXTS:
                texts.extend(text.model_copy(update={"box": _shift_box(text.box, dx, dy)}) for text in plan.texts)
    return (
        tuple(walls[:MAX_WALLS]),
        tuple(detections[:MAX_DETECTIONS]),
        tuple(texts[:MAX_TEXTS]),
        cols * base_w,
        rows * base_h,
    )


@pytest.mark.perf
def test_build_layer__perf_under_budget(fake_clock: FakeClock, record_property: pytest.RecordProperty) -> None:
    """20k tường/5k hộp/5k chữ (trần B5-01): `build_layer` < `_BUDGET_S` giây sau một lượt khởi động seed 100."""
    warm_plan = render_plan(100)
    build_from_plan(warm_plan, fake_clock)

    walls, detections, texts, width_px, height_px = _oversized_inputs()
    assert len(walls) == MAX_WALLS
    assert len(detections) == MAX_DETECTIONS
    assert len(texts) == MAX_TEXTS

    post_rules_seconds: list[float] = []
    real_apply_post_rules = build_module.apply_post_rules  # type: ignore[attr-defined]  # build.py nhập lại tên này

    def _timed_apply_post_rules(layer: SpatialLayer) -> SpatialLayer:
        """Vá đo giờ gọi `apply_post_rules` thật, không thay hành vi."""
        start = time.perf_counter()
        result: SpatialLayer = real_apply_post_rules(layer)
        post_rules_seconds.append(time.perf_counter() - start)
        return result

    with patch.object(build_module, "apply_post_rules", side_effect=_timed_apply_post_rules):
        start = time.perf_counter()
        build_wrapped(
            walls=walls, detections=detections, texts=texts, width_px=width_px, height_px=height_px, clock=fake_clock
        )
        elapsed = time.perf_counter() - start

    logger.info(
        "build_layer elapsed=%.3fs apply_post_rules_total=%.3fs apply_post_rules_calls=%d",
        elapsed,
        sum(post_rules_seconds),
        len(post_rules_seconds),
    )
    record_property("build_layer_elapsed_s", f"{elapsed:.3f}")
    assert elapsed < _BUDGET_S, f"build_layer mất {elapsed:.2f}s, trần {_BUDGET_S:.1f}s (PERF_BUILD_LAYER_MAX_S)"


@pytest.mark.perf
def test_build_walls__many_hugging_stubs_stay_fast(fake_clock: FakeClock) -> None:
    """3b-e trên 5 000 cụm (tường chủ, vách vuông góc, mẩu áp sát) = 15 000 tường: mẩu chỉ dò láng giềng.

    Ảnh 10 mm/px để 100 x 50 ô 10 m nằm trong khung px; mỗi ô là cảnh của test 3b-e ở `test_geometry_openings`.
    """
    walls: list[WallPx] = []
    for cell in range(5000):
        dx, dy = (cell % 100) * 1000, (cell // 100) * 1000
        walls += [
            wall_px(dx, dy + 100, dx + 600, dy + 100, thickness_px=30),
            wall_px(dx + 300, dy + 100, dx + 300, dy + 300, thickness_px=20),
            wall_px(dx + 270, dy + 130, dx + 300, dy + 130, thickness_px=10),
        ]
    dropped: Counter[str] = Counter()
    start = time.perf_counter()
    result = build_walls(
        walls,
        [],
        level_id=new_spatial_id("level", fake_clock),
        scaling=Scaling(10.0, 10.0),
        clock=fake_clock,
        dropped=dropped,
    )
    elapsed = time.perf_counter() - start
    logger.info("build_walls hugging elapsed=%.3fs", elapsed)
    assert len(result.walls) == 10_000
    assert elapsed < 20.0, f"build_walls mất {elapsed:.2f}s, trần 20s"
