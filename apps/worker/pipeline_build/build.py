"""Lắp ráp lớp không gian từ ba artifact ML (B5-05 [6] bước 1, 2, 7, 8) và khuôn `layer.json`.

Bước 3-6 nằm ở `geometry.py`, `rooms.py`; module này chỉ lọc đầu vào không tin được, chốt tỉ lệ,
dựng `Dimension`, chạy luật hậu xử lý và đóng gói `BuiltLayer`. Hàm thuần, không I/O, không sửa
đối số: mọi lỗi hợp đồng là `ValueError` để task đổi thành `PIPELINE_BUILD_INVALID`.
"""

import json
import logging
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from fractions import Fraction
from typing import Any, Final, Literal, Self, get_args

import numpy as np
import shapely
from shapely import STRtree

from apps.worker.pipeline_build.arcs import explode_arcs, join_arcs
from apps.worker.pipeline_build.clusters import Box, grow_region, wall_clusters
from apps.worker.pipeline_build.constants import DROPPED_KEYS
from apps.worker.pipeline_build.furniture_dedup import dedupe_furniture
from apps.worker.pipeline_build.geometry import RoomCloser, Scaling, WallSet, build_walls, close_envelope_gaps
from apps.worker.pipeline_build.ids import new_spatial_id
from apps.worker.pipeline_build.residual import PageRaster, residual_ink_boxes
from apps.worker.pipeline_build.rooms import build_furniture, build_rooms
from apps.worker.pipeline_build.swing import settle_reversed
from apps.worker.pipeline_build.wall_runs import merge_wall_runs
from packages.core.clock import Clock
from packages.core.pipeline import PipelineCode
from packages.domain.rules_ai import apply_post_rules
from packages.domain.scale import (
    RescaleError,
    ScaleSample,
    axis_scales,
    classify_scale_range,
    infer_scale,
    rescale_dimensions,
    rescale_unreviewed,
)
from packages.domain.spatial import (
    Dimension,
    Furniture,
    Segment,
    SpatialLayer,
    Wall,
    check_integrity,
    has_critical,
    js_round,
)
from packages.ml_contracts.artifacts import (
    MAX_DETECTIONS,
    MAX_TEXTS,
    MAX_WALLS,
    BoxPx,
    DetectionPx,
    ObjectsResult,
    TextPx,
    TextResult,
    WallPx,
    WallsResult,
)
from packages.vision.dimensions import pair_dimension_lines, parse_length_mm, parse_scale_note, wall_spans
from packages.vision.dimensions.chains import CLUSTER_TOLERANCE, densest_cluster

_log = logging.getLogger(__name__)

SCHEMA_VERSION: Final = 1
"""`layer.json` v1 (B5-05 [2]); số khác là file của bản khác, không đoán mà đọc."""

_QUANTUM: Final = Decimal("0.000001")
_SAMPLE_ID: Final = re.compile(r"^t(\d+)-w(\d+)-s(\d+|all)$")
"""Id mẫu thật của B5-04 (`pairing.py`): `sall` cho cả tường (prompt ghi `all`)."""

_CHAIN_ID: Final = re.compile(r"^t(\d+)-chain$")
"""Id mẫu chuỗi kích thước (`_chain_samples`): chữ có `span_px` đo trên ảnh, không gắn tường."""

_DIMENSION_TOLERANCE: Final = 0.02
_BUILD_SCALE_TARGET_MM: Final = 150.0
"""Bề dày vách mong muốn khi phải tự chọn tỉ lệ dựng (B5-05 [6] bước 2).

Giữ 150 dù bậc tường sáu mức (100/150/200/220/300/400) cho vách trong 100-110: đây là trung vị bề dày **mặt nạ**
(nét mực dày hơn tường thật, mặt bằng trộn vách 100-110 với tường bao 200-220). Bench-3d r1, 14 bản có đáp án: bề dày
dò được ở tỉ lệ đúng có trung vị ≈ 154 mm (95-273); đặt 120 thì ca chuỗi đúng nay ước lệch thêm ~20 %. ch-01 (cần
~139) và lowres-ch06 (cần ~176) lệch hai phía nên một hằng số không chữa được cả hai."""

_ESTIMATE_AGREEMENT: Final = 0.10
"""Ước từ cửa đi và từ bề dày tường lệch ≤ 10 % → trung bình nhân hai ước; lệch hơn → giữ ước cửa (bench DOOR-SCALE:
cửa lệch trung vị 8-10 %, bề dày tường tới 42 %) và ghi `scaleChecks.estimateSpread`."""

_ANISOTROPY_LIMIT: Final = 0.03
"""Tỉ lệ suy riêng từ chữ ngang và chữ dọc lệch > 3 % → ghi `scaleChecks.anisotropic`. Chỉ khi hai cụm trục cùng
nằm sát khung tường chính (`_FRAME_MARGIN`; ảnh giãn không đều, ch-13) tỉ lệ chuỗi mới đổi thành trung bình nhân hai
trục; cụm nằm ngoài khung là hình vẽ khác tỉ lệ (mặt bằng 1:100 + chi tiết 1:50) → giữ cụm đông nhất."""

_FRAME_MARGIN: Final = 0.25
"""Biên nới khung tường chính, theo cạnh dài của khung: chuỗi kích thước bao căn nằm ngoài vỏ tường 1-3 dòng
(ch-13: chuỗi tổng 9060/7060 cách vỏ ~60 px trên khung 330 px); hình vẽ khác tỉ lệ đặt cạnh căn thì xa hơn."""

_VERTICAL_TEXT_RATIO: Final = 1.3
"""Chữ dọc (đường kích thước đứng): hộp cao > 1,3 lần rộng, như `_VERTICAL_RATIO` của `chains.py`."""

_BUILD_SCALE_MIN: Final = 1.0
_BUILD_SCALE_MAX: Final = 200.0
_DOOR_WIDTH_MM: Final = 745.0
"""Cạnh dài trung vị của hộp YOLO cửa đi đơn (hộp hẹp hơn cánh thật ~850 mm), đo trên bench căn hộ (DOOR-SCALE)."""
_DOOR_MIN_COUNT: Final = 3
_DOUBLE_DOOR_RATIO: Final = 1.6
_AXIS_AGREEMENT: Final = 0.15
"""Cụm chuỗi chung hoà nhưng cụm chữ ngang và chữ dọc đều suy được, lệch <= 15 % → ảnh giãn không đều (scan/in
lệch trục vài %, f-23 7 %, ch-13 13 %), lấy trung bình nhân hai trục; lệch hơn (1:100 vs 1:50 là 2 lần) → không đoán.
Tỉ lệ cứu bằng luật này luôn ghi `scaleChecks.anisotropic` (cụm khác vẫn cạnh tranh) nên vẫn báo `scaleUnresolved`."""
_ROOT_KEYS: Final = frozenset({"schemaVersion", "layer", "dimensions", "scaleMmPerPx", "scaleSource", "dropped"})
_SUGGESTION_CONFIDENCE: Final = 0.5
"""Gợi ý mực dư chưa có điểm của mô hình: lấy đúng trần "kém tin" `LOW_CONFIDENCE_CAP` nên luật 2 không hạ nữa."""

_UNSET_PROJECT_SCALE: Final = Decimal(1)
"""`default_scale_mm_per_px` khi dự án chưa đặt (`DEFAULT_SETTINGS` của `project_settings.read`).

1 mm/px không phải tỉ lệ ai chọn cho mặt bằng (căn 10 m sẽ rộng 10 000 px): chỉ là "chưa đặt". Áp nó làm
tầng ra 0,3 m² (U01), nên khi chưa đọc được tỉ lệ thì dùng tạm ước lượng từ cửa đi (`_door_scale`), thiếu cửa thì từ
bề dày tường; vẫn `unresolved`."""

_NOTE_WALL_MM: Final = (50.0, 500.0)
"""Bề dày tường trung vị (mặt nạ) chấp nhận được ở tỉ lệ ghi chú `TL 1:N`; bench: trung vị ≈ 158 mm.

Ngoài dải là ghi chú đọc hỏng (1:10 thay 1:100) hay PDF in co trang (fit to page): bỏ ghi chú."""

_RASTER_NOTE_AGREEMENT: Final = 0.10
"""PDF chỉ chứa ảnh (không nét vector): khổ trang do người xuất PDF đặt tuỳ ý, không phải khổ in `TL 1:N`. Ghi chú chỉ
được nhận khi lệch ước cửa đi ≤ 10 % (ước cửa lệch trung vị 8-10 %); không có ước cửa hay lệch hơn → bỏ ghi chú."""

_SCALE_SOURCES: Final = frozenset({"pipeline", "project_default"})
"""Hai giá trị `scaleSource` đã khai; B5-06b đọc `layer.json` qua `from_json` nên phải chặn ở đây."""

ScaleBasis = Literal["chain", "paired", "note", "door", "wallThickness", "default"]
"""Tỉ lệ đến từ đâu (QT-SCALE): chuỗi kích thước ≥ 3 mẫu, 2 mẫu đồng thuận, ghi chú `TL 1:N`, ước từ cửa đi,
ước từ bề dày tường, hay tỉ lệ dự án. Chỉ nằm trong `layer.json`; API chưa trả."""

_SCALE_BASES: Final = frozenset(get_args(ScaleBasis))
_OPTIONAL_KEYS: Final = frozenset({"scaleBasis", "scaleChecks", "labelOrientation", "areaNotes"})
"""Khoá gốc tuỳ chọn: `layer.json` dựng trước QT-SCALE không có `scaleBasis` vẫn đọc được."""

ScaleChecks = Mapping[str, Mapping[str, float]]
"""Nghi vấn về tỉ lệ cho bước kiểm chất lượng (chưa lên API): `anisotropic` {x, y} mm/px của hai trục chữ,
`estimateSpread` {door, wallThickness} mm/px của hai ước. Chỉ ghi khi có; `anisotropic` đổi tỉ lệ chuỗi thành
trung bình nhân hai trục khi hai cụm trục cùng sát khung tường chính (`_anisotropy`), `estimateSpread` không đổi
tỉ lệ."""
_SCALE_CHECK_KEYS: Final = {
    "anisotropic": frozenset({"x", "y"}),
    "estimateSpread": frozenset({"door", "wallThickness"}),
}


@dataclass(frozen=True, slots=True)
class BuiltLayer:
    """Kết quả một lượt dựng: lớp, kích thước, tỉ lệ đã chốt và bộ đếm mục bị bỏ.

    `dropped` luôn đủ 12 khoá `DROPPED_KEYS` nên bảng theo dõi của B5-06c không phải đoán khoá
    vắng. `to_json` cho byte tất định (cùng đầu vào → cùng file), `from_json` là nghịch đảo đúng.
    """

    layer: SpatialLayer
    dimensions: tuple[Dimension, ...]
    scale_mm_per_px: Decimal
    scale_source: Literal["pipeline", "project_default"]
    dropped: Mapping[str, int]
    scale_basis: ScaleBasis | None = None
    scale_checks: ScaleChecks = field(default_factory=dict)
    label_orientation: Mapping[str, int] = field(default_factory=dict)
    """BENCH3D-14: số nhãn chữ đọc dọc/ngang (`label_orientation`) cho kiểm `ROTATED_PLAN`; rỗng khi không nhãn nào."""
    area_notes: tuple[float, ...] = ()
    """BENCH3D-24: các số "N m²" (≥ `AREA_NOTE_MIN_M2`) OCR đọc trên bản vẽ (`area_notes`) cho `AREA_TEXT_MISMATCH`."""

    def to_json(self) -> bytes:
        """`layer.json` UTF-8 gọn, khoá theo thứ tự cố định; `scaleMmPerPx` là chuỗi Decimal."""
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "layer": self.layer.model_dump(mode="json", by_alias=True, exclude_none=True),
            "dimensions": [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in self.dimensions],
            "scaleMmPerPx": str(self.scale_mm_per_px),
            "scaleSource": self.scale_source,
            "dropped": {key: self.dropped[key] for key in DROPPED_KEYS},
        }
        if self.scale_basis is not None:
            payload["scaleBasis"] = self.scale_basis
        if self.scale_checks:
            payload["scaleChecks"] = {key: dict(value) for key, value in self.scale_checks.items()}
        if self.label_orientation:
            payload["labelOrientation"] = dict(self.label_orientation)
        if self.area_notes:
            payload["areaNotes"] = list(self.area_notes)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, data: bytes) -> Self:
        """Đọc `layer.json`; JSON hỏng, `schemaVersion` khác 1, khoá lạ hay thiếu → `ValueError`."""
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("layer.json không phải JSON hợp lệ") from exc
        if not isinstance(payload, dict) or not _ROOT_KEYS <= set(payload) <= _ROOT_KEYS | _OPTIONAL_KEYS:
            raise ValueError("layer.json sai bộ khoá gốc")
        if payload["schemaVersion"] != SCHEMA_VERSION:
            raise ValueError(f"layer.json schemaVersion lạ: {payload['schemaVersion']!r}")
        dropped = payload["dropped"]
        if not isinstance(dropped, dict) or set(dropped) != set(DROPPED_KEYS):
            raise ValueError("layer.json sai bộ khoá dropped")
        if payload["scaleSource"] not in _SCALE_SOURCES:
            raise ValueError(f"layer.json scaleSource lạ: {payload['scaleSource']!r}")
        basis = payload.get("scaleBasis")
        if basis is not None and basis not in _SCALE_BASES:
            raise ValueError(f"layer.json scaleBasis lạ: {basis!r}")
        checks = payload.get("scaleChecks", {})
        if not isinstance(checks, dict) or any(
            key not in _SCALE_CHECK_KEYS or not isinstance(value, dict) or set(value) != _SCALE_CHECK_KEYS[key]
            for key, value in checks.items()
        ):
            raise ValueError(f"layer.json scaleChecks lạ: {checks!r}")
        orientation = payload.get("labelOrientation", {})
        if not isinstance(orientation, dict) or (orientation and set(orientation) != _ORIENTATION_KEYS):
            raise ValueError(f"layer.json labelOrientation lạ: {orientation!r}")
        notes = payload.get("areaNotes", [])
        if not isinstance(notes, list) or not all(
            isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x) and x > 0 for x in notes
        ):
            raise ValueError(f"layer.json areaNotes lạ: {notes!r}")
        return cls(
            layer=SpatialLayer.model_validate(payload["layer"]),
            dimensions=tuple(Dimension.model_validate(item) for item in payload["dimensions"]),
            scale_mm_per_px=Decimal(payload["scaleMmPerPx"]),
            scale_source=payload["scaleSource"],
            dropped={key: int(dropped[key]) for key in DROPPED_KEYS},
            scale_basis=basis,
            scale_checks={key: {k: float(v) for k, v in value.items()} for key, value in checks.items()},
            label_orientation={key: int(value) for key, value in orientation.items()},
            area_notes=tuple(float(x) for x in notes),
        )


def _checked(raw: np.ndarray, what: str) -> None:
    """Mảng `[toạ độ…, confidence]`: NaN, vô cực hay `confidence` ngoài `[0, 1]` → `ValueError`.

    Chỉ kiểm phần chung của mọi mảng (hữu hạn, `confidence`); hai phép kiểm riêng của tường
    (`thickness_px > 0`, hai đầu không trùng) nằm ở `_filtered_walls`. Đối số có thể dựng bằng
    `model_construct` nên không tin ràng buộc của B5-01.
    """
    if raw.size == 0:
        return
    if not bool(np.isfinite(raw).all()):
        raise ValueError(f"{what} chứa NaN hay vô cực")
    confidence = raw[:, -1]
    if not bool(((confidence >= 0.0) & (confidence <= 1.0)).all()):
        raise ValueError(f"{what} có confidence ngoài [0, 1]")


def _inside(coords: np.ndarray, width_px: int, height_px: int) -> np.ndarray:
    """Mặt nạ mục nằm trong `[-1, w+1] x [-1, h+1]`; cột chẵn là x, cột lẻ là y."""
    if coords.size == 0:
        return np.ones(len(coords), dtype=bool)
    xs, ys = coords[:, 0::2], coords[:, 1::2]
    return (
        (xs >= -1.0).all(axis=1)
        & (xs <= width_px + 1.0).all(axis=1)
        & (ys >= -1.0).all(axis=1)
        & (ys <= height_px + 1.0).all(axis=1)
    )


def _kept[T](items: Sequence[T], raw: np.ndarray, size: tuple[int, int], dropped: Counter[str]) -> tuple[T, ...]:
    """Mục còn lại sau bước 1; mục ra ngoài ảnh bị bỏ và đếm `outOfImage`."""
    mask = _inside(raw[:, :-1], *size)
    dropped["outOfImage"] += int(len(items) - mask.sum())
    return tuple(item for item, keep in zip(items, mask, strict=True) if keep)


def _filtered_walls(walls: Sequence[WallPx], size: tuple[int, int], dropped: Counter[str]) -> tuple[WallPx, ...]:
    """Tường hợp lệ và nằm trong ảnh; kiểm bằng numpy để chịu được trần 20.000 tường."""
    if len(walls) > MAX_WALLS:
        raise ValueError(f"{len(walls)} tường, trần {MAX_WALLS}")
    raw = np.array(
        [(w.start.x, w.start.y, w.end.x, w.end.y, w.thickness_px, w.confidence) for w in walls],
        dtype=float,
    ).reshape(len(walls), 6)
    _checked(raw, "walls")
    if not bool((raw[:, 4] > 0.0).all()):
        raise ValueError("walls có thickness_px không dương")
    if bool((raw[:, :2] == raw[:, 2:4]).all(axis=1).any()):
        raise ValueError("walls có tường hai đầu trùng nhau")
    return _kept(walls, np.delete(raw, 4, axis=1), size, dropped)


def _filtered_boxes[T: DetectionPx | TextPx](
    items: Sequence[T], limit: int, size: tuple[int, int], dropped: Counter[str], what: str
) -> tuple[T, ...]:
    """Hộp phát hiện hay chữ đã lọc như `_filtered_walls`: cùng luật vì cùng `BoxPx`."""
    if len(items) > limit:
        raise ValueError(f"{len(items)} {what}, trần {limit}")
    raw = np.array(
        [(i.box.x_min, i.box.y_min, i.box.x_max, i.box.y_max, i.confidence) for i in items],
        dtype=float,
    ).reshape(len(items), 5)
    _checked(raw, what)
    return _kept(items, raw, size, dropped)


def _resolved_scale(
    walls: Sequence[WallPx],
    texts: Sequence[TextPx],
    fallback_mm_per_px: Decimal,
    dropped: Counter[str],
) -> tuple[Decimal, ScaleBasis, tuple[ScaleSample, ...], tuple[str, ...], tuple[float, float] | None]:
    """Bước 2: tỉ lệ dây `s`, gốc (`chain`/`paired`/`default`), mẫu, id mẫu bị loại và tỉ lệ hai trục `(x, y)` khi
    tỉ lệ được cứu bằng hai trục (T3, `_AXIS_AGREEMENT`); ngoài khoảng → `scaleOutOfRange`.

    Mẫu chuỗi kích thước (`span_px` đo trên ảnh, cụm tỉ số đông nhất) quyết trước; không đủ
    thì mới tới mẫu ghép chữ-tường. Trả cả hai loại mẫu: bước 7 chỉ dựng `Dimension` từ mẫu
    ghép tường (id `t…-w…`), mẫu chuỗi bị `_SAMPLE_ID` bỏ qua.

    Không có tỉ lệ suy được, ngoài khoảng, hay `s` về 0 sau quantize → `fallback_mm_per_px`
    và log `SCALE_UNRESOLVED` (K19: không bao giờ đoán một tỉ lệ khác).
    """
    paired = pair_dimension_lines(texts, walls)
    chained = densest_cluster(_chain_samples(texts))
    result = infer_scale(chained)
    if result.mm_per_px is None:
        result = infer_scale(paired)
    samples = (*paired, *chained)
    axes: tuple[float, float] | None = None
    if result.mm_per_px is None and not chained:
        across, down = _axis_clusters(texts)
        scales = axis_scales(across, down)
        if scales is not None and max(scales) <= (1 + _AXIS_AGREEMENT) * min(scales):
            # T3 (f-23): hai trục giãn khác nhau làm cụm chung hoà; `build_layer` ghi `scaleChecks.anisotropic`
            result = replace(result, mm_per_px=math.sqrt(scales[0] * scales[1]), paired=False)
            samples, axes = (*paired, *across, *down), scales
    if result.mm_per_px is not None:
        if classify_scale_range(result.mm_per_px) == "inRange":
            value = Decimal(repr(result.mm_per_px)).quantize(_QUANTUM)
            if value != 0:
                return value, "paired" if result.paired else "chain", samples, result.rejected_ids, axes
        else:
            dropped["scaleOutOfRange"] += 1
    _log.info(
        "pipeline_build_dropped",
        extra={"code": PipelineCode.SCALE_UNRESOLVED.value, "reason": result.reason},
    )
    return fallback_mm_per_px, "default", samples, result.rejected_ids, None


def _note_scale(walls: Sequence[WallPx], texts: Sequence[TextPx], px_per_paper_mm: float | None) -> Decimal | None:
    """U01: ghi chú `TL 1:N` + px/mm giấy của PDF → `N / px_per_paper_mm` mm/px, hay `None`.

    Ảnh raster (không biết khổ giấy), không có ghi chú, ghi chú mơ hồ, tỉ lệ ngoài dải 1-200 mm/px
    hay làm tường trung vị ngoài `_NOTE_WALL_MM` → `None`, rơi về luật cũ.
    """
    if px_per_paper_mm is None or px_per_paper_mm <= 0:
        return None
    denominator = parse_scale_note(text.text for text in texts)
    if denominator is None:
        return None
    mm_per_px = denominator / px_per_paper_mm
    if classify_scale_range(mm_per_px) != "inRange":
        return None
    if walls:
        median_mm = float(np.median(np.array([wall.thickness_px for wall in walls], dtype=float))) * mm_per_px
        if not _NOTE_WALL_MM[0] <= median_mm <= _NOTE_WALL_MM[1]:
            _log.info("pipeline_build_scale_note_rejected", extra={"denominator": denominator, "wall_mm": median_mm})
            return None
    value = Decimal(repr(mm_per_px)).quantize(_QUANTUM)
    return value if value != 0 else None


def _trusted_note(
    noted: Decimal | None, *, vector_page: bool, detections: Sequence[DetectionPx], walls: Sequence[WallPx]
) -> Decimal | None:
    """Ghi chú `TL 1:N` còn tin được: PDF vector giữ nguyên; PDF chỉ chứa ảnh cần ước cửa đi đồng thuận.

    Khổ trang PDF ảnh nhúng là tuỳ ý (two-page-ch04: ảnh 150 dpi, ghi chú cho +16 %), nên `N / px_per_paper_mm`
    chỉ đúng khi trang được xuất đúng khổ in; ước cửa (`_door_scale`) là phép thử độc lập duy nhất có sẵn.
    """
    if noted is None or vector_page:
        return noted
    door = _door_scale(detections, walls) if walls else None
    if door is not None and abs(float(noted) / door - 1) <= _RASTER_NOTE_AGREEMENT:
        return noted
    _log.info("pipeline_build_scale_note_rejected", extra={"reason": "rasterPdf", "note": float(noted), "door": door})
    return None


_METRIC_DECIMAL: Final = re.compile(r"\d{1,2}[.,]\d{1,3}")
_METRIC_INTEGER: Final = re.compile(r"\d{1,4}")
"""R8 (cvcfp-2): bản vẽ không ghi đơn vị kiểu "mét-cm" — số thập phân là mét (`2.25`), số nguyên là cm (`80`)."""
_METRIC_MIN_CLUSTER: Final = 2
"""Cụm "mét-cm" nhỏ hơn (một chữ lẻ) không đủ căn cứ đổi cách đọc cả trang: giữ mm như prod."""


def _chain_samples(texts: Sequence[TextPx]) -> tuple[ScaleSample, ...]:
    """Một mẫu mỗi chữ có `span_px` và đọc ra độ dài mm, tỉ số trong dải 1-200 mm/px; id `t<i>-chain`.

    R8/R9 đơn vị theo căn cứ trong chính trang: số trần đọc hai cách — mm (`parse_length_mm`, như prod) và "mét-cm"
    (`_metric_mm`). Chỉ chọn "mét-cm" khi cụm tỉ số đông nhất của nó (a) có ≥ `_METRIC_MIN_CLUSTER` mẫu, (b) đông hơn
    hẳn cụm của cách đọc mm và (c) chứa ít nhất một chữ mà cách đọc mm bỏ (số mét thập phân như `2.25`) — trang chỉ
    toàn số nguyên (mm thuần, VN/Nhật `3450`, `900`) không bao giờ bị đổi, kể cả khi nhân 10 rơi vào dải. Hoà hay thiếu
    căn cứ → mm như prod (không đoán). Cả trang một cách đọc; chữ có đơn vị (`3,5 m`, `350cm`) đọc như nhau."""
    plain = _read_chain(texts, parse_length_mm)
    metric = _read_chain(texts, _metric_mm)
    unread = {f"t{index}-chain" for index, text in enumerate(texts) if parse_length_mm(text.text) is None}
    cluster = _largest_cluster(metric)
    if (
        len(cluster) >= _METRIC_MIN_CLUSTER
        and len(cluster) > len(_largest_cluster(plain))
        and any(sample.id in unread for sample in cluster)
    ):
        _log.info("pipeline_build_chain_unit", extra={"reading": "metric", "cluster": len(cluster)})
        return metric
    return plain


def _largest_cluster(samples: Sequence[ScaleSample]) -> tuple[ScaleSample, ...]:
    """Mẫu của cụm tỉ số lớn nhất (cùng dung sai `densest_cluster`, không áp luật hoà: hoà vẫn là cụm cỡ đó)."""
    ratios = [sample.real_length_mm / sample.pixel_length for sample in samples]
    best: tuple[ScaleSample, ...] = ()
    for centre in ratios:
        pairs = zip(samples, ratios, strict=True)
        members = tuple(sample for sample, ratio in pairs if abs(ratio - centre) <= CLUSTER_TOLERANCE * centre)
        if len(members) > len(best):
            best = members
    return best


def _read_chain(texts: Sequence[TextPx], read: Any) -> tuple[ScaleSample, ...]:
    """Mẫu chuỗi với hàm đọc `read(chữ) -> mm | None`."""
    samples = []
    for index, text in enumerate(texts):
        if text.span_px is None:
            continue
        value = read(text.text)
        if value is not None and classify_scale_range(value / text.span_px) == "inRange":
            samples.append(ScaleSample(f"t{index}-chain", text.span_px, float(value)))
    return tuple(samples)


def _metric_mm(text: str) -> int | None:
    """Cách đọc "mét-cm" cho số trần: `2.25`/`2,25` → 2250 mm, `80` → 800 mm; chữ có đơn vị → `parse_length_mm`."""
    bare = text.strip()
    if _METRIC_DECIMAL.fullmatch(bare):
        value = Fraction(bare.replace(",", ".")) * 1000
        return int(value) if value.denominator == 1 and value > 0 else None
    if _METRIC_INTEGER.fullmatch(bare):
        return int(bare) * 10 if int(bare) > 0 else None
    return parse_length_mm(text)


def _axis_clusters(texts: Sequence[TextPx]) -> tuple[tuple[ScaleSample, ...], tuple[ScaleSample, ...]]:
    """Cụm đông nhất của mẫu chuỗi chữ ngang và của mẫu chuỗi chữ dọc (hộp cao > `_VERTICAL_TEXT_RATIO` lần rộng)."""
    vertical = {
        f"t{index}-chain"
        for index, text in enumerate(texts)
        if text.box.y_max - text.box.y_min > _VERTICAL_TEXT_RATIO * (text.box.x_max - text.box.x_min)
    }
    samples = _chain_samples(texts)
    across = densest_cluster([sample for sample in samples if sample.id not in vertical])
    down = densest_cluster([sample for sample in samples if sample.id in vertical])
    return across, down


def _anisotropy(texts: Sequence[TextPx], frame: Box | None) -> tuple[dict[str, float], bool]:
    """`{x, y}` khi tỉ lệ suy riêng từ chữ chuỗi ngang và dọc (cụm đông nhất mỗi trục) lệch > `_ANISOTROPY_LIMIT`,
    kèm `True` khi hộp chữ của cả hai cụm nằm trong `frame` (khung tường chính, px) nới `_FRAME_MARGIN`: chuỗi bao
    quanh một căn, dù chuỗi ngang chỉ ở một cạnh còn chuỗi dọc ở cạnh khác. Không có khung → `False`."""
    boxes = {f"t{index}-chain": text.box for index, text in enumerate(texts)}
    across, down = _axis_clusters(texts)
    scales = axis_scales(across, down)
    if scales is None or max(scales) <= (1 + _ANISOTROPY_LIMIT) * min(scales):
        return {}, False
    anisotropic = {"x": round(scales[0], 6), "y": round(scales[1], 6)}
    if frame is None:
        return anisotropic, False
    margin = _FRAME_MARGIN * max(frame[2] - frame[0], frame[3] - frame[1])
    x0, y0, x1, y1 = frame[0] - margin, frame[1] - margin, frame[2] + margin, frame[3] + margin
    inside = all(
        x0 <= box.x_min and box.x_max <= x1 and y0 <= box.y_min and box.y_max <= y1
        for box in (boxes[sample.id] for sample in (*across, *down))
    )
    return anisotropic, inside


def _plan_frame(walls: Sequence[WallPx], mm_per_px: float) -> Box | None:
    """Khung tường chính (px): vùng cụm tường dài nhất nới bằng các cụm sát nó (`grow_region`, như bước bỏ cụm xa
    của `geometry`), tính trên mm theo tỉ lệ `mm_per_px` để ngưỡng nối tường mm đúng nghĩa. Không có tường → `None`."""
    if not walls:
        return None
    ends = [[[w.start.x, w.start.y], [w.end.x, w.end.y]] for w in walls]
    lines = np.asarray(shapely.linestrings(np.asarray(ends) * mm_per_px), dtype=object)
    groups = wall_clusters(lines, [w.thickness_px * mm_per_px for w in walls])
    boxes: list[Box] = [tuple(shapely.total_bounds(lines[group]).tolist()) for group in groups]
    lengths = [float(shapely.length(lines[group]).sum()) for group in groups]
    region, _ = grow_region(boxes, range(len(groups)), max(range(len(groups)), key=lengths.__getitem__))
    x0, y0, x1, y1 = region
    return (x0 / mm_per_px, y0 / mm_per_px, x1 / mm_per_px, y1 / mm_per_px)


def _estimate(door: float | None, wall: float) -> tuple[float, ScaleBasis, dict[str, float]]:
    """Tỉ lệ tạm (U01) từ ước cửa đi và ước bề dày tường: (tỉ lệ, gốc, nghi vấn `estimateSpread`).

    Không có ước cửa → bề dày tường. Hai ước lệch ≤ `_ESTIMATE_AGREEMENT` → trung bình nhân, gốc `door`; lệch hơn →
    ước cửa, kèm hai giá trị để bước kiểm chất lượng báo.
    """
    if door is None:
        return wall, "wallThickness", {}
    if max(door, wall) <= (1 + _ESTIMATE_AGREEMENT) * min(door, wall):
        return math.sqrt(door * wall), "door", {}
    return door, "door", {"door": round(door, 6), "wallThickness": round(wall, 6)}


def _build_scale(walls: Sequence[WallPx], final: float) -> float:
    """`s_dựng` khi `project_default`: bề dày trung vị về 150 mm, kẹp `[1, 200]` (bước 2)."""
    if not walls:
        return final
    median = float(np.median(np.array([wall.thickness_px for wall in walls], dtype=float)))
    return min(max(_BUILD_SCALE_TARGET_MM / median, _BUILD_SCALE_MIN), _BUILD_SCALE_MAX)


def _door_scale(detections: Sequence[DetectionPx], walls: Sequence[WallPx]) -> float | None:
    """Tỉ lệ tạm từ cửa đi: cạnh dài trung vị của hộp `door` = `_DOOR_WIDTH_MM`; < `_DOOR_MIN_COUNT` cửa → `None`.

    `walls` không rỗng (chỉ gọi ở nhánh ước lượng, nhánh đó đòi có tường).

    Chỉ tính hộp cách tim tường nào đó ≤ bề dày tường dày nhất: cửa trên sơ đồ tầng thu nhỏ hay chú giải (không
    tường) làm trung vị nhỏ đi và tỉ lệ phình (bench ch-08: 7/12 hộp ở sơ đồ thu nhỏ). Hộp dài hơn
    `_DOUBLE_DOOR_RATIO` lần trung vị bỏ trước lần lấy trung vị thứ hai — chỉ chống cửa đôi/cửa lùa **thiểu số**.
    Bench 11 bản có đáp án (DOOR-SCALE): lệch trung vị ~8-10 %, tối đa ~17 %; bề dày tường lệch tới 42 %.
    """
    rects = np.array([(d.box.x_min, d.box.y_min, d.box.x_max, d.box.y_max) for d in detections if d.label == "door"])
    if len(rects) < _DOOR_MIN_COUNT:
        return None
    lines = shapely.linestrings([[(w.start.x, w.start.y), (w.end.x, w.end.y)] for w in walls])
    reach = max(wall.thickness_px for wall in walls)
    near = np.unique(STRtree(lines).query(shapely.box(*rects.T), predicate="dwithin", distance=reach)[0])
    sides = np.maximum(rects[:, 2] - rects[:, 0], rects[:, 3] - rects[:, 1])[near]
    if len(sides) < _DOOR_MIN_COUNT:
        return None
    single = sides[sides <= _DOUBLE_DOOR_RATIO * float(np.median(sides))]
    return min(max(_DOOR_WIDTH_MM / float(np.median(single)), _BUILD_SCALE_MIN), _BUILD_SCALE_MAX)


def _ratio(sample: ScaleSample) -> float:
    """Tỉ lệ mm/px mà một mẫu ngụ ý."""
    return sample.real_length_mm / sample.pixel_length


def _whole_line(wall_px: WallPx, scaling: Scaling) -> Segment | None:
    """Cả đường tim của tường gốc `j` (mẫu `sall`), mm.

    Không dùng `Wall.centreline` của tường đã giữ: tường gộp ở 3b dài hơn tường gốc mà
    `valueMm` đo trên tường gốc, nên `line` sẽ lệch xa hơn ngưỡng 2 % của bước 7 (F3).
    """
    start = scaling.point(wall_px.start.x, wall_px.start.y)
    end = scaling.point(wall_px.end.x, wall_px.end.y)
    return None if start == end else Segment(start=start, end=end)


def _span_line(wall_px: WallPx, spans: Sequence[float], tag: str, scaling: Scaling) -> Segment | None:
    """Nhịp `k` của tường gốc đổi sang mm; nhịp suy biến sau làm tròn → `None` (không dựng được).

    `spans` là tham số dọc tường tính bằng px (`pairing._merge_points`), nên điểm chia là
    `start + t · đơn vị hướng`.
    """
    index = int(tag)
    if index + 1 >= len(spans):
        return None
    dx, dy = wall_px.end.x - wall_px.start.x, wall_px.end.y - wall_px.start.y
    length = math.hypot(dx, dy)
    ends = [
        scaling.point(wall_px.start.x + dx * spans[k] / length, wall_px.start.y + dy * spans[k] / length)
        for k in (index, index + 1)
    ]
    return None if ends[0] == ends[1] else Segment(start=ends[0], end=ends[1])


def _best_sample(
    candidates: Sequence[tuple[ScaleSample, int, str]], scale: float
) -> tuple[ScaleSample, int, str] | None:
    """Mẫu có tỉ số gần `s` nhất trong ngưỡng 2 %; không mẫu nào đạt → `None`."""
    within = [item for item in candidates if abs(_ratio(item[0]) - scale) / scale <= _DIMENSION_TOLERANCE]
    return min(within, key=lambda item: abs(_ratio(item[0]) - scale)) if within else None


def _grouped_samples(
    samples: Sequence[ScaleSample], rejected: frozenset[str], kept: Mapping[int, int]
) -> dict[int, list[tuple[ScaleSample, int, str]]]:
    """Mẫu còn dùng được, gom theo chỉ số chữ; chữ có mẫu bị loại hết vẫn có khoá (để đếm)."""
    grouped: dict[int, list[tuple[ScaleSample, int, str]]] = {}
    for sample in samples:
        match = _SAMPLE_ID.match(sample.id)
        if match is None:
            continue
        text_index, wall_index = int(match.group(1)), int(match.group(2))
        grouped.setdefault(text_index, [])
        if sample.id not in rejected and wall_index in kept and sample.pixel_length > 0:
            grouped[text_index].append((sample, wall_index, match.group(3)))
    return grouped


def _dimension(
    sample: ScaleSample, line: Segment, wall: Wall, text: TextPx, *, level_id: str, clock: Clock
) -> Dimension:
    """Một chuỗi kích thước tuyến tính do AI dựng; tin cậy là min của chữ và tường (bước 7)."""
    return Dimension(
        id=new_spatial_id("dimension", clock),
        level_id=level_id,
        kind="linear",
        reference_ids=(wall.id,),
        line=line,
        value_mm=js_round(sample.real_length_mm),
        confidence=min(text.confidence, wall.confidence),
        source="ai",
        reviewed=False,
    )


@dataclass(slots=True)
class _SpanIndex:
    """Điểm chia nhịp của một tường, tính trên tập con láng giềng thay vì cả 20.000 tường.

    `wall_spans(walls)[j]` chỉ phụ thuộc tường cắt tim `j` hoặc có đầu mút cách tim `j` không quá
    `max(t_j, t_k) / 2` (`pairing._WallIndex.spans`), nên một tập con **chứa đủ** các tường đó cho
    đúng kết quả ấy; tường thừa trong tập con không sinh thêm điểm chia. Gọi `wall_spans` trên cả
    bảng là O(n²), còn bước 7 chỉ cần vài tường có mẫu được chọn.
    """

    walls: Sequence[WallPx]
    tree: STRtree
    lines: np.ndarray
    reach: float
    cache: dict[int, tuple[float, ...]]

    @classmethod
    def of(cls, walls: Sequence[WallPx]) -> "_SpanIndex":
        """Dựng `STRtree` của mọi tim tường một lần cho cả lượt dựng."""
        coords = [[[w.start.x, w.start.y], [w.end.x, w.end.y]] for w in walls]
        lines = np.asarray(shapely.linestrings(coords), dtype=object)
        thickest = max((w.thickness_px for w in walls), default=0.0)
        return cls(walls, STRtree(lines), lines, thickest / 2, {})

    def spans(self, j: int) -> tuple[float, ...]:
        """Điểm chia nhịp của tường `j`, bằng `wall_spans(walls)[j]`; nhớ lại giữa các chữ."""
        if j not in self.cache:
            distance = max(self.walls[j].thickness_px / 2, self.reach)
            near = {int(k) for k in self.tree.query(self.lines[j], predicate="dwithin", distance=distance)}
            order = sorted(near | {j})
            self.cache[j] = wall_spans([self.walls[k] for k in order])[order.index(j)]
        return self.cache[j]


def _build_dimensions(
    context: "_Attempt", wall_set: WallSet, *, scaling: Scaling, clock: Clock, dropped: Counter[str]
) -> tuple[Dimension, ...]:
    """Bước 7: tối đa một `Dimension` mỗi chữ; chữ có mẫu mà không dựng được → `dimensionUnpaired`."""
    kept = {j: wall_set.input_to_wall[k] for j, k in enumerate(context.first_of) if k in wall_set.input_to_wall}
    grouped = _grouped_samples(context.samples, frozenset(context.rejected), kept)
    built: list[Dimension] = []
    done: set[int] = set()
    if not grouped:
        return _chain_dimensions(context, done, scaling=scaling, clock=clock)
    index = _SpanIndex.of(context.whole_px)
    for text_index in sorted(grouped):
        chosen = _best_sample(grouped[text_index], scaling.final)
        if chosen is None:
            dropped["dimensionUnpaired"] += 1
            continue
        sample, wall_index, tag = chosen
        wall = wall_set.walls[kept[wall_index]]
        wall_px = context.whole_px[wall_index]
        line = (
            _whole_line(wall_px, scaling)
            if tag == "all"
            else _span_line(wall_px, index.spans(wall_index), tag, scaling)
        )
        if line is None:
            dropped["dimensionUnpaired"] += 1
            continue
        text = context.texts[text_index]
        built.append(_dimension(sample, line, wall, text, level_id=context.level_id, clock=clock))
        done.add(text_index)
    return (*built, *_chain_dimensions(context, done, scaling=scaling, clock=clock))


def _chain_dimensions(context: "_Attempt", taken: set[int], *, scaling: Scaling, clock: Clock) -> tuple[Dimension, ...]:
    """UX-02: chữ chỉ có mẫu chuỗi (không ghép được tường) vẫn thành `Dimension`, không gắn thực thể.

    Trước đây mẫu chuỗi quyết tỉ lệ mà không dựng `Dimension` nào, nên ch-03 suy đúng 13,9 mm/px
    vẫn báo "không đọc được số kích thước nào". Đoạn dài `span_px`, giữa hộp chữ, nằm theo chiều dài
    của hộp; mọi mẫu chuỗi trong ngưỡng 2 % của `s` (như bước 7), kể cả khi `s` đến từ ghi chú `TL 1:N`
    với dưới 3 mẫu. Chữ đã có `Dimension` ghép tường hay mẫu bị loại thì bỏ.
    """
    rejected = frozenset(context.rejected)
    built: list[Dimension] = []
    for sample in _chain_samples(context.texts):
        match = _CHAIN_ID.match(sample.id)
        if match is None or sample.id in rejected or int(match.group(1)) in taken:
            continue
        if abs(_ratio(sample) - scaling.final) / scaling.final > _DIMENSION_TOLERANCE:
            continue
        text = context.texts[int(match.group(1))]
        box, half = text.box, sample.pixel_length / 2
        x, y = (box.x_min + box.x_max) / 2, (box.y_min + box.y_max) / 2
        if box.x_max - box.x_min >= box.y_max - box.y_min:
            start, end = scaling.point(max(x - half, 0.0), y), scaling.point(x + half, y)
        else:
            start, end = scaling.point(x, max(y - half, 0.0)), scaling.point(x, y + half)
        if start == end:
            continue
        built.append(
            Dimension(
                id=new_spatial_id("dimension", clock),
                level_id=context.level_id,
                kind="linear",
                reference_ids=(),
                line=Segment(start=start, end=end),
                value_mm=js_round(sample.real_length_mm),
                confidence=text.confidence,
                source="ai",
                reviewed=False,
            )
        )
    return tuple(built)


@dataclass(frozen=True, slots=True)
class _Attempt:
    """Đầu vào đã lọc của bước 3-8, dùng lại y nguyên cho lượt dựng lại sau `RescaleError`."""

    walls_px: tuple[WallPx, ...]
    detections: tuple[DetectionPx, ...]
    texts: tuple[TextPx, ...]
    samples: tuple[ScaleSample, ...]
    rejected: tuple[str, ...]
    level_id: str
    source: str
    suggestions: tuple[DetectionPx, ...] = ()
    arc_of: tuple[int | None, ...] = ()
    """Tường cong gốc của từng dây trong `walls_px` (`explode_arcs`), rỗng khi không có tường cong."""
    whole_px: tuple[WallPx, ...] = ()
    """Tường trước khi nổ cung: chữ kích thước ghép với cả tường cong (dây lớn), không với dây 10° (bước 2, 7)."""
    first_of: tuple[int, ...] = ()
    """Vị trí trong `walls_px` của mảnh đầu mỗi tường `whole_px`."""
    furniture: bool = True
    """Dựng đồ đạc từ hộp phát hiện (QT-FURN-TOGGLE); `False` → lớp không có đồ, hộp vẫn dùng cho ô mở và phòng."""
    page: PageRaster | None = None
    """Trang + mặt nạ tường: đọc chiều mở cửa từ nét vẽ (QT-SWING)."""


def _assembled(
    context: _Attempt, *, scaling: Scaling, clock: Clock, dropped: Counter[str]
) -> tuple[SpatialLayer, WallSet]:
    """Bước 3-6: lớp thô trước khi qua luật hậu xử lý, kèm `WallSet` cho bước 7."""
    shared: dict[str, Any] = {
        "level_id": context.level_id,
        "scaling": scaling,
        "clock": clock,
        "dropped": dropped,
    }
    wall_set = build_walls(context.walls_px, context.detections, arc_of=context.arc_of, page=context.page, **shared)
    # Tường qua khe cửa (bước 3c) không có đầu vào: bỏ khi dựng phòng, `_door_closers` khép khe như cũ (OPEN-ATTACH).
    from_inputs = set(wall_set.input_to_wall.values())
    room_walls = [wall for index, wall in enumerate(wall_set.walls) if index in from_inputs]
    trusted = context.source == "pipeline"
    closers: list[RoomCloser] = []
    rooms = build_rooms(
        room_walls,
        wall_set.bridges,
        context.texts,
        detections=context.detections,
        scale_trusted=trusted,
        closers_out=closers,
        page=context.page,
        **shared,
    )
    joined, rooms = join_arcs(wall_set, rooms, context.arc_of)
    # C5: khe trên mặt ngoài (đoạn nối 3b, đoạn khép phòng) thành tường/tường mang ô mở thay vì để vỏ hở.
    wall_set = close_envelope_gaps(
        replace(joined, leaves=wall_set.leaves),
        rooms,
        level_id=context.level_id,
        scaling=scaling,
        clock=clock,
        page=context.page,
        closers=closers,
    )
    furniture: tuple[Furniture, ...] = ()
    if context.furniture:
        detected = build_furniture(context.detections, rooms, scale_trusted=trusted, **shared)
        suggested = build_furniture(context.suggestions, rooms, scale_trusted=trusted, **shared)
        furniture = (*detected, *(item.model_copy(update={"suggested": True}) for item in suggested))
    layer = SpatialLayer(walls=wall_set.walls, openings=wall_set.openings, rooms=rooms, furniture=furniture)
    return layer, wall_set


def _attempt(
    context: _Attempt, *, scaling: Scaling, clock: Clock, dropped: Counter[str]
) -> tuple[SpatialLayer, tuple[Dimension, ...]]:
    """Bước 3-8 ở `scaling.build`, đúng thứ tự khối [6]: 3-6, rồi 7, rồi 8.

    Bước 7 chạy trước `apply_post_rules` nên `Dimension.confidence` là `min(chữ, tường)` của
    tường lúc bước 7, chưa bị luật 2 hạ xuống; lỗi toàn vẹn critical sau luật → `ValueError`.
    """
    layer, wall_set = _assembled(context, scaling=scaling, clock=clock, dropped=dropped)
    dimensions: tuple[Dimension, ...] = ()
    if context.source == "pipeline":
        dimensions = _build_dimensions(context, wall_set, scaling=scaling, clock=clock, dropped=dropped)
    # Khúc thẳng hàng chạm nhau cùng `kind` → một tường một bề dày (WALL-RUN), rồi `reversed` so với luật phía mở FE
    # trên phòng sau luật hậu xử lý (QT-SWING, `swing.py`).
    layer, dimensions = merge_wall_runs(dedupe_furniture(apply_post_rules(layer)), dimensions)
    layer = settle_reversed(layer, wall_set.leaves)
    if has_critical(check_integrity(layer, level_id=context.level_id)):
        raise ValueError("lớp dựng ra có lỗi toàn vẹn critical")
    return layer, dimensions


def _suggestions(
    page: PageRaster | None,
    walls_px: Sequence[WallPx],
    detections: Sequence[DetectionPx],
    texts: Sequence[TextPx],
    mm_per_px: float | None,
) -> tuple[DetectionPx, ...]:
    """Hộp `other` gợi ý từ mực dư (B4); không có trang, khổ lệch hay tỉ lệ chưa đáng tin → `()`.

    `mm_per_px` `None` = tỉ lệ còn đoán (dự phòng/ước): bộ lọc cỡ theo mét sai thì gợi ý chỉ là rác, nên tắt.
    """
    if page is None or page.gray.shape != page.walls.shape or mm_per_px is None:
        return ()

    def corners(box: BoxPx) -> tuple[float, float, float, float]:
        """Bốn toạ độ của hộp px."""
        return (box.x_min, box.y_min, box.x_max, box.y_max)

    boxes = residual_ink_boxes(
        page,
        detections=[(d.label, corners(d.box)) for d in detections],
        texts=[corners(t.box) for t in texts],
        mm_per_px=mm_per_px,
    )
    return tuple(
        DetectionPx(
            label="other",
            confidence=_SUGGESTION_CONFIDENCE,
            box=BoxPx(x_min=b[0], y_min=b[1], x_max=b[2], y_max=b[3]),
        )
        for b in boxes
    )


def build_layer(
    *,
    level_id: str,
    walls: WallsResult,
    objects: ObjectsResult,
    text: TextResult,
    width_px: int,
    height_px: int,
    fallback_mm_per_px: Decimal,
    clock: Clock,
    page: PageRaster | None = None,
    px_per_paper_mm: float | None = None,
    vector_page: bool = True,
    furniture: bool = True,
) -> BuiltLayer:
    """Ba artifact ML (px) → lớp không gian (mm) theo B5-05 [6]; không sửa đối số, không I/O.

    Đầu vào sai hợp đồng B5-01 (vượt trần, NaN, `confidence` ngoài `[0, 1]`, ảnh < 1 px,
    `fallback_mm_per_px` ≤ 0) → `ValueError`; đầu ra AI xấu chỉ bị bỏ và đếm vào `dropped`.
    Dựng ở `s_dựng` rồi đổi về `s`; `RescaleError` → dựng lại thẳng ở `s`, không ném. `page` (trang + mặt nạ
    tường) bật nguồn gợi ý mực dư: hộp `other` với `suggested=True`, không bao giờ trùng hộp phát hiện.
    `px_per_paper_mm` (PDF) cho phép đọc ghi chú `TL 1:N` khi chuỗi kích thước không ra tỉ lệ (U01):
    thắng tỉ lệ dự án và ước lượng từ bề dày tường; `vector_page=False` (PDF chỉ chứa ảnh) thì ghi chú phải khớp ước
    cửa đi (`_trusted_note`). Ảnh giãn không đều (`scaleChecks.anisotropic`, hai cụm trục sát khung tường chính) → tỉ lệ
    chuỗi là trung bình nhân hai trục. `furniture=False` (cài đặt dự án `detectFurniture` tắt):
    không dựng đồ, không gợi ý mực dư; hộp phát hiện vẫn cho ô mở, tỉ lệ theo cửa và nhãn phòng.
    """
    if width_px < 1 or height_px < 1:
        raise ValueError(f"ảnh {width_px}x{height_px} px không hợp lệ")
    if not fallback_mm_per_px.is_finite() or fallback_mm_per_px <= 0:
        raise ValueError(f"fallback_mm_per_px {fallback_mm_per_px} phải dương và hữu hạn")
    size = (width_px, height_px)
    base: Counter[str] = Counter()
    whole = _filtered_walls(walls.walls, size, base)
    walls_px, arc_of = explode_arcs(whole)
    first_of = tuple(k for k, arc in enumerate(arc_of) if arc is None or k == 0 or arc_of[k - 1] != arc)
    detections = _filtered_boxes(objects.detections, MAX_DETECTIONS, size, base, "detections")
    texts = _filtered_boxes(text.items, MAX_TEXTS, size, base, "texts")
    scale, basis, samples, rejected, axes = _resolved_scale(whole, texts, fallback_mm_per_px, base)
    checks: dict[str, dict[str, float]] = {}
    anisotropic, same_plan = _anisotropy(texts, _plan_frame(whole, float(scale)))
    if axes is not None and not anisotropic:  # T3: hai trục lệch <= 3 % vẫn là nghi vấn, không im lặng
        anisotropic = {"x": round(axes[0], 6), "y": round(axes[1], 6)}
    if anisotropic:
        checks["anisotropic"] = anisotropic
        if basis == "chain" and same_plan:  # cụm đông nhất chỉ là một trục; trung bình nhân chia đều phần giãn
            scale = Decimal(repr(math.sqrt(anisotropic["x"] * anisotropic["y"]))).quantize(_QUANTUM)
    noted = _note_scale(walls_px, texts, px_per_paper_mm) if basis == "default" else None
    noted = _trusted_note(noted, vector_page=vector_page, detections=detections, walls=walls_px)
    if noted is not None:
        scale, basis = noted, "note"
    estimated = basis == "default" and fallback_mm_per_px == _UNSET_PROJECT_SCALE and bool(walls_px)
    if estimated:  # U01: dự án chưa đặt tỉ lệ → tạm ước từ cửa đi và bề dày tường; không phải 1 mm/px
        guess, basis, spread = _estimate(_door_scale(detections, walls_px), _build_scale(walls_px, float(scale)))
        scale = Decimal(repr(guess)).quantize(_QUANTUM)
        if spread:
            checks["estimateSpread"] = spread
    source: Literal["pipeline", "project_default"] = "project_default" if basis == "default" else "pipeline"
    final = float(scale)
    build = final if source == "pipeline" else _build_scale(walls_px, final)
    trusted = source == "pipeline" and not estimated
    suggestions = _suggestions(page, walls_px, detections, texts, final if trusted else None) if furniture else ()
    # Tỉ lệ ước từ tường chỉ đúng tầm ±30 %: không dựng `Dimension` từ mẫu "khớp" nó (bước 7).
    attempt_source = source if trusted else "estimate"
    context = _Attempt(
        walls_px,
        detections,
        texts,
        samples,
        rejected,
        level_id,
        attempt_source,
        suggestions,
        arc_of,
        whole,
        first_of,
        furniture,
    )
    context = replace(context, page=page)
    dropped = base.copy()
    layer, dimensions = _attempt(context, scaling=Scaling(build, final), clock=clock, dropped=dropped)
    if build != final:
        try:
            layer = rescale_unreviewed(layer, build, final)
            dimensions = rescale_dimensions(dimensions, build, final)
        except RescaleError as exc:
            _log.warning("pipeline_build_rescale_fallback", extra={"entity_id": exc.entity_id})
            dropped = base.copy()
            layer, dimensions = _attempt(context, scaling=Scaling(final, final), clock=clock, dropped=dropped)
    return BuiltLayer(
        layer=layer,
        dimensions=dimensions,
        scale_mm_per_px=scale,
        scale_source=source,
        dropped={key: dropped[key] for key in DROPPED_KEYS},
        scale_basis=basis,
        scale_checks=checks,
        label_orientation=label_orientation(texts),
        area_notes=area_notes(texts),
    )


_ORIENTATION_KEYS: Final = frozenset({"vertical", "horizontal"})
_TEXT_ASPECT: Final = 1.5
"""Hộp chữ cao/rộng ≥ 1,5 là chữ đọc dọc (cùng ngưỡng xoay vùng của OCR, `apps.ml.text.reader.ROTATE_RATIO`)."""
_LABEL_MIN_LETTERS: Final = 3


def label_orientation(texts: Sequence[TextPx]) -> dict[str, int]:
    """Số nhãn chữ (≥ 3 chữ cái, không nằm trên đường kích thước) đọc dọc và đọc ngang; không nhãn nào → `{}`.

    Bản vẽ đặt đúng chiều ghi tên phòng nằm ngang; phần lớn nhãn dọc là trang xoay 90° (BENCH3D-14 `ROTATED_PLAN`).
    Số kích thước (trên đường kích thước `span_px`, hay chỉ chữ số) thường ghi dọc nên bị loại.
    """
    vertical = horizontal = 0
    for text in texts:
        if text.span_px is not None or sum(ch.isalpha() for ch in text.text) < _LABEL_MIN_LETTERS:
            continue
        width, height = text.box.x_max - text.box.x_min, text.box.y_max - text.box.y_min
        vertical += height >= _TEXT_ASPECT * width
        horizontal += width >= _TEXT_ASPECT * height
    return {"vertical": vertical, "horizontal": horizontal} if vertical + horizontal else {}


AREA_NOTE_MIN_M2: Final = 25.0
"""Số "N m²" nhỏ hơn là diện tích một phòng (phòng khách căn hộ hiếm quá 25 m²); từ đây trở lên có thể là cả căn."""
_AREA_NOTE: Final = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:m2|m²|㎡)", re.IGNORECASE)


def area_notes(texts: Sequence[TextPx]) -> tuple[float, ...]:
    """Các số diện tích "N m²" (N ≥ `AREA_NOTE_MIN_M2`) đọc được trên bản vẽ, tăng dần, không lặp; không có → `()`.

    Bản vẽ căn hộ thường ghi diện tích căn (tim tường hay thông thủy); bước quality so với tổng diện tích phòng đã
    dựng (BENCH3D-24 `AREA_TEXT_MISMATCH`). Đơn vị m² đủ phân biệt với số đo dài nên không lọc theo `span_px` (bước
    ghép chuỗi kích thước có khi gán nhầm đường kích thước cho chữ diện tích).
    """
    values = {float(match.replace(",", ".")) for text in texts for match in _AREA_NOTE.findall(text.text)}
    return tuple(sorted(value for value in values if value >= AREA_NOTE_MIN_M2))
