"""Metadata thao tác (`Operation`) và CLI xuất OpenAPI (cổng case, BE-00 §12 bước 8).

`operations()` là **nguồn duy nhất** để `tools/case_gate.py` tính tập case bắt buộc
của mỗi route và để quét route so với bảng hợp đồng. Mọi trường đều suy ra từ chính app
đang chạy (cây `dependant`, `response_model`, `RouteOptions`) chứ không từ bảng khai
tay: prompt sau thêm route là tự có metadata đúng, không ai phải sửa file này.

CLI:

    python -m apps.api.core.openapi --out openapi.json [--compare openapi.json]

JSON in ra đã sắp khoá, thụt 2, có dòng trống cuối — để `git diff` của hai lần xuất
chỉ khác đúng chỗ hợp đồng khác.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, get_args, get_origin

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from pydantic import BaseModel
from pydantic.alias_generators import to_camel

from apps.api.core.app import app_routes, create_app, schema_settings
from apps.api.core.pagination import CursorPage
from apps.api.core.permissions import ANY_ROLE, permission_key_of
from apps.api.core.routing import AppRoute

INDENT: Final = 2
_MODULE_NAME: Final = re.compile(r"[a-z][a-z0-9_]*?__")
"""Component Pydantic đặt tên đầy đủ theo module khi hai lớp/bí danh trùng tên (NO-236)."""
_NONE_TYPE: Final = type(None)

_cached_app: FastAPI | None = None


@dataclass(frozen=True, slots=True)
class Operation:
    """Metadata của một thao tác đã mount, đúng các trường cổng case đòi."""

    op: str
    method: str
    path: str
    protected: bool
    versioned: bool
    idempotency: str
    body_limit: int
    has_body: bool
    has_query: bool
    returns_list: bool
    has_optional_response_fields: bool
    body_mirrors_path: bool
    permission_key: str


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    """Cả cây dependency của một route, kể cả dependency của router (quyền, rate limit)."""
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def _body_keys(route: AppRoute) -> set[str]:
    """Khoá camelCase mà thân của route khai (alias của model, hay tên tham số nhúng)."""
    keys: set[str] = set()
    for field in route.dependant.body_params:
        annotation = field.field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            keys |= {sub.alias or to_camel(name) for name, sub in annotation.model_fields.items()}
        else:
            keys.add(field.alias or to_camel(field.name))
    return keys


def _nested_models(annotation: Any) -> Iterator[type[BaseModel]]:
    """Mọi `BaseModel` xuất hiện trong một chú thích kiểu (list, Optional, dict, lồng nhau)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in get_args(annotation):
        yield from _nested_models(arg)


def _admits_none(annotation: Any) -> bool:
    """Kiểu có nhận `None` không — trên dây nghĩa là khoá có thể vắng (W2)."""
    return _NONE_TYPE in get_args(annotation)


def _has_optional_fields(model: type[BaseModel], seen: frozenset[type[BaseModel]]) -> bool:
    """Có trường không bắt buộc ở **bất kỳ độ sâu** nào của model response (C17)."""
    if model in seen:
        return False
    deeper = seen | {model}
    for field in model.model_fields.values():
        if not field.is_required() or _admits_none(field.annotation):
            return True
        if any(_has_optional_fields(nested, deeper) for nested in _nested_models(field.annotation)):
            return True
    return False


def _response_flags(route: AppRoute) -> tuple[bool, bool]:
    """(`returns_list`, `has_optional_response_fields`) của một route."""
    model = route.response_model
    if model is None:
        return False, False
    returns_list = get_origin(model) is list or (isinstance(model, type) and issubclass(model, CursorPage))
    optional = any(_has_optional_fields(nested, frozenset()) for nested in _nested_models(model))
    return returns_list, optional


def _permission_key(route: AppRoute) -> str:
    """Khoá quyền lấy từ cây dependant; route không có cổng quyền nào → `—` (BE-00 §5)."""
    for dependant in _walk(route.dependant):
        key = permission_key_of(dependant.call)
        if key is not None:
            return key
    return ANY_ROLE


def operation_of(route: AppRoute) -> Operation:
    """Metadata của một route đã mount."""
    returns_list, optional_fields = _response_flags(route)
    path_keys = {to_camel(name) for name in route.param_convertors}
    return Operation(
        op=route.name,
        method=route.wire_method,
        path=route.path_format,
        protected=route.protected,
        versioned=route.options.versioned,
        idempotency="auto" if route.uses_idempotency else "off",
        body_limit=route.options.body_limit,
        has_body=bool(route.dependant.body_params),
        has_query=any(dependant.query_params for dependant in _walk(route.dependant)),
        returns_list=returns_list,
        has_optional_response_fields=optional_fields,
        body_mirrors_path=bool(_body_keys(route) & path_keys),
        permission_key=_permission_key(route),
    )


def real_app() -> FastAPI:
    """App thật, dựng một lần mỗi tiến trình, **chỉ để đọc schema** (cổng case, CLI bước 8).

    Nơi duy nhất được dùng `schema_settings()`: hai công cụ này chạy trong container
    verify, không có biến môi trường thật và không mở kết nối nào.
    """
    global _cached_app  # bộ nhớ đệm một lần mỗi tiến trình, đúng như `extensions.discover`
    if _cached_app is None:
        _cached_app = create_app(schema_settings())
    return _cached_app


def operations(app: FastAPI | None = None) -> list[Operation]:
    """Metadata của mọi thao tác đã mount, sắp theo `operationId`."""
    return sorted((operation_of(route) for route in app_routes(app or real_app())), key=lambda item: item.op)


def _check_component_names(schema: dict[str, Any]) -> None:
    """Hai bí danh/model **cùng tên** ở hai module → Pydantic đổi CẢ HAI component sang tên dài
    `apps__api__…` (đổi luôn schema của prompt khác); từ chối để bước 8 hỏng trên mọi nhánh (NO-236).

    Tên dài luôn mở đầu bằng đường module chữ thường (`apps__…`); tên ngắn là CapWords, kể cả
    generic lồng (`CursorPage_list_ItemOut__` không khớp).
    """
    clashes = sorted(name for name in schema.get("components", {}).get("schemas", {}) if _MODULE_NAME.match(name))
    if clashes:
        raise ValueError(f"component OpenAPI trùng tên giữa các module, đổi tên một bên: {', '.join(clashes)}")


def document(app: FastAPI | None = None) -> str:
    """OpenAPI đã chuẩn hoá: khoá sắp, thụt 2, có dòng trống cuối; tên component trùng → `ValueError`."""
    schema = (app or real_app()).openapi()
    _check_component_names(schema)
    return json.dumps(schema, indent=INDENT, sort_keys=True, ensure_ascii=False) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Tham số của CLI: `--out` bắt buộc, `--compare` tuỳ chọn."""
    parser = argparse.ArgumentParser(prog="python -m apps.api.core.openapi")
    parser.add_argument("--out", required=True, help="file JSON để ghi")
    parser.add_argument("--compare", help="file đã commit để so; khác thì thoát khác 0")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Xuất OpenAPI; `--compare` biến bước 8 thành cổng "hợp đồng không đổi ngoài ý muốn"."""
    args = build_parser().parse_args(argv)
    text = document()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"openapi: đã ghi {out} ({len(text)} byte)")  # noqa: T201 — CLI của cổng in kết quả
    if args.compare:
        expected = Path(args.compare)
        if not expected.is_file():
            print(f"openapi: thiếu {expected} để so")  # noqa: T201 — như trên
            return 1
        if expected.read_text(encoding="utf-8") != text:
            print(f"openapi: {expected} lệch với bản vừa xuất")  # noqa: T201 — như trên
            return 1
    return 0


def operation_rows() -> list[dict[str, object]]:
    """Bảng `Operation` dạng dict — báo cáo nghiệm thu và test in ra từ đây."""
    return [asdict(operation) for operation in operations()]


if __name__ == "__main__":
    sys.exit(main())
