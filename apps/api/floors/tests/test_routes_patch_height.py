"""H2 (HOP-DONG-M1 §7) của #34 `floors_patch_spatial_floor`: đổi `heightMm` kéo theo tường "theo tầng".

Postgres thật, route thật cho cả hai phía (#34 và #35): tài liệu tầng dựng bằng PUT #35, đọc lại
bằng session khác.
"""

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.floors.tests._bodies import headers_of, spatial_path
from apps.api.spatial_read.documents import load_document
from apps.api.spatial_read.tests._helpers import Scene, make_scene, other_session
from apps.api.spatial_write.tests._helpers import log_rows, make_wall
from apps.api.spatial_write.tests._route_helpers import put_layer, write_body
from packages.domain.spatial import Opening, SpatialLayer, Wall

INHERITED = "W-INHERIT001"
CUSTOM = "W-CUSTOM0001"
DOOR = "D-HEIGHTDOOR1"


def _wall(level_id: str, entity_id: str, height: int, opening_ids: tuple[str, ...] = ()) -> Wall:
    """Tường dài 4000 mm cao `height`, kèm id ô mở nó chứa."""
    return make_wall(level_id, entity_id=entity_id, length=4000).model_copy(
        update={"height_mm": height, "opening_ids": opening_ids}
    )


def _door(level_id: str, *, sill: int, height: int) -> Opening:
    """Cửa đi rộng 900 trên tường `INHERITED`, đỉnh ở `sill + height`."""
    return Opening.model_validate(
        {
            "id": DOOR,
            "wallId": INHERITED,
            "kind": "door",
            "offsetMm": 500,
            "widthMm": 900,
            "heightMm": height,
            "sillHeightMm": sill,
            "swing": "left",
            "confidence": 0.8,
            "source": "ai",
            "reviewed": False,
        }
    )


async def _seed(api_client: httpx.AsyncClient, scene: Scene, *, door_top: tuple[int, int] = (0, 2100)) -> None:
    """Lớp revision 1: tường theo tầng (3000 = cao tầng) có một cửa, tường tuỳ chỉnh 2800."""
    level_id = scene.floors[0].level_id
    layer = SpatialLayer(
        walls=(_wall(level_id, INHERITED, 3000, (DOOR,)), _wall(level_id, CUSTOM, 2800)),
        openings=(_door(level_id, sill=door_top[0], height=door_top[1]),),
        rooms=(),
        furniture=(),
    )
    response = await put_layer(api_client, scene.project.id, level_id, scene.owner, write_body(0, layer=layer))
    assert response.status_code == 200


async def _patch(api_client: httpx.AsyncClient, scene: Scene, height: int) -> httpx.Response:
    """PATCH #34 chỉ đổi `heightMm`."""
    path = spatial_path(scene.project.id, scene.floors[0].level_id)
    return await api_client.patch(path, json={"heightMm": height}, headers=headers_of(scene.owner))


async def test_floors_patch_spatial_floor__height_moves_inherited_walls(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """3000→3300: tường theo tầng lên 3300, tường tuỳ chỉnh giữ 2800; `revision` 1→2, nhật ký `height_mm`.

    FE cầm `baseVersion 1` gửi #35 → 409 với `remoteChanges` mang `height_mm = 3300` của người PATCH.
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _seed(api_client, scene)

    response = await _patch(api_client, scene, 3300)

    assert response.status_code == 200
    assert response.json()["heightMm"] == 3300
    async with other_session(db_sessionmaker) as db:
        document = await load_document(db, floor.pk)
        rows = await log_rows(db, floor.pk)
    assert document is not None
    assert document.revision == 2
    assert {wall.id: wall.height_mm for wall in document.layer.walls} == {INHERITED: 3300, CUSTOM: 2800}
    logged = [(row.entity_id, row.field, row.value, row.changed_by) for row in rows if row.revision == 2]
    assert logged == [(INHERITED, "height_mm", 3300, scene.owner.id)]

    stale = await put_layer(
        api_client, scene.project.id, floor.level_id, scene.owner, write_body(1, layer=document.layer)
    )
    assert stale.status_code == 409
    changes = {(item["entityId"], item["field"]): item for item in stale.json()["remoteChanges"]}
    assert changes[(INHERITED, "height_mm")]["value"] == 3300


async def test_floors_patch_spatial_floor__height_no_inherited_wall_keeps_revision(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Mọi tường tuỳ chỉnh (2500 ≠ 3000): PATCH 3000→3300 đổi tầng, `revision` và tường giữ nguyên."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    level_id = floor.level_id
    custom = SpatialLayer(
        walls=(_wall(level_id, INHERITED, 2500), _wall(level_id, CUSTOM, 2800)), openings=(), rooms=(), furniture=()
    )
    put = await put_layer(api_client, scene.project.id, level_id, scene.owner, write_body(0, layer=custom))
    assert put.status_code == 200

    response = await _patch(api_client, scene, 3300)

    assert response.status_code == 200
    assert response.json()["heightMm"] == 3300
    async with other_session(db_sessionmaker) as db:
        after = await load_document(db, floor.pk)
    assert after is not None
    assert after.revision == 1
    assert {wall.height_mm for wall in after.layer.walls} == {2500, 2800}


async def test_floors_patch_spatial_floor__height_below_opening(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Cửa đỉnh 900+2100 = 3000 trên tường theo tầng; hạ tầng còn 2900 → 422, không đổi gì."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _seed(api_client, scene, door_top=(900, 2100))

    response = await _patch(api_client, scene, 2900)

    assert response.status_code == 422
    body: dict[str, Any] = response.json()
    assert (body["code"], body["field"]) == ("FLOOR_HEIGHT_BELOW_OPENING", "heightMm")
    async with other_session(db_sessionmaker) as db:
        document = await load_document(db, floor.pk)
        listed = await api_client.get(f"/api/projects/{scene.project.id}/floors", headers=headers_of(scene.owner))
    assert document is not None
    assert document.revision == 1
    assert {wall.height_mm for wall in document.layer.walls} == {3000, 2800}
    assert listed.json()[0]["heightMm"] == 3000


async def test_floors_patch_spatial_floor__height_without_document(
    api_client: httpx.AsyncClient, db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Tầng chưa có tài liệu: chỉ đổi dòng `floors`, không tạo dòng `floor_documents`."""
    scene = await make_scene(db_session)

    response = await _patch(api_client, scene, 3300)

    assert response.status_code == 200
    assert response.json()["heightMm"] == 3300
    async with other_session(db_sessionmaker) as db:
        assert await load_document(db, scene.floors[0].pk) is None
