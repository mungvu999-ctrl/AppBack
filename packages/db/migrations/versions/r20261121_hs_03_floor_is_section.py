"""floor_is_section

Revision ID: r20261121_hs_03
Revises: r20261103_bench3d_07
Create Date: 2026-11-21

HS-03, expand thuần (BE-00 §6.1): `floors.is_section` — bản vẽ của dòng tầng này là mặt cắt, không phải mặt
bằng một tầng (người dùng đánh dấu ở trình quản lý tầng). NOT NULL + `server_default false`: dòng cũ và mã cũ
không ghi cột này đều nhận `false`, không backfill.

Idempotent (L4-N02): stack thử f14 đã thêm cột bằng SQL tay trước khi có revision này; `upgrade` bỏ qua khi cột
đã có (kiểm bằng `sa.inspect`, AppBack không chạy Alembic offline — `env.py`). Cột có sẵn mà khác kiểu/ràng buộc
thì `migrate_check` (so metadata) bắt, không phải revision này.
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
    """Thêm cột NOT NULL có `server_default false` (dòng cũ nhận `false` ngay); cột đã có thì không làm gì."""
    if COLUMN in {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}:
        return
    op.add_column(TABLE, sa.Column(COLUMN, sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    """Bỏ cột (mất dấu "mặt cắt", dòng đó lại được coi là mặt bằng một tầng; dữ liệu khác giữ nguyên)."""
    op.drop_column(TABLE, COLUMN)
