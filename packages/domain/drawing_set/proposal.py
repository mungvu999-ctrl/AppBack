"""Đề xuất cho cả nhà: mỗi hình là tầng nào, cốt sàn, chiều cao tầng, kèm độ chắc và mã câu hỏi (HS-01, T5).

Ghép `titles` (tên hình, loại hình), `levels` (cốt từ chữ), `storeys` (số tầng của cả bộ) và `heights`
(cốt sàn, chiều cao) thành một kết quả cho API. Chỉ thư viện chuẩn, không đọc tệp, không ghi gì.

Luật ghép:
 * Mọi hình là mặt bằng người dùng tải lên nên loại hình KHÔNG được quyết ở đây; `view_kind` chỉ dùng để
   hỏi `NOT_A_PLAN` khi chữ nói mặt cắt/mặt đứng/mái/khu đất/chi tiết với độ tin >= 0,7. Hình như vậy không
   có tầng, cốt hay chiều cao đề xuất và không vào bài toán của cả bộ.
 * Hình không có chữ nào: `NO_TEXT`, loại "other", độ tin 0, không đề xuất gì.
 * Mỗi số tầng một mặt bằng đại diện đưa vào `solve_heights`: bản có tên đọc được trước bản không tên,
   rồi bản thường trước bản khác, rồi nhiều cốt hơn, rồi vị trí thấp hơn (soát A-F4). Hình cùng số tầng còn
   lại chép cốt và chiều cao của đại diện (cùng độ tin) chỉ khi nó không có cốt chính riêng hoặc cốt chính
   riêng cách cốt đại diện không quá 0,25 m; ngược lại nó giữ cốt của chính nó (độ tin <= 0,65), không có
   chiều cao, và hỏi `STOREY_CONFLICT`: hai bản vẽ khác nhau cùng nhận một tầng (soát A-F9). Bất đồng này
   tìm TRƯỚC khi chốt giá trị nên cả đại diện cũng bị chặn 0,65 và hỏi, kéo theo cốt của nó và mọi chiều cao
   nối tầng đó (soát rv2-3). Lửng vào bài
   toán riêng từng hình (để thấy chúng nằm giữa hai tầng); hình không rõ tầng không vào nhưng vẫn đề xuất cốt
   chính của chính nó (soát A-F11): không bao giờ chép cốt của bản vẽ khác đè lên cốt hình tự ghi.
 * Độ tin cốt = min(độ tin cốt, độ tin tầng của hình đại diện). Độ tin chiều cao = min(độ tin chiều cao,
   độ tin tầng của hai tầng nó nối): chữ mâu thuẫn ở một tầng (0,65) kéo chiều cao 0,8 xuống 0,65, như nhân
   nghiên cứu.
 * Giá trị ngoài khoảng của sản phẩm (`to_db_ranges`) không được đề xuất (`None`) mà thành câu hỏi.
 * Dải độ tin (soát A-F4, Codex 6): "chắc" >= 0,9; "một nguồn" phải lớn hơn hẳn 0,7. Độ tin đúng 0,70 trở
   xuống là chưa chắc và phải có câu hỏi, bất kể bộ giải trả gì hay nguồn nào (soát rv2-5): cốt hỏi
   `LEVEL_MISSING`, chiều cao hỏi `HEIGHT_ASSUMED`. Cốt nhập nhằng (hai nhóm cốt gần ngang điểm) không quá
   0,65 nên cũng hỏi `LEVEL_MISSING`: từ vựng chưa có mã "hai cốt có thể", đây là mã gần nhất (soát A-F15).

Mã câu hỏi: `STOREY_UNSURE` (độ tin tầng <= 0,7, không bất đồng), `STOREY_CONFLICT` (có dấu hiệu mạnh chỉ
tầng khác, hoặc cốt riêng lệch cốt đại diện cùng tầng), `LEVEL_MISSING` (không cốt, hoặc cốt <= 0,7),
`HEIGHT_ASSUMED` (chiều cao <= 0,7, mặc định, giả định, bắc qua tầng thiếu mặt bằng hay mỗi tầng ngoài 2,4-4,6 m),
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
from packages.domain.drawing_set.levels import levels_from_texts, main_floor_level
from packages.domain.drawing_set.storeys import PlanView, StoreyChoice, StoreyLabel, solve_storeys
from packages.domain.drawing_set.titles import Kind, KindGuess, OcrText, kind_from_picked, parse_title, pick_title

STOREY_UNSURE: Final = "STOREY_UNSURE"
STOREY_CONFLICT: Final = "STOREY_CONFLICT"
ORDER_DIFFERS: Final = "ORDER_DIFFERS"  # chỉ lớp API nâng (so với thứ tự tầng của dự án); gói này không bao giờ
LEVEL_MISSING: Final = "LEVEL_MISSING"
HEIGHT_ASSUMED: Final = "HEIGHT_ASSUMED"
NOT_A_PLAN: Final = "NOT_A_PLAN"
NO_TEXT: Final = "NO_TEXT"

CONFIDENT: Final = 0.9  # dải "chắc": hai nguồn độc lập đồng ý
SINGLE_SOURCE: Final = 0.7  # dải "một nguồn" phải lớn hơn hẳn mức này; từ mức này trở xuống là chưa chắc, phải hỏi
_SURE: Final = 0.7  # độ tin loại hình từ mức này thì tin là không phải mặt bằng
_EPS: Final = 1e-9  # sai số dấu phẩy động khi so với ranh giới dải
_OWN_CAP: Final = 0.65  # cốt riêng của hình không phải đại diện, cốt nhập nhằng, chiều cao vô lý: độ tin tối đa
_COPY_TOL_MM: Final = 250  # cốt riêng cách cốt đại diện không quá mức này thì vẫn chép của đại diện
_ASSUMED_SOURCES: Final = frozenset({"convention", "default"})
_HEIGHT_FLAGS: Final = frozenset({"assumed", "spans_missing_storey", "implausible"})
_NOT_PLAN_KINDS: Final = frozenset({"section", "elevation", "roof", "site", "detail"})
_CONFLICT_CAP: Final = 0.65  # độ tin tầng tối đa khi có bất đồng (như `storeys`)
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
    title_conflict: bool = False  # tên dưới hình và tên khung tên lệch nhau (`pick_title` báo bất đồng)


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
    level_conflict: bool = False  # cốt riêng của hình lệch cốt đại diện cùng số tầng (hai bản vẽ một tầng)


def unsure(confidence: float) -> bool:
    """Độ tin chưa đạt dải "một nguồn" (đúng 0,70 trở xuống): giá trị như vậy phải đi kèm câu hỏi."""
    return confidence <= SINGLE_SOURCE + _EPS


def _read(view: HouseView) -> _Read:
    """Đọc một hình: không chữ -> không mặt bằng; chữ nói không phải mặt bằng chắc chắn -> loại."""
    if not view.texts:
        return _Read(view, None, None)
    picked = pick_title(view.texts, view.size)  # thuần: chọn một lần cho cả loại hình lẫn tên
    guess = kind_from_picked(view.texts, picked)
    if guess.kind in _NOT_PLAN_KINDS and guess.confidence >= _SURE:
        return _Read(view, guess, None)
    info = parse_title(picked.text) if picked.text else None
    marks = tuple(m.value_m for m in levels_from_texts(view.texts))
    plan = PlanView(view.floor_id, info, marks, view.position, tuple(t.text for t in view.texts))
    return _Read(view, guess, plan, picked.sources_disagree)


def _cap_title_conflicts(reads: Sequence[_Read], choices: dict[str, StoreyChoice]) -> dict[str, StoreyChoice]:
    """Hình có hai nguồn tên lệch nhau: độ tin tầng không quá 0,65, bất kể `solve_storeys` nói gì.

    Làm trước khi tính cốt/chiều cao để độ tin của chúng theo luật min.
    """
    out = dict(choices)
    for read in reads:
        if read.title_conflict and read.plan is not None:
            choice = choices[read.plan.view_id]
            out[read.plan.view_id] = replace(choice, confidence=min(choice.confidence, _CONFLICT_CAP))
    return out


def _rank(plan: PlanView) -> tuple[int, int, int, int, str]:
    """Khoá chọn đại diện của một tầng (nhỏ hơn thì thắng): có tên trước không tên (soát A-F4), rồi bản
    thường trước bản khác, nhiều cốt hơn, vị trí thấp hơn."""
    titled = plan.title is not None
    plain = plan.title is not None and plan.title.variant == "plain"
    return (0 if titled else 1, 0 if plain else 1, -len(plan.levels), plan.position, plan.view_id)


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
    """Phần cốt của `_Values` cho một tầng: cốt hợp lệ + độ tin gấp độ tin tầng; không thì để trống.

    Cốt nhập nhằng không quá 0,65 (soát A-F15). Cốt chưa đạt dải "một nguồn", nguồn nào cũng vậy, thì hỏi
    `LEVEL_MISSING` dù vẫn đề xuất (soát A-F4, rv2-5).
    """
    if db_mm is None:
        return _Values()
    confidence = min(level.confidence, storey_conf, _OWN_CAP if level.ambiguous else 1.0)
    return _Values(db_mm, confidence, level.source, questions=(LEVEL_MISSING,) if unsure(confidence) else ())


def _with_height(base: _Values, height: StoreyHeight, db_mm: int | None, conf: dict[int, float]) -> _Values:
    """Thêm chiều cao vào `base`; độ tin gấp thêm độ tin tầng của hai tầng nó nối.

    Mặc định, quy ước (một đầu cốt là 0.000 của trệt do giả định), giả định, bắc qua tầng thiếu mặt bằng,
    mỗi tầng ngoài 2,4-4,6 m ("implausible", vẫn đề xuất ở <= 0,65), ngoài khoảng sản phẩm hoặc độ tin chưa
    đạt dải "một nguồn" (soát rv2-5) thì hỏi `HEIGHT_ASSUMED`.
    """
    ends = [conf[height.from_number]] + ([conf[height.to_number]] if height.to_number is not None else [])
    confidence = min(height.confidence, *ends, _OWN_CAP if "implausible" in height.flags else 1.0)
    flagged = bool(_HEIGHT_FLAGS & set(height.flags))
    assumed = db_mm is None or height.source in _ASSUMED_SOURCES or flagged or unsure(confidence)
    questions = (*base.questions, HEIGHT_ASSUMED) if assumed else base.questions
    if db_mm is None:
        return replace(base, questions=questions)
    return replace(
        base, height_mm=db_mm, height_confidence=confidence, height_source=height.source, questions=questions
    )


def _ok(db: DbValue) -> int | None:
    """Giá trị mm nếu nằm trong khoảng của sản phẩm, ngược lại `None` (không đề xuất)."""
    return db.value_mm if db.acceptable else None


def _own_mm(plan: PlanView) -> int | None:
    """Cốt chính riêng của hình (mm), `None` khi không có."""
    own = main_floor_level(plan.levels)
    return None if own is None else round(own.value_m * 1000)  # `levels` giới hạn [-10, 60] m: trong khoảng


def _disputes(plan: PlanView, rep_mm: int | None) -> bool:
    """Cốt riêng của hình lệch cốt đại diện cùng số tầng quá 0,25 m (hai bản vẽ khác nhau một tầng)."""
    own_mm = _own_mm(plan)
    return own_mm is not None and rep_mm is not None and abs(own_mm - rep_mm) > _COPY_TOL_MM


def _own_values(plan: PlanView, storey_conf: float, rep: _Values | None) -> _Values:
    """Cốt của hình không phải đại diện: chép của đại diện cùng số tầng (`rep`) khi không có cốt chính
    riêng hoặc cốt riêng cách cốt đại diện không quá 0,25 m; ngược lại cốt riêng ở <= 0,65 (gấp độ tin
    tầng, nên luôn hỏi `LEVEL_MISSING`), không chiều cao, đánh dấu bất đồng khi đại diện có cốt khác
    (soát A-F9, A-F11)."""
    own_mm = _own_mm(plan)
    if own_mm is None:
        return rep if rep is not None else _Values()
    rep_mm = rep.elevation_mm if rep is not None else None
    if rep is not None and rep_mm is not None and not _disputes(plan, rep_mm):
        return rep
    conf = min(_OWN_CAP, storey_conf)
    return _Values(own_mm, conf, "plan", questions=(LEVEL_MISSING,), level_conflict=rep_mm is not None)


def _values_by_view(plans: Sequence[PlanView], choices: dict[str, StoreyChoice]) -> dict[str, _Values]:
    """Cốt và chiều cao đề xuất cho từng mặt bằng: giải trên đại diện, các hình còn lại qua `_own_values`.

    Số tầng có hình cùng số mà cốt riêng lệch cốt đại diện là tầng tranh chấp: tìm trước khi chốt giá trị
    để độ tin tầng 0,65 vào luật min của cốt đại diện và của hai chiều cao nối tầng đó (soát rv2-3).
    """
    reps = _representatives(plans, choices)
    floors, heights = solve_heights([StoreyPlan(choices[p.view_id].number, p.levels) for p in reps])
    elevations, height_db = to_db_ranges(floors, heights)
    numbered = {n: p for p in reps if (n := choices[p.view_id].number) is not None}
    rep_mm = {choices[p.view_id].number: _ok(db) for p, db in zip(reps, elevations, strict=True)}
    disputed = {
        n
        for p in plans
        if (n := choices[p.view_id].number) is not None and p is not numbered[n] and _disputes(p, rep_mm[n])
    }
    conf = {n: min(choices[p.view_id].confidence, _CONFLICT_CAP if n in disputed else 1.0) for n, p in numbered.items()}
    by_height = {h.from_number: (h, _ok(db)) for h, db in zip(heights, height_db, strict=True)}
    out: dict[str, _Values] = {}
    for plan, level, db in zip(reps, floors, elevations, strict=True):
        number = choices[plan.view_id].number
        if number is None:
            out[plan.view_id] = _floor_values(level, _ok(db), choices[plan.view_id].confidence)
            continue
        values = _with_height(_floor_values(level, _ok(db), conf[number]), *by_height[number], conf)
        out[plan.view_id] = replace(values, level_conflict=number in disputed)
    for plan in plans:
        choice = choices[plan.view_id]
        if plan.view_id not in out:
            rep = out[numbered[choice.number].view_id] if choice.number is not None else None
            out[plan.view_id] = _own_values(plan, choice.confidence, rep)
    return out


def _storey_questions(choice: StoreyChoice, conflict: bool = False) -> tuple[str, ...]:
    """Câu hỏi về tầng: bất đồng (của `solve_storeys`, hai nguồn tên hay cốt lệch đại diện) có ưu tiên;
    ngược lại độ tin chưa đạt dải "một nguồn" (đúng 0,70 trở xuống)."""
    if choice.conflict or conflict:
        return (STOREY_CONFLICT,)
    return (STOREY_UNSURE,) if unsure(choice.confidence) else ()


def _plan_floor(read: _Read, choice: StoreyChoice, values: _Values) -> FloorProposal:
    """Đề xuất của một mặt bằng: tầng từ `solve_storeys`, cốt/chiều cao từ `values`."""
    guess = read.guess
    kind_conf = guess.confidence if guess is not None and guess.kind == "plan" else _KNOWN_PLAN_CONF
    storey_conf = min(choice.confidence, _CONFLICT_CAP) if values.level_conflict else choice.confidence
    return FloorProposal(
        read.view.floor_id,
        "plan",
        kind_conf,
        choice.label,
        choice.number,
        storey_conf,
        values.elevation_mm,
        values.elevation_confidence,
        values.elevation_source,
        values.height_mm,
        values.height_confidence,
        values.height_source,
        (*_storey_questions(choice, read.title_conflict or values.level_conflict), *values.questions),
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
    choices = _cap_title_conflicts(reads, solve_storeys(plans, order_known))
    values = _values_by_view(plans, choices)
    return HouseProposal(tuple(_floor(r, choices, values) for r in reads))
