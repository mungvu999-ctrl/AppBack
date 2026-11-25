"""Đề xuất của máy cho từng hình của một bộ hồ sơ: loại, tên, tầng, câu hỏi (K-32a PR(3)).

Ghép các module thuần đã có: `page_kind.classify_page` (K-14) cho loại hình, `titles.pick_title` cho tên,
`proposal.solve_house` (HS-01) cho số tầng của cả bộ, `title_block.read_title_block` (K-65) cho khung tên.
Vào: mỗi trang một `PageReading` (khung máy gợi ý + chữ OCR cả trang, toạ độ trang). Ra: `ViewGuess` theo thứ tự
trang rồi thứ tự khung. Chỉ thư viện chuẩn; không đọc tệp, không ghi gì. Máy chỉ đề xuất — không có đích (tầng
nào nhận hình) nào được chọn ở đây (K-32a §1: "máy không bao giờ tự tạo tầng").

Luật:
 * Chữ thuộc hình chứa tâm hộp chữ (hình nhỏ nhất khi lồng nhau); chữ ngoài mọi hình chỉ dùng cho khung tên.
 * `NOT_PLAN_SURE` = 0,8 (K-14 khuyến nghị: 88 % mặt cắt gắn đúng, 0/93 mặt bằng gắn nhầm ở ngưỡng này): hình
   máy xếp khác mặt bằng với độ tin < 0,8 bị hỏi `NOT_A_PLAN`; từ 0,8 trở lên coi là chắc, không hỏi.
 * Chỉ hình máy xếp mặt bằng vào bài toán số tầng; câu hỏi tầng giữ `STOREY_UNSURE`/`STOREY_CONFLICT`/
   `NOT_A_PLAN` (cốt và chiều cao là việc của N52 sau khi dựng, không hỏi ở màn duyệt).
 * Mặt cắt máy xếp chắc (>= `NOT_PLAN_SURE`) và đọc được từ `_SIDE_MIN_LEVELS` cốt là nguồn cốt thứ hai của cả bộ
   (NHA-04): không cần người đánh dấu. Nó không đổi số tầng, chỉ đổi cốt/chiều cao trong `solve_set`. Mặt đứng
   chưa vào (`_SIDE_KINDS`).
 * Trang chưa đọc chữ (OCR hỏng/hết giờ) hay hình không có chữ: loại "other", độ tin 0, câu hỏi `NO_TEXT`.
 * Loại `cover` của K-14 không có trên dây: ra "other" (lý do giữ trong `machine`).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from packages.domain.drawing_set.page_kind import PageKind, classify_page
from packages.domain.drawing_set.proposal import (
    NO_TEXT,
    NOT_A_PLAN,
    STOREY_CONFLICT,
    STOREY_UNSURE,
    FloorProposal,
    HouseView,
    SectionView,
    solve_house,
)
from packages.domain.drawing_set.sections import side_levels
from packages.domain.drawing_set.storeys import StoreyLabel
from packages.domain.drawing_set.title_block import TitleBlock, read_title_block
from packages.domain.drawing_set.titles import Kind, pick_title

NOT_PLAN_SURE: Final = 0.8
"""Độ tin từ mức này thì tin hình máy xếp khác mặt bằng (mặt cắt/mặt đứng/…) là đúng — ngưỡng K-14 khuyến nghị."""
VIEW_MIN_AREA: Final = 0.01
"""Khung nhỏ hơn 1 % trang bị bỏ: bằng `CROP_MIN_AREA` của #5 (`apps/api/drawings/schemas.py`, gói domain không
nhập `apps`) — khung máy nhỏ hơn thì người duyệt lưu lại qua N59 sẽ bị 422."""
_SIDE_KINDS: Final = frozenset({"section"})
"""Loại hình mà chữ tự vào `solve_house(sections=…)` khi độ tin >= `NOT_PLAN_SURE` (NHA-04); hình chưa chắc không
vào: cốt đọc nhầm từ một mặt bằng sẽ thành bậc thang giả. **Chưa có `elevation`**: đo trên 50 tờ (changes/NHA-04.md)
mặt đứng duy nhất ra thang cốt là số kích thước bị đọc thành cốt, và nó đè một cốt mặt bằng đúng (DS-836, NO-572)."""
_SIDE_MIN_LEVELS: Final = 3
"""Số cốt khác nhau tối thiểu để một mặt cắt máy tự chọn được vào thang: hai cốt thì đường khớp dọc của
`side_levels` luôn đi qua cả hai, không loại được số đọc nhầm (DS-836: thang (4.1, 7.0) đè cốt mặt bằng 3.58 đúng).
Dòng mặt cắt người dùng tự đánh dấu (N52) vẫn nhận từ hai cốt như cũ."""
_STOREY_QUESTIONS: Final = frozenset({STOREY_UNSURE, STOREY_CONFLICT, NOT_A_PLAN})
_WIRE_KIND: Final[dict[PageKind, Kind]] = {
    "plan": "plan",
    "section": "section",
    "elevation": "elevation",
    "roof": "roof",
    "site": "site",
    "detail": "detail",
    "cover": "other",
    "other": "other",
}
"""Loại K-14 → loại trên dây (7 giá trị N52): `cover` (bìa, mục lục) không có trên dây → "other"."""

type Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Text:
    """Một dòng chữ OCR (thoả `titles.OcrText`): nội dung, hộp (x0, y0, x1, y1) px, độ tin của bộ đọc."""

    text: str
    box: Box
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class PageReading:
    """Một trang đã đọc: khổ (rộng, cao) px, khung máy gợi ý (px, có độ chắc), chữ cả trang.

    `read=False`: trang chưa đọc chữ (OCR hỏng hay hết giờ) — khác với "đã đọc mà không có chữ" ở chỗ không có
    khung tên nào để tìm. `boxes` rỗng → một khung cả trang.
    """

    size: tuple[int, int]
    boxes: Sequence[tuple[Box, float]] = ()
    texts: Sequence[Text] = ()
    read: bool = True


@dataclass(frozen=True, slots=True)
class ViewGuess:
    """Đề xuất cho một hình: trang (chỉ số trong danh sách đầu vào), khung theo tỉ lệ trang, các trường dây.

    `machine` là đề xuất gốc kèm dấu hiệu (lưu JSONB `drawing_set_views.machine` cho K-32g đo); khoá
    `questions` của nó là câu hỏi lên dây.
    """

    page: int
    crop: tuple[float, float, float, float]
    kind: Kind
    kind_confidence: float
    title_text: str | None = None
    storey_label: StoreyLabel | None = None
    storey_no: int | None = None
    storey_confidence: float | None = None
    machine: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _View:
    """Một hình trước khi giải tầng: trang, khung px, chữ (toạ độ trong khung), khổ khung, độ chắc khung."""

    page: int
    box: Box
    texts: tuple[Text, ...]
    size: tuple[int, int]
    score: float
    read: bool


def _contains(box: Box, text: Text) -> bool:
    """Tâm hộp chữ nằm trong khung."""
    cx, cy = (text.box[0] + text.box[2]) / 2, (text.box[1] + text.box[3]) / 2
    return box[0] <= cx < box[2] and box[1] <= cy < box[3]


def _shifted(text: Text, box: Box) -> Text:
    """Chữ đổi sang toạ độ trong khung (gốc ở góc trên trái của khung)."""
    x0, y0 = box[0], box[1]
    b = text.box
    return Text(text.text, (b[0] - x0, b[1] - y0, b[2] - x0, b[3] - y0), text.confidence)


def assign_texts(boxes: Sequence[Box], texts: Sequence[Text]) -> list[tuple[Text, ...]]:
    """Chữ của từng khung (toạ độ trong khung): mỗi chữ về khung nhỏ nhất chứa tâm nó; chữ ngoài mọi khung bị bỏ."""
    out: list[list[Text]] = [[] for _ in boxes]
    by_area = sorted(range(len(boxes)), key=lambda i: (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
    for text in texts:
        owner = next((i for i in by_area if _contains(boxes[i], text)), None)
        if owner is not None:
            out[owner].append(_shifted(text, boxes[owner]))
    return [tuple(items) for items in out]


def is_ladder_section(kind: str, confidence: float, texts: Sequence[Text], size: tuple[int, int]) -> bool:
    """Hình này có là nguồn cốt thứ hai của cả bộ không: mặt cắt chắc loại và đọc được đủ số cốt (NHA-04)."""
    if kind not in _SIDE_KINDS or confidence < NOT_PLAN_SURE:
        return False
    return len(side_levels(texts, size)) >= _SIDE_MIN_LEVELS


def _area_ratio(box: Box, size: tuple[int, int]) -> float:
    """Diện tích khung (phần nằm trong trang) theo diện tích trang."""
    width = min(box[2], size[0]) - max(box[0], 0.0)
    height = min(box[3], size[1]) - max(box[1], 0.0)
    return max(0.0, width) * max(0.0, height) / (size[0] * size[1])


def _views(index: int, page: PageReading) -> list[_View]:
    """Các hình của một trang; khung < `VIEW_MIN_AREA` bị bỏ; không còn khung nào → một khung cả trang (độ chắc 0)."""
    width, height = page.size
    boxes = [(box, score) for box, score in page.boxes if _area_ratio(box, page.size) >= VIEW_MIN_AREA]
    boxes = boxes or [((0.0, 0.0, float(width), float(height)), 0.0)]
    texts = assign_texts([b for b, _ in boxes], page.texts)
    return [
        _View(index, box, items, (max(1, round(box[2] - box[0])), max(1, round(box[3] - box[1]))), score, page.read)
        for (box, score), items in zip(boxes, texts, strict=True)
    ]


def _crop(view: _View, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Khung theo tỉ lệ trang `(x, y, rộng, cao)`, kẹp trong [0, 1] (dạng `CropRect` #5)."""
    width, height = size
    x0, y0 = min(1.0, max(0.0, view.box[0] / width)), min(1.0, max(0.0, view.box[1] / height))
    x1, y1 = min(1.0, max(x0, view.box[2] / width)), min(1.0, max(y0, view.box[3] / height))
    return x0, y0, x1 - x0, y1 - y0


def _block(block: TitleBlock) -> dict[str, dict[str, Any]]:
    """Khung tên ra dạng JSON gọn: chỉ các trường đọc chắc (K-65), mỗi trường `{value, confidence}`."""
    fields = {
        "owner": block.owner,
        "project": block.project,
        "location": block.location,
        "sheetNo": block.sheet_no,
        "date": block.date,
    }
    return {
        name: {"value": item.value, "confidence": round(item.confidence, 3)}
        for name, item in fields.items()
        if item is not None
    }


def _questions(kind: Kind, confidence: float) -> list[str]:
    """Câu hỏi về loại của hình có chữ: khác mặt bằng mà chưa chắc (< `NOT_PLAN_SURE`) → `NOT_A_PLAN`."""
    return [NOT_A_PLAN] if kind != "plan" and confidence < NOT_PLAN_SURE else []


def _guess(view: _View, page_size: tuple[int, int], block: dict[str, Any]) -> ViewGuess:
    """Đề xuất loại + tên của một hình (chưa có tầng)."""
    crop = _crop(view, page_size)
    machine: dict[str, Any] = {"split": {"score": view.score}, "titleBlock": block, "read": view.read}
    if not view.texts:
        machine |= {"kind": "other", "kindConfidence": 0.0, "reason": "NO_TEXT", "cues": [], "questions": [NO_TEXT]}
        return ViewGuess(view.page, crop, "other", 0.0, machine=machine)
    picked = pick_title(view.texts, view.size)
    result = classify_page(view.texts, view.size, picked=picked)
    kind, confidence = _WIRE_KIND[result.kind], result.confidence
    machine |= {
        "kind": result.kind,
        "kindConfidence": result.confidence,
        "reason": result.reason,
        "cues": list(result.cues),
        "questions": _questions(kind, confidence),
    }
    return ViewGuess(view.page, crop, kind, confidence, title_text=picked.text or None, machine=machine)


def _with_storey(guess: ViewGuess, floor: FloorProposal) -> ViewGuess:
    """Gắn tầng `solve_house` đề xuất cho một mặt bằng và câu hỏi tầng của nó (mã lặp không thêm lần hai)."""
    asked = guess.machine["questions"]
    questions = [*asked, *(q for q in floor.questions if q in _STOREY_QUESTIONS and q not in asked)]
    machine = guess.machine | {"questions": questions, "storeyQuestions": list(floor.questions)}
    proposed = floor.storey_no is not None or floor.storey_label is not None
    return replace(
        guess,
        storey_label=floor.storey_label,
        storey_no=floor.storey_no,
        storey_confidence=floor.storey_confidence if proposed else None,
        machine=machine,
    )


def solve_set(pages: Sequence[PageReading]) -> tuple[list[ViewGuess], dict[int, FloorProposal]]:
    """`guess_views` kèm đề xuất cả nhà của từng mặt bằng (khoá = chỉ số hình trong danh sách đề xuất).

    Cốt và chiều cao `solve_house` tính ra không lên `ViewGuess` (màn duyệt chưa hỏi chúng), nên thước bộ hồ sơ
    (`tools/bench/muc3/predict.py`, NHA-00) không đo được nếu chỉ có `guess_views`. Bộ không có mặt bằng → `{}`.
    """
    views = [view for index, page in enumerate(pages) for view in _views(index, page)]
    blocks = [_block(read_title_block(page.texts)) if page.read else {} for page in pages]
    guesses = [_guess(view, pages[view.page].size, blocks[view.page]) for view in views]
    plans = [(i, view) for i, (view, guess) in enumerate(zip(views, guesses, strict=True)) if guess.kind == "plan"]
    if not plans:
        return guesses, {}
    sides = [
        SectionView(view.texts, view.size)
        for view, guess in zip(views, guesses, strict=True)
        if is_ladder_section(guess.kind, guess.kind_confidence, view.texts, view.size)
    ]
    house = solve_house([HouseView(f"v{i}", view.texts, view.size, i) for i, view in plans], sections=sides)
    floors = {i: floor for (i, _view), floor in zip(plans, house.floors, strict=True)}
    for i, floor in floors.items():
        guesses[i] = _with_storey(guesses[i], floor)
    return guesses, floors


def guess_views(pages: Sequence[PageReading]) -> list[ViewGuess]:
    """Đề xuất cho mọi hình của bộ (theo thứ tự trang rồi khung); tầng giải chung một lần cho cả bộ.

    Không ném lỗi với đầu vào hợp lệ (trang rỗng, không chữ, không khung). Khổ trang phải dương.
    """
    return solve_set(pages)[0]
