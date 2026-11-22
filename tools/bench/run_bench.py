"""Chạy bộ bench-3d trên stack f14, LẦN LƯỢT từng bản vẽ (mỗi bản một dự án riêng, một tầng).

python -X utf8 -m tools.bench.run_bench <tên-lượt> [--group A,B] [--only ch-03,cn-07] [--min-free-gb 3]

Cấu hình qua biến môi trường (xem `tools/bench/config.py`): BENCH_ROOT, BENCH_API, BENCH_ORIGIN, BENCH_SECRETS.

- Kết quả: runs/<tên-lượt>/<id>/{progress,layer,spatial,quality}.json + meta.json; stack.json chụp phiên bản stack.
- Dừng giữa chừng (Ctrl+C) rồi gọi lại cùng tên-lượt: bỏ qua ca đã có meta.json, chạy tiếp ca còn lại.
- Trước mỗi ca chờ tới khi RAM trống >= --min-free-gb và hàng đợi Celery rỗng (không chen việc phiên khác).
- `--calibrate` (M5): ca xong mà readiness có `scaleUnresolved` và đáp án có mm/px -> đặt tỉ lệ đáp án như màn "Đặt tỷ
  lệ" rồi lấy lại kết quả vào <id>/calibrated/. `--calibrate-only`: chỉ làm bước đó cho ca đã chạy.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.bench import config
from tools.bench.api import BenchError, Session, expect
from tools.bench.calibrate import Api, calibrate
from tools.bench.console import say
from tools.bench.score.io import Bench, Json
from tools.bench.stack import free_gb, queues_busy, snapshot

CHUNK = 5 * 1024 * 1024
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}
CASE_TIMEOUT_S = 15 * 60
POLL_S = 2  # hỏi tiến độ mỗi 2 s (10 s làm mỗi ca chờ thừa tới 10 s)
ARTIFACTS = (
    ("layer", "/floors/{f}/spatial/layer"),
    ("spatial", "/floors/{f}/spatial"),
    ("quality", "/floors/{f}/quality"),
)


@dataclass
class RunContext:
    """Một lượt chạy: tên, thư mục dữ liệu, thư mục kết quả; `sleep`/`now` thay được để test không chờ thật."""

    name: str
    root: Path
    out: Path
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    fetch: Callable[[str], bytes] = field(default_factory=lambda: _download)


def _download(url: str) -> bytes:
    """Tải một URL ký tạm (ảnh trang đã cắt) trong 60 s."""
    expect(url.startswith(("http://", "https://")), f"URL lạ: {url!r}")
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - đã kiểm lược đồ
        data: bytes = r.read()
    return data


def _write_json(path: Path, data: Any) -> None:
    """Ghi JSON UTF-8 thụt 2 (giữ dấu tiếng Việt)."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _create_project(api: Api, ctx: RunContext, cid: str) -> tuple[str, str]:
    """Tạo dự án một tầng cho ca; trả (projectId, floorId)."""
    floors = [{"name": cid, "order": 0, "elevationMm": 0, "heightMm": 3300}]
    s, project = api("POST", "/projects", {"name": f"bench-3d {ctx.name} {cid}", "floors": floors})
    expect(s == 201, f"{cid}: tạo dự án {s} {project}")
    pid = project["id"]
    s, fl = api("GET", f"/projects/{pid}/floors")
    return pid, (fl["items"] if isinstance(fl, dict) else fl)[0]["id"]


def _upload(api: Api, cid: str, plan: Path, pid: str, fid: str) -> str:
    """Tải bản vẽ lên theo từng khúc 5 MB rồi chốt; trả uploadId."""
    blob = plan.read_bytes()
    mime = MIME[plan.suffix.lower()]
    init_body = {"fileName": plan.name, "floorId": fid, "mimeType": mime, "projectId": pid, "sizeBytes": len(blob)}
    s, init = api("POST", f"/projects/{pid}/floors/{fid}/drawings/uploads", init_body)
    expect(s in (200, 201), f"{cid}: khởi tạo tải lên {s} {init}")
    uid: str = init["id"]
    for i in range(0, len(blob), CHUNK):
        chunk = {"chunk": base64.b64encode(blob[i : i + CHUNK]).decode(), "chunkIndex": i // CHUNK}
        s, r = api("POST", f"/projects/{pid}/drawings/uploads/{uid}/chunks", chunk)
        expect(s in (200, 201, 202), f"{cid}: khúc {i // CHUNK} {s} {r}")
    s, r = api("POST", f"/projects/{pid}/drawings/uploads/{uid}/complete", {"uploadId": uid})
    expect(s in (200, 201, 202), f"{cid}: chốt tải lên {s} {r}")
    return uid


def _poll(api: Api, ctx: RunContext, pid: str, uid: str) -> tuple[str, Any]:
    """Chờ lượt xử lý xong (`completed`/`failed`) tối đa 15 phút; trả (trạng thái, tiến độ cuối); quá hạn: `timeout`."""
    t_up, prog = ctx.now(), None
    while ctx.now() - t_up < CASE_TIMEOUT_S:
        ctx.sleep(POLL_S)
        _, prog = api("GET", f"/projects/{pid}/drawings/uploads/{uid}/progress")
        if prog and prog.get("status") in ("completed", "failed"):
            return prog["status"], prog
    return "timeout", prog


def _save_page(ctx: RunContext, d: Path, spatial: Json | None) -> None:
    """Tải ảnh trang đã cắt khung (URL ký tạm, tải ngay) thành page.png: score dùng để dò độ lệch so với ảnh gốc."""
    for drw in (spatial or {}).get("drawings") or []:
        try:
            (d / "page.png").write_bytes(ctx.fetch(drw["url"]))
        except (OSError, BenchError) as e:
            say(f"  không tải được ảnh trang: {e}")
        break


def _save_artifacts(api: Api, ctx: RunContext, cid: str, d: Path, pid: str, fid: str) -> None:
    """Lưu layer/spatial/quality. Lỗi -> ném trước khi ghi meta.json nên lần chạy sau làm lại ca này."""
    for name, path in ARTIFACTS:
        s, body = api("GET", f"/projects/{pid}" + path.format(f=fid))
        expect(s == 200, f"{cid}: {name} {s}")
        _write_json(d / f"{name}.json", {"status": s, "body": body})
        if name == "spatial":
            _save_page(ctx, d, body)


def run_case(api: Api, ctx: RunContext, row: dict[str, str]) -> Json:
    """Chạy một ca: tạo dự án, tải bản vẽ, chờ xử lý, lưu kết quả; meta.json ghi SAU CÙNG (= ca đã xong)."""
    cid = row["id"]
    d = ctx.out / cid
    d.mkdir(exist_ok=True)
    t0 = ctx.now()
    pid, fid = _create_project(api, ctx, cid)
    uid = _upload(api, cid, ctx.root / "plans" / row["file"], pid, fid)
    t_up = ctx.now()
    status, prog = _poll(api, ctx, pid, uid)
    _write_json(d / "progress.json", prog)
    _save_artifacts(api, ctx, cid, d, pid, fid)
    meta = {
        "id": cid, "file": row["file"], "group": row["group"], "projectId": pid, "floorId": fid, "uploadId": uid,
        "status": status, "upload_s": round(t_up - t0, 1), "process_s": round(ctx.now() - t_up, 1),
    }  # fmt: skip
    _write_json(d / "meta.json", meta)
    return meta


def select_cases(bench: Bench, groups: str, only: str) -> list[dict[str, str]]:
    """Các dòng manifest thuộc nhóm `groups` (phẩy) và (nếu `only` không rỗng) nằm trong `only`."""
    wanted = set(filter(None, only.split(",")))
    return [r for r in bench.manifest.values() if r["group"] in groups.split(",") and (not wanted or r["id"] in wanted)]


def _read_secrets(path: Path) -> dict[str, str]:
    """Đọc tệp `KEY=VALUE` (bỏ dòng không có `=`)."""
    return dict(line.strip().split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def _wait_for_stack(min_free_gb: float, sleep: Callable[[float], None]) -> None:
    """Chờ tới khi RAM trống >= `min_free_gb` và hàng đợi Celery rỗng."""
    while (g := free_gb()) < min_free_gb or queues_busy():
        say(f"  chờ: RAM trống {g:.1f} GB, hàng đợi {queues_busy()}")
        sleep(10)


def _run_all(api: Api, ctx: RunContext, a: argparse.Namespace, bench: Bench, todo: list[dict[str, str]]) -> None:
    """Chạy tuần tự các ca còn lại; một ca hỏng không dừng cả lượt (chạy lại sau sẽ thử ca đó lần nữa)."""
    for n, row in enumerate(todo, 1):
        _wait_for_stack(a.min_free_gb, ctx.sleep)
        try:
            m = run_case(api, ctx, row)
            say(f"[{n}/{len(todo)}] {m['id']}: {m['status']} {m['process_s']} s")
            if a.calibrate and m["status"] == "completed":
                say(f"  đặt tỉ lệ: {calibrate(api, bench, row, ctx.out)}")
        except (BenchError, OSError, KeyError, ValueError, TimeoutError) as e:
            say(f"[{n}/{len(todo)}] {row['id']}: LỖI {e!r}")


def _finish_snapshot(out: Path) -> None:
    """Cuối lượt chụp lại stack; khác đầu lượt (container khởi động lại, vá nóng mới) -> cảnh báo + `stack-end.json`."""
    snap = snapshot()
    if json.loads((out / "stack.json").read_text(encoding="utf-8"))["containers"] != snap["containers"]:
        say("CẢNH BÁO: stack đổi giữa lượt (container khởi động lại / vá nóng mới) - xem stack-end.json")
        _write_json(out / "stack-end.json", snap)


def _parser() -> argparse.ArgumentParser:
    """Tham số dòng lệnh."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.run_bench", description=__doc__)
    ap.add_argument("run")
    ap.add_argument("--group", default="A,B,C,D")
    ap.add_argument("--only", default="")
    ap.add_argument("--min-free-gb", type=float, default=3.0)
    ap.add_argument("--calibrate", action="store_true", help="đặt tỉ lệ đáp án khi scaleUnresolved (M5)")
    ap.add_argument("--calibrate-only", action="store_true", help="chỉ đặt tỉ lệ cho ca đã có meta.json")
    return ap


def main(
    argv: Sequence[str] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> int:
    """Điểm vào: dựng phiên, chọn ca, chạy (hoặc chỉ đặt tỉ lệ), chụp stack đầu/cuối lượt. Trả mã thoát."""
    a = _parser().parse_args(argv)
    root = config.bench_root()
    out = root / "runs" / a.run
    out.mkdir(parents=True, exist_ok=True)
    env = _read_secrets(config.bench_secrets())
    bench = Bench(root, out)
    ctx = RunContext(a.run, root, out, sleep, now)
    rows = select_cases(bench, a.group, a.only)
    todo = [r for r in rows if not (out / r["id"] / "meta.json").exists()]
    email, password = env["E2E_ADMIN_EMAIL"].strip(), env["E2E_ADMIN_PASSWORD"].strip()
    api = Session(config.bench_api(), config.bench_origin(), email, password, sleep=sleep)
    if a.calibrate_only:
        for row in rows:
            if (out / row["id"] / "meta.json").exists() and not (
                out / row["id"] / "calibrated" / "layer.json"
            ).exists():
                say(f"{row['id']}: {calibrate(api, bench, row, out)}")
        return 0
    say(f"{len(rows)} ca, còn {len(todo)}")
    _write_json(out / "stack.json", snapshot())
    _run_all(api, ctx, a, bench, todo)
    _finish_snapshot(out)
    say("xong")
    return 0


if __name__ == "__main__":
    sys.exit(main())
