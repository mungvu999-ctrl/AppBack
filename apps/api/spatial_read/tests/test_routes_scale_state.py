"""ML-ACC-R9: `scaleState`/`scaleReasons` trên N16 và hiệu chỉnh một số đo (PUT #35) → `userCalibrated`.

ML-ACC-R10 K4: PUT `scaleEvidence: "machineAccepted"` giữ độ tin của máy lúc chấp nhận (thêm `userAccepted`).
"""

from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.projects.tests.test_routes_common import headers_of
from apps.api.spatial_read.tests._helpers import make_scene
from apps.api.spatial_read.tests._read_helpers import layer_path
from apps.api.spatial_read.tests.test_routes_layer import CRITICAL_SUMMARY, _completed_classic_run
from apps.api.spatial_write.tests._route_helpers import put_layer, write_body
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.spatial import make_floor_document, sample_floor_layer
from packages.testing.fixtures.clock import FakeClock

CASES: dict[str, tuple[str, dict[str, Any] | None, str | None, list[str] | None]] = {
    # tên: (scale_source, quality_summary thêm, scaleState mong đợi, scaleReasons mong đợi)
    "chain-clean": ("pipeline", {"scaleBasis": "chain", "scaleChecks": []}, "determined", ["chain"]),
    "chain-conflict": (
        "pipeline",
        {"scaleBasis": "chain", "scaleChecks": ["sourceConflict"]},
        "unresolved",
        ["chain", "sourceConflict"],
    ),
    "chain-malformed": (
        "pipeline",
        {"scaleBasis": "chain", "scaleChecks": "anisotropic"},
        "unresolved",
        ["basisUnknown"],
    ),
    "door": ("pipeline", {"scaleBasis": "door", "scaleChecks": []}, "estimated", ["door"]),
    "door-spread": (
        "pipeline",
        {"scaleBasis": "door", "scaleChecks": ["estimateSpread"]},
        "estimated",
        ["door", "estimateSpread"],
    ),
    "project-default": ("project_default", {"scaleBasis": "default"}, "unresolved", ["projectDefault"]),
    "human": ("human", None, "userCalibrated", []),
    "none": ("none", None, None, None),
}


@pytest.mark.parametrize("name", list(CASES))
async def test_spatial_read_layer__scale_state(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    name: str,
) -> None:
    """Tám loại tầng → khoá đúng trên N16; `scaleStatus` cũ giữ nguyên; tầng chưa có tỉ lệ thì vắng cả hai khoá."""
    source, extra, state, reasons = CASES[name]
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await make_floor_document(
        db_session,
        floor_pk=floor.pk,
        layer=sample_floor_layer(0, level_id=floor.level_id),
        scale=None if source == "none" else Decimal("12.700000"),
        scale_source=source,
        clock=fake_clock,
    )
    if extra is not None:
        await _completed_classic_run(
            db_session, local_storage, fake_clock, scene=scene, floor=floor, summary={**CRITICAL_SUMMARY, **extra}
        )
    await db_session.commit()
    body = (await api_client.get(layer_path(scene.project.id, floor.level_id), headers=headers_of(scene.owner))).json()
    assert body.get("scaleState") == state
    assert body.get("scaleReasons") == reasons
    assert ("scaleStatus" in body) is (source in ("pipeline", "project_default"))
    assert ("scaleUnresolved" in body["readiness"]["reasons"]) is (state in ("estimated", "unresolved"))


async def test_spatial_write_layer__scale_calibration_becomes_user_calibrated(
    api_client: httpx.AsyncClient, db_session: AsyncSession, fake_clock: FakeClock
) -> None:
    """Tầng pipeline → PUT `scaleMillimetresPerPixel` → `userCalibrated`, và đọc lại lần hai vẫn vậy."""
    scene = await make_scene(db_session)
    floor = scene.floors[0]
    await make_floor_document(
        db_session,
        floor_pk=floor.pk,
        layer=sample_floor_layer(0, level_id=floor.level_id),
        scale=Decimal("12.700000"),
        scale_source="pipeline",
        clock=fake_clock,
    )
    await db_session.commit()
    path, headers = layer_path(scene.project.id, floor.level_id), headers_of(scene.owner)
    before = (await api_client.get(path, headers=headers)).json()
    assert before["scaleStatus"] == "unresolved"
    assert before["scaleState"] == "unresolved"
    put = await put_layer(
        api_client, scene.project.id, floor.level_id, scene.owner, write_body(before["revision"], scale=15.0)
    )
    assert put.status_code == 200
    for _ in range(2):
        after = (await api_client.get(path, headers=headers)).json()
        assert after["scaleState"] == "userCalibrated"
        assert after["scaleReasons"] == []
        assert after["scaleBasis"] == "human"
        assert "scaleStatus" not in after
        assert after["level"]["scaleMillimetresPerPixel"] == 15.0
        assert "scaleUnresolved" not in after["readiness"]["reasons"]


ACCEPT_CASES: dict[str, tuple[dict[str, Any], str, list[str]]] = {
    # tên: (quality_summary thêm của lượt lúc chấp nhận, scaleState sau chấp nhận, scaleReasons sau chấp nhận)
    "determined": ({"scaleBasis": "chain", "scaleChecks": []}, "determined", ["chain", "userAccepted"]),
    "estimated": ({"scaleBasis": "door", "scaleChecks": []}, "estimated", ["door", "userAccepted"]),
    "unresolved": (
        {"scaleBasis": "chain", "scaleChecks": ["anisotropic"]},
        "unresolved",
        ["chain", "anisotropic", "userAccepted"],
    ),
    "malformed": (
        {"scaleBasis": "chain", "scaleChecks": "anisotropic"},
        "unresolved",
        ["basisUnknown", "userAccepted"],
    ),
}


async def _pipeline_floor(
    db: AsyncSession, storage: LocalDiskStorage, clock: FakeClock, summary: dict[str, Any]
) -> tuple[Any, Any]:
    """Tầng pipeline 12,7 mm/px có một lượt `completed` mang `summary`; trả `(scene, floor)` đã commit."""
    scene = await make_scene(db)
    floor = scene.floors[0]
    await make_floor_document(
        db,
        floor_pk=floor.pk,
        layer=sample_floor_layer(0, level_id=floor.level_id),
        scale=Decimal("12.700000"),
        scale_source="pipeline",
        clock=clock,
    )
    await _completed_classic_run(db, storage, clock, scene=scene, floor=floor, summary={**CRITICAL_SUMMARY, **summary})
    await db.commit()
    return scene, floor


@pytest.mark.parametrize("name", list(ACCEPT_CASES))
async def test_spatial_write_layer__scale_accepted(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    fake_clock: FakeClock,
    name: str,
) -> None:
    """K4 #1-#3: chấp nhận số máy → N16 giữ độ tin của máy + `userAccepted`; lượt sạch chạy sau không nâng (R2).

    `scaleStatus` vắng như mọi tỉ lệ người đặt (quy trình), `scaleBasis` là gốc của máy, readiness báo
    `scaleUnresolved` đúng khi độ tin không phải `determined`.
    """
    summary, state, reasons = ACCEPT_CASES[name]
    scene, floor = await _pipeline_floor(db_session, local_storage, fake_clock, summary)
    path, headers = layer_path(scene.project.id, floor.level_id), headers_of(scene.owner)
    before = (await api_client.get(path, headers=headers)).json()
    payload = write_body(before["revision"], scale=12.7)
    payload["body"]["scaleEvidence"] = "machineAccepted"
    put = await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, payload)
    assert put.status_code == 200
    later = {**CRITICAL_SUMMARY, "scaleBasis": "paired", "scaleChecks": []}
    await _completed_classic_run(db_session, local_storage, fake_clock, scene=scene, floor=floor, summary=later)
    await db_session.commit()
    after = (await api_client.get(path, headers=headers)).json()
    assert after["revision"] == before["revision"] + 1
    assert after["scaleState"] == state
    assert after["scaleReasons"] == reasons
    assert after.get("scaleBasis") == (None if name == "malformed" else summary["scaleBasis"])
    assert "scaleStatus" not in after
    assert ("scaleUnresolved" in after["readiness"]["reasons"]) is (state != "determined")


async def test_spatial_write_layer__scale_measured_after_accept(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage, fake_clock: FakeClock
) -> None:
    """K4 #13: đã chấp nhận số máy `estimated` → đo thật (`measured`) → `userCalibrated []`, gốc `human`."""
    scene, floor = await _pipeline_floor(db_session, local_storage, fake_clock, {"scaleBasis": "door"})
    path, headers = layer_path(scene.project.id, floor.level_id), headers_of(scene.owner)
    revision = (await api_client.get(path, headers=headers)).json()["revision"]
    for step, (scale, evidence) in enumerate([(12.7, "machineAccepted"), (14.0, "measured")]):
        payload = write_body(revision + step, scale=scale)
        payload["body"]["scaleEvidence"] = evidence
        assert (await put_layer(api_client, scene.project.id, floor.level_id, scene.owner, payload)).status_code == 200
    after = (await api_client.get(path, headers=headers)).json()
    assert (after["scaleState"], after["scaleReasons"], after["scaleBasis"]) == ("userCalibrated", [], "human")
    assert "scaleUnresolved" not in after["readiness"]["reasons"]
