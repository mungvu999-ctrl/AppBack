"""Chuỗi cốt sàn của ngôi nhà KHÔNG cần số tầng (Mức 3).

Chuyển trung thành phần thuần của nhân nghiên cứu `kernel/levels_chain.py` (`dominant`, `side_like`,
`dom_near`, `dom_w`, `near`, `make_candidates`, `attach`, `node`, `edge`, `start`, `dp`, `confidence`,
`solve_chain`) cùng các hàm phụ thuộc của `kernel/heights2.py` (`cluster`, `build_ladder`, `support`),
chép riêng ở đây để không phụ thuộc `heights.py`. Không chuyển phần đo (`set_inputs`, `load_inputs`,
`measure*`).

Ứng viên = bậc thang cốt của mặt cắt/mặt đứng + cốt chủ đạo của từng mặt bằng; chọn chuỗi tăng dần bằng
quy hoạch động (điểm nút = độ đỡ của thang + mặt bằng nằm trên bậc; điểm cạnh = khoảng cao giống một
tầng), bắt đầu gần 0.000 (hoặc ở mặt bằng tầng hầm).
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

_TOL: Final = 0.06  # hai cốt cách nhau không quá mức này là một sàn
_STOREY: Final = (2.4, 4.6)  # khoảng cao giống một tầng
_STOREY_GOOD: Final = (2.7, 4.3)  # khoảng cao tầng "rất hợp lý"
_MEZZ: Final = 1.6  # khoảng cao nhỏ nhất cho phép (lửng); thấp hơn thì không phải hai sàn
_W_SIDE: Final = 1.0
_W_PLAN: Final = 2.0
_W_ANY: Final = 0.25
_BASE: Final = 1.6
_SKIP: Final = 2.0  # phạt mỗi mặt bằng bị bỏ qua giữa hai bậc liền nhau của chuỗi
_MEZZ_PEN: Final = -1.5
_TIE: Final = 1.0  # hệ số trọng số theo hạng của giá trị hoà trong một mặt bằng (1,0 = chia theo số đếm)
_EPS: Final = 1e-9
_SAME_V: Final = 1e-6  # hai ứng viên trùng cốt sau khi dời về giá trị của mặt bằng
_YARD_M: Final = -0.2  # cốt sân thấp hơn mức này xếp sau cùng khi chia độ đỡ của mặt bằng
_BASEMENT_M: Final = -0.9  # mặt bằng dưới mức này là tầng hầm
_GROUND_EXACT_M: Final = 0.05
_GROUND_NEAR_M: Final = 0.2
_SIDE_LIKE_MIN: Final = 4  # số cốt chủ đạo tối thiểu để một "mặt bằng" bị coi là mặt cắt
_TOP_CONF: Final = 0.8  # trần độ tin của bậc trên cùng chỉ có một mặt bằng (có thể là sân thượng/mái)
_TOL_M: Final = 0.03  # heights2.TOL_M: dung sai gộp cụm
_TOL_L: Final = 0.06  # heights2.TOL_L: dung sai khớp thang

# ponytail: trần khối lượng việc vì đầu vào là dữ liệu ngoài; quy hoạch động O(ứng viên³ x mặt bằng).
# Nhà thật ≤ 60 mặt bằng, ≤ 20 mặt cắt, mỗi tờ ≤ vài chục cốt, ứng viên ≤ ~100 (ứng viên cách nhau
# > 0,06 m). Vượt trần thì ValueError thay vì cắt bớt (cắt bớt sẽ đổi kết quả mà không ai biết).
_MAX_PLANS: Final = 200
_MAX_SIDES: Final = 100
_MAX_MARKS: Final = 1000  # số cốt trên một tờ
_MAX_CANDS: Final = 300

_Dom = list[tuple[float, int]]


@dataclass(frozen=True, slots=True)
class ChainLevel:
    """Một bậc của chuỗi cốt sàn."""

    level_m: float
    confidence: float
    sources: tuple[str, ...]  # "plan", "section"
    plans: tuple[int, ...]  # chỉ số trong plan_marks của các mặt bằng gắn vào bậc này
    mezzanine: bool = False


@dataclass(slots=True)
class _Cand:
    v: float
    side: int = 0
    plans: list[int] = field(default_factory=list)
    pw: float = 0.0
    any: int = 0


def _cluster(cnt: dict[float, int], tol: float = _TOL_M) -> list[tuple[float, list[float], int]]:
    """Cụm tham lam quanh giá trị gặp nhiều nhất: [(hạt, thành viên, tổng số đếm)]."""
    left = dict(cnt)
    out: list[tuple[float, list[float], int]] = []
    while left:
        seed = min(left, key=lambda v: (-left[v], v))
        mem = [v for v in left if abs(v - seed) <= tol]
        out.append((seed, mem, sum(left.pop(v) for v in mem)))
    return out


def _build_ladder(sides: Sequence[Sequence[float]]) -> dict[float, int]:
    """{cốt: độ đỡ} — độ đỡ = số mặt cắt chứa cốt (đã gộp cụm)."""
    cnt = Counter(v for s in sides for v in {round(x, 3) for x in s})
    return {seed: sum(any(round(x, 3) in mem for x in s) for s in sides) for seed, mem, _ in _cluster(cnt)}


def _support(v: float, ladder: dict[float, int]) -> int:
    """Độ đỡ của v trên thang (0 = không nằm trên thang, trong 0,06 m)."""
    return max((n for lv, n in ladder.items() if abs(lv - v) <= _TOL_L + _EPS), default=0)


def _dominant(marks: Sequence[float]) -> _Dom:
    """Cốt chủ đạo của một mặt bằng: cụm có số đếm ≥ nửa cụm lớn nhất, [(v, n)] lớn trước."""
    cl = sorted(_cluster(Counter(round(m, 3) for m in marks)), key=lambda c: (-c[2], abs(c[0])))
    return [(v, n) for v, _, n in cl if n >= 0.5 * cl[0][2]] if cl else []


def _side_like(dom: _Dom) -> bool:
    """'Mặt bằng' có cốt chủ đạo trải nhiều tầng thực ra là mặt cắt/mặt đứng bị đọc nhầm."""
    vs = [v for v, _ in dom]
    return len(vs) >= _SIDE_LIKE_MIN and max(vs) - min(vs) > 2 * _STOREY[1]


def _dom_near(dom: _Dom, v: float) -> int:
    """Số đếm của cốt chủ đạo gần v trong một mặt bằng (0 = không có)."""
    return max((n for d, n in dom if abs(d - v) <= _TOL + _EPS), default=0)


def _dom_w(dom: _Dom, v: float) -> float:
    """Phần độ đỡ của một mặt bằng dành cho v: theo số đếm, giá trị thấp giống sàn của nhóm hoà trước
    (mặt bằng tầng còn ghi chiếu nghỉ / lửng phía trên; cốt sân dưới -0,2 xếp sau cùng)."""
    order = sorted(dom, key=lambda x: (x[0] < _YARD_M, x[0]))
    w = {d: n * _TIE**r for r, (d, n) in enumerate(order)}
    tot = sum(w.values())
    return sum(x for d, x in w.items() if abs(d - v) <= _TOL + _EPS) / tot if tot else 0.0


def _near(v: float, vals: Sequence[float]) -> bool:
    """True khi có giá trị trong vals cách v không quá _TOL."""
    return any(abs(v - x) <= _TOL + _EPS for x in vals)


def _make_candidates(plan_marks: list[list[float]], ladder: dict[float, int], doms: list[_Dom]) -> list[_Cand]:
    """Bậc thang + cốt chủ đạo của mặt bằng, gộp trong _TOL, tăng dần."""
    vals = sorted(set(ladder) | {d for dom in doms for d, _ in dom})
    cands: list[_Cand] = []
    for v in vals:
        if cands and abs(v - cands[-1].v) <= _TOL:
            continue
        cands.append(_Cand(v))
    for c in cands:
        v = c.v
        c.side = _support(v, ladder)
        c.plans = [i for i, dom in enumerate(doms) if _dom_near(dom, v)]
        c.pw = sum(_dom_w(doms[i], v) for i in c.plans)
        c.any = sum(_near(v, m) for m in plan_marks)
        own = Counter(d for i in c.plans for d, _ in doms[i] if abs(d - v) <= _TOL + _EPS)
        if own:  # đặt sàn lên chính cốt của mặt bằng (bậc thang có thể lệch vài cm)
            c.v = own.most_common(1)[0][0]
    out: list[_Cand] = []
    for c in sorted(cands, key=lambda c: (c.v, -c.pw)):
        if not out or abs(c.v - out[-1].v) > _SAME_V:
            out.append(c)
    return out


def _attach(chain_cands: list[_Cand], doms: list[_Dom]) -> list[list[int]]:
    """Mỗi mặt bằng vào MỘT bậc: cốt chủ đạo có số đếm lớn nhất nằm trong chuỗi (hoà → bậc thấp)."""
    out: list[list[int]] = [[] for _ in chain_cands]
    for i, dom in enumerate(doms):
        hits = [(_dom_near(dom, c.v), -k) for k, c in enumerate(chain_cands) if _dom_near(dom, c.v)]
        if hits:
            out[-max(hits)[1]].append(i)
    return out


def _node(c: _Cand, nside: int) -> float:
    """Điểm đỡ của một ứng viên (âm = không đáng thành một bậc riêng)."""
    side = 2.0 * c.side / nside + 0.5 * (c.side >= 2) if nside else 0.0
    return _W_SIDE * side + _W_PLAN * min(c.pw, 2) + _W_ANY * min(c.any, 4) - _BASE


def _edge(a: float, b: float) -> float | None:
    """Điểm của hai bậc liền nhau a → b (None = không cho phép)."""
    g = b - a
    if g < _MEZZ - _EPS:
        return None
    if _STOREY[0] <= g <= _STOREY[1]:
        return 1.0 if _STOREY_GOOD[0] <= g <= _STOREY_GOOD[1] else 0.5
    if g < _STOREY[0]:
        return _MEZZ_PEN  # lửng
    return -1.0 if g <= 2 * _STOREY[1] else -3.0  # bỏ qua một tầng


def _start(c: _Cand, has_low_plan: bool) -> float:
    """Điểm của c làm bậc thấp nhất: trệt gần 0.000, hoặc mặt bằng tầng hầm thấp hơn."""
    if abs(c.v) <= _GROUND_EXACT_M:
        return 1.5
    if abs(c.v) <= _GROUND_NEAR_M:
        return 0.8
    if c.plans and (c.v < _BASEMENT_M or has_low_plan):
        return 0.0
    return -2.5


def _dp(cands: list[_Cand], nside: int, doms: list[_Dom]) -> list[int]:
    """Chuỗi tăng tốt nhất (chỉ số trong cands)."""
    low_plan = any(c.plans and c.v < _BASEMENT_M for c in cands)
    best: list[float] = []
    back: list[int | None] = []
    for i, c in enumerate(cands):
        b: float = _start(c, low_plan)
        p: int | None = None
        for j in range(i):
            e = _edge(cands[j].v, c.v)
            if e is not None:
                own = set(cands[j].plans) | set(c.plans)
                e -= _SKIP * sum(
                    min(sum(_dom_w(doms[q], x.v) for q in x.plans if q not in own), 1) for x in cands[j + 1 : i]
                )
            if e is not None and best[j] + e > b:
                b, p = best[j] + e, j
        best.append(b + _node(c, nside))
        back.append(p)
    if not best:
        return []
    k: int | None = max(range(len(best)), key=best.__getitem__)
    path: list[int] = []
    while k is not None:
        path.append(k)
        k = back[k]
    return path[::-1]


def _confidence(c: _Cand, prev: float | None, nxt: float | None) -> float:
    """≥ 0,9 khi có cả mặt bằng và thang với khoảng giống tầng; một nguồn 0,7-0,9; còn lại < 0,7."""
    gaps = [
        g for g in (c.v - prev if prev is not None else None, nxt - c.v if nxt is not None else None) if g is not None
    ]
    good = all(_STOREY[0] <= g <= _STOREY[1] for g in gaps)
    if c.plans and c.side and good:
        return 0.93
    if (c.plans or c.side >= 2) and good:
        return 0.8
    if c.plans or c.side:
        return 0.65
    return 0.5


def _check_size(plan_marks: Sequence[Sequence[float]], sides: Sequence[Sequence[float]]) -> None:
    """Chặn đầu vào quá cỡ trước khi chạy các bước O(n²)/O(n³)."""
    if len(plan_marks) > _MAX_PLANS or len(sides) > _MAX_SIDES:
        raise ValueError(f"quá nhiều tờ: {len(plan_marks)} mặt bằng, {len(sides)} mặt cắt")
    if any(len(ms) > _MAX_MARKS for ms in (*plan_marks, *sides)):
        raise ValueError(f"một tờ có hơn {_MAX_MARKS} cốt")


def solve_chain(plan_marks: Sequence[Sequence[float]], sides: Sequence[Sequence[float]]) -> tuple[ChainLevel, ...]:
    """Chuỗi cốt sàn của ngôi nhà từ dưới lên, không cần số tầng.

    plan_marks: các cốt (m) đọc được trên từng tờ được phân loại là mặt bằng.
    sides: các cốt (m) tin cậy của từng mặt cắt/mặt đứng (thang cốt).
    ValueError khi đầu vào vượt trần khối lượng việc.
    """
    _check_size(plan_marks, sides)
    marks = [[round(m, 3) for m in ms] for ms in plan_marks]
    doms = [_dominant(ms) for ms in marks]
    sidey = [_side_like(d) for d in doms]
    all_sides = list(sides) + [ms for ms, b in zip(marks, sidey, strict=True) if b]
    doms = [[] if b else d for d, b in zip(doms, sidey, strict=True)]
    ladder = _build_ladder(all_sides)
    cands = _make_candidates(marks, ladder, doms)
    if len(cands) > _MAX_CANDS:
        raise ValueError(f"quá nhiều cốt ứng viên: {len(cands)}")
    path = _dp(cands, sum(bool(s) for s in all_sides), doms)
    # Bậc chỉ có mặt cắt nằm trên mặt bằng cao nhất là mái/đỉnh/tường chắn mái (dev: 6/6).
    while len(path) > 1 and not cands[path[-1]].plans and any(cands[i].plans for i in path):
        path.pop()
    vs = [cands[i].v for i in path]
    att = _attach([cands[i] for i in path], doms)
    out: list[ChainLevel] = []
    for k, i in enumerate(path):
        c = cands[i]
        conf = _confidence(c, vs[k - 1] if k else None, vs[k + 1] if k + 1 < len(vs) else None)
        if k == len(path) - 1 and k and len(att[k]) < 2:
            conf = min(conf, _TOP_CONF)  # đỉnh chuỗi chỉ có một mặt bằng: có thể là sàn mái/sân thượng
        sources = (("plan",) if c.plans else ()) + (("section",) if c.side else ())
        mezz = bool(k) and vs[k] - vs[k - 1] < _STOREY[0]
        out.append(ChainLevel(c.v, conf, sources, tuple(att[k]), mezz))
    return tuple(out)
