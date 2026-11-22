"""Cốt sàn từng tầng và chiều cao tầng của cả nhà, từ mặt bằng và thang cốt của mặt cắt (HS-01, HS-02).

Chuyển từ nhân nghiên cứu `kernel/heights3.py` (`solve_levels`: thang cốt `build_ladder`/`support`, ứng
viên mỗi tầng kèm bắt cốt WC trũng/phòng nâng theo thang `sunk_rung` và ứng viên chỉ có trên thang, điểm
đơn và điểm cặp, quy hoạch động qua các tầng, `floor_record`, quy ước tầng trệt `ground_level`, luật
tầng hầm, lửng `mezz`, `fill_missing`, `plausible_rungs`) và phần hậu xử lý của `kernel/solve_set.py`
(`fill_from_chain`, `heights_of`, `fill_defaults`, `mezz_floors`, `mezz_checks`).

Thang cốt rỗng (`sides` rỗng) và không có chuỗi neo (`chain` None) cho đúng kết quả của bản chỉ mặt bằng.

Cố ý KHÔNG chuyển:
- đọc cốt trên mặt cắt (`side_values`, `zero_marks`, `register_v`) và chuỗi cốt neo
  (`levels_chain.solve_chain`): người gọi dựng `sides`/`chain` (sections.py, chain.py);
- chuỗi kích thước (`solve_set.apply_dims`): cần tỉ lệ bản vẽ;
- vế thứ hai của `mezz_checks` (`heights3.intermediate_rung`): cần cốt của mặt bằng KHÔNG được chọn
  cho tầng nào, mà sản phẩm mỗi số tầng chỉ có một mặt bằng nên danh sách đó luôn rỗng và vế này không
  bao giờ hạ độ chắc. Vế thứ nhất (lửng có cốt nằm trong khoảng → "skips_mezzanine") đã có;
- cờ `ground_raised` và độ chắc gán tầng (nhân lấy min với độ chắc tầng): thuộc lớp đề xuất.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Final, Literal

from packages.domain.drawing_set.levels import level_groups

DEFAULT_HEIGHT_M: Final = 3.3  # chiều cao giả định khi không có cốt (solve_set.DEFAULT_H)
ELEVATION_RANGE_MM: Final = (-30000, 300000)  # FloorRow.elevation_mm
HEIGHT_RANGE_MM: Final = (2000, 10000)  # FloorRow.height_mm

_TOL_M: Final = 0.03  # hai cốt cách nhau không quá mức này là một
_TOL_L: Final = 0.06  # cốt nằm trên một bậc thang khi cách bậc không quá mức này
_AMBIG: Final = 0.5  # tổng điểm phương án tốt nhất hơn phương án khác dưới mức này → nhập nhằng
_CONV_SCORE: Final = 2.0  # điểm của cốt 0.000 giả định cho tầng trệt không ghi cốt
_RUNG_SCORE: Final = 2.0  # điểm của ứng viên nằm trên thang
_RUNG_MULTI: Final = 0.5  # thưởng thêm khi bậc có trên ít nhất hai mặt cắt
_GROUND_NEAR_M: Final = 0.20
_GROUND_MISS: Final = -1.5  # tầng trệt ở cốt cách mốc trệt quá 0,20 m
_MEZZ_SCORE: Final = 1.0  # điểm cặp của khoảng cao được một tầng lửng chia đôi
_MEZZ_PART_M: Final = 2.2  # mỗi phần của khoảng bị lửng chia phải cao ít nhất mức này
_MEZZ_MARGIN_M: Final = 0.5  # lửng nằm trong khoảng tầng khi cách hai đầu hơn mức này
_BASEMENT_TOP_M: Final = -0.3  # cốt tầng hầm thấp hơn mốc trệt ít nhất mức này
_RAISED_MIN_M: Final = 0.3  # trệt tự ghi cốt chủ đạo ≥ mức này (không có 0.000 riêng) thì giữ cốt đó
_SUNK_M: Final = (0.015, 0.12)  # cốt mặt bằng thấp hơn một bậc trong khoảng này = sàn WC/ban công trũng
_RAISED_MAX_M: Final = 0.06  # ... cao hơn bậc 0,015 m tới mức này = phòng nâng; cốt tầng là bậc
_CHAIN_TOL_M: Final = 0.05  # cốt tầng khớp chuỗi neo khi cách không quá mức này
_RUNG_REF_M: Final = 3.3  # bậc thang chia hợp lý: gần mức này mỗi tầng thì tốt hơn
# ponytail: trần ứng viên mỗi tầng. Mặt bằng thật có dưới 30 cốt khác nhau; quá số này thì giữ các sàn
# gặp nhiều lần nhất (trệt luôn giữ sàn gần mốc trệt) để quy hoạch động O(tầng x trần²) xong trong thời hạn API.
# Ứng viên chỉ có trên thang có trần riêng cùng số (bậc trên nhiều mặt cắt trước): tối đa 2 x trần mỗi tầng.
_MAX_CANDS: Final = 30
_TIGHT: Final = (2.6, 4.2)  # chiều cao mỗi tầng "chắc chắn hợp lý"
_LOOSE: Final = (2.4, 4.6)  # chiều cao mỗi tầng "còn hợp lý"

_CONF_SURE: Final = 0.93  # cốt có cả trên mặt bằng lẫn thang, khoảng tầng chặt
_CONF_PLAN: Final = 0.8  # cốt từ một nguồn (hoặc hai nguồn nhưng khoảng chỉ lỏng/cần lửng), khoảng hợp lý
_CONF_WEAK: Final = 0.6  # khoảng tầng vô lý; hoặc trệt theo quy ước; hoặc tầng chèn từ thang
_CONF_AMBIG: Final = 0.7
_CONF_UNKNOWN: Final = 0.5
_CONF_MEZZ: Final = 0.7
_CONF_HEIGHT_MAX: Final = 0.85  # chiều cao không có hai đầu "plan+section" cùng khớp chuỗi neo; cốt lấy từ chuỗi
_CONF_MISSING: Final = 0.65  # chiều cao bắc qua một số tầng không có mặt bằng
_CONF_RUNG_HEIGHT: Final = 0.55  # chiều cao còn mặc định được thay bằng bậc thang phía trên
_CONF_DEFAULT: Final = 0.4

LevelSource = Literal["plan+section", "section", "plan", "convention", "default"]
HeightSource = Literal["plan+section", "section", "plan", "convention", "default"]
_RANK: Final[Mapping[str, int]] = {"plan+section": 3, "section": 2, "plan": 1, "convention": 0}
Ladder = Mapping[float, int]  # bậc thang cốt (m) → số mặt cắt có bậc đó


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
    """Cốt sàn của một tầng: giá trị (m), nguồn, độ chắc, cờ nhập nhằng và các cốt thay thế gần điểm.

    `snapped_from_m`: cốt mặt bằng đã được đưa về bậc thang gần nó (thấp hơn cốt tầng = sàn WC/ban công
    trũng, cao hơn = phòng nâng); `None` khi cốt lấy nguyên.
    """

    level_m: float | None
    source: LevelSource | None
    confidence: float
    ambiguous: bool = False
    alternatives: tuple[float, ...] = ()
    snapped_from_m: float | None = None


@dataclass(frozen=True, slots=True)
class StoreyHeight:
    """Chiều cao từ tầng `from_number` lên tầng đánh số kế tiếp `to_number` (`None` với tầng trên cùng).

    Cờ: "skips_mezzanine" (có lửng ở giữa, vẫn tính một cặp), "spans_missing_storey" (thiếu mặt bằng
    của số tầng ở giữa: không tự chia), "missing_storey_filled" (thiếu đúng một tầng và một bậc thang
    chia khoảng hợp lý: `to_number` là số tầng thiếu, chiều cao = bậc - cốt tầng dưới; chiều cao của
    chính tầng thiếu = cốt tầng trên - bậc không ghi vì tầng đó không có bản ghi), "assumed" (giá trị
    mặc định 3,3 m mỗi tầng), "implausible" (mỗi tầng ngoài 2,4-4,6 m và không lửng nào giải thích: vẫn
    đề xuất nhưng phải hỏi, soát A-F3/F4).
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
    """Ứng viên cốt của một tầng: giá trị (`None` = không biết), số cốt ủng hộ, có phải quy ước trệt,
    cốt mặt bằng đã đưa về bậc (`sunk`), số mặt cắt có bậc chứa nó (`support`), thuộc tầng trệt."""

    value: float | None
    count: int = 0
    conv: bool = False
    sunk: float | None = None
    support: int = 0
    ground: bool = False


@dataclass(frozen=True, slots=True)
class _Storey:
    """Ngữ cảnh một tầng đánh số trong quy hoạch động."""

    number: int
    total: int
    ground: bool
    basement: bool


@dataclass(frozen=True, slots=True)
class _Problem:
    """Toàn bộ dữ liệu của một lần giải: tầng theo số tăng dần, ứng viên, mốc trệt, cốt các lửng, thang."""

    storeys: tuple[_Storey, ...]
    cands: tuple[tuple[_Cand, ...], ...]
    g0: float
    mezz: tuple[float, ...]
    ladder: Ladder


def _build_ladder(sides: Sequence[Sequence[float]]) -> dict[float, int]:
    """Thang cốt `{bậc: số mặt cắt có bậc}` (heights3.build_ladder): gom tham lam quanh giá trị gặp ở nhiều
    mặt cắt nhất (hoà thì thấp hơn), cốt cách hạt ≤ 0,03 m cùng một bậc; thứ tự = thứ tự hạt."""
    rounded = [{round(x, 3) for x in side} for side in sides]
    left = Counter(v for side in rounded for v in side)
    ladder: dict[float, int] = {}
    while left:
        seed = min(left, key=lambda v: (-left[v], v))
        members = {v for v in left if abs(v - seed) <= _TOL_M}
        for v in members:
            del left[v]
        ladder[seed] = sum(bool(side & members) for side in rounded)
    return ladder


def _support(value: float, ladder: Ladder) -> int:
    """Số mặt cắt của bậc gần `value` trong 0,06 m (0 = không nằm trên thang)."""
    return max((n for rung, n in ladder.items() if abs(rung - value) <= _TOL_L + 1e-9), default=0)


def _plan_values(marks: Sequence[float], ground: bool, ladder: Ladder) -> list[tuple[float, int]]:
    """Cốt riêng của một mặt bằng `(giá trị, số lần)` sau luật "sàn hoàn thiện chính" của heights3.

    Khác `levels.main_floor_level`: giữ mọi nhóm kèm số lần; đại diện là 0.000 trên mặt bằng trệt, rồi
    giá trị nằm trên thang.
    """
    return level_groups(marks, prefer_zero=ground, ladder=ladder)


def _ground_anchor(marks: Sequence[float], ladder: Ladder) -> float:
    """Mốc của tầng trệt: cốt chủ đạo của chính nó khi ≥ 0,3 m, gặp ít nhất hai lần, nằm trên thang (hoặc
    không có thang) và không có 0.000 riêng (trệt +1.300 trên sân 0.000); ngược lại 0.0 (heights3.ground_level)."""
    vals = _plan_values(marks, True, ladder)
    if not vals or any(abs(v) <= _TOL_M for v, _ in vals):
        return 0.0
    value, count = max(vals, key=lambda c: (c[1], c[0]))
    on_ladder = _support(value, ladder) > 0 or not ladder
    return value if value >= _RAISED_MIN_M and count >= 2 and on_ladder else 0.0


def _sunk_rung(value: float, ladder: Ladder) -> float | None:
    """Bậc mà cốt mặt bằng `value` là sàn trũng (thấp hơn 0,015-0,12 m) hoặc phòng nâng (cao hơn
    0,015-0,06 m) của nó; cách bậc gần nhất dưới 0,015 m là nằm trên bậc → `None` (heights3.sunk_rung)."""
    if not ladder or min(abs(value - r) for r in ladder) < _SUNK_M[0]:
        return None
    rung = min(ladder, key=lambda r: abs(value - r))
    below, above = rung - value, value - rung
    return rung if _SUNK_M[0] <= below <= _SUNK_M[1] or _SUNK_M[0] <= above <= _RAISED_MAX_M else None


def _candidates(
    storey: _Storey, marks: Sequence[float], g0: float, ladder: Ladder, mezz: Sequence[float]
) -> tuple[_Cand, ...]:
    """Ứng viên của một tầng (heights3.candidates).

    Các cốt riêng (cốt ngay dưới/trên một bậc thì đưa về bậc, gộp số lần với ứng viên đã ở bậc đó); mọi bậc
    thang chưa có, trừ bậc trùng cốt một lửng; trệt thêm 0.000 theo quy ước khi chưa có; hầm chỉ nhận cốt
    dưới mốc trệt ít nhất 0,3 m hoặc "không biết" (cốt hầm đọc nhầm không đẩy trệt lên, DS-248).
    """
    vals = _plan_values(marks, storey.ground, ladder)
    if len(vals) > _MAX_CANDS:
        far = [not (storey.ground and abs(v - g0) <= _GROUND_NEAR_M) for v, _ in vals]
        ranked = sorted(range(len(vals)), key=lambda j: (far[j], -vals[j][1]))
        vals = [vals[j] for j in sorted(ranked[:_MAX_CANDS])]  # giữ thứ tự tăng dần (hoà điểm chọn như cũ)
    out: list[_Cand] = []
    for v, n in vals:
        rung = _sunk_rung(v, ladder)
        same = next(
            (
                j
                for j, c in enumerate(out)
                if rung is not None and c.value is not None and abs(c.value - rung) <= _TOL_M
            ),
            None,
        )
        if same is not None:
            out[same] = replace(out[same], count=out[same].count + n)
        else:
            out.append(_Cand(rung, n, sunk=v) if rung is not None else _Cand(v, n))
    rungs = [
        r
        for r in ladder
        if not any(c.value is not None and abs(r - c.value) <= _TOL_M for c in out)
        and not any(abs(r - m) <= _TOL_L for m in mezz)
    ]
    out += [_Cand(r) for r in rungs[:_MAX_CANDS]]
    if storey.ground and g0 == 0.0 and not any(c.value is not None and abs(c.value) <= _TOL_M for c in out):
        out.append(_Cand(0.0, conv=True))
    if storey.basement:
        out = [c for c in out if c.value is not None and c.value <= g0 + _BASEMENT_TOP_M] + [_Cand(None)]
    out = out or [_Cand(None)]
    return tuple(
        replace(c, support=_support(c.value, ladder) if c.value is not None else 0, ground=storey.ground) for c in out
    )


def _unary(cand: _Cand, storey: _Storey, g0: float) -> float:
    """Điểm đơn: bậc thang (thưởng thêm khi ≥ 2 mặt cắt) hoặc quy ước trệt, tỉ lệ cốt của mặt bằng ủng hộ,
    độ gần mốc trệt của tầng trệt."""
    if cand.value is None:
        return 0.0
    if cand.support:
        score = _RUNG_SCORE + (_RUNG_MULTI if cand.support >= 2 else 0.0)
    else:
        score = _CONV_SCORE if cand.conv else 0.0
    score += cand.count / max(storey.total, 1)
    if storey.ground:
        d = abs(cand.value - g0)
        score += 1.0 if d <= _TOL_M else 0.5 if d <= _GROUND_NEAR_M else _GROUND_MISS
    return score


def _mezz_explains(low: float, high: float, k: int, splitters: Sequence[float], top: float = _LOOSE[1]) -> bool:
    """Khoảng quá cao cho `k` tầng (mỗi tầng > `top`) nhưng hợp lý khi một cốt trong `splitters` (lửng, hoặc
    bậc thang) chia nó thành `k + 1` phần hợp lý, hai đầu mỗi phần ≥ 2,2 m."""
    gap = high - low
    return (
        gap / k > top
        and _LOOSE[0] <= gap / (k + 1) <= _LOOSE[1]
        and any(low + _MEZZ_PART_M <= m <= high - _MEZZ_PART_M for m in splitters)
    )


def _splitters(a: _Cand, b: _Cand, p: _Problem) -> Sequence[float]:
    """Cốt được phép chia khoảng a → b (heights3.mezz): các lửng; thêm mọi bậc thang khi a là tầng trệt và
    b có cốt trên mặt bằng của nó (lửng không ghi nhãn, chỉ ngay trên trệt như mọi ca đã gặp)."""
    return (*p.mezz, *p.ladder) if a.ground and b.count > 0 and p.ladder else p.mezz


def _pair(a: _Cand, b: _Cand, k: int, p: _Problem) -> float:
    """Điểm cặp của hai tầng liền nhau cách `k` số tầng: khoảng âm bị phạt, khoảng hợp lý được thưởng."""
    if a.value is None or b.value is None:
        return 0.0
    if b.value - a.value <= 0:
        return -2.0
    if _mezz_explains(a.value, b.value, k, _splitters(a, b, p)):
        return _MEZZ_SCORE
    per = (b.value - a.value) / k
    return 1.5 if _TIGHT[0] <= per <= _TIGHT[1] else 0.5 if _LOOSE[0] <= per <= _LOOSE[1] else -1.0


def _step(p: _Problem, i: int) -> int:
    """Số tầng giữa tầng thứ `i - 1` và `i` (theo số tăng dần)."""
    return p.storeys[i].number - p.storeys[i - 1].number


def _parts(p: _Problem, path: Sequence[int]) -> tuple[list[float], list[float]]:
    """Điểm đơn từng tầng và điểm cặp từng cặp kề của một lựa chọn (chỉ số ứng viên mỗi tầng)."""
    unary = [_unary(p.cands[i][j], p.storeys[i], p.g0) for i, j in enumerate(path)]
    pairs = [_pair(p.cands[i - 1][path[i - 1]], p.cands[i][path[i]], _step(p, i), p) for i in range(1, len(path))]
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
            opts = [prev_best[q] + _pair(prev, c, step, p) for q, prev in enumerate(prev_cands)]
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


def _gaps(p: _Problem, path: Sequence[int], i: int, band: tuple[float, float]) -> Literal["ok", "mezz", "bad"]:
    """Khoảng tới các tầng kề (heights3.gaps): "ok" khi mỗi tầng nằm trong `band`, "mezz" khi một khoảng
    cần luật lửng, "bad" khi có khoảng vô lý. Khoảng hầm → trệt chỉ xét trên bản ghi của tầng hầm."""
    out: Literal["ok", "mezz", "bad"] = "ok"
    for n in (i - 1, i + 1):
        if not 0 <= n < len(path) or (n < i and p.storeys[n].basement and not p.storeys[i].basement):
            continue
        lo, hi = sorted((i, n))
        a, b = p.cands[lo][path[lo]], p.cands[hi][path[hi]]
        if a.value is None or b.value is None:
            continue
        k = p.storeys[hi].number - p.storeys[lo].number
        if band[0] <= (b.value - a.value) / k <= band[1]:
            continue
        if _mezz_explains(a.value, b.value, k, _splitters(a, b, p), band[1]):
            out = "mezz"
            continue
        return "bad"
    return out


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
            alt_pairs[i - 1] = _pair(p.cands[i - 1][path[i - 1]], cand, _step(p, i), p)
        if i + 1 < len(path):
            alt_pairs[i] = _pair(cand, p.cands[i + 1][path[i + 1]], _step(p, i + 1), p)
        if best - _sum_parts(alt_unary, alt_pairs) < _AMBIG:
            near.append(cand)
    return bool(near), tuple(c.value for c in near if c.value is not None)


def _floor_level(p: _Problem, path: list[int], i: int, parts: tuple[list[float], list[float]]) -> FloorLevel:
    """Cốt, nguồn, độ chắc của tầng thứ `i` từ lựa chọn (heights3.floor_record).

    Nguồn: có trên mặt bằng và thang "plan+section", chỉ thang "section", chỉ mặt bằng "plan", không đâu
    "convention" (0.000 giả định của trệt). Độ chắc: hai nguồn và khoảng tầng chặt 0,93; hai nguồn, khoảng
    lỏng hoặc cần lửng 0,8; một nguồn (hoặc mặt bằng khi không có thang), khoảng hợp lý 0,8; còn lại 0,6
    (quy ước trệt, khoảng vô lý); nhập nhằng ≤ 0,7.
    """
    cand = p.cands[i][path[i]]
    if cand.value is None:
        return FloorLevel(None, None, _CONF_UNKNOWN)
    on, has = cand.support > 0, cand.count > 0
    source: LevelSource = "plan+section" if on and has else "section" if on else "plan" if has else "convention"
    tight, loose = _gaps(p, path, i, _TIGHT), _gaps(p, path, i, _LOOSE)
    if has and on and tight == "ok":
        confidence = _CONF_SURE
    elif (on or has) and loose != "bad":  # floor_record: hai nguồn khoảng lỏng/lửng, hoặc một nguồn: cùng 0,8
        confidence = _CONF_PLAN
    else:  # quy ước trệt (CONV_CONF) hoặc khoảng vô lý: cùng 0,6
        confidence = _CONF_WEAK
    ambiguous, alternatives = _near_alternatives(p, path, i, parts)
    if ambiguous:
        confidence = min(confidence, _CONF_AMBIG)
    return FloorLevel(cand.value, source, confidence, ambiguous, alternatives, cand.sunk)


def _mezzanine_level(marks: Sequence[float], ladder: Ladder) -> FloorLevel:
    """Cốt của lửng: cốt chủ đạo (đông nhất, hoà thì cao hơn) trên mặt bằng của nó; nằm trên thang thì
    "plan+section" 0,8, ngược lại "plan" 0,7 (solve_set.mezz_floors)."""
    vals = _plan_values(marks, False, ladder)
    if not vals:
        return FloorLevel(None, None, _CONF_UNKNOWN)
    value = max(vals, key=lambda c: (c[1], c[0]))[0]
    if _support(value, ladder):
        return FloorLevel(value, "plan+section", _CONF_PLAN)
    return FloorLevel(value, "plan", _CONF_MEZZ)


def _problem(numbered: Sequence[tuple[int, StoreyPlan]], mezz: tuple[float, ...], ladder: Ladder) -> _Problem:
    """Dựng bài toán: mốc trệt từ mặt bằng số 1, ứng viên từng tầng theo số tăng dần."""
    g0 = next((_ground_anchor(plan.marks, ladder) for number, plan in numbered if number == 1), 0.0)
    storeys = tuple(_Storey(n, len(plan.marks), n == 1, n <= 0) for n, plan in numbered)
    cands = tuple(_candidates(s, plan.marks, g0, ladder, mezz) for s, (_, plan) in zip(storeys, numbered, strict=True))
    return _Problem(storeys, cands, g0, mezz, ladder)


def _from_chain(number: int, level: FloorLevel, chain: Sequence[tuple[float, float, bool]]) -> tuple[FloorLevel, bool]:
    """Đối chiếu một tầng với chuỗi neo (solve_set.fill_from_chain): `(cốt, chain_ok)`.

    `chain_ok` khi cốt tầng cách cốt chuỗi của cùng số tầng ≤ 0,05 m. Tầng không có cốt lấy cốt chuỗi,
    độ chắc ≤ 0,85, chain_ok sai. Tầng hầm (số ≤ 0) không có cốt chuỗi.
    """
    if not 0 <= number - 1 < len(chain):
        return level, False
    value, confidence, has_section = chain[number - 1]
    if level.level_m is None:
        source: LevelSource = "plan+section" if has_section else "plan"
        return FloorLevel(value, source, min(confidence, _CONF_HEIGHT_MAX)), False
    return level, abs(value - level.level_m) <= _CHAIN_TOL_M


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


def _plausible_rungs(low: float, ladder: Ladder, k: int = 1, band: tuple[float, float] = _TIGHT) -> list[float]:
    """Bậc r có (r - low) / k trong `band`, tốt nhất trước: nhiều mặt cắt hơn, rồi gần 3,3 m mỗi tầng
    (heights3.plausible_rungs)."""
    rungs = [r for r in ladder if band[0] <= (r - low) / k <= band[1]]
    return sorted(rungs, key=lambda r: (-ladder[r], abs((r - low) / k - _RUNG_REF_M)))


def _filled_missing(
    n_low: int, a: FloorLevel, b: FloorLevel, mezz: Sequence[float], ladder: Ladder
) -> StoreyHeight | None:
    """Thiếu đúng một tầng giữa a và b (heights3.fill_missing): bậc thang để cả hai khoảng 2,4-4,6 m thì
    chiều cao tầng dưới = bậc - cốt a, nguồn "section", độ chắc min(cốt a, 0,6) như heights_of trên tầng
    chèn 0,6; không có bậc → `None` (người gọi giữ "spans_missing_storey")."""
    if a.level_m is None or b.level_m is None:
        return None
    low, high = a.level_m, b.level_m
    rungs = [r for r in _plausible_rungs(low, ladder, 1, _LOOSE) if _LOOSE[0] <= high - r <= _LOOSE[1]]
    if not rungs:
        return None
    rung = rungs[0]
    skips = any(low + _MEZZ_MARGIN_M < m < rung - _MEZZ_MARGIN_M for m in mezz)
    flags = ("skips_mezzanine",) * skips + ("missing_storey_filled",)
    confidence = min(a.confidence, _CONF_WEAK, _CONF_HEIGHT_MAX)
    return StoreyHeight(n_low, n_low + 1, round(rung - low, 3), "section", confidence, flags)


def _height(
    low: tuple[int, FloorLevel, bool],
    high: tuple[int, FloorLevel, bool],
    skips: bool,
    mezz: Sequence[float],
    ladder: Ladder,
) -> StoreyHeight:
    """Chiều cao giữa hai tầng đánh số liền nhau = hiệu cốt; thiếu cốt → 3,3 m mỗi tầng ở 0,4 "assumed".

    Phần tử thứ ba của mỗi đầu là chain_ok. Nguồn là nguồn yếu hơn của hai cốt. Độ chắc = min hai cốt,
    ≤ 0,85 trừ khi cả hai "plan+section" và chain_ok (solve_set.heights_of), ≤ 0,65 khi bắc qua tầng thiếu.
    Hiệu cốt chia số tầng ngoài 2,4-4,6 m (như `_gaps`) mà không lửng nào giải thích thì thêm cờ
    "implausible": giá trị giữ nguyên (tầng hầm, lửng thấp 2,0-2,4 m vẫn có thể đúng), lớp trên hỏi.
    """
    (n_low, a, ok_a), (n_high, b, ok_b) = low, high
    k = n_high - n_low
    flags = (["skips_mezzanine"] if skips else []) + (["spans_missing_storey"] if k >= 2 else [])
    if a.level_m is None or b.level_m is None:
        height = round(DEFAULT_HEIGHT_M * k, 3)
        return StoreyHeight(n_low, n_high, height, "default", _CONF_DEFAULT, (*flags, "assumed"))
    gap = b.level_m - a.level_m
    plan_above = b.source in ("plan", "plan+section")
    splitters = (*mezz, *ladder) if n_low == 1 and plan_above else mezz
    if not (_LOOSE[0] <= gap / k <= _LOOSE[1] or _mezz_explains(a.level_m, b.level_m, k, splitters)):
        flags.append("implausible")
    weaker = min((a.source or "plan", b.source or "plan"), key=lambda s: _RANK.get(s, 0))
    sure = weaker == "plan+section" and ok_a and ok_b
    confidence = min(a.confidence, b.confidence, 1.0 if sure else _CONF_HEIGHT_MAX, _CONF_MISSING if k >= 2 else 1.0)
    return StoreyHeight(n_low, n_high, round(gap, 3), weaker, confidence, tuple(flags))


def _heights(
    numbered: Sequence[tuple[int, int]],
    levels: Sequence[FloorLevel],
    mezz_idx: Sequence[int],
    ladder: Ladder,
    chain_ok: Mapping[int, bool],
) -> list[StoreyHeight]:
    """Chiều cao mọi cặp tầng đánh số liền nhau (thiếu đúng một tầng mà thang chia được thì chiều cao tầng
    dưới tới tầng thiếu), rồi tầng trên cùng (mặc định 3,3 m ở 0,4)."""
    mezz = tuple(lv for m in mezz_idx if (lv := levels[m].level_m) is not None)
    out = []
    for (na, ia), (nb, ib) in pairwise(numbered):
        filled = _filled_missing(na, levels[ia], levels[ib], mezz, ladder) if nb - na == 2 else None
        if filled is not None:
            out.append(filled)
            continue
        ends = (na, levels[ia], chain_ok.get(ia, False)), (nb, levels[ib], chain_ok.get(ib, False))
        out.append(_height(*ends, _skips_mezzanine((ia, ib), levels, mezz_idx), mezz, ladder))
    if numbered:
        top = numbered[-1][0]
        out.append(StoreyHeight(top, None, DEFAULT_HEIGHT_M, "default", _CONF_DEFAULT, ("assumed",)))
    return out


def _fill_defaults(
    numbered: Sequence[tuple[int, int]], floors: list[FloorLevel], heights: list[StoreyHeight], ladder: Ladder
) -> None:
    """Chiều cao còn mặc định mà tầng dưới có cốt, tầng trên không (solve_set.fill_defaults): bậc thang
    hợp lý phía trên → chiều cao nguồn "section" 0,55, tầng trên nhận cốt bậc đó ở 0,55. Đi từ dưới lên nên
    cốt vừa suy ra nối tiếp được lên tầng kế. Sửa tại chỗ `floors` và `heights`."""
    for pos, ((na, ia), (nb, ib)) in enumerate(pairwise(numbered)):
        h, a, b = heights[pos], floors[ia], floors[ib]
        if h.source != "default" or a.level_m is None or b.level_m is not None:
            continue
        rungs = _plausible_rungs(a.level_m, ladder, nb - na)
        if not rungs:
            continue
        flags = tuple(f for f in h.flags if f != "assumed")
        heights[pos] = replace(
            h, height_m=round(rungs[0] - a.level_m, 3), source="section", confidence=_CONF_RUNG_HEIGHT, flags=flags
        )
        floors[ib] = FloorLevel(round(rungs[0], 3), "section", _CONF_RUNG_HEIGHT)


def solve_heights(
    storeys: Sequence[StoreyPlan],
    sides: Sequence[Sequence[float]] = (),
    chain: Sequence[tuple[float, float, bool]] | None = None,
) -> tuple[tuple[FloorLevel, ...], tuple[StoreyHeight, ...]]:
    """Cốt sàn mỗi tầng (cùng thứ tự đầu vào) và chiều cao các cặp tầng đánh số liền nhau.

    `storeys` theo thứ tự bộ bản vẽ, mỗi số tầng một mặt bằng. Lửng không vào quy hoạch động nhưng
    cốt của nó làm khoảng cao bị nó chia thành hợp lý; nó không cắt cặp tầng.

    `sides`: mỗi mặt cắt/mặt đứng một dãy cốt (m) đọc được trên nó, đã bỏ ngoại lai; dựng thành thang cốt.
    `chain`: chuỗi cốt đã neo theo số tầng, `chain[n - 1] = (cốt m, độ chắc, có nguồn mặt cắt)` của tầng
    số n; `None` khi không có chuỗi neo. Chuỗi chỉ đối chiếu (chain_ok, cần cho chiều cao ≥ 0,9) và lấp
    cốt còn thiếu, không đổi lựa chọn của quy hoạch động.
    """
    ladder = _build_ladder(sides)
    mezz_idx = [i for i, s in enumerate(storeys) if s.number is None]
    levels = {i: _mezzanine_level(storeys[i].marks, ladder) for i in mezz_idx}
    numbered = sorted((s.number, i) for i, s in enumerate(storeys) if s.number is not None)
    chain_ok: dict[int, bool] = {}
    if numbered:
        mezz = tuple(lv.level_m for lv in levels.values() if lv.level_m is not None)
        problem = _problem([(n, storeys[i]) for n, i in numbered], mezz, ladder)
        path = _dp(problem)
        parts = _parts(problem, path)
        levels |= {i: _floor_level(problem, path, pos, parts) for pos, (_, i) in enumerate(numbered)}
        if chain is not None:
            for n, i in numbered:
                levels[i], chain_ok[i] = _from_chain(n, levels[i], chain)
    floors = [levels[i] for i in range(len(storeys))]
    heights = _heights(numbered, floors, mezz_idx, ladder, chain_ok)
    _fill_defaults(numbered, floors, heights, ladder)
    return tuple(floors), tuple(heights)


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
