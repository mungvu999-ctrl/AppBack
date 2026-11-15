"""Lõi bước `wallSegmentation`: ảnh trang → mặt nạ tường → `walls.json` + `walls.png`.

Tách khỏi `tasks.py` để chạy được **không** cần kho, broker hay vòng sự kiện: ba nhánh
bộ tách (giả, cổ điển, ONNX) chỉ khác nhau ở cách dựng mặt nạ, phần còn lại — kiểm khổ,
vector hoá, kẹp trần `MAX_WALLS`, đổi sang hợp đồng `WallPx` — dùng chung một đường mã
nên J06/M01 (cùng vào → cùng bytes) không phụ thuộc nhánh nào.

Nhánh cổ điển kẹp `confidence ≤ CLASSIC_CONFIDENCE_CAP`: đó là heuristic hình thái, B5-05
không được đọc nó chắc ngang một model đã huấn luyện.

Trang PDF vector (UX41 B1) có thêm `PageVectors`: tường dựng từ nét PDF (`vector_walls`) với mặt
nạ làm trọng tài — tim thẳng tuyệt đối, bề dày đúng nét. Trọng tài không nhận đủ tường thì vector
hoá mặt nạ như trang raster. `walls.png` luôn là mặt nạ; trần `MAX_WALLS` và `NOT_A_FLOOR_PLAN`
áp cho cả hai nguồn.
"""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

import cv2
import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]  # onnxruntime 1.30 không có py.typed
from numpy.typing import NDArray

from apps.ml.runtime.errors import MODEL_FORMAT_UNSUPPORTED, NOT_A_FLOOR_PLAN
from apps.ml.walls.segformer import SegformerOnnxSegmenter
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.artifacts import MAX_WALLS, PointPx, WallPx, WallsResult, encode_mask, walls_to_json
from packages.ml_contracts.fakes import FakeWallSegmenter
from packages.ml_contracts.payloads import ModelRef
from packages.ml_contracts.ports import RgbImage
from packages.vision.preprocess.types import RgbImage as CheckedRgbImage
from packages.vision.walls.classic import classic_wall_mask, default_min_thickness_px
from packages.vision.walls.pdf_walls import PageVectors, vector_walls
from packages.vision.walls.types import WallSegment
from packages.vision.walls.vectorize import keep_longest, vectorize_with_stats

__all__ = [
    "CLASSIC_CONFIDENCE_CAP",
    "DRAWING_WALL_LENGTH_RATIO",
    "INK_REFINE_BRANCHES",
    "MIN_WALL_LENGTH_RATIO",
    "SUSPECT_WALL_LENGTH_RATIO",
    "WallsStepOutput",
    "segment_page",
    "to_wall_px",
]

_log: Final = logging.getLogger(__name__)

CLASSIC_CONFIDENCE_CAP: Final = 0.5
"""Trần độ tin của đường lùi cổ điển: hình thái học không chắc bằng model đã huấn luyện."""

MIN_WALL_LENGTH_RATIO: Final = 1.0
"""Tổng chiều dài tim tường tối thiểu, tính theo cạnh dài của trang, để nhận trang là bản vẽ mặt bằng.

Đo 2026-10-18 với SegFormer-B0 huấn luyện trên CubiCasa5k (IoU 0.816): 45 bản vẽ tập test thấp
nhất 3.02; 11 ảnh không phải bản vẽ (biểu đồ cột/đường/tròn, bảng, văn bản, sơ đồ khối, ảnh
chụp, ảnh chụp màn hình) cao nhất 0.23. Chỉ áp cho nhánh `onnx`: nhánh cổ điển cho ảnh chụp
tới 27.07 nên không phân biệt được, nhánh giả chỉ phục vụ trang tổng hợp.

Dưới số này mà vẫn ≥ `SUSPECT_WALL_LENGTH_RATIO` thì trang chỉ bị từ chối khi tường không gom thành một bản vẽ gọn
(`DRAWING_WALL_LENGTH_RATIO`): căn nhỏ giữa trang trắng, tờ cả tầng nhiều căn nhỏ cũng tụt dưới 1.0 (BENCH3D-14).
"""

SUSPECT_WALL_LENGTH_RATIO: Final = 0.3
"""Sàn tuyệt đối theo cạnh trang: ảnh không phải bản vẽ của bộ hiệu chỉnh cao nhất 0.23 — dưới sàn luôn từ chối."""

DRAWING_WALL_LENGTH_RATIO: Final = 2.0
"""Tổng tim tường theo cạnh dài hộp bao chính các tường, tối thiểu khi tỉ số theo trang rơi vào dải nghi ngờ.

Bản vẽ khép phòng: một phòng vuông đã 4, mặt bằng CubiCasa ≥ 3.02 (hộp bao ≤ trang). Nét rời rải khắp ảnh (ảnh chụp,
phối cảnh) có hộp bao gần bằng trang nên tỉ số này ≈ tỉ số theo trang < 1. Chưa hiệu chỉnh trên SegFormer thật: log
`walls_not_a_floor_plan` ghi cả hai tỉ số để hiệu chỉnh lại.
"""

type Used = Literal["onnx", "classic", "fake"]
type WallsFrom = Literal["vector", "mask"]

INK_REFINE_BRANCHES: Final[frozenset[str]] = frozenset({"onnx"})
"""Nhánh được đọc lại tim/bề dày tường từ nét mực trang (UX41-B2, `packages.vision.walls.ink_refine`).

Chỉ `onnx`: mặt nạ SegFormer đo tường hai nét từ mép ngoài tới mép ngoài (dư một bề rộng nét, U19). Mặt
nạ cổ điển dựng từ chính mực, nhánh giả vẽ trang tổng hợp: không có gì để sửa mà đổi bytes J06. Rỗng = tắt.
"""


@dataclass(frozen=True, slots=True)
class WallsStepOutput:
    """Artifact của bước, kèm nhánh đã dùng, nguồn tường và số phần tử bỏ (`spur`, `short`, `cap`).

    `used`, `walls_from` và `dropped` không đi vào artifact — chúng là số đo cho log và test, còn
    `run_step` chỉ lấy `artifacts`.
    """

    artifacts: dict[str, bytes]
    used: Used
    dropped: Mapping[str, int]
    walls_from: WallsFrom = "mask"


def to_wall_px(seg: WallSegment) -> WallPx:
    """`WallSegment` (numpy thuần) → `WallPx` (hợp đồng), giữ nguyên từng con số.

    Làm tròn đã xong ở `vectorize_with_stats` ([6] bước 11); đổi kiểu ở đây mà tính lại
    số nào thì `walls.json` của cùng một mặt nạ sẽ lệch giữa hai lượt giao (J06).
    """
    return WallPx(
        start=PointPx(x=seg.start[0], y=seg.start[1]),
        end=PointPx(x=seg.end[0], y=seg.end[1]),
        thickness_px=seg.thickness_px,
        confidence=seg.confidence,
        sagitta_px=seg.sagitta_px,
    )


def _mask_of(
    image: RgbImage,
    model: ModelRef,
    *,
    backend: str,
    session: ort.InferenceSession | None,
    px_per_paper_mm: float | None,
) -> tuple[NDArray[np.bool_], Used]:
    """Mặt nạ tường của trang và tên nhánh đã dựng nó.

    `ML_BACKEND=fake` thắng mọi thứ (M01: không model nào được nạp trong lượt giả);
    `ModelRef` cổ điển (`is_classic`) không có gì để nạp nên đi đường hình thái
    (`classic_wall_mask`: mở bỏ nét mảnh rồi lấp khe cửa sổ, nên mặt nạ có thể chứa điểm
    không có mực — dải giữa các nét cửa sổ, tường vẽ rỗng hai nét); còn lại
    `prepare` đã nạp `session` nên `cast` thay cho một nhánh chết không test nổi.

    Lệch khỏi prompt: `classic_wall_mask` nhận `RgbImage` của `packages.vision.preprocess`
    (dataclass có `.pixels`), không phải `ports.RgbImage` (alias mảng) — bọc ở đây, một view
    chỉ đọc trên chính mảng của `run_step`, không chép điểm ảnh.
    """
    if backend == "fake":
        return FakeWallSegmenter().segment(image), "fake"
    if model.is_classic:
        height, width = image.shape[:2]
        thickness = default_min_thickness_px(width, height, px_per_paper_mm)
        return classic_wall_mask(CheckedRgbImage(image), min_thickness_px=thickness), "classic"
    segmenter = SegformerOnnxSegmenter(cast("ort.InferenceSession", session))
    return segmenter.segment(image), "onnx"


def _capped(seg: WallSegment) -> WallSegment:
    """Cùng đoạn, độ tin không quá `CLASSIC_CONFIDENCE_CAP`."""
    if seg.confidence <= CLASSIC_CONFIDENCE_CAP:
        return seg
    return WallSegment(seg.start, seg.end, seg.thickness_px, CLASSIC_CONFIDENCE_CAP, seg.sagitta_px)


def segment_page(
    image: RgbImage,
    model: ModelRef,
    *,
    backend: str,
    session: ort.InferenceSession | None,
    px_per_paper_mm: float | None = None,
    run_id: str | None = None,
    vectors: PageVectors | None = None,
) -> WallsStepOutput:
    """Tách tường một trang đã nắn; cùng ảnh + cùng nhánh + cùng `vectors` → cùng bytes artifact (J06, M01).

    Mặt nạ khác khổ ảnh là model sai hợp đồng, không phải trang xấu →
    `MODEL_FORMAT_UNSUPPORTED`. Có `vectors` (PDF vector) và trọng tài mặt nạ nhận đủ tường →
    tường từ nét PDF (`walls_from="vector"`), không thì vector hoá mặt nạ. Model đã huấn luyện thấy
    tổng tường ngắn hơn `MIN_WALL_LENGTH_RATIO` lần cạnh dài trang mà không gom thành bản vẽ gọn (`_looks_like_plan`)
    → `NOT_A_FLOOR_PLAN`. Quá
    `MAX_WALLS` đoạn thì giữ các đoạn dài nhất và đếm `cap`, vì `WallsResult` từ chối nhiều hơn thế.
    """
    started = time.monotonic()
    mask, used = _mask_of(image, model, backend=backend, session=session, px_per_paper_mm=px_per_paper_mm)
    mask_ms = _since(started)
    if mask.shape != image.shape[:2]:
        raise PermanentError(MODEL_FORMAT_UNSUPPORTED)
    vectorized = time.monotonic()
    from_pdf = None if vectors is None else vector_walls(vectors, evidence=mask)
    walls_from: WallsFrom = "mask" if from_pdf is None else "vector"
    if from_pdf is None:
        gray = np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), np.uint8) if used in INK_REFINE_BRANCHES else None
        result = vectorize_with_stats(mask, gray, rect=True)
        walls, dropped_by = result.walls, result.dropped
    else:
        walls, dropped_by = from_pdf.walls, {}
    if used == "onnx" and not _looks_like_plan(walls, mask.shape, run_id):
        raise PermanentError(NOT_A_FLOOR_PLAN)
    dropped = {**dropped_by, "cap": max(0, len(walls) - MAX_WALLS)}
    if dropped["cap"]:
        walls = keep_longest(walls, MAX_WALLS)
    if used == "classic":
        walls = tuple(_capped(seg) for seg in walls)
    vector_ms = _since(vectorized)
    encoded = time.monotonic()
    artifacts = {
        "walls.json": walls_to_json(WallsResult(walls=tuple(to_wall_px(seg) for seg in walls))),
        "walls.png": encode_mask(mask),
    }
    _log.info(
        "walls_segmented",
        extra={
            "run_id": run_id,
            "used": used,
            "walls_from": walls_from,
            "walls": len(walls),
            "dropped": dropped,
            "mask_ms": mask_ms,
            "vectorize_ms": vector_ms,
            "encode_ms": _since(encoded),
        },
    )
    return WallsStepOutput(artifacts=artifacts, used=used, dropped=dropped, walls_from=walls_from)


def _looks_like_plan(walls: Sequence[WallSegment], shape: tuple[int, ...], run_id: str | None) -> bool:
    """Tổng tim tường ≥ `MIN_WALL_LENGTH_RATIO` cạnh dài trang, hoặc ≥ `SUSPECT_WALL_LENGTH_RATIO` cạnh trang và
    ≥ `DRAWING_WALL_LENGTH_RATIO` cạnh dài hộp bao các tường. Từ chối thì log hai tỉ số (hiệu chỉnh lại ngưỡng)."""
    total = _total_length(walls)
    page_ratio = total / max(shape[:2])
    if page_ratio >= MIN_WALL_LENGTH_RATIO:
        return True
    ends = np.asarray([point for seg in walls for point in (seg.start, seg.end)], dtype=np.float64).reshape(-1, 2)
    extent = float(np.ptp(ends, axis=0).max()) if len(ends) else 0.0
    drawing_ratio = total / extent if extent > 0 else 0.0
    if page_ratio >= SUSPECT_WALL_LENGTH_RATIO and drawing_ratio >= DRAWING_WALL_LENGTH_RATIO:
        return True
    _log.info(
        "walls_not_a_floor_plan",
        extra={"run_id": run_id, "page_ratio": round(page_ratio, 3), "drawing_ratio": round(drawing_ratio, 3)},
    )
    return False


def _total_length(walls: Sequence[WallSegment]) -> float:
    """Tổng chiều dài tim tường (px)."""
    return sum(float(np.hypot(seg.end[0] - seg.start[0], seg.end[1] - seg.start[1])) for seg in walls)


def _since(started: float) -> int:
    """Mili giây trôi qua từ một mốc `time.monotonic()`."""
    return int((time.monotonic() - started) * 1000)
