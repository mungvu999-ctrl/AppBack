"""Bảng `activity_log` — nhật ký mọi thao tác có `logged = true` ở `cases.toml` (C18, W6).

`actor_id` **không** có FK tới `users`: một dòng nhật ký phải sống lâu hơn người
dùng bị xoá mềm (và lâu hơn `system:pipeline`, không phải một hàng `users`), nên
khoá ngoại sẽ chặn nhầm việc xoá mềm hoặc đòi `ON DELETE SET NULL` làm mất dấu vết
ai đã làm gì. Không có cột `request_id`: header `X-Request-Id` do client tự đặt
được, ghi vào nhật ký sẽ để lộ dữ liệu không đáng tin (W6).

`KIND_PATTERN`, `OBJECT_CODE_MAX`, `OBJECT_LABEL_MAX` là hằng công khai để
`record_activity` (E) kiểm tra trước khi ghi — CHECK ở đây chỉ là tầng phòng thủ
cuối, không phải nơi báo lỗi cho người dùng.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin

TABLE: Final = "activity_log"

KIND_PATTERN: Final = r"^[a-z]+\.[a-z_]+$"
OBJECT_CODE_MAX: Final = 120
OBJECT_LABEL_MAX: Final = 200


class ActivityLog(Base, TimestampMixin):
    """Một dòng nhật ký hoạt động: ai, làm gì, trên đối tượng nào, lúc nào."""

    __tablename__ = TABLE

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    object_code: Mapped[str] = mapped_column(Text)
    object_label: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(Text, default=None)
    at: Mapped[datetime] = mapped_column()

    __table_args__ = (
        CheckConstraint(f"kind ~ '{KIND_PATTERN}'", name="kind_format"),
        CheckConstraint(f"char_length(object_code) BETWEEN 1 AND {OBJECT_CODE_MAX}", name="object_code_length"),
        CheckConstraint(f"char_length(object_label) BETWEEN 1 AND {OBJECT_LABEL_MAX}", name="object_label_length"),
        Index(f"ix_{TABLE}_actor_id_at_id", "actor_id", text("at DESC"), text("id DESC")),
        Index(f"ix_{TABLE}_at", "at"),
    )
