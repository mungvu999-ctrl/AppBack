"""Quét route của app **thật** và kiểm mã lỗi duy nhất toàn repo (BE-00 §3.2, §4).

Hai bộ kiểm này tự áp cho mọi prompt sau: thêm route sai đường, sai `operationId`,
sai khoá quyền, hay khai trùng mã lỗi là hỏng ngay ở đây, không phải đợi H1.
"""

import importlib
import pkgutil
import re
from pathlib import Path
from typing import Final

import pytest

from apps.api.core.app import app_routes, discover_routers
from apps.api.core.openapi import Operation, operations, real_app
from apps.api.core.routing import AppRoute
from packages.core.errors import ERRORS
from tools.charter import BindRow, load_bind_rows

REPO_ROOT: Final = Path(__file__).resolve().parents[4]

OPERATION_ID_RE: Final = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
ERROR_CODE_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{2,63}")

CORE_PUBLIC_OPS: Final = frozenset({"health_live", "health_ready", "files_read_object"})
PUBLIC_CASE_TYPES: Final = frozenset({"C", "R", "P", "B", "S"})

# Module không nhập khi quét mã lỗi: có tác dụng phụ lúc nhập, hoặc không thuộc API.
SKIP_PARTS: Final = frozenset({"tests", "migrations", "celery_main"})
SKIP_SUFFIXES: Final = ("__main__", "cli")
SCAN_PACKAGES: Final = ("packages", "apps.api", "apps.worker")


@pytest.fixture(scope="module")
def bind_rows() -> list[BindRow]:
    """Các dòng hợp đồng thật."""
    return load_bind_rows(REPO_ROOT)


@pytest.fixture(scope="module")
def real_operations() -> list[Operation]:
    """Metadata thao tác của app thật."""
    return operations()


def test_method_and_path_are_unique(real_operations: list[Operation]) -> None:
    """Hai route cùng method và đường thì một trong hai không bao giờ được gọi."""
    pairs = [(operation.method, operation.path) for operation in real_operations]
    assert len(pairs) == len(set(pairs))


def test_operation_ids_are_snake_case_and_unique(real_operations: list[Operation]) -> None:
    """BE-00 §3.2: snake_case, **không** chứa `__` (tên test dùng `__` làm dấu tách case)."""
    ids = [operation.op for operation in real_operations]
    assert len(ids) == len(set(ids))
    for op in ids:
        assert OPERATION_ID_RE.fullmatch(op), op
        assert "__" not in op


def test_paths_match_bind_rows(real_operations: list[Operation], bind_rows: list[BindRow]) -> None:
    """K06: đường và method của thao tác có trong bảng hợp đồng phải đúng dòng đó."""
    rows = {row.operation_id: row for row in bind_rows}
    for operation in real_operations:
        row = rows.get(operation.op)
        if row is None:
            continue
        assert (operation.method, operation.path) == (row.method, row.path), operation.op


def test_permission_keys_match_bind_rows(real_operations: list[Operation], bind_rows: list[BindRow]) -> None:
    """BE-00 §5: `Operation.permission_key` phải khớp `lock` ở `cases.toml`."""
    rows = {row.operation_id: row for row in bind_rows}
    for operation in real_operations:
        row = rows.get(operation.op)
        if row is None:
            continue
        assert operation.permission_key == row.lock, operation.op


def test_public_routes_are_allowed(real_operations: list[Operation], bind_rows: list[BindRow]) -> None:
    """Mặc định mọi route được bảo vệ; công khai chỉ cho Loại C, R, P, B, S và ba route lõi."""
    allowed = CORE_PUBLIC_OPS | {row.operation_id for row in bind_rows if row.case_type in PUBLIC_CASE_TYPES}
    public = {operation.op for operation in real_operations if not operation.protected}
    assert public <= allowed, sorted(public - allowed)


def test_every_route_is_an_app_route() -> None:
    """Không `mount`, không route thường: mọi route đi qua `AppRoute` (BE-00 §2)."""
    app = real_app()
    business = app_routes(app)
    schema_paths = {app.openapi_url}
    for route in app.routes:
        if route in business:
            continue
        assert getattr(route, "path", None) in schema_paths, route
    assert business, "app thật phải có ít nhất một route"
    assert all(isinstance(route, AppRoute) for route in business)


def test_every_router_module_contributes_a_route() -> None:
    """Mỗi `apps/api/<module>/router.py` phải góp ≥ 1 route."""
    for name, router in discover_routers():
        assert router.routes, name


def _scanned_modules() -> list[str]:
    """Mọi module sản phẩm có thể khai mã lỗi (BE-00 §4)."""
    names: list[str] = []
    for package in SCAN_PACKAGES:
        root = importlib.import_module(package)
        for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
            parts = info.name.split(".")
            if SKIP_PARTS & set(parts) or info.name.endswith(SKIP_SUFFIXES):
                continue
            names.append(info.name)
    return sorted(names)


def test_error_codes_are_unique_and_well_formed() -> None:
    """Nhập hết mã sản phẩm rồi soát sổ mã: đúng mẫu và không trùng (BE-00 §4)."""
    for name in _scanned_modules():
        importlib.import_module(name)
    codes = [code.code for code in ERRORS.all()]
    assert codes, "sổ mã lỗi không được rỗng"
    assert len(codes) == len(set(codes))
    for code in codes:
        assert ERROR_CODE_RE.fullmatch(code), code
