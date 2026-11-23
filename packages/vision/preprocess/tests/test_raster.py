"""Nạp PNG/JPEG: EXIF (U01), chuẩn hoá mode (U02), trần điểm ảnh (U03), tệp hỏng (U07)."""

import io

import numpy as np
import pytest
from PIL import Image, ImageFile

from packages.vision.preprocess.errors import VisionError
from packages.vision.preprocess.raster import encode_png, load_raster, load_raster_fit
from packages.vision.preprocess.tests import synthetic


def test_load_raster_u01_exif() -> None:
    """Orientation 6 quay ảnh 40x20 thành 20x40 và đưa dải đỏ sang đúng cạnh."""
    image = load_raster(synthetic.jpeg_with_orientation(40, 20, 6))
    assert (image.width_px, image.height_px) == (20, 40)
    # Orientation 6 = xoay 90° theo chiều kim đồng hồ: nửa trên (đỏ) về cạnh phải.
    assert image.pixels[5, 15] == pytest.approx(np.array([255, 0, 0]), abs=8)
    assert image.pixels[5, 4] == pytest.approx(np.array([0, 0, 255]), abs=8)


def test_load_raster_u01_unreadable_exif_is_orientation_one() -> None:
    """EXIF hỏng không phải lỗi dữ liệu: ảnh vẫn nạp, chỉ là không xoay."""
    data = bytearray(synthetic.jpeg_with_orientation(40, 20, 6))
    tiff_start = data.index(b"Exif\x00\x00") + 6
    data[tiff_start : tiff_start + 2] = b"\xff\xff"  # thứ tự byte TIFF vô nghĩa
    image = load_raster(bytes(data))
    assert (image.width_px, image.height_px) == (40, 20)


def _cmyk_jpeg() -> bytes:
    """JPEG chế độ CMYK — ảnh quét từ máy in hay ở chế độ này, phải đổi được về RGB."""
    return synthetic.encode(Image.new("CMYK", (6, 4), (0, 0, 0, 0)), "JPEG")


def _gray16_png() -> bytes:
    """PNG xám 16-bit lấy ba mức đầu/giữa/cuối dải, để chốt phép hạ xuống 8-bit."""
    source = Image.new("I;16", (4, 1))
    source.putdata([65535, 32768, 0, 65535])
    return synthetic.encode(source, "PNG")


def _transparent_rgba_png() -> bytes:
    """PNG RGBA trong suốt hoàn toàn: nền phải hoá trắng chứ không hoá đen."""
    return synthetic.encode(Image.new("RGBA", (4, 2), (0, 0, 0, 0)), "PNG")


def _transparent_palette_png() -> bytes:
    """PNG bảng màu `P` có chỉ số trong suốt — đường đổi chế độ khác với `RGBA`."""
    palette = Image.new("P", (4, 2), 0)
    palette.putpalette([0, 0, 0] * 256)
    palette.info["transparency"] = 0
    return synthetic.encode(palette, "PNG")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_cmyk_jpeg(), (255, 255, 255)),
        (_transparent_rgba_png(), (255, 255, 255)),
        (_transparent_palette_png(), (255, 255, 255)),
        (synthetic.encode(Image.new("LA", (4, 2), (0, 0)), "PNG"), (255, 255, 255)),
        (synthetic.encode(Image.new("1", (4, 2), 1), "PNG"), (255, 255, 255)),
        (synthetic.encode(Image.new("L", (4, 2), 128), "PNG"), (128, 128, 128)),
    ],
)
def test_load_raster_u02_modes(data: bytes, expected: tuple[int, int, int]) -> None:
    """Mọi mode về RGB 8-bit; phần trong suốt ghép lên **trắng** vì nền bản vẽ là giấy."""
    image = load_raster(data)
    assert image.pixels.dtype == np.uint8
    assert image.pixels.shape[2] == 3
    assert image.pixels[0, 0] == pytest.approx(np.array(expected), abs=2)


def test_load_raster_u02_gray16_uses_full_range() -> None:
    """Xám 16-bit chia theo dải 16-bit: 65.535 → 255, 32.768 → 128 (không cắt ở 255)."""
    pixels = load_raster(_gray16_png()).pixels
    assert pixels[0, 0, 0] == 255
    assert pixels[0, 1, 0] == pytest.approx(128, abs=1)
    assert pixels[0, 2, 0] == 0


def test_load_raster_u03_bomb_checks_size_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    """PNG khai 8.000 x 8.000 bị chặn bằng `w x h` ở đầu tệp — `ImageFile.load` không chạy lần nào."""
    calls = 0
    original = ImageFile.ImageFile.load

    def counting_load(self: ImageFile.ImageFile) -> object:
        """Bản `ImageFile.load` có đếm, để chứng minh U03 chặn **trước** khi giải mã."""
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(ImageFile.ImageFile, "load", counting_load)
    with pytest.raises(VisionError) as excinfo:
        load_raster(synthetic.png_declaring(8000, 8000), max_pixels=40_000_000)
    assert str(excinfo.value) == "IMAGE_TOO_LARGE"
    assert calls == 0


def test_load_raster_u03_bomb_decompression_bomb_error() -> None:
    """Ảnh khai lớn hơn cả trần riêng của Pillow: `DecompressionBombError` cũng là "quá lớn"."""
    with pytest.raises(VisionError) as excinfo:
        load_raster(synthetic.png_declaring(20_000, 20_000), max_pixels=40_000_000)
    assert str(excinfo.value) == "IMAGE_TOO_LARGE"


def test_load_raster_u03_jpeg_draft_down_to_limit() -> None:
    """JPEG 2 x trần, cả hai cạnh lẻ: bộ giải trả ảnh 1/2 nên nạp được và vẫn dưới trần."""
    max_pixels = 20_000
    data = synthetic.encode(Image.new("RGB", (301, 133), (10, 20, 30)), "JPEG")
    image = load_raster(data, max_pixels=max_pixels)
    assert image.width_px * image.height_px <= max_pixels
    assert (image.width_px, image.height_px) == (151, 67)


def test_load_raster_u03_jpeg_draft_down_needs_quarter() -> None:
    """Sát 4 x trần với cả hai cạnh lẻ: 1/2 vẫn vượt vì `ceil`, phải xuống 1/4."""
    max_pixels = 20_000
    data = synthetic.encode(Image.new("RGB", (283, 282), (10, 20, 30)), "JPEG")
    image = load_raster(data, max_pixels=max_pixels)
    assert (image.width_px, image.height_px) == (71, 71)
    assert image.width_px * image.height_px <= max_pixels


def test_load_raster_u03_jpeg_beyond_draft_range() -> None:
    """JPEG lớn hơn 4 x trần: 1/4 vẫn vượt, không có hệ số nào cứu → `IMAGE_TOO_LARGE`."""
    data = synthetic.encode(Image.new("RGB", (400, 300), (10, 20, 30)), "JPEG")
    with pytest.raises(VisionError) as excinfo:
        load_raster(data, max_pixels=20_000)
    assert str(excinfo.value) == "IMAGE_TOO_LARGE"


@pytest.mark.parametrize(("max_pixels", "raises"), [(10_000, False), (9_999, True)])
def test_load_raster_u03_png_exactly_at_limit(max_pixels: int, raises: bool) -> None:
    """Bằng trần thì đạt, vượt đúng một điểm ảnh thì hỏng — biên đóng ở phía trần."""
    data = synthetic.encode(Image.new("RGB", (100, 100), (7, 7, 7)), "PNG")
    if raises:
        with pytest.raises(VisionError) as excinfo:
            load_raster(data, max_pixels=max_pixels)
        assert str(excinfo.value) == "IMAGE_TOO_LARGE"
    else:
        assert load_raster(data, max_pixels=max_pixels).width_px == 100


def _half(data: bytes) -> bytes:
    """Nửa đầu của tệp — cách dựng tệp cụt mà không phải commit tệp nhị phân."""
    return data[: len(data) // 2]


@pytest.mark.parametrize(
    "data",
    [
        _half(synthetic.encode(Image.new("RGB", (60, 60), (3, 4, 5)), "PNG")),
        _half(synthetic.jpeg_with_orientation(60, 60, 1)),
        synthetic.corrupt_png_crc(synthetic.encode(Image.new("RGB", (60, 60), (3, 4, 5)), "PNG")),
    ],
)
def test_load_raster_u07_truncated(data: bytes) -> None:
    """PNG cụt (`OSError: image file is truncated`), JPEG cụt (`Truncated File Read`) và
    PNG sai CRC (`broken data stream`) — cả ba là `OSError` của Pillow → `FILE_CORRUPT`."""
    with pytest.raises(VisionError) as excinfo:
        load_raster(data)
    assert str(excinfo.value) == "FILE_CORRUPT"


def test_load_raster_unidentified_is_file_corrupt() -> None:
    """Chữ ký PNG đúng nhưng phần sau không phải PNG → Pillow không nhận ra → `FILE_CORRUPT`."""
    with pytest.raises(VisionError) as excinfo:
        load_raster(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert str(excinfo.value) == "FILE_CORRUPT"


def test_load_raster_file_type_mismatch() -> None:
    """PDF đưa vào `load_raster` là lỗi loại tệp, không phải tệp hỏng."""
    with pytest.raises(VisionError) as excinfo:
        load_raster(synthetic.make_pdf(1))
    assert str(excinfo.value) == "FILE_TYPE_MISMATCH"


def test_load_raster_does_not_touch_pillow_globals() -> None:
    """Biến toàn cục của Pillow giữ nguyên: gán chúng là không an toàn luồng (BE-00 §11)."""
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False
    assert Image.MAX_IMAGE_PIXELS == 89_478_485


def test_encode_png_round_trip_without_metadata() -> None:
    """PNG mã hoá ra giải lại đúng từng điểm ảnh và không mang chunk metadata nào."""
    source = load_raster(synthetic.encode(Image.new("RGB", (8, 5), (12, 34, 56)), "PNG"))
    data = encode_png(source)
    assert np.array_equal(load_raster(data).pixels, source.pixels)
    assert b"eXIf" not in data
    assert b"tEXt" not in data
    with Image.open(io.BytesIO(data), formats=["PNG"]) as decoded:
        assert decoded.info.get("exif") is None


def test_load_raster_dark_paper_is_inverted() -> None:
    """Bản vẽ nền đen nét trắng (bench nt-05): trung vị xám < 128 → đảo về giấy trắng nét đen."""
    source = Image.new("RGB", (40, 20), (10, 10, 10))
    source.paste((250, 250, 250), (0, 10, 40, 12))
    pixels = load_raster(synthetic.encode(source, "PNG")).pixels
    assert tuple(pixels[0, 0]) == (245, 245, 245)
    assert tuple(pixels[11, 5]) == (5, 5, 5)


def test_load_raster_light_paper_is_kept() -> None:
    """Giấy sáng giữ nguyên, kể cả nét đen dày."""
    source = Image.new("RGB", (40, 20), (240, 240, 240))
    source.paste((0, 0, 0), (0, 0, 40, 8))
    pixels = load_raster(synthetic.encode(source, "PNG")).pixels
    assert tuple(pixels[0, 0]) == (0, 0, 0)
    assert tuple(pixels[19, 0]) == (240, 240, 240)


def test_load_raster_dim_photo_of_white_paper_is_kept() -> None:
    """Ảnh chụp giấy trắng thiếu sáng: nền xám 110 (< 128) nhưng nét đen 20 tối hơn nền → không đảo."""
    source = Image.new("RGB", (40, 20), (110, 110, 110))
    source.paste((20, 20, 20), (0, 10, 40, 12))
    pixels = load_raster(synthetic.encode(source, "PNG")).pixels
    assert tuple(pixels[0, 0]) == (110, 110, 110)
    assert tuple(pixels[11, 5]) == (20, 20, 20)


# ---------------------------------------------------------------- load_raster_fit (NO-567)
@pytest.mark.parametrize(
    "source",
    [
        Image.new("RGB", (301, 203), (240, 240, 240)),
        Image.new("L", (301, 203), 240),
        Image.new("P", (301, 203), 0),
        Image.new("RGBA", (301, 203), (0, 0, 0, 0)),
        Image.new("I;16", (301, 203), 0),
    ],
    ids=["rgb", "l", "p", "rgba", "gray16"],
)
def test_load_raster_fit_png_over_limit_is_downsampled(source: Image.Image) -> None:
    """PNG vượt trần đích (61.103 > 20.000) nhưng dưới trần giải mã: co về khổ lớn nhất <= trần, giữ tỉ lệ, mọi
    mode về RGB 8-bit — `load_raster` cùng trần thì `IMAGE_TOO_LARGE`."""
    data = synthetic.encode(source, "PNG")
    with pytest.raises(VisionError, match="IMAGE_TOO_LARGE"):
        load_raster(data, max_pixels=20_000)
    image = load_raster_fit(data, max_pixels=20_000, decode_max_pixels=100_000)
    assert image.width_px * image.height_px <= 20_000
    assert (image.width_px, image.height_px) == (172, 116)
    assert image.pixels.dtype == np.uint8
    assert image.pixels.shape[2] == 3


def test_load_raster_fit_keeps_strokes_and_paper() -> None:
    """Co lọc hộp giữ nét đậm (dải đen 10 px còn tối) và vẫn đảo nền tối như `load_raster`."""
    light = Image.new("RGB", (400, 200), (250, 250, 250))
    light.paste((0, 0, 0), (0, 100, 400, 110))
    pixels = load_raster_fit(synthetic.encode(light, "PNG"), max_pixels=20_000, decode_max_pixels=100_000).pixels
    assert pixels.shape[:2] == (100, 200)
    assert tuple(pixels[0, 0]) == (250, 250, 250)
    assert int(pixels[52, 100, 0]) < 10
    dark = Image.new("RGB", (400, 200), (10, 10, 10))
    dark.paste((250, 250, 250), (0, 100, 400, 110))
    inverted = load_raster_fit(synthetic.encode(dark, "PNG"), max_pixels=20_000, decode_max_pixels=100_000).pixels
    assert tuple(inverted[0, 0]) == (245, 245, 245)


def test_load_raster_fit_jpeg_uses_draft_then_fits() -> None:
    """JPEG 8 x trần đích (cạnh lẻ): `draft` của bộ giải hạ cỡ trước, rồi co đúng về khổ vừa trần."""
    data = synthetic.encode(Image.new("RGB", (801, 401), (200, 200, 200)), "JPEG")
    image = load_raster_fit(data, max_pixels=40_000, decode_max_pixels=400_000)
    assert (image.width_px, image.height_px) == (282, 141)


def test_load_raster_fit_under_limit_is_unchanged() -> None:
    """Ảnh đã dưới trần đích: không co, pixel y như `load_raster`."""
    data = synthetic.encode(Image.new("RGB", (100, 50), (7, 70, 170)), "PNG")
    fitted = load_raster_fit(data, max_pixels=20_000, decode_max_pixels=100_000)
    np.testing.assert_array_equal(fitted.pixels, load_raster(data, max_pixels=20_000).pixels)


def test_load_raster_fit_bomb_checks_decode_limit_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    """PNG khai vượt trần giải mã bị chặn từ `w x h` ở đầu tệp — `ImageFile.load` không chạy lần nào; ảnh khai
    quá trần riêng của Pillow (`DecompressionBombError`) cũng là `IMAGE_TOO_LARGE`."""
    calls = 0
    original = ImageFile.ImageFile.load

    def counting_load(self: ImageFile.ImageFile) -> object:
        """Bản `ImageFile.load` có đếm."""
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(ImageFile.ImageFile, "load", counting_load)
    for width, height in ((9_000, 9_000), (20_000, 20_000)):
        with pytest.raises(VisionError, match="IMAGE_TOO_LARGE"):
            load_raster_fit(synthetic.png_declaring(width, height), max_pixels=1_000, decode_max_pixels=80_000_000)
    assert calls == 0


@pytest.mark.parametrize("kind", ["png", "jpeg"])
def test_load_raster_fit_corrupt_file(kind: str) -> None:
    """Tệp cụt vẫn là `FILE_CORRUPT`, không bị nuốt thành ảnh co; byte lạ là `FILE_TYPE_MISMATCH`."""
    source = Image.effect_noise((300, 300), 64).convert("RGB")
    data = synthetic.encode(source, kind.upper())
    with pytest.raises(VisionError, match="FILE_CORRUPT"):
        load_raster_fit(_half(data), max_pixels=20_000, decode_max_pixels=100_000)
    with pytest.raises(VisionError, match="FILE_TYPE_MISMATCH"):
        load_raster_fit(b"not an image at all", max_pixels=20_000, decode_max_pixels=100_000)
