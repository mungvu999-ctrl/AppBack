"""floor_is_section

Revision ID: r20261121_hs_03
Revises: r20261103_bench3d_07
Create Date: 2026-11-21

HS-03, expand thuần (BE-00 §6.1): `floors.is_section` — bản vẽ của dòng tầng này là mặt cắt, không phải mặt
bằng một tầng (người dùng đánh dấu ở trình quản lý tầng). NOT NULL + `server_default false`: dòng cũ và mã cũ
không ghi cột này đều nhận `false`, không backfill.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "r20261121_hs_03"
down_revision: str | None = "r20261103_bench3d_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "floors"
COLUMN: Final = "is_section"


def upgrade() -> None:
    """Thêm cột NOT NULL có `server_default false` (dòng cũ nhận `false` ngay)."""
    op.add_column(TABLE, sa.Column(COLUMN, sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    """Bỏ cột (mất dấu "mặt cắt", dòng đó lại được coi là mặt bằng một tầng; dữ liệu khác giữ nguyên)."""
    op.drop_column(TABLE, COLUMN)
