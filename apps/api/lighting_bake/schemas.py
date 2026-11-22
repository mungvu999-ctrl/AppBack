"""Model trên dây của nướng ánh sáng (HOP-DONG-MOI, camelCase qua `WireModel`)."""

from typing import Literal

from apps.api.core.wire import WireDatetime, WireModel

type LightingBakeStatus = Literal["queued", "running", "done", "failed"]


class LightingBake(WireModel):
    """Một lượt nướng: N42 trả lượt vừa mở, N43 trả lượt mới nhất của dự án."""

    id: str
    status: LightingBakeStatus
    created_at: WireDatetime
    started_at: WireDatetime | None = None
    ended_at: WireDatetime | None = None
    failure_code: str | None = None


class LightingBakeAvailability(WireModel):
    """N49 — dự án này có nướng được không (mẫu `DesignConceptState.enabled`)."""

    enabled: bool
