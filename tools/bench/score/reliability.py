"""Nhóm R (độ tin cậy: thời gian, tính toàn vẹn của layer) và nhóm D (ca hành vi: chặn, tách, giống bản gốc)."""

from __future__ import annotations

import math
from typing import Any

from tools.bench.score.case import Case
from tools.bench.score.geometry import band
from tools.bench.score.io import Json

# Mã "không phải mặt bằng"; luật chặn mới dùng mã khác thì thêm vào đây
BLOCK_CODES = {"NOT_A_FLOOR_PLAN"}
_BIG_LIMIT_S, _SMALL_LIMIT_S = 360, 180


def reliability_marks(case: Case, c: dict[str, str]) -> Any:
    """R1 (thời gian xử lý), R2 (layer toàn vẹn: id tham chiếu tồn tại, số hữu hạn), R3 (xem tay). Trả `process_s`."""
    big = case.row["file"].startswith("huge") or case.row["file"].endswith(".pdf")
    lim = _BIG_LIMIT_S if big else _SMALL_LIMIT_S
    t = case.meta.get("process_s")
    c["R1"] = "T" if case.status != "completed" else ("—" if t is None else band(t, lim, 2 * lim))
    c["R2"] = "T" if _broken(case) else "Đ"
    c["R3"] = "?" if case.cid == "two-page-ch04" else "—"
    return t


def _broken(case: Case) -> bool:
    """Layer có tham chiếu treo (ô mở/phòng trỏ tường không có, tường trỏ ô mở không có) hoặc số không hữu hạn."""
    wall_ids = {x["id"] for x in case.walls}
    op_ids = {y["id"] for y in case.ops}
    bad = [x for x in case.ops if x["wallId"] not in wall_ids]
    bad += [x for x in case.walls for i in x.get("openingIds", []) if i not in op_ids]
    bad += [r for r in case.rooms if any(i not in wall_ids for i in r.get("wallIds", []))]
    nums = [v for w in case.wsegs for v in w] + [v for r in case.rooms for p in r["outline"] for v in (p["x"], p["y"])]
    return bool(bad) or any(not math.isfinite(v) for v in nums)


def _wall_length(walls: list[Json]) -> float:
    """Tổng chiều dài tim tường (mm AI)."""
    return sum(
        math.dist(
            (w["centreline"]["start"]["x"], w["centreline"]["start"]["y"]),
            (w["centreline"]["end"]["x"], w["centreline"]["end"]["y"]),
        )
        for w in walls
    )


def _same_as_reference(case: Case, n: dict[str, float]) -> str:
    """Ca "giống bản gốc" (giong-01): mô hình xoay phải gần như y hệt `nt-01-single` (±10 %), hoặc đã báo.

    So tổng chiều dài tường thay vì số đoạn: bản xoay có thể thiếu vài mẩu < 0,8 m mà mô hình vẫn y hệt.
    Đổi `n["walls"]` thành tổng chiều dài (mm AI) để báo cáo ghi đúng số đã so.
    """
    ref = case.bench.load("nt-01-single", "layer")
    ref_layer = ((ref or {}).get("body") or {}).get("layer") or {}
    rn: dict[str, float] = {
        "walls": len(ref_layer.get("walls") or []),
        "rooms": len(ref_layer.get("rooms") or []),
        "openings": len(ref_layer.get("openings") or []),
    }
    n["walls"], rn["walls"] = _wall_length(case.walls), _wall_length(ref_layer.get("walls") or [])
    close = all(abs(n[k] - rn[k]) <= max(1, 0.1 * rn[k]) for k in n)
    return "Đ" if close or case.signal else "T"


def behaviour_marks(case: Case, c: dict[str, str]) -> tuple[str, bool | None, dict[str, float]]:
    """Nhóm D (đáp án `behaviour`): đặt c["D"]; trả (hành vi, "báo đúng lý do" hoặc None, số tường/phòng/ô mở)."""
    beh = case.key["behaviour"]
    n: dict[str, float] = {"walls": len(case.walls), "rooms": len(case.rooms), "openings": len(case.ops)}
    codes = set(case.ready.get("checks") or []) | {f["code"] for f in case.findings} | {case.prog.get("error")}
    right: bool | None = None
    if beh.startswith("tach"):  # M2: chỉ MULTIPLE_PLANS
        right = "MULTIPLE_PLANS" in codes
        c["D"] = "Đ" if right else "T"
    elif beh.startswith("chan"):  # M1: không trả mô hình dựng
        blocked = (
            case.status == "failed"
            or not case.walls
            or case.ready.get("state") == "blocked"
            or bool(BLOCK_CODES & codes)
        )
        c["D"] = "Đ" if blocked else "T"
        right = blocked or "PERSPECTIVE_DETECTED" in codes
    elif beh == "giong-01":
        c["D"] = _same_as_reference(case, n)
    else:
        c["D"] = "?"
    return beh, right, n
