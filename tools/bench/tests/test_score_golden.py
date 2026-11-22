"""Bộ chấm mới phải ra ĐÚNG TỪNG Ô như bộ chấm gốc trên bộ dữ liệu tổng hợp (bản vàng do `score.py` gốc sinh)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.bench.score.io import Bench
from tools.bench.score.report import render_markdown, score_run

# Fixture `synth_root` dùng chung (luật cổng: chỉ một conftest.py ở gốc repo).
pytest_plugins = ["tools.bench.tests.fixtures"]

DATA = Path(__file__).parent / "data"


def _normalized(res: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Qua JSON như tệp `score.json` thật (numpy -> float) để so với bản vàng."""
    out: list[dict[str, Any]] = json.loads(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    return out


def test_score_matches_golden_cell_by_cell(synth_root: Path) -> None:
    """Từng ca, từng tiêu chí, từng số đo trong `info` bằng bản vàng; kết luận, im lặng, outcome cũng vậy."""
    res = score_run(Bench(synth_root, synth_root / "run"))
    golden: list[dict[str, Any]] = json.loads((DATA / "golden-score.json").read_text(encoding="utf-8"))
    got = _normalized(res)
    assert [r["id"] for r in got] == [r["id"] for r in golden]
    for g, r in zip(golden, got, strict=True):
        assert r == g, g["id"]


def test_markdown_matches_golden(synth_root: Path) -> None:
    """Bảng markdown (`score.md`) giống bản vàng từng dòng."""
    res = score_run(Bench(synth_root, synth_root / "run"))
    text = "\n".join(render_markdown("run", res)) + "\n"
    assert text == (DATA / "golden-score.md").read_text(encoding="utf-8")
