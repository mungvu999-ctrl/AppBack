"""Lớp đề xuất đã lưu → hình học plan.json (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.5, M2-QL).

Đọc `floor["suggestions"]` của snapshot (document đã lưu, không chạy luật). Dựng bản `active`/`overridden` không
`stale` với `{**params, **override}`; `adopted` đã nằm trong `layer`; `disabled`/`stale` không dựng mà vào
`appSuggestions.disabled` của scene. Sổ ghi kind `suggestion:<rule>`, nặng nhất `minor` (đề xuất hỏng không kéo bản
xuất về `degraded`, M1 §5): mọi bản ghi phụ (vật liệu thiếu…) bị hạ về `minor`, lỗi dựng thành `skipped minor`.
Mỗi đề xuất dựng nguyên khối hoặc không gì (không để lại nửa hình).

Toạ độ như phần còn lại của plan: mét, X = x_mm/1000, Y = -y_mm/1000, Z tính từ cốt sàn kết cấu của tầng. Tham số
mm trong hệ của `layer`; khung tường như `suggestions.rules.geom.Frame` (gốc `centreline.start`, `left` = phía pháp
tuyến (-dy, dx) tính theo mm). Tên nút ngắn `<Loại>_<sha256(id)[:12]>` (Blender cắt tên ở 63 byte); id đầy đủ nằm
trong extras `suggestionId`.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import shapely
from shapely.errors import ShapelyError
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from packages.domain.model_export import defaults
from packages.domain.model_export.assets import AssetResolver
from packages.domain.model_export.materials import MaterialResolver, Role
from packages.domain.model_export.plan.openings import _frame, _inside_sign
from packages.domain.model_export.records import RecordBook, Severity, Status
from packages.domain.model_export.units import GRID, cap_triangles, polygon_rings, polygons_of, to_m

BUILT_STATES: Final = ("active", "overridden")
GROUP: Final = "Suggestions"
FIN: Final = defaults.FINISH_FLOOR_M
BUILD_ERRORS: Final = (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, ShapelyError)
"""Tham số hỏng/hình học suy biến của một đề xuất → `skipped minor`, không làm hỏng cả lượt xuất."""


class SkipError(Exception):
    """Đề xuất không dựng được; `args[0]` là lý do tiếng Việt cho sổ."""


@dataclass
class Parts:
    """Hình của một đề xuất, chỉ ghép vào plan khi dựng xong trọn vẹn."""

    prisms: list[dict[str, Any]] = field(default_factory=list)
    assemblies: list[dict[str, Any]] = field(default_factory=list)
    instances: list[dict[str, Any]] = field(default_factory=list)
    attach: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """(tên assembly có sẵn, hộp) — khung cánh gắn vào bộ cửa sổ."""
    notes: list[str] = field(default_factory=list)
    """Lý do `degraded` (vẫn dựng)."""


@dataclass
class Summary:
    """Gom qua mọi tầng cho extras scene `appSuggestions`."""

    shas: set[str] = field(default_factory=set)
    counts: Counter[str] = field(default_factory=Counter)
    disabled: list[str] = field(default_factory=list)
    seen: bool = False

    def extras(self) -> dict[str, Any] | None:
        """`{rulesetSha, counts, disabled}`; `rulesetSha` là chuỗi khi mọi tầng cùng bảng luật, danh sách khi lệch,
        `None` khi chưa có meta. Không tầng nào có đề xuất → `None` (plan giữ nguyên như trước M2)."""
        if not self.seen:
            return None
        shas = sorted(self.shas)
        return {
            "rulesetSha": shas[0] if len(shas) == 1 else (shas or None),
            "counts": dict(sorted(self.counts.items())),
            "disabled": sorted(self.disabled),
        }


def effective(s: Mapping[str, Any]) -> dict[str, Any]:
    """`override` > `params` (contract §3)."""
    return {**(s.get("params") or {}), **(s.get("override") or {})}


def node_props(s: Mapping[str, Any]) -> dict[str, Any]:
    """Extras của mọi nút một đề xuất dựng ra."""
    return {
        "prov": "suggested",
        "rule": s["rule"],
        "ruleVersion": s.get("ruleVersion"),
        "suggestionId": s["id"],
        "hostRef": dict(s.get("hostRef") or {}),
        "state": s.get("state", "active"),
    }


def short_name(label: str, sid: str) -> str:
    """`<label>_<sha256(id)[:12]>`: ổn định, ngắn hơn trần 63 byte của Blender."""
    return f"{label}_{hashlib.sha256(sid.encode('utf-8')).hexdigest()[:12]}"


def _cap(severity: Severity) -> Severity:
    """Nặng nhất `minor`."""
    return "minor" if severity in ("major", "critical") else severity


def _m(x: float, y: float) -> tuple[float, float]:
    """Điểm mm của layer → mét của plan (như `units.to_m`)."""
    return x / 1000, -y / 1000


def _r(*xs: float) -> list[float]:
    """Làm tròn µm cho plan tất định."""
    return [round(x, 6) for x in xs]


def model_size_m(meta: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """`sizeMm` của gói (list `[x, y, z]` hoặc dict `w/d/h` hay `x/y/z`) → mét; thiếu → None.

    Riêng cho đề xuất: `assets._size_mm` chỉ đọc `x/y/z` (đang sửa ở nhánh M1), gói thật ghi `w/d/h`."""
    s = meta.get("sizeMm")
    if isinstance(s, Mapping):
        vals = (s.get("w", s.get("x", 0)), s.get("d", s.get("y", 0)), s.get("h", s.get("z", 0)))
    elif isinstance(s, (list, tuple)) and len(s) == 3:
        vals = tuple(s)
    else:
        return None
    out = tuple(float(v) / 1000 for v in vals)
    return (out[0], out[1], out[2]) if all(v > 0 for v in out) else None


@dataclass(frozen=True)
class Frame:
    """Trục tim tường theo mm: gốc `start`, `d` dọc tường, trái = (-dy, dx)."""

    ox: float
    oy: float
    dx: float
    dy: float
    length: float

    def at(self, along: float, across: float = 0.0) -> tuple[float, float]:
        """Điểm mm từ toạ độ cục bộ."""
        return self.ox + self.dx * along - self.dy * across, self.oy + self.dy * along + self.dx * across

    def rect_m(self, a0: float, a1: float, c0: float, c1: float) -> Polygon:
        """Chữ nhật cục bộ [a0, a1] x [c0, c1] (mm) → đa giác mét."""
        return Polygon([_m(*self.at(a, c)) for a, c in ((a0, c0), (a1, c0), (a1, c1), (a0, c1))])


def line_frame(start: Mapping[str, float], end: Mapping[str, float]) -> Frame:
    """Khung của đoạn `start`→`end` (mm); đoạn rỗng → ValueError."""
    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
    length = math.hypot(dx, dy)
    if length == 0:
        raise ValueError("đoạn có độ dài 0")
    return Frame(float(start["x"]), float(start["y"]), dx / length, dy / length, length)


class LevelCtx:
    """Ngữ cảnh một tầng: tường/phòng/ô mở theo id, sàn hoàn thiện và cao độ trần từng phòng."""

    def __init__(
        self,
        f: Mapping[str, Any],
        wp: Any,
        sl: Any,
        mats: MaterialResolver,
        assets: AssetResolver,
        plan: Mapping[str, Any],
    ) -> None:
        """Gom các bảng tra từ tầng và kết quả M1 của tầng."""
        layer = f["layer"]
        self.lid: str = f["levelId"]
        self.height: float = f["heightMm"] / 1000
        self.mats, self.assets = mats, assets
        self.walls = {w["id"]: w for w in layer.get("walls", [])}
        self.pieces = {p["id"]: p for p in wp.pieces}
        self.wall_union: BaseGeometry = wp.union
        self.openings = {o["id"]: o for o in layer.get("openings", [])}
        self.rooms = {r["id"]: r for r in layer.get("rooms", [])}
        self.room_polys = {
            rid: Polygon([to_m(p) for p in r["outline"]])
            for rid, r in self.rooms.items()
            if len(r.get("outline") or ()) >= 3
        }
        self.furniture = {x["id"]: x for x in layer.get("furniture", [])}
        self.finish = {x["roomId"]: x["geom"] for x in sl.finishes}
        self.ceil_z = {
            x["roomId"]: x["z"]
            for x in sl.ceilings
            if x["roomId"] in self.rooms and not mats.is_outdoor(self.rooms[x["roomId"]])
        }
        self.assembly_names = {a["name"] for a in plan["assemblies"]}
        self.runs: list[tuple[str, Polygon]] = []

    def wall(self, s: Mapping[str, Any]) -> tuple[dict[str, Any], Frame]:
        """Tường chủ thẳng của đề xuất + khung của nó."""
        wall = self.walls.get((s.get("hostRef") or {}).get("wall"))
        if wall is None:
            raise SkipError("không còn tường chủ")
        if wall.get("curveOffsetMm"):
            raise SkipError("tường cong chưa hỗ trợ")
        line = wall["centreline"]
        return wall, line_frame(line["start"], line["end"])

    def window(self, s: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """(ô mở, mảnh tường M1, khung ô mở mét) của đề xuất gắn cửa sổ."""
        o = self.openings.get((s.get("hostRef") or {}).get("opening"))
        if o is None:
            raise SkipError("không còn ô mở chủ")
        piece = self.pieces.get(o.get("wallId"))
        if piece is None or piece.get("curved"):
            raise SkipError("ô mở không có tường thẳng dựng được")
        return o, piece, _frame(o, piece)

    def room_id(self, s: Mapping[str, Any]) -> str:
        """Phòng chủ của đề xuất."""
        rid = (s.get("hostRef") or {}).get("room")
        if rid not in self.rooms:
            raise SkipError("không còn phòng chủ")
        return str(rid)

    def ceiling(self, rid: str) -> float:
        """Cao độ mặt dưới trần của phòng; phòng không có trần → Skip."""
        if rid not in self.ceil_z:
            raise SkipError("phòng không có trần")
        return float(self.ceil_z[rid])

    def mat(self, ref: Any, role: Role, sid: str, book: RecordBook, fallback: str | None = None) -> str:
        """Khoá vật liệu (ref rỗng → `fallback` → mặc định của vai trò)."""
        return self.mats.resolve(ref or None, role, self.lid, sid, book, fallback=fallback)


def _prisms(
    geom: Any, name: str, ctx: LevelCtx, kind: str, z0: float, z1: float, mat: str, props: dict[str, Any]
) -> list[dict[str, Any]]:
    """Một prism mỗi mảnh đa giác (đã bắt lưới 0,1 mm); không còn mảnh nào → Skip."""
    if z1 - z0 <= 1e-4:
        raise SkipError("chiều cao không dương")
    out: list[dict[str, Any]] = []
    for poly in polygons_of(shapely.set_precision(geom, GRID)):
        if poly.area < defaults.SLIVER_AREA_M2:
            continue
        rings = polygon_rings(poly)
        out.append(
            {
                "name": name if not out else f"{name}_{len(out)}",
                "level": ctx.lid,
                "group": GROUP,
                "kind": kind,
                "appId": props["suggestionId"],
                "rings": rings,
                "caps": cap_triangles(rings),
                "z0": round(z0, 4),
                "z1": round(z1, 4),
                "faceMaterials": {"side": mat, "top": mat, "bottom": mat, "sideByRoom": []},
                "props": dict(props),
            }
        )
    if not out:
        raise SkipError("hình rỗng sau khi cắt")
    return out


def _box(
    name: str,
    parent: str,
    c: tuple[float, float, float],
    size: tuple[float, float, float],
    angle: float,
    mat: str,
    props: dict[str, Any],
) -> dict[str, Any]:
    """Hộp assembly theo toạ độ tầng; cỡ không dương → Skip."""
    if min(size) <= 0:
        raise SkipError(f"{name}: kích thước không dương")
    return {
        "name": name,
        "parent": parent,
        "c": _r(*c),
        "size": _r(*size),
        "angle": round(angle, 6),
        "material": mat,
        "props": dict(props),
    }


def _assembly(name: str, ctx: LevelCtx, props: dict[str, Any], boxes: list[dict[str, Any]]) -> dict[str, Any]:
    """Assembly một đề xuất."""
    return {
        "name": name,
        "level": ctx.lid,
        "group": GROUP,
        "appId": props["suggestionId"],
        "props": dict(props),
        "pivots": [],
        "boxes": boxes,
    }


# ---------------------------------------------------------------- dựng theo kind
def _face_band(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], spans: list[tuple[float, float]]
) -> list[Polygon]:
    """Dải trên mặt tường `side` theo các đoạn `spans` (mm dọc tường), dày `thicknessMm`."""
    wall, fr = ctx.wall(s)
    half = wall["thicknessMm"] / 2
    sign = 1 if p["side"] == "left" else -1
    c0, c1 = sign * half, sign * (half + p["thicknessMm"])
    return [fr.rect_m(lo, hi, min(c0, c1), max(c0, c1)) for lo, hi in spans if hi > lo]


def build_skirting(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """R1: dải len trên mặt tường, từ mặt sàn hoàn thiện cao `heightMm`."""
    ctx.wall(s)
    mat = ctx.mat(p.get("material"), "frame", s["id"], book)
    rects = _face_band(ctx, s, p, [(a, b) for a, b in p["spansMm"]])
    name = short_name("Skirting", s["id"])
    return Parts(
        prisms=_prisms(shapely.union_all(rects), name, ctx, "skirting", FIN, FIN + p["heightMm"] / 1000, mat, props)
    )


def build_wall_tile(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """R2: gạch ốp từ sàn hoàn thiện tới `heightMm` (kẹp dưới trần) trên `spansMm`; dưới cửa sổ tới `topMm` của
    `sillSpansMm`. Ô mở đã được luật cắt sẵn khỏi các đoạn."""
    ctx.wall(s)
    mat = ctx.mat(p.get("material"), "wall_interior", s["id"], book)
    rid = (s.get("hostRef") or {}).get("room")
    top = FIN + p["heightMm"] / 1000
    if rid in ctx.ceil_z:
        top = min(top, ctx.ceil_z[rid])
    name = short_name("WallTile", s["id"])
    parts = Parts()
    full = _face_band(ctx, s, p, [(a, b) for a, b in p.get("spansMm") or ()])
    if full:
        parts.prisms += _prisms(shapely.union_all(full), name, ctx, "wall-finish", FIN, top, mat, props)
    for k, (a, b, sill) in enumerate(p.get("sillSpansMm") or ()):
        under = _face_band(ctx, s, p, [(a, b)])
        if under:
            parts.prisms += _prisms(
                under[0], f"{name}_s{k}", ctx, "wall-finish", FIN, min(sill / 1000, top), mat, props
            )
    if not parts.prisms:
        raise SkipError("không có đoạn ốp nào")
    return parts


def build_cornice(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """R3: phào dải sát tường bên trong phòng, ngay dưới trần (vật liệu vắng = vật liệu trần của phòng)."""
    rid = ctx.room_id(s)
    ceil = ctx.ceiling(rid)
    fin = ctx.finish[rid]
    room = ctx.rooms[rid]
    mat = ctx.mat(p.get("material") or room.get("ceilingMaterial"), "ceiling", s["id"], book)
    d = p["depthMm"] / 1000
    band = shapely.intersection(
        shapely.difference(fin, fin.buffer(-d, join_style="mitre")), ctx.wall_union.buffer(d + 0.03), grid_size=GRID
    )
    z1 = ceil - defaults.CORNICE_TOP_GAP_M
    return Parts(
        prisms=_prisms(band, short_name("Cornice", s["id"]), ctx, "cornice", z1 - p["heightMm"] / 1000, z1, mat, props)
    )


def _obox(
    name: str,
    parent: str,
    f: Mapping[str, Any],
    u: float,
    v: float,
    z: float,
    size: tuple[float, float, float],
    mat: str,
    props: dict[str, Any],
) -> dict[str, Any]:
    """Hộp tại (u, v) của khung ô mở M1 (mét), cao tâm `z`, xoay theo tường."""
    cx = f["c"][0] + f["d"][0] * u + f["n"][0] * v
    cy = f["c"][1] + f["d"][1] * u + f["n"][1] * v
    return _box(name, parent, (cx, cy, z), size, f["angle"], mat, props)


def build_casing(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """C1: nẹp trong cửa sổ hai bên + đầu (bậu đá là cạnh dưới), cùng kiểu nẹp cửa đi."""
    _, piece, f = ctx.window(s)
    t, w, h, z0 = piece["thicknessM"], f["w"], f["h"], f["sill"]
    inside = _inside_sign(f, t, list(ctx.room_polys.values()))
    tw, tt = p["widthMm"] / 1000, p["thicknessMm"] / 1000
    mat = ctx.mat(p.get("material"), "window_frame", s["id"], book)
    root = short_name("Casing", s["id"])
    v = inside * (t / 2 + tt / 2)
    boxes = [
        _obox(f"{root}_L", root, f, -w / 2 - tw / 2, v, z0 + (h + tw) / 2, (tw, tt, h + tw), mat, props),
        _obox(f"{root}_R", root, f, w / 2 + tw / 2, v, z0 + (h + tw) / 2, (tw, tt, h + tw), mat, props),
        _obox(f"{root}_H", root, f, 0, v, z0 + h + tw / 2, (w + 2 * tw, tt, tw), mat, props),
    ]
    return Parts(assemblies=[_assembly(root, ctx, props, boxes)])


def build_sash(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """C2: khung cánh quanh từng ô kính, gắn vào pivot cánh của bộ cửa sổ M1 (cánh quay thì khung quay theo)."""
    o, _, f = ctx.window(s)
    window = f"Window_{o['id']}"
    if window not in ctx.assembly_names:
        raise SkipError("bộ cửa sổ không được dựng")
    fw = defaults.WINDOW_FRAME_W_M
    w, h, z0 = f["w"], f["h"], f["sill"]
    count = 2 if o.get("swing") in ("sliding", "double") else 1
    iw, ih = w - 2 * fw, h - 2 * fw
    sw = iw / count
    sb, sd = p["frameWidthMm"] / 1000, p["frameDepthMm"] / 1000
    if 2 * sb >= min(sw, ih):
        raise SkipError("khung cánh dày hơn ô kính")
    mat = ctx.mat(None, "window_frame", s["id"], book)
    parts = Parts()
    for k in range(count):
        pivot = f"{window}_Sash{k}"
        u = -iw / 2 + sw / 2 + k * sw
        v = (k - 0.5) * 0.025 if count > 1 else 0.0
        zc = z0 + h / 2
        for tag, du, z, size in (
            ("L", -sw / 2 + sb / 2, zc, (sb, sd, ih)),
            ("R", sw / 2 - sb / 2, zc, (sb, sd, ih)),
            ("T", 0.0, z0 + fw + ih - sb / 2, (sw - 2 * sb, sd, sb)),
            ("B", 0.0, z0 + fw + sb / 2, (sw - 2 * sb, sd, sb)),
        ):
            parts.attach.append((window, _obox(f"{pivot}_Frame{tag}", pivot, f, u + du, v, z, size, mat, props)))
    return parts


def build_railing(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """R4: tay vịn + kính + trụ (cách nhau ≤ `postSpacingMaxMm`) trên từng đoạn hở của cạnh phòng."""
    fr = line_frame(p["start"], p["end"])
    rid = (s.get("hostRef") or {}).get("room")
    room = ctx.room_polys.get(rid) if rid else None
    probe = Point(_m(*fr.at(fr.length / 2, 50)))
    inward = 1 if room is None or room.contains(probe) else -1
    hgt, hr, g = p["heightMm"] / 1000, p["handrailMm"] / 1000, p["glassMm"] / 1000
    metal = ctx.mat(p.get("metalMaterial"), "metal", s["id"], book)
    glass = ctx.mat(p.get("glassMaterial"), "glass", s["id"], book)
    root = short_name("Railing", s["id"])
    angle = math.atan2(-fr.dy, fr.dx)
    boxes: list[dict[str, Any]] = []
    inset = inward * defaults.RAILING_INSET_M * 1000
    post = defaults.RAILING_POST_M
    for k, (lo, hi) in enumerate(p["spansMm"]):
        length = (hi - lo) / 1000
        mid = (lo + hi) / 2
        cx, cy = _m(*fr.at(mid, inset))
        nm = f"{root}_{k}"
        boxes.append(_box(f"{nm}_Top", root, (cx, cy, FIN + hgt - hr / 2), (length, hr, hr), angle, metal, props))
        gz0 = FIN + defaults.RAILING_GLASS_BOTTOM_M
        gz1 = FIN + hgt - hr
        glass_size = (max(length - 2 * post, post), g, gz1 - gz0)
        boxes.append(_box(f"{nm}_Glass", root, (cx, cy, (gz0 + gz1) / 2), glass_size, angle, glass, props))
        posts = max(2, math.ceil(length * 1000 / p["postSpacingMaxMm"]) + 1)
        for j in range(posts):
            u = lo + (post / 2 + j * (length - post) / (posts - 1)) * 1000
            px, py = _m(*fr.at(u, inset))
            size = (post, post, hgt - hr)
            boxes.append(_box(f"{nm}_Post{j}", root, (px, py, FIN + (hgt - hr) / 2), size, angle, metal, props))
    if not boxes:
        raise SkipError("cạnh không còn đoạn hở")
    return Parts(assemblies=[_assembly(root, ctx, props, boxes)])


def build_cabinet_run(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """F2/F3: dãy tủ trên đường lưng `start`→`end`, nằm phía pháp tuyến trái, sâu `depthMm`.

    Tủ dưới: chân + cánh theo mô-đun + mặt đá (mặt đá trùm 20 mm ra trước, chỉ một tấm mỗi dãy). Hai dãy cùng tầng
    chồng ≥ 50 % → dãy sau bị bỏ (lỗi hai mặt đá lệch 4,5 mm của thí nghiệm B)."""
    fr = line_frame(p["start"], p["end"])
    length, depth = p["lengthMm"], p["depthMm"]
    mid = fr.length / 2
    fp = fr.rect_m(mid - length / 2, mid + length / 2, 0, depth)
    slot = p["slot"]
    for other_slot, other in ctx.runs:
        if other_slot == slot and fp.intersection(other).area >= defaults.CABINET_DUP_OVERLAP * min(
            fp.area, other.area
        ):
            raise SkipError("hộp trùng với một dãy tủ khác cùng tầng")
    z0, z1 = FIN + p["bottomMm"] / 1000, FIN + p["topMm"] / 1000
    angle = math.atan2(-fr.dy, fr.dx)
    root = short_name("CabinetRun", s["id"])
    cab = ctx.mat(p.get("material"), "frame", s["id"], book, fallback=ctx.mats.family("cabinetry"))
    lm, dm = length / 1000, depth / 1000
    gap = defaults.CABINET_GAP_M
    modules = max(1, round(length / p["moduleMm"]))
    mw = lm / modules

    def at(along_m: float, across_m: float) -> tuple[float, float]:
        return _m(*fr.at(mid + along_m * 1000, across_m * 1000))

    boxes: list[dict[str, Any]] = []
    if slot == "base":
        plinth, top_t = defaults.CABINET_PLINTH_M, defaults.CABINET_COUNTERTOP_M
        hh = (z1 - z0) - plinth - top_t
        if hh <= 0:
            raise SkipError("tủ dưới thấp hơn chân + mặt đá")
        top = ctx.mat(p.get("countertopMaterial"), "stone", s["id"], book, fallback=ctx.mats.family("countertop"))
        for k in range(modules):
            c = at(-lm / 2 + mw * (k + 0.5), (dm - 0.02) / 2)
            boxes.append(
                _box(f"{root}_Door{k}", root, (*c, z0 + plinth + hh / 2), (mw - gap, dm - 0.02, hh), angle, cab, props)
            )
        boxes.append(
            _box(
                f"{root}_Plinth",
                root,
                (*at(0, (dm - 0.08) / 2), z0 + plinth / 2),
                (lm, dm - 0.08, plinth),
                angle,
                cab,
                props,
            )
        )
        boxes.append(
            _box(
                f"{root}_Top",
                root,
                (*at(0, (dm + 0.02) / 2), z1 - top_t / 2),
                (lm, dm + 0.02, top_t),
                angle,
                top,
                props,
            )
        )
    else:
        for k in range(modules):
            c = at(-lm / 2 + mw * (k + 0.5), dm / 2)
            boxes.append(
                _box(f"{root}_Upper{k}", root, (*c, (z0 + z1) / 2), (mw - gap, dm, z1 - z0), angle, cab, props)
            )
    ctx.runs.append((slot, fp))
    parts = Parts(assemblies=[_assembly(root, ctx, props, boxes)])
    if p.get("degradedVi"):
        parts.notes.append(str(p["degradedVi"]))
    return parts


def build_curtain(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """D2: một tấm rèm xếp nếp (zíc zắc ±30 mm, bước `pleatMm`/2) + nửa thanh treo của nó; `L` về phía đầu tường."""
    _, piece, f = ctx.window(s)
    rid = (s.get("hostRef") or {}).get("room")
    polys = [ctx.room_polys[rid]] if rid in ctx.room_polys else list(ctx.room_polys.values())
    inside = _inside_sign(f, piece["thicknessM"], polys)
    n = (f["n"][0] * inside, f["n"][1] * inside)
    t = piece["thicknessM"]
    off = t / 2 + p["offsetMm"] / 1000
    base = (f["c"][0] + n[0] * off, f["c"][1] + n[1] * off)
    half = f["w"] / 2 + p["extMm"] / 1000
    pw = min(p["widthMm"] / 1000, 2 * half)
    u0, u1 = (-half, -half + pw) if p["side"] == "L" else (half - pw, half)
    steps = max(2, round(pw / (p["pleatMm"] / 2000)))
    amp, thick = defaults.CURTAIN_AMP_M, defaults.CURTAIN_T_M
    front, back = [], []
    for k in range(steps + 1):
        u = u0 + (u1 - u0) * k / steps
        v = amp * (1 if k % 2 else -1)
        front.append((base[0] + f["d"][0] * u + n[0] * v, base[1] + f["d"][1] * u + n[1] * v))
        back.append((base[0] + f["d"][0] * u + n[0] * (v - thick), base[1] + f["d"][1] * u + n[1] * (v - thick)))
    top = p["topMm"] / 1000
    if rid in ctx.ceil_z:
        top = min(top, ctx.ceil_z[rid] - defaults.CEILING_GAP_M)
    mat = ctx.mat(p.get("material"), "frame", s["id"], book)
    name = short_name("Curtain", s["id"])
    prisms = _prisms(Polygon(front + back[::-1]), name, ctx, "curtain", defaults.CURTAIN_BOTTOM_M, top, mat, props)
    rod = defaults.CURTAIN_ROD_M
    r0, r1 = (-half - 0.03, 0.0) if p["side"] == "L" else (0.0, half + 0.03)
    rc = (r0 + r1) / 2
    metal = ctx.mat(None, "metal", s["id"], book)
    rod_set = f"{name}_RodSet"
    rod_c = (base[0] + f["d"][0] * rc, base[1] + f["d"][1] * rc, top + 0.02)
    rod_box = _box(f"{name}_Rod", rod_set, rod_c, (r1 - r0, rod, rod), f["angle"], metal, props)
    parts = Parts(prisms=prisms, assemblies=[_assembly(rod_set, ctx, props, [rod_box])])
    if p.get("degradedVi"):
        parts.notes.append(str(p["degradedVi"]))
    return parts


def _rect_m(centre: Mapping[str, float], w_mm: float, d_mm: float, rot_deg: float) -> Polygon:
    """Chữ nhật w x d (mm) tâm `centre`, xoay `rotationDeg` của editor (Y xuống) → đa giác mét."""
    th = math.radians(rot_deg)
    c, s_ = math.cos(th), math.sin(th)
    pts = []
    for x, y in ((-w_mm / 2, -d_mm / 2), (w_mm / 2, -d_mm / 2), (w_mm / 2, d_mm / 2), (-w_mm / 2, d_mm / 2)):
        pts.append(_m(centre["x"] + x * c - y * s_, centre["y"] + x * s_ + y * c))
    return Polygon(pts)


def build_rug(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """D1: tấm thảm phẳng trên sàn hoàn thiện."""
    w, d = p["sizeMm"]
    mat = ctx.mat(p.get("material"), "frame", s["id"], book, fallback=ctx.mats.family("rug"))
    poly = _rect_m(p["centre"], w, d, p.get("rotationDeg") or 0)
    name = short_name("Rug", s["id"])
    return Parts(prisms=_prisms(poly, name, ctx, "rug", FIN, FIN + p["thicknessMm"] / 1000, mat, props))


def _instance(
    ctx: LevelCtx,
    name: str,
    mid: str,
    at: tuple[float, float, float],
    rot_z: float,
    scale: float,
    props: dict[str, Any],
) -> dict[str, Any]:
    """Instance mẫu gói, neo theo hộp bao (worker đặt đáy hộp ở `at.z`, tâm hộp ở `at.xy`)."""
    meta = ctx.assets.model(mid)
    return {
        "name": name,
        "level": ctx.lid,
        "group": GROUP,
        "model": f"models/{mid}.glb",
        "anchor": "bbox",
        "at": [round(at[0], 4), round(at[1], 4), round(at[2], 4)],
        "rotZ": round(rot_z, 6),
        "scale": [scale, scale, scale],
        "props": {
            **props,
            "license": meta.get("license", ""),
            "author": meta.get("author", ""),
            "source": meta.get("source", ""),
            "prov_model": "suggested",
            "status": "built",
        },
    }


def _model(ctx: LevelCtx, mid: Any) -> tuple[str, tuple[float, float, float] | None]:
    """Mẫu có trong gói (Skip khi không) + cỡ thật mét nếu gói ghi."""
    if not mid or not ctx.assets.has_model(str(mid)):
        raise SkipError(f"mẫu {mid} chưa có trong gói xuất")
    return str(mid), model_size_m(ctx.assets.model(str(mid)))


def build_item(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """P1-P3, D3-D5, AID: mẫu `modelId` co đều để vừa hộp `sizeMm` (quá ±10 % → bỏ), đáy hộp trên sàn hoàn thiện
    + `liftMm`, tâm hộp ở `centre`."""
    mid, size = _model(ctx, p.get("modelId"))
    target = [v / 1000 for v in p["sizeMm"]]
    notes = []
    scale = 1.0
    if size is None:
        notes.append(f"mẫu {mid} chưa có kích thước trong gói, giữ cỡ thật")
    else:
        scale = round(min(t / m for t, m in zip(target, size, strict=True)), 4)
        if abs(round(scale, 3) - 1) > defaults.SUGGESTION_SCALE_TOL + 1e-9:  # rules round scale to 3 decimals
            pct = f"{scale * 100:.0f}"
            raise SkipError(f"mẫu {mid} phải co giãn {pct} % để vừa hộp (ngoài ±10 %)")
    x, y = to_m(p["centre"])
    at = (x, y, FIN + (p.get("liftMm") or 0) / 1000)
    rot = -math.radians(p.get("rotationDeg") or 0)
    inst = _instance(ctx, short_name("Item", s["id"]), mid, at, rot, scale, props)
    return Parts(instances=[inst], notes=notes)


def build_ceiling_light(
    ctx: LevelCtx, s: Mapping[str, Any], p: Mapping[str, Any], props: dict[str, Any], book: RecordBook
) -> Parts:
    """L1: đèn thả (mẫu, đỉnh hộp chạm trần) hoặc đèn ốp hộp `sizeMm` ngay dưới trần, tại `centre`."""
    rid = ctx.room_id(s)
    ceil = ctx.ceiling(rid)
    x, y = to_m(p["centre"])
    name = short_name("CeilingLight", s["id"])
    if p.get("fixture") == "pendant":
        mid, size = _model(ctx, p.get("modelId"))
        if size is None:
            raise SkipError(f"mẫu {mid} chưa có kích thước trong gói")
        return Parts(instances=[_instance(ctx, name, mid, (x, y, ceil - size[2]), 0.0, 1.0, props)])
    w, d, h = (v / 1000 for v in p["sizeMm"])
    mat = ctx.mat(None, "ceiling", s["id"], book)
    box = _box(f"{name}_Panel", name, (x, y, ceil - h / 2), (w, d, h), 0.0, mat, props)
    return Parts(assemblies=[_assembly(name, ctx, props, [box])])


Builder = Callable[[LevelCtx, Mapping[str, Any], Mapping[str, Any], dict[str, Any], RecordBook], Parts]
BUILDERS: Final[dict[str, Builder]] = {
    "skirting": build_skirting,
    "wallTile": build_wall_tile,
    "cornice": build_cornice,
    "casing": build_casing,
    "sash": build_sash,
    "railing": build_railing,
    "cabinetRun": build_cabinet_run,
    "curtain": build_curtain,
    "rug": build_rug,
    "ceilingLight": build_ceiling_light,
    "item": build_item,
}
"""`modelPick` không dựng hình riêng: nó đổi mẫu của đồ chủ (`model_picks`)."""


# ---------------------------------------------------------------- điều phối
def is_effective(s: Mapping[str, Any]) -> bool:
    """Dựng được: `active`/`overridden` và không `stale`."""
    return s.get("state", "active") in BUILT_STATES and not s.get("stale")


def model_picks(f: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """`modelPick` đang hiệu lực theo id đồ chủ."""
    return {
        s["hostRef"]["furniture"]: s
        for s in sorted(f.get("suggestions") or (), key=lambda x: x["id"])
        if s.get("kind") == "modelPick" and is_effective(s) and (s.get("hostRef") or {}).get("furniture")
    }


def replaced_furniture(f: Mapping[str, Any]) -> dict[str, str]:
    """Đồ chủ (+ `mergedIds`) của dãy tủ dưới đang hiệu lực → id đề xuất thay nó: mẫu của chúng không dựng."""
    out: dict[str, str] = {}
    for s in sorted(f.get("suggestions") or (), key=lambda x: x["id"]):
        p = effective(s)
        if s.get("kind") == "cabinetRun" and is_effective(s) and p.get("slot") == "base":
            for fid in [(s.get("hostRef") or {}).get("furniture"), *(p.get("mergedIds") or ())]:
                if fid:
                    out.setdefault(fid, s["id"])
    return out


def _apply(plan: dict[str, Any], parts: Parts) -> None:
    """Ghép phần đã dựng vào plan."""
    plan["prisms"] += parts.prisms
    plan["assemblies"] += parts.assemblies
    plan["instances"] += parts.instances
    by_name = {a["name"]: a for a in plan["assemblies"]}
    for name, box in parts.attach:
        by_name[name]["boxes"].append(box)


def _record(
    book: RecordBook, lid: str, s: Mapping[str, Any], status: Status, reasons: list[str], severity: Severity
) -> None:
    """Một bản ghi `suggestion:<rule>` mỗi đề xuất."""
    book.add(lid, f"suggestion:{s['rule']}", s["id"], status, _cap(severity), "; ".join(r for r in reasons if r))


def suggestions_plan(
    f: Mapping[str, Any],
    wp: Any,
    sl: Any,
    mats: MaterialResolver,
    assets: AssetResolver,
    book: RecordBook,
    plan: dict[str, Any],
    summary: Summary,
) -> None:
    """Dựng lớp đề xuất của một tầng đã dựng phần M1 vào `plan`; ghi sổ và gom `summary`."""
    raw = sorted(f.get("suggestions") or (), key=lambda x: x["id"])
    meta = f.get("suggestionsMeta") or {}
    if not raw and not meta:
        return
    summary.seen = True
    if meta.get("rulesetSha"):
        summary.shas.add(str(meta["rulesetSha"]))
    ctx = LevelCtx(f, wp, sl, mats, assets, plan)
    lid = ctx.lid
    for s in raw:
        state = s.get("state", "active")
        if s.get("stale") or state == "disabled":
            summary.disabled.append(s["id"])
            summary.counts["stale" if s.get("stale") else "disabled"] += 1
            continue
        if state == "adopted":
            summary.counts["adopted"] += 1
            continue
        if s.get("kind") == "modelPick":
            continue  # counted where the host furniture is resolved (build._furniture)
        builder = BUILDERS.get(str(s.get("kind")))
        if builder is None or state not in BUILT_STATES:
            summary.counts["skipped"] += 1
            _record(book, lid, s, "skipped", [f"kind/trạng thái {s.get('kind')}/{state} chưa hỗ trợ"], "minor")
            continue
        sub = RecordBook()
        try:
            parts = builder(ctx, s, effective(s), node_props(s), sub)
        except SkipError as exc:
            summary.counts["skipped"] += 1
            _record(book, lid, s, "skipped", [str(exc.args[0])], "minor")
            continue
        except BUILD_ERRORS as exc:
            summary.counts["skipped"] += 1
            _record(
                book, lid, s, "skipped", [f"tham số đề xuất không dựng được ({type(exc).__name__}: {exc})"], "minor"
            )
            continue
        _apply(plan, parts)
        reasons = parts.notes + [r.reason for r in sub.records if r.status != "built"]
        summary.counts["built"] += 1
        if reasons:
            _record(book, lid, s, "degraded", reasons, "minor")
        else:
            _record(book, lid, s, "built", [], "info")
