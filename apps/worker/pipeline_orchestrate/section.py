"""Lượt chạy của dòng mặt cắt/mặt đứng chỉ đọc chữ (K-32c, NO-481): không tách tường, không dò đồ đạc, không dựng.

Dòng `floors.is_section` (HS-03) vẫn đi đường của một tầng (bảng tệp khoá ngoài tới `floors.pk`), nhưng lượt chạy
của nó chỉ chạy `preprocess` + `dimensionReading` (OCR: cốt, chiều cao tầng, tên hình, cho bộ giải của bộ hồ sơ).
Hai bước ML kia được **bỏ qua** bằng cách ghi `used[họ] = "skipped"` rồi đóng bước — nên quét bù (`queue_infer` bỏ họ
đã có trong `used`) và kết quả tới muộn (`_live`) đều cư xử đúng mà không thêm cột hay trạng thái. Quyết định đọc từ
`used["wallSegmentation"]`, không đọc lại `is_section`: đánh dấu giữa lượt không làm lượt đổi đường giữa chừng.

Khi `dimensionReading` xong, lượt đóng thẳng (`spatialDataBuild` + `qualityCheck` completed), tóm tắt `sectionOnly`:
`readiness` của tầng ấy không đòi kiểm tường (xem `apps/api/spatial_read/readiness.py`), không ghi tài liệu tầng, không
tạo phiên bản, không thông báo "AI xong", không xếp ý tưởng thiết kế.
"""

from collections.abc import Mapping
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.runs import record_step
from apps.api.spatial_read.readiness import SECTION_ONLY_KEY
from apps.worker.pipeline_orchestrate.pins import RunPins, record_quality_summary, record_used
from packages.core.clock import Clock
from packages.ml_contracts.families import ModelFamily

SKIPPED: Final = "skipped"
"""Giá trị `used` của họ bị bỏ qua cho dòng mặt cắt."""

SKIPPED_FAMILIES: Final[tuple[ModelFamily, ...]] = ("wallSegmentation", "openingAndFurnitureDetection")
"""Hai họ không chạy trên mặt cắt, theo thứ tự `PIPELINE_STEPS`."""

BUILD_STEP: Final = "spatialDataBuild"
QUALITY_STEP: Final = "qualityCheck"


def is_section_run(used: Mapping[ModelFamily, str]) -> bool:
    """Lượt này bỏ qua tách tường (tức chạy theo đường mặt cắt)."""
    return used.get("wallSegmentation") == SKIPPED


async def skip_section_steps(db: AsyncSession, *, run_id: str, pins: RunPins, clock: Clock) -> bool:
    """Ghi `skipped` cho hai họ ML và đóng hai bước đó; `False` = lượt đã kết thúc/bị thay (người gọi dừng).

    Gọi lại được (giao lặp): họ đã có trong `used` thì `record_used` trả `False` còn `record_step` bỏ bước lùi.
    """
    for family in SKIPPED_FAMILIES:
        if family not in pins.used:
            await record_used(db, run_id=run_id, family=family, used=SKIPPED)
        if await record_step(db, run_id=run_id, step=family, status="completed", clock=clock) is None:
            return False
    return True


async def finish_section_run(db: AsyncSession, *, run_id: str, clock: Clock) -> bool:
    """Đóng lượt mặt cắt khi chữ đã xong: tóm tắt `sectionOnly`, rồi `spatialDataBuild` và `qualityCheck` completed."""
    if await record_step(db, run_id=run_id, step=BUILD_STEP, status="completed", clock=clock) is None:
        return False
    summary = {
        SECTION_ONLY_KEY: True,
        "hasCritical": False,
        "criticalCount": 0,
        "lowConfidence": {},
        "checks": [],
        "criticalChecks": [],
    }
    await record_quality_summary(db, run_id=run_id, summary=summary)
    return await record_step(db, run_id=run_id, step=QUALITY_STEP, status="completed", clock=clock) is not None
