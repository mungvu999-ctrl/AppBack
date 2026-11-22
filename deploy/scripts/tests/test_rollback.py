"""Test `deploy/scripts/rollback.sh` — không migrate, mặc định previous_tag, tag ghi đè, dùng
compose của releases/<tag> và đổi mọi dịch vụ ứng dụng (K-35)."""

from __future__ import annotations

import os
import subprocess
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

SCRIPT = REPO_ROOT / "deploy" / "scripts" / "rollback.sh"
PREV = "sha-0000000000a1"
CUR = "sha-0000000000b2"

SMOKE_ROUTES = {
    "/api/health": Reply(200),
    "/api/ready": Reply(200),
    "/": Reply(200, headers={"Content-Security-Policy": "default-src 'self'"}),
    "/draco/draco_decoder.wasm": Reply(200, body=b"wasm"),
    "/api/nope": Reply(404, body=b'{"code":"NOT_FOUND"}'),
}


def _run(
    tmp_path: Path,
    args: tuple[str, ...] = (),
    *,
    releases: tuple[str, ...] = (PREV, CUR),
    previous: str | None = PREV,
    extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path, Path]:
    """Máy đích giả đang chạy CUR (previous_tag = `previous`), docker giả luôn healthy; chạy
    rollback.sh; trả (kết quả, log docker, APPBACK_DIR, thư mục trạng thái docker)."""
    root = tmp_path / "opt-appback"
    env = appback_dir(root, running=CUR, releases=releases)
    if previous is not None:
        (root / "previous_tag").write_text(previous, encoding="utf-8")
    bin_dir, state = fake_docker(tmp_path)
    log = tmp_path / "docker.log"
    with HttpStub(SMOKE_ROUTES) as stub:
        env |= {
            "APPBACK_BASE_URL": stub.url,
            "APPBACK_HEALTH_TIMEOUT_S": "5",
            "DOCKER_STATE": str(state),
            "FAKE_LOG": str(log),
            **(extra or {}),
        }
        result = run_script(SCRIPT, list(args), env=env, bin_dir=bin_dir)
    return result, list(read_log(log)), root, state


def test_rollback_no_tag_and_no_previous_tag_exits_2(tmp_path: Path) -> None:
    """Không đối số và không có previous_tag → thoát 2."""
    result, lines, _, _ = _run(tmp_path, previous=None)
    assert result.returncode == 2
    assert lines == []


def test_rollback_uses_previous_tag_and_never_migrates(tmp_path: Path) -> None:
    """Mặc định đọc previous_tag, log không có migrate/alembic; sau smoke ghi release.env,
    symlink current → releases/<tag>, current_tag."""
    result, lines, root, _ = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert lines, "docker giả phải được gọi"
    assert not any("migrate" in ln or "alembic" in ln for ln in lines)
    assert (root / "current_tag").read_text(encoding="utf-8") == PREV
    assert (root / "release.env").read_text(encoding="utf-8") == f"IMAGE_TAG={PREV}\n"
    assert os.readlink(root / "current") == f"releases/{PREV}"


def test_rollback_explicit_tag_wins_over_previous_tag(tmp_path: Path) -> None:
    """Tag chỉ định trên dòng lệnh thắng nội dung previous_tag."""
    explicit = "sha-0000000000c3"
    result, _, root, _ = _run(tmp_path, (explicit,), releases=(PREV, CUR, explicit))
    assert result.returncode == 0, result.stderr
    assert (root / "current_tag").read_text(encoding="utf-8") == explicit


def test_rollback_uses_release_compose_and_swaps_every_app_service(tmp_path: Path) -> None:
    """Rollback chạy compose của releases/<tag> (lùi cả cấu hình), nạp appback.env + release.env,
    và đổi api rồi worker, beat, web, ml, ml-train (COMPOSE_PROFILES=ml) — không chỉ api/worker/beat."""
    result, lines, root, state = _run(tmp_path, extra={"COMPOSE_PROFILES": "ml"})
    assert result.returncode == 0, result.stderr
    compose_file, env_files = (state / "pull_env").read_text(encoding="utf-8").splitlines()
    assert compose_file == str(root / "releases" / PREV / "deploy" / "compose" / "prod.yml")
    assert env_files == f"{root / 'appback.env'},{root / 'release.env'}"
    scale2 = next(i for i, ln in enumerate(lines) if "scale api=2" in ln)
    swap = lines.index("docker compose up -d --no-deps worker beat web ml ml-train")
    assert scale2 < swap


def test_rollback_missing_release_bundle_exits_2_before_docker(tmp_path: Path) -> None:
    """Không còn releases/<tag> → thoát 2 kèm thông báo, không gọi docker."""
    result, lines, _, _ = _run(tmp_path, releases=(CUR,))
    assert result.returncode == 2
    assert "chưa có bó phát hành" in result.stderr
    assert lines == []


def test_rollback_service_listing_failure_exits_1_without_recording(tmp_path: Path) -> None:
    """Đổi dịch vụ ứng dụng hỏng → thoát 1, trạng thái phát hành giữ bản đang chạy."""
    result, _, root, _ = _run(tmp_path, extra={"DOCKER_CONFIG_FAIL": "1"})
    assert result.returncode == 1
    assert "không đổi được dịch vụ ứng dụng" in result.stderr
    assert (root / "current_tag").read_text(encoding="utf-8") == CUR
