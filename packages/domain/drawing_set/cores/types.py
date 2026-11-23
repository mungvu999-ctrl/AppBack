"""Kiểu dữ liệu vào/ra của lõi thang xuyên tầng (K-32e): hộp mm, ô thang, mặt bằng, dịch chồng tầng, lõi.

Mọi toạ độ là mm trong khung riêng của từng mặt bằng; dịch chồng tầng đổi khung tầng trên về khung tầng dưới.
Đây là GIAO DIỆN ĐẦU VÀO TỐI THIỂU: K-32d (chồng tầng) chỉ cần điền `StackShift`, không có kiểu nào ở đây nhập
mã K-32d. Chỉ thư viện chuẩn.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Final, Literal

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (mm)
Point = tuple[float, float]
WellKind = Literal["stair", "curved"]

UNLINKED_CONFIDENCE: Final = 0.3  # ô thang chỉ thấy trên một tầng: không phải lõi


@dataclass(frozen=True)
class PlanText:
    """Một dòng chữ trên mặt bằng: nội dung và tâm hộp chữ (mm). Dùng cho bằng chứng thang và veto phòng."""

    text: str
    centre_mm: Point


@dataclass(frozen=True)
class Well:
    """Một ô thang dò được trên một mặt bằng (hộp mm), kèm hình dạng và bằng chứng.

    `angle_deg`/`pitch_mm` None (thang cong) thì coi là giống mọi ô khác. `yolo_ok` None = mặt bằng này không có
    lớp mô hình; True/False = có hộp mô hình ≥ 0,3 trùng ô hay không (chế độ `confirm`, nguồn thứ hai).
    `evidence` ⊂ {"stair_word", "tread_numbers"} (xem `evidence.evidence_near`).
    """

    box: Box
    kind: WellKind = "stair"
    angle_deg: float | None = None
    pitch_mm: float | None = None
    evidence: frozenset[str] = frozenset()
    yolo_ok: bool | None = None


@dataclass(frozen=True)
class CorePlan:
    """Mặt bằng của một tầng: các ô thang cùng những gì phần mở rộng tầng cần để phủ quyết.

    `texts`: chữ trên mặt bằng (tên phòng đè lên giếng thang = thang không tới đó).
    `wall_cover(box)`: phần (0..1) hộp mm bị mặt nạ tường phủ; None = không có mặt nạ (coi là 0).
    `cell_of(point)`: hộp ô tường kín quanh một điểm (cho ô trống); None = không có mặt nạ.
    """

    floor_no: int
    view_id: str
    wells: tuple[Well, ...] = ()
    texts: tuple[PlanText, ...] = ()
    wall_cover: Callable[[Box], float] | None = field(default=None, compare=False)
    cell_of: Callable[[Point], Box | None] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class StackShift:
    """Dịch chồng tầng của hai mặt bằng liền kề (B + shift_mm → khung A, A là tầng dưới) và độ chắc 0..1.

    `shift_mm` None = chưa căn được (nối tầng qua cặp này bị bỏ, độ chắc coi là 0).
    """

    shift_mm: Point | None
    confidence: float


@dataclass(frozen=True)
class StairCore:
    """Lõi thang xuyên ≥ 2 tầng.

    `floors` = quan sát + giả định (`assumed_floors`); `confidence` chỉ theo đoạn quan sát, độ chắc từng tầng ở
    `floor_confidence`. `centre_mm` theo khung tầng thấp nhất của `floors`. `members` = (view_id, chỉ số ô).
    `assumed_boxes` = (view_id, hộp mm) của tầng giả định.
    """

    floors: tuple[int, ...]
    observed_floors: tuple[int, ...]
    assumed_floors: tuple[int, ...]
    centre_mm: tuple[int, int]
    confidence: float
    floor_confidence: Mapping[int, float]
    sources: tuple[str, ...]
    shift_conf: float
    shape_ok: bool
    members: tuple[tuple[str, int], ...]
    assumed_boxes: tuple[tuple[str, Box], ...] = ()


@dataclass(frozen=True)
class UnlinkedWell:
    """Ô thang chỉ thấy trên một tầng (độ chắc 0,3): chưa đủ gọi là lõi."""

    floor_no: int
    view_id: str
    well_index: int
    box_mm: tuple[int, int, int, int]
    confidence: float = UNLINKED_CONFIDENCE


@dataclass(frozen=True)
class VoidHint:
    """Gợi ý ô trống/giếng trời từ chữ: hộp (nếu có ô tường kín) hoặc chỉ một điểm."""

    floor_no: int
    view_id: str
    text: str
    confidence: float
    source: Literal["text+cell", "text"]
    box_mm: tuple[int, int, int, int] | None = None
    centre_mm: tuple[int, int] | None = None


def validate_plans(plans: Sequence[CorePlan], shifts: Sequence[StackShift | None]) -> None:
    """Ném ValueError nếu mặt bằng không tăng dần theo số tầng hoặc số dịch khác số mặt bằng - 1 (fail-closed)."""
    if len(shifts) != max(0, len(plans) - 1):
        raise ValueError(f"cần {max(0, len(plans) - 1)} dịch chồng tầng, nhận {len(shifts)}")
    for lower, upper in pairwise(plans):
        if upper.floor_no <= lower.floor_no:
            raise ValueError("mặt bằng phải sắp theo số tầng tăng dần, không trùng tầng")
