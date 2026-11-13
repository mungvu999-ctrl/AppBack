"""Danh mục mẫu nội thất từ tệp `.glb` thật (ngoài git): manifest `files.json` cạnh module này.

Tệp nhị phân không nằm trong repo (luật danh mục: không commit tệp nhị phân); manifest chỉ giữ
danh tính, nguồn, giấy phép và `sha256` của tệp đã chuẩn hoá. CLI `import-files` đọc tệp từ một
thư mục, so `sha256` với manifest rồi phát hành. Nạp và kiểm một lần lúc nhập module.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from packages.domain.library.catalogue import CATALOGUE, LIBRARY_GROUPS, is_item_id

MANIFEST: Final = Path(__file__).with_name("files.json")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_URL_RE: Final = re.compile(r"https?://[^\s\"'()<>;,]+")
_TITLE_RE: Final = re.compile(r'"([^"]+)"')
# Giấy phép có bản cố định; "CC-BY" không ghi bản (bản demo Blender) trỏ trang danh mục giấy phép CC.
_LICENSE_URLS: Final = {
    "CC-BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY 3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC-BY": "https://creativecommons.org/licenses/",
}
MODIFIED_NOTE: Final = "đã chỉnh sửa: chuyển đổi, chuẩn hoá tệp GLB"


@dataclass(frozen=True)
class FileItem:
    """Một mẫu từ tệp: id, tên tiếng Việt, nhóm, giấy phép, tác giả (có thể rỗng), nguồn, `sha256` tệp."""

    id: str
    name: str
    group: str
    license: str
    author: str
    source: str
    model_sha256: str

    @property
    def attribution(self) -> str:
        """Ghi công một dòng, các phần nối bằng `" · "`.

        Mẫu CC BY (TASL, NO-437): `[“tên gốc”] · <ai> · <giấy phép> · [URL giấy phép] · <URL nguồn>… · đã chỉnh sửa …`
        (manifest giữ sha của tệp đã chuẩn hoá nên mọi mẫu từ tệp đều đã chỉnh sửa).
        Giấy phép khác: `"<ai> · <giấy phép>"`; không có tác giả thì suy từ nguồn, không suy được thì chỉ giấy phép.
        Phần là URL thì FE hiện thành liên kết.
        """
        if self.author:
            who = self.author
        elif "polyhaven.com" in self.source:
            who = "Poly Haven"
        elif self.source.startswith("Pascal"):
            who = "Pascal"
        else:
            return self.license
        if not self.license.startswith("CC-BY"):
            return f"{who} · {self.license}"
        title = _TITLE_RE.search(self.source)
        parts = [
            *([f"“{title.group(1)}”"] if title else []),
            who,
            self.license,
            *([_LICENSE_URLS[self.license]] if self.license in _LICENSE_URLS else []),
            *_URL_RE.findall(self.source),
            MODIFIED_NOTE,
        ]
        return " · ".join(parts)


def load_file_catalogue(path: Path = MANIFEST) -> tuple[FileItem, ...]:
    """Đọc và kiểm manifest: id hợp lệ, không trùng nhau hay trùng `CATALOGUE`, nhóm hợp lệ, sha 64 hex."""
    items = tuple(FileItem(**entry) for entry in json.loads(path.read_text(encoding="utf-8")))
    taken = {item.id for item in CATALOGUE}
    for item in items:
        if not is_item_id(item.id):
            raise ValueError(f"id sai mẫu: {item.id!r}")
        if item.id in taken:
            raise ValueError(f"id trùng: {item.id!r}")
        taken.add(item.id)
        if item.group not in LIBRARY_GROUPS:
            raise ValueError(f"nhóm lạ {item.group!r} của {item.id!r}")
        if not _SHA256_RE.fullmatch(item.model_sha256):
            raise ValueError(f"sha256 sai của {item.id!r}")
    return items


FILE_CATALOGUE: Final = load_file_catalogue()
