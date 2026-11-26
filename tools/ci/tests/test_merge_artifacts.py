"""Test `tools.ci.merge_artifacts` — [8] "merge_artifacts": gộp junit + vết, kiểm đủ artifact."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.case_gate import parse_junit
from tools.ci.merge_artifacts import main


def _write_job(root: Path, job: str, *, junit: str | None, trace: str | None, coverage: bool = True) -> None:
    """Ghi artifact giả của một job dưới `<root>/<job>/`; `None` nghĩa là cố tình bỏ file đó."""
    job_dir = root / job
    job_dir.mkdir(parents=True, exist_ok=True)
    if junit is not None:
        (job_dir / f"junit-{job}.xml").write_text(junit, encoding="utf-8")
    if trace is not None:
        (job_dir / f"trace-{job}.jsonl").write_text(trace, encoding="utf-8")
    if coverage:
        (job_dir / f".coverage.{job}").write_bytes(b"")


_JUNIT_UNIT = """<testsuites>
  <testsuite name="unit" tests="2">
    <testcase classname="tools.ci.tests.x" name="test_unit_case">
      <properties><property name="k" value="v"/></properties>
    </testcase>
    <testcase classname="tools.ci.tests.x" name="test_unit_skip"><skipped message="chua cai"/></testcase>
  </testsuite>
</testsuites>
"""

# Gốc là <testsuite> trực tiếp (không bọc <testsuites>) — phải được nhận.
_JUNIT_INTEGRATION = """<testsuite name="integration" tests="1">
  <testcase classname="tools.ci.tests.x" name="test_integration_case"><failure message="loi">tb</failure></testcase>
</testsuite>
"""

# Tên testcase trùng với unit (test_unit_case) để kiểm "tên trùng giữ đủ, không khử trùng".
_JUNIT_ML = """<testsuites>
  <testsuite name="ml" tests="1">
    <testcase classname="tools.ci.tests.y" name="test_unit_case"/>
  </testsuite>
</testsuites>
"""

_JOBS = ("unit", "integration", "ml")


def _write_happy_path(root: Path) -> None:
    """Ba job hợp lệ: `unit`, `integration` (gốc `<testsuite>` trần), `ml`; vết `unit` có dòng trống cuối cố ý."""
    _write_job(root, "unit", junit=_JUNIT_UNIT, trace='{"test": "test_unit_case", "op": "opA", "status": 200}\n\n')
    _write_job(
        root,
        "integration",
        junit=_JUNIT_INTEGRATION,
        trace='{"test": "test_integration_case", "op": "opB", "status": 500}\n',
    )
    _write_job(root, "ml", junit=_JUNIT_ML, trace="")


def _run(tmp_path: Path, jobs: str = ",".join(_JOBS)) -> tuple[int, Path, Path]:
    """Gọi `main()` với `--root tmp_path`; trả (mã thoát, đường junit-out, đường trace-out)."""
    junit_out = tmp_path / "out" / "junit.xml"
    trace_out = tmp_path / "out" / "trace.jsonl"
    junit_out.parent.mkdir(parents=True, exist_ok=True)
    code = main(["--root", str(tmp_path), "--jobs", jobs, "--junit-out", str(junit_out), "--trace-out", str(trace_out)])
    return code, junit_out, trace_out


def test_merge_keeps_all_testcases_including_duplicates_and_skipped_failure(tmp_path: Path) -> None:
    _write_happy_path(tmp_path)
    code, junit_out, _ = _run(tmp_path)
    assert code == 0

    before = sum(x.count("<testcase") for x in (_JUNIT_UNIT, _JUNIT_INTEGRATION, _JUNIT_ML))
    merged_root = ET.parse(junit_out).getroot()  # noqa: S314 -- file do main() sinh trong test, không phải input ngoài
    after = list(merged_root.iter("testcase"))
    assert merged_root.tag == "testsuites"
    assert len(after) == before == 4
    names = [c.get("name") for c in after]
    assert names.count("test_unit_case") == 2  # trùng tên không bị khử


def test_merged_junit_readable_by_case_gate_parse_junit(tmp_path: Path) -> None:
    _write_happy_path(tmp_path)
    code, junit_out, _ = _run(tmp_path)
    assert code == 0

    tests = parse_junit(junit_out)
    by_name = {t.name: t for t in tests}
    assert len(tests) == 4
    assert by_name["test_unit_skip"].outcome == "skipped"
    assert by_name["test_integration_case"].outcome == "failed"
    assert by_name["test_unit_case"].outcome == "passed"


def test_merge_trace_keeps_all_lines_and_drops_trailing_blank(tmp_path: Path) -> None:
    _write_happy_path(tmp_path)
    code, _, trace_out = _run(tmp_path)
    assert code == 0

    lines = trace_out.read_text(encoding="utf-8").splitlines()
    assert lines == [
        '{"test": "test_unit_case", "op": "opA", "status": 200}',
        '{"test": "test_integration_case", "op": "opB", "status": 500}',
    ]


def test_empty_trace_file_is_valid(tmp_path: Path) -> None:
    _write_job(tmp_path, "solo", junit=_JUNIT_UNIT, trace="")
    code, _, trace_out = _run(tmp_path, jobs="solo")
    assert code == 0
    assert trace_out.read_text(encoding="utf-8") == ""


def test_missing_artifact_of_a_job_fails_and_names_job(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_happy_path(tmp_path)
    (tmp_path / "integration" / "junit-integration.xml").unlink()
    code, junit_out, _ = _run(tmp_path)
    assert code == 1
    assert not junit_out.exists()
    err = capsys.readouterr().err
    assert "thiếu artifact của job integration: junit-integration.xml" in err


def test_missing_trace_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_job(tmp_path, "solo", junit=_JUNIT_UNIT, trace=None)
    code, _, _ = _run(tmp_path, jobs="solo")
    assert code == 1
    assert "thiếu artifact của job solo: trace-solo.jsonl" in capsys.readouterr().err


def test_missing_coverage_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_job(tmp_path, "solo", junit=_JUNIT_UNIT, trace="", coverage=False)
    code, _, _ = _run(tmp_path, jobs="solo")
    assert code == 1
    assert "thiếu artifact của job solo: .coverage.*" in capsys.readouterr().err


def test_malformed_junit_xml_fails_naming_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_job(tmp_path, "solo", junit="<testsuites><testsuite>", trace="")
    code, junit_out, _ = _run(tmp_path, jobs="solo")
    assert code == 1
    assert not junit_out.exists()
    err = capsys.readouterr().err
    assert "junit hỏng" in err
    assert "junit-solo.xml" in err


def test_trace_line_not_json_fails_with_file_and_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_job(tmp_path, "solo", junit=_JUNIT_UNIT, trace='{"test": "a", "op": "b", "status": 200}\nkhong-phai-json\n')
    code, _, trace_out = _run(tmp_path, jobs="solo")
    assert code == 1
    assert not trace_out.exists()
    err = capsys.readouterr().err
    assert "trace-solo.jsonl:2" in err


def test_blank_line_in_middle_of_trace_is_treated_as_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_job(
        tmp_path,
        "solo",
        junit=_JUNIT_UNIT,
        trace='{"test": "a", "op": "b", "status": 200}\n\n{"test": "c", "op": "d", "status": 200}\n',
    )
    code, _, _ = _run(tmp_path, jobs="solo")
    assert code == 1
    assert "trace-solo.jsonl:2" in capsys.readouterr().err
