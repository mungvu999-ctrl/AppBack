"""#35 `PUT .../spatial/layer` — GV: C01 C02 C03 C06 C07 C08 C09 C09b C14 C16 C17.

Chỉ phần **HTTP** của B3-03: ánh xạ lỗi của `write_layer` sang status/`code`, thứ tự quyền →
guard 428 → Pydantic, và thân dây của 200/409. Luật nghiệp vụ mức hàm (tỉ lệ, `merge`, tranh
id, bảng đếm) đã có test riêng ở `test_writer*.py` — không lặp lại ở đây.

Postgres thật, thành viên thật, không mock session và không mock `write_layer` (K22, K23): mọi
khẳng định "đã lưu" đọc lại bằng chính route N16 hay bằng một truy vấn mới.
"""

import asyncio
import unicodedata
from typing import Any, Final

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.projects.tests.test_routes_common import headers_of
from apps.api.spatial_read.tests._helpers import make_scene
from apps.api.spatial_write.tests._helpers import log_count, log_rows, make_room, make_wall, simple_layer
from apps.api.spatial_write.tests._route_helpers import (
    layer_path,
    make_furniture,
    put_layer,
    wire,
    write_body,
)
from packages.db.models.floors import FloorRow
from packages.domain.spatial import clear_area_m2
from packages.domain.spatial.samples import PASCAL_FIELDS, with_pascal_fields
from packages.testing.factories.auth import make_user
from packages.testing.factories.spatial import sample_floor_layer
from packages.testing.fixtures.clock import FakeClock

MISSING_FLOOR: Final = "L-NOSUCHFLOOR"
"""Một `level_id` không có trong bất kỳ dự án nào — C08 "tài nguyên không tồn tại"."""

MILLIS: Final = 4
"""Độ dài `sssZ` của phần sau dấu chấm trong ngày giờ dây (W3)."""


def _raw_layer(wall: dict[str, Any], *, rooms: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Lớp **dây thô** một tường (và phòng nếu có): C02 cần giá trị mà mô hình miền từ chối."""
    return {"walls": [wall], "openings": [], "rooms": rooms or [], "furniture": []}


def _bad_bodies(level_id: str) -> dict[str, tuple[dict[str, Any], str]]:
    """Bảy dạng thân sai của C02 → `(thân, field mong đợi)`; mỗi dạng chạm một luật khác."""
    wall = wire(make_wall(level_id))
    room = wire(make_room(level_id))
    return {
        "thickness_not_integer": (
            {"baseVersion": 0, "body": {"layer": _raw_layer(wall | {"thicknessMm": 12.5})}},
            "body.layer.walls.0.thicknessMm",
        ),
        "negative_base": (write_body(-1, scale=10.0), "baseVersion"),
        "zero_scale": (write_body(0, scale=0), "body.scaleMillimetresPerPixel"),
        "huge_scale": (write_body(0, scale=1e22), "body.scaleMillimetresPerPixel"),
        "huge_integer_scale": (write_body(0, scale=10**310), "body.scaleMillimetresPerPixel"),
        "empty_body": ({"baseVersion": 0, "body": {}}, "body"),
        "evidence_without_scale": (
            {"baseVersion": 0, "body": {"scaleEvidence": "machineAccepted"}},
            "body.scaleEvidence",
        ),
        "unknown_evidence": (
            {"baseVersion": 0, "body": {"scaleMillimetresPerPixel": 10.0, "scaleEvidence": "guessed"}},
            "body.scaleEvidence",
        ),
        "bidi_room_name": (
            {"baseVersion": 0, "body": {"layer": _raw_layer(wall, rooms=[room | {"name": "B‮p"}])}},
            "body.layer.rooms.0.name",
        ),
    }


async def test_spatial_write_layer__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tầng chưa có tài liệu, `baseVersion 0`, lớp toà mẫu → 200 `revision 1`; N16 trả đúng lớp ấy.

    Hàng catalog: [BE4-G-208] [BE4-G-308].
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    layer = sample_floor_layer(0, level_id=floor.level_id)

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=layer))

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 1
    assert len(body["layer"]["walls"]) == len(layer.walls)
    assert {wall["levelId"] for wall in body["layer"]["walls"]} == {floor.level_id}
    read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
    assert read.status_code == 200
    assert read.json()["revision"] == 1
    assert read.json()["layer"] == body["layer"]


async def test_spatial_write_layer__C01_clear_area(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """BENCH3D-09: `clearAreaM2` chỉ ở phía đọc — PUT không mang nó → 200 và phản hồi tính lại cho mọi phòng
    (FE thay kho bằng phản hồi); PUT mang nó → 422, vì `Room` vào là `extra="forbid"` (FE bỏ trường trước khi gửi).
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    layer = sample_floor_layer(0, level_id=floor.level_id)

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=layer))

    assert response.status_code == 200
    rooms = response.json()["layer"]["rooms"]
    assert rooms
    for room, source in zip(rooms, layer.rooms, strict=True):
        assert room["clearAreaM2"] == float(clear_area_m2(source.outline, layer.walls) or -1)
        assert room["clearAreaM2"] <= room["areaM2"]

    echoed = write_body(1, layer=layer)
    echoed["body"]["layer"]["rooms"][0]["clearAreaM2"] = rooms[0]["clearAreaM2"]
    rejected = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, echoed)
    assert rejected.status_code == 422


async def test_spatial_write_layer__C01_pascal_fields(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """FE3D-B J-v3: PUT nhận sơn tường, mặt sàn phòng, biến thể/mô hình/ô vật liệu đồ đạc; N16 trả lại đúng."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    layer = with_pascal_fields(sample_floor_layer(0, level_id=floor.level_id))

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=layer))

    assert response.status_code == 200
    read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
    stored = read.json()["layer"]
    for name, extra in PASCAL_FIELDS.items():
        assert stored[name][0].items() >= extra.items()
    assert stored == response.json()["layer"]


@pytest.mark.parametrize(("swing", "status"), [("sliding", 200), ("left", 422)])
async def test_spatial_write_layer__C01_door_style(
    api_client: httpx.AsyncClient, db_session: AsyncSession, swing: str, status: int
) -> None:
    """EDITOR3D-DOT2: `doorStyle` khứ hồi trên cửa đi lùa; trên cửa bản lề thì cả thân 422 (FE bỏ trường)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    payload = write_body(0, layer=sample_floor_layer(0, level_id=floor.level_id))
    openings = payload["body"]["layer"]["openings"]
    openings[0] = {**openings[0], "kind": "door", "swing": swing, "doorStyle": "pocket"}

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, payload)

    assert response.status_code == status
    if status == 200:
        read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
        assert read.json()["layer"]["openings"][0]["doorStyle"] == "pocket"


async def test_spatial_write_layer__C01_scale(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thân chỉ mang tỉ lệ (F-04c "áp cho mọi tầng") → 200 và N16 hết `scaleStatus`.

    Hàng catalog: [BE4-G-209].
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, scale=12.7))

    assert response.status_code == 200
    assert response.json()["revision"] == 1
    read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
    assert "scaleStatus" not in read.json()
    assert read.json()["level"]["scaleMillimetresPerPixel"] == 12.7


async def test_spatial_write_layer__C01_edited(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """PUT dời đỉnh một tường AI (thân gửi `edited: false`) → 200; N16 đọc lại thấy `edited: true` (H1).

    Golden H1: tường giữ `source: "ai"`, `reviewed: false` — sửa không phải duyệt. Tầng mồi qua một PUT như
    các ca route khác (ghi thẳng `db_session` chưa commit làm route thua đua khoá → 503); luật "dời đỉnh mới
    thành `edited`" do `test_writer.py::test_write_layer_marks_moved_ai_wall_edited` giữ.
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    first = await put_layer(
        api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=simple_layer(floor.level_id))
    )
    assert first.status_code == 200, first.text
    moved = simple_layer(floor.level_id, walls=(make_wall(floor.level_id, length=1500),))

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(1, layer=moved))

    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
    wall = read.json()["layer"]["walls"][0]
    assert (wall["edited"], wall["source"], wall["reviewed"]) == (True, "ai", False)
    assert wall["centreline"]["end"] == {"x": 1500, "y": 0}
    assert read.json()["layer"] == response.json()["layer"]


@pytest.mark.parametrize("case", list(_bad_bodies("L-DUMMYLEVEL")))
async def test_spatial_write_layer__C02(api_client: httpx.AsyncClient, db_session: AsyncSession, case: str) -> None:
    """mm không nguyên, `baseVersion` âm, tỉ lệ 0 hay quá lớn, `body` rỗng, tên phòng U+202E → 422 kèm `field`.

    Hàng catalog: [BE4-G-210] [BE4-G-309].

    Hàng catalog: [B05-G-054].
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    payload, field = _bad_bodies(floor.level_id)[case]

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, payload)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION"
    assert body["field"] == field


@pytest.mark.parametrize("where", ["wall", "body", "envelope"])
async def test_spatial_write_layer__C03(api_client: httpx.AsyncClient, db_session: AsyncSession, where: str) -> None:
    """Khoá lạ trong một bức tường, trong `body`, hay ở vỏ ngoài → 422 `VALIDATION`.

    Hàng catalog: [BE4-G-310].
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    payload = write_body(0, layer=simple_layer(floor.level_id))
    if where == "wall":
        payload["body"]["layer"]["walls"][0]["nonsense"] = 1
    elif where == "body":
        payload["body"]["nonsense"] = 1
    else:
        payload["nonsense"] = 1

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


async def test_spatial_write_layer__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống không phải thành viên → 404 `resource:"project"`, không phải 403 (BE-00 §4).

    Hàng catalog: [BE4-G-212] [BE4-G-311].

    Hàng catalog: [B05-G-049].
    Catalog: B09-spw-003.
    """
    scene = await make_scene(db_session)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await put_layer(
        api_client, scene.project.id, scene.floors[0].level_id, outsider, write_body(0, scale=10.0)
    )

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_spatial_write_layer__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Viewer là thành viên nhưng ma trận `layer.edit` cho `False` → 403 `FORBIDDEN`.

    Hàng catalog: [BE4-G-211] [BE4-G-312].

    Hàng catalog: [B05-G-048].
    Catalog: B09-spw-002.
    """
    viewer = await make_user(db_session, role="viewer")
    scene = await make_scene(db_session, members=[viewer])

    response = await put_layer(
        api_client, scene.project.id, scene.floors[0].level_id, viewer, write_body(0, scale=10.0)
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("missing", ["absent", "soft_deleted", "other_project"])
async def test_spatial_write_layer__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock, missing: str
) -> None:
    """Tầng không có, tầng xoá mềm, tầng của dự án khác → 404 `resource:"floor"`.

    Hàng catalog: [BE4-G-313].

    Hàng catalog: [B05-G-050].
    """
    scene = await make_scene(db_session)
    other = await make_scene(db_session)
    floor_id = MISSING_FLOOR
    if missing == "soft_deleted":
        floor_id = scene.floors[0].level_id
        await db_session.execute(
            update(FloorRow).where(FloorRow.pk == scene.floors[0].pk).values(deleted_at=fake_clock.now())
        )
        await db_session.commit()
    elif missing == "other_project":
        floor_id = other.floors[0].level_id

    response = await put_layer(api_client, scene.project.id, floor_id, scene.owner, write_body(0, scale=10.0))

    assert response.status_code == 404
    assert response.json()["resource"] == "floor"


async def test_spatial_write_layer__C09_stale(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """A đổi `thicknessMm` và xoá đồ đạc ở bản ghi 1-2; B gửi `baseVersion 0` → 409 đúng thân W20.

    Hàng catalog: [BE4-G-213] [BE4-G-314].
    """
    second = await make_user(db_session, name="Người thứ hai")
    scene = await make_scene(db_session, members=[second])
    floor = scene.floors[0]
    start = simple_layer(floor.level_id, furniture=(make_furniture(floor.level_id),))
    first = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=start))
    assert first.status_code == 200
    thicker = simple_layer(floor.level_id, walls=(make_wall(floor.level_id, thickness=250),))
    second_write = await put_layer(
        api_client, scene.project.id, floor.level_id, scene.owner, write_body(1, layer=thicker)
    )
    assert second_write.status_code == 200

    response = await put_layer(
        api_client, scene.project.id, floor.level_id, second, write_body(0, layer=simple_layer(floor.level_id))
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["currentVersion"] == 2
    changes = {(item["entityType"], item["field"]): item for item in body["remoteChanges"]}
    thickness = changes[("wall", "thickness_mm")]
    assert thickness["value"] == 250
    assert thickness["changedBy"] == scene.owner.id
    assert thickness["changedByName"] == scene.owner.name
    assert thickness["changedAt"].endswith("Z")
    assert len(thickness["changedAt"].split(".")[1]) == MILLIS
    assert "value" not in changes[("furniture", "__deleted__")]


async def test_spatial_write_layer__C09_missing(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thân không có khoá `baseVersion` → 428 `PRECONDITION_REQUIRED`, trước cả Pydantic.

    Hàng catalog: [BE4-G-214] [BE4-G-315].

    Hàng catalog: [B05-G-047].
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]

    response = await put_layer(
        api_client, scene.project.id, floor.level_id, scene.owner, {"body": {"scaleMillimetresPerPixel": 10.0}}
    )

    assert response.status_code == 428
    assert response.json()["code"] == "PRECONDITION_REQUIRED"


async def test_spatial_write_layer__C09b(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Cùng người gửi lại **đúng** thân với base đã lưu → 200 `revision 1`, nhật ký không thêm dòng.

    Hàng catalog: [BE4-G-215] [BE4-G-316].

    Hàng catalog: [B05-G-046].
    """
    second = await make_user(db_session, name="Người thứ hai")
    scene = await make_scene(db_session, members=[second])
    floor = scene.floors[0]
    payload = write_body(0, layer=simple_layer(floor.level_id))
    floor_pk, level_id, project_id = floor.pk, floor.level_id, scene.project.id
    first = await put_layer(api_client, project_id, level_id, scene.owner, payload)
    assert first.status_code == 200
    await db_session.commit()
    before = await log_count(db_session, floor_pk)

    repeat = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, payload)

    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    assert repeat.json()["revision"] == 1
    await db_session.commit()
    assert await log_count(db_session, floor_pk) == before
    other_body = write_body(0, layer=simple_layer(level_id, walls=(make_wall(level_id, thickness=250),)))
    assert (await put_layer(api_client, project_id, level_id, scene.owner, other_body)).status_code == 409
    assert (await put_layer(api_client, project_id, level_id, second, payload)).status_code == 409


async def test_spatial_write_layer__C16(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tên phòng NFD → response, `document` và `value` của nhật ký đều NFC.

    Hàng catalog: [BE4-G-318].
    """
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    nfd = unicodedata.normalize("NFD", "Bếp Nhỏ")
    assert nfd != "Bếp Nhỏ"
    floor_pk, level_id, project_id = floor.pk, floor.level_id, scene.project.id

    response = await put_layer(
        api_client,
        project_id,
        level_id,
        scene.owner,
        write_body(0, layer=simple_layer(level_id, rooms=(make_room(level_id, name=nfd),))),
    )

    assert response.status_code == 200
    assert response.json()["layer"]["rooms"][0]["name"] == "Bếp Nhỏ"
    read = await api_client.get(layer_path(project_id, level_id), headers=headers_of(scene.owner))
    assert read.json()["layer"]["rooms"][0]["name"] == "Bếp Nhỏ"
    await db_session.commit()
    assert [row.value for row in await log_rows(db_session, floor_pk) if row.field == "name"] == ["Bếp Nhỏ"]


async def test_spatial_write_layer__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Đồ đạc chưa gắn phòng → response **vắng** khoá `roomId`, không phải `null` (W2)."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    layer = simple_layer(floor.level_id, furniture=(make_furniture(floor.level_id),))

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=layer))

    assert response.status_code == 200
    item = response.json()["layer"]["furniture"][0]
    assert "roomId" not in item
    assert item["id"] == "F-ROUTEFURN1"


async def test_spatial_write_layer__C14(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Hai session thật cùng `baseVersion 0`, thân khác → đúng một 200, một 409; nhật ký của bên thắng.

    Mỗi request của `api_client` mở session riêng qua `AppRoute`, nên đây là hai giao dịch
    Postgres thật chạy song song — không phải hai lượt gọi hàm trong cùng một session.

    Hàng catalog: [BE4-G-317].

    Hàng catalog: [B05-G-059].
    """
    second = await make_user(db_session, name="Người thứ hai")
    scene = await make_scene(db_session, members=[second])
    floor = scene.floors[0]
    floor_pk, level_id, project_id = floor.pk, floor.level_id, scene.project.id
    writers = (scene.owner.id, second.id)

    results = await asyncio.gather(
        put_layer(api_client, project_id, level_id, scene.owner, write_body(0, layer=simple_layer(level_id))),
        put_layer(
            api_client,
            project_id,
            level_id,
            second,
            write_body(0, layer=simple_layer(level_id, walls=(make_wall(level_id, thickness=250),))),
        ),
    )

    statuses = sorted(response.status_code for response in results)
    await db_session.commit()
    rows = await log_rows(db_session, floor_pk)
    winner = next(index for index, response in enumerate(results) if response.status_code == 200)
    loser = next(response for response in results if response.status_code == 409)
    print(f"C14 spatial_write_layer: status = {[r.status_code for r in results]}, dòng nhật ký = {len(rows)}")
    assert statuses == [200, 409]
    assert results[winner].json()["revision"] == 1
    assert {row.revision for row in rows} == {1}
    assert {row.changed_by for row in rows} == {writers[winner]}
    assert loser.json()["currentVersion"] == 1
    assert len(loser.json()["remoteChanges"]) >= 1


async def test_spatial_write_layer__C01_curved_wall(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """PUT tường cong `curveOffsetMm` → N16 trả lại đúng giá trị; tường thẳng vắng khoá; vượt nửa dây → 422."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    curved = make_wall(floor.level_id).model_copy(update={"curve_offset_mm": -400})
    layer = simple_layer(floor.level_id, walls=(curved,))

    response = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, write_body(0, layer=layer))

    assert response.status_code == 200, response.text
    read = await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))
    assert read.json()["layer"]["walls"][0]["curveOffsetMm"] == -400
    straight = wire(make_wall(floor.level_id))
    assert "curveOffsetMm" not in straight
    too_deep = {"baseVersion": 1, "body": {"layer": _raw_layer(straight | {"curveOffsetMm": 600})}}
    rejected = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, too_deep)
    assert rejected.status_code == 422
