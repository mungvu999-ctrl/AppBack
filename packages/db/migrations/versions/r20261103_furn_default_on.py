"""project_detect_furniture_default_on

Revision ID: r20261103_furn_default_on
Revises: r20261103_bench3d_07
Create Date: 2026-11-03

BENCH3D-FURN-ON: chủ dự án bật lại nhận diện đồ đạc làm mặc định (03/11/2026). Chỉ đổi mặc định
phía server của `project_settings.detect_furniture` sang `true`; dòng đã lưu giữ nguyên lựa chọn.
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "r20261103_furn_default_on"
down_revision: str | None = "r20261103_bench3d_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "project_settings"


def upgrade() -> None:
    """Dòng tạo mới nhận `true` khi không ghi rõ."""
    op.alter_column(TABLE, "detect_furniture", server_default=sa.true())


def downgrade() -> None:
    """Trả mặc định `false` như QT-FURN-TOGGLE."""
    op.alter_column(TABLE, "detect_furniture", server_default=sa.false())
