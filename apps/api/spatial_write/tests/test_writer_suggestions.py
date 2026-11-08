"""Lớp đề xuất M2 trong `write_layer` (HOP-DONG-DU-LIEU-LOP-DE-XUAT §4, §6, §8).

Lớp nhỏ của `_layer` (một tường nằm trên cạnh dưới của một phòng bếp) luôn có len chân tường R1 của cặp (phòng,
tường) và đèn trần L1 của phòng; các luật đồ đạc (P1…) có thể thêm bản ghi, nên test chỉ khẳng định trên hai id này.
Postgres thật, session thật (K23); chỉ trần `reconcile` của ca 422 `SUGGESTION_CAP` được thu nhỏ bằng
`functools.partial` — logic phía trên DB, không phải DB.
"""

import functools
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.projects.tests.test_routes_common import headers_of
from apps.api.spatial_read.tests._helpers import make_scene
from apps.api.spatial_read.tests._read_helpers import graph_path
from apps.api.spatial_write import writer
from apps.api.spatial_write.errors import SUGGESTION_CAP
from apps.api.spatial_write.tests._helpers import (
    FURNITURE_ID,
    ROOM_ID,
    WALL_ID,
    RecordingMerge,
    make_room,
    make_wall,
    owned_ids,
    reread,
    simple_layer,
)
from apps.api.spatial_write.tests._route_helpers import layer_path, put_layer, wire, write_body
from apps.api.spatial_write.writer import LayerWrite, WriteResult, write_layer
from packages.core.clock import Clock
from packages.core.errors import MISSING, SYSTEM_PIPELINE, AppError, VersionConflictError
from packages.db.models.auth import User
from packages.db.models.floors import FloorRow
from packages.db.models.spatial import FloorDocumentRow
from packages.domain.spatial import Furniture, SpatialLayer, entity_ids
from packages.domain.suggestions import Suggestion, load_ruleset, reconcile
from packages.testing.factories.auth import make_user
from packages.testing.factories.spatial import make_floor_document
from packages.testing.fixtures.clock import FakeClock

R1_ID: Final = f"DRESS-R1-{ROOM_ID}-{WALL_ID}"
L1_ID: Final = f"DRESS-L1-{ROOM_ID}"
GOLDEN_DIR: Final = Path("packages/domain/suggestions/tests/data")
GOLDEN_CASES: Final = ("ch-04", "ch-06", "syn-penthouse")
BUDGET_MS: Final = 150.0
"""§8 quyết định 6: `reconcile` vượt 150 ms p95 thì chuyển job nền."""
CEILING_MS: Final = 3000.0
"""Trần chặn hồi quy bệnh lý của phép đo (kể cả dưới `coverage`); con số so với `BUDGET_MS` nằm trong báo cáo."""


def _layer(level_id: str, *, thickness: int = 200, furniture: tuple[Furniture, ...] = ()) -> SpatialLayer:
    """Một tường 1 000 mm trên cạnh dưới phòng bếp 4 000 x 4 250 mm: đủ cho R1 và L1."""
    return simple_layer(
        level_id, walls=(make_wall(level_id, thickness=thickness),), rooms=(make_room(level_id),), furniture=furniture
    )


def _adopted_bed(level_id: str) -> Furniture:
    """Món người dùng đã nhận nuôi từ đề xuất L1 (đủ cho bước 6 của §3: `origin.suggestionId` khớp id đề xuất)."""
    return Furniture.model_validate(
        {
            "id": FURNITURE_ID,
            "levelId": level_id,
            "roomId": ROOM_ID,
            "kind": "bed",
            "centre": {"x": 2000, "y": 2000},
            "boundingBox": {"min": {"x": 1000, "y": 1000}, "max": {"x": 3000, "y": 3000}},
            "rotationDeg": 0,
            "origin": {"suggestionId": L1_ID, "rule": "L1", "ruleVersion": 1},
            "confidence": 1.0,
            "source": "human",
            "reviewed": False,
        }
    )


async def _write(
    db: AsyncSession,
    floor: FloorRow,
    actor: User,
    clock: Clock,
    *,
    base: int,
    layer: SpatialLayer | None = None,
    state: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
    **options: Any,
) -> WriteResult:
    """`write_layer` với hai khoá M2 của thân; `options` đi thẳng vào hàm thật (ví dụ `merge`)."""
    return await write_layer(
        db,
        floor_pk=floor.pk,
        base_revision=base,
        body=LayerWrite(
            layer=layer,
            scale_mm_per_px=None,
            suggestion_state=None if state is None else tuple(state),
            suggestions_enabled=enabled,
        ),
        actor_id=options.pop("actor_id", actor.id),
        actor_name=actor.name,
        clock=clock,
        may_approve=True,
        **options,
    )


def _by_id(items: tuple[Suggestion, ...]) -> dict[str, Suggestion]:
    """Đề xuất theo id."""
    return {item.id: item for item in items}


def _ours(items: tuple[Suggestion, ...]) -> list[tuple[str, int]]:
    """`(id, createdRevision)` của hai đề xuất R1, L1 mà lớp nhỏ luôn sinh, theo id."""
    return [(item.id, item.created_revision) for item in items if item.id in (L1_ID, R1_ID)]


async def _stored(db: AsyncSession, floor_pk: int) -> dict[str, Any]:
    """Cột `document` thô (jsonb) của một tầng."""
    stmt = select(FloorDocumentRow.document).where(FloorDocumentRow.floor_pk == floor_pk)
    document: dict[str, Any] = (await db.execute(stmt)).scalar_one()
    return document


async def test_layer_change__suggestions_in_same_revision(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Lượt đổi `layer` sinh lớp đề xuất trong **cùng** revision; id đề xuất không vào `floor_entity_ids`.

    Lượt đổi `layer` sau đó sinh lại trên revision mới mà giữ id và `createdRevision` (§2.3, §3 bước 2).
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]

    first = await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))

    assert (first.revision, first.applied, first.suggestions_enabled) == (1, True, True)
    assert _ours(first.suggestions) == [(L1_ID, 1), (R1_ID, 1)]
    assert {item.created_revision for item in first.suggestions} == {1}
    assert first.suggestions_meta is not None
    assert (first.suggestions_meta.basis_revision, first.suggestions_meta.ruleset_sha) == (1, load_ruleset().sha)
    document = await reread(db_session, floor.pk)
    assert (document.revision, document.suggestions, document.suggestions_meta) == (
        1,
        first.suggestions,
        first.suggestions_meta,
    )
    raw = await _stored(db_session, floor.pk)
    assert raw["suggestionsEnabled"] is True
    assert [item["id"] for item in raw["suggestions"]] == [item.id for item in first.suggestions]
    assert await owned_ids(db_session, scene.project.id) == set(entity_ids(first.layer))

    second = await _write(
        db_session, floor, scene.owner, fake_clock, base=1, layer=_layer(floor.level_id, thickness=220)
    )

    assert second.revision == 2
    assert second.suggestions_meta is not None
    assert second.suggestions_meta.basis_revision == 2
    assert _ours(second.suggestions) == [(L1_ID, 1), (R1_ID, 1)]


async def test_resend_same_body__no_regeneration_no_bump(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """C09b và bước 10: gửi lại cùng thân, hay lớp không đổi, không sinh lại và không tăng revision.

    Tầng mới chưa lưu khoá `suggestionsEnabled` (cờ suy từ revision 0): lượt ghi không đổi gì vẫn không tăng revision
    chỉ để ghi khoá ấy.
    """
    scene = await make_scene(db_session, floors=2)
    floor, empty = scene.floors
    layer = _layer(floor.level_id)
    first = await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=layer)

    again = await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=layer)
    same = await _write(db_session, floor, scene.owner, fake_clock, base=1, layer=layer)

    assert (again.revision, again.applied, again.suggestions) == (1, False, first.suggestions)
    assert (same.revision, same.applied, same.suggestions_meta) == (1, False, first.suggestions_meta)
    untouched = await _write(
        db_session,
        empty,
        scene.owner,
        fake_clock,
        base=0,
        layer=SpatialLayer(walls=(), openings=(), rooms=(), furniture=()),
    )
    assert (untouched.revision, untouched.applied, untouched.suggestions_enabled) == (0, False, True)
    assert "suggestionsEnabled" not in await _stored(db_session, empty.pk)


async def test_client_params_ignored(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`suggestions`, `params`, `stale`, `createdRevision` do client gửi: nhận và bỏ qua (W18); N16 đọc đúng bản lưu."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    forged = {"id": R1_ID, "rule": "R1", "params": {"heightMm": 999}, "state": "disabled"}

    created = await put_layer(
        api_client,
        scene.project.id,
        floor.level_id,
        scene.owner,
        {"baseVersion": 0, "body": {"layer": wire(_layer(floor.level_id)), "suggestions": [forged]}},
    )
    row = {
        "id": R1_ID,
        "state": "overridden",
        "override": {"heightMm": 120},
        "params": {"heightMm": 5},
        "stale": {"since": 0, "reasonVi": "giả"},
        "createdRevision": 99,
    }
    tuned = await put_layer(
        api_client,
        scene.project.id,
        floor.level_id,
        scene.owner,
        {"baseVersion": 1, "body": {"suggestionState": [row]}},
    )

    assert created.status_code == 200
    skirting = next(item for item in created.json()["suggestions"] if item["id"] == R1_ID)
    assert (skirting["params"]["heightMm"], skirting["state"]) == (90, "active")
    assert tuned.status_code == 200
    body = tuned.json()
    assert (body["revision"], body["suggestionsEnabled"], body["suggestionsMeta"]["basisRevision"]) == (2, True, 1)
    skirting = next(item for item in body["suggestions"] if item["id"] == R1_ID)
    assert (skirting["state"], skirting["override"], skirting["params"]["heightMm"]) == (
        "overridden",
        {"heightMm": 120},
        90,
    )
    assert skirting["createdRevision"] == 1
    assert "stale" not in skirting
    read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
    assert read.status_code == 200
    assert {key: read.json()[key] for key in ("suggestions", "suggestionsMeta", "suggestionsEnabled")} == {
        key: body[key] for key in ("suggestions", "suggestionsMeta", "suggestionsEnabled")
    }


async def test_unknown_suggestion_id__422(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Id lạ → 422 `SUGGESTION_UNKNOWN`; khoá điều chỉnh ngoài `overridable` → `SUGGESTION_OVERRIDE_INVALID`;
    id trùng hay thân chỉ có `suggestions` → `VALIDATION`. Không lượt nào tăng revision.
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    created = await put_layer(
        api_client,
        scene.project.id,
        floor.level_id,
        scene.owner,
        {"baseVersion": 0, "body": {"layer": wire(_layer(floor.level_id))}},
    )
    assert created.status_code == 200
    cases = [
        (
            [{"id": R1_ID, "state": "disabled"}, {"id": "DRESS-R1-NOPE", "state": "disabled"}],
            "SUGGESTION_UNKNOWN",
            "body.suggestionState.1.id",
        ),
        (
            [{"id": R1_ID, "state": "overridden", "override": {"pleatMm": 1}}],
            "SUGGESTION_OVERRIDE_INVALID",
            "body.suggestionState.0.override",
        ),
        (
            [{"id": L1_ID, "state": "disabled"}, {"id": L1_ID, "state": "active"}],
            "VALIDATION",
            "body.suggestionState.1.id",
        ),
    ]
    for rows, code, field in cases:
        response = await put_layer(
            api_client,
            scene.project.id,
            floor.level_id,
            scene.owner,
            {"baseVersion": 1, "body": {"suggestionState": rows}},
        )
        assert response.status_code == 422, rows
        assert (response.json()["code"], response.json()["field"]) == (code, field)
    only_list = await put_layer(
        api_client, scene.project.id, floor.level_id, scene.owner, {"baseVersion": 1, "body": {"suggestions": []}}
    )
    assert (only_list.status_code, only_list.json()["code"]) == (422, "VALIDATION")
    assert (await reread(db_session, floor.pk)).revision == 1


async def test_override_value_invalid__422(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """F9: giá trị `override` kiểm theo lược đồ `kind` ngay ở biên: chiều cao âm, quá trần 10 m, vật liệu sai mẫu →
    422 `SUGGESTION_OVERRIDE_INVALID` trỏ đúng dòng; không lượt nào ghi. Giá trị hợp lệ vẫn ghi được."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    put = functools.partial(put_layer, api_client, scene.project.id, floor.level_id, scene.owner)
    assert (await put({"baseVersion": 0, "body": {"layer": wire(_layer(floor.level_id))}})).status_code == 200
    for override in ({"heightMm": -500}, {"heightMm": 1_000_000_000_000}, {"material": "x"}):
        rows = [{"id": L1_ID, "state": "disabled"}, {"id": R1_ID, "state": "overridden", "override": override}]
        response = await put({"baseVersion": 1, "body": {"suggestionState": rows}})
        assert response.status_code == 422, override
        body = response.json()
        assert (body["code"], body["field"]) == ("SUGGESTION_OVERRIDE_INVALID", "body.suggestionState.1.override")
    assert (await reread(db_session, floor.pk)).revision == 1
    ok = [{"id": R1_ID, "state": "overridden", "override": {"heightMm": 120}}]
    assert (await put({"baseVersion": 1, "body": {"suggestionState": ok}})).status_code == 200


async def test_pipeline_merge__user_state_survives(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Trạng thái người dùng đặt (tắt L1, chỉnh R1) sống qua lượt `merge` của pipeline đổi `layer` (§3 bước 2)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))
    state: list[dict[str, Any]] = [
        {"id": L1_ID, "state": "disabled"},
        {"id": R1_ID, "state": "overridden", "override": {"material": "library:paint-grey"}},
    ]
    tuned = await _write(db_session, floor, scene.owner, fake_clock, base=1, state=state)
    assert tuned.suggestions_meta is not None
    assert tuned.suggestions_meta.basis_revision == 1
    merge = RecordingMerge(lambda base: base.model_copy(update={"walls": (make_wall(floor.level_id, thickness=150),)}))

    merged = await _write(db_session, floor, scene.owner, fake_clock, base=2, merge=merge, actor_id=SYSTEM_PIPELINE)

    assert (merged.revision, merge.calls) == (3, 1)
    assert merged.suggestions_meta is not None
    assert merged.suggestions_meta.basis_revision == 3
    items = _by_id(merged.suggestions)
    assert items[L1_ID].state == "disabled"
    assert (items[R1_ID].state, items[R1_ID].override) == ("overridden", {"material": "library:paint-grey"})


async def test_conflict_409__lists_suggestion_state_changes(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Bản ghi cũ → 409 liệt kê cả thay đổi `layer` lẫn `state`/`override` đề xuất của người kia; không gì bị đè."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    other = await make_user(db_session, name="Người thứ hai")
    await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))
    await _write(
        db_session,
        floor,
        other,
        fake_clock,
        base=1,
        layer=_layer(floor.level_id, thickness=250),
        state=[{"id": L1_ID, "state": "disabled"}, {"id": R1_ID, "state": "overridden", "override": {"heightMm": 120}}],
    )

    with pytest.raises(VersionConflictError) as caught:
        await _write(db_session, floor, scene.owner, fake_clock, base=1, layer=_layer(floor.level_id, thickness=300))

    assert caught.value.current_version == 2
    assert {change.entity_type for change in caught.value.remote_changes} == {"wall", "suggestion"}
    assert {
        (change.entity_id, change.field, json.dumps(change.value))
        for change in caught.value.remote_changes
        if change.entity_type == "suggestion"
    } == {
        (L1_ID, "state", '"disabled"'),
        (R1_ID, "state", '"overridden"'),
        (R1_ID, "override", '{"heightMm": 120}'),
    }
    assert _by_id((await reread(db_session, floor.pk)).suggestions)[L1_ID].state == "disabled"


async def test_conflict_409__state_only_change_blocks_stale_state(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """F4: người kia **chỉ** đổi trạng thái đề xuất (tắt L1, gỡ điều chỉnh R1; không đổi `layer`). Lượt cầm nền cũ gửi
    `suggestionState` của mình → 409 liệt kê đúng ba đổi ấy (gỡ điều chỉnh = `override` bị gỡ, `MISSING`); trạng
    thái của người kia còn nguyên."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    other = await make_user(db_session, name="Người thứ hai")
    await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))
    tuned = [{"id": R1_ID, "state": "overridden", "override": {"heightMm": 120}}]
    await _write(db_session, floor, scene.owner, fake_clock, base=1, state=tuned)
    await _write(db_session, floor, other, fake_clock, base=2, state=[{"id": L1_ID, "state": "disabled"}])

    with pytest.raises(VersionConflictError) as caught:
        await _write(
            db_session, floor, scene.owner, fake_clock, base=2, state=[*tuned, {"id": L1_ID, "state": "active"}]
        )

    remote = caught.value.remote_changes
    assert caught.value.current_version == 3
    assert {(c.entity_type, c.entity_id, c.field, c.value) for c in remote} == {
        ("suggestion", L1_ID, "state", "disabled"),
        ("suggestion", R1_ID, "state", "active"),
        ("suggestion", R1_ID, "override", MISSING),
    }
    assert {c.changed_by for c in remote} == {other.id}
    stored = _by_id((await reread(db_session, floor.pk)).suggestions)
    assert (stored[L1_ID].state, stored[R1_ID].state) == ("disabled", "active")


async def test_adopted_furniture_removed__suggestion_disabled(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """Món có `origin.suggestionId` → đề xuất `adopted` (`adoptedAs` = món) ngay lượt ghi ấy; `suggestionState` gửi
    lại cùng trạng thái không đổi gì; xoá món thì đề xuất về `disabled` (§3 bước 6)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    bed = _adopted_bed(floor.level_id)
    first = await _write(
        db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id, furniture=(bed,))
    )
    lamp = _by_id(first.suggestions)[L1_ID]
    assert (lamp.state, lamp.adopted_as) == ("adopted", FURNITURE_ID)
    same = await _write(db_session, floor, scene.owner, fake_clock, base=1, state=[{"id": L1_ID, "state": "adopted"}])
    assert not same.applied
    assert _by_id(same.suggestions)[L1_ID].adopted_as == FURNITURE_ID

    removed = await _write(db_session, floor, scene.owner, fake_clock, base=1, layer=_layer(floor.level_id))

    assert _by_id(removed.suggestions)[L1_ID].state == "disabled"


async def test_existing_floor__disabled_until_switched_on(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """§8 quyết định 1: tầng có dữ liệu trước M2 không sinh đề xuất; bật thì sinh ngay, tắt thì giữ bản đã lưu."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await make_floor_document(db_session, floor_pk=floor.pk, layer=_layer(floor.level_id), revision=3, clock=fake_clock)

    edited = await _write(
        db_session, floor, scene.owner, fake_clock, base=3, layer=_layer(floor.level_id, thickness=220)
    )
    assert (edited.revision, edited.suggestions, edited.suggestions_enabled) == (4, (), False)
    assert not {"suggestions", "suggestionsMeta", "suggestionsEnabled"} & set(await _stored(db_session, floor.pk))

    switched = await _write(db_session, floor, scene.owner, fake_clock, base=4, enabled=True)
    assert (switched.revision, switched.suggestions_enabled) == (5, True)
    assert _ours(switched.suggestions) == [(L1_ID, 5), (R1_ID, 5)]

    off = await _write(
        db_session, floor, scene.owner, fake_clock, base=5, layer=_layer(floor.level_id, thickness=300), enabled=False
    )
    assert (off.revision, off.suggestions_enabled, off.suggestions) == (6, False, switched.suggestions)
    assert "suggestionsEnabled" not in await _stored(db_session, floor.pk)


async def test_new_ruleset__applied_on_first_layer_change(
    db_session: AsyncSession, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """§8 quyết định 4: bộ luật mới không đổi revision nào khi không ai sửa `layer`; lượt đổi `layer` đầu tiên áp nó
    và ghi nhật ký sha cũ → mới. Bản `stale` không có dữ liệu người dùng bị dọn đúng lúc ấy (§3 bước 5).
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))
    raw = await _stored(db_session, floor.pk)
    old_sha = "0" * 64
    gone = {**raw["suggestions"][0], "id": f"DRESS-L1-{ROOM_ID}-x", "stale": {"since": 1, "reasonVi": "cũ"}}
    patched = {
        **raw,
        "suggestions": [*raw["suggestions"], gone],
        "suggestionsMeta": {**raw["suggestionsMeta"], "rulesetSha": old_sha},
    }
    await db_session.execute(
        update(FloorDocumentRow).where(FloorDocumentRow.floor_pk == floor.pk).values(document=patched)
    )

    idle = await _write(db_session, floor, scene.owner, fake_clock, base=1, layer=_layer(floor.level_id))
    assert idle.applied is False
    assert idle.suggestions_meta is not None
    assert idle.suggestions_meta.ruleset_sha == old_sha

    with caplog.at_level(logging.INFO, logger=writer.__name__):
        changed = await _write(
            db_session, floor, scene.owner, fake_clock, base=1, layer=_layer(floor.level_id, thickness=220)
        )

    assert changed.suggestions_meta is not None
    assert changed.suggestions_meta.ruleset_sha == load_ruleset().sha
    assert f"DRESS-L1-{ROOM_ID}-x" not in _by_id(changed.suggestions)
    assert _ours(changed.suggestions) == [(L1_ID, 1), (R1_ID, 1)]
    record = next(item for item in caplog.records if item.message == "suggestions_ruleset_changed")
    assert (record.ruleset_from, record.ruleset_to) == (old_sha, load_ruleset().sha)  # type: ignore[attr-defined]


async def test_cap_exceeded__422(
    db_session: AsyncSession, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vượt trần đề xuất mà không còn bản nào dọn được (đều có dữ liệu người dùng) → 422 `SUGGESTION_CAP`, không ghi."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))
    disabled = [{"id": L1_ID, "state": "disabled"}, {"id": R1_ID, "state": "disabled"}]
    stored = await _write(db_session, floor, scene.owner, fake_clock, base=1, state=disabled)
    monkeypatch.setattr(writer, "reconcile", functools.partial(reconcile, cap=1))

    with pytest.raises(AppError) as caught:
        await _write(db_session, floor, scene.owner, fake_clock, base=2, layer=_layer(floor.level_id, thickness=220))

    assert caught.value.code == SUGGESTION_CAP
    assert caught.value.params["count"] == len(stored.suggestions)
    assert (await reread(db_session, floor.pk)).revision == 2


def _golden_layers() -> dict[str, SpatialLayer]:
    """Ba lớp đo hiệu năng của gói `suggestions`: ch-04, ch-06 và syn-penthouse (37 tường, 10 phòng)."""
    return {
        case: SpatialLayer.model_validate(json.loads((GOLDEN_DIR / f"{case}.layer.json").read_text(encoding="utf-8")))
        for case in GOLDEN_CASES
    }


def test_reconcile_p95__golden_layers_within_budget(capsys: pytest.CaptureFixture[str]) -> None:
    """§8 quyết định 6: đo `reconcile` của một lượt ghi (có `prev`) trên ba lớp vàng, p95 của 20 lượt.

    In p95 từng lớp cùng cờ "trong ngân sách 150 ms" để báo cáo ghi lại; chỉ chặn ở `CEILING_MS` vì máy cổng và
    `coverage` làm số dao động, còn quyết định chuyển job nền là của chủ dự án, không phải của test.
    """
    ruleset = load_ruleset()
    report = {}
    for case, layer in _golden_layers().items():
        prev = reconcile((), layer, ruleset, revision=1).suggestions
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            reconcile(prev, layer, ruleset, revision=2)
            samples.append((time.perf_counter() - start) * 1000)
        p95 = round(statistics.quantiles(samples, n=20)[-1], 1)
        report[case] = (len(prev), p95, p95 <= BUDGET_MS)
    with capsys.disabled():
        print("\nreconcile (records, p95 ms, within 150 ms):", report)
    assert all(p95 <= CEILING_MS for _, p95, _ in report.values()), report


async def test_put_furniture_origin__adopts_then_disables(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """PUT #35 mang món có `origin.suggestionId` → đề xuất ấy `adopted`, `adoptedAs` = id món; lượt sau xoá món →
    `disabled`, bỏ `adoptedAs` (§3 bước 6)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    project_id, level_id = scene.project.id, floor.level_id
    first = await put_layer(api_client, project_id, level_id, scene.owner, write_body(0, layer=_layer(level_id)))
    bed = _adopted_bed(level_id)
    adopted = await put_layer(
        api_client, project_id, level_id, scene.owner, write_body(1, layer=_layer(level_id, furniture=(bed,)))
    )
    removed = await put_layer(api_client, project_id, level_id, scene.owner, write_body(2, layer=_layer(level_id)))

    assert first.status_code == adopted.status_code == removed.status_code == 200
    lamp = next(item for item in adopted.json()["suggestions"] if item["id"] == L1_ID)
    assert (lamp["state"], lamp["adoptedAs"]) == ("adopted", FURNITURE_ID)
    lamp = next(item for item in removed.json()["suggestions"] if item["id"] == L1_ID)
    assert lamp["state"] == "disabled"
    assert "adoptedAs" not in lamp


async def test_spatial_graph__floor_revisions_carry_suggestions(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """N15 `floorRevisions[]` mang ba khoá lớp đề xuất như N16 (editor nạp tầng qua N15); tầng tắt → vắng cả ba."""
    scene = await make_scene(db_session, floors=2)
    on, off = scene.floors
    await make_floor_document(db_session, floor_pk=off.pk, revision=3, clock=fake_clock)
    await db_session.commit()
    await put_layer(api_client, scene.project.id, on.level_id, scene.owner, write_body(0, layer=_layer(on.level_id)))

    graph = await api_client.get(graph_path(scene.project.id), headers=headers_of(scene.owner))
    layer = await api_client.get(layer_path(scene.project.id, on.level_id), headers=headers_of(scene.owner))

    assert graph.status_code == layer.status_code == 200
    first, second = graph.json()["floorRevisions"]
    keys = ("suggestions", "suggestionsMeta", "suggestionsEnabled")
    assert {key: first[key] for key in keys} == {key: layer.json()[key] for key in keys}
    assert first["suggestions"]
    assert second == {"floorId": off.level_id, "revision": 3}


def _plant(sid: str, x: int, state: str = "active") -> dict[str, Any]:
    """Đề xuất D5 (chậu cây 500 x 500 mm) dạng jsonb, tâm `(x, 2000)` trong phòng bếp của `_layer`."""
    return {
        "id": sid,
        "rule": "D5",
        "ruleVersion": 1,
        "kind": "item",
        "hostRef": {"room": ROOM_ID},
        "params": {
            "furnitureKind": "other",
            "modelId": "plant-ph",
            "centre": {"x": x, "y": 2000},
            "sizeMm": [500, 500, 800],
            "rotationDeg": 0,
        },
        "state": state,
        "createdRevision": 1,
    }


async def test_switched_on_suggestion_colliding__stale_with_reason(
    db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """F6: lượt chỉ bật lại một đề xuất (không sinh lại) mà nó chồng lên món luật đặt lúc nó tắt → bản vừa bật thành
    `stale` có lý do va chạm, trạng thái người dùng giữ `active`; món kia không đổi. Không còn va chạm `rule` nào."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await _write(db_session, floor, scene.owner, fake_clock, base=0, layer=_layer(floor.level_id))
    raw = await _stored(db_session, floor.pk)
    kept, back = f"DRESS-D5-{ROOM_ID}-a", f"DRESS-D5-{ROOM_ID}-b"
    patched = {**raw, "suggestions": [*raw["suggestions"], _plant(kept, 2000), _plant(back, 2100, "disabled")]}
    patched["suggestions"].sort(key=lambda item: item["id"])
    await db_session.execute(
        update(FloorDocumentRow).where(FloorDocumentRow.floor_pk == floor.pk).values(document=patched)
    )

    result = await _write(db_session, floor, scene.owner, fake_clock, base=1, state=[])

    items = _by_id(result.suggestions)
    assert (items[back].state, items[kept].stale) == ("active", None)
    stale = items[back].stale
    assert stale is not None
    assert (stale.since, kept in stale.reason_vi) == (2, True)
    assert result.suggestions_meta is not None
    assert result.suggestions_meta.basis_revision == 1
