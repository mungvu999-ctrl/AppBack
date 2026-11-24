"""Gộp kết quả theo phần (`test`/`dev`/`test2`) và nhóm (`all`/`vn`/`other`), dựng báo cáo và bảng chữ.

Lỗi im lặng = dự đoán sai mà độ chắc >= 0,9; độ phủ = tỉ lệ dự đoán đạt mốc đó (xem `SCHEMA.md`, quy ước độ chắc).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.bench.muc3.io import load_json
from tools.bench.muc3.metrics import ITEM_METRICS, Item, Json, score_set
from tools.bench.muc3.sheets import SHEET_FIELDS, sheet_results

THRESHOLDS = (0.5, 0.7, 0.9, 0.95)
GROUPS = ("all", "vn", "other")
NAMES = {
    "kind": "1 view kind",
    "storey": "2 storey number",
    "set_exact": "3 set exact",
    "h_plan_section": "4 height plan+section",
    "h_plan": "4b height plan",
    "stack": "5 stack (xanh)",
    "stairs": "6 stair cores",
}


def pct(c: int, n: int) -> str:
    """`đúng/tổng tỉ lệ%`; tổng 0 thì `0/0 -`."""
    return f"{c}/{n} {100 * c / n:.1f}%" if n else "0/0 -"


def fmt_f(x: float | None) -> str:
    """Tỉ lệ (0..1) thành phần trăm nguyên; `None` thành `-`."""
    return "-" if x is None else f"{100 * x:.0f}%"


def risk_table(items: list[Item]) -> Json:
    """Thống kê rủi ro im lặng của danh sách `(độ chắc, đúng?)` theo từng mốc `THRESHOLDS`.

    Chỉ tính các mục có dự đoán (độ chắc không `None`); `coverage` = tỉ lệ dự đoán đạt mốc,
    `error` = tỉ lệ sai trong đó.
    """
    preds = [(c, ok) for c, ok in items if c is not None]
    rows: dict[float, Json] = {}
    for t in THRESHOLDS:
        hi = [ok for c, ok in preds if c >= t]
        rows[t] = {
            "coverage": len(hi) / len(preds) if preds else None,
            "n": len(hi),
            "error": sum(not ok for ok in hi) / len(hi) if hi else None,
        }
    return {
        "n_pred": len(preds),
        "n_truth": len(items),
        "silent_error_rate": rows[0.9]["error"],
        "coverage_ge_0.9": rows[0.9]["coverage"],
        "risk_coverage": rows,
    }


def aggregate(results: list[Json], part: str, grp: str) -> Json:
    """Chỉ số tổng của các bộ thuộc một phần và một nhóm (`all`, `vn` = bộ DS-, `other`)."""
    rs = [r for r in results if r["part"] == part and (grp == "all" or (grp == "vn") == r["vn"])]
    out: Json = {"n_sets": len(rs)}
    for m in ITEM_METRICS:
        items = [it for r in rs for it in r["items"][m]]
        out[m] = {
            "correct": sum(ok for _, ok in items),
            "total": len(items),
            "predicted": sum(c is not None for c, _ in items),
            "risk": risk_table(items),
            "extra": sum(r["extra"][m] for r in rs),
        }
    out["set_exact"] = {"correct": sum(r["set_exact"] for r in rs), "total": len(rs)}
    st = [r["stairs"] for r in rs if r["stairs"] is not None]
    out["stairs"] = {"correct": sum(st), "total": len(st)}
    return out


def _cells(report: Json, parts: list[str], fn: Any) -> str:
    """Nối một ô mỗi cột `(phần, nhóm)`, căn phải 20 ký tự; `fn(metrics)` ra chữ của ô."""
    return "".join(fn(report["metrics"][f"{p}/{g}"]).rjust(20) for p in parts for g in GROUPS)


def _table_lines(report: Json, parts: list[str]) -> list[str]:
    """Bảng chính (số bộ, từng chỉ tiêu kèm số thừa), độ phủ và lỗi im lặng."""
    cols = [(p, g) for p in parts for g in GROUPS]
    lines = [
        f"truth dir: {report['truth_dir']} (sheet labels: {report['sheets_dir']})",
        "metric".ljust(24) + "".join(f"{p}/{g}".rjust(20) for p, g in cols),
        "sets".ljust(24) + _cells(report, parts, lambda m: str(m["n_sets"])),
    ]
    for k, name in NAMES.items():
        lines.append(name.ljust(24) + _cells(report, parts, lambda m, k=k: pct(m[k]["correct"], m[k]["total"])))
        if k in ITEM_METRICS:
            lines.append("  extra:".ljust(24) + _cells(report, parts, lambda m, k=k: f"extra: {m[k]['extra']}"))
    lines += ["", "coverage (any pred / truth items):"]
    for k in ITEM_METRICS:
        lines.append(("  " + k).ljust(24) + _cells(report, parts, lambda m, k=k: pct(m[k]["predicted"], m[k]["total"])))
    lines += ["", "silent error = wrong share among preds with conf >= 0.9 ; (cov = share of preds >= 0.9)"]
    for k in ITEM_METRICS:
        lines.append(
            ("  " + k).ljust(24)
            + _cells(
                report,
                parts,
                lambda m, k=k: (
                    f"{fmt_f(m[k]['risk']['silent_error_rate'])} (cov {fmt_f(m[k]['risk']['coverage_ge_0.9'])})"
                ),
            )
        )
    return lines


def render(report: Json, parts: list[str]) -> str:
    """Bảng chữ thuần từ báo cáo: bảng chính, độ phủ, lỗi im lặng, rủi ro-độ phủ theo mốc, tách hình trên tờ."""
    lines = _table_lines(report, parts)
    lines += ["", "risk-coverage, part/all: threshold -> coverage, error"]
    for k in ITEM_METRICS:
        for p in parts:
            rc = report["metrics"][f"{p}/all"][k]["risk"]["risk_coverage"]
            cells = "  ".join(f">={t}: cov {fmt_f(rc[t]['coverage'])} err {fmt_f(rc[t]['error'])}" for t in THRESHOLDS)
            lines.append(f"  {k} {p}: {cells}")
    if report.get("sheets"):
        lines += ["", "sheet splitting (recall / precision / kind acc on matched):"]
        for key, v in report["sheets"].items():
            lines.append(
                f"  {key}: recall {pct(v['recalled'], v['truth_major'])}  precision {pct(v['matched'], v['pred'])}"
                f"  kind {pct(v['kind_ok'], v['matched'])}"
            )
    return "\n".join(lines)


def run(pred_dir: Path, parts: list[str], split: Json, truth_dir: Path, sheets_dir: Path) -> Json:
    """Dựng báo cáo; đáp án (tệp từng bộ, nhãn tờ) chỉ đọc từ hai thư mục được đưa vào.

    Bộ thuộc `split` mà không có tệp đáp án thì bỏ qua; thiếu tệp dự đoán thì coi là không dự đoán gì.
    """
    results: list[Json] = []
    for p in parts:
        for s in split[p]:
            truth = load_json(truth_dir / (s + ".json"))
            if truth is not None:
                results.append(score_set(truth, load_json(pred_dir / (s + ".json")) or {}))
    sh = sheet_results(sheets_dir, pred_dir, split) if sheets_dir.is_dir() else {}
    return {
        "pred": str(pred_dir),
        "truth_dir": str(truth_dir),
        "sheets_dir": str(sheets_dir),
        "metrics": {f"{p}/{g}": aggregate(results, p, g) for p in parts for g in GROUPS},
        "sheets": {f"{p}/{g}": dict(zip(SHEET_FIELDS, v, strict=True)) for (p, g), v in sh.items() if p in parts},
    }
