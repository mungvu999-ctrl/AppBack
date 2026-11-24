"""Tiền xử lý ảnh bản vẽ: nhận diện, PDF, raster, góc khung, nắn (B2-05a).

Chỉ xuất lại tên của khối [2]; nhập gói không có tác dụng phụ (không đọc tệp, không
đọc biến môi trường, không đụng biến toàn cục của Pillow).
"""

from packages.vision.preprocess.errors import VisionError
from packages.vision.preprocess.geometry import (
    deskew,
    find_frame,
    order_corners,
    quad_from_ratios,
    quad_to_ratios,
    rectify,
    validate_quad,
)
from packages.vision.preprocess.pdf import (
    effective_dpi,
    pdf_page_count,
    pdf_page_size_pt,
    render_pdf_page,
    render_pdf_thumbnails,
)
from packages.vision.preprocess.raster import encode_png, load_raster, load_raster_fit
from packages.vision.preprocess.sniff import sniff_kind
from packages.vision.preprocess.types import (
    DEFAULT_DPI,
    DEFAULT_MAX_PIXELS,
    Homography,
    Quad,
    RectifyResult,
    RgbImage,
    compose,
)

__all__ = [
    "DEFAULT_DPI",
    "DEFAULT_MAX_PIXELS",
    "Homography",
    "Quad",
    "RectifyResult",
    "RgbImage",
    "VisionError",
    "compose",
    "deskew",
    "effective_dpi",
    "encode_png",
    "find_frame",
    "load_raster",
    "load_raster_fit",
    "order_corners",
    "pdf_page_count",
    "pdf_page_size_pt",
    "quad_from_ratios",
    "quad_to_ratios",
    "rectify",
    "render_pdf_page",
    "render_pdf_thumbnails",
    "sniff_kind",
    "validate_quad",
]
