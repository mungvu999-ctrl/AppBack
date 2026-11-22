"""Nhóm M0 (cảnh 3D dựng đủ như layer), M1-M5 (xem tay), F1-F4 (bày đồ trong 3D, từ `probe.json`) và U (thân thiện)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from tools.bench.score.case import Case
from tools.bench.score.geometry import Arr, band, inside
from tools.bench.score.io import Json
from tools.bench.score.openings import OpeningFacts
from tools.bench.score.rooms import RoomFacts
from tools.bench.score.walls import WallFacts

_MANUAL = ("M1", "M2", "M3", "M4", "M5")
_NEAR_DOOR_MM = 600
_TOO_CLOSE_MM = 300


@dataclass
class FurnitureFacts:
    """Số đo bày đồ cho báo cáo: số món, tỉ lệ nằm trong phòng, số món chắn cửa, số cặp trùng chỗ."""

    items: int
    in_room: float | None
    near_door: int | None
    too_close: int | None


def probe_mark(case: Case, c: dict[str, str], probe: Json | None, facts: OpeningFacts) -> None:
    """M0 (tự động, từ shot3d): cảnh 3D tải xong và dựng đủ tường/ô mở như layer; M1-M5 soát tay trên 3d*.png."""
    if probe is None:
        c["M0"] = "—"
    else:
        cn = probe.get("counts") or {}
        gap = (
            abs(cn.get("wall", 0) - len(case.walls))
            + abs(cn.get("door", 0) - facts.doors)
            + abs(cn.get("window", 0) - facts.windows)
        )
        if not probe.get("ready") or not cn.get("wall") or probe.get("errors"):
            c["M0"] = "T"
        else:
            c["M0"] = band(gap, 0, max(1, 0.1 * (len(case.walls) + len(case.ops))))
    for name in _MANUAL:
        c[name] = "?"


def _xz_mm(it: Json) -> Arr:
    """Vị trí món đồ trên mặt sàn (x, z) theo mm AI (cảnh tính bằng m)."""
    return np.array([it["position"][0], it["position"][2]]) * 1000


def _is_light_or_rug(it: Json) -> bool:
    """Đèn trần và thảm: không chắn lối, không tính chồng chỗ."""
    return it["id"].startswith("item_LIGHT") or "rug" in (it.get("src") or "")


def _floor_items(items: list[Json]) -> list[Arr]:
    """Đồ đặt trên sàn để xét chồng chỗ (đèn trên tủ, bình trên bàn, thảm, chậu bếp lọt trong dãy tủ chồng là đúng)."""
    return [
        _xz_mm(it)
        for it in items
        if it.get("position")
        and it["position"][1] < 0.05
        and not _is_light_or_rug(it)
        and not it["id"].startswith("item_DRESS-sink")
        and "kitchen-sink-gen" not in (it.get("src") or "")
    ]


def furniture_marks(case: Case, c: dict[str, str], probe: Json | None) -> FurnitureFacts:
    """F1 (phòng ngủ/khách có đồ), F2 (đồ trong phòng), F3 (đồ chắn cửa), F4 (hai món trùng chỗ); thiếu probe → "—"."""
    items_raw = (probe or {}).get("items", [])
    items = [_xz_mm(it) for it in items_raw if it.get("position")]
    if probe is None:
        for name in ("F1", "F2", "F3", "F4"):
            c[name] = "—"
        return FurnitureFacts(len(items), None, None, None)
    polys = [[(p["x"], p["y"]) for p in r["outline"]] for r in case.rooms]
    in_room = [[i for i, poly in enumerate(polys) if inside((float(q[0]), float(q[1])), poly)] for q in items]
    empty = sum(
        r["usage"] in ("bedroom", "livingRoom") and not any(i in h for h in in_room) for i, r in enumerate(case.rooms)
    )
    c["F1"] = band(empty, 0, 1)
    f_in = sum(bool(h) for h in in_room) / len(items) if items else None
    c["F2"] = "—" if f_in is None else "Đ" if f_in >= 0.95 else "C" if f_in >= 0.85 else "T"
    doors = case.centers("door", lambda p: (p["x"], p["y"]))
    on_floor = [_xz_mm(it) for it in items_raw if it.get("position") and not _is_light_or_rug(it)]
    f_door: Any = sum(any(np.linalg.norm(q - d) * case.k < _NEAR_DOOR_MM for d in doors) for q in on_floor)
    c["F3"] = band(f_door, 0, 1)
    fi = _floor_items(items_raw)
    f_close: Any = sum(
        np.linalg.norm(fi[i] - fi[j]) * case.k < _TOO_CLOSE_MM for i in range(len(fi)) for j in range(i + 1, len(fi))
    )
    c["F4"] = band(f_close, 0, 1)
    return FurnitureFacts(len(items), f_in, f_door, f_close)


def usability_marks(
    case: Case,
    c: dict[str, str],
    probe: Json | None,
    process_s: Any,
    walls: WallFacts,
    opens: OpeningFacts,
    rooms: RoomFacts,
) -> tuple[Any, int]:
    """U1 (giây từ lúc tải bản vẽ tới khi thấy 3D), U3 (ước số lần người dùng phải sửa tay). Trả (giây, số lần sửa)."""
    t_view = (
        (case.meta.get("process_s") or 0) + ((probe or {}).get("load_ms") or 0) / 1000
        if process_s is not None and probe
        else None
    )
    c["U1"] = band(t_view, 180, 360)
    kc = case.key.get("counts") or {}
    fixes = (
        (abs(opens.doors - kc["doors"]) if kc.get("doors") is not None else 0)
        + (abs(opens.windows - kc["windows"]) if kc.get("windows") is not None else 0)
        + (abs(len(case.rooms) - rooms.want) if rooms.want is not None else 0)
        + walls.frag
        + walls.over
        + (1 if case.cal or case.body.get("scaleStatus", "unresolved") == "unresolved" else 0)
        + (3 if c.get("W1") == "T" else 0)
    )
    c["U3"] = band(fixes, 5, 10)
    return t_view, fixes
