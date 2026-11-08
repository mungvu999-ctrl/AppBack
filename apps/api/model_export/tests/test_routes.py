"""N49 `POST …/model-exports`, N50 `GET …/latest`, N51 `GET …/files/{file_name}` (M1-01).

Postgres, kho local và broker thật (K23).
"""

import asyncio
import dataclasses
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.lighting_bake.tests._helpers import bake_broker as bake_broker
from apps.api.model_export import service, snapshot_source
from apps.api.model_export.jobs import run_model_export_sweep
from apps.api.projects.tests.test_routes_common import headers_of
from packages.core.clock import SystemClock
from packages.db.models.auth import User
from packages.db.models.project_settings import ProjectSettingsRow
from packages.db.models.projects import Project
from packages.messaging.redis import SyncRedis
from packages.storage.keys import model_export_object
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.floors import make_floor
from packages.testing.factories.model_export import make_model_export
from packages.testing.factories.projects import make_project
from packages.testing.factories.spatial import make_floor_document, sample_floor_layer
from packages.testing.fixtures.messaging import queued_payloads

BAKE_QUEUE = "bake"


def _path(project_id: str, tail: str = "") -> str:
    """Đường N49/N50/N51."""
    return f"/api/projects/{project_id}/model-exports{tail}"


async def _project(db: AsyncSession, *, viewer: bool = False) -> tuple[User, Project, list[User]]:
    """Chủ dự án (và một viewer nếu cần) đã commit."""
    owner = await make_user(db)
    members = [await make_user(db, role="viewer")] if viewer else []
    project = await make_project(db, owner=owner, members=members)
    await db.commit()
    return owner, project, members


async def _statuses(db: AsyncSession, project_id: str) -> list[str]:
    """Trạng thái mọi lượt của dự án theo thời điểm tạo."""
    rows = await db.execute(
        text("SELECT status FROM model_exports WHERE project_id = :p ORDER BY created_at, id"), {"p": project_id}
    )
    return list(rows.scalars())


async def test_model_export_start_export__C01(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage, bake_broker: SyncRedis
) -> None:
    """Lần đầu → 202 `queued`, `snapshot.json` trong kho, task trên hàng `bake`; lặp lại → 200 cùng dòng."""
    owner, project, _ = await _project(db_session)
    floor = await make_floor(db_session, project=project)
    await db_session.commit()

    first = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))
    again = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert first.status_code == 202, first.text
    body = first.json()
    assert body["id"].startswith("mex_")
    assert body["status"] == "queued"
    assert body["stale"] is False
    assert body["snapshotRevisions"] == [{"levelId": floor.level_id, "levelName": floor.name, "revision": 0}]
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]
    assert await _statuses(db_session, project.id) == ["queued"]
    snapshot = await local_storage.stat(model_export_object(project.id, body["id"], "snapshot.json"))
    assert snapshot is not None
    assert [p["export_id"] for p in queued_payloads(bake_broker, BAKE_QUEUE)] == [body["id"]]

    await db_session.execute(text("UPDATE floors SET height_mm = 3300 WHERE pk = :pk"), {"pk": floor.pk})
    await db_session.commit()
    latest = await api_client.get(_path(project.id, "/latest"), headers=headers_of(owner))
    assert latest.json()["stale"] is True


async def test_model_export_start_export__supersede(
    db_session: AsyncSession, api_client: AsyncClient, bake_broker: SyncRedis
) -> None:
    """Lượt `queued|running` khác dấu vân → `superseded`; dòng `done` giữ nguyên."""
    owner, project, _ = await _project(db_session)
    await make_model_export(db_session, project_id=project.id, status="done", quality="ready")
    running = await make_model_export(db_session, project_id=project.id, status="running")

    response = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert response.status_code == 202
    assert sorted(await _statuses(db_session, project.id)) == ["done", "queued", "superseded"]
    found = await db_session.execute(text("SELECT status FROM model_exports WHERE id = :id"), {"id": running.id})
    assert found.scalar_one() == "superseded"


async def test_model_export_start_export__pruned_cache(
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    api_client: AsyncClient,
    local_storage: LocalDiskStorage,
    bake_broker: SyncRedis,
) -> None:
    """Dấu vân quay lại một lượt `done` đã bị lịch quét dọn tệp (bật/tắt rồi bật lại) → 202 lượt mới, không 200.

    Lượt cũ thành `superseded` để unique partial `uq_model_exports_fingerprint` không chặn lượt mới.
    """
    owner, project, _ = await _project(db_session)
    await make_floor(db_session, project=project)
    await db_session.commit()
    first = (await api_client.post(_path(project.id), json={}, headers=headers_of(owner))).json()
    await db_session.execute(
        text(
            "UPDATE model_exports SET status = 'done', quality = 'ready', glb_bytes = 1, started_at = now(), "
            "ended_at = now(), report_summary = '{\"counts\": {}}'::jsonb WHERE id = :id"
        ),
        {"id": first["id"]},
    )
    await db_session.commit()
    for _ in range(5):
        await make_model_export(
            db_session, project_id=project.id, status="done", quality="ready", report_summary={"counts": {}}
        )
    assert (await run_model_export_sweep(db_sessionmaker, local_storage, SystemClock())).pruned == 1

    again = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert again.status_code == 202, again.text
    assert again.json()["id"] != first["id"]
    assert again.json()["fingerprint"] == first["fingerprint"]
    assert again.json()["status"] == "queued"
    found = await db_session.execute(text("SELECT status FROM model_exports WHERE id = :id"), {"id": first["id"]})
    assert found.scalar_one() == "superseded"


async def test_model_export_start_export__C14(
    db_session: AsyncSession, api_client: AsyncClient, bake_broker: SyncRedis
) -> None:
    """Hai yêu cầu song song cùng dấu vân → một dòng, cùng id trả về, một task."""
    owner, project, _ = await _project(db_session)

    responses = await asyncio.gather(
        *(api_client.post(_path(project.id), json={}, headers=headers_of(owner)) for _ in range(2))
    )

    assert sorted(r.status_code for r in responses) in ([200, 202], [202, 202]) or all(
        r.status_code in (200, 202) for r in responses
    )
    assert len({r.json()["id"] for r in responses}) == 1
    assert await _statuses(db_session, project.id) == ["queued"]
    assert len(queued_payloads(bake_broker, BAKE_QUEUE)) == 1


async def test_model_export_start_export__concurrent_supersede(
    db_session: AsyncSession, api_client: AsyncClient, bake_broker: SyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hai N49 song song **khác** dấu vân → lượt sau thay lượt trước: một lượt sống, một `superseded`."""
    owner, project, _ = await _project(db_session)
    real_take = snapshot_source.take
    fingerprints = iter(("fp-a", "fp-b"))

    async def take(db: AsyncSession, project_id: str, versions: Mapping[str, Any]) -> snapshot_source.Snapshot:
        """Ảnh chụp thật, dấu vân khác nhau ở mỗi lần gọi (như tài liệu đổi giữa hai lần bấm)."""
        snap = await real_take(db, project_id, versions)
        return dataclasses.replace(snap, fingerprint=next(fingerprints))

    monkeypatch.setattr(snapshot_source, "take", take)

    responses = await asyncio.gather(
        *(api_client.post(_path(project.id), json={}, headers=headers_of(owner)) for _ in range(2))
    )

    assert [r.status_code for r in responses] == [202, 202]
    assert sorted(await _statuses(db_session, project.id)) == ["queued", "superseded"]


async def test_model_export_start_export__C07(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Viewer (không có `model.export`) → 403, không mở lượt."""
    _, project, members = await _project(db_session, viewer=True)

    response = await api_client.post(_path(project.id), json={}, headers=headers_of(members[0], role="viewer"))

    assert response.status_code == 403
    assert await _statuses(db_session, project.id) == []


async def test_model_export_start_export__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Không là thành viên → 404 `project`."""
    _, project, _ = await _project(db_session)
    outsider = await make_user(db_session)
    await db_session.commit()

    response = await api_client.post(_path(project.id), json={}, headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_model_export_read_latest__C01(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Bỏ `superseded`; dấu vân khác hiện tại → `stale`; gom thiếu sót theo lý do."""
    owner, project, _ = await _project(db_session)
    summary = {
        "counts": {"wall:built": 3},
        "records": [
            {"severity": "major", "reason": "mái chưa hỗ trợ trong M1"},
            {"severity": "major", "reason": "mái chưa hỗ trợ trong M1"},
        ],
    }
    done = await make_model_export(
        db_session, project_id=project.id, status="done", quality="degraded", report_summary=summary, glb_bytes=10
    )
    await make_model_export(db_session, project_id=project.id, status="superseded")

    response = await api_client.get(_path(project.id, "/latest"), headers=headers_of(owner))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == done.id
    assert body["stale"] is True
    assert body["currentFingerprint"] != body["fingerprint"]
    assert body["counts"] == {"wall:built": 3}
    assert body["issues"] == [{"severity": "major", "reason": "mái chưa hỗ trợ trong M1", "count": 2}]


async def test_model_export_read_latest__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Chưa có lượt → 404 `modelExport`."""
    owner, project, _ = await _project(db_session)
    response = await api_client.get(_path(project.id, "/latest"), headers=headers_of(owner))
    assert response.status_code == 404
    assert response.json()["resource"] == "modelExport"


async def test_model_export_download_file__C01(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage
) -> None:
    """Lượt `done` `ready` → stream `model.glb`."""
    owner, project, _ = await _project(db_session)
    row = await make_model_export(db_session, project_id=project.id, status="done", quality="ready")
    await local_storage.put(
        model_export_object(project.id, row.id, "model.glb"),
        b"glTF-bytes",
        content_type="model/gltf-binary",
        max_bytes=100,
    )

    response = await api_client.get(_path(project.id, f"/{row.id}/files/model.glb"), headers=headers_of(owner))

    assert response.status_code == 200
    assert response.content == b"glTF-bytes"


async def test_model_export_download_file__not_ready(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """`blocked` hay chưa `done` → 409 `MODEL_EXPORT_NOT_READY`; thiếu object → 404."""
    owner, project, _ = await _project(db_session)
    blocked = await make_model_export(db_session, project_id=project.id, status="done", quality="blocked")
    running = await make_model_export(db_session, project_id=project.id, status="running")

    r1 = await api_client.get(_path(project.id, f"/{blocked.id}/files/model.glb"), headers=headers_of(owner))
    r2 = await api_client.get(_path(project.id, f"/{running.id}/files/report.json"), headers=headers_of(owner))
    r3 = await api_client.get(_path(project.id, f"/{blocked.id}/files/report.json"), headers=headers_of(owner))

    assert (r1.status_code, r1.json()["code"]) == (409, "MODEL_EXPORT_NOT_READY")
    assert r2.status_code == 409
    assert r3.status_code == 404


async def test_model_export_download_file__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Lượt của dự án khác (IDOR), tên lạ, id sai mẫu → 404."""
    owner, project, _ = await _project(db_session)
    _, other, _ = await _project(db_session)
    foreign = await make_model_export(db_session, project_id=other.id, status="done", quality="ready")

    for tail in (f"/{foreign.id}/files/model.glb", f"/{foreign.id}/files/snapshot.json", "/bad/files/model.glb"):
        response = await api_client.get(_path(project.id, tail), headers=headers_of(owner))
        assert response.status_code == 404, tail


async def test_model_export_start_export__scene_style(
    db_session: AsyncSession, api_client: AsyncClient, bake_broker: SyncRedis
) -> None:
    """Đổi `scene_style` của dự án → N50 `stale`, N49 mở job mới (dấu vân gồm phong cách)."""
    owner, project, _ = await _project(db_session)
    first = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))
    db_session.add(
        ProjectSettingsRow(
            project_id=project.id,
            revision=1,
            building_type="mixed",
            length_unit="mm",
            snap_tolerance_mm=50,
            confidence_threshold=Decimal("0.75"),
            default_scale_mm_per_px=Decimal(1),
            scene_style="go-am",
            last_writer_id=owner.id,
            last_body_sha256="0" * 64,
        )
    )
    await db_session.commit()

    latest = await api_client.get(_path(project.id, "/latest"), headers=headers_of(owner))
    second = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert latest.json()["stale"] is True
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["fingerprint"] != first.json()["fingerprint"]


async def test_model_export_start_export__unexpected_error(
    db_session: AsyncSession,
    api_client: AsyncClient,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lỗi bất ngờ sau khi đã ghi `snapshot.json` → 500, object bị xoá, không còn dòng nào."""
    owner, project, _ = await _project(db_session)

    def boom(*_args: object) -> None:
        """Giả lỗi lúc đăng ký gửi task."""
        raise RuntimeError("giả lỗi")

    monkeypatch.setattr(service, "on_after_commit", boom)

    response = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert response.status_code == 500
    assert await _statuses(db_session, project.id) == []
    assert [info.key async for info in local_storage.list_prefix(f"projects/{project.id}/model-exports/")] == []


async def test_model_export_start_export__layer_from_document(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage, bake_broker: SyncRedis
) -> None:
    """Snapshot lấy đúng `document["layer"]` của dạng lưu thật (codec), không lấy cả document (lỗi đo trên f14)."""
    owner, project, _ = await _project(db_session)
    floor = await make_floor(db_session, project=project)
    layer = sample_floor_layer(0, level_id=floor.level_id)
    await make_floor_document(db_session, floor_pk=floor.pk, layer=layer, revision=3, clock=SystemClock())
    await db_session.commit()

    created = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert created.status_code == 202, created.text
    key = model_export_object(project.id, created.json()["id"], "snapshot.json")
    raw = b"".join([chunk async for chunk in local_storage.open_read(key)])
    floors = json.loads(raw)["floors"]
    assert len(floors) == 1
    assert floors[0]["revision"] == 3
    assert len(floors[0]["layer"]["walls"]) == len(layer.walls) > 0
    assert len(floors[0]["layer"]["openings"]) == len(layer.openings)
    assert {"axes", "dimensions"}.isdisjoint(floors[0]["layer"])


async def test_model_export_read_latest__issues_skip_info(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """`info` không vào `issues` (FE strict chỉ nhận minor|major|critical — lỗi CONTRACT_VALIDATION đo qua UI 07/11).

    Thiếu sót sắp nặng trước, cùng mức thì nhiều trước.
    """
    owner, project, _ = await _project(db_session)
    summary = {
        "counts": {"style:built": 1},
        "records": [
            {"severity": "info", "reason": "dùng phong cách mặc định am-ap (dự án chưa chọn phong cách)"},
            {"severity": "minor", "reason": "cửa xếp dựng như 2 tấm"},
            {"severity": "major", "reason": "tường trùng"},
            {"severity": "critical", "reason": "tầng không có tường"},
            {"severity": "major", "reason": "mẫu chưa có trong gói xuất"},
            {"severity": "major", "reason": "mẫu chưa có trong gói xuất"},
        ],
    }
    await make_model_export(
        db_session, project_id=project.id, status="done", quality="blocked", report_summary=summary, glb_bytes=None
    )

    response = await api_client.get(_path(project.id, "/latest"), headers=headers_of(owner))

    assert response.status_code == 200, response.text
    assert response.json()["issues"] == [
        {"severity": "critical", "reason": "tầng không có tường", "count": 1},
        {"severity": "major", "reason": "mẫu chưa có trong gói xuất", "count": 2},
        {"severity": "major", "reason": "tường trùng", "count": 1},
        {"severity": "minor", "reason": "cửa xếp dựng như 2 tấm", "count": 1},
    ]


OPTIONAL_UNSET = ("quality", "startedAt", "endedAt", "failureCode", "glbBytes")
"""Trường tuỳ chọn của `ModelExport` mà lượt `queued` chưa có (C17: vắng khoá, không `null`)."""


async def test_model_export_start_export__C08(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Dự án không tồn tại → 404 `project`, không mở lượt."""
    owner, _, _ = await _project(db_session)
    response = await api_client.post(_path("prj_" + "0" * 26), json={}, headers=headers_of(owner))
    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_model_export_start_export__C17(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage, bake_broker: SyncRedis
) -> None:
    """Lượt mới `queued`: các trường chưa có giá trị vắng khoá."""
    owner, project, _ = await _project(db_session)
    await make_floor(db_session, project=project)
    await db_session.commit()

    response = await api_client.post(_path(project.id), json={}, headers=headers_of(owner))

    assert response.status_code == 202, response.text
    assert not set(OPTIONAL_UNSET) & set(response.json())


async def test_model_export_read_latest__C06(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Không là thành viên → 404 `project`, kể cả khi dự án có lượt xuất."""
    _, project, _ = await _project(db_session)
    await make_model_export(db_session, project_id=project.id, status="done", quality="ready")
    outsider = await make_user(db_session)
    await db_session.commit()

    response = await api_client.get(_path(project.id, "/latest"), headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_model_export_read_latest__C17(db_session: AsyncSession, api_client: AsyncClient) -> None:
    """Lượt `queued` đọc qua N50: các trường chưa có giá trị vắng khoá."""
    owner, project, _ = await _project(db_session)
    await make_model_export(db_session, project_id=project.id)

    response = await api_client.get(_path(project.id, "/latest"), headers=headers_of(owner))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert not set(OPTIONAL_UNSET) & set(response.json())


async def test_model_export_download_file__C06(
    db_session: AsyncSession, api_client: AsyncClient, local_storage: LocalDiskStorage
) -> None:
    """Không là thành viên → 404 `project`, không stream tệp."""
    _, project, _ = await _project(db_session)
    row = await make_model_export(db_session, project_id=project.id, status="done", quality="ready")
    await local_storage.put(
        model_export_object(project.id, row.id, "model.glb"),
        b"glTF-bytes",
        content_type="model/gltf-binary",
        max_bytes=100,
    )
    outsider = await make_user(db_session)
    await db_session.commit()

    response = await api_client.get(_path(project.id, f"/{row.id}/files/model.glb"), headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"
