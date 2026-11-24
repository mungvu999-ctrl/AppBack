"""CLI: `BENCH_MUC3_ROOT=... python -X utf8 -m tools.bench.muc3 --pred <thư mục> [--part test|dev|test2]`.

Mỗi bộ hồ sơ một tệp `<thư mục>/<bộ>.json` (dạng ở `SCHEMA.md`). Đáp án mặc định `<gốc>/truth`, chọn `truth_v2`
hay `truth_v3` bằng `--truth-dir`; nhãn tờ lấy `<truth-dir>/sheets` nếu có, không thì `<gốc>/sheets`.
In bảng ra stdout; `--json` ghi thêm báo cáo đầy đủ.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.bench.muc3.config import muc3_root
from tools.bench.muc3.io import load_json
from tools.bench.muc3.report import render, run


def main(argv: Sequence[str] | None = None) -> int:
    """Chấm một thư mục dự đoán; trả 0, hoặc 2 khi không đọc được `split.json`."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.muc3", description=__doc__)
    ap.add_argument("--pred", type=Path, required=True, help="thư mục dự đoán, mỗi bộ một <bộ>.json")
    ap.add_argument("--part", choices=("test", "dev", "test2"), help="một phần; mặc định test và dev")
    ap.add_argument("--split-file", type=Path, help="mặc định <BENCH_MUC3_ROOT>/split.json")
    ap.add_argument("--truth-dir", type=Path, help="thư mục đáp án từng bộ; mặc định <BENCH_MUC3_ROOT>/truth")
    ap.add_argument("--json", type=Path, help="ghi báo cáo đầy đủ ra tệp JSON")
    a = ap.parse_args(argv)
    root = muc3_root()
    split = load_json(a.split_file or root / "split.json")
    if split is None:
        sys.stderr.write("không đọc được split.json\n")
        return 2
    truth_dir = a.truth_dir or root / "truth"
    sheets = truth_dir / "sheets"
    parts = [a.part] if a.part else ["test", "dev"]
    rep = run(a.pred, parts, split, truth_dir, sheets if sheets.is_dir() else root / "sheets")
    sys.stdout.write(render(rep, parts) + "\n")
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
