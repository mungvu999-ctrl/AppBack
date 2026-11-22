"""Model trên dây của đề xuất cho cả nhà (HS-02, camelCase; trường tuỳ chọn **vắng**, không `null`, W2).

Enum dây là `Literal`. Giá trị miền ngoài tập (nhãn tầng, nguồn, mã câu hỏi mới) không làm 500 cả trang:
`house_proposal_out` đổi nó về giá trị dây dè dặt nhất và ghi log; còn khoảng hở thì
`test_wire_enums_cover_what_the_domain_can_produce` bắt ở CI. Độ tin được kẹp vào [0, 1] (W3).
"""

import logging
from collections.abc import Iterable
from typing import Final, Literal, get_args

from apps.api.core.wire import WireModel
from apps.api.floors.schemas import ELEVATION_MAX, ELEVATION_MIN, HEIGHT_MAX, HEIGHT_MIN
from packages.domain.drawing_set import FloorProposal, HouseProposal
from packages.domain.drawing_set.proposal import ORDER_TEXT_CONFLICT

type FloorKind = Literal["plan", "section", "elevation", "roof", "site", "detail", "other"]
type StoreyLabel = Literal["ground", "basement", "mezzanine", "tum", "attic", "roof_terrace", "floor"]
type LevelSource = Literal["plan", "plan+section", "section", "dims", "convention", "default"]
type QuestionCode = Literal[
    "STOREY_UNSURE",
    "STOREY_CONFLICT",
    "ORDER_TEXT_CONFLICT",
    "LEVEL_MISSING",
    "HEIGHT_ASSUMED",
    "NOT_A_PLAN",
    "NO_TEXT",
]
type OrderReason = Literal["title", "level"]

_log: Final = logging.getLogger(__name__)


class QuestionOut(WireModel):
    """Một câu hỏi cho người dùng: chỉ mã, FE viết câu tiếng Việt.

    `ORDER_TEXT_CONFLICT` kèm lý do: `reason` = tên hình ("title") hay cốt ("level") mâu thuẫn thứ tự tầng của
    dự án, và `textStoreyNo` = số tầng chữ ghi (khi đọc được). Các mã khác không có hai trường này (vắng, W2).
    """

    code: QuestionCode
    reason: OrderReason | None = None
    text_storey_no: int | None = None


class FloorProposalOut(WireModel):
    """Đề xuất cho một tầng; `storeyLabel`, cốt và chiều cao vắng khi máy không đề xuất."""

    floor_id: str
    kind: FloorKind
    kind_confidence: float
    storey_label: StoreyLabel | None = None
    storey_no: int | None = None
    storey_confidence: float
    elevation_mm: int | None = None
    elevation_confidence: float | None = None
    elevation_source: LevelSource | None = None
    height_mm: int | None = None
    height_confidence: float | None = None
    height_source: LevelSource | None = None
    questions: list[QuestionOut]
    is_section: bool | None = None
    """`true` khi người dùng đã đánh dấu bản vẽ của dòng này là mặt cắt (`floors.is_section`); vắng khi không."""


class HouseProposalOut(WireModel):
    """`GET …/house-proposal`: công tắc, các tầng theo thứ tự dự án và id lượt chạy đã cho chữ.

    Công tắc tắt → `{"enabled": false, "floors": [], "runIds": []}` (mẫu N47), không phải lỗi.
    """

    enabled: bool
    floors: list[FloorProposalOut]
    run_ids: list[str]


_KINDS: Final = get_args(FloorKind.__value__)
_LABELS: Final = get_args(StoreyLabel.__value__)
_SOURCES: Final = get_args(LevelSource.__value__)
_CODES: Final = get_args(QuestionCode.__value__)
_REASONS: Final = get_args(OrderReason.__value__)


def _wire(value: str | None, allowed: tuple[str, ...], fallback: str | None, field: str) -> str | None:
    """`value` nếu nằm trong `allowed` (hay `None`), không thì `fallback` + một dòng log."""
    if value is None or value in allowed:
        return value
    _log.warning("house_proposal_unknown_value", extra={"field": field, "value": value})
    return fallback


def _unit(value: float | None) -> float | None:
    """Độ tin kẹp vào [0, 1] (W3)."""
    return None if value is None else min(1.0, max(0.0, value))


def _in_range(value: int | None, lo: int, hi: int, field: str) -> bool:
    """Số nằm trong khoảng của `floors` (#34 nhận được) — ngoài khoảng thì người dùng "Nhận" cũng bị 422.

    Ngoài khoảng → bỏ cả số, độ tin, nguồn (vắng, W2) và ghi log; FE vẫn `.catch` phòng thủ (L4-N07, NO-442 mục 3).
    """
    if value is None or lo <= value <= hi:
        return True
    _log.warning("house_proposal_out_of_range", extra={"field": field, "value": value})
    return False


def _question(code: str, floor: FloorProposal) -> dict[str, str | int | None]:
    """Một câu hỏi ra dây; `ORDER_TEXT_CONFLICT` mang lý do của miền (lý do lạ → vắng, không 500)."""
    out: dict[str, str | int | None] = {"code": _wire(code, _CODES, "STOREY_UNSURE", "questions")}
    conflict = floor.order_conflict
    if code == ORDER_TEXT_CONFLICT and conflict is not None:
        out["reason"] = _wire(conflict.reason, _REASONS, None, "order_reason")
        out["text_storey_no"] = conflict.text_number
    return out


def _floor_out(floor: FloorProposal, section: bool = False) -> FloorProposalOut:
    """Một tầng ra dây; `section`: dòng được người dùng đánh dấu mặt cắt (`isSection: true`, vắng khi không).

    Giá trị ngoài enum dây: `kind` → `other`, nhãn tầng → vắng, nguồn → `default`, mã câu hỏi → `STOREY_UNSURE`.
    Cốt/chiều cao ngoài khoảng của `floors` → vắng cùng độ tin và nguồn của nó.
    """
    elevation = _in_range(floor.elevation_mm, ELEVATION_MIN, ELEVATION_MAX, "elevation_mm")
    height = _in_range(floor.height_mm, HEIGHT_MIN, HEIGHT_MAX, "height_mm")
    elevation_source = _wire(floor.elevation_source, _SOURCES, "default", "elevation_source")
    height_source = _wire(floor.height_source, _SOURCES, "default", "height_source")
    return FloorProposalOut.model_validate(
        {
            "floor_id": floor.floor_id,
            "kind": _wire(floor.kind, _KINDS, "other", "kind"),
            "kind_confidence": _unit(floor.kind_confidence),
            "storey_label": _wire(floor.storey_label, _LABELS, None, "storey_label"),
            "storey_no": floor.storey_no,
            "storey_confidence": _unit(floor.storey_confidence),
            "elevation_mm": floor.elevation_mm if elevation else None,
            "elevation_confidence": _unit(floor.elevation_confidence) if elevation else None,
            "elevation_source": elevation_source if elevation else None,
            "height_mm": floor.height_mm if height else None,
            "height_confidence": _unit(floor.height_confidence) if height else None,
            "height_source": height_source if height else None,
            "questions": [_question(code, floor) for code in floor.questions],
            "is_section": True if section else None,
        }
    )


def house_proposal_out(
    proposal: HouseProposal, run_ids: Iterable[str], section_ids: frozenset[str] = frozenset()
) -> HouseProposalOut:
    """Đề xuất của miền ra dây (`enabled=True`), giữ thứ tự tầng; `section_ids` = dòng đã đánh dấu mặt cắt."""
    floors = [_floor_out(floor, floor.floor_id in section_ids) for floor in proposal.floors]
    return HouseProposalOut(enabled=True, floors=floors, run_ids=list(run_ids))


DISABLED: Final = HouseProposalOut(enabled=False, floors=[], run_ids=[])
"""Phản hồi khi `DRAWING_SET_ENABLED` tắt."""
