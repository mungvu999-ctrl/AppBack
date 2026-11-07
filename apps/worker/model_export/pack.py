"""Gói vật liệu/mẫu của bộ xuất: tải từ kho về cache theo phiên bản, kiểm sha256 (HOP-DONG-M1 §8).

Bố cục kho `model-export-packs/{version}/…` (`model_export_pack_object`). `manifest.json` là bắt buộc —
thiếu → `MODEL_EXPORT_PACK_MISSING`. Bảng (`materials.json`, …) thiếu → bảng rỗng + bản ghi `major`.
Tệp ảnh/mẫu chỉ tải đúng những tệp plan dùng; tệp thiếu trong kho, ngoài manifest hay sai sha256 thì
không có mặt trong cache, và `compile_plan.bpy` ghi bản ghi `major` cho nó (không im lặng).
Ghi cache bằng tệp tạm + `os.replace`: hai worker cùng tải một tệp không làm hỏng nhau.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from apps.api.model_export.state import MODEL_EXPORT_PACK_MISSING
from packages.messaging import PermanentError
from packages.storage.keys import model_export_pack_object
from packages.storage.port import ObjectStorage

__all__ = ["PACK_TABLES", "Pack", "fetch_files", "load_pack", "plan_files", "prune_cache"]

MANIFEST: Final = "manifest.json"
PACK_TABLES: Final = {
    "materials": "materials.json",
    "sceneMaterials": "scene-materials.json",
    "models": "models.json",
    "furnitureDefaults": "furniture-defaults.json",
    "editorDefaults": "editor-defaults.json",
}
"""Khoá `pack` mà `build_plan` đọc → tên tệp trong gói."""


@dataclass
class Pack:
    """Gói đã nạp: thư mục cache, sha256 từng tệp theo manifest, bảng cho `build_plan`, bản ghi thiếu bảng."""

    version: str
    root: Path
    files: Mapping[str, str]
    tables: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, str]] = field(default_factory=list)


def _sha256(path: Path) -> str:
    """sha256 hex của tệp, đọc theo khúc."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _fetch(storage: ObjectStorage, key: str, dest: Path) -> bool:
    """Tải `key` về `dest` qua tệp tạm; `False` khi object không có."""
    if await storage.stat(key) is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    handle = await asyncio.to_thread(part.open, "wb")
    try:
        async for chunk in storage.open_read(key):
            await asyncio.to_thread(handle.write, chunk)
    finally:
        await asyncio.to_thread(handle.close)
    os.replace(part, dest)
    return True


async def _cached(storage: ObjectStorage, pack: Pack, rel: str) -> bool:
    """Tệp `rel` có trong cache và đúng sha256 của manifest (tải khi chưa có/sai); `False` nếu không được."""
    expected = pack.files.get(rel)
    path = pack.root / rel
    if expected is None or not path.resolve().is_relative_to(pack.root.resolve()):
        return False
    if path.is_file() and await asyncio.to_thread(_sha256, path) == expected:
        return True
    if not await _fetch(storage, model_export_pack_object(pack.version, rel), path):
        return False
    if await asyncio.to_thread(_sha256, path) != expected:
        path.unlink()
        return False
    return True


async def load_pack(storage: ObjectStorage, version: str, cache_dir: Path) -> Pack:
    """Manifest (luôn tải lại) + bốn bảng của gói `version`; thiếu manifest → `MODEL_EXPORT_PACK_MISSING`."""
    if not version:
        raise PermanentError(MODEL_EXPORT_PACK_MISSING)
    root = cache_dir / version
    if not await _fetch(storage, model_export_pack_object(version, MANIFEST), root / MANIFEST):
        raise PermanentError(MODEL_EXPORT_PACK_MISSING)
    manifest = json.loads((root / MANIFEST).read_bytes())
    pack = Pack(version=version, root=root, files=manifest.get("files") or {})
    for key, name in PACK_TABLES.items():
        if await _cached(storage, pack, name):
            pack.tables[key] = json.loads((root / name).read_bytes())
        else:
            pack.tables[key] = {}
            pack.records.append(
                {
                    "level": "",
                    "kind": "pack",
                    "id": name,
                    "status": "degraded",
                    "severity": "major",
                    "reason": f"gói xuất {version} thiếu bảng {name}",
                }
            )
    return pack


def plan_files(plan: Mapping[str, Any]) -> set[str]:
    """Đường tương đối trong gói mà plan dùng: ảnh của vật liệu + tệp mẫu của instance."""
    rels = {path for spec in plan.get("materials", {}).values() for path in (spec.get("maps") or {}).values() if path}
    return rels | {inst["model"] for inst in plan.get("instances", [])}


async def fetch_files(storage: ObjectStorage, pack: Pack, rels: Iterable[str]) -> list[str]:
    """Đưa từng tệp vào cache; trả các tệp không lấy được (sắp tăng) để ghi log."""
    return sorted([rel for rel in rels if not await _cached(storage, pack, rel)])


def prune_cache(cache_dir: Path, keep: str, max_bytes: int) -> None:
    """Cache vượt `max_bytes` → xoá thư mục các phiên bản khác `keep`.

    ponytail: xoá cả phiên bản chứ không LRU từng tệp — gói đổi phiên bản hiếm, đủ cho M1.
    """
    sizes = {p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) for p in cache_dir.iterdir() if p.is_dir()}
    if sum(sizes.values()) <= max_bytes:
        return
    for path in sizes:
        if path.name != keep:
            with contextlib.suppress(OSError):
                shutil.rmtree(path)
