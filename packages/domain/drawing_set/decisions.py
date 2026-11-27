"""Quyết định của người duyệt trên đề xuất ngôi nhà (NHA-06b, thiết kế §2.E): mô hình lưu, cách áp, lý do chặn.

`Decisions` là phần `decisions` của cột JSONB `drawing_sets.building`: số người duyệt nhập cho từng tầng và từng mái
(khoá là `levelRef` = mã hình mặt bằng), nguồn đã chọn cho từng mâu thuẫn, các cảnh báo đã xem. `decide` tính đề xuất
kèm quyết định; `blockers` liệt kê những gì còn chặn xác nhận (N60). Dải hợp lệ của từng con số do tầng API kiểm lúc
nhận (N59); ở đây chỉ giữ kiểu dữ liệu để JSONB hỏng bị từ chối ngay lúc đọc.
"""

import hashlib
from collections.abc import Sequence
from typing import Final, Literal

from pydantic import Field

from packages.domain.drawing_set.building import BuildingProposal, Source, propose_building
from packages.domain.drawing_set.proposal import HouseView, SectionView
from packages.domain.drawing_set.rules import RuleHit
from packages.domain.spatial.model import DomainModel

type RoofForm = Literal["flat", "shed", "gable", "hip"]

ROOF_FIELDS: Final = (
    "form",
    "pitch_deg",
    "quarter_turns",
    "eave_elevation_mm",
    "ridge_elevation_mm",
    "overhang_mm",
    "parapet_mm",
    "material",
)
"""Các trường của `RoofSpec` người duyệt sửa được; tên trùng tên trường của `RoofDecision`."""


class LevelDecision(DomainModel):
    """Cốt và chiều cao người duyệt nhập cho tầng của hình `level_ref`; trường rỗng = giữ giá trị đang đề xuất."""

    level_ref: str
    elevation_mm: int | None = None
    height_mm: int | None = None


class RoofDecision(DomainModel):
    """Mái của tầng có hình `level_ref`: các trường người duyệt nhập, hoặc `skip` = "không dựng mái, tôi tự vẽ"."""

    level_ref: str
    skip: bool = False
    form: RoofForm | None = None
    pitch_deg: float | None = None
    quarter_turns: int | None = None
    eave_elevation_mm: int | None = None
    ridge_elevation_mm: int | None = None
    overhang_mm: int | None = None
    parapet_mm: int | None = None
    material: str | None = None


class Decisions(DomainModel):
    """Mọi quyết định của người duyệt cho một bộ. `picks`: khoá mâu thuẫn → nguồn đã chọn. `seen`: khoá (`hit_key`)
    của các cảnh báo người duyệt đã bấm "Đã xem"."""

    levels: tuple[LevelDecision, ...] = ()
    roofs: tuple[RoofDecision, ...] = ()
    picks: dict[str, Source] = Field(default_factory=dict)
    seen: tuple[str, ...] = ()


def decide(
    decisions: Decisions | None,
    views: Sequence[HouseView],
    sections: Sequence[SectionView] = (),
    *,
    roofs: Sequence[HouseView] = (),
) -> BuildingProposal:
    """Đề xuất cho cả nhà tính kèm quyết định của người duyệt (`None` = chưa có quyết định nào: đề xuất của máy).

    Quyết định trỏ tới hình không còn trong `views` bị bỏ qua ở đây; `blockers` báo nó để N60 từ chối.
    """
    chosen = decisions or Decisions()
    edits = {
        roof.level_ref: {name: value for name in ROOF_FIELDS if (value := getattr(roof, name)) is not None}
        for roof in chosen.roofs
    }
    return propose_building(
        views,
        sections,
        roofs=roofs,
        picks=chosen.picks,
        levels={level.level_ref: (level.elevation_mm, level.height_mm) for level in chosen.levels},
        roof_edits=edits,
        skip_roofs={roof.level_ref for roof in chosen.roofs if roof.skip},
    )


KEY_MAX: Final = 200
"""Khoá dài hơn mức này (luật kèm nhiều hình, như M-18 với nhiều hình mái) được thay bằng băm, để luôn gửi lại được."""


def hit_key(hit: RuleHit) -> str:
    """Khoá của một vi phạm: mã, các hình, các số liệu. Số liệu đổi thì khoá đổi, nên cảnh báo phải được xem lại.

    Máy chủ phát khoá này trên dây (`violations[].key`) và client gửi lại nguyên văn trong `seen`; client không tự
    ghép, vì số thực in ra khác nhau giữa các ngôn ngữ (`30.0` so với `30`). Khoá quá `KEY_MAX` ký tự thành
    `<mã>|sha256:<băm>`.
    """
    key = "|".join([hit.code, *hit.floor_refs, *map(str, hit.values)])
    return key if len(key) <= KEY_MAX else f"{hit.code}|sha256:{hashlib.sha256(key.encode()).hexdigest()}"


def blockers(proposal: BuildingProposal, decisions: Decisions | None) -> list[str]:
    """Những gì còn chặn xác nhận, theo thứ tự: quyết định trỏ tới hình không còn (`decision:<hình>`), mâu thuẫn chưa
    chọn nguồn (`conflict:<khoá>`), vi phạm mức lỗi (`error:<khoá>`), cảnh báo chưa xem (`warning:<khoá>`).

    `proposal` phải là kết quả của `decide` với chính `decisions` này. Gợi ý (`hint`) không bao giờ chặn.
    """
    chosen = decisions or Decisions()
    views = {floor.floor_id for floor in proposal.floors}
    seen = set(chosen.seen)
    refs = [level.level_ref for level in chosen.levels] + [roof.level_ref for roof in chosen.roofs]
    found = [f"decision:{ref}" for ref in refs if ref not in views]
    found += [f"conflict:{conflict.key}" for conflict in proposal.conflicts if conflict.chosen_source is None]
    found += [f"error:{hit_key(hit)}" for hit in proposal.violations if hit.level == "error"]
    found += [
        f"warning:{key}" for hit in proposal.violations if hit.level == "warning" and (key := hit_key(hit)) not in seen
    ]
    return found
