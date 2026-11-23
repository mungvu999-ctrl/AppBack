"""Payload task của bộ hồ sơ nhiều bản vẽ (K-32a, BE-00 §7).

Chỉ một id: tác vụ tự tra `drawing_sets` dưới khoá dòng rồi mới làm việc, nên giao lặp hay bộ đã bị xoá mềm/đổi
trạng thái giữa chừng đều là no-op (J06). Chuỗi: `drawing_set.analyze` (dựng ảnh trang) → `ml.infer.drawing_set.read`
(worker `ml`, hợp đồng ở `packages/ml_contracts/drawing_sets.py`) → `drawing_set.publish` (phân loại, ghi hình).
"""

from typing import Annotated, Final

from pydantic import AfterValidator

from packages.core.ids import check_id
from packages.messaging.tasks import TaskPayload

DRAWING_SET_ANALYZE_TASK: Final = "drawing_set.analyze"
"""Tên task — N56 (`apps.api.drawing_sets`) gửi, thân task ở `apps/api/drawing_sets/jobs.py`. Tiền tố
`drawing_set.` không có trong bảng định tuyến nên đi hàng `default`."""
DRAWING_SET_PUBLISH_TASK: Final = "drawing_set.publish"
"""Phân loại + đề xuất tầng + ghi hình (`apps/api/drawing_sets/jobs.py`); worker `ml` gửi sau lượt đọc; hàng
`default`."""

DrawingSetId = Annotated[str, AfterValidator(lambda value: check_id("dws", value))]


class DrawingSetAnalyzePayload(TaskPayload):
    """`drawing_set.analyze`: dựng ảnh trang cho một bộ `analyzing` rồi xếp lượt đọc."""

    schema_version: int = 1
    set_id: DrawingSetId


class DrawingSetPublishPayload(TaskPayload):
    """`drawing_set.publish`: đọc kết quả từng trang, phân loại và ghi hình cho bộ còn `analyzing`."""

    schema_version: int = 1
    set_id: DrawingSetId
