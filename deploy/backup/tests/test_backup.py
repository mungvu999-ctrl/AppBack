"""Test `deploy/backup/backup.sh` (B0-10 [3], [8]) — pytest bước 5.

`docker` và `age` là lệnh giả (`fake_bin`) chỉ để kiểm thứ tự; thân của chúng ghi tệp
thật (giả lập `pg_dump`/`mc mirror`/`age`) để `manifest.json` băm được đúng nội dung
trên đĩa. Không có Docker/age thật trong lượt test này (support.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deploy.scripts.tests.support import appback_dir, fake_bin, read_log, run_script

BACKUP_SH = Path(__file__).resolve().parents[1] / "backup.sh"
RUNNING = "sha-aaaaaaaaaaaa"

# Thân lệnh `docker` giả: nhận biết pg_dump/psql/mc mirror/tar qua nội dung "$*" rồi
# ghi tệp thật vào thư mục gắn qua "-v host:container" để manifest.json băm được.
_FAKE_DOCKER_BODY = r"""
args="$*"
host_mount=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-v" ]; then
    host_mount="${a%%:*}"
  fi
  prev="$a"
done

case "$args" in
  *pg_dump*)
    printf 'FAKE-PG-DUMP-BYTES\n'
    exit 0
    ;;
  *"select version_num"*)
    printf 'fakerev0001\n'
    exit 0
    ;;
  *"mc mirror --remove"*)
    exit 0
    ;;
  *"mc mirror"*)
    mkdir -p "$host_mount/projects/proj1"
    printf 'object-a' > "$host_mount/projects/proj1/a.bin"
    printf 'object-b' > "$host_mount/projects/proj1/b.bin"
    printf 'object-c' > "$host_mount/c.bin"
    exit 0
    ;;
  *"entrypoint tar"*|*" tar "*)
    src="$(mktemp -d)"
    printf 'vol-object-1' > "$src/x.bin"
    printf 'vol-object-2' > "$src/y.bin"
    tar -cf - -C "$src" .
    rm -rf "$src"
    exit 0
    ;;
  *"DROP DATABASE"*|*"CREATE DATABASE"*|*pg_restore*)
    cat >/dev/null
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""

_FAKE_AGE_BODY = r"""
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    out="$a"
  fi
  prev="$a"
done
src="${@: -1}"
if [ "$1" = "-d" ]; then
  cp "$src" "$out"
else
  printf 'AGE-ENCRYPTED:' > "$out"
  cat "$src" >> "$out"
fi
"""


def _bin_dir(tmp_path: Path, *, fail_pg_dump: bool = False) -> Path:
    """Đặt lệnh giả `docker`/`age` vào `tmp_path/bin`; `fail_pg_dump` mô phỏng pg_dump hỏng."""
    docker_body = _FAKE_DOCKER_BODY
    if fail_pg_dump:
        docker_body = 'case "$*" in *pg_dump*) exit 1 ;; esac\n' + docker_body
    return fake_bin(tmp_path / "bin", {"docker": docker_body, "age": _FAKE_AGE_BODY})


def _run_backup(tmp_path: Path, env: dict[str, str]) -> tuple[int, str, Path]:
    """Chạy `backup.sh` với `env`, trả (mã thoát, log lệnh, BACKUP_TARGET)."""
    target = tmp_path / "target"
    log = tmp_path / "log"
    env = dict(env)
    fail_pg_dump = env.pop("_FAIL", "") == "1"
    full_env = {
        "FAKE_LOG": str(log),
        "BACKUP_TARGET": str(target),
        **appback_dir(tmp_path / "opt-appback", running=RUNNING),
        **env,
    }
    result = run_script(BACKUP_SH, env=full_env, bin_dir=_bin_dir(tmp_path, fail_pg_dump=fail_pg_dump))
    return result.returncode, "\n".join(read_log(log)), target


def _sha256(path: Path) -> str:
    """SHA-256 hex của một tệp thật trên đĩa."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_backup__manifest_has_required_fields_and_correct_hashes(tmp_path: Path) -> None:
    """s3 không mã hoá: manifest đủ trường §3, files khớp SHA-256 thật, object_count đúng."""
    code, log, target = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "BACKUP_ALLOW_PLAINTEXT": "1"})
    assert code == 0, log

    dirs = [p for p in target.iterdir() if p.is_dir()]
    assert len(dirs) == 1
    stage = dirs[0]
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))

    for key in ("created_at", "alembic_head", "storage", "encrypted", "object_count", "files"):
        assert key in manifest

    assert manifest["storage"] == "s3"
    assert manifest["encrypted"] is False
    assert manifest["alembic_head"] == "fakerev0001"
    assert manifest["object_count"] == 3
    assert len(manifest["files"]) == 4  # db.dump + 3 object

    for rel, expected in manifest["files"].items():
        assert _sha256(stage / rel) == expected


def test_backup__pg_dump_runs_before_mirror_and_mirror_has_no_remove(tmp_path: Path) -> None:
    """Log lệnh: pg_dump đứng trước mc mirror; không lệnh mirror nào dùng --remove."""
    code, log, _ = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "BACKUP_ALLOW_PLAINTEXT": "1"})
    assert code == 0, log

    lines = log.splitlines()
    pg_dump_idx = next(i for i, line in enumerate(lines) if "pg_dump" in line)
    mirror_idx = next(i for i, line in enumerate(lines) if "mc mirror" in line)
    assert pg_dump_idx < mirror_idx

    for line in lines:
        if "mirror" in line:
            assert "--remove" not in line


def test_backup__local_storage_produces_tar_and_skips_mirror(tmp_path: Path) -> None:
    """APPBACK_STORAGE=local → objects.tar, không lệnh mc mirror nào chạy."""
    code, log, target = _run_backup(tmp_path, {"APPBACK_STORAGE": "local", "BACKUP_ALLOW_PLAINTEXT": "1"})
    assert code == 0, log
    assert "mirror" not in log

    stage = next(p for p in target.iterdir() if p.is_dir())
    assert (stage / "objects.tar").is_file()
    assert not (stage / "objects").exists()

    with tarfile.open(stage / "objects.tar") as tar:
        member_files = [m for m in tar.getmembers() if m.isfile()]
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["storage"] == "local"
    assert manifest["object_count"] == len(member_files) == 2
    assert set(manifest["files"]) == {"db.dump", "objects.tar"}


def test_backup__age_recipient_encrypts_and_leaves_only_age_files(tmp_path: Path) -> None:
    """Có BACKUP_AGE_RECIPIENT → chỉ còn .age + manifest.json, encrypted: true."""
    code, log, target = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "BACKUP_AGE_RECIPIENT": "age1testrecipient"})
    assert code == 0, log

    stage = next(p for p in target.iterdir() if p.is_dir())
    names = {p.name for p in stage.iterdir()}
    assert names == {"db.dump.age", "objects.tar.age", "manifest.json"}

    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is True
    assert set(manifest["files"]) == {"db.dump.age", "objects.tar.age"}
    for rel, expected in manifest["files"].items():
        assert _sha256(stage / rel) == expected


def test_backup__production_without_age_recipient_exits_1_before_dump(tmp_path: Path) -> None:
    """APP_ENV=production mà thiếu BACKUP_AGE_RECIPIENT → thoát 1, không pg_dump, không bản rõ (C-18)."""
    code, log, target = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "APP_ENV": "production"})
    assert code == 1, log
    assert "pg_dump" not in log
    assert not target.exists() or not any(target.iterdir())


@pytest.mark.parametrize("app_env", ["staging", "dev", ""])
def test_backup__any_env_without_age_recipient_exits_1_before_dump(tmp_path: Path, app_env: str) -> None:
    """Không chỉ production: staging, dev và `APP_ENV` rỗng thiếu recipient cũng thoát 1, không ghi gì."""
    env = {"APPBACK_STORAGE": "s3", "APP_ENV": app_env}
    code, log, target = _run_backup(tmp_path, env)
    assert code == 1, log
    assert "pg_dump" not in log
    assert not target.exists() or not any(target.iterdir())


def test_backup__plaintext_opt_out_runs_without_recipient(tmp_path: Path) -> None:
    """`BACKUP_ALLOW_PLAINTEXT=1` là lối thoát tường minh duy nhất: chạy bản rõ, `encrypted: false`."""
    env = {"APPBACK_STORAGE": "s3", "APP_ENV": "dev", "BACKUP_ALLOW_PLAINTEXT": "1"}
    code, log, target = _run_backup(tmp_path, env)
    assert code == 0, log
    stage = next(p for p in target.iterdir() if p.is_dir())
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is False


def test_backup__production_ignores_plaintext_opt_out(tmp_path: Path) -> None:
    """Cờ `BACKUP_ALLOW_PLAINTEXT=1` chỉ cho dev/diễn tập: `APP_ENV=production` vẫn thoát 1, không pg_dump."""
    env = {"APPBACK_STORAGE": "s3", "APP_ENV": "production", "BACKUP_ALLOW_PLAINTEXT": "1"}
    code, log, target = _run_backup(tmp_path, env)
    assert code == 1, log
    assert "pg_dump" not in log
    assert not target.exists() or not any(target.iterdir())


def test_backup__plaintext_opt_out_value_must_be_exactly_1(tmp_path: Path) -> None:
    """Giá trị khác `1` (vd `true`) không phải opt-out: vẫn thoát 1."""
    code, log, _ = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "BACKUP_ALLOW_PLAINTEXT": "true"})
    assert code == 1, log


def test_backup__production_with_age_recipient_encrypts(tmp_path: Path) -> None:
    """APP_ENV=production có BACKUP_AGE_RECIPIENT → chạy bình thường, chỉ còn tệp .age (C-18)."""
    env = {"APPBACK_STORAGE": "s3", "APP_ENV": "production", "BACKUP_AGE_RECIPIENT": "age1testrecipient"}
    code, log, target = _run_backup(tmp_path, env)
    assert code == 0, log
    stage = next(p for p in target.iterdir() if p.is_dir())
    assert {p.name for p in stage.iterdir()} == {"db.dump.age", "objects.tar.age", "manifest.json"}


def test_backup__pg_dump_failure_exits_1_and_removes_stage_dir(tmp_path: Path) -> None:
    """pg_dump hỏng → thoát 1, không để lại thư mục dở dang."""
    code, log, target = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "BACKUP_ALLOW_PLAINTEXT": "1", "_FAIL": "1"})
    assert code == 1, log
    if target.exists():
        assert not any(target.iterdir())


def _make_day_dirs(target: Path, days_ago: list[int]) -> list[Path]:
    """Dựng thư mục giả `yyyymmddT000000Z` cho mỗi ngày trong `days_ago` (0 = hôm nay)."""
    target.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    made = []
    for delta in days_ago:
        ts = (now - timedelta(days=delta)).strftime("%Y%m%dT000000Z")
        d = target / ts
        d.mkdir(exist_ok=True)
        made.append(d)
    return made


def test_backup__rotate_only_keeps_seven_days_and_weekly_and_untouched_stray(tmp_path: Path) -> None:
    """Xoay vòng giữ 7 bản mới nhất + bản mới nhất mỗi tuần của 4 tuần; thư mục lạ không bị xoá."""
    target = tmp_path / "target"
    _make_day_dirs(target, list(range(40)))
    stray = target / "not-a-backup"
    stray.mkdir()

    env = {"FAKE_LOG": str(tmp_path / "log"), "BACKUP_TARGET": str(target)}
    result = run_script(BACKUP_SH, args=["--rotate-only"], env=env, bin_dir=_bin_dir(tmp_path))
    assert result.returncode == 0, result.stderr

    remaining = sorted(p.name for p in target.iterdir())
    assert stray.name in remaining

    pattern = re.compile(r"^\d{8}T\d{6}Z$")
    dated = [name for name in remaining if pattern.fullmatch(name)]

    now = datetime.now(UTC)
    expected = {(now - timedelta(days=d)).strftime("%Y%m%dT000000Z") for d in range(7)}
    for i in range(4):
        week_key = (now - timedelta(weeks=i)).isocalendar()[:2]
        same_week = [d for d in range(40) if (now - timedelta(days=d)).isocalendar()[:2] == week_key]
        if same_week:
            latest = min(same_week)
            expected.add((now - timedelta(days=latest)).strftime("%Y%m%dT000000Z"))

    assert set(dated) == expected


def test_backup__stage_dir_and_dump_are_owner_only(tmp_path: Path) -> None:
    """SEC-040: dưới `umask 022` của phiên SSH/systemd, thư mục bản sao lưu và tệp script tạo vẫn chỉ chủ đọc được.

    Bản rõ (không `BACKUP_AGE_RECIPIENT`) chứa hash mật khẩu và mọi bản vẽ: 0644/0755 là cho mọi tài khoản
    cục bộ đọc. Chạy qua `bash -c 'umask 022; exec …'` để kết quả không phụ thuộc umask của tiến trình pytest.
    """
    target = tmp_path / "target"
    log = tmp_path / "log"
    env = {"FAKE_LOG": str(log), "BACKUP_TARGET": str(target), "APPBACK_STORAGE": "s3", "BACKUP_ALLOW_PLAINTEXT": "1"}
    env |= appback_dir(tmp_path / "opt-appback", running=RUNNING)
    wrapper = tmp_path / "umask022.sh"
    wrapper.write_text(f'umask 022\nexec bash "{BACKUP_SH}"\n', encoding="utf-8")
    result = run_script(wrapper, env=env, bin_dir=_bin_dir(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr

    (stage,) = [p for p in target.iterdir() if p.is_dir()]
    # Chỉ khẳng định thứ script tự tạo: `objects/**` do container `mc`/`tar` sinh (docker giả ở đây ghi trên host nên
    # không chứng minh gì về chúng) — chúng được bảo vệ bởi thư mục cha 0700.
    modes = {name: (stage / name).stat().st_mode & 0o777 for name in (".", "db.dump", "manifest.json", "objects")}
    assert modes == {".": 0o700, "db.dump": 0o600, "manifest.json": 0o600, "objects": 0o700}


def test_backup__s3_mirror_runs_as_host_user(tmp_path: Path) -> None:
    """NO-327: `mc mirror` chạy bằng uid:gid của người chạy script, không root — không thì `objects/**` thuộc root
    và `rm -rf`/xoay vòng của `deploy` hỏng EPERM (đo bằng alpine: thư mục root-owned không xoá được)."""
    code, log, _ = _run_backup(tmp_path, {"APPBACK_STORAGE": "s3", "BACKUP_ALLOW_PLAINTEXT": "1"})
    assert code == 0, log
    (mirror,) = [line for line in log.splitlines() if "mirror" in line]
    assert f"--user {os.getuid()}:{os.getgid()}" in mirror, mirror


def test_backup__local_tar_streams_to_a_host_owned_file(tmp_path: Path) -> None:
    """NO-327: stage_dir là 0700 của `deploy` nên container `api` (uid 10001) không ghi vào đó được — tar đi qua
    stdout (`-T`, không gắn volume đích) và host ghi `objects.tar` 0600."""
    code, log, target = _run_backup(tmp_path, {"APPBACK_STORAGE": "local", "BACKUP_ALLOW_PLAINTEXT": "1"})
    assert code == 0, log
    (tar_line,) = [line for line in log.splitlines() if " tar " in f" {line} " and "docker" in line]
    assert " -T " in tar_line, tar_line
    assert "backup-dest" not in tar_line, tar_line
    (stage,) = [p for p in target.iterdir() if p.is_dir()]
    assert (stage / "objects.tar").stat().st_mode & 0o777 == 0o600


def test_backup__without_image_tag_uses_release_env_for_compose(tmp_path: Path) -> None:
    """L5-N07: môi trường KHÔNG có IMAGE_TAG, máy đích có release.env → compose nhận
    COMPOSE_ENV_FILES = appback.env,release.env (prod.yml `${IMAGE_TAG:?}` nội suy được) và
    `exec postgres pg_dump` chạy, không cần đặt IMAGE_TAG trong appback.env."""
    seen = tmp_path / "env-seen"
    root = tmp_path / "opt-appback"
    env = appback_dir(root, running=RUNNING)
    body = f'case "$*" in *pg_dump*) printf "%s" "${{IMAGE_TAG-unset}}|$COMPOSE_ENV_FILES" > "{seen}" ;; esac\n'
    bin_dir = fake_bin(tmp_path / "bin", {"docker": body + _FAKE_DOCKER_BODY, "age": _FAKE_AGE_BODY})
    env |= {"FAKE_LOG": str(tmp_path / "log"), "BACKUP_TARGET": str(tmp_path / "target"), "BACKUP_ALLOW_PLAINTEXT": "1"}
    result = run_script(BACKUP_SH, env=env, bin_dir=bin_dir)
    assert result.returncode == 0, result.stderr
    assert seen.read_text(encoding="utf-8") == f"unset|{root / 'appback.env'},{root / 'release.env'}"


def test_backup__no_release_yet_exits_2_before_any_docker(tmp_path: Path) -> None:
    """Chưa deploy lần nào (không có release.env) → thoát 2 "chưa có bản phát hành nào", không
    gọi docker, không tạo thư mục bản sao lưu (L5-N07)."""
    target = tmp_path / "target"
    log = tmp_path / "log"
    env = appback_dir(tmp_path / "opt-appback")
    env |= {"FAKE_LOG": str(log), "BACKUP_TARGET": str(target), "BACKUP_ALLOW_PLAINTEXT": "1"}
    result = run_script(BACKUP_SH, env=env, bin_dir=_bin_dir(tmp_path))
    assert result.returncode == 2
    assert "chưa có bản phát hành nào" in result.stderr
    assert list(read_log(log)) == []
    assert not target.exists() or not any(target.iterdir())
