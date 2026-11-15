"""Lõi bước tách tường: chọn nhánh, kiểm khổ mặt nạ, trần `MAX_WALLS`, kẹp độ tin cổ điển.

Chạy `segment_page` trần — không kho, không broker, không vòng sự kiện — nên mỗi luật một
test ngắn. Bộ tách ONNX chạy `onnxruntime` **thật** trên model tí hon của `onnx_fixtures`;
chỉ hai chỗ thay bộ tách bằng một `WallSegmenter` dựng tay (mặt nạ sai khổ, mặt nạ nhiều
đoạn) vì không mặt nạ thật nào dựng được hai cảnh ấy — đó là fake ở đúng tầng cổng (K23).
"""

import asyncio
import logging

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]  # onnxruntime 1.30 không có py.typed
import pytest
from numpy.typing import NDArray

from apps.ml.runtime.settings import MlSettings
from apps.ml.runtime.tasks_util import InferContext
from apps.ml.walls import step as step_module
from apps.ml.walls import tasks as tasks_module
from apps.ml.walls.step import CLASSIC_CONFIDENCE_CAP, segment_page, to_wall_px
from apps.ml.walls.tasks import MODEL_VERSION_FAMILY_MISMATCH, _prepare
from apps.ml.walls.tests.helpers import classic_ref, infer_payload, put_sync, storage_ref
from apps.ml.walls.tests.onnx_fixtures import make_threshold_segformer
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.artifacts import MAX_WALLS, decode_mask, walls_from_json
from packages.ml_contracts.synthetic import render_plan
from packages.storage.local import LocalDiskStorage
from packages.vision.walls.pdf_walls import PageVectors
from packages.vision.walls.types import WallSegment

PLAN = render_plan(101)
PAGE_H, PAGE_W = PLAN.walls_mask.shape


def walls_of(output: step_module.WallsStepOutput) -> tuple[object, ...]:
    """Các tường trong `walls.json` của một lượt chạy."""
    return walls_from_json(output.artifacts["walls.json"]).walls


class _FixedSegmenter:
    """`WallSegmenter` trả đúng một mặt nạ dựng sẵn, bất kể ảnh vào."""

    def __init__(self, mask: NDArray[np.bool_]) -> None:
        """`mask` là thứ `segment` trả về mỗi lần gọi."""
        self.mask = mask

    def segment(self, image: NDArray[np.uint8]) -> NDArray[np.bool_]:
        """Mặt nạ dựng sẵn; cố ý **không** theo khổ `image`."""
        return self.mask


def use_segmenter(monkeypatch: pytest.MonkeyPatch, mask: NDArray[np.bool_]) -> None:
    """Thay bộ tách của nhánh `fake` bằng một bộ trả `mask`."""
    monkeypatch.setattr(step_module, "FakeWallSegmenter", lambda: _FixedSegmenter(mask))


def test_segment_page_uses_the_fake_segmenter_on_fake_backend() -> None:
    """`ML_BACKEND=fake`: `used="fake"` và mặt nạ ghi ra là đúng đáp án của trang."""
    output = segment_page(PLAN.pixels, classic_ref(), backend="fake", session=None)
    assert output.used == "fake"
    mask = decode_mask(output.artifacts["walls.png"], width_px=PAGE_W, height_px=PAGE_H)
    assert np.array_equal(mask, PLAN.walls_mask)
    assert walls_of(output)


def test_segment_page_falls_back_to_classic_for_an_inactive_model() -> None:
    """`ModelRef` cổ điển ở backend `onnx`: `used="classic"` và vẫn thấy tường."""
    output = segment_page(PLAN.pixels, classic_ref(), backend="onnx", session=None)
    assert output.used == "classic"
    assert walls_of(output)


def test_segment_page_caps_confidence_on_the_classic_branch() -> None:
    """Đường lùi hình thái không được trông chắc bằng model: mọi `confidence` ≤ 0,5."""
    output = segment_page(PLAN.pixels, classic_ref(), backend="onnx", session=None)
    walls = walls_from_json(output.artifacts["walls.json"]).walls
    assert walls
    assert max(wall.confidence for wall in walls) <= CLASSIC_CONFIDENCE_CAP


def test_segment_page_runs_onnx_when_the_model_is_pinned_to_a_version() -> None:
    """`ModelRef` dạng storage: nhánh ONNX chạy thật trên phiên đã nạp, `used="onnx"`."""
    data = make_threshold_segformer()
    ref, _ = storage_ref(data)
    session = ort.InferenceSession(data, providers=["CPUExecutionProvider"])
    output = segment_page(PLAN.pixels, ref, backend="onnx", session=session)
    assert output.used == "onnx"
    mask = decode_mask(output.artifacts["walls.png"], width_px=PAGE_W, height_px=PAGE_H)
    assert mask.shape == (PAGE_H, PAGE_W)


def test_segment_page_rejects_a_page_without_walls_on_the_onnx_branch() -> None:
    """Model thật không thấy tường nào trên trang trắng → `NOT_A_FLOOR_PLAN`, không artifact rỗng."""
    data = make_threshold_segformer()
    ref, _ = storage_ref(data)
    session = ort.InferenceSession(data, providers=["CPUExecutionProvider"])
    blank = np.full_like(PLAN.pixels, 255)
    with pytest.raises(PermanentError) as caught:
        segment_page(blank, ref, backend="onnx", session=session)
    assert caught.value.code == "NOT_A_FLOOR_PLAN"


def test_segment_page_keeps_a_page_without_walls_on_the_fake_branch() -> None:
    """Nhánh giả không kiểm "có phải bản vẽ": trang trắng vẫn ra `walls.json` rỗng."""
    blank = np.full_like(PLAN.pixels, 255)
    output = segment_page(blank, classic_ref(), backend="fake", session=None)
    assert walls_of(output) == ()


def test_segment_page_rejects_a_mask_of_another_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mặt nạ khác khổ ảnh là model sai hợp đồng → `MODEL_FORMAT_UNSUPPORTED`."""
    use_segmenter(monkeypatch, np.zeros((PAGE_H // 2, PAGE_W), dtype=np.bool_))
    with pytest.raises(PermanentError) as caught:
        segment_page(PLAN.pixels, classic_ref(), backend="fake", session=None)
    assert caught.value.code == "MODEL_FORMAT_UNSUPPORTED"


def bar_grid(bands: int, columns: int) -> NDArray[np.bool_]:
    """Lưới `bands x columns` thanh ngang 15 x 3 px, rời nhau — không vòng theo điểm ảnh.

    Khe ngang 8 px > 2 bề dày nên bước chữ nhật (WALLS-RECT) không gộp các thanh cùng hàng."""
    rows = np.arange(bands * 6) % 6 < 3
    cols = np.arange(columns * 23) % 23 < 15
    return np.logical_and(rows[:, None], cols[None, :])


def test_segment_page_keeps_the_longest_walls_over_the_contract_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quá `MAX_WALLS` đoạn: giữ đúng trần và đếm phần bỏ vào `dropped["cap"]`."""
    mask = bar_grid(141, 150)
    use_segmenter(monkeypatch, mask)
    image = np.zeros((*mask.shape, 3), dtype=np.uint8)
    output = segment_page(image, classic_ref(), backend="fake", session=None)
    assert output.dropped["cap"] > 0
    assert len(walls_of(output)) == MAX_WALLS


def test_segment_page_counts_no_cap_when_under_the_limit() -> None:
    """Trang thường: `dropped` có khoá `cap` bằng 0, không phải thiếu khoá."""
    output = segment_page(PLAN.pixels, classic_ref(), backend="fake", session=None)
    assert output.dropped["cap"] == 0


def test_segment_page_logs_no_object_key(caplog: pytest.LogCaptureFixture) -> None:
    """Bản ghi `walls_segmented` mang số đo, **không** mang khoá object hay đường tệp."""
    caplog.set_level(logging.INFO, logger="apps.ml.walls.step")
    payload = infer_payload(width=PAGE_W, height=PAGE_H, model_ref=classic_ref())
    segment_page(PLAN.pixels, classic_ref(), backend="fake", session=None, run_id=payload.run_id)
    (record,) = [item for item in caplog.records if item.message == "walls_segmented"]
    assert record.__dict__["run_id"] == payload.run_id
    assert record.__dict__["used"] == "fake"
    assert {"mask_ms", "vectorize_ms", "encode_ms", "dropped", "walls"} <= set(record.__dict__)
    assert not {"page_key", "artifact_prefix"} & set(record.__dict__)


def test_to_wall_px_keeps_every_number() -> None:
    """Đổi kiểu sang hợp đồng không tính lại số nào (nếu không, J06 hỏng)."""
    seg = WallSegment((1.25, 2.5), (30.75, 2.5), 12.5, 0.875)
    wall = to_wall_px(seg)
    assert (wall.start.x, wall.start.y) == seg.start
    assert (wall.end.x, wall.end.y) == seg.end
    assert (wall.thickness_px, wall.confidence) == (seg.thickness_px, seg.confidence)


def test_prepare_rejects_a_payload_of_another_family() -> None:
    """Payload bước khác hỏng ngay ở `prepare`, trước khi đọc trang → không chạm kho."""
    payload = infer_payload(width=PAGE_W, height=PAGE_H, step="dimensionReading")
    with pytest.raises(PermanentError) as caught:
        asyncio.run(_prepare(payload))
    assert caught.value.code == MODEL_VERSION_FAMILY_MISMATCH


# --- trang PDF vector (UX41 B1) -------------------------------------------------------------

FRAME_SIDE = 200
FRAME_BANDS = ((20, 20, 180, 30), (20, 170, 180, 180), (20, 20, 30, 180), (170, 20, 180, 180))
"""Bốn dải tường dày 10 px `(x0, y0, x1, y1)` quanh một phòng — vừa là mặt nạ vừa là nét PDF."""


def frame_mask() -> NDArray[np.bool_]:
    """Mặt nạ của bốn dải tường."""
    mask = np.zeros((FRAME_SIDE, FRAME_SIDE), dtype=np.bool_)
    for x0, y0, x1, y1 in FRAME_BANDS:
        mask[y0:y1, x0:x1] = True
    return mask


def frame_vectors() -> PageVectors:
    """Nét PDF của bốn dải: bốn cạnh mỗi hình chữ nhật, như PDFium đọc `re f`, bề dày 5..20 px."""
    edges = [
        edge
        for x0, y0, x1, y1 in FRAME_BANDS
        for edge in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0))
    ]
    return PageVectors(np.asarray(edges, dtype=np.float64), (5.0, 20.0), None)


def frame_page() -> NDArray[np.uint8]:
    """Ảnh trang cùng khổ mặt nạ (bộ tách giả bỏ qua nội dung ảnh)."""
    return np.zeros((FRAME_SIDE, FRAME_SIDE, 3), dtype=np.uint8)


def test_segment_page_builds_walls_from_pdf_vectors_when_the_mask_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Có nét PDF và mặt nạ xác nhận: tường từ nét (bề dày đúng 10 px), `walls.png` vẫn là mặt nạ."""
    use_segmenter(monkeypatch, frame_mask())
    output = segment_page(frame_page(), classic_ref(), backend="fake", session=None, vectors=frame_vectors())
    assert output.walls_from == "vector"
    walls = walls_from_json(output.artifacts["walls.json"]).walls
    assert len(walls) == 4
    assert {wall.thickness_px for wall in walls} == {10.0}
    mask = decode_mask(output.artifacts["walls.png"], width_px=FRAME_SIDE, height_px=FRAME_SIDE)
    assert np.array_equal(mask, frame_mask())
    assert output.dropped == {"cap": 0}


def test_segment_page_falls_back_to_the_mask_when_vectors_are_not_walls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mặt nạ không xác nhận nét PDF nào (mặt nạ rỗng) → vector hoá mặt nạ như trang raster."""
    use_segmenter(monkeypatch, np.zeros((FRAME_SIDE, FRAME_SIDE), dtype=np.bool_))
    output = segment_page(frame_page(), classic_ref(), backend="fake", session=None, vectors=frame_vectors())
    assert output.walls_from == "mask"
    assert walls_of(output) == ()
    assert {"spur", "short", "cap"} <= set(output.dropped)


def test_segment_page_without_vectors_uses_the_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không có nét PDF (trang raster): luôn vector hoá mặt nạ, kể cả khi mặt nạ là tường đẹp."""
    use_segmenter(monkeypatch, frame_mask())
    output = segment_page(frame_page(), classic_ref(), backend="fake", session=None)
    assert output.walls_from == "mask"
    assert walls_of(output)


def test_segment_page_caps_vector_walls_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trần `MAX_WALLS` áp cho cả tường từ nét PDF: giữ dài nhất, đếm `cap`."""
    use_segmenter(monkeypatch, frame_mask())
    monkeypatch.setattr(step_module, "MAX_WALLS", 2)
    output = segment_page(frame_page(), classic_ref(), backend="fake", session=None, vectors=frame_vectors())
    assert output.walls_from == "vector"
    assert len(walls_of(output)) == 2
    assert output.dropped == {"cap": 2}


def vector_context(monkeypatch: pytest.MonkeyPatch, storage: LocalDiskStorage) -> None:
    """Ngữ cảnh worker trỏ vào kho thật của test, bộ giả (không nạp model)."""
    context = InferContext(storage=storage, settings=MlSettings(ml_backend="fake"))
    monkeypatch.setattr(tasks_module, "infer_context", lambda: context)


def test_prepare_reads_the_page_vectors(local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """`vectors_key` có object hợp lệ → `prepare` trả đúng nét đã ghi (bước tiền xử lý → bước tường)."""
    vector_context(monkeypatch, local_storage)
    payload = infer_payload(width=FRAME_SIDE, height=FRAME_SIDE, with_vectors=True)
    assert payload.vectors_key is not None
    put_sync(local_storage, payload.vectors_key, frame_vectors().to_json(), "application/json")
    session, vectors = asyncio.run(_prepare(payload))
    assert session is None
    assert vectors is not None
    assert np.array_equal(vectors.segments, frame_vectors().segments)
    assert vectors.thickness_px == (5.0, 20.0)


@pytest.mark.parametrize("stored", [None, b"{", b"x" * 64], ids=["missing", "corrupt", "too_large"])
def test_prepare_ignores_unusable_vectors(
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stored: bytes | None,
) -> None:
    """`vectors.json` thiếu, hỏng hay quá trần → `None` và một cảnh báo không mang khoá; bước không hỏng."""
    vector_context(monkeypatch, local_storage)
    monkeypatch.setattr(tasks_module, "VECTORS_MAX_BYTES", 32)
    caplog.set_level(logging.WARNING, logger="apps.ml.walls.tasks")
    payload = infer_payload(width=FRAME_SIDE, height=FRAME_SIDE, with_vectors=True)
    assert payload.vectors_key is not None
    if stored is not None:
        put_sync(local_storage, payload.vectors_key, stored, "application/json")
    assert asyncio.run(_prepare(payload)) == (None, None)
    (record,) = [item for item in caplog.records if item.message == "walls_vectors_unusable"]
    assert record.__dict__["run_id"] == payload.run_id
    assert payload.vectors_key not in record.getMessage()


def _segments(*ends: tuple[tuple[float, float], tuple[float, float]]) -> tuple[WallSegment, ...]:
    return tuple(WallSegment(start, end, 4.0, 0.9) for start, end in ends)


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


AD_PAGE = (435, 800)
"""Trang quảng cáo 800 x 435 px (kiểu e-mpp-1): mặt bằng chỉ chiếm ~1/5 bề ngang."""


@pytest.mark.parametrize(
    ("walls", "expected"),
    [
        # Căn 160 x 110 px giữa trang trắng, một vách: 650 px = 0,81 cạnh trang nhưng 4,1 lần hộp bao → nhận.
        pytest.param(_segments(*_rect(300, 160, 460, 270), ((380, 160), (380, 270))), True, id="small-plan"),
        # Tám nét 50 px rải khắp ảnh (ảnh chụp/phối cảnh): 0,5 cạnh trang, hộp bao ~ trang → từ chối.
        pytest.param(
            _segments(*[((90 * i, 40 * i), (90 * i + 50, 40 * i)) for i in range(8)]), False, id="scattered-lines"
        ),
        # Bản vẽ gọn nhưng quá nhỏ (0,28 cạnh trang, dưới sàn 0,3) → từ chối như bộ hiệu chỉnh.
        pytest.param(_segments(*_rect(10, 10, 60, 50), ((35, 10), (35, 50))), False, id="below-floor"),
        # Mặt bằng lấp trang: ≥ 1 cạnh trang → nhận như cũ.
        pytest.param(_segments(*_rect(20, 20, 780, 415)), True, id="full-page-plan"),
    ],
)
def test_wall_evidence_gate_accepts_compact_drawings_on_a_large_page(
    walls: tuple[WallSegment, ...], expected: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """BENCH3D-14: dưới 1 cạnh trang chỉ nhận khi tường gom thành bản vẽ gọn; từ chối thì log hai tỉ số."""
    with caplog.at_level(logging.INFO, logger=step_module.__name__):
        assert step_module._looks_like_plan(walls, AD_PAGE, "run_01") is expected
    rejected = [r for r in caplog.records if r.getMessage() == "walls_not_a_floor_plan"]
    assert len(rejected) == (0 if expected else 1)
