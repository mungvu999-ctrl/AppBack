"""Chấm bản nháp mái so với đáp án đọc bằng mắt `truth_review/roof_review.json` (NHA-10a, thiết kế §2.C Câu 8).

Chuyển từ `score_roof.py` ngoài repo, giữ nguyên luật chấm để số mới so được với số cũ:

- loại đúng: trùng loại thô (`flat` / `pitched` / `mixed`); dự đoán `unknown` hay vắng là "không trả lời"; đáp án
  `unknown` thì không chấm;
- cốt chân đúng: lệch không quá 0,2 m so với `base_level_m` **hoặc** `stair_tower_level_m`; cốt đỉnh: so `top_level_m`.

Thêm so với bản cũ: khoảng tin cậy Wilson 95 % của đúng/tổng, và hai mốc "trước" tính trên cùng tập bộ — không có mái,
luôn đoán mái bằng. Mốc thứ ba (bộ đoán nghiên cứu cũ) chấm bằng cách trỏ `--pred` vào thư mục `pred_roof` của nó.

CLI: `BENCH_MUC3_ROOT=... python -X utf8 -m tools.bench.muc3.roof --pred <thư mục> [--part dev|test]
[--split-file <tệp>] [--roof-truth <tệp>] [--json <tệp>]`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from tools.bench.muc3.config import muc3_root
from tools.bench.muc3.io import load_json
from tools.bench.muc3.metrics import Json

TOL_M: Final = 0.2
FIELDS: Final = ("type", "base", "top")
CONFIDENT: Final = (0.9, 0.7)
"""Mốc độ chắc để đếm "sai mà chắc" (quy ước ở `SCHEMA.md`)."""
_Z95: Final = 1.959964
_BASELINES: Final[Mapping[str, Json]] = {"none": {}, "always_flat": {"type": "flat", "confidence": 0.0}}

type Verdict = tuple[bool, bool, float]
"""Một trường của một bộ: (có trả lời, đúng, độ chắc)."""


def wilson(k: int, n: int) -> tuple[float, float] | None:
    """Khoảng tin cậy Wilson 95 % của tỉ lệ `k/n`; `n = 0` thì `None`."""
    if n == 0:
        return None
    z2 = _Z95**2
    centre = (k / n + z2 / (2 * n)) / (1 + z2 / n)
    half = _Z95 * math.sqrt(k * (n - k) / n**3 + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def draft_of(pred: Json | None) -> Json:
    """Bản nháp mái trong một tệp dự đoán: khoá `roof` (đường sản phẩm), hoặc cả tệp (thư mục `pred_roof` cũ)."""
    if pred is None:
        return {}
    return (pred["roof"] or {}) if "roof" in pred else pred


def _level(refs: Sequence[float], value: object, confidence: float) -> Verdict:
    """Phán một cốt (m) so với các đáp án chấp nhận được; không phải số là "không trả lời"."""
    if not isinstance(value, int | float):
        return False, False, confidence
    return True, any(abs(value - ref) <= TOL_M for ref in refs), confidence


def judge(ref: Json, draft: Json) -> dict[str, Verdict]:
    """Phán từng trường của một bộ; trường vắng khỏi kết quả là trường đáp án không có giá trị."""
    out: dict[str, Verdict] = {}
    if ref.get("type") not in (None, "unknown"):
        answered = draft.get("type") not in (None, "unknown")
        out["type"] = (answered, answered and draft["type"] == ref["type"], draft.get("confidence") or 0)
    sure = draft.get("level_confidence") or 0
    if ref.get("base_level_m") is not None:
        bases = [level for level in (ref["base_level_m"], ref.get("stair_tower_level_m")) if level is not None]
        out["base"] = _level(bases, draft.get("base_level_m"), sure)
    if ref.get("top_level_m") is not None:
        out["top"] = _level([ref["top_level_m"]], draft.get("top_level_m"), sure)
    return out


def tally(rows: Sequence[Mapping[str, Verdict]]) -> Json:
    """Gộp phán quyết của nhiều bộ: mỗi trường đúng / có trả lời / tổng, Wilson của đúng/tổng, và `[sai, số câu]`
    trong các câu trả lời có độ chắc đạt từng mốc `CONFIDENT`."""
    out: Json = {}
    for name in FIELDS:
        seen = [row[name] for row in rows if name in row]
        answers = [verdict for verdict in seen if verdict[0]]
        correct = sum(1 for verdict in answers if verdict[1])
        out[name] = {
            "correct": correct,
            "answered": len(answers),
            "total": len(seen),
            "wilson": wilson(correct, len(seen)),
            "wrong_at": {
                str(mark): [
                    sum(1 for verdict in answers if verdict[2] >= mark and not verdict[1]),
                    sum(1 for verdict in answers if verdict[2] >= mark),
                ]
                for mark in CONFIDENT
            },
        }
    return out


def run(pred_dir: Path, refs: Sequence[Json], split: Json, parts: Sequence[str]) -> Json:
    """Báo cáo `{"<phần>/<all|sure>": {sets, missing, fields, baselines}}` cho các bộ có đáp án mái thuộc từng phần.

    `sure` là các bộ người gắn nhãn đánh dấu chắc. Bộ thiếu tệp dự đoán, hay tệp hỏng, tính là "không trả lời" và được
    đếm ở `missing`: một lượt dự đoán ghi dở không được trông như mô hình ít trả lời đi.
    """
    report: Json = {}
    for part in parts:
        members = [ref for ref in refs if ref["set"] in (split.get(part) or [])]
        for label, chosen in (("all", members), ("sure", [ref for ref in members if ref.get("sure")])):
            preds = [load_json(pred_dir / f"{ref['set']}.json") for ref in chosen]
            drafts = [draft_of(pred) for pred in preds]
            report[f"{part}/{label}"] = {
                "sets": len(chosen),
                "missing": sum(1 for pred in preds if pred is None),
                "fields": tally([judge(ref, draft) for ref, draft in zip(chosen, drafts, strict=True)]),
                "baselines": {name: tally([judge(ref, fixed) for ref in chosen]) for name, fixed in _BASELINES.items()},
            }
    return report


def _percent(interval: Sequence[float] | None) -> str:
    """Khoảng Wilson thành `[a%-b%]`; không có mẫu thì `[-]`."""
    return "[-]" if interval is None else f"[{100 * interval[0]:.0f}%-{100 * interval[1]:.0f}%]"


def render(report: Json) -> str:
    """Bảng chữ: mỗi nhóm một khối; mỗi trường `đúng/trả lời/tổng [Wilson]` và số sai trong câu trả lời chắc."""
    lines: list[str] = []
    for key, block in report.items():
        part, label = key.split("/")
        size = f"{block['sets']} bộ, {block['missing']} không đọc được dự đoán"
        lines.append(f"{part} {label} ({size})  đúng/trả lời/tổng [Wilson 95 % của đúng/tổng]")
        for name in FIELDS:
            cell = block["fields"][name]
            wrong = "   ".join(f"sai@{mark} {bad}/{n}" for mark, (bad, n) in cell["wrong_at"].items())
            counts = f"{cell['correct']}/{cell['answered']}/{cell['total']}"
            lines.append(f"  {name:5s} {counts} {_percent(cell['wilson'])}   {wrong}")
        none, flat = (block["baselines"][name]["type"] for name in ("none", "always_flat"))
        lines.append(
            f"  mốc trước — không có mái: type {none['correct']}/{none['total']}"
            f" · luôn mái bằng: type {flat['correct']}/{flat['total']} {_percent(flat['wilson'])}"
        )
    return "\n".join(lines)


def load_refs(path: Path) -> list[Json] | None:
    """Đáp án mái: một danh sách object có `set`; không đọc được hoặc sai hình thì `None`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list) or not all(isinstance(ref, dict) and "set" in ref for ref in data):
        return None
    return data


def main(argv: Sequence[str] | None = None) -> int:
    """Chấm một thư mục dự đoán; trả 0, hoặc 2 khi không đọc được `split.json` hay tệp đáp án mái."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.muc3.roof", description=__doc__)
    ap.add_argument("--pred", type=Path, required=True, help="thư mục dự đoán, mỗi bộ một <bộ>.json")
    ap.add_argument("--part", choices=("dev", "test"), help="một phần; mặc định dev và test")
    ap.add_argument("--split-file", type=Path, help="mặc định <BENCH_MUC3_ROOT>/split.json")
    ap.add_argument("--roof-truth", type=Path, help="mặc định <BENCH_MUC3_ROOT>/truth_review/roof_review.json")
    ap.add_argument("--json", type=Path, help="ghi báo cáo đầy đủ ra tệp JSON")
    a = ap.parse_args(argv)
    root = muc3_root()
    split_path = a.split_file or root / "split.json"
    truth_path = a.roof_truth or root / "truth_review" / "roof_review.json"
    split, refs = load_json(split_path), load_refs(truth_path)
    if split is None or refs is None:
        sys.stderr.write(f"không đọc được {split_path if split is None else truth_path}\n")
        return 2
    report = run(a.pred, refs, split, [a.part] if a.part else ["dev", "test"])
    sys.stdout.write(render(report) + "\n")
    if a.json:
        a.json.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
