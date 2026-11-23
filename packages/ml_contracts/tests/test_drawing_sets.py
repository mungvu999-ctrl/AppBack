"""Hợp đồng worker `ml` ↔ bộ hồ sơ (K-32a PR(3)): payload lượt đọc và kết quả đọc trang — luật biên tin cậy."""

from collections.abc import Callable
from typing import Final

import pytest
from pydantic import ValidationError

from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.ml_contracts.artifacts import ARTIFACT_JSON_MAX_BYTES, MASK_MAX_PIXELS, BoxPx, TextPx
from packages.ml_contracts.drawing_sets import (
    READ_PAGES_MAX,
    DrawingSetReadPayload,
    PageReadingDoc,
    PageRef,
    ReadingBox,
    reading_from_json,
    reading_to_json,
)
from packages.ml_contracts.families import ModelFamily
from packages.ml_contracts.payloads import ModelRef

CLOCK: Final = SystemClock()
FILE: Final = new_id("dwf", CLOCK)


def _model(family: ModelFamily = "dimensionReading") -> ModelRef:
    """Model dạng cổ điển của một họ."""
    return ModelRef(version_id=None, family=family, weights_key=None, pinned_name=None, checksum_sha256="")


def _payload(pages: tuple[PageRef, ...], model: ModelRef | None = None) -> DrawingSetReadPayload:
    """Payload lượt đọc với id hợp lệ."""
    return DrawingSetReadPayload(
        project_id=new_id("prj", CLOCK), set_id=new_id("dws", CLOCK), pages=pages, model=model or _model()
    )


def _text(x_max: float = 20.0, y_max: float = 20.0) -> TextPx:
    """Một dòng chữ góc trên trái."""
    return TextPx(text="A", box=BoxPx(x_min=1, y_min=1, x_max=x_max, y_max=y_max), confidence=0.5)


def test_payload_accepts_pages_and_text_model() -> None:
    """Hợp lệ: trang khác nhau, model họ đọc chữ; tối đa `READ_PAGES_MAX` trang."""
    pages = tuple(PageRef(file_id=new_id("dwf", CLOCK), page_index=i % 20) for i in range(READ_PAGES_MAX))
    assert len(_payload(pages).pages) == READ_PAGES_MAX


@pytest.mark.parametrize(
    "build",
    [
        lambda: _payload((PageRef(file_id=FILE, page_index=0),), _model("wallSegmentation")),
        lambda: _payload((PageRef(file_id=FILE, page_index=0), PageRef(file_id=FILE, page_index=0))),
        lambda: _payload(()),
        lambda: PageRef(file_id=FILE, page_index=20),
        lambda: PageRef(file_id="upl_" + "0" * 26, page_index=0),
    ],
    ids=["sai-ho-model", "trang-trung", "khong-trang", "trang-qua-19", "sai-tien-to"],
)
def test_payload_rejects(build: Callable[[], object]) -> None:
    """Sai họ model, trang trùng, rỗng, chỉ số trang > 19, id sai tiền tố → `ValidationError` (thông điệp độc)."""
    with pytest.raises(ValidationError):
        build()


def test_reading_round_trip() -> None:
    """Kết quả đọc ghi rồi đọc lại trùng từng trường."""
    box = ReadingBox(x0=0, y0=0, x1=100, y1=50, score=0.9)
    doc = PageReadingDoc(width_px=100, height_px=50, boxes=(box,), texts=(_text(),), read=True)
    assert reading_from_json(reading_to_json(doc)) == doc


@pytest.mark.parametrize(
    "fields",
    [
        {"width_px": MASK_MAX_PIXELS, "height_px": 2},
        {"boxes": (ReadingBox(x0=0, y0=0, x1=101, y1=50, score=0.9),)},
        {"boxes": (ReadingBox(x0=0, y0=0, x1=10, y1=51, score=0.9),)},
        {"texts": (_text(x_max=101.0),)},
        {"texts": (_text(y_max=51.0),)},
    ],
    ids=["qua-tran-diem-anh", "khung-vuot-ngang", "khung-vuot-doc", "chu-vuot-ngang", "chu-vuot-doc"],
)
def test_reading_rejects_out_of_page(fields: dict[str, object]) -> None:
    """Khổ quá trần, khung hay hộp chữ vượt khổ ảnh → `ValidationError`."""
    base: dict[str, object] = {"width_px": 100, "height_px": 50, "boxes": (), "texts": (), "read": True}
    with pytest.raises(ValidationError):
        PageReadingDoc.model_validate(base | fields)


def test_reading_box_must_be_ordered() -> None:
    """Khung rỗng hay lật ngược → `ValidationError`."""
    with pytest.raises(ValidationError):
        ReadingBox(x0=10, y0=0, x1=10, y1=5, score=0.5)


def test_reading_from_json_caps_bytes() -> None:
    """Thân quá `ARTIFACT_JSON_MAX_BYTES` bị từ chối trước khi parse."""
    with pytest.raises(ValueError, match="trần"):
        reading_from_json(b" " * (ARTIFACT_JSON_MAX_BYTES + 1))
