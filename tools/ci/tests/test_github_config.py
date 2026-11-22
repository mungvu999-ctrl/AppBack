"""Quét tĩnh cấu hình GitHub (DEBT-02 W1/C03, FIX-122): `dependabot.yml`, `ci.yml`, `commits.yml`, `.gitleaks.toml`.

Không mạng. Bổ sung `test_workflows.py` (chủ C02) cho các điều NO-178/NO-182/NO-183/NO-330.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COMMITS_YML = REPO_ROOT / ".github" / "workflows" / "commits.yml"
DEPENDABOT_YML = REPO_ROOT / ".github" / "dependabot.yml"
GITLEAKS_TOML = REPO_ROOT / ".gitleaks.toml"

# Mười bốn commit lịch sử mang chuỗi giả (Idempotency-Key, mật khẩu e2e, SECRET_KEY) và đoạn trích
# chính chuỗi đó trong SEC-043.md — `gitleaks detect --log-opts=--all` báo `generic-api-key` (NO-330).
# Hai SHA đầu là hai commit đã miễn từ trước (B0-04, B0-06). Nguồn DUY NHẤT của tập allowlist: so bằng, để một
# SHA thừa (tắt gitleaks cho cả commit đó, kể cả bí mật thật) làm test đỏ.
FROZEN_FAKE_SECRET_COMMITS = frozenset(
    {
        "eb40a3c513d685ffd48807986badb2a3dbcc2584",
        "1b97b24564d433c356d8d6385ab11b4a90fa8a69",
        "cce2a63c229f738afe8adb3101e589af70b9fd96",
        "1a22d90ee58ecfbdcae966fa3b2fee89fd17aedd",
        "6cca3fdfaf9b1162c5849190d69fcf4fca840b01",
        "5d58fc8cfc0120df8e981ee3bc40a91a5148df42",
        "2bc59317990fb2310535d6aada27e425ec20c167",
        "95fe5545e493c23750ef7b055510f823a691b42e",
        "2857e22dc460f8a6f1b5c21c6993e7f33a73612b",
        "7587f462fd5a02173dd92f6dac45ad586a9aa748",
        "35c3020c21f63433237e88a0e4b6066056974189",
        "ad3aa964a3443f946aa851efd5119987c7efaef8",
        "09dc9d0efb74cfafff5fe0f977f417a5f7e4b4b6",
        "ad7b5a060ac22728350fc714869087f3f4a0a9fb",
    }
)


def _load_yaml(path: Path) -> dict[Any, Any]:
    """Đọc một tệp YAML thành mapping."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    return data


def _docker_update() -> dict[str, Any]:
    """Khối `updates` của hệ `docker` trong `dependabot.yml`."""
    doc = _load_yaml(DEPENDABOT_YML)
    (docker,) = [u for u in doc["updates"] if u["package-ecosystem"] == "docker"]
    assert isinstance(docker, dict)
    return docker


def test_dependabot_docker__ignores_minor_for_python_and_node() -> None:
    """NO-178: bản minor của ảnh nền `python`/`node` (3.12→3.14, bỏ corepack) không được đề xuất."""
    ignores = _docker_update().get("ignore", [])
    for name in ("python", "node"):
        assert any(
            i.get("dependency-name") == name and "version-update:semver-minor" in i.get("update-types", [])
            for i in ignores
        ), name


def test_dependabot_docker__does_not_ignore_every_major() -> None:
    """NO-183: hệ `docker` không có kênh security-update — `ignore` major trần che mọi bản nền mới."""
    for i in _docker_update().get("ignore", []):
        assert not (i.get("dependency-name") == "*" and "version-update:semver-major" in i.get("update-types", []))


def test_dependabot__group_pr_titles_fit_commit_header_limit() -> None:
    """L8-N02: tiêu đề PR nhóm của Dependabot (công thức của nó, 99 bản cập nhật) ≤ 72 ký tự — job `commits` kiểm tiêu đề PR."""
    doc = _load_yaml(DEPENDABOT_YML)
    for update in doc["updates"]:
        prefix = update["commit-message"]["prefix"]
        for group in update["groups"]:
            for where in ("across 1 directory", f"in {update['directory']}"):
                title = f"{prefix}(deps): Bump the {group} group {where} with 99 updates"
                assert len(title) <= 72, f"{len(title)} ký tự: {title}"


def test_ci_yml__no_job_gated_on_event_action() -> None:
    """NO-182: `ci.yml` không có job nào lọc theo `github.event.action` — job bị bỏ qua vì điều kiện báo `skipped`,
    mà GitHub tính `skipped` là thành công của required check; `edited` thuộc về `commits.yml`."""
    for name, job in _load_yaml(CI_YML)["jobs"].items():
        assert "github.event.action" not in str(job.get("if", "")), name


def test_gitleaks_toml__history_findings_allowlisted_by_commit() -> None:
    """NO-330: mọi commit lịch sử chứa chuỗi giả đã đóng băng nằm trong `commits` (không miễn theo đường)."""
    with GITLEAKS_TOML.open("rb") as f:
        data = tomllib.load(f)
    commits = data["allowlist"]["commits"]
    assert len(commits) == len(set(commits))
    assert set(commits) == FROZEN_FAKE_SECRET_COMMITS
    assert not any("tests" in p for p in data["allowlist"]["paths"])


def test_commits_yml__concurrency_group_separate_from_ci() -> None:
    """NO-182: run `commits` của `edited` không được huỷ run `ci` đầy đủ đang chạy của cùng nhánh."""
    ci_group = _load_yaml(CI_YML)["concurrency"]["group"]
    commits_group = _load_yaml(COMMITS_YML)["concurrency"]["group"]
    assert ci_group.startswith("ci-")
    assert commits_group.startswith("commits-")


def test_packages_sources__do_not_import_tools_pinned_images() -> None:
    """NO-184: mã dưới `packages/` (trừ test) không nhập `tools.pinned_images` — hằng ảnh ghim nằm ở
    `packages.core.pinned_images`, tooling là lớp ngoài cùng không được là nguồn của gói."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "packages").rglob("*.py")
        if "tests" not in path.parts
        and re.search(r"^\s*(?:from|import)\s+tools\.pinned_images", path.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []
