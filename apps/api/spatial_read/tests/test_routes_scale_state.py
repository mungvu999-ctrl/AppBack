"""ML-ACC-R9: `scaleState`/`scaleReasons` trên N16 và hiệu chỉnh một số đo (PUT #35) → `userCalibrated`."""

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
