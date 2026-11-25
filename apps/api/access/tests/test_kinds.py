"""Đối chiếu `ActivityKind`/`KIND_OPERATIONS` với dòng hợp đồng thật có `logged = true` (B1-02.md [8]).

Gốc repo tính từ `__file__`, không đường tuyệt đối (K nhắc lại BE-01 [9]) — cùng khuôn
`apps/api/core/tests/test_routes.py`.
"""

import logging
import re
from pathlib import Path
from typing import Final

import pytest

from apps.api.access.kinds import KIND_OPERATIONS, ActivityKind
from tools.charter import BindRow, load_bind_rows

REPO_ROOT: Final = Path(__file__).resolve().parents[4]

_KIND_RE: Final = re.compile(r"^[a-z]+\.[a-z_]+$")

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def logged_op_ids() -> set[str]:
    """operationId của mọi dòng BE-BIND có cột Nhật ký `có`."""
    rows: list[BindRow] = load_bind_rows(REPO_ROOT)
    return {row.operation_id for row in rows if row.logged}


def test_every_activity_kind_value_matches_pattern() -> None:
    """Mọi giá trị `kind` khớp `^[a-z]+\\.[a-z_]+$` (ràng buộc CHECK của bảng)."""
    for kind in ActivityKind:
        assert _KIND_RE.match(kind.value), kind


def test_kind_operations_covers_every_activity_kind_member() -> None:
    """`KIND_OPERATIONS` phủ đủ mọi thành viên `ActivityKind`, không thiếu."""
    assert set(KIND_OPERATIONS) == set(ActivityKind)


def test_no_two_kinds_point_to_the_same_operation() -> None:
    """Không hai hằng trỏ cùng một `op`."""
    ops = list(KIND_OPERATIONS.values())
    assert len(ops) == len(set(ops))


def test_every_logged_bind_row_has_exactly_one_kind(logged_op_ids: set[str]) -> None:
    """Mỗi `op` có Nhật ký `có` có đúng một hằng `kind` trỏ tới."""
    mapped_ops = list(KIND_OPERATIONS.values())
    for op_id in logged_op_ids:
        assert mapped_ops.count(op_id) == 1, op_id


def test_no_kind_points_to_an_unlogged_or_unknown_operation(logged_op_ids: set[str]) -> None:
    """Không hằng nào trỏ tới `op` không có, hoặc có mà Nhật ký không `có`."""
    for kind, op_id in KIND_OPERATIONS.items():
        assert op_id in logged_op_ids, (kind, op_id)


def test_kind_operation_table_has_one_row_per_logged_bind_row(logged_op_ids: set[str]) -> None:
    """Dựng bảng `kind` ↔ `op` (27 dòng, sắp theo `op`) và in bằng `logging`."""
    table = sorted(((op_id, kind.value) for kind, op_id in KIND_OPERATIONS.items()), key=lambda row: row[0])
    logger.info("kind <-> op (%d dòng):", len(table))
    for op_id, kind_value in table:
        logger.info("  %-30s %s", op_id, kind_value)
    assert len(table) == len(logged_op_ids)
