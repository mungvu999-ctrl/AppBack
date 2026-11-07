"""Ô mở → cutter (hộp khoét tường) + assembly (bộ khung/cánh/kính mặc định) cho plan.json (HOP-DONG-M1 §1, §1.1, §4.4).

Toạ độ ra: mét, X = x_mm/1000, Y = -y_mm/1000, Z tính từ cốt sàn của tầng (tầng mang `elevation` riêng).
Mọi hộp của assembly (`boxes[].c`, `angle`) và mọi pivot (`at`, `angle`) đều ở toạ độ **tầng** (không phải toạ độ
cục bộ của cha); `parent` chỉ nói nút nào giữ hộp, Blender gắn cha giữ nguyên biến đổi thế giới.

Hệ trục của một ô mở: `d` = hướng tim tường từ `start` tới `end`, `n = (-d.y, d.x)` pháp tuyến trái; `u` đo dọc `d`
từ tâm ô mở, `v` đo dọc `n`. `offsetMm` là mép trái ô mở tính từ `start` (như `span_points` của `spatial.arc`).
"""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Point, Polygon

from packages.domain.model_export import defaults
from packages.domain.model_export.records import RecordBook, Severity
from packages.domain.model_export.units import to_m

CUTTER_EXTRA_M = 0.2
"""Hộp khoét dày hơn tường 0,2 m (§10 / brief A3) để boolean không để lại màng mỏng."""
EDGE_TOL_MM = 1
"""Ô mở vượt đầu tường quá ngần này (mm) thì ghi `degraded major` (§7 H4 dùng cùng ngưỡng 1 mm)."""
INSIDE_PROBE_M = 0.15
"""Điểm dò phía phòng: cách mặt tường ngần này theo pháp tuyến."""
HANDLE_HEIGHT_M = 1.0
"""Tay nắm cao 1 m trên ngưỡng (giá trị prototype-v2)."""
CUT_ONLY_KINDS = ("gap", "void", "opening")
"""Kiểu ô mở chỉ khoét lỗ, không có bộ khung (§1.1)."""


def openings_plan(
    openings: list[dict[str, Any]],
    wall_plan: Any,
    rooms: list[dict[str, Any]],
    level_id: str,
    materials: Any,
    book: RecordBook,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Trả `(cutters, assemblies)` của một tầng; ghi sổ mỗi ô mở vào `book`.

    `wall_plan.pieces`: các mảnh tường của A2 (dict có `id`, `centreline` ((x,y),(x,y)) mét, `thicknessM`, `heightM`,
    `curved`). `rooms`: phòng của document (outline mm) để biết phía trong cho bậu cửa sổ.
    `materials.resolve(None, role, level_id, id, book)` trả khoá vật liệu theo vai trò (§3, mặc định trung tính).
    """
    walls = {p["id"]: p for p in wall_plan.pieces}
    room_polys = [Polygon([to_m(p) for p in r["outline"]]) for r in rooms if len(r.get("outline") or ()) >= 3]
    cutters: list[dict[str, Any]] = []
    assemblies: list[dict[str, Any]] = []
    for o in openings:
        kind = o.get("kind") or "opening"
        wall = walls.get(o.get("wallId"))
        reason = _skip_reason(o, wall)
        if reason or wall is None:
            book.add(level_id, kind, o["id"], "skipped", "major", reason)
            continue
        frame = _frame(o, wall)
        cutters.append(_cutter(o, wall, frame))
        problems = _overrun(o, wall, frame)
        if kind in CUT_ONLY_KINDS:
            _record(book, level_id, kind, o["id"], problems, "chỉ khoét lỗ")
            continue
        frame["inside"] = _inside_sign(frame, wall["thicknessM"], room_polys)
        mat = _Mats(materials, level_id, o["id"], book)
        if kind == "window":
            assembly = _window(o, wall, frame, mat, level_id)
        else:
            assembly = _door(o, wall, frame, mat, level_id)
            if o.get("doorStyle") == "folding":
                problems.append(("minor", "cửa xếp dựng như 2 tấm"))
        assemblies.append(assembly)
        _record(book, level_id, kind, o["id"], problems, "")
    return cutters, assemblies


def _skip_reason(o: dict[str, Any], wall: dict[str, Any] | None) -> str:
    """Lý do bỏ ô mở (§1): không tường chủ, tường cong, rộng/cao ≤ 50 mm; rỗng = dựng được."""
    if wall is None:
        return "không có tường chủ"
    if wall.get("curved"):
        return "ô mở trên tường cong chưa hỗ trợ"
    if o["widthMm"] <= defaults.MIN_OPENING_MM or o["heightMm"] <= defaults.MIN_OPENING_MM:
        return f"ô mở rộng/cao ≤ {defaults.MIN_OPENING_MM} mm"
    return ""


def _frame(o: dict[str, Any], wall: dict[str, Any]) -> dict[str, Any]:
    """Khung toạ độ của ô mở: tâm đáy `c`, hướng `d`, pháp tuyến `n`, góc, kích thước (mét), chiều dài tường."""
    (sx, sy), (ex, ey) = wall["centreline"]
    length = math.hypot(ex - sx, ey - sy)
    dx, dy = (ex - sx) / length, (ey - sy) / length
    w, off = o["widthMm"] / 1000, o["offsetMm"] / 1000
    return {
        "c": (sx + dx * (off + w / 2), sy + dy * (off + w / 2)),
        "d": (dx, dy),
        "n": (-dy, dx),
        "angle": math.atan2(dy, dx),
        "w": w,
        "h": o["heightMm"] / 1000,
        "sill": o["sillHeightMm"] / 1000,
        "length": length,
    }


def _cutter(o: dict[str, Any], wall: dict[str, Any], f: dict[str, Any]) -> dict[str, Any]:
    """Hộp khoét đúng toạ độ document, không kẹp (§1, §4.4)."""
    return {
        "target": f"Wall_{wall['id']}",
        "box": {
            "c": _r3(*f["c"], f["sill"] + f["h"] / 2),
            "size": _r3(f["w"], wall["thicknessM"] + CUTTER_EXTRA_M, f["h"]),
            "angle": round(f["angle"], 6),
        },
        "appId": o["id"],
    }


def _overrun(o: dict[str, Any], wall: dict[str, Any], f: dict[str, Any]) -> list[tuple[Severity, str]]:
    """Ô mở vượt đầu tường / vượt cao tường → `degraded major`, giữ nguyên toạ độ (§1, §7 H4)."""
    problems: list[tuple[Severity, str]] = []
    length_mm = f["length"] * 1000
    over = max(-o["offsetMm"], o["offsetMm"] + o["widthMm"] - length_mm, 0)
    if over > EDGE_TOL_MM:
        problems.append(("major", f"ô mở vượt đầu tường {round(over)} mm"))
    over_top = o["sillHeightMm"] + o["heightMm"] - wall["heightM"] * 1000
    if over_top > EDGE_TOL_MM:
        problems.append(("major", f"ô mở vượt cao tường {round(over_top)} mm"))
    return problems


def _record(
    book: RecordBook, level_id: str, kind: str, oid: str, problems: list[tuple[Severity, str]], ok_reason: str
) -> None:
    """Một bản ghi mỗi ô mở: nặng nhất trong `problems` quyết định mức; không lỗi → `built info`."""
    if not problems:
        book.add(level_id, kind, oid, "built", "info", ok_reason)
        return
    severity: Severity = "major" if any(s == "major" for s, _ in problems) else "minor"
    book.add(level_id, kind, oid, "degraded", severity, "; ".join(r for _, r in problems))


def _inside_sign(f: dict[str, Any], thickness: float, room_polys: list[Polygon]) -> int:
    """+1 nếu phía `+n` của tường có phòng, -1 nếu chỉ phía `-n` có, +1 khi không biết (cả hai/không phía nào)."""
    probe = thickness / 2 + INSIDE_PROBE_M

    def has_room(sign: int) -> bool:
        pt = Point(f["c"][0] + f["n"][0] * probe * sign, f["c"][1] + f["n"][1] * probe * sign)
        return any(p.contains(pt) for p in room_polys)

    return -1 if not has_room(1) and has_room(-1) else 1


class _Mats:
    """Hỏi khoá vật liệu theo vai trò một lần mỗi vai trò cho một ô mở."""

    def __init__(self, materials: Any, level_id: str, oid: str, book: RecordBook) -> None:
        """Giữ ngữ cảnh gọi `materials.resolve`."""
        self._args = (materials, level_id, oid, book)
        self._cache: dict[str, str] = {}

    def __call__(self, role: str) -> str:
        """Khoá vật liệu của vai trò (ref rỗng → mặc định trung tính)."""
        if role not in self._cache:
            materials, level_id, oid, book = self._args
            self._cache[role] = str(materials.resolve(None, role, level_id, oid, book))
        return self._cache[role]


def _root(
    o: dict[str, Any], wall: dict[str, Any], f: dict[str, Any], level_id: str, group: str, prefix: str
) -> dict[str, Any]:
    """Nút gốc assembly + props bắt buộc (brief A3)."""
    return {
        "name": f"{prefix}_{o['id']}",
        "level": level_id,
        "group": group,
        "appId": o["id"],
        "props": {
            "appId": o["id"],
            "kind": o.get("kind"),
            "swing": o.get("swing") or "",
            "doorStyle": o.get("doorStyle") or "",
            "reversed": bool(o.get("reversed")),
            "widthM": round(f["w"], 4),
            "heightM": round(f["h"], 4),
            "sillM": round(f["sill"], 4),
            "hostWall": wall["id"],
            "prov_position": "document",
            "prov_assembly": "default",
        },
        "pivots": [],
        "boxes": [],
    }


def _box(
    asm: dict[str, Any],
    parent: str,
    name: str,
    f: dict[str, Any],
    u: float,
    v: float,
    z: float,
    size: tuple[float, ...],
    material: str,
) -> None:
    """Thêm hộp tâm tại (u, v) trong khung ô mở, cao `z` (tâm hộp), xoay theo tường."""
    cx = f["c"][0] + f["d"][0] * u + f["n"][0] * v
    cy = f["c"][1] + f["d"][1] * u + f["n"][1] * v
    asm["boxes"].append(
        {
            "name": name,
            "parent": parent,
            "c": _r3(cx, cy, z),
            "size": _r3(*size),
            "angle": round(f["angle"], 6),
            "material": material,
            "props": {"prov_assembly": "default"},
        }
    )


def _door(o: dict[str, Any], wall: dict[str, Any], f: dict[str, Any], mat: _Mats, level_id: str) -> dict[str, Any]:
    """Cửa đi (§1.1): khung lót + nẹp hai mặt + ngưỡng; bản lề có pivot ở bản lề; lùa/âm tường/xếp = 2 tấm."""
    asm = _root(o, wall, f, level_id, "Doors", "Door")
    root, t, w, h, z0 = asm["name"], wall["thicknessM"], f["w"], f["h"], f["sill"]
    lin, tw, tt = defaults.DOOR_LINING_M, defaults.DOOR_TRIM_W_M, defaults.DOOR_TRIM_T_M
    _box(asm, root, f"{root}_JambL", f, -w / 2 + lin / 2, 0, z0 + h / 2, (lin, t, h), mat("frame"))
    _box(asm, root, f"{root}_JambR", f, w / 2 - lin / 2, 0, z0 + h / 2, (lin, t, h), mat("frame"))
    _box(asm, root, f"{root}_Head", f, 0, 0, z0 + h - lin / 2, (w, t, lin), mat("frame"))
    for side in (1, -1):
        v = side * (t / 2 + tt / 2)
        tag = "A" if side > 0 else "B"
        _box(asm, root, f"{root}_TrimL{tag}", f, -w / 2 - tw / 2, v, z0 + (h + tw) / 2, (tw, tt, h + tw), mat("frame"))
        _box(asm, root, f"{root}_TrimR{tag}", f, w / 2 + tw / 2, v, z0 + (h + tw) / 2, (tw, tt, h + tw), mat("frame"))
        _box(asm, root, f"{root}_TrimH{tag}", f, 0, v, z0 + h + tw / 2, (w + 2 * tw, tt, tw), mat("frame"))
    _box(
        asm,
        root,
        f"{root}_Threshold",
        f,
        0,
        0,
        z0 - defaults.DOOR_THRESHOLD_M / 2,
        (w, t, defaults.DOOR_THRESHOLD_M),
        mat("stone"),
    )
    cw, ch = w - 2 * lin, h - lin
    if o.get("swing") == "sliding":
        _panels(asm, f, cw, ch, z0, mat, "door-panel")
    else:
        _leaves(asm, o, f, cw, ch, z0, mat)
    return asm


def _panels(asm: dict[str, Any], f: dict[str, Any], cw: float, ch: float, z0: float, mat: _Mats, kind: str) -> None:
    """2 tấm khung nhôm + kính lệch nhau theo pháp tuyến (cửa lùa, âm tường, xếp)."""
    root, half, bar = asm["name"], cw / defaults.SLIDING_DOOR_PANELS, defaults.WINDOW_FRAME_W_M
    for k in range(defaults.SLIDING_DOOR_PANELS):
        name = f"{root}_Panel{k}"
        u, v = -cw / 2 + half / 2 + k * half, (k - 0.5) * defaults.DOOR_LEAF_T_M
        asm["pivots"].append(_pivot(name, f, u, v, z0, {"operable": "slide", "kind": kind}))
        _box(asm, name, f"{name}_Frame", f, u, v, z0 + ch / 2, (half, defaults.DOOR_LEAF_T_M, ch), mat("metal"))
        glass = (half - 2 * bar, defaults.GLASS_T_M, ch - 2 * bar)
        _box(asm, name, f"{name}_Glass", f, u, v, z0 + ch / 2, glass, mat("glass"))


def _leaves(
    asm: dict[str, Any], o: dict[str, Any], f: dict[str, Any], cw: float, ch: float, z0: float, mat: _Mats
) -> None:
    """Cánh bản lề: `left` bản lề đầu -u, `right` đầu +u, `double` hai cánh bản lề hai đầu; pivot đặt ở bản lề."""
    swing = o.get("swing")
    hinges = (-1, 1) if swing == "double" else ((1,) if swing == "right" else (-1,))
    lw = cw / len(hinges)
    root, leaf_t = asm["name"], defaults.DOOR_LEAF_T_M
    for k, hs in enumerate(hinges):
        name = f"{root}_Leaf{k}"
        hinge_u = hs * cw / 2
        props = {"operable": "hinge", "kind": "door-leaf", "hinge": "right" if hs > 0 else "left"}
        asm["pivots"].append(_pivot(name, f, hinge_u, 0, z0, props))
        centre_u = hinge_u - hs * lw / 2
        _box(asm, name, f"{name}_Slab", f, centre_u, 0, z0 + ch / 2, (lw, leaf_t, ch), mat("door_leaf"))
        for side in (1, -1):
            hu = hinge_u - hs * (lw - 0.09)
            tag = "A" if side > 0 else "B"
            size = (0.13, 0.016, 0.018)
            _box(
                asm,
                name,
                f"{name}_Handle{tag}",
                f,
                hu,
                side * (leaf_t / 2 + 0.008),
                z0 + HANDLE_HEIGHT_M,
                size,
                mat("metal"),
            )


def _pivot(name: str, f: dict[str, Any], u: float, v: float, z: float, props: dict[str, Any]) -> dict[str, Any]:
    """Pivot (nút rỗng) tại (u, v, z) khung ô mở, xoay theo tường."""
    x = f["c"][0] + f["d"][0] * u + f["n"][0] * v
    y = f["c"][1] + f["d"][1] * u + f["n"][1] * v
    return {
        "name": name,
        "at": _r3(x, y, z),
        "angle": round(f["angle"], 6),
        "props": {**props, "prov_assembly": "default"},
    }


def _window(o: dict[str, Any], wall: dict[str, Any], f: dict[str, Any], mat: _Mats, level_id: str) -> dict[str, Any]:
    """Cửa sổ (§1.1): khung 50 x 70, `sliding`/`double` 2 cánh, còn lại 1 cánh; kính 8 mm; bậu đá phía phòng."""
    asm = _root(o, wall, f, level_id, "Windows", "Window")
    root, t, w, h, z0 = asm["name"], wall["thicknessM"], f["w"], f["h"], f["sill"]
    fw, fd = defaults.WINDOW_FRAME_W_M, defaults.WINDOW_FRAME_D_M
    zc = z0 + h / 2
    _box(asm, root, f"{root}_FrameL", f, -w / 2 + fw / 2, 0, zc, (fw, fd, h), mat("window_frame"))
    _box(asm, root, f"{root}_FrameR", f, w / 2 - fw / 2, 0, zc, (fw, fd, h), mat("window_frame"))
    _box(asm, root, f"{root}_FrameT", f, 0, 0, z0 + h - fw / 2, (w, fd, fw), mat("window_frame"))
    _box(asm, root, f"{root}_FrameB", f, 0, 0, z0 + fw / 2, (w, fd, fw), mat("window_frame"))
    count = 2 if o.get("swing") in ("sliding", "double") else 1
    iw, ih = w - 2 * fw, h - 2 * fw
    sw = iw / count
    operable = "slide" if o.get("swing") == "sliding" else ("fixed" if o.get("swing") == "fixed" else "hinge")
    for k in range(count):
        name = f"{root}_Sash{k}"
        u = -iw / 2 + sw / 2 + k * sw
        v = (k - 0.5) * 0.025 if count > 1 else 0.0
        asm["pivots"].append(_pivot(name, f, u, v, z0, {"operable": operable, "kind": "window-sash"}))
        _box(asm, name, f"{name}_Glass", f, u, v, zc, (sw - 0.02, defaults.GLASS_T_M, ih - 0.02), mat("window_glass"))
    sill_t = defaults.WINDOW_SILL_T_M
    v = f["inside"] * (t / 2 + 0.02)
    _box(asm, root, f"{root}_Sill", f, 0, v, z0 - sill_t / 2, (w + 0.06, 0.06, sill_t), mat("stone"))
    return asm


def _r3(*xs: float) -> list[float]:
    """Làm tròn 6 chữ số (µm) để plan.json tất định."""
    return [round(x, 6) for x in xs]
