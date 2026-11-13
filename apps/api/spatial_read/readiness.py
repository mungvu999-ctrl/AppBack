"""`readiness` của N16: tầng "chạy xong" khác tầng "dùng được" (FIX-P0-10, thiết kế H5 §2.2).

Tính **lúc đọc**, không lưu: luật nằm một chỗ ở `readiness_of` (hàm thuần), dữ kiện của lượt
lấy bằng **một** truy vấn ở `latest_run_facts`. Lý do cấp lượt (`criticalIssues`,
`fallback*Model`) tắt bằng chính `level_reviewed` của `Level.reviewed` — không thêm định nghĩa
"đã kiểm" thứ hai. `scaleUnresolved` chỉ tắt khi tỉ lệ thật sự là `human` (đã chấm hạng theo
trang, cùng nguồn với `scaleStatus`) và là số đo thật — tỉ lệ `human` chấp nhận số máy (ML-ACC-R10 K4) giữ độ tin
của ảnh chụp căn cứ lúc chấp nhận (`scale_snapshot_of`), thêm mã `userAccepted`.

Module này nhập được trong ngữ cảnh worker: không `fastapi`, không `starlette`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast, get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.spatial_read.assemble import level_reviewed
from apps.api.spatial_read.documents import ScaleSource
from apps.api.spatial_read.wire import ReadinessOut, ReadinessReason, ScaleBasis, ScaleState
from packages.db.models.drawings import PipelineRunRow
from packages.db.models.pipeline_orchestrate import PipelineRunModelsRow
from packages.domain.spatial import ScaleAcceptance, SpatialLayer
from packages.ml_contracts.families import ModelFamily
from packages.ml_contracts.payloads import ModelRef

_FALLBACK_REASONS: Final[tuple[tuple[ModelFamily, ReadinessReason], ...]] = (
    ("wallSegmentation", "fallbackWallModel"),
    ("openingAndFurnitureDetection", "fallbackObjectModel"),
    ("dimensionReading", "fallbackTextModel"),
)
"""Họ model → lý do khi họ ấy chạy bằng ref cổ điển; thứ tự này là thứ tự trên dây."""

GEOMETRY_CHECK_ORDER: Final = (
    "OPEN_ENVELOPE",
    "WALL_FRAGMENTS",
    "MULTIPLE_PLANS",
    "ROOMS_WITHOUT_DOOR",
    "ROOM_USAGE_UNKNOWN",
    "ROTATED_PLAN",
    "AREA_TEXT_MISMATCH",
)
"""Thứ tự `readiness.checks` trên dây (BENCH3D-09); mã lạ đứng sau, giữ thứ tự của lượt."""

_MEASURED_BASES: Final = frozenset({"chain", "paired"})
"""Tỉ lệ đo từ nhiều số đo đồng thuận trên bản vẽ (chuỗi kích thước, cặp chữ-tường): không có nghi vấn thì không
báo `scaleUnresolved`. `note` (một chữ OCR `TL 1:N` chia khổ giấy, in co trang là lệch x2), `door`/`wallThickness`
/`default` (ước lượng) vẫn báo."""

_ESTIMATED_BASES: Final = frozenset({"note", "door", "wallThickness"})
"""Gốc ước lượng (cửa 745 mm, bề dày tường 150 mm, một chữ `TL 1:N`): tối đa `estimated`, không bao giờ `determined`."""

_PIPELINE_BASES: Final = frozenset(get_args(ScaleBasis)) - {"human"}


@dataclass(frozen=True, slots=True)
class RunFacts:
    """Dữ kiện của lượt `completed` mới nhất: `pinned` thô (JSONB) và tóm tắt quality (có thể `None`)."""

    pinned: Mapping[str, object]
    quality_summary: Mapping[str, object] | None


def _is_classic(value: object) -> bool:
    """Ref ghim là cổ điển (`pinned_name`, `weights_key` đều trống) — đọc qua `ModelRef` như `load_pins`."""
    return ModelRef.model_validate(value).is_classic


def _pipeline_basis(value: object) -> ScaleBasis | None:
    """Một gốc của pipeline (`chain`…`default`); mọi giá trị khác (`human`, gốc lạ, không phải chuỗi) → `None`."""
    return cast("ScaleBasis", value) if value in _PIPELINE_BASES else None


def _checks_of(summary: Mapping[str, object]) -> tuple[str, ...] | None:
    """`quality_summary.scaleChecks` thành bộ mã; vắng/`null` = `()`, hỏng dạng (không phải list chuỗi) = `None`."""
    raw = summary.get("scaleChecks")
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        return None
    return tuple(raw)


def scale_snapshot_of(stored: ScaleSource, summary: Mapping[str, object] | None) -> ScaleAcceptance:
    """Ảnh chụp căn cứ của máy khi người dùng chấp nhận số máy (ML-ACC-R10 K4), lấy **lúc ghi**.

    Tỉ lệ đang là `project_default` → gốc `default`; còn lại = gốc + `scaleChecks` của tóm tắt lượt mới nhất.
    Chưa có lượt, gốc lạ hay `scaleChecks` hỏng dạng → `basis=None` (đọc ra `unresolved`/`basisUnknown`, như #70).
    """
    if stored == "project_default":
        return ScaleAcceptance(basis="default")
    basis = None if summary is None else _pipeline_basis(summary.get("scaleBasis"))
    checks = None if summary is None else _checks_of(summary)
    if basis is None or checks is None:
        return ScaleAcceptance(basis=None)
    return ScaleAcceptance(basis=basis, checks=checks)


def scale_basis_of(
    run: RunFacts | None, stored: ScaleSource, effective: ScaleSource, accepted: ScaleAcceptance | None = None
) -> ScaleBasis | None:
    """Gốc tỉ lệ cho `scaleBasis`: `human` khi tỉ lệ người đo còn hiệu lực; tỉ lệ pipeline → gốc lượt mới nhất ghi.

    Tỉ lệ `human` chấp nhận số máy → gốc của ảnh chụp lúc chấp nhận (`None` thì vắng). Tỉ lệ người đặt bị hạ hạng
    (trang đổi, `stored` vẫn `human`) không phải do lượt nào đo → vắng.
    """
    if effective == "human":
        return "human" if accepted is None else _pipeline_basis(accepted.basis)
    if stored not in ("pipeline", "project_default") or run is None or run.quality_summary is None:
        return None
    return _pipeline_basis(run.quality_summary.get("scaleBasis"))


def scale_state_of(
    effective: ScaleSource,
    stored: ScaleSource,
    basis: ScaleBasis | None,
    summary: Mapping[str, object] | None,
    accepted: ScaleAcceptance | None = None,
) -> tuple[ScaleState | None, tuple[str, ...]]:
    """Độ tin của tỉ lệ (ML-ACC-R9): một luật cho `scaleState`/`scaleReasons` và cho `scaleUnresolved` của readiness.

    `None` khi tầng chưa có tỉ lệ. Lý do = gốc, rồi tên khoá `quality_summary.scaleChecks` theo thứ tự lượt ghi.
    `scaleChecks` hỏng dạng (không phải danh sách chuỗi: chuỗi, số, dict, list lẫn phần tử khác chuỗi) → `unresolved`
    với `basisUnknown`, không bao giờ `determined`/`estimated` (ML-ACC-R10); vắng khoá, `null`, `[]` = hợp lệ.
    Tỉ lệ `human` chấp nhận số máy (`accepted`, K4) → đúng độ tin của máy theo ảnh chụp lúc chấp nhận, thêm
    `userAccepted`; không bao giờ `userCalibrated`, không đọc lượt chạy sau đó (`summary` bỏ qua).
    """
    if effective == "human":
        if accepted is None:
            return "userCalibrated", ()
        machine = {"scaleChecks": list(accepted.checks)}
        state, reasons = scale_state_of("pipeline", "pipeline", _pipeline_basis(accepted.basis), machine)
        return state, (*reasons, "userAccepted")
    if effective == "none":
        return None, ()
    if effective == "project_default" or basis == "default":
        return "unresolved", ("projectDefault",)
    if stored == "human":
        return "unresolved", ("pageChanged",)
    if basis is None or basis == "human" or summary is None:
        return "unresolved", ("basisUnknown",)
    checks = _checks_of(summary)
    if checks is None:
        return "unresolved", ("basisUnknown",)
    reasons = (basis, *checks)
    if basis in _MEASURED_BASES:
        return ("unresolved" if checks else "determined"), reasons
    if basis in _ESTIMATED_BASES and "sourceConflict" not in checks:
        return "estimated", reasons
    return "unresolved", reasons


def _critical_checks(summary: Mapping[str, object]) -> tuple[str, ...]:
    """Mã kiểm hình học critical của tóm tắt, theo `GEOMETRY_CHECK_ORDER`; lượt trước BENCH3D-09 → rỗng."""
    codes = summary.get("criticalChecks")
    if not isinstance(codes, list):
        return ()
    known = [code for code in GEOMETRY_CHECK_ORDER if code in codes]
    return (*known, *(code for code in codes if isinstance(code, str) and code not in GEOMETRY_CHECK_ORDER))


def _integrity_critical(summary: Mapping[str, object], checks: tuple[str, ...]) -> bool:
    """Còn lỗi toàn vẹn critical: `criticalCount` đếm cả kiểm hình học, trừ chúng ra; lượt cũ → theo `hasCritical`."""
    if summary.get("hasCritical") is not True:
        return False
    count = summary.get("criticalCount")
    if "criticalChecks" not in summary or not isinstance(count, int):
        return True
    return count > len(checks)


def readiness_of(
    *,
    run: RunFacts | None,
    scale_source: ScaleSource,
    layer: SpatialLayer,
    scale_basis: ScaleBasis | None = None,
    scale_accepted: ScaleAcceptance | None = None,
) -> ReadinessOut:
    """Luật H5 §2.2: `notEvaluated` khi chưa có lượt; còn lý do → `needsReview`; hết → `ready`.

    `scaleUnresolved` là dữ kiện của tầng nên vẫn báo cả khi chưa có lượt — trừ tỉ lệ đo từ bản vẽ
    (`_MEASURED_BASES`) mà lượt không ghi nghi vấn `scaleChecks` (BENCH3D-09). Tóm tắt `NULL` (lượt
    cũ, trước FIX-P0-08) không sinh `criticalIssues`; họ vắng trong `pinned` không sinh lý do.
    Kiểm hình học critical → `geometrySuspect` kèm `checks`; `criticalIssues` chỉ còn cho lỗi toàn vẹn.
    `scale_accepted` (K4): tỉ lệ `human` chấp nhận số máy vẫn báo `scaleUnresolved` khi ảnh chụp không `determined`.
    """
    reasons: list[ReadinessReason] = []
    summary = None if run is None else run.quality_summary
    state, _ = scale_state_of(scale_source, scale_source, scale_basis, summary, scale_accepted)
    if state not in (None, "determined", "userCalibrated"):
        reasons.append("scaleUnresolved")
    checks: tuple[str, ...] = ()
    if run is not None and not level_reviewed(layer, scale_source):
        if summary is not None:
            checks = _critical_checks(summary)
            if _integrity_critical(summary, checks):
                reasons.append("criticalIssues")
            if checks:
                reasons.append("geometrySuspect")
        reasons.extend(
            reason for family, reason in _FALLBACK_REASONS if family in run.pinned and _is_classic(run.pinned[family])
        )
    if run is None:
        return ReadinessOut(state="notEvaluated", reasons=tuple(reasons))
    return ReadinessOut(state="needsReview" if reasons else "ready", reasons=tuple(reasons), checks=checks or None)


async def latest_run_facts(db: AsyncSession, floor_pk: int) -> RunFacts | None:
    """Lượt `completed` mới nhất chưa bị thay của tầng, join `pipeline_run_models`; chưa có → `None`.

    Join trái: lượt xong mà không có dòng ghim (dữ liệu trước B5-06a) vẫn là "đã có lượt", chỉ
    không có lý do cấp lượt nào.
    """
    stmt = (
        select(PipelineRunModelsRow.pinned, PipelineRunModelsRow.quality_summary)
        .select_from(PipelineRunRow)
        .outerjoin(PipelineRunModelsRow, PipelineRunModelsRow.run_id == PipelineRunRow.id)
        .where(
            PipelineRunRow.floor_pk == floor_pk,
            PipelineRunRow.status == "completed",
            PipelineRunRow.superseded_by.is_(None),
        )
        .order_by(PipelineRunRow.created_at.desc(), PipelineRunRow.id.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        return None
    pinned, quality_summary = row
    return RunFacts(pinned=pinned or {}, quality_summary=quality_summary)
