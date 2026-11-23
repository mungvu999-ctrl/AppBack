"""`BuiltLayer.to_json`/`from_json` (B5-05 [2], "Dây"): khứ hồi đúng, byte tất định, khoá lạ → lỗi."""

import dataclasses
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from apps.worker.pipeline_build.build import AreaLabel, BuiltLayer, area_labels, area_notes, label_orientation
from apps.worker.pipeline_build.constants import DROPPED_KEYS
from packages.domain.spatial import Dimension, Point, Segment, SpatialLayer, Wall
from packages.ml_contracts.artifacts import BoxPx, TextPx


def _built() -> BuiltLayer:
    """Một `BuiltLayer` nhỏ nhưng đủ hình dạng: một tường, một kích thước, `dropped` đủ khoá."""
    wall = Wall(
        id="W-0000000000000000000000001",
        level_id="L-0000000000000000000000001",
        centreline=Segment(start=Point(x=0, y=0), end=Point(x=5000, y=0)),
        thickness_mm=100,
        height_mm=3600,
        kind="partition",
        opening_ids=(),
        confidence=0.9,
        source="ai",
        reviewed=False,
    )
    dimension = Dimension(
        id="M-0000000000000000000000001",
        level_id=wall.level_id,
        kind="linear",
        reference_ids=(wall.id,),
        line=wall.centreline,
        value_mm=5000,
        confidence=0.7,
        source="ai",
        reviewed=False,
    )
    return BuiltLayer(
        layer=SpatialLayer(walls=(wall,), openings=(), rooms=(), furniture=()),
        dimensions=(dimension,),
        scale_mm_per_px=Decimal("5.000000"),
        scale_source="pipeline",
        dropped=dict.fromkeys(DROPPED_KEYS, 0),
    )


def test_built_layer__json_round_trip() -> None:
    """`from_json(b.to_json()) == b` (bất biến [6]) và byte lặp lại y hệt giữa hai lần gọi."""
    built = _built()
    assert BuiltLayer.from_json(built.to_json()) == built
    assert built.to_json() == built.to_json()


def test_built_layer__dropped_always_twelve_keys() -> None:
    """`dropped` ghi ra luôn đủ 12 khoá `DROPPED_KEYS`, đúng thứ tự dây."""
    payload = json.loads(_built().to_json())
    assert list(payload["dropped"]) == list(DROPPED_KEYS)
    assert payload["scaleMmPerPx"] == "5.000000"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: p.update(extra=1), "bộ khoá gốc"),
        (lambda p: p.pop("scaleSource"), "bộ khoá gốc"),
        (lambda p: p.update(schemaVersion=2), "schemaVersion"),
        (lambda p: p["dropped"].update(mystery=1), "bộ khoá dropped"),
        (lambda p: p["dropped"].pop("outOfImage"), "bộ khoá dropped"),
    ],
)
def test_built_layer__from_json_rejects(mutate: Callable[[dict[str, Any]], object], reason: str) -> None:
    """Mọi lệch bộ khoá hay số hiệu bản đều là `ValueError`, không đọc nửa vời."""
    payload = json.loads(_built().to_json())
    mutate(payload)
    with pytest.raises(ValueError, match=reason):
        BuiltLayer.from_json(json.dumps(payload).encode())


def test_built_layer__from_json_rejects_broken_json() -> None:
    """Byte không phải JSON → `ValueError` (task đổi thành `PIPELINE_ARTIFACT_INVALID`)."""
    with pytest.raises(ValueError, match="JSON"):
        BuiltLayer.from_json(b"{khong-phai-json")


def test_built_layer__from_json_rejects_unknown_scale_source() -> None:
    """`scaleSource` ngoài hai giá trị đã khai → `ValueError` (B5-06b đọc file này qua `from_json`)."""
    payload = json.loads(_built().to_json())
    payload["scaleSource"] = "banana"
    with pytest.raises(ValueError, match="scaleSource"):
        BuiltLayer.from_json(json.dumps(payload).encode())


def test_built_layer__scale_basis_round_trips_and_is_optional() -> None:
    """QT-SCALE: `scaleBasis` khứ hồi; `layer.json` cũ không có khoá vẫn đọc được (`None`); giá trị lạ → lỗi."""
    built = dataclasses.replace(_built(), scale_basis="paired")
    assert json.loads(built.to_json())["scaleBasis"] == "paired"
    assert BuiltLayer.from_json(built.to_json()) == built
    assert "scaleBasis" not in json.loads(_built().to_json())
    payload = json.loads(built.to_json())
    payload["scaleBasis"] = "banana"
    with pytest.raises(ValueError, match="scaleBasis"):
        BuiltLayer.from_json(json.dumps(payload).encode())


def test_built_layer__scale_checks_round_trip_and_reject_unknown_shapes() -> None:
    """`scaleChecks` khứ hồi, vắng khi rỗng; khoá lạ hay thiếu trường → lỗi."""
    built = dataclasses.replace(_built(), scale_checks={"anisotropic": {"x": 26.8, "y": 30.5}})
    assert BuiltLayer.from_json(built.to_json()) == built
    assert "scaleChecks" not in json.loads(_built().to_json())
    shapes: list[object] = [{"banana": {"x": 1}}, {"anisotropic": {"x": 1}}, {"estimateSpread": 3}, []]
    for bad in shapes:
        payload = json.loads(built.to_json())
        payload["scaleChecks"] = bad
        with pytest.raises(ValueError, match="scaleChecks"):
            BuiltLayer.from_json(json.dumps(payload).encode())


def test_built_layer__label_orientation_round_trips_and_rejects_unknown_shapes() -> None:
    """BENCH3D-14: `labelOrientation` khứ hồi, vắng khi rỗng; khoá lạ hay không phải object → lỗi."""
    built = dataclasses.replace(_built(), label_orientation={"vertical": 5, "horizontal": 1})
    assert BuiltLayer.from_json(built.to_json()) == built
    assert "labelOrientation" not in json.loads(_built().to_json())
    for bad in [{"vertical": 1}, {"vertical": 1, "horizontal": 0, "diagonal": 2}, [1, 2]]:
        payload = json.loads(built.to_json())
        payload["labelOrientation"] = bad
        with pytest.raises(ValueError, match="labelOrientation"):
            BuiltLayer.from_json(json.dumps(payload).encode())


def _label(text: str, width: float, height: float, span_px: float | None = None) -> TextPx:
    return TextPx(text=text, box=BoxPx(x_min=0, y_min=0, x_max=width, y_max=height), confidence=0.9, span_px=span_px)


def test_label_orientation_counts_room_labels_only() -> None:
    """Nhãn ≥ 3 chữ cái theo hướng hộp; số kích thước (trên đường kích thước hay chỉ chữ số) và hộp gần vuông bỏ."""
    texts = [
        _label("PHONG NGU", 40, 200),
        _label("BEP", 30, 90),
        _label("WC", 20, 60),  # 2 chữ cái
        _label("3600", 20, 120),  # chữ số
        _label("KHACH", 30, 150, span_px=800.0),  # trên đường kích thước
        _label("SANH", 160, 40),
        _label("KHO", 50, 50),  # gần vuông
    ]
    assert label_orientation(texts) == {"vertical": 2, "horizontal": 1}
    assert label_orientation([_label("3600", 20, 120)]) == {}


def test_built_layer__area_notes_round_trip_and_reject_bad_values() -> None:
    """BENCH3D-24: `areaNotes` khứ hồi, vắng khi rỗng; không phải danh sách số dương → lỗi."""
    built = dataclasses.replace(_built(), area_notes=(59.0, 67.66))
    assert BuiltLayer.from_json(built.to_json()).area_notes == (59.0, 67.66)
    assert "areaNotes" not in json.loads(_built().to_json())
    for bad in ({"a": 1}, ["59"], [0], [True]):
        payload = json.loads(_built().to_json())
        payload["areaNotes"] = bad
        with pytest.raises(ValueError, match="areaNotes"):
            BuiltLayer.from_json(json.dumps(payload).encode())


def test_built_layer__page_unavailable_round_trips_and_rejects_non_bool() -> None:
    """L2-N02: `pageUnavailable` khứ hồi, vắng khi `False` (layer.json cũ đọc ra `False`); không phải bool → lỗi."""
    built = dataclasses.replace(_built(), page_unavailable=True)
    assert BuiltLayer.from_json(built.to_json()).page_unavailable is True
    assert "pageUnavailable" not in json.loads(_built().to_json())
    assert BuiltLayer.from_json(_built().to_json()).page_unavailable is False
    for bad in (1, "true", None):
        payload = json.loads(_built().to_json())
        payload["pageUnavailable"] = bad
        with pytest.raises(ValueError, match="pageUnavailable"):
            BuiltLayer.from_json(json.dumps(payload).encode())


def test_area_notes_reads_unit_areas_only() -> None:
    """Số kèm m2/m²/㎡ (cả trên đường kích thước gán nhầm), ≥ 25 m², không lặp; số đo dài và diện tích phòng bỏ."""
    texts = [
        _label("46M2", 20, 60, span_px=229.5),
        _label("DT: 67,66 m²", 20, 60),
        _label("67.66m2", 20, 60),
        _label("WC 3.2m2", 20, 60),
        _label("3600", 20, 120),
        _label("12345m2", 20, 60),
    ]
    assert area_notes(texts) == (46.0, 67.66)
    assert area_notes([_label("3600", 20, 120)]) == ()


def test_built_layer__area_labels_round_trip_and_reject_bad_values() -> None:
    """K-16: `areaLabels` khứ hồi, vắng khi rỗng; phần tử không phải `{x, y, m2}` mm nguyên / m² dương → lỗi."""
    built = dataclasses.replace(_built(), area_labels=(AreaLabel(1200, -300, 12.5), AreaLabel(4000, 900, 3.0)))
    assert json.loads(built.to_json())["areaLabels"][0] == {"x": 1200, "y": -300, "m2": 12.5}
    assert BuiltLayer.from_json(built.to_json()) == built
    assert "areaLabels" not in json.loads(_built().to_json())
    shapes: list[object] = [
        {"x": 1},
        [[1, 2, 3.0]],
        [{"x": 1, "y": 2}],
        [{"x": 1, "y": 2, "m2": 3.0, "z": 0}],
        [{"x": 1.5, "y": 2, "m2": 3.0}],
        [{"x": True, "y": 2, "m2": 3.0}],
        [{"x": 1, "y": 2, "m2": 0}],
        [{"x": 1, "y": 2, "m2": "3"}],
        [{"x": 1, "y": 2, "m2": False}],
    ]
    for bad in shapes:
        payload = json.loads(built.to_json())
        payload["areaLabels"] = bad
        with pytest.raises(ValueError, match="areaLabels"):
            BuiltLayer.from_json(json.dumps(payload).encode())


def _at(text: str, x: float, y: float) -> TextPx:
    return TextPx(text=text, box=BoxPx(x_min=x - 10, y_min=y - 5, x_max=x + 10, y_max=y + 5), confidence=0.9)


def test_area_labels_keep_room_areas_with_their_position() -> None:
    """Mọi số "N m²" (cả diện tích phòng mà `area_notes` bỏ) kèm tâm hộp ở mm; hộp hai số, số 0, không đơn vị → bỏ."""
    texts = [
        _at("P.NGỦ 12,5m2", 100, 40),
        _at("WC 3.2 m²", 20, 30),
        _at("67.66m2", 300, 200),
        _at("12 m2 / 14 m2", 50, 50),
        _at("0 m2", 60, 60),
        _at("3600", 70, 70),
        _at("WC 3.2 m²", 20, 30),
    ]
    assert area_labels(texts, 20.0) == (
        AreaLabel(400, 600, 3.2),
        AreaLabel(2000, 800, 12.5),
        AreaLabel(6000, 4000, 67.66),
    )
    assert area_labels([], 20.0) == ()
