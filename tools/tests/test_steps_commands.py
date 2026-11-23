"""tools/verify/steps.py — 8 bước và các việc lock/openapi/merge-heads/gc/shell.

Lệnh con (ruff, mypy, pytest…) được thay bằng bộ ghi khi chỉ kiểm logic điều
phối; openapi (module giả) và merge-heads (alembic thật trên thư mục tạm) chạy
tiến trình thật.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, cast

import pytest

from tools.contract import runner_client
from tools.contract.runner_client import RunnerError
from tools.verify import steps


@dataclass
class FakeRun:
    """Thay `steps._run`: ghi lệnh, trả mã thoát theo `rules` (khớp chuỗi con đầu tiên)."""

    rules: dict[str, tuple[int, str]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        """Ghi lệnh vào `calls`, trả mã thoát và stdout của quy tắc đầu tiên có chuỗi con khớp (mặc định 0)."""
        self.calls.append(cmd)
        joined = " ".join(cmd)
        for needle, (rc, stdout) in self.rules.items():
            if needle in joined:
                return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def ran(self, needle: str) -> bool:
        """Đúng nếu có lệnh đã ghi chứa chuỗi `needle`."""
        return any(needle in " ".join(c) for c in self.calls)


@dataclass
class FakeProc:
    """Thay `subprocess.Popen` của mypy nền: `wait`/`poll`/`kill` ghi mốc vào `events`."""

    returncode: int
    events: list[str]
    done: bool = False

    def wait(self) -> int:
        """Chờ xong: ghi mốc "wait", từ đó `poll` trả mã thoát."""
        self.events.append("wait")
        self.done = True
        return self.returncode

    def poll(self) -> int | None:
        """None khi còn chạy (bước 5 kiểm điều này), mã thoát sau khi `wait`/`kill`."""
        return self.returncode if self.done else None

    def kill(self) -> None:
        """Giết tiến trình nền khi lượt dừng sớm."""
        self.events.append("kill")
        self.done = True


def _starter(events: list[str], proc: FakeProc) -> Callable[[], FakeProc]:
    """Thay `steps.start_mypy`: ghi mốc "start" rồi trả `proc` (không dựng tiến trình thật)."""

    def start(env: dict[str, str] | None = None) -> FakeProc:
        """Ghi mốc (kèm thư mục cache mypy nếu lượt truyền env riêng) rồi trả tiến trình giả."""
        events.append("start" if env is None else f"start {env.get('MYPY_CACHE_DIR')}")
        return proc

    return start


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo giả trong thư mục tạm: trỏ REPO_ROOT/OUT_DIR/WORK_DIR vào đó và xoá các biến môi trường của lượt thật."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(steps, "REPO_ROOT", root)
    monkeypatch.setattr(steps, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(steps, "WORK_DIR", tmp_path / "work")
    monkeypatch.delenv("VERIFY_BRANCH", raising=False)
    monkeypatch.delenv("VERIFY_CHANGED", raising=False)
    # Lượt cổng thật (bước 5) đặt biến này: test không được ghi vào log thật của lượt đang chạy nó
    monkeypatch.delenv("VERIFY_LOG_FILE", raising=False)
    # coverage `patch = subprocess` đo mọi tiến trình con qua biến này; tiến trình
    # con ở đây chạy trong repo giả (module openapi giả…) — không được lẫn vào số đo
    monkeypatch.delenv("COVERAGE_PROCESS_CONFIG", raising=False)
    # Mypy nền của cổng đầy đủ (C1) đi qua `_popen`: chặn ở đây để không test nào dựng mypy thật,
    # đúng như `_fake` chặn `_run`. Test cần hành vi riêng thì thay `steps.start_mypy`.
    monkeypatch.setattr(steps, "_popen", lambda cmd, **kw: FakeProc(0, []))
    # `start_mypy` mở MYPY_LOG bằng "wb": trỏ thật (/tmp/mypy.log) thì test xoá rỗng log của mypy nền
    # đang chạy trong chính lượt cổng (review PERF-01 F1)
    monkeypatch.setattr(steps, "MYPY_LOG", tmp_path / "mypy.log")
    return root


def _fake(monkeypatch: pytest.MonkeyPatch, **rules: tuple[int, str]) -> FakeRun:
    """Thay `steps._run` bằng `FakeRun`; tên quy tắc đổi `__` thành khoảng trắng."""
    fake = FakeRun(rules={k.replace("__", " "): v for k, v in rules.items()})
    monkeypatch.setattr(steps, "_run", fake)
    return fake


def _touch(root: Path, rel: str) -> None:
    """Tạo file rỗng `rel` dưới `root` (kèm thư mục cha)."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


# --- bước 1-4 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "number"),
    [
        (steps.step_ruff_format, "1"),
        (steps.step_ruff_check, "2"),
        (steps.step_mypy, "3"),
        (steps.step_lint_imports, "4"),
    ],
)
@pytest.mark.parametrize(("rc", "status"), [(0, steps.STATUS_OK), (1, steps.STATUS_FAIL)])
def test_bước_1_đến_4_theo_mã_thoát(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    fn: Callable[[], steps.StepOutcome],
    number: str,
    rc: int,
    status: str,
) -> None:
    """Bước 1-4 đạt khi lệnh thoát 0, hỏng khi khác 0, kèm đúng số bước."""
    monkeypatch.setattr(steps, "_run", FakeRun(rules={"": (rc, "")}))
    outcome = fn()
    assert (outcome.number, outcome.status) == (number, status)


# --- bước 5 -------------------------------------------------------------------------


def test_bước_5_đạt_gọi_đủ_chuỗi(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bước 5 đạt: gọi pytest, rồi coverage json, rồi coverage_gate."""
    monkeypatch.delenv(steps.PYTEST_WORKERS_ENV, raising=False)
    fake = _fake(monkeypatch)
    assert steps.step_coverage().status == steps.STATUS_OK
    # FIX-112: pytest-cov gộp sẵn dữ liệu của mọi tiến trình xdist → không còn `coverage combine`
    assert [c[:2] for c in fake.calls][:2] == [["pytest", "-n"], ["coverage", "json"]]
    assert fake.ran("tools.coverage_gate")


def test_bước_5_chạy_song_song_dưới_cov(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bước 5 phải mang `-n`, `--dist loadfile` và `--cov`; thiếu `--cov` là độ phủ tụt về 0."""
    monkeypatch.delenv(steps.PYTEST_WORKERS_ENV, raising=False)
    fake = _fake(monkeypatch)
    steps.step_coverage()
    assert fake.calls[0] == [
        "pytest",
        "-n",
        steps.DEFAULT_PYTEST_WORKERS,
        "--dist",
        "loadfile",
        "--cov",
        "--cov-report=",
    ]
    assert not fake.ran("coverage combine")


def test_bước_5_pytest_hỏng_không_gọi_gate(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pytest hỏng thì bước 5 hỏng và không gọi coverage json hay coverage_gate."""
    fake = _fake(monkeypatch, pytest=(1, ""))
    assert steps.step_coverage().status == steps.STATUS_FAIL
    assert not fake.ran("tools.coverage_gate")
    assert not fake.ran("coverage json")


def test_bước_5_coverage_json_hỏng_không_bị_nuốt(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # vd cấu hình [paths] sai: coverage json hỏng thì bước 5 hỏng, không lặng lẽ đi tiếp
    """`coverage json` hỏng thì bước 5 hỏng, không gọi coverage_gate."""
    fake = _fake(monkeypatch, coverage__json=(1, ""))
    assert steps.step_coverage().status == steps.STATUS_FAIL
    assert not fake.ran("tools.coverage_gate")


def test_bước_5_gate_hỏng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """coverage_gate thoát khác 0 thì bước 5 hỏng."""
    _fake(monkeypatch, **{"tools.coverage_gate": (1, "")})
    assert steps.step_coverage().status == steps.STATUS_FAIL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", "2"),
        (" 4 ", "4"),
        ("", steps.DEFAULT_PYTEST_WORKERS),
        ("auto", steps.DEFAULT_PYTEST_WORKERS),
        ("0", steps.DEFAULT_PYTEST_WORKERS),
        ("-3", steps.DEFAULT_PYTEST_WORKERS),
        ("2.5", steps.DEFAULT_PYTEST_WORKERS),
    ],
)
def test_số_tiến_trình_bước_5_theo_env(monkeypatch: pytest.MonkeyPatch, value: str, expected: str) -> None:
    """`VERIFY_PYTEST_WORKERS` đặt `-n`; giá trị lạ về mặc định thay vì truyền tiếp cho xdist."""
    monkeypatch.setenv(steps.PYTEST_WORKERS_ENV, value)
    assert steps.pytest_workers() == expected


def test_số_tiến_trình_bước_5_mặc_định(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không đặt biến môi trường thì số tiến trình bước 5 là mặc định."""
    monkeypatch.delenv(steps.PYTEST_WORKERS_ENV, raising=False)
    assert steps.pytest_workers() == steps.DEFAULT_PYTEST_WORKERS == "6"  # 10 bị OOM (PERF-03)


# --- bước 5b ------------------------------------------------------------------------


def test_5b_không_đơn_vị_bị_chạm_bỏ_pytest_perf(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không có đơn vị bị chạm thì bước 5b không collect pytest perf nhưng vẫn chạy case_gate."""
    fake = _fake(monkeypatch)
    outcome = steps.step_perf()
    assert (outcome.status, outcome.detail) == (steps.STATUS_OK, "perf: 0 đơn vị bị chạm")
    assert not fake.ran("--collect-only")
    assert fake.ran("tools.case_gate")


def test_5b_đơn_vị_bị_chạm_có_perf_thì_chạy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đơn vị bị chạm có test perf thì bước 5b chạy pytest perf chỉ trên các tệp perf đã collect."""
    (repo / "packages" / "vision").mkdir(parents=True)
    monkeypatch.setenv("VERIFY_CHANGED", "packages/vision/x.py\ndocs/a.md")
    fake = _fake(monkeypatch, **{"--collect-only": (0, "packages/vision/tests/test_p.py::test_speed\n\n1 test\n")})
    outcome = steps.step_perf()
    assert (outcome.status, outcome.detail) == (steps.STATUS_OK, "perf: 1 test")
    perf_run = [c for c in fake.calls if "--junitxml=/tmp/junit-perf.xml" in c]
    perf_file = "packages/vision/tests/test_p.py"
    assert perf_run == [["pytest", "-m", "perf and not gpu", "--junitxml=/tmp/junit-perf.xml", perf_file]]


def test_5b_đơn_vị_bị_chạm_không_có_perf_thì_bỏ(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đơn vị bị chạm không có test perf thì bước 5b bỏ qua lượt chạy perf."""
    (repo / "packages" / "vision").mkdir(parents=True)
    monkeypatch.setenv("VERIFY_CHANGED", "packages/vision/x.py")
    fake = _fake(monkeypatch, **{"--collect-only": (5, "no tests collected\n")})
    assert steps.step_perf().detail == "perf: 0 đơn vị bị chạm"
    assert not fake.ran("junit-perf")


def test_5b_integration_chạy_mọi_perf(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nhánh integration: bước 5b collect perf không giới hạn đường dẫn."""
    monkeypatch.setenv("VERIFY_BRANCH", "integration")
    fake = _fake(monkeypatch, **{"--collect-only": (0, "a/tests/test_p.py::test_speed\n")})
    assert steps.step_perf().status == steps.STATUS_OK
    collect = next(c for c in fake.calls if "--collect-only" in c)
    assert collect[-1] == "--junitxml=/tmp/junit-collect.xml"  # không giới hạn đường


def test_5b_perf_mang_tên_case_hỏng_trước_khi_chạy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test perf mang tên case thì bước 5b hỏng, không chạy case_gate."""
    monkeypatch.setenv("VERIFY_BRANCH", "integration")
    fake = _fake(monkeypatch, **{"--collect-only": (0, "a/tests/test_p.py::test_x_create__C04\n")})
    outcome = steps.step_perf()
    assert outcome.status == steps.STATUS_FAIL
    assert "test_x_create__C04" in outcome.detail
    assert not fake.ran("tools.case_gate")


def test_5b_perf_hỏng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pytest perf thoát khác 0 thì bước 5b hỏng."""
    monkeypatch.setenv("VERIFY_BRANCH", "integration")
    _fake(monkeypatch, **{"--collect-only": (0, "a/tests/test_p.py::test_speed\n"), "junit-perf": (1, "")})
    assert steps.step_perf().status == steps.STATUS_FAIL


def test_5b_case_gate_hỏng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """case_gate thoát khác 0 thì bước 5b hỏng."""
    _fake(monkeypatch, **{"tools.case_gate": (1, "")})
    assert steps.step_perf().status == steps.STATUS_FAIL


# --- bước 6-8: file điều kiện -------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "cond", "owner"),
    [
        (steps.step_migrations, "packages/db/migrate_check.py", "B0-03"),
        (steps.step_contract, "tools/contract/check.py", "B0-07"),
        (steps.step_openapi, "apps/api/core/openapi.py", "B0-06"),
    ],
)
def test_bước_6_8_không_áp_dụng_rồi_hỏng_rồi_chạy(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    fn: Callable[[], steps.StepOutcome],
    cond: str,
    owner: str,
) -> None:
    """Bước 6-8: không áp dụng khi thiếu file điều kiện, hỏng khi chủ đã hợp nhất, chạy khi có file."""
    fake = _fake(monkeypatch)
    assert fn().status == steps.STATUS_NA
    _touch(repo, f"changes/{owner}.md")
    assert fn().status == steps.STATUS_FAIL
    assert fake.calls == []
    _touch(repo, cond)
    assert fn().status == steps.STATUS_OK
    assert fake.calls


def test_bước_6_lint_hỏng_không_gọi_migrate_check(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """lint_migrations hỏng thì bước 6 hỏng và không gọi migrate_check."""
    _touch(repo, "packages/db/migrate_check.py")
    fake = _fake(monkeypatch, **{"tools.lint_migrations": (1, "")})
    assert steps.step_migrations().status == steps.STATUS_FAIL
    assert not fake.ran("packages.db.migrate_check")


def test_bước_6_migrate_check_hỏng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """migrate_check thoát khác 0 thì bước 6 hỏng."""
    _touch(repo, "packages/db/migrate_check.py")
    _fake(monkeypatch, **{"packages.db.migrate_check": (1, "")})
    assert steps.step_migrations().status == steps.STATUS_FAIL


def test_bước_7_hỏng_theo_mã_thoát(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bước 7 hỏng khi tools.contract.check thoát khác 0."""
    _touch(repo, "tools/contract/check.py")
    _fake(monkeypatch, **{"tools.contract.check": (1, "")})
    assert steps.step_contract().status == steps.STATUS_FAIL


def test_bước_8_integration_so_với_bản_commit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-105: bản tham chiếu đã commit là `docs/contracts/openapi.json` — `openapi.json` ở gốc bị
    `.gitignore`, không có trên nhánh tích hợp/CI."""
    _touch(repo, "apps/api/core/openapi.py")
    monkeypatch.setenv("VERIFY_BRANCH", "integration")
    fake = _fake(monkeypatch)
    steps.step_openapi()
    assert fake.calls[0][-2:] == ["--compare", "docs/contracts/openapi.json"]


def test_bước_8_worker_không_so(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nhánh worker: bước 8 không truyền `--compare`, và hỏng theo mã thoát."""
    _touch(repo, "apps/api/core/openapi.py")
    fake = _fake(monkeypatch, **{"apps.api.core.openapi": (1, "")})
    assert steps.step_openapi().status == steps.STATUS_FAIL
    assert "--compare" not in fake.calls[0]


# --- verify / main ------------------------------------------------------------------


def test_main_verify_in_bảng_và_mã_thoát(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main verify` in bảng có dòng "chưa chạy" và thoát 1 khi một bước hỏng; `--steps` chọn đúng bước."""
    monkeypatch.setattr(
        steps,
        "_ALL_STEPS",
        [
            ("1", lambda: steps.StepOutcome("1", "a", steps.STATUS_OK)),
            ("2", lambda: steps.StepOutcome("2", "b", steps.STATUS_FAIL)),
            ("3", lambda: steps.StepOutcome("3", "c", steps.STATUS_OK)),
        ],
    )
    assert steps.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "chưa chạy" in out
    assert steps.main(["verify", "--steps", "1"]) == 0


def _mypy_tree(root: Path) -> None:
    """Dựng cây nguồn tối thiểu mypy đọc: `pyproject.toml` khai `files`, `uv.lock`, một module."""
    (root / "pyproject.toml").write_text('[tool.mypy]\nfiles = ["packages"]\n', encoding="utf-8")
    (root / "uv.lock").write_text("lock-v1", encoding="utf-8")
    _touch(root, "packages/core/a.py")


def test_mypy_source_hash__ổn_định_và_đổi_theo_từng_đầu_vào(repo: Path) -> None:
    """Băm xác định; đổi bất kỳ đầu vào nào (nguồn, lock, cấu hình, thêm file .pyi) là băm khác."""
    _mypy_tree(repo)
    assert steps.mypy_source_hash() == steps.mypy_source_hash()
    for rel, text in [
        ("packages/core/a.py", "x = 1\n"),
        ("uv.lock", "lock-v2"),
        ("pyproject.toml", '[tool.mypy]\nfiles = ["packages"]\nstrict = true\n'),
        ("packages/core/b.pyi", "y: int\n"),
    ]:
        before = steps.mypy_source_hash()
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
        assert steps.mypy_source_hash() != before, rel


def test_mypy_source_hash__bỏ_qua_pycache(repo: Path) -> None:
    """`__pycache__` là bytecode sinh ra, mypy không đọc: thêm vào không được đổi băm (cache sẽ trượt mỗi lượt)."""
    _mypy_tree(repo)
    base = steps.mypy_source_hash()
    _touch(repo, "packages/core/__pycache__/a.cpython-313.py")
    assert steps.mypy_source_hash() == base


def test_mypy_cache_dir__thư_mục_anh_em_theo_băm(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-306: cache nằm cạnh `MYPY_CACHE_DIR` cũ, tên `mypy-tree-<băm>`; đổi nguồn → thư mục khác (nguội)."""
    _mypy_tree(repo)
    monkeypatch.setenv("MYPY_CACHE_DIR", "/work/mypy-x")
    first = steps.mypy_cache_dir()
    assert Path(first).parent == Path("/work")
    assert Path(first).name == f"{steps.MYPY_CACHE_PREFIX}{steps.mypy_source_hash()}"
    (repo / "packages" / "core" / "a.py").write_text("z = 2\n", encoding="utf-8")
    assert steps.mypy_cache_dir() != first


def test_mypy_cache_dir__không_có_biến_thì_nguội(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chạy ngoài container (không `MYPY_CACHE_DIR`) thì giữ hành vi nguội `os.devnull`, không ghi cache lạ."""
    _mypy_tree(repo)
    monkeypatch.delenv("MYPY_CACHE_DIR", raising=False)
    assert steps.mypy_cache_dir() == os.devnull


def test_step_mypy__truyền_cache_băm_qua_env_không_đổi_môi_trường_lượt(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bước 3 tuần tự: mypy nhận `MYPY_CACHE_DIR` theo băm qua `env`, môi trường của lượt giữ giá trị cũ."""
    _mypy_tree(repo)
    monkeypatch.setenv("MYPY_CACHE_DIR", "/work/mypy-x")
    envs: list[str] = []

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        """Ghi `MYPY_CACHE_DIR` trong `env` truyền cho mypy."""
        envs.append(kw["env"]["MYPY_CACHE_DIR"])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(steps, "_run", fake_run)
    assert steps.step_mypy().status == steps.STATUS_OK
    assert envs == [steps.mypy_cache_dir()]
    assert os.environ["MYPY_CACHE_DIR"] == "/work/mypy-x"


def test_full_gate_steps__bước_3_chạy_sau_bước_5() -> None:
    """C1: mypy đã chạy nền từ đầu nên bước 3 chỉ còn là lượt chờ, xếp ngay sau bước 5 — bước khác giữ thứ tự."""
    order = [number for number, _ in steps.full_gate_steps(cast("subprocess.Popen[bytes]", None))]
    assert order.index("3") == order.index("5") + 1
    assert [n for n in order if n != "3"] == [n for n, _ in steps._ALL_STEPS if n != "3"]


def test_in_step_order__bảng_vẫn_theo_số_bước() -> None:
    """Bảng E.10 in theo thứ tự bước của `_ALL_STEPS` dù bước 3 chạy sau bước 5; dòng không phải bước xuống cuối."""
    outcomes = [
        steps.StepOutcome("5", "pytest", steps.STATUS_OK),
        steps.StepOutcome("—", "chép mẫu golden", steps.STATUS_FAIL),
        steps.StepOutcome("3", "mypy --strict", steps.STATUS_OK),
        steps.StepOutcome("1", "ruff format", steps.STATUS_OK),
    ]
    assert [o.number for o in steps._in_step_order(outcomes)] == ["1", "3", "5", "—"]


def test_verify__cổng_đầy_đủ_chạy_mypy_nền_song_song_bước_5(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1: mypy khởi động TRƯỚC bước 5 và chỉ được chờ SAU khi bước 5 xong — đó là phần chạy chồng nhau."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    events: list[str] = []
    proc = FakeProc(0, events)
    monkeypatch.setattr(steps, "MYPY_LOG", repo / "mypy.log")
    (repo / "mypy.log").write_text("Success: no issues found\n", encoding="utf-8")
    monkeypatch.setattr(steps, "start_mypy", _starter(events, proc))

    def fake_cov() -> steps.StepOutcome:
        """Bước 5 giả: mypy nền phải còn đang chạy lúc nó chạy — đó là phần tiết kiệm."""
        events.append("bước 5")
        assert proc.poll() is None
        return steps.StepOutcome("5", "pytest", steps.STATUS_OK)

    monkeypatch.setattr(steps, "_ALL_STEPS", [("3", steps.step_mypy), ("5", fake_cov)])
    assert steps.main(["verify"]) == 0
    assert events == ["start", "bước 5", "wait"]
    out = capsys.readouterr().out
    assert "Success: no issues found" in out  # log của mypy nền vào log cổng, sau bước 5
    assert out.index("  3 |") < out.index("  5 |")  # bảng vẫn in bước 3 trước bước 5


def test_verify__cổng_đầy_đủ_mypy_nền_hỏng_làm_lượt_hỏng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trạng thái bước 3 lấy từ MÃ THOÁT thật của tiến trình nền (K25): mypy đỏ → bảng "hỏng", lượt thoát 1."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    events: list[str] = []
    monkeypatch.setattr(steps, "MYPY_LOG", repo / "mypy.log")
    monkeypatch.setattr(steps, "start_mypy", _starter(events, FakeProc(1, events)))
    monkeypatch.setattr(
        steps,
        "_ALL_STEPS",
        [("3", steps.step_mypy), ("5", lambda: steps.StepOutcome("5", "pytest", steps.STATUS_OK))],
    )
    assert steps.main(["verify"]) == 1


def test_verify__bước_trước_hỏng_thì_bước_3_chưa_chạy_và_mypy_nền_bị_giết(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dừng sớm giữ nguyên: bước 5 hỏng → bước 3 "chưa chạy" (K25), và tiến trình mypy nền không bị bỏ sống."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    events: list[str] = []
    monkeypatch.setattr(steps, "MYPY_LOG", repo / "mypy.log")
    monkeypatch.setattr(steps, "start_mypy", _starter(events, FakeProc(0, events)))
    monkeypatch.setattr(
        steps,
        "_ALL_STEPS",
        [("3", steps.step_mypy), ("5", lambda: steps.StepOutcome("5", "pytest", steps.STATUS_FAIL))],
    )
    assert steps.main(["verify"]) == 1
    assert "kill" in events
    assert steps.STATUS_SKIP in capsys.readouterr().out


def test_verify__steps_lẻ_chạy_mypy_tuần_tự(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--steps` thiếu bước là lượt xem trước của worker: mypy chạy tại chỗ, không dựng tiến trình nền."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    events: list[str] = []
    monkeypatch.setattr(steps, "start_mypy", _starter(events, FakeProc(0, events)))
    seen: list[str] = []

    def fake_mypy() -> steps.StepOutcome:
        """Bước 3 giả chạy tại chỗ: ghi mốc để kiểm nó được gọi tuần tự, không qua tiến trình nền."""
        seen.append("mypy")
        return steps.StepOutcome("3", "mypy", steps.STATUS_OK)

    monkeypatch.setattr(
        steps,
        "_ALL_STEPS",
        [("3", fake_mypy), ("4", lambda: steps.StepOutcome("4", "lint", steps.STATUS_OK))],
    )
    assert steps.main(["verify", "--steps", "3"]) == 0
    assert events == []
    assert seen == ["mypy"]


# --- cổng nhanh (PERF-03) ------------------------------------------------------------


def _no_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đồ thị import rỗng: test của `steps` không dựng grimp trên repo giả (đồ thị thật: `test_affected.py`)."""
    monkeypatch.setattr("tools.verify.affected.importers_of", lambda _module: set())


def test_fast_test_paths__đồ_thị_import_hay_cả_bộ_khi_đổi_đầu_vào_bước_7(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tệp test của đơn vị bị chạm (đồ thị rỗng); đầu vào bước 7 đổi → cả bộ (mẫu golden phải đủ)."""
    _no_graph(monkeypatch)
    _touch(repo, "apps/api/auth/tests/test_login.py")
    _touch(repo, "apps/api/notes/tests/test_notes.py")
    assert steps.fast_test_paths(["apps/api/auth/router.py", "README.md"]) == ["apps/api/auth/tests/test_login.py"]
    assert steps.fast_test_paths(["docs/x.md"]) == []
    assert steps.fast_test_paths(["apps/api/auth/router.py", "tools/contract/APPFRONT_SHA"]) is None


def test_fast_cov_units__đơn_vị_mã_còn_trên_đĩa(repo: Path) -> None:
    """Nguồn `--cov`: đơn vị dưới packages/apps/tools còn trên đĩa; bỏ đơn vị đã xoá và `deploy`/`tests`."""
    _touch(repo, "apps/api/auth/router.py")
    _touch(repo, "deploy/x.sh")
    changed = ["apps/api/auth/router.py", "apps/api/gone/router.py", "deploy/x.sh", "README.md"]
    assert steps.fast_cov_units(changed) == ["apps/api/auth"]


def test_fast_mypy_env__cache_ấm_theo_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cổng nhanh dùng `mypy-fast-<tên>` cạnh cache của cổng đầy đủ; không có biến thì giữ nguyên env."""
    monkeypatch.setenv("MYPY_CACHE_DIR", "/work/mypy-x")
    monkeypatch.setenv("VERIFY_NAME", "Gate5 A")
    assert steps.fast_mypy_env()["MYPY_CACHE_DIR"] == "/work/mypy-fast-gate5-a"
    monkeypatch.delenv("MYPY_CACHE_DIR")
    assert "MYPY_CACHE_DIR" not in steps.fast_mypy_env()


@pytest.mark.parametrize(
    ("rules", "status", "ran_gate"),
    [
        ({}, steps.STATUS_OK, True),
        ({"pytest": (5, "")}, steps.STATUS_NA, False),
        ({"pytest": (1, "")}, steps.STATUS_FAIL, False),
        ({"tools.coverage_gate": (1, "")}, steps.STATUS_FAIL, True),
    ],
)
def test_step_fast_pytest__tệp_bị_ảnh_hưởng_độ_phủ_đơn_vị_chạm(
    repo: Path, monkeypatch: pytest.MonkeyPatch, rules: dict[str, tuple[int, str]], status: str, ran_gate: bool
) -> None:
    """pytest nhận tệp test bị ảnh hưởng + `--cov=<đơn vị chạm>`; rồi `coverage json` → `coverage_gate`.
    Mã 5 (không thu được test) là "không áp dụng"; pytest hay cổng độ phủ hỏng → "hỏng"."""
    _no_graph(monkeypatch)
    _touch(repo, "apps/api/auth/router.py")
    _touch(repo, "apps/api/auth/tests/test_login.py")
    monkeypatch.setenv("VERIFY_CHANGED", "apps/api/auth/router.py\n")
    fake = FakeRun(rules=rules)
    monkeypatch.setattr(steps, "_run", fake)
    outcome = steps.step_fast_pytest()
    assert fake.calls[0] == [
        "pytest", "-n", steps.FAST_PYTEST_WORKERS, "--dist", "loadfile", "--durations=10",
        "--cov=apps/api/auth", "--cov-report=", "apps/api/auth/tests/test_login.py",
    ]  # fmt: skip
    assert fake.ran("tools.coverage_gate") is ran_gate
    assert outcome.status == status


def test_step_fast_pytest__không_test_nào_thì_không_áp_dụng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chỉ đổi tài liệu: không chạy pytest, bước 5 "không áp dụng" chứ không "đạt" (K25)."""
    monkeypatch.setenv("VERIFY_CHANGED", "docs/x.md\n")
    fake = _fake(monkeypatch)
    assert steps.step_fast_pytest().status == steps.STATUS_NA
    assert fake.calls == []


def test_step_fast_pytest__cả_bộ_không_đơn_vị_mã_thì_không_đo_độ_phủ(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uv.lock` đổi: cả bộ (không đường dẫn), không đơn vị mã nào → không `--cov`, không `coverage_gate`."""
    monkeypatch.setenv("VERIFY_CHANGED", "uv.lock\n")
    fake = _fake(monkeypatch)
    assert steps.step_fast_pytest().status == steps.STATUS_OK
    assert fake.calls == [["pytest", "-n", steps.FAST_PYTEST_WORKERS, "--dist", "loadfile", "--durations=10"]]


@pytest.mark.parametrize(
    ("changed", "order"),
    [
        ("apps/worker/x/tasks.py", ["1", "2", "4", "5", "3"]),
        ("packages/db/models/x.py", ["1", "2", "4", "5", "3", "6"]),
        ("apps/api/auth/router.py", ["1", "2", "4", "5", "3", "8"]),
        ("tools/contract/APPFRONT_SHA", ["1", "2", "4", "0", "5", "3", "7"]),
    ],
)
def test_fast_gate_steps__bước_6_7_8_chỉ_khi_đầu_vào_đổi(
    repo: Path, monkeypatch: pytest.MonkeyPatch, changed: str, order: list[str]
) -> None:
    """Thứ tự 1, 2, 4, 5, 3 (mypy nền chờ sau pytest); 6/7/8 theo `FAST_STEP_INPUTS`, bước 7 kéo theo bước 0."""
    monkeypatch.setenv("VERIFY_CHANGED", f"{changed}\n")
    assert [n for n, _ in steps.fast_gate_steps(cast(Any, FakeProc(0, [])))] == order


@pytest.mark.parametrize(("mypy_rc", "pytest_rc", "rc"), [(0, 0, 0), (1, 0, 1), (0, 1, 1)])
def test_verify_fast__mypy_nền_cache_ấm_và_mã_thoát(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mypy_rc: int,
    pytest_rc: int,
    rc: int,
) -> None:
    """`verify --fast`: mypy nền trên cache ấm, trạng thái từ mã thoát THẬT, bảng nói rõ đây là cổng nhanh."""
    events: list[str] = []
    monkeypatch.setenv("MYPY_CACHE_DIR", "/work/mypy-x")
    monkeypatch.setenv("VERIFY_NAME", "w")
    monkeypatch.setenv("VERIFY_CHANGED", "tools/x.py\n")
    _touch(repo, "tools/x.py")
    _touch(repo, "tools/tests/test_x.py")
    _no_graph(monkeypatch)
    monkeypatch.setattr(steps, "start_mypy", _starter(events, FakeProc(mypy_rc, events)))
    _fake(monkeypatch, pytest=(pytest_rc, ""))
    assert steps.main(["verify", "--fast"]) == rc
    assert events[0] == "start /work/mypy-fast-w"
    out = capsys.readouterr().out
    assert "Cổng NHANH" in out
    assert out.index("  3 |") < out.index("  5 |")
    assert "coverage_gate" not in out  # dòng bước 5 mang tên của cổng nhanh, cả khi "chưa chạy"


# --- lock / openapi -----------------------------------------------------------------


def test_lock_chép_uv_lock_xoá_file_cũ(repo: Path) -> None:
    """`lock` chép uv.lock ra thư mục ra và xoá file cũ ở đó."""
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    old = steps.OUT_DIR / "lock" / "cũ.txt"
    old.parent.mkdir(parents=True)
    old.write_text("x", encoding="utf-8")
    assert steps.main(["lock"]) == 0
    assert sorted(p.name for p in (steps.OUT_DIR / "lock").iterdir()) == ["uv.lock"]


def test_lock_thư_mục_ra_là_mount_point_không_gỡ_được(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-001: `run.sh` mount host vào đúng `/src-out/lock`.

    `rmtree` xoá nội dung nhưng để lại chính mount point (EBUSY bị nuốt) — dựng
    lại đúng hành vi đó để chắc `_clean_dir` không ném `FileExistsError`.
    """

    def rmtree_giữ_gốc(path: Path | str, **_kw: Any) -> None:
        """Giả `shutil.rmtree`: xoá nội dung nhưng giữ thư mục gốc, như mount point."""
        for child in Path(path).iterdir():
            child.unlink()

    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    out = steps.OUT_DIR / "lock"
    out.mkdir(parents=True)
    (out / "cũ.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(shutil, "rmtree", rmtree_giữ_gốc)

    assert steps.main(["lock"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["uv.lock"]


def test_openapi_thiếu_module_nêu_b0_06(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`openapi` thiếu module thì thoát 1 và nêu B0-06 ở stderr."""
    assert steps.main(["openapi"]) == 1
    assert "B0-06" in capsys.readouterr().err


def test_openapi_module_giả_ghi_đúng_file_xoá_file_cũ(repo: Path) -> None:
    """`openapi` với module giả ghi đúng `openapi.json` và xoá file cũ trong thư mục ra."""
    for pkg in ("apps", "apps/api", "apps/api/core"):
        _touch(repo, f"{pkg}/__init__.py")
    (repo / "apps" / "api" / "core" / "openapi.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path(sys.argv[sys.argv.index('--out') + 1]).write_text('{\"openapi\": \"3.1.0\"}')\n",
        encoding="utf-8",
    )
    stale = steps.OUT_DIR / "openapi" / "cũ.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    assert steps.main(["openapi"]) == 0
    out_dir = steps.OUT_DIR / "openapi"
    assert sorted(p.name for p in out_dir.iterdir()) == ["openapi.json"]
    assert (out_dir / "openapi.json").read_text(encoding="utf-8") == '{"openapi": "3.1.0"}'


# --- merge-heads --------------------------------------------------------------------

_ALEMBIC_INI = "[alembic]\nscript_location = %(here)s/migrations\n"
# Template **thật** của repo, không bản tự chế: bản giả `repr(down_revision)` che lỗi tuple→chuỗi (NO-249).
_MAKO = Path(__file__).resolve().parents[2] / "packages" / "db" / "migrations" / "script.py.mako"


def _alembic_repo(root: Path, *revisions: tuple[str, str | None]) -> Path:
    """Dựng repo alembic tối thiểu (ini, mako, các revision) và trả thư mục `versions`."""
    db = root / "packages" / "db"
    versions = db / "migrations" / "versions"
    versions.mkdir(parents=True)
    (db / "alembic.ini").write_text(_ALEMBIC_INI, encoding="utf-8")
    shutil.copy(_MAKO, db / "migrations" / "script.py.mako")
    for rev, down in revisions:
        (versions / f"{rev}_viec.py").write_text(
            f'revision = "{rev}"\ndown_revision = {down!r}\n\n\n'
            "def upgrade() -> None:\n    pass\n\n\n"
            "def downgrade() -> None:\n    pass\n",
            encoding="utf-8",
        )
    return versions


def _no_subprocess(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    """Thay `_run` để báo lỗi nếu có lệnh nào bị gọi."""
    raise AssertionError(f"không được gọi {cmd} trước khi kiểm xong tên")


def test_merge_heads_sai_mẫu_hỏng_trước_alembic(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`merge-heads` sai mẫu tên thì thoát 1, không gọi alembic."""
    monkeypatch.setattr(steps, "_run", _no_subprocess)
    _alembic_repo(repo)
    assert steps.main(["merge-heads", "r20260919_merge_w00"]) == 1
    assert "sai mẫu" in capsys.readouterr().err


def test_merge_heads_thiếu_alembic_ini_nêu_b0_03(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`merge-heads` thiếu alembic.ini thì thoát 1 và nêu B0-03."""
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 1
    assert "B0-03" in capsys.readouterr().err


def test_merge_heads_trùng_revision_có_sẵn_hỏng_trước_alembic(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`merge-heads` trùng tên revision có sẵn thì thoát 1, không gọi alembic."""
    _alembic_repo(repo, ("r20260919_merge_w00_1", None))
    monkeypatch.setattr(steps, "_run", _no_subprocess)
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 1
    assert "đã là revision" in capsys.readouterr().err


def test_merge_heads_một_head_hỏng(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`merge-heads` chỉ có một head thì thoát 1."""
    _alembic_repo(repo, ("r20260919_b0_03", None))
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 1
    assert "chỉ 1 head" in capsys.readouterr().err


def test_merge_heads_hai_head_ra_đúng_một_file(repo: Path) -> None:
    """Hai head thì `merge-heads` ra đúng một file revision merge, repo còn một head."""
    versions = _alembic_repo(repo, ("r20260919_b0_03", None), ("r20260919_b0_04", None))
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 0
    out = sorted((steps.OUT_DIR / "merge-heads").iterdir())
    assert len(out) == 1
    assert "r20260919_merge_w00_1" in out[0].read_text(encoding="utf-8")
    assert steps._alembic_heads(repo / "packages" / "db" / "alembic.ini") == ["r20260919_merge_w00_1"]
    assert (versions / out[0].name).is_file()


_TWO_HEADS = (0, "a (head)\nb (head)\n")


def test_merge_heads_alembic_merge_hỏng(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`alembic merge` hỏng thì `merge-heads` thoát 1 và dừng ngay sau merge."""
    _alembic_repo(repo)
    fake = _fake(monkeypatch, **{"alembic.ini merge": (1, ""), "alembic.ini heads": _TWO_HEADS})
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 1
    assert fake.calls[-1][-4:-2] == ["merge", "heads"]  # dừng ngay sau merge


def test_merge_heads_sau_merge_còn_hai_head_hỏng(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sau merge vẫn còn hai head thì `merge-heads` thoát 1 và báo các head."""
    _alembic_repo(repo)
    _fake(monkeypatch, **{"alembic.ini heads": _TWO_HEADS})
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 1
    assert "sau merge còn ['a', 'b']" in capsys.readouterr().err


def test_merge_heads_lint_hỏng_không_chép(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """lint_migrations hỏng sau merge thì `merge-heads` thoát 1 và không chép file ra."""
    versions = _alembic_repo(repo)
    heads = iter(["a (head)\nb (head)\n", "r20260919_merge_w00_1 (head)\n"])

    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        """Giả `_run`: trả head lần lượt cho lệnh `heads`, còn lại ghi một revision lỗi tên."""
        if cmd[-1] == "heads":
            return subprocess.CompletedProcess(cmd, 0, stdout=next(heads), stderr="")
        (versions / "bad.py").write_text('revision = "r20260919_merge_w00_0"\n', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(steps, "_run", run)
    assert steps.main(["merge-heads", "r20260919_merge_w00_1"]) == 1
    assert not (steps.OUT_DIR / "merge-heads").exists()


# --- gc / shell / _touched_units ----------------------------------------------------


def test_gc_xoá_thư_mục_của_worktree_đã_mất(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`gc` xoá mypy-*/mypy-fast-* của worktree đã mất, và mọi `venv-<tên>` kiểu cũ (venv giờ theo băm lock)."""
    for name in ("venv-a", "venv-b", "mypy-a", "mypy-c", "mypy-fast-a", "mypy-fast-c", "uv-cache"):
        (steps.WORK_DIR / name).mkdir(parents=True)
    monkeypatch.setenv("VERIFY_VALID_NAMES", "a")
    assert steps.main(["gc"]) == 0
    assert sorted(p.name for p in steps.WORK_DIR.iterdir()) == ["mypy-a", "mypy-fast-a", "uv-cache"]


def test_gc_giữ_n_thư_mục_theo_băm_mới_nhất(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`mypy-tree-<băm>`, `venv-lock-<băm>` không thuộc worktree nào: mỗi loại giữ `MYPY_CACHE_KEEP` cái mới nhất."""
    monkeypatch.setattr(steps, "MYPY_CACHE_KEEP", 2)
    for prefix in (steps.MYPY_CACHE_PREFIX, steps.VENV_PREFIX):
        for index in range(4):
            d = steps.WORK_DIR / f"{prefix}{index:016x}"
            d.mkdir(parents=True)
            os.utime(d, (1_700_000_000 + index, 1_700_000_000 + index))
    monkeypatch.setenv("VERIFY_VALID_NAMES", "")
    assert steps.main(["gc"]) == 0
    assert sorted(p.name for p in steps.WORK_DIR.iterdir()) == sorted(
        f"{prefix}{index:016x}" for prefix in (steps.MYPY_CACHE_PREFIX, steps.VENV_PREFIX) for index in (2, 3)
    )


def test_shell_exec_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cmd_shell` gọi `os.execvp` chạy bash."""
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(os, "execvp", lambda file, args: calls.append((file, args)))
    steps.cmd_shell(argparse.Namespace())
    assert calls == [("bash", ["bash"])]


def test_touched_units_từ_verify_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_touched_units` suy đơn vị bị chạm từ `VERIFY_CHANGED`, bỏ file không thuộc đơn vị."""
    monkeypatch.setenv("VERIFY_CHANGED", "packages/core/a.py\napps/api/auth/r.py\ndocs/x.md\n")
    assert steps._touched_units() == {"packages/core", "apps/api/auth"}


def test_run_thật_in_lệnh(capsys: pytest.CaptureFixture[str]) -> None:
    """`_run` thật chạy tiến trình con, trả mã thoát/stdout và in dòng lệnh bắt đầu bằng `$ `."""
    r = steps._run([sys.executable, "-c", "print('ok')"], capture_output=True)
    assert (r.returncode, r.stdout.strip()) == (0, "ok")
    assert capsys.readouterr().out.startswith("$ ")


# --- FIX-006, FIX-008 ----------------------------------------------------------------


def test_clean_dir_không_nuốt_lỗi_xoá_mục_con(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-008: mục con không xoá được thì `_clean_dir` ném, không để file cũ sót lại im lặng.

    Sót lại là `merge-heads` chép nhầm revision cũ ra ngoài. Container chạy bằng root nên
    quyền tệp không chặn được xoá; dựng lỗi bằng cách cho `os.unlink` từ chối đúng một file.
    """
    out = steps.OUT_DIR / "merge-heads"
    out.mkdir(parents=True)
    (out / "cũ.py").write_text("x", encoding="utf-8")
    real_unlink = os.unlink

    def unlink_bị_khoá(path: Any, *args: Any, **kwargs: Any) -> None:
        """`os.unlink` từ chối riêng `cũ.py`, như file đang bị giữ."""
        if Path(path).name == "cũ.py":
            raise PermissionError(13, "bị khoá", str(path))
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink_bị_khoá)
    with pytest.raises(PermissionError, match="bị khoá"):
        steps._clean_dir(out)


def test_clean_dir_xoá_cả_thư_mục_con_giữ_gốc(repo: Path) -> None:
    """Thư mục con cũng bị xoá; chính thư mục ra (có thể là mount point, NO-001) được giữ."""
    out = steps.OUT_DIR / "lock"
    (out / "con" / "cháu").mkdir(parents=True)
    (out / "con" / "cháu" / "a.txt").write_text("x", encoding="utf-8")
    (out / "b.txt").write_text("x", encoding="utf-8")
    assert steps._clean_dir(out) == out
    assert out.is_dir()
    assert list(out.iterdir()) == []


def _fail_one_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Một bước hỏng: việc chép mẫu vẫn phải chạy (người điều phối cần mẫu nhất khi đỏ)."""
    monkeypatch.setattr(steps, "_ALL_STEPS", [("7", lambda: steps.StepOutcome("7", "h", steps.STATUS_FAIL))])


def test_verify_chép_mẫu_golden_ra_thư_mục_ra(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-043, ENV §2: cuối lượt, `CONTRACT_SAMPLES_DIR` chép ra `/src-out/contract-samples`, mẫu cũ bị thay."""
    samples = tmp_path / "contract-samples"
    (samples / "op").mkdir(parents=True)
    (samples / "op" / "C01-1.json").write_text("{}", encoding="utf-8")
    out = steps.OUT_DIR / "contract-samples"
    out.mkdir(parents=True)
    (out / "cũ.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CONTRACT_SAMPLES_DIR", str(samples))
    _fail_one_step(monkeypatch)
    assert steps.main(["verify"]) == 1
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == ["op/C01-1.json"]


def test_verify_không_có_mẫu_thì_thư_mục_ra_rỗng(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lượt không test nào ghi mẫu (thư mục chưa tạo) → thư mục ra rỗng, không còn mẫu của lượt trước."""
    out = steps.OUT_DIR / "contract-samples"
    out.mkdir(parents=True)
    (out / "cũ.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CONTRACT_SAMPLES_DIR", str(tmp_path / "chua-co"))
    _fail_one_step(monkeypatch)
    assert steps.main(["verify"]) == 1
    assert list(out.iterdir()) == []


def _table_rows(out: str) -> dict[str, str]:
    """Dòng bảng cổng theo cột `#` (một dòng mỗi bước)."""
    return {line.split("|")[0].strip(): line for line in out.splitlines() if " | " in line}


def test_verify_chép_mẫu_hỏng_vẫn_in_đủ_bảng_và_thoát_1(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """NO-075: thư mục ra không ghi được → bảng vẫn in đủ, thêm một dòng lỗi chép mẫu, thoát 1 — không traceback.

    Container chạy bằng root nên quyền tệp không chặn được ghi; dựng lỗi bằng thư mục ra nằm dưới một file
    (`NotADirectoryError`), cùng họ `OSError` với probe `VERIFY_OUT_DIR=/proc/rv` của review.
    """
    blocker = tmp_path / "là-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(steps, "OUT_DIR", blocker / "out")
    monkeypatch.setenv("CONTRACT_SAMPLES_DIR", str(tmp_path / "contract-samples"))
    monkeypatch.setattr(steps, "_ALL_STEPS", [("1", lambda: steps.StepOutcome("1", "a", steps.STATUS_OK))])
    assert steps.main(["verify"]) == 1
    captured = capsys.readouterr()
    rows = _table_rows(captured.out)
    assert steps.STATUS_OK in rows["1"]
    assert "chép mẫu golden" in rows["—"]
    assert steps.STATUS_FAIL in rows["—"]
    assert "Not a directory" in rows["—"]
    assert "chép mẫu golden hỏng" in captured.err


def test_verify_ngoài_container_không_chép(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không đặt `CONTRACT_SAMPLES_DIR` (chạy ngoài cổng) → không tạo gì dưới thư mục ra."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    _fail_one_step(monkeypatch)
    assert steps.main(["verify"]) == 1
    assert not (steps.OUT_DIR / "contract-samples").exists()


# --- log cổng ra thư mục host (NO-080) ---------------------------------------------


def _printing_step(number: str, status: str) -> tuple[str, Callable[[], steps.StepOutcome]]:
    """Bước giả in qua một tiến trình con thật (`steps._run`, như pytest hay `coverage_gate`): ghi thẳng fd 1, 2."""

    def fn() -> steps.StepOutcome:
        """Chạy một tiến trình con in ra stdout và stderr rồi trả kết quả bước giả."""
        code = f"import sys; print('tóm tắt pytest {number}'); print('cảnh báo {number}', file=sys.stderr)"
        assert steps._run([sys.executable, "-c", code]).returncode == 0
        return steps.StepOutcome(number, f"bước {number}", status)

    return number, fn


@pytest.fixture
def log_file(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`VERIFY_LOG_FILE` như `run.sh` đặt: một file dưới thư mục ra, thư mục cha chưa có."""
    path = steps.OUT_DIR / "verify" / "20260922T000000Z-lượt.log"
    monkeypatch.setenv("VERIFY_LOG_FILE", str(path))
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    return path


def test_verify_ghi_cả_output_của_lượt_ra_file_log(
    log_file: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """NO-080: file log có output của tiến trình con, bảng cổng và mã thoát; stdout/stderr vẫn như cũ, vẫn tách."""
    monkeypatch.setattr(
        steps, "_ALL_STEPS", [_printing_step("1", steps.STATUS_OK), _printing_step("2", steps.STATUS_FAIL)]
    )
    assert steps.main(["verify"]) == 1
    captured = capfd.readouterr()
    text = log_file.read_text(encoding="utf-8")
    for needle in ("tóm tắt pytest 1", "cảnh báo 2", "Trạng thái", "mã thoát: 1"):
        assert needle in text
    rows = _table_rows(text)
    assert rows == _table_rows(captured.out)
    assert (steps.STATUS_OK in rows["1"], steps.STATUS_FAIL in rows["2"]) == (True, True)
    assert "tóm tắt pytest 2" in captured.out.splitlines()
    assert "cảnh báo 2" in captured.err.splitlines()
    assert "cảnh báo 2" not in captured.out.splitlines()


def test_verify_log_nối_thêm_không_đè_lượt_trước(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cùng tên file (hai lượt trong một giây) → nối tiếp, lượt trước không mất."""
    monkeypatch.setattr(steps, "_ALL_STEPS", [_printing_step("1", steps.STATUS_OK)])
    assert steps.main(["verify"]) == 0
    assert steps.main(["verify"]) == 0
    assert log_file.read_text(encoding="utf-8").count("mã thoát: 0") == 2


def _no_step() -> steps.StepOutcome:
    """Bước giả: báo lỗi nếu bị chạy."""
    raise AssertionError("không được chạy bước nào khi chưa mở được log")


def test_verify_log_không_mở_được_thì_hỏng_trước_bước_đầu(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thư mục log không tạo được (thiếu mount, đường nằm dưới một file) → `OSError` trước khi chạy bước nào."""
    blocker = tmp_path / "là-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("VERIFY_LOG_FILE", str(blocker / "verify" / "lượt.log"))
    monkeypatch.setattr(steps, "_ALL_STEPS", [("1", _no_step)])
    with pytest.raises(NotADirectoryError):
        steps.main(["verify"])


def _exploding_step() -> steps.StepOutcome:
    """Bước in một dòng rồi ném lỗi lạ, như thiếu công cụ trong venv (`FileNotFoundError`)."""
    print("trước khi nổ")
    raise RuntimeError("bước nổ giữa lượt")


def test_verify_bước_ném_lỗi_lạ_vẫn_có_traceback_và_mã_thoát_trong_log(
    log_file: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """NO-089: bước ném lỗi lạ → log có output trước đó, traceback và "mã thoát: 1"; stderr vẫn có traceback.

    Không so thứ tự traceback với "mã thoát": stdout và stderr đi hai ống, hai luồng chép vào log.
    """
    monkeypatch.setattr(steps, "_ALL_STEPS", [("1", _exploding_step)])
    assert steps.main(["verify"]) == 1
    text = log_file.read_text(encoding="utf-8")
    for needle in (
        "trước khi nổ",
        "Traceback (most recent call last)",
        "RuntimeError: bước nổ giữa lượt",
        "mã thoát: 1",
    ):
        assert needle in text
    assert "RuntimeError: bước nổ giữa lượt" in capfd.readouterr().err


def test_verify_tiến_trình_con_mồ_côi_không_treo_cổng(
    log_file: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Tiến trình con còn giữ fd 1 sau lượt → bỏ đợi sau `LOG_DRAIN_TIMEOUT_S`, báo stderr; log đủ, mã thoát giữ."""
    orphans: list[subprocess.Popen[bytes]] = []

    def step() -> steps.StepOutcome:
        """Bước giả: sinh tiến trình con ngủ lâu để giữ fd rồi trả kết quả đạt."""
        orphans.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))
        return steps.StepOutcome("1", "a", steps.STATUS_OK)

    monkeypatch.setattr(steps, "LOG_DRAIN_TIMEOUT_S", 0.2)
    monkeypatch.setattr(steps, "_ALL_STEPS", [("1", step)])
    try:
        assert steps.main(["verify"]) == 0
    finally:
        for orphan in orphans:
            orphan.kill()
            orphan.wait()
    assert "mã thoát: 0" in log_file.read_text(encoding="utf-8")
    assert "tiến trình con còn giữ fd 1" in capfd.readouterr().err


def test_write_each_bỏ_đích_hỏng_giữ_đích_còn_lại() -> None:
    """Đĩa log đầy giữa lượt → bỏ riêng đích đó, stdout vẫn nhận đủ; luồng chép không chết (ống đầy là cổng treo)."""

    class DiskFull(io.BytesIO):
        """Đích ghi luôn báo đĩa đầy."""

        def write(self, _b: Any) -> int:
            """Ném `OSError` đĩa đầy mỗi lần ghi."""
            raise OSError(28, "No space left on device")

    full, good = DiskFull(), io.BytesIO()
    sinks: list[BinaryIO] = [full, good]
    problems: list[str] = []
    steps._write_each(sinks, b"a", problems)
    steps._write_each(sinks, b"b", problems)
    assert (good.getvalue(), sinks) == (b"ab", [good])
    assert len(problems) == 1
    assert "No space left" in problems[0]


# --- làm ấm runner Node (NO-048) ----------------------------------------------------


def _warm(fake: FakeRun, error: Exception | None = None) -> Callable[[Path], Path]:
    """Thay `ensure_node_modules`: ghi lượt gọi vào cùng sổ lệnh của `fake` để so thứ tự với bước 5.

    Chỉ nhận đúng một đối số vị trí: gọi kèm `cache_dir`/`package_dir` (lệch kho npm hay băm lock
    của fixture `contract_build`) thì `TypeError`.
    """

    def ensure(node_dir: Path) -> Path:
        """Ghi lượt gọi vào sổ lệnh của `fake`, ném `error` nếu có, trả đường băm lock giả."""
        fake.calls.append(["ensure_node_modules", str(node_dir)])
        if error is not None:
            raise error
        return node_dir / "băm-lock"

    return ensure


@pytest.mark.parametrize("argv", [[], ["--steps", "5"], ["--steps", "7"], ["--steps", "1,5b,7"]])
def test_verify_làm_ấm_node_modules_trước_bước_5(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """NO-048: `npm ci` lạnh chạy ở pha chuẩn bị, trên đúng thư mục của `contract_build`, trước mọi lệnh bước 5."""
    node_dir = tmp_path / "contract-node"
    monkeypatch.setenv("CONTRACT_NODE_DIR", str(node_dir))
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    fake = _fake(monkeypatch)
    monkeypatch.setattr(runner_client, "ensure_node_modules", _warm(fake))
    assert steps.main(["verify", *argv]) == 0
    assert fake.calls[0] == ["ensure_node_modules", str(runner_client.node_dir_from_env())]
    assert [c for c in fake.calls if c[0] == "ensure_node_modules"] == [fake.calls[0]]


@pytest.mark.parametrize("argv", [["--steps", "1,2,4"], ["--steps", "5b,6,8"]])
def test_verify_không_bước_cần_runner_thì_không_làm_ấm(
    repo: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """`--steps` không gồm 5 hay 7 → không đụng `npm`, không đổi volume."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    fake = _fake(monkeypatch)
    monkeypatch.setattr(runner_client, "ensure_node_modules", _warm(fake))
    assert steps.main(["verify", *argv]) == 0
    assert not fake.ran("ensure_node_modules")


@pytest.mark.parametrize(
    "error",
    [
        RunnerError("npm thoát 1: getaddrinfo ENOTFOUND registry.npmjs.org\nnpm ERR! chi tiết"),
        PermissionError(13, "cấm"),
    ],
)
def test_verify_làm_ấm_hỏng_là_lỗi_hạ_tầng_của_cổng(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    """npm hay đĩa hỏng lúc làm ấm → cổng hỏng, bảng nêu lỗi; bước 5 không chạy (không để pytest tải mạng)."""
    monkeypatch.delenv("CONTRACT_SAMPLES_DIR", raising=False)
    fake = _fake(monkeypatch)
    monkeypatch.setattr(runner_client, "ensure_node_modules", _warm(fake, error))
    assert steps.main(["verify"]) == 1
    assert not fake.ran("coverage run")
    rows = _table_rows(capsys.readouterr().out)
    assert steps.STATUS_FAIL in rows["0"]
    assert str(error).splitlines()[0] in rows["0"]
    assert steps.STATUS_SKIP in rows["5"]


# --- junit bước 5 ra thư mục log (NO-324) -------------------------------------------


@pytest.fixture
def junit_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """`(junit.xml của pytest, file log cổng)` trong thư mục tạm; `steps.JUNIT_FILE` và `VERIFY_LOG_FILE` trỏ vào."""
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuites/>", encoding="utf-8")
    log_dir = tmp_path / "verify-out"
    log_dir.mkdir()
    monkeypatch.setattr(steps, "JUNIT_FILE", junit)
    monkeypatch.setenv("VERIFY_LOG_FILE", str(log_dir / "20261003T000000Z-abc.log"))
    return junit, log_dir


def test_bước_5__junit_chép_cạnh_log_cổng(
    repo: Path, monkeypatch: pytest.MonkeyPatch, junit_pair: tuple[Path, Path]
) -> None:
    """NO-324: xong pytest, `junit.xml` được chép thành `<tên log>.junit.xml` cạnh log trong thư mục mount."""
    _fake(monkeypatch)
    assert steps.step_coverage().status == steps.STATUS_OK
    assert (junit_pair[1] / "20261003T000000Z-abc.junit.xml").read_text(encoding="utf-8") == "<testsuites/>"


def test_bước_5__junit_vẫn_chép_khi_pytest_hỏng(
    repo: Path, monkeypatch: pytest.MonkeyPatch, junit_pair: tuple[Path, Path]
) -> None:
    """Lúc đỏ là lúc cần junit nhất: pytest hỏng vẫn chép."""
    _fake(monkeypatch, pytest=(1, ""))
    assert steps.step_coverage().status == steps.STATUS_FAIL
    assert (junit_pair[1] / "20261003T000000Z-abc.junit.xml").is_file()


def test_bước_5__không_log_hoặc_không_junit_thì_không_chép_và_không_hỏng(
    repo: Path, monkeypatch: pytest.MonkeyPatch, junit_pair: tuple[Path, Path]
) -> None:
    """Không `VERIFY_LOG_FILE` (chạy tay trong shell) hay pytest chưa kịp ghi junit: bỏ qua, bước vẫn theo mã thoát."""
    _fake(monkeypatch)
    monkeypatch.delenv("VERIFY_LOG_FILE")
    assert steps.step_coverage().status == steps.STATUS_OK
    assert list(junit_pair[1].iterdir()) == []
    monkeypatch.setenv("VERIFY_LOG_FILE", str(junit_pair[1] / "x.log"))
    junit_pair[0].unlink()
    assert steps.step_coverage().status == steps.STATUS_OK
    assert list(junit_pair[1].iterdir()) == []


def test_bước_5__chép_junit_hỏng_chỉ_cảnh_báo(
    repo: Path, monkeypatch: pytest.MonkeyPatch, junit_pair: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Thư mục log không ghi được: một dòng cảnh báo ở stderr, bước 5 vẫn đạt (junit là bản sao, như log)."""
    _fake(monkeypatch)
    shutil.rmtree(junit_pair[1])
    assert steps.step_coverage().status == steps.STATUS_OK
    assert "junit" in capsys.readouterr().err


# --- junit bước 5b ra thư mục log (NO-324) ------------------------------------------


@pytest.fixture
def perf_junit(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Thư mục log cổng; `steps.JUNIT_PERF_FILE` trỏ vào một junit perf có sẵn, đơn vị `packages/vision` bị chạm."""
    junit = tmp_path / "junit-perf.xml"
    junit.write_text("<testsuites/>", encoding="utf-8")
    log_dir = tmp_path / "verify-out"
    log_dir.mkdir()
    monkeypatch.setattr(steps, "JUNIT_PERF_FILE", junit)
    monkeypatch.setenv("VERIFY_LOG_FILE", str(log_dir / "20261003T000000Z-abc.log"))
    (repo / "packages" / "vision").mkdir(parents=True)
    monkeypatch.setenv("VERIFY_CHANGED", "packages/vision/x.py")
    return log_dir


def test_bước_5b__junit_perf_chép_cạnh_log_cổng(perf_junit: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NO-324: xong pytest perf, junit thành `<tên log>.perf.junit.xml` cạnh log (không đè junit bước 5)."""
    _fake(monkeypatch, **{"--collect-only": (0, "packages/vision/tests/test_p.py::test_speed\n")})
    assert steps.step_perf().status == steps.STATUS_OK
    assert (perf_junit / "20261003T000000Z-abc.perf.junit.xml").read_text(encoding="utf-8") == "<testsuites/>"


def test_bước_5b__junit_perf_vẫn_chép_khi_perf_hỏng(perf_junit: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Perf hỏng vẫn chép junit — lúc đỏ là lúc cần nó nhất."""
    _fake(
        monkeypatch,
        **{
            "--collect-only": (0, "packages/vision/tests/test_p.py::test_speed\n"),
            "-m perf and not gpu --junitxml": (1, ""),
        },
    )
    assert steps.step_perf().status == steps.STATUS_FAIL
    assert (perf_junit / "20261003T000000Z-abc.perf.junit.xml").is_file()


# --- NO-350: deploy/ là đơn vị của bước 5b ------


def test_5b_deploy_bị_chạm_chạy_perf_của_deploy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File dưới `deploy/` bị chạm thì bước 5b chạy pytest perf trong `deploy` (NO-350)."""
    (repo / "deploy").mkdir()
    monkeypatch.setenv("VERIFY_CHANGED", "deploy/scripts/healthcheck.sh")
    fake = _fake(monkeypatch, **{"--collect-only": (0, "deploy/scripts/tests/test_p.py::test_speed\n")})
    assert steps.step_perf().status == steps.STATUS_OK
    perf_run = [c for c in fake.calls if "--junitxml=/tmp/junit-perf.xml" in c]
    perf_file = "deploy/scripts/tests/test_p.py"
    assert perf_run == [["pytest", "-m", "perf and not gpu", "--junitxml=/tmp/junit-perf.xml", perf_file]]
