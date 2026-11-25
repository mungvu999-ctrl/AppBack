"""Đọc bảng thao tác hợp đồng và `changes/` — nguồn dữ liệu dùng chung của các cổng.

Một thao tác hợp đồng (`BindRow`) ghép từ hai nơi, không có bảng viết tay thứ ba:

- `docs/contracts/openapi.json` (người điều phối sinh bằng `run.sh openapi`): method, đường, tên thao tác;
- `apps/api/<module>/cases.toml`, mục `[[endpoint]]` của chính module: `type` (Loại, bắt buộc để có dòng),
  `lock` (khoá quyền, vắng = `—`), `logged` (có ghi nhật ký, vắng = không), `outside` (`+ngoài`, vắng = không).

`load_bind_rows` và `merged_prompts` cài **một lần** ở đây; case_gate, contract và test gọi lại, không viết
bộ đọc riêng (BE-01 [2]).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

OPENAPI_FILE = Path("docs") / "contracts" / "openapi.json"
CASES_GLOB = "apps/api/*/cases.toml"
NO_LOCK = "—"


@dataclass(frozen=True, slots=True)
class BindRow:
    """Một thao tác hợp đồng: đường từ `openapi.json`, Loại/khoá/nhật ký từ `cases.toml` của module."""

    method: str
    path: str
    operation_id: str
    case_type: str
    outside: bool
    lock: str
    logged: bool


def _openapi_routes(spec_file: Path) -> dict[str, tuple[str, str]]:
    """Tên thao tác → (METHOD, đường) của mọi thao tác trong `openapi.json`.

    FastAPI ghi `operationId` = tên hàm route + đường (ký tự ngoài chữ/số thành `_`) + `_` + method; cắt đuôi
    đó ra tên thao tác. `operationId` không đúng khuôn (ai đó đổi cách sinh id) → `ValueError`.
    """
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    routes: dict[str, tuple[str, str]] = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            suffix = re.sub(r"\W", "_", path) + "_" + method
            operation_id = operation["operationId"]
            if not operation_id.endswith(suffix) or operation_id == suffix:
                raise ValueError(f"operationId sai khuôn FastAPI ở {method.upper()} {path}: {operation_id!r}")
            routes[operation_id.removesuffix(suffix)] = (method.upper(), path)
    return routes


def load_bind_rows(repo: str | Path) -> list[BindRow]:
    """Thao tác hợp đồng của `repo`, xếp theo tên thao tác.

    Có dòng khi mục `[[endpoint]]` khai `type` **và** `openapi.json` có thao tác đó. Mục không `type` chỉ là
    thêm/miễn case; mục có `type` mà `openapi.json` chưa có (route mới, chưa sinh lại) không ra dòng — cổng hợp
    đồng báo "đã mount mà không có dòng hợp đồng". Một thao tác khai `type` hai lần → `ValueError`.
    """
    root = Path(repo)
    routes = _openapi_routes(root / OPENAPI_FILE)
    typed: dict[str, dict[str, object]] = {}
    for cases_file in sorted(root.glob(CASES_GLOB)):
        for entry in tomllib.loads(cases_file.read_text(encoding="utf-8")).get("endpoint", []):
            if "type" not in entry:
                continue
            if entry["op"] in typed:
                raise ValueError(f"{entry['op']}: khai `type` hai lần (lần hai ở {cases_file.as_posix()})")
            typed[entry["op"]] = entry
    return [
        BindRow(
            method=routes[op][0],
            path=routes[op][1],
            operation_id=op,
            case_type=str(entry["type"]),
            outside=bool(entry.get("outside", False)),
            lock=str(entry.get("lock", NO_LOCK)),
            logged=bool(entry.get("logged", False)),
        )
        for op, entry in sorted(typed.items())
        if op in routes
    ]


def merged_prompts(repo: str | Path) -> set[str]:
    """Mã prompt đã hợp nhất = tên file (không đuôi) dưới `<repo>/changes/*.md`."""
    changes_dir = Path(repo) / "changes"
    if not changes_dir.is_dir():
        return set()
    return {p.stem for p in changes_dir.glob("*.md")}
