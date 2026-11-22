"""Quét tĩnh `deploy.yml`, `restore-drill.yml` (B0-10 [8] "Quét deploy.yml, restore-drill.yml").

Không mạng, không Docker thật — chỉ soi cấu trúc YAML và chạy thật đoạn bash kiểm
tag trích trực tiếp từ YAML (không chép regex vào test, tránh trôi giữa hai nơi).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from deploy.scripts.tests.support import REPO_ROOT, run_script

CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RESTORE_DRILL_YML = REPO_ROOT / ".github" / "workflows" / "restore-drill.yml"

_SHA_USES_RE = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@([0-9a-f]{40})(?:\s*#.*)?$")


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


def _ci_pinned_shas() -> set[str]:
    """Tập SHA 40 ký tự đã ghim trong `ci.yml` — nguồn duy nhất được phép dùng lại (hop-dong.md §5)."""
    doc = _load_yaml(CI_YML)
    shas = set()
    for uses in _walk_key(doc["jobs"], "uses"):
        m = _SHA_USES_RE.match(uses)
        assert m, f"ci.yml có uses trôi: {uses}"
        shas.add(m.group(1))
    return shas


def test_deploy_and_restore_drill_uses_pinned_by_sha_already_in_ci() -> None:
    """Mọi `uses:` của deploy.yml/restore-drill.yml là SHA 40 ký tự, và SHA đó đã ghim trong ci.yml."""
    ci_shas = _ci_pinned_shas()
    for path in (DEPLOY_YML, RESTORE_DRILL_YML):
        doc = _load_yaml(path)
        uses_values = _walk_key(doc["jobs"], "uses")
        assert uses_values, f"{path}: không có uses: nào"
        for uses in uses_values:
            m = _SHA_USES_RE.match(uses)
            assert m, f"{path}: uses trôi hoặc thiếu SHA 40 ký tự: {uses}"
            assert m.group(1) in ci_shas, f"{path}: SHA không có trong ci.yml: {uses}"


def test_deploy_yml_staging_runs_only_on_ci_success_main_and_checks_out_head_sha() -> None:
    """Job `images` (build ảnh cho staging) chỉ chạy khi `workflow_run` của CI kết thúc thành công
    trên `main`; checkout dùng đúng `head_sha` của lượt CI đó, không phải `github.sha`."""
    doc = _load_yaml(DEPLOY_YML)
    images = doc["jobs"]["images"]
    condition = str(images.get("if", ""))
    assert "workflow_run" in condition
    assert "conclusion == 'success'" in condition
    assert "head_branch == 'main'" in condition
    checkout_steps = [s for s in images["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert any(s.get("with", {}).get("ref") == "${{ github.event.workflow_run.head_sha }}" for s in checkout_steps)


def test_deploy_yml_environments_match_prompt() -> None:
    """`promote`/`production` chạm production; rollback chọn Environment theo `inputs.target`
    (production vẫn cần người duyệt kể cả qua rollback thủ công)."""
    doc = _load_yaml(DEPLOY_YML)
    assert doc["jobs"]["promote"]["environment"] == "production"
    assert doc["jobs"]["production"]["environment"] == "production"
    assert doc["jobs"]["rollback"]["environment"] == "${{ inputs.target }}"


def test_deploy_yml_no_forbidden_interpolation_in_run_steps() -> None:
    """Luật [9] mở rộng (review round 1, Nit SEC-04): **không** `${{ … }}` nào được nội suy
    thẳng vào `run:` — kể cả `steps.*.outputs.*` an toàn theo nội dung (chỉ hex) như trước, vì
    nó vẫn lách qua luật cấm gốc. Mọi giá trị động (secrets, inputs, github.event, steps
    outputs) phải đi qua `env:` của step/job rồi dùng `$BIẾN` trong `run:`."""
    doc = _load_yaml(DEPLOY_YML)
    for run_body in _walk_key(doc["jobs"], "run"):
        assert "${{" not in run_body, run_body


def test_deploy_yml_every_ssh_job_checks_tag_with_same_regex() -> None:
    """Review round 1 P2 (SEC-01): `promote` (dùng `TAG_NAME` để gắn tag ảnh GHCR) và
    `production` (ghép `TAG_NAME` vào lệnh ssh) phải kiểm tag bằng đúng regex của `rollback`
    (`:209` cũ) **trước** khi dùng — không chỉ job `rollback`."""
    doc = _load_yaml(DEPLOY_YML)
    rollback_check = _rollback_tag_check_script()
    m = re.search(r"=~\s*(\S+)\s*\]\]", rollback_check)
    assert m, rollback_check
    tag_regex = m.group(1)
    for job_name, var_name in (("promote", "TAG_NAME"), ("production", "TAG_NAME")):
        steps = doc["jobs"][job_name]["steps"]
        checks = [s.get("run", "") for s in steps if "=~" in s.get("run", "")]
        assert checks, f"{job_name}: không có bước kiểm tag bằng regex"
        for run_body in checks:
            assert tag_regex in run_body, f"{job_name}: regex lệch khỏi rollback — {run_body}"
            assert f'"${var_name}"' in run_body or f"${{{var_name}}}" in run_body, run_body


def test_deploy_yml_packages_write_only_on_images_and_promote() -> None:
    """`packages: write` chỉ ở hai job cần đẩy/gắn tag ảnh GHCR; job khác không xin quyền đó."""
    doc = _load_yaml(DEPLOY_YML)
    for name, job in doc["jobs"].items():
        perms = job.get("permissions", {}) or {}
        if name in ("images", "promote"):
            assert perms.get("packages") == "write", name
        else:
            assert perms.get("packages") != "write", name


def test_deploy_yml_workflow_level_permissions_contents_read_only() -> None:
    doc = _load_yaml(DEPLOY_YML)
    assert doc.get("permissions") == {"contents": "read"}


def test_deploy_yml_no_pull_request_target_no_continue_on_error() -> None:
    text = DEPLOY_YML.read_text(encoding="utf-8")
    doc = _load_yaml(DEPLOY_YML)
    assert "pull_request_target" not in _triggers(doc)
    assert "continue-on-error" not in text


def test_deploy_yml_concurrency_grouped_by_environment_never_cancels() -> None:
    """Review round 1 P2 (CON-01): `concurrency` phải ở MỨC JOB, khoá theo **môi trường thật**
    (không phải theo nhánh/tag kích hoạt) — nếu không, deploy production qua tag và rollback
    production qua `workflow_dispatch` rơi vào hai nhóm khác nhau và có thể chạy đồng thời
    trên cùng máy. `images`+`staging` → `deploy-staging`; `promote`+`production` →
    `deploy-production`; `rollback` → theo `inputs.target` thật. Không huỷ lượt đang chạy."""
    doc = _load_yaml(DEPLOY_YML)
    assert "concurrency" not in doc, "phải bỏ concurrency mức workflow, chuyển xuống mức job"
    expected_group = {
        "images": "deploy-staging",
        "staging": "deploy-staging",
        "promote": "deploy-production",
        "production": "deploy-production",
    }
    for job_name, group in expected_group.items():
        job = doc["jobs"][job_name]
        assert job["concurrency"]["group"] == group, job_name
        assert job["concurrency"]["cancel-in-progress"] is False, job_name
    rollback = doc["jobs"]["rollback"]
    assert rollback["concurrency"]["group"] == "deploy-${{ inputs.target }}"
    assert rollback["concurrency"]["cancel-in-progress"] is False
    # production và rollback(target=production) phải khoá CÙNG group để loại trừ lẫn nhau —
    # rollback không thể biết trước inputs.target lúc test tĩnh nên chỉ khẳng định mẫu group.
    assert expected_group["production"] == "deploy-production"


def test_restore_drill_yml_has_schedule_and_workflow_dispatch() -> None:
    doc = _load_yaml(RESTORE_DRILL_YML)
    triggers = _triggers(doc)
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers


def test_restore_drill_yml_builds_then_runs_drill() -> None:
    doc = _load_yaml(RESTORE_DRILL_YML)
    runs = _walk_key(doc["jobs"], "run")
    assert any("tools/ci/job.sh build --no-scan" in r for r in runs)
    assert any("deploy/scripts/drill.sh" in r for r in runs)


_WEB_BUILD_RE = re.compile(r"job\.sh build|web-context\.sh")
_APPFRONT_REPOSITORY = "mungvu999-ctrl/AppFront"


def test_every_workflow_building_web_checks_out_appfront() -> None:
    """K-38: job nào dựng ảnh web (`job.sh build` hoặc `web-context.sh`) phải có `env.APPFRONT_REPO` và một bước
    checkout `mungvu999-ctrl/AppFront` vào đúng `path` đó — thiếu thì `web-context.sh` rơi về `<cha>/AppFront` không
    tồn tại trên runner (restore-drill hỏng 8/8 lượt)."""
    seen = 0
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        doc = _load_yaml(path)
        for job_name, job in (doc.get("jobs") or {}).items():
            if not any(_WEB_BUILD_RE.search(r) for r in _walk_key(job, "run")):
                continue
            seen += 1
            where = f"{path.name}:{job_name}"
            env = {**(doc.get("env") or {}), **(job.get("env") or {})}
            appfront_repo = env.get("APPFRONT_REPO")
            assert appfront_repo, f"{where}: dựng ảnh web nhưng thiếu env.APPFRONT_REPO"
            checkout_paths = [
                step.get("with", {}).get("path")
                for step in job.get("steps", [])
                if str(step.get("uses", "")).startswith("actions/checkout@")
                and step.get("with", {}).get("repository") == _APPFRONT_REPOSITORY
            ]
            assert checkout_paths, f"{where}: thiếu bước checkout {_APPFRONT_REPOSITORY}"
            assert any(p and appfront_repo.rstrip("/").endswith(f"/{p}") for p in checkout_paths), (
                f"{where}: path checkout {checkout_paths} không khớp APPFRONT_REPO={appfront_repo}"
            )
    assert seen >= 3, "không thấy đủ job dựng ảnh web (ci, deploy, restore-drill) — regex trôi?"


# ---------------------------------------------------------------------------
# Đoạn bash kiểm tag của rollback — trích thẳng từ YAML, chạy thật (không chép regex)
# ---------------------------------------------------------------------------


def _rollback_tag_check_script() -> str:
    """Trả về thân `run:` của bước rollback chứa kiểm regex tag (`=~`) — trích từ YAML thật,
    để test không tự chép lại regex và trôi khỏi workflow thật."""
    doc = _load_yaml(DEPLOY_YML)
    for step in doc["jobs"]["rollback"]["steps"]:
        run_body = step.get("run", "")
        if "=~" in run_body:
            return run_body
    raise AssertionError("không tìm thấy bước kiểm tag (=~) trong job rollback")


def _run_rollback_tag_check(tmp_path: Path, tag: str) -> subprocess.CompletedProcess[str]:
    """Chạy đoạn bash thật với `TAG=<tag>`, không đặt bí mật SSH nào (nhánh hợp lệ sẽ tự
    ::notice:: và thoát 0; nhánh không khớp regex phải thoát khác 0 trước khi chạm bí mật)."""
    script = tmp_path / "rollback-tag-check.sh"
    script.write_text("set -euo pipefail\n" + _rollback_tag_check_script(), encoding="utf-8")
    env = {
        "TARGET": "staging",
        "TAG": tag,
        "STAGING_SSH_KEY": "",
        "STAGING_HOST": "",
        "STAGING_KNOWN_HOSTS": "",
        "PRODUCTION_SSH_KEY": "",
        "PRODUCTION_HOST": "",
        "PRODUCTION_KNOWN_HOSTS": "",
        "RUNNER_TEMP": str(tmp_path),
    }
    return run_script(script, env=env, timeout=30)


def test_rollback_tag_check_accepts_semver_tag(tmp_path: Path) -> None:
    result = _run_rollback_tag_check(tmp_path, "v1.2.3")
    assert result.returncode == 0, result.stdout + result.stderr


def test_rollback_tag_check_accepts_sha_tag(tmp_path: Path) -> None:
    result = _run_rollback_tag_check(tmp_path, "sha-0123456789ab")
    assert result.returncode == 0, result.stdout + result.stderr


def test_rollback_tag_check_rejects_incomplete_semver(tmp_path: Path) -> None:
    result = _run_rollback_tag_check(tmp_path, "v1.2")
    assert result.returncode != 0


def test_rollback_tag_check_rejects_non_hex_sha(tmp_path: Path) -> None:
    result = _run_rollback_tag_check(tmp_path, "sha-XYZ")
    assert result.returncode != 0


def test_rollback_tag_check_rejects_command_injection_attempt(tmp_path: Path) -> None:
    """Case lỗi nghiêm trọng: tag mang theo lệnh shell — chỉ được so bằng regex, không bao giờ
    thực thi phần đuôi độc hại."""
    result = _run_rollback_tag_check(tmp_path, "v1.2.3; rm -rf /")
    assert result.returncode != 0
