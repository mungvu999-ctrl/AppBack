"""Chấm một ca: ghép các nhóm tiêu chí theo thứ tự cố định rồi kết luận (`score_case`)."""

from __future__ import annotations

from typing import Any

from tools.bench.score.case import Case, build_case
from tools.bench.score.io import Bench, Json
from tools.bench.score.openings import OpeningFacts, opening_marks
from tools.bench.score.probe import FurnitureFacts, furniture_marks, probe_mark, usability_marks
from tools.bench.score.reliability import behaviour_marks, reliability_marks
from tools.bench.score.rooms import RoomFacts, room_marks
from tools.bench.score.scale import scale_marks
from tools.bench.score.verdict import behaviour_verdict, is_silent, verdict_of
from tools.bench.score.walls import WallFacts, truth_marks, wall_marks


def _behaviour_case(case: Case, c: dict[str, str]) -> Json:
    """Kết quả ca hành vi (nhóm D): chỉ R1-R3 và D, kèm số tường/phòng/ô mở và lý do báo."""
    beh, right, n = behaviour_marks(case, c)
    verdict, silent = behaviour_verdict(case, c, right)
    return {
        "id": case.cid, "group": case.group, "c": c, "verdict": verdict, "silent": silent,
        "info": {
            **n, "findings": [f["code"] for f in case.findings], "readiness": case.ready, "behaviour": beh,
            "right_reason": right if verdict == "TRƯỢT" else None,
        },
    }  # fmt: skip


def _info(
    case: Case,
    t: Any,
    worst: Any,
    walls: WallFacts,
    opens: OpeningFacts,
    rooms: RoomFacts,
    truth: tuple[Any, ...],
    furn: FurnitureFacts,
    usable: tuple[Any, int],
    findings_ready: tuple[list[str], Json],
) -> Json:
    """Phần `info` của kết quả: số đo thô cho từng tiêu chí, để soi lại vì sao ra mức đó."""
    key, kc = case.key, case.key.get("counts") or {}
    rec, pre, o2, w6_bad = truth
    return {
        "status": case.status, "process_s": t, "scale_ai": round(case.sc, 3), "scale_key": key.get("mm_per_px"),
        "scale_note": None if key.get("mm_per_px") or case.scale_known else "mm thật ước 20 mm/px", "offset": case.off,
        "walls": len(case.walls), "rooms": len(case.rooms), "doors": opens.doors, "windows": opens.windows,
        "key_doors": kc.get("doors"), "key_windows": kc.get("windows"), "key_rooms": rooms.want,
        "S3_err_pct": worst, "W1_enclosed": walls.enclosed, "W1_leaked_mm": walls.leaked, "W2_tier_ok": walls.tier_ok,
        "W4": [walls.frag, walls.over], "W5": [rec, pre], "W6_bad": w6_bad, "O1c": opens.o1c, "O2": o2,
        "O3_viol": opens.violations, "O5_missing": opens.missing, "P3": rooms.p3, "P4": rooms.p4,
        "P5_median_pct": rooms.p5, "P6_sum_m2": round(rooms.sum_m2, 1), "P6_key_m2": rooms.key_m2,
        "P7": {"self_cross": rooms.self_cross, "under_1m2": rooms.under_1m2, "overlap": rooms.overlap},
        "F": {"items": furn.items, "in_room": furn.in_room, "near_door": furn.near_door, "too_close": furn.too_close},
        "U1_s": usable[0], "U3_fixes": usable[1], "findings": findings_ready[0], "readiness": findings_ready[1],
    }  # fmt: skip


def score_case(bench: Bench, cid: str, cal: bool = False) -> Json:
    """Chấm ca `cid` → {id, group, c (từng tiêu chí), verdict, silent, info}. `cal`: đọc kết quả sau bước đặt tỉ lệ."""
    case = build_case(bench, cid, cal)
    c: dict[str, str] = {}
    t = reliability_marks(case, c)
    if case.key.get("behaviour"):
        return _behaviour_case(case, c)
    worst = scale_marks(case, c)
    walls = wall_marks(case, c)
    truth = truth_marks(case, c)
    opens = opening_marks(case, c, walls.outside)
    rooms = room_marks(case, c)
    probe = bench.load(cid, "probe", cal)
    probe_mark(case, c, probe, opens)
    furn = furniture_marks(case, c, probe)
    usable = usability_marks(case, c, probe, t, walls, opens, rooms)
    for name in case.key.get("exclude") or []:  # M4: tiêu chí không áp cho ca này (lý do ở `note_dot4` của đáp án)
        if name in c:
            c[name] = "—"
    info = _info(
        case, t, worst, walls, opens, rooms, truth, furn, usable, ([f["code"] for f in case.findings], case.ready)
    )
    return {
        "id": cid, "group": case.group, "c": c, "verdict": verdict_of(list(c.values())),
        "silent": is_silent(case, c), "info": info,
    }  # fmt: skip
