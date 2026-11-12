"""Model response của thư viện .glb (B2-06 [2]); khớp `LibraryItemSchema` strict của FE.

Không có `furnitureKind`, `ownerId`, `publishedAt` (K01). `preview_url` là `None` thì
`WireModel` bỏ hẳn khoá (W2): FE coi `previewUrl: null` là sai schema.
"""

from typing import Literal

from apps.api.core.wire import WireModel

type LibraryGroup = Literal["table", "chair", "bed", "sofa", "storage", "sanitary", "kitchen", "technical", "decor"]
type LibrarySource = Literal["mine", "catalogue"]


class LibraryItemOut(WireModel):
    """Một mẫu nội thất đã phát hành; số đo đo từ chính tệp `.glb`."""

    id: str
    name: str
    group: LibraryGroup
    source: LibrarySource
    width_mm: int
    depth_mm: int
    height_mm: int
    triangle_count: int
    file_size_bytes: int
    model_url: str
    preview_url: str | None = None
    attribution: str | None = None  # `FileItem.attribution` (CC BY: đủ TASL); mẫu dựng bằng mã thì vắng
