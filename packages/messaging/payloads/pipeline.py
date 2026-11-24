"""Payload task của bước dựng lớp không gian và các bước sau nó (B5-05 [2], BE-00 §7).

`RunStepPayload` chỉ mang `run_id`: `pipeline.persist.run` (B5-06b), `pipeline.quality.run`
(B5-07) tự tra lại lượt chạy dưới khoá dòng, không tin trạng thái chép trong thông điệp
(BE-00 §7 "Không tin `apps/ml`"). `BuildStepPayload` cho `pipeline.build.run` mang thêm toạ độ
và tỉ lệ dự phòng vì bước dựng chưa có bảng lượt chạy để tự tra (B5-05 [1]).
"""

from decimal import Decimal
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from packages.core.ids import check_id, is_spatial_id
from packages.core.object_keys import check_key
from packages.messaging.tasks import TaskPayload

RunId = Annotated[str, AfterValidator(lambda value: check_id("run", value))]
LevelId = Annotated[str, AfterValidator(lambda value: _check_level_id(value))]
FallbackScale = Annotated[Decimal, Field(gt=0, le=1000, allow_inf_nan=False)]
PixelSize = Annotated[int, Field(ge=1)]


def _check_level_id(value: str) -> str:
    """`level_id` phải đạt `is_spatial_id("level", …)` (B5-05 [2])."""
    if not is_spatial_id("level", value):
        raise ValueError(f"id tầng phải có dạng L-<base36 HOA>: {value!r}")
    return value


class RunStepPayload(TaskPayload):
    """Task chỉ cần biết lượt chạy nào: `pipeline.persist.run`, `pipeline.quality.run`."""

    schema_version: int = 1
    run_id: RunId


class BuildStepPayload(TaskPayload):
    """`pipeline.build.run`: đọc ba artifact ML dưới `run_prefix`, dựng `layer.json`."""

    schema_version: int = 1
    run_id: RunId
    level_id: LevelId
    run_prefix: str
    width_px: PixelSize
    height_px: PixelSize
    fallback_mm_per_px: FallbackScale
    page_key: Annotated[str, AfterValidator(check_key)] | None = None
    """Trang đã nắn cho nguồn gợi ý mực dư (B4); vắng ở thông điệp cũ → không gợi ý."""
    px_per_paper_mm: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    """px mỗi mm giấy (PDF, `drawings.px_per_paper_mm`); vắng → không đọc ghi chú `TL 1:N` (U01)."""
    detect_furniture: bool = True
    """Cài đặt dự án `detectFurniture` (QT-FURN-TOGGLE); vắng ở thông điệp cũ → dựng đồ như trước.

    Mặc định `True` này LỆCH mặc định `False` của cài đặt dự án (`project_settings`), chủ ý giữ (L2-N05 B):
    `step_done` luôn truyền giá trị nên chỉ thông điệp cũ/thiếu trường bị bật. Đổi theo quyết định K-20.
    """

    @model_validator(mode="after")
    def _check_run_prefix(self) -> Self:
        """`run_prefix` bắt đầu `projects/`, kết thúc `/runs/{run_id}/`, không chứa `..` (B5-05 [2])."""
        suffix = f"/runs/{self.run_id}/"
        segments = self.run_prefix.split("/")
        if not self.run_prefix.startswith("projects/") or not self.run_prefix.endswith(suffix) or ".." in segments:
            raise ValueError(f"run_prefix phải là projects/…{suffix}, không '..': {self.run_prefix!r}")
        return self
