"""Số tầng của từng mặt bằng trong một bộ bản vẽ: tên hình, cốt sàn và thứ tự hình (HS-01, T3).

Gương phần TẦNG của `F:/runs/dwglab/muc3/kernel/solve_set.py` (v2-v6) cùng `storey_order.py`
(quy ước đánh số của cả bộ, thứ tự cốt) và các dấu hiệu chữ/cốt/thứ tự của `storey_cues.py`.
Chỉ thư viện chuẩn. Đầu vào đều là mặt bằng (sản phẩm biết vậy) nên độ tin tầng không bị chặn
bởi độ tin loại hình.

Đánh số theo bộ: tầng trệt = 1, lên trên 2, 3 ...; hầm 0, -1; lửng và không rõ = None.
Luật giữ như kernel:
 * Quy ước của BỘ: bộ có cả "trệt" lẫn "tầng 1" -> tầng N = N + 1; "lầu N" = N + 1;
   "first floor" (đếm kiểu Anh) = 2; tầng kiểu thứ tự tiếng Anh mơ hồ -> tối đa 0,85.
 * Tầng đọc từ tên không bị dấu hiệu yếu hơn lật: bất đồng thành `conflict`, độ tin 0,65.
 * >= 0,9 khi hai họ dấu hiệu khác nhau cùng ý và không dấu hiệu mạnh nào chỉ nơi khác;
   một họ mạnh 0,72-0,85; chỉ dấu hiệu yếu < 0,7; phương án nhì sát nút -> tối đa 0,7.
 * Dấu hiệu cốt đếm từ dưới lên ("0.000 = tầng 1", hạng cốt) chỉ dùng khi chuỗi được NEO:
   mặt bằng mà chữ đặt ở tầng 1 nằm trên cốt thấp nhất.
Không chuyển: chuỗi cốt có mặt cắt (`levels_chain`), dấu hiệu tường trùng (twin, cần mặt nạ),
xếp lại theo khớp cao độ (`level_fit`), đánh số kiểu schema (`renumber`).
"""

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import count, pairwise
from typing import Literal

from packages.domain.drawing_set.titles import TitleInfo, fold, parse_title

Family = Literal["text", "level", "order"]
StoreyLabel = Literal["ground", "basement", "mezzanine", "tum", "attic", "roof_terrace", "floor"]

# Họ của từng nguồn; "order" gom thứ tự hình và phép loại trừ (họ "weak" của kernel).
_FAMILY: dict[str, Family] = {
    "title": "text",
    "words": "text",
    "rank": "text",
    "level": "level",
    "level_order": "level",
    "order": "order",
    "elim": "order",
}
_STRONG = {
    "title": 0.6,
    "words": 0.45,
    "rank": 0.5,
    "level": 0.45,
    "level_order": 0.6,
}  # điểm tối thiểu của một phiếu mạnh
_BOTTOM_LEVEL = (0.6, 0.4)  # điểm của hai luật cốt đếm từ dưới lên
_AGREE, _HOLE = 0.4, 0.6
_BEAM, _PER_PLAN = 200, 4
_CLOSE = 0.3
_CONFLICT = 0.65
_ORDINAL_CAP = 0.85
_ROOF_STOREY_CAP = 0.7  # tum/sân thượng: tầng cao nhất + 1, chưa biết có phòng hay không
_LADDER_SCORE = 0.75  # phiếu bậc cốt (kernel: 0,8 x độ tin của bậc trong chuỗi)
_NUMBERED = ("plain", "furniture")  # biến thể mang số tầng
_KIND_CONF = 0.92  # độ tin loại hình của mặt bằng đã biết (chế độ "known" của kernel)
_MERGE_DOM, _MERGE_STOREY, _NEAR0, _MEZZ_GAP = 0.03, 0.25, 0.6, 2.2
_PLANISH = re.compile(r"MAT ?BANG|BINH DO|\bPLAN\b|FLOOR")
# Dòng MỞ ĐẦU bằng tên loại hình ("MAT BANG TANG 2", "SECOND FLOOR PLAN"): tên hình thật, không phải ghi chú
# nhắc tới hình khác ("XEM MAT BANG TANG 2").
_TITLE_HEAD = re.compile(r"^(?:MAT ?BANG|BINH DO)\b|^\w+ FLOOR PLAN\b")
_NONSTOREY = re.compile(r"CAU ?TAO|CHI TIET|DETAIL|\bWC\b|\bSAN\b(?! THUONG)")
# Dòng KHÔNG phải tên hình dù có chữ tầng: mô tả công trình ("NHÀ PHỐ 1 TRỆT 2 LẦU", "3 TẦNG"),
# ghi chú thang/mặt cắt ("LÊN TẦNG 3", "SÀN TẦNG 2", "CỐT ..."). Chúng không bỏ phiếu tầng (soát mã F2).
_NOT_TITLE = re.compile(
    r"CONG TRINH|DU AN|NHA PHO|BIET THU|CHU DAU TU|\d\s*(?:TRET|TANG|LAU)\b(?!\s*\d)"
    r"|\bLEN\b|\bXUONG\b|\bUP\b|\bDN\b|\bDOWN\b|SAN TANG|\bCOT\b"
)
_GROUND_WORD = r"\bTRET\b|\bGRO\w{1,6}\s+FLOOR|\bGROUND\b"
_FIRST_WORD = r"\b(?:TANG|LAU)\s*0?1\b"
# Một tầng cao 2,6-4,2 m; tỉ số (chênh cốt / chênh số tầng) của hai mặt bằng kề nhau phải trong 2,4-4,6 m.
_STOREY_H = (2.6, 4.2)
_GAP_OK = (2.4, 4.6)
_DATUM = 0.15  # cốt +-0.000 của tầng 1: mốc tuyệt đối mà tên không mã hoá được
_DUP_LEVEL = 0.5  # hai mặt bằng thường cùng số tầng, cốt lệch hơn ngần này: giữ cái khớp hàng xóm
_UNCHECKED_CAP = 0.6  # quy ước bộ khác mặc định mà không có cốt để kiểm: phải hỏi
_TEXT_ONLY_CAP = 0.85  # trần khi họ cốt không qua phép thử khoảng cách tầng
_ORD = {"FIRST": 2, "SECOND": 3, "THIRD": 4, "FOURTH": 5, "FIFTH": 6, "SIXTH": 7}
_ENG = re.compile(r"\b(?:" + "|".join(_ORD) + r")\s+FLOOR|\b\d+\s*(?:ST|ND|RD|TH)\s+FLOOR")
_RELAXED = (
    (re.compile(r"LAU\s*0?(\d{1,2})(?!\d)"), 1, "lau"),
    (re.compile(r"T[AC]NG\s*0?(\d{1,2})(?!\d)"), 0, "tang"),
    (re.compile(r"\b(\d)\s*(?:ST|ND|RD|TH)\s+FLOOR"), 1, "ordinal"),
)
# Độ sâu hầm sau mọi từ hầm mà `parse_title` nhận (HAM, BASEMENT, CELLAR, UNDERGROUND) hoặc mã "B2" đứng riêng.
_BASEMENT_NO = re.compile(r"(?:HAM|BASEMENT|CELLAR|UNDERGROUND|TANG AM)\s*B?\s*-?\s*(\d+)|\bB\s*-?\s*(\d+)\b")
_BASEMENT_TAG = re.compile(r"\bB\b")  # có mã "B" mà không đọc được số: độ sâu có ghi nhưng không rõ
_MAX_BASEMENT = 6  # hầm sâu nhất đọc được; sâu hơn hoặc số lạ -> hầm không số
_UNSURE_BASEMENT = 0.5
# Chặn khối lượng việc: chữ OCR là dữ liệu ngoài, số tầng lạ không được sinh hàng triệu ứng viên.
_MAX_VIEWS = 200  # quá số hình này trong một lần gọi: không chọn tầng cho hình nào (độ tin 0)
_SPAN_CAP = 40  # dải tầng ứng viên rộng tối đa (và không rộng hơn số mặt bằng)

Votes = dict[int, dict[str, float]]


@dataclass(frozen=True)
class PlanView:
    """Một mặt bằng của bộ: mã, tên hình đã đọc, các cốt (m), vị trí trong bộ, chữ khác trên hình."""

    view_id: str
    title: TitleInfo | None
    levels: tuple[float, ...] = ()
    position: int = 0
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alternative:
    """Phương án nhì: số tầng và điểm."""

    number: int
    score: float


@dataclass(frozen=True)
class StoreyConflict:
    """Một dấu hiệu mạnh chỉ tầng khác tầng đã chọn."""

    source: str
    family: Family
    number: int


@dataclass(frozen=True)
class StoreyChoice:
    """Tầng chọn cho một mặt bằng (số theo bộ: trệt = 1, hầm 0, -1; lửng/không rõ = None)."""

    label: StoreyLabel | None
    number: int | None
    confidence: float
    sources: tuple[Family, ...]
    alternative: Alternative | None
    conflict: tuple[StoreyConflict, ...]


_NONE = StoreyChoice(None, None, 0.0, (), None, ())


@dataclass(frozen=True)
class _Row:
    """Kết quả bước tên + thứ tự cốt (`assign_storeys` của kernel) cho một mặt bằng có số."""

    floor: int | None
    confidence: float
    source: str
    title_floor: int | None


# ---------------------------------------------------------------- tên hình -> số tầng như kernel
def _norm(view: PlanView) -> str:
    """Tên đã gấp, rỗng khi không có tên."""
    return view.title.norm if view.title else ""


def _numbered(view: PlanView) -> bool:
    """Mặt bằng thường/nội thất và không phải hầm, lửng, tum, sân thượng."""
    t = view.title
    if t is None or t.kind != "plan" or t.variant not in _NUMBERED:
        return False
    return not isinstance(t.storey, str) or t.storey == "ground"


def _written_floor(view: PlanView) -> int | None:
    """Số tầng theo quy ước mặc định của kernel: trệt/tầng 1 = 1, tầng N = N, lầu N = N + 1, "first floor" = 2."""
    t = view.title
    if t is None or not _numbered(view):
        return None
    if isinstance(t.storey, int):
        return t.storey + (1 if t.numbering in ("lau", "floor_ordinal") else 0)
    return 1 if t.storey == "ground" else None


# ---------------------------------------------------------------- cốt sàn trội
def _merge(values: Sequence[float], tol: float) -> list[list[float]]:
    """Giá trị đã sắp -> nhóm có phần tử cách phần tử đầu nhóm <= tol."""
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and v - groups[-1][0] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def dominant(levels: Sequence[float], near_zero: bool = False) -> float | None:
    """Cốt hay gặp nhất (gom 0,03 m); hoà: gần 0 nhất nếu `near_zero`, không thì cao hơn. None khi không có cốt."""
    if not levels:
        return None
    cnt = Counter(round(v, 3) for v in levels)
    cands = [(sum(cnt[v] for v in g), sum(g) / len(g)) for g in _merge(list(cnt), _MERGE_DOM)]
    top = max(c for c, _ in cands)
    tied = [v for c, v in cands if c == top]
    return min(tied, key=abs) if near_zero else max(tied)


# ---------------------------------------------------------------- quy ước đánh số của bộ
def _has(views: Sequence[PlanView], pattern: str) -> bool:
    """Có tên hình nào khớp mẫu."""
    return any(re.search(pattern, _norm(v)) for v in views)


def _decide(views: Sequence[PlanView]) -> str:
    """Quy ước do tên hình nêu: "trệt" và "tầng/lầu 1" trên HAI hình khác nhau -> "tret_plus_tang1" (một tên
    nêu cả hai, "TẦNG 1 (TRỆT)", là tầng trệt và không bật quy ước: soát mã F1); first floor hoặc main
    floor/level (trệt kiểu Mỹ/Canada, `titles._GROUND`) mà không có ground -> "us_english"."""
    marks = [(bool(re.search(_GROUND_WORD, n)), bool(re.search(_FIRST_WORD, n))) for n in map(_norm, views)]
    if any(g and not f for g, f in marks) and any(f and not g for g, f in marks):
        return "tret_plus_tang1"
    if _has(views, r"\bFIRST\s+FLOOR|\bMAIN\s+(?:FLOOR|LEVEL)\b") and not any(g for g, _ in marks):
        return "us_english"
    return "unchanged"


def _storeys(views: Sequence[PlanView], conv: str) -> dict[str, int]:
    """{mã: số tầng theo tên dưới quy ước `conv`} cho mặt bằng có số.

    Cách đếm Mỹ chỉ lùi tầng mà `_written_floor` đã cộng một vì thứ tự tiếng Anh (`floor_ordinal`): tên hai ngôn ngữ
    `TẦNG 2 / SECOND FLOOR PLAN` lấy số từ phần tiếng Việt nên giữ nguyên.
    """
    out: dict[str, int] = {}
    for v in views:
        t = _written_floor(v)
        if t is None:
            continue
        n = _norm(v)
        ordinal = v.title is not None and v.title.numbering == "floor_ordinal"
        if conv == "tret_plus_tang1" and re.search(r"\bTANG\s*0?\d", n) and not re.search(_GROUND_WORD, n):
            t += 1
        elif conv == "us_english" and ordinal and _ENG.search(n):
            t -= 1
        out[v.view_id] = t
    return out


def _level_ok(views: Sequence[PlanView], st: dict[str, int]) -> bool | None:
    """None khi < 2 mặt bằng có cả số và cốt; ngược lại: số tăng theo cốt và mặt bằng gần 0 nhất là tầng 1."""
    pairs = []
    for v in views:
        d = dominant(v.levels)
        if v.view_id in st and d is not None:
            pairs.append((d, st[v.view_id]))
    its = sorted(pairs)
    if len(its) < 2:
        return None
    for i, a in enumerate(its):
        for b in its[i + 1 :]:
            if (b[1] <= a[1]) if b[0] - a[0] > _MERGE_STOREY else (b[1] != a[1]):
                return False
    near = min(its, key=lambda x: abs(x[0]))
    return near[1] == 1 or abs(near[0]) > _NEAR0


def normalise_convention(views: Sequence[PlanView]) -> tuple[str, bool | None, dict[str, int]]:
    """(quy ước, kết quả kiểm theo cốt, {mã: số tầng theo tên}); quy ước sai với cốt thì đổi sang cái khớp."""
    chosen = _decide(views)
    cands: list[tuple[str, dict[str, int]]] = []
    for conv in (chosen, *(c for c in ("unchanged", "tret_plus_tang1", "us_english") if c != chosen)):
        st = _storeys(views, conv)
        if all(st != s for _, s in cands):
            cands.append((conv, st))
    conv, st = cands[0]
    ok = _level_ok(views, st)
    if ok is False:
        alt = next(((c, s) for c, s in cands[1:] if _level_ok(views, s)), None)
        if alt:
            (conv, st), ok = alt, True
    return conv, ok, st


def _unchecked_convention(views: Sequence[PlanView]) -> set[str]:
    """Mã các mặt bằng mà số tầng chỉ đổi vì quy ước bộ khác mặc định, khi quy ước đó không kiểm được bằng
    cốt (< 2 mặt bằng có số và cốt). Tầng của chúng phải dưới 0,7 để hạ nguồn hỏi (soát mã F1)."""
    conv, ok, st = normalise_convention(views)
    if conv == "unchanged" or ok is not None:
        return set()
    base = _storeys(views, "unchanged")
    return {k for k, f in st.items() if base.get(k) != f}


# ---------------------------------------------------------------- thứ tự cốt (level_floors)
def _number_clusters(vals: list[float], titled: set[int]) -> list[float]:
    """Cụm cốt tăng dần -> số tầng (lửng = số dưới + 0,5); cụm gần 0 nhất (|v| <= 0,6) là tầng 1."""
    near = [i for i, v in enumerate(vals) if abs(v) <= _NEAR0]
    g = min(near, key=lambda i: abs(vals[i])) if near else 0
    nums: dict[int, float] = {i: float(g - i) for i in range(g)}
    kept, mezz = [g], []
    for i in range(g + 1, len(vals)):
        while kept and vals[i] - vals[kept[-1]] < _MEZZ_GAP:
            lo = kept[-1]
            if lo != g and lo not in titled and i in titled:  # cụm không có tên tầng là lửng
                mezz.append(kept.pop())
            else:
                mezz.append(i)
                break
        else:
            kept.append(i)
    for n, i in enumerate(kept, 1):
        nums[i] = n
    for i in mezz:
        nums[i] = max((nums[k] for k in kept if vals[k] < vals[i]), default=0) + 0.5
    return [nums[i] for i in range(len(vals))]


def _level_floors(views: Sequence[PlanView], st: dict[str, int]) -> tuple[dict[str, float | None], int, bool]:
    """({mã: số tầng theo thứ tự cốt}, số cụm, có cụm gần 0) cho mặt bằng có số."""
    use = [v for v in views if _numbered(v)]
    low = min((st[v.view_id] for v in use if v.view_id in st), default=None)
    dom = {v.view_id: dominant(v.levels, low is not None and st.get(v.view_id) == low) for v in use}
    groups = _merge([d for d in dom.values() if d is not None], _MERGE_STOREY)
    vals = [sum(g) / len(g) for g in groups]
    idx = {d: i for i, g in enumerate(groups) for d in g}
    titled = {idx[d] for k, d in dom.items() if d is not None and k in st}
    nums = _number_clusters(vals, titled) if vals else []
    out: dict[str, float | None] = {k: (nums[idx[d]] if d is not None else None) for k, d in dom.items()}
    return out, len(vals), any(abs(v) <= _NEAR0 for v in vals)


def _row_of(
    t: int | None, lv: float | None, level_conf: float, tcap: float, agree_others: int
) -> tuple[float | None, float, str]:
    """Bảng kết hợp tên + thứ tự cốt của `assign_storeys` cho một mặt bằng."""
    if t is not None and lv is not None and t == lv:
        return t, 0.92, "title+level"
    if t is not None and lv is None:
        return t, tcap, "title"
    if t is None and lv is not None:
        return lv, level_conf, "level"
    if t is None:
        return None, 0.0, "none"
    return (lv, 0.7, "level>title") if agree_others >= 2 else (t, 0.6, "title>level")


def _rows(views: Sequence[PlanView]) -> dict[str, _Row]:
    """Tầng theo tên (quy ước của bộ) + thứ tự cốt cho mọi mặt bằng có số (`storey_rows` của kernel)."""
    _, ok, st = normalise_convention(views)
    lv, ncl, ground = _level_floors(views, st)
    tcap = {True: 0.85, False: 0.6, None: 0.8}[ok]
    level_conf = 0.75 if ncl >= 2 and ground else 0.55
    out: dict[str, _Row] = {}
    for v in views:
        if not (_numbered(v) or v.title is None):
            continue
        t, lf = st.get(v.view_id), lv.get(v.view_id)
        others = {st[k] for k in st if k != v.view_id and st[k] == lv.get(k)}
        f, c, src = _row_of(t, lf, level_conf, tcap, len(others))
        if ok is False and "title" in src:
            c = min(c, 0.6)
        floor = int(f) if f is not None and f == int(f) and f >= 1 else None
        out[v.view_id] = _Row(floor, round(min(c, _KIND_CONF), 3), src, t)
    return out


def _title_storey(row: _Row) -> int | None:
    """Tầng đọc từ tên: câu trả lời dựa trên tên, hoặc tầng theo tên khi cốt đã lật nó (level>title)."""
    if row.source == "level>title":
        return row.title_floor if row.title_floor is not None and row.title_floor >= 1 else None
    return row.floor if row.source in ("title", "title+level", "title>level") else None


# ---------------------------------------------------------------- dấu hiệu (storey_cues)
_BASEMENT_WORD = re.compile(r"\bHAM\b|BASEMENT|CELLAR|UNDERGROUND|\bLUNG\b|MEZZ")
_ORD_WORD = re.compile(r"\b(" + "|".join(_ORD) + r")\s+FLOOR")
Cue = list[tuple[int, float]]


def _relaxed(n: str) -> tuple[int, str] | None:
    """(số tầng quy ước mặc định, cách viết "lau" | "tang" | "ordinal" | "ground") trong chữ đã gấp, hoặc None."""
    if _BASEMENT_WORD.search(n):
        return None
    for pattern, add, how in _RELAXED:
        m = pattern.search(n)
        if m and 0 < int(m.group(1)) < 30:
            return int(m.group(1)) + add, how
    if re.search(r"TRET|TRIET|GROUND", n):
        return 1, "ground"
    m = _ORD_WORD.search(n)
    return (_ORD[m.group(1)], "ordinal") if m else None


def floor_relaxed(n: str, conv: str = "unchanged") -> int | None:
    """Số tầng trong một chữ đã gấp, nới biên phải ("BANGTRET", "LAU1"); None nếu không có.

    `conv` là quy ước của BỘ (`normalise_convention`), áp như `_storeys` áp cho tên: bộ trệt + tầng 1
    thì "TANG N" = N + 1, bộ "first floor" kiểu Mỹ thì tầng thứ tự tiếng Anh lùi một.
    """
    r = _relaxed(n)
    if r is None:
        return None
    f, how = r
    if conv == "tret_plus_tang1" and how == "tang":
        return f + 1
    return f - 1 if conv == "us_english" and how == "ordinal" else f


def _title_like(n: str) -> bool:
    """Dòng chữ (đã gấp) trông như tên hình: nêu loại bản vẽ (MAT BANG/PLAN, hoặc `parse_title` nhận ra mặt
    bằng) và không phải mô tả công trình hay ghi chú thang/mặt cắt (`_NOT_TITLE`)."""
    if not n or _NOT_TITLE.search(n):
        return False
    if _PLANISH.search(n):
        return True
    info = parse_title(n)
    return info is not None and info.kind == "plan"


def _cue_words(view: PlanView, conv: str = "unchanged") -> Cue:
    """Từ chỉ tầng ở các dòng TRÔNG NHƯ TÊN HÌNH (`_title_like`), đọc theo quy ước `conv` của bộ (nên tên của
    chính hình không bỏ phiếu chống lại nó); dòng có MAT BANG/PLAN nặng hơn, ghi chú cấu tạo nhẹ hơn.
    Cùng họ chữ với tên: xác nhận hay chống tên, không bao giờ tự thành phiếu mạnh (`_is_strong`)."""
    votes: dict[int, float] = {}
    for text in view.texts or (_norm(view),):
        n = fold(text)
        f = floor_relaxed(n, conv) if _title_like(n) else None
        if f is not None:
            w = 1.0 if _PLANISH.search(n) else 0.3 if _NONSTOREY.search(n) else 0.6
            votes[f] = max(votes.get(f, 0.0), w)
    tot = sum(votes.values())
    return [(f, 0.8 * w / tot) for f, w in votes.items()]


def _cue_level_rank(v: float, dom: dict[str, float | None]) -> Cue:
    """Không có neo: hạng cốt trong các mặt bằng, khi cốt thấp nhất là 0."""
    vals = sorted({round(x, 1) for x in dom.values() if x is not None and x > -0.3})
    return [(vals.index(round(v, 1)) + 1, 0.4)] if round(v, 1) in vals and abs(vals[0]) <= 0.15 else []


def storeys_apart(diff_m: float) -> int | None:
    """Số tầng giữa hai cốt cách nhau `diff_m` (m), mỗi tầng 2,6-4,2 m; None khi không số nào hoặc hơn một số
    khớp (7,2 m = 2 tầng; 8,0 m mơ hồ 2/3; 1,8 m không phải tầng). Điều này cốt nói được mà tên không."""
    ks = [k for k in range(1, _SPAN_CAP + 1) if _STOREY_H[0] * k <= diff_m <= _STOREY_H[1] * k]
    return ks[0] if len(ks) == 1 else None


def _span(lo: int, hi: int, n: int) -> range:
    """Tầng lo..hi; rỗng khi dải rộng hơn số mặt bằng `n` (hoặc `_SPAN_CAP`): dải đó không nói gì mà tốn việc."""
    return range(lo, hi + 1) if 0 <= hi - lo < min(n, _SPAN_CAP) else range(0)


def _cue_level(vid: str, dom: dict[str, float | None], anchors: dict[str, int]) -> Cue:
    """Tầng từ cốt trội của hình so với cốt trội của các mặt bằng neo ({mã: tầng})."""
    v = dom.get(vid)
    if v is None:
        return []
    by_floor: dict[int, list[float]] = defaultdict(list)
    for a, f in anchors.items():
        d = dom.get(a)
        if a != vid and d is not None:
            by_floor[f].append(d)
    known = {f: sorted(x)[len(x) // 2] for f, x in by_floor.items()}
    for f, lv in known.items():
        if abs(lv - v) <= 0.15:
            return [(f, 0.7)]
    if abs(v) <= 0.15 and 1 not in known:
        return [(1, 0.6)]
    if not known:
        return _cue_level_rank(v, dom)
    below = [f for f, lv in known.items() if lv < v]
    above = [f for f, lv in known.items() if lv > v]
    if not above:
        # Trên mọi neo: cách neo cao nhất bao nhiêu tầng do chênh cốt nói, không phải "ngay trên". Dưới ngưỡng
        # mạnh: cốt trội của neo có thể là chiếu nghỉ, nên phiếu này không tự tạo bất đồng (đã có `_gap_check`).
        k = storeys_apart(v - known[max(below)])
        return [(max(below) + k, 0.4)] if k else []
    lo, hi = (max(below) + 1 if below else 1), min(above) - 1
    span = _span(lo, hi, len(dom))
    return [(f, 0.5 / len(span)) for f in span]


def _cue_order(vid: str, ids: list[str], anchors: dict[str, int]) -> Cue:
    """Tầng nằm giữa hai mặt bằng neo gần nhất trước và sau theo thứ tự trong bộ."""
    i = ids.index(vid)
    prev = next((anchors[x] for x in reversed(ids[:i]) if x in anchors), None)
    nxt = next((anchors[x] for x in ids[i + 1 :] if x in anchors), None)
    if nxt is None:
        return [(prev, 0.3), (prev + 1, 0.3)] if prev is not None else []
    if prev is None:
        return [(nxt, 0.3), (nxt - 1, 0.3)] if nxt >= 2 else [(nxt, 0.3)]
    lo, hi = sorted((prev, nxt))
    span = _span(lo, hi, len(ids))
    return [(f, 0.6 / len(span)) for f in span]


def _cue_elim(vid: str, anchors: dict[str, int], n: int = _MAX_VIEWS) -> Cue:
    """Tầng 1..đỉnh chưa mặt bằng neo nào giữ (đỉnh + 1 khi không còn chỗ trống); không gì khi số chỗ trống
    vượt số mặt bằng `n` (hoặc `_SPAN_CAP`)."""
    if vid in anchors:
        return []
    top = max(anchors.values(), default=0)
    used = set(anchors.values())
    if top - len(used) > min(n, _SPAN_CAP):  # neo đều >= 1 nên top - len(used) là số chỗ trống
        return []
    free = [f for f in range(1, top + 1) if f not in used] or [top + 1]
    return [(f, 0.5 / len(free)) for f in free]


def _cue_rank(views: Sequence[PlanView], anchors: dict[str, int]) -> dict[str, Cue]:
    """>= 2 mặt bằng chưa neo cùng một tên không số ("MAT BANG TANG"): đánh số theo thứ tự vào các tầng trống."""
    groups: dict[str, list[str]] = defaultdict(list)
    for v in views:
        n = _norm(v)
        if v.view_id not in anchors and n and _PLANISH.search(n) and floor_relaxed(n) is None:
            groups[re.sub(r"[^A-Z ]", "", n).strip()].append(v.view_id)
    out: dict[str, Cue] = {}
    used = set(anchors.values())
    for ids in groups.values():
        if len(ids) < 2:
            continue
        free = (f for f in count(1) if f not in used)  # lười: chỉ sinh đủ len(ids) tầng trống
        for vid, f in zip(ids, free, strict=False):
            out[vid] = [(f, 0.6)]
    return out


def _candidates(
    plans: Sequence[PlanView], rows: dict[str, _Row], order_known: bool, conv: str = "unchanged"
) -> dict[str, list[tuple[str, int, float]]]:
    """{mã: [(nguồn, tầng, điểm)]} cho mọi mặt bằng có số hoặc chưa rõ tên; chưa gộp; `conv` = quy ước bộ."""
    anchors = {v.view_id: f for v in plans if (f := rows[v.view_id].floor) is not None}
    ids = [v.view_id for v in plans]
    dom = {v.view_id: dominant(v.levels, near_zero=True) for v in plans}
    rank = _cue_rank(plans, anchors) if order_known else {}
    out: dict[str, list[tuple[str, int, float]]] = {}
    for v in plans:
        own = {k: f for k, f in anchors.items() if k != v.view_id}
        cues = {
            "words": _cue_words(v, conv),
            "level": _cue_level(v.view_id, dom, own),
            "order": _cue_order(v.view_id, ids, own) if order_known else [],
            "elim": _cue_elim(v.view_id, anchors, len(plans)),
            "rank": rank.get(v.view_id, []),
        }
        out[v.view_id] = [(s, f, round(x, 3)) for s, c in cues.items() for f, x in c]
    return out


# ---------------------------------------------------------------- gộp theo họ (solve_set bước 2-4)
def _is_strong(source: str, score: float) -> bool:
    """Phiếu mạnh cho độ tin; phiếu chữ ("words") không bao giờ tự mạnh (soát mã F2), chỉ chống được tên."""
    return source != "words" and score >= _STRONG.get(source, 9.0)


def _strong_fams(src: dict[str, float]) -> set[Family]:
    """Họ có ít nhất một phiếu mạnh."""
    return {_FAMILY[s] for s, x in src.items() if _is_strong(s, x)}


def _unary(src: dict[str, float]) -> float:
    """Điểm một lựa chọn tầng: điểm tốt nhất mỗi họ cộng lại + thưởng mỗi họ mạnh cùng ý thêm."""
    fam: dict[Family, float] = defaultdict(float)
    for s, x in src.items():
        fam[_FAMILY[s]] = max(fam[_FAMILY[s]], x)
    return sum(fam.values()) + _AGREE * max(len(_strong_fams(src)) - 1, 0)


def _ground_ids(rows: dict[str, _Row], cands: dict[str, list[tuple[str, int, float]]]) -> set[str]:
    """Mặt bằng mà chữ đặt ở tầng 1: tên (theo quy ước của bộ), hoặc phiếu chữ/hạng mạnh ở tầng 1 khi hình
    không có tầng theo tên. Kernel xét cả phiếu chữ của hình có tên, nên "TANG 1" của bộ trệt + tầng 1 (= tầng 2)
    làm mất neo; ở đây tên thắng chữ như mọi nơi khác."""
    low = {k for k, r in rows.items() if _title_storey(r) == 1}
    untitled = {k for k, r in rows.items() if _title_storey(r) is None}
    strong = {
        k for k, cs in cands.items() if any(f == 1 and s in ("words", "rank") and x >= _STRONG[s] for s, f, x in cs)
    }
    return low | (strong & untitled)


def _dominants(views: Sequence[PlanView]) -> dict[str, float]:
    """{mã: cốt trội} của các hình có cốt."""
    return {v.view_id: d for v in views if (d := dominant(v.levels)) is not None}


def _anchored(dom: dict[str, float], ground: set[str]) -> bool:
    """Neo: mọi mặt bằng chữ đặt ở tầng 1 (có cốt) đều nằm trên cụm cốt thấp nhất của bộ (ít nhất một)."""
    att = [dom[k] for k in ground if k in dom]
    return bool(att) and all(d - min(dom.values()) <= _MERGE_STOREY for d in att)


def _level_order(dom: dict[str, float], ground: set[str], anchored: bool) -> dict[str, int]:
    """Bậc thang cốt của cả bộ (thay chuỗi cốt `levels_chain` của kernel): {mã: tầng}.

    Cốt trội của MỌI mặt bằng (cả lửng, mái) gom 0,25 m thành bậc; bậc của mặt bằng trệt là tầng 1, mỗi
    bậc trên cộng số tầng mà chênh cốt nói (`storeys_apart`: 7,2 m = hai tầng), dừng ở chênh mơ hồ. Chỉ
    dùng khi chuỗi được neo. Một bậc thừa (lửng, chiếu nghỉ ghi cốt nhiều như sàn) đẩy các tầng trên lệch
    một bậc: đó là bất đồng mà kernel ghi ở DS-254.
    """
    if not anchored:
        return {}
    groups = _merge(list(dom.values()), _MERGE_STOREY)
    idx = {d: i for i, g in enumerate(groups) for d in g}
    mean = [sum(g) / len(g) for g in groups]
    base = min(idx[dom[k]] for k in ground if k in dom)
    nums = {base: 1}
    for i in range(base + 1, len(groups)):
        k = storeys_apart(mean[i] - mean[i - 1])
        if k is None:
            break
        nums[i] = nums[i - 1] + k
    return {k: nums[idx[d]] for k, d in dom.items() if idx[d] in nums}


def _family_scores(row: _Row, cands: list[tuple[str, int, float]], anchored: bool, ladder: int | None) -> Votes:
    """{tầng: {nguồn: điểm}} của một mặt bằng: tên, câu trả lời theo cốt, các dấu hiệu, bậc cốt."""
    d: Votes = defaultdict(dict)
    t = _title_storey(row)
    if t is not None:
        d[t]["title"] = 0.6 + 0.4 * row.confidence
    if row.floor is not None and row.floor != t:
        d[row.floor]["level"] = max(0.5, row.confidence)
    for s, f, x in cands:
        if s == "level" and x in _BOTTOM_LEVEL and not anchored:
            continue
        d[f][s] = max(d[f].get(s, 0.0), x)
    if ladder is not None:
        d[ladder]["level_order"] = _LADDER_SCORE
    return {f: v for f, v in d.items() if f >= 1}


def _disagreeing(opts: Votes, f: int) -> tuple[StoreyConflict, ...]:
    """Nguồn mạnh có tầng tốt nhất khác `f` và không có phiếu nào ở `f` (tính theo nguồn)."""
    best: dict[str, tuple[float, int]] = {}
    for g, src in opts.items():
        for s, x in src.items():
            if x >= _STRONG.get(s, 9.0) and x > best.get(s, (0.0, 0))[0]:
                best[s] = (x, g)
    here = set(opts.get(f, {}))
    return tuple(StoreyConflict(s, _FAMILY[s], g) for s, (_, g) in sorted(best.items()) if g != f and s not in here)


def _rival_titles(view: PlanView, f: int, conv: str) -> tuple[StoreyConflict, ...]:
    """Tên hình thứ hai trên cùng hình (`_TITLE_HEAD`) nêu tầng khác `f`. Phiếu chữ đã chia theo tổng nên hai tên
    ngang nhau thành hai phiếu dưới ngưỡng mạnh và `_disagreeing` không thấy; ở đây xét từng dòng."""
    rivals: set[int] = set()
    for text in view.texts:
        n = fold(text)
        if _TITLE_HEAD.search(n) and _title_like(n) and (g := floor_relaxed(n, conv)) is not None and g != f:
            rivals.add(g)
    return tuple(StoreyConflict("words", "text", g) for g in sorted(rivals))


def storey_confidence(src: dict[str, float], alt_far: bool, conflict: bool = False, anchored: bool = True) -> float:
    """Hai họ mạnh (cốt phải được neo), không bất đồng >= 0,9; một họ mạnh 0,72-0,85; bất đồng 0,65;
    chỉ yếu 0,55; nhì sát nút <= 0,7."""
    strong = _strong_fams(src)
    if conflict:
        return _CONFLICT if src else 0.0
    if len(strong) >= 2:
        c = (0.95 if "text" in strong else 0.9) if anchored else 0.85
    elif strong:
        best = max(x for s, x in src.items() if _is_strong(s, x))
        c = 0.85 if src.get("title", 0.0) >= 0.9 else min(0.85, max(0.72, best))
    else:
        c = 0.55 if src else 0.0
    return c if alt_far else min(c, 0.7)


# ---------------------------------------------------------------- chọn tổ hợp
Assign = dict[str, int | None]


def _holes(assign: Assign) -> int:
    """Số tầng thiếu giữa 1 và tầng cao nhất của một tổ hợp."""
    used = {f for f in assign.values() if f is not None}
    return (max(used) - len(used)) if used else 0


def _beam(plans: list[tuple[str, Votes]]) -> list[tuple[float, Assign]]:
    """[(điểm, {mã: tầng})]; mỗi mặt bằng xét tối đa 4 tầng điểm cao nhất, giữ 200 tổ hợp."""
    states: list[tuple[float, Assign]] = [(0.0, {})]
    for vid, opts in plans:
        ranked = sorted(opts.items(), key=lambda kv: -_unary(kv[1]))[:_PER_PLAN]
        choices: list[tuple[int | None, dict[str, float]]] = [*ranked] or [(None, {})]
        nxt = [(sc + (_unary(src) if f is not None else 0.0), {**a, vid: f}) for sc, a in states for f, src in choices]
        states = sorted(nxt, key=lambda x: -x[0])[:_BEAM]
    return [(sc - _HOLE * _holes(a), a) for sc, a in states]


def _picked(assign: Assign, views: dict[str, PlanView]) -> dict[int, str]:
    """{tầng: mã mặt bằng đại diện}: bản thường trước nội thất, nhiều cốt hơn, mã nhỏ hơn."""
    best: dict[int, tuple[tuple[int, int, str], str]] = {}
    for vid, f in assign.items():
        if f is None:
            continue
        t = views[vid].title
        key = (0 if t is None or t.variant == "plain" else 1, -len(views[vid].levels), vid)
        if f not in best or key < best[f][0]:
            best[f] = (key, vid)
    return {f: x[1] for f, x in best.items()}


def _choose(plans: list[tuple[str, Votes]], views: dict[str, PlanView]) -> tuple[Assign, Assign | None, float]:
    """(tổ hợp tốt nhất, tổ hợp nhì có đại diện khác hoặc None, khoảng cách điểm)."""
    scored: list[tuple[float, Assign]] = []
    seen: list[dict[int, str]] = []
    for sc, a in sorted(_beam(plans), key=lambda x: -x[0])[:12]:
        p = _picked(a, views)
        if p not in seen:
            seen.append(p)
            scored.append((sc, a))
    if len(scored) < 2:
        return scored[0][1], None, 9.0
    return scored[0][1], scored[1][1], scored[0][0] - scored[1][0]


# ---------------------------------------------------------------- kết quả
def _explain(view: PlanView, full: Votes, f: int, close: bool, anchored: bool, conv: str) -> StoreyChoice:
    """Tầng đã chọn + độ tin, họ ủng hộ, phương án nhì, bất đồng (`conv` = quy ước bộ, cho tên thứ hai)."""
    src = full.get(f, {})
    conflict = _disagreeing(full, f)
    conflict += tuple(c for c in _rival_titles(view, f, conv) if c not in conflict)
    c = storey_confidence(src, not close, bool(conflict), anchored)
    t = view.title
    if t is not None and t.numbering == "floor_ordinal" and "title" in src:
        c = min(c, _ORDINAL_CAP)  # "4th floor": Mỹ đếm trệt là 1, Anh đếm là 0 -> một cách đọc mơ hồ
    alts = sorted(((g, _unary(s)) for g, s in full.items() if g != f), key=lambda x: -x[1])[:1]
    alt = Alternative(alts[0][0], round(alts[0][1], 3)) if alts else None
    fams = tuple(sorted({_FAMILY[s] for s in src}))
    return StoreyChoice("ground" if f == 1 else "floor", f, round(c, 3), fams, alt, conflict)


def _basement(norm: str) -> StoreyChoice:
    """Hầm sâu N (1.._MAX_BASEMENT, nhiều chữ số được) -> số 1 - N ở 0,8; không ghi độ sâu = hầm duy nhất (0);
    độ sâu ngoài khoảng hoặc không đọc được -> hầm không số, độ tin < 0,7."""
    m = _BASEMENT_NO.search(norm)
    digits = (m.group(1) or m.group(2)) if m else "0" if _BASEMENT_TAG.search(norm) else "1"
    depth = int(digits) if len(digits) <= 2 else 0
    if 1 <= depth <= _MAX_BASEMENT:
        return StoreyChoice("basement", 1 - depth, 0.8, ("text",), None, ())
    return StoreyChoice("basement", None, _UNSURE_BASEMENT, ("text",), None, ())


def _word_choice(view: PlanView, top: int) -> StoreyChoice:
    """Hầm 0, -1 theo số hầm (0,8); lửng không số (0,6); tum/sân thượng = tầng cao nhất + 1, tối đa 0,7
    (quy ước đáp án tính nó là tầng khi có phòng, tên không cho biết điều đó); còn lại không rõ."""
    t = view.title
    if t is None:
        return _NONE
    if t.storey == "basement":
        return _basement(t.norm)
    if t.storey == "mezzanine":
        return StoreyChoice("mezzanine", None, 0.6, ("text",), None, ())
    if t.storey in ("tum", "attic", "roof_terrace"):
        return StoreyChoice(t.storey, top + 1, _ROOF_STOREY_CAP, ("text",), None, ())
    return _NONE


# ---------------------------------------------------------------- kiểm cả bộ sau khi chọn (soát mã F1-F3)
def _plain(view: PlanView) -> bool:
    """Bản thường (hoặc chưa rõ tên): hai bản như vậy cùng tầng không phải biến thể của nhau."""
    return view.title is None or view.title.variant == "plain"


def _main_marks(levels: Sequence[float]) -> list[float]:
    """Tối đa 8 cốt hay gặp nhất của một hình (chặn khối lượng việc của phép thử khoảng cách)."""
    return [v for v, _ in Counter(round(x, 2) for x in levels).most_common(8)]


def _gap_fits(lo: Sequence[float], hi: Sequence[float], df: int) -> bool:
    """Có cốt của hình dưới và hình trên mà (chênh cốt) / (chênh số tầng `df`) nằm trong 2,4-4,6 m: cốt trội
    có thể là chiếu nghỉ (C4 của soát mã) nên xét các cốt chính, không chỉ cốt trội."""
    return any(_GAP_OK[0] <= (b - a) / df <= _GAP_OK[1] for a in lo for b in hi)


def _gap_check(
    nums: dict[str, int], dom: dict[str, float], marks: dict[str, list[float]]
) -> tuple[set[int], dict[int, int]]:
    """(tầng qua phép thử khoảng cách ở mọi phía, {tầng trên của cặp hỏng: tầng mà chênh cốt trội chỉ}).

    Cặp tầng có cốt kề nhau: (chênh cốt) / (chênh số tầng) phải trong 2,4-4,6 m, nếu không họ cốt chỉ là thứ
    tự chứ không xác nhận số. Tầng qua khi có ít nhất một cặp và mọi cặp của nó đều qua; tầng 1 ở cốt
    +-0.000 không hàng xóm nào có cốt cũng qua (mốc tuyệt đối). Cốt của một tầng: mã đầu tiên trong `nums`.
    """
    rep: dict[int, str] = {}
    for vid, f in nums.items():
        if vid in dom:
            rep.setdefault(f, vid)
    lv = {f: dom[k] for f, k in rep.items()}
    fs = sorted(lv)
    pairs = list(pairwise(fs))
    bad = {
        b: a + round((lv[b] - lv[a]) / (sum(_STOREY_H) / 2))
        for a, b in pairs
        if not _gap_fits(marks[rep[a]], marks[rep[b]], b - a)
    }
    broken = set(bad) | {a for a, b in pairs if b in bad}
    datum = {1} if fs == [1] and abs(lv[1]) <= _DATUM else set()
    return {f for p in pairs for f in p} - broken | datum, bad


def _keeper(ids: list[str], nums: dict[str, int], dom: dict[str, float], marks: dict[str, list[float]]) -> str | None:
    """Bản giữ số khi các bản thường trùng tầng có cốt lệch > 0,5 m: bản duy nhất ít cặp hỏng nhất với
    hàng xóm (`_gap_check`); None khi cốt thiếu, gần nhau hoặc không bản nào khớp hơn."""
    lv = [dom[k] for k in ids if k in dom]
    if len(lv) < len(ids) or max(lv) - min(lv) <= _DUP_LEVEL:
        return None
    rest = {k: f for k, f in nums.items() if k not in ids}
    bad = {k: len(_gap_check({k: nums[k], **rest}, dom, marks)[1]) for k in ids}
    winners = [k for k, b in bad.items() if b == min(bad.values())]
    return winners[0] if len(winners) == 1 else None


def _duplicates(
    nums: dict[str, int], dom: dict[str, float], marks: dict[str, list[float]], by_id: dict[str, PlanView]
) -> tuple[set[str], set[str]]:
    """(bản mất số, bản giữ số nhưng bất đồng) khi >= 2 bản thường (không là biến thể của nhau) cùng tầng."""
    groups: dict[int, list[str]] = defaultdict(list)
    for vid, f in nums.items():
        if _plain(by_id[vid]):
            groups[f].append(vid)
    dropped: set[str] = set()
    clash: set[str] = set()
    for ids in groups.values():
        keep = _keeper(ids, nums, dom, marks) if len(ids) > 1 else ids[0]
        if keep is None:
            clash |= set(ids)
        else:
            dropped |= set(ids) - {keep}
    return dropped, clash


def _flag(c: StoreyChoice, source: str, family: Family, number: int) -> StoreyChoice:
    """Thêm một bất đồng của cả bộ; độ tin tối đa 0,65."""
    return replace(
        c, confidence=min(c.confidence, _CONFLICT), conflict=(*c.conflict, StoreyConflict(source, family, number))
    )


def _set_checks(out: dict[str, StoreyChoice], views: Sequence[PlanView]) -> None:
    """Kiểm cả bộ sau khi chọn tầng, sửa `out` tại chỗ: bản thường trùng tầng ("duplicate_storey"), khoảng
    cách cốt sai với chênh số tầng ("level_gap", họ cốt không tính cho >= 0,9), quy ước bộ không kiểm được."""
    by_id = {v.view_id: v for v in views}
    dom = _dominants(views)
    marks = {v.view_id: _main_marks(v.levels) for v in views}
    keyed = sorted(
        (k for k, c in out.items() if c.label in ("ground", "floor")), key=lambda k: (not _plain(by_id[k]), k)
    )
    nums = {k: n for k in keyed if (n := out[k].number) is not None}
    dropped, clash = _duplicates(nums, dom, marks, by_id)
    for k in sorted(dropped):
        out[k] = _flag(replace(out[k], label=None, number=None), "duplicate_storey", "order", nums.pop(k))
    for k in clash:
        out[k] = _flag(out[k], "duplicate_storey", "order", nums[k])
    passed, bad = _gap_check(nums, dom, marks)
    for k, f in nums.items():
        if f in bad:
            out[k] = _flag(out[k], "level_gap", "level", bad[f])
        elif f not in passed:
            out[k] = replace(out[k], confidence=min(out[k].confidence, _TEXT_ONLY_CAP))
    for k in _unchecked_convention(views) & set(nums):
        out[k] = replace(out[k], confidence=min(out[k].confidence, _UNCHECKED_CAP))


def solve_storeys(views: Sequence[PlanView], order_known: bool = True) -> dict[str, StoreyChoice]:
    """{mã: StoreyChoice} cho mọi mặt bằng của MỘT bộ (gọi một lần cho cả bộ: dấu hiệu nhìn hàng xóm).

    `order_known=False` khi vị trí trong bộ không chắc: tắt dấu hiệu thứ tự và đánh số theo thứ tự
    (kernel đo được thứ tự sai sinh lỗi tự tin). Quá `_MAX_VIEWS` hình trong một lần gọi: mọi hình
    không tầng, độ tin 0 (chặn khối lượng việc; một nhà thật không có ngần ấy mặt bằng).
    """
    if len(views) > _MAX_VIEWS:
        return {v.view_id: _NONE for v in views}
    # Tên "mặt bằng" mà thật ra là ghi chú thang hay mô tả công trình ("LÊN TẦNG 3") không nói tầng.
    ordered = [
        replace(v, title=None) if v.title and v.title.kind == "plan" and not _title_like(v.title.norm) else v
        for v in sorted(views, key=lambda v: (v.position, v.view_id))
    ]
    by_id = {v.view_id: v for v in ordered}
    rows = _rows(ordered)
    plans = [v for v in ordered if v.view_id in rows]
    conv = normalise_convention(ordered)[0]
    cands = _candidates(plans, rows, order_known, conv)
    ground, dom = _ground_ids(rows, cands), _dominants(ordered)
    anchored = _anchored(dom, ground)
    ladder = _level_order(dom, ground, anchored)
    full = {
        v.view_id: _family_scores(rows[v.view_id], cands[v.view_id], anchored, ladder.get(v.view_id)) for v in plans
    }
    todo: list[tuple[str, Votes]] = []
    for v in plans:
        t = _title_storey(rows[v.view_id])
        todo.append((v.view_id, {t: full[v.view_id][t]} if t else full[v.view_id]))  # tên cố định tầng
    best, run, gap = _choose(todo, by_id) if todo else ({}, None, 9.0)
    top = max((f for f in best.values() if f is not None), default=0)
    out: dict[str, StoreyChoice] = {}
    for v in ordered:
        f = best.get(v.view_id)
        if f is None:
            out[v.view_id] = _word_choice(v, top)
            continue
        close = run is not None and gap < _CLOSE and run.get(v.view_id) != f
        out[v.view_id] = _explain(v, full[v.view_id], f, close, anchored, conv)
    _set_checks(out, ordered)
    return out
