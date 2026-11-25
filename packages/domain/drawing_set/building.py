"""Đề xuất cho cả ngôi nhà (NHA-05a, thiết kế §2.E): tầng, mâu thuẫn giữa các nguồn, hệ đơn vị, vi phạm luật ghép.

Bọc `solve_house`, không sửa nó: tầng trả nguyên `FloorProposal`. Thêm vào đó các ứng viên theo nguồn cho cốt của
từng hình (cốt hình tự ghi, bậc thang cốt của mặt cắt); hai nguồn lệch quá dung sai thành `Conflict` mà máy không tự
chọn. Lựa chọn của người duyệt ghi theo **nguồn** chứ không theo chỉ số, nên tính lại làm ứng viên đổi thứ tự cũng
không trỏ nhầm. Mô hình là pydantic vì đây là hình dạng sẽ lưu JSONB (`drawing_sets.building`, NHA-06). Chưa có mái
(NHA-05b), chưa nối API hay DB; kết quả không lưu ở đâu.
"""

from collections.abc import Mapping, Sequence
from typing import Final, Literal, Self, cast

from pydantic import Field, model_validator

from packages.domain.drawing_set.feet_inch import parse_feet_inch
from packages.domain.drawing_set.levels import levels_from_texts, main_floor_level
from packages.domain.drawing_set.proposal import FloorProposal, HouseView, SectionView, solve_house
from packages.domain.drawing_set.rules import (
    HEIGHT_OK_MM,
    RuleHit,
    t01_stack,
    t02_storey_count,
    t03_height,
    t05_sources,
    t06_units,
    t07_assumed,
)
from packages.domain.drawing_set.sections import side_levels
from packages.domain.drawing_set.titles import OcrText
from packages.domain.spatial.model import DomainModel

type Source = Literal["reviewer", "plan+section", "section", "elevation", "plan", "convention", "default"]

# Thứ tự ưu tiên nguồn của cốt và chiều cao tầng (thiết kế §2.E), nguồn đứng trước thắng.
LEVEL_PRIORITY: Final[tuple[Source, ...]] = (
    "reviewer",
    "plan+section",
    "section",
    "elevation",
    "plan",
    "convention",
    "default",
)
CONFLICT_CAP: Final = 0.65  # độ tin tối đa của giá trị còn mâu thuẫn chưa chọn; cùng mức `proposal._CONFLICT_CAP`
_GROUND_MIN_MM: Final = -300  # bậc thấp hơn mức này là tầng hầm, không tính vào số tầng nổi
# Sàn trên mái (cùng tập với `order._ROOFISH`): thường thấp hơn 2,4 m nên thang cốt không đếm, mặt bằng cũng không đếm.
_ROOFTOP: Final = frozenset({"tum", "attic", "roof_terrace"})
_UNIT_SURE: Final = 0.9
_UNIT_MIXED: Final = 0.5
_UNIT_UNKNOWN: Final = 0.3


class Measured(DomainModel):
    """Một giá trị kèm độ tin, nguồn và các hình nó được đọc ra."""

    value: int | str
    confidence: float = Field(ge=0, le=1)
    source: Source
    view_ids: tuple[str, ...] = ()


class Conflict(DomainModel):
    """Hai nguồn của cùng một trường lệch quá dung sai. `chosen_source` rỗng = người duyệt chưa chọn (chặn xác nhận)."""

    key: str
    field: Literal["elevationMm"]
    candidates: tuple[Measured, ...]
    chosen_source: Source | None = None

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        """Ít nhất hai ứng viên, giá trị là số mm, mỗi nguồn một ứng viên, nguồn đã chọn là một trong số đó.

        JSONB cũ hay bị sửa tay sai các điều này thì hỏng ngay lúc đọc, thay vì chọn bừa hoặc vỡ ở `effective`.
        """
        sources = [candidate.source for candidate in self.candidates]
        if len(sources) < 2 or len(set(sources)) != len(sources):  # mâu thuẫn cần hai phía khác nguồn
            raise ValueError(f"mâu thuẫn {self.key} cần ít nhất hai ứng viên, mỗi nguồn một ứng viên")
        if not all(isinstance(candidate.value, int) for candidate in self.candidates):
            raise ValueError(f"ứng viên của {self.key} phải là số mm")
        if self.chosen_source is not None and self.chosen_source not in sources:
            raise ValueError(f"nguồn đã chọn {self.chosen_source} không có trong ứng viên của {self.key}")
        return self


class BuildingProposal(DomainModel):
    """Đề xuất cho cả nhà: hệ đơn vị, tầng (nguyên kiểu của `solve_house`), mâu thuẫn và vi phạm luật ghép."""

    unit_system: Measured
    floors: tuple[FloorProposal, ...]
    conflicts: tuple[Conflict, ...]
    violations: tuple[RuleHit, ...]


def _priority(candidate: Measured) -> int:
    """Khoá sắp ứng viên theo bảng ưu tiên; mỗi nguồn chỉ có một ứng viên nên thứ tự không phụ thuộc đầu vào."""
    return LEVEL_PRIORITY.index(candidate.source)


def level_conflict(ref: str, candidates: Sequence[Measured], pick: Source | None) -> Conflict | None:
    """Mâu thuẫn về cốt của hình `ref`, hoặc `None` khi các nguồn khớp nhau (luật T-05).

    Ứng viên được sắp theo bảng ưu tiên. `pick` là nguồn người duyệt đã chọn; nguồn không còn trong ứng viên thì coi
    như chưa chọn.
    """
    ordered = tuple(sorted(candidates, key=_priority))
    if t05_sources(ref, [int(c.value) for c in ordered]) is None:
        return None
    chosen = pick if any(c.source == pick for c in ordered) else None
    return Conflict(key=f"elevationMm:{ref}", field="elevationMm", candidates=ordered, chosen_source=chosen)


def effective(conflict: Conflict) -> Measured:
    """Giá trị đang hiệu lực của một mâu thuẫn: ứng viên của nguồn đã chọn; chưa chọn thì ứng viên ưu tiên nhất,
    độ tin chặn ở 0,65 để mục đó vẫn bị đánh dấu "cần duyệt"."""
    if conflict.chosen_source is not None:
        return next(c for c in conflict.candidates if c.source == conflict.chosen_source)
    top = min(conflict.candidates, key=_priority)
    return top.model_copy(update={"confidence": min(top.confidence, CONFLICT_CAP)})


def _level_candidates(floor: FloorProposal, elevation_mm: int, texts: Sequence[OcrText]) -> list[Measured]:
    """Các ứng viên cốt của một hình: giá trị `solve_house` đã chọn, cộng cốt chính hình tự ghi khi giá trị đã chọn
    đến từ nguồn khác (có thang cốt của mặt cắt thì `solve_house` lấy bậc thang, kể cả khi hình ghi khác hẳn).

    shortcut: không sinh ứng viên mặt cắt cho hình mà `solve_house` giữ cốt của chính nó; chưa thấy ca nào như vậy khi
    thang cốt có bậc gần. Nếu gặp thì thêm bậc thang gần nhất làm ứng viên ở đây.
    """
    ref = floor.floor_id
    source = cast("Source", floor.elevation_source or "default")
    chosen = Measured(value=elevation_mm, confidence=floor.elevation_confidence or 0.0, source=source, view_ids=(ref,))
    own = main_floor_level(mark.value_m for mark in levels_from_texts(texts))
    if source == "plan" or own is None:
        return [chosen]
    confidence = min(chosen.confidence, CONFLICT_CAP)
    return [chosen, Measured(value=round(own.value_m * 1000), confidence=confidence, source="plan", view_ids=(ref,))]


def _section_storeys(rungs_mm: Sequence[int]) -> int | None:
    """Số tầng nổi đọc trên thang cốt của mặt cắt, `None` khi thang không cho biết.

    Đứng ở bậc trệt (bậc trong khoảng ±300 mm) rồi đi lên: bậc kế cách bậc đang đứng 2,4-4,6 m khép một tầng (bậc
    trên cùng là mái); bậc gần hơn (diềm, parapet, chiếu nghỉ) bị bỏ qua. Thang không có bậc trệt, hoặc có khoảng
    trống quá 4,6 m, là thang không đọc được: trả `None` chứ không đếm hụt.
    shortcut: không biết vai của bậc nên mặt cắt thiếu bậc mái sẽ đếm hụt một tầng; NHA-05b có từ khoá cốt thì thay.
    """
    floors = [rung for rung in rungs_mm if rung >= _GROUND_MIN_MM]
    if not floors or floors[0] > -_GROUND_MIN_MM:
        return None
    count, at = 0, floors[0]
    for rung in floors[1:]:
        if rung - at > HEIGHT_OK_MM[1]:
            return None
        if rung - at >= HEIGHT_OK_MM[0]:
            count, at = count + 1, rung
    return count or None


def _unit_system(pages: Sequence[Sequence[OcrText]]) -> tuple[Measured, RuleHit | None]:
    """Hệ đơn vị của cả bộ từ chữ: đếm chữ feet-inch và cốt mét. Lẫn hai hệ hoặc không có gì thì kèm vi phạm T-06."""
    metric = imperial = 0
    for texts in pages:
        rest = [item for item in texts if parse_feet_inch(item.text) is None]
        imperial += len(texts) - len(rest)
        metric += len(levels_from_texts(rest))
    hit = t06_units(metric, imperial)
    confidence = _UNIT_SURE if hit is None else _UNIT_MIXED if metric or imperial else _UNIT_UNKNOWN
    value = "imperial" if imperial > metric else "metric"
    return Measured(value=value, confidence=confidence, source="plan" if metric or imperial else "default"), hit


def propose_building(
    views: Sequence[HouseView],
    sections: Sequence[SectionView] = (),
    *,
    order_known: bool = False,
    picks: Mapping[str, Source] | None = None,
) -> BuildingProposal:
    """Đề xuất cho cả nhà từ các hình mặt bằng và mặt cắt người duyệt đã chốt loại.

    `order_known` như `solve_house`. `picks`: khoá mâu thuẫn (`elevationMm:<hình>`) → nguồn người duyệt đã chọn.
    `floors` luôn là giá trị máy đề xuất; giá trị sau lựa chọn lấy bằng `effective(conflict)`, và luật T-01 kiểm
    trên giá trị đó (chọn cốt khác thì chiều cao tầng dưới không còn khớp: phải thấy ngay).
    Vi phạm kiểm ở đây: T-01, T-02, T-03, T-05 (mâu thuẫn chưa chọn), T-06, T-07. T-04 cần đích tầng người duyệt
    gán nên do nơi có dữ liệu đó gọi (`rules.t04_one_plan_per_storey`, NHA-06).

    Raises:
        ValueError: hai hình trùng `floor_id` (từ `solve_house`).
    """
    floors = solve_house(views, order_known=order_known, sections=sections).floors
    sides = [side_levels(section.texts, section.size) for section in sections]
    rungs_mm = sorted({round(level * 1000) for side in sides for level in side})
    texts = {view.floor_id: view.texts for view in views}
    picked = picks or {}

    conflicts: list[Conflict] = []
    hits: list[RuleHit] = []
    storeys: dict[int, tuple[str, int, int]] = {}  # số tầng → hình đại diện, cốt, chiều cao
    for floor in floors:
        ref = floor.floor_id
        for field, source in (("elevationMm", floor.elevation_source), ("heightMm", floor.height_source)):
            if (assumed := t07_assumed(ref, field, source)) is not None:
                hits.append(assumed)
        if floor.elevation_mm is None:
            continue
        level_mm = floor.elevation_mm
        candidates = _level_candidates(floor, level_mm, texts[ref])
        conflict = level_conflict(ref, candidates, picked.get(f"elevationMm:{ref}"))
        if conflict is not None:
            conflicts.append(conflict)
            level_mm = int(effective(conflict).value)
            values = [int(candidate.value) for candidate in conflict.candidates]
            if conflict.chosen_source is None and (clash := t05_sources(ref, values)) is not None:
                hits.append(clash)
        if floor.storey_no is not None and floor.height_mm is not None:
            storeys.setdefault(floor.storey_no, (ref, level_mm, floor.height_mm))

    for number in sorted(storeys):
        ref, _, height_mm = storeys[number]
        if (tall := t03_height(ref, height_mm)) is not None:
            hits.append(tall)
        if number + 1 in storeys:
            hits.extend(t01_stack([storeys[number], storeys[number + 1]]))
    plans = len(
        {
            floor.storey_no
            for floor in floors
            if floor.storey_no is not None and floor.storey_no >= 1 and floor.storey_label not in _ROOFTOP
        }
    )
    unit_system, unit_hit = _unit_system([*texts.values(), *(section.texts for section in sections)])
    for hit in (t02_storey_count(plans, _section_storeys(rungs_mm)), unit_hit):
        if hit is not None:
            hits.append(hit)
    return BuildingProposal(unit_system=unit_system, floors=floors, conflicts=tuple(conflicts), violations=tuple(hits))
