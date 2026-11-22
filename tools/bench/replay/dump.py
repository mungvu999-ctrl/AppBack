"""Tải một lần đầu ra ML + dữ kiện DB của các lượt bench về ml-cache/<ca>/ (chỉ đọc f14).

python -X utf8 -m tools.bench.replay.dump [--runs runs/r4b tap-f/runs/f2] [--force]

Mỗi ca: ctx.json (payload bước dựng: width/height/page_key/px_per_paper_mm/fallback/detectFurniture, floor height,
confidenceThreshold, pinned, quality_summary, trạng thái lượt) + mọi object dưới runs/<run>/ (walls.json, walls.png,
objects.json, text.json, vectors.json nếu có, layer.json + quality.json thật để so) + page.png (trang đã cắt =
page_key).
Chạy script `INNER` trong f14-api-1 qua `docker exec -i` (stdin), chỉ SELECT + GET; kết quả về stdout dạng
JSON-lines base64. `--runs` tính tương đối `BENCH_ROOT`.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tools.bench import config
from tools.bench.console import warn

# Script chạy TRONG f14-api-1 (có asyncpg, minio và biến môi trường kết nối); nằm ở dạng chuỗi vì nó không chạy ở đây.
INNER = r"""
import os, sys, json, base64, asyncio
import asyncpg
from minio import Minio
c = Minio("minio:9000", access_key=os.environ["S3_ACCESS_KEY"], secret_key=os.environ["S3_SECRET_KEY"],
          secure=False)
cases = json.loads(sys.stdin.readline())
def out(**k): sys.stdout.write(json.dumps(k) + "\n"); sys.stdout.flush()
def enc(b): return base64.b64encode(b).decode()
def js(v): return json.loads(v) if isinstance(v, str) else v
SQL_FLOOR = "select pk, height_mm from floors where project_id=$1 and level_id=$2"
SQL_DRAWING = ("select page_key, width_px, height_px, px_per_paper_mm, upload_id from drawings "
               "where floor_pk=$1")
SQL_RUN = ("select id, status, error_code, current_step from pipeline_runs where floor_pk=$1 and "
           "upload_id=$2 order by created_at desc, id desc limit 1")
SQL_SETTINGS = ("select confidence_threshold, default_scale_mm_per_px, detect_furniture "
                "from project_settings where project_id=$1")
SQL_MODEL = "select pinned, quality_summary from pipeline_run_models where run_id=$1"
DRAWING_KEYS = ("page_key", "width_px", "height_px", "px_per_paper_mm", "upload_id")
async def context(db, pid, fid, uid, fl):
    dr = await db.fetchrow(SQL_DRAWING, fl["pk"])
    run = await db.fetchrow(SQL_RUN, fl["pk"], uid)
    st = await db.fetchrow(SQL_SETTINGS, pid)
    pm = await db.fetchrow(SQL_MODEL, run["id"]) if run else None
    qs = None if pm is None or pm["quality_summary"] is None else js(pm["quality_summary"])
    settings = None if st is None else {
        "confidenceThreshold": str(st["confidence_threshold"]),
        "defaultScaleMmPerPx": str(st["default_scale_mm_per_px"]),
        "detectFurniture": st["detect_furniture"]}
    ctx = {"projectId": pid, "levelId": fid, "uploadId": uid, "floorHeightMm": fl["height_mm"],
           "drawing": None if dr is None else {k: dr[k] for k in DRAWING_KEYS},
           "run": None if run is None else dict(run), "settings": settings,
           "pinned": None if pm is None else js(pm["pinned"]), "qualitySummary": qs}
    return ctx, dr, run
async def main():
    db = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    for case, pid, fid, uid in cases:
        fl = await db.fetchrow(SQL_FLOOR, pid, fid)
        if fl is None:
            out(case=case, err="nofloor")
            continue
        ctx, dr, run = await context(db, pid, fid, uid, fl)
        out(case=case, name="ctx.json", data=enc(json.dumps(ctx, ensure_ascii=False, indent=1).encode()))
        if run is not None:
            pre = f"projects/{pid}/floors/{fid}/uploads/{uid}/runs/{run['id']}/"
            for o in c.list_objects("appback", prefix=pre, recursive=True):
                data = c.get_object("appback", o.object_name).read()
                out(case=case, name="run/" + o.object_name[len(pre):], data=enc(data))
        if dr is not None:
            out(case=case, name="page.png", data=enc(c.get_object("appback", dr["page_key"]).read()))
    await db.close()
asyncio.run(main())
"""


def pending_cases(root: Path, runs: list[str], cache: Path, force: bool) -> list[list[str]]:
    """Ca của các lượt `runs` (tương đối `root`) cần tải: có `floorId` và (khi không `force`) chưa có `ctx.json`."""
    cases = []
    for r in runs:
        for m in sorted((root / r).glob("*/meta.json")):
            meta = json.loads(m.read_text(encoding="utf-8"))
            cid = m.parent.name
            if not meta.get("floorId") or (not force and (cache / cid / "ctx.json").exists()):
                continue
            cases.append([cid, meta["projectId"], meta["floorId"], meta["uploadId"]])
    return cases


def _store(cache: Path, rec: dict[str, Any]) -> bool:
    """Ghi một bản ghi JSON-lines vào cache; bản ghi lỗi (`err`) chỉ cảnh báo. Trả đã ghi tệp hay chưa."""
    if "err" in rec:
        warn(str(rec))
        return False
    f = cache / rec["case"] / rec["name"]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(base64.b64decode(rec["data"]))
    return True


def fetch(cases: list[list[str]], cache: Path) -> tuple[int, int]:
    """Chạy `INNER` trong f14-api-1 cho `cases` và ghi từng tệp nhận được; trả (số tệp ghi, mã thoát docker)."""
    p = subprocess.Popen(  # noqa: S603 - lệnh cố định; dữ liệu qua stdin
        ["docker", "exec", "-i", "f14-api-1", "python", "-c", INNER],  # noqa: S607
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )  # fmt: skip
    if p.stdin is not None:
        p.stdin.write(json.dumps(cases) + "\n")
        p.stdin.close()
    n = sum(_store(cache, json.loads(line)) for line in p.stdout or [])
    p.wait()
    return n, p.returncode


def main(argv: Sequence[str] | None = None) -> int:
    """Điểm vào: tải các ca còn thiếu vào `ml-cache/`. Trả mã thoát của docker (0 khi không có gì để tải)."""
    ap = argparse.ArgumentParser(prog="python -m tools.bench.replay.dump", description=__doc__)
    ap.add_argument("--runs", nargs="*", default=["runs/r4b", "tap-f/runs/f2"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    root = config.bench_root()
    cache = config.ml_cache(root)
    cases = pending_cases(root, a.runs, cache, a.force)
    warn(f"{len(cases)} ca cần tải")
    if not cases:
        return 0
    n, rc = fetch(cases, cache)
    warn(f"ghi {n} tệp, mã thoát {rc}")
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
