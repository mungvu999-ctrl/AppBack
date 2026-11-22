"""Gọi API của stack f14 bằng urllib (không phụ thuộc ngoài): `call` một lần có thử lại, `Session` giữ đăng nhập."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any

from tools.bench.console import say

Sleep = Callable[[float], None]
Response = tuple[int, Any, Message | None]
_ATTEMPTS = 5
_HTTP_TIMEOUT_S = 120


class BenchError(RuntimeError):
    """Lỗi của lượt bench (đăng nhập, tạo dự án, tải tệp...): ca đó không có kết quả, lượt vẫn chạy tiếp ca khác."""


def expect(ok: bool, what: str) -> None:
    """Ném `BenchError(what)` khi `ok` sai; thay `assert` (không bị tắt khi chạy -O)."""
    if not ok:
        raise BenchError(what)


def _request(
    base: str, origin: str, method: str, path: str, data: bytes | None, token: str | None, cookie: str | None
) -> urllib.request.Request:
    """Dựng một `Request` cho `base + path` kèm Origin, JSON, Bearer, Cookie."""
    expect(base.startswith(("http://", "https://")), f"BENCH_API phải là http(s): {base!r}")
    req = urllib.request.Request(base + path, data=data, method=method)  # noqa: S310 - địa chỉ cấu hình, đã kiểm lược đồ
    req.add_header("Origin", origin)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if cookie:
        req.add_header("Cookie", cookie)
    return req


def call(
    base: str,
    origin: str,
    method: str,
    path: str,
    body: Any = None,
    token: str | None = None,
    cookie: str | None = None,
    sleep: Sleep = time.sleep,
) -> Response:
    """Một lời gọi JSON -> (mã, thân đã giải, header).

    api/ml có thể tự khởi động lại: 5xx thử lại (10 s, 20 s...), lỗi mạng thử lại (20 s, 40 s...), tối đa 5 lần, rồi
    ném `TimeoutError`; mã 4xx trả nguyên cho người gọi (R-16: lỗi của người gọi không thử lại).
    """
    data = None if body is None else json.dumps(body).encode()
    for attempt in range(_ATTEMPTS):
        req = _request(base, origin, method, path, data, token, cookie)
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as r:  # noqa: S310 - xem _request
                payload = r.read()
                return r.status, (json.loads(payload) if payload else None), r.headers
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < _ATTEMPTS - 1:
                say(f"  thử lại {method} {path} {e.code}")
                sleep(10 * (attempt + 1))
                continue
            return e.code, json.loads(e.read() or b"null"), e.headers
        except (TimeoutError, ConnectionError, urllib.error.URLError) as e:
            say(f"  thử lại {method} {path} {type(e).__name__}")
            sleep(20 * (attempt + 1))
    raise TimeoutError(f"{method} {path}")


def _cookie_pair(set_cookie: str) -> tuple[str, str]:
    """Cặp (tên, giá trị) từ một header Set-Cookie (bỏ thuộc tính sau dấu `;`)."""
    name, _, value = set_cookie.split(";", 1)[0].partition("=")
    return name, value


class Session:
    """Phiên đăng nhập admin: giữ cookie (refresh token xoay vòng) và access token; tự làm mới khi gặp 401."""

    def __init__(self, base: str, origin: str, email: str, password: str, sleep: Sleep = time.sleep) -> None:
        """Đăng nhập ngay; `email`/`password` là tài khoản E2E của stack f14."""
        self.base, self.origin, self.sleep = base, origin, sleep
        self.email, self.password = email, password
        self.cookie = ""
        self.token: str | None = None
        self.login()

    def _call(
        self, method: str, path: str, body: Any = None, token: str | None = None, cookie: str | None = None
    ) -> Response:
        """`call` với địa chỉ/Origin/ngủ của phiên."""
        return call(self.base, self.origin, method, path, body, token, cookie, self.sleep)

    def login(self) -> None:
        """POST /auth/login rồi lấy access token qua refresh. Ném `BenchError` nếu máy chủ từ chối."""
        s, _, h = self._call(
            "POST", "/auth/login", {"email": self.email, "password": self.password, "rememberMe": False}
        )
        expect(s in (200, 204), f"đăng nhập lỗi {s}")
        self.cookie = "; ".join(c.split(";", 1)[0] for c in ((h.get_all("Set-Cookie") if h else None) or []))
        self.refresh(relogin=False)

    def refresh(self, relogin: bool = True) -> None:
        """POST /auth/refresh. Refresh token xoay vòng: giữ cookie mới (cookie cũ đã bị thu hồi).

        Phiên khác đăng nhập cùng tài khoản có thể thu hồi phiên này: `relogin` cho phép đăng nhập lại một lần.
        """
        s, body, h = self._call("POST", "/auth/refresh", cookie=self.cookie)
        new = (h.get_all("Set-Cookie") if h is not None else None) or []
        if new:
            jar = dict(c.split("=", 1) for c in self.cookie.split("; ") if "=" in c)
            jar.update(_cookie_pair(c) for c in new)
            self.cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
        self.token = (body or {}).get("accessToken") or (body or {}).get("access_token")
        if not self.token and relogin:
            say("  đăng nhập lại")
            self.login()
            return
        expect(bool(self.token), f"refresh lỗi {s}")

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Gọi API với Bearer; 401 thì làm mới token và gọi lại một lần."""
        s, b, _ = self._call(method, path, body, self.token)
        if s == 401:
            self.refresh()
            s, b, _ = self._call(method, path, body, self.token)
        return s, b
