"""Nơi **duy nhất** đổi cột `floor_documents.document` (jsonb) ↔ mô hình `packages.domain.spatial`.

Một luật đọc, một luật ghi, một lỗi: mọi module khác nhận `FloorDocument` đã giải mã
(`documents.py`) và không bao giờ tự chạm vào dict dây. Nhờ vậy nâng lược đồ chỉ phải
sửa ở đây (FIX.md luật 7).

Ghi bằng `model_dump(mode="json", by_alias=True, exclude_none=True)`: khoá camelCase, mm
là số nguyên JSON, trường tuỳ chọn **vắng khoá** chứ không `null` (W2, K02). Đọc bằng
`model_validate` của B3-01 với `extra="forbid"`: khoá lạ, mm thập phân, id sai mẫu W4 đều
là tài liệu hỏng. Đọc **không bao giờ tự sửa** — một tài liệu hỏng là lỗi phải thấy, không
phải dữ liệu cần đoán lại.

`axes` và `origin` (gốc toạ độ, mm, tuỳ chọn) là dữ liệu màn Trục gửi qua #35 (G4b-17);
tài liệu cũ không có khoá `origin` vẫn đọc được, nên không cần migration. `scaleAccepted` (ảnh chụp căn cứ
tỉ lệ của máy lúc người dùng chấp nhận số máy, ML-ACC-R10 K4) theo cùng cách: vắng khoá = không chấp nhận.
"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.domain.spatial import (
    Axis,
    Dimension,
    Point,
    ScaleAcceptance,
    SpatialLayer,
    document_to_json,
    entity_ids,
)

__all__ = [
    "DocumentCorruptError",
    "DocumentEnvelope",
    "decode_document",
    "document_from_json",
    "document_to_json",
    "entity_ids",
]
"""`document_to_json` và `entity_ids` sống ở `packages.domain.spatial.document` (NO-220); đây chỉ chuyển tiếp."""


class DocumentCorruptError(Exception):
    """Cột `document` (hay `schema_version`) của một tầng không giải mã được.

    Không phải `AppError`: người dùng không sửa được gì, nên handler cuối trả 500 và
    log `floor_pk` để người vận hành chạy `check-documents` tìm hết các dòng cùng cảnh.
    """


class DocumentEnvelope(BaseModel):
    """Khung của cột `document`; `extra="forbid"` nên khoá lạ ở **mức ngoài** cũng là hỏng."""

    model_config = ConfigDict(extra="forbid")

    layer: SpatialLayer
    axes: tuple[Axis, ...]
    dimensions: tuple[Dimension, ...]
    origin: Point | None = None
    scale_accepted: ScaleAcceptance | None = Field(default=None, alias="scaleAccepted")


def document_from_json(raw: Mapping[str, object]) -> tuple[SpatialLayer, tuple[Axis, ...], tuple[Dimension, ...]]:
    """Giải một cột `document`; bất kỳ lệch nào so với mô hình B3-01 → `DocumentCorruptError`."""
    envelope = decode_document(raw)
    return envelope.layer, envelope.axes, envelope.dimensions


def decode_document(raw: Mapping[str, object]) -> DocumentEnvelope:
    """Giải cả khung, kể cả `origin` (G4b-17); lệch mô hình → `DocumentCorruptError`."""
    try:
        return DocumentEnvelope.model_validate(raw)
    except ValidationError as error:
        raise DocumentCorruptError(str(error)) from error
