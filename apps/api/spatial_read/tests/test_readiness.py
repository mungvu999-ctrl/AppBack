"""`apps.api.spatial_read.readiness.readiness_of` — bảng ca của luật H5 §2.2 (FIX-P0-10).

Hàm thuần, không DB: mỗi lý do bật/tắt một ca; đường truy vấn `latest_run_facts` được kiểm qua
HTTP ở `test_routes_layer.py` (`__C01_readiness_*`).
"""

from collections.abc import Mapping
from dataclasses import replace

import pytest

from apps.api.spatial_read.assemble import effective_scale_source
from apps.api.spatial_read.documents import ScaleSource, empty_document
from apps.api.spatial_read.readiness import RunFacts, readiness_of, scale_basis_of, scale_state_of
from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.core.object_keys import model_prefix
from packages.domain.spatial import SpatialLayer
from packages.ml_contracts.families import MODEL_FAMILIES, ModelFamily
from packages.ml_contracts.payloads import ModelRef
from packages.testing.factories.pipeline_orchestrate import classic_ref
from packages.testing.factories.spatial import sample_floor_layer

LEVEL_ID = "L-LEVEL000000"
REVIEWED = sample_floor_layer(0, level_id=LEVEL_ID, reviewed=True)
UNREVIEWED = sample_floor_layer(0, level_id=LEVEL_ID)
CRITICAL: Mapping[str, object] = {"hasCritical": True, "criticalCount": 1, "lowConfidence": {}}
CLEAN: Mapping[str, object] = {"hasCritical": False, "criticalCount": 0, "lowConfidence": {}}


def _stored(family: ModelFamily) -> dict[str, object]:
    """Ref dạng storage (không cổ điển) của một họ, dạng JSONB như `pin_models` ghi."""
    version_id = new_id("mdl", SystemClock())
    return ModelRef(
        version_id=version_id,
        family=family,
        weights_key=f"{model_prefix(version_id)}model.onnx",
        pinned_name=None,
        checksum_sha256="a" * 64,
    ).model_dump(mode="json")


def _run(*, summary: Mapping[str, object] | None = CLEAN, classic: tuple[ModelFamily, ...] = ()) -> RunFacts:
    """Dữ kiện một lượt: họ trong `classic` ghim cổ điển, họ còn lại ghim bản storage."""
    pinned: dict[str, object] = {
        family: classic_ref(family).model_dump(mode="json") if family in classic else _stored(family)
        for family in MODEL_FAMILIES
    }
    return RunFacts(pinned=pinned, quality_summary=summary)


@pytest.mark.parametrize(
    ("run", "scale_source", "layer", "state", "reasons"),
    [
        pytest.param(None, "human", UNREVIEWED, "notEvaluated", (), id="no-run"),
        pytest.param(None, "pipeline", UNREVIEWED, "notEvaluated", ("scaleUnresolved",), id="no-run-scale"),
        pytest.param(_run(), "human", UNREVIEWED, "ready", (), id="ready"),
        pytest.param(_run(), "pipeline", REVIEWED, "needsReview", ("scaleUnresolved",), id="scale-pipeline"),
        pytest.param(
            _run(), "project_default", REVIEWED, "needsReview", ("scaleUnresolved",), id="scale-project-default"
        ),
        pytest.param(_run(), "none", UNREVIEWED, "ready", (), id="scale-none"),
        pytest.param(_run(summary=CRITICAL), "human", UNREVIEWED, "needsReview", ("criticalIssues",), id="critical"),
        pytest.param(_run(summary=None), "human", UNREVIEWED, "ready", (), id="summary-null"),
        pytest.param(
            _run(classic=("wallSegmentation",)), "human", UNREVIEWED, "needsReview", ("fallbackWallModel",), id="wall"
        ),
        pytest.param(
            _run(classic=("openingAndFurnitureDetection",)),
            "human",
            UNREVIEWED,
            "needsReview",
            ("fallbackObjectModel",),
            id="object",
        ),
        pytest.param(
            _run(classic=("dimensionReading",)), "human", UNREVIEWED, "needsReview", ("fallbackTextModel",), id="text"
        ),
        pytest.param(
            _run(summary=CRITICAL, classic=MODEL_FAMILIES),
            "pipeline",
            UNREVIEWED,
            "needsReview",
            ("scaleUnresolved", "criticalIssues", "fallbackWallModel", "fallbackObjectModel", "fallbackTextModel"),
            id="all-in-order",
        ),
        pytest.param(
            _run(summary=CRITICAL, classic=MODEL_FAMILIES), "human", REVIEWED, "ready", (), id="reviewed-clears-run"
        ),
        pytest.param(
            _run(summary=CRITICAL, classic=MODEL_FAMILIES),
            "pipeline",
            REVIEWED,
            "needsReview",
            ("scaleUnresolved", "criticalIssues", "fallbackWallModel", "fallbackObjectModel", "fallbackTextModel"),
            id="reviewed-but-scale-unresolved-keeps-all",
        ),
        pytest.param(RunFacts(pinned={}, quality_summary=None), "human", UNREVIEWED, "ready", (), id="no-pins-row"),
    ],
)
def test_readiness_of_table(
    run: RunFacts | None, scale_source: ScaleSource, layer: SpatialLayer, state: str, reasons: tuple[str, ...]
) -> None:
    """Mỗi lý do bật/tắt đúng luật; thứ tự lý do cố định; `level_reviewed` chỉ tắt lý do cấp lượt."""
    out = readiness_of(run=run, scale_source=scale_source, layer=layer)
    assert (out.state, out.reasons) == (state, reasons)


def test_readiness_of_human_scale_demoted_on_other_page() -> None:
    """Tỉ lệ `human` gắn trang P1, trang hiện tại P2 → hạ về `pipeline` → lại `scaleUnresolved`."""
    doc = replace(empty_document(1), layer=REVIEWED, scale_source="human", scale_page_key="P1")
    same = readiness_of(run=_run(), scale_source=effective_scale_source(doc, "P1"), layer=doc.layer)
    other = readiness_of(run=_run(), scale_source=effective_scale_source(doc, "P2"), layer=doc.layer)
    assert (same.state, same.reasons) == ("ready", ())
    assert (other.state, other.reasons) == ("needsReview", ("scaleUnresolved",))


GEOMETRY: Mapping[str, object] = {
    "hasCritical": True,
    "criticalCount": 2,
    "lowConfidence": {},
    "checks": ["ROOMS_WITHOUT_DOOR", "MULTIPLE_PLANS", "ROOM_USAGE_UNKNOWN"],
    "criticalChecks": ["NEW_CODE", "MULTIPLE_PLANS"],
}
"""Lượt BENCH3D-09: hai kiểm hình học critical (một mã lạ), không lỗi toàn vẹn."""


@pytest.mark.parametrize(
    ("summary", "reasons", "checks"),
    [
        pytest.param(GEOMETRY, ("geometrySuspect",), ("MULTIPLE_PLANS", "NEW_CODE"), id="geometry-only"),
        pytest.param(
            {**GEOMETRY, "criticalCount": 3},
            ("criticalIssues", "geometrySuspect"),
            ("MULTIPLE_PLANS", "NEW_CODE"),
            id="geometry-and-integrity",
        ),
        pytest.param({**GEOMETRY, "criticalChecks": [], "criticalCount": 1}, ("criticalIssues",), None, id="integrity"),
        pytest.param({**CRITICAL, "checks": ["OPEN_ENVELOPE"]}, ("criticalIssues",), None, id="pre-bench3d09-run"),
        pytest.param(
            {**GEOMETRY, "criticalCount": "x"},
            ("criticalIssues", "geometrySuspect"),
            ("MULTIPLE_PLANS", "NEW_CODE"),
            id="bad-count",
        ),
        pytest.param({**CLEAN, "criticalChecks": "x"}, (), None, id="bad-list"),
    ],
)
def test_readiness_of_geometry_checks(
    summary: Mapping[str, object], reasons: tuple[str, ...], checks: tuple[str, ...] | None
) -> None:
    """BENCH3D-09: kiểm hình học critical → `geometrySuspect` + `checks` thứ tự cố định; toàn vẹn → `criticalIssues`."""
    out = readiness_of(run=_run(summary=summary), scale_source="human", layer=UNREVIEWED)
    assert (out.reasons, out.checks) == (reasons, checks)
    reviewed = readiness_of(run=_run(summary=summary), scale_source="human", layer=REVIEWED)
    assert (reviewed.state, reviewed.reasons, reviewed.checks) == ("ready", (), None)


@pytest.mark.parametrize(
    ("basis", "scale_checks", "unresolved"),
    [
        pytest.param("chain", [], False, id="chain-agreed"),
        pytest.param("note", [], True, id="note-lone-ocr-still-unresolved"),
        pytest.param("paired", ["anisotropic"], True, id="paired-anisotropic"),
        pytest.param("door", [], True, id="door-estimate"),
        pytest.param(None, [], True, id="no-basis"),
    ],
)
def test_readiness_of_measured_scale(basis: str | None, scale_checks: list[str], unresolved: bool) -> None:
    """BENCH3D-09: tỉ lệ đo từ bản vẽ, không nghi vấn → không `scaleUnresolved`; ước lượng vẫn báo."""
    run = _run(summary={**CLEAN, "scaleBasis": basis, "scaleChecks": scale_checks})
    out = readiness_of(
        run=run, scale_source="pipeline", layer=REVIEWED, scale_basis=scale_basis_of(run, "pipeline", "pipeline")
    )
    assert ("scaleUnresolved" in out.reasons) is unresolved


def test_scale_basis_of() -> None:
    """`human` còn hiệu lực → `human`; tỉ lệ pipeline → gốc lượt ghi; người đặt bị hạ hạng, lượt cũ, gốc lạ → vắng."""
    run = _run(summary={**CLEAN, "scaleBasis": "door"})
    assert scale_basis_of(run, "human", "human") == "human"
    assert scale_basis_of(run, "pipeline", "pipeline") == "door"
    assert scale_basis_of(run, "project_default", "project_default") == "door"
    assert scale_basis_of(run, "human", "pipeline") is None
    assert scale_basis_of(None, "pipeline", "pipeline") is None
    assert scale_basis_of(_run(summary=None), "pipeline", "pipeline") is None
    assert scale_basis_of(_run(summary={**CLEAN, "scaleBasis": "human"}), "pipeline", "pipeline") is None
    assert scale_basis_of(run, "none", "none") is None


@pytest.mark.parametrize(
    ("effective", "stored", "basis", "summary", "expected"),
    [
        pytest.param("human", "human", "human", None, ("userCalibrated", ()), id="human"),
        pytest.param("none", "none", None, None, (None, ()), id="no-scale"),
        pytest.param(
            "project_default", "project_default", "default", CLEAN, ("unresolved", ("projectDefault",)), id="default"
        ),
        pytest.param("pipeline", "human", None, CLEAN, ("unresolved", ("pageChanged",)), id="page-changed"),
        pytest.param("pipeline", "pipeline", None, CLEAN, ("unresolved", ("basisUnknown",)), id="old-run"),
        pytest.param("pipeline", "pipeline", "chain", None, ("unresolved", ("basisUnknown",)), id="no-summary"),
        pytest.param(
            "pipeline", "pipeline", "chain", {"scaleChecks": []}, ("determined", ("chain",)), id="chain-agreed"
        ),
        pytest.param("pipeline", "pipeline", "paired", {}, ("determined", ("paired",)), id="paired-no-key"),
        pytest.param(
            "pipeline",
            "pipeline",
            "chain",
            {"scaleChecks": ["anisotropic"]},
            ("unresolved", ("chain", "anisotropic")),
            id="chain-doubt",
        ),
        pytest.param("pipeline", "pipeline", "door", {"scaleChecks": []}, ("estimated", ("door",)), id="door-745"),
        pytest.param(
            "pipeline",
            "pipeline",
            "door",
            {"scaleChecks": ["estimateSpread"]},
            ("estimated", ("door", "estimateSpread")),
            id="door-spread",
        ),
        pytest.param("pipeline", "pipeline", "wallThickness", {}, ("estimated", ("wallThickness",)), id="thickness"),
        pytest.param("pipeline", "pipeline", "note", {"scaleChecks": []}, ("estimated", ("note",)), id="note"),
        pytest.param(
            "pipeline",
            "pipeline",
            "door",
            {"scaleChecks": ["sourceConflict"]},
            ("unresolved", ("door", "sourceConflict")),
            id="door-conflict",
        ),
    ],
)
def test_scale_state_of_table(
    effective: ScaleSource,
    stored: ScaleSource,
    basis: str | None,
    summary: Mapping[str, object] | None,
    expected: tuple[str | None, tuple[str, ...]],
) -> None:
    """ML-ACC-R9: mỗi nhánh của luật; cửa 745 mm tối đa `estimated`, chuỗi khớp `determined`, mâu thuẫn `unresolved`."""
    assert scale_state_of(effective, stored, basis, summary) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("basis", [None, "chain", "paired", "note", "door", "wallThickness", "default", "human"])
@pytest.mark.parametrize("checks", [[], ["anisotropic"], ["sourceConflict"], ["estimateSpread"]])
@pytest.mark.parametrize("source", ["none", "pipeline", "project_default", "human"])
def test_scale_unresolved_iff_state_is_doubtful(source: ScaleSource, checks: list[str], basis: str | None) -> None:
    """Một luật, hai đầu ra: `scaleUnresolved` có đúng khi `scaleState` không phải determined/userCalibrated/vắng."""
    run = _run(summary={**CLEAN, "scaleChecks": checks})
    state, _ = scale_state_of(source, source, basis, run.quality_summary)  # type: ignore[arg-type]
    out = readiness_of(run=run, scale_source=source, layer=REVIEWED, scale_basis=basis)  # type: ignore[arg-type]
    assert ("scaleUnresolved" in out.reasons) is (state not in (None, "determined", "userCalibrated"))
