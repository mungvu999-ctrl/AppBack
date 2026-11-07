"""Nghiệp vụ N49-N51 (HOP-DONG-M1 §6). SQL thuần trên bảng `model_exports`.

N49 theo thứ tự hợp đồng: dựng snapshot + dấu vân → dòng sống cùng dấu vân thì trả 200 (cache) →
lượt `queued|running` khác thành `superseded` → ghi `snapshot.json` vào kho, chèn dòng `queued`,
`send_task` sau commit → 202. Hai yêu cầu đua cùng dấu vân: unique partial
`uq_model_exports_fingerprint` cho một lượt thắng; lượt thua xoá object của mình và trả dòng thắng.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from apps.api.core.auth import Principal
from apps.api.model_export import snapshot_source, state
from apps.api.model_export.errors import MODEL_EXPORT_NOT_READY, export_not_found
from apps.api.model_export.schemas import ModelExport
from apps.api.model_export.settings import ModelExportSettings
from packages.core.clock import Clock
from packages.core.ids import is_id, new_id
from packages.db.hooks import on_after_commit
from packages.domain.model_export.snapshot import canonical_json
from packages.domain.model_export.version import compiler_version, normalized_bytes
from packages.messaging import send_task
from packages.messaging.payloads.model_export import (
    MODEL_EXPORT_FILES,
    MODEL_EXPORT_SNAPSHOT,
    MODEL_EXPORT_TASK,
    ModelExportPayload,
)
from packages.storage.keys import model_export_object
from packages.storage.port import ObjectStorage, content_disposition

CACHE_CONTROL: Final = "private, max-age=600"
GLB_NAME: Final = "model.glb"

_SELECT: Final = (
    "SELECT id, status, quality, fingerprint, snapshot_revisions, report_summary, created_at, started_at, "
    "ended_at, failure_code, glb_bytes FROM model_exports "
)
_LIVE: Final = text(_SELECT + "WHERE project_id = :p AND fingerprint = :fp AND status IN ('queued', 'running', 'done')")
_LATEST: Final = text(
    _SELECT + "WHERE project_id = :p AND status <> 'superseded' ORDER BY created_at DESC, id DESC LIMIT 1"
)
_INSERT: Final = text(
    "INSERT INTO model_exports (id, project_id, created_by, fingerprint, snapshot_sha, snapshot_revisions, "
    "compiler_version, pack_version, status, created_at, updated_at) VALUES (:id, :p, :by, :fp, :sha, "
    "CAST(:revs AS jsonb), :compiler, :pack, 'queued', :now, :now)"
)
_FILE: Final = text("SELECT status, quality FROM model_exports WHERE id = :id AND project_id = :p")


def _issues(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Gom `records` của `report_summary` theo (mức, lý do), đếm số bản ghi."""
    records = (summary or {}).get("records", [])
    grouped = Counter((r.get("severity", ""), r.get("reason", "")) for r in records)
    return [{"severity": s, "reason": r, "count": n} for (s, r), n in grouped.items()]


def _wire(row: Row[tuple[object, ...]], current: str) -> ModelExport:
    """Một dòng `model_exports` + dấu vân hiện tại → `ModelExport`."""
    data = row._asdict()
    summary = data.pop("report_summary")
    return ModelExport.model_validate(
        {
            **data,
            "stale": data["fingerprint"] != current,
            "current_fingerprint": current,
            "snapshot_revisions": [
                {"level_id": r["levelId"], "level_name": r["levelName"], "revision": r["revision"]}
                for r in data["snapshot_revisions"]
            ],
            "counts": (summary or {}).get("counts", {}),
            "issues": _issues(summary),
        }
    )


def _compiler(settings: ModelExportSettings) -> str:
    """Phiên bản bộ biên dịch: ghim tay qua cấu hình, không thì suy từ mã (đổi mã → cache cũ hết hiệu lực).

    Mã đọc ảnh chụp (`snapshot_source.py`) cũng quyết định đầu ra, nên băm cả nó vào: lỗi đọc document trên f14
    (07/11) từng để lại một lượt sai mà cache trả lại mãi vì dấu vân không đổi.
    """
    if settings.model_export_compiler_version:
        return settings.model_export_compiler_version
    reader = hashlib.sha256(normalized_bytes(Path(snapshot_source.__file__))).hexdigest()[:8]
    return f"{compiler_version()}.{reader}"


def _versions(settings: ModelExportSettings) -> dict[str, Any]:
    """`versions` theo cấu hình đang chạy."""
    return snapshot_source.versions_of(_compiler(settings), settings.model_export_pack_version)


async def start_export(
    *,
    db: AsyncSession,
    storage: ObjectStorage,
    project_id: str,
    principal: Principal,
    clock: Clock,
    settings: ModelExportSettings,
) -> tuple[ModelExport, int]:
    """N49 — trả `(lượt, mã HTTP)`: 200 khi đã có lượt sống cùng dấu vân, 202 khi mở lượt mới; tự `commit`."""
    snap = await snapshot_source.take(db, project_id, _versions(settings))
    existing = (await db.execute(_LIVE, {"p": project_id, "fp": snap.fingerprint})).first()
    if existing is not None:
        return _wire(existing, snap.fingerprint), 200
    await state.supersede(db, project_id, clock)
    export_id = new_id("mex", clock)
    key = model_export_object(project_id, export_id, MODEL_EXPORT_SNAPSHOT)
    body = canonical_json(snap.body)
    await storage.put(key, body, content_type="application/json", max_bytes=settings.model_export_snapshot_max_bytes)
    params = {
        "id": export_id,
        "p": project_id,
        "by": principal.user_id,
        "fp": snap.fingerprint,
        "sha": snap.sha,
        "revs": json.dumps(snap.revisions, ensure_ascii=False),
        "compiler": _compiler(settings),
        "pack": settings.model_export_pack_version,
        "now": clock.now(),
    }
    try:
        await db.execute(_INSERT, params)
        on_after_commit(db, lambda: send_task(MODEL_EXPORT_TASK, ModelExportPayload(export_id=export_id)))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await storage.delete(key)
        winner = (await db.execute(_LIVE, {"p": project_id, "fp": snap.fingerprint})).one()
        return _wire(winner, snap.fingerprint), 200
    except BaseException:
        await storage.delete(key)
        raise
    row = (await db.execute(_LIVE, {"p": project_id, "fp": snap.fingerprint})).one()
    return _wire(row, snap.fingerprint), 202


async def read_latest(db: AsyncSession, *, project_id: str, settings: ModelExportSettings) -> ModelExport:
    """N50 — lượt mới nhất không `superseded`, kèm `stale`; chưa có → 404 `modelExport`."""
    row = (await db.execute(_LATEST, {"p": project_id})).first()
    if row is None:
        raise export_not_found()
    return _wire(row, await snapshot_source.current_fingerprint(db, project_id, _versions(settings)))


async def download_file(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, export_id: str, file_name: str
) -> StreamingResponse:
    """N51 — stream một tệp kết quả; lượt dự án khác/id sai/tên lạ → 404, chưa sẵn sàng → 409.

    `model.glb` đòi `done` và chất lượng khác `blocked`; `report.json`, `CREDITS.txt` đòi `done`.
    """
    if file_name not in MODEL_EXPORT_FILES or not is_id("mex", export_id):
        raise export_not_found()
    row = (await db.execute(_FILE, {"id": export_id, "p": project_id})).first()
    if row is None:
        raise export_not_found()
    if row.status != "done" or (file_name == GLB_NAME and row.quality == "blocked"):
        raise MODEL_EXPORT_NOT_READY.error()
    key = model_export_object(project_id, export_id, file_name)
    info = await storage.stat(key)
    if info is None:
        raise export_not_found()
    return StreamingResponse(
        storage.open_read(key),
        media_type=info.content_type,
        headers={"Content-Disposition": content_disposition("attachment", file_name), "Cache-Control": CACHE_CONTROL},
    )
