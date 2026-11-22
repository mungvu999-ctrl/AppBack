"""Chạy lại phần SAU ML (dựng + trộn + quality + readiness) bằng mã của một worktree AppBack trên ml-cache/.

python -X utf8 -m tools.bench.replay.replay <worktree> <thư mục ra> [--set bench|F|all] [--jobs 4] [--cases a b ...]
    [--local]

Ra <ra>/<ca>/{layer,spatial,quality,progress,meta}.json + page.png: cùng dạng `run_bench` nên `python -m
tools.bench.score <ra>` chấm được (tập F: `BENCH_ROOT` trỏ vào gốc tập F). Thêm replay.json (trạng thái, giờ),
built-layer.json (layer.json bước dựng), run-quality.json (quality.json của pipeline_quality).

Không tái tạo (lấy nguyên từ lượt gốc, vì nằm TRƯỚC ML hoặc ngoài BE): quality.json của API (đánh giá ảnh),
spatial.json, meta.json (process_s của lượt gốc), probe.json/3d*.png (cảnh AppFront - không chép, M0/F1-4 thành "-").
Mã chạy trong container `BENCH_WORKER_IMAGE` (mặc định `appback-worker:f14`: thư viện của ảnh f14; worktree đổi
uv.lock thì phải dựng ảnh), --network none, worktree mount chỉ đọc ở /tmp/w. `--local` (CI, K-17b): chạy `inner.py`
bằng chính trình Python đang chạy (venv `uv sync --all-packages` của worktree), không cần docker.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tools.bench import config
from tools.bench.console import say, warn

INNER = Path(__file__).with_name("inner.py")
_COPY_ALWAYS = ("spatial.json", "quality.json")
_EMPTY_LAYER = {
    "status": 200,
    "body": {
        "revision": 0, "level": {"id": None}, "readiness": {"state": "notEvaluated", "reasons": []},
        "layer": {"walls": [], "openings": [], "rooms": [], "furniture": []},
    },
}  # fmt: skip


def cases_of(orig_run: Path, cache: Path) -> list[str]:
    """Ca của lượt gốc `orig_run` có đủ `ctx.json` trong cache, theo thứ tự tên."""
    return sorted(p.parent.name for p in orig_run.glob("*/meta.json") if (cache / p.parent.name / "ctx.json").exists())


def docker_command(worktree: Path, cache: Path, out: Path, image: str, cases: list[str]) -> list[str]:
    """Lệnh `docker run` chạy `inner.py` cho `cases`: không mạng, worktree/cache mount chỉ đọc, `out` ghi được."""
    return [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{worktree.as_posix()}:/tmp/w:ro", "-v", f"{cache.as_posix()}:/cache:ro",
        "-v", f"{out.as_posix()}:/out", "-v", f"{INNER.as_posix()}:/inner.py:ro",
        "-w", "/tmp/w", "-e", "PYTHONPATH=/tmp/w", "-e", "PYTHONDONTWRITEBYTECODE=1",  # noqa: S108 - đường trong container
        image, "python", "/inner.py", *cases,
    ]  # fmt: skip


def local_command(worktree: Path, cache: Path, out: Path, cases: list[str]) -> tuple[list[str], dict[str, str]]:
    """(lệnh, biến môi trường thêm) chạy `inner.py` bằng trình Python hiện tại, đọc mã của `worktree` (không docker)."""
    env = {
        "PYTHONPATH": str(worktree), "PYTHONDONTWRITEBYTECODE": "1", "BENCH_REPLAY_CACHE": str(cache),
        "BENCH_REPLAY_OUT": str(out),
    }  # fmt: skip
    return [sys.executable, str(INNER), *cases], env


def run_container(
    cmd: list[str], extra_env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.Popen[str]:
    """Chạy một container replay (hoặc `inner.py` cục bộ); stdout+stderr gộp để in từng dòng."""
    return subprocess.Popen(  # noqa: S603 - lệnh dựng từ `docker_command`/`local_command`, không qua shell
        cmd, env={**os.environ, "MSYS_NO_PATHCONV": "1", **(extra_env or {})}, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", cwd=cwd,
    )  # fmt: skip


def _copy_if(src: Path, dst: Path) -> bool:
    """Chép `src` sang `dst` nếu có; trả đã chép hay chưa."""
    if src.exists():
        shutil.copy(src, dst)
        return True
    return False


def _failed_original(d: Path, orig: Path) -> None:
    """Lượt gốc hỏng ở ML (không có gì để dựng lại): giữ kết quả gốc."""
    for n in ("layer.json", "progress.json"):
        _copy_if(orig / n, d / n)


def _replayed(d: Path, res: dict[str, Any], meta: dict[str, Any]) -> None:
    """Ghi `progress.json` theo kết quả replay (và layer rỗng khi replay hỏng mà không có layer)."""
    status = res["status"]
    prog = {
        "status": status,
        "step": "qualityCheck" if status == "completed" else "spatialDataBuild",
        "progressPercent": 100,
    }
    if res.get("error"):
        prog["error"] = res["error"]
    meta["status"] = status
    (d / "progress.json").write_text(json.dumps(prog, ensure_ascii=False, indent=1), encoding="utf-8")
    if status != "completed" and not (d / "layer.json").exists():
        (d / "layer.json").write_text(json.dumps(_EMPTY_LAYER), encoding="utf-8")


def finish(out: Path, cid: str, orig: Path, cache: Path) -> None:
    """Hoàn thiện thư mục ra của một ca: chép tệp lấy từ lượt gốc, ghi progress/meta theo `replay.json`."""
    d = out / cid
    res = json.loads((d / "replay.json").read_text(encoding="utf-8"))
    for n in _COPY_ALWAYS:
        _copy_if(orig / n, d / n)
    if not _copy_if(cache / cid / "page.png", d / "page.png"):
        _copy_if(orig / "page.png", d / "page.png")
    meta = json.loads((orig / "meta.json").read_text(encoding="utf-8"))
    if res.get("replayed"):
        _replayed(d, res, meta)
    else:
        _failed_original(d, orig)
    meta["replay"] = {k: res.get(k) for k in ("replayed", "status", "error", "replay_s")}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    """Tham số dòng lệnh."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.replay.replay", description=__doc__)
    ap.add_argument("worktree", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--set", choices=["bench", "F", "all"], default="all")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--cases", nargs="*")
    ap.add_argument("--local", action="store_true", help="chạy inner.py bằng Python hiện tại, không docker (CI)")
    return ap


def _start(a: argparse.Namespace, cache: Path, out: Path, cases: list[str]) -> subprocess.Popen[str]:
    """Khởi một tiến trình replay cho `cases`: container `BENCH_WORKER_IMAGE`, hoặc cục bộ khi `--local`."""
    if a.local:
        cmd, env = local_command(a.worktree.resolve(), cache.resolve(), out.resolve(), cases)
        return run_container(cmd, env, cwd=a.worktree.resolve())  # cùng thư mục làm việc như `-w /tmp/w`
    return run_container(docker_command(a.worktree.resolve(), cache, out.resolve(), config.bench_worker_image(), cases))


def main(argv: Sequence[str] | None = None) -> int:
    """Điểm vào: chia ca của mỗi tập cho `--jobs` container, đợi xong rồi `finish` từng ca. Trả mã thoát."""
    a = _parser().parse_args(argv)
    root = config.bench_root()
    cache = config.ml_cache(root)
    sets = config.replay_sets(root)
    names = ["bench", "F"] if a.set == "all" else [a.set]
    plan = [(s, c) for s in names for c in cases_of(sets[s][0], cache) if not a.cases or c in a.cases]
    outs = {s: (a.out / s if a.set == "all" else a.out) for s in names}
    for o in outs.values():
        o.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    procs = []
    for s in names:  # chia đều các ca của mỗi tập cho --jobs container
        cs = [c for s2, c in plan if s2 == s]
        k = max(1, min(a.jobs, len(cs)))
        procs += [_start(a, cache, outs[s], cs[i::k]) for i in range(k) if cs[i::k]]
    for p in procs:
        for line in p.stdout or []:
            sys.stdout.write(line)
        p.wait()
    for s, c in plan:
        if (outs[s] / c / "replay.json").exists():
            finish(outs[s], c, sets[s][0] / c, cache)
        else:
            warn(f"!! {c}: không có replay.json")
    say(f"{len(plan)} ca, {time.monotonic() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
