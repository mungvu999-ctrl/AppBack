"""Payload task của bộ hồ sơ nhiều bản vẽ (K-32a, BE-00 §7).

Chỉ một id: tác vụ tự tra `drawing_sets` dưới khoá dòng rồi mới làm việc, nên giao lặp hay bộ đã bị xoá mềm/đổi
trạng thái giữa chừng đều là no-op (J06).
"""

from typing import Annotated, Final

from pydantic import AfterValidator

from packages.core.ids import check_id
from packages.messaging.tasks import TaskPayload

DRAWING_SET_ANALYZE_TASK: Final = "drawing_set.analyze"
"""Tên task — N56 (`apps.api.drawing_sets`) gửi, thân task ở `apps/api/drawing_sets/jobs.py`. Tiền tố
`drawing_set.` không có trong bảng định tuyến nên đi hàng `default`."""

DrawingSetId = Annotated[str, AfterValidator(lambda value: check_id("dws", value))]


class DrawingSetAnalyzePayload(TaskPayload):
    """`drawing_set.analyze`: rã trang và đề xuất hình cho một bộ `analyzing`."""

    schema_version: int = 1
    set_id: DrawingSetId
