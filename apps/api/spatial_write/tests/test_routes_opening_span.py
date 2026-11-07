"""H4 (HOP-DONG-M1 §7) của #35 `spatial_write_layer`: ô mở vượt đầu tường → 422 `VALIDATION`.

Chỉ ô mở bị chạm trong lượt ghi mới bị kiểm; dữ liệu cũ đã vượt (cấy qua đường `merge`, đường
không đi qua phép kiểm của thân) vẫn ghi được khi không ai chạm nó.
"""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.tests._helpers import Scene, make_scene
from apps.api.spatial_write.tests._helpers import RecordingMerge, make_wall, write
from apps.api.spatial_write.tests._route_helpers import make_furniture, put_layer, wire, write_body
from packages.core.errors import AppError
from packages.domain.spatial import Opening, SpatialLayer, Wall
from packages.testing.fixtures.clock import FakeClock

WALL = "W-SPANWALL01"
DOOR = "D-SPANDOOR01"
FIELD = "body.layer.openings.0.offsetMm"


def _layer(level_id: str, *, offset: int, length: int = 1000, curve: int = 0, swing: str = "left") -> SpatialLayer:
    """Một tường dài `length` (cong nếu `curve ≠ 0`) với một cửa rộng 900 ở `offset`."""
    wall: Wall = make_wall(level_id, entity_id=WALL, length=length).model_copy(
        update={"opening_ids": (DOOR,), "curve_offset_mm": curve}
    )
    door = Opening.model_validate(
        {
            "id": DOOR,
            "wallId": WALL,
            "kind": "door",
            "offsetMm": offset,
            "widthMm": 900,
            "heightMm": 2100,
            "sillHeightMm": 0,
            "swing": swing,
            "confidence": 0.8,
            "source": "ai",
            "reviewed": False,
        }
    )
    return SpatialLayer(walls=(wall,), openings=(door,), rooms=(), furniture=())


async def _put(api_client: httpx.AsyncClient, scene: Scene, base: int, layer: SpatialLayer) -> httpx.Response:
    """PUT #35 của người sở hữu dự án."""
    return await put_layer(
        api_client, scene.project.id, scene.floors[0].level_id, scene.owner, write_body(base, layer=layer)
    )


def _assert_span_error(response: httpx.Response) -> None:
    """422 `VALIDATION` trỏ đúng `offsetMm` của ô mở đầu tiên."""
    assert response.status_code == 422
    body: dict[str, Any] = response.json()
    assert (body["code"], body["field"]) == ("VALIDATION", FIELD)


async def _legacy(db_session: AsyncSession, scene: Scene, fake_clock: FakeClock) -> SpatialLayer:
    """Tài liệu revision 1 có cửa 200+900 vượt tường 1000 — dữ liệu cũ, trước H4."""
    legacy = _layer(scene.floors[0].level_id, offset=200)
    await write(
        db_session, floor=scene.floors[0], actor=scene.owner, clock=fake_clock, merge=RecordingMerge(lambda _: legacy)
    )
    await db_session.commit()
    return legacy


async def test_spatial_write_layer__opening_span_new_overflow(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Cửa mới 200+900 trên tường 1000 → 422; 100+900 (vừa khít) → 200."""
    scene = await make_scene(db_session)
    level_id = scene.floors[0].level_id

    _assert_span_error(await _put(api_client, scene, 0, _layer(level_id, offset=200)))
    assert (await _put(api_client, scene, 0, _layer(level_id, offset=100))).status_code == 200


async def test_spatial_write_layer__opening_span_legacy_untouched(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Cửa cũ đã vượt, lượt ghi chỉ đổi `swing` → 200: dữ liệu cũ không làm kẹt autosave."""
    scene = await make_scene(db_session)
    legacy = await _legacy(db_session, scene, fake_clock)

    swung = legacy.model_copy(
        update={"openings": tuple(item.model_copy(update={"swing": "right"}) for item in legacy.openings)}
    )
    response = await _put(api_client, scene, 1, swung)

    assert response.status_code == 200
    assert response.json()["revision"] == 2


async def test_spatial_write_layer__opening_span_legacy_moved(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Cửa cũ đã vượt, đổi `offsetMm` mà vẫn vượt → 422; tường chủ đổi hình (ngắn lại) → 422."""
    scene = await make_scene(db_session)
    level_id = scene.floors[0].level_id
    await _legacy(db_session, scene, fake_clock)

    _assert_span_error(await _put(api_client, scene, 1, _layer(level_id, offset=150)))
    _assert_span_error(await _put(api_client, scene, 1, _layer(level_id, offset=200, length=990)))


async def test_spatial_write_layer__opening_span_curved_wall_skipped(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Tường cong: không kiểm (chưa rõ đo theo cung hay dây) — cửa 200+900 trên dây 1000 vẫn ghi."""
    scene = await make_scene(db_session)

    response = await _put(api_client, scene, 0, _layer(scene.floors[0].level_id, offset=200, curve=300))

    assert response.status_code == 200


async def test_spatial_write_layer__stair_variant(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Cầu thang `variant` theo `StairType` của Pascal (`straight`, …) → 200; giá trị lạ → 422 trỏ `variant`."""
    scene = await make_scene(db_session)
    level_id = scene.floors[0].level_id
    stair = wire(make_furniture(level_id)) | {"kind": "stair", "variant": "straight"}
    payload = write_body(0, layer=_layer(level_id, offset=50))
    payload["body"]["layer"]["furniture"] = [stair]
    path_args = (api_client, scene.project.id, level_id, scene.owner)

    assert (await put_layer(*path_args, payload)).status_code == 200

    payload["baseVersion"] = 1
    payload["body"]["layer"]["furniture"] = [stair | {"variant": "zigzag"}]
    response = await put_layer(*path_args, payload)
    assert response.status_code == 422
    assert response.json()["field"] == "body.layer.furniture.0.variant"


async def test_write_layer__opening_span_only_on_user_route(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Không bật `check_spans` (khôi phục phiên bản ghi lại tài liệu cũ) → cửa vượt vẫn ghi; bật → 422."""
    scene = await make_scene(db_session)
    layer = _layer(scene.floors[0].level_id, offset=200)

    restored = await write(db_session, floor=scene.floors[0], actor=scene.owner, clock=fake_clock, layer=layer)
    moved = layer.model_copy(update={"openings": (layer.openings[0].model_copy(update={"offset_mm": 250}),)})
    with pytest.raises(AppError) as caught:
        await write(
            db_session,
            floor=scene.floors[0],
            actor=scene.owner,
            clock=fake_clock,
            base_revision=1,
            layer=moved,
            check_spans=True,
        )

    assert restored.revision == 1
    assert (caught.value.code.code, caught.value.params.get("field")) == ("VALIDATION", FIELD)
