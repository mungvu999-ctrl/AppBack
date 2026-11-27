"""Model trên dây của "Tải cả bộ hồ sơ" N54-N61 (K-32a §3; camelCase, tuỳ chọn **vắng** không `null`, W2).

Thân request kế thừa `WireRequest` (khoá lạ → 422, C03); luật không cần DB (độ dài, dải, mẫu id tầng, NFC của tên)
kiểm ở đây, luật cần DB/kho (tệp thuộc bộ, trang trong tệp, tầng có thật) kiểm ở service. `kind`, `storeyLabel`,
`questions[].code` dùng chung tập giá trị của N52 (`schemas.py`). Độ tin kẹp [0, 1].
"""

from typing import Annotated, Final, Literal

from pydantic import Field

from apps.api.core.wire import WireDatetime, WireModel, WireRequest
from apps.api.drawing_sets.building_schemas import BuildingSource
from apps.api.drawing_sets.schemas import FloorKind, QuestionCode, StoreyLabel
from apps.api.drawings.schemas import CropRect, FileName
from apps.api.floors.schemas import (
    ELEVATION_MAX,
    ELEVATION_MIN,
    HEIGHT_MAX,
    HEIGHT_MIN,
    FloorIdField,
    FloorName,
)

FILES_MAX: Final = 20
"""Trần số tệp một bộ (K-32a quyết định 1)."""
VIEWS_MAX: Final = 60
"""Trần số hình một bản duyệt N59 (`field: "body.views"`)."""
MIME_TYPE_MAX: Final = 255
STOREY_NO_MIN: Final = -9
STOREY_NO_MAX: Final = 199
"""Số tầng người duyệt gõ: tầng hầm tới -9, tầng cao tới 199 (bộ giải N52 không vượt dải này)."""

type SetStatus = Literal["receiving", "analyzing", "review", "confirmed", "failed"]
type FileStatus = Literal["receiving", "complete", "rejected"]
type ViewSource = Literal["ai", "user"]
type TargetMode = Literal["floor", "newFloor", "section"]


class FileDeclIn(WireRequest):
    """Một tệp khai ở N54; đuôi + `mimeType` chỉ để từ chối sớm (`.dwg`, lệch), magic bytes quyết ở N56 (K14)."""

    file_name: FileName
    mime_type: str = Field(max_length=MIME_TYPE_MAX)
    size_bytes: int = Field(ge=1)


class CreateSetBody(WireRequest):
    """Thân N54: tên bộ tuỳ chọn (1-120 ký tự NFC) và 1-`FILES_MAX` tệp."""

    name: FloorName | None = None
    files: list[FileDeclIn] = Field(min_length=1, max_length=FILES_MAX)


class SetChunkBody(WireRequest):
    """Thân N55: một khúc base64 và chỉ số của nó (như #6); trần độ dài thật theo `UPLOAD_CHUNK_BYTES` ở service."""

    chunk: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)


class CompleteSetBody(WireRequest):
    """Thân N56; `setId` chỉ để guard W21 so với đường."""

    set_id: str = Field(min_length=1)


class FloorTargetIn(WireRequest):
    """Đích "tầng có sẵn"; `replace: true` = người duyệt tick "Thay bản vẽ cũ" (chỉ nhận `true`, vắng = không thay)."""

    mode: Literal["floor"]
    floor_id: FloorIdField
    replace: Literal[True] | None = None


class NewFloorTargetIn(WireRequest):
    """Đích "tầng mới": id `L-…` client sinh (như #10) và tên tầng."""

    mode: Literal["newFloor"]
    floor_id: FloorIdField
    name: FloorName


class SectionTargetIn(WireRequest):
    """Đích "mặt cắt": trước K-32c thành dòng tầng `isSection` ẩn khỏi 3D (HS-03)."""

    mode: Literal["section"]
    name: FloorName


type ViewTargetIn = Annotated[FloorTargetIn | NewFloorTargetIn | SectionTargetIn, Field(discriminator="mode")]


class ViewEditIn(WireRequest):
    """Một hình trong bản duyệt N59: `id` vắng = hình người vẽ thêm; `target` vắng = chưa gán đích."""

    id: str | None = Field(default=None, min_length=1)
    file_id: str = Field(min_length=1)
    page_index: int = Field(ge=0)
    crop: CropRect
    excluded: bool
    kind: FloorKind
    storey_no: int | None = Field(default=None, ge=STOREY_NO_MIN, le=STOREY_NO_MAX)
    target: ViewTargetIn | None = None


SEEN_MAX: Final = 500
"""Trần số cảnh báo "đã xem" trong một bản duyệt (60 hình, mỗi hình vài cảnh báo)."""
ROOF_MM_MAX: Final = 5_000
"""Trần độ đua và chiều cao parapet người duyệt nhập (mm)."""
PITCH_DEG_MAX: Final = 85.0
"""Độ dốc mái phải dưới 85° (từ đó là tường, luật M-17)."""

PRINTABLE: Final = r"^[^\x00-\x1f\x7f]+$"
"""Khoá người dùng gửi không mang ký tự điều khiển (JSONB của Postgres từ chối `\\u0000`)."""

type _Ref = Annotated[str, Field(min_length=1, max_length=64, pattern=PRINTABLE)]
type _ElevationMm = Annotated[int, Field(strict=True, ge=ELEVATION_MIN, le=ELEVATION_MAX)]
type _RoofMm = Annotated[int, Field(strict=True, ge=0, le=ROOF_MM_MAX)]


class LevelDecisionIn(WireRequest):
    """Cốt và chiều cao người duyệt nhập cho tầng của hình `levelRef` (dải như #10); trường vắng = không nhập."""

    level_ref: _Ref
    elevation_mm: _ElevationMm | None = None
    height_mm: Annotated[int, Field(strict=True, ge=HEIGHT_MIN, le=HEIGHT_MAX)] | None = None


class RoofDecisionIn(WireRequest):
    """Mái của tầng có hình `levelRef`: các trường người duyệt nhập, hoặc `skip` = "không dựng mái, tôi tự vẽ"."""

    level_ref: _Ref
    skip: bool = False
    form: Literal["flat", "shed", "gable", "hip"] | None = None
    pitch_deg: Annotated[float, Field(ge=0, lt=PITCH_DEG_MAX)] | None = None
    quarter_turns: Annotated[int, Field(strict=True, ge=0, le=3)] | None = None
    eave_elevation_mm: _ElevationMm | None = None
    ridge_elevation_mm: _ElevationMm | None = None
    overhang_mm: _RoofMm | None = None
    parapet_mm: _RoofMm | None = None
    material: Literal["tile", "metal", "shingle"] | None = None


class BuildingDecisionsIn(WireRequest):
    """Quyết định của người duyệt trên đề xuất ngôi nhà (NHA-06b), thay **cả** bản đã lưu.

    `picks`: khoá mâu thuẫn (`elevationMm:<hình>`, `pitchDeg:<hình>`) → nguồn đã chọn. `seen`: khoá của các cảnh báo
    đã bấm "Đã xem", lấy nguyên văn từ `violations[].key` của route đọc `building`.
    """

    levels: list[LevelDecisionIn] = Field(default_factory=list, max_length=VIEWS_MAX)
    roofs: list[RoofDecisionIn] = Field(default_factory=list, max_length=VIEWS_MAX)
    picks: dict[Annotated[str, Field(min_length=1, max_length=96, pattern=PRINTABLE)], BuildingSource] = Field(
        default_factory=dict, max_length=2 * VIEWS_MAX
    )
    seen: list[Annotated[str, Field(min_length=1, max_length=256, pattern=PRINTABLE)]] = Field(
        default_factory=list, max_length=SEEN_MAX
    )


class ViewsBodyIn(WireRequest):
    """`body` của N59: **cả** danh sách hình (thay thế), tối đa `VIEWS_MAX`; `name` tuỳ chọn đổi tên bộ (K-65).

    `name` vắng = giữ tên hiện có (không có cách xoá tên); có thì luật như N54 (trim + NFC, 1-120 ký tự).
    `building` vắng = giữ quyết định đã lưu (client chưa biết khoá này không được làm mất chúng); có thì thay cả bản,
    `{}` là xoá hết.
    """

    name: FloorName | None = None
    views: list[ViewEditIn] = Field(max_length=VIEWS_MAX)
    building: BuildingDecisionsIn | None = None


class ReplaceViewsBody(WireRequest):
    """`{baseVersion, body}` của N59 (W20); thiếu `baseVersion` bị khung chặn 428 trước Pydantic."""

    base_version: Annotated[int, Field(strict=True, ge=0)]
    body: ViewsBodyIn


class ConfirmSetBody(WireRequest):
    """Thân N60: `setId` (W21) và `revision` của bản duyệt người dùng đang nhìn."""

    set_id: str = Field(min_length=1)
    revision: Annotated[int, Field(strict=True, ge=0)]


class CropOut(WireModel):
    """Khung của hình theo tỉ lệ trang gốc, cùng khoá với `CropRect` của #5."""

    x_ratio: float
    y_ratio: float
    width_ratio: float
    height_ratio: float


class ViewQuestionOut(WireModel):
    """Một câu hỏi cho người duyệt: chỉ mã (bộ mã N52), FE viết câu tiếng Việt."""

    code: QuestionCode


class ViewTargetOut(WireModel):
    """Đích đã lưu: `floorId` vắng ở `section`, `name` vắng ở `floor`, `replace` chỉ có khi `true`."""

    mode: TargetMode
    floor_id: str | None = None
    name: str | None = None
    replace: Literal[True] | None = None


class DrawingSetFileOut(WireModel):
    """Một tệp của bộ; `rejectedCode` (mã U-case) chỉ có khi `rejected`, `pageCount` sau N56."""

    id: str
    file_name: str
    size_bytes: int
    chunk_count: int
    status: FileStatus
    rejected_code: str | None = None
    page_count: int | None = None


class DrawingSetViewOut(WireModel):
    """Một hình của bộ; `imageUrl` = ảnh trang ký sẵn (W23), `sheetNo` = số hiệu theo khung tên, `uploadId` sau N60."""

    id: str
    file_id: str
    page_index: int
    crop: CropOut
    source: ViewSource
    excluded: bool
    image_url: str | None = None
    kind: FloorKind
    kind_confidence: float = Field(ge=0.0, le=1.0)
    title_text: str | None = None
    sheet_no: str | None = None
    storey_label: StoreyLabel | None = None
    storey_no: int | None = None
    storey_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    questions: list[ViewQuestionOut]
    target: ViewTargetOut | None = None
    upload_id: str | None = None


class DrawingSetTitleBlockOut(WireModel):
    """Khung tên chung của bộ (K-65): chỉ trường mà các trang đọc được khớp nhau; trường lệch/không đọc được vắng."""

    owner: str | None = None
    project: str | None = None
    location: str | None = None
    date: str | None = None


class DrawingSetOut(WireModel):
    """N54-N57, N59-N61: một bộ đầy đủ tệp và hình (hình theo `ord`, tệp theo thứ tự khai)."""

    id: str
    status: SetStatus
    revision: int
    progress_percent: int = Field(ge=0, le=100)
    name: str | None = None
    error_code: str | None = None
    created_at: WireDatetime
    confirmed_at: WireDatetime | None = None
    title_block: DrawingSetTitleBlockOut | None = None
    files: list[DrawingSetFileOut]
    views: list[DrawingSetViewOut]


class DrawingSetSummaryOut(WireModel):
    """Một mục N58: không kèm tệp/hình, chỉ số đếm."""

    id: str
    status: SetStatus
    name: str | None = None
    created_at: WireDatetime
    confirmed_at: WireDatetime | None = None
    file_count: int
    view_count: int
