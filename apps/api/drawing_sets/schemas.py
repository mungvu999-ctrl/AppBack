"""Model trên dây của đề xuất cho cả nhà (HS-02, camelCase; trường tuỳ chọn **vắng**, không `null`, W2).

Enum dây là `Literal`. Giá trị miền ngoài tập (nhãn tầng, nguồn, mã câu hỏi mới) không làm 500 cả trang:
`house_proposal_out` đổi nó về giá trị dây dè dặt nhất và ghi log; còn khoảng hở thì
`test_wire_enums_cover_what_the_domain_can_produce` bắt ở CI. Độ tin được kẹp vào [0, 1] (W3).
"""

import logging
from collections.abc import Iterable
from typing import Final, Literal, get_args

from apps.api.core.wire import WireModel
from packages.domain.drawing_set import FloorProposal, HouseProposal
from packages.domain.drawing_set.proposal import ORDER_DIFFERS

type FloorKind = Literal["plan", "section", "elevation", "roof", "site", "detail", "other"]
type StoreyLabel = Literal["ground", "basement", "mezzanine", "tum", "attic", "roof_terrace", "floor"]
type LevelSource = Literal["plan", "plan+section", "section", "dims", "convention", "default"]
type QuestionCode = Literal[
    "STOREY_UNSURE", "STOREY_CONFLICT", "ORDER_DIFFERS", "LEVEL_MISSING", "HEIGHT_ASSUMED", "NOT_A_PLAN", "NO_TEXT"
]

_log: Final = logging.getLogger(__name__)


class QuestionOut(WireModel):
    """Một câu hỏi cho người dùng: chỉ mã, FE viết câu tiếng Việt."""

    code: QuestionCode


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


def _wire(value: str | None, allowed: tuple[str, ...], fallback: str | None, field: str) -> str | None:
    """`value` nếu nằm trong `allowed` (hay `None`), không thì `fallback` + một dòng log."""
    if value is None or value in allowed:
        return value
    _log.warning("house_proposal_unknown_value", extra={"field": field, "value": value})
    return fallback


def _unit(value: float | None) -> float | None:
    """Độ tin kẹp vào [0, 1] (W3)."""
    return None if value is None else min(1.0, max(0.0, value))


def _floor_out(floor: FloorProposal, order_differs: frozenset[str]) -> FloorProposalOut:
    """Một tầng ra dây; `ORDER_DIFFERS` thêm cuối nếu tầng nằm trong `order_differs` (miền không bao giờ tự nâng nó).

    Giá trị ngoài enum dây: `kind` → `other`, nhãn tầng → vắng, nguồn → `default`, mã câu hỏi → `STOREY_UNSURE`.
    """
    codes = [_wire(code, _CODES, "STOREY_UNSURE", "questions") for code in floor.questions]
    codes += [ORDER_DIFFERS] if floor.floor_id in order_differs else []
    return FloorProposalOut.model_validate(
        {
            "floor_id": floor.floor_id,
            "kind": _wire(floor.kind, _KINDS, "other", "kind"),
            "kind_confidence": _unit(floor.kind_confidence),
            "storey_label": _wire(floor.storey_label, _LABELS, None, "storey_label"),
            "storey_no": floor.storey_no,
            "storey_confidence": _unit(floor.storey_confidence),
            "elevation_mm": floor.elevation_mm,
            "elevation_confidence": _unit(floor.elevation_confidence),
            "elevation_source": _wire(floor.elevation_source, _SOURCES, "default", "elevation_source"),
            "height_mm": floor.height_mm,
            "height_confidence": _unit(floor.height_confidence),
            "height_source": _wire(floor.height_source, _SOURCES, "default", "height_source"),
            "questions": [{"code": code} for code in codes],
        }
    )


def house_proposal_out(
    proposal: HouseProposal, run_ids: Iterable[str], order_differs: frozenset[str]
) -> HouseProposalOut:
    """Đề xuất của miền ra dây (`enabled=True`): giữ thứ tự tầng, `ORDER_DIFFERS` cho tầng trong `order_differs`."""
    floors = [_floor_out(floor, order_differs) for floor in proposal.floors]
    return HouseProposalOut(enabled=True, floors=floors, run_ids=list(run_ids))


DISABLED: Final = HouseProposalOut(enabled=False, floors=[], run_ids=[])
"""Phản hồi khi `DRAWING_SET_ENABLED` tắt."""
