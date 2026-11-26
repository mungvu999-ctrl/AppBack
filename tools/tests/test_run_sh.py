"""`tools/verify/run.sh verify`: trần số log cổng giữ trên host (NO-090), dọn hỏng không chặn cổng (NO-092).

Chạy bản chép của `run.sh` thật trong một repo git tạm, `docker` giả trên `PATH` ghi lại đối số:
kiểm đúng đoạn shell mà lượt cổng chạy, không dựng container.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[2] / "tools" / "verify" / "run.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LOG_KEEP = 20
"""Trần `VERIFY_LOG_KEEP` của `run.sh`: số log tối đa trong thư mục sau một lượt, tính cả log của lượt đó."""


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """Repo git tạm (tên có dấu cách) chứa `tools/verify/run.sh` thật và một commit — `run.sh` đọc HEAD."""
    root = tmp_path / "cây làm việc"
    (root / "tools" / "verify").mkdir(parents=True)
    shutil.copy2(RUN_SH, root / "tools" / "verify" / "run.sh")
    shutil.copy2(RUN_SH.with_name("appfront_repo.sh"), root / "tools" / "verify" / "appfront_repo.sh")  # run.sh nạp nó
    git = [GIT, "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([GIT, "init", "-q", str(root)], check=True)  # noqa: S603 — lệnh cố định trên thư mục tạm
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)  # noqa: S603 — như trên
    return root


def _fake_command(bin_dir: Path, name: str, body: str) -> None:
    """Lệnh `sh` giả `name` trong `bin_dir` — `_run_verify` đặt `bin_dir` trước `PATH` thật."""
    bin_dir.mkdir(exist_ok=True)
    command = bin_dir / name
    command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    command.chmod(0o755)


def _run_verify(root: Path, bin_dir: Path) -> tuple[list[str], str]:
    """`bash run.sh verify` với `docker` giả (cùng lệnh giả khác đã đặt trong `bin_dir`); lượt phải thoát 0.

    Trả đối số `docker` nhận được (mỗi dòng một đối số) và stderr của `run.sh`.
    """
    calls = bin_dir / "docker-args"
    _fake_command(bin_dir, "docker", f'printf "%s\\n" "$@" > "{calls}"')
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(root / "tools" / "verify" / "run.sh"), "verify"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return calls.read_text(encoding="utf-8").splitlines(), result.stderr


def _old_logs(log_dir: Path, count: int) -> list[str]:
    """`count` log cổng cũ trong `log_dir`, mtime tăng dần theo thứ tự tên trả về."""
    log_dir.mkdir(parents=True)
    names = [f"20260901T0000{i:02d}Z-{i:012x}.log" for i in range(count)]
    for i, name in enumerate(names):
        (log_dir / name).write_text("lượt cũ\n", encoding="utf-8")
        os.utime(log_dir / name, (1_700_000_000 + i, 1_700_000_000 + i))
    return names


@pytest.mark.parametrize("existing", [0, LOG_KEEP - 1, LOG_KEEP, LOG_KEEP + 3])
def test_verify_giữ_log_mới_nhất_dưới_trần(worktree: Path, tmp_path: Path, existing: int) -> None:
    """NO-090: trước lượt còn đúng `LOG_KEEP - 1` log mới nhất (theo mtime); file khác trong thư mục để nguyên."""
    log_dir = worktree / ".cache" / "src-out" / "verify"
    names = _old_logs(log_dir, existing)
    (log_dir / "ghi-chú.txt").write_text("không phải log\n", encoding="utf-8")

    args, _ = _run_verify(worktree, tmp_path / "bin")

    kept = names[max(0, existing - (LOG_KEEP - 1)) :]
    assert sorted(p.name for p in log_dir.iterdir()) == sorted([*kept, "ghi-chú.txt"])
    assert any(a.startswith("VERIFY_LOG_FILE=/src-out/verify/") for a in args)


def test_verify_vẫn_chạy_cổng_khi_dọn_log_cũ_hỏng(worktree: Path, tmp_path: Path) -> None:
    """NO-092: `rm` hỏng (log cũ bị giữ trên Windows: "Device or resource busy") chỉ để lại một dòng cảnh báo.

    Dọn log là việc phụ: `docker` vẫn được gọi, mã thoát là của cổng (0), log cũ còn nguyên.
    """
    log_dir = worktree / ".cache" / "src-out" / "verify"
    names = _old_logs(log_dir, LOG_KEEP)
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "rm", 'echo "rm: cannot remove $2: Device or resource busy" >&2; exit 1')

    args, stderr = _run_verify(worktree, bin_dir)

    assert any(a.startswith("VERIFY_LOG_FILE=/src-out/verify/") for a in args)
    assert "Device or resource busy" in stderr
    assert "dọn log cổng cũ hỏng" in stderr
    assert sorted(p.name for p in log_dir.iterdir()) == sorted(names)


def test_gc_lowercases_and_normalizes_worktree_names(worktree: Path, tmp_path: Path) -> None:
    """NO-101/NO-164: `tr -c 'a-z0-9_-\\n' '-'` cũ thoát 1 ("range-endpoints … reverse collating
    sequence order") ngay khi tên worktree có cả `_` lẫn `-`, chặn `gc` trước khi gọi `docker`.

    Thêm một worktree tên `B7-01_Extra` (chữ hoa + gạch dưới, mẫu gây lỗi thật đã gặp khi dọn B7-01)
    rồi kiểm `VERIFY_VALID_NAMES` truyền cho container `gc` có đúng tên đã chuẩn hoá.
    """
    extra = tmp_path / "B7-01_Extra"
    subprocess.run(  # noqa: S603 — git của repo tạm, không nhận input người dùng
        [GIT, "-C", str(worktree), "worktree", "add", "-q", "--detach", str(extra)],
        check=True,
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = bin_dir / "docker-args"
    _fake_command(bin_dir, "docker", f'printf "%s\\n" "$@" > "{calls}"')
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(worktree / "tools" / "verify" / "run.sh"), "gc"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    args = calls.read_text(encoding="utf-8").splitlines()
    valid_names_arg = next(a for a in args if a.startswith("VERIFY_VALID_NAMES="))
    names = valid_names_arg.removeprefix("VERIFY_VALID_NAMES=").split(",")
    assert "b7-01_extra" in names


def _gc_section() -> str:
    """Nhánh `gc)` của `run.sh` (từ `  gc)` tới `;;` đầu tiên sau nó)."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index("\n  gc)")
    return text[start : text.index(";;", start)]


def test_gc__git_chạy_ở_cwd_không_truyền_đường() -> None:
    """NO-205: `run.sh` đã `cd "$REPO_ROOT"`, nên nhánh `gc` gọi `git` ở thư mục hiện tại, không truyền đường nào.

    Truyền `git -C "$REPO_ROOT"` là chính lỗi cũ: dưới `MSYS_NO_PATHCONV=1`, `$REPO_ROOT` giữ dạng `/f/...` mà git
    Windows không hiểu → thoát 128 ngay ở phép gán `valid_names`. Test quét tĩnh vì lỗi thật chỉ ra trên Git Bash
    Windows (`PATH=<docker giả> bash tools/verify/run.sh gc` thoát 128 trước khi sửa, 0 sau khi sửa).
    """
    section = _gc_section()
    assert "git -C" not in section  # `run.sh` đã `cd` vào gốc repo: git dùng thư mục hiện tại, không cần đường nào
    assert "$REPO_ROOT" not in section


def test_shell__build_xong_trước_khi_đọc_stdin(worktree: Path, tmp_path: Path) -> None:
    """NO-299: `compose run --build` đọc stdin cho `buildx bake -f -` và nuốt script nhiều dòng của `shell`.

    `docker` giả: lệnh có `--build` ăn hết stdin (như buildx thật), lệnh `run` còn lại ghi lại stdin nhận được.
    Script phải tới được lệnh `run` — tức bước build tách riêng, không đọc stdin.
    """
    bin_dir = tmp_path / "bin"
    seen = bin_dir / "stdin-seen"
    _fake_command(
        bin_dir,
        "docker",
        f'case " $* " in *" --build "*) cat > /dev/null ;; *" run "*) cat > "{seen}" ;; esac',
    )
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    script = "echo một\necho hai\n"
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(worktree / "tools" / "verify" / "run.sh"), "shell"],
        env=env,
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert seen.read_text(encoding="utf-8") == script


def test_volume_work_được_tạo_tường_minh_trước_lượt_compose(worktree: Path, tmp_path: Path) -> None:
    """NO-185: `appback-work` là `external` trong `verify.yml`, nên `run.sh` phải `docker volume create` nó
    trước mọi lệnh compose — nếu không, lượt đầu trên máy sạch (CI) hỏng vì volume chưa có."""
    bin_dir = tmp_path / "bin"
    log = bin_dir / "docker-calls"
    _fake_command(bin_dir, "docker", f'echo "$*" >> "{log}"')
    # Tắt dò stack dùng chung (PERF-10): test này chỉ kiểm thứ tự volume → build → compose.
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "VERIFY_SHARED_STACK": "off"}
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(worktree / "tools" / "verify" / "run.sh"), "verify"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "volume create appback-work"
    assert calls[1].startswith("image inspect ")  # PERF-03: nhãn ảnh trùng băm Dockerfile thì bỏ `compose build`
    assert calls[2].startswith("compose ")


def test_shell__báo_làm_việc_trên_bản_chép(worktree: Path, tmp_path: Path) -> None:
    """NO-190: `shell` in một dòng stderr nói thẳng `/tmp/w` là bản chép, sửa trong đó không về worktree."""
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "docker", "cat > /dev/null")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(worktree / "tools" / "verify" / "run.sh"), "shell"],
        env=env,
        input="true\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "BẢN CHÉP /tmp/w" in result.stderr


def test_verify__junit_mồ_côi_bị_dọn_cùng_log(worktree: Path, tmp_path: Path) -> None:
    """NO-324: `<log>.junit.xml` không có log đi kèm (log đã bị dọn theo trần) bị dọn; junit của log còn giữ."""
    log_dir = worktree / ".cache" / "src-out" / "verify"
    names = _old_logs(log_dir, LOG_KEEP + 2)
    for name in names:
        (log_dir / name.replace(".log", ".junit.xml")).write_text("<testsuites/>", encoding="utf-8")

    _run_verify(worktree, tmp_path / "bin")

    kept = [n.replace(".log", ".junit.xml") for n in names[3:]]
    assert sorted(p.name for p in log_dir.glob("*.junit.xml")) == sorted(kept)


@pytest.mark.parametrize("task", ["verify", "gc"])
def test_mọi_việc_build_riêng_trước_run_không_còn_run_build(worktree: Path, tmp_path: Path, task: str) -> None:
    """NO-299: `run --build` cũ đọc stdin ở mọi việc; nay `build verify` đứng riêng, trước `run` (verify, gc)."""
    bin_dir = tmp_path / "bin"
    log = bin_dir / "docker-calls"
    _fake_command(bin_dir, "docker", f'echo "$*" >> "{log}"')
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(worktree / "tools" / "verify" / "run.sh"), task],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert not any("--build" in c for c in calls)
    build = next(i for i, c in enumerate(calls) if c.endswith(" build verify"))
    run = next(i for i, c in enumerate(calls) if " run --rm" in c)
    assert build < run


def test_verify__junit_perf_mồ_côi_bị_dọn_cùng_log(worktree: Path, tmp_path: Path) -> None:
    """NO-324: `<log>.perf.junit.xml` đi cùng log như `<log>.junit.xml`: còn log thì giữ, mất log thì dọn."""
    log_dir = worktree / ".cache" / "src-out" / "verify"
    names = _old_logs(log_dir, LOG_KEEP + 2)
    for name in names:
        (log_dir / name.replace(".log", ".perf.junit.xml")).write_text("<testsuites/>", encoding="utf-8")

    _run_verify(worktree, tmp_path / "bin")

    kept = [n.replace(".log", ".perf.junit.xml") for n in names[3:]]
    assert sorted(p.name for p in log_dir.glob("*.perf.junit.xml")) == sorted(kept)


# `docker` giả cho PERF-10: trả lời `inspect` về stack f14 (chạy hay không theo `$STACK_RUNNING`, env, IP),
# mọi lệnh khác ghi `TEST_SHARED_*` mà `compose` nhận được.
_STACK_DOCKER = """
if [ "$1" = inspect ]; then
  case "$3" in
    *State.Running*) echo "$STACK_RUNNING" ;;
    *Config.Env*) case "$4" in
        *postgres*) printf 'POSTGRES_USER=app\\nPOSTGRES_PASSWORD=p@ss:w/rd\\n' ;;
        *minio*) printf 'MINIO_ROOT_USER=mu\\nMINIO_ROOT_PASSWORD=ms,x\\n' ;;
      esac ;;
    *IPAddress*) case "$4" in
        *postgres*) echo 10.9.0.2 ;; *broker*) echo 10.9.0.3 ;; *cache*) echo 10.9.0.4 ;; *minio*) echo 10.9.0.5 ;;
      esac ;;
  esac
  exit 0
fi
env | grep '^TEST_SHARED_' | sort > "$SHARED_ENV_FILE"
"""


def _run_with_stack(root: Path, tmp_path: Path, **env_extra: str) -> tuple[dict[str, str], str]:
    """`run.sh verify` với `docker` giả của stack; trả `TEST_SHARED_*` compose nhận được và stdout+stderr."""
    bin_dir = tmp_path / "bin"
    _fake_command(bin_dir, "docker", _STACK_DOCKER)
    env_file = tmp_path / "shared-env"
    base = {k: v for k, v in os.environ.items() if not k.startswith("TEST_SHARED_") and k != "VERIFY_SHARED_STACK"}
    env = {**base, **env_extra, "SHARED_ENV_FILE": str(env_file), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(root / "tools" / "verify" / "run.sh"), "verify"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    pairs = (line.partition("=") for line in env_file.read_text(encoding="utf-8").splitlines())
    return {name: value for name, _, value in pairs}, result.stdout + result.stderr


def test_verify__running_stack_hands_its_services_to_the_tests(worktree: Path, tmp_path: Path) -> None:
    """PERF-10: f14 chạy → compose nhận địa chỉ IP trên `f14_appback` + mật khẩu đọc từ env container stack.

    User/mật khẩu được mã hoá phần trăm trong URL; mật khẩu không hiện ra stdout/stderr (log cổng).
    """
    shared, output = _run_with_stack(worktree, tmp_path, STACK_RUNNING="true")

    assert shared == {
        "TEST_SHARED_NETWORK": "f14_appback",
        "TEST_SHARED_POSTGRES_URL": "postgresql+asyncpg://app:p%40ss%3Aw%2Frd@10.9.0.2:5432/postgres",
        "TEST_SHARED_REDIS_BROKER_URL": "redis://10.9.0.3:6379/0",
        "TEST_SHARED_REDIS_CACHE_URL": "redis://10.9.0.4:6379/0",
        "TEST_SHARED_MINIO": "10.9.0.5:9000,mu,ms,x",
    }
    assert "p@ss" not in output
    assert "p%40ss" not in output
    assert "ms,x" not in output


@pytest.mark.parametrize(
    "env_extra",
    [{"STACK_RUNNING": "false"}, {"STACK_RUNNING": "true", "VERIFY_SHARED_STACK": "off"}],
    ids=["stack-tắt", "off"],
)
def test_verify__no_stack_means_own_containers(worktree: Path, tmp_path: Path, env_extra: dict[str, str]) -> None:
    """Stack không chạy, hay `VERIFY_SHARED_STACK=off`: không biến nào → test dựng container như trước."""
    shared, _ = _run_with_stack(worktree, tmp_path, **env_extra)
    assert shared == {}


def _changed(root: Path, tmp_path: Path, *args: str) -> set[str]:
    """`run.sh verify <args>` với `docker` giả; trả tập đường `VERIFY_CHANGED` mà cổng trao cho container."""
    bin_dir = tmp_path / "bin"
    out = tmp_path / "changed"
    _fake_command(bin_dir, "docker", f'printf "%s" "$VERIFY_CHANGED" > "{out}"')
    result = subprocess.run(  # noqa: S603 — bash + run.sh của repo tạm
        [BASH, str(root / "tools" / "verify" / "run.sh"), "verify", *args],
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return set(out.read_text(encoding="utf-8").splitlines())


def test_verify_fast__stacked_branch_compares_with_its_pushed_parent(worktree: Path, tmp_path: Path) -> None:
    """PERF-13: cổng nhanh của nhánh xếp chồng chỉ thấy phần của chính nó, kể cả sau khi nó đã được đẩy.

    Cổng đầy đủ vẫn so với `main` nên thấy cả tệp của lát trước.
    """
    git = [GIT, "-C", str(worktree), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "branch", "-M", "main"], check=True)  # noqa: S603 — lệnh cố định trên thư mục tạm
    subprocess.run([*git, "checkout", "-q", "-b", "feature/child"], check=True)  # noqa: S603 — như trên
    for name, pushed_as in (("parent.txt", "feature/parent"), ("child.txt", "feature/child")):
        (worktree / name).write_text("x\n", encoding="utf-8")
        (worktree / f"more-{name}").write_text("x\n", encoding="utf-8")  # lát nào cũng lớn hơn nhánh gộp thêm bên dưới
        subprocess.run([*git, "add", name, f"more-{name}"], check=True)  # noqa: S603 — như trên
        subprocess.run([*git, "commit", "-q", "-m", name], check=True)  # noqa: S603 — như trên
        subprocess.run([*git, "update-ref", f"refs/remotes/origin/{pushed_as}", "HEAD"], check=True)  # noqa: S603

    slices = {"parent.txt", "child.txt", "side.txt"}
    assert _changed(worktree, tmp_path, "--fast") & slices == {"child.txt"}
    assert _changed(worktree, tmp_path) & slices == {"parent.txt", "child.txt"}

    # Gộp thêm một nhánh đã đẩy khác: hai gốc ngang hàng, cổng nhanh lấy gốc cho ít tệp đổi nhất (lát trước).
    subprocess.run([*git, "checkout", "-q", "-b", "feature/side", "main"], check=True)  # noqa: S603 — như trên
    (worktree / "side.txt").write_text("x\n", encoding="utf-8")
    subprocess.run([*git, "add", "side.txt"], check=True)  # noqa: S603 — như trên
    subprocess.run([*git, "commit", "-q", "-m", "side"], check=True)  # noqa: S603 — như trên
    subprocess.run([*git, "update-ref", "refs/remotes/origin/feature/side", "HEAD"], check=True)  # noqa: S603
    subprocess.run([*git, "checkout", "-q", "feature/child"], check=True)  # noqa: S603 — như trên
    subprocess.run([*git, "merge", "-q", "--no-ff", "-m", "gộp", "feature/side"], check=True)  # noqa: S603 — như trên
    assert _changed(worktree, tmp_path, "--fast") & slices == {"child.txt", "side.txt"}
