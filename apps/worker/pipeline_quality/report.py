"""Báo cáo chất lượng thuần của một lượt (B5-07 [2], [6]): toàn vẹn + đếm mục AI tin cậy thấp.

Không chạm DB hay kho: `service` đọc lớp dưới session rồi gọi `build_report` ngoài mọi session.
`to_json_bytes` tất định (cùng tài liệu → cùng byte) để giao lặp ghi đè `quality.json` y hệt.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from apps.worker.pipeline_quality.checks import GeometryCheck, geometry_checks
from packages.domain.spatial.integrity import IntegrityIssue, check_integrity, has_critical
from packages.domain.spatial.model import Reviewed, SpatialLayer

QUALITY_REPORT_MAX_BYTES: Final = 8 * 1024 * 1024
"""Trần `quality.json` khi `put` (B5-07 [5]): 8 MiB."""

QUALITY_ARTIFACT: Final = "quality.json"
"""Tên artifact ghi dưới `run_prefix(…, "qualityCheck")` (B5-07, luật chung)."""

PAGE_RASTER_UNAVAILABLE: Final = "PAGE_RASTER_UNAVAILABLE"
"""L2-N02: bước dựng không đọc được raster trang (`BuiltLayer.page_unavailable`) nên chạy chế độ suy giảm (cánh cửa
không đọc, tường ký hiệu cửa không bỏ). Critical: lớp có thể sai mà nhìn không ra, tầng phải về "Cần xem lại"."""

_LOW_CONFIDENCE_LISTS: Final = ("walls", "openings", "rooms", "furniture")
"""Bốn danh sách đếm tin cậy thấp, theo đúng thứ tự khoá của `lowConfidence`."""


@dataclass(frozen=True)
class RunQualityReport:
    """Kết quả kiểm một lượt; `low_confidence` có đúng bốn khoá walls, openings, rooms, furniture."""

    run_id: str
    revision: int
    confidence_threshold: float
    issues: tuple[IntegrityIssue, ...]
    has_critical: bool
    low_confidence: Mapping[str, int]
    checks: tuple[GeometryCheck, ...] = ()
    """Kiểm hình học đã bật (BENCH3D-01); mục `critical` cũng bật `has_critical`."""

    def to_json_bytes(self) -> bytes:
        """`quality.json` theo B5-07 [2]: khoá cố định, `refId` vắng khi `None`, byte tất định."""
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "runId": self.run_id,
            "revision": self.revision,
            "confidenceThreshold": self.confidence_threshold,
            "hasCritical": self.has_critical,
            "integrity": [_issue_wire(issue) for issue in self.issues],
            "lowConfidence": dict(self.low_confidence),
            "checks": [check.to_wire() for check in self.checks],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _issue_wire(issue: IntegrityIssue) -> dict[str, object]:
    """Một lỗi toàn vẹn ra dây; `refId` vắng hẳn khoá khi `None` (W2)."""
    wire: dict[str, object] = {"rule": issue.rule, "severity": issue.severity, "entityId": issue.entity_id}
    if issue.ref_id is not None:
        wire["refId"] = issue.ref_id
    return wire


def _low_confidence_count(entities: Iterable[Reviewed], threshold: float) -> int:
    """Số mục AI chưa duyệt có `confidence < threshold` (ngặt) trong một danh sách.

    Mọi thực thể của `SpatialLayer` có `confidence` bắt buộc (không `None` được, W1) —
    "mục không có `confidence`" của prompt không xảy ra với mô hình hiện tại; ghi "Lệch
    khỏi prompt" trong báo cáo.
    """
    return sum(
        1 for entity in entities if entity.source == "ai" and entity.reviewed is False and entity.confidence < threshold
    )


def build_report(
    *,
    run_id: str,
    revision: int,
    layer: SpatialLayer,
    level_id: str,
    threshold: Decimal,
    scale_basis: str | None = None,
    label_orientation: Mapping[str, int] | None = None,
    area_notes: Sequence[float] = (),
    page_unavailable: bool = False,
) -> RunQualityReport:
    """Dựng báo cáo theo B5-07 [6]: toàn vẹn (`check_integrity`) + đếm tin cậy thấp bốn danh sách + kiểm hình học
    (`scale_basis`, `label_orientation` của `layer.json`, BENCH3D-14; `area_notes`, BENCH3D-24; `page_unavailable`
    → `PAGE_RASTER_UNAVAILABLE` đứng cuối, L2-N02)."""
    issues = tuple(check_integrity(layer, level_id=level_id))
    checks = geometry_checks(layer, scale_basis=scale_basis, label_orientation=label_orientation, area_notes=area_notes)
    if page_unavailable:
        checks = (*checks, GeometryCheck(code=PAGE_RASTER_UNAVAILABLE, critical=True, detail={}))
    threshold_f = float(threshold)
    low_confidence = {name: _low_confidence_count(getattr(layer, name), threshold_f) for name in _LOW_CONFIDENCE_LISTS}
    return RunQualityReport(
        run_id=run_id,
        revision=revision,
        confidence_threshold=threshold_f,
        issues=issues,
        has_critical=has_critical(issues) or any(check.critical for check in checks),
        low_confidence=low_confidence,
        checks=checks,
    )
