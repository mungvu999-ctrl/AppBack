"""Bảng `share_links`: liên kết chỉ đọc cho người ngoài dự án (hợp đồng #47-#49).

`token` là chuỗi ngẫu nhiên `secrets.token_urlsafe(32)`; hạn (`expires_at`) và thu hồi
(`revoked_at`) do máy chủ quyết, FE chỉ hiển thị `status` máy chủ trả.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin
from packages.db.models.projects import PROJECTS

SHARE_LINKS: Final = "share_links"


class ShareLinkRow(Base, TimestampMixin):
    """Một liên kết chia sẻ của một dự án; xoá cứng dự án thì dòng đi theo (CASCADE)."""

    __tablename__ = SHARE_LINKS

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"), index=True)
    # ponytail: token lưu thô để GET danh sách trả lại `url`; băm nếu cần chống lộ DB.
    token: Mapped[str] = mapped_column(Text, unique=True)
    permission: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    viewpoint_code: Mapped[str | None] = mapped_column(Text, default=None)
    label: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (CheckConstraint("permission IN ('view', 'comment')", name="permission_values"),)
