"""Bảng `notifications`: thông báo của một người (B4-02 [5], hợp đồng #19-#22).

- `dedupe_key` UNIQUE: `notify` chèn `ON CONFLICT DO NOTHING`, nên lượt giao lại của task không sinh dòng thứ hai (J10).
- `stream_id` là id mục Redis Stream đã phát; `NULL` = chưa biết đã phát chưa (lịch quét bù ghi nó, K32).
- Xoá cứng dự án thì thông báo đi theo (CASCADE); xoá **mềm** dự án chỉ ẩn thông báo ở #19.
- Hai CHECK giữ luật dây ở lớp cuối: lớp theo tầng phải có `floor_level_id`; lời mời chỉ dẫn tới `projectSettings`.
"""

from typing import Final, Literal, get_args

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin
from packages.db.models.auth import USERS, one_of
from packages.db.models.projects import PROJECTS

NOTIFICATIONS: Final = "notifications"

NotificationKind = Literal["aiCompleted", "violationFound", "projectInvite", "aiFailed"]
"""Bốn loại server được gửi; `commentMention` của FE là việc của v2 nên không có ở đây.

`aiFailed` đứng cuối: CHECK `kind` dựng theo thứ tự này (`one_of`), revision `r20261026_b4_02_p0_11` chép đúng chuỗi.
"""
NotificationPlace = Literal[
    "walls", "objects", "dimensions", "grids", "rooms", "thickness", "floors", "rules", "projectSettings"
]
"""Chín màn mà một thông báo được dẫn tới (FE `NOTIFICATION_PLACES`)."""

KINDS: Final[tuple[str, ...]] = get_args(NotificationKind)
PLACES: Final[tuple[str, ...]] = get_args(NotificationPlace)
FLOOR_PLACES: Final = frozenset(PLACES[:6])
"""Sáu lớp duyệt theo tầng: dựng đường dẫn cần `floorId`."""
INVITE_PLACE: Final = "projectSettings"


def _sql_list(values: frozenset[str] | tuple[str, ...]) -> str:
    """`'a', 'b'` cho `IN (...)`; thứ tự cố định để model và migration cùng một chuỗi."""
    return ", ".join(f"'{value}'" for value in sorted(values))


class NotificationRow(Base, TimestampMixin):
    """Một thông báo; `user_id` là người nhận, không bao giờ lấy từ thân request (K05)."""

    __tablename__ = NOTIFICATIONS

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{USERS}.id"))
    kind: Mapped[str] = mapped_column(Text)
    place: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"))
    project_name: Mapped[str] = mapped_column(Text)
    floor_level_id: Mapped[str | None] = mapped_column(Text, default=None)
    object_label: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text, default=None)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    stream_id: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        CheckConstraint(one_of("kind", KINDS), name="kind"),
        CheckConstraint(one_of("place", PLACES), name="place"),
        CheckConstraint(f"place NOT IN ({_sql_list(FLOOR_PLACES)}) OR floor_level_id IS NOT NULL", name="floor_place"),
        CheckConstraint(f"kind <> 'projectInvite' OR place = '{INVITE_PLACE}'", name="invite_place"),
        # Phục vụ #19 (`ORDER BY created_at DESC, id DESC` theo người) và bước xoá của lịch dọn.
        Index(f"ix_{NOTIFICATIONS}_user_id_created_at_id", "user_id", text("created_at DESC"), text("id DESC")),
        # Lịch quét bù chỉ đọc các dòng chưa có `stream_id`.
        Index(f"ix_{NOTIFICATIONS}_created_at_unsent", "created_at", postgresql_where=text("stream_id IS NULL")),
    )
