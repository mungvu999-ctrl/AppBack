"""Quyền máy chủ cho xuất mô hình và liên kết chia sẻ: từng vai, 404 trước 403, hết hạn, thu hồi."""

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.project_settings.tests.support import headers_of, seed_project
from packages.db.models.auth import User
from packages.db.models.projects import Project
from packages.testing.factories.auth import make_user
from packages.testing.fixtures.clock import FakeClock


async def _setup(db: AsyncSession) -> tuple[Project, User, User, User, User]:
    """Dự án có admin, engineer, viewer là thành viên + một admin ngoài dự án."""
    admin = await make_user(db, role="admin")
    engineer = await make_user(db, role="engineer")
    viewer = await make_user(db, role="viewer")
    outsider = await make_user(db, role="admin")
    project = await seed_project(db, owner=engineer, members=[admin, viewer])
    return project, admin, engineer, viewer, outsider


async def test_export_roles(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """admin/engineer 200, viewer 403, người ngoài (kể cả admin) 404."""
    project, admin, engineer, viewer, outsider = await _setup(db_session)
    path = f"/api/projects/{project.id}/exports"
    for user in (admin, engineer):
        response = await api_client.post(path, headers=headers_of(user), json={})
        assert response.status_code == 200, response.text
        assert "floorRevisions" in response.json()
    assert (await api_client.post(path, headers=headers_of(viewer), json={})).status_code == 403
    response = await api_client.post(path, headers=headers_of(outsider), json={})
    assert response.status_code == 404
    assert response.json()["resource"] == "project"
    assert (await api_client.post(path, json={})).status_code == 401


async def test_share_links_roles_and_lifecycle(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Tạo/liệt kê/thu hồi theo vai; tuyến công khai mở được tới khi thu hồi."""
    project, admin, engineer, viewer, outsider = await _setup(db_session)
    path = f"/api/projects/{project.id}/share-links"
    assert (await api_client.post(path, headers=headers_of(viewer), json={})).status_code == 403
    assert (await api_client.get(path, headers=headers_of(viewer))).status_code == 403
    assert (await api_client.post(path, headers=headers_of(outsider), json={})).status_code == 404

    created = await api_client.post(path, headers=headers_of(engineer), json={"permission": "view", "label": "khách"})
    assert created.status_code == 201, created.text
    link = created.json()
    assert link["status"] == "active"
    assert link["passwordProtected"] is False
    token = link["url"].rsplit("/", 1)[1]
    assert len(token) >= 40

    refused = await api_client.post(path, headers=headers_of(engineer), json={"password": "bi-mat-123"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "password"

    listed = await api_client.get(path, headers=headers_of(admin))
    assert [item["id"] for item in listed.json()] == [link["id"]]

    opened = await api_client.get(f"/api/share/{token}")
    assert opened.status_code == 200, opened.text
    assert opened.json()["link"]["id"] == link["id"]

    item = f"{path}/{link['id']}"
    assert (await api_client.delete(item, headers=headers_of(viewer))).status_code == 403
    assert (await api_client.delete(item, headers=headers_of(outsider))).status_code == 404
    assert (await api_client.delete(f"{path}/shl_nope", headers=headers_of(admin))).status_code == 404
    revoked = await api_client.delete(item, headers=headers_of(admin))
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    gone = await api_client.get(f"/api/share/{token}")
    assert (gone.status_code, gone.json()["code"], gone.json()["reason"]) == (404, "NOT_FOUND", "revoked")


async def test_share_link_expired_and_unknown_token(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Hết hạn → `expired` trong danh sách và 404 ở tuyến công khai; token bịa → 404."""
    project, _, engineer, _, _ = await _setup(db_session)
    path = f"/api/projects/{project.id}/share-links"
    expires = (fake_clock.now() + timedelta(hours=1)).isoformat()
    created = await api_client.post(path, headers=headers_of(engineer), json={"expiresAt": expires})
    assert created.status_code == 201, created.text
    token = created.json()["url"].rsplit("/", 1)[1]
    assert (await api_client.get(f"/api/share/{token}")).status_code == 200
    fake_clock.advance(timedelta(hours=2))
    expired = await api_client.get(f"/api/share/{token}")
    assert (expired.status_code, expired.json()["reason"]) == (404, "expired")
    assert (await api_client.get(path, headers=headers_of(engineer))).json()[0]["status"] == "expired"
    unknown = await api_client.get("/api/share/not-a-real-token")
    assert unknown.status_code == 404
    assert "reason" not in unknown.json()


# ---------------------------------------------------------------------------
# Case theo tên (CASE.md §2.2): N39 export (Đ), #47 list (Đ), #48 create (G), #49 revoke (G), N38 open (C)
# ---------------------------------------------------------------------------


def _links_path(project: Project) -> str:
    return f"/api/projects/{project.id}/share-links"


async def _create_link(client: httpx.AsyncClient, project: Project, user: User, **body: object) -> dict[str, Any]:
    """Tạo một liên kết qua HTTP; trả thân 201."""
    response = await client.post(_links_path(project), headers=headers_of(user), json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_share_links_export__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Kỹ sư → 200, đồ thị N15 của chính dự án."""
    project, _, engineer, _, _ = await _setup(db_session)
    response = await api_client.post(f"/api/projects/{project.id}/exports", headers=headers_of(engineer), json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["floorRevisions"] == []
    assert body["graph"]["building"]["name"] == project.name


async def test_share_links_export__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống ngoài dự án → 404 `resource:"project"`."""
    project, _, _, _, outsider = await _setup(db_session)
    response = await api_client.post(f"/api/projects/{project.id}/exports", headers=headers_of(outsider), json={})
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_share_links_export__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404, kể cả cho chính người sở hữu."""
    engineer = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=engineer, deleted_at=fake_clock.now())
    response = await api_client.post(f"/api/projects/{project.id}/exports", headers=headers_of(engineer), json={})
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_share_links_export__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Dự án không địa chỉ → vắng khoá `address` (W2), không `null`."""
    project, _, engineer, _, _ = await _setup(db_session)
    response = await api_client.post(f"/api/projects/{project.id}/exports", headers=headers_of(engineer), json={})
    assert response.status_code == 200
    assert "address" not in response.json()["graph"]["building"]


async def test_share_links_list__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin thành viên → 200, đúng liên kết đã tạo với đủ trường."""
    project, admin, engineer, _, _ = await _setup(db_session)
    link = await _create_link(api_client, project, engineer, label="khách")
    response = await api_client.get(_links_path(project), headers=headers_of(admin))
    assert response.status_code == 200
    assert response.json() == [link]


async def test_share_links_list__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống ngoài dự án → 404 `resource:"project"` (trước 403)."""
    project, _, _, _, outsider = await _setup(db_session)
    response = await api_client.get(_links_path(project), headers=headers_of(outsider))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_share_links_list__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    engineer = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=engineer, deleted_at=fake_clock.now())
    response = await api_client.get(_links_path(project), headers=headers_of(engineer))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_share_links_list__C15(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Danh sách cũ: rỗng → `[]`, một phần tử, ba phần tử trả trọn, mới trước, thứ tự ổn định."""
    project, _, engineer, _, _ = await _setup(db_session)
    path = _links_path(project)
    assert (await api_client.get(path, headers=headers_of(engineer))).json() == []
    ids: list[str] = []
    for label in ("một", "hai", "ba"):
        ids.append((await _create_link(api_client, project, engineer, label=label))["id"])
        fake_clock.advance(timedelta(seconds=1))
        if len(ids) == 1:
            listed = (await api_client.get(path, headers=headers_of(engineer))).json()
            assert [item["id"] for item in listed] == ids
    first = await api_client.get(path, headers=headers_of(engineer))
    second = await api_client.get(path, headers=headers_of(engineer))
    assert [item["id"] for item in first.json()] == ids[::-1]
    assert first.json() == second.json()


async def test_share_links_list__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Liên kết không nhãn, không hạn, chưa thu hồi → vắng các khoá tuỳ chọn."""
    project, _, engineer, _, _ = await _setup(db_session)
    await _create_link(api_client, project, engineer)
    item = (await api_client.get(_links_path(project), headers=headers_of(engineer))).json()[0]
    for key in ("expiresAt", "revokedAt", "viewpointCode", "label"):
        assert key not in item
    assert None not in item.values()


async def test_share_links_create__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Kỹ sư → 201, liên kết `active` mang đúng quyền, nhãn, góc nhìn; token ngẫu nhiên dài."""
    project, _, engineer, _, _ = await _setup(db_session)
    response = await api_client.post(
        _links_path(project),
        headers=headers_of(engineer),
        json={"permission": "comment", "label": "Khách hàng", "viewpointCode": "vp-1"},
    )
    assert response.status_code == 201, response.text
    link = response.json()
    assert (link["status"], link["permission"], link["label"], link["viewpointCode"]) == (
        "active",
        "comment",
        "Khách hàng",
        "vp-1",
    )
    assert link["id"].startswith("shl")
    assert len(link["url"].rsplit("/share/", 1)[1]) >= 40


async def test_share_links_create__C02(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """`permission` ngoài tập cho phép → 422 `VALIDATION` có `field`."""
    project, _, engineer, _, _ = await _setup(db_session)
    response = await api_client.post(_links_path(project), headers=headers_of(engineer), json={"permission": "edit"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION"
    assert body["field"] == "permission"


async def test_share_links_create__C03(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Khoá lạ → 422 `VALIDATION`, không tạo dòng."""
    project, _, engineer, _, _ = await _setup(db_session)
    response = await api_client.post(_links_path(project), headers=headers_of(engineer), json={"lạ": 1})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"
    assert (await api_client.get(_links_path(project), headers=headers_of(engineer))).json() == []


async def test_share_links_create__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống ngoài dự án → 404 `resource:"project"`."""
    project, _, _, _, outsider = await _setup(db_session)
    response = await api_client.post(_links_path(project), headers=headers_of(outsider), json={})
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_share_links_create__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thành viên `viewer` (vai mạnh nhất thiếu `share.create`) → 403 `FORBIDDEN`."""
    project, _, _, viewer, _ = await _setup(db_session)
    response = await api_client.post(_links_path(project), headers=headers_of(viewer), json={})
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_share_links_create__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404."""
    engineer = await make_user(db_session, role="engineer")
    project = await seed_project(db_session, owner=engineer, deleted_at=fake_clock.now())
    response = await api_client.post(_links_path(project), headers=headers_of(engineer), json={})
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_share_links_create__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thân rỗng → vắng `expiresAt`, `revokedAt`, `viewpointCode`, `label`; không `null`."""
    project, _, engineer, _, _ = await _setup(db_session)
    response = await api_client.post(_links_path(project), headers=headers_of(engineer), json={})
    assert response.status_code == 201
    link = response.json()
    for key in ("expiresAt", "revokedAt", "viewpointCode", "label"):
        assert key not in link
    assert None not in link.values()


async def test_share_links_revoke__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin → 200 `revoked` có `revokedAt`; tuyến công khai thôi mở được."""
    project, admin, engineer, _, _ = await _setup(db_session)
    link = await _create_link(api_client, project, engineer)
    response = await api_client.delete(f"{_links_path(project)}/{link['id']}", headers=headers_of(admin))
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"]) == (link["id"], "revoked")
    assert "revokedAt" in body
    token = link["url"].rsplit("/", 1)[1]
    assert (await api_client.get(f"/api/share/{token}")).status_code == 404


async def test_share_links_revoke__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Admin hệ thống ngoài dự án → 404 `resource:"project"`, liên kết còn `active`."""
    project, _, engineer, _, outsider = await _setup(db_session)
    link = await _create_link(api_client, project, engineer)
    response = await api_client.delete(f"{_links_path(project)}/{link['id']}", headers=headers_of(outsider))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"
    listed = (await api_client.get(_links_path(project), headers=headers_of(engineer))).json()
    assert listed[0]["status"] == "active"


async def test_share_links_revoke__C07(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thành viên `viewer` → 403 `FORBIDDEN`, liên kết còn `active`."""
    project, _, engineer, viewer, _ = await _setup(db_session)
    link = await _create_link(api_client, project, engineer)
    response = await api_client.delete(f"{_links_path(project)}/{link['id']}", headers=headers_of(viewer))
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    listed = (await api_client.get(_links_path(project), headers=headers_of(engineer))).json()
    assert listed[0]["status"] == "active"


async def test_share_links_revoke__C08(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Id liên kết không có, hoặc thuộc dự án khác → 404 `resource:"shareLink"`."""
    project, admin, engineer, _, _ = await _setup(db_session)
    other = await seed_project(db_session, owner=engineer, members=[admin])
    foreign = await _create_link(api_client, other, engineer)
    for link_id in ("shl_nope", foreign["id"]):
        response = await api_client.delete(f"{_links_path(project)}/{link_id}", headers=headers_of(admin))
        assert response.status_code == 404
        assert response.json()["resource"] == "shareLink"


async def test_share_links_revoke__C17(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Thu hồi liên kết không nhãn, không hạn → vắng `expiresAt`, `viewpointCode`, `label`."""
    project, admin, engineer, _, _ = await _setup(db_session)
    link = await _create_link(api_client, project, engineer)
    body = (await api_client.delete(f"{_links_path(project)}/{link['id']}", headers=headers_of(admin))).json()
    for key in ("expiresAt", "viewpointCode", "label"):
        assert key not in body
    assert None not in body.values()


async def test_share_links_open__C01(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Không token đăng nhập → 200: liên kết, tên dự án và đồ thị chỉ đọc."""
    project, _, engineer, _, _ = await _setup(db_session)
    link = await _create_link(api_client, project, engineer, label="khách")
    token = link["url"].rsplit("/", 1)[1]
    response = await api_client.get(f"/api/share/{token}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["link"] == link
    assert body["projectName"] == project.name
    assert body["graph"]["floorRevisions"] == []
