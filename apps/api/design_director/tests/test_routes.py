"""N47 `GET …/design-concept`, N48 `POST …/design-concept` (AID-01, cổng case).

Postgres và broker thật (K23). Case chung C04 C05 C12 C13 C25 ở `test_common.py`.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.design_director import state
from apps.api.projects.tests.test_routes_common import headers_of
from packages.db.models.auth import User
from packages.db.models.projects import Project
from packages.messaging.redis import SyncRedis, broker_redis_sync
from packages.testing.factories.auth import make_user
from packages.testing.factories.design_concept import RESULT, make_design_concept
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.design_director import TEST_QUOTA
from packages.testing.fixtures.messaging import queued_payloads

QUEUE: Final = "default"
MISSING_PROJECT: Final = "prj_" + "0" * 26


@pytest.fixture
def broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Broker thật với hàng `default` rỗng ở đầu và cuối test."""
    client = broker_redis_sync()
    client.delete(QUEUE)
    try:
        yield client
    finally:
        client.delete(QUEUE)
        client.close()


def _path(project_id: str) -> str:
    """Đường N47/N48."""
    return f"/api/projects/{project_id}/design-concept"


def _tasks(broker: SyncRedis) -> list[object]:
    """Id concept của mọi task `design.director.run` đang chờ."""
    return [p["concept_id"] for p in queued_payloads(broker, QUEUE) if "concept_id" in p]


async def _project(db: AsyncSession, *, viewer: bool = False) -> tuple[User, Project, list[User]]:
    """Chủ dự án (và một viewer nếu cần) đã commit."""
    owner = await make_user(db, role="engineer")
    members = [await make_user(db, role="viewer")] if viewer else []
    project = await make_project(db, owner=owner, members=members)
    await db.commit()
    return owner, project, members


async def _rows(db: AsyncSession, project_id: str) -> list[dict[str, object]]:
    """Mọi concept của dự án, cũ trước."""
    found = await db.execute(
        text("SELECT * FROM design_concepts WHERE project_id = :p ORDER BY created_at, id"), {"p": project_id}
    )
    return [dict(row) for row in found.mappings()]


async def test_design_director_read_state__C01(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """`active` = `selected_at` mới nhất; `suggestedStyleId` = kiểu của lượt đề xuất `done` mới nhất."""
    owner, project, _ = await _project(db_session)
    now = datetime.now(UTC)
    await make_design_concept(
        db_session, project_id=project.id, status="done", suggested=True, style_id="indochine", selected_at=now
    )
    picked = await make_design_concept(
        db_session,
        project_id=project.id,
        status="done",
        style_id="rustic",
        result={**RESULT, "nameVi": "Mộc"},
        selected_at=now + timedelta(seconds=1),
    )

    response = await api_client.get(_path(project.id), headers=headers_of(owner))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert body["suggestedStyleId"] == "indochine"
    assert body["active"]["id"] == picked.id
    assert body["active"]["result"] == {**RESULT, "nameVi": "Mộc"}
    assert body["active"]["styleId"] == "rustic"


async def test_design_director_read_state__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Admin hệ thống không là thành viên → 404 `resource:"project"`."""
    _, project, _ = await _project(db_session)
    outsider = await make_user(db_session)
    await db_session.commit()
    response = await api_client.get(_path(project.id), headers=headers_of(outsider, role="admin"))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_design_director_read_state__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Dự án không tồn tại → 404."""
    owner, _, _ = await _project(db_session)
    response = await api_client.get(_path(MISSING_PROJECT), headers=headers_of(owner))
    assert response.status_code == 404


async def test_design_director_read_state__C17(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Chưa có concept, tính năng tắt → chỉ `enabled: false`; `suggestedStyleId`, `active` vắng khoá."""
    owner, project, _ = await _project(db_session)
    response = await api_client.get(_path(project.id), headers=headers_of(owner))
    assert response.status_code == 200
    assert response.json() == {"enabled": False}


async def test_design_director_select_concept__C01(
    db_session: AsyncSession, api_client: AsyncClient, broker: SyncRedis, design_director_on: None
) -> None:
    """Kiểu mới → 202 `queued`, dòng đã chọn, task `design.director.run` sau commit."""
    owner, project, _ = await _project(db_session)

    response = await api_client.post(_path(project.id), json={"styleId": "japandi"}, headers=headers_of(owner))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["id"].startswith("dcn_")
    assert (body["status"], body["styleId"], body["suggested"]) == ("queued", "japandi", False)
    rows = await _rows(db_session, project.id)
    assert [(r["id"], r["created_by"], r["selected_at"] is not None) for r in rows] == [(body["id"], owner.id, True)]
    assert _tasks(broker) == [body["id"]]


async def test_design_director_select_concept__C02(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """Mã kiểu lạ → 422 `VALIDATION` có `field`."""
    owner, project, _ = await _project(db_session)
    response = await api_client.post(_path(project.id), json={"styleId": "baroque"}, headers=headers_of(owner))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"
    assert "field" in response.json()


async def test_design_director_select_concept__C03(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """Khoá lạ → 422 `VALIDATION`, không mở lượt."""
    owner, project, _ = await _project(db_session)
    response = await api_client.post(
        _path(project.id), json={"styleId": "japandi", "force": True}, headers=headers_of(owner)
    )
    assert response.status_code == 422
    assert await _rows(db_session, project.id) == []


async def test_design_director_select_concept__C06(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """Admin hệ thống không là thành viên → 404 `resource:"project"`."""
    _, project, _ = await _project(db_session)
    outsider = await make_user(db_session)
    await db_session.commit()
    response = await api_client.post(_path(project.id), json={}, headers=headers_of(outsider, role="admin"))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_design_director_select_concept__C07(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """Thành viên `viewer` (không có `project.settings.edit`) → 403, không mở lượt."""
    _, project, members = await _project(db_session, viewer=True)
    response = await api_client.post(
        _path(project.id), json={"styleId": "japandi"}, headers=headers_of(members[0], role="viewer")
    )
    assert response.status_code == 403
    assert await _rows(db_session, project.id) == []


async def test_design_director_select_concept__C08(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """Dự án không tồn tại → 404."""
    owner, _, _ = await _project(db_session)
    response = await api_client.post(_path(MISSING_PROJECT), json={}, headers=headers_of(owner))
    assert response.status_code == 404


async def test_design_director_select_concept__C14(
    db_session: AsyncSession, api_client: AsyncClient, broker: SyncRedis, design_director_on: None
) -> None:
    """Hai lượt chọn cùng kiểu song song → cả hai 202 cùng một concept; một dòng, một task."""
    owner, project, _ = await _project(db_session)

    responses = await asyncio.gather(
        *(api_client.post(_path(project.id), json={"styleId": "tropical"}, headers=headers_of(owner)) for _ in range(2))
    )

    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(await _rows(db_session, project.id)) == 1
    assert len(_tasks(broker)) == 1


async def test_select_concept_returns_the_winner_after_losing_the_insert_race(
    db_session: AsyncSession,
    api_client: AsyncClient,
    broker: SyncRedis,
    design_director_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C14 tất định: tìm đầu trượt, chèn đụng lượt mở cùng khoá → tìm lại, trả lượt thắng, không gửi task."""
    owner, project, _ = await _project(db_session)
    key = await state.compute_input_key(db_session, project.id, "tropical")
    winner = await make_design_concept(db_session, project_id=project.id, style_id="tropical", input_key=key)
    real_find = state.find_reusable
    calls = 0

    async def find_after_first_miss(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_find(*args, **kwargs)

    monkeypatch.setattr(state, "find_reusable", find_after_first_miss)

    response = await api_client.post(_path(project.id), json={"styleId": "tropical"}, headers=headers_of(owner))

    assert response.status_code == 202, response.text
    assert response.json()["id"] == winner.id
    assert len(await _rows(db_session, project.id)) == 1
    assert _tasks(broker) == []


async def test_design_director_select_concept__C17(
    db_session: AsyncSession, api_client: AsyncClient, broker: SyncRedis, design_director_on: None
) -> None:
    """`{}` = đề xuất AI: lượt `queued` mới vắng `styleId`, `failureCode`, `startedAt`, `endedAt`, `result`."""
    owner, project, _ = await _project(db_session)
    response = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))
    assert response.status_code == 202
    assert set(response.json()) == {"id", "suggested", "status", "createdAt"}
    assert response.json()["suggested"] is True


async def test_select_concept_reuses_a_concept_with_the_same_input(
    db_session: AsyncSession, api_client: AsyncClient, broker: SyncRedis, design_director_on: None
) -> None:
    """Concept `done` cùng `input_key` → 202 trả lại nó, đặt `selected_at`, không gửi task; `styleId: null` cũng vậy."""
    owner, project, _ = await _project(db_session)
    key = await state.compute_input_key(db_session, project.id, "japandi")
    auto_key = await state.compute_input_key(db_session, project.id, None)
    done = await make_design_concept(
        db_session, project_id=project.id, status="done", style_id="japandi", input_key=key
    )
    auto = await make_design_concept(
        db_session, project_id=project.id, status="running", suggested=True, input_key=auto_key
    )

    first = await api_client.post(_path(project.id), json={"styleId": "japandi"}, headers=headers_of(owner))
    second = await api_client.post(_path(project.id), json={"styleId": None}, headers=headers_of(owner))

    assert (first.status_code, first.json()["id"], first.json()["status"]) == (202, done.id, "done")
    assert (second.status_code, second.json()["id"], second.json()["status"]) == (202, auto.id, "running")
    assert len(await _rows(db_session, project.id)) == 2
    assert _tasks(broker) == []
    state_body = (await api_client.get(_path(project.id), headers=headers_of(owner))).json()
    assert state_body["active"]["id"] == auto.id


async def test_select_concept_refuses_over_the_daily_quota(
    db_session: AsyncSession, api_client: AsyncClient, design_director_on: None
) -> None:
    """Chủ dự án đã mở `TEST_QUOTA` lượt hôm nay (kể cả ở dự án khác) → 429 `DESIGN_DIRECTOR_QUOTA`."""
    owner, project, _ = await _project(db_session)
    other = await make_project(db_session, owner=owner)
    await db_session.commit()
    for _ in range(TEST_QUOTA):
        await make_design_concept(db_session, project_id=other.id, status="failed")

    response = await api_client.post(_path(project.id), json={"styleId": "japandi"}, headers=headers_of(owner))

    assert response.status_code == 429
    assert response.json()["code"] == "DESIGN_DIRECTOR_QUOTA"
    assert "Retry-After" in response.headers
    assert await _rows(db_session, project.id) == []


async def test_select_concept_refuses_when_disabled(
    db_session: AsyncSession, api_client: AsyncClient, design_director_off: None
) -> None:
    """Bật công tắc mà thiếu khoá → 503 `DESIGN_DIRECTOR_DISABLED`; N47 báo `enabled: false`."""
    owner, project, _ = await _project(db_session)

    response = await api_client.post(_path(project.id), json={"styleId": "japandi"}, headers=headers_of(owner))

    assert response.status_code == 503
    assert response.json()["code"] == "DESIGN_DIRECTOR_DISABLED"
    assert (await api_client.get(_path(project.id), headers=headers_of(owner))).json() == {"enabled": False}
