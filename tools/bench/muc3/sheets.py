"""Chấm tách hình trên tờ: ghép khung dự đoán với khung đáp án một-một theo IoU >= 0,5."""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

from tools.bench.muc3.io import load_json
from tools.bench.muc3.metrics import Json, norm_kind

IOU_MIN = 0.5
SHEET_FIELDS = ("truth_major", "recalled", "pred", "matched", "kind_ok", "n_matched")


def iou(a: list[float], b: list[float]) -> float:
    """Giao trên hợp của hai khung `[x0, y0, x1, y1]`; không giao thì 0."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    i = w * h
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def match_boxes(truth_views: list[Json], pred_views: list[Json]) -> list[tuple[int, int]]:
    """Ghép tham lam một-một theo IoU giảm dần, dừng dưới `IOU_MIN`; trả `[(chỉ số đáp án, chỉ số dự đoán)]`."""
    cand = sorted(
        ((iou(t["box"], p["box"]), i, j) for i, t in enumerate(truth_views) for j, p in enumerate(pred_views)),
        reverse=True,
    )
    used_t: set[int] = set()
    used_p: set[int] = set()
    out: list[tuple[int, int]] = []
    for v, i, j in cand:
        if v < IOU_MIN:
            break
        if i not in used_t and j not in used_p:
            used_t.add(i)
            used_p.add(j)
            out.append((i, j))
    return out


def score_sheet(truth_sheet: Json, pred_views: list[Json]) -> list[int]:
    """Sáu số theo `SHEET_FIELDS`: hình chính của đáp án, số được tìm lại, số khung dự đoán, số khung khớp,
    số khung khớp đúng loại, số khung khớp (lặp lại để cộng dồn)."""
    tv = truth_sheet["views"]
    m = match_boxes(tv, pred_views)
    rec = sum(not tv[i].get("minor") for i, _ in m)
    kind = sum(norm_kind(tv[i]["kind"]) == norm_kind(pred_views[j].get("kind")) for i, j in m)
    return [sum(not v.get("minor") for v in tv), rec, len(pred_views), len(m), kind, len(m)]


def sheet_results(sheets_dir: Path, pred_dir: Path, split: dict[str, Any]) -> dict[tuple[str, str], list[int]]:
    """Tổng `score_sheet` theo `(phần, nhóm)` trên mọi tờ có nhãn của bộ được chấm.

    Bộ không có tệp dự đoán hay không có `sheet_views` coi như không dự đoán khung nào (mọi hình chính bị bỏ sót).
    """
    part_of = {s: p for p in ("test", "dev", "test2") if p in split for s in split[p]}
    res: dict[tuple[str, str], list[int]] = {}
    for path in sorted(glob.glob(str(sheets_dir / "*__s[0-9][0-9].json"))):
        stem = Path(path).stem
        ts, setname = load_json(Path(path)), stem.split("__")[0]
        pred = load_json(pred_dir / (setname + ".json")) or {}
        if setname not in part_of or ts is None:
            continue
        r = score_sheet(ts, (pred.get("sheet_views") or {}).get(stem, []))
        for key in ((part_of[setname], "all"), (part_of[setname], "vn" if setname.startswith("DS-") else "other")):
            res[key] = [a + b for a, b in zip(res.get(key, [0] * 6), r, strict=True)]
    return res
