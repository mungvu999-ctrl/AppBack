"""DEBT-02 C07 (NO-272/246/278) — test khẳng định trần đồng hồ tường phải gắn `perf` (BE-00 §12).

Quét tĩnh (AST) các tệp test đã rà trong cụm C07: một hàm `test_*` có `assert` cận trên về thời gian
(`elapsed < …`, `perf_counter() - started <= …`, `monotonic() - t0 < 1`) mà không mang `@pytest.mark.perf`
là hỏng — dưới `pytest-xdist` (`-n 6`) trần đo tuần tự không giữ được. Hàm phụ (`_run…`) có trần thì hỏng
trừ khi có test gọi nó và mọi test gọi nó đều mang `perf` (gọi bắc cầu qua hàm phụ khác không dò).
Hai luật đi kèm: test mang mã case (`test_<op>__<case>`, cổng case) **không bao giờ** gắn `perf`
(`perf_case_named` của `tools/verify/steps.py` làm bước 5b hỏng), và không đặt `pytestmark = perf` cấp tệp
(kéo cả test không có trần rời bước 5).

Giới hạn: đây là quét **cú pháp** (biến/thuộc tính/khoá tên `elapsed|duration|took`, lời gọi đồng hồ). Hạn
chờ rộng (`wait_until(timeout_s=…)`, `wait_closed(WAIT_S)`) không phải cận trên đo được nên không bị quét —
việc phân biệt "chờ rộng" với "trần sát" là quyết định của người rà (`quyet-dinh.md` của C07), không phải của máy.
Phạm vi là danh sách `SCANNED` (các tệp đã rà), không phải toàn repo: các tệp khác còn nợ ghi ở `DEBT.md`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tools.verify.steps import CASE_IN_NAME_RE

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED = (
    "apps/api/admin_ml_registry/tests/test_upload.py",
    "apps/api/auth/tests/test_verifier.py",
    "apps/api/auth_recovery/tests/test_jobs.py",
    "apps/api/core/tests/test_routing.py",
    "apps/api/drawings/tests/test_upload_flow.py",
    "apps/api/measurements/tests/test_routes_read_delete.py",
    "apps/api/streams/tests/test_fixture_streams.py",
    "apps/api/streams/tests/test_streams_open_notifications.py",
    "apps/api/streams/tests/test_streams_open_progress.py",
    "apps/api/telemetry/tests/test_feature_flags_route.py",
    "apps/ml/runtime/tests/test_device_gpu.py",
    "apps/ml/text/tests/test_reader_real.py",
    "apps/ml/training_runner/tests/test_runtime.py",
    "apps/worker/datasets/tests/test_perf.py",
    "apps/worker/pipeline_quality/tests/test_runtime.py",
    "apps/worker/pipeline_steps/tests/test_sweep_rules.py",
    "deploy/scripts/tests/test_deploy.py",
    "deploy/scripts/tests/test_smoke.py",
    "packages/db/tests/test_hooks.py",
    "packages/domain/library/tests/test_catalogue.py",
    "packages/mail/tests/test_fixtures.py",
    "packages/mail/tests/test_sender.py",
)
_CLOCKS = frozenset({"perf_counter", "monotonic", "time"})
_ELAPSED_NAME = re.compile(r"elapsed|duration|took", re.IGNORECASE)


def _is_time(node: ast.expr) -> bool:
    """Biểu thức là một khoảng thời gian: biến `elapsed…`, lời gọi đồng hồ, hay `đồng_hồ - mốc`."""
    if isinstance(node, ast.Name):
        return bool(_ELAPSED_NAME.search(node.id))
    if isinstance(node, ast.Attribute):
        return bool(_ELAPSED_NAME.search(node.attr))
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and bool(_ELAPSED_NAME.search(str(node.slice.value)))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in _CLOCKS
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in _CLOCKS
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub) and _is_time(node.left)


def _is_ceiling(assertion: ast.Assert) -> bool:
    """`assert` đặt cận **trên** cho một khoảng thời gian (cận dưới `>= 1.0` không tính)."""
    test = assertion.test
    if not isinstance(test, ast.Compare):
        return False
    operands = [test.left, *test.comparators]
    for index, op in enumerate(test.ops):
        if isinstance(op, ast.Lt | ast.LtE) and _is_time(operands[index]):
            return True
        if isinstance(op, ast.Gt | ast.GtE) and _is_time(operands[index + 1]):
            return True
    return False


def _has_perf(node: ast.AST) -> bool:
    """Cây `node` (decorator hay giá trị `pytestmark`) nhắc tới marker `perf`."""
    return any(isinstance(item, ast.Attribute) and item.attr == "perf" for item in ast.walk(node))


def _calls(caller: ast.AST, name: str) -> bool:
    """Thân `caller` nhắc tới hàm `name` (lời gọi trần `name(...)` hay `self.name(...)`)."""
    return any(
        (isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name)
        for n in ast.walk(caller)
    )


def violations(source: str) -> list[str]:
    """Lỗi của một tệp test: trần chưa `perf`, `perf` mang mã case, `pytestmark = perf` cấp tệp.

    Quét mọi hàm: test có trần phải `perf`; hàm phụ có trần hỏng khi không test nào gọi nó hoặc có test gọi
    nó mà thiếu `perf` (trần trong hàm phụ chạy ở bước 5 qua test đó).
    """
    tree = ast.parse(source)
    found = [
        f"dòng {stmt.lineno}: pytestmark perf cấp tệp"
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets)
        and _has_perf(stmt.value)
    ]
    class_marked = {
        id(inner)
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef) and any(_has_perf(d) for d in cls.decorator_list)
        for inner in ast.walk(cls)
    }
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    marked = {id(f) for f in functions if id(f) in class_marked or any(_has_perf(d) for d in f.decorator_list)}
    tests = [f for f in functions if f.name.startswith("test_")]
    for node in functions:
        is_test = node.name.startswith("test_")
        if is_test and id(node) in marked and CASE_IN_NAME_RE.search(node.name):
            found.append(f"{node.name}: perf không được mang mã case")
        ceiling = [a.lineno for a in ast.walk(node) if isinstance(a, ast.Assert) and _is_ceiling(a)]
        if not ceiling or id(node) in marked:
            continue
        if is_test:
            found.append(f"{node.name}: trần thời gian ở dòng {ceiling} nhưng thiếu @pytest.mark.perf")
            continue
        callers = [t for t in tests if _calls(t, node.name)]
        if not callers or any(id(t) not in marked for t in callers):
            found.append(f"{node.name}: hàm phụ có trần thời gian ở dòng {ceiling}, test gọi nó thiếu perf")
    return found


@pytest.mark.parametrize("relative", SCANNED)
def test_scanned_files_mark_wall_clock_ceilings_perf(relative: str) -> None:
    """Mỗi tệp đã rà: không còn trần đồng hồ tường ở bước 5, không `perf` mang mã case."""
    assert violations((REPO_ROOT / relative).read_text(encoding="utf-8")) == []


def test_scanner_flags_each_violation_kind() -> None:
    """Lưới quét tự kiểm: bắt đủ ba loại lỗi (cả đồng hồ gọi tên trần, hàm phụ có trần).

    Bỏ qua cận dưới, hạn chờ, test đã `perf` và hàm phụ chỉ test `perf` gọi.
    """
    source = (
        "import pytest\n"
        "pytestmark = pytest.mark.perf\n"
        "def test_a():\n    assert elapsed < 2\n"
        "def test_b():\n    assert 1.5 < time.perf_counter() - started < 3\n"
        "def test_c():\n    assert time.monotonic() - started >= 1.0\n    assert x < 3\n"
        "@pytest.mark.perf\ndef test_d__J09():\n    assert elapsed < 2\n"
        "@pytest.mark.perf\ndef test_e():\n    assert elapsed <= 2\n"
        "def test_f():\n    assert outcome['elapsed'] <= 10\n"
        "@pytest.mark.perf\nclass TestG:\n    def test_g(self):\n        assert self.duration < 1\n"
        "def test_h():\n    assert monotonic() - t0 < 1\n"
        "def _x():\n    assert elapsed <= 2\n"
        "def test_i():\n    _x()\n"
        "def _y():\n    assert elapsed <= 2\n"
        "@pytest.mark.perf\ndef test_j():\n    _y()\n"
    )
    found = violations(source)
    assert len(found) == 7
    assert found[0].startswith("dòng 2")
    assert any("test_a" in item and "[4]" in item for item in found)
    assert any("test_b" in item for item in found)
    assert any("test_d__J09" in item and "mã case" in item for item in found)
    assert any("test_f" in item for item in found)
    assert any(item.startswith("test_h:") for item in found)
    assert any(item.startswith("_x:") for item in found)
    clean = {"test_c", "test_e", "test_g", "test_i", "test_j", "_y"}
    assert not any(item.split(":")[0] in clean for item in found)
