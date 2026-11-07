"""Phiên bản bộ biên dịch (M1-01): đổi mã domain hoặc bộ thực thi Blender thì phiên bản đổi, cache cũ hết hiệu lực.

API không chứa `apps/worker` (ảnh api chỉ có `packages`), nên mã thực thi Blender đi vào phiên bản qua hằng
`EXECUTOR_SHA`; test của worker (`apps/worker/model_export/tests/test_version.py`) hỏng khi `compile_plan.bpy`
đổi mà hằng này chưa đổi. Xuống dòng được chuẩn hoá về LF trước khi băm, để checkout Windows (CRLF) và ảnh dựng
từ `git archive` (LF) cho cùng một phiên bản.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path
from typing import Final

PREFIX: Final = "m1-01"
EXECUTOR_SHA: Final = "750a80dbf786725268854409b7519a755d5b6bc29bdcd44bc7fce6d64998ab59"
"""sha256 (xuống dòng LF) của `apps/worker/model_export/compile_plan.bpy`."""


def normalized_bytes(path: Path) -> bytes:
    """Nội dung tệp với CRLF đổi thành LF."""
    return path.read_bytes().replace(b"\r\n", b"\n")


@cache
def compiler_version() -> str:
    """`m1-01.<sha12>` băm mọi `.py` của gói này (trừ `tests/`), theo đường tương đối đã sắp, cộng `EXECUTOR_SHA`."""
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/"):
            continue
        digest.update(rel.encode("utf-8") + b"\0" + normalized_bytes(path) + b"\0")
    digest.update(EXECUTOR_SHA.encode("ascii"))
    return f"{PREFIX}.{digest.hexdigest()[:12]}"
