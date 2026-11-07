"""Payload task của bộ xuất GLB phía máy chủ (M1-01, BE-00 §7).

Chỉ một id: worker tự tra `model_exports` và đọc `snapshot.json` trong kho.
"""

from typing import Annotated, Final

from pydantic import AfterValidator

from packages.core.ids import check_id
from packages.messaging.tasks import TaskPayload

MODEL_EXPORT_TASK: Final = "bake.model_export.compile"
"""Tên task — tiền tố `bake.` đưa task lên hàng `bake` (`packages.messaging.celery_app`).
Hợp đồng ghi `model_export.compile.run`, nhưng tên đó rơi vào hàng `default`."""

MODEL_EXPORT_SNAPSHOT: Final = "snapshot.json"
"""Object API ghi dưới `model_export_prefix(project_id, export_id)` trước khi gửi task."""

MODEL_EXPORT_FILES: Final = ("model.glb", "report.json", "CREDITS.txt")
"""Tên object đầu ra worker ghi; N51 chỉ phục vụ đúng các tên này."""

ModelExportId = Annotated[str, AfterValidator(lambda value: check_id("mex", value))]


class ModelExportPayload(TaskPayload):
    """`bake.model_export.compile`: dựng GLB cho một lượt `queued`."""

    schema_version: int = 1
    export_id: ModelExportId
