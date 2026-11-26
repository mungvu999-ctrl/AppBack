"""Đọc chữ mái của cả bộ (NHA-05b, thiết kế §2.C Câu 6): độ dốc, vật liệu và chữ thoát nước.

Nguồn là chữ OCR của các hình mặt bằng mái và các mặt cắt. Mỗi nguồn cho nhiều nhất một trị số dốc; hai nguồn lệch nhau
là việc của lớp trên (`building`, luật M-03). Chưa đọc cốt đỉnh mái hay cốt parapet: muốn ghép từ khoá với con số phải
biết vị trí trên hình, và chưa có bộ thật nào để đo cách ghép (nợ ghi ở `changes/NHA-05b.md`).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from packages.domain.drawing_set.level_words import level_role
from packages.domain.drawing_set.proposal import SectionView
from packages.domain.drawing_set.slope import Slope, parse_slope
from packages.domain.drawing_set.titles import OcrText, fold

type RoofSource = Literal["roofPlan", "section"]
type Material = Literal["tile", "metal", "shingle"]

# Phải có "MÁI"/"LỢP" đứng trước: bỏ dấu thì "ngồi" trùng "ngói", "tồn" trùng "tôn". Bỏ sót đỡ hại hơn nhận nhầm.
_MATERIALS: Final[tuple[tuple[Material, re.Pattern[str]], ...]] = (
    ("tile", re.compile(r"\b(?:MAI|LOP) NGOI\b|\bROOF(?:ING)? TILES?\b|\bTILE ROOF(?:ING)?\b")),
    ("metal", re.compile(r"\b(?:MAI|LOP) TON\b|\bMETAL ROOF(?:ING)?\b")),
    ("shingle", re.compile(r"\bSHINGLES?\b")),
)
_DRAIN: Final = re.compile(r"\bDRAINS?\b|\bSCUPPERS?\b")
_DRAIN_ROLES: Final = frozenset({"parapet", "gutter"})
_SOURCES: Final[tuple[RoofSource, ...]] = ("roofPlan", "section")  # thứ tự trả về: hình mái trước


@dataclass(frozen=True, slots=True)
class PitchRead:
    """Trị số dốc của một nguồn, kèm các hình đọc ra nó (mặt cắt chưa mang id nên để rỗng)."""

    slope: Slope
    source: RoofSource
    view_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterialRead:
    """Vật liệu mái đọc bằng từ khoá, kèm nguồn và hình."""

    material: Material
    source: RoofSource
    view_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoofReading:
    """Những gì chữ của bộ nói về mái: độ dốc theo nguồn, vật liệu, có chữ thoát nước không, và các hình mái."""

    pitches: tuple[PitchRead, ...]
    material: MaterialRead | None
    drains: bool
    roof_view_ids: tuple[str, ...]


def _rank(slope: Slope) -> tuple[bool, float]:
    """Khoá chọn trị số của một nguồn: rõ thắng nhập nhằng, rồi dốc hơn thắng."""
    return (not slope.ambiguous, slope.rise_over_run)


def read_roof(roof_views: Mapping[str, Sequence[OcrText]], sections: Sequence[SectionView]) -> RoofReading:
    """Đọc chữ mái từ các hình mặt bằng mái (`mã hình → chữ`) và các mặt cắt.

    shortcut: mỗi nguồn lấy trị số dốc nhất (mái dốc hay ghi thêm `i=2%` ở sê nô), và vật liệu là từ khoá gặp đầu
    tiên; nhà có hai mái khác độ dốc hoặc khác vật liệu sẽ bị gộp. Tách theo từng mái khi có mái nhiều cấp (NHA-11).
    """
    pages: list[tuple[RoofSource, tuple[str, ...], Sequence[OcrText]]] = [
        ("roofPlan", (view_id,), texts) for view_id, texts in roof_views.items()
    ]
    pages += [("section", (), section.texts) for section in sections]
    best: dict[RoofSource, PitchRead] = {}
    material: MaterialRead | None = None
    drains = False
    for source, view_ids, texts in pages:
        for item in texts:
            slope = parse_slope(item.text)
            if slope is not None and source == "section" and slope.ambiguous:
                slope = None  # `1:2`, `1:5` trên mặt cắt thường là tỉ lệ của chi tiết
            if slope is not None and (source not in best or _rank(slope) > _rank(best[source].slope)):
                best[source] = PitchRead(slope, source, view_ids)
            folded = fold(item.text)
            name = next((name for name, pattern in _MATERIALS if pattern.search(folded)), None)
            if material is None and name is not None:
                material = MaterialRead(name, source, view_ids)
            drains = drains or level_role(item.text) in _DRAIN_ROLES or _DRAIN.search(folded) is not None
    pitches = tuple(best[source] for source in _SOURCES if source in best)
    return RoofReading(pitches, material, drains, tuple(roof_views))
