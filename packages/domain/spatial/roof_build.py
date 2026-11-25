"""Tham số thô của một mái → node Pascal `roof` + `roof-segment` (NHA-02, thiết kế §2.C Câu 4-5).

BE ghi đủ khoá hình học để node nạp vào Pascal rồi đọc ngược ra vẫn y nguyên; lệch một khoá là mái AI bị đánh dấu
`edited` ngay lần mở đầu. Ba chỗ Pascal tự sửa node lúc nạp và cách né: `support` là `level` (cao độ do BE quyết,
Pascal không bám đỉnh tường), `pitch` luôn > 0 kể cả mái bằng, không ghi `fascia` / `metadata.autoGutter`.

Quy ước toạ độ: mặt bằng `(x, y)` mm ↔ Pascal `(x, z)` m; mặt bằng quay +θ = yaw -θ (`floorAlignment.ts:85,103`).
Chưa nối vào luồng nào; nơi dùng đầu tiên là NHA-07a.
"""

import math
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import JsonValue

type RoofForm = Literal["flat", "shed", "gable", "hip"]

_FLAT_PITCH_DEG: Final = 40.0  # mặc định của schema Pascal; dạng `flat` bỏ qua `pitch` khi dựng hình
_MAX_PITCH_DEG: Final = 85.0  # trần của schema Pascal
_DECK_M: Final = 0.1  # hai bề dày mặc định của Pascal, ghi tường minh để BE tính được cốt đỉnh hoàn thiện
_SHINGLE_M: Final = 0.05
_QUARTER_TURN: Final = math.pi / 2


@dataclass(frozen=True, slots=True)
class RoofRect:
    """Một mặt mái chữ nhật: tâm trên mặt bằng (mm), cạnh dọc trục chính và cạnh vuông góc trục chính."""

    cx_mm: float
    cy_mm: float
    along_mm: float
    across_mm: float


@dataclass(frozen=True, slots=True)
class RoofRects:
    """Các chữ nhật của một mái; trục chính chung quay `angle_deg` ngược chiều kim đồng hồ so với trục x."""

    angle_deg: float
    rects: tuple[RoofRect, ...]


def ridge_height_mm(form: RoofForm, width_mm: float, depth_mm: float, pitch_deg: float) -> float:
    """Chiều cao từ diềm tới đỉnh = quãng dốc x tan(độ dốc); quãng dốc theo dạng như `getPrimarySlopeRun` của Pascal.

    `width_mm` là cạnh mang đỉnh mái. Mái bằng không có đỉnh nên ra 0, dù node ghi `pitch` 40.
    """
    run = {"flat": 0.0, "shed": depth_mm, "gable": depth_mm / 2, "hip": min(width_mm, depth_mm) / 2}[form]
    return run * math.tan(math.radians(pitch_deg))


def _metres(mm: float) -> float:
    """mm → m, tròn tới micromet; cộng 0.0 để số không âm không ra JSON."""
    return round(mm / 1000, 6) + 0.0


def roof_nodes(
    rects: RoofRects,
    *,
    form: RoofForm,
    pitch_deg: float,
    quarter_turns: int,
    eave_above_floor_mm: float,
    overhang_mm: float,
    wall_thickness_mm: float,
    roof_id: str,
) -> tuple[dict[str, JsonValue], ...]:
    """Phần tử của `Roof.nodes`: node `roof` rồi mỗi chữ nhật một `roof-segment`, không mang cây.

    `eave_above_floor_mm` là cốt diềm (mặt dưới kết cấu mái tại mép tường) so với sàn tầng mái che; `overhang_mm` là
    độ đua ngang. `quarter_turns` quay đỉnh mái quanh tâm từng chữ nhật; 0 là đỉnh dọc cạnh dài. Mái một dốc: mép cao
    nằm ở phía -z cục bộ của mặt mái (`getRoofSegmentSurfaceY` của Pascal), tức phía âm của trục vuông góc trục chính
    khi `quarter_turns` = 0 trên chữ nhật nằm dọc trục; mỗi phần tư vòng đưa mép cao sang -dọc, +vuông góc, +dọc.
    Mái bằng bỏ qua `pitch_deg`. Ném `ValueError` cho tham số Pascal sẽ tự sửa (độ dốc ngoài (0, 85), kích thước
    không dương) hoặc dựng sai hình (shed nhiều mặt).
    """
    sloped = form != "flat"
    if not rects.rects:
        raise ValueError("mái phải có ít nhất một chữ nhật")
    sides = [side for rect in rects.rects for side in (rect.along_mm, rect.across_mm)]
    if not all(0 < side < math.inf for side in sides) or not all(
        0 <= t < math.inf for t in (overhang_mm, wall_thickness_mm)
    ):
        raise ValueError("kích thước mái phải hữu hạn: cạnh chữ nhật dương, độ đua và bề dày tường không âm")
    if sloped and not 0 < pitch_deg < _MAX_PITCH_DEG:
        raise ValueError(f"độ dốc mái {pitch_deg}° nằm ngoài (0, {_MAX_PITCH_DEG})")
    if quarter_turns not in range(4):
        raise ValueError(f"hướng mái {quarter_turns} phải là 0..3 phần tư vòng")
    if form == "shed" and len(rects.rects) > 1:
        raise ValueError("mái một dốc chỉ dựng được trên một chữ nhật")

    angle = math.radians(rects.angle_deg)
    cos, sin = math.cos(angle), math.sin(angle)
    framed = [(r.cx_mm * cos + r.cy_mm * sin, -r.cx_mm * sin + r.cy_mm * cos, r) for r in rects.rects]
    mid_u = (min(u - r.along_mm / 2 for u, _, r in framed) + max(u + r.along_mm / 2 for u, _, r in framed)) / 2
    mid_v = (min(v - r.across_mm / 2 for _, v, r in framed) + max(v + r.across_mm / 2 for _, v, r in framed)) / 2
    pitch = pitch_deg if sloped else _FLAT_PITCH_DEG
    slope_cos = math.cos(math.radians(pitch)) if sloped else 1.0

    roof: dict[str, JsonValue] = {
        "object": "node",
        "id": f"roof_{roof_id}",
        "type": "roof",
        "visible": True,
        "metadata": {},
        "position": [
            _metres(mid_u * cos - mid_v * sin),
            _metres(eave_above_floor_mm),
            _metres(mid_u * sin + mid_v * cos),
        ],
        "rotation": -angle + 0.0,
        "support": {"kind": "level"},
    }
    segments: list[dict[str, JsonValue]] = []
    for index, (u, v, rect) in enumerate(framed):
        turns = (quarter_turns + int(rect.along_mm < rect.across_mm)) % 4
        width, depth = (rect.across_mm, rect.along_mm) if turns % 2 else (rect.along_mm, rect.across_mm)
        segments.append(
            {
                "object": "node",
                "id": f"rseg_{roof_id}_{index}",
                "type": "roof-segment",
                "visible": True,
                "metadata": {},
                "position": [_metres(u - mid_u), 0.0, _metres(v - mid_v)],
                "rotation": turns * _QUARTER_TURN,
                "roofType": form,
                "width": _metres(width),
                "depth": _metres(depth),
                "wallHeight": 0.0,
                "pitch": pitch,
                "wallThickness": _metres(wall_thickness_mm),
                "deckThickness": _DECK_M,
                "overhang": _metres(overhang_mm / slope_cos),
                "shingleThickness": _SHINGLE_M,
            }
        )
    return (roof, *segments)
