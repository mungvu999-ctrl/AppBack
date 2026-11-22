"""Chấm cả một lượt và ghi `score.md`, `score.json`, `tham-my.csv` (phiếu chấm thẩm mỹ tay, chỉ tạo khi chưa có)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.bench.score.engine import score_case
from tools.bench.score.io import Bench, Json
from tools.bench.score.verdict import finalize

COLS = [
    "R1", "R2", "R3", "S1", "S2", "S3", "S4", "S5", "W1", "W2", "W3", "W4", "W5", "W6", "O1", "O1b", "O1c", "O2",
    "O3", "O4", "O5", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "M0", "F1", "F2", "F3", "F4", "U1", "U3", "D",
]  # fmt: skip
NEED = {
    "A": "≥ 80 %", "B": "3/3", "C": "≥ 6/8, vector-apt bắt buộc", "D": "7/7", "E": "tham khảo (tập mới)",
    "F": "tham khảo (tập F)",
}  # fmt: skip
SHEET_HEADER = [
    "id", "A1 ánh sáng", "A2 vật liệu-màu", "A3 hình khối-tỉ lệ", "A4 sạch lỗi hiển thị", "A5 bày đồ hợp lý",
    "A6 cảm giác chung", "U2 khung nhìn đầu", "U4 cảnh báo dễ hiểu", "M1-M5 ghi chú", "người chấm",
]  # fmt: skip


def case_ids(bench: Bench) -> list[str]:
    """Ca của manifest có mặt trong lượt (thư mục `<id>/` hoặc tệp `<id>.layer.json` của lượt cũ), theo thứ tự id."""
    run = bench.run
    return sorted(i for i in bench.manifest if (run / i).is_dir() or (run / f"{i}.layer.json").exists())


def score_run(bench: Bench) -> list[Json]:
    """Chấm mọi ca; ca có `calibrated/layer.json` chấm cả tự động lẫn sau bước đặt tỉ lệ (M5)."""
    res = []
    for cid in case_ids(bench):
        auto = score_case(bench, cid)
        final = score_case(bench, cid, cal=True) if (bench.run / cid / "calibrated" / "layer.json").exists() else auto
        res.append(finalize(auto, final))
    return res


def _row(r: Json) -> str:
    """Một dòng bảng markdown của ca `r`."""
    cells = " | ".join(r["c"].get(k, "") for k in COLS)
    return (
        f"| {r['id']} | {r['group']} | {cells} | {'1B' if r['step1'] else ''} | {r['verdict_auto']} | "
        f"{r['verdict']} | {'CÓ' if r['silent'] else ''} |"
    )


def _group_rows(res: list[Json]) -> list[str]:
    """Bảng tổng hợp theo nhóm A-F (chỉ nhóm có ca)."""
    lines = []
    for g in "ABCDEF":
        gr = [r for r in res if r["group"] == g]
        if gr:
            n1 = sum(r["outcome"] == "tự động" for r in gr)
            n2 = sum(r["outcome"] == "1 bước" for r in gr)
            lines.append(
                f"| {g} | {len(gr)} | {n1} | {n2} | {n1 + n2} | {sum(r['outcome'] == 'trượt' for r in gr)} | "
                f"{sum(r['silent'] for r in gr)} | {NEED[g]} |"
            )
    return lines


def render_markdown(name: str, res: list[Json]) -> list[str]:
    """Nội dung `score.md` (từng dòng): bảng ca, bảng nhóm, số lỗi im lặng, ca hành vi báo sai lý do."""
    lines = [
        f"# Chấm {name}", "",
        "1B = chấm sau 1 bước người dùng (đặt tỉ lệ đáp án, calibrated/); Tự động = kết luận trước bước đó.", "",
        "| Ca | Nhóm | " + " | ".join(COLS) + " | 1B | Tự động | Kết luận | Lỗi im lặng |",
        "|" + "---|" * (len(COLS) + 6),
    ]  # fmt: skip
    lines += [_row(r) for r in res]
    lines += [
        "",
        "| Nhóm | Ca | Đạt tự động | Đạt sau 1 bước | ĐẠT (tổng) | Trượt | Lỗi im lặng | Ngưỡng |",
        "|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    lines += _group_rows(res)
    wrong = [r["id"] for r in res if (r["info"].get("right_reason") is False) and not r["silent"]]
    lines += [
        "",
        f"**Lỗi im lặng: {sum(r['silent'] for r in res)}** (ngưỡng 0; TRƯỢT mà readiness `ready`, K-11 — luật cũ: "
        f"{sum(r['silent_old'] for r in res)}). "
        "M1–M5 soát tay trên <id>/3d.png, 3d-top.png; R3 xem tay.",  # noqa: RUF001
        "Ca hành vi trượt có báo nhưng sai lý do (không phải MULTIPLE_PLANS / không phải mặt bằng): "
        f"{', '.join(wrong) or 'không'}.",
    ]
    return lines


def write_outputs(run: Path, res: list[Json], lines: list[str]) -> None:
    """Ghi `tham-my.csv` (nếu chưa có), `score.md`, `score.json` vào thư mục lượt."""
    sheet = run / "tham-my.csv"
    if not sheet.exists():
        with sheet.open("w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(SHEET_HEADER)
            for r in res:
                if r["verdict"] != "?" and r["group"] != "D":
                    wr.writerow([r["id"]] + [""] * 10)
    (run / "score.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run / "score.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
