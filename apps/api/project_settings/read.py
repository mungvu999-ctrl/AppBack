"""Đọc cài đặt dự án — hàm công khai cho prompt sau (B5-05, B5-06b đọc tỉ lệ mặc định, W24).

Không nhập `fastapi`: worker nhập được module này ngoài tiến trình API (BE-00 §7).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.models.project_settings import ProjectSettingsRow


@dataclass(frozen=True)
class ProjectSettingsValue:
    """Cài đặt đã lưu (hay mặc định); `revision 0` nghĩa là chưa từng ghi."""

    revision: int
    building_type: str
    notes: str | None
    length_unit: str
    snap_tolerance_mm: int
    confidence_threshold: Decimal
    default_scale_mm_per_px: Decimal
    scene_style: str | None = None
    detect_furniture: bool = True
    """QT-FURN-TOGGLE: dựng đồ đạc AI vào lớp; `False` = chỉ tường, ô mở, phòng."""
    separate_units: bool = False
    """UX-R2 K34: các tầng là những căn hộ riêng, không phải tầng chồng của một toà nhà."""


DEFAULT_SETTINGS: Final = ProjectSettingsValue(
    revision=0,
    building_type="residential",
    notes=None,
    length_unit="mm",
    snap_tolerance_mm=50,
    confidence_threshold=Decimal("0.75"),
    default_scale_mm_per_px=Decimal(1),
)
"""Giá trị khi dự án chưa có dòng (`projectSettingsGateway.ts:141-149`)."""


def value_of(row: ProjectSettingsRow) -> ProjectSettingsValue:
    """Đổi một dòng DB thành giá trị bất biến."""
    return ProjectSettingsValue(
        revision=row.revision,
        building_type=row.building_type,
        notes=row.notes,
        length_unit=row.length_unit,
        snap_tolerance_mm=row.snap_tolerance_mm,
        confidence_threshold=row.confidence_threshold,
        default_scale_mm_per_px=row.default_scale_mm_per_px,
        scene_style=row.scene_style,
        detect_furniture=row.detect_furniture,
        separate_units=row.separate_units,
    )


async def find_row(db: AsyncSession, project_id: str) -> ProjectSettingsRow | None:
    """Dòng cài đặt (đọc lại từ DB, bỏ qua bản cache của session); `None` khi chưa có."""
    return (
        await db.execute(
            select(ProjectSettingsRow)
            .where(ProjectSettingsRow.project_id == project_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def read_settings(db: AsyncSession, project_id: str) -> ProjectSettingsValue:
    """Cài đặt của dự án; chưa có dòng → `DEFAULT_SETTINGS` (revision 0). Không kiểm quyền, không khoá."""
    row = await find_row(db, project_id)
    return DEFAULT_SETTINGS if row is None else value_of(row)
