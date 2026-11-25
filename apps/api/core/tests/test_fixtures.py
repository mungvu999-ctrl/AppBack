"""Fixture HTTP dùng chung (`packages/testing/fixtures/api.py`): observer và vết case.

Vết case là đầu vào của `tools/case_gate.py`: ghi sai là một test đặt đúng
tên vẫn không được tính, hoặc tệ hơn — được tính cho sai thao tác.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import httpx
import pytest

from packages.testing.fixtures import api as api_fixtures

TEST_NAME: Final = "test_mau__C04"


def _response(path: str, status: int, *, content: bytes, content_type: str = "application/json") -> httpx.Response:
    """Response đã gắn request — `trace_case` đọc method và đường từ request."""
    request = httpx.Request("GET", f"https://testserver{path}")
    return httpx.Response(status, content=content, headers={"content-type": content_type}, request=request)


def _item() -> Any:
    """Đủ cho observer: chỉ cần `name`."""
    return SimpleNamespace(name=TEST_NAME)


def test_trace_case_writes_one_line_for_a_real_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Một response của thao tác thật → đúng một dòng `{test, op, status, code}`."""
    trace = tmp_path / "vet.jsonl"
    monkeypatch.setenv(api_fixtures.TRACE_ENV, str(trace))
    body = json.dumps({"code": "NOT_FOUND", "requestId": "rid-12345678"}).encode()
    api_fixtures.trace_case(_item(), _response("/api/files/token-bat-ky", 404, content=body))
    assert json.loads(trace.read_text(encoding="utf-8")) == {
        "test": TEST_NAME,
        "op": "files_read_object",
        "status": 404,
        "code": "NOT_FOUND",
    }


def test_trace_case_skips_paths_outside_the_real_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đường của app thử không khớp thao tác nào → không ghi, để không sinh vết case giả."""
    trace = tmp_path / "vet.jsonl"
    monkeypatch.setenv(api_fixtures.TRACE_ENV, str(trace))
    api_fixtures.trace_case(_item(), _response("/api/sample/public", 200, content=b"{}"))
    assert not trace.exists()


def test_trace_case_without_trace_file_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chạy test ngoài cổng (không đặt `CASE_TRACE_FILE`) thì observer không làm gì."""
    monkeypatch.delenv(api_fixtures.TRACE_ENV, raising=False)
    api_fixtures.trace_case(_item(), _response("/api/health", 200, content=b'{"status": "ok"}'))


@pytest.mark.parametrize(
    ("content", "content_type", "expected"),
    [
        (b'{"code": "VALIDATION", "requestId": "rid"}', "application/json", "VALIDATION"),
        (b"{khong-phai-json", "application/json", None),
        (b'["mang"]', "application/json", None),
        (b"van ban", "text/plain", None),
    ],
    ids=["thân W7", "JSON hỏng", "không phải object", "không phải JSON"],
)
def test_error_code_reads_only_w7_bodies(content: bytes, content_type: str, expected: str | None) -> None:
    """Mã lỗi chỉ lấy từ thân W7; thân khác không được làm hỏng observer."""
    response = _response("/api/health", 500, content=content, content_type=content_type)
    assert api_fixtures._error_code(response) == expected


def test_notify_without_running_test_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """Response gửi ngoài một test (vd trong hook) không có item để đưa cho observer."""
    monkeypatch.setattr(api_fixtures, "_current_item", None)
    api_fixtures._notify(_response("/api/health", 200, content=b"{}"))


def test_pytest_configure_registers_the_trace_observer_once() -> None:
    """Gọi `pytest_configure` lần hai (plugin nạp lại) không đăng ký trùng observer."""
    config: Any = SimpleNamespace(stash=pytest.Stash())
    api_fixtures.pytest_configure(config)
    api_fixtures.pytest_configure(config)
    assert config.stash[api_fixtures.API_RESPONSE_OBSERVERS] == [api_fixtures.trace_case]


def test_api_env_flushes_both_redis_roles(request: pytest.FixtureRequest, api_env: None) -> None:
    """Mọi lượt ASGI đến từ `127.0.0.1`: bộ đếm `store="safe"` và khoá đăng nhập ở DB an toàn cũng phải được
    `FLUSHDB` sau mỗi test như DB cache, không thì test sau bị test trước làm 429 (NO-055)."""
    assert {"cache_client", "safe_client"} <= set(request.fixturenames)


def test_api_env_keeps_the_metrics_exporter_off(api_env: None) -> None:
    """NO-159: app của test không được mở exporter thật.

    `metrics_lifespan` gắn vào router công khai nên **mọi** app test sẽ nghe cổng 9464
    (mặc định của `ObservabilitySettings`) và trả tới `POLL_INTERVAL_S` lúc tắt: đo được
    ~1,8 s trên 215 test của module khác. Test lifespan của B7-01 tự đặt cổng riêng.
    """
    assert os.environ["METRICS_PORT"] == "0"
