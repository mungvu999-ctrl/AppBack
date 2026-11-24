"""Thước Mức 3: đầu ra giả đúng hoàn toàn = 100 %, sai cố ý báo đúng chỗ (tiêu chí E1 Mức 3); dữ liệu tổng hợp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.bench.muc3 import metrics, sheets
from tools.bench.muc3.io import load_json
from tools.bench.muc3.metrics import Json, score_set
from tools.bench.muc3.report import aggregate, fmt_f, pct, render, risk_table, run


def truth(name: str, *, vn: bool = True, part: str = "test") -> Json:
    """Đáp án tổng hợp: hai mặt bằng, một mặt cắt, một hình `unknown` (không chấm), một chồng xanh, hai tầng thang."""
    return {
        "set": name,
        "vn": vn,
        "part": part,
        "views": [
            {"id": f"{name}_p1", "kind": "plan", "floor_no": 1},
            {"id": f"{name}_p2", "kind": "plan?", "floor_no": 2},
            {"id": f"{name}_s", "kind": "section", "floor_no": None},
            {"id": f"{name}_u", "kind": "unknown", "floor_no": None},
        ],
        "floors": [{"floor_no": 1, "view_id": f"{name}_p1"}, {"floor_no": 2, "view_id": f"{name}_p2"}],
        "storey_heights": [{"from": 1, "to": 2, "height_m": 3.0, "basis": "plan+section"}],
        "stack": [{"a": 1, "b": 2, "colour": "xanh", "shift_mm": [100, 0]}],
        "stairs": [{"floor_no": 1, "stairs": [{}]}, {"floor_no": 2, "stairs": [{}]}],
    }


def pred(name: str, *, kind: str = "section", height: float = 3.0, shift: list[float] | None = None) -> Json:
    """Dự đoán đúng hoàn toàn (mặc định); tham số đổi đúng một thứ cho ca sai cố ý."""
    return {
        "views": [
            {"id": f"{name}_p1", "kind": "plan", "floor_no": 1, "confidence": 0.95},
            {"id": f"{name}_p2", "kind": "plan", "floor_no": 2, "confidence": 0.95},
            {"id": f"{name}_s", "kind": kind, "floor_no": None, "confidence": 0.95},
        ],
        "floors": [{"floor_no": 1, "view_id": f"{name}_p1"}, {"floor_no": 2, "view_id": f"{name}_p2"}],
        "storey_heights": [{"from": 1, "to": 2, "height_m": height, "confidence": 0.95}],
        "stack": [{"a": f"{name}_p1", "b": f"{name}_p2", "shift_mm": shift or [100, 0], "confidence": 0.95}],
        "stair_cores": [{"floors": [1, 2], "confidence": 0.8}],
    }


def test_perfect_prediction_scores_everything_right() -> None:
    """Đầu ra đúng hoàn toàn: mọi chỉ tiêu đủ 100 %, không dự đoán thừa, `unknown` không bị chấm."""
    r = score_set(truth("S"), pred("S"))
    assert r["set_exact"] is True
    assert r["stairs"] is True
    for k in metrics.ITEM_METRICS:
        assert all(ok for _, ok in r["items"][k]), k
    assert [len(r["items"][k]) for k in ("kind", "storey", "h_plan_section", "h_plan", "stack")] == [3, 2, 1, 0, 1]
    assert sum(r["extra"].values()) == 0


def test_deliberate_errors_are_reported_where_they_happen() -> None:
    """Sai loại hình, sai chiều cao, sai độ dời: mỗi lỗi hiện đúng ở chỉ tiêu của nó, bộ không còn "đúng hoàn toàn"."""
    r = score_set(truth("X"), pred("X", kind="elevation", height=3.3, shift=[100, 500]))
    assert [ok for _, ok in r["items"]["kind"]] == [True, True, False]
    assert r["items"]["h_plan_section"] == [(0.95, False)]
    assert r["items"]["stack"] == [(0.95, False)]
    assert [ok for _, ok in r["items"]["storey"]] == [True, True]
    assert r["set_exact"] is False


def test_height_tolerance_is_inclusive_on_rounded_millimetres() -> None:
    """3,6 so 3,55 m lệch đúng 50 mm (số thực lệch 5e-17) phải đạt; 3,549 m (51 mm) thì trượt."""
    t = {"storey_heights": [{"from": 1, "to": 2, "height_m": 3.6, "basis": "plan"}]}
    p: Json = {"storey_heights": [{"from": 1, "to": 2, "height_m": 3.55, "confidence": 0.9}]}
    assert metrics.height_items(t, p, "plan") == [(0.9, True)]
    p["storey_heights"][0]["height_m"] = 3.549
    assert metrics.height_items(t, p, "plan") == [(0.9, False)]


def test_height_reviewed_basis_counts_as_plan_section_and_unscored_is_skipped() -> None:
    """Chiều cao người soát là chỉ tiêu chính (`plan+section`); `scored` = False hay `height_m` null thì bỏ."""
    t = {
        "storey_heights": [
            {"from": 1, "to": 2, "height_m": 3.0, "basis": "reviewed"},
            {"from": 2, "to": 3, "height_m": 3.0, "basis": "reviewed", "scored": False},
            {"from": 3, "to": 4, "height_m": None, "basis": "reviewed"},
            {"from": 4, "to": 5, "height_m": 3.0, "basis": "plan"},
        ]
    }
    assert metrics.height_items(t, {}, "plan+section") == [(None, False)]
    assert metrics.height_items(t, {"storey_heights": [{"from": 1, "to": 2}]}, "plan+section") == [(None, False)]
    assert metrics.height_items(t, {}, "plan") == [(None, False)]


def test_storey_uses_floor_confidence_and_skips_unscored_floors() -> None:
    """Độ chắc của tầng lấy `floor_confidence` nếu có; tầng không hình, `scored` False, số không nguyên không chấm."""
    t = {
        "floors": [
            {"floor_no": 1, "view_id": "a"},
            {"floor_no": 2, "view_id": None},
            {"floor_no": 3, "view_id": "c", "scored": False},
            {"floor_no": None, "view_id": "d"},
            {"floor_no": 4, "view_id": "gone"},
        ]
    }
    p: Json = {"views": [{"id": "a", "floor_no": 1, "confidence": 0.3}]}
    assert metrics.storey_items(t, p) == [(0.3, True), (None, False)]
    p["views"][0]["floor_confidence"] = 0.7
    assert metrics.storey_items(t, p)[0] == (0.7, True)


def test_missing_prediction_counts_as_wrong_everywhere() -> None:
    """Thiếu cả tệp dự đoán: không mục nào có độ chắc, mọi mục sai, thang không tìm thấy."""
    r = score_set(truth("M"), {})
    assert r["items"]["kind"][0] == (None, False)
    assert r["set_exact"] is False
    assert r["stairs"] is False
    assert r["items"]["stack"] == [(None, False)]


def test_stack_pair_without_shift_in_prediction_is_wrong() -> None:
    """Cặp chồng có trong dự đoán nhưng `shift_mm` null: sai, không có độ chắc; đáp án không xanh bị bỏ."""
    t = truth("S")
    t["stack"].append({"a": 2, "b": 3, "colour": "cam", "shift_mm": [0, 0]})
    t["stack"].append({"a": 3, "b": 4, "colour": "xanh", "shift_mm": None})
    p = pred("S")
    p["stack"][0]["shift_mm"] = None
    assert metrics.stack_items(t, p) == [(None, False)]


def test_extras_are_counted_per_metric() -> None:
    """Dự đoán thừa: hình lạ, tầng lạ, chiều cao theo nguồn `plan` hay khác, cặp chồng lạ; chiều cao null không tính."""
    p = pred("S")
    p["views"].append({"id": "stray", "kind": "plan"})
    p["views"].append("rác")
    p["floors"].append({"floor_no": 9, "view_id": "stray"})
    p["storey_heights"] += [
        {"from": 5, "to": 6, "height_m": 3.0, "source": "plan"},
        {"from": 6, "to": 7, "height_m": 3.0, "source": "section"},
        {"from": 7, "to": 8, "height_m": None},
    ]
    p["stack"].append({"a": "stray", "b": "S_p1", "shift_mm": [0, 0]})
    assert score_set(truth("S"), p)["extra"] == {
        "kind": 1,
        "storey": 1,
        "h_plan_section": 1,
        "h_plan": 1,
        "stack": 1,
    }


def test_stairs_need_consecutive_floors_in_a_core() -> None:
    """Chỉ chấm thang khi đáp án có hai tầng liền kề cùng thang; lõi dự đoán phải liệt kê đủ cả hai tầng."""
    t = truth("S")
    assert metrics.stair_pairs(t) == [(1, 2)]
    assert metrics.stairs_ok(t, {"stair_cores": [{"floors": [1]}]}) is False
    assert metrics.stairs_ok(t, {"stair_cores": [{"floors": [1, 2, 3]}]}) is True
    assert metrics.stairs_ok({"stairs": [{"floor_no": 1, "stairs": [{}]}]}, {}) is None
    assert metrics.stairs_ok({}, {}) is None


def test_aggregate_and_risk() -> None:
    """Gộp theo phần/nhóm; lỗi im lặng = sai trong số dự đoán chắc >= 0,9; bộ không DS- thuộc nhóm `other`."""
    good = score_set(truth("DS-1"), pred("DS-1"))
    bad = score_set(truth("X", vn=False), pred("X", kind="elevation", height=3.3, shift=[100, 500]))
    a = aggregate([good, bad], "test", "all")
    assert a["kind"]["correct"] == 5
    assert a["kind"]["total"] == 6
    assert a["set_exact"] == {"correct": 1, "total": 2}
    assert a["h_plan_section"]["risk"]["silent_error_rate"] == pytest.approx(0.5)
    assert a["stairs"] == {"correct": 2, "total": 2}
    assert aggregate([good, bad], "test", "vn")["n_sets"] == 1
    assert aggregate([good, bad], "test", "other")["n_sets"] == 1
    assert aggregate([good, bad], "dev", "all")["n_sets"] == 0


def test_risk_table_edges() -> None:
    """Không dự đoán: tỉ lệ `None`; chỉ có dự đoán dưới mốc: độ phủ 0 và lỗi `None`."""
    empty = risk_table([(None, False)])
    assert empty["n_pred"] == 0
    assert empty["silent_error_rate"] is None
    assert empty["coverage_ge_0.9"] is None
    low = risk_table([(0.6, False), (0.8, True)])
    assert low["coverage_ge_0.9"] == 0.0
    assert low["silent_error_rate"] is None
    assert low["risk_coverage"][0.5]["error"] == 0.5


def test_formatting_helpers() -> None:
    """`pct` an toàn với tổng 0; `fmt_f` hiển thị `-` cho `None`."""
    assert pct(0, 0) == "0/0 -"
    assert pct(1, 4) == "1/4 25.0%"
    assert fmt_f(None) == "-"
    assert fmt_f(0.944) == "94%"


def test_box_matching_is_one_to_one_by_iou() -> None:
    """Ghép khung tham lam một-một; khung không giao hay dưới 0,5 không ghép; một đáp án không nhận hai khung."""
    assert sheets.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert sheets.match_boxes([{"box": [0, 0, 10, 10]}], [{"box": [1, 1, 10, 10]}, {"box": [50, 50, 60, 60]}]) == [
        (0, 0)
    ]
    assert sheets.match_boxes([{"box": [0, 0, 10, 10]}], [{"box": [0, 0, 10, 10]}, {"box": [0, 0, 10, 9]}]) == [(0, 0)]
    one = {
        "views": [
            {"box": [0, 0, 10, 10], "kind": "plan", "minor": False},
            {"box": [20, 0, 30, 10], "kind": "section", "minor": True},
        ]
    }
    got = sheets.score_sheet(
        one, [{"box": [0, 0, 10, 10], "kind": "plan"}, {"box": [100, 100, 110, 110], "kind": "plan"}]
    )
    assert got == [1, 1, 2, 1, 1, 1]


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8", newline="\n")


def test_run_reads_truth_dir_and_counts_missing_sheet_views_as_misses(tmp_path: Path) -> None:
    """`run` chỉ đọc đáp án từ thư mục được đưa vào; không có `sheet_views` thì bỏ sót hết hình chính."""
    t = truth("DS-1")
    _write(tmp_path / "tr" / "DS-1.json", t)
    _write(tmp_path / "sh" / "DS-1__s00.json", {"views": [{"box": [0, 0, 10, 10], "kind": "plan", "minor": False}]})
    _write(tmp_path / "sh" / "XX__s00.json", {"views": []})
    _write(tmp_path / "sh" / "DS-9__s00.json", {"views": []})
    (tmp_path / "pr").mkdir()
    split = {"test": ["DS-1", "absent"], "dev": []}
    rep = run(tmp_path / "pr", ["test"], split, tmp_path / "tr", tmp_path / "sh")
    assert rep["metrics"]["test/all"]["n_sets"] == 1
    assert rep["truth_dir"].endswith("tr")
    assert rep["sheets"]["test/all"]["truth_major"] == 1
    assert rep["sheets"]["test/all"]["recalled"] == 0
    assert "sheet splitting" in render(rep, ["test"])
    no_sheets = run(tmp_path / "pr", ["test"], split, tmp_path / "tr", tmp_path / "none")
    assert no_sheets["sheets"] == {}
    assert "sheet splitting" not in render(no_sheets, ["test"])


def test_load_json_rejects_missing_broken_and_non_object(tmp_path: Path) -> None:
    """Tệp thiếu, hỏng hay không phải object cho `None` (bộ chấm đếm thành không dự đoán)."""
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    assert load_json(tmp_path / "nope.json") is None
    assert load_json(tmp_path / "bad.json") is None
    assert load_json(tmp_path / "list.json") is None
    _write(tmp_path / "ok.json", {"a": 1})
    assert load_json(tmp_path / "ok.json") == {"a": 1}
