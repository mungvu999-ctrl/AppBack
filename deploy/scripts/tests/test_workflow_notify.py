"""Quét tĩnh `notify.yml` và chạy thật thân của nó (B0-10 [8] "Quét notify.yml",
"Thân tin cảnh báo").

Hai đoạn `run:` được trích thẳng từ YAML rồi chạy bằng `bash` thật: điều kiện gửi
(bảng B0-10 [6]) và bước gửi JSON qua `HttpStub` — không chép lại logic vào test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from deploy.scripts.tests.support import REPO_ROOT, HttpStub, Reply, fake_bin, read_log, run_script

CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RESTORE_DRILL_YML = REPO_ROOT / ".github" / "workflows" / "restore-drill.yml"
NOTIFY_YML = REPO_ROOT / ".github" / "workflows" / "notify.yml"
COMMITS_YML = REPO_ROOT / ".github" / "workflows" / "commits.yml"
CODEQL_YML = REPO_ROOT / ".github" / "workflows" / "codeql.yml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


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


def _workflow_names(*paths: Path) -> set[str]:
    """`name:` thật của các workflow — nguồn duy nhất cho `workflow_run.workflows` (không chép tay)."""
    return {_load_yaml(p)["name"] for p in paths}


def test_notify_watches_exact_names_of_watched_workflows() -> None:
    """Danh sách `workflow_run.workflows` lấy đúng `name:` thật của từng workflow được báo (không chép tay)."""
    doc = _load_yaml(NOTIFY_YML)
    watched = set(_triggers(doc)["workflow_run"]["workflows"])
    assert watched == _workflow_names(CI_YML, DEPLOY_YML, RESTORE_DRILL_YML, COMMITS_YML, CODEQL_YML)


def test_notify_watches_every_workflow_running_on_push_to_main() -> None:
    """FIX-136: mọi workflow chạy trên `push: main` nằm trong danh sách của notify — tách một job ra workflow mới
    (vd `commits` khỏi `CI`) không được làm lỗi trên `main` thành im lặng."""
    watched = set(_triggers(_load_yaml(NOTIFY_YML))["workflow_run"]["workflows"])
    on_main_push = set()
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        doc = _load_yaml(path)
        push = _triggers(doc).get("push") or {}
        if "main" in push.get("branches", []):
            on_main_push.add(doc["name"])
    assert on_main_push, "không workflow nào chạy trên push: main"
    assert on_main_push <= watched, on_main_push - watched


def test_notify_permissions_empty_and_no_checkout() -> None:
    """Job notify không quyền token và không checkout: lượt được báo có thể từ PR fork, không được chạy mã của nó."""
    doc = _load_yaml(NOTIFY_YML)
    assert doc.get("permissions") == {}
    uses_values = _walk_key(doc["jobs"], "uses")
    assert not any(u.startswith("actions/checkout@") for u in uses_values)


def test_notify_has_workflow_dispatch_with_message_input() -> None:
    """L5-N03: `workflow_dispatch` với input `message` để gửi tin thử (`gh workflow run notify.yml -f message=…`)."""
    triggers = _triggers(_load_yaml(NOTIFY_YML))
    assert "workflow_dispatch" in triggers
    assert "message" in (triggers["workflow_dispatch"] or {}).get("inputs", {})


def test_notify_job_permission_is_only_issues_write() -> None:
    """L5-N03: quyền token chỉ ở mức job và chỉ `issues: write` (issue dự phòng); workflow-level giữ `{}`."""
    doc = _load_yaml(NOTIFY_YML)
    assert doc.get("permissions") == {}
    assert doc["jobs"]["notify"].get("permissions") == {"issues": "write"}


def test_notify_workflow__secret_only_from_environment() -> None:
    """SEC-061: job đọc `secrets.*` (trừ `GITHUB_TOKEN`) phải khai `environment:` — secret cấp repository thì mọi
    workflow, kể cả chạy mã từ nhánh khác, đều đọc được URL webhook cảnh báo."""
    doc = _load_yaml(NOTIFY_YML)
    for name, job in doc["jobs"].items():
        used = set(re.findall(r"secrets\.(\w+)", json.dumps(job))) - {"GITHUB_TOKEN"}
        assert not used or job.get("environment"), f"job {name} đọc secret cấp repository: {sorted(used)}"


def test_notify_run_steps_never_interpolate_event_directly() -> None:
    """Mọi giá trị `github.event.workflow_run.*` phải đi qua `env:`, không nội suy thẳng vào `run:`
    (lượt được báo có thể tới từ PR fork, mang secret của job notify)."""
    doc = _load_yaml(NOTIFY_YML)
    for run_body in _walk_key(doc["jobs"], "run"):
        assert "${{ github.event." not in run_body, run_body


# ---------------------------------------------------------------------------
# Điều kiện gửi — chạy thật đoạn bash quyết định, không chép bảng case vào Python
# ---------------------------------------------------------------------------


def _decision_script() -> str:
    """Thân `run:` của bước quyết định gửi (chứa `case "$WORKFLOW_NAME"`), trích từ YAML thật."""
    doc = _load_yaml(NOTIFY_YML)
    for step in doc["jobs"]["notify"]["steps"]:
        run_body = step.get("run", "")
        if 'case "$WORKFLOW_NAME"' in run_body:
            return run_body
    raise AssertionError("không tìm thấy bước quyết định gửi (case $WORKFLOW_NAME)")


def _run_decision(
    tmp_path: Path, *, workflow: str, conclusion: str, head_branch: str, event_name: str = "workflow_run"
) -> bool:
    """Chạy thật đoạn quyết định với biến `env:` như GitHub đặt; trả `True` nếu bước đặt `SEND_ALERT=1`
    vào `$GITHUB_ENV` (đúng cơ chế GitHub Actions dùng để truyền biến sang bước sau)."""
    github_env = tmp_path / "github_env"
    github_env.write_text("", encoding="utf-8")
    script = tmp_path / "decision.sh"
    script.write_text("set -euo pipefail\n" + _decision_script(), encoding="utf-8")
    result = run_script(
        script,
        env={
            "WORKFLOW_NAME": workflow,
            "CONCLUSION": conclusion,
            "HEAD_BRANCH": head_branch,
            "EVENT_NAME": event_name,
            "GITHUB_ENV": str(github_env),
        },
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return "SEND_ALERT=1" in github_env.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("workflow", "conclusion", "head_branch", "expected_send"),
    [
        ("CI", "failure", "main", True),
        ("CI", "failure", "feature/x", False),
        ("CI", "success", "main", False),
        ("Deploy", "success", "main", True),
        ("Deploy", "failure", "main", True),
        ("Deploy", "cancelled", "main", False),
        ("Restore drill", "failure", "main", True),
        ("Restore drill", "success", "main", False),
        ("Commits", "failure", "main", True),
        ("Commits", "failure", "feature/x", False),
        ("Commits", "success", "main", False),
        ("CodeQL", "failure", "main", True),
        ("CodeQL", "failure", "feature/x", False),
        ("CodeQL", "success", "main", False),
    ],
)
def test_notify_send_decision_matches_prompt_table(
    tmp_path: Path, workflow: str, conclusion: str, head_branch: str, expected_send: bool
) -> None:
    """Điều kiện gửi chạy thật bằng bash khớp bảng B0-10 [6] (CI/Commits/CodeQL: failure trên main)."""
    assert _run_decision(tmp_path, workflow=workflow, conclusion=conclusion, head_branch=head_branch) is expected_send


def test_notify_dispatch_always_sends(tmp_path: Path) -> None:
    """`workflow_dispatch` (tin thử) luôn gửi, dù `workflow_run.*` trống."""
    assert _run_decision(tmp_path, workflow="", conclusion="", head_branch="", event_name="workflow_dispatch") is True


# ---------------------------------------------------------------------------
# Bước gửi — HttpStub thật, thiếu webhook, máy chủ 500
# ---------------------------------------------------------------------------

_SHA = "0123456789abdeadbeef"


def _send_script() -> str:
    """Thân `run:` của bước gửi (POST `ALERT_WEBHOOK_URL`), trích từ YAML thật."""
    doc = _load_yaml(NOTIFY_YML)
    for step in doc["jobs"]["notify"]["steps"]:
        run_body = step.get("run", "")
        if "ALERT_WEBHOOK_URL" in run_body and "curl" in run_body:
            return run_body
    raise AssertionError("không tìm thấy bước gửi (curl ALERT_WEBHOOK_URL)")


def _run_send(
    tmp_path: Path, *, webhook_url: str, event_name: str = "workflow_run", dispatch_message: str = ""
) -> Any:
    """Chạy thật bước gửi của notify.yml với biến `env:` của một lượt Deploy hỏng (hoặc tin thử) và webhook cho trước.

    `$GITHUB_ENV` và `$RUNNER_TEMP` trỏ vào `tmp_path` để đọc lại cờ dự phòng và tệp tin.
    """
    (tmp_path / "github_env").write_text("", encoding="utf-8")
    script = tmp_path / "send.sh"
    script.write_text("set -euo pipefail\n" + _send_script(), encoding="utf-8")
    return run_script(
        script,
        broken_python3=False,  # bước của workflow GitHub: runner có python3 thật
        env={
            "EVENT_NAME": event_name,
            "DISPATCH_MESSAGE": dispatch_message,
            "WORKFLOW_NAME": "Deploy",
            "CONCLUSION": "failure",
            "HEAD_BRANCH": "main",
            "HEAD_SHA": _SHA,
            "HTML_URL": "https://github.com/mungvu2004/AppBack/actions/runs/1",
            "ALERT_WEBHOOK_URL": webhook_url,
            "GITHUB_ENV": str(tmp_path / "github_env"),
            "RUNNER_TEMP": str(tmp_path),
        },
        timeout=30,
    )


def _fallback_flag(tmp_path: Path) -> bool:
    """`True` nếu bước gửi đã đặt `ALERT_FALLBACK=1` vào `$GITHUB_ENV` (cần mở issue dự phòng)."""
    return "ALERT_FALLBACK=1" in (tmp_path / "github_env").read_text(encoding="utf-8")


def test_notify_send_posts_json_with_matching_text_and_content(tmp_path: Path) -> None:
    """Tin gửi là JSON có `text` = `content`, một dòng, mang tên workflow, sha 12 ký tự và đường dẫn run."""
    with HttpStub() as stub:
        result = _run_send(tmp_path, webhook_url=stub.url)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(stub.posts) == 1
        _path, body = stub.posts[0]
        payload = json.loads(body)
    assert payload["text"] == payload["content"]
    message = payload["text"]
    assert "\n" not in message
    assert "Deploy" in message
    assert _SHA[:12] in message
    assert "https://github.com/mungvu2004/AppBack/actions/runs/1" in message


def test_notify_send_ok_does_not_request_fallback(tmp_path: Path) -> None:
    """Webhook nhận tin (200) thì không bật cờ dự phòng."""
    with HttpStub() as stub:
        result = _run_send(tmp_path, webhook_url=stub.url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not _fallback_flag(tmp_path)


def test_notify_send_missing_webhook_url_warns_and_requests_fallback(tmp_path: Path) -> None:
    """L5-N03: chưa cấu hình webhook thì `::warning::` (không còn `::notice::` xanh), không POST, thoát 0 và bật cờ
    mở issue dự phòng; tin được lưu để bước issue dùng lại."""
    result = _run_send(tmp_path, webhook_url="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout
    assert "::notice::" not in result.stdout
    assert _fallback_flag(tmp_path)
    assert "Deploy" in (tmp_path / "alert-message.txt").read_text(encoding="utf-8")


def test_notify_send_server_error_warns_requests_fallback_and_exits_zero(tmp_path: Path) -> None:
    """Webhook trả 500 thì `::warning::`, bật cờ dự phòng, thoát 0 — lỗi kênh báo không che kết quả workflow gốc."""
    with HttpStub(post_reply=Reply(status=500)) as stub:
        result = _run_send(tmp_path, webhook_url=stub.url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout
    assert _fallback_flag(tmp_path)


def test_notify_dispatch_posts_the_input_message(tmp_path: Path) -> None:
    """Tin thử (`workflow_dispatch`) gửi đúng nội dung input `message`, không phải câu mẫu của workflow_run."""
    with HttpStub() as stub:
        result = _run_send(
            tmp_path, webhook_url=stub.url, event_name="workflow_dispatch", dispatch_message="thu canh bao"
        )
        payload = json.loads(stub.posts[0][1])
    assert result.returncode == 0, result.stdout + result.stderr
    assert payload["text"] == payload["content"] == "thu canh bao"


# ---------------------------------------------------------------------------
# Bước issue dự phòng — `gh` giả ghi log, chạy thật thân `run:` trích từ YAML
# ---------------------------------------------------------------------------


def _fallback_script() -> str:
    """Thân `run:` của bước issue dự phòng (chứa `gh issue`), trích từ YAML thật."""
    doc = _load_yaml(NOTIFY_YML)
    for step in doc["jobs"]["notify"]["steps"]:
        if "gh issue" in step.get("run", ""):
            assert "ALERT_FALLBACK" in step.get("if", ""), "bước issue phải chỉ chạy khi cờ dự phòng bật"
            return str(step["run"])
    raise AssertionError("không tìm thấy bước issue dự phòng (gh issue)")


def _run_fallback(tmp_path: Path, *, existing_issue: str, workflow: str = "CI") -> list[str]:
    """Chạy bước issue dự phòng với `gh` giả (`issue list` in `existing_issue`); trả các dòng log lệnh `gh`."""
    log = tmp_path / "fake.log"
    bin_dir = fake_bin(
        tmp_path / "bin",
        {"gh": 'if [ "$1 $2" = "issue list" ]; then printf "%s" "$FAKE_EXISTING_ISSUE"; fi'},
    )
    (tmp_path / "alert-message.txt").write_text("Workflow CI kết thúc failure", encoding="utf-8")
    script = tmp_path / "fallback.sh"
    script.write_text("set -euo pipefail\n" + _fallback_script(), encoding="utf-8")
    result = run_script(
        script,
        bin_dir=bin_dir,
        env={
            "FAKE_LOG": str(log),
            "FAKE_EXISTING_ISSUE": existing_issue,
            "EVENT_NAME": "workflow_run",
            "WORKFLOW_NAME": workflow,
            "RUNNER_TEMP": str(tmp_path),
        },
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return list(read_log(log))


def test_notify_fallback_creates_alert_issue_when_none_open(tmp_path: Path) -> None:
    """Chưa có issue `alert` mở cho workflow → `gh issue create --label alert`, tiêu đề mang tên workflow."""
    lines = _run_fallback(tmp_path, existing_issue="")
    creates = [ln for ln in lines if ln.startswith("gh issue create")]
    assert len(creates) == 1, lines
    assert "--label alert" in creates[0]
    assert "Cảnh báo: CI" in creates[0]
    assert not any(ln.startswith("gh issue comment") for ln in lines)


def test_notify_fallback_comments_on_existing_issue_instead_of_new_one(tmp_path: Path) -> None:
    """Đã có issue `alert` mở cùng tên workflow → bình luận vào đó, không tạo thêm (CI đỏ liên tục không spam)."""
    lines = _run_fallback(tmp_path, existing_issue="42")
    comments = [ln for ln in lines if ln.startswith("gh issue comment")]
    assert len(comments) == 1, lines
    assert comments[0].startswith("gh issue comment 42 ")
    assert not any(ln.startswith("gh issue create") for ln in lines)


def test_readme_names_the_alerts_environment_for_the_webhook_secret() -> None:
    """NO-332: hướng dẫn vận hành xếp `ALERT_WEBHOOK_URL` vào Environment `alerts`, không phải secret cấp repository
    (khớp `environment: alerts` của notify.yml)."""
    readme = (REPO_ROOT / "deploy" / "scripts" / "README.md").read_text(encoding="utf-8")
    secret_lines = [line for line in readme.splitlines() if "ALERT_WEBHOOK_URL" in line and "Secret GitHub" in line]
    assert not secret_lines, secret_lines
    assert "Environment `alerts`" in readme
