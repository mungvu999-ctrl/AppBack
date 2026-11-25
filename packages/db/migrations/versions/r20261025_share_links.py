"""share_links

Revision ID: r20261025_share_links
Revises: r20261024_fe3d_roof
Create Date: 2026-10-25

Expand thuần (BE-00 §6.1): bảng mới `share_links` cho hợp đồng #47-#49 (liên kết chia sẻ có hạn,
thu hồi được). Không đụng bảng cũ nên không mang `# contract:`.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "r20261025_share_links"
down_revision: str | None = "r20261024_fe3d_roof"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "share_links"


def upgrade() -> None:
    """Tạo bảng `share_links` + chỉ mục theo dự án + token duy nhất."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("permission", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewpoint_code", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("permission IN ('view', 'comment')", name=op.f("ck_share_links_permission_values")),
        sa.UniqueConstraint("token", name=op.f("uq_share_links_token")),
    )
    op.create_index(op.f("ix_share_links_project_id"), TABLE, ["project_id"])


def downgrade() -> None:
    """Bỏ bảng (mất mọi liên kết chia sẻ; người nhận gặp 404)."""
    op.drop_table(TABLE)
