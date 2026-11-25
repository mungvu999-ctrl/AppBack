"""Điều phối 8 bước cổng verify TRONG container — BE-00 §12, B0-01 [6]C.

Chạy bằng `exec uv run python -m tools.verify.steps <việc> [...]` từ
`tools/verify/in_container.sh`, cwd đã là `/tmp/w` (bản chép của `/src`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import tomllib
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, BinaryIO

from tools.charter import merged_prompts
from tools.contract import runner_client
from tools.coverage_gate import unit_of
from tools.lint_migrations import MERGE_REVISION_RE, revision_of
from tools.lint_migrations import main as lint_migrations_main
from tools.verify import affected

REPO_ROOT = Path.cwd()
OUT_DIR = Path(os.environ.get("VERIFY_OUT_DIR", "/src-out"))
WORK_DIR = Path(os.environ.get("VERIFY_WORK_DIR", "/work"))

STATUS_OK = "đạt"
STATUS_FAIL = "hỏng"
STATUS_SKIP = "chưa chạy"
STATUS_NA = "không áp dụng"

_NAME_UNSAFE_RE = re.compile(r"[^a-z0-9_-]")


def normalize_verify_name(name: str) -> str:
    """Cùng thuật toán chuẩn hoá `VERIFY_NAME` của `tools/verify/run.sh` (`[a-z0-9_-]`)."""
    return _NAME_UNSAFE_RE.sub("-", name.lower())


@dataclass
class StepOutcome:
    """Kết quả một bước: số bước, tên, trạng thái và chi tiết."""

    number: str
    name: str
    status: str
    detail: str = ""


def _run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    """In dòng lệnh rồi chạy `cmd` trong `REPO_ROOT`, trả `CompletedProcess`."""
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=REPO_ROOT, text=True, **kw)


def _popen(cmd: list[str], **kw: Any) -> subprocess.Popen[bytes]:
    """In dòng lệnh rồi chạy `cmd` NỀN trong `REPO_ROOT`, trả `Popen` chưa chờ (cặp với `_run`).

    Mọi tiến trình nền của cổng đi qua đây, như mọi tiến trình đồng bộ đi qua `_run`: test thay một
    trong hai hàm này là không còn tiến trình thật nào chạy.
    """
    print(f"$ {' '.join(cmd)} &")
    return subprocess.Popen(cmd, cwd=REPO_ROOT, **kw)


def _changed_files() -> list[str]:
    """Các đường tương đối bị đổi so với `main` (dòng của `VERIFY_CHANGED`, `run.sh` tính)."""
    return [line.strip() for line in os.environ.get("VERIFY_CHANGED", "").splitlines() if line.strip()]


def _touched_units() -> set[str]:
    """Các đơn vị (gói/app) bị chạm, suy ra từ các dòng của `VERIFY_CHANGED`."""
    return {u for u in (unit_of(c) for c in _changed_files()) if u is not None}


def _gate_status(condition_file: Path, owner: str) -> str | None:
    """None = chạy thật; STATUS_NA / STATUS_FAIL = kết quả cố định (BE-01 [6]C)."""
    if condition_file.is_file():
        return None
    if owner in merged_prompts(REPO_ROOT):
        return STATUS_FAIL
    return STATUS_NA


def _infra_failure(number: str, name: str, exc: Exception) -> StepOutcome:
    """Lỗi hạ tầng của chính cổng (npm, đĩa, thư mục ra) thành một dòng bảng "hỏng".

    Bảng một dòng mỗi bước: bảng chỉ giữ dòng đầu (≤ 160 ký tự), lỗi đầy đủ in ra stderr ngay lúc đó.
    """
    print(f"{name} hỏng: {exc}", file=sys.stderr)
    first_line = str(exc).partition("\n")[0][:160]
    return StepOutcome(number, name, STATUS_FAIL, f"lỗi hạ tầng cổng: {first_line}")


def _print_table(outcomes: list[StepOutcome]) -> None:
    """In bảng trạng thái các bước (số, tên, trạng thái, chi tiết)."""
    print()
    print(f"{'#':>3} | {'Bước':<28} | {'Trạng thái':<14} | Chi tiết")
    print("-" * 90)
    for o in outcomes:
        print(f"{o.number:>3} | {o.name:<28} | {o.status:<14} | {o.detail}")
    print()


# ---------------------------------------------------------------------------
# Pha chuẩn bị
# ---------------------------------------------------------------------------

_RUNNER_STEPS = {"5", "7"}
"""Bước chạy runner Node: 5 (fixture `contract_build` của pytest) và 7 (`tools.contract.check`)."""


def step_warm_node_modules() -> StepOutcome:
    """Cài `node_modules` của runner Node trước bước 5, để pytest không tải mạng (NO-048).

    Lượt lạnh `npm ci` tải từ `registry.npmjs.org`; để fixture `contract_build` làm là tải mạng
    giữa test. Gọi đúng `ensure_node_modules(node_dir_from_env())` như `build_layout`: cùng thư mục,
    cùng băm lock, cùng kho npm, nên bước 5 và 7 thấy đích đã có. npm, mạng hay đĩa hỏng là lỗi
    hạ tầng của cổng: bước hỏng, mọi bước sau "chưa chạy" — không để pytest thử tải lại.
    """
    name = "làm ấm node_modules"
    try:
        target = runner_client.ensure_node_modules(runner_client.node_dir_from_env())
    except (runner_client.RunnerError, OSError) as exc:
        return _infra_failure("0", name, exc)
    return StepOutcome("0", name, STATUS_OK, str(target))


# ---------------------------------------------------------------------------
# 8 bước
# ---------------------------------------------------------------------------


def step_ruff_format() -> StepOutcome:
    """Bước 1: `ruff format --check`."""
    r = _run(["ruff", "format", "--check", "."])
    return StepOutcome("1", "ruff format --check", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


def step_ruff_check() -> StepOutcome:
    """Bước 2: `ruff check`."""
    r = _run(["ruff", "check", "."])
    return StepOutcome("2", "ruff check", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


MYPY_LOG = Path("/tmp/mypy.log")
"""Nơi mypy nền ghi stdout+stderr; bước 3 in lại nguyên văn lúc chờ xong, để log cổng không xen với pytest."""

MYPY_CACHE_PREFIX = "mypy-tree-"
"""Tiền tố thư mục cache mypy khoá theo băm cây (`gc` nhận ra để giữ N cái gần nhất)."""

MYPY_CACHE_KEEP = 3
"""Số thư mục `mypy-tree-*` `gc` giữ lại (mới nhất theo mtime). Mỗi cái ~200 MB; 3 phủ HEAD + hai lượt trước."""


VENV_PREFIX = "venv-lock-"
"""Tiền tố venv dùng chung theo băm `uv.lock` (`in_container.sh`); `gc` giữ `MYPY_CACHE_KEEP` cái mới nhất."""


def _hash_tree(files: list[Path], extra: list[Path]) -> str:
    """sha256 của (đường dẫn, nội dung) từng file trong `files` rồi `extra`, cộng phiên bản mypy.

    Mỗi phần vào băm kèm độ dài: hai cây khác nhau không thể ghép thành cùng một chuỗi byte
    (file "ab" + "" khác file "a" + "b"), nên trúng băm thật sự là trùng đầu vào.
    """
    h = hashlib.sha256()
    try:
        h.update(version("mypy").encode())
    except PackageNotFoundError:  # mypy chưa cài: băm vẫn xác định, bước 3 sẽ hỏng vì thiếu lệnh
        h.update(b"mypy-missing")
    for path in [*files, *extra]:
        try:
            data = path.read_bytes()
        except OSError:  # file biến mất giữa lúc quét: băm theo "không đọc được", không ném giữa cổng
            data = b"<unreadable>"
        for part in (str(path).encode(), data):
            h.update(str(len(part)).encode())
            h.update(part)
    return h.hexdigest()[:16]


def mypy_source_hash() -> str:
    """Băm MỌI đầu vào của mypy: `.py` dưới `[tool.mypy] files`, `pyproject.toml`, `uv.lock`, phiên bản mypy.

    Lấy danh sách thư mục từ chính `pyproject.toml` (không chép lại `["packages", "apps", "tools"]`),
    bỏ `__pycache__`. `pyproject.toml` phủ cấu hình mypy, `uv.lock` phủ mọi stub/thư viện mypy đọc
    theo `mypy_path`, phiên bản mypy phủ chính trình kiểm — nên hai lượt cùng băm có đầu vào giống
    hệt nhau từng byte.
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    try:
        config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        # Không đọc được cấu hình thì không suy ra được root: băm vẫn phủ pyproject + uv.lock ở
        # `_hash_tree` (kể cả khi thiếu), nên nó vẫn xác định — và mypy cũng sẽ hỏng ngay vì cấu hình.
        config = {}
    roots = config.get("tool", {}).get("mypy", {}).get("files", [])
    sources = sorted(
        p
        for root in roots
        for pattern in ("*.py", "*.pyi")
        for p in (REPO_ROOT / root).rglob(pattern)
        if "__pycache__" not in p.parts
    )
    return _hash_tree(sources, [pyproject, REPO_ROOT / "uv.lock"])


def mypy_cache_dir() -> str:
    """`MYPY_CACHE_DIR` của lượt: `<thư mục cha>/mypy-tree-<băm>`, hoặc `os.devnull` nếu không có biến (NO-306).

    NO-306: cache ấm cố định `/work/mypy-${VERIFY_NAME}` đổi KẾT QUẢ — mypy so băm nguồn từng module,
    khớp thì phát lại lỗi (hay "Success") đã lưu dù đầu vào hiện tại không còn sinh ra nó; `main` @ 30b78ae
    đỏ bước 3 giả. Cách chữa cũ là chạy nguội mọi cổng đầy đủ (212 s mỗi lượt).

    Khoá thư mục bằng băm toàn bộ đầu vào làm cái đó thừa: trúng băm nghĩa là mọi file mypy đọc giống
    hệt lượt đã ghi cache, nên kết quả phát lại ĐÚNG BẰNG kết quả một lượt nguội — không còn cache "lai"
    giữa hai cây nguồn khác nhau. Lệch một byte → băm khác → thư mục mới, rỗng, tức là chạy nguội.
    `gc` dọn thư mục cũ (`MYPY_CACHE_KEEP`). Hai lượt cùng băm chạy song song (ENV §4 cho tối đa 2)
    ghi chung thư mục: mypy đọc không nổi một file cache dở thì coi module cũ và tính lại — chậm hơn,
    không sai; không khoá file cho một trường hợp hiếm mà hậu quả chỉ là mất cache.
    """
    current = os.environ.get("MYPY_CACHE_DIR")
    if not current:
        return os.devnull
    return str(Path(current).parent / f"{MYPY_CACHE_PREFIX}{mypy_source_hash()}")


def _mypy_env() -> dict[str, str]:
    """Môi trường cho tiến trình mypy: như lượt này, chỉ đổi `MYPY_CACHE_DIR` sang thư mục theo băm cây."""
    return {**os.environ, "MYPY_CACHE_DIR": mypy_cache_dir()}


def step_mypy() -> StepOutcome:
    """Bước 3: `mypy` (strict) chạy ngay tại chỗ, cache theo băm cây (`mypy_cache_dir`)."""
    r = _run(["mypy"], env=_mypy_env())
    return StepOutcome("3", "mypy --strict", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


def start_mypy(env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    """Chạy mypy nền, stdout+stderr vào `MYPY_LOG` — để nó chạy song song bước 5 (C1).

    Bước 3 nguội ~219 s, bước 5 ~808 s: chạy chồng lên nhau thì 219 s đó biến mất khỏi tổng. Output
    phải vào file chứ không ra stdout chung, nếu không dòng của mypy xen giữa dòng pytest trong log cổng.
    """
    handle = MYPY_LOG.open("wb")
    try:
        return _popen(["mypy"], stdout=handle, stderr=subprocess.STDOUT, env=env or _mypy_env())
    finally:
        handle.close()  # Popen đã giữ fd riêng của nó


def join_mypy(proc: subprocess.Popen[bytes]) -> StepOutcome:
    """Bước 3 của cổng đầy đủ: chờ mypy nền xong, in lại `MYPY_LOG`, trạng thái từ mã thoát THẬT."""
    returncode = proc.wait()
    print("--- mypy (nền) ---")
    try:
        print(MYPY_LOG.read_text(encoding="utf-8", errors="replace"), end="")
    except OSError as exc:
        print(f"đọc {MYPY_LOG} hỏng: {exc}", file=sys.stderr)
    return StepOutcome("3", "mypy --strict", STATUS_OK if returncode == 0 else STATUS_FAIL)


@contextmanager
def background_mypy(env: dict[str, str] | None = None) -> Iterator[subprocess.Popen[bytes]]:
    """Chạy mypy nền suốt khối; ra khối mà nó còn sống thì giết.

    Bước 1/2 hỏng là cổng dừng sớm, bước 3 ghi "chưa chạy" — mypy nền phải chết theo, nếu không
    container còn một tiến trình con giữ fd và `_stop_pumps` đợi hết `LOG_DRAIN_TIMEOUT_S`.
    """
    proc = start_mypy(env)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def step_lint_imports() -> StepOutcome:
    """Bước 4: `lint-imports` kiểm ranh giới import."""
    r = _run(["lint-imports"])
    return StepOutcome("4", "lint-imports", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


JUNIT_FILE = Path("/tmp/junit.xml")
"""Nơi pytest ghi junit (`addopts` của `pyproject.toml`); `case_gate` đọc đúng đường này."""


JUNIT_PERF_FILE = Path("/tmp/junit-perf.xml")
"""Nơi bước 5b ghi junit của pytest perf (`--junitxml` ở `step_perf`); `case_gate` đọc đúng đường này."""


def export_junit(source: Path, suffix: str) -> None:
    """Chép `source` thành `<tên log><suffix>` cạnh log cổng khi lượt có `VERIFY_LOG_FILE` (NO-324).

    Container `AutoRemove` nên junit ở `/tmp` mất theo; thư mục log là bind-mount ra host. Là bản sao
    như log: không log, chưa có junit hay chép hỏng thì bỏ qua (hỏng thì cảnh báo ở stderr), không đổi
    kết quả bước 5.
    """
    log_file = os.environ.get("VERIFY_LOG_FILE")
    if not log_file or not source.is_file():
        return
    dest = Path(log_file).with_suffix(suffix)
    try:
        shutil.copy2(source, dest)
    except OSError as exc:
        print(f"chép junit {source} -> {dest} hỏng: {exc}", file=sys.stderr)


PYTEST_WORKERS_ENV = "VERIFY_PYTEST_WORKERS"
DEFAULT_PYTEST_WORKERS = "6"


def pytest_workers() -> str:
    """Số tiến trình xdist của bước 5 — `VERIFY_PYTEST_WORKERS`, mặc định 6 (FIX-112, đo lại FIX-350).

    Đo 2026-10-05, một cổng chạy một mình, 8383 test, container cổng 12 CPU / 11,5 GB (NO-271, NO-280):
    `-n 4` 571 s (đỉnh RAM VM 8,7 GiB) · `-n 6` 505 s (8,5 GiB) · `-n 8` 513 s (10,1 GiB), cả ba xanh,
    không chập chờn. `-n 6` nhanh nhất; `-n 8` không nhanh hơn mà chỉ còn chừa ~1,4 GiB cho VM. RAM
    gần như nằm hết ở container `verify-run`: mỗi tiến trình pytest nạp `torch` ~2 GiB PSS, còn
    ~10 container dịch vụ cộng lại chỉ ~0,5 GiB — nên số tiến trình là số nhân của RAM. Hai cổng
    cùng lúc (2 lần ~8,5 GiB) vẫn vượt VM, ENV §4 giữ "một cổng đầy đủ" (đặt 3 khi chạy cạnh).
    Giá trị rỗng hay lạ → dùng mặc định chứ không truyền tiếp cho pytest (xdist báo lỗi dùng sai,
    bước 5 hỏng vì cấu hình chứ không vì mã).
    """
    value = os.environ.get(PYTEST_WORKERS_ENV, "").strip()
    return value if value.isdigit() and int(value) > 0 else DEFAULT_PYTEST_WORKERS


def step_coverage() -> StepOutcome:
    """Bước 5: pytest song song dưới `pytest-cov`, rồi `coverage_gate` (FIX-112).

    `pytest-cov` chứ không `coverage run -m pytest`: xdist dựng tiến trình con qua execnet, mà
    `coverage run` chỉ đo tiến trình chủ — độ phủ sẽ tụt gần về 0. Plugin bật coverage trong từng
    tiến trình con rồi gộp vào `COVERAGE_FILE` (cấu hình `parallel = true` giữ nguyên), nên không
    còn `coverage combine` riêng: gộp xong rồi, gọi lại chỉ thấy "No data to combine".
    `--cov` không kèm đường dẫn: lấy `[tool.coverage.run] source`, cùng nguồn như trước.
    `--cov-report=` tắt báo cáo của plugin — số thật do `coverage_gate` in ra từ `coverage json`.
    `--dist loadfile`: mọi test cùng file vào cùng một tiến trình, nên fixture phiên (container
    dịch vụ) dựng một lần cho cả file và test phụ thuộc thứ tự trong một file không bị xé ra.
    PERF-03 đã thử `loadgroup` + nhóm theo tệp: nhóm không được áp, test cùng tệp bị xé ra mọi worker
    (mỗi worker huấn luyện lại YOLO, pytest 585 → 1.108 s; test ghi chung `/tmp/ci-pip-audit.json` đỏ) — đừng thử lại.
    """
    steps = [
        ["pytest", "-n", pytest_workers(), "--dist", "loadfile", "--cov", "--cov-report="],
        ["coverage", "json"],
    ]
    for cmd in steps:
        r = _run(cmd)
        if cmd[0] == "pytest":
            export_junit(JUNIT_FILE, ".junit.xml")  # cả khi pytest hỏng: lúc đỏ là lúc cần junit nhất
        if r.returncode != 0:
            return StepOutcome("5", "pytest -n (cov)", STATUS_FAIL, "pytest hoặc coverage hỏng")
    r = _run([sys.executable, "-m", "tools.coverage_gate"])
    return StepOutcome("5", "pytest -n (cov) → coverage_gate", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


_PERF_EXPR = "perf and not gpu"
CASE_IN_NAME_RE = re.compile(r"__[A-Z]\d{2}[a-z]?(?:_|\[|$)")


def perf_case_named(node_ids: list[str]) -> list[str]:
    """Test `perf` mang tên case (`__<caseid>`) → hỏng: case phải tính ở bước 5, dưới coverage."""
    return [n for n in node_ids if CASE_IN_NAME_RE.search(n.rsplit("::", 1)[-1])]


def _collect_perf(paths: list[str]) -> list[str]:
    """Collect test `perf` (không gpu) trong `paths`, trả danh sách node id."""
    # --junitxml riêng: addopts ghi /tmp/junit.xml, collect-only không được đè junit của bước 5
    r = _run(
        ["pytest", "--collect-only", "-q", "-m", _PERF_EXPR, "--junitxml=/tmp/junit-collect.xml", *paths],
        capture_output=True,
    )
    return [line.strip() for line in r.stdout.splitlines() if "::" in line]


def step_perf() -> StepOutcome:
    """Bước 5b: collect và chạy pytest perf của đơn vị bị chạm, rồi `case_gate`."""
    name = "pytest -m perf → case_gate"
    integration = os.environ.get("VERIFY_BRANCH") == "integration"
    # integration: mọi testpaths (paths rỗng); worker: chỉ đơn vị bị chạm
    paths = [] if integration else [u for u in sorted(_touched_units()) if (REPO_ROOT / u).is_dir()]
    perf_ids = _collect_perf(paths) if integration or paths else []

    bad = perf_case_named(perf_ids)
    if bad:
        return StepOutcome("5b", name, STATUS_FAIL, f"test perf mang tên case: {', '.join(bad)}")

    detail = f"perf: {len(perf_ids)} test"
    if perf_ids:
        # Chỉ các tệp chứa test perf vừa collect: khỏi import/collect lại cả đơn vị (~5 nghìn test bị bỏ chọn).
        perf_files = sorted({node.split("::", 1)[0] for node in perf_ids})
        r = _run(["pytest", "-m", _PERF_EXPR, f"--junitxml={JUNIT_PERF_FILE}", *perf_files])
        export_junit(JUNIT_PERF_FILE, ".perf.junit.xml")  # cả khi perf hỏng
        if r.returncode != 0:
            return StepOutcome("5b", name, STATUS_FAIL, "test perf hỏng")
    elif not integration:
        detail = "perf: 0 đơn vị bị chạm"

    r = _run([sys.executable, "-m", "tools.case_gate"])
    return StepOutcome("5b", name, STATUS_OK if r.returncode == 0 else STATUS_FAIL, detail)


def step_migrations() -> StepOutcome:
    """Bước 6: `lint_migrations` rồi `migrate_check`, theo file điều kiện của B0-03."""
    gated = _gate_status(REPO_ROOT / "packages" / "db" / "migrate_check.py", "B0-03")
    if gated is not None:
        return StepOutcome("6", "lint_migrations → migrate_check", gated)
    r1 = _run([sys.executable, "-m", "tools.lint_migrations"])
    if r1.returncode != 0:
        return StepOutcome("6", "lint_migrations → migrate_check", STATUS_FAIL, "lint_migrations hỏng")
    r2 = _run([sys.executable, "-m", "packages.db.migrate_check"])
    return StepOutcome("6", "lint_migrations → migrate_check", STATUS_OK if r2.returncode == 0 else STATUS_FAIL)


def step_contract() -> StepOutcome:
    """Bước 7: `tools.contract.check`, theo file điều kiện của B0-07."""
    gated = _gate_status(REPO_ROOT / "tools" / "contract" / "check.py", "B0-07")
    if gated is not None:
        return StepOutcome("7", "H1 H3 H4 H5 (tools.contract.check)", gated)
    r = _run([sys.executable, "-m", "tools.contract.check"])
    return StepOutcome("7", "H1 H3 H4 H5 (tools.contract.check)", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


def step_openapi() -> StepOutcome:
    """Bước 8: xuất openapi ra /tmp, so với bản đã commit ở nhánh integration; theo file điều kiện B0-06."""
    gated = _gate_status(REPO_ROOT / "apps" / "api" / "core" / "openapi.py", "B0-06")
    if gated is not None:
        return StepOutcome("8", "openapi", gated)
    cmd = [sys.executable, "-m", "apps.api.core.openapi", "--out", "/tmp/openapi.json"]
    if os.environ.get("VERIFY_BRANCH") == "integration":
        # NO-105: bản tham chiếu đã commit là `docs/contracts/openapi.json` (`openapi.json` ở gốc
        # bị .gitignore, không có trên nhánh tích hợp/CI — BE-00 §12 bước 8).
        cmd += ["--compare", "docs/contracts/openapi.json"]
    r = _run(cmd)
    return StepOutcome("8", "openapi", STATUS_OK if r.returncode == 0 else STATUS_FAIL)


# Tên hiển thị cho dòng "chưa chạy" (bước chưa được gọi thì không có StepOutcome riêng)
_STEP_LABELS = {
    "1": "ruff format --check",
    "2": "ruff check",
    "3": "mypy --strict",
    "4": "lint-imports",
    "5": "pytest -n (cov) → coverage_gate",
    "5b": "pytest -m perf → case_gate",
    "6": "lint_migrations → migrate_check",
    "7": "H1 H3 H4 H5 (tools.contract.check)",
    "8": "openapi",
}

_ALL_STEPS = [
    ("0", step_warm_node_modules),
    ("1", step_ruff_format),
    ("2", step_ruff_check),
    ("3", step_mypy),
    ("4", step_lint_imports),
    ("5", step_coverage),
    ("5b", step_perf),
    ("6", step_migrations),
    ("7", step_contract),
    ("8", step_openapi),
]


def run_steps(steps: Sequence[tuple[str, Callable[[], StepOutcome]]], wanted: set[str] | None) -> list[StepOutcome]:
    """Chạy tuần tự, dừng ở bước hỏng đầu — bước sau ghi 'chưa chạy'. Thuần, dễ test."""
    outcomes: list[StepOutcome] = []
    failed = False
    for number, fn in steps:
        if wanted is not None and number not in wanted:
            continue
        if failed:
            outcomes.append(StepOutcome(number, _STEP_LABELS.get(number, number), STATUS_SKIP))
            continue
        outcome = fn()
        outcomes.append(outcome)
        if outcome.status == STATUS_FAIL:
            failed = True
    return outcomes


def full_gate_steps(proc: subprocess.Popen[bytes]) -> list[tuple[str, Callable[[], StepOutcome]]]:
    """Thứ tự chạy của cổng đầy đủ: bước 3 chỉ còn là lượt CHỜ mypy nền, đặt ngay sau bước 5 (C1).

    mypy nền khởi động trước bước 1 nên nó chạy chồng lên bước 1, 2, 4 và 5. Dừng sớm giữ nguyên theo
    thứ tự chạy mới: bước 1/2/4/5 hỏng → bước 3 "chưa chạy" (và `background_mypy` giết tiến trình).
    Khác một điểm, không tránh được khi song song: mypy hỏng không còn làm bước 4, 5 thành "chưa chạy"
    — chúng đã chạy xong rồi, nên bảng in kết quả THẬT của chúng. Mã thoát vẫn 1.
    """
    steps = [(number, fn) for number, fn in _ALL_STEPS if number != "3"]
    after_5 = [number for number, _ in steps].index("5") + 1
    steps.insert(after_5, ("3", lambda: join_mypy(proc)))
    return steps


def _mypy_runs_beside_pytest(wanted: set[str] | None) -> bool:
    """Lượt này có chạy chồng mypy lên bước 5 không: cổng đầy đủ VÀ lượt thật sự có cả bước 3 và bước 5.

    Thiếu một trong hai thì không có gì để chồng lên nhau — chạy tuần tự như `--steps` của worker, khỏi
    dựng tiến trình nền rồi giết ngay.
    """
    numbers = {number for number, _ in _ALL_STEPS}
    full_gate = wanted is None or wanted >= numbers
    return full_gate and {"3", "5"} <= (numbers if wanted is None else wanted & numbers)


_STEP_ORDER = {number: index for index, (number, _) in enumerate(_ALL_STEPS)}


def _in_step_order(outcomes: list[StepOutcome]) -> list[StepOutcome]:
    """Sắp bảng theo thứ tự bước của `_ALL_STEPS` (bước 3 chạy sau bước 5 vẫn in ở dòng của nó).

    Dòng không phải bước (chép mẫu golden, số "—") xuống cuối, giữ thứ tự tương đối.
    """
    return sorted(outcomes, key=lambda o: _STEP_ORDER.get(o.number, len(_STEP_ORDER)))


# ---------------------------------------------------------------------------
# Cổng nhanh (PERF-03): `verify --fast` — xem trước mỗi lần sửa, cổng đầy đủ vẫn chạy trước merge
# ---------------------------------------------------------------------------

FAST_PYTEST_WORKERS = "4"
FAST_MYPY_PREFIX = "mypy-fast-"
_FAST_PYTEST = "pytest bị ảnh hưởng (cov đơn vị chạm)"
FAST_STEP_INPUTS: dict[str, tuple[str, ...]] = {
    "6": ("packages/db/",),
    # shortcut: `apps/api/*/cases.toml` đổi không kéo bước 7 ở cổng nhanh (tiền tố không tả được, thêm cả
    # `apps/api/` thì mọi lần sửa API trả giá bước 0 + 7); cổng đầy đủ trước merge luôn chạy bước 7.
    "7": ("tools/contract/", "tools/charter.py", "docs/contracts/"),
    "8": ("apps/api/", "packages/domain/", "packages/ml_contracts/"),
}
"""Bước có điều kiện của cổng nhanh → tiền tố đầu vào của nó: không tệp đổi nào khớp thì bỏ bước."""


def fast_test_paths(changed: list[str]) -> list[str] | None:
    """Tệp test của cổng nhanh (`affected.affected_tests`); `None` = cả bộ, `[]` = không test nào.

    Bước 7 cần mẫu golden của MỌI thao tác (`_missing_success`), mà chỉ cả bộ mới ghi đủ: đầu vào bước 7
    đổi thì chạy cả bộ. Phần đồ thị import không thấy (nạp động, `python -m`) là việc của cổng đầy đủ.
    """
    if _fast_step_wanted("7", changed):
        return None
    return affected.affected_tests(changed, REPO_ROOT)


def fast_cov_units(changed: list[str]) -> list[str]:
    """Đơn vị mã bị chạm còn trên đĩa — nguồn `--cov` của cổng nhanh (đơn vị ngoài `source` không đo)."""
    units = {u for u in (unit_of(c) for c in changed) if u is not None}
    return sorted(u for u in units if u.startswith(affected.IMPORT_ROOTS) and (REPO_ROOT / u).is_dir())


def _fast_step_wanted(number: str, changed: list[str]) -> bool:
    """Có tệp đổi nào nằm dưới đầu vào của bước `number` (`FAST_STEP_INPUTS`)."""
    return any(c.startswith(FAST_STEP_INPUTS[number]) for c in changed)


def fast_mypy_env() -> dict[str, str]:
    """Cache mypy ẤM theo worktree (`mypy-fast-<VERIFY_NAME>`): chỉ module đổi bị kiểm lại.

    Xem trước, không thay cổng đầy đủ: cache ấm có thể phát lại kết quả cũ khi tệp đổi mà giữ nguyên
    mtime và cỡ (NO-306); cổng đầy đủ vẫn dùng cache theo băm cây (`mypy_cache_dir`).
    """
    current = os.environ.get("MYPY_CACHE_DIR")
    if not current:
        return dict(os.environ)
    name = normalize_verify_name(os.environ.get("VERIFY_NAME", "")) or "default"
    return {**os.environ, "MYPY_CACHE_DIR": str(Path(current).parent / f"{FAST_MYPY_PREFIX}{name}")}


def step_fast_pytest() -> StepOutcome:
    """Bước 5 của cổng nhanh: pytest tệp bị ảnh hưởng, độ phủ + `coverage_gate` chỉ trên đơn vị bị chạm.

    Tập con test cho độ phủ không cao hơn cả bộ, nên đạt ở đây thì ngưỡng đơn vị/tập tệp bị chạm của cổng
    đầy đủ cũng đạt; dòng "tổng" của `coverage_gate` ở đây chỉ là tổng các đơn vị bị chạm.
    """
    changed = _changed_files()
    paths = fast_test_paths(changed)
    if paths == []:
        return StepOutcome("5", _FAST_PYTEST, STATUS_NA, "không test nào bị ảnh hưởng")
    detail = "cả bộ" if paths is None else f"{len(paths)} tệp test"
    units = fast_cov_units(changed)
    cov = [*(f"--cov={u}" for u in units), "--cov-report="] if units else []
    r = _run(["pytest", "-n", FAST_PYTEST_WORKERS, "--dist", "loadfile", "--durations=10", *cov, *(paths or [])])
    if r.returncode == 5:  # pytest không thu được test nào: không hỏng, nhưng cũng không "đạt" (K25)
        return StepOutcome("5", _FAST_PYTEST, STATUS_NA, f"không có test — {detail}")
    if r.returncode != 0:
        return StepOutcome("5", _FAST_PYTEST, STATUS_FAIL, f"pytest hỏng — {detail}")
    if not units:
        return StepOutcome("5", _FAST_PYTEST, STATUS_OK, f"{detail}; không đơn vị mã nào để đo độ phủ")
    for cmd in (["coverage", "json"], [sys.executable, "-m", "tools.coverage_gate"]):
        if _run(cmd).returncode != 0:
            return StepOutcome("5", _FAST_PYTEST, STATUS_FAIL, f"độ phủ hỏng — {detail}")
    return StepOutcome("5", _FAST_PYTEST, STATUS_OK, f"{detail}; cov {', '.join(units)}")


def fast_gate_steps(proc: subprocess.Popen[bytes]) -> list[tuple[str, Callable[[], StepOutcome]]]:
    """Thứ tự cổng nhanh: 1, 2, 4, pytest bị ảnh hưởng, chờ mypy nền; 6/7/8 chỉ khi đầu vào của chúng đổi."""
    changed = _changed_files()
    steps: list[tuple[str, Callable[[], StepOutcome]]] = [
        ("1", step_ruff_format),
        ("2", step_ruff_check),
        ("4", step_lint_imports),
    ]
    if _fast_step_wanted("7", changed):
        steps.append(("0", step_warm_node_modules))  # fixture `contract_build` của bước 5 cần runner Node
    steps += [("5", step_fast_pytest), ("3", lambda: join_mypy(proc))]
    conditional = {"6": step_migrations, "7": step_contract, "8": step_openapi}
    steps += [(n, fn) for n, fn in conditional.items() if _fast_step_wanted(n, changed)]
    return steps


def _run_fast() -> int:
    """`verify --fast`: các bước của `fast_gate_steps`, mypy nền trên cache ấm; 1 nếu có dòng "hỏng"."""
    with background_mypy(fast_mypy_env()) as proc:
        outcomes = _in_step_order(run_steps(fast_gate_steps(proc), None))
    for outcome in outcomes:  # dòng "chưa chạy" lấy nhãn của cổng đầy đủ — bước 5 ở đây không đo độ phủ
        if outcome.number == "5":
            outcome.name = _FAST_PYTEST
    _print_table(outcomes)
    print(
        "Cổng NHANH: test bị ảnh hưởng (đồ thị import + fixture), độ phủ chỉ đơn vị bị chạm, không 5b; 6/7/8 chỉ"
        " khi đầu vào đổi — `verify` đầy đủ trước merge."
    )
    return 1 if any(o.status == STATUS_FAIL for o in outcomes) else 0


def _run_verify(args: argparse.Namespace) -> int:
    """Chạy các bước được chọn, chép mẫu golden, in bảng; 1 nếu có dòng "hỏng".

    Cổng đầy đủ (không `--steps`, hay `--steps` gồm mọi bước) chạy mypy NỀN song song bước 5
    (`full_gate_steps`); `--steps` của worker chạy tuần tự như cũ. Cả hai dùng cùng cache mypy khoá
    theo băm cây (`mypy_cache_dir`), nên kết quả bước 3 của worker cũng bằng kết quả nguội (NO-306).
    """
    if getattr(args, "fast", False):
        return _run_fast()
    wanted = set(args.steps.split(",")) if args.steps else None
    # Không ai gõ bước "0": nó đi kèm khi lượt có bước cần runner Node.
    if wanted is not None and wanted & _RUNNER_STEPS:
        wanted.add("0")
    if _mypy_runs_beside_pytest(wanted):
        with background_mypy() as proc:
            outcomes = run_steps(full_gate_steps(proc), wanted)
    else:
        outcomes = run_steps(_ALL_STEPS, wanted)
    outcomes = _in_step_order(outcomes)
    # Chép cả khi có bước hỏng: lúc đỏ là lúc người điều phối cần xem mẫu nhất. Chép hỏng
    # là một dòng bảng "hỏng" (thoát 1), không phải traceback thay chỗ cả bảng (NO-075).
    try:
        export_contract_samples()
    except OSError as exc:
        outcomes.append(_infra_failure("—", "chép mẫu golden", exc))
    _print_table(outcomes)
    return 1 if any(o.status == STATUS_FAIL for o in outcomes) else 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Lượt verify; `run.sh` đặt `VERIFY_LOG_FILE` thì cả output của lượt đồng thời vào file đó (NO-080).

    Log là bản sao, stdout vẫn là nguồn chính: ghi log hỏng giữa lượt chỉ báo ra stderr, không đổi mã
    thoát. Không mở được file log → `OSError` ngay, trước bước đầu (thiếu mount là cấu hình hỏng).
    Bước ném lỗi lạ → traceback vào log và stderr, mã thoát 1, vẫn có dòng "mã thoát" (NO-089).
    """
    log_file = os.environ.get("VERIFY_LOG_FILE")
    if not log_file:
        return _run_verify(args)
    with _tee_output(Path(log_file)) as problems:
        try:
            rc = _run_verify(args)
        # In traceback khi fd 1/2 còn nối vào log: để nó nổi qua `with` thì interpreter in ra stderr gốc, sau khi
        # `_tee_output` đã trả fd — log dừng ở dòng output cuối, không lý do, không "mã thoát".
        except Exception:  # noqa: BLE001 — không nuốt: traceback in ra đủ, mã thoát 1
            traceback.print_exc()
            rc = 1
        print(f"mã thoát: {rc}")
    for problem in problems:
        print(f"log cổng {log_file}: {problem}", file=sys.stderr)
    return rc


# ---------------------------------------------------------------------------
# Log cổng ra thư mục host — NO-080
# ---------------------------------------------------------------------------

LOG_DRAIN_TIMEOUT_S = 10.0
"""Trần chờ mỗi luồng chép xả nốt ống khi lượt xong. Quá trần nghĩa là một tiến trình con mồ côi còn giữ
đầu ghi của ống: bỏ đợi thay vì treo cổng — phần nó in sau đó không vào log."""


def _write_each(sinks: list[BinaryIO], chunk: bytes, problems: list[str]) -> None:
    """Ghi `chunk` vào từng đích; đích hỏng (đĩa đầy, file đã đóng) thì gỡ khỏi `sinks`, lý do vào `problems`."""
    for sink in list(sinks):
        try:
            sink.write(chunk)
            sink.flush()
        except (OSError, ValueError) as exc:
            sinks.remove(sink)
            problems.append(f"bỏ một đích ghi: {exc}")


def _pump(read_fd: int, sinks: list[BinaryIO], problems: list[str]) -> None:
    """Chép từng khúc của một ống sang mọi đích tới khi mọi đầu ghi đã đóng (EOF).

    Đích hỏng không làm luồng dừng: luồng chết là ống đầy, mọi tiến trình đang in bị chặn — cổng treo.
    """
    with open(read_fd, "rb", buffering=0) as src:
        while chunk := src.read(65536):
            _write_each(sinks, chunk, problems)


def _start_pump(fd: int, log: BinaryIO, problems: list[str]) -> tuple[int, BinaryIO, threading.Thread]:
    """Thay `fd` bằng đầu ghi của một ống mới (tiến trình con thừa hưởng); một luồng chép ống sang fd gốc và `log`."""
    original = os.fdopen(os.dup(fd), "wb")
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, fd)
    os.close(write_fd)
    thread = threading.Thread(target=_pump, args=(read_fd, [original, log], problems), daemon=True)
    thread.start()
    return fd, original, thread


def _stop_pumps(pumps: list[tuple[int, BinaryIO, threading.Thread]], problems: list[str]) -> None:
    """Trả fd gốc (đóng đầu ghi của mình → EOF), đợi mỗi luồng xả nốt trong `LOG_DRAIN_TIMEOUT_S`."""
    for fd, original, _thread in pumps:
        os.dup2(original.fileno(), fd)
    for fd, original, thread in pumps:
        thread.join(LOG_DRAIN_TIMEOUT_S)
        if thread.is_alive():
            problems.append(f"tiến trình con còn giữ fd {fd} sau {LOG_DRAIN_TIMEOUT_S} s — phần in sau đó mất")
        original.close()


@contextmanager
def _tee_output(log_file: Path) -> Iterator[list[str]]:
    """Chép output của lượt — cả của tiến trình con (ruff, pytest, coverage_gate) — vào `log_file` (NO-080).

    Container `verify-run` tự xoá khi thoát và output chỉ đi qua client `docker compose run`: client chết
    (shell bọc bị cắt) là mất log. `run.sh` mount thư mục host của `log_file`, nên file còn lại. fd 1 và 2
    mỗi fd một ống + một luồng chép: stdout/stderr vẫn tách như cũ, chỉ một đối tượng ghi file. Đợi luồng
    xả xong rồi mới trả, để dòng cuối (bảng, mã thoát) không mất khi tiến trình thoát. Trao danh sách vấn đề
    của log (đích hỏng, tiến trình con mồ côi), đọc sau khi khối `with` xong.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with (
        log_file.open("ab") as log,
        # sys.stdout có thể là đối tượng không đi qua fd 1 (pytest capture): print đi qua fd 1, 2 đã đổi.
        open(1, "w", encoding="utf-8", buffering=1, closefd=False) as out,
        open(2, "w", encoding="utf-8", buffering=1, closefd=False) as err,
    ):
        sys.stdout.flush()
        sys.stderr.flush()
        pumps = [_start_pump(fd, log, problems) for fd in (1, 2)]
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            yield problems
        finally:
            out.flush()
            err.flush()
            sys.stdout, sys.stderr = saved
            _stop_pumps(pumps, problems)


# ---------------------------------------------------------------------------
# Việc khác — [2]
# ---------------------------------------------------------------------------


def _clean_dir(d: Path) -> Path:
    """Thư mục ra của một việc: xoá sạch nội dung trước khi ghi ([6]C).

    `run.sh` bind-mount thư mục host **đúng vào** `/src-out/<việc>` (`lock`, `openapi`,
    `merge-heads`, `contract-samples`), mà mount point thì không gỡ được (EBUSY, NO-001).
    Vì vậy chỉ xoá **từng mục con**, giữ chính `d`, và để lỗi xoá nổi lên: sót một file
    cũ là `merge-heads` chép nhầm revision cũ ra ngoài (NO-008).
    """
    d.mkdir(parents=True, exist_ok=True)
    for child in d.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return d


def export_contract_samples() -> None:
    """Chép mẫu golden của lượt ra `/src-out/contract-samples` (ENV §2, NO-043).

    Không đặt `CONTRACT_SAMPLES_DIR` (chạy ngoài cổng) → không làm gì. Đặt mà chưa test nào
    ghi mẫu → thư mục ra rỗng, để không còn mẫu của lượt trước làm người đọc nhầm.
    """
    source = os.environ.get("CONTRACT_SAMPLES_DIR")
    if not source:
        return
    dest = _clean_dir(OUT_DIR / "contract-samples")
    if Path(source).is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True)


def cmd_lock(_args: argparse.Namespace) -> int:
    """Việc `lock`: chép `uv.lock` ra thư mục ra sau khi xoá nội dung cũ."""
    # in_container.sh đã chạy `uv lock` (không --upgrade) rồi `uv sync --locked`.
    dest = _clean_dir(OUT_DIR / "lock")
    shutil.copy2(REPO_ROOT / "uv.lock", dest / "uv.lock")
    print(f"đã ghi {dest / 'uv.lock'}")
    return 0


def cmd_openapi(_args: argparse.Namespace) -> int:
    """Việc `openapi`: xuất `openapi.json` hợp nhất ra thư mục ra; thiếu module B0-06 thì thoát 1."""
    if not (REPO_ROOT / "apps" / "api" / "core" / "openapi.py").is_file():
        print("openapi: thiếu apps/api/core/openapi.py — chủ B0-06 chưa hợp nhất", file=sys.stderr)
        return 1
    dest = _clean_dir(OUT_DIR / "openapi")
    return _run([sys.executable, "-m", "apps.api.core.openapi", "--out", str(dest / "openapi.json")]).returncode


def _alembic_heads(alembic_ini: Path) -> list[str]:
    """Danh sách revision head của alembic theo `alembic heads`."""
    r = _run(["alembic", "-c", str(alembic_ini), "heads"], capture_output=True)
    return [line.split()[0] for line in r.stdout.splitlines() if line.strip()]


def cmd_merge_heads(args: argparse.Namespace) -> int:
    """Việc `merge-heads`: tạo revision merge hai head, kiểm lint, chép file mới ra thư mục ra."""
    name: str = args.name
    if not MERGE_REVISION_RE.match(name):
        print(f"merge-heads: '{name}' sai mẫu r<yyyymmdd>_merge_w<nn>_<k>", file=sys.stderr)
        return 1

    alembic_ini = REPO_ROOT / "packages" / "db" / "alembic.ini"
    if not alembic_ini.is_file():
        print("merge-heads: thiếu packages/db/alembic.ini — chủ B0-03 chưa hợp nhất", file=sys.stderr)
        return 1

    versions_dir = REPO_ROOT / "packages" / "db" / "migrations" / "versions"
    before = sorted(versions_dir.glob("*.py")) if versions_dir.is_dir() else []
    if name in {revision_of(p) for p in before}:
        print(f"merge-heads: '{name}' đã là revision có sẵn", file=sys.stderr)
        return 1

    heads = _alembic_heads(alembic_ini)
    if len(heads) < 2:
        print(f"merge-heads: chỉ {len(heads)} head, cần ≥ 2", file=sys.stderr)
        return 1

    if _run(["alembic", "-c", str(alembic_ini), "merge", "heads", "--rev-id", name]).returncode != 0:
        return 1
    after_heads = _alembic_heads(alembic_ini)
    if after_heads != [name]:
        print(f"merge-heads: sau merge còn {after_heads}, cần đúng [{name}]", file=sys.stderr)
        return 1
    if lint_migrations_main([str(versions_dir)]) != 0:
        print("merge-heads: lint_migrations hỏng sau khi merge", file=sys.stderr)
        return 1

    dest = _clean_dir(OUT_DIR / "merge-heads")
    new_files = [p for p in versions_dir.glob("*.py") if p not in before]
    for p in new_files:
        shutil.copy2(p, dest / p.name)
    print(f"merge-heads: {len(new_files)} file mới -> {dest}")
    return 0


def cmd_shell(_args: argparse.Namespace) -> int:
    """Việc `shell`: thay tiến trình bằng bash để gỡ lỗi."""
    os.execvp("bash", ["bash"])


def cmd_gc(_args: argparse.Namespace) -> int:
    """Xoá venv-*/mypy-* của worktree không còn tồn tại, và venv/cache mypy theo băm đã cũ.

    `mypy-tree-<băm>` không thuộc worktree nào (khoá bằng nội dung cây nguồn — `mypy_cache_dir`): giữ
    `MYPY_CACHE_KEEP` thư mục mới nhất theo mtime, xoá phần còn lại. `run.sh` truyền danh sách worktree
    còn sống cho nhóm còn lại: `mypy-<tên>` và `mypy-fast-<tên>` (cổng nhanh) của worktree sống được giữ;
    `venv-<tên>` kiểu cũ luôn bị xoá — venv giờ dùng chung theo băm `uv.lock` (PERF-03).
    """
    valid = {n for n in os.environ.get("VERIFY_VALID_NAMES", "").split(",") if n}
    all_dirs = sorted([*WORK_DIR.glob("venv-*"), *WORK_DIR.glob("mypy-*")])
    hashed = [d for d in all_dirs if d.name.startswith((MYPY_CACHE_PREFIX, VENV_PREFIX))]
    keep = {
        d
        for prefix in (MYPY_CACHE_PREFIX, VENV_PREFIX)
        for d in sorted(
            (h for h in hashed if h.name.startswith(prefix)), key=lambda d: d.stat().st_mtime, reverse=True
        )[:MYPY_CACHE_KEEP]
    }
    removed = []
    for d in all_dirs:
        fast = d.name.startswith(FAST_MYPY_PREFIX)
        owner = d.name.removeprefix(FAST_MYPY_PREFIX) if fast else d.name.split("-", 1)[1]
        if d in keep or (d not in hashed and not d.name.startswith("venv-") and owner in valid):
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    print(f"gc: xoá {len(removed)} thư mục: {removed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dựng parser lệnh con (verify/lock/openapi/merge-heads/shell/gc) và chạy việc được chọn."""
    parser = argparse.ArgumentParser(prog="tools.verify.steps")
    sub = parser.add_subparsers(dest="task", required=True)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--steps", default=None)
    p_verify.add_argument("--fast", action="store_true", help="cổng nhanh: chỉ đơn vị bị chạm, không độ phủ")
    p_verify.set_defaults(fn=cmd_verify)

    sub.add_parser("lock").set_defaults(fn=cmd_lock)
    sub.add_parser("openapi").set_defaults(fn=cmd_openapi)

    p_merge = sub.add_parser("merge-heads")
    p_merge.add_argument("name")
    p_merge.set_defaults(fn=cmd_merge_heads)

    sub.add_parser("shell").set_defaults(fn=cmd_shell)
    sub.add_parser("gc").set_defaults(fn=cmd_gc)

    args = parser.parse_args(argv)
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
