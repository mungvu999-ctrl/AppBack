"""Bước 1 và 2 của `build_layer` (B5-05 [6]): lọc đầu vào, chốt tỉ lệ, `s_dựng`, dựng lại."""

import json
import logging
import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Final

import pytest

from apps.api.project_settings.read import DEFAULT_SETTINGS
from apps.worker.pipeline_build import build as build_module
from apps.worker.pipeline_build.build import BuiltLayer, build_layer
from apps.worker.pipeline_build.tests.helpers import build_wrapped
from packages.domain.scale import RescaleError, ScaleSample
from packages.ml_contracts.artifacts import (
    BoxPx,
    DetectionPx,
    ObjectsResult,
    PointPx,
    TextPx,
    TextResult,
    WallPx,
    WallsResult,
)
from packages.ml_contracts.synthetic import render_plan
from packages.testing.fixtures.clock import FakeClock

LEVEL_ID = "L-0000000000000000000000001"
SIZE: dict[str, Any] = {"width_px": 1600, "height_px": 1200}
_Q = Decimal("0.000001")


def _wall(x0: float, y0: float, x1: float, y1: float, thickness_px: float = 20.0) -> WallPx:
    """Tường px ngang hay dọc, tin cậy cố định 0,9 để `confidence` của đầu ra dễ đoán."""
    return WallPx(start=PointPx(x=x0, y=y0), end=PointPx(x=x1, y=y1), thickness_px=thickness_px, confidence=0.9)


def _run(
    walls: Sequence[WallPx],
    texts: Sequence[TextPx],
    clock: FakeClock,
    *,
    detections: Sequence[DetectionPx] = (),
    fallback: str = "5",
    px_per_paper_mm: float | None = None,
    vector_page: bool = True,
) -> BuiltLayer:
    """Gọi `build_layer` trên khổ 1600x1200 mặc định; tham số còn lại giữ nguyên giữa các ca."""
    return build_wrapped(
        walls=walls,
        detections=detections,
        texts=texts,
        fallback_mm_per_px=Decimal(fallback),
        clock=clock,
        level_id=LEVEL_ID,
        px_per_paper_mm=px_per_paper_mm,
        vector_page=vector_page,
        **SIZE,
    )


def test_build_layer__scale_from_pipeline_matches_answer(fake_clock: FakeClock) -> None:
    """Seed 100 tổng hợp → `pipeline`, lệch `mm_per_px` đáp án ≤ 2 % (K19: không đoán tỉ lệ)."""
    plan = render_plan(100)
    built = _run(plan.walls, plan.texts, fake_clock, detections=plan.detections)
    assert built.scale_source == "pipeline"
    assert abs(float(built.scale_mm_per_px) - plan.mm_per_px) / plan.mm_per_px <= 0.02


def test_build_layer__no_text_falls_back_to_project_default(
    fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """Không chữ nào → không suy được tỉ lệ: `s` là `fallback`, log mang `SCALE_UNRESOLVED`."""
    caplog.set_level(logging.INFO, logger=build_module.__name__)
    built = _run([_wall(100, 100, 1100, 100)], (), fake_clock)
    assert (built.scale_source, built.scale_basis) == ("project_default", "default")
    assert built.scale_mm_per_px == Decimal("5")
    assert any(getattr(record, "code", None) == "SCALE_UNRESOLVED" for record in caplog.records)


def test_build_layer__unset_project_scale_uses_wall_estimate(fake_clock: FakeClock) -> None:
    """U01: dự án chưa đặt tỉ lệ (1 mm/px) → tỉ lệ tạm từ bề dày tường (150 mm / 15 px = 10), không `Dimension`."""
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    built = _run(walls, (), fake_clock, fallback="1")
    assert (built.scale_source, built.scale_mm_per_px) == ("pipeline", Decimal("10.000000"))
    assert built.scale_basis == "wallThickness"
    assert built.dimensions == ()
    assert built.layer.walls[0].thickness_mm >= 100  # không còn tường 15 mm của tỉ lệ 1 mm/px


def _door(x: float, length: float, y: float = 95) -> DetectionPx:
    """Hộp cửa đi đứng, cạnh dài `length` px, góc trên ở `(x, y)`; mặc định chạm tường ngang y = 100."""
    return DetectionPx(label="door", box=BoxPx(x_min=x, y_min=y, x_max=x + 10, y_max=y + length), confidence=0.9)


def test_build_layer__unset_project_scale_uses_door_estimate(fake_clock: FakeClock) -> None:
    """DOOR-SCALE: cửa 40/50/60 + cửa đôi 120 (> 1,6 x trung vị 55) bị bỏ → 745 / 50 = 14,9; không bỏ thì 745 / 55."""
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    doors = [_door(300, 40), _door(340, 50), _door(380, 60), _door(600, 120)]
    built = _run(walls, (), fake_clock, detections=doors, fallback="1")
    assert (built.scale_source, built.scale_mm_per_px, built.scale_basis) == ("pipeline", Decimal("14.900000"), "door")
    assert built.dimensions == ()


def test_build_layer__door_estimate_ignores_doors_away_from_walls(fake_clock: FakeClock) -> None:
    """DOOR-SCALE: cửa ở sơ đồ thu nhỏ (cách mọi tường > bề dày dày nhất) không tính → vẫn 745 / 50 = 14,9."""
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    near = [_door(300 + 40 * k, 50) for k in range(3)]
    far = [_door(1300 + 20 * k, 12, y=1000) for k in range(4)]
    built = _run(walls, (), fake_clock, detections=near + far, fallback="1")
    assert built.scale_mm_per_px == Decimal("14.900000")


def test_build_layer__door_estimate_needs_three_doors_by_walls(fake_clock: FakeClock) -> None:
    """DOOR-SCALE: 5 hộp cửa nhưng chỉ 2 sát tường → không đủ, ước từ bề dày tường (150 / 15 = 10)."""
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    doors = [_door(300, 50), _door(400, 50)] + [_door(1300 + 20 * k, 12, y=1000) for k in range(3)]
    built = _run(walls, (), fake_clock, detections=doors, fallback="1")
    assert built.scale_mm_per_px == Decimal("10.000000")


def test_build_layer__unset_project_scale_two_doors_uses_wall_estimate(fake_clock: FakeClock) -> None:
    """DOOR-SCALE: dưới 3 cửa đi (cửa sổ không tính) → vẫn ước từ bề dày tường (150 / 15 = 10)."""
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    window = DetectionPx(label="window", box=BoxPx(x_min=700, y_min=300, x_max=710, y_max=350), confidence=0.9)
    built = _run(walls, (), fake_clock, detections=[_door(300, 50), _door(400, 50), window], fallback="1")
    assert built.scale_mm_per_px == Decimal("10.000000")


def test_build_layer__unset_project_scale_without_walls_keeps_default(fake_clock: FakeClock) -> None:
    """Không tường nào để ước → giữ `project_default` như cũ."""
    built = _run([], (), fake_clock, fallback="1")
    assert (built.scale_source, built.scale_mm_per_px) == ("project_default", Decimal("1"))


def test_unset_project_scale_matches_settings_default() -> None:
    """Hằng "chưa đặt" của bước dựng phải đúng mặc định `project_settings`."""
    assert DEFAULT_SETTINGS.default_scale_mm_per_px == build_module._UNSET_PROJECT_SCALE


@pytest.mark.parametrize("fallback", ["0", "-1", "NaN"])
def test_build_layer__rejects_bad_fallback(fallback: str, fake_clock: FakeClock) -> None:
    """`fallback_mm_per_px` không dương hay không hữu hạn là sai hợp đồng → `ValueError`."""
    with pytest.raises(ValueError, match="fallback_mm_per_px"):
        _run([_wall(100, 100, 1100, 100)], (), fake_clock, fallback=fallback)


@pytest.mark.parametrize(("width", "height"), [(0, 1200), (1600, 0)])
def test_build_layer__rejects_empty_image(width: int, height: int, fake_clock: FakeClock) -> None:
    """Ảnh nhỏ hơn 1 px không có hệ toạ độ để kẹp đầu vào → `ValueError`."""
    with pytest.raises(ValueError, match="không hợp lệ"):
        build_wrapped(
            width_px=width, height_px=height, fallback_mm_per_px=Decimal("5"), clock=fake_clock, level_id=LEVEL_ID
        )


def test_build_layer__scale_out_of_range_falls_back(fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mẫu 500 mm/px (ngoài khoảng) → đếm `scaleOutOfRange` rồi dùng `fallback`, không áp 500."""
    samples = tuple(ScaleSample(id=f"t{i}-w0-s0", pixel_length=10.0, real_length_mm=5000.0) for i in range(4))
    monkeypatch.setattr(build_module, "pair_dimension_lines", lambda texts, walls: samples)
    built = _run([_wall(100, 100, 1100, 100)], (), fake_clock)
    assert built.scale_source == "project_default"
    assert built.dropped["scaleOutOfRange"] == 1


def test_build_layer__rescale_error_rebuilds_at_final_scale(
    fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`RescaleError` khi đổi `s_dựng → s` → dựng lại thẳng ở `s`, không ném, có log riêng."""

    def _explode(layer: object, old: float, new: float) -> object:
        """Bản vá của `rescale_unreviewed`: luôn báo một thực thể hỏng (K23, phụ thuộc ngoài module)."""
        raise RescaleError("W-0000000000000000000000001")

    caplog.set_level(logging.WARNING, logger=build_module.__name__)
    monkeypatch.setattr(build_module, "rescale_unreviewed", _explode)
    built = _run([_wall(100, 100, 1100, 100)], (), fake_clock)
    assert built.scale_source == "project_default"
    assert any(record.getMessage() == "pipeline_build_rescale_fallback" for record in caplog.records)


def test_build_layer__rejects_non_finite_coordinate(fake_clock: FakeClock) -> None:
    """Toạ độ NaN dựng lén bằng `model_construct` vẫn bị bước 1 chặn → `ValueError`."""
    wall = WallPx.model_construct(
        start=PointPx.model_construct(x=float("nan"), y=100.0),
        end=PointPx(x=1100.0, y=100.0),
        thickness_px=20.0,
        confidence=0.9,
    )
    with pytest.raises(ValueError, match="NaN"):
        _run([wall], (), fake_clock)


def test_build_layer__rejects_confidence_out_of_unit_range(fake_clock: FakeClock) -> None:
    """`confidence` 1,5 là đầu ra sai hợp đồng B5-01, không phải đầu ra xấu → `ValueError`."""
    detection = DetectionPx.model_construct(
        label="door", box=BoxPx(x_min=400, y_min=90, x_max=500, y_max=110), confidence=1.5
    )
    with pytest.raises(ValueError, match="confidence"):
        _run([_wall(100, 100, 1100, 100)], (), fake_clock, detections=[detection])


def test_build_layer__drops_entities_outside_image(fake_clock: FakeClock) -> None:
    """Tường nằm ngoài `[-1, w+1] x [-1, h+1]` bị bỏ và đếm `outOfImage`, không làm hỏng lượt."""
    built = _run([_wall(100, 100, 1100, 100), _wall(5000, 5000, 6000, 5000)], (), fake_clock)
    assert built.dropped["outOfImage"] == 1
    assert len(built.layer.walls) == 1


def test_build_layer__post_rules_snap_fixture_to_wall(fake_clock: FakeClock) -> None:
    """Luật hậu xử lý B3-06 chạy: lavabo cách mặt tường 120 mm bị dời sát (`apply_post_rules`)."""
    fixture = DetectionPx(
        label="sanitary_fixture",
        box=BoxPx(x_min=300.0, y_min=134.0, x_max=400.0, y_max=174.0),
        confidence=0.8,
    )
    built = _run([_wall(100, 100, 1100, 100)], (), fake_clock, detections=[fixture])
    assert len(built.layer.furniture) == 1
    assert built.layer.furniture[0].centre.y < 770


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"thickness_px": 0.0}, "thickness_px"),
        ({"thickness_px": -5.0}, "thickness_px"),
        ({"end": PointPx(x=100.0, y=100.0)}, "hai đầu trùng"),
    ],
)
def test_build_layer__rejects_degenerate_wall(overrides: dict[str, Any], match: str, fake_clock: FakeClock) -> None:
    """Tường suy biến dựng lén bằng `model_construct` → `ValueError`, không `ZeroDivisionError`.

    `_build_scale` chia cho bề dày trung vị; bề dày 0 lọt qua bước 1 sẽ ném `ZeroDivisionError`
    mà `tasks._build_and_write` (`except ValueError`) không bắt được, nên thông điệp rơi vào
    đường thử lại thay vì `step_done failed` (review lượt 1, F1). `WallsResult` cũng dựng bằng
    `model_construct`: qua hàm dựng thường thì pydantic chặn trước, không tới được bước 1.
    """
    fields: dict[str, Any] = {
        "start": PointPx(x=100.0, y=100.0),
        "end": PointPx(x=1100.0, y=100.0),
        "thickness_px": 20.0,
        "confidence": 0.9,
        **overrides,
    }
    wall = WallPx.model_construct(None, **fields)
    with pytest.raises(ValueError, match=match):
        build_layer(
            level_id=LEVEL_ID,
            walls=WallsResult.model_construct(walls=(wall,)),
            objects=ObjectsResult(detections=()),
            text=TextResult(items=()),
            fallback_mm_per_px=Decimal("5"),
            clock=fake_clock,
            **SIZE,
        )


def _chain_text(value: str, cx: float, span_px: float, y: float = 1100) -> TextPx:
    """Chữ kích thước ngoài căn (xa mọi tường) mà bước đọc chữ đã đo `span_px`."""
    return TextPx(
        text=value, box=BoxPx(x_min=cx - 15, y_min=y, x_max=cx + 15, y_max=y + 14), confidence=0.9, span_px=span_px
    )


def _agreed_chain() -> list[TextPx]:
    """Ba chữ chuỗi đồng thuận 20 mm/px."""
    return [_chain_text("3000", 200, 150), _chain_text("2000", 500, 100), _chain_text("4000", 800, 200)]


def test_build_layer__agreed_chain_persists_as_measured(fake_clock: FakeClock) -> None:
    """OCR-SCALE: ≥ 3 số đo đồng thuận, không nghi vấn → `measured`; `layer.json` vẫn ghi `pipeline`."""
    built = _run([_wall(100, 100, 1100, 100, 10.0)], _agreed_chain(), fake_clock)
    assert (built.scale_basis, built.scale_checks) == ("chain", {})
    assert (built.scale_source, built.persisted_scale_source) == ("pipeline", "measured")


@pytest.mark.parametrize(
    ("wall", "median_mm", "frame_mm"),
    [
        pytest.param(_wall(100, 100, 1100, 100, 1.0), 20.0, 20000.0, id="thin-walls"),
        pytest.param(_wall(100, 100, 1100, 100, 30.0), 600.0, 20000.0, id="thick-walls"),
        pytest.param(_wall(100, 100, 200, 100, 10.0), 200.0, 2000.0, id="tiny-frame"),
    ],
)
def test_build_layer__implausible_walls_stay_provisional(
    fake_clock: FakeClock, wall: WallPx, median_mm: float, frame_mm: float
) -> None:
    """OCR-SCALE: số đo đồng thuận mà tường ra vô lý → nghi vấn, giữ `pipeline` để người kiểm."""
    built = _run([wall], _agreed_chain(), fake_clock)
    assert built.scale_checks["implausibleWalls"] == {"medianMm": median_mm, "frameMm": frame_mm}
    assert built.persisted_scale_source == "pipeline"


def test_build_layer__estimate_never_persists_as_measured(fake_clock: FakeClock) -> None:
    """OCR-SCALE: ước lượng và mặc định dự án giữ nguồn của lượt dựng."""
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    assert _run(walls, (), fake_clock, fallback="1").persisted_scale_source == "pipeline"
    assert _run(walls, (), fake_clock).persisted_scale_source == "project_default"


def test_build_layer__scale_from_dimension_chains(fake_clock: FakeClock) -> None:
    """Ba chữ chuỗi ở 20 mm/px + một chữ đọc sai: chuỗi quyết `s` = 20, `pipeline`, dù không ghép được tường nào."""
    texts = [_chain_text("3000", 200, 150), _chain_text("2000", 500, 100), _chain_text("4000", 800, 200)]
    texts.append(_chain_text("5.202", 1100, 60))  # nhãn diện tích đọc thành 5202 mm: ngoài cụm
    texts.append(_chain_text("9000", 1300, 30))  # 300 mm/px: ngoài dải 1-200, không thành mẫu
    built = _run([_wall(100, 100, 1100, 100)], texts, fake_clock)
    assert (built.scale_source, built.scale_mm_per_px, built.scale_basis) == ("pipeline", Decimal("20"), "chain")
    # UX-02: ba chữ trong cụm thành `Dimension` không gắn tường, dài đúng số đọc; chữ ngoài cụm thì không.
    assert sorted(dim.value_mm for dim in built.dimensions) == [2000, 3000, 4000]
    for dim in built.dimensions:
        length = math.dist((dim.line.start.x, dim.line.start.y), (dim.line.end.x, dim.line.end.y))
        assert (dim.reference_ids, round(length)) == ((), dim.value_mm)


def test_build_layer__few_chain_samples_fall_back_to_wall_pairing(fake_clock: FakeClock) -> None:
    """Hai mẫu chuỗi (< 3) lệch nhau 5 % (ngoài nhánh `paired`) không đủ: tỉ lệ lấy từ ghép chữ-tường."""
    plan = render_plan(100)
    texts = [*plan.texts, _chain_text("3000", 200, 30), _chain_text("2000", 500, 21)]
    built = _run(plan.walls, texts, fake_clock, detections=plan.detections)
    assert built.scale_source == "pipeline"
    assert abs(float(built.scale_mm_per_px) - plan.mm_per_px) / plan.mm_per_px <= 0.02


def _note(value: str) -> TextPx:
    """Ghi chú tỉ lệ ở khung tên (xa mọi tường), không có `span_px`."""
    return TextPx(text=value, box=BoxPx(x_min=1300, y_min=1150, x_max=1500, y_max=1170), confidence=0.9)


def test_build_layer__scale_note_with_paper_size(fake_clock: FakeClock) -> None:
    """U01: PDF 150 DPI (≈ 5,9 px/mm giấy) + `TL 1/100` → `s` = 100 / 5,9 ≈ 16,9 mm/px, `pipeline`, thắng ước lượng."""
    px_per_paper_mm = 150 / 25.4
    walls = [_wall(100, 100, 1100, 100, 12.0), _wall(100, 100, 100, 900, 12.0)]
    note = _note("MẶT BẰNG TẦNG 1 - TL 1/100")
    built = _run(walls, [note], fake_clock, fallback="1", px_per_paper_mm=px_per_paper_mm)
    assert (built.scale_source, built.scale_basis) == ("pipeline", "note")
    assert built.scale_mm_per_px == Decimal(repr(100 / px_per_paper_mm)).quantize(Decimal("0.000001"))


def test_build_layer__note_scale_keeps_two_chain_dimensions(fake_clock: FakeClock) -> None:
    """UX-02: hai chữ chuỗi (< 3, có số < 1 m: không `paired`) khớp `s` của ghi chú `TL 1/100` ra hai `Dimension`."""
    texts = [_chain_text("3000", 200, 150), _chain_text("900", 500, 45), _note("TL 1/100")]
    built = _run([_wall(100, 100, 1100, 100, 10.0)], texts, fake_clock, fallback="1", px_per_paper_mm=5.0)
    assert (built.scale_source, built.scale_mm_per_px, built.scale_basis) == ("pipeline", Decimal("20"), "note")
    assert sorted(dim.value_mm for dim in built.dimensions) == [900, 3000]


def test_build_layer__two_agreeing_chains_resolve_as_paired(fake_clock: FakeClock) -> None:
    """B1 (ch-09): chỉ hai chữ chuỗi 12130/491,5 và 11310/458 (lệch 0,06 %) → `s` ≈ 24,69, gốc `paired`."""
    texts = [_chain_text("12130", 200, 491.5), _chain_text("11310", 500, 458)]
    built = _run([_wall(100, 100, 1100, 100, 10.0)], texts, fake_clock)
    assert (built.scale_source, built.scale_basis) == ("pipeline", "paired")
    assert abs(float(built.scale_mm_per_px) - 24.687) < 0.001


@pytest.mark.parametrize(
    ("note", "px_per_paper_mm", "thickness_px"),
    [
        ("TL 1/100", None, 12.0),  # ảnh raster: không biết khổ giấy
        ("PHÒNG KHÁCH", 150 / 25.4, 12.0),  # không có ghi chú
        ("TL 1/10", 150 / 25.4, 12.0),  # tường trung vị 20 mm: ghi chú đọc hỏng
        ("TL 1/5000", 1.0, 12.0),  # 5000 mm/px: ngoài dải 1-200
    ],
)
def test_build_layer__scale_note_ignored(
    note: str, px_per_paper_mm: float | None, thickness_px: float, fake_clock: FakeClock
) -> None:
    """Thiếu khổ giấy, thiếu ghi chú hay ghi chú vô lý → giữ `project_default` như cũ."""
    walls = [_wall(100, 100, 1100, 100, thickness_px), _wall(100, 100, 100, 900, thickness_px)]
    built = _run(walls, [_note(note)], fake_clock, px_per_paper_mm=px_per_paper_mm)
    assert (built.scale_source, built.scale_mm_per_px) == ("project_default", Decimal("5"))


def test_build_layer__scale_note_without_walls(fake_clock: FakeClock) -> None:
    """Không tường nào để soát bề dày → nhận ghi chú `TL 1:100` với 5 px/mm giấy = 20 mm/px."""
    built = _run([], [_note("TL 1/100")], fake_clock, fallback="1", px_per_paper_mm=5.0)
    assert (built.scale_source, built.scale_mm_per_px) == ("pipeline", Decimal("20.000000"))


def _vertical_chain_text(value: str, cy: float, span_px: float, x: float = 20) -> TextPx:
    """Chữ kích thước dọc (hộp cao hơn 1,3 lần rộng) ở mép trái, xa mọi tường."""
    return TextPx(
        text=value, box=BoxPx(x_min=x, y_min=cy - 15, x_max=x + 14, y_max=cy + 15), confidence=0.9, span_px=span_px
    )


_SHELL: Final = (
    _wall(100, 100, 1100, 100),
    _wall(1100, 100, 1100, 1100),
    _wall(1100, 1100, 100, 1100),
    _wall(100, 1100, 100, 100),
)
"""Vỏ căn vuông 1000 px: khung tường chính (100, 100)-(1100, 1100), biên nới 250 px."""


def test_build_layer__axes_13_percent_apart_use_geometric_mean(fake_clock: FakeClock) -> None:
    """ch-13: chuỗi ngang chỉ ở cạnh dưới 20 mm/px, chuỗi dọc chỉ ở cạnh trái 22,6 mm/px (lệch 13 %, hai hộp chữ
    không cắt nhau) sát khung tường → `scaleChecks.anisotropic` và tỉ lệ √(20 · 22,6)."""
    across = [_chain_text("3000", 200, 150, 1150), _chain_text("2000", 500, 100, 1150)]
    across += [_chain_text("4000", 800, 200, 1150), _chain_text("2500", 1000, 125, 1150)]  # ngang đông hơn
    down = [_vertical_chain_text("2260", 300, 100), _vertical_chain_text("4520", 500, 200)]
    down.append(_vertical_chain_text("3390", 800, 150))
    built = _run(_SHELL, [*across, *down], fake_clock)
    alone = _run(_SHELL, across, fake_clock)
    assert (alone.scale_mm_per_px, alone.scale_basis) == (Decimal("20.000000"), "chain")
    assert (built.scale_mm_per_px, built.scale_basis) == (Decimal(repr(math.sqrt(20 * 22.6))).quantize(_Q), "chain")
    assert built.scale_checks == {"anisotropic": {"x": 20.0, "y": 22.6}}
    assert json.loads(built.to_json())["scaleChecks"] == {"anisotropic": {"x": 20.0, "y": 22.6}}
    assert alone.scale_checks == {}


def test_build_layer__door_and_wall_estimates_combine_or_are_recorded(fake_clock: FakeClock) -> None:
    """Cửa 745 / 50 = 14,9 vs tường 150 / 15 = 10 (lệch 49 %) → giữ cửa, ghi `estimateSpread`; tường 150 / 10,5
    ≈ 14,29 (lệch 4 %) → trung bình nhân √(14,9 · 14,29), không ghi."""
    doors = [_door(300, 40), _door(340, 50), _door(380, 60)]
    walls = [_wall(100, 100, 1100, 100, 15.0), _wall(100, 100, 100, 900, 15.0)]
    built = _run(walls, (), fake_clock, detections=doors, fallback="1")
    assert (built.scale_mm_per_px, built.scale_basis) == (Decimal("14.900000"), "door")
    assert built.scale_checks == {"estimateSpread": {"door": 14.9, "wallThickness": 10.0}}
    walls = [_wall(100, 100, 1100, 100, 10.5), _wall(100, 100, 100, 900, 10.5)]
    built = _run(walls, (), fake_clock, detections=doors, fallback="1")
    expected = Decimal(repr(math.sqrt(14.9 * 150 / 10.5))).quantize(Decimal("0.000001"))
    assert (built.scale_mm_per_px, built.scale_basis, built.scale_checks) == (expected, "door", {})


@pytest.mark.parametrize("walls", [_SHELL, ()], ids=["shell", "no-walls"])
def test_build_layer__detail_at_other_scale_keeps_main_plan_scale(
    walls: Sequence[WallPx], fake_clock: FakeClock
) -> None:
    """Mặt bằng 1:100 (6 chữ ngang 10 mm/px, hai phía) + chi tiết 1:50 ngoài khung tường nới (3 chữ dọc 5 mm/px), hay
    không có tường để dựng khung → giữ 10 mm/px, chỉ ghi `scaleChecks.anisotropic`."""
    across = [_chain_text(str(1000 * k), 200 * k, 100 * k, y) for k in (1, 2, 3) for y in (40, 1100)]
    detail = [_vertical_chain_text(str(500 * k), 300 + 60 * k, 100 * k, 1500) for k in (1, 2, 3)]
    built = _run(walls, [*across, *detail], fake_clock)
    assert (built.scale_mm_per_px, built.scale_basis) == (Decimal("10.000000"), "chain")
    assert built.scale_checks == {"anisotropic": {"x": 10.0, "y": 5.0}}


@pytest.mark.parametrize(
    ("door_px", "basis", "scale"),
    [
        pytest.param(45, "note", Decimal(repr(100 / (150 / 25.4))).quantize(_Q), id="door-agrees-2pct"),
        pytest.param(50, "door", Decimal("14.900000"), id="door-off-14pct"),
        pytest.param(None, "wallThickness", Decimal("12.500000"), id="no-door"),
    ],
)
def test_build_layer__raster_pdf_note_needs_door_agreement(
    door_px: int | None, basis: str, scale: Decimal, fake_clock: FakeClock
) -> None:
    """PDF chỉ chứa ảnh: `TL 1/100` ở 150 DPI (16,93 mm/px) chỉ nhận khi ước cửa lệch ≤ 10 %, không thì về ước lượng."""
    walls = [_wall(100, 100, 1100, 100, 12.0), _wall(100, 100, 100, 900, 12.0)]
    doors = [] if door_px is None else [_door(300 + 40 * k, door_px) for k in range(3)]
    note = [_note("TL 1/100")]
    built = _run(walls, note, fake_clock, detections=doors, fallback="1", px_per_paper_mm=150 / 25.4, vector_page=False)
    assert (built.scale_basis, built.scale_mm_per_px) == (basis, scale)


@pytest.mark.parametrize(
    ("down_ratio", "basis"),
    [
        pytest.param(21.4, "chain", id="axes-7pct"),
        pytest.param(30.0, "default", id="axes-50pct"),
    ],
)
def test_build_layer__tied_axis_clusters_use_geometric_mean(
    down_ratio: float, basis: str, fake_clock: FakeClock
) -> None:
    """BENCH3D-21 T3 (f-23): 3 chữ ngang 20 mm/px và 3 chữ dọc 21,4 (lệch 7 %) → cụm chung hoà rỗng; hai trục lệch
    <= 15 % → √(20 · 21,4), gốc `chain`, kèm `anisotropic`. Lệch 50 % (hai hình khác tỉ lệ) → không đoán."""
    across = [_chain_text(str(20 * span), 200 + 300 * k, span) for k, span in enumerate((100, 150, 200))]
    spans = (100, 150, 200)
    down = [_vertical_chain_text(str(round(down_ratio * span)), 300 + 250 * k, span) for k, span in enumerate(spans)]
    built = _run([_wall(100, 100, 1100, 100, 10.0)], [*across, *down], fake_clock)
    assert built.scale_basis == basis
    if basis == "chain":
        assert built.scale_mm_per_px == Decimal(repr(math.sqrt(20 * down_ratio))).quantize(_Q)
        assert built.scale_checks["anisotropic"] == {"x": 20.0, "y": down_ratio}
    else:
        assert built.scale_mm_per_px == Decimal("5")


def test_build_layer__axis_rescue_flagged_even_when_axes_agree(fake_clock: FakeClock) -> None:
    """Codex P1: cụm 20 mm/px (ngang 20, dọc 20,4: lệch 2 % < ngưỡng `_anisotropy`) hoà với cụm 50 cùng 14 mẫu → T3
    cứu bằng hai trục nhưng cụm 50 vẫn cạnh tranh: phải ghi `scaleChecks.anisotropic` để readiness báo."""

    def texts(ratios: Sequence[float], vertical: bool) -> list[TextPx]:
        make = _vertical_chain_text if vertical else _chain_text
        return [make(str(round(r * 100)), 100 + 40 * k, 100) for k, r in enumerate(ratios)]

    across = texts([19.41] * 2 + [20.0] * 4 + [20.59] * 2 + [50.0] * 7, vertical=False)
    down = texts([19.81] * 2 + [20.4] * 4 + [20.99] * 2 + [50.0] * 7, vertical=True)
    built = _run([_wall(100, 100, 1100, 100, 10.0)], [*across, *down], fake_clock)
    assert built.scale_basis == "chain"
    assert 19.9 < float(built.scale_mm_per_px) < 20.5
    assert set(built.scale_checks) == {"anisotropic"}
