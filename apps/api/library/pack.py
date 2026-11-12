"""Gói mẫu thư viện tái tạo được (M15): gom `<id>.glb`/`<id>.png` của `FILE_CATALOGUE` từ nhiều thư mục nguồn.

Tệp nhị phân ở ngoài git; nguồn có phiên bản là `files.json` (ghim `model_sha256` từng id). `build_pack` chỉ ghi
khi **mọi** id có bản `.glb` khớp sha ở một nguồn — thiếu hay lệch là hỏng cả lượt, không gói dở. Gói ra dùng
cho `import-files` (dịch vụ `migrate`, `LIBRARY_PACK_DIR`), thay cho `docker cp` tay.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from packages.domain.library import FILE_CATALOGUE, FileItem


@dataclass(frozen=True, slots=True)
class PackReport:
    """Kết quả `build_pack`: số mục đã chép (0 khi hỏng), số ảnh đi kèm, id thiếu tệp, id chỉ có bản lệch sha."""

    copied: int = 0
    previews: int = 0
    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()


def _sha256(path: Path) -> str:
    """`sha256` của tệp, đọc theo khúc (R-22: gói ~900 MB)."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _locate(item: FileItem, sources: tuple[Path, ...]) -> tuple[Path | None, bool]:
    """`(thư mục nguồn đầu tiên có <id>.glb khớp sha, đã gặp bản lệch sha)`."""
    seen_other = False
    for source in sources:
        glb = source / f"{item.id}.glb"
        if glb.is_file():
            if _sha256(glb) == item.model_sha256:
                return source, seen_other
            seen_other = True
    return None, seen_other


def build_pack(out: Path, sources: tuple[Path, ...], catalogue: tuple[FileItem, ...] = FILE_CATALOGUE) -> PackReport:
    """Kiểm hết rồi mới chép `<id>.glb` (+ `<id>.png` cùng thư mục nếu có) vào `out`; hỏng thì không ghi gì."""
    found: dict[str, Path] = {}
    missing: list[str] = []
    mismatched: list[str] = []
    for item in catalogue:
        where, seen_other = _locate(item, sources)
        if where is not None:
            found[item.id] = where
        else:
            (mismatched if seen_other else missing).append(item.id)
    if missing or mismatched:
        return PackReport(missing=tuple(missing), mismatched=tuple(mismatched))
    out.mkdir(parents=True, exist_ok=True)
    previews = 0
    for item_id, source in found.items():
        shutil.copyfile(source / f"{item_id}.glb", out / f"{item_id}.glb")
        png = source / f"{item_id}.png"
        if png.is_file():
            shutil.copyfile(png, out / f"{item_id}.png")
            previews += 1
    return PackReport(copied=len(found), previews=previews)
