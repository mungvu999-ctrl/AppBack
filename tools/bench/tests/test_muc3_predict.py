"""Dự đoán đường sản phẩm cho thước Mức 3 (NHA-00): đệm đọc trang → `solve_set` → JSON chấm được; dữ liệu tổng hợp."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from packages.domain.drawing_set.building import BuildingProposal, Measured, RoofSpec
from packages.domain.drawing_set.proposal import FloorProposal
from packages.ml_contracts.artifacts import BoxPx, TextPx
from packages.ml_contracts.drawing_sets import PageReadingDoc, ReadingBox, reading_to_json
from tools.bench.muc3.__main__ import main as score_main
from tools.bench.muc3.metrics import Json
from tools.bench.muc3.predict import (
    EXPOSED,
    _heights,
    _kind,
    load_sheets,
    main,
    predict_set,
    roof_draft,
    split_by_exposure,
)
from tools.bench.tests.test_muc3_score import _write

PANEL = (2000, 1200)


def _text(text: str, x0: float, y0: float, x1: float, y1: float) -> TextPx:
    """Một dòng chữ OCR tổng hợp."""
    return TextPx(text=text, box=BoxPx(x_min=x0, y_min=y0, x_max=x1, y_max=y1), confidence=0.9)


def _doc(*titles: str) -> PageReadingDoc:
    """Một tờ ghép ngang, mỗi tấm 2000x1200 một khung: vài nhãn phòng và tên hình lớn ở giữa dưới."""
    width, height = PANEL
    texts = []
    for n, title in enumerate(titles):
        dx = n * width
        texts += [_text(f"P{i}", dx + 200 + 150 * i, 300, dx + 260 + 150 * i, 330) for i in range(6)]
        texts.append(_text(title, dx + width * 0.3, height * 0.85, dx + width * 0.7, height * 0.92))
    boxes = tuple(ReadingBox(x0=n * width, y0=0, x1=(n + 1) * width, y1=height, score=0.9) for n in range(len(titles)))
    return PageReadingDoc(width_px=width * len(titles), height_px=height, boxes=boxes, texts=tuple(texts), read=True)


def _label(*ids: str, scale: tuple[int, int] = (3, 2)) -> Json:
    """Nhãn tờ ở khổ gốc lớn hơn ảnh đã đọc (ngang x3, dọc x2 — lệch nhau để bắt nhầm rộng/cao), mỗi tấm một hình."""
    w, h = PANEL[0] * scale[0], PANEL[1] * scale[1]
    views = [{"id": v, "kind": "plan", "box": [n * w, 0, (n + 1) * w, h], "minor": False} for n, v in enumerate(ids)]
    return {"size": [w * len(ids), h], "views": views}


def _cache(root: Path, sheets: Mapping[str, tuple[PageReadingDoc, Json | None]]) -> tuple[Path, Path]:
    """Ghi đệm (kèm `MANIFEST.json`) và nhãn tờ; trả `(thư mục đệm, thư mục nhãn)`."""
    readings, labels = root / "readings", root / "sheets"
    readings.mkdir(parents=True, exist_ok=True)
    for stem, (doc, label) in sheets.items():
        (readings / f"{stem}.json").write_bytes(reading_to_json(doc))
        if label is not None:
            _write(labels / f"{stem}.json", label)
    _write(readings / "MANIFEST.json", {stem: {} for stem in sheets})
    return readings, labels


TWO = _doc("MẶT BẰNG TẦNG 1", "MẶT BẰNG TẦNG 2")


def test_predict_set_names_views_by_truth_boxes(tmp_path: Path) -> None:
    """Khung máy ra điểm ảnh khổ gốc, mang mã hình đáp án khớp IoU; loại và tầng là của máy."""
    dirs = _cache(tmp_path, {"S__s00": (TWO, _label("S__f00_plan", "S__f01_plan"))})
    pred = predict_set(load_sheets(*dirs)["S"])
    assert [v["box"] for v in pred["sheet_views"]["S__s00"]] == [
        pytest.approx([0, 0, 6000, 2400]),
        pytest.approx([6000, 0, 12000, 2400]),
    ]
    assert [(v["id"], v["kind"], v["floor_no"]) for v in pred["views"]] == [
        ("S__f00_plan", "plan", 1),
        ("S__f01_plan", "plan", 2),
    ]
    assert all(v["floor_confidence"] is not None for v in pred["views"])
    assert [(f["floor_no"], f["view_id"]) for f in pred["floors"]] == [(1, "S__f00_plan"), (2, "S__f01_plan")]
    assert [(h["from"], h["to"]) for h in pred["storey_heights"]] == [(1, 2)]


def test_predict_set_solves_storeys_across_sheets(tmp_path: Path) -> None:
    """Bộ hai tờ: tầng giải chung cả bộ, mã hình gán đúng tờ (chỉ số hình chạy liên tục qua các tờ)."""
    sheets = {
        "S__s00": (_doc("MẶT BẰNG TẦNG 1"), _label("S__f00_plan")),
        "S__s01": (_doc("MẶT BẰNG TẦNG 2"), _label("S__f01_plan")),
    }
    pred = predict_set(load_sheets(*_cache(tmp_path, sheets))["S"])
    assert [(v["id"], v["floor_no"]) for v in pred["views"]] == [("S__f00_plan", 1), ("S__f01_plan", 2)]
    assert sorted(pred["sheet_views"]) == ["S__s00", "S__s01"]


def test_unmatched_box_gets_no_view_id(tmp_path: Path) -> None:
    """Khung máy không khớp khung đáp án nào (IoU < 0,5): vẫn có trong `sheet_views`, không vào `views`; tầng mất mã."""
    far = {"size": [4000, 1200], "views": [{"id": "S__f00_plan", "kind": "plan", "box": [0, 0, 400, 300]}]}
    pred = predict_set(load_sheets(*_cache(tmp_path, {"S__s00": (TWO, far)}))["S"])
    assert len(pred["sheet_views"]["S__s00"]) == 2
    assert pred["views"] == []
    assert [f["view_id"] for f in pred["floors"]] == [None, None]


def _measured(value: float | str, confidence: float = 0.8) -> Measured:
    """Một giá trị đo nguồn `plan`."""
    return Measured(value=value, confidence=confidence, source="plan")


def _roof(form: str, eave_mm: int | None, *, confidence: float = 0.8, ridge_mm: int | None = None) -> RoofSpec:
    """`RoofSpec` dạng `form`, diềm ở `eave_mm` (độ tin 0,6), đỉnh ở `ridge_mm` nếu có."""
    return RoofSpec(
        level_ref="v",
        form=_measured(form, confidence),
        pitch_deg=_measured(30.0),
        quarter_turns=_measured(0),
        eave_elevation_mm=None if eave_mm is None else _measured(eave_mm, 0.6),
        overhang_mm=_measured(300),
        ridge_elevation_mm=None if ridge_mm is None else _measured(ridge_mm),
    )


def _building(*roofs: RoofSpec) -> BuildingProposal:
    """Đề xuất ngôi nhà chỉ mang các mái cho trước."""
    return BuildingProposal(
        unit_system=_measured("metric"), floors=(), levels=(), roofs=roofs, conflicts=(), violations=()
    )


@pytest.mark.parametrize(
    ("roofs", "expected"),
    [
        ((), {}),
        (
            (_roof("flat", 6600),),
            {"type": "flat", "confidence": 0.8, "base_level_m": 6.6, "top_level_m": None, "level_confidence": 0.6},
        ),
        (
            (_roof("hip", None, confidence=0.4),),
            {"type": "pitched", "confidence": 0.4, "base_level_m": None, "top_level_m": None, "level_confidence": None},
        ),
        (
            (_roof("flat", 6600), _roof("gable", 9900, confidence=0.5, ridge_mm=11400)),
            {"type": "mixed", "confidence": 0.5, "base_level_m": 9.9, "top_level_m": 11.4, "level_confidence": 0.6},
        ),
        (
            (_roof("shed", 9900), _roof("gable", 6600)),
            {"type": "pitched", "confidence": 0.8, "base_level_m": 9.9, "top_level_m": None, "level_confidence": 0.6},
        ),
    ],
    ids=["khong-mai", "mai-bang", "mai-doc-khong-cot", "san-thuong-va-tum", "hai-mai-doc"],
)
def test_roof_draft_maps_roof_specs_to_the_coarse_reference(roofs: tuple[RoofSpec, ...], expected: Json) -> None:
    """Đáp án mái chỉ có loại thô: `flat` → bằng, ba dạng còn lại → dốc, lẫn cả hai → hỗn hợp; cốt chân lấy diềm cao
    nhất (đáp án nhận cả chân mái lẫn tum), độ chắc là mái kém chắc nhất; không có mái thì không trả lời."""
    assert roof_draft(_building(*roofs)) == expected


def test_predict_set_carries_the_roof_of_the_product_proposal(tmp_path: Path) -> None:
    """Bản nháp mái đi cùng dự đoán của bộ, lấy từ `propose_building` trên chính các hình máy đề xuất."""
    dirs = _cache(tmp_path, {"S__s00": (TWO, _label("S__f00_plan", "S__f01_plan"))})
    roof = predict_set(load_sheets(*dirs)["S"])["roof"]
    assert sorted(roof) == ["base_level_m", "confidence", "level_confidence", "top_level_m", "type"]
    assert roof["type"] in ("flat", "pitched", "mixed")
    assert 0 <= roof["confidence"] <= 1


def test_heights_pair_consecutive_storeys_and_prefer_the_floor_with_a_height() -> None:
    """Chiều cao gắn cặp số tầng liền nhau. Trùng số tầng: hình có chiều cao (đại diện) thắng hình đứng trước không có,
    rồi tới hình đứng trước. Tầng trên cùng, tầng không số, tầng thiếu chiều cao bị bỏ."""
    floors = {
        0: FloorProposal("v0", "plan", 0.9, storey_no=1),
        1: FloorProposal("v1", "plan", 0.9, storey_no=1, height_mm=3300, height_source="plan", height_confidence=0.8),
        2: FloorProposal("v2", "plan", 0.9, storey_no=1, height_mm=9999),
        3: FloorProposal("v3", "plan", 0.9, storey_no=2),
        4: FloorProposal("v4", "plan", 0.9, storey_no=3, height_mm=3000),
        5: FloorProposal("v5", "plan", 0.9),
    }
    assert _heights(floors) == [{"from": 1, "to": 2, "height_m": 3.3, "source": "plan", "confidence": 0.8}]
    assert _heights({}) == []


@pytest.mark.parametrize(
    ("wire", "scored"),
    [("plan", "plan"), ("roof", "roof"), ("site", "site"), ("detail", "unknown"), ("other", "unknown")],
)
def test_kind_maps_unscored_wire_kinds_to_unknown(wire: str, scored: str) -> None:
    """`detail`/`other` không có trong thước → `unknown`; loại được chấm và `site` giữ nguyên."""
    assert _kind(wire) == scored


def test_split_by_exposure_keeps_order_and_parts() -> None:
    """27 bộ đã lộ tách khỏi phần sạch, giữ thứ tự; phần vắng là rỗng; `train` không sang tệp mới."""
    clean, exposed = split_by_exposure({"test": ["A", "DS-248", "B"], "train": ["DS-250"]})
    assert clean == {"test": ["A", "B"], "dev": []}
    assert exposed == {"test": ["DS-248"], "dev": []}
    assert len(EXPOSED) == 27


def test_load_sheets_refuses_incomplete_or_broken_cache(tmp_path: Path) -> None:
    """Thiếu manifest (lượt dựng chưa xong), thiếu nhãn tờ, thiếu tệp đệm, đệm sai hợp đồng: đều nổ, không lặng lẽ
    thành "không dự đoán"."""
    with pytest.raises(ValueError, match="MANIFEST"):
        load_sheets(tmp_path, tmp_path)
    readings, labels = _cache(tmp_path, {"S__s00": (TWO, None)})
    with pytest.raises(ValueError, match="không có nhãn"):
        load_sheets(readings, labels)
    _write(labels / "S__s00.json", _label("a", "b"))
    assert list(load_sheets(readings, labels)) == ["S"]
    (readings / "S__s00.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="width_px"):
        load_sheets(readings, labels)
    (readings / "S__s00.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_sheets(readings, labels)


def _root(root: Path) -> None:
    """Gốc dữ liệu tổng hợp: một bộ sạch có nhãn tờ + đáp án, một bộ đã lộ chỉ có tên trong split."""
    _write(root / "split.json", {"test": ["S"], "dev": ["DS-248"]})
    _cache(root, {"S__s00": (TWO, _label("S__f00_plan", "S__f01_plan"))})
    views = [{"id": f"S__f0{i}_plan", "kind": "plan", "floor_no": i + 1} for i in range(2)]
    floors = [{"floor_no": i + 1, "view_id": f"S__f0{i}_plan"} for i in range(2)]
    truth = {"set": "S", "vn": False, "part": "test", "views": views, "floors": floors}
    _write(root / "truth" / "S.json", truth | {"storey_heights": [], "stack": []})


def test_cli_predictions_score_and_repeat_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Đệm → dự đoán → bộ chấm K-33 trên phần sạch: đúng 2/2 loại, tầng, khung; chạy lại ra tệp trùng từng byte."""
    _root(tmp_path)
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    out = tmp_path / "pred"
    args = ["--readings", str(tmp_path / "readings"), "--out", str(out)]
    assert main(args) == 0
    assert "1 bộ" in capsys.readouterr().out
    first = (out / "S.json").read_bytes()
    assert main(args) == 0
    assert (out / "S.json").read_bytes() == first
    assert json.loads((out / "split_clean.json").read_text(encoding="utf-8")) == {"test": ["S"], "dev": []}
    assert json.loads((out / "split_exposed.json").read_text(encoding="utf-8")) == {"test": [], "dev": ["DS-248"]}
    rep_path = tmp_path / "rep.json"
    clean = str(out / "split_clean.json")
    assert score_main(["--pred", str(out), "--part", "test", "--split-file", clean, "--json", str(rep_path)]) == 0
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    assert [rep["metrics"]["test/all"][k]["correct"] for k in ("kind", "storey")] == [2, 2]
    assert rep["sheets"]["test/all"]["recalled"] == 2


def test_cli_fails_when_split_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Không đọc được `split.json` thì thoát mã 2, không ghi dự đoán nào."""
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    assert main(["--readings", str(tmp_path), "--out", str(tmp_path / "pred")]) == 2
    assert "split.json" in capsys.readouterr().err
    assert not (tmp_path / "pred").exists()
