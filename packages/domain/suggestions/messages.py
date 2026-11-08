"""Vietnamese sentences the suggestion layer writes for people (BE-00 §10): stale reasons and log notes."""

from typing import Final

TIMES: Final = "\u00d7"
"""Multiplication sign (escaped: ruff RUF001 flags the literal)."""
HOST_GONE: Final = "đồ chủ đã bị xoá khỏi bản vẽ"
RULE_GONE: Final = "luật không còn trong bộ luật"
NO_LONGER: Final = "luật không còn đề xuất cho chủ này"
MAJOR_STALE: Final = "luật đã đổi, xem lại"
HAS_MODEL: Final = "đồ chủ đã có mẫu người chọn"
LOCKED_KIND: Final = "đồ đã duyệt hoặc đã sửa: luật không đổi loại đồ"
CURVED_WALL: Final = "tường cong: chưa hỗ trợ len chân tường"
OTHER_FACE: Final = "tường giáp cùng phòng ở cả hai mặt: chỉ giữ mặt dài hơn"
CLOSED_LOGGIA: Final = "lô gia kín tường trong dữ liệu, không có cạnh hở"
NO_ROOM_INSIDE: Final = "không xác định được phòng phía trong"
CURTAIN_NO_FIT: Final = "rèm không vừa giữa hai tường bên"
ADOPTED_GONE: Final = "món nhận nuôi đã bị xoá: đề xuất chuyển sang tắt"
PURGED: Final = "gợi ý cũ không có dữ liệu người dùng: xoá khi đổi bộ luật"
CAP_PURGED: Final = "vượt trần số đề xuất: xoá gợi ý cũ không có dữ liệu người dùng"
MIGRATED: Final = "luật đổi bản chính: chuyển trạng thái sang id mới"


def dropped_override(keys: list[str]) -> str:
    """Log line for override keys no longer allowed after a rule bump."""
    return f"điều chỉnh {', '.join(keys)} không còn áp dụng"


def curtain_degraded(ext_mm: int) -> str:
    """D2 fell back to a narrower overhang."""
    return f"rèm chỉ rộng hơn ô cửa {_m(ext_mm)} m mỗi bên"


def model_too_big(model_id: str, ratio_pct: int, tol_pct: int) -> str:
    """F1 would need to shrink the model more than allowed."""
    return f"{model_id} cần co còn {ratio_pct} % (quá {tol_pct} %), giữ mẫu hiện có"


def model_pick(long_mm: int, short_mm: int, name_vi: str) -> str:
    """F1 reason, e.g. `hộp 0,60 x 0,48 m → bồn cầu` with a real multiplication sign."""
    return f"hộp {_m(long_mm, 2)} {TIMES} {_m(short_mm, 2)} m → {name_vi}"


def _m(mm: int, digits: int = 1) -> str:
    """Millimetres as metres with a Vietnamese decimal comma."""
    return f"{mm / 1000:.{digits}f}".replace(".", ",")


CURVED_WALL_TILE: Final = "tường cong: chưa hỗ trợ ốp gạch"
UPPER_BLOCKED: Final = "tủ trên vướng cửa sổ hoặc cửa đi trên cùng mảng tường"
NIGHTSTAND_NO_ROOM: Final = "không đủ chỗ cạnh đầu giường (vướng tường, đồ hoặc vùng mở cửa)"
COFFEE_NO_ROOM: Final = "không đủ chỗ đặt bàn trà trước sofa"
NO_FREE_WALL: Final = "không có mảng tường trống đủ rộng"
USAGE_VI: Final = {
    "livingRoom": "phòng khách",
    "bedroom": "phòng ngủ",
    "kitchen": "bếp",
    "bathroom": "phòng tắm",
    "corridor": "hành lang",
    "stairwell": "buồng thang",
    "utility": "phòng kỹ thuật",
    "loggia": "lô gia",
    "other": "phòng khác",
}
COLLISION_VI: Final = {
    "wall": "tường",
    "outside-room": "ra ngoài phòng",
    "opening-clearance": "vùng mở cửa đi",
    "window": "vùng cửa sổ",
    "item-item": "đồ khác",
}


def run_wrong_room(usage: str | None) -> str:
    """F2 role gate."""
    where = USAGE_VI.get(usage or "", "chỗ không thuộc phòng nào")
    return f"hộp tủ bếp nằm trong {where}, không phải bếp hay phòng khách: không dựng dãy tủ"


def run_off_wall(pct: int, max_mm: int, need_pct: int) -> str:
    """F2 wall-contact gate (a floating cabinet)."""
    return (
        f"mặt sau hộp chỉ có {pct} % chiều dài cách tường ≤ {_m(max_mm, 2)} m (cần ≥ {need_pct} %): không dựng dãy tủ"
    )


def run_too_short(long_mm: int, min_mm: int) -> str:
    """F2: box shorter than a run."""
    return f"hộp dài {_m(long_mm, 2)} m, ngắn hơn {_m(min_mm, 2)} m: không dựng dãy tủ"


def run_too_deep(short_mm: int, max_mm: int) -> str:
    """F2: box deeper than a run."""
    return f"hộp sâu {_m(short_mm, 2)} m, quá {_m(max_mm, 2)} m: không giống dãy tủ"


def duplicate_box(pct: int, big_id: str) -> str:
    """F2: a box folded into a larger one of the same kind."""
    return f"hộp trùng: chồng {pct} % lên hộp {big_id}; gộp thành một dãy tủ"


def merged_boxes(ids: list[str]) -> str:
    """F2 run that absorbed duplicate boxes."""
    return f"gộp hộp trùng {', '.join(ids)}"


def collides(what: str, types: list[str]) -> str:
    """A rule-created collision that could not be slid away: the suggestion is skipped."""
    return f"{what} sẽ va {', '.join(COLLISION_VI.get(t, t) for t in types)}: bỏ đề xuất"


def no_room_for(model_id: str, w_mm: int, d_mm: int, tol_pct: int) -> str:
    """P1/D5 placement found no spot even after the allowed shrink."""
    return f"không đủ chỗ cho {model_id} {_m(w_mm, 2)} {TIMES} {_m(d_mm, 2)} m (đã thử co tới {tol_pct} %)"


def scaled(model_id: str, pct: int) -> str:
    """Placement had to shrink the model."""
    return f"{model_id} co còn {pct} % để vừa chỗ"


def rug_inside(pct: int, need_pct: int) -> str:
    """D1: too little of the rug lies in the room."""
    return f"thảm chỉ còn {pct} % trong phòng (cần ≥ {need_pct} %)"
