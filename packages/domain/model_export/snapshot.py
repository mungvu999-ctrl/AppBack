"""Immutable export snapshot, its sha and the cache fingerprint (HOP-DONG-M1 §2). Pure, no DB."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = 1


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


def build_snapshot(
    project_id: str,
    floors: Iterable[Mapping[str, Any]],
    versions: Mapping[str, Any],
    scene_style: str | None = None,
) -> dict[str, Any]:
    """Snapshot dict (schemaVersion 1); floors already carry `layer`, sorted by order then levelId.
    `sceneStyle` = project style from settings (null = editor default style)."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "projectId": project_id,
        "floors": sorted((dict(f) for f in floors), key=lambda f: (f["order"], f["levelId"])),
        "versions": dict(versions),
        "sceneStyle": scene_style,
    }
