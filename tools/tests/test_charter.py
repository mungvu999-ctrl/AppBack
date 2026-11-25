"""tools/charter.py — dòng hợp đồng ghép từ `openapi.json` và `cases.toml`; `merged_prompts` đọc `changes/`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.charter import BindRow, load_bind_rows, merged_prompts

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_PATHS = {
    "/api/x": {"post": {"operationId": "x_create_api_x_post"}, "get": {"operationId": "x_list_api_x_get"}},
    "/api/x/{x_id}": {"delete": {"operationId": "x_delete_api_x__x_id__delete"}},
}


def _repo(tmp_path: Path, paths: dict[str, dict[str, dict[str, str]]], **cases: str) -> Path:
    """Repo tạm: `docs/contracts/openapi.json` với `paths`, mỗi `cases` là `apps/api/<module>/cases.toml`."""
    (tmp_path / "docs" / "contracts").mkdir(parents=True)
    (tmp_path / "docs" / "contracts" / "openapi.json").write_text(json.dumps({"paths": paths}), encoding="utf-8")
    for module, text in cases.items():
        (tmp_path / "apps" / "api" / module).mkdir(parents=True)
        (tmp_path / "apps" / "api" / module / "cases.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_repo_thật_đọc_đủ_111_dòng() -> None:
    """Dữ liệu thật: 111 thao tác hợp đồng, một dòng mẫu đủ mọi trường."""
    rows = {row.operation_id: row for row in load_bind_rows(REPO_ROOT)}
    assert len(rows) == 111
    assert rows["projects_create_project"] == BindRow(
        method="POST",
        path="/api/projects",
        operation_id="projects_create_project",
        case_type="G*",
        outside=False,
        lock="project.create",
        logged=True,
    )
    assert sum(row.outside for row in rows.values()) == 13
    assert sum(row.logged for row in rows.values()) == 31


def test_ghép_đường_openapi_với_trường_cases_toml(tmp_path: Path) -> None:
    """Đường và method từ `openapi.json`; trường vắng lấy mặc định; kết quả xếp theo tên thao tác."""
    repo = _repo(
        tmp_path,
        _PATHS,
        b='[[endpoint]]\nop = "x_delete"\ntype = "G"\nlock = "admin"\nlogged = true\noutside = true\n',
        a='[[endpoint]]\nop = "x_create"\ntype = "G*"\n',
    )
    assert load_bind_rows(repo) == [
        BindRow("POST", "/api/x", "x_create", "G*", outside=False, lock="—", logged=False),
        BindRow("DELETE", "/api/x/{x_id}", "x_delete", "G", outside=True, lock="admin", logged=True),
    ]


def test_mục_không_type_hay_ngoài_openapi_không_ra_dòng(tmp_path: Path) -> None:
    """Mục chỉ thêm/miễn case (không `type`) và mục `type` của route chưa có trong `openapi.json` đều bị bỏ."""
    repo = _repo(
        tmp_path,
        _PATHS,
        a='[[endpoint]]\nop = "x_list"\nextra = ["C02"]\n\n[[endpoint]]\nop = "x_moi"\ntype = "Đ"\n',
        b='[[task]]\nfn = "viec_nen"\nrequire = ["J03"]\n',
    )
    assert load_bind_rows(repo) == []


def test_khai_type_hai_lần_ném_valueerror(tmp_path: Path) -> None:
    """Một thao tác chỉ được khai `type` ở một `cases.toml`."""
    entry = '[[endpoint]]\nop = "x_create"\ntype = "G"\n'
    with pytest.raises(ValueError, match="x_create: khai `type` hai lần"):
        load_bind_rows(_repo(tmp_path, _PATHS, a=entry, b=entry))


@pytest.mark.parametrize("operation_id", ["x_create", "_api_x_post"])
def test_operation_id_sai_khuôn_ném_valueerror(tmp_path: Path, operation_id: str) -> None:
    """`operationId` thiếu đuôi đường + method, hay chỉ có đuôi (tên rỗng), là sai khuôn FastAPI."""
    repo = _repo(tmp_path, {"/api/x": {"post": {"operationId": operation_id}}})
    with pytest.raises(ValueError, match="operationId sai khuôn"):
        load_bind_rows(repo)


def test_merged_prompts_đọc_changes(tmp_path: Path) -> None:
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "B0-01.md").write_text("x", encoding="utf-8")
    (tmp_path / "changes" / "B1-01.md").write_text("x", encoding="utf-8")
    assert merged_prompts(tmp_path) == {"B0-01", "B1-01"}


def test_merged_prompts_thiếu_thư_mục_trả_rỗng(tmp_path: Path) -> None:
    assert merged_prompts(tmp_path) == set()
