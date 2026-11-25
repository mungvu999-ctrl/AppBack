"""notifications

Revision ID: r20260928_b4_02
Revises: r20260928_b3_05
Create Date: 2026-09-28

Bảng `notifications` (B4-02 [5], hợp đồng #19-#22): expand thuần, một bảng mới với FK `users` và FK `projects`
`ON DELETE CASCADE`. Hằng số chép tay từ `packages/db/models/notifications.py`, không nhập module model.
Tên constraint bọc `op.f(...)` để quy ước của `Base.metadata` không ghép tiền tố lần hai.

Luật expand/contract: BE-00 §6.1 (`tools/lint_migrations.py` chặn drop/rename/NOT NULL).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "r20260928_b4_02"
down_revision: str | None = "r20260928_b3_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "notifications"
USERS: Final = "users"
PROJECTS: Final = "projects"

KINDS: Final = "'aiCompleted', 'violationFound', 'projectInvite'"
PLACES: Final = "'walls', 'objects', 'dimensions', 'grids', 'rooms', 'thickness', 'floors', 'rules', 'projectSettings'"
FLOOR_PLACES: Final = "'dimensions', 'grids', 'objects', 'rooms', 'thickness', 'walls'"


def upgrade() -> None:
    """Expand: bảng mới, hai index (danh sách theo người; quét bù chưa phát) và bốn CHECK."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("place", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("project_name", sa.Text(), nullable=False),
        sa.Column("floor_level_id", sa.Text(), nullable=True),
        sa.Column("object_label", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("stream_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"kind IN ({KINDS})", name=op.f(f"ck_{TABLE}_kind")),
        sa.CheckConstraint(f"place IN ({PLACES})", name=op.f(f"ck_{TABLE}_place")),
        sa.CheckConstraint(
            f"place NOT IN ({FLOOR_PLACES}) OR floor_level_id IS NOT NULL", name=op.f(f"ck_{TABLE}_floor_place")
        ),
        sa.CheckConstraint(
            "kind <> 'projectInvite' OR place = 'projectSettings'", name=op.f(f"ck_{TABLE}_invite_place")
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{USERS}.id"], name=op.f(f"fk_{TABLE}_user_id_{USERS}")),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{PROJECTS}.id"], name=op.f(f"fk_{TABLE}_project_id_{PROJECTS}"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.UniqueConstraint("dedupe_key", name=op.f(f"uq_{TABLE}_dedupe_key")),
    )
    op.create_index(
        f"ix_{TABLE}_user_id_created_at_id", TABLE, ["user_id", sa.text("created_at DESC"), sa.text("id DESC")]
    )
    op.create_index(
        f"ix_{TABLE}_created_at_unsent", TABLE, ["created_at"], postgresql_where=sa.text("stream_id IS NULL")
    )


def downgrade() -> None:
    """Bỏ bảng (kéo theo hai index); không bảng nào khác trỏ tới nó."""
    op.drop_table(TABLE)
