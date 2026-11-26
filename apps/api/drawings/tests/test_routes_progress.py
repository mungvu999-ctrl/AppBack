"""Case của #8 `drawings_read_progress` (Đ: C01 C06 C08 C17).

C01 có **bốn** hậu tố: cổng H1 đòi mẫu 2xx cho đủ bốn nhánh `pending`, `running`,
`completed`, `failed` của `Progress` (`tools/contract/check.py`). Bốn nhánh ấy
dựng bằng `runs.start_run` + `runs.record_step` thật chứ không vá cột: luật trạng thái chỉ
có một nguồn.
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.runs import record_step, start_run
from apps.api.drawings.tests._helpers import sync_bus_reset as sync_bus_reset
from apps.api.drawings.tests._upload_helpers import headers_of, make_stage, progress_path, upload_png
from packages.core.pipeline import PIPELINE_STEPS
from packages.storage.port import ObjectStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.drawings import make_complete_upload, make_upload
from packages.testing.fixtures.clock import FakeClock

FIRST_STEP = PIPELINE_STEPS[0][0]
LAST_STEP = PIPELINE_STEPS[-1][0]


async def test_drawings_read_progress__C01_pending(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Lượt tải còn `receiving` → `pending`, phần trăm 0, bước `preprocess`."""
    stage = await make_stage(db_session)
    upload = await make_upload(db_session, project=stage.scene.project, floor=stage.scene.floor)
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"id": upload.id, "status": "pending", "step": FIRST_STEP, "progressPercent": 0}


async def test_drawings_read_progress__C01_running(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: ObjectStorage,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Bước đầu đang chạy → `running`, có `startedAt`, chưa có `endedAt` (K33)."""
    stage = await make_stage(db_session)
    upload = await make_complete_upload(
        db_session, local_storage, project=stage.scene.project, floor=stage.scene.floor, data=upload_png(600)
    )
    run = await start_run(db_session, upload_id=upload.id, clock=fake_clock)
    await record_step(db_session, run_id=run.id, step=FIRST_STEP, status="running", clock=fake_clock)
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire["status"] == "running"
    assert wire["step"] == FIRST_STEP
    assert wire["startedAt"].endswith("Z")
    assert "endedAt" not in wire


async def test_drawings_read_progress__C01_completed(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: ObjectStorage,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Mọi bước xong → `completed`, 100%, `step` là bước cuối và **có** `endedAt`."""
    stage = await make_stage(db_session)
    upload = await make_complete_upload(
        db_session, local_storage, project=stage.scene.project, floor=stage.scene.floor, data=upload_png(600)
    )
    run = await start_run(db_session, upload_id=upload.id, clock=fake_clock)
    for step, _ in PIPELINE_STEPS:
        await record_step(db_session, run_id=run.id, step=step, status="completed", clock=fake_clock)
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire["status"] == "completed"
    assert wire["progressPercent"] == 100
    assert wire["step"] == LAST_STEP
    assert wire["endedAt"].endswith("Z")


async def test_drawings_read_progress__C01_failed(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: ObjectStorage,
    fake_clock: FakeClock,
    sync_bus_reset: None,
) -> None:
    """Bước hỏng → `failed` kèm `error` UPPER_SNAKE và **không** `endedAt` (K33)."""
    stage = await make_stage(db_session)
    upload = await make_complete_upload(
        db_session, local_storage, project=stage.scene.project, floor=stage.scene.floor, data=upload_png(600)
    )
    run = await start_run(db_session, upload_id=upload.id, clock=fake_clock)
    await record_step(
        db_session, run_id=run.id, step=FIRST_STEP, status="failed", clock=fake_clock, error_code="FILE_CORRUPT"
    )
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire["status"] == "failed"
    assert wire["error"] == "FILE_CORRUPT"
    assert "endedAt" not in wire


async def test_drawings_read_progress__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Người ngoài dự án (vai `admin`) → 404 `resource:"project"`.
    Catalog: B09-drw-011.
    """
    stage = await make_stage(db_session)
    upload = await make_upload(db_session, project=stage.scene.project, floor=stage.scene.floor)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=headers_of(outsider))

    assert response.status_code == 404
    assert response.json()["resource"] == "project"


async def test_drawings_read_progress__C08(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Lượt tải không tồn tại → 404 `resource:"upload"`."""
    stage = await make_stage(db_session)

    path = progress_path(stage.project_id, "upl_00000000000000000000000000")
    response = await api_client.get(path, headers=stage.headers)

    assert response.status_code == 404
    assert response.json()["resource"] == "upload"


async def test_drawings_read_progress__C17(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Trường tuỳ chọn chưa có giá trị thì **vắng khoá**, không `null` (W2)."""
    stage = await make_stage(db_session)
    upload = await make_upload(db_session, project=stage.scene.project, floor=stage.scene.floor)
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"id", "status", "step", "progressPercent"}


async def test_progress_of_rejected_upload_reports_code(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None
) -> None:
    """Lượt tải bị #7 từ chối → `failed` mang chính `rejected_code`."""
    stage = await make_stage(db_session)
    upload = await make_upload(
        db_session,
        project=stage.scene.project,
        floor=stage.scene.floor,
        status="rejected",
        rejected_code="FILE_TYPE_MISMATCH",
    )
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "FILE_TYPE_MISMATCH"


@pytest.mark.parametrize("rejected_code", ["DEPENDENCY_UNAVAILABLE", "NOT_FOUND", None])
async def test_drawings_read_progress__unclassified_rejected_code_reaches_error(
    api_client: httpx.AsyncClient, db_session: AsyncSession, sync_bus_reset: None, rejected_code: str | None
) -> None:
    """QA-03d BE2-G-234: mã ngoài bảng 28 mã của FE (từ #7 khi kho hỏng, BE2-G-083) vẫn tới `Progress.error`
    nguyên văn, và `rejected_code` rỗng thành `""` — FE tự rơi về câu "chưa phân loại" (vitest phía FE)."""
    stage = await make_stage(db_session)
    upload = await make_upload(
        db_session, project=stage.scene.project, floor=stage.scene.floor, status="rejected", rejected_code=rejected_code
    )
    await db_session.commit()

    response = await api_client.get(progress_path(stage.project_id, upload.id), headers=stage.headers)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": upload.id,
        "status": "failed",
        "step": FIRST_STEP,
        "progressPercent": 0,
        "error": rejected_code or "",
    }
