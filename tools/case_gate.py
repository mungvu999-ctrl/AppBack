"""Cổng case — cài đúng CASE.md §2.3, nguồn duy nhất (B0-01 [6]H).

Kiến trúc: các hàm tính toán thuần (`evaluate`, `required_cases_for`, …) nhận
dữ liệu qua tham số, không tự đọc file — để test dựng fixture tạm mà không
cần B0-05/B0-06 đã hợp nhất. `main()` mới nối vào dữ liệu thật (`operations()`,
`load_bind_rows`, `registered_tasks()`, junit, `cases.toml`, vết case).

Định dạng `CASE_TRACE_FILE` (B0-06 ghi): JSON Lines, mỗi dòng
`{"test": "<tên test>", "op": "<operationId>", "status": <int>, "code": "<mã lỗi|null>"}`.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from packages.core.case_names import split_case_test_name
from tools.charter import BindRow, load_bind_rows, pending_bind_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
# junit bước 5 (addopts) và bước 5b; file nào có thì đọc
JUNIT_PATHS = (Path("/tmp/junit.xml"), Path("/tmp/junit-perf.xml"))
# route hạ tầng/tệp nhị phân (= H1_EXEMPT): miễn cảnh báo "đã mount nhưng không có dòng";
# N44 có dòng BE-BIND nên vẫn đòi case
INFRA_OPS = frozenset(
    {"files_read_object", "health_live", "health_ready", "lighting_bake_download_file", "model_export_download_file"}
)

# ---------------------------------------------------------------------------
# Kiểu dữ liệu
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    """Gương của `apps.api.core.openapi.Operation` (B0-06). Trường thiếu = áp dụng."""

    op: str
    method: str
    path: str
    protected: bool | None = None
    versioned: bool | None = None
    idempotency: str | None = None
    has_body: bool | None = None
    has_query: bool | None = None
    returns_list: bool | None = None
    has_optional_response_fields: bool | None = None
    body_mirrors_path: bool | None = None
    permission_key: str | None = None


@dataclass(frozen=True)
class TestResult:
    """Một testcase đọc từ junit: tên, kết quả, lý do bỏ qua, cờ xfail, marker và file."""

    __test__ = False  # tên trùng "Test*" — báo pytest đây không phải lớp test

    name: str
    outcome: str  # passed | failed | error | skipped
    skip_reason: str = ""
    is_xfail: bool = False
    markers: frozenset[str] = frozenset()
    file: str = ""


@dataclass(frozen=True)
class CaseTraceEntry:
    """Một dòng vết case (`CASE_TRACE_FILE`): test nào gọi `op` nào, nhận status và mã lỗi gì."""

    test: str
    op: str
    status: int
    code: str | None = None


@dataclass
class EndpointOverride:
    """Phần `[[endpoint]]` của `cases.toml`: case đòi thêm (`extra`) và case được miễn kèm lý do (`waive`)."""

    extra: set[str] = field(default_factory=set)
    waive: dict[str, str] = field(default_factory=dict)


@dataclass
class TaskRequirement:
    """Phần `[[task]]` của `cases.toml`: tên hàm task và case đòi thêm ngoài J01, J06."""

    fn: str
    require: set[str] = field(default_factory=set)


@dataclass
class OpResult:
    """Kết quả một thao tác: case bắt buộc, case tìm thấy, case được miễn."""

    row_id: str
    op: str
    required: set[str]
    found: set[str]
    waived: dict[str, str]

    @property
    def missing(self) -> set[str]:
        """Case bắt buộc chưa có test đạt và không được miễn."""
        return self.required - self.found - set(self.waived)

    @property
    def ok(self) -> bool:
        """Đúng khi không còn gì thiếu hay hỏng."""
        return not self.missing


@dataclass
class GateResult:
    """Kết quả toàn cổng: bảng thao tác, task thiếu, phát hiện hỏng, cảnh báo thao tác chưa có dòng BE-BIND."""

    op_results: list[OpResult] = field(default_factory=list)
    task_missing: list[str] = field(default_factory=list)
    task_rows: list[str] = field(default_factory=list)  # mỗi task một dòng, đạt cũng in (NO-309)
    findings: list[str] = field(default_factory=list)  # skipped/xfail/gpu/miễn sai/task lạ…
    unmounted_warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Đúng khi không còn gì thiếu hay hỏng."""
        return not self.task_missing and not self.findings and all(r.ok for r in self.op_results)


# ---------------------------------------------------------------------------
# Tập case bắt buộc — CASE.md §2.1, §2.2, "Case thêm cố định"
# ---------------------------------------------------------------------------

_CASE_CHUNG = {"C04", "C05", "C12", "C13", "C25"}

_LOAI_CANDIDATES: dict[str, set[str]] = {
    "Đ": {"C01", "C06", "C08", "C15", "C17"},
    "Đ*": {"C01", "C15", "C17"},
    "G": {"C01", "C02", "C03", "C06", "C07", "C08", "C16", "C17", "C18", "C21"},
    "G*": {"C01", "C02", "C03", "C08", "C17", "C21", "C23"},
    "GV": {"C01", "C02", "C03", "C06", "C07", "C08", "C16", "C17", "C21", "C09", "C09b", "C14"},
    "C": {"C01", "C02", "C03", "C11", "C16", "C24", "C27", "C26"},
    "R": {"C01", "C11", "C19", "C20", "C24"},
    "P": {"C01", "C24"},
    "B": {"C01", "C12"},
    "T-init": {"C01", "C02", "C03", "C06", "C07", "C11", "C21", "U05", "U08", "U09", "U12"},
    "T-chunk": {"C01", "C02", "C06", "C08", "C21", "U10"},
    "T-complete": {"C01", "C06", "C08", "C14", "C21", "U07", "U10", "U11"},
    "T-avatar": {"C01", "C02", "C03", "U03", "U07", "U08"},
    "A": {"C01", "C02", "C03", "C07", "C17", "C18", "C21", "C09", "C09b", "C14", "C15", "C08"},
    "S": {"S01", "S02", "S03", "S04", "S05", "S07", "S09", "S06", "S08"},
}

# row_id → case thêm cố định (CASE §2.2 "Case thêm cố định")
_FIXED_EXTRA: dict[str, set[str]] = {
    "10": {"C14"},
    "17": {"C14"},
    "41": {"C14"},
    "N3": {"C14", "C11"},
    "N4": {"C14"},
    "N13": {"C11"},
    "N14": {"C11"},
    "44": {"C11"},
    "37": {"C11"},
    "N8": {"C28"},
}

_ONLY_WAIVABLE = "C16"


def _flag(value: bool | None) -> bool:
    """Trường thiếu (trước B0-06) coi là 'áp dụng'."""
    return True if value is None else value


def _gate(case_id: str, *, row: BindRow, op: Operation, loai: str) -> bool:
    """Case có chú thích (CASE §2.3 bước 2) có áp cho thao tác này không, tính từ metadata và cột BE-BIND."""
    if case_id == "C07":
        return row.lock != "—"
    if case_id in ("C15",):
        return _flag(op.returns_list)
    if case_id == "C17":
        return _flag(op.has_optional_response_fields)
    if case_id == "C18":
        return row.logged
    if case_id == "C21":
        return _flag(op.has_body) and _flag(op.body_mirrors_path)
    if case_id == "C02":
        return _flag(op.has_body) or _flag(op.has_query)
    if case_id == "C03":
        return _flag(op.has_body)
    if case_id in ("C08", "C23") and loai in ("G*", "A"):
        return "{" in row.path
    if case_id in ("C09", "C09b", "C14") and loai == "A":
        return _flag(op.versioned)
    if case_id == "C24":
        return loai in ("C", "R", "P")
    if case_id == "C27":
        return row.row_id in ("1", "N8")
    if case_id == "C26":
        return row.row_id in ("N9", "N10")
    if case_id == "S06":
        return row.lock == "thành viên"
    if case_id == "S08":
        return "progress" in (row.operation_id or "")
    return True


def required_cases_for(row: BindRow, op: Operation) -> set[str]:
    """Tập case bắt buộc của một thao tác theo dòng BE-BIND và metadata (CASE §2.1, §2.2, case thêm cố định)."""
    loai = row.case_type
    candidates = set(_LOAI_CANDIDATES.get(loai, set()))
    required = {c for c in candidates if _gate(c, row=row, op=op, loai=loai)}

    if _flag(op.protected):
        required |= _CASE_CHUNG
        idempotency_applies = row.method.upper() != "GET" and op.idempotency != "off" and not _flag(op.versioned)
        if idempotency_applies:
            required |= {"C10", "C22"}

    # CASE §2.1: route bảo vệ `+ngoài` cần C10 riêng (không lặp thư/thông báo/job/SSE)
    if row.outside and _flag(op.protected):
        required.add("C10")

    required |= _FIXED_EXTRA.get(row.row_id, set())
    return required


# ---------------------------------------------------------------------------
# junit + vết case
# ---------------------------------------------------------------------------

_EMPTY_PARAMS_RE = re.compile(r"empty parameter set", re.IGNORECASE)


def parse_junit(*paths: Path) -> list[TestResult]:
    """Đọc các file junit có thật thành `TestResult`; xfail ghi cờ riêng để cổng cấm, skipped giữ lý do."""
    out: list[TestResult] = []
    for path in paths:
        if not path.is_file():
            continue
        root = ET.parse(path).getroot()  # noqa: S314 — junit do chính pytest trong container sinh
        cases = root.iter("testcase") if root.tag != "testcase" else [root]
        for case in cases:
            name = case.get("name", "")
            classname = case.get("classname", "")
            outcome = "passed"
            skip_reason = ""
            is_xfail = False
            if case.find("failure") is not None:
                outcome = "failed"
            elif case.find("error") is not None:
                outcome = "error"
            skipped = case.find("skipped")
            if skipped is not None:
                skip_type = skipped.get("type", "")
                if skip_type == "pytest.xfail":
                    is_xfail = True
                    outcome = "passed"  # xfail được coi là "qua" bước chạy, nhưng vẫn bị case_gate cấm
                else:
                    outcome = "skipped"
                    skip_reason = skipped.get("message", "")
            out.append(
                TestResult(
                    name=name,
                    outcome=outcome,
                    skip_reason=skip_reason,
                    is_xfail=is_xfail,
                    file=classname.replace(".", "/"),
                )
            )
    return out


def parse_case_trace(path: Path | None) -> list[CaseTraceEntry]:
    """Đọc vết case JSON Lines; không có file thì trả rỗng."""
    if path is None or not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(CaseTraceEntry(test=d["test"], op=d["op"], status=int(d["status"]), code=d.get("code")))
    return out


_FIXED_STATUS: dict[str, int] = {
    "C02": 422,
    "C03": 422,
    "C04": 401,
    "C05": 401,
    "C06": 404,
    "C07": 403,
    "C08": 404,
    "C11": 429,
    "C12": 413,
    "C13": 503,
    "C21": 422,
    "C22": 503,
    "C23": 404,
    "C24": 403,
}
_FIXED_CODE: dict[str, str] = {
    "C03": "VALIDATION",
    "C21": "PATH_BODY_MISMATCH",
    "C22": "IDEMPOTENCY_IN_PROGRESS",
    "C24": "ORIGIN_MISMATCH",
}

# task (CASE.md §2.3 bản 7): `test_<tên hàm>__<case>`, case J (§4), U của task tiền xử lý (§2.2), M của task ML
# (§6); id tham số `[...]` được như op, hậu tố `_<việc>` thì không
_TEST_TASK_RE = re.compile(r"^test_(?P<fn>.+)__(?P<case>[JUM]\d{2})(?:\[.*\])?$")


def _case_matches_trace(case_id: str, op_id: str, test_name: str, trace: list[CaseTraceEntry]) -> bool:
    """Test có ít nhất một response của đúng `op` trong vết, và khớp status/mã cố định của case nếu có."""
    hits = [t for t in trace if t.op == op_id and t.test == test_name]
    if not hits:
        return False
    expected_status = _FIXED_STATUS.get(case_id)
    expected_code = _FIXED_CODE.get(case_id)
    if expected_status is None and expected_code is None:
        return True
    return any(
        (expected_status is None or h.status == expected_status) and (expected_code is None or h.code == expected_code)
        for h in hits
    )


def _found_cases_by_op(
    tests: list[TestResult], trace: list[CaseTraceEntry]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(case từ test riêng `test_<op>__<case>`, case chung từ `test_common__<case>[<op>]`)."""
    found: dict[str, set[str]] = {}
    common: dict[str, set[str]] = {}
    for t in tests:
        if t.outcome != "passed" or t.is_xfail:
            continue
        parts = split_case_test_name(t.name)
        if parts is None or (parts.common and parts.case not in _CASE_CHUNG | {"C10", "C22"}):
            continue
        if _case_matches_trace(parts.case, parts.op, t.name, trace):
            (common if parts.common else found).setdefault(parts.op, set()).add(parts.case)
    return found, common


def _task_found(tests: list[TestResult]) -> dict[str, set[str]]:
    """Case task (J/U/M) theo tên hàm từ các test đạt tên `test_<hàm>__<case>[<id tham số>]` (CASE.md §2.3 bản 7)."""
    found: dict[str, set[str]] = {}
    for t in tests:
        if t.outcome != "passed" or t.is_xfail:
            continue
        m = _TEST_TASK_RE.match(t.name)
        if m:
            found.setdefault(m.group("fn"), set()).add(m.group("case"))
    return found


# ---------------------------------------------------------------------------
# cases.toml
# ---------------------------------------------------------------------------


def load_cases_toml(paths: list[Path]) -> tuple[dict[str, EndpointOverride], list[TaskRequirement]]:
    """Gộp mọi `cases.toml`: override theo thao tác và danh sách yêu cầu task."""
    overrides: dict[str, EndpointOverride] = {}
    tasks: list[TaskRequirement] = []
    for path in paths:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("endpoint", []):
            ov = overrides.setdefault(entry["op"], EndpointOverride())
            ov.extra |= set(entry.get("extra", []))
            ov.waive.update(entry.get("waive", {}))
        for entry in data.get("task", []):
            tasks.append(TaskRequirement(fn=entry["fn"], require=set(entry.get("require", []))))
    return overrides, tasks


# ---------------------------------------------------------------------------
# Kiểm cấu trúc: skipped/xfail, marker gpu ngoài apps/ml/**
# ---------------------------------------------------------------------------


def check_skipped_xfail(tests: list[TestResult]) -> list[str]:
    """Phát hiện test xfail (luôn cấm) và test skipped trừ trường hợp tham số rỗng."""
    findings = []
    for t in tests:
        if t.is_xfail:
            findings.append(f"xfail bị cấm: {t.name}")
        elif t.outcome == "skipped" and not _EMPTY_PARAMS_RE.search(t.skip_reason):
            findings.append(f"test bị bỏ qua (skipped): {t.name} ({t.skip_reason or 'không lý do'})")
    return findings


_GPU_MARK_RE = re.compile(r"^(pytest\.)?mark\.gpu\b")


def _mark_texts(tree: ast.Module) -> list[str]:
    """Mọi biểu thức marker: decorator của hàm/lớp và phần tử `pytestmark = ...`."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.extend(ast.unparse(d) for d in node.decorator_list)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            values = node.value.elts if isinstance(node.value, ast.List | ast.Tuple) else [node.value]
            out.extend(ast.unparse(v) for v in values)
    return out


def check_gpu_markers(root: Path) -> list[str]:
    """Quét tĩnh mọi test_*.py: marker `gpu` chỉ hợp lệ dưới apps/ml/**."""
    findings = []
    for base in ("packages", "apps", "tools"):
        d = root / base
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("test_*.py")):
            rel = path.relative_to(root)
            if rel.parts[:2] == ("apps", "ml"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
            if any(_GPU_MARK_RE.match(m) for m in _mark_texts(tree)):
                findings.append(f"marker gpu ngoài apps/ml/**: {rel.as_posix()}")
    return findings


# ---------------------------------------------------------------------------
# Đánh giá
# ---------------------------------------------------------------------------


def evaluate(
    operations: list[Operation],
    bind_rows: list[BindRow],
    overrides: dict[str, EndpointOverride],
    tests: list[TestResult],
    trace: list[CaseTraceEntry],
    task_names: list[str],
    task_requirements: list[TaskRequirement],
) -> GateResult:
    """Áp CASE §2.3 lên dữ liệu đã nạp: bảng thao tác, miễn hợp lệ, task lạ/trùng/thiếu, mỗi task một dòng."""
    result = GateResult()

    rows_by_op = {r.operation_id: r for r in bind_rows if r.operation_id is not None}
    specific_by_op, common_by_op = _found_cases_by_op(tests, trace)

    for op in operations:
        row = rows_by_op.get(op.op)
        if row is None:
            if op.op not in INFRA_OPS:
                result.unmounted_warnings.append(op.op)
            continue

        found = specific_by_op.get(op.op, set()) | common_by_op.get(op.op, set())
        if row.outside and _flag(op.protected):
            # test chung không thoả C10 của route `+ngoài`
            found = specific_by_op.get(op.op, set()) | (common_by_op.get(op.op, set()) - {"C10"})
            if op.idempotency == "off":
                result.findings.append(f'{op.op}: route bảo vệ +ngoài mà idempotency="off" (CASE §2.1)')

        required = required_cases_for(row, op)
        ov = overrides.get(op.op, EndpointOverride())
        required |= ov.extra

        waived: dict[str, str] = {}
        for case_id, reason in ov.waive.items():
            if case_id != _ONLY_WAIVABLE:
                result.findings.append(f"miễn case khác {_ONLY_WAIVABLE} bị cấm: {op.op} miễn {case_id}")
                continue
            if len(reason.strip()) < 10:
                result.findings.append(f"lý do miễn {case_id} quá ngắn: {op.op}")
                continue
            waived[case_id] = reason

        result.op_results.append(OpResult(row_id=row.row_id, op=op.op, required=required, found=found, waived=waived))

    task_found = _task_found(tests)
    names = set(task_names)
    for req in task_requirements:
        if req.fn not in names:
            result.findings.append(f"cases.toml khai task lạ, không có trong sổ: {req.fn}")

    seen: set[str] = set()
    for n in task_names:
        if n in seen:
            result.findings.append(f"hai task/periodic trùng tên hàm: {n}")
        seen.add(n)

    extra_require = {r.fn: r.require for r in task_requirements}
    for fn in sorted(names):
        need = {"J01", "J06"} | extra_require.get(fn, set())
        have = task_found.get(fn, set())
        missing = need - have
        status = "thiếu " + ",".join(sorted(missing)) if missing else "đạt"
        result.task_rows.append(f"{fn} | bắt buộc {sorted(need)} | tìm thấy {sorted(have & need)} | {status}")
        if missing:
            result.task_missing.append(f"{fn}: thiếu {sorted(missing)}")

    return result


# ---------------------------------------------------------------------------
# main() — nối dữ liệu thật
# ---------------------------------------------------------------------------


def _optional_attr(module: str, attr: str) -> Any:
    """Hàm của prompt chưa hợp nhất (B0-05, B0-06) → None thay vì lỗi nhập."""
    try:
        return getattr(importlib.import_module(module), attr, None)
    except ImportError:
        return None


def _real_operations() -> list[Operation]:
    """Sổ thao tác thật của B0-06, chép sang gương `Operation`; chủ chưa hợp nhất → rỗng."""
    operations = _optional_attr("apps.api.core.openapi", "operations")
    if operations is None:
        return []
    names = [f.name for f in fields(Operation)]
    # chỉ chép trường gương này biết; trường B0-06 thêm sau không làm vỡ cổng
    out: list[Operation] = []
    for o in operations():
        kwargs: dict[str, Any] = {n: getattr(o, n, None) for n in names}
        out.append(Operation(**kwargs))
    return out


def _real_task_names() -> list[str]:
    """Sổ task thật `packages.messaging.registered_tasks()`; chưa có → rỗng."""
    registered_tasks = _optional_attr("packages.messaging", "registered_tasks")
    return [] if registered_tasks is None else list(registered_tasks())


def main() -> int:
    """Nối dữ liệu thật, in bảng thao tác và task, trả 0 khi đạt, 1 khi hỏng."""
    bind_rows = [*load_bind_rows(REPO_ROOT / "docs" / "charter" / "BE-BIND.md"), *pending_bind_rows(REPO_ROOT)]
    cases_toml_paths = sorted((REPO_ROOT / "apps").glob("*/*/cases.toml"))
    overrides, task_requirements = load_cases_toml(cases_toml_paths)

    tests = parse_junit(*JUNIT_PATHS)

    trace_file = os.environ.get("CASE_TRACE_FILE")
    trace = parse_case_trace(Path(trace_file) if trace_file else None)

    result = evaluate(_real_operations(), bind_rows, overrides, tests, trace, _real_task_names(), task_requirements)
    result.findings.extend(check_skipped_xfail(tests))
    result.findings.extend(check_gpu_markers(REPO_ROOT))

    print(f"case_gate: {len(result.op_results)} thao tác đã mount, {len(result.unmounted_warnings)} cảnh báo")
    for r in result.op_results:
        status = "đạt" if r.ok else "thiếu " + ",".join(sorted(r.missing))
        print(f"  {r.op} | bắt buộc {sorted(r.required)} | tìm thấy {sorted(r.found)} | {status}")
    print(f"case_gate: {len(result.task_rows)} task/lịch trong sổ")
    for row in result.task_rows:
        print(f"  {row}")
    for w in result.unmounted_warnings:
        print(f"  CẢNH BÁO: {w} đã mount nhưng không có dòng BE-BIND")
    for f in result.findings:
        print(f"  HỎNG: {f}")
    for t in result.task_missing:
        print(f"  HỎNG task: {t}")

    ok = result.ok
    print("case_gate:", "đạt" if ok else "hỏng")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
