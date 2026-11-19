"""Cốt sàn từng tầng và chiều cao tầng của cả nhà, chỉ từ mặt bằng (HS-01).

Chuyển từ nhân nghiên cứu `kernel/heights3.py` (`solve_levels`: ứng viên mỗi tầng, điểm đơn và điểm
cặp, quy hoạch động qua các tầng, `floor_record`, `make_heights`, quy ước tầng trệt, luật tầng hầm,
lửng) và phần hậu xử lý không cần mặt cắt của `kernel/solve_set.py` (`heights_of`, `fill_defaults`,
`mezz_floors`, `mezz_checks`).

Cố ý KHÔNG chuyển, vì sản phẩm không có mặt cắt/mặt đứng nên "thang cốt" luôn rỗng: dựng thang
(`heights3.build_ladder`, `support`, `side_values`, `zero_marks`), bắt cốt WC trũng theo thang
(`sunk_rung`), chèn tầng thiếu theo thang (`fill_missing`, `plausible_rungs`, `intermediate_rung`),
chuỗi cốt neo (`solve_set.fill_from_chain`, `levels_chain`) và chuỗi kích thước (`solve_set.apply_dims`).
Hệ quả: không cốt hay chiều cao nào đạt 0,9 (nguồn thứ hai luôn là mặt cắt).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final, Literal

from packages.domain.drawing_set.levels import level_groups

DEFAULT_HEIGHT_M: Final = 3.3  # chiều cao giả định khi không có cốt (solve_set.DEFAULT_H)
ELEVATION_RANGE_MM: Final = (-30000, 300000)  # FloorRow.elevation_mm
HEIGHT_RANGE_MM: Final = (2000, 10000)  # FloorRow.height_mm

_TOL_M: Final = 0.03  # hai cốt cách nhau không quá mức này là một
_AMBIG: Final = 0.5  # tổng điểm phương án tốt nhất hơn phương án khác dưới mức này → nhập nhằng
_CONV_SCORE: Final = 2.0  # điểm của cốt 0.000 giả định cho tầng trệt không ghi cốt
_GROUND_NEAR_M: Final = 0.20
_GROUND_MISS: Final = -1.5  # tầng trệt ở cốt cách mốc trệt quá 0,20 m
_MEZZ_SCORE: Final = 1.0  # điểm cặp của khoảng cao được một tầng lửng chia đôi
_MEZZ_PART_M: Final = 2.2  # mỗi phần của khoảng bị lửng chia phải cao ít nhất mức này
_MEZZ_MARGIN_M: Final = 0.5  # lửng nằm trong khoảng tầng khi cách hai đầu hơn mức này
_BASEMENT_TOP_M: Final = -0.3  # cốt tầng hầm thấp hơn mốc trệt ít nhất mức này
_RAISED_MIN_M: Final = 0.3  # trệt tự ghi cốt chủ đạo ≥ mức này (không có 0.000 riêng) thì giữ cốt đó
# ponytail: trần ứng viên mỗi tầng. Mặt bằng thật có dưới 30 cốt khác nhau; quá số này thì giữ các sàn
# gặp nhiều lần nhất (trệt luôn giữ sàn gần mốc trệt) để quy hoạch động O(tầng × trần²) xong trong thời hạn API.
_MAX_CANDS: Final = 30
_TIGHT: Final = (2.6, 4.2)  # chiều cao mỗi tầng "chắc chắn hợp lý"
_LOOSE: Final = (2.4, 4.6)  # chiều cao mỗi tầng "còn hợp lý"

_CONF_PLAN: Final = 0.8  # cốt từ mặt bằng, khoảng tầng hợp lý
_CONF_WEAK: Final = 0.6  # cốt từ mặt bằng với khoảng tầng vô lý; hoặc trệt theo quy ước
_CONF_AMBIG: Final = 0.7
_CONF_UNKNOWN: Final = 0.5
_CONF_MEZZ: Final = 0.7
_CONF_HEIGHT_MAX: Final = 0.85  # chiều cao không kèm chuỗi cốt neo không vượt mức này
_CONF_MISSING: Final = 0.65  # chiều cao bắc qua một số tầng không có mặt bằng
_CONF_DEFAULT: Final = 0.4

LevelSource = Literal["plan", "convention", "default"]
HeightSource = Literal["plan", "convention", "default"]


@dataclass(frozen=True, slots=True)
class StoreyPlan:
    """Mặt bằng đã chọn cho một tầng: số tầng trong bộ (trệt = 1, hầm 0, -1; lửng `None`) và cốt đọc được (m).

    Mỗi số tầng một mặt bằng (người gọi chọn). Cốt lặp lại bao nhiêu lần thì ghi bấy nhiêu lần: số lần
    là bằng chứng (vd `tuple(m.value_m for m in levels_from_texts(texts))`).
    """

    number: int | None
    marks: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class FloorLevel:
    """Cốt sàn của một tầng: giá trị (m), nguồn, độ chắc, cờ nhập nhằng và các cốt thay thế gần điểm."""

    level_m: float | None
    source: LevelSource | None
    confidence: float
    ambiguous: bool = False
    alternatives: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class StoreyHeight:
    """Chiều cao từ tầng `from_number` lên tầng đánh số kế tiếp `to_number` (`None` với tầng trên cùng).

    Cờ: "skips_mezzanine" (có lửng ở giữa, vẫn tính một cặp), "spans_missing_storey" (thiếu mặt bằng
    của số tầng ở giữa: không tự chia), "assumed" (giá trị mặc định 3,3 m mỗi tầng), "implausible" (mỗi
    tầng ngoài 2,4-4,6 m và không lửng nào giải thích: vẫn đề xuất nhưng phải hỏi, soát A-F3/F4).
    """

    from_number: int
    to_number: int | None
    height_m: float
    source: HeightSource
    confidence: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DbValue:
    """Giá trị đổi sang mm cho cơ sở dữ liệu; `acceptable` sai khi không có giá trị hoặc nằm ngoài khoảng."""

    value_mm: int | None
    acceptable: bool


@dataclass(frozen=True, slots=True)
class _Cand:
    """Ứng viên cốt của một tầng: giá trị (`None` = không biết), số cốt ủng hộ, có phải quy ước trệt."""

    value: float | None
    count: int = 0
    conv: bool = False


@dataclass(frozen=True, slots=True)
class _Storey:
    """Ngữ cảnh một tầng đánh số trong quy hoạch động."""

    number: int
    total: int
    ground: bool
    basement: bool


@dataclass(frozen=True, slots=True)
class _Problem:
    """Toàn bộ dữ liệu của một lần giải: tầng theo số tăng dần, ứng viên, mốc trệt, cốt các lửng."""

    storeys: tuple[_Storey, ...]
    cands: tuple[tuple[_Cand, ...], ...]
    g0: float
    mezz: tuple[float, ...]


def _plan_values(marks: Sequence[float], ground: bool) -> list[tuple[float, int]]:
    """Cốt riêng của một mặt bằng `(giá trị, số lần)` sau luật "sàn hoàn thiện chính" của heights3.

    Khác `levels.main_floor_level`: giữ mọi nhóm kèm số lần; đại diện là 0.000 trên mặt bằng trệt.
    """
    return level_groups(marks, prefer_zero=ground)


def _ground_anchor(marks: Sequence[float]) -> float:
    """Mốc của tầng trệt: cốt chủ đạo của chính nó khi ≥ 0,3 m, gặp ít nhất hai lần và không có 0.000 riêng
    (trệt +1.300 trên sân 0.000); ngược lại 0.0 (heights3.ground_level, v6)."""
    vals = _plan_values(marks, True)
    if not vals or any(abs(v) <= _TOL_M for v, _ in vals):
        return 0.0
    value, count = max(vals, key=lambda c: (c[1], c[0]))
    return value if value >= _RAISED_MIN_M and count >= 2 else 0.0


def _candidates(storey: _Storey, marks: Sequence[float], g0: float) -> tuple[_Cand, ...]:
    """Ứng viên của một tầng: các cốt riêng; trệt thêm 0.000 theo quy ước khi chưa có; hầm chỉ nhận cốt
    dưới mốc trệt ít nhất 0,3 m hoặc "không biết" (cốt hầm đọc nhầm không đẩy trệt lên, DS-248)."""
    vals = _plan_values(marks, storey.ground)
    if len(vals) > _MAX_CANDS:
        far = [not (storey.ground and abs(v - g0) <= _GROUND_NEAR_M) for v, _ in vals]
        ranked = sorted(range(len(vals)), key=lambda j: (far[j], -vals[j][1]))
        vals = [vals[j] for j in sorted(ranked[:_MAX_CANDS])]  # giữ thứ tự tăng dần (hoà điểm chọn như cũ)
    out = [_Cand(v, n) for v, n in vals]
    if storey.ground and g0 == 0.0 and not any(c.value is not None and abs(c.value) <= _TOL_M for c in out):
        out.append(_Cand(0.0, conv=True))
    if storey.basement:
        out = [c for c in out if c.value is not None and c.value <= g0 + _BASEMENT_TOP_M] + [_Cand(None)]
    return tuple(out) or (_Cand(None),)


def _unary(cand: _Cand, storey: _Storey, g0: float) -> float:
    """Điểm đơn: quy ước trệt, tỉ lệ cốt của mặt bằng ủng hộ, độ gần mốc trệt của tầng trệt."""
    if cand.value is None:
        return 0.0
    score = (_CONV_SCORE if cand.conv else 0.0) + cand.count / max(storey.total, 1)
    if storey.ground:
        d = abs(cand.value - g0)
        score += 1.0 if d <= _TOL_M else 0.5 if d <= _GROUND_NEAR_M else _GROUND_MISS
    return score


def _mezz_explains(low: float, high: float, k: int, mezz: Sequence[float]) -> bool:
    """Khoảng quá cao cho `k` tầng nhưng hợp lý khi một lửng có cốt chia nó thành các phần ≥ 2,2 m."""
    gap = high - low
    return (
        gap / k > _LOOSE[1]
        and _LOOSE[0] <= gap / (k + 1) <= _LOOSE[1]
        and any(low + _MEZZ_PART_M <= m <= high - _MEZZ_PART_M for m in mezz)
    )


def _pair(a: _Cand, b: _Cand, k: int, mezz: Sequence[float]) -> float:
    """Điểm cặp của hai tầng liền nhau cách `k` số tầng: khoảng âm bị phạt, khoảng hợp lý được thưởng."""
    if a.value is None or b.value is None:
        return 0.0
    if b.value - a.value <= 0:
        return -2.0
    if _mezz_explains(a.value, b.value, k, mezz):
        return _MEZZ_SCORE
    per = (b.value - a.value) / k
    return 1.5 if _TIGHT[0] <= per <= _TIGHT[1] else 0.5 if _LOOSE[0] <= per <= _LOOSE[1] else -1.0


def _step(p: _Problem, i: int) -> int:
    """Số tầng giữa tầng thứ `i - 1` và `i` (theo số tăng dần)."""
    return p.storeys[i].number - p.storeys[i - 1].number


def _parts(p: _Problem, path: Sequence[int]) -> tuple[list[float], list[float]]:
    """Điểm đơn từng tầng và điểm cặp từng cặp kề của một lựa chọn (chỉ số ứng viên mỗi tầng)."""
    unary = [_unary(p.cands[i][j], p.storeys[i], p.g0) for i, j in enumerate(path)]
    pairs = [_pair(p.cands[i - 1][path[i - 1]], p.cands[i][path[i]], _step(p, i), p.mezz) for i in range(1, len(path))]
    return unary, pairs


def _sum_parts(unary: list[float], pairs: list[float]) -> float:
    """Tổng điểm, cộng đúng thứ tự cũ (đơn bằng `sum`, rồi từng cặp) để so `_AMBIG` không lệch số thực."""
    score = sum(unary)
    for value in pairs:
        score += value
    return score


def _dp(p: _Problem) -> list[int]:
    """Lựa chọn tốt nhất bằng quy hoạch động qua các tầng (heights3.dp_choose)."""
    best = [[_unary(c, p.storeys[0], p.g0) for c in p.cands[0]]]
    back: list[list[int]] = []
    for i in range(1, len(p.cands)):
        row, links = [], []
        step, prev_best, prev_cands = _step(p, i), best[-1], p.cands[i - 1]
        for c in p.cands[i]:
            opts = [prev_best[q] + _pair(prev, c, step, p.mezz) for q, prev in enumerate(prev_cands)]
            q = max(range(len(opts)), key=opts.__getitem__)
            row.append(opts[q] + _unary(c, p.storeys[i], p.g0))
            links.append(q)
        best.append(row)
        back.append(links)
    j = max(range(len(best[-1])), key=best[-1].__getitem__)
    path = [j]
    for links in reversed(back):
        j = links[j]
        path.append(j)
    return path[::-1]


def _gap_bad(p: _Problem, path: Sequence[int], i: int) -> bool:
    """Có khoảng tới tầng kề nào vô lý (ngoài 2,4-4,6 m mỗi tầng và không được lửng giải thích).

    Khoảng hầm → trệt chỉ xét trên bản ghi của tầng hầm.
    """
    for n in (i - 1, i + 1):
        if not 0 <= n < len(path) or (n < i and p.storeys[n].basement and not p.storeys[i].basement):
            continue
        lo, hi = sorted((i, n))
        a, b = p.cands[lo][path[lo]].value, p.cands[hi][path[hi]].value
        if a is None or b is None:
            continue
        k = p.storeys[hi].number - p.storeys[lo].number
        if not (_LOOSE[0] <= (b - a) / k <= _LOOSE[1] or _mezz_explains(a, b, k, p.mezz)):
            return True
    return False


def _near_alternatives(
    p: _Problem, path: list[int], i: int, parts: tuple[list[float], list[float]]
) -> tuple[bool, tuple[float, ...]]:
    """Ứng viên khác của tầng `i` làm tổng điểm kém dưới 0,5: cờ nhập nhằng và các cốt đó.

    Đổi ứng viên tầng `i` chỉ đổi điểm đơn của nó và hai điểm cặp kề: tính lại ba số đó, không gọi lại
    toàn bộ hàm điểm (`parts` = `_parts(p, path)`, tính một lần cho mọi tầng).
    """
    unary, pairs = parts
    best = _sum_parts(unary, pairs)
    near = []
    for j, cand in enumerate(p.cands[i]):
        if j == path[i]:
            continue
        alt_unary, alt_pairs = unary.copy(), pairs.copy()
        alt_unary[i] = _unary(cand, p.storeys[i], p.g0)
        if i > 0:
            alt_pairs[i - 1] = _pair(p.cands[i - 1][path[i - 1]], cand, _step(p, i), p.mezz)
        if i + 1 < len(path):
            alt_pairs[i] = _pair(cand, p.cands[i + 1][path[i + 1]], _step(p, i + 1), p.mezz)
        if best - _sum_parts(alt_unary, alt_pairs) < _AMBIG:
            near.append(cand)
    return bool(near), tuple(c.value for c in near if c.value is not None)


def _floor_level(p: _Problem, path: list[int], i: int, parts: tuple[list[float], list[float]]) -> FloorLevel:
    """Cốt, nguồn, độ chắc của tầng thứ `i` từ lựa chọn (heights3.floor_record, thang rỗng).

    Cốt có trên mặt bằng: 0,8, khoảng tầng vô lý 0,6; 0.000 theo quy ước trệt: 0,6; nhập nhằng ≤ 0,7.
    """
    cand = p.cands[i][path[i]]
    if cand.value is None:
        return FloorLevel(None, None, _CONF_UNKNOWN)
    if cand.count:
        source: LevelSource = "plan"
        confidence = _CONF_WEAK if _gap_bad(p, path, i) else _CONF_PLAN
    else:
        source, confidence = "convention", _CONF_WEAK
    ambiguous, alternatives = _near_alternatives(p, path, i, parts)
    if ambiguous:
        confidence = min(confidence, _CONF_AMBIG)
    return FloorLevel(cand.value, source, confidence, ambiguous, alternatives)


def _mezzanine_level(marks: Sequence[float]) -> FloorLevel:
    """Cốt của lửng: cốt chủ đạo (đông nhất, hoà thì cao hơn) trên mặt bằng của nó, 0,7 (solve_set.mezz_floors)."""
    vals = _plan_values(marks, False)
    if not vals:
        return FloorLevel(None, None, _CONF_UNKNOWN)
    return FloorLevel(max(vals, key=lambda c: (c[1], c[0]))[0], "plan", _CONF_MEZZ)


def _problem(numbered: Sequence[tuple[int, StoreyPlan]], mezz: tuple[float, ...]) -> _Problem:
    """Dựng bài toán: mốc trệt từ mặt bằng số 1, ứng viên từng tầng theo số tăng dần."""
    g0 = next((_ground_anchor(plan.marks) for number, plan in numbered if number == 1), 0.0)
    storeys = tuple(_Storey(n, len(plan.marks), n == 1, n <= 0) for n, plan in numbered)
    cands = tuple(_candidates(s, plan.marks, g0) for s, (_, plan) in zip(storeys, numbered, strict=True))
    return _Problem(storeys, cands, g0, mezz)


def _skips_mezzanine(pair_idx: tuple[int, int], levels: Sequence[FloorLevel], mezz_idx: Sequence[int]) -> bool:
    """Có lửng giữa hai tầng: cốt lửng nằm trong khoảng (cách hai đầu > 0,5 m) như solve_set.mezz_checks;
    lửng hoặc cặp không có cốt thì xét vị trí trong thứ tự bộ bản vẽ (nhân không biết thứ tự này)."""
    low, high = (levels[i].level_m for i in pair_idx)
    for m in mezz_idx:
        level = levels[m].level_m
        if level is not None and low is not None and high is not None:
            if low + _MEZZ_MARGIN_M < level < high - _MEZZ_MARGIN_M:
                return True
        elif min(pair_idx) < m < max(pair_idx):
            return True
    return False


def _height(
    low: tuple[int, FloorLevel], high: tuple[int, FloorLevel], skips: bool, mezz: Sequence[float]
) -> StoreyHeight:
    """Chiều cao giữa hai tầng đánh số liền nhau = hiệu cốt; thiếu cốt → 3,3 m mỗi tầng ở 0,4 "assumed".

    Hiệu cốt chia số tầng ngoài 2,4-4,6 m (như `_gap_bad`) mà không lửng nào giải thích thì thêm cờ
    "implausible": giá trị giữ nguyên (tầng hầm, lửng thấp 2,0-2,4 m vẫn có thể đúng), lớp trên hỏi.
    """
    (n_low, a), (n_high, b) = low, high
    k = n_high - n_low
    flags = (["skips_mezzanine"] if skips else []) + (["spans_missing_storey"] if k >= 2 else [])
    if a.level_m is None or b.level_m is None:
        height = round(DEFAULT_HEIGHT_M * k, 3)
        return StoreyHeight(n_low, n_high, height, "default", _CONF_DEFAULT, (*flags, "assumed"))
    gap = b.level_m - a.level_m
    if not (_LOOSE[0] <= gap / k <= _LOOSE[1] or _mezz_explains(a.level_m, b.level_m, k, mezz)):
        flags.append("implausible")
    source: HeightSource = "convention" if "convention" in (a.source, b.source) else "plan"
    confidence = min(a.confidence, b.confidence, _CONF_HEIGHT_MAX, _CONF_MISSING if k >= 2 else 1.0)
    return StoreyHeight(n_low, n_high, round(gap, 3), source, confidence, tuple(flags))


def _heights(
    numbered: Sequence[tuple[int, int]], levels: Sequence[FloorLevel], mezz_idx: Sequence[int]
) -> tuple[StoreyHeight, ...]:
    """Chiều cao mọi cặp tầng đánh số liền nhau, rồi tầng trên cùng (mặc định 3,3 m ở 0,4)."""
    mezz = tuple(lv for m in mezz_idx if (lv := levels[m].level_m) is not None)
    out = [
        _height((na, levels[ia]), (nb, levels[ib]), _skips_mezzanine((ia, ib), levels, mezz_idx), mezz)
        for (na, ia), (nb, ib) in pairwise(numbered)
    ]
    if numbered:
        top = numbered[-1][0]
        out.append(StoreyHeight(top, None, DEFAULT_HEIGHT_M, "default", _CONF_DEFAULT, ("assumed",)))
    return tuple(out)


def solve_heights(storeys: Sequence[StoreyPlan]) -> tuple[tuple[FloorLevel, ...], tuple[StoreyHeight, ...]]:
    """Cốt sàn mỗi tầng (cùng thứ tự đầu vào) và chiều cao các cặp tầng đánh số liền nhau.

    `storeys` theo thứ tự bộ bản vẽ, mỗi số tầng một mặt bằng. Lửng không vào quy hoạch động nhưng
    cốt của nó làm khoảng cao bị nó chia thành hợp lý; nó không cắt cặp tầng.
    """
    mezz_idx = [i for i, s in enumerate(storeys) if s.number is None]
    levels = {i: _mezzanine_level(storeys[i].marks) for i in mezz_idx}
    numbered = sorted((s.number, i) for i, s in enumerate(storeys) if s.number is not None)
    if numbered:
        mezz = tuple(lv.level_m for lv in levels.values() if lv.level_m is not None)
        problem = _problem([(n, storeys[i]) for n, i in numbered], mezz)
        path = _dp(problem)
        parts = _parts(problem, path)
        levels |= {i: _floor_level(problem, path, pos, parts) for pos, (_, i) in enumerate(numbered)}
    floors = tuple(levels[i] for i in range(len(storeys)))
    return floors, _heights(numbered, floors, mezz_idx)


def _to_mm(value_m: float | None, allowed: tuple[int, int]) -> DbValue:
    """Mét → mm; ngoài khoảng cho phép thì báo không nhận được, không tự kẹp."""
    if value_m is None:
        return DbValue(None, False)
    value_mm = round(value_m * 1000)
    return DbValue(value_mm, allowed[0] <= value_mm <= allowed[1])


def to_db_ranges(
    floors: Sequence[FloorLevel], heights: Sequence[StoreyHeight]
) -> tuple[tuple[DbValue, ...], tuple[DbValue, ...]]:
    """Cốt sàn và chiều cao đổi sang mm theo khoảng của `FloorRow` (cốt -30000…300000, cao 2000…10000).

    Giá trị ngoài khoảng giữ nguyên và mang `acceptable=False` để lớp trên hỏi người dùng thay vì ghi sai.
    """
    return (
        tuple(_to_mm(f.level_m, ELEVATION_RANGE_MM) for f in floors),
        tuple(_to_mm(h.height_m, HEIGHT_RANGE_MM) for h in heights),
    )
