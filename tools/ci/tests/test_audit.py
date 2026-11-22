"""Test `tools.ci.audit` — CASE §2.3 [8] "audit-allowlist": hết hạn/thiếu lý do → hỏng; hợp lệ → đạt."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from tools.ci.audit import _load_allowlist, main

REPO_ROOT = Path(__file__).resolve().parents[3]
_TODAY = "2026-09-22"


def _allowlist(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "allowlist.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _report(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(tmp_path: Path, allowlist: Path, report: Path, today: str = _TODAY) -> int:
    return main(["--allowlist", str(allowlist), "--report", str(report), "--today", today])


_EMPTY_REPORT: dict[str, Any] = {"dependencies": []}
_VALID_ENTRY = """
[[ignore]]
id = "GHSA-aaaa"
reason = "khong the nang cap ngay, cho ban va"
expires = 2027-01-01
"""


def test_expired_entry_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    allow = _allowlist(tmp_path, '[[ignore]]\nid = "GHSA-x"\nreason = "da qua han roi ma"\nexpires = 2020-01-01\n')
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 1
    assert "đã hết hạn" in capsys.readouterr().err


def test_missing_field_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    allow = _allowlist(tmp_path, '[[ignore]]\nid = "GHSA-x"\nreason = "thieu truong expires"\n')
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 1
    assert "thiếu trường" in capsys.readouterr().err


def test_reason_too_short_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    allow = _allowlist(tmp_path, '[[ignore]]\nid = "GHSA-x"\nreason = "ngan"\nexpires = 2027-01-01\n')
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 1
    assert "lý do miễn quá ngắn" in capsys.readouterr().err


def test_duplicate_id_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = (
        '[[ignore]]\nid = "GHSA-x"\nreason = "ly do dau tien du dai"\nexpires = 2027-01-01\n'
        '[[ignore]]\nid = "GHSA-x"\nreason = "ly do thu hai du dai"\nexpires = 2027-01-01\n'
    )
    allow = _allowlist(tmp_path, body)
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 1
    assert "id miễn trùng" in capsys.readouterr().err


def test_expires_not_a_toml_date_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    allow = _allowlist(
        tmp_path, '[[ignore]]\nid = "GHSA-x"\nreason = "chuoi khong phai ngay toml"\nexpires = "2027-01-01"\n'
    )
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 1
    assert "không phải ngày TOML" in capsys.readouterr().err


def test_valid_allowlist_with_no_matching_vuln_passes_with_reminder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = _allowlist(tmp_path, _VALID_ENTRY)
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 0
    assert "GHSA-aaaa không khớp lỗ hổng nào" in capsys.readouterr().out


def test_vuln_with_fix_outside_allowlist_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.0.0",
                "vulns": [{"id": "GHSA-bbbb", "fix_versions": ["2.1.0"], "aliases": []}],
            }
        ]
    }
    code = _run(tmp_path, _allowlist(tmp_path, ""), _report(tmp_path, report))
    assert code == 1
    assert "requests 2.0.0 GHSA-bbbb sửa ở 2.1.0" in capsys.readouterr().err


def test_vuln_waived_by_alias_passes(tmp_path: Path) -> None:
    report = {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.0.0",
                "vulns": [{"id": "CVE-2020-1", "fix_versions": ["2.1.0"], "aliases": ["GHSA-aaaa"]}],
            }
        ]
    }
    code = _run(tmp_path, _allowlist(tmp_path, _VALID_ENTRY), _report(tmp_path, report))
    assert code == 0


def test_vuln_without_fix_version_passes_with_note(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.0.0",
                "vulns": [{"id": "GHSA-cccc", "fix_versions": [], "aliases": []}],
            }
        ]
    }
    code = _run(tmp_path, _allowlist(tmp_path, ""), _report(tmp_path, report))
    assert code == 0
    assert "chưa có bản sửa" in capsys.readouterr().out


def test_skipped_package_only_prints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = {"dependencies": [{"name": "weird-pkg", "version": "1.0", "skip_reason": "resolution", "vulns": []}]}
    code = _run(tmp_path, _allowlist(tmp_path, ""), _report(tmp_path, report))
    assert code == 0
    assert "bỏ qua gói weird-pkg" in capsys.readouterr().out


def test_malformed_report_json_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text("khong phai json", encoding="utf-8")
    code = _run(tmp_path, _allowlist(tmp_path, ""), report_path)
    assert code == 1
    assert "không phải JSON hợp lệ" in capsys.readouterr().err


def test_default_today_used_when_not_passed(tmp_path: Path) -> None:
    allow = _allowlist(tmp_path, _VALID_ENTRY)
    code = main(["--allowlist", str(allow), "--report", str(_report(tmp_path, _EMPTY_REPORT))])
    assert code == 0


def test_committed_allowlist_is_valid_and_only_has_known_entries(tmp_path: Path) -> None:
    """Tệp thật hợp lệ; chỉ chứa mục miễn đã biết (K-51: CVE-2026-126360 tới 06/12/2026 — xoá khi sàn >=1.45.2 vào main)."""
    allow = REPO_ROOT / "tools" / "ci" / "audit-allowlist.toml"
    entries, errors = _load_allowlist(allow, datetime.date.fromisoformat(_TODAY))
    assert {e.id for e in entries} <= {"CVE-2026-126360"}
    assert all(e.expires <= datetime.date(2026, 12, 6) for e in entries)
    assert errors == []
    # cũng đọc được qua CLI thật, không chỉ qua hàm nội bộ
    code = _run(tmp_path, allow, _report(tmp_path, _EMPTY_REPORT))
    assert code == 0
