"""Ba bảng của lớp không gian (B3-02 [5]): `floor_documents`, `floor_change_log`, `floor_entity_ids`.

`floor_documents` giữ **một** dòng mỗi tầng: bản dây camelCase đã kiểm của lớp
(`document`), số bản ghi (`revision`) và tỉ lệ mm/px. Tách khỏi `floors` vì một lượt ghi
lớp (B3-03) khoá dòng này chứ không khoá dòng tầng, và vì jsonb lớn không nên nằm cùng
bảng mà #12 quét theo trang.

Không xoá mềm ở cả ba bảng: vòng đời của chúng gắn chặt vào tầng, nên `ON DELETE CASCADE`
tới `floors.pk` (và `projects.id` cho `floor_entity_ids`) là đủ — xoá cứng một tầng là
**một** lệnh `DELETE`, như `packages/db/models/drawings.py`.

CHECK ở đây là tầng phòng thủ cuối (như `floors`): nơi báo lỗi cho người dùng là
`apps/api/spatial_read` (`DocumentCorruptError` → 500) và B3-03 (422). Riêng hai CHECK
`removed = (value IS NULL)` và `jsonb_typeof(value) <> 'null'` là bất biến **thật** của
nhật ký: "trường bị gỡ" phải phân biệt được với "trường mang giá trị JSON `null`".

`scale_mm_per_px` là `numeric(12,6)` → `Decimal` ở Python, không bao giờ `float` (K20):
tỉ lệ nhân với toạ độ pixel ra mm, sai số nhị phân của `float` đẩy tường lệch hàng mm.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin
from packages.db.models.auth import one_of
from packages.db.models.floors import FLOORS
from packages.db.models.projects import PROJECTS

FLOOR_DOCUMENTS: Final = "floor_documents"
FLOOR_CHANGE_LOG: Final = "floor_change_log"
FLOOR_ENTITY_IDS: Final = "floor_entity_ids"

SCALE_SOURCES: Final = ("human", "measured", "pipeline", "project_default", "none")
"""Nguồn của tỉ lệ mm/px — nguồn duy nhất cho CHECK, `ScaleSource` và test (W24)."""

CHANGE_ENTITY_TYPES: Final = ("vertex", "wall", "door", "window", "furniture", "room", "dimension", "roof")
"""`entityType` của nhật ký thay đổi (HOP-DONG-MOI §1.3); ô mở tách theo `kind`."""

CHANGED_BY_RE: Final = r"^(usr_[0-9A-HJKMNP-TV-Z]{26}|system:pipeline)$"
"""Người ghi: một `sub` người dùng, hoặc đúng chuỗi `system:pipeline` (B5-06b). Không `system:worker`."""


class FloorDocumentRow(Base, TimestampMixin):
    """Tài liệu không gian của một tầng; `floor_pk` vừa là PK vừa là FK nên tối đa một dòng mỗi tầng."""

    __tablename__ = FLOOR_DOCUMENTS

    floor_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{FLOORS}.pk", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    document: Mapped[Any] = mapped_column(JSONB)
    scale_mm_per_px: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    scale_source: Mapped[str] = mapped_column(Text)
    scale_page_key: Mapped[str | None] = mapped_column(Text, default=None)
    last_writer_id: Mapped[str | None] = mapped_column(Text, default=None)
    last_body_sha256: Mapped[str | None] = mapped_column(CHAR(64), default=None)
    last_base_revision: Mapped[int | None] = mapped_column(BigInteger, default=None)

    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("scale_mm_per_px IS NULL OR scale_mm_per_px > 0", name="scale_mm_per_px_positive"),
        CheckConstraint(one_of("scale_source", SCALE_SOURCES), name="scale_source"),
        # Tỉ lệ và nguồn đi liền nhau: `'none'` nghĩa là chưa hiệu chỉnh nên không được
        # mang số, và có số thì phải nêu số ấy từ đâu ra (W24).
        CheckConstraint("(scale_source = 'none') = (scale_mm_per_px IS NULL)", name="scale_source_matches_scale"),
    )


class FloorChangeLogRow(Base, TimestampMixin):
    """Một trường đổi trong một lượt ghi (B3-03 ghi; B3-03, B3-04 đọc). Không ai sửa dòng đã ghi."""

    __tablename__ = FLOOR_CHANGE_LOG

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    floor_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{FLOORS}.pk", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(BigInteger)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    field: Mapped[str] = mapped_column(Text)
    # `none_as_null=True`: `value=None` ghi SQL NULL chứ không phải JSON `null`. Mặc định của
    # SQLAlchemy là ngược lại, và mọi dòng "trường bị gỡ" sẽ đâm vào CHECK dưới đây.
    value: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True, default=None)
    removed: Mapped[bool] = mapped_column(Boolean)
    changed_at: Mapped[datetime] = mapped_column()
    changed_by: Mapped[str] = mapped_column(Text)
    changed_by_name: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        CheckConstraint(one_of("entity_type", CHANGE_ENTITY_TYPES), name="entity_type"),
        # Gỡ trường và đặt trường là hai việc khác nhau; cờ và giá trị không được rời nhau.
        CheckConstraint("removed = (value IS NULL)", name="removed_matches_value"),
        # `to_jsonb(NULL)` ra JSON `null`, không phải SQL NULL — dòng như vậy sẽ đọc là
        # "có giá trị" mà giá trị lại là `null`, phá thế phân biệt ở CHECK trên.
        CheckConstraint("value IS NULL OR jsonb_typeof(value) <> 'null'", name="value_not_json_null"),
        CheckConstraint(f"changed_by ~ '{CHANGED_BY_RE}'", name="changed_by_format"),
        CheckConstraint("changed_by_name <> ''", name="changed_by_name_not_empty"),
        # F-08 đọc nhật ký của một tầng theo bản ghi, và lịch sử của **một** trường.
        Index(f"ix_{FLOOR_CHANGE_LOG}_floor_pk_revision", "floor_pk", "revision"),
        Index(
            f"ix_{FLOOR_CHANGE_LOG}_floor_pk_entity_id_field_revision",
            "floor_pk",
            "entity_id",
            "field",
            "revision",
        ),
    )


class FloorEntityIdRow(Base, TimestampMixin):
    """Chủ sở hữu một id thực thể trong một dự án (W4): id là duy nhất **toàn dự án**, không chỉ trong tầng.

    PK `(project_id, entity_id)` là chỗ duy nhất cưỡng chế luật ấy; `claim_entity_ids`
    dựa vào `ON CONFLICT DO NOTHING` của chính PK này để hai lượt ghi song song không
    thể cùng nhận một id.
    """

    __tablename__ = FLOOR_ENTITY_IDS

    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    floor_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{FLOORS}.pk", ondelete="CASCADE"))

    __table_args__ = (
        # Xoá một tầng, và `claim_entity_ids` gom id của tầng mình, đều tra theo `floor_pk`.
        Index(f"ix_{FLOOR_ENTITY_IDS}_floor_pk", "floor_pk"),
    )
