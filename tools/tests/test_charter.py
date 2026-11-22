"""tools/charter.py — [8]: BE-BIND thật đọc đủ 49 + 2 + 46 dòng; dòng sai khuôn → ValueError; query bị bỏ."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.charter import _split_cells, load_bind_rows, merged_prompts

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BE_BIND = REPO_ROOT / "docs" / "charter" / "BE-BIND.md"

_TABLE_HEADER = (
    "| # | Đường v1 | operationId | Loại | Khoá | Nhật ký | Chủ | Ghi chú |\n|---|---|---|---|---|---|---|---|\n"
)


def _write_table(tmp_path: Path, *rows: str) -> Path:
    p = tmp_path / "bind.md"
    p.write_text(_TABLE_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_be_bind_thật_đọc_đủ_49_2_53_dòng() -> None:
    rows = load_bind_rows(BE_BIND)
    numeric = [r for r in rows if r.row_id.isdigit()]
    s_rows = [r for r in rows if r.row_id.startswith("S")]
    n_rows = [r for r in rows if r.row_id.startswith("N")]
    assert len(numeric) == 49
    assert len(s_rows) == 2
    assert len(n_rows) == 53


def test_query_bị_bỏ() -> None:
    rows = load_bind_rows(BE_BIND)
    n17 = next(r for r in rows if r.row_id == "N17")
    assert "?" not in n17.path
    assert n17.path == "/api/projects/{project_id}/versions"


def test_v2_operation_id_là_none() -> None:
    rows = load_bind_rows(BE_BIND)
    row2 = next(r for r in rows if r.row_id == "2")
    assert row2.operation_id is None
    assert row2.owner == "v2"


def test_khoá_bỏ_backtick(tmp_path: Path) -> None:
    """NO-128: cột Khoá đọc ra tên khoá trần, không giữ backtick markdown.

    Dựng bằng `_write_table` (độc lập với hiến chương): nếu BE-BIND đổi ô Khoá của hàng 25,
    test này vẫn canh đúng hồi quy thay vì đi xanh im lặng vì hàng đó không còn backtick.
    """
    p = _write_table(tmp_path, "| 1 | `POST /api/x` | `x_create` | G | `project.create` | có | B1-01 | |")
    rows = load_bind_rows(p)
    assert rows[0].lock == "project.create"


def test_outside_được_tách_khỏi_loại() -> None:
    rows = load_bind_rows(BE_BIND)
    row7 = next(r for r in rows if r.row_id == "7")
    assert row7.outside is True
    assert row7.case_type == "T-complete"


def test_dòng_thiếu_cột_ném_valueerror(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text(
        "| # | Đường v1 | operationId | Loại | Khoá | Nhật ký | Chủ | Ghi chú |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 1 | `POST /api/x` | `x_create` | G | — | — | B1-01 |\n",  # thiếu 1 cột
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="số cột lệch"):
        load_bind_rows(bad)


def test_method_path_sai_khuôn_ném_valueerror(tmp_path: Path) -> None:
    bad = _write_table(tmp_path, "| 1 | POST /api/x | `x_create` | G | — | — | B1-01 | |")
    with pytest.raises(ValueError, match="sai khuôn"):
        load_bind_rows(bad)


def test_bảng_khác_không_có_đường_v1_bị_bỏ_qua(tmp_path: Path) -> None:
    p = tmp_path / "other.md"
    p.write_text(
        "| Tên | Giá trị |\n|---|---|\n| Vai | admin, engineer, viewer |\n",
        encoding="utf-8",
    )
    assert load_bind_rows(p) == []


def test_merged_prompts_đọc_changes(tmp_path: Path) -> None:
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "B0-01.md").write_text("x", encoding="utf-8")
    (tmp_path / "changes" / "B1-01.md").write_text("x", encoding="utf-8")
    assert merged_prompts(tmp_path) == {"B0-01", "B1-01"}


def test_merged_prompts_thiếu_thư_mục_trả_rỗng(tmp_path: Path) -> None:
    assert merged_prompts(tmp_path) == set()


def test_dòng_không_mở_bằng_pipe_không_được_coi_là_bảng(tmp_path: Path) -> None:
    """`_is_separator_line` trả `False` khi dòng không mở bằng `|`: bảng không được nhận diện."""
    p = tmp_path / "bad.md"
    p.write_text(
        "| # | Đường v1 | operationId | Loại | Khoá | Nhật ký | Chủ | Ghi chú |\n"
        "---:---:---:---:---:---:---:---\n"  # không mở bằng |
        "| 1 | `POST /api/x` | `x_create` | G | — | — | B1-01 | |\n",
        encoding="utf-8",
    )
    assert load_bind_rows(p) == []


def test_bảng_thiếu_cột_khoá_ném_valueerror(tmp_path: Path) -> None:
    """`_find_col` ném `ValueError` khi header thiếu hẳn một cột bắt buộc (ở đây: Khoá)."""
    bad = tmp_path / "bad.md"
    bad.write_text(
        "| # | Đường v1 | operationId | Loại | Nhật ký | Chủ | Ghi chú |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | `POST /api/x` | `x_create` | G | — | B1-01 | |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="thiếu cột"):
        load_bind_rows(bad)


def test_method_lạ_ném_valueerror(tmp_path: Path) -> None:
    """`_parse_method_path` ném `ValueError` khi method khớp khuôn nhưng không thuộc bộ cho phép."""
    bad = _write_table(tmp_path, "| 1 | `HEAD /api/x` | `x_get` | G | — | — | B1-01 | |")
    with pytest.raises(ValueError, match="method lạ"):
        load_bind_rows(bad)


def test_split_cells_ô_không_mở_bằng_pipe() -> None:
    """`_split_cells` bỏ qua việc cắt ký tự đầu khi dòng không mở bằng `|`."""
    assert _split_cells("a | b |") == ["a", "b"]


def test_split_cells_ô_không_đóng_bằng_pipe() -> None:
    """`_split_cells` bỏ qua việc cắt ký tự cuối khi dòng không đóng bằng `|`."""
    assert _split_cells("| a | b") == ["a", "b"]
