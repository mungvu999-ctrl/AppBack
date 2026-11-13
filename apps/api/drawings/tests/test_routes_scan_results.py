"""Case của N49 `drawings_read_scan_results` (Đ: C01 C06 C08) + các trạng thái chưa có artifact."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.tests._upload_helpers import Stage, headers_of, make_stage
from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.db.models.drawings import PipelineRunRow, UploadRow
from packages.ml_contracts.artifacts import (
    BoxPx,
    DetectionPx,
    ObjectsResult,
    PointPx,
    WallPx,
    WallsResult,
    objects_to_json,
    walls_to_json,
)
from packages.storage.keys import run_artifact
from packages.storage.port import ObjectStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawings import make_upload
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.clock import FakeClock

_MISSING_UPLOAD = "upl_00000000000000000000000000"


def _path(project_id: str, upload_id: str) -> str:
    """Đường N49."""
    return f"/api/projects/{project_id}/drawings/uploads/{upload_id}/scan-results"


async def _upload(db: AsyncSession, stage: Stage) -> UploadRow:
    """Một lượt tải đã commit của sân khấu."""
    row = await make_upload(db, project=stage.scene.project, floor=stage.scene.floor, status="complete")
    await db.commit()
    return row


async def _run(db: AsyncSession, stage: Stage, upload: UploadRow) -> str:
    """Một lượt chạy đang chạy của lượt tải; trả id."""
    run_id = new_id("run", SystemClock())
    db.add(
        PipelineRunRow(
            id=run_id,
            upload_id=upload.id,
            floor_pk=stage.scene.floor.pk,
            status="running",
            current_step="wallSegmentation",
            progress_percent=5,
        )
    )
    await db.commit()
    return run_id


async def test_drawings_read_scan_results__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: ObjectStorage
) -> None:
    """Đủ hai artifact → trả đúng toạ độ tường và hộp."""
    stage = await make_stage(db_session)
    upload = await _upload(db_session, stage)
    run_id = await _run(db_session, stage, upload)
    wall = WallPx(start=PointPx(x=1, y=2), end=PointPx(x=30, y=2), thickness_px=4, confidence=0.9)
    box = BoxPx(x_min=5, y_min=6, x_max=7, y_max=8)
    for step, name, data in (
        ("wallSegmentation", "walls.json", walls_to_json(WallsResult(walls=(wall,)))),
        (
            "openingAndFurnitureDetection",
            "objects.json",
            objects_to_json(ObjectsResult(detections=(DetectionPx(label="door", box=box, confidence=0.8),))),
        ),
    ):
        key = run_artifact(stage.project_id, stage.scene.floor.level_id, upload.id, run_id, step, name)
        await local_storage.put(key, data, content_type="application/json", max_bytes=len(data))

    response = await api_client.get(_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "walls": [{"x1": 1, "y1": 2, "x2": 30, "y2": 2}],
        "boxes": [{"label": "door", "xMin": 5, "yMin": 6, "xMax": 7, "yMax": 8}],
    }


async def test_drawings_read_scan_results_empty_without_run(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Chưa có lượt chạy → rỗng, không lỗi."""
    stage = await make_stage(db_session)
    upload = await _upload(db_session, stage)

    response = await api_client.get(_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"walls": [], "boxes": []}


async def test_drawings_read_scan_results_empty_before_artifacts(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Lượt đang chạy, chưa bước nào xong → rỗng, không lỗi."""
    stage = await make_stage(db_session)
    upload = await _upload(db_session, stage)
    await _run(db_session, stage, upload)

    response = await api_client.get(_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"walls": [], "boxes": []}


async def test_drawings_read_scan_results__C06(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """Người ngoài dự án (vai `admin`) → 404 `resource:"project"`."""
    stage = await make_stage(db_session)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await api_client.get(_path(stage.project_id, _MISSING_UPLOAD), headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_drawings_read_scan_results__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Dự án đã xoá mềm → 404, kể cả cho chính chủ."""
    owner = await make_user(db_session)
    project = await make_project(db_session, owner=owner, deleted_at=fake_clock.now())
    await db_session.commit()

    response = await api_client.get(_path(project.id, _MISSING_UPLOAD), headers=headers_of(owner))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"
