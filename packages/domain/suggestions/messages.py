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
MAJOR_RESET: Final = "luật đổi bản chính: điều chỉnh của người dùng không còn áp dụng"


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
