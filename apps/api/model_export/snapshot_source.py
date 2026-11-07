"""Đọc tầng + document + phong cách dự án thành snapshot bất biến (HOP-DONG-M1 §2).

Một câu `SELECT` duy nhất: một dòng gốc mang `project_settings.scene_style`, nối trái với
`floors` + `floor_documents`. Một câu lệnh Postgres luôn đọc trên một ảnh nhất quán, nên mọi tầng,
mọi document và phong cách cùng một thời điểm mà không phải đổi mức cô lập của giao dịch cổng
quyền đã mở. Dự án chưa có tầng vẫn ra đúng một dòng (cột tầng NULL) để giữ `scene_style`.
`current_fingerprint` chỉ đọc cột `revision`, không đọc jsonb.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.model_export.snapshot import SCHEMA_VERSION, build_snapshot, fingerprint, snapshot_sha

_COLUMNS: Final = (
    "s.scene_style, f.level_id, f.name, f.floor_order, f.elevation_mm, f.height_mm, f.hidden_in_3d, "
    "COALESCE(d.revision, 0) AS revision"
)
_FROM: Final = (
    " FROM (SELECT (SELECT scene_style FROM project_settings WHERE project_id = :p) AS scene_style) s "
    "LEFT JOIN (floors f LEFT JOIN floor_documents d ON d.floor_pk = f.pk) "
    "ON f.project_id = :p AND f.deleted_at IS NULL ORDER BY f.floor_order, f.level_id"
)
_FULL: Final = text(f"SELECT {_COLUMNS}, d.document{_FROM}")
_META: Final = text(f"SELECT {_COLUMNS}{_FROM}")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Snapshot đã dựng cùng dấu vân, sha và revision/tên từng tầng."""

    body: dict[str, Any]
    fingerprint: str
    sha: str
    revisions: list[dict[str, Any]]


def versions_of(compiler: str, pack: str) -> dict[str, Any]:
    """Khối `versions` của snapshot và dấu vân."""
    return {"compiler": compiler, "pack": pack, "schema": SCHEMA_VERSION}


def _meta(row: Mapping[Any, Any]) -> dict[str, Any]:
    """Một dòng SQL → phần meta của tầng trong snapshot (camelCase)."""
    return {
        "levelId": row["level_id"],
        "name": row["name"],
        "order": row["floor_order"],
        "elevationMm": row["elevation_mm"],
        "heightMm": row["height_mm"],
        "hiddenIn3d": row["hidden_in_3d"],
        "revision": row["revision"],
    }


def _floor_rows(rows: Sequence[Mapping[Any, Any]]) -> list[Mapping[Any, Any]]:
    """Bỏ dòng gốc không có tầng (dự án chưa có tầng nào)."""
    return [row for row in rows if row["level_id"] is not None]


def _layer_of(document: Mapping[str, Any] | None) -> dict[str, Any]:
    """Lớp không gian của document đã lưu (`codec.document_to_json`: `{layer, axes, dimensions}`); chưa có → rỗng."""
    layer = (document or {}).get("layer")
    return dict(layer) if isinstance(layer, Mapping) else {}


async def take(db: AsyncSession, project_id: str, versions: Mapping[str, Any]) -> Snapshot:
    """Dựng snapshot đầy đủ (kèm `layer` của từng tầng; tầng chưa có document → lớp rỗng)."""
    rows = (await db.execute(_FULL, {"p": project_id})).mappings().all()
    scene_style = rows[0]["scene_style"]
    floors = [{**_meta(row), "layer": _layer_of(row["document"])} for row in _floor_rows(rows)]
    body = build_snapshot(project_id, floors, versions, scene_style=scene_style)
    return Snapshot(
        body=body,
        fingerprint=fingerprint(floors, versions, scene_style=scene_style),
        sha=snapshot_sha(body),
        revisions=[{"levelId": f["levelId"], "levelName": f["name"], "revision": f["revision"]} for f in floors],
    )


async def current_fingerprint(db: AsyncSession, project_id: str, versions: Mapping[str, Any]) -> str:
    """Dấu vân hiện tại của dự án (gồm phong cách) — không đọc document."""
    rows = (await db.execute(_META, {"p": project_id})).mappings().all()
    return fingerprint([_meta(row) for row in _floor_rows(rows)], versions, scene_style=rows[0]["scene_style"])
