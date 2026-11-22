"""Test `deploy/scripts/healthcheck.sh` — bộ đếm lỗi liên tiếp, cảnh báo webhook, đĩa đầy."""

from __future__ import annotations

import json
from pathlib import Path

from deploy.scripts.tests.support import (
    FAKE_COMPOSE_SERVICES,
    REPO_ROOT,
    HttpStub,
    Reply,
    appback_dir,
    fake_bin,
    read_log,
    run_script,
)
from deploy.tests.compose_reader import resolve_all_services

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


# K-39 — giám sát dịch vụ và hàng đợi. `docker` giả: `compose ps -a` in tệp $HC_PS (hỏng nếu
# HC_PS_FAIL=1); `exec redis-broker … LLEN <hàng>` in $HC_QUEUES/<hàng> (mặc định 0); `exec <dịch vụ>
# … inspect active` in $HC_ACTIVE (mặc định: không việc nào). Mỗi lệnh ghi một dòng vào $FAKE_LOG.
_HC_DOCKER = (
    FAKE_COMPOSE_SERVICES
    + r"""
if [[ "$1 $2 $3" == "compose ps -a" ]]; then
  [[ "${HC_PS_FAIL:-0}" == "1" ]] && exit 1
  cat "$HC_PS"
  exit 0
fi
if [[ "$1 $2 $3 $4" == "compose exec -T redis-broker" ]]; then
  q="${*: -1}"
  cat "$HC_QUEUES/$q" 2>/dev/null || echo 0
  exit 0
fi
if [[ "$1 $2 $3" == "compose exec -T" ]]; then
  if [[ -n "${HC_ACTIVE:-}" ]]; then printf '%s\n' "$HC_ACTIVE"; else printf '%s\n' '{"celery@x": []}'; fi
  exit 0
fi
exit 0
"""
)
_ALL_OK = (
    "postgres running healthy\nredis-broker running healthy\nredis-cache running healthy\n"
    "api running healthy\nworker running healthy\nbeat running healthy\nweb running healthy\n"
    "ml running healthy\nml-train running healthy\n"
)
_READY = {"/api/ready": Reply(200), "/ping": Reply(200)}


class _Host:
    """Máy đích giả cho healthcheck.sh: bản đang chạy, docker/df giả, thư mục trạng thái dùng chung
    giữa các lượt (mô phỏng timer chạy liên tiếp)."""

    def __init__(self, tmp_path: Path, stub: HttpStub, **extra: str) -> None:
        """Dựng cấu trúc K-35 (có release.env) + docker giả; `extra` thêm biến môi trường."""
        self.stub = stub
        self.ps = tmp_path / "ps.txt"
        self.queues = tmp_path / "queues"
        self.queues.mkdir()
        self.log = tmp_path / "docker.log"
        self.bin = fake_bin(tmp_path / "bin", {"docker": _HC_DOCKER})
        _fake_df(10, self.bin)
        self.env = appback_dir(tmp_path / "opt-appback", running="sha-0000000000a1") | {
            "APPBACK_BASE_URL": stub.url,
            "APPBACK_STATE_DIR": str(tmp_path / "state"),
            "ALERT_WEBHOOK_URL": f"{stub.url}/hook",
            "COMPOSE_PROFILES": "ml",
            "HC_PS": str(self.ps),
            "HC_QUEUES": str(self.queues),
            "FAKE_LOG": str(self.log),
            **extra,
        }
        self.set_ps(_ALL_OK)

    def set_ps(self, text: str) -> None:
        """Nội dung `docker compose ps -a` cho lượt tới."""
        self.ps.write_text(text, encoding="utf-8")

    def run(self, **extra: str) -> list[str]:
        """Một lượt healthcheck.sh (phải thoát 0); trả các tin cảnh báo MỚI gửi trong lượt này."""
        before = len(self.stub.posts)
        result = run_script(SCRIPT, [], env=self.env | extra, bin_dir=self.bin)
        assert result.returncode == 0, result.stderr
        return [json.loads(body)["text"] for _, body in self.stub.posts[before:]]


def test_healthcheck_unhealthy_ml_alerts_once_then_reminds_then_resolves(tmp_path: Path) -> None:
    """ml `unhealthy` → đúng một tin; lượt sau vẫn hỏng → im (chưa tới ALERT_REPEAT_S=3600);
    ALERT_REPEAT_S=0 → nhắc lại "(vẫn còn)"; hồi phục → đúng một tin "đã hồi phục", lượt sau im."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub)
        host.set_ps(_ALL_OK.replace("ml running healthy", "ml running unhealthy"))
        assert host.run() == ["dịch vụ ml hỏng (trạng thái: running, unhealthy)"]
        assert host.run() == []
        assert host.run(ALERT_REPEAT_S="0") == ["dịch vụ ml hỏng (trạng thái: running, unhealthy) (vẫn còn)"]
        host.set_ps(_ALL_OK)
        assert host.run() == ["đã hồi phục: dịch vụ ml chạy lại"]
        assert host.run() == []


def test_healthcheck_paused_or_missing_service_alerts(tmp_path: Path) -> None:
    """`docker pause` (giả treo) → trạng thái paused; container vắng (chết, bị xoá) → "vắng"."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub)
        host.set_ps(_ALL_OK.replace("ml running", "ml paused").replace("worker running healthy\n", ""))
        alerts = host.run()
    assert sorted(alerts) == ["dịch vụ ml hỏng (trạng thái: paused, healthy)", "dịch vụ worker hỏng (trạng thái: vắng)"]


def test_healthcheck_ping_url_only_on_clean_run(tmp_path: Path) -> None:
    """APPBACK_PING_URL được gọi khi lượt không thấy vấn đề nào; có vấn đề thì không gọi (dead man's
    switch ngoài máy tự báo khi im)."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub, APPBACK_PING_URL=f"{stub.url}/ping")
        assert host.run() == []
        assert stub.gets.count("/ping") == 1
        host.set_ps(_ALL_OK.replace("beat running healthy", "beat exited"))
        host.run()
        assert stub.gets.count("/ping") == 1


def test_healthcheck_stuck_queue_alerts_after_n_runs_without_active_task(tmp_path: Path) -> None:
    """`ml.infer` 5 việc qua 3 lượt, ml không chạy việc nào → cảnh báo hàng kẹt ở lượt 3 (không
    sớm hơn); hàng giảm → "đã hồi phục". `inspect active` chỉ gọi khi đủ số lượt."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub)
        (host.queues / "ml.infer").write_text("5\n", encoding="utf-8")
        assert host.run() == []
        assert host.run() == []
        assert not any("inspect" in ln for ln in read_log(host.log))
        assert host.run() == ["hàng ml.infer kẹt: 5 việc chờ, không giảm qua 3 lượt, ml không chạy việc nào"]
        (host.queues / "ml.infer").write_text("4\n", encoding="utf-8")
        assert host.run() == ["đã hồi phục: hàng ml.infer chạy lại"]


def test_healthcheck_busy_queue_with_active_task_is_not_stuck(tmp_path: Path) -> None:
    """Hàng không giảm nhưng dịch vụ đang chạy việc (huấn luyện dài) → không cảnh báo."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub, HC_ACTIVE='{"celery@x": [{"id": "t1", "name": "train"}]}')
        (host.queues / "ml.training").write_text("2\n", encoding="utf-8")
        alerts = [a for _ in range(4) for a in host.run()]
    assert alerts == []


def test_healthcheck_skips_queues_of_disabled_profiles(tmp_path: Path) -> None:
    """Không bật profile ml → không đọc hàng ml.infer/ml.training, không kiểm dịch vụ ml."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub, COMPOSE_PROFILES="")
        host.set_ps(_ALL_OK.replace("ml running healthy\nml-train running healthy\n", ""))
        assert host.run() == []
    lines = list(read_log(host.log))
    assert any(ln.endswith(" default") for ln in lines), lines
    assert not any(ln.endswith((" ml.infer", " ml.training")) for ln in lines), lines


def test_healthcheck_compose_failure_alerts_and_still_exits_0(tmp_path: Path) -> None:
    """`docker compose ps` hỏng (Docker treo) → một cảnh báo compose, không cảnh báo từng dịch vụ."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub, HC_PS_FAIL="1")
        alerts = host.run()
    assert len(alerts) == 1
    assert alerts[0].startswith("không đọc được trạng thái compose")


def test_healthcheck_skips_compose_checks_before_first_release(tmp_path: Path) -> None:
    """Chưa có release.env (chưa deploy) → bỏ qua kiểm dịch vụ/hàng, không gọi docker, vẫn thoát 0."""
    with HttpStub(_READY) as stub:
        host = _Host(tmp_path, stub)
        (Path(host.env["APPBACK_DIR"]) / "release.env").unlink()
        assert host.run() == []
    assert not [ln for ln in read_log(host.log) if ln.startswith("docker ")]


def test_ml_services_have_celery_ping_healthcheck() -> None:
    """K-39: ml, ml-train, ml-gpu (sau khi giải `extends` của prod.yml) có healthcheck ping Celery của
    chính container; start_period đủ cho nạp mô hình, interval 60 s (ping tốn ~1 nhân)."""
    services = resolve_all_services(REPO_ROOT / "deploy" / "compose" / "prod.yml")
    for name in ("ml", "ml-train", "ml-gpu"):
        hc = services[name].get("healthcheck") or {}
        assert "inspect ping -d celery@$$HOSTNAME" in " ".join(hc.get("test", [])), name
        assert hc.get("interval") == "60s", name
        assert hc.get("start_period") == "120s", name


def test_health_timer_runs_every_minute() -> None:
    """K-39: timer 1 phút — ml chết/treo có tin trong ≤ 5 phút (3 lần ping hỏng ~3 phút + 1 phút)."""
    timer = (REPO_ROOT / "deploy" / "scripts" / "systemd" / "appback-health.timer").read_text(encoding="utf-8")
    assert "\nOnUnitActiveSec=1min\n" in timer
