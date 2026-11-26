"""Bộ ghi golden: response của test → mẫu JSON cho H1 ở bước 7 (B0-07 [2], [6]).

Observer của `API_RESPONSE_OBSERVERS` (B0-06), gắn bởi `packages/testing/fixtures/golden.py`.
Chỉ ghi khi đủ cả ba:

- tên test tách được theo cổng case (`test_<op>__<case>[_<hậu tố>]`, `test_common__<case>[<op>]`);
- (method, đường) của request khớp **đúng** thao tác `<op>` của tên, theo `operations()`;
- status < 500 và không phải `text/event-stream` (luồng không được đọc ở đây; test luồng gọi
  `record_stream_event` với khung đã nhận).

`CONTRACT_SAMPLES_DIR` chưa đặt → không ghi gì (chạy pytest tay không làm bẩn cây). File
`<op>/<case>[_<hậu tố>]-<n>.json`, `n` từ 1 theo thứ tự trong test; ghi nguyên tử (file tạm
`.part` rồi `link` — không bao giờ đè), an toàn khi nhiều tiến trình cùng ghi. Không ghi header;
`accessToken` trong thân được thay bằng chuỗi giả cùng dạng trước khi chạm đĩa.
"""

import json
import os
import re
import tempfile
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import httpx
import pytest

from apps.api.core.openapi import Operation, operations
from packages.core.case_names import split_case_test_name

SAMPLES_ENV: Final = "CONTRACT_SAMPLES_DIR"
EVENT_STREAM: Final = "text/event-stream"
TOKEN_KEY: Final = "accessToken"  # noqa: S105 — tên khoá phải che, không phải một mật khẩu
SERVER_ERROR: Final = 500
_UNSAFE_CHARS_RE: Final = re.compile(r"[^A-Za-z0-9_.-]+")
_NAME_RE: Final = re.compile(r"[A-Za-z0-9_]+")
_PATH_PARAM_RE: Final = re.compile(r"(\{[^}]+\})")

_written: weakref.WeakKeyDictionary[httpx.Response, Path] = weakref.WeakKeyDictionary()
"""Response → file mẫu của nó, để `attach_context` gắn ngữ cảnh **sau** khi bộ ghi đã ghi."""


@dataclass(frozen=True, slots=True)
class OperationMatch:
    """Thao tác mà một request khớp: `operationId`, đường khuôn, tham số đường."""

    op: str
    path_template: str
    path_params: dict[str, str]


type Resolver = Callable[[str, str], OperationMatch | None]


def samples_root() -> Path | None:
    """Thư mục mẫu của lượt verify, hay `None` khi không chạy trong cổng."""
    value = os.environ.get(SAMPLES_ENV)
    return Path(value) if value else None


def split_test_name(name: str) -> tuple[str, str, str] | None:
    """`(op, case, gốc tên file)` theo cổng case, hay `None` khi tên không phải test case.

    Tách bằng `packages.core.case_names.split_case_test_name` (nguồn duy nhất, R-07).
    Hậu tố (`_missing`, `[tham-số]`) thành `<case>_<hậu tố>`, chỉ giữ ký tự an toàn cho tên file.
    """
    parts = split_case_test_name(name)
    if parts is None:
        return None
    suffix = _UNSAFE_CHARS_RE.sub("_", parts.tail).strip("_")
    return parts.op, parts.case, f"{parts.case}_{suffix}" if suffix else parts.case


def _path_pattern(template: str) -> re.Pattern[str]:
    """Regex của một đường khuôn, mỗi `{tên}` thành nhóm có tên (một đoạn đường)."""
    parts = _PATH_PARAM_RE.split(template)
    return re.compile("".join(f"(?P<{p[1:-1]}>[^/]+)" if p.startswith("{") else re.escape(p) for p in parts))


def operation_resolver(ops: Iterable[Operation]) -> Resolver:
    """Hàm khớp (method, đường thật) → `OperationMatch` trên một tập thao tác."""
    table = [(item.method, _path_pattern(item.path), item) for item in ops]

    def resolve(method: str, path: str) -> OperationMatch | None:
        """Thao tác đầu tiên cùng method mà đường khớp trọn."""
        for expected, pattern, item in table:
            found = pattern.fullmatch(path)
            if found and expected == method.upper():
                return OperationMatch(item.op, item.path, found.groupdict())
        return None

    return resolve


@cache
def _real_resolver() -> Resolver:
    """Bảng khớp của app thật, dựng một lần mỗi tiến trình."""
    return operation_resolver(operations())


def resolve_operation(method: str, path: str) -> OperationMatch | None:
    """Thao tác của app thật mà request khớp; test của bộ ghi thay hàm này bằng bảng của app thử."""
    return _real_resolver()(method, path)


def mask_tokens(value: Any) -> Any:
    """Bản sao của `value` với mọi `accessToken` chuỗi thay bằng chuỗi giả **cùng dạng**.

    Giữ số đoạn và độ dài mỗi đoạn (JWT vẫn là ba đoạn), nên mẫu vẫn qua `strict/refresh.ts`
    mà không mang byte nào của token thật (BE-00 §11, B0-07 [9]).
    """
    if isinstance(value, dict):
        return {
            key: ".".join("A" * len(part) for part in child.split("."))
            if key == TOKEN_KEY and isinstance(child, str)
            else mask_tokens(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [mask_tokens(child) for child in value]
    return value


def _dump_part(directory: Path, data: Mapping[str, Any]) -> Path:
    """Ghi `data` vào file tạm `.part` trong `directory` (cùng hệ tệp với đích, để `link` được)."""
    fd, name = tempfile.mkstemp(prefix=".", suffix=".part", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return Path(name)


def write_sample(directory: Path, stem: str, data: Mapping[str, Any]) -> Path:
    """Ghi nguyên tử thành `<stem>-<n>.json` với `n` nhỏ nhất còn trống, trả đường đã ghi.

    `os.link` không bao giờ đè và nguyên tử, nên hai tiến trình cùng tên test (hai file test
    khác nhau) không làm mất mẫu của nhau; trong một tiến trình, `n` tăng theo thứ tự gọi.
    """
    directory.mkdir(parents=True, exist_ok=True)
    part = _dump_part(directory, data)
    try:
        number = 1
        while True:
            target = directory / f"{stem}-{number}.json"
            try:
                os.link(part, target)
            except FileExistsError:
                number += 1
                continue
            return target
    finally:
        part.unlink()


def _body(response: httpx.Response) -> Any:
    """Thân đã giải JSON; `None` khi rỗng; chuỗi thô khi không phải JSON (H1 sẽ báo hỏng)."""
    if not response.content:
        return None
    try:
        return mask_tokens(response.json())
    except ValueError:
        return response.text


def record_response(item: pytest.Item, response: httpx.Response) -> None:
    """Observer: ghi một mẫu khi tên test, thao tác và status đều khớp (docstring module)."""
    root = samples_root()
    content_type = response.headers.get("content-type", "")
    if root is None or response.status_code >= SERVER_ERROR or content_type.startswith(EVENT_STREAM):
        return
    parsed = split_test_name(item.name)
    request = response.request
    matched = resolve_operation(request.method, request.url.path) if parsed else None
    if parsed is None or matched is None or matched.op != parsed[0]:
        return
    op, case, stem = parsed
    sample = {
        "operationId": op,
        "case": case,
        "method": request.method,
        "pathTemplate": matched.path_template,
        "pathParams": matched.path_params,
        "query": dict(request.url.params),
        "status": response.status_code,
        "contentType": content_type,
        "body": _body(response),
    }
    _written[response] = write_sample(root / op, stem, sample)


def attach_context(response: httpx.Response, **context: Any) -> None:
    """Gắn dữ liệu ngữ cảnh (vd `floorOrder=[...]` cho N7) vào mẫu của `response` (H1 ngữ cảnh).

    Không chạy trong cổng → không làm gì. Trong cổng mà response không được ghi (tên test,
    thao tác hay status không khớp) → `ValueError`: ngữ cảnh gắn vào đâu cũng không tới H1.
    Ghi lại file bằng file tạm + `os.replace` (nguyên tử).
    """
    path = _written.get(response)
    if path is None:
        if samples_root() is not None:
            raise ValueError("response này không được bộ ghi golden ghi, không có mẫu để gắn ngữ cảnh")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["context"] = {**data.get("context", {}), **context}
    os.replace(_dump_part(path.parent, data), path)


def record_stream_event(op: str, case: str, data: Any) -> None:
    """Ghi `data:` của một khung SSE **đã nhận** thành mẫu H5 `<op>/<case>-event-<n>.json`.

    Người gọi truyền `json.loads(frame.data)` của khung đã qua `parse_sse` trên byte thật,
    không bao giờ dữ liệu đầu vào đã publish (B0-07 [6].8). Không chạy trong cổng → không ghi.
    """
    root = samples_root()
    if root is None:
        return
    if not (_NAME_RE.fullmatch(op) and _NAME_RE.fullmatch(case)):
        raise ValueError(f"op/case phải là [A-Za-z0-9_]+: {op!r}, {case!r}")
    write_sample(root / op, f"{case}-event", {"operationId": op, "case": case, "event": data})
