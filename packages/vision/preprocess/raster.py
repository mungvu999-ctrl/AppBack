"""Nạp PNG/JPEG thành `RgbImage` chuẩn hoá, và mã hoá ngược ra PNG.

Trần điểm ảnh kiểm **trước** `load()` bằng `w x h` đọc từ đầu tệp (K13, BE-00 §11):
đó là điểm duy nhất chặn được bom nén mà không tốn bộ nhớ. Không gán
`Image.MAX_IMAGE_PIXELS` hay `ImageFile.LOAD_TRUNCATED_IMAGES` — đó là biến toàn
cục của cả tiến trình, không an toàn luồng. Mọi biến đổi làm bằng numpy/Pillow,
không vòng lặp Python theo điểm ảnh (K28).
"""

import io
import math
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

from packages.vision.preprocess.errors import VisionError
from packages.vision.preprocess.geometry import fit_size
from packages.vision.preprocess.sniff import sniff_kind
from packages.vision.preprocess.types import DEFAULT_MAX_PIXELS, RgbImage

_PIL_FORMATS: Final[dict[str, str]] = {"png": "PNG", "jpeg": "JPEG"}

# JPEG giải mã được ở 1/2 hay 1/4 kích thước ngay trong bộ giải DCT, nên ảnh tới
# 4 lần trần vẫn nạp được mà không bao giờ dựng mảng vượt trần. PNG không có cơ
# chế tương đương: vượt trần là hỏng.
_MAX_DRAFT_SCALE: Final = 4

_GRAY16_MODES: Final = frozenset(("I", "I;16", "I;16B", "I;16L"))
_RESIZE_MODES: Final = frozenset(("L", "RGB", "CMYK"))
"""Mode `Image.resize` lọc hộp được và `_to_rgb_array` đổi đúng sau đó (không alpha, không bảng màu)."""
_REDUCING_GAP: Final = 2.0
"""`reduce()` nguyên trước tới khi còn gấp >= 2 lần khổ đích, rồi lọc hộp phần còn lại (nhanh, ít bộ nhớ)."""
_WHITE: Final = (255, 255, 255)
_DARK_PAPER_MEDIAN: Final = 128
"""Trung vị độ sáng (kênh lớn nhất, như V của HSV) dưới số này là bản vẽ nền tối nét sáng
(bản in âm, ảnh chụp màn hình CAD nền đen, bench nt-05): đảo lại."""
_PAPER_SAMPLE_STRIDE: Final = 4
_STROKE_CONTRAST: Final = 32
"""Điểm ảnh lệch trung vị hơn ngần này mức xám mới tính là nét (bỏ nhiễu nền, vân giấy)."""


def _draft_down(image: Image.Image, max_pixels: int) -> None:
    """Xin bộ giải JPEG trả ảnh nhỏ hơn khi `w x h` nằm trong `(trần, 4 x trần]`.

    Pillow chọn hệ số theo **phép chia nguyên**, nên truyền `(w // s, h // s)` với
    `s` nhỏ nhất đưa `ceil(w/s) x ceil(h/s)` về dưới trần; kích thước thật đọc lại
    từ `image.size` vì bộ giải làm tròn lên. Không cần kiểm `s = 4`: trong dải này
    `w x h <= 4 x trần` nên `ceil(w/4) x ceil(h/4)` luôn lọt (chỉ hụt với ảnh cạnh
    dưới 3 px, tức trần dưới 9 điểm ảnh — không phải khổ ảnh thật nào).
    """
    width, height = image.size
    if not max_pixels < width * height <= _MAX_DRAFT_SCALE * max_pixels:
        return
    scale = 2 if math.ceil(width / 2) * math.ceil(height / 2) <= max_pixels else _MAX_DRAFT_SCALE
    image.draft("RGB", (width // scale, height // scale))


def _open_within_limit(
    buffer: io.BytesIO, pil_format: str, max_pixels: int, *, fit_pixels: int | None = None
) -> Image.Image:
    """Mở tệp, hạ cỡ JPEG nếu được, rồi chặn ảnh vượt trần **trước** khi giải mã (U03).

    `fit_pixels` (đường `load_raster_fit`): JPEG xin bộ giải DCT hạ cỡ nhiều nhất có thể mà vẫn không nhỏ hơn
    khổ vừa `fit_pixels` (Pillow `draft` chọn hệ số 1/2/4/8 giữ ảnh >= khổ xin); `max_pixels` lúc đó là trần giải
    mã, kiểm sau `draft`.

    `DecompressionBombError` của Pillow (ảnh khai lớn hơn hai lần trần mặc định của
    nó) cũng là "ảnh quá lớn". `Image.open` đã đọc phần đầu tệp nên chính nó ném
    `UnidentifiedImageError` (chữ ký sai) hay `OSError("Truncated File Read")`
    (JPEG cụt giữa bảng marker) — cả hai là tệp hỏng (U07).
    """
    try:
        image = Image.open(buffer, formats=[pil_format])
    except Image.DecompressionBombError as exc:
        raise VisionError("IMAGE_TOO_LARGE") from exc
    except OSError as exc:  # `UnidentifiedImageError` là lớp con của `OSError`
        raise VisionError("FILE_CORRUPT") from exc
    if pil_format == "JPEG" and fit_pixels is None:
        _draft_down(image, max_pixels)
    elif pil_format == "JPEG" and fit_pixels is not None and image.width * image.height > fit_pixels:
        image.draft("RGB", fit_size(image.width, image.height, fit_pixels))
    width, height = image.size
    if width * height > max_pixels:
        raise VisionError("IMAGE_TOO_LARGE")
    return image


def _over_white(image: Image.Image) -> Image.Image:
    """Ghép ảnh có alpha lên nền **trắng** — nền bản vẽ là giấy, ghép lên đen làm đảo nét."""
    rgba = image.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, _WHITE)
    canvas.paste(rgba, mask=rgba.getchannel("A"))
    return canvas


def _has_alpha(image: Image.Image) -> bool:
    """Ảnh mang kênh trong suốt, kể cả bảng màu `P` khai `transparency`."""
    return image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)


def _to_rgb_array(image: Image.Image) -> NDArray[np.uint8]:
    """Chuẩn hoá mọi mode Pillow về mảng RGB 8-bit (U02), bỏ mọi metadata.

    Xám 16-bit chia theo dải 16-bit (`>> 8`) chứ không cắt ở 255: cắt biến mọi
    điểm sáng hơn 255 thành trắng, mất hết nét của ảnh quét 16-bit.
    """
    if image.mode in _GRAY16_MODES:
        gray = (np.asarray(image, dtype=np.uint32) >> 8).astype(np.uint8)
        return np.repeat(gray[:, :, np.newaxis], 3, axis=2)
    if _has_alpha(image):
        image = _over_white(image)
    return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _light_paper(pixels: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Đảo màu bản vẽ nền tối (bench nt-05) để mọi bước sau luôn thấy nét tối trên giấy sáng.

    Độ sáng = kênh lớn nhất nên mảng màu bão hoà (đỏ, lam) vẫn là sáng; chỉ nền đen/xám tối mới bị đảo.
    Nền tối thôi chưa đủ: ảnh chụp giấy trắng thiếu sáng cũng có trung vị < 128 nhưng nét vẫn **tối hơn** nền.
    Chỉ đảo khi điểm ảnh sáng hơn nền rõ (> `_STROKE_CONTRAST`) nhiều hơn điểm ảnh tối hơn nền rõ: nét sáng trên
    nền tối. Trung vị lấy trên lưới thưa mỗi 4 điểm ảnh (đủ đại diện cho nền, không sắp xếp cả ảnh); hai tỉ lệ
    đếm trên cả ảnh vì nét mảnh có thể lọt giữa các ô lưới.
    """
    brightness = pixels.max(axis=2)
    median = float(np.median(brightness[::_PAPER_SAMPLE_STRIDE, ::_PAPER_SAMPLE_STRIDE]))
    if median >= _DARK_PAPER_MEDIAN:
        return pixels
    lighter = np.count_nonzero(brightness > median + _STROKE_CONTRAST)
    darker = np.count_nonzero(brightness < median - _STROKE_CONTRAST)
    if lighter > darker:
        return 255 - pixels
    return pixels


def load_raster(data: bytes, *, max_pixels: int = DEFAULT_MAX_PIXELS) -> RgbImage:
    """PNG/JPEG → `RgbImage` đã áp EXIF orientation và chuẩn hoá về RGB 8-bit.

    Định dạng lấy từ `sniff_kind` và truyền thẳng cho Pillow (`formats=[...]`) để
    một tệp không bao giờ được thử bằng bộ giải của định dạng khác (K14). Loại
    khác PNG/JPEG → `FILE_TYPE_MISMATCH`; vượt trần → `IMAGE_TOO_LARGE`; tệp cụt
    hay hỏng chunk → `FILE_CORRUPT` (U07). EXIF không đọc được thì Pillow trả thẻ
    rỗng, coi như orientation 1 — không phải lỗi dữ liệu (U01). Nền tối được đảo về giấy sáng.
    """
    pil_format = _PIL_FORMATS.get(sniff_kind(data))
    if pil_format is None:
        raise VisionError("FILE_TYPE_MISMATCH")
    with io.BytesIO(data) as buffer:
        image = _open_within_limit(buffer, pil_format, max_pixels)
        try:
            image.load()
        except OSError as exc:
            # PNG cụt → "image file is truncated"; PNG sai CRC → "broken data stream";
            # JPEG cụt → "Truncated File Read". Pillow ném cả ba dưới dạng `OSError`.
            raise VisionError("FILE_CORRUPT") from exc
        pixels = _to_rgb_array(ImageOps.exif_transpose(image) or image)
        return RgbImage(np.ascontiguousarray(_light_paper(pixels)))


def load_raster_fit(data: bytes, *, max_pixels: int, decode_max_pixels: int) -> RgbImage:
    """Như `load_raster` nhưng ảnh lớn hơn `max_pixels` được **co** về dưới trần thay vì `IMAGE_TOO_LARGE` (NO-567).

    Bảo vệ bom nén giữ nguyên: `w x h` khai ở đầu tệp (sau `draft` của JPEG) vượt `decode_max_pixels` →
    `IMAGE_TOO_LARGE` **trước** khi giải mã; `DecompressionBombError` của Pillow, tệp cụt/hỏng như `load_raster`.
    JPEG hạ cỡ ngay trong bộ giải (`draft`); PNG không có đường đó nên giải mã đủ (tốn tới `decode_max_pixels` x
    số kênh byte) rồi co bằng `Image.resize` lọc hộp (trung bình vùng như `cv2.INTER_AREA`) với `reducing_gap`
    (`reduce()` nguyên trước, lọc sau) — co trước khi đổi sang mảng RGB nên các bản chép sau đều ở cỡ đích. Mode
    `resize` không lọc được (bảng màu, alpha, xám 16-bit, 1-bit) đổi sang RGB trước. Kết quả `w x h <= max_pixels`.
    """
    pil_format = _PIL_FORMATS.get(sniff_kind(data))
    if pil_format is None:
        raise VisionError("FILE_TYPE_MISMATCH")
    with io.BytesIO(data) as buffer:
        image = _open_within_limit(buffer, pil_format, decode_max_pixels, fit_pixels=max_pixels)
        try:
            image.load()
        except OSError as exc:
            raise VisionError("FILE_CORRUPT") from exc
        image = ImageOps.exif_transpose(image) or image
        size = fit_size(image.width, image.height, max_pixels)
        if size != image.size:
            if image.mode not in _RESIZE_MODES:
                image = Image.fromarray(_to_rgb_array(image), mode="RGB")
            image = image.resize(size, Image.Resampling.BOX, reducing_gap=_REDUCING_GAP)
        pixels = _to_rgb_array(image)
        return RgbImage(np.ascontiguousarray(_light_paper(pixels)))


def encode_png(img: RgbImage, *, compress_level: int = 1) -> bytes:
    """`RgbImage` → byte PNG **không** chunk metadata (`eXIf`, `tEXt`).

    `compress_level=1` là mặc định vì ảnh trang đi vào kho object rồi bị đọc lại
    ngay; đổi lên 6+ tốn CPU gấp nhiều lần mà chỉ bớt vài phần trăm dung lượng.
    """
    buffer = io.BytesIO()
    Image.fromarray(np.array(img.pixels), mode="RGB").save(buffer, format="PNG", compress_level=compress_level)
    return buffer.getvalue()
