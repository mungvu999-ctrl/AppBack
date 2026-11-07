"""Model trên dây của bộ xuất GLB (camelCase qua `WireModel`)."""

from typing import Literal

from apps.api.core.wire import WireDatetime, WireModel

type ModelExportStatus = Literal["queued", "running", "done", "failed", "superseded"]
type ModelExportQuality = Literal["ready", "degraded", "blocked"]


class SnapshotRevision(WireModel):
    """Revision của một tầng trong snapshot."""

    level_id: str
    level_name: str
    revision: int


class ExportIssue(WireModel):
    """Thiếu sót gom theo (mức, lý do) từ `report_summary.records`."""

    severity: str
    reason: str
    count: int


class ModelExport(WireModel):
    """Một lượt xuất: N49 trả lượt mở/đã có, N50 trả lượt mới nhất không bị thay thế."""

    id: str
    status: ModelExportStatus
    quality: ModelExportQuality | None = None
    stale: bool
    fingerprint: str
    current_fingerprint: str
    snapshot_revisions: list[SnapshotRevision]
    counts: dict[str, int]
    issues: list[ExportIssue]
    created_at: WireDatetime
    started_at: WireDatetime | None = None
    ended_at: WireDatetime | None = None
    failure_code: str | None = None
    glb_bytes: int | None = None
