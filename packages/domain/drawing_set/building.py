"""Đề xuất cho cả ngôi nhà (NHA-05a, thiết kế §2.E): tầng, mâu thuẫn giữa các nguồn, hệ đơn vị, vi phạm luật ghép.

Bọc `solve_house`, không sửa nó: tầng trả nguyên `FloorProposal`. Thêm vào đó các ứng viên theo nguồn cho cốt của
từng hình (cốt hình tự ghi, bậc thang cốt của mặt cắt); hai nguồn lệch quá dung sai thành `Conflict` mà máy không tự
chọn. Lựa chọn của người duyệt ghi theo **nguồn** chứ không theo chỉ số, nên tính lại làm ứng viên đổi thứ tự cũng
không trỏ nhầm. Mô hình là pydantic vì đây là hình dạng sẽ lưu JSONB (`drawing_sets.building`, NHA-06).

Mái (NHA-05b): mỗi tầng được che một `RoofSpec`, từng trường kèm nguồn và độ tin, suy theo thứ tự của thiết kế §2.C
Câu 6 từ chữ mà `roof_reading` đọc được; hai nguồn độ dốc lệch nhau cũng thành `Conflict`. Chỉ đề xuất, không dựng
hình. Chưa nối API hay DB; kết quả không lưu ở đâu.
"""

import itertools
import math
from collections.abc import Mapping, Sequence
from typing import Final, Literal, Self, cast

from pydantic import Field, model_validator

from packages.domain.drawing_set.feet_inch import parse_feet_inch
from packages.domain.drawing_set.levels import levels_from_texts, main_floor_level
from packages.domain.drawing_set.proposal import FloorProposal, HouseView, SectionView, solve_house
from packages.domain.drawing_set.roof_reading import RoofReading, read_roof
from packages.domain.drawing_set.rules import (
    FLAT_MAX_DEG,
    HEIGHT_OK_MM,
    TUM_LABELS,
    RuleHit,
    m03_pitch,
    m05_form,
    m06_rooftop,
    m07_flat_drain,
    m09_min_pitch,
    m18_roof_plan,
    m19_ambiguous,
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

type Source = Literal["reviewer", "plan+section", "roofPlan", "section", "elevation", "plan", "convention", "default"]

# Thứ tự ưu tiên nguồn (thiết kế §2.E), nguồn đứng trước thắng. `roofPlan` chỉ có ở các trường của mái.
LEVEL_PRIORITY: Final[tuple[Source, ...]] = (
    "reviewer",
    "plan+section",
    "roofPlan",
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
# Độ tin của các trường mái (thiết kế §2.C Câu 6: quy ước ≤ 0,4, mặc định ≤ 0,3; §2.D: dạng `a:b` ≤ 0,5).
_READ: Final = 0.8  # trị số ghi rõ trên một nguồn
_AMBIGUOUS: Final = 0.5
_GUESS: Final = 0.6  # suy từ nhãn hình (sân thượng → mái bằng) và hướng mái đoán (§2.E)
_MATERIAL: Final = 0.7
_CONVENTION: Final = 0.4
_DEFAULT: Final = 0.3
_SLOPED_PITCH_DEG: Final = 30.0  # độ dốc mặc định khi chỉ biết là mái dốc
_PITCH_BY_MATERIAL: Final[Mapping[str, float]] = {"tile": 30.0, "metal": 8.5, "shingle": 26.6}  # ngói, tôn 15 %, 6:12
_OVERHANG_MM: Final = (300, 450)  # độ đua quy ước: bộ hệ mét, bộ feet-inch; mái bằng thì 0


class Measured(DomainModel):
    """Một giá trị kèm độ tin, nguồn và các hình nó được đọc ra. Số mm là số nguyên; số thực chỉ dùng cho độ dốc."""

    value: int | float | str
    confidence: float = Field(ge=0, le=1)
    source: Source
    view_ids: tuple[str, ...] = ()


class Conflict(DomainModel):
    """Hai nguồn của cùng một trường lệch quá dung sai. `chosen_source` rỗng = người duyệt chưa chọn (chặn xác nhận)."""

    key: str
    field: Literal["elevationMm", "pitchDeg"]
    candidates: tuple[Measured, ...]
    chosen_source: Source | None = None

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        """Ít nhất hai ứng viên, giá trị là số (cốt: số mm nguyên), mỗi nguồn một ứng viên, nguồn đã chọn có trong đó.

        JSONB cũ hay bị sửa tay sai các điều này thì hỏng ngay lúc đọc, thay vì chọn bừa hoặc vỡ ở `effective`.
        """
        sources = [candidate.source for candidate in self.candidates]
        if len(sources) < 2 or len(set(sources)) != len(sources):  # mâu thuẫn cần hai phía khác nguồn
            raise ValueError(f"mâu thuẫn {self.key} cần ít nhất hai ứng viên, mỗi nguồn một ứng viên")
        number, unit = (int, "số mm") if self.field == "elevationMm" else ((int, float), "số độ")
        if not all(isinstance(candidate.value, number) for candidate in self.candidates):
            raise ValueError(f"ứng viên của {self.key} phải là {unit}")
        if self.chosen_source is not None and self.chosen_source not in sources:
            raise ValueError(f"nguồn đã chọn {self.chosen_source} không có trong ứng viên của {self.key}")
        return self


class RoofSpec(DomainModel):
    """Mái đề xuất cho một tầng (thiết kế §2.E). `level_ref` là mã hình mặt bằng đại diện của tầng được che.

    `form`: flat | shed | gable | hip. `pitch_deg`: độ; mái bằng vẫn ghi trị số thoát nước đọc được. `quarter_turns`:
    số góc phần tư xoay so với trục chính của đường bao, luôn là đoán. `eave_elevation_mm` rỗng khi tầng chưa có cốt.
    `ridge_elevation_mm` và `parapet_mm` chưa nơi nào đọc: người duyệt nhập (NHA-06).
    """

    level_ref: str
    form: Measured
    pitch_deg: Measured
    quarter_turns: Measured
    eave_elevation_mm: Measured | None
    overhang_mm: Measured
    ridge_elevation_mm: Measured | None = None
    parapet_mm: Measured | None = None
    material: Measured | None = None


class BuildingProposal(DomainModel):
    """Đề xuất cho cả nhà: hệ đơn vị, tầng (nguyên kiểu của `solve_house`), mái, mâu thuẫn và vi phạm luật ghép."""

    unit_system: Measured
    floors: tuple[FloorProposal, ...]
    roofs: tuple[RoofSpec, ...]
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


def _assumed(value: float | str, source: Literal["convention", "default"]) -> Measured:
    """Giá trị không đọc từ bản vẽ: quy ước hoặc mặc định, độ tin chặn theo thiết kế Câu 6."""
    return Measured(value=value, confidence=_CONVENTION if source == "convention" else _DEFAULT, source=source)


def _pitch(
    ref: str, reading: RoofReading, pick: Source | None
) -> tuple[Measured | None, Conflict | None, list[RuleHit]]:
    """Độ dốc đọc được cho mái của `ref` (độ, một chữ số lẻ), `None` khi chữ không ghi.

    Hai nguồn lệch quá 3° thành mâu thuẫn `pitchDeg:<ref>`: chưa chọn thì lấy nguồn ưu tiên hơn với độ tin bị chặn,
    kèm M-03, và M-05 nếu một nguồn nói mái bằng còn nguồn kia nói mái dốc. `pick` không còn trong ứng viên thì bỏ.
    """
    candidates = tuple(
        Measured(
            value=round(math.degrees(math.atan(read.slope.rise_over_run)), 1),
            confidence=_AMBIGUOUS if read.slope.ambiguous else _READ,
            source=read.source,
            view_ids=read.view_ids,
        )
        for read in reading.pitches
    )
    if not candidates:
        return None, None, []
    degs = [float(candidate.value) for candidate in candidates]
    clash = m03_pitch(ref, degs)
    if clash is None:  # các nguồn khớp nhau: lấy trị số rõ nhất, bằng nhau thì hình mái đứng trước
        return max(candidates, key=lambda candidate: candidate.confidence), None, []
    chosen = pick if any(candidate.source == pick for candidate in candidates) else None
    conflict = Conflict(key=f"pitchDeg:{ref}", field="pitchDeg", candidates=candidates, chosen_source=chosen)
    if chosen is not None:
        return effective(conflict), conflict, []
    split = m05_form(ref, ["flat" if deg < FLAT_MAX_DEG else "sloped" for deg in degs])
    return effective(conflict), conflict, [clash] if split is None else [clash, split]


def _form_and_pitch(
    read: Measured | None, reading: RoofReading, terraces: Sequence[str], *, tum: bool, imperial: bool
) -> tuple[Measured, Measured]:
    """Dạng và độ dốc của mái theo thứ tự suy luận của thiết kế §2.C Câu 6.

    Độ dốc đọc được: dưới 5 % là mái bằng (nguồn của chính trị số đó), từ 5 % là mái dốc mà dạng chưa đọc được nên
    lấy hai mái theo quy ước. Không có trị số: hình sân thượng → bằng; tum → bằng mặc định; có chữ vật liệu → dốc
    theo quy ước của vật liệu; bộ feet-inch → hai mái, 30°; còn lại mái bằng mặc định.
    """
    if read is not None:
        if float(read.value) < FLAT_MAX_DEG:
            return read.model_copy(update={"value": "flat"}), read
        return _assumed("gable", "convention"), read
    if terraces:
        seen = Measured(value="flat", confidence=_GUESS, source="plan", view_ids=tuple(terraces[:1]))
        return seen, _assumed(0.0, "default")
    if tum or (reading.material is None and not imperial):
        return _assumed("flat", "default"), _assumed(0.0, "default")
    if reading.material is not None:
        return _assumed("gable", "convention"), _assumed(_PITCH_BY_MATERIAL[reading.material.material], "convention")
    return _assumed("gable", "convention"), _assumed(_SLOPED_PITCH_DEG, "default")


def _roof_storeys(
    floors: Sequence[FloorProposal], storeys: Mapping[int, tuple[str, int, int]]
) -> tuple[list[FloorProposal], list[str]]:
    """Hình đại diện của các tầng có thể mang mái, từ trên xuống, và mã các hình sân thượng nằm trên cùng.

    Đại diện là hình mà `storeys` đã chọn (có cốt và chiều cao), không có thì hình đầu tiên mang số tầng đó. Hình sân
    thượng là mặt trên của mái bằng chứ không phải tầng được che, nên không nhận mái; chỉ sân thượng nằm trên mọi
    tầng thường mới là bằng chứng "mái bằng" (sân thượng giữa nhà không nói gì về mái trên cùng).
    """
    by_id = {floor.floor_id: floor for floor in floors}
    reps: dict[int, FloorProposal] = {}
    for floor in floors:
        if floor.storey_no is not None:
            reps.setdefault(floor.storey_no, floor)
    reps.update({number: by_id[ref] for number, (ref, _, _) in storeys.items()})
    ordered = [reps[number] for number in sorted(reps, reverse=True)]
    terraces = itertools.takewhile(lambda floor: floor.storey_label == "roof_terrace", ordered)
    covered = [floor for floor in ordered if floor.storey_label != "roof_terrace"]
    return covered, [floor.floor_id for floor in terraces]


def _roofs(
    floors: Sequence[FloorProposal],
    storeys: Mapping[int, tuple[str, int, int]],
    reading: RoofReading,
    *,
    imperial: bool,
    picked: Mapping[str, Source],
) -> tuple[list[RoofSpec], list[Conflict], list[RuleHit]]:
    """Mái đề xuất (thiết kế §2.C Câu 3 và Câu 6), mâu thuẫn độ dốc và vi phạm nhóm M kiểm lúc đề xuất.

    Tầng cao nhất có mái. Tầng cao nhất là tum/áp mái thì nó có mái riêng và tầng ngay dưới nhận một tấm phủ (M-06).
    Khi đó chữ mà ghi mái dốc: áp mái nhận mái dốc ấy và không có tấm phủ; tum thì không đề xuất mái nào (mái dốc sẽ
    xuyên qua tum) và M-06 ghi rõ `sloped`. Diềm nằm ở đỉnh tường của tầng, thừa kế nguồn và độ tin của chiều cao
    tầng. Dạng hay độ dốc là giả định thì hỏi: M-18 khi bộ có hình mặt bằng mái, ngược lại T-07.
    shortcut: mọi mái của đợt đầu dùng chung dạng, độ dốc và vật liệu; mái nhiều cấp là NHA-11.
    """
    covered, terraces = _roof_storeys(floors, storeys)
    if not covered:
        orphan = m18_roof_plan(reading.roof_view_ids, None)
        return [], [], [] if orphan is None else [orphan]
    ref, label = covered[0].floor_id, covered[0].storey_label
    rooftop = label in TUM_LABELS
    read, conflict, hits = _pitch(ref, reading, picked.get(f"pitchDeg:{ref}"))
    form, pitch = _form_and_pitch(read, reading, terraces, tum=rooftop, imperial=imperial)
    flat = form.value == "flat"
    pierced = label == "tum" and not flat
    targets = [] if pierced else covered[:2] if rooftop and flat else covered[:1]
    tum = m06_rooftop([floor.floor_id for floor in covered[:2]], label, sloped=pierced)
    found = reading.material
    material = (
        None
        if found is None
        else Measured(value=found.material, confidence=_MATERIAL, source=found.source, view_ids=found.view_ids)
    )
    overhang = Measured(value=0 if flat else _OVERHANG_MM[imperial], confidence=_CONVENTION, source="convention")
    turns = Measured(value=0, confidence=_GUESS, source="convention")
    levels = {view: level_mm + height_mm for view, level_mm, height_mm in storeys.values()}

    roofs: list[RoofSpec] = []
    asks: list[RuleHit | None] = [tum, *hits]
    for floor in targets:
        view = floor.floor_id
        eave = (
            None
            if view not in levels
            else Measured(
                value=levels[view],
                confidence=floor.height_confidence or 0.0,
                source=cast("Source", floor.height_source or "default"),
                view_ids=(view,),
            )
        )
        roofs.append(
            RoofSpec(
                level_ref=view,
                form=form,
                pitch_deg=pitch,
                quarter_turns=turns,
                eave_elevation_mm=eave,
                overhang_mm=overhang,
                material=material,
            )
        )
        asks.append(m07_flat_drain(view, str(form.value), drains=reading.drains))
        if not flat and found is not None:
            asks.append(m09_min_pitch(view, float(pitch.value), found.material))
    unclear = next((r.slope.text for r in reading.pitches if r.slope.ambiguous and r.source == pitch.source), None)
    unsure = m18_roof_plan(reading.roof_view_ids, form.source if roofs else None)
    asks += [m19_ambiguous(ref, unclear), unsure]
    if unsure is None and roofs:
        asks.append(t07_assumed(ref, "roofForm", form.source))
        asks.append(None if flat else t07_assumed(ref, "roofPitchDeg", pitch.source))
    return roofs, [] if conflict is None else [conflict], [hit for hit in asks if hit is not None]


def propose_building(
    views: Sequence[HouseView],
    sections: Sequence[SectionView] = (),
    *,
    roofs: Sequence[HouseView] = (),
    order_known: bool = False,
    picks: Mapping[str, Source] | None = None,
) -> BuildingProposal:
    """Đề xuất cho cả nhà từ các hình mặt bằng (`views`), mặt cắt và mặt bằng mái (`roofs`) người duyệt đã chốt loại.

    `order_known` như `solve_house`. `picks`: khoá mâu thuẫn (`elevationMm:<hình>`, `pitchDeg:<hình>`) → nguồn người
    duyệt đã chọn. `floors` luôn là giá trị máy đề xuất; giá trị sau lựa chọn lấy bằng `effective(conflict)`, và luật
    T-01 kiểm trên giá trị đó (chọn cốt khác thì chiều cao tầng dưới không còn khớp: phải thấy ngay). Các hình
    trong `roofs` và mọi mặt cắt là nguồn chữ của mái; hình máy tự xếp là mái trong `views` thì không (NHA-06a).
    Vi phạm kiểm ở đây: T-01, T-02, T-03, T-05 (mâu thuẫn chưa chọn), T-06, T-07 và M-03, M-05, M-06, M-07, M-09,
    M-18, M-19. T-04, M-02 và M-17 cần giá trị người duyệt gán hoặc sửa nên do nơi có dữ liệu đó gọi (NHA-06).

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
    reading = read_roof({view.floor_id: view.texts for view in roofs}, sections)
    imperial = unit_system.value == "imperial"
    specs, roof_conflicts, roof_hits = _roofs(floors, storeys, reading, imperial=imperial, picked=picked)
    return BuildingProposal(
        unit_system=unit_system,
        floors=floors,
        roofs=tuple(specs),
        conflicts=(*conflicts, *roof_conflicts),
        violations=(*hits, *roof_hits),
    )
