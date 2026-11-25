"""rule_configs

Revision ID: r20260928_b3_05
Revises: r20260928_b2_07
Create Date: 2026-09-28

Bảng `rule_configs` (B3-05 [5], hợp đồng N21/N22): expand thuần, một bảng mới với một FK
`ON DELETE CASCADE` về `projects`. Hằng số chép tay từ `packages/db/models/rules.py`, không nhập module model.
Tên constraint bọc `op.f(...)` để quy ước của `Base.metadata` không ghép tiền tố lần hai.

Luật expand/contract: BE-00 §6.1 (`tools/lint_migrations.py` chặn drop/rename/NOT NULL).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r20260928_b3_05"
down_revision: str | None = "r20260928_b2_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_CONFIGS: Final = "rule_configs"
PROJECTS: Final = "projects"


def upgrade() -> None:
    """Expand: bảng mới; `last_writer_id`, `last_body_sha256` NULL được (dòng chèn sẵn chưa có người ghi)."""
    op.create_table(
        RULE_CONFIGS,
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("overrides", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("last_writer_id", sa.Text(), nullable=True),
        sa.Column("last_body_sha256", postgresql.CHAR(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision >= 0", name=op.f(f"ck_{RULE_CONFIGS}_revision_min")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            [f"{PROJECTS}.id"],
            name=op.f(f"fk_{RULE_CONFIGS}_project_id_{PROJECTS}"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=f"pk_{RULE_CONFIGS}"),
    )


def downgrade() -> None:
    """Bỏ bảng; không bảng nào khác trỏ tới nó."""
    op.drop_table(RULE_CONFIGS)
