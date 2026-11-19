"""Model trên dây của đề xuất cho cả nhà (HS-02, camelCase; trường tuỳ chọn **vắng**, không `null`, W2).

Enum dây là `Literal` nên giá trị miền ngoài tập (nhãn tầng, nguồn, mã câu hỏi mới) làm `ValidationError`
ở `house_proposal_out` — hỏng to ở test thay vì lọt sang FE (zod `.strict()` sẽ từ chối cả trang).
"""

from collections.abc import Iterable
from typing import Literal

from apps.api.core.wire import WireModel
from packages.domain.drawing_set import FloorProposal, HouseProposal
from packages.domain.drawing_set.proposal import ORDER_DIFFERS

type FloorKind = Literal["plan", "section", "elevation", "roof", "site", "detail", "other"]
type StoreyLabel = Literal["ground", "basement", "mezzanine", "attic", "roof_terrace", "floor"]
type LevelSource = Literal["plan", "plan+section", "section", "dims", "convention", "default"]
type QuestionCode = Literal[
    "STOREY_UNSURE", "STOREY_CONFLICT", "ORDER_DIFFERS", "LEVEL_MISSING", "HEIGHT_ASSUMED", "NOT_A_PLAN", "NO_TEXT"
]


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
    """`GET …/house-proposal`: các tầng theo thứ tự dự án và id lượt chạy đã cho chữ."""

    floors: list[FloorProposalOut]
    run_ids: list[str]


def _floor_out(floor: FloorProposal, order_differs: frozenset[str]) -> FloorProposalOut:
    """Một tầng ra dây; `ORDER_DIFFERS` thêm cuối nếu tầng nằm trong `order_differs` (miền không bao giờ tự nâng nó).

    Dựng từ `dict` chứ không gọi trực tiếp: kiểu miền là `str` còn dây là `Literal`, và `model_validate`
    kiểm giá trị lúc chạy (R-17) thay vì để mypy ép kiểu mù.
    """
    codes = [*floor.questions, *([ORDER_DIFFERS] if floor.floor_id in order_differs else [])]
    return FloorProposalOut.model_validate(
        {
            "floor_id": floor.floor_id,
            "kind": floor.kind,
            "kind_confidence": floor.kind_confidence,
            "storey_label": floor.storey_label,
            "storey_no": floor.storey_no,
            "storey_confidence": floor.storey_confidence,
            "elevation_mm": floor.elevation_mm,
            "elevation_confidence": floor.elevation_confidence,
            "elevation_source": floor.elevation_source,
            "height_mm": floor.height_mm,
            "height_confidence": floor.height_confidence,
            "height_source": floor.height_source,
            "questions": [{"code": code} for code in codes],
        }
    )


def house_proposal_out(
    proposal: HouseProposal, run_ids: Iterable[str], order_differs: frozenset[str]
) -> HouseProposalOut:
    """Đề xuất của miền ra dây: giữ thứ tự tầng, thêm `ORDER_DIFFERS` cho các tầng trong `order_differs`."""
    return HouseProposalOut(
        floors=[_floor_out(floor, order_differs) for floor in proposal.floors], run_ids=list(run_ids)
    )
