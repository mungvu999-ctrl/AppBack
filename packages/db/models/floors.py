"""Bảng `floors`: một tầng thuộc một dự án (B2-03 [5]).

`level_id` là id không gian client sinh (`is_spatial_id("level", …)`, W4), không phải
`new_id`. Xoá mềm (`SoftDeleteMixin`): #11 chỉ đặt `deleted_at`, lịch dọn xoá cứng sau
`FLOOR_RESTORE_WINDOW_S + FLOOR_PURGE_AFTER_D`. `unique_active` chỉ tính dòng chưa xoá,
nên id có thể tái dùng sau khi tầng cũ đã xoá mềm quá cửa sổ khôi phục.

CHECK ở đây là tầng phòng thủ cuối (như `project_floor_summaries`, B2-01) — nơi báo lỗi
cho người dùng là `apps/api/floors` (422 theo [2]), không phải DB.
`hidden_in_3d` (FE3D-B3): tầng bị ẩn khỏi cảnh 3D, mặc định `false`.
`is_section` (HS-03): bản vẽ của dòng này là mặt cắt chứ không phải mặt bằng một tầng, mặc định `false`;
đề xuất cho cả nhà (`apps/api/drawing_sets`) đọc cốt trên nó thay vì coi nó là một sàn.
"""

from typing import Final

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Identity, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, SoftDeleteMixin, TimestampMixin, mm_column, unique_active
from packages.db.models.projects import PROJECTS

FLOORS: Final = "floors"


class FloorRow(Base, TimestampMixin, SoftDeleteMixin):
    """Một tầng; `created_by` là `sub` người tạo (K05), không FK (người có thể đã bị xoá)."""

    __tablename__ = FLOORS

    pk: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"))
    level_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    floor_order: Mapped[int] = mapped_column(Integer)
    elevation_mm: Mapped[int] = mm_column()
    height_mm: Mapped[int] = mm_column()
    hidden_in_3d: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_section: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_by: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("level_id ~ '^L-[0-9A-Z]{10,64}$'", name="level_id_format"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_length"),
        CheckConstraint("floor_order BETWEEN 0 AND 999", name="floor_order_range"),
        CheckConstraint("elevation_mm BETWEEN -30000 AND 300000", name="elevation_mm_range"),
        CheckConstraint("height_mm BETWEEN 2000 AND 10000", name="height_mm_range"),
        unique_active(f"uq_{FLOORS}_level", "project_id", "level_id"),
        # Dựng `FloorOut` theo thứ tự (floor_order, pk), chỉ tầng chưa xoá (#12, `floor_outs`).
        Index(
            f"ix_{FLOORS}_project_id_floor_order_pk",
            "project_id",
            "floor_order",
            "pk",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # `project_of_floor` tra theo `level_id` một mình, chỉ tầng chưa xoá.
        Index(f"ix_{FLOORS}_level_id", "level_id", postgresql_where=text("deleted_at IS NULL")),
        # Lịch dọn quét tầng đã xoá mềm quá hạn.
        Index(f"ix_{FLOORS}_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
        # PERF-03: lịch dọn tra cờ "còn dòng anh em cùng (project_id, level_id)" bất kể đã xoá
        # hay chưa (jobs.py `_sibling_flag`, `_has_sibling`) — ba index trên đều một phần nên
        # không dòng nào phủ được câu này; thêm đầy đủ, không xoá ba index đã chốt ở [5].
        Index(f"ix_{FLOORS}_project_id_level_id", "project_id", "level_id"),
    )
