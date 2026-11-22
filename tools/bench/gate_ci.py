"""Lệnh `gate ci` (K-17b): replay sau-ML của worktree trên `ml-cache` → chấm hai tập → so baseline.

Công tắc `BENCH_GATE` (biến repo của GitHub hoặc môi trường máy):
- `off`: tắt hẳn, mã 0.
- `auto` (mặc định): thiếu dữ liệu bench (`BENCH_ROOT`, `ml-cache`, lượt gốc, đáp án) thì báo "bỏ qua" và mã 0 — dữ
  liệu ở ngoài repo (Q8 a) và chưa có kho riêng cho CI tải (NO-468), nên runner GitHub hiện luôn rơi vào nhánh này.
- `required`: thiếu dữ liệu là lỗi (mã 2) — bật khi đã có bước tải dữ liệu và job thành check bắt buộc.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path

from tools.bench import config
from tools.bench.console import say, warn
from tools.bench.gate import compare, write_report
from tools.bench.replay import replay
from tools.bench.score.io import Bench
from tools.bench.score.report import render_markdown, score_run, write_outputs

MODES = ("off", "auto", "required")


def gate_mode(env: Mapping[str, str]) -> str:
    """Giá trị công tắc `BENCH_GATE` (mặc định `auto`); ném `ValueError` khi lạ."""
    mode = (env.get("BENCH_GATE") or "auto").strip().lower()
    if mode not in MODES:
        raise ValueError(f"BENCH_GATE={mode!r} không hợp lệ (chọn {', '.join(MODES)})")
    return mode


def missing_data(env: Mapping[str, str]) -> list[str]:
    """Những thứ còn thiếu để replay + chấm (rỗng = đủ): gốc, `ml-cache`, lượt gốc và MANIFEST của từng tập."""
    raw = (env.get("BENCH_ROOT") or "").strip()
    if not raw:
        return ["BENCH_ROOT chưa đặt"]
    root = Path(raw)
    need = [root / "ml-cache"]
    for orig, score_root in config.replay_sets(root, env).values():
        need += [orig, score_root / "MANIFEST.csv", score_root / "answers"]
    return [str(p) for p in need if not p.exists()]


def notice(msg: str, env: Mapping[str, str]) -> None:
    """Một dòng thông báo; trên GitHub Actions in dạng chú thích `::notice::` để hiện trên trang PR."""
    say(f"::notice title=bench-replay::{msg}" if env.get("GITHUB_ACTIONS") == "true" else msg)


def score_sets(out: Path, root: Path, env: Mapping[str, str]) -> dict[str, Path]:
    """Chấm từng tập đã replay dưới `out/<tập>` (ghi `score.json`/`score.md`); trả {tập: thư mục lượt}."""
    runs = {}
    for name, (_orig, score_root) in config.replay_sets(root, env).items():
        run = out / name
        res = score_run(Bench(score_root, run))
        write_outputs(run, res, render_markdown(f"{name} (replay)", res))
        runs[name] = run
    return runs


def run(a: argparse.Namespace, env: Mapping[str, str] | None = None) -> int:
    """Chạy cổng theo công tắc; trả mã thoát (0 đạt/bỏ qua, 1 tụt, 2 lỗi dữ liệu hoặc thiếu dữ liệu khi `required`)."""
    e = os.environ if env is None else env
    mode = gate_mode(e)
    if mode == "off":
        notice("cổng bench tắt (BENCH_GATE=off)", e)
        return 0
    gaps = missing_data(e)
    if gaps:
        msg = "thiếu dữ liệu bench: " + "; ".join(gaps) + " (dữ liệu ngoài repo, chờ kho riêng — NO-468)"
        if mode == "required":
            warn(msg)
            return 2
        notice("bỏ qua cổng bench — " + msg, e)
        return 0
    root = config.bench_root(e)
    replay_args = [str(a.worktree), str(a.out), "--set", "all", "--jobs", str(a.jobs)]
    replay.main([*replay_args, "--local"] if a.local else replay_args)
    runs = score_sets(a.out, root, e)
    baseline = json.loads(a.baseline.read_text(encoding="utf-8"))
    code, lines = compare(baseline, runs, (a.max_pass_drop, a.max_silent_rise), config.ml_cache(root))
    summary = e.get("GITHUB_STEP_SUMMARY")
    write_report(lines, Path(summary) if summary else None)
    return code
