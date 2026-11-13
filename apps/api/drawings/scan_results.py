"""N49: kết quả mô hình đã có của lượt chạy mới nhất, cho hiệu ứng vẽ trên màn Xử lý.

Đọc thẳng `walls.json`/`objects.json` trong kho: artifact chỉ ghi khi bước xong nên thứ FE vẽ luôn
là kết quả cuối, không phải ứng viên từng lát. Bước chưa xong thì mảng của nó rỗng, không phải lỗi.
Toạ độ là pixel của trang đã nắn, cùng hệ với `sourceImageUrl`.
"""

import asyncio
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.wire import WireModel
from apps.api.drawings.uploads import UploadFacts
from packages.db.models.drawings import PipelineRunRow
from packages.ml_contracts.artifacts import ARTIFACT_JSON_MAX_BYTES, objects_from_json, walls_from_json
from packages.storage.keys import run_artifact
from packages.storage.port import ObjectStorage, read_all_capped


class _ArtifactNotReadyError(Exception):
    """Artifact chưa có hoặc quá trần: hiệu ứng bỏ qua bước đó, lượt chạy không bị ảnh hưởng."""


class ScanWallOut(WireModel):
    """Đường tim một tường."""

    x1: float
    y1: float
    x2: float
    y2: float


class ScanBoxOut(WireModel):
    """Hộp một vật phát hiện được."""

    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class ScanResultsOut(WireModel):
    """Kết quả các bước đã xong; bước chưa xong là mảng rỗng."""

    walls: list[ScanWallOut]
    boxes: list[ScanBoxOut]


async def _read[Result](
    storage: ObjectStorage, facts: UploadFacts, run_id: str, step: str, name: str, parse: Callable[[bytes], Result]
) -> Result | None:
    """Một artifact đã giải, hay `None` khi bước chưa ghi nó; lỗi tạm của kho vẫn nổi lên."""
    key = run_artifact(facts.project_id, facts.level_id, facts.id, run_id, step, name)
    try:
        data = await read_all_capped(
            storage,
            key,
            max_bytes=ARTIFACT_JSON_MAX_BYTES,
            too_large=_ArtifactNotReadyError,
            on_missing=_ArtifactNotReadyError,
        )
    except _ArtifactNotReadyError:
        return None
    return await asyncio.to_thread(parse, data)


async def read_scan_results(db: AsyncSession, storage: ObjectStorage, facts: UploadFacts) -> ScanResultsOut:
    """Tường và hộp của lượt chạy mới nhất; chưa có lượt nào thì rỗng."""
    stmt = (
        select(PipelineRunRow.id)
        .where(PipelineRunRow.upload_id == facts.id)
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
    )
    run_id = (await db.execute(stmt)).scalar_one_or_none()
    if run_id is None:
        return ScanResultsOut(walls=[], boxes=[])
    walls, objects = await asyncio.gather(
        _read(storage, facts, run_id, "wallSegmentation", "walls.json", walls_from_json),
        _read(storage, facts, run_id, "openingAndFurnitureDetection", "objects.json", objects_from_json),
    )
    return ScanResultsOut(
        walls=[]
        if walls is None
        else [ScanWallOut(x1=w.start.x, y1=w.start.y, x2=w.end.x, y2=w.end.y) for w in walls.walls],
        boxes=[]
        if objects is None
        else [
            ScanBoxOut(label=d.label, x_min=d.box.x_min, y_min=d.box.y_min, x_max=d.box.x_max, y_max=d.box.y_max)
            for d in objects.detections
        ],
    )
