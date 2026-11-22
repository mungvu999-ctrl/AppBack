"""Dữ liệu đã giải một ca bench cho việc chấm: layer, tỉ lệ, độ lệch, phát hiện chất lượng, đổi hệ toạ độ.

Hệ toạ độ: điểm layer (mm của AI) → px ảnh gốc = mm / scaleAI + độ lệch trang → "mm thật" = px x mm/px đáp án
(không có thì scaleAI; AI chưa có tỉ lệ thì 20). Tiêu chí hình học chấm trên mm thật để tách khỏi lỗi tỉ lệ.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from tools.bench.score.geometry import Arr, Point
from tools.bench.score.io import Bench, Json

# Cảnh báo ảnh chung (độ phân giải, tương phản…) hiện ở gần như mọi ca
GENERIC = {"RESOLUTION_TOO_LOW", "LOW_CONTRAST", "HIGH_NOISE", "SKEW_DETECTED", "SKEW_NOT_MEASURED", "FRAME_NOT_FOUND"}
DEFAULT_MM_PER_PX = 20.0  # mm thật ước khi cả đáp án lẫn AI chưa có tỉ lệ
Frame = Callable[[Json], Any]


@dataclass
class Case:
    """Một ca đã đọc: đầu vào chấm cho mọi nhóm tiêu chí. `wsegs` là (N,4) đoạn tim tường theo mm thật."""

    bench: Bench
    cid: str
    row: dict[str, str]
    key: Json
    meta: Json
    prog: Json
    qual: Json | None
    body: Json
    level: Json
    sc: float
    off: tuple[int, int] | None
    status: str
    findings: list[Json]
    cal: bool
    walls: list[Json] = field(init=False)
    ops: list[Json] = field(init=False)
    rooms: list[Json] = field(init=False)
    scale_known: bool = field(init=False)
    tm: float = field(init=False)
    ready: Json = field(init=False)
    signal: bool = field(init=False)
    wsegs: Arr = field(init=False)
    wall_by: dict[str, tuple[Json, Arr]] = field(init=False)

    def __post_init__(self) -> None:
        """Suy ra các đại lượng dẫn xuất: tỉ lệ, readiness, tín hiệu "có báo", đoạn tường theo mm thật."""
        layer = self.body.get("layer") or {}
        self.walls = layer.get("walls") or []
        self.ops = layer.get("openings") or []
        self.rooms = layer.get("rooms") or []
        self.scale_known = abs(self.sc - 1.0) > 1e-6
        self.tm = self.key.get("mm_per_px") or (self.sc if self.scale_known else DEFAULT_MM_PER_PX)
        self.ready = self.body.get("readiness") or {}
        self.signal = _signal(self)
        mm = self.mm
        self.wsegs = (
            np.array([[*mm(w["centreline"]["start"]), *mm(w["centreline"]["end"])] for w in self.walls])
            if self.walls
            else np.zeros((0, 4))
        )
        self.wall_by = {w["id"]: (w, self.wsegs[i]) for i, w in enumerate(self.walls)}

    @property
    def k(self) -> float:
        """Hệ số mm AI → mm thật."""
        return self.tm / self.sc

    @property
    def group(self) -> str:
        """Nhóm của ca (A-F) theo manifest."""
        return self.row["group"]

    def px(self, p: Json) -> Point:
        """Điểm AI (mm) → px ảnh gốc."""
        o = self.off or (0, 0)
        return (p["x"] / self.sc + o[0], p["y"] / self.sc + o[1])

    def mm(self, p: Json) -> Point:
        """Điểm AI → mm thật."""
        q = self.px(p)
        return (q[0] * self.tm, q[1] * self.tm)

    def op_center(self, op: Json, frame: Frame | None = None) -> Any:
        """Tâm một ô mở trong hệ `frame` (mặc định mm thật); None khi tường chủ không có."""
        w = self.wall_by.get(op["wallId"])
        if not w:
            return None
        s, e = w[0]["centreline"]["start"], w[0]["centreline"]["end"]
        length = math.dist((s["x"], s["y"]), (e["x"], e["y"])) or 1
        t = (op["offsetMm"] + op["widthMm"] / 2) / length
        pt = {"x": s["x"] + t * (e["x"] - s["x"]), "y": s["y"] + t * (e["y"] - s["y"])}
        return (frame or self.mm)(pt)

    def centers(self, kind: str | None, frame: Frame | None = None) -> list[Arr]:
        """Tâm (mảng 2) các ô mở có tường chủ, lọc theo `kind` nếu cho."""
        return [
            np.array(self.op_center(x, frame))
            for x in self.ops
            if (kind is None or x["kind"] == kind) and self.op_center(x, frame)
        ]


def _signal(case: Case) -> bool:
    """Ca đã "báo" người dùng chưa (đối nghịch của lỗi im lặng).

    Cảnh báo ảnh chung chỉ tính là "có báo" ở ca ảnh xấu (nhóm C); ca khác phải có lý do readiness hoặc mã riêng.
    Lượt thất bại kèm mã lỗi (vd NOT_A_FLOOR_PLAN) là đã báo rõ, không phải lỗi im lặng.
    """
    reasons = case.ready.get("reasons", [])
    specific = any(r != "scaleUnresolved" for r in reasons) or any(f["code"] not in GENERIC for f in case.findings)
    poor = case.group == "C" and any(f["severity"] == "poor" for f in case.findings)
    return specific or bool(case.prog.get("error")) or poor


def build_case(bench: Bench, cid: str, cal: bool = False) -> Case:
    """Đọc mọi tệp của ca `cid` (`cal`: tệp sau bước đặt tỉ lệ trong `calibrated/`) và dựng `Case`."""
    row, key = bench.manifest[cid], bench.key_of(cid)
    meta, prog = bench.load(cid, "meta") or {}, bench.load(cid, "progress") or {}
    lay, qual = bench.load(cid, "layer", cal), bench.load(cid, "quality", cal)
    body = (lay or {}).get("body") or {}
    level = body.get("level") or {}
    off = bench.find_offset(cid) if row["group"] in "ABDF" else None  # E chỉ có hành vi
    floors = ((qual or {}).get("body") or {}).get("floors", [])
    findings = [f for fl in floors if fl.get("floorId") == level.get("id") for f in fl.get("findings", [])]
    return Case(
        bench=bench, cid=cid, row=row, key=key, meta=meta, prog=prog, qual=qual, body=body, level=level,
        sc=level.get("scaleMillimetresPerPixel") or 1.0, off=off,
        status=meta.get("status") or prog.get("status") or "missing", findings=findings, cal=cal,
    )  # fmt: skip
