"""Phân loại trang bản vẽ: mặt bằng / mặt cắt / mặt đứng / mái / khu đất / chi tiết / bìa / khác (K-14).

Hàm thuần, chỉ thư viện chuẩn: nhận chữ OCR cả trang và (nếu có) đặc trưng hình học, trả loại + độ tin + lý do.
Gương `classify()` của `F:/runs/dwglab/muc3/kernel/classify.py` (tên hình quyết loại, nội dung chỉ nâng/hạ độ tin),
dùng lại `titles.pick_title`/`parse_title` của HS-01 cho phần chữ. Số đo: `changes/K-14.md`.

Khác kernel có chủ ý:
 * Dấu hiệu cốt đứng đòi thêm sự khác nhau về giá trị: >= 3 cốt phân biệt (làm tròn 0,1 m), trải >= 2,5 m và
   trải dọc >= 0,35 chiều cao trang. Kernel chỉ đếm >= 3 cốt trải dọc, nên mặt bằng có vài dấu "±0.000" rải rác
   dễ bị coi là mặt cắt.
 * Cầu thang/cửa/cửa sổ/phòng/tường đến từ bộ dò của pipeline (`PageGeometry`), không phải nhãn đáp án như kernel.
   Cầu thang riêng lẻ chỉ là dấu hiệu yếu vì mặt cắt cũng có cầu thang.
 * Thêm loại `cover` (bìa, mục lục, danh mục bản vẽ), nhận từ chữ khi trang ngắn và tên chưa được khung tên
   xác nhận; `site` giữ nguyên là khu đất (kernel ép bản Việt về mặt bằng để khớp đáp án thử).
 * Ảnh không nét theo trục (phối cảnh) mà không có tên hình: `other`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from packages.domain.drawing_set.levels import levels_from_texts
from packages.domain.drawing_set.titles import OcrText, PickedTitle, fold, parse_title, pick_title

PageKind = Literal["plan", "section", "elevation", "roof", "site", "detail", "cover", "other"]

# Độ tin cậy (heuristic, cùng bậc với kernel; chưa hiệu chỉnh trên pipeline, xem NO-512).
CONF_AGREE_STRONG: Final = 0.92  # tên và dấu hiệu mạnh cùng ý
CONF_AGREE_WEAK: Final = 0.85
CONF_TITLE_ONLY: Final = 0.8
CONF_CONFLICT: Final = 0.55  # tên nói một đằng, dấu hiệu mạnh nói đằng khác: vẫn theo tên nhưng không dám chắc
CONF_CONTENT_STRONG: Final = 0.65  # không có tên: trần của mọi kết luận từ nội dung
CONF_CONTENT_WEAK: Final = 0.5
CONF_COVER: Final = 0.75
CONF_VERTICAL_GUESS: Final = 0.45  # không tên, dấu hiệu đứng mạnh: mặt cắt hay mặt đứng không phân biệt được
CONF_VERTICAL_WEAK: Final = 0.3
CONF_NOTHING: Final = 0.1

LEVELS_MIN_DISTINCT: Final = 3  # cốt phân biệt tối thiểu để coi là mặt cắt/mặt đứng
LEVELS_MIN_RANGE_M: Final = 2.5  # khoảng trải của các cốt (một tầng cao >= 2,6 m)
LEVELS_MIN_SPREAD: Final = 0.35  # trải dọc của các cốt, theo chiều cao trang
BUBBLE_GRID_MIN: Final = 4  # bong bóng trục tối thiểu cho "lưới hai chiều"
BUBBLE_GRID_SPREAD: Final = 0.3  # trải ngang và dọc tối thiểu của lưới hai chiều
BUBBLE_ROW_MAX_SPREAD: Final = 0.15  # một hàng: trải dọc nhỏ hơn mức này
FEW_TEXTS: Final = 2  # trang gần như trống: thường là hình đứng chưa đọc ra chữ
MANY_TEXTS: Final = 20
MIN_AXIS_SHARE: Final = 0.65  # gương `apps/worker/pipeline_quality/checks.py`: dưới mức này trông như phối cảnh
CONF_TITLE_UNDER_ONLY: Final = 0.8  # độ tin của `pick_title` khi chỉ có tên dưới hình (không cần khung tên xác nhận)
CONF_TITLE_CONFIRMED: Final = 0.9  # độ tin của `pick_title` khi tên dưới hình và khung tên khớp nhau
COVER_MAX_TEXTS: Final = 60  # bìa/mục lục ít chữ hơn một mặt bằng thật; nhiều hơn thì không phải bìa
LEVEL_DECIMALS: Final = 1
_MIN_BUBBLES_ROW: Final = 2

_COVER: Final = re.compile(
    r"\bMUC LUC\b|\bDANH MUC (?:BAN VE|HO SO|HINH)\b|\bTRANG BIA\b|\bBIA HO SO\b"
    r"|\bCOVER (?:SHEET|PAGE)\b|\bINDEX OF DRAWINGS\b|\bLIST OF DRAWINGS\b|\bDRAWING (?:LIST|INDEX)\b|\bTITLE SHEET\b"
)
_COVER_LINE: Final = re.compile(r"^(?:BIA|COVER)$")  # cả dòng chỉ là "BÌA"

_Vec = tuple[float, float]


@dataclass(frozen=True)
class PageGeometry:
    """Đặc trưng hình học của một trang, do pipeline cấp (None/0 = chưa đo hoặc không có).

    `bubbles`: tâm các bong bóng trục (px). `axis_share`: tỉ lệ nét theo trục trong [0, 1].
    Đếm: tường, phòng kín, ô mở (cửa + cửa sổ), cầu thang.
    """

    axis_share: float | None = None
    bubbles: Sequence[_Vec] = ()
    n_walls: int = 0
    n_rooms: int = 0
    n_openings: int = 0
    n_stairs: int = 0


@dataclass(frozen=True)
class PageKindResult:
    """Loại trang, độ tin trong [0, 1], mã lý do chính và các dấu hiệu đã dùng (mã máy đọc, thứ tự ổn định)."""

    kind: PageKind
    confidence: float
    reason: str
    cues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Cue:
    """Dấu hiệu nội dung: `side` "plan" | "vertical" | None, `strong`, mã lý do."""

    side: Literal["plan", "vertical"] | None
    strong: bool
    code: str


def _span(values: Sequence[float], size: float) -> float:
    """Trải của một dãy toạ độ theo kích thước trang; -1 khi dưới hai điểm."""
    if len(values) < _MIN_BUBBLES_ROW or size <= 0:
        return -1.0
    return (max(values) - min(values)) / size


def _levels_vertical(texts: Sequence[OcrText], height: float) -> bool:
    """Nhiều cốt khác giá trị, trải cao và trải dọc: dấu hiệu của hình đứng (mặt cắt/mặt đứng)."""
    marks = levels_from_texts(texts)
    values = sorted({round(m.value_m, LEVEL_DECIMALS) for m in marks})
    if len(values) < LEVELS_MIN_DISTINCT or values[-1] - values[0] < LEVELS_MIN_RANGE_M:
        return False
    return _span([m.y for m in marks], height) >= LEVELS_MIN_SPREAD


def _plan_objects(geo: PageGeometry) -> _Cue | None:
    """Dấu hiệu mặt bằng từ bộ dò: phòng/ô mở là mạnh, cầu thang hay tường riêng lẻ là yếu."""
    if geo.n_rooms >= 2 or geo.n_openings >= 2:
        return _Cue("plan", True, f"OBJECTS_{geo.n_rooms}R_{geo.n_openings}O")
    if geo.n_rooms or geo.n_openings or geo.n_stairs:
        return _Cue("plan", False, "OBJECTS_FEW")
    return None


def _content_cue(texts: Sequence[OcrText], size: tuple[float, float], geo: PageGeometry, stacked: bool) -> _Cue:
    """Dấu hiệu từ nội dung theo thứ tự ưu tiên của kernel: vật thể, cốt đứng, lưới trục, hàng trục, số chữ.

    `stacked`: kết quả `_levels_vertical` (tính một lần ở người gọi, còn dùng cho luật đè tên)."""
    objects = _plan_objects(geo)
    if objects is not None and objects.strong:
        return objects
    if stacked:  # trước lưới trục: bộ dò bong bóng hay đọc mặt cắt thành lưới hai chiều (kernel: 12/52 hình đứng)
        return _Cue("vertical", True, "LEVELS_VERTICAL")
    bub = geo.bubbles
    dx, dy = _span([b[0] for b in bub], size[0]), _span([b[1] for b in bub], size[1])
    if len(bub) >= BUBBLE_GRID_MIN and dx >= BUBBLE_GRID_SPREAD and dy >= BUBBLE_GRID_SPREAD:
        return _Cue("plan", True, "BUBBLE_GRID")
    if len(bub) >= _MIN_BUBBLES_ROW and 0 <= dy < BUBBLE_ROW_MAX_SPREAD:
        return _Cue("vertical", False, "BUBBLE_ROW")
    if objects is not None:
        return objects
    if len(texts) <= FEW_TEXTS:
        return _Cue("vertical", False, "EMPTY_PAGE")
    if len(texts) >= MANY_TEXTS:
        return _Cue("plan", False, "MANY_TEXTS")
    return _Cue(None, False, "NO_USABLE_CONTENT")


_EXPECT: Final = {"plan": "plan", "roof": "plan", "section": "vertical", "elevation": "vertical"}


def _with_title(kind: PageKind, picked: PickedTitle, cue: _Cue) -> PageKindResult:
    """Tên đọc ra loại hình: đồng ý -> 0,92/0,85; không dấu hiệu -> 0,8; mâu thuẫn mạnh -> 0,55; tên kém tin là trần."""
    expected = _EXPECT.get(kind)
    if expected is None or cue.side is None:
        conf, reason = CONF_TITLE_ONLY, "TITLE_ONLY"
    elif cue.side == expected:
        conf, reason = (
            (CONF_AGREE_STRONG, "TITLE_AND_CONTENT_AGREE") if cue.strong else (CONF_AGREE_WEAK, "TITLE_WEAK_AGREE")
        )
    elif cue.strong:
        conf, reason = CONF_CONFLICT, "TITLE_CONTENT_CONFLICT"
    else:
        conf, reason = CONF_TITLE_ONLY, "TITLE_ONLY"
    if kind == "other":
        conf = min(conf, CONF_TITLE_ONLY)
    cues = (f"TITLE:{kind}", f"SOURCE:{picked.source}", f"CONTENT:{cue.code}")
    if picked.confidence < CONF_TITLE_UNDER_ONLY:  # chỉ khung tên đọc ra, hoặc hai nguồn tên lệch nhau: tên kém tin
        conf = min(conf, picked.confidence)
    return PageKindResult(kind, conf, reason, cues)


def _overridden(title: PickedTitle, geo: PageGeometry) -> PageKindResult:
    """Tên "mặt bằng" chỉ một mình (chưa khung tên nào xác nhận) mà trang có chồng cốt cao: tên thường là ghi chú
    ("xem mặt bằng", "sika floor") bị chọn nhầm. Đứng về nội dung, độ tin thấp (< 0,7: không đủ để cảnh báo)."""
    kind: PageKind = "section" if len(geo.bubbles) >= _MIN_BUBBLES_ROW else "elevation"
    return PageKindResult(
        kind, CONF_CONFLICT, "TITLE_OVERRIDDEN_BY_LEVELS", (f"TITLE:{title.text}", "CONTENT:LEVELS_VERTICAL")
    )


def _is_cover(texts: Sequence[OcrText]) -> bool:
    """Chữ cả trang nói đây là bìa/mục lục/danh mục bản vẽ, và trang không đông chữ như một bản vẽ thật."""
    if len(texts) > COVER_MAX_TEXTS:
        return False
    folded = [fold(t.text) for t in texts]
    return any(_COVER_LINE.match(f) for f in folded) or _COVER.search(" | ".join(folded)) is not None


def _content_only(texts: Sequence[OcrText], geo: PageGeometry, cue: _Cue) -> PageKindResult:
    """Không tên hình: bìa theo chữ, phối cảnh theo nét, rồi dấu hiệu nội dung (trần 0,65, không bao giờ >= 0,7)."""
    cues = (f"CONTENT:{cue.code}",)
    if _is_cover(texts):
        return PageKindResult("cover", CONF_COVER, "COVER_WORDS", ("TEXT:cover",))
    if geo.axis_share is not None and geo.axis_share < MIN_AXIS_SHARE and not (cue.side == "plan" and cue.strong):
        return PageKindResult("other", CONF_CONTENT_WEAK, "LOW_AXIS_SHARE", (*cues, f"AXIS_SHARE:{geo.axis_share:.2f}"))
    if cue.side == "plan":
        return PageKindResult("plan", CONF_CONTENT_STRONG if cue.strong else CONF_CONTENT_WEAK, "CONTENT_ONLY", cues)
    if cue.side == "vertical":
        # hàng trục nghiêng về mặt cắt
        kind: PageKind = "section" if len(geo.bubbles) >= _MIN_BUBBLES_ROW else "elevation"
        return PageKindResult(kind, CONF_VERTICAL_GUESS if cue.strong else CONF_VERTICAL_WEAK, "CONTENT_ONLY", cues)
    return PageKindResult("other", CONF_NOTHING, "NO_TITLE_NO_CONTENT", cues)


def classify_page(
    texts: Sequence[OcrText],
    size: tuple[float, float],
    geometry: PageGeometry | None = None,
    *,
    picked: PickedTitle | None = None,
) -> PageKindResult:
    """Phân loại một trang từ chữ OCR (`size` = rộng, cao ảnh, px) và đặc trưng hình học nếu có.

    Tên hình (`pick_title` rồi `parse_title`) quyết loại; không đọc ra tên thì bìa/phối cảnh/nội dung quyết,
    độ tin tối đa 0,75 (< 0,9 là ngưỡng cảnh báo `PAGE_NOT_PLAN` của K-14). `picked`: người gọi đã chọn tên thì
    truyền vào để khỏi chọn hai lần. Hàm không ném lỗi với đầu vào rỗng (trang không chữ -> `other`, 0,1).
    """
    geo = geometry or PageGeometry()
    stacked = _levels_vertical(texts, size[1])
    cue = _content_cue(texts, size, geo, stacked)
    title = picked if picked is not None else pick_title(texts, size)
    info = parse_title(title.text) if title.text else None
    # tên chưa được khung tên xác nhận mà trang ngắn nói "bìa/mục lục": "TRANG BÌA" bị `pick_title` sửa gần thành "TẦNG"
    if info is None or (title.confidence < CONF_TITLE_CONFIRMED and _is_cover(texts)):
        return _content_only(texts, geo, cue)
    if stacked and info.kind in ("plan", "roof") and title.confidence < CONF_TITLE_CONFIRMED:
        return _overridden(title, geo)
    return _with_title(info.kind, title, cue)
