"""Luật chung của N15 và N16: nguồn tỉ lệ hiệu lực, `scaleStatus`, và dựng `Level`.

Ba route đọc phải trả **cùng một** `Level` cho cùng một tầng — `id`, `areaM2`, `reviewed`
của N15 không được lệch N16 — nên phép dựng nằm ở đây đúng một chỗ, và cả hai đường đều
lấy `id/name/order/elevationMm/heightMm/areaM2` từ chính `FloorOut` mà #12, #33 trả
(`floors.lookup.floor_outs`), chứ không đọc lại bảng đếm theo cách riêng.

**Tỉ lệ gắn với trang** (HOP-DONG-MOI §4 #35): một tỉ lệ `human` chỉ còn là `human` khi
trang bản vẽ hiện tại vẫn là trang lúc người hiệu chỉnh. Pipeline chạy lại ra trang khác
thì tỉ lệ cũ không còn xác nhận được gì, nên nó tụt xuống `pipeline` và N16 hiện
`scaleStatus: 'unresolved'`. Giá trị tỉ lệ **không** đổi — chỉ hạng của nó đổi.

Module này nhập được trong ngữ cảnh worker: không `fastapi`, không `starlette`.
"""

from statistics import median
from typing import Final, Literal

from apps.api.projects.wire import FloorOut
from apps.api.spatial_read.documents import FloorDocument, ScaleSource
from apps.api.spatial_read.wire import LevelOut
from packages.domain.scale import classify_scale_range
from packages.domain.spatial import SpatialLayer

_HUMAN_CONFIDENCE = 1.0
"""`Level` và `Building` do server suy ra, không do mô hình đoán → `source='human'`, tin cậy 1."""


def effective_scale_source(doc: FloorDocument, page_key: str | None) -> ScaleSource:
    """Hạng thật của tỉ lệ: `human`, `measured` gắn với **trang** lúc đặt/đọc, trang khác → `pipeline`.

    Cổng `drawing_pages` không biết tầng (vắng khoá) hay tài liệu chưa ghi `scale_page_key`
    thì giữ nguyên hạng cũ: không có bằng chứng trang đã đổi, và hạ hạng vì thiếu dữ liệu sẽ
    bắt người dùng hiệu chỉnh lại một tỉ lệ vẫn đúng.
    """
    if doc.scale_source not in ("human", "measured") or doc.scale_page_key is None or page_key is None:
        return doc.scale_source
    return doc.scale_source if page_key == doc.scale_page_key else "pipeline"


def scale_status(scale_source: ScaleSource) -> Literal["unresolved"] | None:
    """`'unresolved'` cho tỉ lệ tạm (`pipeline`, `project_default`); `human`, `measured`, `none` vắng khoá (W24).

    `measured`: từ ≥ 3 số đo OCR đồng thuận, không nghi vấn (`BuiltLayer.persisted_scale_source`), không bắt xác nhận.
    """
    return "unresolved" if scale_source in ("pipeline", "project_default") else None


def provisional_scale_source(scale_source: ScaleSource) -> Literal["pipeline", "project_default"] | None:
    """Nguồn của tỉ lệ tạm, đi kèm `scaleStatus` (UX-R1 K12): vắng đúng khi `scaleStatus` vắng.

    `project_default` là "máy không đo được, đang dùng mặc định của dự án" (thường 1 mm/px) — FE
    tô đỏ và chặn bước phụ thuộc số đo; `pipeline` là "máy đo được, cần người kiểm" (vàng).
    """
    if scale_source == "pipeline" or scale_source == "project_default":
        return scale_source
    return None


SUGGESTED_WALL_THICKNESS_MM: Final = 150
"""Bề dày tường trung vị mà tỉ lệ đề xuất nhắm tới — cùng đích `s_dựng` của pipeline (bước 2)."""


def suggested_scale_mm_per_px(doc: FloorDocument, scale_source: ScaleSource) -> float | None:
    """Tỉ lệ đề xuất cho tầng `project_default` (UX-R2 K01): đưa bề dày tường trung vị về 150 mm.

    Tính lúc đọc từ tỉ lệ đang lưu và tường đang có, không lưu, không đổi tầng: đây chỉ là gợi ý
    để người dùng xác nhận, bề dày 150 mm chưa ai kiểm. Vắng khi nguồn khác, chưa có tỉ lệ hay
    tường, hoặc kết quả (3 chữ số thập phân) ra ngoài dải đọc được 1-200 mm/px.
    """
    if scale_source != "project_default" or doc.scale_mm_per_px is None or not doc.layer.walls:
        return None
    thickness = median(wall.thickness_mm for wall in doc.layer.walls)
    value = round(float(doc.scale_mm_per_px) * SUGGESTED_WALL_THICKNESS_MM / thickness, 3)
    return value if classify_scale_range(value) == "inRange" else None


def level_reviewed(layer: SpatialLayer, scale_source: ScaleSource) -> bool:
    """Tầng coi là đã duyệt: có ≥ 1 tường, mọi mục của lớp `reviewed`, và tỉ lệ không còn tạm.

    v1 **không** tính `Axis`, `Dimension` (HOP-DONG-MOI §4.1): kích thước là chú thích đo vẽ,
    và bắt duyệt hết chúng sẽ khoá cả tầng vì một chuỗi OCR không ai đụng tới. Tầng trống
    (0 tường) không phải "đã duyệt xong" mà là "chưa có gì để duyệt".
    """
    return (
        bool(layer.walls) and all(entity.reviewed for entity in layer.entities()) and scale_status(scale_source) is None
    )


def level_out(floor: FloorOut, doc: FloorDocument, scale_source: ScaleSource) -> LevelOut:
    """`Level` của một tầng cho cả N15 lẫn N16; `scaleMillimetresPerPixel` vắng khi chưa có.

    Nhận **nguồn đã chấm hạng** chứ không nhận `page_key`: N16 còn cần chính nguồn ấy cho
    `scaleStatus`, nên để hàm này tự gọi `effective_scale_source` là bắt người gọi tính hai lần
    cùng một thứ — và mở đường cho hai chỗ chấm hạng lệch nhau.
    """
    scale = doc.scale_mm_per_px
    return LevelOut(
        id=floor.id,
        name=floor.name,
        order=floor.order,
        elevation_mm=floor.elevation_mm,
        height_mm=floor.height_mm,
        area_m2=floor.area_m2,
        scale_millimetres_per_pixel=None if scale is None else float(scale),
        confidence=_HUMAN_CONFIDENCE,
        source="human",
        reviewed=level_reviewed(doc.layer, scale_source),
    )
