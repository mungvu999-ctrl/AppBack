"""Đọc `docs/charter/BE-BIND.md` và `changes/` — nguồn dữ liệu dùng chung.

`load_bind_rows` và `merged_prompts` được B0-01 cài **một lần**; B0-06, B0-07,
B0-09 gọi lại, không viết parser riêng (BE-01 [2]).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ROW_RE = re.compile(r"^`(?P<method>[A-Z]+) (?P<path>/\S*)`$")
_SEP_CHARS = set("-: \t")
_EMPTY_CELL = {"—", "-", ""}


@dataclass(frozen=True, slots=True)
class BindRow:
    """Một dòng của bảng hợp đồng BE-BIND (§1 hoặc §2)."""

    row_id: str
    method: str
    path: str
    operation_id: str | None
    case_type: str
    outside: bool
    lock: str
    logged: bool
    owner: str


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _is_separator_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    return all(c in _SEP_CHARS or c == "|" for c in stripped)


def _split_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _find_col(headers: list[str], *, prefix: str) -> int:
    for i, h in enumerate(headers):
        if h == prefix or h.startswith(prefix):
            return i
    raise ValueError(f"thiếu cột bắt đầu bằng {prefix!r} trong bảng {headers!r}")


def _parse_method_path(cell: str) -> tuple[str, str]:
    m = _ROW_RE.match(cell)
    if not m:
        raise ValueError(f"cột Đường v1 sai khuôn: {cell!r}")
    method = m.group("method")
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"method lạ: {method!r} trong {cell!r}")
    path = m.group("path").split("?", 1)[0]
    return method, path


def _parse_case_type(cell: str) -> tuple[str, bool]:
    text = cell.replace("\\*", "*").strip()
    if text.endswith("+ngoài"):
        return text[: -len("+ngoài")].strip(), True
    return text, False


def _strip_markdown(cell: str) -> str:
    return cell.strip().strip("*`").strip()


def _parse_table(lines: list[str]) -> list[BindRow]:
    headers = _split_cells(lines[0])
    if "Đường v1" not in headers or "operationId" not in headers:
        return []

    col_id = _find_col(headers, prefix="#")
    col_path = _find_col(headers, prefix="Đường v1")
    col_op = _find_col(headers, prefix="operationId")
    col_type = _find_col(headers, prefix="Loại")
    col_lock = _find_col(headers, prefix="Khoá")
    col_logged = _find_col(headers, prefix="Nhật ký")
    col_owner = _find_col(headers, prefix="Chủ")

    rows: list[BindRow] = []
    for raw in lines[2:]:
        cells = _split_cells(raw)
        if len(cells) != len(headers):
            raise ValueError(f"số cột lệch header ({len(cells)} != {len(headers)}): {raw!r}")

        method, path = _parse_method_path(cells[col_path])
        op_cell = _strip_markdown(cells[col_op])
        operation_id = None if op_cell in _EMPTY_CELL else op_cell
        case_type, outside = _parse_case_type(cells[col_type])
        owner = _strip_markdown(cells[col_owner])

        rows.append(
            BindRow(
                row_id=cells[col_id].strip(),
                method=method,
                path=path,
                operation_id=operation_id,
                case_type=case_type,
                outside=outside,
                lock=_strip_markdown(cells[col_lock]),
                logged=cells[col_logged].strip() == "có",
                owner=owner,
            )
        )
    return rows


def load_bind_rows(path: str | Path) -> list[BindRow]:
    """Đọc mọi bảng hợp đồng (§1, §2, …) trong `path`, gộp thành một danh sách.

    Bảng được nhận diện qua header có cả `Đường v1` và `operationId`; các bảng
    khác trong file (§3-§5) bị bỏ qua. Dòng sai khuôn (số cột lệch, method lạ,
    method+path không đúng dạng `` `METHOD /path` ``) → `ValueError`.
    """
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()

    rows: list[BindRow] = []
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]) and i + 1 < len(lines) and _is_separator_line(lines[i + 1]):
            j = i + 2
            while j < len(lines) and _is_table_line(lines[j]):
                j += 1
            rows.extend(_parse_table(lines[i:j]))
            i = j
        else:
            i += 1
    return rows


PENDING_CHARTER_GLOB = "docs/reviews/pending/charter-*.md"


def pending_bind_rows(repo: str | Path) -> list[BindRow]:
    """Dòng BE-BIND **chờ chủ dự án duyệt**: bảng khuôn BE-BIND trong `<repo>/docs/reviews/pending/charter-*.md`.

    Cổng hợp đồng và cổng case đọc thêm các dòng này để route của nhánh vẫn được kiểm trong khi câu
    chữ hiến chương chờ duyệt (BE-00 §2). Duyệt xong, người điều phối chép dòng vào BE-BIND và xoá
    tệp chờ; dòng trùng `operationId` thì dòng BE-BIND thắng ở nơi gọi.
    """
    return [row for path in sorted(Path(repo).glob(PENDING_CHARTER_GLOB)) for row in load_bind_rows(path)]


def merged_prompts(repo: str | Path) -> set[str]:
    """Mã prompt đã hợp nhất = tên file (không đuôi) dưới `<repo>/changes/*.md`."""
    changes_dir = Path(repo) / "changes"
    if not changes_dir.is_dir():
        return set()
    return {p.stem for p in changes_dir.glob("*.md")}
