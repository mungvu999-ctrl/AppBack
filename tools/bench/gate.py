"""Cổng chống tụt (K-17b): so kết quả chấm replay sau-ML với baseline trong repo, từng tập (bench, F).

    python -X utf8 -m tools.bench.gate compare <baseline.json> --set bench=<lượt> --set F=<lượt>
        [--max-pass-drop 2] [--max-silent-rise 0] [--ml-cache DIR] [--report FILE]
    python -X utf8 -m tools.bench.gate snapshot <baseline.json> --set bench=<lượt> --set F=<lượt> --source-sha SHA
        [--ml-cache DIR]
    python -X utf8 -m tools.bench.gate ci <worktree> <thư mục ra> [--baseline FILE] [--jobs 4] [--local]

`<lượt>` là thư mục đã chấm (có `score.json`). Đỏ (mã 1) khi một tập tụt quá `--max-pass-drop` ca ĐẠT (Q8: tụt > 2)
hoặc tăng quá `--max-silent-rise` lỗi im lặng (mặc định 0: tăng ≥ 1 là đỏ). Ca có trong baseline mà thiếu ở lượt mới
tính là không ĐẠT. Mã 2: lỗi dùng/dữ liệu (thiếu tập, baseline chụp trên `ml-cache` khác). `ci`: xem `gate_ci.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.bench.console import say, warn

Json = dict[str, Any]
BASELINE = Path(__file__).with_name("baseline") / "main.json"
FORMAT = 1
OK_VERDICT = "ĐẠT"


@dataclass
class SetDiff:
    """Chênh của một tập giữa baseline và lượt mới (chỉ trên các ca của baseline)."""

    name: str
    base_pass: int
    new_pass: int
    base_silent: int
    new_silent: int
    lost: list[str] = field(default_factory=list)  # ĐẠT → không ĐẠT
    gained: list[str] = field(default_factory=list)  # không ĐẠT → ĐẠT
    new_silent_ids: list[str] = field(default_factory=list)
    fixed_silent_ids: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # có ở baseline, thiếu ở lượt mới


def load_scores(run: Path) -> dict[str, Json]:
    """`<run>/score.json` → {id: {verdict, silent}}."""
    rows = json.loads((run / "score.json").read_text(encoding="utf-8"))
    return {r["id"]: {"verdict": r["verdict"], "silent": bool(r["silent"])} for r in rows}


def cache_digest(cache: Path) -> str:
    """sha256 của cả thư mục `ml-cache` (đường tương đối + nội dung từng tệp, theo thứ tự tên)."""
    h = hashlib.sha256()
    for p in sorted(x for x in cache.rglob("*") if x.is_file()):
        h.update(p.relative_to(cache).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def snapshot(sets: dict[str, Path], source: Json) -> Json:
    """Baseline từ các lượt đã chấm: nguồn (SHA, ml-cache) + kết luận/im lặng từng ca của từng tập."""
    return {"format": FORMAT, "source": source, "sets": {name: load_scores(run) for name, run in sets.items()}}


def compare_set(name: str, base: dict[str, Json], new: dict[str, Json]) -> SetDiff:
    """So một tập theo các ca của baseline; ca thiếu ở lượt mới coi như không ĐẠT, không im lặng."""
    blank: Json = {"verdict": "thiếu", "silent": False}
    got = {cid: new.get(cid, blank) for cid in base}
    d = SetDiff(
        name,
        base_pass=sum(r["verdict"] == OK_VERDICT for r in base.values()),
        new_pass=sum(r["verdict"] == OK_VERDICT for r in got.values()),
        base_silent=sum(r["silent"] for r in base.values()),
        new_silent=sum(r["silent"] for r in got.values()),
    )
    for cid in sorted(base):
        b, n = base[cid], got[cid]
        if cid not in new:
            d.missing.append(cid)
        if (b["verdict"] == OK_VERDICT) != (n["verdict"] == OK_VERDICT):
            (d.lost if b["verdict"] == OK_VERDICT else d.gained).append(cid)
        if b["silent"] != n["silent"]:
            (d.new_silent_ids if n["silent"] else d.fixed_silent_ids).append(cid)
    return d


def failures(diffs: list[SetDiff], max_pass_drop: int, max_silent_rise: int) -> list[str]:
    """Lý do đỏ của cổng (rỗng = đạt)."""
    out = []
    for d in diffs:
        if d.base_pass - d.new_pass > max_pass_drop:
            out.append(f"{d.name}: ĐẠT {d.base_pass} → {d.new_pass} (tụt quá {max_pass_drop})")
        if d.new_silent - d.base_silent > max_silent_rise:
            out.append(f"{d.name}: lỗi im lặng {d.base_silent} → {d.new_silent} (tăng quá {max_silent_rise})")
    return out


def _ids(xs: list[str]) -> str:
    """Danh sách ca gọn cho bảng."""
    return ", ".join(xs) or "—"


def render(baseline: Json, diffs: list[SetDiff], fails: list[str]) -> list[str]:
    """Báo cáo markdown: nguồn baseline, bảng từng tập, ca đổi kết luận/im lặng, kết luận cổng."""
    src = baseline.get("source") or {}
    lines = [
        "# Cổng chống tụt bench (replay sau-ML)", "",
        f"Baseline: `{src.get('pipeline_sha', '?')}` ({src.get('note', '')})", "",
        "| Tập | ĐẠT trước | ĐẠT sau | Im lặng trước | Im lặng sau | Mất ĐẠT | Thêm ĐẠT | Im lặng mới | Hết im lặng "
        "| Thiếu |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    lines += [
        f"| {d.name} | {d.base_pass} | {d.new_pass} | {d.base_silent} | {d.new_silent} | {_ids(d.lost)} | "
        f"{_ids(d.gained)} | {_ids(d.new_silent_ids)} | {_ids(d.fixed_silent_ids)} | {_ids(d.missing)} |"
        for d in diffs
    ]
    verdict = "**ĐỎ** — " + "; ".join(fails) if fails else "**XANH**"
    return [*lines, "", f"Kết luận: {verdict}"]


def compare(
    baseline: Json, sets: dict[str, Path], limits: tuple[int, int], cache: Path | None
) -> tuple[int, list[str]]:
    """So các lượt `sets` với `baseline` → (mã thoát, báo cáo). Mã 2 khi thiếu tập hoặc ml-cache khác lúc chụp."""
    want = baseline.get("sets") or {}
    if baseline.get("format") != FORMAT or sorted(want) != sorted(sets):
        return 2, [f"baseline (format {baseline.get('format')}, tập {sorted(want)}) không khớp tập {sorted(sets)}"]
    pinned = (baseline.get("source") or {}).get("ml_cache_sha256")
    if cache is not None and pinned and cache_digest(cache) != pinned:
        return 2, ["ml-cache khác bản lúc chụp baseline: chụp lại baseline (`gate snapshot`) trong cùng PR"]
    diffs = [compare_set(name, want[name], load_scores(run)) for name, run in sets.items()]
    fails = failures(diffs, *limits)
    return (1 if fails else 0), render(baseline, diffs, fails)


def parse_sets(items: list[str]) -> dict[str, Path]:
    """`["bench=a", "F=b"]` → {tên: đường}; ném `ValueError` khi thiếu `=`."""
    out = {}
    for it in items:
        name, sep, path = it.partition("=")
        if not sep or not name or not path:
            raise ValueError(f"--set cần dạng tên=thư-mục, nhận {it!r}")
        out[name] = Path(path)
    return out


def _parser() -> argparse.ArgumentParser:
    """Tham số dòng lệnh của ba lệnh con."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.gate", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("compare", "snapshot"):
        p = sub.add_parser(name)
        p.add_argument("baseline", type=Path)
        p.add_argument("--set", dest="sets", action="append", required=True, help="tên=thư mục lượt đã chấm")
        p.add_argument("--ml-cache", type=Path)
    sub.choices["compare"].add_argument("--report", type=Path, help="thêm báo cáo vào tệp (vd $GITHUB_STEP_SUMMARY)")
    sub.choices["snapshot"].add_argument("--source-sha", required=True)
    sub.choices["snapshot"].add_argument("--note", default="")
    ci = sub.add_parser("ci")
    ci.add_argument("worktree", type=Path)
    ci.add_argument("out", type=Path)
    ci.add_argument("--baseline", type=Path, default=BASELINE)
    ci.add_argument("--jobs", type=int, default=4)
    ci.add_argument("--local", action="store_true", help="replay không docker (venv hiện tại), dùng trên CI")
    for p in (sub.choices["compare"], ci):
        p.add_argument("--max-pass-drop", type=int, default=2, help="số ca ĐẠT được phép tụt mỗi tập (Q8: 2)")
        p.add_argument("--max-silent-rise", type=int, default=0, help="số lỗi im lặng được phép tăng mỗi tập")
    return ap


def write_report(lines: list[str], report: Path | None) -> None:
    """In báo cáo ra stdout, thêm vào `report` nếu có."""
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if report is not None:
        with report.open("a", encoding="utf-8") as f:
            f.write(text)


def _snapshot_cmd(a: argparse.Namespace) -> int:
    """`snapshot`: ghi baseline từ các lượt đã chấm."""
    source: Json = {"pipeline_sha": a.source_sha, "note": a.note}
    if a.ml_cache is not None:
        source["ml_cache_sha256"] = cache_digest(a.ml_cache)
    data = snapshot(parse_sets(a.sets), source)
    a.baseline.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    a.baseline.write_text(text, encoding="utf-8", newline="\n")  # LF cả trên Windows: diff baseline gọn
    say(f"baseline → {a.baseline}: " + ", ".join(f"{k} {len(v)} ca" for k, v in data["sets"].items()))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Điểm vào; trả mã thoát (0 đạt, 1 tụt, 2 lỗi dùng/dữ liệu)."""
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "snapshot":
            return _snapshot_cmd(a)
        if a.cmd == "ci":
            from tools.bench import gate_ci  # nhập muộn: gate_ci nhập lại gate

            return gate_ci.run(a)
        baseline = json.loads(a.baseline.read_text(encoding="utf-8"))
        code, lines = compare(baseline, parse_sets(a.sets), (a.max_pass_drop, a.max_silent_rise), a.ml_cache)
    except (ValueError, OSError) as e:
        warn(f"gate: {e}")
        return 2
    write_report(lines, a.report)
    return code


if __name__ == "__main__":
    sys.exit(main())
