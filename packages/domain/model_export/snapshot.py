"""Immutable export snapshot, its sha and the cache fingerprint (HOP-DONG-M1 §2). Pure, no DB."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = 1
SUGGESTION_KEYS = ("suggestions", "suggestionsMeta")
"""Khoá lớp đề xuất (HOP-DONG-DU-LIEU-LOP-DE-XUAT §2.1) chép từ document sang tầng của snapshot."""


def canonical_json(obj: Any) -> bytes:
    """Canonical UTF-8 JSON: sorted keys, no spaces, non-ASCII kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(obj: Any) -> str:
    """sha256 hex of canonical JSON."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def snapshot_sha(snapshot: Mapping[str, Any]) -> str:
    """sha256 of the whole canonical snapshot."""
    return _sha(snapshot)


def fingerprint(
    floors_meta: Iterable[Mapping[str, Any]],
    versions: Mapping[str, Any],
    fmt: str = "glb",
    scene_style: str | None = None,
) -> str:
    """sha256 over `[levelId, order, elevationMm, heightMm, hiddenIn3d, revision]` per floor
    (sorted by order) + versions + format + project style. Never reads documents: revision covers them.
    `scene_style=None` leaves the key out, so fingerprints without a style stay as before."""
    rows = sorted(
        (
            [f["levelId"], f["order"], f["elevationMm"], f["heightMm"], f["hiddenIn3d"], f["revision"]]
            for f in floors_meta
        ),
        key=lambda r: (r[1], r[0]),
    )
    payload: dict[str, Any] = {"floors": rows, "versions": dict(versions), "format": fmt}
    if scene_style is not None:
        payload["sceneStyle"] = scene_style
    return _sha(payload)


def suggestion_keys(document: Mapping[str, Any] | None) -> dict[str, Any]:
    """`suggestions` + `suggestionsMeta` của document đã lưu, chỉ khi lớp đang bật và danh sách không rỗng.

    Lớp tắt (`suggestionsEnabled` vắng/khác `true`) thì không đề xuất nào vào bản xuất dù writer giữ các bản đã lưu
    (§8 quyết định 1, không âm thầm dựng thứ người dùng đã tắt). Không có lớp → dict rỗng, nên tầng của snapshot và
    `snapshotSha` giữ nguyên như trước M2.
    Dấu vân không đổi: `revision` đã bao cả ba khoá (bật/tắt lớp là một lượt ghi tăng revision, §4)."""
    doc = document or {}
    if doc.get("suggestionsEnabled") is not True or not doc.get("suggestions"):
        return {}
    return {k: doc[k] for k in SUGGESTION_KEYS if doc.get(k) is not None}


def build_snapshot(
    project_id: str,
    floors: Iterable[Mapping[str, Any]],
    versions: Mapping[str, Any],
    scene_style: str | None = None,
) -> dict[str, Any]:
    """Snapshot dict (schemaVersion 1); floors already carry `layer` (+ `suggestion_keys` when present),
    sorted by order then levelId.
    `sceneStyle` = project style from settings (null = editor default style)."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "projectId": project_id,
        "floors": sorted((dict(f) for f in floors), key=lambda f: (f["order"], f["levelId"])),
        "versions": dict(versions),
        "sceneStyle": scene_style,
    }
