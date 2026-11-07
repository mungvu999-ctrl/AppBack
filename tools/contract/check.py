"""Bước 7 của verify: hợp đồng FE — H1, H1 ngữ cảnh, H3, H4, H5 (B0-07 [6], BE-00 §12).

    python -m tools.contract.check [--build-dir DIR]

Đọc `tools/contract/APPFRONT_SHA`; biến môi trường `APPFRONT_DIR`, `CONTRACT_NODE_DIR`,
`CONTRACT_SAMPLES_DIR` (mẫu golden), `CONTRACT_REQUIRE_ALL=1` (người điều phối đặt từ M3);
BE-BIND và `changes/` qua `tools.charter`; thao tác đã mount qua `operations()` của B0-06.

In tám kiểm theo thứ tự, mỗi kiểm `đạt` / `hỏng` / `không áp dụng` kèm lý do, rồi chi tiết
của kiểm hỏng; thoát 1 khi có kiểm hỏng, runner hỏng hay mẫu hỏng. "Không áp dụng" chỉ khi
prompt chủ của kiểm chưa hợp nhất (BE-00 §12: H3 ← B1-02, H4 ← B3-05, H5 ← B4-01).
"""

import argparse
import importlib
import os
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from apps.api.core.openapi import operations
from tools.charter import BindRow, load_bind_rows, merged_prompts
from tools.contract import h3, h4
from tools.contract.runner_client import (
    AppFrontMissingError,
    RunnerError,
    appfront_dir_from_env,
    build_layout,
    node_dir_from_env,
    run_command,
)
from tools.contract.samples import EventSample, HttpSample, SampleError, bad_datetimes, load_samples
from tools.verify.steps import STATUS_FAIL, STATUS_NA, STATUS_OK

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
BIND_FILE: Final = REPO_ROOT / "docs" / "charter" / "BE-BIND.md"
SHA_FILE: Final = Path(__file__).resolve().parent / "APPFRONT_SHA"
SAMPLES_ENV: Final = "CONTRACT_SAMPLES_DIR"
REQUIRE_ALL_ENV: Final = "CONTRACT_REQUIRE_ALL"

H1_EXEMPT: Final = frozenset(
    {"health_live", "health_ready", "files_read_object", "lighting_bake_download_file", "model_export_download_file"}
)
"""Miễn H1 **đóng** (B0-07 [2], K06): thêm tên vào đây là trái hiến chương."""
PROGRESS_OP: Final = "drawings_read_progress"
PROGRESS_BRANCHES: Final = ("pending", "running", "completed", "failed")
STREAM_TYPE: Final = "S"
PERMISSIONS_MODULE: Final = "packages.domain.permissions"
RULES_MODULE: Final = "apps.api.rules.catalog"
H3_OWNER: Final = "B1-02"
H4_OWNER: Final = "B3-05"
H5_OWNER: Final = "B4-01"


@dataclass(frozen=True, slots=True)
class Mounted:
    """Một thao tác đã mount — đủ để so với dòng BE-BIND (K06)."""

    op: str
    method: str
    path: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Kết quả một kiểm: trạng thái, một dòng tóm tắt, và từng chỗ lệch khi hỏng."""

    name: str
    status: str
    summary: str
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Inputs:
    """Dữ liệu của một lượt kiểm; test dựng tay để kiểm từng luật mà không cần repo thật."""

    rows: Mapping[str, BindRow]
    mounted: Sequence[Mounted]
    merged: frozenset[str]
    samples: Sequence[HttpSample] = ()
    events: Sequence[EventSample] = ()
    require_all: bool = False


def contract_rows(bind_file: Path = BIND_FILE) -> dict[str, BindRow]:
    """Dòng BE-BIND §1, §2 theo `operationId`; dòng v2 không có `operationId` nên tự rơi ra."""
    return {row.operation_id: row for row in load_bind_rows(bind_file) if row.operation_id is not None}


def mounted_operations() -> list[Mounted]:
    """Thao tác của app thật (`apps.api.core.openapi.operations()`, B0-06)."""
    return [Mounted(item.op, item.method, item.path) for item in operations()]


def gather_inputs() -> Inputs:
    """Dữ liệu thật của repo và của lượt verify này."""
    samples_dir = os.environ.get(SAMPLES_ENV)
    http, events = load_samples(Path(samples_dir) if samples_dir else None)
    return Inputs(
        rows=contract_rows(),
        mounted=mounted_operations(),
        merged=frozenset(merged_prompts(REPO_ROOT)),
        samples=http,
        events=events,
        require_all=os.environ.get(REQUIRE_ALL_ENV) == "1",
    )


def verdict(name: str, problems: Sequence[str], summary: str) -> CheckResult:
    """`đạt` khi không có chỗ lệch nào, còn lại `hỏng` kèm từng chỗ lệch."""
    return CheckResult(name, STATUS_FAIL if problems else STATUS_OK, summary, tuple(problems))


def not_yet(name: str, owner: str, merged: frozenset[str], missing: str) -> CheckResult:
    """Phần của `owner` chưa có: `không áp dụng` khi chủ chưa hợp nhất, `hỏng` khi đã (BE-00 §12)."""
    if owner in merged:
        return CheckResult(name, STATUS_FAIL, f"{owner} đã hợp nhất mà {missing}", (f"{owner}: {missing}",))
    return CheckResult(name, STATUS_NA, f"{owner} chưa hợp nhất — {missing}")


def schema_modules(build_dir: Path) -> list[str]:
    """`@/api/schemas/<tên>` của mọi file `.ts` ở gốc thư mục schema, trừ file test."""
    folder = build_dir / "src" / "api" / "schemas"
    return [
        f"@/api/schemas/{path.stem}"
        for path in sorted(folder.glob("*.ts"))
        if not path.name.endswith((".test.ts", ".spec.ts"))
    ]


def check_map(shapes: Mapping[str, str], rows: Mapping[str, BindRow]) -> CheckResult:
    """Bản đồ đủ: mỗi dòng BE-BIND không v2 có mục, không mục nào ngoài BE-BIND và miễn."""
    problems = [f"{op}: dòng BE-BIND không có mục trong schema-map.ts" for op in sorted(rows.keys() - shapes.keys())]
    problems += [f"{op}: mục thừa, không có dòng BE-BIND" for op in sorted(shapes.keys() - rows.keys() - H1_EXEMPT)]
    return verdict("Bản đồ đủ", problems, f"{len(shapes)} mục / {len(rows)} dòng BE-BIND không v2")


def _unknown_mounts(inputs: Inputs) -> list[str]:
    """Thao tác đã mount mà không thuộc BE-BIND (không v2) hay miễn, hoặc lệch đường (K06)."""
    problems = []
    for mounted in inputs.mounted:
        row = inputs.rows.get(mounted.op)
        if row is None and mounted.op not in H1_EXEMPT:
            problems.append(f"{mounted.op}: đã mount mà không có dòng BE-BIND (không v2) hay miễn (K06)")
        elif row is not None and (mounted.method, mounted.path) != (row.method, row.path):
            where = f"{mounted.method} {mounted.path}"
            problems.append(f"{mounted.op}: mount ở {where}, BE-BIND ghi {row.method} {row.path} (K06)")
    return problems


def _unmounted(inputs: Inputs) -> list[str]:
    """Dòng BE-BIND phải mount mà chưa: chủ đã hợp nhất, hay `CONTRACT_REQUIRE_ALL=1`."""
    mounted = {item.op for item in inputs.mounted}
    problems = []
    for op, row in inputs.rows.items():
        if op in mounted:
            continue
        if inputs.require_all:
            problems.append(f"{op}: chưa mount ({REQUIRE_ALL_ENV}=1)")
        elif row.owner in inputs.merged:
            problems.append(f"{op}: chủ {row.owner} đã hợp nhất (changes/{row.owner}.md) mà chưa mount")
    return problems


def check_mounted(inputs: Inputs) -> CheckResult:
    """Thao tác đã mount khớp BE-BIND, và mọi thao tác phải có đã mount."""
    problems = _unknown_mounts(inputs) + _unmounted(inputs)
    return verdict("Thao tác đã mount", problems, f"{len(inputs.mounted)} đã mount / {len(inputs.rows)} dòng BE-BIND")


def _missing_success(samples: Sequence[HttpSample], shapes: Mapping[str, str], inputs: Inputs) -> list[str]:
    """Thao tác đã mount, có thân, không phải luồng (H5 kiểm thay) mà không có mẫu 2xx nào."""
    covered = {sample.operation_id for sample in samples if 200 <= sample.status < 300}
    problems = []
    for mounted in inputs.mounted:
        row = inputs.rows.get(mounted.op)
        # Không dòng BE-BIND (miễn) hay không có mục bản đồ: kiểm "bản đồ đủ" và "đã mount" đã báo.
        if row is None or row.case_type == STREAM_TYPE or shapes.get(mounted.op, "empty") == "empty":
            continue
        if mounted.op not in covered:
            problems.append(f"{mounted.op}: đã mount mà không có mẫu 2xx nào (test C01 ghi golden)")
    return problems


def _progress_branches(samples: Sequence[HttpSample], inputs: Inputs) -> list[str]:
    """`drawings_read_progress` đã mount phải có mẫu 2xx cho đủ 4 nhánh `status` (BE-BIND §4)."""
    if PROGRESS_OP not in {item.op for item in inputs.mounted}:
        return []
    seen = {
        sample.body.get("status")
        for sample in samples
        if sample.operation_id == PROGRESS_OP and 200 <= sample.status < 300 and isinstance(sample.body, dict)
    }
    missing = [branch for branch in PROGRESS_BRANCHES if branch not in seen]
    return [f"{PROGRESS_OP}: thiếu mẫu 2xx nhánh {', '.join(missing)} (BE-BIND §4)"] if missing else []


def check_h1(
    samples: Sequence[HttpSample], verdicts: Sequence[Mapping[str, Any]], shapes: Mapping[str, str], inputs: Inputs
) -> CheckResult:
    """H1: mẫu giải đạt, ngày giờ đúng W3, thao tác đã mount có mẫu 2xx, đủ nhánh `Progress`."""
    problems = [f"{sample.file}: {bad}" for sample in samples for bad in bad_datetimes(sample.body)]
    problems += [f"{item['file']}: {item['reason']}" for item in verdicts if item["decode"] == "fail"]
    problems += _missing_success(samples, shapes, inputs)
    problems += _progress_branches(samples, inputs)
    return verdict("H1", problems, f"{len(samples)} mẫu response")


def check_context(verdicts: Sequence[Mapping[str, Any]]) -> CheckResult:
    """H1 ngữ cảnh (HOP-DONG-MOI §0.2 B): runner chạy luật trên mẫu đã giải đạt."""
    applied = [item for item in verdicts if item["context"] != "none"]
    problems = [f"{item['file']}: {item['contextReason']}" for item in applied if item["context"] == "fail"]
    return verdict("H1 ngữ cảnh", problems, f"{len(applied)} mẫu có luật ngữ cảnh")


def mirror_module(name: str) -> ModuleType | None:
    """Module gương của BE, hay `None` khi chính nó hoặc gói cha chưa có; lỗi nhập khác nổi lên."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name is not None and (name == exc.name or name.startswith(f"{exc.name}.")):
            return None
        raise


def check_h3(fe: Mapping[str, Any], module_name: str, merged: frozenset[str]) -> CheckResult:
    """H3: ma trận quyền FE ↔ `packages.domain.permissions` (B1-02)."""
    mirror = mirror_module(module_name)
    if mirror is None:
        return not_yet("H3", H3_OWNER, merged, f"chưa có {module_name}")
    problems = h3.compare_permissions(fe, mirror)
    return verdict("H3", problems, f"{len(fe['keys'])} khoá, {len(fe['roles'])} vai")


def check_h4(fe: Mapping[str, Any], module_name: str, merged: frozenset[str]) -> CheckResult:
    """H4: đúng 25 mã FE (luôn kiểm), rồi mã và khoá ngưỡng ↔ `apps.api.rules.catalog` (B3-05)."""
    problems = h4.fe_count_problems(fe)
    catalog = mirror_module(module_name)
    if catalog is not None:
        problems += h4.compare_rules(fe, catalog)
    elif not problems:
        return not_yet("H4", H4_OWNER, merged, f"FE {len(fe['codes'])} mã; chưa có {module_name}")
    return verdict("H4", problems, f"{len(fe['codes'])} mã, {len(fe['thresholds'])} khoá ngưỡng")


def check_h5(inputs: Inputs, verdicts: Sequence[Mapping[str, Any]]) -> CheckResult:
    """H5: khung SSE đã nhận giải bằng schema của S1/S2, ngày giờ đúng W3; luồng đã mount phải có mẫu."""
    streams = {op for op, row in inputs.rows.items() if row.case_type == STREAM_TYPE}
    mounted = sorted(streams & {item.op for item in inputs.mounted})
    if not mounted and not inputs.events:
        return not_yet("H5", H5_OWNER, inputs.merged, "S1/S2 chưa mount")
    recorded = {event.operation_id for event in inputs.events}
    problems = [
        f"{e.file}: {e.operation_id} không phải luồng Loại S" for e in inputs.events if e.operation_id not in streams
    ]
    problems += [f"{e.file}: {bad}" for e in inputs.events for bad in bad_datetimes(e.body, "event")]
    problems += [f"{item['file']}: {item['reason']}" for item in verdicts if item["decode"] == "fail"]
    problems += [f"{item['file']}: {item['contextReason']}" for item in verdicts if item["context"] == "fail"]
    problems += [f"{op}: đã mount mà không có mẫu luồng (record_stream_event)" for op in mounted if op not in recorded]
    return verdict("H5", problems, f"{len(inputs.events)} khung SSE")


def _sample_payload(sample: HttpSample) -> dict[str, Any]:
    """Một mẫu response ở dạng runner đọc."""
    return {
        "file": sample.file,
        "operationId": sample.operation_id,
        "status": sample.status,
        "body": sample.body,
        "pathParams": sample.path_params,
        "context": sample.context,
    }


def decode(
    build_dir: Path, samples: Sequence[HttpSample], events: Sequence[EventSample]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Giải mọi mẫu trong **một** lượt runner; trả (kết quả response, kết quả khung SSE) theo thứ tự vào."""
    payload = [_sample_payload(sample) for sample in samples]
    payload += [{"file": e.file, "operationId": e.operation_id, "event": True, "body": e.body} for e in events]
    verdicts: list[dict[str, Any]] = run_command(build_dir, "decode", {"samples": payload})["verdicts"]
    return verdicts[: len(samples)], verdicts[len(samples) :]


def run_checks(build_dir: Path, inputs: Inputs) -> list[CheckResult]:
    """Tám kiểm theo thứ tự của B0-07 [6]; mẫu của thao tác miễn không vào H1."""
    modules = schema_modules(build_dir)
    smoke = run_command(build_dir, "smoke", {"schemaModules": modules})
    shapes: dict[str, str] = smoke["shapes"]
    samples = [sample for sample in inputs.samples if sample.operation_id not in H1_EXEMPT]
    http_verdicts, event_verdicts = decode(build_dir, samples, inputs.events)
    return [
        verdict("Smoke", smoke["problems"], f"{len(modules)} module schema, {len(shapes)} mục bản đồ"),
        check_map(shapes, inputs.rows),
        check_mounted(inputs),
        check_h1(samples, http_verdicts, shapes, inputs),
        check_context(http_verdicts),
        check_h3(run_command(build_dir, "permissions", {}), PERMISSIONS_MODULE, inputs.merged),
        check_h4(run_command(build_dir, "rules", {}), RULES_MODULE, inputs.merged),
        check_h5(inputs, event_verdicts),
    ]


def render(results: Sequence[CheckResult]) -> list[str]:
    """Bảng một dòng mỗi kiểm, rồi chi tiết của các kiểm hỏng."""
    lines = [f"{'Kiểm':<18} | {'Trạng thái':<13} | Chi tiết", "-" * 80]
    lines += [f"{item.name:<18} | {item.status:<13} | {item.summary}" for item in results]
    for item in results:
        lines += [f"[{item.name}] {problem}" for problem in item.problems]
    return lines


def _emit(line: str) -> None:
    """Một dòng ra stdout — đầu ra của cổng, không phải log."""
    sys.stdout.write(line + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Dựng runner, chạy tám kiểm, in bảng; 1 khi có gì hỏng.

    Không `--build-dir` → thư mục tạm `contract-*`, xoá khi xong dù đạt hay hỏng (≈ 16 MB bản chép
    `src`, NO-052); symlink `node_modules` bị gỡ, kho dùng chung trên volume không bị chạm.
    Có `--build-dir` → giữ lại để gỡ lỗi.
    """
    parser = argparse.ArgumentParser(prog="python -m tools.contract.check")
    parser.add_argument("--build-dir", type=Path, help="thư mục chưa có hay rỗng, giữ lại; mặc định thư mục tạm, xoá")
    args = parser.parse_args(argv)
    _emit(f"Bước 7 — AppFront @ {SHA_FILE.read_text(encoding='utf-8').strip()}")
    started = time.monotonic()
    with ExitStack() as scratch:
        build_dir = args.build_dir or Path(scratch.enter_context(tempfile.TemporaryDirectory(prefix="contract-")))
        try:
            build_layout(appfront_dir_from_env(), node_dir_from_env(), build_dir)
            built = time.monotonic()
            results = run_checks(build_dir, gather_inputs())
        except (AppFrontMissingError, RunnerError, SampleError) as exc:
            _emit(f"bước 7 hỏng: {exc}")
            return 1
    for line in render(results):
        _emit(line)
    _emit(f"thời gian: {time.monotonic() - started:.1f} s (dựng runner {built - started:.1f} s)")
    return 1 if any(item.status == STATUS_FAIL for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())
