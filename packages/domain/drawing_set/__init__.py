"""Bộ bản vẽ thuần: tên hình, loại hình, tầng, cốt, chiều cao và đề xuất cho cả nhà từ chữ OCR (HS-01).

Chỉ thư viện chuẩn; chưa nối vào API (xem `changes/HS-01.md`).
"""

from packages.domain.drawing_set.heights import (
    DbValue,
    FloorLevel,
    StoreyHeight,
    StoreyPlan,
    solve_heights,
    to_db_ranges,
)
from packages.domain.drawing_set.legacy_text import decode_legacy, detect_encoding
from packages.domain.drawing_set.page_kind import PageGeometry, PageKind, PageKindResult, classify_page
from packages.domain.drawing_set.proposal import FloorProposal, HouseProposal, HouseView, SectionView, solve_house
from packages.domain.drawing_set.storeys import (
    Alternative,
    PlanView,
    StoreyChoice,
    StoreyConflict,
    solve_storeys,
)
from packages.domain.drawing_set.titles import (
    KindGuess,
    OcrText,
    PickedTitle,
    TitleInfo,
    fold,
    parse_title,
    pick_title,
    view_kind,
)

__all__ = [
    "Alternative",
    "DbValue",
    "FloorLevel",
    "FloorProposal",
    "HouseProposal",
    "HouseView",
    "KindGuess",
    "OcrText",
    "PageGeometry",
    "PageKind",
    "PageKindResult",
    "PickedTitle",
    "PlanView",
    "SectionView",
    "StoreyChoice",
    "StoreyConflict",
    "StoreyHeight",
    "StoreyPlan",
    "TitleInfo",
    "classify_page",
    "decode_legacy",
    "detect_encoding",
    "fold",
    "parse_title",
    "pick_title",
    "solve_heights",
    "solve_house",
    "solve_storeys",
    "to_db_ranges",
    "view_kind",
]
