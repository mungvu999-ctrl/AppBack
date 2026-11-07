"""model_exports

Revision ID: r20261107_m1_01
Revises: r20261103_bench3d_07
Create Date: 2026-11-07

Expand thuần (BE-00 §6.1): bảng mới `model_exports` cho M1-01 (N49-N51, xuất GLB phía máy chủ).
Không đụng bảng cũ nên không mang `# contract:`.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r20261107_m1_01"
down_revision: str | None = "r20261103_bench3d_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "model_exports"


def upgrade() -> None:
    """Tạo bảng `model_exports` + chỉ mục theo dự án + chặn trùng dấu vân đang sống."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("snapshot_sha", sa.Text(), nullable=False),
        sa.Column("snapshot_revisions", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("pack_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("quality", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("report_summary", postgresql.JSONB(), nullable=True),
        sa.Column("glb_bytes", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'superseded')",
            name=op.f("ck_model_exports_status_values"),
        ),
        sa.CheckConstraint(
            "quality IS NULL OR quality IN ('ready', 'degraded', 'blocked')",
            name=op.f("ck_model_exports_quality_values"),
        ),
    )
    op.create_index(op.f("ix_model_exports_project_id_created_at"), TABLE, ["project_id", "created_at"])
    op.create_index(
        op.f("uq_model_exports_fingerprint"),
        TABLE,
        ["project_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'done')"),
    )


def downgrade() -> None:
    """Bỏ bảng (mất mọi lượt xuất; tệp trong kho object thành rác của dự án)."""
    op.drop_table(TABLE)
