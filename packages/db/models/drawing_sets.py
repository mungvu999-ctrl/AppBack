"""Bốn bảng của "bộ hồ sơ nhiều bản vẽ" (K-32a, lát cắt 2): một lớp **phía trên** luồng tải từng tầng.

`drawing_sets` (một lần tải cả bộ) → `drawing_set_files` (từng tệp khai ở N54) → `drawing_set_file_chunks` (khúc
nhận ở N55, mẫu `upload_chunks`) và `drawing_set_views` (từng hình máy tách hay người vẽ thêm, duyệt ở N59).
Không sửa `uploads`, `pipeline_runs`, `drawings`, `floors`: quan hệ với chúng chỉ một chiều, từ bộ xuống —
`views.upload_id` điền lúc xác nhận (N60) và `views.target_floor_pk` (đích "tầng có sẵn"). Hai FK này
`SET NULL` để dọn tầng/lượt tải không bị bộ hồ sơ chặn; mọi FK còn lại `CASCADE` để xoá cứng dự án là một lệnh.

`drawing_sets` xoá mềm (N61 chỉ đặt `deleted_at`; tầng và lượt chạy đã tạo giữ nguyên). Lượt phân tích bộ
**không** ghi `pipeline_runs` (cột `floor_pk` NOT NULL): trạng thái nằm ở `status` + `progress_percent`.
CHECK ở đây là tầng phòng thủ cuối — nơi báo lỗi cho người dùng là `apps/api/drawing_sets` (422/409).
Tập `kind` lấy từ miền (`packages.domain.drawing_set.titles.Kind`), không chép hằng.
"""

from datetime import datetime
from typing import Any, Final, get_args

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, SoftDeleteMixin, TimestampMixin
from packages.db.models.auth import one_of
from packages.db.models.drawings import UPLOAD_STATUSES, UPLOADS
from packages.db.models.floors import FLOORS
from packages.db.models.projects import PROJECTS
from packages.domain.drawing_set.titles import Kind

DRAWING_SETS: Final = "drawing_sets"
DRAWING_SET_FILES: Final = "drawing_set_files"
DRAWING_SET_FILE_CHUNKS: Final = "drawing_set_file_chunks"
DRAWING_SET_VIEWS: Final = "drawing_set_views"

SET_STATUSES: Final = ("receiving", "analyzing", "review", "confirmed", "failed")
"""Vòng đời một bộ (K-32a §3); `confirmed`/`failed` là trạng thái cuối."""
FILE_STATUSES: Final = UPLOAD_STATUSES
"""Vòng đời một tệp của bộ = vòng đời một lượt tải (`receiving complete rejected`), dùng chung một nguồn."""
VIEW_SOURCES: Final = ("ai", "user")
"""Hình do máy tách (`ai`, chỉ được loại trừ) hay người duyệt vẽ thêm (`user`, vắng khỏi N59 thì xoá)."""
VIEW_KINDS: Final[tuple[str, ...]] = get_args(Kind)
"""7 loại hình của N52 (`plan section elevation roof site detail other`), suy từ miền."""
TARGET_MODES: Final = ("floor", "newFloor", "section")
"""Đích của hình khi xác nhận: tầng có sẵn, tầng mới (id client sinh), hay dòng mặt cắt."""
_CONFIDENCE: Final = "độ tin của máy trong [0, 1], không phải độ dài hay tiền"


class DrawingSetRow(Base, TimestampMixin, SoftDeleteMixin):
    """Một bộ hồ sơ của một dự án; `created_by`/`confirmed_by` là `sub` (K05), không FK.

    `revision` tăng mỗi lần N59 ghi bản duyệt (GV, W20) và là điều kiện của N60 (`DRAWING_SET_REVISION_STALE`).
    `progress_percent` chỉ tăng trong một lượt phân tích (FE vẽ thanh tiến độ thẳng từ số này).
    """

    __tablename__ = DRAWING_SETS

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{PROJECTS}.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    analysis_started_at: Mapped[datetime | None] = mapped_column(default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(default=None)
    confirmed_by: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(one_of("status", SET_STATUSES), name="status"),
        CheckConstraint("progress_percent BETWEEN 0 AND 100", name="progress_percent_range"),
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        CheckConstraint("name IS NULL OR char_length(name) BETWEEN 1 AND 120", name="name_length"),
        # N58: bộ của dự án, mới trước, phân trang theo cursor `(created_at, id)` (W22), bỏ bộ đã xoá mềm.
        Index(
            f"ix_{DRAWING_SETS}_project_id_created_at_id",
            "project_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class DrawingSetFileRow(Base, TimestampMixin):
    """Một tệp khai ở N54; `ord` là thứ tự người dùng chọn (UNIQUE trong bộ), `page_count` ghi ở N56."""

    __tablename__ = DRAWING_SET_FILES

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    set_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{DRAWING_SETS}.id", ondelete="CASCADE"))
    ord: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(Text)
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger)
    declared_type: Mapped[str] = mapped_column(Text)
    sniffed_kind: Mapped[str | None] = mapped_column(Text, default=None)
    chunk_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    rejected_code: Mapped[str | None] = mapped_column(Text, default=None)
    original_key: Mapped[str | None] = mapped_column(Text, default=None)
    content_sha256: Mapped[str | None] = mapped_column(Text, default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        CheckConstraint(one_of("status", FILE_STATUSES), name="status"),
        CheckConstraint("ord >= 0", name="ord_non_negative"),
        CheckConstraint("declared_size_bytes > 0", name="declared_size_bytes_positive"),
        CheckConstraint("chunk_count > 0", name="chunk_count_positive"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="page_count_positive"),
        CheckConstraint("content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256_format"),
        Index(f"uq_{DRAWING_SET_FILES}_set_id_ord", "set_id", "ord", unique=True),
    )


class DrawingSetFileChunkRow(Base, TimestampMixin):
    """Một khúc đã nhận của một tệp trong bộ; PK `(file_id, chunk_index)` nên gửi lại khúc là ghi đè (U10)."""

    __tablename__ = DRAWING_SET_FILE_CHUNKS

    file_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{DRAWING_SET_FILES}.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
    )


class DrawingSetViewRow(Base, TimestampMixin):
    """Một hình trên một trang của một tệp: khung `crop` (tỉ lệ trang, dạng `CropRect` của #5) + loại + đích.

    `machine` giữ đề xuất gốc của máy và cues (để đo K-32g) — người sửa loại/tầng không ghi đè nó. Hình `ai`
    không bao giờ bị xoá ở N59, chỉ `excluded`. `target_*` chỉ có nghĩa khi `target_mode` đặt; `upload_id`
    điền lúc N60 xác nhận (đường từ bộ xuống `uploads` → `pipeline_runs`).
    """

    __tablename__ = DRAWING_SET_VIEWS

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    set_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{DRAWING_SETS}.id", ondelete="CASCADE"))
    file_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{DRAWING_SET_FILES}.id", ondelete="CASCADE"))
    ord: Mapped[int] = mapped_column(Integer)
    page_index: Mapped[int] = mapped_column(Integer)
    crop: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True, default=None)
    """Khung `{x, y, w, h}` theo tỉ lệ trang gốc (như `CropRect` của #5); `NULL` = cả trang."""
    source: Mapped[str] = mapped_column(Text)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    kind: Mapped[str] = mapped_column(Text)
    kind_confidence: Mapped[float] = mapped_column(Float, info={"float_reason": _CONFIDENCE})
    title_text: Mapped[str | None] = mapped_column(Text, default=None)
    storey_label: Mapped[str | None] = mapped_column(Text, default=None)
    storey_no: Mapped[int | None] = mapped_column(Integer, default=None)
    storey_confidence: Mapped[float | None] = mapped_column(Float, default=None, info={"float_reason": _CONFIDENCE})
    target_mode: Mapped[str | None] = mapped_column(Text, default=None)
    target_floor_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{FLOORS}.pk", ondelete="SET NULL"), default=None
    )
    new_floor_id: Mapped[str | None] = mapped_column(Text, default=None)
    new_floor_name: Mapped[str | None] = mapped_column(Text, default=None)
    replace_drawing: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    machine: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True, default=None)
    upload_id: Mapped[str | None] = mapped_column(Text, ForeignKey(f"{UPLOADS}.id", ondelete="SET NULL"), default=None)

    __table_args__ = (
        CheckConstraint(one_of("source", VIEW_SOURCES), name="source"),
        CheckConstraint(one_of("kind", VIEW_KINDS), name="kind"),
        CheckConstraint(f"target_mode IS NULL OR {one_of('target_mode', TARGET_MODES)}", name="target_mode"),
        CheckConstraint("ord >= 0", name="ord_non_negative"),
        CheckConstraint("page_index >= 0", name="page_index_non_negative"),
        CheckConstraint("kind_confidence BETWEEN 0 AND 1", name="kind_confidence_range"),
        CheckConstraint("storey_confidence IS NULL OR storey_confidence BETWEEN 0 AND 1", name="storey_confidence"),
        Index(f"uq_{DRAWING_SET_VIEWS}_set_id_ord", "set_id", "ord", unique=True),
        # K-32d/K-32e tra ngược "lượt tải này thuộc bộ nào" (`upload_id → drawing_set_views`); chỉ hình đã xác
        # nhận có `upload_id`, nên index một phần. Cũng phục vụ `SET NULL` khi lịch dọn xoá một lượt tải.
        Index(f"ix_{DRAWING_SET_VIEWS}_upload_id", "upload_id", postgresql_where=text("upload_id IS NOT NULL")),
    )
