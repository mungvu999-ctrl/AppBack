"""CLI nạp gói vật liệu/mẫu vào kho (HOP-DONG-M1 §8).

`python -m apps.api.model_export.cli import-pack <dir>`: đọc `manifest.json`
(`{version, createdAt, sources, files: {đường tương đối: sha256}, missing: […]}`, §8), kiểm sha256 từng tệp, rồi ghi
`model-export-packs/{version}/…` (mọi tệp trong `files`, gồm `editor-defaults.json` nếu có, và `manifest.json`).
Sai sha hay thiếu tệp → không ghi gì, thoát 1. `missing[]` chỉ in ra (gói vẫn nạp).
Sau khi nạp, đặt `MODEL_EXPORT_PACK_VERSION=<version>` cho api/worker.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from packages.core.clock import SystemClock
from packages.storage.keys import model_export_pack_object
from packages.storage.port import ObjectStorage

MANIFEST = "manifest.json"
PACK_MAX_FILE_BYTES = 512 * 1024 * 1024


def verify_pack(root: Path) -> tuple[str, dict[str, Any]]:
    """Kiểm manifest + sha256; trả `(version, manifest)`; sai → `ValueError` nêu tệp hỏng."""
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    version = manifest["version"]
    bad = [
        rel
        for rel, digest in manifest["files"].items()
        if not (root / rel).is_file() or hashlib.sha256((root / rel).read_bytes()).hexdigest() != digest
    ]
    if bad:
        raise ValueError(f"tệp sai sha256 hoặc thiếu: {', '.join(sorted(bad))}")
    return version, manifest


async def import_pack(root: Path, storage: ObjectStorage) -> tuple[str, list[str]]:
    """Kiểm rồi ghi mọi tệp của gói vào kho; trả `(phiên bản, manifest.missing)`."""
    version, manifest = verify_pack(root)
    for rel in [*sorted(manifest["files"]), MANIFEST]:
        data = (root / rel).read_bytes()
        await storage.put(
            model_export_pack_object(version, rel),
            data,
            content_type="application/octet-stream",
            max_bytes=PACK_MAX_FILE_BYTES,
        )
    return version, [str(item) for item in manifest.get("missing", [])]


def _storage() -> ObjectStorage:
    """Kho object theo biến môi trường của tiến trình."""
    from packages.core.settings import get_core_settings
    from packages.storage.factory import create_storage
    from packages.storage.settings import get_storage_settings

    return create_storage(get_storage_settings(), get_core_settings(), SystemClock())


def main(argv: list[str] | None = None) -> int:
    """Điểm vào CLI; trả mã thoát."""
    parser = argparse.ArgumentParser(prog="model_export")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import-pack").add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    try:
        version, missing = asyncio.run(import_pack(args.dir, _storage()))
    except (ValueError, KeyError, OSError) as exc:
        sys.stderr.write(f"nạp gói thất bại: {exc}\n")
        return 1
    if missing:
        sys.stderr.write(f"gói thiếu {len(missing)} mục: {', '.join(missing)}\n")
    sys.stdout.write(f"đã nạp gói {version}; đặt MODEL_EXPORT_PACK_VERSION={version}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
