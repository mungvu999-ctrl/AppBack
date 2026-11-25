"""`Operation` và CLI xuất OpenAPI (cổng case, BE-00 §12 bước 8)."""

import json
import re
from pathlib import Path
from typing import Annotated, Final

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field

from apps.api.core.auth import FakeTokenVerifier
from apps.api.core.openapi import Operation, document, main, operation_rows, operations
from apps.api.core.routing import DEFAULT_BODY_LIMIT
from apps.api.core.tests.sample import BIG_BODY_LIMIT, SAMPLE_ROUTERS, build_sample_app, sample_app
from packages.testing.fixtures.clock import FakeClock

__all__ = ["sample_app"]

REAL_OPS: Final = ("files_read_object", "health_live", "health_ready")


class _ShortSide:
    """Nơi khai bí danh `Label` thứ nhất."""

    type Label = Annotated[str, Field(max_length=4)]


class _LongSide:
    """Nơi khai bí danh `Label` thứ hai — cùng tên, khác ràng buộc."""

    type Label = Annotated[str, Field(min_length=2)]


class _Pair(BaseModel):
    """Thân dùng cả hai bí danh cùng tên (NO-236)."""

    short: _ShortSide.Label
    long: _LongSide.Label


def _by_op(app: FastAPI) -> dict[str, Operation]:
    """`Operation` của app theo `operationId`."""
    return {operation.op: operation for operation in operations(app)}


def test_real_app_keeps_the_core_operations_public() -> None:
    """Ba route lõi luôn có và công khai (khối [7] của B0-06); route công khai không bao giờ có idempotency.

    Không ghim tổng số thao tác: mỗi prompt sau thêm route của nó (FIX-029).
    """
    ops = {operation.op: operation for operation in operations()}
    assert set(REAL_OPS) <= set(ops)
    assert all(ops[op].protected is False for op in REAL_OPS)
    assert all(operation.idempotency == "off" for operation in ops.values() if not operation.protected)


def test_operation_rows_have_every_field() -> None:
    """`case_gate` đọc đúng những tên trường này."""
    expected = {
        "op",
        "method",
        "path",
        "protected",
        "versioned",
        "idempotency",
        "body_limit",
        "has_body",
        "has_query",
        "returns_list",
        "has_optional_response_fields",
        "body_mirrors_path",
        "permission_key",
    }
    assert all(set(row) == expected for row in operation_rows())


def test_protected_post_uses_idempotency(sample_app: FastAPI) -> None:
    """POST được bảo vệ, không versioned → idempotency `auto`, trần 1 MiB."""
    ops = _by_op(sample_app)
    assert ops["sample_create_item"].protected is True
    assert ops["sample_create_item"].idempotency == "auto"
    assert ops["sample_create_item"].has_body is True
    assert ops["sample_create_item"].body_limit == DEFAULT_BODY_LIMIT


def test_versioned_route_has_no_idempotency(sample_app: FastAPI) -> None:
    """Route GV dựa vào C09b chứ không dùng bảng idempotency (BE-00 §7)."""
    operation = _by_op(sample_app)["sample_replace_version"]
    assert operation.versioned is True
    assert operation.idempotency == "off"


def test_big_body_route_has_its_own_limit(sample_app: FastAPI) -> None:
    """Trần thân khai qua `route_options` ra đúng trong metadata."""
    operation = _by_op(sample_app)["sample_write_big"]
    assert operation.body_limit == BIG_BODY_LIMIT
    assert operation.idempotency == "off"


def test_body_mirrors_path_only_when_keys_overlap(sample_app: FastAPI) -> None:
    """C21 chỉ áp khi thân mang id trùng nghĩa với id trên đường (W21)."""
    ops = _by_op(sample_app)
    assert ops["sample_create_project_item"].body_mirrors_path is True
    assert ops["sample_create_item"].body_mirrors_path is False


def test_returns_list_and_query_of_cursor_page(sample_app: FastAPI) -> None:
    """`CursorPage` là danh sách (C15) và `page_params` là query (C02)."""
    operation = _by_op(sample_app)["sample_list_page"]
    assert operation.returns_list is True
    assert operation.has_query is True


def test_optional_response_fields(sample_app: FastAPI) -> None:
    """C17 chỉ áp khi response có trường tuỳ chọn ở bất kỳ độ sâu nào."""
    ops = _by_op(sample_app)
    assert ops["sample_read_optional"].has_optional_response_fields is True
    assert ops["sample_create_item"].has_optional_response_fields is False


def test_permission_key_comes_from_dependency(sample_app: FastAPI) -> None:
    """Route có cổng quyền phơi khoá của nó; route không có mang `—`."""
    ops = _by_op(sample_app)
    assert ops["sample_guarded_create"].permission_key == "sample.manage"
    assert ops["sample_create_item"].permission_key == "—"


def test_public_route_is_not_protected(sample_app: FastAPI) -> None:
    """Route của `public_router` báo `protected=False`."""
    assert _by_op(sample_app)["sample_public_read"].protected is False


def test_document_is_sorted_and_ends_with_newline() -> None:
    """Hai lượt xuất chỉ khác nhau đúng chỗ hợp đồng khác (khoá sắp, thụt 2)."""
    text = document()
    assert text.endswith("}\n")
    assert json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n" == text


def test_document_of_sample_app_has_paths(fake_clock: FakeClock, storage_env: None) -> None:
    """`document()` xuất được schema của một app bất kỳ, không riêng app thật."""
    app = build_sample_app(fake_clock, routers=SAMPLE_ROUTERS)
    assert isinstance(app.state.token_verifier, FakeTokenVerifier)
    assert "/api/sample/items" in json.loads(document(app))["paths"]


def test_cli_writes_and_compares(tmp_path: Path) -> None:
    """`--compare` là cổng "hợp đồng không đổi ngoài ý muốn" của bước 8."""
    out = tmp_path / "duoi" / "openapi.json"
    assert main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == document()
    assert main(["--out", str(out), "--compare", str(out)]) == 0


def test_cli_fails_on_drift(tmp_path: Path) -> None:
    """Bản đã commit lệch bản vừa xuất → thoát 1."""
    out = tmp_path / "openapi.json"
    expected = tmp_path / "da-commit.json"
    expected.write_text('{"khac": true}\n', encoding="utf-8")
    assert main(["--out", str(out), "--compare", str(expected)]) == 1


def test_cli_fails_when_compare_file_missing(tmp_path: Path) -> None:
    """Thiếu file để so cũng là hỏng, không phải đạt."""
    out = tmp_path / "openapi.json"
    assert main(["--out", str(out), "--compare", str(tmp_path / "khong-co.json")]) == 1


def test_cli_requires_out() -> None:
    """Thiếu `--out` là lỗi dùng CLI."""
    with pytest.raises(SystemExit):
        main([])


def test_document__every_path_parameter_is_declared() -> None:
    """NO-237: tham số đường chỉ được dependency đọc (`request.path_params`) vẫn có trong `parameters`."""
    schema = json.loads(document())
    missing = [
        f"{method.upper()} {path}: {name}"
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        for name in re.findall(r"{(\w+)}", path)
        if name not in {param["name"] for param in operation.get("parameters", []) if param["in"] == "path"}
    ]
    assert missing == []


def test_document__refuses_two_aliases_with_one_name() -> None:
    """NO-236: hai bí danh cùng tên làm Pydantic đổi **cả hai** component sang tên dài → bước 8 hỏng."""
    app = FastAPI()

    @app.post("/pair")
    async def pair_write(body: _Pair) -> None:
        """Route duy nhất dùng cả hai bí danh."""

    with pytest.raises(ValueError, match="Label"):
        document(app)


def test_document__real_app_has_only_short_component_names() -> None:
    """NO-236: app thật không có component nào mang tên đầy đủ theo module (`apps__…`, `packages__…`)."""
    assert [name for name in json.loads(document())["components"]["schemas"] if re.match(r"[a-z]", name)] == []
