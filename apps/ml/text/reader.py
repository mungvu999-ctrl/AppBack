"""OCR trang bản vẽ: dò vùng chữ bằng bộ dò của wheel, nhận dạng bằng phiên ONNX đã kiểm.

Hai model, hai mức tin cậy khác nhau (BE-00 §9): bộ **dò** là model nhà cung cấp nằm
trong wheel `rapidocr-onnxruntime` đã khoá ở `uv.lock` — nó chỉ nhận ảnh trang và trả
tứ giác, không quyết chữ, nên dùng thẳng lớp `TextDetector` của wheel; bộ **nhận dạng**
có thể là model người dùng tải lên nên **chỉ** tới đây qua `load_onnx` (checksum, luật
ONNX không tin) và bảng ký tự của nó cũng bị kiểm như dữ liệu không tin (K12, [7]).

Trang bản vẽ rộng hơn nhiều so với khổ bộ dò quen chạy, nên ảnh được chia lát
`DET_TILE_PX` chồng `DET_OVERLAP_PX`: chữ nằm vắt mép **trong** của lát bị bỏ ở lát đó
và được lát bên cạnh bắt trọn; phần chồng sinh tứ giác trùng, gộp theo IoU hộp bao.

Mọi lượt chạy đều CPU (`load_onnx` đã ép `CPUExecutionProvider`, bộ dò cấu hình
`use_cuda: false`) — không bao giờ lấy khoá `gpu:0`.
"""

import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Self

import cv2
import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]  # onnxruntime 1.30 không có py.typed
import rapidocr_onnxruntime  # type: ignore[import-untyped]  # wheel không có py.typed
from numpy.typing import NDArray
from rapidocr_onnxruntime.ch_ppocr_det import TextDetector  # type: ignore[import-untyped]  # wheel không có py.typed
from rapidocr_onnxruntime.utils import read_yaml  # type: ignore[import-untyped]  # wheel không có py.typed

from apps.ml.runtime.errors import MODEL_FORMAT_UNSUPPORTED, ORT_ERRORS
from packages.messaging.run_cancel import checkpoint
from packages.messaging.tasks import PermanentError
from packages.ml_contracts.artifacts import BoxPx, TextPx
from packages.ml_contracts.ports import RgbImage

__all__ = [
    "DET_OVERLAP_PX",
    "DET_TILE_PX",
    "MAX_TEXT_ITEMS",
    "REC_HEIGHT_PX",
    "REC_MAX_WIDTH_PX",
    "TEXT_CONFIDENCE_MIN",
    "UPRIGHT_SURE_CONFIDENCE",
    "UPSIDE_DOWN_SUSPECT",
    "RapidOcrReader",
    "ctc_decode",
    "rec_characters",
]

DET_TILE_PX: Final = 1600
DET_OVERLAP_PX: Final = 200
REC_HEIGHT_PX: Final = 48
REC_MAX_WIDTH_PX: Final = 960
TEXT_CONFIDENCE_MIN: Final = 0.5
MAX_TEXT_ITEMS: Final = 5000

REC_WIDTH_STEP_PX: Final = 80
"""`W` làm tròn lên bội số này: vài khổ vào cố định cho ORT tái dùng kế hoạch của phiên."""

REC_MIN_WIDTH_PX: Final = 320
"""Sàn của `W`, bằng khổ `rec_img_shape` 48x320 của PP-OCR: tensor hẹp hơn làm số 0 cuối chuỗi
ngắn đọc thành 8 (`8.000` → `8.008`); đo seed 100-109: 105/122 → 114/122 chữ kích thước (NO-254)."""

MAX_CHARACTERS: Final = 20_000
"""Trần dòng của metadata `character` ([7]): bảng ký tự là dữ liệu của model không tin."""

TEXT_MAX_CHARS: Final = 64
"""Trần của `TextPx.text` (B5-01); chuỗi dài hơn là rác của bộ đọc, bỏ chứ không cắt."""

EDGE_TOLERANCE_PX: Final = 2.0
MERGE_IOU: Final = 0.5
ROTATE_RATIO: Final = 1.5
DARK_BACKGROUND_MEAN: Final = 127.0
BLANK_INDEX: Final = 0

UPRIGHT_SURE_CONFIDENCE: Final = 0.9
"""Lượt không xoay tin cậy từ mức này thì bỏ lượt xoay 180° (PERF-03 P19: mỗi ô chữ từng
chạy rec hai lần). Cao hơn hẳn `TEXT_CONFIDENCE_MIN` vì chữ ngược đọc thẳng vẫn có thể ra
chuỗi tin cậy vừa (`8.00` → `00.8`); dưới ngưỡng thì vẫn đọc cả hai và lấy lượt tốt hơn."""

UPSIDE_DOWN_SUSPECT: Final = re.compile(r"0[0-9]{2,}")
"""Số nguyên ≥ 3 chữ số có số 0 đứng đầu: kích thước không bao giờ ghi thế, nhưng số tận cùng bằng 0 đọc ngược 180°
ra đúng dạng này với tin cậy cao (`6000` dọc → `0009` 0.999, f-19) vì 0/1/6/8/9 lật vẫn là chữ số. Hai chữ số
(`01`, `02`) bỏ ngoài: đó thường là số thứ tự căn/phòng/trục, đọc thẳng là đúng."""

_PLAIN_NUMBER: Final = re.compile(r"[1-9][0-9]*")
"""Lượt 180° chỉ thay lượt nghi ngược khi là số nguyên không số 0 đầu, **cùng số ký tự** (lật không đổi số chữ số)."""


@lru_cache(maxsize=1)
def _detector() -> TextDetector:
    """Bộ dò của wheel, dựng một lần mỗi tiến trình (phiên ORT giữ cả trọng số trong RAM).

    Cấu hình lấy nguyên `Det` của `config.yaml` trong wheel — cùng ngưỡng mà `RapidOCR()`
    dùng — chỉ thay `model_path` thành đường tuyệt đối tới model đi kèm wheel.
    """
    package = Path(str(rapidocr_onnxruntime.__file__)).parent
    config: dict[str, Any] = dict(read_yaml(package / "config.yaml")["Det"])
    config["model_path"] = str(package / config["model_path"])
    return TextDetector(config)


def rec_characters(session: ort.InferenceSession) -> tuple[str, ...]:
    """Bảng ký tự CTC của model nhận dạng: `("blank", *ký tự của metadata, " ")`.

    Metadata `character` là dữ liệu của model **không tin** ([7]): thiếu, rỗng, quá
    `MAX_CHARACTERS` dòng hay có dòng khác đúng một ký tự → `MODEL_FORMAT_UNSUPPORTED`.
    Vị trí `blank` ở 0 và khoảng trắng ở cuối là quy ước PP-OCR mà model được huấn luyện.
    """
    raw = session.get_modelmeta().custom_metadata_map.get("character")
    if raw is None:
        raise PermanentError(MODEL_FORMAT_UNSUPPORTED)
    lines = str(raw).splitlines()
    if not lines or len(lines) > MAX_CHARACTERS or any(len(line) != 1 for line in lines):
        raise PermanentError(MODEL_FORMAT_UNSUPPORTED)
    return ("blank", *lines, " ")


def ctc_decode(probs: NDArray[np.float32], characters: tuple[str, ...]) -> tuple[str, float]:
    """Giải CTC greedy một vùng: `probs` hình `(T, C)` → chuỗi và độ tin cậy trung bình.

    Argmax từng bước, gộp chỉ số lặp liền nhau, bỏ `blank` (chỉ số 0); `confidence` là
    trung bình xác suất lớn nhất của **các bước còn giữ**, nên chuỗi rỗng trả `("", 0.0)`
    chứ không phải trung bình của mảng rỗng.
    """
    indices = probs.argmax(axis=1)
    best = probs.max(axis=1)
    keep = indices != BLANK_INDEX
    keep[1:] &= indices[1:] != indices[:-1]
    picked = indices[keep]
    if picked.size == 0:
        return "", 0.0
    return "".join(characters[int(index)] for index in picked.tolist()), float(best[keep].mean())


def _starts(size: int, tile: int, overlap: int) -> list[int]:
    """Điểm bắt đầu các lát theo một trục; lát cuối dịch sát mép, không đệm ảnh."""
    if size <= tile:
        return [0]
    step = tile - overlap
    starts = list(range(0, size - tile, step))
    starts.append(size - tile)
    return starts


def _tiles(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Lưới lát `(x0, y0, x1, y1)` phủ hết trang, chồng nhau `DET_OVERLAP_PX`."""
    return [
        (x0, y0, min(x0 + DET_TILE_PX, width), min(y0 + DET_TILE_PX, height))
        for y0 in _starts(height, DET_TILE_PX, DET_OVERLAP_PX)
        for x0 in _starts(width, DET_TILE_PX, DET_OVERLAP_PX)
    ]


def _detect_tile(detector: TextDetector, tile: RgbImage) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Tứ giác `(N, 4, 2)` và điểm dò của một lát, toạ độ trong lát.

    Gọi thẳng ba phần của `TextDetector` thay vì `__call__`: `__call__` của wheel 1.4.4
    vứt mảng điểm dò đi, mà luật gộp trùng ở đây cần giữ tứ giác điểm cao hơn.
    """
    empty = (np.empty((0, 4, 2), dtype=np.float32), np.empty(0, dtype=np.float32))
    prepared = detector.get_preprocess(max(tile.shape[:2]))(tile)
    if prepared is None:
        return empty
    quads, scores = detector.postprocess_op(detector.infer(prepared)[0], tile.shape[:2])
    if len(quads) == 0:
        return empty
    return np.asarray(quads, dtype=np.float32).reshape(-1, 4, 2), np.asarray(scores, dtype=np.float32)


def _inner_edge(
    quads: NDArray[np.float32], tile: tuple[int, int, int, int], page: tuple[int, int]
) -> NDArray[np.bool_]:
    """Tứ giác nào chạm mép **trong** của lát (≤ `EDGE_TOLERANCE_PX`) — chữ bị lát này cắt.

    Mép trùng biên trang không phải mép trong: chữ sát lề trang không có lát nào bắt hộ.
    """
    x0, y0, x1, y1 = tile
    width, height = page
    low, high = quads.min(axis=1), quads.max(axis=1)
    touched = np.zeros(len(quads), dtype=np.bool_)
    if x0 > 0:
        touched |= low[:, 0] <= EDGE_TOLERANCE_PX
    if y0 > 0:
        touched |= low[:, 1] <= EDGE_TOLERANCE_PX
    if x1 < width:
        touched |= high[:, 0] >= (x1 - x0) - 1 - EDGE_TOLERANCE_PX
    if y1 < height:
        touched |= high[:, 1] >= (y1 - y0) - 1 - EDGE_TOLERANCE_PX
    return touched


def _iou(box: NDArray[np.float64], others: NDArray[np.float64]) -> NDArray[np.float64]:
    """IoU hộp bao trục của một hộp `(4,)` với `(N, 4)` hộp khác (`x0, y0, x1, y1`)."""
    lo = np.maximum(box[:2], others[:, :2])
    hi = np.minimum(box[2:], others[:, 2:])
    inter = np.prod(np.clip(hi - lo, 0.0, None), axis=1)
    area = float(np.prod(np.clip(box[2:] - box[:2], 0.0, None)))
    areas = np.prod(np.clip(others[:, 2:] - others[:, :2], 0.0, None), axis=1)
    union = area + areas - inter
    return np.divide(inter, union, out=np.zeros_like(union), where=union > 0)


def _merge(quads: list[NDArray[np.float32]], scores: list[float]) -> list[NDArray[np.float32]]:
    """Bỏ tứ giác trùng (IoU hộp bao ≥ `MERGE_IOU`), giữ cái có điểm dò cao hơn.

    Duyệt theo điểm giảm dần nên cái được giữ luôn là cái điểm cao nhất của cụm; hoà
    điểm thì `argsort` ổn định giữ cái tới trước (lát trên/trái), nên kết quả tất định.
    """
    if not quads:
        return []
    boxes = np.array([[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads], dtype=np.float64)
    kept: list[int] = []
    for raw in np.argsort(-np.asarray(scores, dtype=np.float64), kind="stable").tolist():
        index = int(raw)
        if kept and _iou(boxes[index], boxes[kept]).max() >= MERGE_IOU:
            continue
        kept.append(index)
    return [quads[index] for index in kept]


def _detect(detector: TextDetector, image: RgbImage) -> list[NDArray[np.float32]]:
    """Tứ giác chữ của cả trang, toạ độ trang, đã bỏ mép lát và gộp trùng."""
    height, width = image.shape[:2]
    quads: list[NDArray[np.float32]] = []
    scores: list[float] = []
    for tile in _tiles(width, height):
        checkpoint()  # K-24: lượt bị huỷ thì dừng giữa hai lát dò chữ
        x0, y0, x1, y1 = tile
        found, found_scores = _detect_tile(detector, np.ascontiguousarray(image[y0:y1, x0:x1]))
        if len(found) == 0:
            continue
        usable = ~_inner_edge(found, tile, (width, height))
        quads.extend(found[usable] + np.array([x0, y0], dtype=np.float32))
        scores.extend(found_scores[usable].tolist())
    return _merge(quads, scores)


def _otsu(crop: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Nhị phân hoá Otsu trên ảnh xám, đảo khi nền tối, trả 3 kênh chỉ có 0 và 255.

    Bộ nhận dạng PP-OCR học trên chữ tối nền sáng; ô nền đen của khung tên mà không đảo
    thì mọi vùng trong đó đọc ra rác.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(gray.mean()) < DARK_BACKGROUND_MEAN:
        binary = cv2.bitwise_not(binary)
    return np.asarray(cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB), dtype=np.uint8)


def _crop(image: RgbImage, quad: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Nắn phối cảnh tứ giác về chữ nhật cạnh bằng cạnh tứ giác, dựng ngang, rồi Otsu.

    Tứ giác từ bộ dò theo thứ tự trên-trái, trên-phải, dưới-phải, dưới-trái; vùng đứng
    (cao/rộng ≥ `ROTATE_RATIO`) xoay 90° ngược chiều kim đồng hồ để hàng chữ nằm ngang
    như bộ nhận dạng chờ. Tứ giác suy biến trả ảnh rỗng, người gọi bỏ.
    """
    width = round(max(np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[3] - quad[2])))
    height = round(max(np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2])))
    if width < 1 or height < 1:
        return np.empty((0, 0, 3), dtype=np.uint8)
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), target)
    patch = cv2.warpPerspective(image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)
    if height / width >= ROTATE_RATIO:
        patch = cv2.rotate(patch, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return _otsu(np.asarray(patch, dtype=np.uint8))


def _rec_widths(width: int, height: int) -> tuple[int, int]:
    """`(rộng sau khi co, rộng tensor)`: giữ tỉ lệ về cao 48, đệm lên bội `REC_WIDTH_STEP_PX`, ≥ `REC_MIN_WIDTH_PX`."""
    resized = min(max(math.ceil(REC_HEIGHT_PX * width / height), 1), REC_MAX_WIDTH_PX)
    stepped = math.ceil(resized / REC_WIDTH_STEP_PX) * REC_WIDTH_STEP_PX
    padded = min(max(stepped, REC_MIN_WIDTH_PX), REC_MAX_WIDTH_PX)
    return resized, padded


def _rec_input(crop: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Tensor `(1, 3, 48, W)` chuẩn hoá `(x/255 - 0,5)/0,5`, phần thừa đệm 0 bên phải."""
    height, width = crop.shape[:2]
    resized, padded = _rec_widths(width, height)
    scaled = cv2.resize(crop, (resized, REC_HEIGHT_PX), interpolation=cv2.INTER_LINEAR)
    normalised = (np.asarray(scaled, dtype=np.float32) / 255.0 - 0.5) / 0.5
    tensor = np.zeros((1, 3, REC_HEIGHT_PX, padded), dtype=np.float32)
    tensor[0, :, :, :resized] = normalised.transpose(2, 0, 1)
    return tensor


def _item(text: str, confidence: float, quad: NDArray[np.float32], page: tuple[int, int]) -> TextPx | None:
    """Một mục `TextPx`, hay `None` khi chuỗi hay hộp không dùng được (đầu ra AI xấu, BE-00 §9).

    Chuỗi chuẩn hoá NFC rồi `strip`; bỏ khi rỗng, dài quá `TEXT_MAX_CHARS`, hay tin cậy
    dưới `TEXT_CONFIDENCE_MIN`. Hộp là hộp bao trục của tứ giác, kẹp trong trang và làm
    tròn 2 chữ số; kẹp xong mà suy biến (chữ nằm trọn trên biên) thì cũng bỏ.
    """
    cleaned = unicodedata.normalize("NFC", text).strip()
    if not cleaned or len(cleaned) > TEXT_MAX_CHARS or confidence < TEXT_CONFIDENCE_MIN:
        return None
    width, height = page
    x_min, x_max = (round(float(np.clip(value, 0.0, width)), 2) for value in (quad[:, 0].min(), quad[:, 0].max()))
    y_min, y_max = (round(float(np.clip(value, 0.0, height)), 2) for value in (quad[:, 1].min(), quad[:, 1].max()))
    if x_min >= x_max or y_min >= y_max:
        return None
    box = BoxPx(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
    return TextPx(text=cleaned, box=box, confidence=min(confidence, 1.0))


class RapidOcrReader:
    """`TextReader` chạy trên phiên ONNX nhận dạng đã qua `load_onnx` (B5-01).

    Bất biến: bảng ký tự đã kiểm khớp số lớp `C` của model **lúc dựng**, nên `read`
    không bao giờ tra ngoài bảng; mọi lỗi của model là `PermanentError` có mã, không
    `except Exception` (R-16).
    """

    def __init__(self, session: ort.InferenceSession, characters: tuple[str, ...]) -> None:
        """Dùng `from_session`: hàm dựng không kiểm gì, nó chỉ giữ phiên và bảng đã kiểm."""
        self._session = session
        self._characters = characters
        self._input = session.get_inputs()[0].name
        self._output = session.get_outputs()[0].name

    @classmethod
    def from_session(cls, rec_session: ort.InferenceSession, *, pinned_name: str | None = None) -> Self:
        """Dựng bộ đọc từ phiên nhận dạng; bảng ký tự lệch số lớp của model → `MODEL_FORMAT_UNSUPPORTED`.

        `pinned_name` nhận vào để người gọi (task, B6-04b) khỏi phân nhánh: model rec
        ghim của wheel **có** metadata `character` nên không có đường lùi nào theo tên
        ghim — xem "Lệch khỏi prompt" của B5-04.
        """
        characters = rec_characters(rec_session)
        classes = rec_session.get_outputs()[0].shape[-1]
        if isinstance(classes, int) and classes != len(characters):
            raise PermanentError(MODEL_FORMAT_UNSUPPORTED)
        return cls(rec_session, characters)

    def _run(self, crop: NDArray[np.uint8]) -> tuple[str, float]:
        """Một lượt nhận dạng; `onnxruntime` ném lúc chạy → `MODEL_FORMAT_UNSUPPORTED`."""
        try:
            probs = self._session.run([self._output], {self._input: _rec_input(crop)})[0]
        except ORT_ERRORS as exc:
            raise PermanentError(MODEL_FORMAT_UNSUPPORTED) from exc
        return ctc_decode(np.asarray(probs, dtype=np.float32)[0], self._characters)

    def _best(self, crop: NDArray[np.uint8]) -> tuple[str, float]:
        """Đọc hướng 0°; dưới `UPRIGHT_SURE_CONFIDENCE` đọc thêm 180° và lấy lượt tin cậy hơn (hoà → 0°).

        Lượt 0° ra `UPSIDE_DOWN_SUSPECT` thì luôn đọc thêm 180°, kể cả khi tin cậy ≥ ngưỡng, và nhận lượt 180° khi nó
        đủ `TEXT_CONFIDENCE_MIN` và là `_PLAIN_NUMBER` cùng độ dài; còn lại vẫn theo tin cậy.

        Thay cho bộ phân loại góc của `RapidOCR()`: bộ ấy lật ngược chuỗi gần đối xứng và
        đo được 67/122 chữ kích thước đúng, tắt nó đo được 114/122 (NO-063).
        """
        upright = self._run(crop)
        suspect = UPSIDE_DOWN_SUSPECT.fullmatch(upright[0].strip()) is not None
        if upright[1] >= UPRIGHT_SURE_CONFIDENCE and not suspect:
            return upright
        flipped = self._run(np.asarray(cv2.rotate(crop, cv2.ROTATE_180), dtype=np.uint8))
        turned = flipped[0].strip()
        if (
            suspect
            and flipped[1] >= TEXT_CONFIDENCE_MIN
            and _PLAIN_NUMBER.fullmatch(turned)
            and len(turned) == len(upright[0].strip())
        ):
            return flipped
        return upright if upright[1] >= flipped[1] else flipped

    def read(self, image: RgbImage) -> tuple[TextPx, ...]:
        """Chữ của cả trang, sắp theo `(y_min, x_min)`, tối đa `MAX_TEXT_ITEMS` mục."""
        height, width = image.shape[:2]
        items: list[TextPx] = []
        for quad in _detect(_detector(), image):
            checkpoint()  # K-24: thưa theo `CHECK_INTERVAL_S`, rẻ dù trang có hàng trăm dòng chữ
            crop = _crop(image, quad)
            if crop.size == 0:
                continue
            text, confidence = self._best(crop)
            item = _item(text, confidence, quad, (width, height))
            if item is not None:
                items.append(item)
        items.sort(key=lambda item: (item.box.y_min, item.box.x_min))
        return tuple(items[:MAX_TEXT_ITEMS])
