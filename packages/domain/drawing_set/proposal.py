"""Đề xuất cho cả nhà: mỗi hình là tầng nào, cốt sàn, chiều cao tầng, kèm độ chắc và mã câu hỏi (HS-01, T5).

Ghép `titles` (tên hình, loại hình), `levels` (cốt từ chữ), `storeys` (số tầng của cả bộ) và `heights`
(cốt sàn, chiều cao) thành một kết quả cho API. Chỉ thư viện chuẩn, không đọc tệp, không ghi gì.

Luật ghép:
 * Mọi hình là mặt bằng người dùng tải lên nên loại hình KHÔNG được quyết ở đây; `view_kind` chỉ dùng để
   hỏi `NOT_A_PLAN` khi chữ nói mặt cắt/mặt đứng/mái/khu đất/chi tiết với độ tin >= 0,7. Hình như vậy không
   có tầng, cốt hay chiều cao đề xuất và không vào bài toán của cả bộ.
 * Hình không có chữ nào: `NO_TEXT`, loại "other", độ tin 0, không đề xuất gì.
 * Mỗi số tầng một mặt bằng đại diện đưa vào `solve_heights`: bản thường (hoặc không tên) trước bản khác,
   rồi nhiều cốt hơn, rồi vị trí thấp hơn. Hình cùng số tầng còn lại chép cốt và chiều cao của đại diện
   (cùng độ tin), nhưng giữ độ tin tầng của chính nó. Lửng vào bài toán riêng từng hình (để thấy chúng
   nằm giữa hai tầng); hình không rõ tầng không vào.
 * Độ tin cốt = min(độ tin cốt, độ tin tầng của hình đại diện). Độ tin chiều cao = min(độ tin chiều cao,
   độ tin tầng của hai tầng nó nối): chữ mâu thuẫn ở một tầng (0,65) kéo chiều cao 0,8 xuống 0,65, như nhân
   nghiên cứu.
 * Giá trị ngoài khoảng của sản phẩm (`to_db_ranges`) không được đề xuất (`None`) mà thành câu hỏi.

Mã câu hỏi: `STOREY_UNSURE` (độ tin tầng < 0,7, không bất đồng), `STOREY_CONFLICT` (có dấu hiệu mạnh chỉ
tầng khác), `LEVEL_MISSING`, `HEIGHT_ASSUMED` (mặc định, giả định hay bắc qua tầng thiếu mặt bằng),
`NOT_A_PLAN`, `NO_TEXT`. `ORDER_DIFFERS` KHÔNG bao giờ được nâng ở đây: lớp API nâng nó khi so thứ tự
tầng đề xuất với thứ tự tầng của dự án; gói này chỉ nhận `order_known` để tắt dấu hiệu thứ tự.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from packages.domain.drawing_set.heights import (
    DbValue,
    FloorLevel,
    StoreyHeight,
    StoreyPlan,
    solve_heights,
    to_db_ranges,
)
from packages.domain.drawing_set.levels import levels_from_texts
from packages.domain.drawing_set.storeys import PlanView, StoreyChoice, StoreyLabel, solve_storeys
from packages.domain.drawing_set.titles import Kind, KindGuess, OcrText, parse_title, pick_title, view_kind

STOREY_UNSURE: Final = "STOREY_UNSURE"
STOREY_CONFLICT: Final = "STOREY_CONFLICT"
ORDER_DIFFERS: Final = "ORDER_DIFFERS"  # chỉ lớp API nâng (so với thứ tự tầng của dự án); gói này không bao giờ
LEVEL_MISSING: Final = "LEVEL_MISSING"
HEIGHT_ASSUMED: Final = "HEIGHT_ASSUMED"
NOT_A_PLAN: Final = "NOT_A_PLAN"
NO_TEXT: Final = "NO_TEXT"

_SURE: Final = 0.7  # độ tin tầng thấp hơn mức này thì hỏi; độ tin loại hình từ mức này thì tin là không phải mặt bằng
_NOT_PLAN_KINDS: Final = frozenset({"section", "elevation", "roof", "site", "detail"})
_KNOWN_PLAN_CONF: Final = 0.5  # độ tin loại hình khi người dùng tải lên là mặt bằng mà chữ không xác nhận


@dataclass(frozen=True)
class HouseView:
    """Một hình của cả nhà: mã tầng, chữ OCR cả trang, kích thước ảnh (rộng, cao), vị trí trong dự án."""

    floor_id: str
    texts: Sequence[OcrText]
    size: tuple[int, int]
    position: int


@dataclass(frozen=True)
class FloorProposal:
    """Đề xuất cho một hình; trường `None` là không đề xuất. Cốt và chiều cao tính bằng mm theo `FloorRow`.

    Mặc định là "không đề xuất gì" (hình không chữ hoặc không phải mặt bằng): chỉ cần mã hình, loại và câu hỏi.
    """

    floor_id: str
    kind: Kind
    kind_confidence: float
    storey_label: StoreyLabel | None = None
    storey_no: int | None = None
    storey_confidence: float = 0.0
    elevation_mm: int | None = None
    elevation_confidence: float | None = None
    elevation_source: str | None = None
    height_mm: int | None = None
    height_confidence: float | None = None
    height_source: str | None = None
    questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class HouseProposal:
    """Đề xuất cho cả nhà, theo thứ tự hình đầu vào."""

    floors: tuple[FloorProposal, ...]


@dataclass(frozen=True)
class _Read:
    """Một hình đã đọc: đoán loại hình (`None` khi không có chữ) và mặt bằng cho bài toán tầng (`None` khi loại)."""

    view: HouseView
    guess: KindGuess | None
    plan: PlanView | None


@dataclass(frozen=True)
class _Values:
    """Cốt và chiều cao đề xuất của một hình, cùng câu hỏi về chúng."""

    elevation_mm: int | None = None
    elevation_confidence: float | None = None
    elevation_source: str | None = None
    height_mm: int | None = None
    height_confidence: float | None = None
    height_source: str | None = None
    questions: tuple[str, ...] = (LEVEL_MISSING,)


def _read(view: HouseView) -> _Read:
    """Đọc một hình: không chữ -> không mặt bằng; chữ nói không phải mặt bằng chắc chắn -> loại."""
    if not view.texts:
        return _Read(view, None, None)
    guess = view_kind(view.texts, view.size)
    if guess.kind in _NOT_PLAN_KINDS and guess.confidence >= _SURE:
        return _Read(view, guess, None)
    picked = pick_title(view.texts, view.size)
    info = parse_title(picked.text) if picked.text else None
    marks = tuple(m.value_m for m in levels_from_texts(view.texts))
    plan = PlanView(view.floor_id, info, marks, view.position, tuple(t.text for t in view.texts))
    return _Read(view, guess, plan)


def _rank(plan: PlanView) -> tuple[int, int, int, str]:
    """Khoá chọn đại diện của một tầng (nhỏ hơn thì thắng); bản không tên tính như bản thường, như `storeys`."""
    plain = plan.title is None or plan.title.variant == "plain"
    return (0 if plain else 1, -len(plan.levels), plan.position, plan.view_id)


def _representatives(plans: Sequence[PlanView], choices: dict[str, StoreyChoice]) -> list[PlanView]:
    """Mặt bằng đưa vào `solve_heights`, theo thứ tự bộ: một đại diện mỗi số tầng và mọi lửng."""
    best: dict[int, PlanView] = {}
    mezzanines: list[PlanView] = []
    for plan in plans:
        choice = choices[plan.view_id]
        if choice.number is None:
            if choice.label == "mezzanine":
                mezzanines.append(plan)
        elif choice.number not in best or _rank(plan) < _rank(best[choice.number]):
            best[choice.number] = plan
    return sorted([*best.values(), *mezzanines], key=lambda p: (p.position, p.view_id))


def _floor_values(level: FloorLevel, db_mm: int | None, storey_conf: float) -> _Values:
    """Phần cốt của `_Values` cho một tầng: cốt hợp lệ + độ tin gấp độ tin tầng; không thì để trống."""
    if db_mm is None:
        return _Values()
    return _Values(db_mm, min(level.confidence, storey_conf), level.source, questions=())


def _with_height(base: _Values, height: StoreyHeight, db_mm: int | None, conf: dict[int, float]) -> _Values:
    """Thêm chiều cao vào `base`; độ tin gấp thêm độ tin tầng của hai tầng nó nối.

    Mặc định, giả định, bắc qua tầng thiếu mặt bằng hoặc ngoài khoảng sản phẩm thì hỏi `HEIGHT_ASSUMED`.
    """
    ends = [conf[height.from_number]] + ([conf[height.to_number]] if height.to_number is not None else [])
    flagged = bool({"assumed", "spans_missing_storey"} & set(height.flags))
    assumed = db_mm is None or height.source == "default" or flagged
    questions = (*base.questions, HEIGHT_ASSUMED) if assumed else base.questions
    if db_mm is None:
        return replace(base, questions=questions)
    confidence = min(height.confidence, *ends)
    return replace(
        base, height_mm=db_mm, height_confidence=confidence, height_source=height.source, questions=questions
    )


def _ok(db: DbValue) -> int | None:
    """Giá trị mm nếu nằm trong khoảng của sản phẩm, ngược lại `None` (không đề xuất)."""
    return db.value_mm if db.acceptable else None


def _values_by_view(plans: Sequence[PlanView], choices: dict[str, StoreyChoice]) -> dict[str, _Values]:
    """Cốt và chiều cao đề xuất cho từng mặt bằng: giải trên đại diện, hình cùng số tầng chép của đại diện."""
    reps = _representatives(plans, choices)
    floors, heights = solve_heights([StoreyPlan(choices[p.view_id].number, p.levels) for p in reps])
    elevations, height_db = to_db_ranges(floors, heights)
    numbered = {n: p for p in reps if (n := choices[p.view_id].number) is not None}
    conf = {n: choices[p.view_id].confidence for n, p in numbered.items()}
    by_height = {h.from_number: (h, _ok(db)) for h, db in zip(heights, height_db, strict=True)}
    out: dict[str, _Values] = {}
    for plan, level, db in zip(reps, floors, elevations, strict=True):
        number = choices[plan.view_id].number
        values = _floor_values(level, _ok(db), choices[plan.view_id].confidence)
        out[plan.view_id] = _with_height(values, *by_height[number], conf) if number is not None else values
    for plan in plans:  # hình cùng số tầng với đại diện chép cốt và chiều cao của nó
        number = choices[plan.view_id].number
        if plan.view_id not in out and number is not None:
            out[plan.view_id] = out[numbered[number].view_id]
    return out


def _storey_questions(choice: StoreyChoice) -> tuple[str, ...]:
    """Câu hỏi về tầng: bất đồng có ưu tiên; ngược lại độ tin dưới 0,7."""
    if choice.conflict:
        return (STOREY_CONFLICT,)
    return (STOREY_UNSURE,) if choice.confidence < _SURE else ()


def _plan_floor(read: _Read, choice: StoreyChoice, values: _Values) -> FloorProposal:
    """Đề xuất của một mặt bằng: tầng từ `solve_storeys`, cốt/chiều cao từ `values`."""
    guess = read.guess
    kind_conf = guess.confidence if guess is not None and guess.kind == "plan" else _KNOWN_PLAN_CONF
    return FloorProposal(
        read.view.floor_id,
        "plan",
        kind_conf,
        choice.label,
        choice.number,
        choice.confidence,
        values.elevation_mm,
        values.elevation_confidence,
        values.elevation_source,
        values.height_mm,
        values.height_confidence,
        values.height_source,
        (*_storey_questions(choice), *values.questions),
    )


def _bare_floor(read: _Read) -> FloorProposal:
    """Hình không được đề xuất gì: không chữ (`NO_TEXT`, loại "other") hoặc không phải mặt bằng (`NOT_A_PLAN`)."""
    guess = read.guess
    if guess is None:
        return FloorProposal(read.view.floor_id, "other", 0.0, questions=(NO_TEXT,))
    return FloorProposal(read.view.floor_id, guess.kind, guess.confidence, questions=(NOT_A_PLAN,))


def _floor(read: _Read, choices: dict[str, StoreyChoice], values: dict[str, _Values]) -> FloorProposal:
    """Đề xuất của một hình đã đọc; hình không rõ tầng (không vào `solve_heights`) không có cốt."""
    if read.plan is None:
        return _bare_floor(read)
    return _plan_floor(read, choices[read.plan.view_id], values.get(read.plan.view_id, _Values()))


def solve_house(views: Sequence[HouseView], *, order_known: bool = False) -> HouseProposal:
    """Đề xuất tầng, cốt và chiều cao cho cả nhà từ chữ OCR của từng hình (gọi một lần cho cả dự án).

    `order_known=False` (mặc định, vì thứ tự tầng trong dự án do người dùng đặt nên chưa chắc đúng) tắt dấu
    hiệu thứ tự của `solve_storeys`. Kết quả theo thứ tự `views`. `floor_id` phải khác nhau.

    Raises:
        ValueError: hai hình trùng `floor_id`.
    """
    if len({v.floor_id for v in views}) != len(views):
        raise ValueError("floor_id trùng nhau")
    reads = [_read(v) for v in views]
    plans = [r.plan for r in reads if r.plan is not None]
    choices = solve_storeys(plans, order_known)
    values = _values_by_view(plans, choices)
    return HouseProposal(tuple(_floor(r, choices, values) for r in reads))
