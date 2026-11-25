"""`packages/core/case_names.py`: tách tên test case theo cổng case (NO-337, chuyển từ `test_case_gate.py`)."""

import pytest

from packages.core.case_names import split_case_test_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test_x_create__C01", ("x_create", "C01", "", False)),
        ("test_x_create__C09b_missing", ("x_create", "C09b", "_missing", False)),
        ("test_x_create__C15[3 tầng]", ("x_create", "C15", "[3 tầng]", False)),
        ("test_common__C04[x_create]", ("x_create", "C04", "", True)),
        ("test_common__C04_x", ("common", "C04", "_x", False)),
        ("test_x_create_is_public", None),
        ("test_x_create__c01", None),
    ],
)
def test_tách_tên_test_case(name: str, expected: tuple[str, str, str, bool] | None) -> None:
    """Hàm công khai mà cổng case và bộ ghi golden dùng chung: dạng chung xét trước dạng riêng, tên lạ → `None`."""
    assert split_case_test_name(name) == expected
