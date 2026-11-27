"""Chỗ nối bộ hồ sơ → ngôi nhà có mái, đi bằng route thật (NHA-10d).

Mỗi nửa đã có test riêng: N59/N60 đóng băng đề xuất (`test_set_building_decisions.py`), tác vụ ráp dựng mái từ một
bản đóng băng dựng tay (`test_set_assembly.py`), `readiness` báo `roofUnreviewed` (`spatial_read`). Tệp này nối chúng:
bản đóng băng do chính N60 ghi phải là thứ tác vụ ráp đọc được, và mái nó ghi phải là thứ N15/N16 trả và #35 nhận lại.

Phần ML không thuộc chỗ nối này: lượt chạy của tầng được đánh dấu xong và lớp tường/phòng được gieo bằng factory.
"""

from collections import Counter
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets.assembly import assemble_sets
from apps.api.drawing_sets.tests._set_helpers import (
    broker as broker,
)
from apps.api.drawing_sets.tests._set_helpers import (
    drawing_set_on as drawing_set_on,
)
from apps.api.drawing_sets.tests.test_set_assembly import RECT, _layer, _room
from apps.api.drawing_sets.tests.test_set_building_decisions import (
    SECOND_LEVEL,
    _building,
    _confirm,
    _save,
    _warning_keys,
)
from apps.api.drawing_sets.tests.test_set_building_routes import _house
from apps.api.drawings.tests._upload_helpers import Stage
from apps.api.spatial_write.tests._route_helpers import layer_path
from packages.db.models.drawings import PipelineRunRow
from packages.db.models.floors import FloorRow
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.spatial import make_floor_document
from packages.testing.fixtures.clock import FakeClock

pytestmark = pytest.mark.usefixtures("drawing_set_on", "broker")


async def _layer_doc(client: httpx.AsyncClient, stage: Stage) -> dict[str, Any]:
    """N16 của tầng mang mái (tầng 2); khẳng định 200."""
    response = await client.get(layer_path(stage.project_id, SECOND_LEVEL), headers=stage.headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _finish_top_floor(maker: async_sessionmaker[AsyncSession], stage: Stage, clock: FakeClock) -> None:
    """Tầng 2 "chạy xong": lượt chạy N60 vừa xếp hàng thành `completed`, lớp có một tường và một phòng chữ nhật."""
    async with maker() as db:
        floor_pk = (
            await db.execute(
                select(FloorRow.pk).where(FloorRow.project_id == stage.project_id, FloorRow.level_id == SECOND_LEVEL)
            )
        ).scalar_one()
        run = (await db.execute(select(PipelineRunRow).where(PipelineRunRow.floor_pk == floor_pk))).scalar_one()
        run.status = "completed"
        layer = _layer(SECOND_LEVEL, _room(SECOND_LEVEL, RECT))
        await make_floor_document(db, floor_pk=floor_pk, layer=layer, revision=1, clock=clock)
        await db.commit()


async def test_confirmed_set_gets_an_ai_roof_that_a_person_can_approve(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
) -> None:
    """Duyệt (N59) → xác nhận (N60) → tầng chạy xong → ráp: tầng 2 có mái AI đúng đề xuất đã đóng băng, N16 báo
    `roofUnreviewed` và N15 mang mái; người duyệt mái qua #35 thì lý do hết, lượt ráp sau không ghi đè (M-11)."""
    stage, seeded = await _house(db_session, local_storage)
    seen = _warning_keys(await _building(api_client, stage, seeded))
    assert (await _save(api_client, stage, seeded, 0, {"levels": [], "roofs": [], "seen": seen})).status_code == 200
    assert (await _confirm(api_client, stage, seeded, 1)).status_code == 200

    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter()  # pipeline chưa xong: chưa có gì để ráp
    await _finish_top_floor(db_sessionmaker, stage, fake_clock)
    assert await assemble_sets(db_sessionmaker, fake_clock) == Counter({"BUILT": 1})

    doc = await _layer_doc(api_client, stage)
    (roof,) = doc["layer"]["roofs"]
    assert (roof["source"], roof["reviewed"], roof["levelId"]) == ("ai", False, SECOND_LEVEL)
    segment = roof["nodes"][1]  # hình mái ghi `i=30%`: hai mái theo quy ước, dốc 16,7°
    assert (segment["type"], segment["roofType"], segment["pitch"]) == ("roof-segment", "gable", 16.7)
    assert "roofUnreviewed" in doc["readiness"]["reasons"]
    graph = await api_client.get(f"/api/projects/{stage.project_id}/spatial", headers=stage.headers)
    assert [item["id"] for item in graph.json()["graph"]["roofs"]] == [roof["id"]]

    # Như AppFront ghi lại: bỏ `clearAreaM2` (máy chủ tự tính, #35 không nhận), đổi mái sang người duyệt.
    rooms = [{key: value for key, value in room.items() if key != "clearAreaM2"} for room in doc["layer"]["rooms"]]
    approved = doc["layer"] | {"rooms": rooms, "roofs": [roof | {"source": "human", "reviewed": True}]}
    response = await api_client.put(
        layer_path(stage.project_id, SECOND_LEVEL),
        json={"baseVersion": doc["revision"], "body": {"layer": approved}},
        headers=stage.headers,
    )
    assert response.status_code == 200, response.text
    rerun = await assemble_sets(db_sessionmaker, fake_clock)
    assert rerun == Counter({"BUILT": 1})  # tầng lại tới lượt vì `revision` đổi; mái đã duyệt được giữ, không hỏng
    after = await _layer_doc(api_client, stage)
    (kept,) = after["layer"]["roofs"]
    assert (kept["id"], kept["source"], kept["reviewed"]) == (roof["id"], "human", True)
    assert "roofUnreviewed" not in after["readiness"]["reasons"]
