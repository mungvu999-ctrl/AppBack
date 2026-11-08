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
tài liệu cũ không có khoá `origin` vẫn đọc được, nên không cần migration.

Lớp đề xuất M2 (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.1) thêm ba khoá tuỳ chọn cùng luật "vắng khi rỗng":
`suggestions` + `suggestionsMeta` (luôn đi cặp) và `suggestionsEnabled` (chỉ ghi `true`; vắng = theo mặc định
của tầng, xem `documents.document_from_row`).
"""

from collections.abc import Mapping, Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from packages.domain.spatial import Axis, Dimension, Point, SpatialLayer, document_to_json, entity_ids
from packages.domain.suggestions import Suggestion, SuggestionsMeta, document_keys

__all__ = [
    "DocumentCorruptError",
    "DocumentEnvelope",
    "decode_document",
    "document_from_json",
    "document_to_json",
    "entity_ids",
    "suggestion_keys",
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
    suggestions: tuple[Suggestion, ...] = ()
    suggestions_meta: SuggestionsMeta | None = Field(default=None, alias="suggestionsMeta")
    suggestions_enabled: StrictBool | None = Field(default=None, alias="suggestionsEnabled")

    @model_validator(mode="after")
    def _suggestions_with_meta(self) -> Self:
        """`suggestions` không rỗng thì phải có `suggestionsMeta` (`document_keys` luôn ghi cả hai)."""
        if self.suggestions and self.suggestions_meta is None:
            raise ValueError("suggestions thiếu suggestionsMeta")
        return self


def suggestion_keys(items: Sequence[Suggestion], meta: SuggestionsMeta | None, *, enabled: bool) -> dict[str, object]:
    """Ba khoá lớp đề xuất cho `document_to_json`: cặp danh sách/meta khi danh sách không rỗng, cờ chỉ khi bật."""
    keys: dict[str, object] = {} if meta is None else document_keys(items, meta)
    if enabled:
        keys["suggestionsEnabled"] = True
    return keys


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
