"""Test `deploy/scripts/deploy.sh` — bó phát hành, nạp biến, dựng hạ tầng, thứ tự đổi
container, hỏng migrate/smoke/timeout (K-35, L5-N01, L5-N07)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path

from deploy.scripts.tests.support import (
    REPO_ROOT,
    HttpStub,
    Reply,
    appback_dir,
    fake_docker,
    read_log,
    run_script,
)
from deploy.tests.compose_reader import load_yaml

SCRIPT = REPO_ROOT / "deploy" / "scripts" / "deploy.sh"
LIB = REPO_ROOT / "deploy" / "scripts" / "lib.sh"
NEW = "sha-0000000000b2"
OLD = "sha-0000000000a1"

SMOKE_ROUTES = {
    "/api/health": Reply(200),
    "/api/ready": Reply(200),
    "/": Reply(200, headers={"Content-Security-Policy": "default-src 'self'"}),
    "/draco/draco_decoder.wasm": Reply(200, body=b"wasm"),
    "/api/nope": Reply(404, body=b'{"code":"NOT_FOUND"}'),
}


def _run(
    tmp_path: Path,
    *,
    args: tuple[str, ...] = (NEW,),
    running: str | None = OLD,
    releases: tuple[str, ...] = (OLD, NEW),
    routes: dict[str, Reply] | None = None,
    env_lines: str = "APP_ENV=test\n",
    extra: dict[str, str] | None = None,
    timeout: float = 60,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path, Path]:
    """Dựng /opt/appback giả + docker giả, chạy deploy.sh; trả (kết quả, log docker, APPBACK_DIR, state).

    Bản `running` đang chạy (release.env + current_tag), mỗi tag trong `releases` có bó kèm bản
    chép `deploy/scripts` thật — deploy.sh hỏng gọi đúng rollback.sh của bản trước.
    """
    root = tmp_path / "opt-appback"
    env = appback_dir(root, running=running, releases=releases, env_lines=env_lines, with_scripts=True)
    bin_dir, state = fake_docker(tmp_path)
    log = tmp_path / "docker.log"
    with HttpStub(SMOKE_ROUTES if routes is None else routes) as stub:
        env |= {
            "APPBACK_BASE_URL": stub.url,
            "APPBACK_HEALTH_TIMEOUT_S": "5",
            "APPBACK_HEALTH_POLL_S": "1",
            "DOCKER_STATE": str(state),
            "FAKE_LOG": str(log),
            **(extra or {}),
        }
        result = run_script(SCRIPT, list(args), env=env, bin_dir=bin_dir, timeout=timeout)
    return result, list(read_log(log)), root, state


def _first(lines: list[str], needle: str) -> int:
    """Chỉ số dòng đầu tiên chứa `needle` (ném StopIteration nếu không có — test đỏ rõ)."""
    return next(i for i, ln in enumerate(lines) if needle in ln)


def test_deploy_dry_run_prints_order_and_only_reads_compose_config(tmp_path: Path) -> None:
    """--dry-run in đúng thứ tự pull → dựng hạ tầng → migrate → publish → scale api=2 → healthy
    → chờ DNS → xoá cũ → đổi dịch vụ ứng dụng (worker, beat, web) → smoke; docker chỉ được gọi
    lệnh đọc `compose config --services`; không ghi trạng thái nào (K-35, L5-N01)."""
    result, lines, root, _ = _run(tmp_path, args=(NEW, "--dry-run"))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    order = [
        "compose pull",
        "up -d --wait --wait-timeout 5 postgres redis-broker redis-cache",
        "run --rm migrate",
        "apps.api.library.cli publish",
        "scale api=2",
        "healthy",
        "dịch lại DNS",
        "xoá",
        "up -d --no-deps worker beat web",
        "smoke",
    ]
    positions = [out.find(word) for word in order]
    assert all(p != -1 for p in positions), out
    assert positions == sorted(positions), out
    assert lines == ["docker compose config --services"]
    assert (root / "current_tag").read_text(encoding="utf-8") == OLD
    assert (root / "release.env").read_text(encoding="utf-8") == f"IMAGE_TAG={OLD}\n"
    assert not (root / "previous_tag").exists()
    assert not (root / "current").exists()


def test_deploy_dry_run_profiles_add_minio_bootstrap_and_ml_services(tmp_path: Path) -> None:
    """COMPOSE_PROFILES=minio,ml → dựng thêm minio (chờ healthy) + minio-init, đổi thêm ml,
    ml-train; không bật thì không có (L5-N01, K-35 D3)."""
    on, _, _, _ = _run(tmp_path / "on", args=(NEW, "--dry-run"), extra={"COMPOSE_PROFILES": "minio,ml"})
    assert on.returncode == 0, on.stderr
    assert "up -d --wait --wait-timeout 5 minio\n" in on.stdout
    assert "compose run --rm minio-init" in on.stdout
    assert "up -d --no-deps worker beat web ml ml-train" in on.stdout
    assert on.stdout.find("minio-init") < on.stdout.find("run --rm migrate")

    off, _, _, _ = _run(tmp_path / "off", args=(NEW, "--dry-run"))
    assert off.returncode == 0, off.stderr
    assert "minio" not in off.stdout
    assert "ml-train" not in off.stdout


def test_deploy_reads_compose_profiles_from_appback_env(tmp_path: Path) -> None:
    """`ssh … deploy.sh` không có EnvironmentFile: COMPOSE_PROFILES trong appback.env (có nháy,
    dòng CRLF) vẫn phải được đọc — không thì ml không bao giờ được đổi (K-35 D2/D3)."""
    result, _, _, _ = _run(tmp_path, args=(NEW, "--dry-run"), env_lines='APP_ENV=test\r\nCOMPOSE_PROFILES="ml"\r\n')
    assert result.returncode == 0, result.stderr
    assert "up -d --no-deps worker beat web ml ml-train" in result.stdout


def test_deploy_success_runs_in_order_and_records_release(tmp_path: Path) -> None:
    """Chạy thật với docker giả: compose của releases/<tag>, biến từ appback.env + release.env,
    thứ tự pull → hạ tầng → migrate → đổi api → đổi dịch vụ; sau smoke ghi release.env, symlink
    current → releases/<tag>, current_tag, previous_tag (K-35, L5-N07)."""
    result, lines, root, state = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    idx = [
        _first(lines, "compose pull"),
        _first(lines, "up -d --wait"),
        _first(lines, "run --rm migrate"),
        _first(lines, "scale api=2"),
        _first(lines, "docker inspect"),
        lines.index("docker rm old1"),
        _first(lines, "scale api=1"),
        lines.index("docker compose up -d --no-deps worker beat web"),
    ]
    assert idx == sorted(idx), lines
    compose_file, env_files = (state / "pull_env").read_text(encoding="utf-8").splitlines()
    assert compose_file == str(root / "releases" / NEW / "deploy" / "compose" / "prod.yml")
    assert env_files == f"{root / 'appback.env'},{root / 'release.env'}"
    assert (root / "release.env").read_text(encoding="utf-8") == f"IMAGE_TAG={NEW}\n"
    assert os.readlink(root / "current") == f"releases/{NEW}"
    assert (root / "current_tag").read_text(encoding="utf-8") == NEW
    assert (root / "previous_tag").read_text(encoding="utf-8") == OLD


def test_deploy_first_run_on_empty_host_bootstraps_and_records(tmp_path: Path) -> None:
    """Máy trống (chưa release.env, chưa current_tag): chỉ nạp appback.env, vẫn dựng hạ tầng
    rồi tạo dịch vụ, cuối cùng ghi release.env và symlink — `deploy.sh <tag>` là lệnh duy nhất
    (L5-N01)."""
    result, lines, root, state = _run(tmp_path, running=None, releases=(NEW,))
    assert result.returncode == 0, result.stderr
    assert _first(lines, "up -d --wait") < _first(lines, "run --rm migrate")
    assert (state / "pull_env").read_text(encoding="utf-8").splitlines()[1] == str(root / "appback.env")
    assert (root / "release.env").read_text(encoding="utf-8") == f"IMAGE_TAG={NEW}\n"
    assert not (root / "previous_tag").exists()


def test_deploy_missing_release_bundle_exits_2_before_docker(tmp_path: Path) -> None:
    """Chưa giải nén releases/<tag> → thoát 2 kèm thông báo, không gọi docker (cả --dry-run)."""
    for args in ((NEW,), (NEW, "--dry-run")):
        result, lines, _, _ = _run(tmp_path / args[-1].strip("-"), args=args, releases=(OLD,))
        assert result.returncode == 2, result.stderr
        assert "chưa có bó phát hành" in result.stderr
        assert lines == []


def test_deploy_missing_appback_env_exits_2_before_docker(tmp_path: Path) -> None:
    """Thiếu appback.env → thoát 2 trước khi gọi docker (compose sẽ hỏng ở `${VAR:?}`)."""
    result, lines, _, _ = _run(tmp_path, extra={"APPBACK_ENV_FILE": str(tmp_path / "khong-co.env")})
    assert result.returncode == 2
    assert "thiếu" in result.stderr
    assert lines == []


def test_deploy_image_tag_in_appback_env_exits_2(tmp_path: Path) -> None:
    """appback.env có `IMAGE_TAG=` → thoát 2: tag chỉ được nằm ở release.env (L5-N07)."""
    result, lines, _, _ = _run(tmp_path, env_lines="APP_ENV=test\n  export IMAGE_TAG=dev\n")
    assert result.returncode == 2
    assert "IMAGE_TAG" in result.stderr
    assert lines == []


def test_deploy_caller_compose_file_and_env_files_are_respected(tmp_path: Path) -> None:
    """Người gọi tự đặt COMPOSE_FILE/COMPOSE_ENV_FILES (kiểm tay, drill) → không đòi bó, không
    đòi appback.env, giữ nguyên giá trị đã đặt."""
    result, _, root, state = _run(
        tmp_path,
        releases=(),
        extra={
            "COMPOSE_FILE": "/srv/tu-dat/prod.yml",
            "COMPOSE_ENV_FILES": "/srv/tu-dat/a.env",
            "APPBACK_ENV_FILE": str(tmp_path / "khong-co.env"),
        },
    )
    assert result.returncode == 0, result.stderr
    pulled = (state / "pull_env").read_text(encoding="utf-8").splitlines()
    assert pulled == ["/srv/tu-dat/prod.yml", "/srv/tu-dat/a.env"]
    assert (root / "current_tag").read_text(encoding="utf-8") == NEW


def test_deploy_bootstrap_failure_stops_before_migrate(tmp_path: Path) -> None:
    """Dựng hạ tầng hỏng → thoát 1, không migrate, không đổi api, trạng thái giữ nguyên (L5-N01)."""
    result, lines, root, _ = _run(tmp_path, extra={"DOCKER_INFRA_EXIT": "1"})
    assert result.returncode == 1
    assert "hỏng dựng hạ tầng" in result.stderr
    assert not any("migrate" in ln or "scale" in ln for ln in lines)
    assert (root / "current_tag").read_text(encoding="utf-8") == OLD


def test_deploy_migrate_failure_does_not_touch_containers(tmp_path: Path) -> None:
    """Migrate hỏng → không có up/stop/rm sau migrate, thoát khác 0."""
    result, lines, _, _ = _run(tmp_path, extra={"DOCKER_MIGRATE_EXIT": "1"})
    assert result.returncode != 0
    migrate_idx = _first(lines, "migrate")
    assert all("up" not in ln and "stop" not in ln and " rm " not in f" {ln} " for ln in lines[migrate_idx + 1 :])


def test_deploy_smoke_failure_runs_previous_release_rollback(tmp_path: Path) -> None:
    """Smoke hỏng → rollback.sh của releases/<previous_tag> chạy với compose của bản đó và đổi lại
    api + mọi dịch vụ ứng dụng; release.env/symlink không trỏ bản mới (K-35)."""
    routes = dict(SMOKE_ROUTES)
    del routes["/draco/draco_decoder.wasm"]
    result, lines, root, state = _run(tmp_path, routes=routes)
    assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert sum(1 for ln in lines if "scale api=2" in ln) == 2, lines
    assert sum(1 for ln in lines if ln == "docker compose up -d --no-deps worker beat web") == 2, lines
    pulled_compose = (state / "pull_env").read_text(encoding="utf-8").splitlines()[::2]
    assert pulled_compose == [
        str(root / "releases" / NEW / "deploy" / "compose" / "prod.yml"),
        str(root / "releases" / OLD / "deploy" / "compose" / "prod.yml"),
    ]
    assert (root / "current_tag").read_text(encoding="utf-8") != NEW
    assert (root / "release.env").read_text(encoding="utf-8") != f"IMAGE_TAG={NEW}\n"


def test_deploy_service_listing_failure_rolls_back(tmp_path: Path) -> None:
    """`docker compose config --services` hỏng giữa lúc đổi dịch vụ → thoát 1, gọi rollback, không
    ghi bản mới là đang chạy."""
    result, _, root, _ = _run(tmp_path, extra={"DOCKER_CONFIG_FAIL": "1"})
    assert result.returncode == 1
    assert "rollback về previous_tag" in result.stderr
    assert (root / "current_tag").read_text(encoding="utf-8") == OLD


def test_deploy_new_container_unhealthy_timeout_keeps_old_and_removes_new(tmp_path: Path) -> None:
    """Container mới không healthy tới timeout → không dừng container cũ, container mới bị xoá;
    không có previous_tag thì không tự rollback."""
    result, lines, root, _ = _run(
        tmp_path, running=None, releases=(NEW,), extra={"APPBACK_HEALTH_TIMEOUT_S": "1", "DOCKER_UNHEALTHY": "1"}
    )
    assert result.returncode != 0
    assert "không có previous_tag" in result.stderr
    assert not any(ln.startswith("docker stop") and "old1" in ln for ln in lines)
    assert "docker rm -f c1" in lines
    assert not (root / "current_tag").exists()


def test_deploy_scale2_creates_no_new_container_fails_fast_without_touching_old(tmp_path: Path) -> None:
    """Review round 1 P3 (RES-01): `--scale api=2` không thêm container nào (`new_id` rỗng)
    → hỏng ngay, KHÔNG chờ đủ `APPBACK_HEALTH_TIMEOUT_S` và không đụng container cũ."""
    # `timeout=15` là hạn chờ: chờ đủ 120s thì TimeoutExpired → test đỏ; không có `assert` đồng hồ.
    result, lines, root, _ = _run(
        tmp_path,
        running=None,
        releases=(NEW,),
        extra={"APPBACK_HEALTH_TIMEOUT_S": "120", "DOCKER_SCALE2_NO_NEW": "1"},
        timeout=15,
    )
    assert result.returncode != 0
    assert not any(ln.startswith("docker stop") and "old1" in ln for ln in lines)
    assert not (root / "current_tag").exists()


def test_deploy_respects_api_swap_settle_s(tmp_path: Path) -> None:
    """`APPBACK_API_SWAP_SETTLE_S` (R-05, mặc định sản xuất 11s — chờ nginx tự dịch lại DNS
    trước khi xoá container `api` cũ, NO-116) phải thật sự được `sleep`: đặt giá trị nhỏ khác 0
    và đo thời gian, để không ai âm thầm xoá dòng `sleep` trong `swap_api` mà test khác (mặc
    định 0 qua `run_script`) không bắt được."""
    start = time.monotonic()
    result, _, _, _ = _run(tmp_path, extra={"APPBACK_API_SWAP_SETTLE_S": "2"}, timeout=30)
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    assert elapsed >= 2, f"elapsed={elapsed:.1f}s — swap_api không còn sleep theo APPBACK_API_SWAP_SETTLE_S"


def test_deploy_dry_run_publishes_library_after_migrate__no242(tmp_path: Path) -> None:
    """NO-242: migrate + seed để thư viện `.glb` rỗng tới lượt lịch đầu; kế hoạch deploy phải
    có `apps.api.library.cli publish` ngay sau migrate."""
    result, _, _, _ = _run(tmp_path, args=(NEW, "--dry-run"))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.find("migrate") < out.find("apps.api.library.cli publish") < out.find("scale api=2")


def _lib(script: str, env: dict[str, str]) -> str:
    """Chạy `source lib.sh; <script>` với môi trường hiện tại + `env` (bỏ biến COMPOSE_* của máy);
    trả stdout."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "IMAGE_", "APPBACK_"))}
    out = subprocess.run(  # noqa: S603 — bash + tham số cố định của test
        ["bash", "-c", f'source "{LIB.as_posix()}"; {script}'],  # noqa: S607 — bash
        env={**base, **env}, capture_output=True, text=True, check=True, encoding="utf-8",
    )  # fmt: skip
    return out.stdout


def test_lib_env_files_and_defaults_follow_release_layout(tmp_path: Path) -> None:
    """lib tự nạp: COMPOSE_ENV_FILES = appback.env[,release.env] (chỉ tệp đang có), COMPOSE_FILE
    = current/deploy/compose/prod.yml; biến script đọc từ appback.env (rỗng vẫn là rỗng —
    IMAGE_REGISTRY="" nghĩa ảnh cục bộ), biến đã có trong môi trường thắng tệp (K-35 D2)."""
    show = (
        'printf "%s|%s|%s|%s|%s" "$COMPOSE_ENV_FILES" "$COMPOSE_FILE" "$IMAGE_REGISTRY" '
        '"$APPBACK_BASE_URL" "${COMPOSE_PROFILES-}"'
    )
    env = appback_dir(tmp_path / "a", env_lines="IMAGE_REGISTRY=\nAPPBACK_BASE_URL='https://x.vn'\n")
    root = env["APPBACK_DIR"]
    assert _lib(show, env) == f"{root}/appback.env|{root}/current/deploy/compose/prod.yml||https://x.vn|"

    env = appback_dir(tmp_path / "b", running=OLD, env_lines="COMPOSE_PROFILES=ml\n")
    root = env["APPBACK_DIR"]
    got = _lib(show, {**env, "COMPOSE_PROFILES": "minio"})
    files = f"{root}/appback.env,{root}/release.env"
    assert got == f"{files}|{root}/current/deploy/compose/prod.yml|ghcr.io/mungvu999-ctrl|http://127.0.0.1|minio"


def test_lib_caller_env_files_disable_env_management(tmp_path: Path) -> None:
    """COMPOSE_ENV_FILES do người gọi đặt (drill.sh) → lib không đọc appback.env, không ghép
    release.env, và cả hai hàm kiểm môi trường trả 0 dù thiếu tệp (L5-N07: giữ hành vi drill)."""
    env = {
        "APPBACK_DIR": str(tmp_path),
        "APPBACK_ENV_FILE": str(tmp_path / "khong-co.env"),
        "COMPOSE_ENV_FILES": "deploy/compose/env.example",
    }
    probe = 'assert_env_ready && assert_release_env && printf "%s|%s" "$COMPOSE_ENV_FILES" "${COMPOSE_PROFILES-unset}"'
    out = _lib(probe, env)
    assert out == "deploy/compose/env.example|unset"


def test_fake_compose_services_and_non_app_set_match_prod_yml() -> None:
    """Bản giả `compose config --services` (support.FAKE_COMPOSE_SERVICES) và tập loại trừ
    APPBACK_NON_APP_SERVICES của lib.sh khớp prod.yml thật: không profile → hạ tầng + migrate +
    api + đúng worker/beat/web là dịch vụ ứng dụng; profile minio → minio, minio-init (hạ tầng);
    profile ml → ml, ml-train (ứng dụng). prod.yml thêm dịch vụ mà không ai cập nhật thì đỏ."""
    services = load_yaml(REPO_ROOT / "deploy" / "compose" / "prod.yml")["services"]
    by_profile: dict[str, set[str]] = {}
    for name, svc in services.items():
        for profile in svc.get("profiles", [""]):
            by_profile.setdefault(profile, set()).add(name)
    m = re.search(r'^APPBACK_NON_APP_SERVICES="([^"]+)"$', LIB.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, "lib.sh: không tìm thấy APPBACK_NON_APP_SERVICES"
    non_app = set(m.group(1).split())
    assert by_profile[""] == {"postgres", "redis-broker", "redis-cache", "migrate", "api", "worker", "beat", "web"}
    assert by_profile[""] - non_app == {"worker", "beat", "web"}
    assert by_profile["minio"] == {"minio", "minio-init"} <= non_app
    assert by_profile["ml"] == {"ml", "ml-train"}
    assert not by_profile["ml"] & non_app


_NGINX_TEMPLATES = (
    REPO_ROOT / "deploy" / "nginx" / "templates" / "dev" / "app.conf.template",
    REPO_ROOT / "deploy" / "nginx" / "templates" / "prod" / "app.conf.template",
)


def test_deploy_default_swap_settle_s_covers_nginx_resolver_ttl() -> None:
    """Review round 2 N3 (R-05): ràng buộc "mặc định >= TTL cache DNS của nginx + biên an
    toàn" (comment `lib.sh`) chưa có test nào chốt trước đây — B0-08 nâng `resolver …
    valid=…` mà không ai sửa mặc định ở đây thì cổng vẫn xanh, deploy lại 502 thật. Trích
    `valid=(\\d+)s` thẳng từ CẢ HAI template thật (chỉ đọc, không sửa — thuộc B0-08) và đối
    chiếu với mặc định `${APPBACK_API_SWAP_SETTLE_S:=N}` trích thẳng từ `lib.sh` (dạng
    `:-N` bị cấm bên dưới), không chép tay hằng số nào ở hai phía."""
    lib_sh = (REPO_ROOT / "deploy" / "scripts" / "lib.sh").read_text(encoding="utf-8")
    m = re.search(r'^: "\$\{APPBACK_API_SWAP_SETTLE_S:=(\d+)\}"$', lib_sh, re.MULTILINE)
    assert m, 'không tìm thấy dòng khai báo : "${APPBACK_API_SWAP_SETTLE_S:=N}" trong lib.sh'
    default_settle_s = int(m.group(1))
    assert "APPBACK_API_SWAP_SETTLE_S:-" not in lib_sh, (
        "lib.sh: còn bản sao mặc định dạng ${APPBACK_API_SWAP_SETTLE_S:-N} — phải dùng biến trần (NO-120)"
    )

    # README là bản thứ ba của cùng hằng số (NO-120): đọc từ bảng biến môi trường,
    # không chép tay, để tài liệu không trôi khỏi lib.sh.
    readme = (REPO_ROOT / "deploy" / "scripts" / "README.md").read_text(encoding="utf-8")
    rm = re.search(r"\| `APPBACK_API_SWAP_SETTLE_S` \| `(\d+)` \|", readme)
    assert rm, "README.md: không tìm thấy dòng bảng của APPBACK_API_SWAP_SETTLE_S"
    assert int(rm.group(1)) == default_settle_s, f"README.md ghi mặc định {rm.group(1)}, lib.sh khai {default_settle_s}"

    ttls: list[int] = []
    for template in _NGINX_TEMPLATES:
        text = template.read_text(encoding="utf-8")
        tm = re.search(r"resolver\s+\S+\s+valid=(\d+)s", text)
        assert tm, f"không tìm thấy `resolver … valid=Ns` trong {template}"
        ttls.append(int(tm.group(1)))

    max_ttl = max(ttls)
    assert default_settle_s >= max_ttl + 1, (
        f"mặc định APPBACK_API_SWAP_SETTLE_S={default_settle_s} không đủ che TTL cache DNS "
        f"lớn nhất của nginx ({max_ttl}s) + 1s biên an toàn"
    )


def test_deploy_bad_tag_exits_2(tmp_path: Path) -> None:
    """Tag không khớp mẫu hợp đồng §2 → thoát 2."""
    env = {"APPBACK_DIR": str(tmp_path)}
    result = run_script(SCRIPT, ["!!not-a-tag"], env=env)
    assert result.returncode == 2


def test_readme_ml_env_check__uses_allowlist() -> None:
    """Chặn tái phát NO-204: lệnh kiểm `ml.env` ở README in dòng **ngoài** danh sách cho phép.

    Danh sách cho phép = tiền tố `ML_`/`METRICS_` (như `test_ml_env_example_holds_only_ml_knobs`),
    không phải danh sách cấm hẹp — biến lạ nào lọt vào `ml.env` cũng bị bắt.
    """
    readme = (REPO_ROOT / "deploy" / "scripts" / "README.md").read_text(encoding="utf-8")
    cmd = next(line for line in readme.splitlines() if "grep" in line and "/etc/appback/ml.env" in line)
    assert "grep -vE" in cmd, f"README: lệnh kiểm ml.env phải theo danh sách cho phép: {cmd}"
    for prefix in ("ML_", "METRICS_"):
        assert prefix in cmd, f"README: danh sách cho phép thiếu {prefix}: {cmd}"


def test_lib_empty_swap_settle_s_falls_back_to_default__no200(tmp_path: Path) -> None:
    """NO-200: `APPBACK_API_SWAP_SETTLE_S=` rỗng (appback.env) làm `sleep ""` hỏng giữa
    `swap_api`; rỗng phải về mặc định, còn `0` vẫn giữ nguyên (tắt chờ)."""
    lib = REPO_ROOT / "deploy" / "scripts" / "lib.sh"
    for given, want in (("", "11"), ("0", "0"), ("3", "3")):
        out = subprocess.run(  # noqa: S603 — bash + tham số cố định của test
            ["bash", "-c", f'source "{lib.as_posix()}"; printf %s "$APPBACK_API_SWAP_SETTLE_S"'],  # noqa: S607 — bash
            env={**os.environ, "APPBACK_API_SWAP_SETTLE_S": given},
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        assert out.stdout == want, f"đặt {given!r} → {out.stdout!r}, muốn {want!r}"


def test_readme_lists_required_smtp_vars__no196() -> None:
    """NO-196: mọi biến `${SMTP_*|MAIL_*:?…}` bắt buộc ở `base.yml` làm mọi lệnh compose trên
    prod hỏng nếu `appback.env` thiếu — README phải có dòng bảng và mục §12 nhắc tới chúng."""
    base = (REPO_ROOT / "deploy" / "compose" / "base.yml").read_text(encoding="utf-8")
    required = sorted(set(re.findall(r"\$\{((?:SMTP|MAIL)_\w+):\?", base)))
    assert {"SMTP_HOST", "MAIL_FROM"} <= set(required), required
    readme = (REPO_ROOT / "deploy" / "scripts" / "README.md").read_text(encoding="utf-8")
    section12 = readme.split("## 12.", 1)[1].split("\n## ", 1)[0]
    for name in required:
        assert re.search(rf"^\| `{name}` \|", readme, re.MULTILINE), f"README: thiếu dòng bảng {name}"
        assert name in section12, f"README §12: không nhắc {name}"


def test_readme_restore_documents_library_publish_decision__no242() -> None:
    """NO-242 / C17b: `restore.sh` không chạy `publish` (nó đổ lại CSDL + object cùng một bản
    sao lưu) — quyết định đó phải được README ghi rõ cạnh lệnh `publish`, và `restore.sh` đúng
    là không gọi nó."""
    readme = (REPO_ROOT / "deploy" / "scripts" / "README.md").read_text(encoding="utf-8")
    assert re.search(r"`restore\.sh` \*\*không\*\* chạy `publish`", readme), "README: thiếu quyết định restore/publish"
    restore = (REPO_ROOT / "deploy" / "backup" / "restore.sh").read_text(encoding="utf-8")
    assert "library.cli" not in restore


def test_lib_json_escape_roundtrips_through_json_and_unescape__no189() -> None:
    """`json_escape` (thay `python3 json.dumps`) cho chuỗi mà `json.loads` đọc lại đúng, và
    `json_unescape` đảo ngược nó — kể cả dấu `"`, `\\`, xuống dòng, tab và chữ Việt."""
    lib = REPO_ROOT / "deploy" / "scripts" / "lib.sh"
    for original in ('a"b', "c\\d", "x\ny\tz", 'tệp "lạ"\\n.bin', ""):
        out = subprocess.run(  # noqa: S603 — bash + tham số cố định của test
            ["bash", "-c", f'source "{lib.as_posix()}"; json_escape "$ORIGINAL"'],  # noqa: S607 — bash
            env={**os.environ, "ORIGINAL": original},
            capture_output=True, text=True, check=True, encoding="utf-8",
        )  # fmt: skip
        assert json.loads(f'"{out.stdout}"') == original, f"{original!r} → {out.stdout!r}"
        back = subprocess.run(  # noqa: S603 — bash + tham số cố định của test
            ["bash", "-c", f'source "{lib.as_posix()}"; json_unescape "$ESCAPED"'],  # noqa: S607 — bash
            env={**os.environ, "ESCAPED": out.stdout},
            capture_output=True, text=True, check=True, encoding="utf-8",
        )  # fmt: skip
        assert back.stdout == original, f"{out.stdout!r} → {back.stdout!r}, muốn {original!r}"


BUNDLE = REPO_ROOT / "deploy" / "scripts" / "bundle.sh"


def test_bundle_contains_every_file_compose_references(tmp_path: Path) -> None:
    """K-35 D1: bó phát hành giải nén ra phải tự đủ — mọi `extends.file` của prod.yml và mọi bind
    tương đối (`../minio/*`) của base.yml trỏ tới tệp có trong bó; có scripts + backup; không mang
    tests/ hay __pycache__/ lên máy đích. Đọc YAML của CHÍNH bản trong bó."""
    out = tmp_path / "out" / "deploy-bundle-sha-0000000000b2.tar.gz"
    result = run_script(BUNDLE, [str(out)])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(out)
    dest = tmp_path / "releases" / NEW
    dest.mkdir(parents=True)
    with tarfile.open(out) as tar:
        names = tar.getnames()
        tar.extractall(dest, filter="data")
    assert not [n for n in names if "/tests" in n or "__pycache__" in n], names
    compose_dir = dest / "deploy" / "compose"
    prod = load_yaml(compose_dir / "prod.yml")
    for name, svc in prod["services"].items():
        ref = svc.get("extends", {}).get("file")
        if ref:
            assert (compose_dir / ref).is_file(), f"{name}: extends {ref} thiếu trong bó"
    base = load_yaml(compose_dir / "base.yml")
    volumes = [v for svc in base["services"].values() for v in svc.get("volumes", []) if isinstance(v, str)]
    relative = [v.split(":", 1)[0] for v in volumes if v.startswith(".")]
    assert relative, "base.yml không còn bind tương đối nào — cập nhật test"
    for bind in relative:
        assert (compose_dir / bind).is_file(), f"bind {bind} thiếu trong bó"
    for script in ("deploy.sh", "rollback.sh", "lib.sh", "smoke.sh", "healthcheck.sh", "alert.sh"):
        assert (dest / "deploy" / "scripts" / script).is_file(), script
    for script in ("backup.sh", "restore.sh"):
        assert (dest / "deploy" / "backup" / script).is_file(), script


def test_bundle_wrong_arguments_exit_2(tmp_path: Path) -> None:
    """Thiếu/thừa đối số → thoát 2, không tạo tệp."""
    for args in ([], [str(tmp_path / "a.tgz"), "thua"], [""]):
        assert run_script(BUNDLE, args).returncode == 2, args
    assert not (tmp_path / "a.tgz").exists()
