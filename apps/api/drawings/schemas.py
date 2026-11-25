"""Mọi model trên dây của module bản vẽ (B2-04 [2]): thân #5-#7, `Progress`, mục N7.

**Ranh giới kiểm:** ở đây chỉ những luật không cần tra DB hay đọc cấu hình, tức những luật
mà câu trả lời luôn là 422 `VALIDATION` kèm đúng `field`:

| luật | chỗ kiểm |
|---|---|
| `fileName` NFC, 1-255, cấm Cc/bidi/`/`/`\\` ([6] #5 bước 2) | `clean_file_name` ở đây |
| `sizeBytes >= 1`, `pageIndex` 0-19 ([6] #5 bước 4) | ở đây |
| `chunkIndex >= 0`, `chunk` không rỗng ([6] #6 bước 2) | ở đây |
| đuôi tệp ↔ `mimeType`, `.dwg` (`CAD_NOT_SUPPORTED`, `FILE_TYPE_MISMATCH`) | route (#5 bước 2-3) |
| `sizeBytes` quá trần → **413**, `pageIndex != 0` với ảnh | route (#5 bước 4) |
| `chunkIndex < chunk_count`, base64 hỏng, khúc quá `UPLOAD_CHUNK_BYTES` | route (#6 bước 2) |

Ba thân đều **khai** khoá id của đường (`projectId`, `floorId`, `uploadId`) để guard W21 so
thân với đường; giá trị chỉ dùng để so, không bao giờ dùng để tra (K09).
"""

from typing import Annotated, Any, Final, Literal

from pydantic import BeforeValidator, Field, model_validator

from apps.api.core.pagination import CursorPage
from apps.api.core.wire import WireDatetime, WireModel, WireRequest
from packages.core.text import clean_text

FILE_NAME_MAX: Final = 255
PAGE_INDEX_MAX: Final = 19
MIME_TYPE_MAX: Final = 255
CROP_MIN_AREA: Final = 0.01
"""Diện tích tối thiểu (tỉ lệ trang) của vùng cắt #5 — nhỏ hơn thì không còn là một mặt bằng."""
CROP_EPS: Final = 1e-6
"""Dung sai làm tròn của `x + width`, `y + height` so với 1."""

_FORBIDDEN_CHARS: Final = frozenset("/\\")


def clean_file_name(value: Any) -> Any:
    """Trim + NFC rồi 1-255 ký tự, cấm Cc, ký tự đảo chiều và dấu tách đường dẫn.

    Tên tệp đi thẳng vào `Content-Disposition` của URL ký và vào tên hiển thị của bản vẽ,
    nên `/`, `\\` bị cấm ở **biên HTTP** chứ không chỉ lúc dựng khoá object (K13).
    Không phải chuỗi thì trả nguyên cho Pydantic báo lỗi kiểu.
    """
    if not isinstance(value, str):
        return value
    try:
        text = clean_text(value)
    except ValueError as exc:
        raise ValueError(f"fileName: {exc}") from exc
    if not 1 <= len(text) <= FILE_NAME_MAX:
        raise ValueError(f"fileName phải 1-{FILE_NAME_MAX} ký tự sau chuẩn hoá")
    bad = next((ch for ch in text if ch in _FORBIDDEN_CHARS), None)
    if bad is not None:
        raise ValueError(f"fileName chứa ký tự cấm U+{ord(bad):04X}")
    return text


FileName = Annotated[str, BeforeValidator(clean_file_name)]


class CropRect(WireRequest):
    """Vùng cắt của một tầng trên tờ nhiều mặt bằng, theo tỉ lệ trang gốc **chưa nắn**.

    Trang gốc là ảnh như trình duyệt hiện (đã xoay theo EXIF) hoặc trang `pageIndex` của PDF;
    `x`, `y` là góc trên-trái. Sai luật → 422 `VALIDATION`, `field` là `crop` hoặc `crop.<khoá>`.
    """

    x_ratio: float = Field(ge=0.0, le=1.0)
    y_ratio: float = Field(ge=0.0, le=1.0)
    width_ratio: float = Field(ge=0.0, le=1.0)
    height_ratio: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _inside_page(self) -> "CropRect":
        """Vùng phải nằm trong trang (dung sai `CROP_EPS`) và đủ `CROP_MIN_AREA`."""
        if self.x_ratio + self.width_ratio > 1.0 + CROP_EPS or self.y_ratio + self.height_ratio > 1.0 + CROP_EPS:
            raise ValueError("vùng cắt vượt ra ngoài trang")
        if self.width_ratio * self.height_ratio < CROP_MIN_AREA:
            raise ValueError("vùng cắt quá nhỏ")
        return self

    def corners(self) -> list[list[float]]:
        """4 góc TL,TR,BR,BL (cùng dạng `quality_assessments.corners`), kẹp về `[0, 1]`."""
        right = min(self.x_ratio + self.width_ratio, 1.0)
        bottom = min(self.y_ratio + self.height_ratio, 1.0)
        return [[self.x_ratio, self.y_ratio], [right, self.y_ratio], [right, bottom], [self.x_ratio, bottom]]


class InitUploadBody(WireRequest):
    """Thân #5; `pageIndex` vắng = 0 (F-03), `mimeType` rỗng được nhận (magic bytes quyết ở #7).

    `crop` vắng/null = cả trang; có thì pipeline của tầng chỉ xử lý vùng đó (MULTI-FLOOR-01).
    """

    file_name: FileName
    floor_id: str = Field(min_length=1)
    mime_type: str = Field(max_length=MIME_TYPE_MAX)
    project_id: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    # Vắng `pageIndex` với PDF nhiều trang không `crop`: #7 tự chọn trang bản vẽ (BENCH3D-07, cột uploads.page_auto).
    page_index: int = Field(default=0, ge=0, le=PAGE_INDEX_MAX)
    crop: CropRect | None = None


class UploadChunkBody(WireRequest):
    """Thân #6: một khúc base64 và chỉ số của nó; trần độ dài thật theo `UPLOAD_CHUNK_BYTES` ở route."""

    chunk: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)


class CompleteUploadBody(WireRequest):
    """Thân #7; `uploadId` chỉ để guard W21 so với đường."""

    upload_id: str = Field(min_length=1)


class ProgressOut(WireModel):
    """`ProgressSchema` của FE (`src/api/schemas/index.ts:165-184`); luật giá trị ở `progress.py`.

    `endedAt` chỉ đi kèm `completed`, `error` là mã UPPER_SNAKE — hai bất biến ấy là việc của
    `progress_of`, ở đây chỉ khai trường tuỳ chọn để W2 bỏ chúng khi vắng.
    """

    id: str
    status: Literal["pending", "running", "completed", "failed"]
    step: str
    progress_percent: int = Field(ge=0, le=100)
    started_at: WireDatetime | None = None
    ended_at: WireDatetime | None = None
    error: str | None = None
    queue_ahead: int | None = Field(default=None, ge=0)


class LatestFloorUploadOut(WireModel):
    """Mục N7 (`src/api/schemas/uploads.ts`): lượt tải mới nhất của một tầng.

    `sourceImageUrl` là ảnh trang **đã nắn** (HOP-DONG-MOI §4.2) và chỉ có khi bản vẽ đang
    dùng của tầng thuộc đúng lượt tải này.
    """

    floor_id: str
    floor_name: str
    source_image_url: str | None = None
    upload_id: str


LatestFloorUploadPage = CursorPage[LatestFloorUploadOut]
"""Response N7: `{items, nextCursor?}` (W22), mục sắp theo `Floor.order`."""


class PipelineRunOut(WireModel):
    """Mục N7b: một lượt chạy pipeline của một tầng (màn Nhật ký lượt xử lý, UIFIX-B7).

    `step` + `progressPercent` cùng nghĩa với `Progress`: FE tự dựng các giai
    đoạn từ chúng. `startedAt`/`endedAt`/`errorCode`/`supersededBy` vắng khoá khi chưa có (W2).
    Lượt `failed` **không** có `endedAt` (K33): giờ dừng của nó là `updatedAt`.
    """

    run_id: str
    upload_id: str
    floor_id: str
    floor_name: str
    status: Literal["pending", "running", "completed", "failed"]
    step: str
    progress_percent: int
    error_code: str | None = None
    created_at: WireDatetime
    updated_at: WireDatetime
    started_at: WireDatetime | None = None
    ended_at: WireDatetime | None = None
    superseded_by: str | None = None


PipelineRunPage = CursorPage[PipelineRunOut]
"""Response N7b: `{items, nextCursor?}` (W22), lượt mới nhất trước."""
