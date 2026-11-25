"""Test `tools.ci.import_all` — [8] "import_all": nhập hỏng khi thiếu phụ thuộc của app.

Mọi test dùng gói `apps.<app>` tạm đều chạy trong **tiến trình Python mới** (`subprocess.run`,
BE-00 §12): tên module luôn là `apps.<app>...` bất kể `--root` trỏ đâu, nên nếu chạy trong cùng
tiến trình pytest thì `sys.modules["apps"]` từ test khác (hoặc từ chính repo thật) sẽ che mất gói
tạm — không tái hiện được lỗi cần kiểm, và để lại rác trong `sys.modules` của tiến trình test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci.import_all import main

_RUNNER = """
import os, sys
if os.environ.get("BLOCK_FASTAPI"):
    sys.modules["fastapi"] = None
if os.environ.get("PATCH_SCHEDULES"):
    from packages.messaging import schedules
    calls = []
    if os.environ.get("SCHEDULE_FAILS"):
        def _boom():
            raise RuntimeError("lich hong")
        schedules.discover_jobs = _boom
    else:
        schedules.discover_jobs = lambda: calls.append("discover_jobs")
    schedules.beat_schedule = lambda: calls.append("beat_schedule") or {}
from tools.ci.import_all import main
code = main(sys.argv[1:])
sentinel = os.environ.get("SENTINEL_FILE")
if sentinel:
    from pathlib import Path
    Path(sentinel).write_text(",".join(calls))
sys.exit(code)
"""


def _make_app(root: Path, app: str, *, extra_files: dict[str, str] | None = None) -> None:
    """Gói `apps.<app>` tạm hợp lệ: `__init__.py`, một module thường, một thư mục `tests/` bị loại khỏi nhập."""
    (root / "apps").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "__init__.py").write_text("", encoding="utf-8")
    app_dir = root / "apps" / app
    app_dir.mkdir(exist_ok=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = app_dir / "tests"
    tests_dir.mkdir()
    # module lỗi cố ý dưới tests/: import_all phải bỏ qua, không nhập nhầm
    (tests_dir / "test_broken_should_be_skipped.py").write_text("import mot_module_khong_ton_tai\n", encoding="utf-8")
    for name, content in (extra_files or {}).items():
        (app_dir / name).write_text(content, encoding="utf-8")


def _run_subprocess(
    app: str, root: Path, *, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(  # noqa: S603 -- gọi chính python.exe của tiến trình test, không phải input ngoài
        [sys.executable, "-c", _RUNNER, app, "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


@pytest.mark.parametrize("app", ["api", "worker", "ml"])
def test_valid_temp_app_package_succeeds(tmp_path: Path, app: str) -> None:
    _make_app(tmp_path, app)
    result = _run_subprocess(app, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "import_all: đã nhập" in result.stdout


def test_worker_job_importing_missing_fastapi_fails(tmp_path: Path) -> None:
    _make_app(tmp_path, "worker", extra_files={"jobs.py": "import fastapi\n"})
    result = _run_subprocess("worker", tmp_path, env_extra={"BLOCK_FASTAPI": "1"})
    assert result.returncode == 1
    assert "apps.worker.jobs" in result.stderr
    assert "fastapi" in result.stderr


def test_worker_calls_discover_jobs_and_beat_schedule(tmp_path: Path) -> None:
    _make_app(tmp_path, "worker")
    sentinel = tmp_path / "sentinel.txt"
    result = _run_subprocess("worker", tmp_path, env_extra={"PATCH_SCHEDULES": "1", "SENTINEL_FILE": str(sentinel)})
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == "discover_jobs,beat_schedule"


def test_worker_schedule_failure_counts_as_import_all_failure(tmp_path: Path) -> None:
    _make_app(tmp_path, "worker")
    result = _run_subprocess("worker", tmp_path, env_extra={"PATCH_SCHEDULES": "1", "SCHEDULE_FAILS": "1"})
    assert result.returncode == 1
    assert "packages.messaging.schedules" in result.stderr
    assert "lich hong" in result.stderr


def test_unknown_app_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["bogus"])
    assert exc_info.value.code == 2
