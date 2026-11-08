"""floor_change_log_entity_type_suggestion

Revision ID: r20261108_m2_ql
Revises: r20261107_m1_01
Create Date: 2026-11-08
# contract: CHECK entity_type phải thay (drop + create) để nhận thêm 'suggestion'

Lớp đề xuất M2 lưu trong JSONB `floor_documents.document` nên không cần cột mới, nhưng đổi `state`/`override` của
một đề xuất phải để lại dòng nhật ký `entity_type = 'suggestion'`, không thì lượt ghi cầm bản cũ không nhận 409 và đè
lựa chọn của người kia (HOP-DONG-DU-LIEU-LOP-DE-XUAT §4). Postgres không sửa được biểu thức CHECK tại chỗ nên phải bỏ
rồi dựng lại với tập rộng hơn (mọi dòng cũ vẫn đạt), như `r20261024_fe3d_roof`.
"""

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "r20261108_m2_ql"
down_revision: str | None = "r20261107_m1_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE: Final = "floor_change_log"
CHECK_NAME: Final = "ck_floor_change_log_entity_type"
_OLD: Final = "entity_type IN ('vertex', 'wall', 'door', 'window', 'furniture', 'room', 'dimension', 'roof')"
_NEW: Final = (
    "entity_type IN ('vertex', 'wall', 'door', 'window', 'furniture', 'room', 'dimension', 'roof', 'suggestion')"
)


def upgrade() -> None:
    """Thay CHECK `entity_type` bằng bản có thêm `'suggestion'`."""
    op.drop_constraint(op.f(CHECK_NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(CHECK_NAME), TABLE, _NEW)


def downgrade() -> None:
    """Xoá dòng nhật ký đề xuất (mất lịch sử trạng thái đề xuất; tài liệu giữ nguyên) rồi dựng lại CHECK cũ."""
    op.execute("DELETE FROM floor_change_log WHERE entity_type = 'suggestion'")
    op.drop_constraint(op.f(CHECK_NAME), TABLE, type_="check")
    op.create_check_constraint(op.f(CHECK_NAME), TABLE, _OLD)
