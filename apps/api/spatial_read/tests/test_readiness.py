"""`apps.api.spatial_read.readiness.readiness_of` — bảng ca của luật H5 §2.2 (FIX-P0-10).

Hàm thuần, không DB: mỗi lý do bật/tắt một ca; đường truy vấn `latest_run_facts` được kiểm qua
HTTP ở `test_routes_layer.py` (`__C01_readiness_*`).
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, get_args

import pytest
from pydantic import ValidationError

from apps.api.spatial_read.assemble import effective_scale_source
from apps.api.spatial_read.documents import ScaleSource, empty_document
from apps.api.spatial_read.readiness import (
    RunFacts,
    notices_of,
    readiness_of,
    scale_basis_of,
    scale_snapshot_of,
    scale_state_of,
)
from apps.api.spatial_read.wire import ReadinessReason
from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.core.object_keys import model_prefix
from packages.domain.spatial import ScaleAcceptance, SpatialLayer
from packages.domain.spatial.model import Roof
from packages.ml_contracts.families import MODEL_FAMILIES, ModelFamily
from packages.ml_contracts.payloads import ModelRef
from packages.testing.factories.pipeline_orchestrate import classic_ref
from packages.testing.factories.spatial import sample_floor_layer

LEVEL_ID = "L-LEVEL000000"
REVIEWED = sample_floor_layer(0, level_id=LEVEL_ID, reviewed=True)
UNREVIEWED = sample_floor_layer(0, level_id=LEVEL_ID)
CRITICAL: Mapping[str, object] = {"hasCritical": True, "criticalCount": 1, "lowConfidence": {}}
CLEAN: Mapping[str, object] = {"hasCritical": False, "criticalCount": 0, "lowConfidence": {}}
SECTION_ONLY: Mapping[str, object] = {**CLEAN, "sectionOnly": True}


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
        pytest.param(
            _run(summary=SECTION_ONLY, classic=MODEL_FAMILIES), "pipeline", UNREVIEWED, "ready", (), id="section-only"
        ),
        pytest.param(
            _run(
                summary={**SECTION_ONLY, "criticalChecks": ["OPEN_ENVELOPE"], "hasCritical": True, "criticalCount": 1}
            ),
            "project_default",
            UNREVIEWED,
            "ready",
            (),
            id="section-only-never-blocks",
        ),
    ],
)
def test_readiness_of_table(
    run: RunFacts | None, scale_source: ScaleSource, layer: SpatialLayer, state: str, reasons: tuple[str, ...]
) -> None:
    """Mỗi lý do bật/tắt đúng luật; thứ tự lý do cố định; `level_reviewed` chỉ tắt lý do cấp lượt."""
    out = readiness_of(run=run, scale_source=scale_source, layer=layer)
    assert (out.state, out.reasons) == (state, reasons)


def _pinned(name: str, family: ModelFamily) -> dict[str, object]:
    """Ref dạng ghim (`pinned_name`) của một họ, dạng JSONB như `pin_models` ghi."""
    return ModelRef(
        version_id=new_id("mdl", SystemClock()),
        family=family,
        weights_key=None,
        pinned_name=name,
        checksum_sha256="b" * 64,
    ).model_dump(mode="json")


def _with_pin(family: ModelFamily, ref: object) -> RunFacts:
    """Lượt sạch, mọi họ ghim bản storage, riêng `family` thay bằng `ref`."""
    base = _run()
    return RunFacts(pinned={**base.pinned, family: ref}, quality_summary=CLEAN)


@pytest.mark.parametrize(
    ("family", "ref", "reasons"),
    [
        pytest.param(
            "openingAndFurnitureDetection",
            _pinned("yolov8n", "openingAndFurnitureDetection"),
            ("fallbackObjectModel",),
            id="detection-yolov8n",
        ),
        pytest.param(
            "openingAndFurnitureDetection",
            _pinned("yolov8s", "openingAndFurnitureDetection"),
            ("fallbackObjectModel",),
            id="detection-yolov8s",
        ),
        pytest.param(
            "openingAndFurnitureDetection", _stored("openingAndFurnitureDetection"), (), id="detection-weights-key"
        ),
        pytest.param("dimensionReading", _pinned("rapidocrRec", "dimensionReading"), (), id="other-family-pinned"),
    ],
)
def test_readiness_of_coco_pinned_is_fallback(family: ModelFamily, ref: object, reasons: tuple[str, ...]) -> None:
    """L2-N01: detector COCO gốc ghim (không thấy ô mở) báo `fallbackObjectModel`; ref storage và họ khác thì không."""
    out = readiness_of(run=_with_pin(family, ref), scale_source="human", layer=UNREVIEWED)
    assert out.reasons == reasons
    assert out.state == ("needsReview" if reasons else "ready")


def test_readiness_of_coco_pinned_silenced_when_reviewed() -> None:
    """Tầng đã kiểm (`level_reviewed`) tắt lý do dự phòng, kể cả với detector COCO ghim."""
    run = _with_pin("openingAndFurnitureDetection", _pinned("yolov8n", "openingAndFurnitureDetection"))
    assert readiness_of(run=run, scale_source="human", layer=REVIEWED).reasons == ()


def test_readiness_of_malformed_pin_still_raises() -> None:
    """Dữ liệu `pinned` hỏng dạng vẫn nổ `ValidationError` như trước (không im lặng thành "ready")."""
    with pytest.raises(ValidationError):
        readiness_of(
            run=_with_pin("openingAndFurnitureDetection", {"pinnedName": 7}), scale_source="human", layer=UNREVIEWED
        )


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
        # ML-ACC-R10: `criticalChecks` hỏng dạng = lượt CÓ ghi nghi vấn mà ta không đọc được mã nào.
        # Im lặng trả "ready" là giấu nghi vấn — cùng luật với `scaleChecks` của #70.
        pytest.param({**CLEAN, "criticalChecks": "x"}, ("geometrySuspect",), None, id="bad-str"),
        pytest.param({**CLEAN, "criticalChecks": 3}, ("geometrySuspect",), None, id="bad-number"),
        pytest.param({**CLEAN, "criticalChecks": {"a": 1}}, ("geometrySuspect",), None, id="bad-dict"),
        pytest.param(
            {**CLEAN, "criticalChecks": ["MULTIPLE_PLANS", 7]}, ("geometrySuspect",), None, id="bad-mixed-list"
        ),
        pytest.param(
            {**CRITICAL, "criticalChecks": "x"},
            ("criticalIssues", "geometrySuspect"),
            None,
            id="bad-list-with-integrity",
        ),
        pytest.param({**CLEAN, "criticalChecks": None}, (), None, id="null-is-valid"),
        # L2-N02: mất raster trang → chế độ suy giảm, báo như kiểm hình học, sau các kiểm đã biết, trước mã lạ.
        pytest.param(
            {**CRITICAL, "criticalCount": 3, "criticalChecks": ["X", "PAGE_RASTER_UNAVAILABLE", "OPEN_ENVELOPE"]},
            ("geometrySuspect",),
            ("OPEN_ENVELOPE", "PAGE_RASTER_UNAVAILABLE", "X"),
            id="page-raster-unavailable",
        ),
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
        pytest.param(
            "pipeline",
            "pipeline",
            "chain",
            {"scaleChecks": "anisotropic"},
            ("unresolved", ("basisUnknown",)),
            id="chain-checks-string",
        ),
        pytest.param(
            "pipeline",
            "pipeline",
            "chain",
            {"scaleChecks": 3},
            ("unresolved", ("basisUnknown",)),
            id="chain-checks-number",
        ),
        pytest.param(
            "pipeline",
            "pipeline",
            "chain",
            {"scaleChecks": {"a": 1}},
            ("unresolved", ("basisUnknown",)),
            id="chain-checks-dict",
        ),
        pytest.param(
            "pipeline",
            "pipeline",
            "chain",
            {"scaleChecks": ["anisotropic", 3]},
            ("unresolved", ("basisUnknown",)),
            id="chain-checks-mixed",
        ),
        pytest.param(
            "pipeline",
            "pipeline",
            "door",
            {"scaleChecks": "estimateSpread"},
            ("unresolved", ("basisUnknown",)),
            id="door-checks-string",
        ),
        pytest.param(
            "pipeline",
            "pipeline",
            "paired",
            {"scaleChecks": []},
            ("determined", ("paired",)),
            id="paired-checks-empty",
        ),
        pytest.param(
            "pipeline",
            "pipeline",
            "chain",
            {"scaleChecks": None},
            ("determined", ("chain",)),
            id="chain-checks-null",
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
    """ML-ACC-R9/R10: mỗi nhánh của luật; cửa 745 mm tối đa `estimated`; `scaleChecks` hỏng dạng → `unresolved`."""
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


_TRUST = {"unresolved": 0, "estimated": 1, "determined": 2, "userCalibrated": 3}
"""Thứ tự độ tin (ML-ACC-R10 K4): chấp nhận số máy không bao giờ đi lên thang này."""

_LATER_DETERMINED: Mapping[str, object] = {**CLEAN, "scaleBasis": "chain", "scaleChecks": []}
"""Tóm tắt của một lượt chạy **sau** lần chấp nhận, sạch nhất có thể: không được nâng độ tin (R2)."""


def _machine(stored: ScaleSource, summary: Mapping[str, object] | None) -> tuple[str | None, tuple[str, ...]]:
    """Độ tin máy báo trên N16 ngay trước lần chấp nhận (đường pipeline/project_default của N16)."""
    run = None if summary is None else _run(summary=summary)
    basis = scale_basis_of(run, stored, stored)
    return scale_state_of(stored, stored, basis, summary)


@pytest.mark.parametrize(
    ("stored", "summary", "expected"),
    [
        pytest.param("pipeline", {"scaleBasis": "chain", "scaleChecks": []}, ("determined", ("chain",)), id="chain"),
        pytest.param(
            "pipeline",
            {"scaleBasis": "chain", "scaleChecks": ["anisotropic"]},
            ("unresolved", ("chain", "anisotropic")),
            id="chain-aniso",
        ),
        pytest.param("pipeline", {"scaleBasis": "door"}, ("estimated", ("door",)), id="door"),
        pytest.param(
            "pipeline",
            {"scaleBasis": "door", "scaleChecks": ["sourceConflict"]},
            ("unresolved", ("door", "sourceConflict")),
            id="door-conflict",
        ),
        pytest.param("pipeline", {"scaleBasis": "note", "scaleChecks": []}, ("estimated", ("note",)), id="note"),
        pytest.param("project_default", None, ("unresolved", ("projectDefault",)), id="project-default"),
        pytest.param("pipeline", None, ("unresolved", ("basisUnknown",)), id="no-run"),
        pytest.param(
            "pipeline",
            {"scaleBasis": "chain", "scaleChecks": "anisotropic"},
            ("unresolved", ("basisUnknown",)),
            id="malformed-checks",
        ),
    ],
)
def test_scale_state_accepted_keeps_machine_state(
    stored: ScaleSource, summary: Mapping[str, object] | None, expected: tuple[str, tuple[str, ...]]
) -> None:
    """Bảng chân trị K4 #1-#4: chấp nhận số máy giữ đúng độ tin của máy lúc chấp nhận, thêm `userAccepted`."""
    snapshot = scale_snapshot_of(stored, summary)
    state, reasons = scale_state_of("human", "human", "human", _LATER_DETERMINED, snapshot)
    assert (state, reasons) == (expected[0], (*expected[1], "userAccepted"))
    assert _machine(stored, summary) == expected


_BASES = [None, "chain", "paired", "note", "door", "wallThickness", "default", "human", "bogus"]
_CHECKS: list[object] = [
    [],
    None,
    ["anisotropic"],
    ["sourceConflict"],
    ["estimateSpread"],
    ["anisotropic", "sourceConflict"],
    ["estimateSpread", "sourceConflict"],
    ["anisotropic", "estimateSpread", "sourceConflict"],
    "anisotropic",
    [1],
]


@pytest.mark.parametrize("stored", ["pipeline", "project_default", "human", "none"])
@pytest.mark.parametrize("checks", _CHECKS)
@pytest.mark.parametrize("basis", _BASES)
def test_scale_state_accepted_never_user_calibrated(stored: ScaleSource, checks: object, basis: str | None) -> None:
    """Tính chất K4: mọi (nguồn, gốc, `scaleChecks`) — không bao giờ `userCalibrated`, không tin hơn máy lúc chấp nhận.

    Máy lúc chấp nhận = N16 của nguồn ấy; nguồn `human`/`none` không có độ tin máy riêng nên so với đường pipeline.
    Lượt chạy sau sạch (`_LATER_DETERMINED`) không đổi kết quả; readiness báo `scaleUnresolved` theo đúng độ tin ấy.
    """
    summary = {**CLEAN, "scaleBasis": basis, "scaleChecks": checks}
    snapshot = scale_snapshot_of(stored, summary)
    state, reasons = scale_state_of("human", "human", "human", _LATER_DETERMINED, snapshot)
    machine, _ = _machine("project_default" if stored == "project_default" else "pipeline", summary)
    assert state is not None
    assert machine is not None
    assert state != "userCalibrated"
    assert _TRUST[state] <= _TRUST[machine]
    assert reasons[-1] == "userAccepted"
    run = _run(summary=_LATER_DETERMINED)
    out = readiness_of(run=run, scale_source="human", layer=REVIEWED, scale_accepted=snapshot)
    assert ("scaleUnresolved" in out.reasons) is (state != "determined")


def test_scale_state_measured_human_unchanged() -> None:
    """PUT không có `scaleEvidence` (đo thật, dữ liệu cũ): `userCalibrated []`, gốc `human` — hành vi cũ."""
    assert scale_state_of("human", "human", "human", _LATER_DETERMINED) == ("userCalibrated", ())
    assert scale_basis_of(_run(summary=_LATER_DETERMINED), "human", "human") == "human"


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        pytest.param(ScaleAcceptance(basis="door"), "door", id="door"),
        pytest.param(ScaleAcceptance(basis="default"), "default", id="default"),
        pytest.param(ScaleAcceptance(basis=None), None, id="unknown"),
        pytest.param(ScaleAcceptance(basis="human"), None, id="human-not-machine"),
    ],
)
def test_scale_basis_of_accepted(snapshot: ScaleAcceptance, expected: str | None) -> None:
    """`scaleBasis` của tỉ lệ chấp nhận = gốc máy trong ảnh chụp, không phải `human`; gốc lạ → vắng."""
    assert scale_basis_of(_run(summary=_LATER_DETERMINED), "human", "human", snapshot) == expected


def test_scale_state_accepted_ignored_when_demoted() -> None:
    """Trang đổi (`effective` pipeline, `stored` human): `pageChanged` như cũ, cờ chấp nhận không đổi gì."""
    snapshot = ScaleAcceptance(basis="chain")
    assert scale_state_of("pipeline", "human", None, _LATER_DETERMINED, snapshot) == ("unresolved", ("pageChanged",))


_OPENING = UNREVIEWED.openings[0].id
_ROOM = UNREVIEWED.rooms[0].id
_NOTICES: Mapping[str, object] = {
    **CLEAN,
    "noticeChecks": [
        {"code": "ROOM_AREA_TEXT_MISMATCH", "entityIds": [_ROOM]},
        {"code": "OPENING_SIZE_IMPLAUSIBLE", "entityIds": [_OPENING, "O-GONE"]},
    ],
}


def test_readiness_notices__do_not_change_state_reasons_or_checks() -> None:
    """NO-488: nhắc mức nhẹ đi kèm `ready`; `state`/`reasons`/`checks` y như lượt không có nhắc; thứ tự cố định."""
    out = readiness_of(run=_run(summary=_NOTICES), scale_source="human", layer=UNREVIEWED)
    base = readiness_of(run=_run(), scale_source="human", layer=UNREVIEWED)
    assert (out.state, out.reasons, out.checks) == (base.state, base.reasons, base.checks) == ("ready", (), None)
    assert out.notices is not None
    assert [(n.code, n.entity_ids) for n in out.notices] == [
        ("OPENING_SIZE_IMPLAUSIBLE", (_OPENING,)),  # id đã xoá bị lọc
        ("ROOM_AREA_TEXT_MISMATCH", (_ROOM,)),
    ]
    assert base.notices is None


def test_readiness_notices__alongside_a_blocking_reason() -> None:
    """NO-488: tầng đang `needsReview` vì lý do khác vẫn mang nhắc."""
    out = readiness_of(run=_run(summary=_NOTICES), scale_source="pipeline", layer=UNREVIEWED)
    assert out.state == "needsReview"
    assert out.reasons == ("scaleUnresolved",)
    assert out.notices is not None
    assert len(out.notices) == 2


def test_readiness_notices__silenced_when_level_reviewed_or_section_only_or_no_run() -> None:
    """NO-488: tầng đã duyệt, lượt mặt cắt, chưa có lượt → không `notices` (cùng luật với `geometrySuspect`)."""
    assert readiness_of(run=_run(summary=_NOTICES), scale_source="human", layer=REVIEWED).notices is None
    section = {**_NOTICES, "sectionOnly": True}
    assert readiness_of(run=_run(summary=section), scale_source="human", layer=UNREVIEWED).notices is None
    assert readiness_of(run=None, scale_source="human", layer=UNREVIEWED).notices is None


def test_notices_of__drops_malformed_items_silently_and_never_raises_geometry_suspect() -> None:
    """NO-488: `noticeChecks` hỏng dạng → bỏ im; mục lành còn lại giữ; mục hết id bị bỏ, mục vốn rỗng id giữ."""
    summary = {
        "noticeChecks": [
            "x",
            {"code": "", "entityIds": []},
            {"code": 7, "entityIds": []},
            {"code": "A", "entityIds": "R-1"},
            {"code": "B", "entityIds": [1]},
            {"code": "C", "entityIds": ["GONE"]},
            {"code": "ROTATED_PLAN", "entityIds": []},
            {"code": "NEW_CODE", "entityIds": [_ROOM]},
            {"code": "NEW_CODE", "entityIds": []},
        ]
    }
    assert [(n.code, n.entity_ids) for n in notices_of(summary, UNREVIEWED)] == [
        ("ROTATED_PLAN", ()),
        ("NEW_CODE", (_ROOM,)),
    ]
    for bad in (None, "x", 3, {"code": "A"}):
        assert notices_of({"noticeChecks": bad}, UNREVIEWED) == ()
    assert notices_of({}, UNREVIEWED) == ()
    out = readiness_of(run=_run(summary={**CLEAN, "noticeChecks": "broken"}), scale_source="human", layer=UNREVIEWED)
    assert (out.state, out.reasons, out.notices) == ("ready", (), None)


# --------------------------------------------------------------------------- NHA-07b: mái máy dựng chưa duyệt (M-10)


def _roofed(layer: SpatialLayer, *, source: Literal["ai", "human"], reviewed: bool) -> SpatialLayer:
    """`layer` thêm một mái của nguồn `source`, đã duyệt hay chưa."""
    roof = Roof(
        id="RF-0000000000R1",
        level_id=LEVEL_ID,
        nodes=({"type": "roof"},),
        confidence=0.6,
        source=source,
        reviewed=reviewed,
    )
    return layer.model_copy(update={"roofs": (roof,)})


@pytest.mark.parametrize(
    ("layer", "run", "state", "reasons"),
    [
        pytest.param(
            _roofed(REVIEWED, source="ai", reviewed=False), _run(), "needsReview", ("roofUnreviewed",), id="ai"
        ),
        pytest.param(_roofed(REVIEWED, source="human", reviewed=True), _run(), "ready", (), id="approved"),
        pytest.param(_roofed(REVIEWED, source="human", reviewed=False), _run(), "ready", (), id="hand-drawn"),
        pytest.param(
            _roofed(REVIEWED, source="ai", reviewed=False), None, "notEvaluated", ("roofUnreviewed",), id="no-run"
        ),
    ],
)
def test_ai_roof_must_be_reviewed_before_the_floor_is_ready(
    layer: SpatialLayer, run: RunFacts | None, state: str, reasons: tuple[str, ...]
) -> None:
    """M-10: tầng có mái máy dựng chưa duyệt không `ready`; người duyệt (mái thành `human`, `reviewed`) thì `ready`.

    Mái người vẽ tay chưa duyệt không tính: luật chỉ chặn đầu ra của máy. Là dữ kiện của tầng nên báo cả khi chưa
    có lượt chạy, như `scaleUnresolved`.
    """
    out = readiness_of(run=run, scale_source="human", layer=layer)
    assert (out.state, out.reasons) == (state, reasons)


def test_one_ai_roof_among_reviewed_ones_is_enough() -> None:
    """Nhiều mái: chỉ cần một mái máy dựng chưa duyệt là tầng chưa `ready`."""
    approved = _roofed(REVIEWED, source="human", reviewed=True).roofs[0]
    machine = _roofed(REVIEWED, source="ai", reviewed=False).roofs[0].model_copy(update={"id": "RF-0000000000R2"})
    layer = REVIEWED.model_copy(update={"roofs": (approved, machine)})
    assert readiness_of(run=_run(), scale_source="human", layer=layer).reasons == ("roofUnreviewed",)


def test_section_only_run_stays_ready_even_with_an_ai_roof() -> None:
    """Dòng mặt cắt (`sectionOnly`) không có mặt bằng để dựng mái: vẫn `ready` như trước, luật M-10 không xét."""
    layer = _roofed(REVIEWED, source="ai", reviewed=False)
    out = readiness_of(run=_run(summary=SECTION_ONLY), scale_source="human", layer=layer)
    assert (out.state, out.reasons) == ("ready", ())


def test_roof_reason_comes_last_on_the_wire() -> None:
    """Lý do mới đứng sau mọi lý do cũ: thứ tự cũ của dây không đổi."""
    layer = _roofed(UNREVIEWED, source="ai", reviewed=False)
    out = readiness_of(run=_run(summary=CRITICAL, classic=MODEL_FAMILIES), scale_source="pipeline", layer=layer)
    assert out.reasons == (
        "scaleUnresolved",
        "criticalIssues",
        "fallbackWallModel",
        "fallbackObjectModel",
        "fallbackTextModel",
        "roofUnreviewed",
    )


def test_wire_enum_lists_exactly_the_reasons_the_rule_can_give() -> None:
    """Enum trên dây = đúng bảy lý do hàm sinh ra; thêm lý do mà quên một phía thì test này đỏ (FE giải enum chặt)."""
    assert sorted(get_args(ReadinessReason)) == sorted(
        [
            "scaleUnresolved",
            "criticalIssues",
            "geometrySuspect",
            "fallbackWallModel",
            "fallbackObjectModel",
            "fallbackTextModel",
            "roofUnreviewed",
        ]
    )
