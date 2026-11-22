"""Test `deploy/scripts/healthcheck.sh` — bộ đếm lỗi liên tiếp, cảnh báo webhook, đĩa đầy."""

from __future__ import annotations

import json
from pathlib import Path

from deploy.scripts.tests.support import REPO_ROOT, HttpStub, Reply, fake_bin, read_log, run_script

SCRIPT = REPO_ROOT / "deploy" / "scripts" / "healthcheck.sh"
ALERT_SH = REPO_ROOT / "deploy" / "scripts" / "alert.sh"


def _fake_df(pct: int, bin_dir: Path) -> Path:
    """Lệnh `df` giả: dòng tiêu đề + một dòng dữ liệu với cột Capacity = pct%."""
    body = (
        f"echo 'Filesystem     1024-blocks      Used Available Capacity Mounted on'\n"
        f"printf '/dev/fake  10000000 9000000 1000000  {pct}%% %s\\n' \"$2\"\n"
    )
    return fake_bin(bin_dir, {"df": body})


def _run(
    tmp_path: Path, *, base_url: str, webhook_url: str = "", disk_pct: int = 10, log: Path | None = None
) -> object:
    bin_dir = _fake_df(disk_pct, tmp_path / "bin")
    env = {
        "APPBACK_BASE_URL": base_url,
        "APPBACK_STATE_DIR": str(tmp_path / "state"),
        "APPBACK_DISK_PATH": "/",
        "ALERT_WEBHOOK_URL": webhook_url,
    }
    return run_script(SCRIPT, [], env=env, bin_dir=bin_dir)


def test_healthcheck_first_failure_does_not_post(tmp_path: Path) -> None:
    """Lỗi lần đầu (/api/ready 503) → không POST, thoát 0."""
    with HttpStub({"/api/ready": Reply(503)}) as stub:
        result = _run(tmp_path, base_url=stub.url, webhook_url=f"{stub.url}/hook")
        assert result.returncode == 0, result.stderr
        assert stub.posts == []


def test_healthcheck_second_consecutive_failure_posts_once(tmp_path: Path) -> None:
    """Lỗi lần 2 liên tiếp → đúng một POST, JSON text==content, một dòng."""
    with HttpStub({"/api/ready": Reply(503)}) as stub:
        webhook = f"{stub.url}/hook"
        _run(tmp_path, base_url=stub.url, webhook_url=webhook)
        result = _run(tmp_path, base_url=stub.url, webhook_url=webhook)
        assert result.returncode == 0, result.stderr
        assert len(stub.posts) == 1
        path, body = stub.posts[0]
        assert path == "/hook"
        text = body.decode("utf-8")
        assert "\n" not in text.strip("\n")
        import json

        payload = json.loads(text)
        assert payload["text"] == payload["content"]


def test_healthcheck_success_resets_counter(tmp_path: Path) -> None:
    """Sau 2 lần lỗi, lần 3 thành công → bộ đếm về 0."""
    state_file = tmp_path / "state" / "health-failures"
    with HttpStub({"/api/ready": Reply(503)}) as stub:
        webhook = f"{stub.url}/hook"
        _run(tmp_path, base_url=stub.url, webhook_url=webhook)
        _run(tmp_path, base_url=stub.url, webhook_url=webhook)
    with HttpStub({"/api/ready": Reply(200)}) as stub_ok:
        result = _run(tmp_path, base_url=stub_ok.url, webhook_url=f"{stub_ok.url}/hook")
        assert result.returncode == 0, result.stderr
    assert state_file.read_text(encoding="utf-8") == "0"


def test_healthcheck_corrupt_counter_file_treated_as_zero(tmp_path: Path) -> None:
    """Review round 1 P3 (RES-03): `health-failures` không phải số (vd đĩa đầy ghi dở) →
    coi là 0, `$((failures + 1))` không lỗi số học, vẫn thoát 0 theo hợp đồng §2."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "health-failures").write_text("khong-phai-so", encoding="utf-8")
    with HttpStub({"/api/ready": Reply(503)}) as stub:
        result = _run(tmp_path, base_url=stub.url, webhook_url="")
    assert result.returncode == 0, result.stderr
    assert (state_dir / "health-failures").read_text(encoding="utf-8") == "1"


def test_healthcheck_disk_over_85_percent_alerts(tmp_path: Path) -> None:
    """Đĩa > 85% (df giả) → POST cảnh báo dù /api/ready đạt."""
    with HttpStub({"/api/ready": Reply(200)}) as stub:
        result = _run(tmp_path, base_url=stub.url, webhook_url=f"{stub.url}/hook", disk_pct=90)
        assert result.returncode == 0, result.stderr
        assert len(stub.posts) == 1
        import json

        payload = json.loads(stub.posts[0][1].decode("utf-8"))
        assert "đĩa" in payload["text"]


def test_healthcheck_webhook_failure_still_exits_0(tmp_path: Path) -> None:
    """Webhook trả 500 → chỉ cảnh báo, thoát 0."""
    with HttpStub({"/api/ready": Reply(503)}, post_reply=Reply(500)) as stub:
        webhook = f"{stub.url}/hook"
        _run(tmp_path, base_url=stub.url, webhook_url=webhook)
        result = _run(tmp_path, base_url=stub.url, webhook_url=webhook)
    assert result.returncode == 0, result.stderr
    assert "cảnh báo" in result.stderr


def test_healthcheck_without_webhook_url_no_post_but_warns(tmp_path: Path) -> None:
    """Không có ALERT_WEBHOOK_URL → không POST, vẫn in cảnh báo ra stderr."""
    with HttpStub({"/api/ready": Reply(503)}) as stub:
        _run(tmp_path, base_url=stub.url, webhook_url="")
        result = _run(tmp_path, base_url=stub.url, webhook_url="")
        assert result.returncode == 0, result.stderr
        assert "cảnh báo" in result.stderr
        assert stub.posts == []


def test_healthcheck_test_alert_posts_exactly_one_message_and_exits_0(tmp_path: Path) -> None:
    """L5-N03: `--test-alert` gọi curl đúng một lần (POST tin thử tới webhook), thoát 0, không đụng bộ đếm lỗi."""
    log = tmp_path / "fake.log"
    bin_dir = fake_bin(tmp_path / "bin", {"curl": "exit 0"})
    result = run_script(
        SCRIPT,
        ["--test-alert"],
        env={
            "FAKE_LOG": str(log),
            "APPBACK_STATE_DIR": str(tmp_path / "state"),
            "ALERT_WEBHOOK_URL": "http://webhook.invalid/hook",
        },
        bin_dir=bin_dir,
    )
    assert result.returncode == 0, result.stderr
    curls = [line for line in read_log(log) if line.startswith("curl ")]
    assert len(curls) == 1, curls
    assert "http://webhook.invalid/hook" in curls[0]
    assert "tin thử" in curls[0]
    assert not (tmp_path / "state" / "health-failures").exists()


def test_healthcheck_test_alert_real_post_has_text_equal_content(tmp_path: Path) -> None:
    """`--test-alert` POST thật vào HttpStub: JSON một dòng, `text` = `content`."""
    with HttpStub() as stub:
        result = run_script(
            SCRIPT,
            ["--test-alert"],
            env={"APPBACK_STATE_DIR": str(tmp_path / "state"), "ALERT_WEBHOOK_URL": f"{stub.url}/hook"},
        )
        assert result.returncode == 0, result.stderr
        assert len(stub.posts) == 1
        payload = json.loads(stub.posts[0][1].decode("utf-8"))
    assert payload["text"] == payload["content"]
    assert "tin thử" in payload["text"]


def test_alert_sh_test_alert_flag_and_usage(tmp_path: Path) -> None:
    """`alert.sh --test-alert` gửi một tin rồi thoát 0; không đối số hoặc đối số lạ → thoát 2 và không POST."""
    with HttpStub() as stub:
        env = {"ALERT_WEBHOOK_URL": f"{stub.url}/hook"}
        ok = run_script(ALERT_SH, ["--test-alert"], env=env)
        bad = run_script(ALERT_SH, [], env=env)
        assert ok.returncode == 0, ok.stderr
        assert bad.returncode == 2
        assert len(stub.posts) == 1


def test_alert_sh_without_webhook_only_warns_on_stderr(tmp_path: Path) -> None:
    """Không có `ALERT_WEBHOOK_URL`: `--test-alert` chỉ in cảnh báo ra stderr, thoát 0."""
    result = run_script(ALERT_SH, ["--test-alert"], env={"ALERT_WEBHOOK_URL": ""})
    assert result.returncode == 0, result.stderr
    assert "cảnh báo" in result.stderr


def test_healthcheck_sources_alert_sh_instead_of_defining_alert() -> None:
    """L5-N03: `alert()` chỉ định nghĩa ở `alert.sh`; `healthcheck.sh` `source` nó (K-37, K-39 dùng lại)."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "alert.sh" in text
    assert "\nalert() {" not in text
    assert "\nalert() {" in ALERT_SH.read_text(encoding="utf-8")
