"""Tiện ích HTTP của test route #35 (không phải file test, pytest không thu thập).

Bốn thứ lặp ở mọi file test route: đường của #35, thân `{baseVersion, body}` dạng dây, một
lượt `PUT` với người cụ thể, và đếm dòng `idempotency_records` (route này tắt idempotency nên
"đúng 0 dòng" là một khẳng định thật, không phải chỗ trống).

Dựng dự án/tầng dùng `make_scene` của B3-02, header dùng `headers_of` của B2-01 — không có bản
thứ hai ở đây. Gọi `write_layer` trực tiếp thì dùng `write` của `_helpers.py`.
"""

from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.projects.tests.test_routes_common import headers_of
from packages.db.models.auth import User
from packages.db.models.idempotency import IdempotencyRecord
from packages.domain.spatial import Furniture, SpatialLayer


def layer_path(project_id: str, floor_id: str) -> str:
    """`/api/projects/{project_id}/floors/{floor_id}/spatial/layer`."""
    return f"/api/projects/{project_id}/floors/{floor_id}/spatial/layer"


def wire(model: BaseModel) -> dict[str, Any]:
    """Dạng dây của một mô hình miền (lớp hay một mục) — đúng cái FE gửi lên (camelCase, vắng khoá thay `null`)."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def write_body(base: int, *, layer: SpatialLayer | None = None, scale: float | None = None) -> dict[str, Any]:
    """Thân #35 hợp lệ; khoá nào `None` thì vắng hẳn, để `body: {}` phải dựng bằng tay."""
    body: dict[str, Any] = {}
    if layer is not None:
        body["layer"] = wire(layer)
    if scale is not None:
        body["scaleMillimetresPerPixel"] = scale
    return {"baseVersion": base, "body": body}


async def put_layer(
    client: httpx.AsyncClient,
    project_id: str,
    floor_id: str,
    user: User,
    payload: dict[str, Any],
    **kwargs: Any,
) -> httpx.Response:
    """Một lượt `PUT` #35 với người `user`; `kwargs` đi thẳng vào httpx (ví dụ `headers` thêm)."""
    headers = {**headers_of(user), **kwargs.pop("headers", {})}
    return await client.put(layer_path(project_id, floor_id), json=payload, headers=headers, **kwargs)


async def idempotency_count(db: AsyncSession) -> int:
    """Số dòng `idempotency_records` — route #35 khai `idempotency="off"` nên luôn phải là 0."""
    return int((await db.execute(select(func.count()).select_from(IdempotencyRecord))).scalar_one())


def make_furniture(
    level_id: str, *, entity_id: str = "F-ROUTEFURN1", room_id: str | None = None, source: str = "ai"
) -> Furniture:
    """Một cái bàn 800 x 600 mm; `room_id=None` là mẫu C17 (vắng khoá `roomId` trên dây).

    `_helpers.py` của việc W không có mục đồ đạc nào — C09 (xoá mềm một mục) và C17 cần nó.
    """
    return Furniture.model_validate(
        {
            "id": entity_id,
            "levelId": level_id,
            **({} if room_id is None else {"roomId": room_id}),
            "kind": "table",
            "centre": {"x": 400, "y": 300},
            "boundingBox": {"min": {"x": 0, "y": 0}, "max": {"x": 800, "y": 600}},
            "rotationDeg": 0.0,
            "confidence": 1.0 if source == "human" else 0.8,
            "source": source,
            "reviewed": False,
        }
    )


def fitted(layer: SpatialLayer) -> SpatialLayer:
    """Lớp mẫu với mọi ô mở đặt lọt tường 1000 mm (offset 50, rộng ≤ 900).

    Toà mẫu (`samples.py`, đồng bộ với FE) đặt cửa 300+900 và cửa sổ 500+1200 trên tường 1000 mm —
    vượt đầu tường, nên PUT nó như ô mở **mới** bị H4 chặn (HOP-DONG-M1 §7).
    """
    openings = tuple(
        item.model_copy(update={"offset_mm": 50, "width_mm": min(item.width_mm, 900)}) for item in layer.openings
    )
    return layer.model_copy(update={"openings": openings})
