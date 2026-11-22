"""CLI: `python -X utf8 -m tools.bench.score <thư mục lượt> [--root DIR] [--offsets offsets.json]`.

Ra `<lượt>/score.md` + `score.json` và in bảng ra stdout. Gốc dữ liệu (MANIFEST.csv, plans/, answers/) lấy từ `--root`
hoặc `BENCH_ROOT`; tập F đặt gốc trỏ vào thư mục tập F. Lượt mới: `<run>/<id>/{meta,progress,layer,spatial,
quality}.json` + `page.png`; lượt cũ: `<run>/<id>.layer.json`…
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.bench.config import bench_root
from tools.bench.score.io import Bench
from tools.bench.score.report import render_markdown, score_run, write_outputs


def main(argv: Sequence[str] | None = None) -> int:
    """Chấm một lượt và ghi kết quả; trả mã thoát 0."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.score", description=__doc__)
    ap.add_argument("run", type=Path, help="thư mục lượt (vd runs/r4b)")
    ap.add_argument("--root", type=Path, help="gốc dữ liệu bench; mặc định biến BENCH_ROOT")
    ap.add_argument("--offsets", type=Path, help="{id: [dx, dy]} thay cho dò page.png (lượt cũ)")
    a = ap.parse_args(argv)
    offsets = json.loads(a.offsets.read_text(encoding="utf-8")) if a.offsets else {}
    bench = Bench(a.root or bench_root(), a.run, offsets)
    res = score_run(bench)
    lines = render_markdown(a.run.name, res)
    write_outputs(a.run, res, lines)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
