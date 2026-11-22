"""Cấu hình (biến môi trường), gọi API có thử lại, phiên đăng nhập, lệnh docker/RAM: toàn bộ bằng giả, không mạng."""

from __future__ import annotations

import ctypes
import io
import json
import os
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from tools.bench import api, config, console, stack

# ---------------------------------------------------------------- config


def test_config_defaults_and_overrides() -> None:
    """Mặc định cho API/Origin/ảnh worker; biến môi trường ghi đè."""
    assert config.bench_api({}) == "http://localhost:18081/api"
    assert config.bench_origin({}) == "http://localhost:18080"
    assert config.bench_worker_image({}) == "appback-worker:f14"
    env = {"BENCH_API": "http://x/api", "BENCH_ORIGIN": "http://o", "BENCH_WORKER_IMAGE": "img:1"}
    assert (config.bench_api(env), config.bench_origin(env), config.bench_worker_image(env)) == (
        "http://x/api",
        "http://o",
        "img:1",
    )


def test_config_required_paths() -> None:
    """BENCH_ROOT và BENCH_SECRETS không có mặc định: thiếu thì báo rõ tên biến."""
    with pytest.raises(config.ConfigError, match="BENCH_ROOT"):
        config.bench_root({})
    with pytest.raises(config.ConfigError, match="BENCH_SECRETS"):
        config.bench_secrets({"BENCH_SECRETS": "  "})
    assert config.bench_root({"BENCH_ROOT": "/d"}) == Path("/d")
    assert config.bench_secrets({"BENCH_SECRETS": "/s.env"}) == Path("/s.env")


def test_config_reads_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không truyền `env` thì đọc `os.environ`."""
    monkeypatch.setenv("BENCH_ROOT", "/r")
    assert config.bench_root() == Path("/r")


def test_replay_sets_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đường lượt gốc của hai tập mặc định và ghi đè được; ml-cache nằm dưới gốc."""
    root = Path("/root")
    sets = config.replay_sets(root, {})
    assert sets["bench"] == (root / "runs/r4b", root)
    assert sets["F"] == (root / "tap-f/runs/f2", root / "tap-f")
    over = config.replay_sets(root, {"BENCH_RUN_BENCH": "runs/x", "BENCH_RUN_F": "f/y", "BENCH_ROOT_F": "/other"})
    assert over["bench"][0] == root / "runs/x"
    assert over["F"] == (root / "f/y", Path("/other"))
    monkeypatch.delenv("BENCH_RUN_BENCH", raising=False)
    assert config.replay_sets(root)["bench"][0] == root / "runs/r4b"
    assert config.ml_cache(root) == root / "ml-cache"


def test_console_writes_streams(capsys: pytest.CaptureFixture[str]) -> None:
    """`say` ra stdout, `warn` ra stderr."""
    console.say("a")
    console.warn("b")
    out = capsys.readouterr()
    assert (out.out, out.err) == ("a\n", "b\n")


# ---------------------------------------------------------------- api


class FakeResponse:
    """Phản hồi urlopen giả: dùng được với `with`."""

    def __init__(self, payload: bytes, status: int = 200, headers: Message | None = None) -> None:
        """Giữ thân, mã và header."""
        self.payload, self.status, self.headers = payload, status, headers or Message()

    def __enter__(self) -> FakeResponse:
        """Vào `with`."""
        return self

    def __exit__(self, *a: object) -> None:
        """Thoát `with`."""

    def read(self) -> bytes:
        """Thân phản hồi."""
        return self.payload


def http_error(code: int, body: bytes = b'{"e":1}') -> urllib.error.HTTPError:
    """Một HTTPError với thân JSON."""
    return urllib.error.HTTPError("http://x", code, "err", Message(), io.BytesIO(body))


def script(monkeypatch: pytest.MonkeyPatch, steps: list[Any]) -> list[urllib.request.Request]:
    """Thay `urlopen` bằng kịch bản: mỗi lời gọi lấy một bước (ngoại lệ thì ném, còn lại trả về)."""
    seen: list[urllib.request.Request] = []

    def fake(req: urllib.request.Request, timeout: float = 0) -> Any:
        seen.append(req)
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_call_sends_headers_and_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gửi Origin, JSON, Bearer, Cookie; trả (mã, thân, header)."""
    seen = script(monkeypatch, [FakeResponse(b'{"ok": true}'), FakeResponse(b"")])
    s, body, _ = api.call("http://h/api", "http://o", "POST", "/p", {"a": 1}, token="T", cookie="c=1")  # noqa: S106
    assert (s, body) == (200, {"ok": True})
    req = seen[0]
    assert req.full_url == "http://h/api/p"
    assert req.get_header("Origin") == "http://o"
    assert req.get_header("Content-type") == "application/json"
    assert (req.get_header("Authorization"), req.get_header("Cookie")) == ("Bearer T", "c=1")
    assert api.call("http://h/api", "http://o", "GET", "/p")[1] is None
    assert seen[1].get_header("Content-type") is None


def test_call_retries_5xx_and_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx và lỗi mạng thử lại (ngủ tăng dần); 4xx trả nguyên; hết lượt thử thì ném TimeoutError."""
    sleeps: list[float] = []
    script(monkeypatch, [http_error(503), urllib.error.URLError("down"), FakeResponse(b"{}")])
    assert api.call("http://h", "o", "GET", "/x", sleep=sleeps.append)[0] == 200
    assert sleeps == [10, 40]
    script(monkeypatch, [http_error(404, b"")])
    assert api.call("http://h", "o", "GET", "/x", sleep=sleeps.append)[:2] == (404, None)
    script(monkeypatch, [TimeoutError()] * 5)
    with pytest.raises(TimeoutError):
        api.call("http://h", "o", "GET", "/x", sleep=lambda s: None)
    script(monkeypatch, [http_error(500)] * 5)
    assert api.call("http://h", "o", "GET", "/x", sleep=lambda s: None)[0] == 500


def test_call_rejects_non_http_base() -> None:
    """Địa chỉ không phải http(s) bị từ chối trước khi gọi."""
    with pytest.raises(api.BenchError, match="http"):
        api.call("file:///etc", "o", "GET", "/x")


def cookies(*pairs: str) -> Message:
    """Header giả mang các Set-Cookie."""
    m = Message()
    for p in pairs:
        m["Set-Cookie"] = p
    return m


def test_session_login_refresh_and_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đăng nhập lấy cookie, refresh xoay cookie và lấy token; 401 thì làm mới và gọi lại."""
    seen = script(
        monkeypatch,
        [
            FakeResponse(b"", 204, cookies("sid=1; Path=/", "rt=a; HttpOnly")),
            FakeResponse(b'{"accessToken": "T1"}', 200, cookies("rt=b; Path=/")),
            http_error(401),
            FakeResponse(b'{"access_token": "T2"}', 200, None),
            FakeResponse(b'{"x": 1}'),
        ],
    )
    s = api.Session("http://h", "o", "e@x", "pw", sleep=lambda t: None)
    assert s.cookie == "sid=1; rt=b"
    assert s.token == "T1"  # noqa: S105
    assert s("GET", "/me") == (200, {"x": 1})
    assert s.token == "T2"  # noqa: S105
    assert seen[-1].get_header("Authorization") == "Bearer T2"


def test_session_relogin_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh không ra token: đăng nhập lại một lần; đăng nhập lỗi hoặc vẫn không có token thì ném."""
    script(
        monkeypatch,
        [
            FakeResponse(b"", 200, cookies("rt=a")), FakeResponse(b"{}"),  # login, refresh trống
            FakeResponse(b"", 200, cookies("rt=a")), FakeResponse(b'{"accessToken": "T"}'),  # đăng nhập lại
        ],
    )  # fmt: skip
    # login() ban đầu dùng relogin=False nên refresh trống làm hỏng ngay
    with pytest.raises(api.BenchError, match="refresh lỗi"):
        api.Session("http://h", "o", "e", "p", sleep=lambda t: None)
    script(monkeypatch, [http_error(401, b'{"m":1}')])
    with pytest.raises(api.BenchError, match="đăng nhập lỗi 401"):
        api.Session("http://h", "o", "e", "p", sleep=lambda t: None)
    # phiên đã có: refresh trống khi gặp 401 -> đăng nhập lại
    script(
        monkeypatch,
        [
            FakeResponse(b"", 200, cookies("rt=a")), FakeResponse(b'{"accessToken": "T"}'),
            http_error(401), FakeResponse(b"{}"),  # gọi 401, refresh trống
            FakeResponse(b"", 200, cookies("rt=c")), FakeResponse(b'{"accessToken": "T3"}'), FakeResponse(b'{"ok": 1}'),
        ],
    )  # fmt: skip
    s = api.Session("http://h", "o", "e", "p", sleep=lambda t: None)
    assert s("GET", "/x") == (200, {"ok": 1})
    assert s.token == "T3"  # noqa: S105


# ---------------------------------------------------------------- stack


def test_sh_runs_command() -> None:
    """`sh` trả stdout đã cắt khoảng trắng."""
    import sys

    assert stack.sh(sys.executable, "-c", "print('  hi  ')") == "hi"


def test_free_gb_non_windows_and_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ngoài Windows dùng sysconf; Windows gọi GlobalMemoryStatusEx (giả) và đổi byte -> GiB."""
    if hasattr(os, "sysconf"):
        assert stack.free_gb("linux") >= 0

    class Kernel:
        def GlobalMemoryStatusEx(self, ref: Any) -> None:  # noqa: N802 - tên hàm Win32
            ref._obj.avail = 3 * 2**30

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": Kernel()})(), raising=False)
    assert stack.free_gb("win32") == 3.0


def test_queues_busy_sums_numbers() -> None:
    """Cộng các số `llen`, bỏ dòng không phải số."""
    assert stack.queues_busy(lambda *a: "2\n0\nx\n5") == 7
    assert stack.queues_busy(lambda *a: "") == 0


def test_snapshot_lists_hotpatched_files() -> None:
    """Chụp stack: ảnh, id ảnh, vá nóng (tệp .py thêm/đổi) kèm md5, ML_BACKEND; thiếu thông tin vẫn chụp được."""
    calls: list[tuple[str, ...]] = []

    def run(*cmd: str) -> str:
        calls.append(cmd)
        sub = cmd[1]
        if sub == "inspect":
            if cmd[2] == "f14-web-1":
                return ""
            return json.dumps([{"Config": {"Image": "img"}, "Image": "sha", "State": {"StartedAt": "t0"}}])
        if sub == "diff":
            return "A /app/a.py\nC /app/b.py\nD /app/c.py\nA /app/__pycache__/x.py\nC /app/data.txt"
        if cmd[2] == "f14-api-1" and cmd[3] == "md5sum":
            return "m2  /app/b.py\nm1  /app/a.py"
        return "onnx"

    snap = stack.snapshot(run)
    api_c = snap["containers"]["f14-api-1"]
    assert api_c["image"] == "img"
    assert api_c["hotpatch_md5"] == ["m1  /app/a.py", "m2  /app/b.py"]
    assert api_c["ML_BACKEND"] == "onnx"
    assert snap["containers"]["f14-web-1"]["image"] is None
    assert ("docker", "exec", "f14-api-1", "md5sum", "/app/a.py", "/app/b.py") in calls
