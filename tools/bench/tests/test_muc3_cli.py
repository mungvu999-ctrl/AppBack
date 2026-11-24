"""CLI và cấu hình của thước Mức 3: đường dẫn chỉ qua biến môi trường, không có mặc định theo máy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.bench.muc3.__main__ import main
from tools.bench.muc3.config import ConfigError, muc3_root
from tools.bench.tests.test_muc3_score import _write, pred, truth


def test_muc3_root_requires_env_and_strips_blanks() -> None:
    """Thiếu hay trống `BENCH_MUC3_ROOT` thì báo lỗi nêu tên biến; có thì trả đường dẫn."""
    with pytest.raises(ConfigError, match="BENCH_MUC3_ROOT"):
        muc3_root({})
    with pytest.raises(ConfigError, match="BENCH_MUC3_ROOT"):
        muc3_root({"BENCH_MUC3_ROOT": "  "})
    assert muc3_root({"BENCH_MUC3_ROOT": "/x/y"}) == Path("/x/y")


def _build(root: Path) -> None:
    """Gốc dữ liệu tổng hợp: một bộ test đúng hoàn toàn, truth thường và truth_v3 (đã đổi chiều cao đáp án)."""
    _write(root / "split.json", {"test": ["DS-1"], "dev": []})
    t = truth("DS-1")
    _write(root / "truth" / "DS-1.json", t)
    t3 = truth("DS-1")
    t3["storey_heights"][0]["height_m"] = 4.0
    _write(root / "truth_v3" / "DS-1.json", t3)
    _write(root / "pred" / "DS-1.json", pred("DS-1"))


def test_cli_scores_default_and_alternative_truth_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mặc định đọc `truth/`; `--truth-dir truth_v3` đổi đáp án nên chiều cao trượt; `--json` ghi báo cáo."""
    _build(tmp_path)
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    out_json = tmp_path / "rep.json"
    assert main(["--pred", str(tmp_path / "pred"), "--part", "test", "--json", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "4 height plan+section" in text
    assert "1/1 100.0%" in text
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["metrics"]["test/all"]["h_plan_section"]["correct"] == 1
    assert main(["--pred", str(tmp_path / "pred"), "--truth-dir", str(tmp_path / "truth_v3")]) == 0
    assert "0/1 0.0%" in capsys.readouterr().out


def test_cli_fails_when_split_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Không đọc được `split.json` thì thoát mã 2 và nói rõ trên stderr, không đoán."""
    monkeypatch.setenv("BENCH_MUC3_ROOT", str(tmp_path))
    assert main(["--pred", str(tmp_path)]) == 2
    assert "split.json" in capsys.readouterr().err
