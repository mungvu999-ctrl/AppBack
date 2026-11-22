"""Trợ giúp chung cho test script B0-10 (`deploy/scripts`, `deploy/backup`) — không phải test.

Hai công cụ, dùng lại ở mọi file test của B0-10 (R-07):
- `HttpStub`: máy chủ HTTP thử (`http.server`) — trả đáp án theo đường dẫn, ghi lại mọi
  thân POST (kiểm thân tin cảnh báo `{"text","content"}`, `smoke.sh`).
- `fake_bin` + `run_script`: lệnh giả đặt đầu `PATH`, mỗi lần gọi ghi một dòng
  `<tên> <đối số>` vào `$FAKE_LOG` — **chỉ** để kiểm thứ tự lệnh (B0-10 [8]), không thay
  dịch vụ thật trong kiểm chạy thật.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Reply:
    """Đáp án cố định cho một đường dẫn của `HttpStub`."""

    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    delay_s: float = 0.0
    """Ngủ trước khi trả lời — dựng route "treo" để kiểm ngân sách `--max-time` (smoke.sh)."""


class HttpStub:
    """Máy chủ HTTP thử trên `127.0.0.1:<cổng ngẫu nhiên>`, chạy luồng nền.

    `routes` ánh xạ đường dẫn (không query) → `Reply`; đường lạ trả 404 thân rỗng.
    Mọi POST được ghi vào `posts` dạng `(đường dẫn, thân)` theo thứ tự nhận.
    Dùng làm context manager để máy chủ luôn tắt, kể cả khi test hỏng.
    """

    def __init__(self, routes: Mapping[str, Reply] | None = None, post_reply: Reply | None = None) -> None:
        """Nhận bảng đường dẫn cho GET và đáp án chung cho POST (mặc định 200)."""
        self.routes = dict(routes or {})
        self.post_reply = post_reply or Reply()
        self.posts: list[tuple[str, bytes]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Gốc URL không có `/` cuối, vd `http://127.0.0.1:43127`."""
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def __enter__(self) -> HttpStub:
        """Bật máy chủ."""
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """Tắt máy chủ và giải phóng cổng."""
        self._server.shutdown()
        self._server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        """Lớp xử lý đóng gói `self` — `http.server` chỉ nhận lớp, không nhận thể hiện."""
        stub = self

        class Handler(BaseHTTPRequestHandler):
            """Trả `Reply` theo đường dẫn; ghi thân POST."""

            def _send(self, reply: Reply) -> None:
                """Ngủ `delay_s` (nếu có) rồi gửi mã, header và thân của `reply`."""
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

            def do_GET(self) -> None:
                """GET: tra bảng đường dẫn."""
                self._send(stub.routes.get(self.path.split("?")[0], Reply(404)))

            def do_POST(self) -> None:
                """POST: ghi thân rồi trả `post_reply`."""
                length = int(self.headers.get("Content-Length") or 0)
                stub.posts.append((self.path, self.rfile.read(length)))
                self._send(stub.post_reply)

            def log_message(self, format: str, *args: object) -> None:
                """Im lặng — log truy cập làm rối output pytest."""

        return Handler


def fake_bin(directory: Path, commands: Mapping[str, str]) -> Path:
    """Tạo lệnh giả trong `directory`; trả `directory` để đặt đầu `PATH`.

    `commands` ánh xạ tên lệnh → thân bash chạy **sau** dòng ghi log. Mỗi lần gọi ghi
    `<tên> <đối số…>` vào `$FAKE_LOG` (bắt buộc đặt biến này khi chạy). Thân rỗng = thoát 0.
    Thân có thể đọc `"$@"` để quyết định mã thoát/stdout, vd làm `migrate` hỏng.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in commands.items():
        path = directory / name
        path.write_text(
            '#!/usr/bin/env bash\nprintf \'%s\\n\' "$(basename "$0") $*" >> "$FAKE_LOG"\n' + body + "\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    return directory


def run_script(
    script: Path,
    args: Sequence[str] = (),
    *,
    env: Mapping[str, str] | None = None,
    bin_dir: Path | None = None,
    stdin: str | None = None,
    timeout: float = 60,
    broken_python3: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Chạy `bash <script> <args>`, môi trường hiện tại + `env`, `bin_dir` đứng đầu `PATH`.

    Không ném khi mã thoát khác 0 — test tự kiểm `returncode`, stdout, stderr.
    `APPBACK_API_SWAP_SETTLE_S=0` mặc định (`env` ghi đè được): `lib.sh` `swap_api`
    ngủ thật theo biến này (mặc định sản xuất 11s, chờ nginx tự dịch lại DNS) — test
    dùng docker giả nên không cần chờ, giữ mặc định thật sẽ làm MỌI test đụng
    `swap_api` chậm thêm 11s không lý do (review round 1 R-05).
    """
    full_env = {**os.environ, "APPBACK_API_SWAP_SETTLE_S": "0", **(env or {})}
    # Mọi script chạy với `python3` là shim hỏng (như shim Microsoft Store, NO-189): script nào
    # còn gọi `python3` trên host thì test của nó đỏ, không cần test riêng từng script.
    # `broken_python3=False` chỉ cho bước lấy từ workflow GitHub (chạy trên runner có python3 thật).
    with tempfile.TemporaryDirectory() as shim_root:
        shim = fake_bin(Path(shim_root), {"python3": "exit 9\n"}) if broken_python3 else Path(shim_root)
        search = [str(shim), full_env.get("PATH", "")]
        if bin_dir is not None:
            search.insert(0, str(bin_dir))
        full_env["PATH"] = os.pathsep.join(search)
        full_env.setdefault("FAKE_LOG", str(Path(shim_root) / "shim.log"))
        return subprocess.run(  # noqa: S603 — script của repo, đối số do test đặt
            ["bash", str(script), *args],  # noqa: S607 — "bash" có sẵn trên PATH
            env=full_env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


def read_log(log: Path) -> Iterator[str]:
    """Các dòng của `$FAKE_LOG` theo thứ tự gọi; chưa có lệnh nào → rỗng."""
    if log.exists():
        yield from log.read_text(encoding="utf-8").splitlines()


# Đầu thân `docker` giả (đặt TRƯỚC các nhánh khác): `docker compose config --services` in tập
# dịch vụ của prod.yml theo `COMPOSE_PROFILES` như compose thật (dịch vụ không profile + profile
# đang bật) — để kiểm `load_app_services` lọc đúng mà không cần CLI compose trong container verify
# (không có). `DOCKER_CONFIG_FAIL=1` làm lệnh hỏng. Tập dịch vụ thật được chốt riêng bằng test
# đọc prod.yml (`test_compose.py`), để bản giả không trôi khỏi compose.
FAKE_COMPOSE_SERVICES = """
if [[ "$1 $2 $3" == "compose config --services" ]]; then
  [[ "${DOCKER_CONFIG_FAIL:-0}" == "1" ]] && exit 1
  printf '%s\\n' postgres redis-broker redis-cache migrate api worker beat web
  [[ ",${COMPOSE_PROFILES:-}," == *",minio,"* ]] && printf '%s\\n' minio minio-init
  [[ ",${COMPOSE_PROFILES:-}," == *",ml,"* ]] && printf '%s\\n' ml ml-train
  exit 0
fi
"""


# `docker` giả của deploy.sh/rollback.sh: theo dõi tập container `api` bằng tệp trạng thái
# $DOCKER_STATE — chỉ để kiểm thứ tự lệnh (B0-10 [8]), không thay Docker thật. Mỗi `compose
# pull` ghi COMPOSE_FILE và COMPOSE_ENV_FILES vào $DOCKER_STATE/pull_env (kiểm bó/biến K-35).
# Núm: DOCKER_MIGRATE_EXIT, DOCKER_INFRA_EXIT (`up --wait` hạ tầng), DOCKER_UNHEALTHY,
# DOCKER_SCALE2_NO_NEW, DOCKER_CONFIG_FAIL (FAKE_COMPOSE_SERVICES).
FAKE_DOCKER = (
    FAKE_COMPOSE_SERVICES
    + """
state="$DOCKER_STATE"
mkdir -p "$state"
ids_file="$state/ids"
touch "$ids_file"

if [[ "$1 $2" == "compose pull" ]]; then
  printf '%s\\n%s\\n' "$COMPOSE_FILE" "$COMPOSE_ENV_FILES" >> "$state/pull_env"
  exit 0
fi
if [[ "$1 $2 $3 $4" == "compose run --rm migrate" ]]; then
  exit "${DOCKER_MIGRATE_EXIT:-0}"
fi
if [[ "$1 $2 $3 $4" == "compose ps -q api" ]]; then
  cat "$ids_file"
  exit 0
fi
if [[ "$1 $2 $3 $4" == "compose up -d --wait" ]]; then
  exit "${DOCKER_INFRA_EXIT:-0}"
fi
if [[ "$1 $2" == "compose up" ]]; then
  scale2=0
  next_is_scale=0
  for a in "$@"; do
    if [[ "$next_is_scale" == "1" ]]; then
      [[ "$a" == "api=2" ]] && scale2=1
      next_is_scale=0
    fi
    [[ "$a" == "--scale" ]] && next_is_scale=1
  done
  if [[ "$scale2" == "1" && "${DOCKER_SCALE2_NO_NEW:-0}" == "1" ]]; then
    exit 0
  fi
  if [[ "$scale2" == "1" ]]; then
    n=0
    [[ -f "$state/counter" ]] && n="$(cat "$state/counter")"
    n=$((n + 1))
    echo "$n" > "$state/counter"
    id="c$n"
    echo "$id" >> "$ids_file"
    if [[ "${DOCKER_UNHEALTHY:-0}" == "1" ]]; then
      echo "starting" > "$state/health_$id"
    else
      echo "healthy" > "$state/health_$id"
    fi
  fi
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  id="${*: -1}"
  cat "$state/health_$id" 2>/dev/null || echo "starting"
  exit 0
fi
if [[ "$1" == "rm" ]]; then
  id="${*: -1}"
  grep -vxF "$id" "$ids_file" > "$ids_file.tmp" 2>/dev/null || true
  mv "$ids_file.tmp" "$ids_file" 2>/dev/null || true
  rm -f "$state/health_$id"
  exit 0
fi
exit 0
"""
)


def fake_docker(tmp_path: Path, old_ids: Sequence[str] = ("old1",)) -> tuple[Path, Path]:
    """Đặt `FAKE_DOCKER` đầu PATH với container api cũ `old_ids`; trả (bin_dir, thư mục trạng thái)."""
    bin_dir = fake_bin(tmp_path / "bin", {"docker": FAKE_DOCKER})
    state = tmp_path / "docker-state"
    state.mkdir()
    (state / "ids").write_text("".join(f"{i}\n" for i in old_ids), encoding="utf-8")
    return bin_dir, state


def appback_dir(
    root: Path,
    *,
    running: str | None = None,
    releases: Sequence[str] = (),
    env_lines: str = "APP_ENV=test\n",
    with_scripts: bool = False,
) -> dict[str, str]:
    """Dựng cấu trúc máy đích K-35 dưới `root`, trả biến môi trường trỏ vào đó.

    `root/appback.env` (nội dung `env_lines`); `running` → `release.env` `IMAGE_TAG=<running>`
    + `current_tag`; mỗi tag trong `releases` → `releases/<tag>/deploy/compose/prod.yml` (tệp
    rỗng — docker giả không đọc) và, nếu `with_scripts`, bản chép `deploy/scripts` thật (để
    deploy.sh gọi đúng rollback.sh của bản trước). Trả `APPBACK_DIR`, `APPBACK_ENV_FILE`.
    """
    root.mkdir(parents=True, exist_ok=True)
    env_file = root / "appback.env"
    env_file.write_text(env_lines, encoding="utf-8")
    if running is not None:
        (root / "release.env").write_text(f"IMAGE_TAG={running}\n", encoding="utf-8")
        (root / "current_tag").write_text(running, encoding="utf-8")
    for tag in releases:
        deploy_dir = root / "releases" / tag / "deploy"
        (deploy_dir / "compose").mkdir(parents=True, exist_ok=True)
        (deploy_dir / "compose" / "prod.yml").write_text("", encoding="utf-8")
        if with_scripts:
            shutil.copytree(
                REPO_ROOT / "deploy" / "scripts", deploy_dir / "scripts", ignore=shutil.ignore_patterns("tests")
            )
    return {"APPBACK_DIR": str(root), "APPBACK_ENV_FILE": str(env_file)}
