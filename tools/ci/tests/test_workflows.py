"""Quét tĩnh `ci.yml`, `codeql.yml`, `dependabot.yml`, `.gitleaks.toml`, `job.sh` (B0-09).

Không mạng, không dựng ảnh/container — chỉ soi cấu trúc YAML/TOML và đối chiếu
`job.sh` với hợp đồng CLI đã ghim (hop-dong.md §2, prompt B0-09 [8] "Quét
workflow"/"Quét codeql.yml, dependabot.yml"). Cạm bẫy YAML 1.1: PyYAML đọc khoá
trần `on:` thành khoá boolean `True` — `_triggers()` dò cả hai dạng.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COMMITS_YML = REPO_ROOT / ".github" / "workflows" / "commits.yml"
CODEQL_YML = REPO_ROOT / ".github" / "workflows" / "codeql.yml"
DEPENDABOT_YML = REPO_ROOT / ".github" / "dependabot.yml"
GITLEAKS_TOML = REPO_ROOT / ".gitleaks.toml"
JOB_SH = REPO_ROOT / "tools" / "ci" / "job.sh"
COMMIT_MSG_HOOK = REPO_ROOT / ".githooks" / "commit-msg"

GATE_JOBS = ["lint", "typecheck", "unit-shards", "integration-shards", "ml-shards", "perf", "build"]
# Tên check bắt buộc của nhóm test -> ma trận shard mà nó gom kết quả (PERF-CI).
SHARD_GATHER_JOBS = {"unit": "unit-shards", "integration": "integration-shards", "ml": "ml-shards"}
TEST_SHARD_JOBS = ["unit-shards", "integration-shards", "ml-shards"]
EXPECTED_JOBS = ["changes", *GATE_JOBS, *SHARD_GATHER_JOBS, "contract", "coverage"]
# Workflow của B0-09 chạy mã repo trên push/PR: luật chung (ghim SHA, quyền, không nội suy sự kiện…) áp cho cả hai.
REPO_WORKFLOWS = [CI_YML, COMMITS_YML]

_SHA_USES_RE = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}(?:\s*#.*)?$")
_STEPS_ARG_RE = re.compile(r"--steps\s+([0-9,b]+)")

# Ánh xạ job (bảng [2] hop-dong.md) -> bước verify 1-8/5b nó phủ, đọc thẳng từ
# `job.sh` (không chép tay): mỗi job gọi `tools.verify.steps verify --steps
# <n>` cho các bước dùng lại được nguyên; bước 5 không đi qua CLI đó (job chạy
# `coverage run -m pytest --ci-split=<nhóm>` trực tiếp cho unit/integration/ml).
_ALL_VERIFY_STEPS = {"0", "1", "2", "3", "4", "6", "7", "8", "5b"}


def _load_yaml(path: Path) -> dict[Any, Any]:
    """Đọc một workflow YAML thành dict; ném lỗi rõ ràng nếu không phải mapping."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{path}: gốc YAML không phải mapping"
    return data


def _triggers(doc: dict[Any, Any]) -> dict[Any, Any]:
    """`on:` bị PyYAML (YAML 1.1) đọc thành khoá boolean `True` — trả đúng khối kích hoạt."""
    triggers = doc.get("on", doc.get(True, {}))
    assert isinstance(triggers, dict)
    return triggers


def _walk_key(node: Any, key: str) -> list[str]:
    """Duyệt đệ quy toàn cây YAML, gom mọi giá trị chuỗi của khoá `key` (`uses`/`run`)."""
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, str):
                found.append(v)
            found.extend(_walk_key(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_key(item, key))
    return found


# ---------------------------------------------------------------------------
# ci.yml
# ---------------------------------------------------------------------------


def test_ci_yml_has_eight_jobs() -> None:
    """`ci.yml` đúng tám job của bảng [2] hop-dong.md + `changes`; thêm/bớt job phải sửa `job.sh` và required check."""
    doc = _load_yaml(CI_YML)
    assert set(doc["jobs"]) == set(EXPECTED_JOBS)


def test_commits_yml_has_only_commits_job() -> None:
    """`commits` ở workflow riêng (NO-182): `edited` chỉ sinh đúng một check run, không sinh bản `skipped` trùng tên."""
    doc = _load_yaml(COMMITS_YML)
    assert list(doc["jobs"]) == ["commits"]


def test_ci_yml_only_coverage_waits_on_gate_jobs() -> None:
    """Job cổng chỉ chờ `changes`; chỉ job có phụ thuộc dữ liệu thật chờ job cổng: `coverage` (`.coverage.*`
    mọi shard + junit perf), `contract` (mẫu golden của shard unit/integration), job gom shard."""
    doc = _load_yaml(CI_YML)
    assert "needs" not in doc["jobs"]["changes"]
    assert doc["jobs"]["coverage"]["needs"] == [*TEST_SHARD_JOBS, "perf"]
    assert doc["jobs"]["contract"]["needs"] == ["unit-shards", "integration-shards"]
    for name, shards in SHARD_GATHER_JOBS.items():
        assert doc["jobs"][name]["needs"] == shards, name
    for name in GATE_JOBS:
        assert doc["jobs"][name]["needs"] == "changes", name


def test_ci_yml_gate_jobs_skip_only_when_changes_passed_docs_only() -> None:
    """Fail-closed: job "skipped" được check bắt buộc tính là đạt, nên `changes` hỏng phải chạy đủ cổng, không bỏ."""
    doc = _load_yaml(CI_YML)
    expected = "${{ !cancelled() && (needs.changes.result != 'success' || needs.changes.outputs.code == 'true') }}"
    for name in GATE_JOBS:
        assert doc["jobs"][name]["if"] == expected, name
    coverage_if = doc["jobs"]["coverage"]["if"]
    for job in (*TEST_SHARD_JOBS, "perf"):
        assert f"needs.{job}.result == 'success'" in coverage_if
    # Job gom shard bỏ qua đúng khi ma trận bị bỏ (PR chỉ tài liệu); shard hỏng → job chạy và hỏng.
    for name, shards in SHARD_GATHER_JOBS.items():
        assert doc["jobs"][name]["if"] == f"${{{{ !cancelled() && needs.{shards}.result != 'skipped' }}}}", name


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_no_continue_on_error(path: Path) -> None:
    """Không bước nào nuốt lỗi bằng `continue-on-error` — job đỏ phải làm check đỏ (K24)."""
    assert "continue-on-error" not in path.read_text(encoding="utf-8")


def test_ci_yml_coverage_job_has_no_always() -> None:
    """`coverage` không chạy bằng `always()`: gộp độ phủ khi một job test đã hỏng là tính ngưỡng trên dữ liệu thiếu."""
    doc = _load_yaml(CI_YML)
    coverage_job = doc["jobs"]["coverage"]
    assert "always()" not in str(coverage_job.get("if", ""))
    for step in coverage_job.get("steps", []):
        assert "always()" not in str(step.get("if", ""))


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_uses_pinned_by_sha(path: Path) -> None:
    """Mọi `uses:` ghim SHA 40 ký tự — tag di động cho phép action bị thay mã sau lưng (chuỗi cung ứng)."""
    doc = _load_yaml(path)
    uses_values = _walk_key(doc["jobs"], "uses")
    assert uses_values, "không tìm thấy dòng uses: nào"
    for uses in uses_values:
        assert _SHA_USES_RE.match(uses), f"uses trôi hoặc thiếu SHA 40 ký tự: {uses}"


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_permissions_contents_read_only(path: Path) -> None:
    """Token của workflow chỉ đọc nội dung — job chạy mã của PR không được có quyền ghi repo."""
    doc = _load_yaml(path)
    assert doc.get("permissions") == {"contents": "read"}


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_no_pull_request_target(path: Path) -> None:
    """Không `pull_request_target`: sự kiện đó chạy mã của fork với secret và token ghi."""
    doc = _load_yaml(path)
    assert "pull_request_target" not in _triggers(doc)


def test_commits_yml_pull_request_types_include_edited() -> None:
    """NO-110: mặc định (`opened`, `synchronize`, `reopened`) không chạy lại `commits` khi tiêu đề PR đổi."""
    doc = _load_yaml(COMMITS_YML)
    types = _triggers(doc)["pull_request"]["types"]
    assert set(types) == {"opened", "synchronize", "reopened", "edited"}


def test_ci_yml_pull_request_does_not_run_on_edited() -> None:
    """NO-182: `ci.yml` không chạy trên `edited` — check run `skipped` trùng tên tính là thành công của required."""
    pull_request = _triggers(_load_yaml(CI_YML))["pull_request"]
    assert "edited" not in pull_request.get("types", [])


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_every_job_calls_job_sh(path: Path) -> None:
    """Mỗi job gọi `job.sh <tên job>` — logic CI ở một chỗ chạy được cả local, YAML chỉ là vỏ."""
    doc = _load_yaml(path)
    for name, job in doc["jobs"].items():
        runs = _walk_key(job, "run")
        assert any(f"tools/ci/job.sh {name}" in r for r in runs), name


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_run_steps_never_interpolate_event(path: Path) -> None:
    """`run:` không nội suy `${{ github.event.* }}` (tiêu đề PR là đầu vào người dùng → chèn lệnh); đi qua `env:`."""
    doc = _load_yaml(path)
    for run_body in _walk_key(doc["jobs"], "run"):
        assert "${{ github.event" not in run_body, run_body


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_checkout_steps_do_not_persist_credentials(path: Path) -> None:
    """Không job nào push — token chỉ-đọc không cần sống trong `.git/config` suốt các bước sau
    (/merge-review lượt 1 #15)."""
    doc = _load_yaml(path)
    for name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") is False, f"{name}: {step}"


def test_ci_yml_build_job_uploads_trivy_sarif() -> None:
    """SARIF trivy phải sống qua artifact — container --rm không mount thì mất theo khi xoá
    (/merge-review lượt 1 #3). Bước upload dùng `if: always()` ở MỨC STEP (hợp lệ, [9] chỉ cấm
    `always()` mức job, không cấm bước riêng lẻ trong job)."""
    doc = _load_yaml(CI_YML)
    build_job = doc["jobs"]["build"]
    upload_steps = [
        step for step in build_job.get("steps", []) if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert upload_steps, "job build thiếu bước upload-artifact cho SARIF"
    sarif_step = upload_steps[0]
    assert "trivy" in str(sarif_step["with"]["path"]).lower()
    assert sarif_step.get("if") == "always()"


# ---------------------------------------------------------------------------
# job.sh — hợp các --steps với việc của job, đọc thật từ file (không chép tay)
# ---------------------------------------------------------------------------


def test_job_sh_verify_steps_cover_1_to_8_and_5b() -> None:
    """Hợp các `--steps` mà `job.sh` gọi phủ đủ bước 0 đến 8 và 5b — CI không bỏ sót bước so với cổng local."""
    text = JOB_SH.read_text(encoding="utf-8")
    found: set[str] = set()
    for m in _STEPS_ARG_RE.finditer(text):
        found.update(m.group(1).split(","))
    assert found == _ALL_VERIFY_STEPS, f"job.sh thiếu bước verify: {_ALL_VERIFY_STEPS - found}"
    # Bước 5 không đi qua `tools.verify.steps` (unit/integration/ml gọi `pytest -n --cov
    # --ci-split=<nhóm>` trực tiếp, NO-269) — xác nhận bằng chứng riêng.
    assert 'pytest -n "$(_ci_pytest_workers' in text
    assert "coverage run -m pytest" not in text


def test_job_sh_typecheck_compares_committed_openapi_reference() -> None:
    """NO-105: `docs/contracts/openapi.json` (bản tham chiếu đã commit) thay cho `openapi.json` gốc
    (bị `.gitignore`) — job `typecheck` phải ép chế độ so sánh, không còn `env -u VERIFY_BRANCH`."""
    text = JOB_SH.read_text(encoding="utf-8")
    assert "docs/contracts/openapi.json" in text
    assert "env -u VERIFY_BRANCH" not in text


def test_job_sh_smoke_uses_web_port_for_api_paths_not_api_host_port() -> None:
    """NO-118/NO-180: `api` không còn cổng host riêng (`deploy/compose/ci.yml`) — mọi kiểm smoke
    `/api/...` phải đi qua cổng `web` (nginx proxy `/api/` sang `api:8000`). Khoá hành vi (không
    `curl`/`export` nào dùng `API_HOST_PORT`), không khoá chữ: comment nhắc biến đã bỏ là hợp lệ."""
    text = JOB_SH.read_text(encoding="utf-8")
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    # `MINIO_API_HOST_PORT` là biến khác nên không bị tính
    offenders = [ln for ln in code_lines if re.search(r"(?<![A-Z_])API_HOST_PORT", ln)]
    assert offenders == []
    assert 'curl -fsS --max-time 5 "http://127.0.0.1:${WEB_HTTP_PORT}/api/health"' in text
    assert 'curl -fsS --max-time 5 "http://127.0.0.1:${WEB_HTTP_PORT}/api/ready"' in text


def test_job_sh_pins_docker_images_by_digest() -> None:
    """Ảnh gitleaks/trivy ghim digest `@sha256:` — tag có thể bị đẩy lại, digest thì không."""
    text = JOB_SH.read_text(encoding="utf-8")
    for var in ("GITLEAKS_IMAGE", "TRIVY_IMAGE"):
        m = re.search(rf'{var}="([^"]+)"', text)
        assert m, f"thiếu hằng {var}"
        assert "@sha256:" in m.group(1), f"{var} không ghim digest: {m.group(1)}"


# ---------------------------------------------------------------------------
# job_lint_audit — pip-audit thoát 1 (có lỗ hổng) phải chuyển cho tools.ci.audit
# quyết, không tự "hỏng" (/merge-review lượt 1 #1, P1). Test nguồn thẳng job.sh
# thật (source, không chỉ gọi audit.py) với một `uvx` giả trên PATH.
# ---------------------------------------------------------------------------

_FAKE_UVX = """#!/usr/bin/env bash
# uvx gia: bo qua moi tham so that pip-audit nhan, chi tra ve theo bien moi
# truong ma test dat san — cach ly khoi mang/PyPI that.
printf '%s' "$FAKE_UVX_STDOUT"
exit "${FAKE_UVX_EXIT:-0}"
"""

_VULN_EXEMPTED_JSON = (
    '{"dependencies": [{"name": "demo-pkg", "version": "1.0.0", "vulns": '
    '[{"id": "GHSA-test-0001", "fix_versions": ["1.0.1"], "aliases": []}]}]}'
)
_VULN_UNWAIVED_JSON = (
    '{"dependencies": [{"name": "demo-pkg", "version": "1.0.0", "vulns": '
    '[{"id": "GHSA-test-0002", "fix_versions": ["1.0.1"], "aliases": []}]}]}'
)
_EXEMPT_ALLOWLIST = """
[[ignore]]
id = "GHSA-test-0001"
reason = "fixture cua test merge-review, khong phai loi hong that"
expires = 2099-12-31
"""
_EMPTY_ALLOWLIST = "# rong, khong mien gi\n"


def _run_job_lint_audit(
    tmp_path: Path, *, stdout: str, exit_code: int, allowlist: str
) -> subprocess.CompletedProcess[str]:
    """Nguồn `job.sh` thật rồi gọi thẳng `job_lint_audit` (không kèm ruff/mypy/gitleaks) với `uvx`
    giả trên PATH và `AUDIT_ALLOWLIST` trỏ vào allowlist tạm — đi đúng đường pip-audit →
    tools.ci.audit mà /merge-review lượt 1 #1 đòi, không chỉ gọi `audit.py` riêng."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uvx = bin_dir / "uvx"
    fake_uvx.write_text(_FAKE_UVX, encoding="utf-8")
    fake_uvx.chmod(0o755)
    allowlist_file = tmp_path / "audit-allowlist.toml"
    allowlist_file.write_text(allowlist, encoding="utf-8")
    script = tmp_path / "run.sh"
    script.write_text(
        f'source "{JOB_SH}"\njob_lint_audit\necho "FAILED=$FAILED"\n[ "$FAILED" -eq 0 ]\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_UVX_STDOUT"] = stdout
    env["FAKE_UVX_EXIT"] = str(exit_code)
    env["AUDIT_ALLOWLIST"] = str(allowlist_file)
    return subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path, không nhận input người dùng
        ["bash", str(script)],  # noqa: S607 — "bash" có sẵn trên PATH runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_job_sh_pip_audit_exit_1_with_exempted_vuln_passes(tmp_path: Path) -> None:
    """Case thường: pip-audit thoát 1 (có lỗ hổng) nhưng lỗ hổng đã miễn trong allowlist → đạt —
    tools.ci.audit chạy tới và tự quyết, không bị chặn bởi mã thoát 1 của chính pip-audit."""
    result = _run_job_lint_audit(tmp_path, stdout=_VULN_EXEMPTED_JSON, exit_code=1, allowlist=_EXEMPT_ALLOWLIST)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAILED=0" in result.stdout


def test_job_sh_pip_audit_exit_1_with_unwaived_fixable_vuln_fails(tmp_path: Path) -> None:
    """Case lỗi: pip-audit thoát 1, lỗ hổng có bản sửa mà chưa miễn → hỏng (tools.ci.audit tự bắt)."""
    result = _run_job_lint_audit(tmp_path, stdout=_VULN_UNWAIVED_JSON, exit_code=1, allowlist=_EMPTY_ALLOWLIST)
    assert result.returncode != 0
    assert "FAILED=1" in result.stdout


def test_job_sh_pip_audit_exit_2_fails(tmp_path: Path) -> None:
    """Case lỗi: pip-audit thoát > 1 (lỗi thật của chính công cụ) → hỏng ngay ở bước pip-audit,
    tools.ci.audit không chạy tới (run_step chặn bước sau khi FAILED đã lên 1)."""
    result = _run_job_lint_audit(tmp_path, stdout="", exit_code=2, allowlist=_EMPTY_ALLOWLIST)
    assert result.returncode != 0
    assert "FAILED=1" in result.stdout


def test_job_sh_smoke_compose_uses_ci_yml_standalone() -> None:
    """Case lỗi (hồi quy): smoke KHÔNG được gộp `base.yml` — service `ml-gpu` của `base.yml` chỉ để
    `extends` (không `image`/`build`), gộp trực tiếp làm `docker compose config` hỏng ngay từ bước
    cấu hình (B0-08 đã kiểm `docker compose -f deploy/compose/ci.yml config -q` exit 0 một mình,
    mỗi service trong `ci.yml` tự `extends: file: base.yml` đúng phần nó cần — bao-cao-c2.md).
    """
    text = JOB_SH.read_text(encoding="utf-8")
    assert "deploy/compose/base.yml" not in text, "job.sh không được gộp base.yml vào project compose"
    assert "-f deploy/compose/ci.yml" in text


def test_job_sh_import_all_passes_service_env_file() -> None:
    """Case lỗi (hồi quy): `import_all` phải nhận env như service thật (`ci.yml`/`base.yml`) — chạy
    ảnh không env khiến `packages.messaging` ăn nhầm lỗi thiếu biến môi trường (fail-fast hợp lệ)
    thay vì lỗi thiếu thư viện thật mà `import_all` cần bắt (case [8])."""
    text = JOB_SH.read_text(encoding="utf-8")
    assert "--env-file deploy/compose/env.example" in text


def test_job_sh_unknown_job_exits_2() -> None:
    """Tên job lạ thoát 2 (lỗi dùng), không thoát 0 — workflow gõ sai tên job không được xanh giả."""
    result = subprocess.run(  # noqa: S603 — gọi script cục bộ của chính repo, không nhận input người dùng
        ["bash", str(JOB_SH), "nope"],  # noqa: S607 — "bash" cố ý không full path, có sẵn trên PATH runner
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2


def test_job_contract_exports_writable_verify_out_dir(tmp_path: Path) -> None:
    """NO-051: `export_contract_samples()` (`tools/verify/steps.py`) ghi vào `VERIFY_OUT_DIR`
    (mặc định `/src-out`, chỉ ghi được TRONG container verify) mỗi khi `CONTRACT_SAMPLES_DIR` được
    đặt — job `contract` đặt `CONTRACT_SAMPLES_DIR` mà chạy `tools.verify.steps` NGOÀI container
    (CI thật), nên phải tự trỏ `VERIFY_OUT_DIR` vào một thư mục ghi được cùng lúc."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env_dump = tmp_path / "env-dump.txt"
    fake_python = bin_dir / "python"
    fake_python.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$VERIFY_OUT_DIR" >> "{env_dump}"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text(f'source "{JOB_SH}"\njob_contract\n', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    for key in ("VERIFY_OUT_DIR", "CONTRACT_SAMPLES_DIR", "RUNNER_TEMP"):
        env.pop(key, None)
    result = subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path
        ["bash", str(script)],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    values = {line for line in env_dump.read_text(encoding="utf-8").splitlines() if line}
    assert values, "python giả không được gọi với VERIFY_OUT_DIR đã đặt"
    assert len(values) == 1, f"VERIFY_OUT_DIR đổi giữa các lệnh trong cùng job: {values}"
    out_dir = next(iter(values))
    assert out_dir != "/src-out"
    assert Path(out_dir).is_dir()


def _fake_command(bin_dir: Path, name: str, body: str) -> None:
    """Đặt một lệnh giả thực thi được vào `bin_dir` để chạy `job.sh` thật mà không gọi docker/git thật."""
    bin_dir.mkdir(exist_ok=True)
    command = bin_dir / name
    command.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    command.chmod(0o755)


def test_job_lint_gitleaks_fails_closed_on_empty_git_history(tmp_path: Path) -> None:
    """NO-111: `git rev-list --count --all` = 0 (worktree Orca: `.git` hỏng/không đọc được lịch sử)
    → hỏng ngay, không để gitleaks tự thoát 0 kiểu "0 commits scanned" (xanh giả, /merge-review lượt
    1 #8). `docker`/`gitleaks` giả không được gọi tới — lỗi phải chặn TRƯỚC khi quét."""
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "git", "echo 0")
    docker_calls = tmp_path / "docker-calls"
    _fake_command(bin_dir, "docker", f'echo called >> "{docker_calls}"')
    script = tmp_path / "run.sh"
    script.write_text(
        f'source "{JOB_SH}"\njob_lint_gitleaks\necho "FAILED=$FAILED"\n[ "$FAILED" -eq 1 ]\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path, không nhận input người dùng
        ["bash", str(script)],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAILED=1" in result.stdout
    assert "git rev-list --count --all = 0" in result.stderr
    assert not docker_calls.exists(), "gitleaks không được quét khi chưa xác nhận có lịch sử git thật"


def test_job_build_trivy_mounts_host_sarif_dir(tmp_path: Path) -> None:
    """NO-111: SARIF ghi trong container `--rm` không mount thì mất khi container xoá
    (/merge-review lượt 1 #3) — `job_build_trivy` phải tạo và mount một thư mục host thật.

    `job.sh` tự `cd "$REPO_ROOT"` khi được nguồn (dòng 16) — luôn chạy relative tới gốc repo thật,
    không tới `cwd` của tiến trình gọi nó — nên kiểm nhánh CI thật (`RUNNER_TEMP` luôn được đặt bởi
    GitHub Actions), không kiểm nhánh máy cục bộ (`.cache/trivy` tương đối tới `$REPO_ROOT`).
    """
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "docker-args"
    _fake_command(bin_dir, "docker", f'printf "%s\\n" "$@" > "{calls}"')
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    script = tmp_path / "run.sh"
    script.write_text(f'source "{JOB_SH}"\njob_build_trivy demo\n', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["RUNNER_TEMP"] = str(runner_temp)
    result = subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path
        ["bash", str(script)],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (runner_temp / "trivy").is_dir()
    args = calls.read_text(encoding="utf-8").splitlines()
    assert any(a == f"{runner_temp}/trivy:/out" for a in args), args


def test_job_build_trivy__without_runner_temp_mounts_repo_cache_dir(tmp_path: Path) -> None:
    """NO-191: nhánh máy cục bộ (không `RUNNER_TEMP`) tạo `.cache/trivy` TỪ GỐC REPO (job.sh tự
    `cd "$REPO_ROOT"` khi được nguồn, nên `cwd` của tiến trình gọi không đổi được nơi tạo) và mount
    đúng thư mục đó. Test không cô lập bằng `cwd` mà đối chiếu với gốc repo thật; thư mục chỉ bị xoá
    nếu chính test này tạo ra (`.cache/` bị `.gitignore`)."""
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "docker-args"
    _fake_command(bin_dir, "docker", f'printf "%s\n" "$@" > "{calls}"')
    script = tmp_path / "run.sh"
    script.write_text(f'source "{JOB_SH}"\njob_build_trivy demo\n', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("RUNNER_TEMP", None)
    cache = REPO_ROOT / ".cache" / "trivy"
    existed = cache.exists()
    try:
        result = subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path
            ["bash", str(script)],  # noqa: S607 — `bash` có sẵn trên PATH của runner
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert cache.is_dir()
        assert not (tmp_path / ".cache").exists(), "thư mục cache phải ở gốc repo, không ở cwd của người gọi"
        args = calls.read_text(encoding="utf-8").splitlines()
        assert any(a.endswith("/.cache/trivy:/out") for a in args), args
    finally:
        if not existed:
            cache.rmdir()


def _run_check_nginx_version(tmp_path: Path, version_line: str) -> subprocess.CompletedProcess[str]:
    """Chạy `job_build_check_nginx_version` thật của `job.sh` với `docker` giả in `version_line`."""
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "docker", f'echo "{version_line}"')
    script = tmp_path / "run.sh"
    script.write_text(f'source "{JOB_SH}"\njob_build_check_nginx_version\n', encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path
        ["bash", str(script)],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("version", ["1.30.6", "1.30.9", "1.31.7", "1.31.8"])
def test_job_build_check_nginx_version__passes_at_or_above_line_floor(tmp_path: Path, version: str) -> None:
    """NO-113/183: sàn theo dòng phát hành (stable 1.30 → 1.30.6, mainline 1.31 → 1.31.7)."""
    result = _run_check_nginx_version(tmp_path, f"nginx version: nginx/{version}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("version", "floor"),
    [("1.30.5", "1.30.6"), ("1.31.6", "1.31.7"), ("1.31.0", "1.31.7")],
)
def test_job_build_check_nginx_version__fails_below_own_line_floor(tmp_path: Path, version: str, floor: str) -> None:
    """NO-181/183: dưới sàn CỦA DÒNG mình → thoát đúng 1 kèm lý do trên stderr (không phải 127 vì
    thiếu hàm). 1.31.6 ≥ sàn 1.30.6 theo `sort -V` nhưng vẫn phải hỏng — xanh giả của sàn đơn."""
    result = _run_check_nginx_version(tmp_path, f"nginx version: nginx/{version}")
    assert result.returncode == 1, result.stderr
    assert f"nginx {version} < sàn {floor}" in result.stderr


@pytest.mark.parametrize("version", ["1.29.8", "1.32.0", "2.0.1"])
def test_job_build_check_nginx_version__unknown_line_fails_closed(tmp_path: Path, version: str) -> None:
    """NO-183: dòng chưa có trong bảng sàn (kể cả dòng MỚI hơn) → hỏng kín, buộc người nâng ảnh
    tra advisory rồi thêm sàn, thay vì tự xanh."""
    result = _run_check_nginx_version(tmp_path, f"nginx version: nginx/{version}")
    assert result.returncode == 1, result.stderr
    assert "không có sàn" in result.stderr


def test_job_build_check_nginx_version__unparsable_output_fails(tmp_path: Path) -> None:
    """Đầu ra `nginx -v` không có số phiên bản → hỏng (không xanh vì rỗng)."""
    result = _run_check_nginx_version(tmp_path, "nginx: command not found")
    assert result.returncode == 1


def test_job_build_calls_nginx_version_check() -> None:
    """`job_build` kiểm bản nginx theo bảng sàn từng dòng phát hành, không còn một hằng sàn chung (NO-183)."""
    text = JOB_SH.read_text(encoding="utf-8")
    assert "job_build_check_nginx_version" in text
    assert "NGINX_MIN_VERSION" not in text


def test_job_build_nginx_floor_matches_web_dockerfile_line() -> None:
    """Ảnh `web` thật (`web.Dockerfile`) phải có bản nginx ≥ sàn dòng của nó — bảng sàn và Dockerfile
    không được lệch nhau (ảnh ở dòng chưa có sàn sẽ hỏng kín ở CI, ở đây bắt sớm bằng test tĩnh)."""
    dockerfile = (REPO_ROOT / "deploy" / "docker" / "web.Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"^FROM nginxinc/nginx-unprivileged:(\d+\.\d+)\.(\d+)", dockerfile, re.MULTILINE)
    assert m, "web.Dockerfile không có FROM nginx-unprivileged:<x.y.z>"
    script = f'source "{JOB_SH}"\nnginx_floor_for_line {m.group(1)}\n'
    result = subprocess.run(  # noqa: S603 — script cục bộ, đầu vào là hằng của repo
        ["bash", "-c", script],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"dòng {m.group(1)} của web.Dockerfile chưa có sàn trong job.sh"
    floor_patch = int(result.stdout.strip().rsplit(".", 1)[1])
    assert int(m.group(2)) >= floor_patch


@pytest.mark.parametrize("path", REPO_WORKFLOWS, ids=lambda p: p.name)
def test_job_sh_has_branch_for_every_ci_job(path: Path) -> None:
    """Mỗi job của workflow có nhánh `case` trong `job.sh` — thiếu nhánh thì job rơi vào "job lạ" và hỏng ở CI."""
    doc = _load_yaml(path)
    text = JOB_SH.read_text(encoding="utf-8")
    for name in doc["jobs"]:
        assert re.search(rf"^\s*{re.escape(name)}\)\s", text, re.MULTILINE), f"job.sh thiếu nhánh cho '{name}'"


# ---------------------------------------------------------------------------
# codeql.yml
# ---------------------------------------------------------------------------


def test_codeql_yml_uses_pinned_by_sha() -> None:
    """Action của CodeQL cũng ghim SHA 40 ký tự, như mọi workflow khác."""
    doc = _load_yaml(CODEQL_YML)
    uses_values = _walk_key(doc["jobs"], "uses")
    assert uses_values
    for uses in uses_values:
        assert _SHA_USES_RE.match(uses), uses


def test_codeql_yml_analyzes_python_and_actions() -> None:
    """CodeQL quét đúng Python (mã nghiệp vụ) và Actions (nội suy nguy hiểm trong workflow), không thiếu không thừa."""
    doc = _load_yaml(CODEQL_YML)
    languages: set[str] = set()
    for job in doc["jobs"].values():
        matrix = job.get("strategy", {}).get("matrix", {})
        languages.update(matrix.get("language", []))
    assert languages == {"python", "actions"}


def test_codeql_yml_security_events_write_only_on_analyze_job() -> None:
    """`security-events: write` chỉ ở job `analyze` (tải SARIF), không ở mức workflow — quyền tối thiểu."""
    doc = _load_yaml(CODEQL_YML)
    assert doc.get("permissions", {}).get("security-events") is None
    for name, job in doc["jobs"].items():
        if job.get("permissions", {}).get("security-events") == "write":
            assert name == "analyze", f"job '{name}' không nên có security-events: write"
    assert doc["jobs"]["analyze"]["permissions"]["security-events"] == "write"


# ---------------------------------------------------------------------------
# dependabot.yml
# ---------------------------------------------------------------------------


def test_dependabot_yml_has_three_ecosystems() -> None:
    """Dependabot theo dõi đủ ba hệ `uv`, `github-actions`, `docker` ở đúng thư mục chứa tệp khoá/Dockerfile."""
    doc = _load_yaml(DEPENDABOT_YML)
    by_eco = {u["package-ecosystem"]: u for u in doc["updates"]}
    assert set(by_eco) == {"uv", "github-actions", "docker"}
    assert by_eco["uv"]["directory"] == "/"
    assert by_eco["github-actions"]["directory"] == "/"
    assert by_eco["docker"]["directory"] == "/deploy/docker"


def test_dependabot_yml_ignores_semver_major_for_every_ecosystem() -> None:
    """NO-153: bản major (vd `redis` 6→8, PR #5) không được gộp thẳng — hệ `uv`/`github-actions` phải có
    `ignore` chặn `version-update:semver-major` cho mọi gói (`dependency-name: "*"`). Hệ `docker` được
    miễn (NO-183): không có kênh security-update, `test_github_config.py` giữ phần của nó."""
    doc = _load_yaml(DEPENDABOT_YML)
    for update in doc["updates"]:
        if update["package-ecosystem"] == "docker":
            continue
        ignores = update.get("ignore", [])
        assert any(
            i.get("dependency-name") == "*" and "version-update:semver-major" in i.get("update-types", [])
            for i in ignores
        ), update["package-ecosystem"]


def test_dependabot_commit_prefixes_pass_commit_msg_hook(tmp_path: Path) -> None:
    """`uv`/`github-actions` có `directory: "/"` — Dependabot không nối hậu tố thư mục, tiêu đề ngắn,
    đạt bình thường. `docker` (`directory: "/deploy/docker"`) bị tách ra test riêng dưới đây vì thực
    tế luôn dài hơn 72 ký tự (/merge-review lượt 1 #4)."""
    doc = _load_yaml(DEPENDABOT_YML)
    by_eco = {u["package-ecosystem"]: u for u in doc["updates"]}
    samples = {
        "uv": "bump schemathesis from 3.29.4 to 3.30.0",
        "github-actions": "bump actions/checkout from 7.0.0 to 7.0.1",
    }
    for eco, sample in samples.items():
        prefix = by_eco[eco]["commit-message"]["prefix"]
        header = f"{prefix}(deps): {sample}"
        msg_file = tmp_path / f"msg-{eco}.txt"
        msg_file.write_text(header + "\n", encoding="utf-8")
        result = subprocess.run(  # noqa: S603 — hook cục bộ của chính repo, không nhận input người dùng
            ["bash", str(COMMIT_MSG_HOOK), str(msg_file)],  # noqa: S607 — "bash" có sẵn trên PATH runner
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"{eco}: '{header}' bị hook từ chối: {result.stderr}"


def test_dependabot_docker_title_with_directory_suffix_exceeds_72_and_is_rejected(tmp_path: Path) -> None:
    """Case lỗi thật (/merge-review lượt 1 #4): hệ `docker` có `directory` khác `/`, Dependabot nối
    thêm " in /deploy/docker" vào tiêu đề (chữ thường "bump" — định dạng thật của Dependabot khi
    `commit-message.prefix` + `include: scope` cùng khai) → dòng đầu > 72 ký tự, hook `.githooks/
    commit-msg` từ chối đúng luật. Đây chính là lý do `commits.py` bỏ kiểm dòng đầu từng commit cho
    nhánh `dependabot/**` (squash lấy tiêu đề PR, không lấy dòng đầu commit riêng lẻ)."""
    doc = _load_yaml(DEPENDABOT_YML)
    by_eco = {u["package-ecosystem"]: u for u in doc["updates"]}
    prefix = by_eco["docker"]["commit-message"]["prefix"]
    sample = "bump nginxinc/nginx-unprivileged from 1.28.0-alpine to 1.29.1-alpine in /deploy/docker"
    header = f"{prefix}(deps): {sample}"
    assert len(header) > 72
    msg_file = tmp_path / "msg-docker.txt"
    msg_file.write_text(header + "\n", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 — hook cục bộ của chính repo, không nhận input người dùng
        ["bash", str(COMMIT_MSG_HOOK), str(msg_file)],  # noqa: S607 — "bash" có sẵn trên PATH runner
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0


def test_dependabot_wrong_prefix_rejected_by_commit_msg_hook(tmp_path: Path) -> None:
    """Case lỗi: tiêu đề mang tiền tố **sai** hệ (không phải `build`/`ci` đã khai) bị hook từ chối."""
    msg_file = tmp_path / "msg-wrong-prefix.txt"
    msg_file.write_text("deps(uv): Bump schemathesis from 3.29.4 to 3.30.0\n", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 — hook cục bộ của chính repo, không nhận input người dùng
        ["bash", str(COMMIT_MSG_HOOK), str(msg_file)],  # noqa: S607 — "bash" có sẵn trên PATH runner
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# .gitleaks.toml
# ---------------------------------------------------------------------------


def test_gitleaks_toml_exempts_only_env_example_by_path() -> None:
    """Case thường: `paths` chỉ miễn đúng một file — không miễn file test nào theo đường (K24).

    Miễn vĩnh viễn theo đường sẽ bỏ qua cả bí mật thật thêm vào sau này; bí mật giả trong test
    của B0-04/B0-06 được miễn hẹp hơn qua `commits` (tập SHA so bằng ở
    `test_github_config.py::test_gitleaks_toml__history_findings_allowlisted_by_commit`).
    """
    with GITLEAKS_TOML.open("rb") as f:
        data = tomllib.load(f)
    assert data["extend"]["useDefault"] is True

    allowlists = data.get("allowlist", [])
    if isinstance(allowlists, dict):
        allowlists = [allowlists]
    all_paths = [p for a in allowlists for p in a.get("paths", [])]

    assert all_paths == [r"^deploy/compose/env\.example$"]
    pattern = re.compile(all_paths[0])
    assert pattern.match("deploy/compose/env.example")
    assert not pattern.match("deploy/compose/env.example.bak")
    assert not pattern.match("secrets/deploy/compose/env.example")


@pytest.mark.parametrize(
    ("event", "files", "expected"),
    [
        ("pull_request", ["docs/reviews/x.md", "DEBT.md", ".claude/skills/a/SKILL.md"], "false"),
        ("pull_request", ["docs/reviews/x.md", "apps/api/main.py"], "true"),
        ("pull_request", ["docs/charter/BE-BIND.md"], "true"),
        ("pull_request", ["docs/contracts/openapi.json"], "true"),
        ("pull_request", ["README.md"], "true"),
        ("push", ["docs/reviews/x.md"], "true"),
    ],
)
def test_job_changes_docs_only_decision(tmp_path: Path, event: str, files: list[str], expected: str) -> None:
    """`job.sh changes` chỉ báo `code=false` khi là PR và MỌI đường đổi là tài liệu ngoài charter/contracts."""
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "git", "printf '%s\\n' " + " ".join(files))
    out = tmp_path / "out.txt"
    env = dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(out))
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(  # noqa: S603 — gọi script cục bộ của chính repo
        ["bash", str(JOB_SH), "changes"],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8").splitlines() == [f"code={expected}"]


def test_job_changes_git_failure_fails_job(tmp_path: Path) -> None:
    """`git diff` hỏng → job hỏng (ci.yml khi đó chạy đủ cổng), không báo `code=false`."""
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "git", "exit 128")
    out = tmp_path / "out.txt"
    env = dict(os.environ, GITHUB_EVENT_NAME="pull_request", GITHUB_OUTPUT=str(out))
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(  # noqa: S603 — gọi script cục bộ của chính repo
        ["bash", str(JOB_SH), "changes"],  # noqa: S607 — `bash` có sẵn trên PATH của runner
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert not out.exists()


WEB_TRIVYIGNORE = REPO_ROOT / "tools" / "ci" / "web.trivyignore"
WEB_TRIVYIGNORE_CVES = {"CVE-2026-113425", "CVE-2026-121810", "CVE-2026-123007", "CVE-2026-123633"}
WEB_TRIVYIGNORE_CAP = date(2026, 12, 6)  # WEB-NGINX-CVE: tạo 2026-11-22 + 14 ngày


def _web_trivyignore_entries() -> list[tuple[str, date]]:
    entries: list[tuple[str, date]] = []
    for raw in WEB_TRIVYIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(CVE-\d{4}-\d+) exp:(\d{4}-\d{2}-\d{2})", line)
        assert m, f"dòng .trivyignore sai khuôn (cần '<CVE> exp:<YYYY-MM-DD>'): {raw!r}"
        entries.append((m.group(1), date.fromisoformat(m.group(2))))
    return entries


def test_web_trivyignore__only_the_four_nginx_cves_and_expiry_within_14_days() -> None:
    """WEB-NGINX-CVE: miễn tạm đúng bốn CVE chờ nginx 1.30.7, mọi dòng có hạn ≤ 2026-12-06 (14 ngày)."""
    entries = _web_trivyignore_entries()
    assert {cve for cve, _ in entries} == WEB_TRIVYIGNORE_CVES
    assert len(entries) == len(WEB_TRIVYIGNORE_CVES), "mã bị lặp"
    assert all(exp <= WEB_TRIVYIGNORE_CAP for _, exp in entries)
    assert "1.30.7" in WEB_TRIVYIGNORE.read_text(encoding="utf-8")


def test_job_build_trivy__web_mounts_ignorefile_and_keeps_strict_flags(tmp_path: Path) -> None:
    """WEB-NGINX-CVE: ảnh web mount tệp miễn + `--ignorefile`; ảnh khác không; không hạ mức/bỏ mã thoát."""
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "docker-args"
    _fake_command(bin_dir, "docker", f'printf "%s\n" "$@" > "{calls}"')
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["RUNNER_TEMP"] = str(runner_temp)
    seen: dict[str, list[str]] = {}
    for img in ("web", "api"):
        script = tmp_path / f"run-{img}.sh"
        script.write_text(f'source "{JOB_SH}"\njob_build_trivy {img}\n', encoding="utf-8")
        result = subprocess.run(  # noqa: S603 — script cục bộ dựng trong tmp_path
            ["bash", str(script)],  # noqa: S607 — `bash` có sẵn trên PATH của runner
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        seen[img] = calls.read_text(encoding="utf-8").splitlines()
    web = seen["web"]
    assert any(a.endswith("tools/ci/web.trivyignore:/ignore/web.trivyignore:ro") for a in web), web
    assert web[web.index("--ignorefile") + 1] == "/ignore/web.trivyignore"
    for args in seen.values():
        assert args[args.index("--severity") + 1] == "CRITICAL"
        assert args[args.index("--exit-code") + 1] == "1"
    assert "--ignorefile" not in seen["api"]
    assert not any("trivyignore" in a for a in seen["api"])
