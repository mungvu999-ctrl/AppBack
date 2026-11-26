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
from packages.domain.drawing_set.decisions import Decisions, hit_key

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
    """Một vi phạm luật ghép: mã (`T-01`…, `M-02`…), mức, các hình liên quan và số liệu để FE viết câu.

    `key` là khoá để đánh dấu "Đã xem": FE gửi lại nguyên văn trong `building.seen` của N59, không tự ghép.
    """

    key: str
    code: str
    level: RuleLevel
    floor_refs: list[str]
    values: list[int | float | str]


class LevelSpecOut(WireModel):
    """Cốt và chiều cao đang hiệu lực của hình mặt bằng `levelRef` (số người duyệt nhập > nguồn đã chọn > máy)."""

    level_ref: str
    elevation_mm: MeasuredOut | None = None
    height_mm: MeasuredOut | None = None


class BuildingProposalOut(WireModel):
    """Đề xuất cho cả nhà, đã tính kèm quyết định của người duyệt: hệ đơn vị, tầng (giá trị máy), giá trị đang hiệu
    lực của từng tầng, mái, mâu thuẫn, vi phạm."""

    unit_system: MeasuredOut
    floors: list[FloorProposalOut]
    levels: list[LevelSpecOut]
    roofs: list[RoofSpecOut]
    conflicts: list[ConflictOut]
    violations: list[RuleHitOut]


class LevelDecisionOut(WireModel):
    """Số người duyệt đã nhập cho tầng của hình `levelRef` (trường không nhập thì vắng)."""

    level_ref: str
    elevation_mm: int | None = None
    height_mm: int | None = None


class RoofDecisionOut(WireModel):
    """Số người duyệt đã nhập cho mái của tầng `levelRef`; `skip` = "không dựng mái"."""

    level_ref: str
    skip: bool
    form: str | None = None
    pitch_deg: float | None = None
    quarter_turns: int | None = None
    eave_elevation_mm: int | None = None
    ridge_elevation_mm: int | None = None
    overhang_mm: int | None = None
    parapet_mm: int | None = None
    material: str | None = None


class BuildingDecisionsOut(WireModel):
    """Quyết định đã lưu của người duyệt, đúng hình dạng thân `building` của N59 để FE gửi lại nguyên."""

    levels: list[LevelDecisionOut]
    roofs: list[RoofDecisionOut]
    picks: dict[str, BuildingSource]
    seen: list[str]


class SetBuildingOut(WireModel):
    """`GET …/drawing-sets/{setId}/building`: công tắc, đề xuất và quyết định đã lưu.

    `proposal` vắng khi công tắc tắt (`enabled: false`, mẫu N52) hoặc bộ chưa qua phân tích. `decisions` vắng khi
    chưa ai gửi quyết định (N60 khi đó xác nhận như trước NHA-06b).
    """

    enabled: bool
    proposal: BuildingProposalOut | None = None
    decisions: BuildingDecisionsOut | None = None


BUILDING_DISABLED: Final = SetBuildingOut(enabled=False)
"""Phản hồi khi `DRAWING_SET_ENABLED` tắt."""


def set_building_out(proposal: BuildingProposal | None, decisions: Decisions | None = None) -> SetBuildingOut:
    """Đề xuất và quyết định của miền ra dây (`enabled=True`); `None` → khoá tương ứng vắng."""
    if proposal is None:
        return SetBuildingOut(enabled=True)
    floors = house_proposal_out(HouseProposal(proposal.floors), ()).floors
    return SetBuildingOut.model_validate(
        {
            "enabled": True,
            "proposal": {
                **proposal.model_dump(exclude={"floors", "violations"}),
                "floors": floors,
                "violations": [hit.model_dump() | {"key": hit_key(hit)} for hit in proposal.violations],
            },
            "decisions": None if decisions is None else decisions.model_dump(),
        }
    )
