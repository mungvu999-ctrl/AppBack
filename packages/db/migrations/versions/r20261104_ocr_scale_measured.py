# contract: nới CHECK floor_documents.scale_source thêm measured
"""ocr_scale_measured

Revision ID: r20261104_ocr_scale_measured
Revises: r20261103_bench3d_07
Create Date: 2026-11-04

Tỉ lệ đọc từ ≥ 3 số đo OCR đồng thuận thành hạng riêng `measured`: không còn `scaleStatus: 'unresolved'`. Postgres
không sửa được biểu thức CHECK tại chỗ nên bỏ rồi dựng lại với tập rộng hơn (mọi dòng cũ vẫn đạt). Hằng số chép tay từ
`packages/db/models/spatial.py`, không nhập module model. Không backfill: tầng cũ thành `measured` khi chạy lại.
"""

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "r20261104_ocr_scale_measured"
down_revision: str | None = "r20261103_bench3d_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "floor_documents"
CHECK_NAME: Final = "ck_floor_documents_scale_source"
_OLD: Final = "scale_source IN ('human', 'pipeline', 'project_default', 'none')"
_NEW: Final = "scale_source IN ('human', 'measured', 'pipeline', 'project_default', 'none')"


def upgrade() -> None:
    """Thay CHECK `scale_source` bằng bản có thêm `'measured'`."""
    op.drop_constraint(op.f(CHECK_NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(CHECK_NAME), TABLE, _NEW)


def downgrade() -> None:
    """Hạ `measured` về `pipeline` (giữ giá trị tỉ lệ, chỉ hạ hạng) rồi dựng lại CHECK cũ."""
    op.drop_constraint(op.f(CHECK_NAME), TABLE, type_="check")
    op.execute("UPDATE floor_documents SET scale_source = 'pipeline' WHERE scale_source = 'measured'")
    op.create_check_constraint(op.f(CHECK_NAME), TABLE, _OLD)
