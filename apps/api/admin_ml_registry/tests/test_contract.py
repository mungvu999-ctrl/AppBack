"""Hợp đồng chung của module: metadata route, quyền, wire model, helper `registry` (B6-01 [2]).

Thân `service.py`/`upload.py` còn là `NotImplementedError` (việc của lớp sau), nhưng bốn thứ ở
đây đã chốt và test được ngay:

- **C07** cho cả năm `op`: `require_admin` khai ở router nên cổng chạy **trước** endpoint — vai
  `engineer` nhận 403 mà không chạm thân. Lớp sau **không** viết lại C07;
- `route_options` của N24 (`versioned`) và N26 (trần 512 MiB, `idempotency="off"`, 201);
- `version_out`/`family_out`: vắng trường thay vì `null` (W2, K02), không cột nội bộ nào (K01),
  `metrics` đúng khoá của họ;
- `model_ref_of` và `enqueue_evaluation`: hai dạng `ModelRef` và luật "gửi sau commit" (K17).
"""

import unicodedata
from typing import Any, Final

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.admin_ml_registry.errors import (
    MODEL_CHECKSUM_MISMATCH,
    MODEL_FORMAT_UNSUPPORTED,
    MODEL_VERSION_FAMILY_MISMATCH,
    MODEL_VERSION_NOT_EVALUATED,
    family_not_found,
    version_not_found,
)
from apps.api.admin_ml_registry.registry import EVALUATE_TASK, enqueue_evaluation, model_ref_of
from apps.api.admin_ml_registry.schemas import (
    CreateModelVersionMetadata,
    ModelVersionOut,
    SetActiveModelVersionIn,
    family_out,
    metrics_out,
    version_out,
)
from apps.api.admin_ml_registry.settings import (
    UPLOAD_BODY_LIMIT,
    get_ml_registry_settings,
    reset_ml_registry_settings_cache,
)
from apps.api.admin_ml_registry.tests._helpers import DIMENSION, ML_QUEUE, OPENING, WALL
from apps.api.core.auth import Principal, fake_token
from apps.api.core.routing import AppRoute
from packages.core.ids import new_id
from packages.db.hooks import after_commit_idle
from packages.db.models.admin_ml_registry import ModelFamilyRow, ModelVersionRow
from packages.messaging.redis import broker_redis_sync
from packages.storage.keys import model_artifact
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.admin_ml_registry import make_model_version
from packages.testing.fixtures.clock import FakeClock
from packages.testing.fixtures.messaging import queued_payloads

_ROUTES: Final[tuple[tuple[str, str, str], ...]] = (
    ("ml_list_families", "GET", "/api/admin/ml/model-families"),
    ("ml_activate_version", "PUT", "/api/admin/ml/model-families/wallSegmentation/active"),
    ("ml_list_versions", "GET", "/api/admin/ml/model-versions"),
    ("ml_upload_version", "POST", "/api/admin/ml/model-versions"),
    ("ml_read_version", "GET", "/api/admin/ml/model-versions/mdl_01KB6010000000000000000001"),
)
_BODIES: Final[dict[str, dict[str, Any]]] = {"ml_activate_version": {"baseVersion": 0, "body": {"versionId": None}}}


def _route(app: FastAPI, name: str) -> AppRoute:
    """Route đã mount theo tên endpoint (= `operationId`); không có → `KeyError` nói rõ tên."""
    for route in app.routes:
        if isinstance(route, AppRoute) and route.name == name:
            return route
    raise KeyError(f"route {name!r} chưa được mount")


def _engineer_headers(clock: FakeClock) -> dict[str, str]:
    """Header của một `engineer` — vai bị `require_admin` từ chối (C07)."""
    return {"Authorization": f"Bearer {fake_token(Principal(new_id('usr', clock), 'sid-eng', 'engineer'))}"}


async def _forbidden_for_engineer(client: AsyncClient, clock: FakeClock, op: str) -> None:
    """Gọi một trong năm route bằng vai `engineer` và khẳng định 403 `FORBIDDEN` (C07).

    Cổng `require_admin` khai ở router nên chạy trước endpoint: thân `NotImplementedError` của
    lớp sau không bao giờ được vào, và test này đúng ngay ở lớp hợp đồng.
    """
    _op, method, path = next(row for row in _ROUTES if row[0] == op)
    response = await client.request(method, path, headers=_engineer_headers(clock), json=_BODIES.get(op))

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_ml_list_families__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """N23 chỉ cho `admin`.

    Catalog: B08M-D-040, B09-mlr-003.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_list_families")


async def test_ml_activate_version__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """N24 chỉ cho `admin` — 403 tới trước cả guard 428 của ghi có version.

    Catalog: B08M-D-040, B09-mlr-007.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_activate_version")


async def test_ml_list_versions__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """N25 chỉ cho `admin`.

    Catalog: B08M-D-040, B09-mlr-010.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_list_versions")


async def test_ml_upload_version__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """N26 chỉ cho `admin` — 403 trước khi đọc một byte nào của luồng multipart.

    Catalog: B08M-D-040, B09-mlr-014.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_upload_version")


async def test_ml_read_version__C07(api_client: AsyncClient, fake_clock: FakeClock) -> None:
    """N27 chỉ cho `admin`.

    Catalog: B08M-D-040, B09-mlr-017.
    """
    await _forbidden_for_engineer(api_client, fake_clock, "ml_read_version")


def test_ml_activate_version_is_a_versioned_route(api_app: FastAPI) -> None:
    """N24 khai `versioned=True`, giữ `idempotency="auto"` và trần thân mặc định.

    Catalog: B08M-G-065.
    """
    route = _route(api_app, "ml_activate_version")

    assert route.options.versioned
    assert not route.uses_idempotency
    assert route.options.body_limit < UPLOAD_BODY_LIMIT


def test_ml_upload_version_takes_a_large_body_without_idempotency(api_app: FastAPI) -> None:
    """N26: trần thân 512 MiB buộc `idempotency="off"` (`routing.py:78`) và trả 201.

    Catalog: B08M-G-082.
    """
    route = _route(api_app, "ml_upload_version")

    assert route.options.body_limit == UPLOAD_BODY_LIMIT
    assert route.options.idempotency == "off"
    assert not route.options.versioned
    assert route.status_code == 201


def test_ml_routes_are_all_admin_only(api_app: FastAPI) -> None:
    """Cả năm route mang đúng năm `operationId` của bảng hợp đồng và đều được bảo vệ.

    Catalog: B08M-D-040.
    """
    routes = [_route(api_app, op) for op, _method, _path in _ROUTES]

    assert [route.wire_method for route in routes] == [method for _op, method, _path in _ROUTES]
    assert all(route.protected for route in routes)


def test_set_active_model_version_in_is_strict_on_both_shells() -> None:
    """Khoá lạ ở vỏ ngoài hay trong `body` đều là lỗi validate (C03).

    Catalog: B08M-G-065.
    """
    assert SetActiveModelVersionIn(base_version=3, body={"versionId": None}).body.version_id is None
    with pytest.raises(ValueError, match="creatorId"):
        SetActiveModelVersionIn(base_version=0, body={"versionId": None, "creatorId": "x"})
    with pytest.raises(ValueError, match="note"):
        SetActiveModelVersionIn(base_version=0, body={"versionId": None}, note="x")


def test_create_model_version_metadata_normalises_the_label() -> None:
    """`label` `nfc(strip)` (C16): NFD vào → NFC ra, khoảng trắng hai đầu không tính."""
    metadata = CreateModelVersionMetadata(
        checksumSha256="0" * 64,  # type: ignore[call-arg]  # alias camelCase của `WireRequest`
        family=OPENING,
        label=f"  {unicodedata.normalize('NFD', 'bản mới')}  ",
        weightsFormat="onnx",
    )

    assert metadata.label == "bản mới"
    assert metadata.checksum_sha256 == "0" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [("label", "   "), ("label", "x" * 81), ("checksumSha256", "A" * 64), ("weightsFormat", "pt")],
)
def test_create_model_version_metadata_rejects_bad_fields(field: str, value: str) -> None:
    """Nhãn rỗng/quá dài, checksum chữ hoa, định dạng lạ → lỗi validate có tên trường."""
    payload: dict[str, str] = {
        "checksumSha256": "0" * 64,
        "family": OPENING,
        "label": "bản",
        "weightsFormat": "onnx",
    }
    payload[field] = value

    with pytest.raises(ValueError, match=field):
        CreateModelVersionMetadata(**payload)


def test_metrics_out_demands_the_metric_of_the_family() -> None:
    """`metrics` phải chứa đúng khoá `FAMILY_METRIC[family]`; lệch khoá → `ValueError`."""
    assert metrics_out(DIMENSION, None) is None
    assert metrics_out(DIMENSION, {"cer": 0.25}) is not None
    with pytest.raises(ValueError, match="cer"):
        metrics_out(DIMENSION, {"iou": 0.9})
    with pytest.raises(ValueError, match="map50"):
        metrics_out(OPENING, {"map50": 0.5, "cer": 0.1})


async def test_version_out_drops_internal_columns(db_session: AsyncSession) -> None:
    """Bản `completed` của job huấn luyện: đúng bộ khoá dây, không cột nội bộ nào (K01)."""
    row = await make_model_version(db_session, family=OPENING, training=True)

    wire = version_out(row).model_dump(by_alias=True)

    assert set(wire) == {
        "checksumSha256",
        "createdAt",
        "creatorId",
        "datasetVersionId",
        "evaluationStatus",
        "family",
        "id",
        "label",
        "metrics",
        "trainingJobId",
        "weightsFormat",
    }
    assert wire["metrics"] == {"map50": pytest.approx(0.74)}
    assert wire["trainingJobId"] == row.training_job_id


async def test_version_out_omits_absent_fields_instead_of_null(db_session: AsyncSession) -> None:
    """Bản `pending` tải lên: `metrics`, `trainingJobId`, `datasetVersionId` **vắng** (W2, K02)."""
    row = await make_model_version(db_session, family=WALL, status="pending", weights_format="safetensors")

    wire = version_out(row).model_dump(by_alias=True)

    assert set(wire) == {
        "checksumSha256",
        "createdAt",
        "creatorId",
        "evaluationStatus",
        "family",
        "id",
        "label",
        "weightsFormat",
    }
    assert wire["weightsFormat"] == "safetensors"


def test_family_out_omits_a_family_without_an_active_version() -> None:
    """Họ tường chưa kích hoạt bản nào: `activeVersionId` vắng, không `null` (C17).

    Catalog: B08M-G-005.
    """
    assert family_out(ModelFamilyRow(family=WALL, revision=0)).model_dump(by_alias=True) == {
        "family": WALL,
        "revision": 0,
    }
    active = ModelFamilyRow(family=OPENING, revision=4, active_version_id="mdl_01KB6010000000000000000001")
    assert family_out(active).model_dump(by_alias=True)["activeVersionId"] == active.active_version_id


async def test_model_ref_of_builds_the_storage_form(db_session: AsyncSession) -> None:
    """Bản có `weights_key` → `ModelRef` dạng storage, khoá dưới `ml/models/{id}/`."""
    row = await make_model_version(db_session, family=OPENING)

    ref = model_ref_of(row)

    assert not ref.is_classic
    assert (ref.version_id, ref.pinned_name) == (row.id, None)
    assert ref.weights_key == row.weights_key
    assert ref.checksum_sha256 == row.checksum_sha256


def test_model_ref_of_builds_the_pinned_form() -> None:
    """Bản gốc (`pinned_name`, không `weights_key`) → `ModelRef` dạng ghim."""
    row = ModelVersionRow(
        id="mdl_01KB6010000000000000000001",
        family=OPENING,
        label="gốc",
        weights_format="onnx",
        checksum_sha256="1e252b7363e1936a0f06a40c221f144f65e86ae8ef01c96e71f7fb33cc3a334d",
        pinned_name="yolov8n",
        evaluation_status="pending",
        creator_id="system:pipeline",
    )

    ref = model_ref_of(row)

    assert (ref.pinned_name, ref.weights_key) == ("yolov8n", None)
    assert ref.version_id == row.id


async def test_enqueue_evaluation_sends_the_task_only_after_commit(
    db_session: AsyncSession, messaging_env: None
) -> None:
    """K17: rollback → hàng `ml.infer` rỗng; commit → đúng một thông điệp mang `version_id`."""
    broker = broker_redis_sync()
    broker.delete(ML_QUEUE)
    row = await make_model_version(db_session, family=DIMENSION)
    try:
        # `refresh` mở lại giao dịch mà factory vừa commit: `on_after_commit` gắn callback vào
        # giao dịch **đang** mở, nên không có giao dịch thì rollback chẳng có gì để bỏ.
        await db_session.refresh(row)
        enqueue_evaluation(db_session, row)
        await db_session.rollback()
        await after_commit_idle(db_session)
        assert queued_payloads(broker, ML_QUEUE) == []

        await db_session.refresh(row)
        enqueue_evaluation(db_session, row)
        await db_session.commit()
        await after_commit_idle(db_session)
        payloads = queued_payloads(broker, ML_QUEUE)
    finally:
        broker.delete(ML_QUEUE)
        broker.close()

    assert len(payloads) == 1
    assert payloads[0]["version_id"] == row.id
    assert EVALUATE_TASK.endswith("evaluate_version")


def test_model_version_out_keeps_metrics_as_json_numbers(fake_clock: FakeClock) -> None:
    """`metrics` là số JSON `float`, không chuỗi `Decimal` (HOP-DONG-MOI §8)."""
    wire = ModelVersionOut(
        checksum_sha256="0" * 64,
        created_at=fake_clock.now(),
        creator_id="system:pipeline",
        evaluation_status="completed",
        family=DIMENSION,
        id="mdl_01KB6010000000000000000002",
        label="gốc",
        metrics=metrics_out(DIMENSION, {"cer": 0.5}),
        weights_format="onnx",
    ).model_dump(mode="json", by_alias=True)

    assert wire["metrics"] == {"cer": 0.5}
    assert isinstance(wire["metrics"]["cer"], float)


def test_ml_registry_error_codes_are_all_422() -> None:
    """Bốn mã riêng đều 422 (lỗi ở dữ liệu yêu cầu trỏ tới, không ở quyền hay định dạng)."""
    codes = (
        MODEL_CHECKSUM_MISMATCH,
        MODEL_FORMAT_UNSUPPORTED,
        MODEL_VERSION_FAMILY_MISMATCH,
        MODEL_VERSION_NOT_EVALUATED,
    )

    assert {code.status for code in codes} == {422}
    assert [code.code for code in codes] == sorted(code.code for code in codes)


def test_ml_registry_not_found_helpers_name_the_resource() -> None:
    """404 của module mang đúng hai `resource` mà FE phân biệt (`modelFamily`, `modelVersion`)."""
    assert family_not_found().wire_params() == {"resource": "modelFamily"}
    assert version_not_found().wire_params() == {"resource": "modelVersion"}
    assert family_not_found().code.status == 404


def test_ml_registry_settings_reread_the_environment_after_a_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cấu hình đọc biến môi trường một lần; `reset_…_cache()` là cách duy nhất đọc lại."""
    reset_ml_registry_settings_cache()
    try:
        assert get_ml_registry_settings().model_upload_max_bytes == UPLOAD_BODY_LIMIT
        monkeypatch.setenv("MODEL_UPLOAD_MAX_BYTES", "1048576")
        assert get_ml_registry_settings().model_upload_max_bytes == UPLOAD_BODY_LIMIT
        reset_ml_registry_settings_cache()
        settings = get_ml_registry_settings()
    finally:
        monkeypatch.undo()
        reset_ml_registry_settings_cache()

    assert settings.model_upload_max_bytes == 1048576
    assert (settings.model_eval_requeue_after_s, settings.model_eval_max_attempts) == (1800, 6)


async def test_make_model_version_writes_a_real_weights_object(
    db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Factory có `storage` → object thật, byte đầu đúng định dạng và SHA khớp cột (M02, M03)."""
    row = await make_model_version(db_session, local_storage, family=OPENING, weights_format="onnx")

    info = await local_storage.stat(row.weights_key or "")

    assert info is not None
    assert info.sha256 == row.checksum_sha256
    assert row.weights_key == model_artifact(row.id, "weights.onnx")
