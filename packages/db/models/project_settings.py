"""Bảng `project_settings`: cài đặt một dự án, có `revision` cho ghi có version (B2-02 [5], W20).

Không dòng nào tồn tại cho tới lần ghi đầu (`revision = 1`); `apps/api/project_settings/read.py`
trả mặc định `revision 0` khi chưa có dòng. `last_writer_id` (`sub` người ghi, K05, không FK vì
người có thể bị xoá) và `last_body_sha256` cho phép nhận ra một lượt ghi lặp (C09b).
`scene_style` (FE3D-B2): phong cách cảnh 3D của dự án, NULL = FE dùng mặc định của nó.
`detect_furniture` (QT-FURN-TOGGLE): lượt pipeline có dựng đồ đạc AI không; mặc định bật (BENCH3D-FURN-ON).
`separate_units` (UX-R2 K34): các tầng là những căn hộ riêng, không chồng thành một toà nhà.

CHECK ở đây là tầng phòng thủ cuối; nơi báo lỗi cho người dùng là `apps/api/project_settings` (422).
"""

from decimal import Decimal
from typing import Final

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, Numeric, Text, text, true
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin
from packages.db.models.projects import PROJECTS

PROJECT_SETTINGS: Final = "project_settings"


class ProjectSettingsRow(Base, TimestampMixin):
    """Cài đặt của một dự án; khoá chính là `project_id`, xoá cứng dự án thì dòng đi theo (CASCADE)."""

    __tablename__ = PROJECT_SETTINGS

    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    building_type: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    length_unit: Mapped[str] = mapped_column(Text)
    snap_tolerance_mm: Mapped[int] = mapped_column(Integer)
    confidence_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    default_scale_mm_per_px: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    scene_style: Mapped[str | None] = mapped_column(Text, default=None)
    detect_furniture: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    separate_units: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_writer_id: Mapped[str] = mapped_column(Text)
    last_body_sha256: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_min"),
        CheckConstraint(
            "building_type IN ('residential', 'commercial', 'industrial', 'mixed', 'other')",
            name="building_type_values",
        ),
        CheckConstraint("notes IS NULL OR char_length(notes) BETWEEN 1 AND 500", name="notes_length"),
        CheckConstraint("length_unit IN ('mm', 'm')", name="length_unit_values"),
        CheckConstraint("snap_tolerance_mm BETWEEN 1 AND 120", name="snap_tolerance_mm_range"),
        CheckConstraint("confidence_threshold BETWEEN 0 AND 1", name="confidence_threshold_range"),
        CheckConstraint("default_scale_mm_per_px BETWEEN 0.01 AND 1000", name="default_scale_mm_per_px_range"),
        CheckConstraint("char_length(last_body_sha256) = 64", name="last_body_sha256_length"),
        CheckConstraint(
            "scene_style IS NULL OR scene_style IN ('am-ap', 'hien-dai-sang', 'go-am', 'toi-gian', 'sang-trong-toi')",
            name="scene_style_values",
        ),
    )
