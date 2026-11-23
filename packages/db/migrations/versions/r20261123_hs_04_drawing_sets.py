"""drawing_sets

Revision ID: r20261123_hs_04
Revises: r20261121_hs_03
Create Date: 2026-11-23

K-32a (lát cắt 2, "Tải cả bộ hồ sơ"), expand thuần (BE-00 §6.1): bốn bảng mới `drawing_sets`, `drawing_set_files`,
`drawing_set_file_chunks`, `drawing_set_views` — lớp bộ hồ sơ **phía trên** luồng tải từng tầng. Chỉ
`CREATE TABLE`/`CREATE INDEX`, không đụng bảng cũ nên không mang `# contract:`.

Idempotent (L4-N02): bảng đã có (stack thử dựng bằng SQL tay) thì bỏ qua bảng đó cùng index của nó (kiểm bằng
`sa.inspect`, AppBack không chạy Alembic offline — `env.py`). Bảng có sẵn mà khác cột/ràng buộc thì `migrate_check`
(so metadata) bắt, không phải revision này. Tập giá trị CHECK chép cứng (không nhập model): revision là ảnh chụp
DDL lúc viết, model đổi sau này phải đi revision mới, không được âm thầm đổi revision cũ.
"""

from collections.abc import Callable, Sequence
from typing import Any, Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "r20261123_hs_04"
down_revision: str | None = "r20261121_hs_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETS: Final = "drawing_sets"
FILES: Final = "drawing_set_files"
CHUNKS: Final = "drawing_set_file_chunks"
VIEWS: Final = "drawing_set_views"


def _timestamps() -> list[sa.Column[Any]]:
    """`created_at`/`updated_at` của `TimestampMixin` (timestamptz, mặc định `now()`)."""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _check(table: str, sql: str, name: str) -> sa.CheckConstraint:
    """CHECK đặt tên theo quy ước `ck_<bảng>_<tên>` của `NAMING_CONVENTION`."""
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{name}"))


def _create_sets() -> None:
    """`drawing_sets` + index danh sách N58 (mới trước, chưa xoá mềm)."""
    op.create_table(
        SETS,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("progress_percent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("last_writer_id", sa.Text(), nullable=True),
        sa.Column("last_body_sha256", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _check(SETS, "status IN ('receiving', 'analyzing', 'review', 'confirmed', 'failed')", "status"),
        _check(SETS, "progress_percent BETWEEN 0 AND 100", "progress_percent_range"),
        _check(SETS, "revision >= 0", "revision_non_negative"),
        _check(SETS, "name IS NULL OR char_length(name) BETWEEN 1 AND 120", "name_length"),
        _check(SETS, "last_body_sha256 IS NULL OR last_body_sha256 ~ '^[0-9a-f]{64}$'", "last_body_sha256_format"),
    )
    op.create_index(
        op.f(f"ix_{SETS}_project_id_created_at_id"),
        SETS,
        ["project_id", sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_files() -> None:
    """`drawing_set_files` + UNIQUE `(set_id, ord)`."""
    op.create_table(
        FILES,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("set_id", sa.Text(), sa.ForeignKey(f"{SETS}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_type", sa.Text(), nullable=False),
        sa.Column("sniffed_kind", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rejected_code", sa.Text(), nullable=True),
        sa.Column("original_key", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        *_timestamps(),
        _check(FILES, "status IN ('receiving', 'complete', 'rejected')", "status"),
        _check(FILES, "ord >= 0", "ord_non_negative"),
        _check(FILES, "declared_size_bytes > 0", "declared_size_bytes_positive"),
        _check(FILES, "chunk_count > 0", "chunk_count_positive"),
        _check(FILES, "page_count IS NULL OR page_count > 0", "page_count_positive"),
        _check(FILES, "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", "content_sha256_format"),
    )
    op.create_index(op.f(f"uq_{FILES}_set_id_ord"), FILES, ["set_id", "ord"], unique=True)


def _create_chunks() -> None:
    """`drawing_set_file_chunks`, PK `(file_id, chunk_index)` như `upload_chunks`."""
    op.create_table(
        CHUNKS,
        sa.Column("file_id", sa.Text(), sa.ForeignKey(f"{FILES}.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chunk_index", sa.Integer(), primary_key=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        *_timestamps(),
        _check(CHUNKS, "chunk_index >= 0", "chunk_index_non_negative"),
        _check(CHUNKS, "size_bytes > 0", "size_bytes_positive"),
        _check(CHUNKS, "sha256 ~ '^[0-9a-f]{64}$'", "sha256_format"),
    )


def _create_views() -> None:
    """`drawing_set_views` + UNIQUE `(set_id, ord)` + index một phần theo `upload_id`."""
    op.create_table(
        VIEWS,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("set_id", sa.Text(), sa.ForeignKey(f"{SETS}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Text(), sa.ForeignKey(f"{FILES}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("crop", JSONB(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("excluded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("kind_confidence", sa.Float(), nullable=False),
        sa.Column("title_text", sa.Text(), nullable=True),
        sa.Column("storey_label", sa.Text(), nullable=True),
        sa.Column("storey_no", sa.Integer(), nullable=True),
        sa.Column("storey_confidence", sa.Float(), nullable=True),
        sa.Column("target_mode", sa.Text(), nullable=True),
        sa.Column("target_floor_pk", sa.BigInteger(), sa.ForeignKey("floors.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("new_floor_id", sa.Text(), nullable=True),
        sa.Column("new_floor_name", sa.Text(), nullable=True),
        sa.Column("replace_drawing", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("machine", JSONB(), nullable=True),
        sa.Column("upload_id", sa.Text(), sa.ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True),
        *_timestamps(),
        _check(VIEWS, "source IN ('ai', 'user')", "source"),
        _check(VIEWS, "kind IN ('plan', 'section', 'elevation', 'roof', 'site', 'detail', 'other')", "kind"),
        _check(VIEWS, "target_mode IS NULL OR target_mode IN ('floor', 'newFloor', 'section')", "target_mode"),
        _check(VIEWS, "ord >= 0", "ord_non_negative"),
        _check(VIEWS, "page_index >= 0", "page_index_non_negative"),
        _check(VIEWS, "kind_confidence BETWEEN 0 AND 1", "kind_confidence_range"),
        _check(VIEWS, "storey_confidence IS NULL OR storey_confidence BETWEEN 0 AND 1", "storey_confidence"),
    )
    op.create_index(op.f(f"uq_{VIEWS}_set_id_ord"), VIEWS, ["set_id", "ord"], unique=True)
    op.create_index(
        op.f(f"ix_{VIEWS}_upload_id"), VIEWS, ["upload_id"], postgresql_where=sa.text("upload_id IS NOT NULL")
    )


_CREATORS: Final[tuple[tuple[str, Callable[[], None]], ...]] = (
    (SETS, _create_sets),
    (FILES, _create_files),
    (CHUNKS, _create_chunks),
    (VIEWS, _create_views),
)
"""Thứ tự cha trước con (FK); `downgrade` đi ngược lại."""


def upgrade() -> None:
    """Tạo bốn bảng theo thứ tự FK; bảng đã có thì bỏ qua (cùng index của nó)."""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table, create in _CREATORS:
        if table not in existing:
            create()


def downgrade() -> None:
    """Bỏ bốn bảng, con trước cha (mất mọi bộ hồ sơ; tầng và lượt tải đã tạo từ bộ giữ nguyên)."""
    for table, _ in reversed(_CREATORS):
        op.drop_table(table)
