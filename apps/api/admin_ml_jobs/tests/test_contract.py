"""Hợp đồng chung của module: quyền, wire model (B6-03a [2], [7]).

- **C07** cho cả sáu `op`: `require_admin` khai ở router nên cổng chạy **trước** endpoint;
- `job_out`/`metric_point_out`/`log_line_out`: vắng trường thay vì `null` (W2, K02), không
  cột của cầu nối (K01: `lastHeartbeatAt`, `artifactsPurgedAt`, `requeueCount`);
- `CreateTrainingJobIn`/`CancelTrainingJobIn`: khoá lạ, `datasetVersionId` sai mẫu, `epochs`
  ngoài 1-300 → lỗi validate (C03, C02).
"""

from datetime import UTC, datetime
from typing import Any, Final

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.admin_ml_jobs.schemas import (
    CancelTrainingJobIn,
    CreateTrainingJobIn,
    job_out,
    log_line_out,
    metric_point_out,
)
from apps.api.admin_ml_jobs.tests._helpers import (
    JOBS_PATH,
    OPENING,
    cancel_path,
    job_path,
    logs_path,
    metrics_path,
)
from apps.api.core.auth import Principal, fake_token
from apps.api.core.routing import AppRoute
from packages.core.ids import new_id
from packages.db.models.admin_ml_jobs import TrainingLogRow, TrainingMetricRow
from packages.testing.factories.admin_ml_datasets import make_dataset, make_dataset_version
from packages.testing.factories.admin_ml_jobs import make_training_job
from packages.testing.fixtures.clock import FakeClock

_JOB_ID: Final = "job_01KB6030000000000000000001"
_ROUTES: Final[tuple[tuple[str, str, str], ...]] = (
    ("ml_list_jobs", "GET", JOBS_PATH),
    ("ml_create_job", "POST", JOBS_PATH),
    ("ml_read_job", "GET", job_path(_JOB_ID)),
    ("ml_cancel_job", "POST", cancel_path(_JOB_ID)),
    ("ml_list_job_metrics", "GET", metrics_path(_JOB_ID)),
    ("ml_list_job_logs", "GET", logs_path(_JOB_ID)),
)
_BODIES: Final[dict[str, dict[str, Any]]] = {
    "ml_create_job": {
        "family": OPENING,
        "datasetVersionId": "dsv_01KB6030000000000000000001",
        "baseModel": "yolov8n",
        "epochs": 3,
    },
    "ml_cancel_job": {},
}


def _route(app: FastAPI, name: str) -> AppRoute:
    """Route đã mount theo `operationId`; chưa mount → `KeyError` nói tên."""
    for route in app.routes:
        if isinstance(route, AppRoute) and route.name == name:
            return route
    raise KeyError(f"route {name!r} chưa được mount")


def _engineer_headers(clock: FakeClock) -> dict[str, str]:
    """Token vai `engineer` — vai mạnh nhất vẫn không có khoá `admin` (C07)."""
    return {"Authorization": f"Bearer {fake_token(Principal(new_id('usr', clock), 'sid-eng', 'engineer'))}"}


async def _forbidden_for_engineer(client: AsyncClient, clock: FakeClock, op: str) -> None:
    """Gọi một route bằng vai `engineer` và khẳng định 403 `FORBIDDEN`."""
    _op, method, path = next(row for row in _ROUTES if row[0] == op)
    response = await client.request(method, path, headers=_engineer_headers(clock), json=_BODIES.get(op))

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


# Sáu hàm mỏng thay cho một `parametrize`: `tools/case_gate.py` suy `op` từ **tên hàm**, nên
# một hàm mang tên chung sẽ gắn C07 vào một op không tồn tại và cả sáu op cùng thiếu C07.


async def test_ml_list_jobs__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """`engineer` → 403 `FORBIDDEN` (cổng `require_admin` của router, trước endpoint).

    Catalog: B08M-D-040, B09-mlj-003.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_list_jobs")


async def test_ml_create_job__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """`engineer` → 403; không job nào được chèn vì cổng chặn trước endpoint.

    Catalog: B08M-D-040, B09-mlj-007.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_create_job")


async def test_ml_read_job__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """`engineer` → 403 `FORBIDDEN` (403 thắng 404: quyền kiểm trước tài nguyên).

    Catalog: B08M-D-040, B09-mlj-010.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_read_job")


async def test_ml_cancel_job__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """`engineer` → 403 `FORBIDDEN`; không đặt `cancel_key`, không đổi job.

    Catalog: B08M-D-040, B09-mlj-014.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_cancel_job")


async def test_ml_list_job_metrics__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """`engineer` → 403 `FORBIDDEN`.

    Catalog: B08M-D-040, B09-mlj-017.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_list_job_metrics")


async def test_ml_list_job_logs__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """`engineer` → 403 `FORBIDDEN`.

    Catalog: B08M-D-040, B09-mlj-021.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_list_job_logs")


def test_ml_jobs_routes_are_all_admin_only(api_app: FastAPI) -> None:
    """Sáu route mount đúng method và đều `protected` (hợp đồng N32-N37).

    Catalog: B08M-D-040.
    """
    routes = [_route(api_app, op) for op, _method, _path in _ROUTES]

    assert [route.wire_method for route in routes] == [method for _op, method, _path in _ROUTES]
    assert all(route.protected for route in routes)


def test_create_training_job_in_rejects_unknown_keys() -> None:
    """Khoá lạ trong thân N33 → `ValueError` của Pydantic (C03).

    Catalog: B08M-G-211.
    """
    with pytest.raises(ValueError, match="creatorId"):
        CreateTrainingJobIn(**_BODIES["ml_create_job"], creatorId="usr_x")


def test_create_training_job_in_rejects_a_malformed_dataset_version_id() -> None:
    """`datasetVersionId` không phải `dsv_<ULID>` → lỗi trường, không 404.

    Catalog: B08M-G-211.
    """
    body = {**_BODIES["ml_create_job"], "datasetVersionId": "dst_01KB6030000000000000000001"}
    with pytest.raises(ValueError, match="dsv"):
        CreateTrainingJobIn(**body)


@pytest.mark.parametrize("epochs", [0, 301])
def test_create_training_job_in_rejects_epochs_out_of_range(epochs: int) -> None:
    """`epochs` ngoài 1-300 (CHECK `epochs_range`) → lỗi trường.

    Catalog: B08M-G-211.
    """
    with pytest.raises(ValueError, match="epochs"):
        CreateTrainingJobIn(**{**_BODIES["ml_create_job"], "epochs": epochs})


def test_cancel_training_job_in_rejects_unknown_keys() -> None:
    """Thân N35 là `{}`; khoá lạ không bị bỏ qua (C03).

    Catalog: B08M-G-224.
    """
    assert CancelTrainingJobIn().model_dump() == {}
    with pytest.raises(ValueError, match="reason"):
        CancelTrainingJobIn(reason="x")


async def test_job_out_omits_bridge_columns(db_session: AsyncSession, fake_clock: FakeClock) -> None:
    """`last_heartbeat_at`, `artifacts_purged_at`, `requeue_count`, `updated_at` không ra dây (K01).

    Catalog: B08M-G-230.
    """
    dataset = await make_dataset(db_session, family=OPENING)
    version = await make_dataset_version(db_session, dataset=dataset, status="ready")
    job = await make_training_job(
        db_session,
        dataset_version_id=version.id,
        family=OPENING,
        status="running",
        current_epoch=2,
        last_heartbeat_at=fake_clock.now(),
        requeue_count=3,
    )

    wire = job_out(job).model_dump(by_alias=True)

    assert {"lastHeartbeatAt", "artifactsPurgedAt", "requeueCount", "updatedAt"}.isdisjoint(wire)
    assert set(wire) == {
        "baseModel",
        "createdAt",
        "creatorId",
        "currentEpoch",
        "datasetVersionId",
        "epochs",
        "family",
        "id",
        "startedAt",
        "status",
    }


def test_metric_and_log_wire_models_drop_internal_keys() -> None:
    """`metric_point_out` bỏ `job_id`, `log_line_out` bỏ `job_id`/`dedupe_sha`; số đo `None` vắng.

    Catalog: B08M-G-230.
    """
    at = datetime(2026, 1, 1, tzinfo=UTC)
    metric = TrainingMetricRow(job_id="job_x", split="train", step=4, epoch=1, recorded_at=at, loss=0.25)
    log = TrainingLogRow(job_id="job_x", seq=7, at=at, level="warning", message="cham", dedupe_sha="a" * 64)

    metric_wire = metric_point_out(metric).model_dump(by_alias=True)
    log_wire = log_line_out(log).model_dump(by_alias=True)

    assert set(metric_wire) == {"epoch", "loss", "recordedAt", "split", "step"}
    assert set(log_wire) == {"at", "level", "message", "seq"}
    assert {"iou", "map50"}.isdisjoint(metric_wire)
