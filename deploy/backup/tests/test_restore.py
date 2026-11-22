"""Test `deploy/backup/restore.sh` (B0-10 [3], [8]) — pytest bước 5.

Dựng bản sao lưu giả trên đĩa (nội dung thật, SHA-256 khớp thật trong manifest.json) rồi
chạy `restore.sh` với `docker`/`age` giả (chỉ kiểm thứ tự lệnh, không có Docker/age thật —
support.py) và một `smoke.sh` giả tự viết (không phải lệnh Docker nên không qua `fake_bin`).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from deploy.scripts.tests.support import FAKE_COMPOSE_SERVICES, REPO_ROOT, appback_dir, fake_bin, read_log, run_script

RESTORE_SH = Path(__file__).resolve().parents[1] / "restore.sh"
RUNNING = "sha-aaaaaaaaaaaa"

# `docker` giả: chỉ kiểm thứ tự lệnh, tiêu thụ stdin của các lệnh psql/pg_restore để không
# treo script khi restore.sh không redirect stdin cho DROP/CREATE DATABASE.
_FAKE_DOCKER_BODY = r"""
args="$*"
case "$args" in
  *"ps -q"*)
    printf 'fake-container-id\n'
    exit 0
    ;;
  *"State.Health.Status"*)
    printf 'healthy\n'
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
cp "$src" "$out"
"""

_SMOKE_STUB = """#!/usr/bin/env bash
printf 'smoke %s\\n' "$*" >> "$FAKE_LOG"
"""


def _sha256(data: bytes) -> str:
    """SHA-256 hex của `data`."""
    return hashlib.sha256(data).hexdigest()


def _support_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Lệnh giả `docker`/`age` + `smoke.sh` giả (không qua fake_bin, không phải lệnh Docker).

    `restore.sh` giờ `source` `deploy/scripts/lib.sh` (wait_healthy dùng chung, R-07) —
    chép lib.sh THẬT vào thư mục scripts giả, để test chạy đúng mã sẽ lên máy đích.
    """
    bin_dir = fake_bin(tmp_path / "bin", {"docker": FAKE_COMPOSE_SERVICES + _FAKE_DOCKER_BODY, "age": _FAKE_AGE_BODY})
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "smoke.sh").write_text(_SMOKE_STUB, encoding="utf-8")
    shutil.copy(REPO_ROOT / "deploy" / "scripts" / "lib.sh", scripts_dir / "lib.sh")
    return bin_dir, scripts_dir


def _make_plain_backup(tmp_path: Path, *, storage: str = "s3") -> Path:
    """Bản sao lưu hợp lệ, không mã hoá — manifest khớp SHA-256 tệp thật trên đĩa."""
    backup_dir = tmp_path / "backup" / "20260101T023000Z"
    db_bytes = b"FAKE-DB-DUMP-CONTENT"
    (backup_dir).mkdir(parents=True)
    (backup_dir / "db.dump").write_bytes(db_bytes)
    files = {"db.dump": _sha256(db_bytes)}
    if storage == "s3":
        obj_dir = backup_dir / "objects" / "projects" / "proj1"
        obj_dir.mkdir(parents=True)
        obj_bytes = b"object-a"
        (obj_dir / "a.bin").write_bytes(obj_bytes)
        files["objects/projects/proj1/a.bin"] = _sha256(obj_bytes)
        object_count = 1
    else:
        tar_bytes = b"FAKE-TAR-BYTES"
        (backup_dir / "objects.tar").write_bytes(tar_bytes)
        files["objects.tar"] = _sha256(tar_bytes)
        object_count = 2

    manifest = {
        "created_at": "2026-01-01T02:30:00Z",
        "alembic_head": "rev001",
        "storage": storage,
        "encrypted": False,
        "object_count": object_count,
        "files": files,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return backup_dir


def _make_encrypted_backup(tmp_path: Path) -> Path:
    """Bản sao lưu đã mã hoá (giả) — chỉ `.age` + `manifest.json`, SHA khớp.

    `age` giả chỉ copy byte nguyên xi (không mã hoá thật), nên `objects.tar.age` phải
    chứa tar hợp lệ để `tar -xf` sau khi "giải mã" không hỏng.
    """
    backup_dir = tmp_path / "backup" / "20260101T023000Z"
    backup_dir.mkdir(parents=True)
    db_age = b"AGE-ENCRYPTED:db"
    tar_path = tmp_path / "objects-src.tar"
    with tarfile.open(tar_path, "w") as tar:
        member_path = tmp_path / "a.bin"
        member_path.write_bytes(b"object-a")
        tar.add(member_path, arcname="a.bin")
    objects_age = tar_path.read_bytes()
    (backup_dir / "db.dump.age").write_bytes(db_age)
    (backup_dir / "objects.tar.age").write_bytes(objects_age)
    manifest = {
        "created_at": "2026-01-01T02:30:00Z",
        "alembic_head": "rev001",
        "storage": "s3",
        "encrypted": True,
        "object_count": 1,
        "files": {"db.dump.age": _sha256(db_age), "objects.tar.age": _sha256(objects_age)},
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return backup_dir


def _run_restore(
    tmp_path: Path, backup_dir: Path, *, extra_env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Chạy `restore.sh <backup_dir>`; `stdin=""` tránh treo ở lệnh đọc stdin không redirect."""
    bin_dir, scripts_dir = _support_dir(tmp_path)
    log = tmp_path / "log"
    env = {
        "FAKE_LOG": str(log),
        "APPBACK_SCRIPTS_DIR": str(scripts_dir),
        "BACKUP_AGE_IDENTITY": "/dummy/identity",
        **appback_dir(tmp_path / "opt-appback", running=RUNNING),
        **(extra_env or {}),
    }
    result = run_script(RESTORE_SH, args=[str(backup_dir)], env=env, bin_dir=bin_dir, stdin="")
    return result, "\n".join(read_log(log))


def test_restore__sha_mismatch_exits_3_before_any_stop(tmp_path: Path) -> None:
    """Một tệp lệch SHA-256 → thoát 3, log không có lệnh `stop` nào (kiểm trước khi dừng gì)."""
    backup_dir = _make_plain_backup(tmp_path)
    (backup_dir / "db.dump").write_bytes(b"DA-BI-SUA-DOI")

    result, log = _run_restore(tmp_path, backup_dir)

    assert result.returncode == 3, result.stderr
    assert "stop" not in log


def test_restore__missing_file_in_manifest_exits_3(tmp_path: Path) -> None:
    """Tệp trong manifest.files không tồn tại trên đĩa → thoát 3."""
    backup_dir = _make_plain_backup(tmp_path)
    (backup_dir / "objects" / "projects" / "proj1" / "a.bin").unlink()

    result, log = _run_restore(tmp_path, backup_dir)

    assert result.returncode == 3, result.stderr
    assert "stop" not in log


def test_restore__command_order_stop_drop_create_restore_mirror_migrate_up_smoke(tmp_path: Path) -> None:
    """Thứ tự log: stop → DROP DATABASE … WITH (FORCE) → CREATE DATABASE →
    pg_restore → object → migrate → up → smoke.
    """
    backup_dir = _make_plain_backup(tmp_path)

    result, log = _run_restore(tmp_path, backup_dir)
    assert result.returncode == 0, result.stderr

    lines = log.splitlines()

    def first_index(needle: str) -> int:
        """Hàm phụ của test: first index."""
        return next(i for i, line in enumerate(lines) if needle in line)

    stop_i = first_index(" stop ")
    drop_i = first_index("DROP DATABASE")
    create_i = first_index("CREATE DATABASE")
    restore_i = first_index("pg_restore")
    mirror_i = first_index("mc mirror")
    migrate_i = first_index("run --rm migrate")  # `docker compose run --rm migrate`
    up_i = first_index(" up ")
    smoke_i = first_index("smoke ")

    assert "WITH (FORCE)" in lines[drop_i]
    assert stop_i < drop_i < create_i < restore_i < mirror_i < migrate_i < up_i < smoke_i


def test_restore__local_storage_command_order(tmp_path: Path) -> None:
    """storage=local: cùng thứ tự, khôi phục object qua tar thay vì mc mirror."""
    backup_dir = _make_plain_backup(tmp_path, storage="local")

    result, log = _run_restore(tmp_path, backup_dir)
    assert result.returncode == 0, result.stderr
    assert "tar -xf" in log
    assert "smoke " in log


def test_restore__encrypted_without_identity_exits_2_before_stop(tmp_path: Path) -> None:
    """Bản `.age` mà thiếu `BACKUP_AGE_IDENTITY` → thoát 2, trước mọi lệnh `stop`."""
    backup_dir = _make_encrypted_backup(tmp_path)

    result, log = _run_restore(tmp_path, backup_dir, extra_env={"BACKUP_AGE_IDENTITY": ""})

    assert result.returncode == 2, result.stderr
    assert "stop" not in log


def test_restore__encrypted_with_identity_decrypts_and_proceeds(tmp_path: Path) -> None:
    """Bản `.age` với `BACKUP_AGE_IDENTITY` hợp lệ → giải mã rồi chạy hết luồng."""
    backup_dir = _make_encrypted_backup(tmp_path)

    result, log = _run_restore(tmp_path, backup_dir)

    assert result.returncode == 0, result.stderr
    assert "smoke " in log


def test_restore__invalid_storage_exits_1_before_any_stop(tmp_path: Path) -> None:
    """Review round 1 P2 (SEC-02/LOG-02): `storage` lạ trong manifest → thoát 1 NGAY sau kiểm
    SHA-256, trước `stop`/`DROP DATABASE` — trước đây chỉ bị bắt ở bước khôi phục object,
    sau khi CSDL đã bị drop."""
    backup_dir = _make_plain_backup(tmp_path)
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["storage"] = "ftp"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result, log = _run_restore(tmp_path, backup_dir)

    assert result.returncode == 1, result.stderr
    assert "stop" not in log
    assert "DROP DATABASE" not in log


def test_restore__relative_backup_dir_is_resolved_to_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review round 1 P3 (SEC-03): `backup_dir` tương đối vẫn phải khôi phục đạt — Docker bind
    `-v` cần đường tuyệt đối, không tự giải theo cwd của script."""
    backup_dir = _make_plain_backup(tmp_path)
    monkeypatch.chdir(backup_dir.parent)
    relative = backup_dir.name

    bin_dir, scripts_dir = _support_dir(tmp_path)
    log = tmp_path / "log"
    env = {
        "FAKE_LOG": str(log),
        "APPBACK_SCRIPTS_DIR": str(scripts_dir),
        "BACKUP_AGE_IDENTITY": "/dummy/identity",
        **appback_dir(tmp_path / "opt-appback", running=RUNNING),
    }
    result = run_script(RESTORE_SH, args=[relative], env=env, bin_dir=bin_dir, stdin="")

    assert result.returncode == 0, result.stderr


def test_restore__missing_argument_exits_2(tmp_path: Path) -> None:
    """Không đối số → thoát 2."""
    bin_dir, _ = _support_dir(tmp_path)
    result = run_script(RESTORE_SH, args=[], env={"FAKE_LOG": str(tmp_path / "log")}, bin_dir=bin_dir, stdin="")
    assert result.returncode == 2, result.stderr


def test_restore__missing_directory_exits_2(tmp_path: Path) -> None:
    """Thư mục không tồn tại → thoát 2."""
    bin_dir, _ = _support_dir(tmp_path)
    result = run_script(
        RESTORE_SH,
        args=[str(tmp_path / "khong-ton-tai")],
        env={"FAKE_LOG": str(tmp_path / "log")},
        bin_dir=bin_dir,
        stdin="",
    )
    assert result.returncode == 2, result.stderr


def test_restore__local_objects_tar_goes_through_stdin(tmp_path: Path) -> None:
    """NO-327: `objects.tar` là 0600 của `deploy` (backup chạy dưới umask 077) nên container `api` (uid 10001) không
    đọc được nếu gắn nó làm volume — tar vào qua stdin (`-T`), host mở tệp."""
    backup_dir = _make_plain_backup(tmp_path, storage="local")
    result, log = _run_restore(tmp_path, backup_dir)
    assert result.returncode == 0, result.stderr
    (line,) = [entry for entry in log.splitlines() if "tar -xf" in entry]
    assert " -T " in line, line
    assert "/restore/objects.tar" not in line, line


def test_restore__brings_back_every_app_service_with_release_tag(tmp_path: Path) -> None:
    """L5-N07: bật lại api + đúng tập dịch vụ ứng dụng của compose (theo COMPOSE_PROFILES — có ml
    khi bật "ml"), compose nhận release.env (IMAGE_TAG = bản đang chạy) qua COMPOSE_ENV_FILES."""
    backup_dir = _make_plain_backup(tmp_path)
    result, log = _run_restore(tmp_path, backup_dir, extra_env={"COMPOSE_PROFILES": "ml"})
    assert result.returncode == 0, result.stderr
    assert "docker compose up -d api worker beat web ml ml-train" in log.splitlines()


def test_restore__no_release_yet_exits_2_before_any_stop(tmp_path: Path) -> None:
    """Không có release.env (chưa deploy lần nào) → thoát 2 trước mọi lệnh docker — không để hệ
    thống dừng nửa chừng vì compose không nội suy được IMAGE_TAG."""
    backup_dir = _make_plain_backup(tmp_path)
    root = tmp_path / "trong"
    result, log = _run_restore(tmp_path, backup_dir, extra_env=appback_dir(root))
    assert result.returncode == 2
    assert "chưa có bản phát hành nào" in result.stderr
    assert log == ""
