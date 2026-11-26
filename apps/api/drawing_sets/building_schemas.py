"""Model trên dây của đề xuất ngôi nhà cho một bộ hồ sơ (NHA-06a; camelCase, trường tuỳ chọn **vắng**, W2).

Phần tầng tái dùng `FloorProposalOut` của N52 (`floorId` ở đây là mã hình của bộ). Mái, mâu thuẫn và vi phạm đi
nguyên hình dạng của miền (`packages.domain.drawing_set.building`): mỗi trường mái là một `Measured` có nguồn, độ tin
và các hình nó được đọc ra. FE viết câu tiếng Việt theo `code` của vi phạm. Chưa có quyết định của người duyệt trên
dây: lát NHA-06b thêm khi N59 biết lưu chúng.
"""

from typing import Final, Literal

from apps.api.core.wire import WireModel
from apps.api.drawing_sets.schemas import FloorProposalOut, house_proposal_out
from packages.domain.drawing_set import HouseProposal
from packages.domain.drawing_set.building import BuildingProposal

type BuildingSource = Literal[
    "reviewer", "plan+section", "roofPlan", "section", "elevation", "plan", "convention", "default"
]
type RuleLevel = Literal["error", "warning", "hint"]
type ConflictField = Literal["elevationMm", "pitchDeg"]


class MeasuredOut(WireModel):
    """Một giá trị kèm độ tin, nguồn và các hình nó được đọc ra. Số mm là số nguyên; số thực chỉ dùng cho độ dốc."""

    value: int | float | str
    confidence: float
    source: BuildingSource
    view_ids: list[str]


class RoofSpecOut(WireModel):
    """Mái đề xuất cho tầng có hình đại diện `levelRef`; cốt diềm vắng khi tầng chưa có cốt."""

    level_ref: str
    form: MeasuredOut
    pitch_deg: MeasuredOut
    quarter_turns: MeasuredOut
    eave_elevation_mm: MeasuredOut | None = None
    overhang_mm: MeasuredOut
    ridge_elevation_mm: MeasuredOut | None = None
    parapet_mm: MeasuredOut | None = None
    material: MeasuredOut | None = None


class ConflictOut(WireModel):
    """Hai nguồn của một trường lệch quá dung sai; `chosenSource` vắng = người duyệt chưa chọn."""

    key: str
    field: ConflictField
    candidates: list[MeasuredOut]
    chosen_source: BuildingSource | None = None


class RuleHitOut(WireModel):
    """Một vi phạm luật ghép: mã (`T-01`…, `M-02`…), mức, các hình liên quan và số liệu để FE viết câu."""

    code: str
    level: RuleLevel
    floor_refs: list[str]
    values: list[int | float | str]


class BuildingProposalOut(WireModel):
    """Đề xuất của máy cho cả nhà: hệ đơn vị, tầng theo thứ tự hình mặt bằng, mái, mâu thuẫn, vi phạm."""

    unit_system: MeasuredOut
    floors: list[FloorProposalOut]
    roofs: list[RoofSpecOut]
    conflicts: list[ConflictOut]
    violations: list[RuleHitOut]


class SetBuildingOut(WireModel):
    """`GET …/drawing-sets/{setId}/building`: công tắc và đề xuất.

    `proposal` vắng khi công tắc tắt (`enabled: false`, mẫu N52) hoặc bộ chưa qua phân tích.
    """

    enabled: bool
    proposal: BuildingProposalOut | None = None


BUILDING_DISABLED: Final = SetBuildingOut(enabled=False)
"""Phản hồi khi `DRAWING_SET_ENABLED` tắt."""


def set_building_out(proposal: BuildingProposal | None) -> SetBuildingOut:
    """Đề xuất của miền ra dây (`enabled=True`); `None` → khoá `proposal` vắng."""
    if proposal is None:
        return SetBuildingOut(enabled=True)
    floors = house_proposal_out(HouseProposal(proposal.floors), ()).floors
    return SetBuildingOut.model_validate(
        {"enabled": True, "proposal": {**proposal.model_dump(exclude={"floors"}), "floors": floors}}
    )
