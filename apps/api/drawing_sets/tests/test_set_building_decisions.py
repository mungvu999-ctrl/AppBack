"""Quyết định của người duyệt trên đề xuất ngôi nhà (NHA-06b): N59 lưu, route đọc trả lại và áp, N60 chặn + đóng băng.

Bộ mẫu là ba trang của `test_set_building_routes._house`: hai mặt bằng (±0.000, +3.300) và một mặt bằng mái `i=30%`.
"""

from typing import Any, Final

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.drawing_sets.building_schemas import LevelDecisionOut, RoofDecisionOut
from apps.api.drawing_sets.confirm import _reviewed_levels
from apps.api.drawing_sets.set_schemas import LevelDecisionIn, RoofDecisionIn
from apps.api.drawing_sets.tests._set_helpers import NEW_LEVEL, ReviewSet, confirm_path, edit_of, views_body, views_path
from apps.api.drawing_sets.tests._set_helpers import (
    broker as broker,
)
from apps.api.drawing_sets.tests._set_helpers import (
    drawing_set_on as drawing_set_on,
)
from apps.api.drawing_sets.tests.test_set_building_routes import _house, _path
from apps.api.drawings.tests._upload_helpers import Stage
from packages.db.models.drawing_sets import DrawingSetRow
from packages.db.models.floors import FloorRow
from packages.domain.drawing_set.building import LevelSpec, Measured, propose_building
from packages.domain.drawing_set.decisions import LevelDecision, RoofDecision
from packages.storage.local import LocalDiskStorage

SECOND_LEVEL: Final = "L-0000000000NEW2"

pytestmark = pytest.mark.usefixtures("drawing_set_on", "broker")


def _edits(seeded: ReviewSet, **changes: Any) -> list[dict[str, Any]]:
    """Ba hình của bộ mẫu: hai mặt bằng thành hai tầng mới, hình mái không có đích; `changes` ghi đè hình thứ hai."""
    ground, upper, roof = seeded.views
    return [
        edit_of(ground, target={"mode": "newFloor", "floorId": NEW_LEVEL, "name": "Tầng 1"}),
        edit_of(upper, target={"mode": "newFloor", "floorId": SECOND_LEVEL, "name": "Tầng 2"}) | changes,
        edit_of(roof),
    ]


async def _save(
    client: httpx.AsyncClient,
    stage: Stage,
    seeded: ReviewSet,
    base: int,
    building: dict[str, Any] | None,
    **changes: Any,
) -> httpx.Response:
    """N59 với ba hình của bộ mẫu; `building` vắng = thân không có khoá đó (client cũ)."""
    body = views_body(base, *_edits(seeded, **changes))
    if building is not None:
        body["body"]["building"] = building
    return await client.put(views_path(stage.project_id, seeded.drawing_set.id), json=body, headers=stage.headers)


async def _building(client: httpx.AsyncClient, stage: Stage, seeded: ReviewSet) -> dict[str, Any]:
    """Route đọc đề xuất ngôi nhà; khẳng định 200."""
    response = await client.get(_path(stage, seeded.drawing_set.id), headers=stage.headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _confirm(client: httpx.AsyncClient, stage: Stage, seeded: ReviewSet, revision: int) -> httpx.Response:
    """N60 cho bản duyệt `revision`."""
    set_id = seeded.drawing_set.id
    return await client.post(
        confirm_path(stage.project_id, set_id), json={"setId": set_id, "revision": revision}, headers=stage.headers
    )


def _warning_keys(wire: dict[str, Any]) -> list[str]:
    """Khoá của mọi cảnh báo trong đề xuất trên dây, đúng như máy chủ phát (`violations[].key`)."""
    return [hit["key"] for hit in wire["proposal"]["violations"] if hit["level"] == "warning"]


async def _floors(maker: async_sessionmaker[AsyncSession], stage: Stage) -> dict[str, tuple[int, int]]:
    """`level_id → (cốt, chiều cao)` của hai tầng mới."""
    async with maker() as session:
        rows = await session.execute(
            select(FloorRow.level_id, FloorRow.elevation_mm, FloorRow.height_mm).where(
                FloorRow.project_id == stage.project_id, FloorRow.level_id.in_([NEW_LEVEL, SECOND_LEVEL])
            )
        )
        return {level_id: (elevation, height) for level_id, elevation, height in rows}


async def _stored(maker: async_sessionmaker[AsyncSession], seeded: ReviewSet) -> Any:
    """Cột `building` của bộ, đọc bằng session mới."""
    async with maker() as session:
        row = await session.get(DrawingSetRow, seeded.drawing_set.id)
        assert row is not None
        return row.building


# --------------------------------------------------------------------------- N59 + route đọc


async def test_saved_decisions_come_back_and_shape_the_proposal(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """N59 lưu quyết định cùng `revision` với hình; route đọc trả lại đúng (trường không nhập thì vắng) và áp chúng."""
    stage, seeded = await _house(db_session, local_storage)
    upper = seeded.views[1].id
    building = {
        "levels": [{"levelRef": upper, "heightMm": 3000}],
        "roofs": [{"levelRef": upper, "form": "hip", "pitchDeg": 35}],
        "picks": {},
        "seen": ["T-07|x|heightMm|default"],
    }
    saved = await _save(api_client, stage, seeded, 0, building)
    assert (saved.status_code, saved.json()["revision"]) == (200, 1)
    wire = await _building(api_client, stage, seeded)
    assert wire["decisions"] == {
        "levels": [{"levelRef": upper, "heightMm": 3000}],
        "roofs": [{"levelRef": upper, "skip": False, "form": "hip", "pitchDeg": 35.0}],
        "picks": {},
        "seen": ["T-07|x|heightMm|default"],
    }
    reviewed = {"confidence": 1.0, "source": "reviewer", "viewIds": [upper]}
    assert wire["proposal"]["levels"][1]["heightMm"] == {"value": 3000, **reviewed}
    (roof,) = wire["proposal"]["roofs"]
    assert (roof["form"], roof["eaveElevationMm"]["value"]) == ({"value": "hip", **reviewed}, 6300)


async def test_set_without_decisions_has_no_decisions_key(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Chưa ai gửi quyết định: khoá `decisions` vắng (W2), đề xuất là của máy."""
    stage, seeded = await _house(db_session, local_storage)
    wire = await _building(api_client, stage, seeded)
    assert "decisions" not in wire
    assert [level["levelRef"] for level in wire["proposal"]["levels"]] == [view.id for view in seeded.views[:2]]


async def test_body_without_building_keeps_the_saved_decisions(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Client cũ không biết khoá `building`: lưu hình không được xoá quyết định đã có; gửi `{}` mới là xoá."""
    stage, seeded = await _house(db_session, local_storage)
    upper = seeded.views[1].id
    await _save(api_client, stage, seeded, 0, {"levels": [{"levelRef": upper, "heightMm": 3000}]})
    assert (await _save(api_client, stage, seeded, 1, None)).status_code == 200
    assert (await _building(api_client, stage, seeded))["decisions"]["levels"] == [
        {"levelRef": upper, "heightMm": 3000}
    ]
    assert (await _save(api_client, stage, seeded, 2, {})).status_code == 200
    assert (await _building(api_client, stage, seeded))["decisions"] == {
        "levels": [],
        "roofs": [],
        "picks": {},
        "seen": [],
    }


@pytest.mark.parametrize(
    ("building", "field"),
    [
        ({"levels": [{"levelRef": "dwv_missing", "heightMm": 3000}]}, "body.building.levels.0.levelRef"),
        (
            {"roofs": [{"levelRef": "ROOF", "form": "hip"}]},
            "body.building.roofs.0.levelRef",
        ),  # hình mái, không phải tầng
        ({"levels": [{"levelRef": "UPPER"}, {"levelRef": "UPPER"}]}, "body.building.levels.1.levelRef"),
    ],
)
async def test_decision_must_point_at_a_plan_view_of_this_review(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    building: dict[str, Any],
    field: str,
) -> None:
    """`levelRef` phải là một hình mặt bằng đang dùng trong chính bản duyệt này, mỗi hình một quyết định: sai → 422."""
    stage, seeded = await _house(db_session, local_storage)
    names = {"UPPER": seeded.views[1].id, "ROOF": seeded.views[2].id}
    for group in building.values():
        for item in group:
            item["levelRef"] = names.get(item["levelRef"], item["levelRef"])
    response = await _save(api_client, stage, seeded, 0, building)
    assert (response.status_code, response.json()["code"], response.json()["field"]) == (422, "VALIDATION", field)
    await db_session.refresh(seeded.drawing_set)
    assert seeded.drawing_set.revision == 0


@pytest.mark.parametrize(
    "item",
    [
        {"levels": [{"levelRef": "UPPER", "elevationMm": 400_000}]},
        {"levels": [{"levelRef": "UPPER", "heightMm": 1_000}]},
        {"roofs": [{"levelRef": "UPPER", "pitchDeg": 85}]},
        {"roofs": [{"levelRef": "UPPER", "quarterTurns": 4}]},
        {"roofs": [{"levelRef": "UPPER", "form": "dome"}]},
        {"picks": {"elevationMm:x": "guess"}},
        {"unknown": 1},
        {"seen": ["T-07|a\u0000b"]},
        {"picks": {"elevationMm:\u0001": "plan"}},
    ],
)
async def test_decision_values_outside_their_range_are_rejected(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage, item: dict[str, Any]
) -> None:
    """Cốt/chiều cao ngoài dải của tầng, độ dốc từ 85°, hướng ngoài 0..3, dạng/nguồn lạ, khoá lạ → 422, không lưu."""
    stage, seeded = await _house(db_session, local_storage)
    for group in item.values():
        for entry in group if isinstance(group, list) else []:
            if isinstance(entry, dict):
                entry["levelRef"] = seeded.views[1].id
    response = await _save(api_client, stage, seeded, 0, item)
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION")


def test_decision_models_stay_in_step_across_wire_and_domain() -> None:
    """Thân N59, phản hồi route đọc và mô hình lưu của miền có cùng tập trường — thêm trường thì thêm cả ba."""
    assert set(LevelDecisionIn.model_fields) == set(LevelDecisionOut.model_fields) == set(LevelDecision.model_fields)
    assert set(RoofDecisionIn.model_fields) == set(RoofDecisionOut.model_fields) == set(RoofDecision.model_fields)


# --------------------------------------------------------------------------- N60


async def test_set_that_never_got_decisions_confirms_as_before(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Bộ chưa từng nhận `building` (FE cũ): N60 không chặn gì, tầng mới xếp chồng 3000 mm như trước, không lưu gì."""
    stage, seeded = await _house(db_session, local_storage)
    await _save(api_client, stage, seeded, 0, None)
    assert (await _confirm(api_client, stage, seeded, 1)).status_code == 200
    floors = await _floors(db_sessionmaker, stage)
    assert floors[SECOND_LEVEL][0] - floors[NEW_LEVEL][0] == 3000
    assert {height for _, height in floors.values()} == {3000}
    assert await _stored(db_sessionmaker, seeded) is None


@pytest.mark.parametrize(
    ("building", "field"),
    [
        ({}, "building.warnings"),  # cảnh báo của máy (chiều cao mặc định, dạng mái) chưa ai xem
        ({"roofs": [{"levelRef": "UPPER", "form": "gable", "ridgeElevationMm": 6000}]}, "building.violations"),
    ],
)
async def test_confirm_is_refused_while_the_building_is_not_settled(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
    building: dict[str, Any],
    field: str,
) -> None:
    """Còn cảnh báo chưa xem, hoặc còn vi phạm mức lỗi (đỉnh mái thấp hơn diềm) → 422, bộ vẫn `review`, chưa có tầng."""
    stage, seeded = await _house(db_session, local_storage)
    for roof in building.get("roofs", []):
        roof["levelRef"] = seeded.views[1].id
    assert (await _save(api_client, stage, seeded, 0, building)).status_code == 200
    response = await _confirm(api_client, stage, seeded, 1)
    assert (response.status_code, response.json()["code"], response.json()["field"]) == (422, "VALIDATION", field)
    assert await _floors(db_sessionmaker, stage) == {}
    await db_session.refresh(seeded.drawing_set)
    assert seeded.drawing_set.status == "review"


async def test_decision_for_a_view_no_longer_reviewed_blocks_confirm(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Quyết định trỏ tới hình sau đó bị loại khỏi bản duyệt (client cũ lưu, không gửi `building`) → 422 ở N60."""
    stage, seeded = await _house(db_session, local_storage)
    upper = seeded.views[1].id
    await _save(api_client, stage, seeded, 0, {"levels": [{"levelRef": upper, "heightMm": 3000}]})
    assert (await _save(api_client, stage, seeded, 1, None, excluded=True)).status_code == 200
    response = await _confirm(api_client, stage, seeded, 2)
    assert (response.status_code, response.json()["field"]) == (422, "building.decisions")


async def test_confirm_freezes_the_proposal_and_new_floors_take_the_reviewed_levels(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Xem hết cảnh báo rồi xác nhận: tầng mới mang cốt/chiều cao đang hiệu lực (kể cả số người duyệt nhập), bản đề
    xuất được đóng băng một lần, và route đọc từ đó trả đúng bản đóng băng (không tính lại)."""
    stage, seeded = await _house(db_session, local_storage)
    upper = seeded.views[1].id
    building: dict[str, Any] = {
        "levels": [{"levelRef": upper, "heightMm": 3000}],
        "roofs": [{"levelRef": upper, "form": "hip", "pitchDeg": 35}],
    }
    await _save(api_client, stage, seeded, 0, building)
    building["seen"] = _warning_keys(await _building(api_client, stage, seeded))
    assert (await _save(api_client, stage, seeded, 1, building)).status_code == 200
    before = await _building(api_client, stage, seeded)

    response = await _confirm(api_client, stage, seeded, 2)
    assert (response.status_code, response.json()["status"]) == (200, "confirmed")
    assert await _floors(db_sessionmaker, stage) == {NEW_LEVEL: (0, 3300), SECOND_LEVEL: (3300, 3000)}
    stored = await _stored(db_sessionmaker, seeded)
    assert (stored["confirmed"]["schemaVersion"], stored["decisions"]["seen"]) == (1, building["seen"])
    after = await _building(api_client, stage, seeded)
    assert (after["proposal"], after["decisions"]) == (before["proposal"], before["decisions"])
    assert (await _confirm(api_client, stage, seeded, 2)).status_code == 200  # gọi lại: bộ hiện tại, không ghi thêm


async def test_typed_level_places_the_new_floor_where_the_reviewer_said(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    local_storage: LocalDiskStorage,
) -> None:
    """Người duyệt nhập cốt 3600 cho tầng 2 (máy đọc 3300): tầng mới nằm ở 3600 chứ không xếp chồng lên đỉnh tầng 1,
    và cảnh báo "hở 300 mm" (T-01) phải được xem trước khi xác nhận."""
    stage, seeded = await _house(db_session, local_storage)
    building: dict[str, Any] = {"levels": [{"levelRef": seeded.views[1].id, "elevationMm": 3600}]}
    await _save(api_client, stage, seeded, 0, building)
    assert (await _confirm(api_client, stage, seeded, 1)).json()["field"] == "building.warnings"
    building["seen"] = _warning_keys(await _building(api_client, stage, seeded))
    assert any(key.startswith("T-01|") for key in building["seen"])
    assert (await _save(api_client, stage, seeded, 1, building)).status_code == 200
    assert (await _confirm(api_client, stage, seeded, 2)).status_code == 200
    assert await _floors(db_sessionmaker, stage) == {NEW_LEVEL: (0, 3300), SECOND_LEVEL: (3600, 3300)}


@pytest.mark.parametrize("broken", ["version", "shape", "missing"])
async def test_unreadable_frozen_copy_falls_back_to_a_fresh_proposal(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    caplog: pytest.LogCaptureFixture,
    broken: str,
) -> None:
    """Bản đóng băng khác phiên bản, sai hình dạng (mã mới thêm trường) hay thiếu khoá: route đọc tính lại và ghi một
    dòng log, không trả 500 mãi mãi cho bộ đã xác nhận."""
    stage, seeded = await _house(db_session, local_storage)
    expected = (await _building(api_client, stage, seeded))["proposal"]
    cases: dict[str, dict[str, Any]] = {
        "version": {"schemaVersion": 99, "proposal": {}},
        "shape": {"schemaVersion": 1, "proposal": {"unitSystem": "metric"}},
        "missing": {"schemaVersion": 1},
    }
    frozen = cases[broken]
    seeded.drawing_set.status, seeded.drawing_set.building = "confirmed", {"confirmed": frozen}
    await db_session.commit()
    with caplog.at_level("WARNING", logger="apps.api.drawing_sets.building_read"):
        wire = await _building(api_client, stage, seeded)
    assert wire["proposal"] == expected
    assert [record.message for record in caplog.records] == ["set_building_frozen_unreadable"]


def test_levels_outside_the_floor_range_are_not_used() -> None:
    """Cốt/chiều cao đang hiệu lực nằm ngoài dải của tầng (#10) không được dùng khi tạo tầng: tầng đó xếp chồng."""

    def level(ref: str, elevation: int | None, height: int | None) -> LevelSpec:
        """Một `LevelSpec` với giá trị máy."""
        made = [
            None if value is None else Measured(value=value, confidence=0.9, source="plan", view_ids=(ref,))
            for value in (elevation, height)
        ]
        return LevelSpec(level_ref=ref, elevation_mm=made[0], height_mm=made[1])

    levels = (level("a", 3300, 3000), level("b", 400_000, 1_000), level("c", None, None), level("d", -30_000, 10_000))
    frozen = propose_building([]).model_copy(update={"levels": levels})
    assert _reviewed_levels(frozen) == {"a": (3300, 3000), "b": (None, None), "c": (None, None), "d": (-30_000, 10_000)}
    assert _reviewed_levels(None) == {}
