"""set_building

Revision ID: r20261125_nha_06b
Revises: r20261123_hs_04
Create Date: 2026-11-25

NHA-06b, expand thuần (BE-00 §6.1): `drawing_sets.building` — quyết định của người duyệt trên đề xuất ngôi nhà
(`decisions`, N59 ghi) và bản đề xuất đóng băng lúc xác nhận (`confirmed`, N60 ghi). Cột nullable, không default,
không backfill: NULL = bộ chưa có quyết định nào, N60 xác nhận như trước.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "r20261125_nha_06b"
down_revision: str | None = "r20261123_hs_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "drawing_sets"
COLUMN: Final = "building"


def upgrade() -> None:
    """Thêm cột `JSONB NULL` (chỉ sửa catalog, không viết lại bảng)."""
    op.add_column(TABLE, sa.Column(COLUMN, JSONB(), nullable=True))


def downgrade() -> None:
    """Bỏ cột (mất quyết định của người duyệt và bản đóng băng; tầng đã tạo không đổi)."""
    op.drop_column(TABLE, COLUMN)
