"""Task xuất GLB phía máy chủ: snapshot → plan.json → Blender → `model.glb` + `report.json` + `CREDITS.txt` (M1-01).

Luồng: `claim` (`None` = giao lặp/đã `superseded`, no-op) → đọc `snapshot.json` → nạp gói theo
`versions.pack` → `build_plan` → tải tệp gói plan dùng → Blender CPU trong tiến trình con có session riêng
(không giữ `gpu:0`) → kiểm `model.glb` + `geometry.json` → `quality_of` → ghi tệp → `finish`.
`finish` trả `False` (lượt đã bị thay) → xoá tiền tố của lượt, không ghi đè gì. Hỏng → `on_failed`
ghi `fail` rồi xoá tiền tố. Session ngắn, không giữ khi chạm storage hay chờ Blender (K22, K36).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import struct
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.model_export.state import (
    FAILURE_CODES,
    MODEL_EXPORT_FAILED,
    MODEL_EXPORT_OUTPUT_INVALID,
    MODEL_EXPORT_TIMEOUT,
    claim,
    fail,
    finish,
)
from apps.worker.lighting_bake.tasks import file_chunks
from apps.worker.model_export.blender import blender_argv
from apps.worker.model_export.pack import fetch_files, load_pack, plan_files, prune_cache
from apps.worker.model_export.settings import get_worker_settings
from packages.core.clock import Clock, SystemClock
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.db.engine import session_scope, worker_sessionmaker
from packages.domain.model_export.build import build_plan
from packages.domain.model_export.quality import quality_of
from packages.messaging import PermanentError, define_task, runner
from packages.messaging.payloads.model_export import (
    MODEL_EXPORT_SNAPSHOT,
    MODEL_EXPORT_TASK,
    ModelExportPayload,
)
from packages.messaging.tasks import TASK_TIMEOUT
from packages.storage.keys import model_export_object, model_export_prefix
from packages.storage.port import ObjectStorage

__all__ = ["ArgvFor", "compile_model_export", "fail_model_export", "geometry_records", "run_model_export"]

_log: Final = logging.getLogger(__name__)

ArgvFor = Callable[[Path, Path, Path], list[str]]
"""`(plan.json, thư mục gói, thư mục ra) -> argv`; test thay Blender bằng `tests/fake_blender.py`."""

BuildPlan = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]

CRITICAL_KINDS: Final = frozenset(("wall", "slab", "floor", "ceiling"))
"""Tường, sàn kết cấu, sàn hoàn thiện, trần: hở hoặc suy biến → `critical` (§4.6); kiến trúc khác → `major`."""
SEVERITY_RANK: Final = {"critical": 0, "major": 1, "minor": 2, "info": 3}
SUMMARY_RECORDS: Final = 50
REPORT_MAX_BYTES: Final = 1024 * 1024
LOG_TAIL_BYTES: Final = 2048
PASCAL_LICENSE: Final = "LICENSE-PASCAL-MIT.txt"
"""Nguyên văn giấy phép MIT của mẫu Pascal trong gói; MIT đòi kèm văn bản này khi phân phối mẫu."""
_ENV_KEEP: Final = ("PATH", "LANG", "LC_ALL", "PYTHONPATH")
"""Không DSN, không khoá S3: Blender chỉ thấy thư mục tạm (như LB-01)."""


def _child_env(tmp: Path) -> dict[str, str]:
    """Môi trường tối thiểu, `HOME=TMPDIR=tmp`, ẩn GPU (dựng hình học chỉ cần CPU)."""
    env = {name: os.environ[name] for name in _ENV_KEEP if name in os.environ}
    env["HOME"] = env["TMPDIR"] = str(tmp)
    env["CUDA_VISIBLE_DEVICES"] = ""
    return env


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """Giết cả nhóm tiến trình của Blender để không bỏ lại con mồ côi."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


def _log_tail(log_path: Path, export_id: str, returncode: int | None) -> None:
    """Ghi `LOG_TAIL_BYTES` byte cuối của log Blender khi lượt hỏng."""
    data = log_path.read_bytes()[-LOG_TAIL_BYTES:]
    _log.warning(
        "model_export_blender_failed",
        extra={"export_id": export_id, "returncode": returncode, "tail": data.decode("utf-8", "replace")},
    )


async def _spawn(argv: list[str], tmp: Path, *, timeout_s: float, export_id: str) -> None:
    """Chạy Blender; quá hạn → `MODEL_EXPORT_TIMEOUT`, thoát ≠ 0 → `MODEL_EXPORT_FAILED`.

    `-SIGKILL` mà cha không giết là OOM của cgroup: lỗi tạm `DEPENDENCY_UNAVAILABLE` (như LB-01).
    """
    log_path = tmp / "blender.log"
    with log_path.open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            env=_child_env(tmp),
            start_new_session=True,
        )
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except TimeoutError:
            _kill_group(proc)
            await proc.wait()
            _log_tail(log_path, export_id, None)
            raise PermanentError(MODEL_EXPORT_TIMEOUT) from None
        except BaseException:
            _kill_group(proc)
            await asyncio.shield(proc.wait())
            raise
    if returncode == 0:
        return
    _log_tail(log_path, export_id, returncode)
    if returncode == -signal.SIGKILL:
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=5)
    raise PermanentError(MODEL_EXPORT_FAILED)


def check_glb(path: Path, max_bytes: int) -> int:
    """Kích thước của `model.glb` hợp lệ: magic `glTF`, version 2, độ dài header khớp tệp, khúc đầu là JSON
    parse được, trong trần byte. Sai → `MODEL_EXPORT_OUTPUT_INVALID`."""
    size = path.stat().st_size if path.is_file() else 0
    if not 20 <= size <= max_bytes:
        raise PermanentError(MODEL_EXPORT_OUTPUT_INVALID)
    with path.open("rb") as handle:
        magic, version, length, chunk_len, chunk_type = struct.unpack("<4sIII4s", handle.read(20))
        if (magic, version, length, chunk_type) != (b"glTF", 2, size, b"JSON") or chunk_len > size - 20:
            raise PermanentError(MODEL_EXPORT_OUTPUT_INVALID)
        try:
            json.loads(handle.read(chunk_len))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PermanentError(MODEL_EXPORT_OUTPUT_INVALID) from None
    return size


def _read_geometry(path: Path) -> dict[str, Any]:
    """`geometry.json` của Blender; thiếu/hỏng/sai dạng → `MODEL_EXPORT_OUTPUT_INVALID`."""
    try:
        geometry = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise PermanentError(MODEL_EXPORT_OUTPUT_INVALID) from None
    if not isinstance(geometry, dict) or not isinstance(geometry.get("open"), list):
        raise PermanentError(MODEL_EXPORT_OUTPUT_INVALID)
    return geometry


def geometry_records(geometry: Mapping[str, Any]) -> list[dict[str, str]]:
    """Bản ghi từ kiểm hình học: đối tượng hở / có mặt suy biến; tường-sàn-trần → `critical`, khác → `major`."""
    out = []
    for item in geometry.get("open", []):
        reason = f"{item['name']} hở: {item['boundary']} cạnh biên, {item['nonManifold']} cạnh không-manifold"
        out.append(_geometry_record(item, reason))
    for item in geometry.get("degenerateObjects", []):
        out.append(_geometry_record(item, f"{item['name']} có {item['faces']} mặt suy biến"))
    return out


def _geometry_record(item: Mapping[str, Any], reason: str) -> dict[str, str]:
    """Một bản ghi `geometry` cho đối tượng `item`; hình của đề xuất M2 (`suggestionId`) → `suggestion:<rule>`
    `minor`: đề xuất hỏng không kéo bản xuất về `degraded` (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.5)."""
    if item.get("suggestionId"):
        return {
            "level": "",
            "kind": f"suggestion:{item.get('rule')}",
            "id": item["suggestionId"],
            "status": "degraded",
            "severity": "minor",
            "reason": reason,
        }
    severity = "critical" if item.get("kind") in CRITICAL_KINDS else "major"
    return {
        "level": "",
        "kind": "geometry",
        "id": item.get("appId") or item["name"],
        "status": "degraded",
        "severity": severity,
        "reason": reason,
    }


def _credits(plan: Mapping[str, Any], skipped: set[str], mit_text: str | None = None) -> str:
    """`CREDITS.txt`: mỗi mẫu/vật liệu có giấy phép một dòng `tên: tác giả · giấy phép · nguồn`.

    Có mẫu MIT được dựng và gói có `LICENSE-PASCAL-MIT.txt` → nối nguyên văn giấy phép vào cuối.
    """
    lines = set()
    mit = False
    for inst in plan.get("instances", []):
        props = inst.get("props") or {}
        if inst["name"] not in skipped and props.get("license"):
            lines.add(f"- {inst['model']}: {props.get('author', '')} · {props['license']} · {props.get('source', '')}")
            mit = mit or "MIT" in props["license"]
    for key, spec in plan.get("materials", {}).items():
        if spec.get("license"):
            lines.add(f"- {key}: {spec.get('author', '')} · {spec['license']} · {spec.get('source', '')}")
    body = "\n".join(["Nguồn mẫu và vật liệu trong tệp:", *sorted(lines)]) + "\n"
    if mit and mit_text:
        body += f"\nGiấy phép MIT của mẫu Pascal ({PASCAL_LICENSE}):\n\n{mit_text.rstrip()}\n"
    return body


def _report(
    plan: Mapping[str, Any], records: list[dict[str, str]], geometry: Mapping[str, Any], stats: dict[str, Any]
) -> dict[str, Any]:
    """`report.json` (§5); bản ghi vượt trần 1 MiB bị cắt, giữ bản nặng nhất trước."""
    counts: dict[str, int] = {}
    for rec in records:
        counts[f"{rec['kind']}:{rec['status']}"] = counts.get(f"{rec['kind']}:{rec['status']}", 0) + 1
    ordered = sorted(records, key=lambda r: SEVERITY_RANK.get(r["severity"], 9))
    stamps = plan.get("stamps", {})
    report = {
        "snapshotSha": stamps.get("snapshotSha"),
        "fingerprint": stamps.get("fingerprint"),
        "compiler": stamps.get("compiler"),
        "pack": stamps.get("pack"),
        "quality": quality_of(records),
        "counts": dict(sorted(counts.items())),
        "records": ordered,
        "geometry": {
            "objects": geometry.get("objects", 0),
            "open": [o["name"] for o in geometry["open"]],
            "degenerate": geometry.get("degenerate", 0),
        },
        "stats": {"tris": geometry.get("tris", 0), **stats},
    }
    while len(json.dumps(report, ensure_ascii=False).encode()) > REPORT_MAX_BYTES:
        report["records"] = report["records"][: len(report["records"]) // 2]
    return report


async def _read_object(storage: ObjectStorage, key: str) -> bytes:
    """Đọc trọn một object nhỏ (`snapshot.json`)."""
    return b"".join([chunk async for chunk in storage.open_read(key)])


async def _put(storage: ObjectStorage, key: str, path: Path, content_type: str, max_bytes: int) -> None:
    """Ghi tệp lên kho theo luồng."""
    await storage.put(key, file_chunks(path), content_type=content_type, max_bytes=max_bytes)


async def compile_model_export(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    clock: Clock,
    *,
    export_id: str,
    argv_for: ArgvFor | None = None,
    timeout_s: float | None = None,
    build: BuildPlan = build_plan,
) -> None:
    """Một lượt xuất đầy đủ (lõi của task); `argv_for`/`timeout_s`/`build` chỉ để test thay ranh giới."""
    async with session_scope(sessionmaker) as db:
        project_id = await claim(db, export_id, clock)
    if project_id is None:
        _log.info("model_export_not_claimable", extra={"export_id": export_id})
        return
    settings = get_worker_settings()
    cache_dir = Path(settings.model_export_pack_cache_dir)
    build_argv = argv_for or (lambda plan, pack, out: blender_argv(settings.blender_bin, plan, pack, out))
    started = time.monotonic()
    snapshot = json.loads(
        await _read_object(storage, model_export_object(project_id, export_id, MODEL_EXPORT_SNAPSHOT))
    )
    pack = await load_pack(storage, str((snapshot.get("versions") or {}).get("pack", "")), cache_dir)
    plan = build(snapshot, pack.tables)
    missing = await fetch_files(storage, pack, plan_files(plan))
    licence = pack.root / PASCAL_LICENSE
    mit_text = None if await fetch_files(storage, pack, [PASCAL_LICENSE]) else licence.read_text(encoding="utf-8")
    if missing:
        _log.warning("model_export_pack_files_missing", extra={"export_id": export_id, "files": missing[:20]})
    max_bytes = settings.model_export_max_output_bytes
    with tempfile.TemporaryDirectory(prefix="model-export-") as tmp_name:
        tmp = Path(tmp_name)
        plan_path, out = tmp / "plan.json", tmp / "out"
        out.mkdir()
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        await _spawn(
            build_argv(plan_path, pack.root, out),
            tmp,
            timeout_s=timeout_s or settings.model_export_timeout_s,
            export_id=export_id,
        )
        glb_bytes = check_glb(out / "model.glb", max_bytes)
        geometry = _read_geometry(out / "geometry.json")
        blender_records = list(geometry.get("records") or [])
        records = list(plan.get("records", [])) + pack.records + blender_records + geometry_records(geometry)
        stats = {"glbBytes": glb_bytes, "seconds": round(time.monotonic() - started, 2)}
        report = _report(plan, records, geometry, stats)
        skipped = {r["id"] for r in blender_records if r.get("status") == "skipped"}
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        (out / "CREDITS.txt").write_text(_credits(plan, skipped, mit_text), encoding="utf-8")
        blocked = report["quality"] == "blocked"
        files = {"report.json": "application/json", "CREDITS.txt": "text/plain; charset=utf-8"}
        if not blocked:  # N51 không phục vụ GLB của lượt `blocked`: khỏi tốn kho
            files["model.glb"] = "model/gltf-binary"
        for name, content_type in files.items():
            await _put(storage, model_export_object(project_id, export_id, name), out / name, content_type, max_bytes)
    prune_cache(cache_dir, pack.version, settings.model_export_pack_cache_max_bytes)
    summary = {"counts": report["counts"], "records": report["records"][:SUMMARY_RECORDS]}
    async with session_scope(sessionmaker) as db:
        done = await finish(
            db,
            export_id,
            clock,
            quality=report["quality"],
            report_summary=summary,
            glb_bytes=None if blocked else glb_bytes,
        )
    if not done:
        # Lượt đã `superseded`/`failed` trong lúc dựng: object vừa ghi không dòng nào dùng.
        _log.warning("model_export_finish_skipped", extra={"export_id": export_id})
        await storage.delete_prefix(model_export_prefix(project_id, export_id))


async def _project_of(sessionmaker: async_sessionmaker[AsyncSession], export_id: str) -> str | None:
    """Dự án của lượt xuất, `None` khi dòng không còn (dự án đã xoá, CASCADE)."""
    async with sessionmaker() as db:
        found = await db.execute(text("SELECT project_id FROM model_exports WHERE id = :id"), {"id": export_id})
        project_id: str | None = found.scalar_one_or_none()
        return project_id


async def fail_model_export(
    sessionmaker: async_sessionmaker[AsyncSession], storage: ObjectStorage, clock: Clock, *, export_id: str, code: str
) -> None:
    """Chốt `failed` rồi xoá cả tiền tố của lượt — xoá **sau** commit; `fail` trả `False` → không xoá gì.

    Mã chung của `define_task` không thuộc `FAILURE_CODES` của bảng: `TASK_TIMEOUT` → `MODEL_EXPORT_TIMEOUT`,
    còn lại → `MODEL_EXPORT_FAILED`.
    """
    if code not in FAILURE_CODES:
        code = MODEL_EXPORT_TIMEOUT if code == TASK_TIMEOUT else MODEL_EXPORT_FAILED
    project_id = await _project_of(sessionmaker, export_id)
    async with session_scope(sessionmaker) as db:
        failed = await fail(db, export_id, code, clock)
    if failed and project_id is not None:
        await storage.delete_prefix(model_export_prefix(project_id, export_id))


def open_storage(clock: Clock) -> ObjectStorage:
    """Kho thật của tiến trình worker, nhập trễ (khuôn `apps/worker/lighting_bake/tasks.py`)."""
    from packages.storage.factory import create_storage
    from packages.storage.settings import get_storage_settings

    return create_storage(get_storage_settings(), None, clock)


async def _fail_with_process_resources(export_id: str, code: str) -> None:
    """Chốt `failed` bằng tài nguyên thật, dựng **trong** vòng sự kiện của `runner()`."""
    clock = SystemClock()
    await fail_model_export(worker_sessionmaker(), open_storage(clock), clock, export_id=export_id, code=code)


def _on_failed(payload: ModelExportPayload, code: str) -> None:
    """`on_failed` của `define_task`: đánh `failed` và dọn tiền tố trên vòng sự kiện dùng chung."""
    _log.error("model_export_failed", extra={"export_id": payload.export_id, "code": code})
    runner().run(_fail_with_process_resources(payload.export_id, code))


@define_task(name=MODEL_EXPORT_TASK, payload=ModelExportPayload, on_failed=_on_failed)
async def run_model_export(payload: ModelExportPayload) -> None:
    """Hàm task mỏng: dựng tài nguyên thật rồi gọi lõi `compile_model_export`."""
    clock = SystemClock()
    await compile_model_export(worker_sessionmaker(), open_storage(clock), clock, export_id=payload.export_id)
