"""Bộ chấm mái của thước Mức 3 (NHA-10a): luật chấm của `score_roof.py` cũ, Wilson 95 %, hai mốc "trước"."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.bench.muc3.metrics import Json
from tools.bench.muc3.roof import Verdict, draft_of, judge, load_refs, main, render, run, tally, wilson
from tools.bench.tests.test_muc3_score import _write

REF: Json = {"set": "A", "type": "flat", "base_level_m": 10.0, "top_level_m": 11.0, "stair_tower_level_m": 13.0}


def test_judge_keeps_the_rules_of_the_old_scorer() -> None:
    """Ca tự kiểm của `score_roof.py`: loại so bằng nhau, cốt chân khớp chân mái hoặc tum, dung sai 0,2 m."""
    draft = {"type": "mixed", "base_level_m": 13.1, "top_level_m": 11.3, "confidence": 0.9, "level_confidence": 0.7}
    assert judge(REF, draft) == {"type": (True, False, 0.9), "base": (True, True, 0.7), "top": (True, False, 0.7)}
    assert judge(REF, {"type": "unknown"})["type"] == (False, False, 0)
    assert judge(REF, {}) == {"type": (False, False, 0), "base": (False, False, 0), "top": (False, False, 0)}


def test_judge_skips_fields_the_reference_does_not_have() -> None:
    """Đáp án `unknown` hay thiếu cốt thì trường đó không được chấm, dù dự đoán có trả lời."""
    assert judge({"type": "unknown"}, {"type": "flat", "base_level_m": 3.0}) == {}
    only_base = judge({"type": None, "base_level_m": 3.0}, {"base_level_m": 3.19, "level_confidence": 0.8})
    assert only_base == {"base": (True, True, 0.8)}
    assert judge({"base_level_m": 3.0}, {"base_level_m": 3.3})["base"] == (True, False, 0)


def test_draft_of_reads_both_prediction_layouts() -> None:
    """Dự đoán đường sản phẩm để bản nháp mái dưới khoá `roof`; thư mục `pred_roof` cũ để phẳng; thiếu tệp là rỗng."""
    assert draft_of({"views": [], "roof": {"type": "flat"}}) == {"type": "flat"}
    assert draft_of({"type": "pitched", "confidence": 0.6}) == {"type": "pitched", "confidence": 0.6}
    assert draft_of({"views": [], "roof": None}) == {}
    assert draft_of(None) == {}


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [(5, 10, 0.2366, 0.7634), (0, 10, 0.0, 0.2775), (10, 10, 0.7225, 1.0)],
)
def test_wilson_95_interval(k: int, n: int, low: float, high: float) -> None:
    """Khoảng tin cậy Wilson 95 % của k/n; mẫu rỗng không có khoảng."""
    assert wilson(k, n) == pytest.approx((low, high), abs=1e-4)
    assert wilson(0, 0) is None


def test_tally_counts_answers_and_confident_mistakes() -> None:
    """Mỗi trường: đúng / có trả lời / tổng, Wilson của đúng/tổng, số sai trong câu trả lời chắc >= 0,9 và >= 0,7."""
    rows: list[dict[str, Verdict]] = [
        {"type": (True, True, 0.95), "base": (True, False, 0.75)},
        {"type": (True, False, 0.95)},
        {"type": (False, False, 0)},
    ]
    got = tally(rows)
    assert got["type"] == {
        "correct": 1,
        "answered": 2,
        "total": 3,
        "wilson": pytest.approx(wilson(1, 3)),
        "wrong_at": {"0.9": [1, 2], "0.7": [1, 2]},
    }
    assert got["base"]["wrong_at"] == {"0.9": [0, 0], "0.7": [1, 1]}
    empty = {"0.9": [0, 0], "0.7": [0, 0]}
    assert got["top"] == {"correct": 0, "answered": 0, "total": 0, "wilson": None, "wrong_at": empty}


def _root(root: Path) -> list[Json]:
    """Gốc tổng hợp: bốn bộ có đáp án mái (một bộ `unknown`), ba dự đoán ở hai dạng tệp, một bộ không có dự đoán."""
    refs: list[Json] = [
        {"set": "A", "type": "flat", "base_level_m": 10.0, "top_level_m": 11.0, "sure": True},
        {"set": "B", "type": "pitched", "base_level_m": None, "top_level_m": None, "sure": False},
        {"set": "C", "type": "mixed", "base_level_m": 7.0, "top_level_m": 9.0, "sure": True},
        {"set": "D", "type": "unknown", "base_level_m": None, "top_level_m": None},
    ]
    _write(root / "truth_review" / "roof_review.json", refs)
    _write(root / "split.json", {"dev": ["A", "B", "D"], "test": ["C"], "train": ["Z"]})
    roof = {"type": "flat", "confidence": 0.95, "base_level_m": 10.1, "top_level_m": None, "level_confidence": 0.5}
    _write(root / "pred" / "A.json", {"views": [], "roof": roof})
    _write(root / "pred" / "B.json", {"type": "flat", "confidence": 0.95})
    _write(root / "pred" / "D.json", {"roof": {"type": "flat"}})
    return refs


def test_run_scores_parts_and_sure_sets_next_to_the_baselines(tmp_path: Path) -> None:
    """Chấm theo phần và nhóm "chắc"; hai mốc "trước" (không có mái, luôn đoán mái bằng) tính trên cùng tập bộ."""
    refs = _root(tmp_path)
    split = json.loads((tmp_path / "split.json").read_text(encoding="utf-8"))
    report = run(tmp_path / "pred", refs, split, ["dev", "test"])
    assert sorted(report) == ["dev/all", "dev/sure", "test/all", "test/sure"]
    dev = report["dev/all"]
    assert (dev["sets"], dev["missing"]) == (3, 0)
    assert (report["test/all"]["sets"], report["test/all"]["missing"]) == (1, 1)  # C không có tệp dự đoán
    assert [dev["fields"]["type"][key] for key in ("correct", "answered", "total")] == [1, 2, 2]
    assert dev["fields"]["type"]["wrong_at"]["0.9"] == [1, 2]
    assert [dev["fields"]["base"][key] for key in ("correct", "answered", "total")] == [1, 1, 1]
    assert [dev["fields"]["top"][key] for key in ("correct", "answered", "total")] == [0, 0, 1]
    assert dev["baselines"]["always_flat"]["type"]["correct"] == 1
    assert dev["baselines"]["none"]["type"] == tally([{"type": (False, False, 0)}] * 2)["type"]
    assert report["dev/sure"]["sets"] == 1
    assert report["dev/sure"]["fields"]["type"]["correct"] == 1
    lost = report["test/all"]
    assert [lost["fields"]["type"][key] for key in ("correct", "answered", "total")] == [0, 0, 1]
    assert lost["baselines"]["always_flat"]["type"]["correct"] == 0


def test_render_prints_counts_with_the_interval(tmp_path: Path) -> None:
    """Bảng chữ: mỗi nhóm một khối, mỗi trường một dòng `đúng/trả lời/tổng` kèm khoảng Wilson, và dòng mốc "trước"."""
    refs = _root(tmp_path)
    split = json.loads((tmp_path / "split.json").read_text(encoding="utf-8"))
    text = render(run(tmp_path / "pred", refs, split, ["dev"]))
    assert "dev all (3 bộ, 0 không đọc được dự đoán)" in text
    assert "dev sure (1 bộ, 0 không đọc được dự đoán)" in text
    assert "type  1/2/2" in text
    assert "[9%-91%]" in text
    assert "top   0/0/1" in text
    assert "luôn mái bằng: type 1/2" in text
    assert "test" not in text


def test_cli_scores_from_the_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mặc định đọc `<gốc>/truth_review/roof_review.json` và `<gốc>/split.json`; `--json` ghi báo cáo đầy đủ."""
    _root(tmp_path)
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    out = tmp_path / "rep.json"
    assert main(["--pred", str(tmp_path / "pred"), "--part", "dev", "--json", str(out)]) == 0
    assert "dev all (3 bộ, 0 không đọc được dự đoán)" in capsys.readouterr().out
    assert sorted(json.loads(out.read_text(encoding="utf-8"))) == ["dev/all", "dev/sure"]
    (tmp_path / "pred" / "C.json").write_text("{", encoding="utf-8")  # tệp hỏng cũng được đếm
    assert main(["--pred", str(tmp_path / "pred")]) == 0
    assert "test all (1 bộ, 1 không đọc được dự đoán)" in capsys.readouterr().out


def test_cli_fails_when_an_input_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Không đọc được `split.json` hay tệp đáp án mái thì thoát mã 2 kèm tên tệp, không in bảng."""
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    assert main(["--pred", str(tmp_path)]) == 2
    assert "split.json" in capsys.readouterr().err
    _write(tmp_path / "split.json", {"dev": [], "test": []})
    assert main(["--pred", str(tmp_path)]) == 2
    assert "roof_review.json" in capsys.readouterr().err
    _write(tmp_path / "bad.json", {"not": "a list"})
    assert main(["--pred", str(tmp_path), "--roof-truth", str(tmp_path / "bad.json")]) == 2
    assert load_refs(tmp_path / "bad.json") is None
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert load_refs(tmp_path / "broken.json") is None
