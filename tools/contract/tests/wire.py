"""Thân dây mẫu, hợp lệ theo schema FE ở SHA ghim, để test dựng biến thể hỏng từ đó.

Chỉ dữ liệu, không schema: mọi phép kiểm đi qua runner Node thật và zod thật (K23).
JWT dựng lúc chạy (gitleaks của CI bắt literal `eyJ…`, B0-07 [9]).
"""

import base64
import json
from typing import Any, Final

ULID: Final = "01HZX3K5N8Q9R2S4T6V7W8X9YZ"
USER_ID: Final = f"usr_{ULID}"
PROJECT_ID: Final = f"prj_{ULID}"
VERSION_ID: Final = f"ver_{ULID}"
UPLOAD_ID: Final = f"upl_{ULID}"
AT: Final = "2026-01-01T00:00:00.000Z"
LEVEL_ID: Final = "L-ABCDEFGHIJ"
LEVEL_2_ID: Final = "L-TANGHAI000"
FAMILIES: Final = ("wallSegmentation", "openingAndFurnitureDetection", "dimensionReading")


def _b64(data: dict[str, Any]) -> str:
    """base64url không đệm của một JSON."""
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def fake_jwt() -> str:
    """JWT ba đoạn dựng lúc chạy; chữ ký giả (không ai kiểm)."""
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64({'sub': USER_ID})}.chu-ky-gia"


def review(**fields: Any) -> dict[str, Any]:
    """Thực thể không gian có đủ trường duyệt (HOP-DONG-MOI §4.1)."""
    return {"confidence": 1, "reviewed": False, "source": "human", **fields}


def project(**overrides: Any) -> dict[str, Any]:
    """`ProjectSchema` hợp lệ (`src/api/schemas/index.ts:212`)."""
    body = {"createdAt": AT, "floors": [], "id": PROJECT_ID, "members": [], "name": "Nhà A"}
    return {**body, "status": "draft", "updatedAt": AT, **overrides}


def refresh(**overrides: Any) -> dict[str, Any]:
    """Thân W16 hợp lệ theo `strict/refresh.ts`."""
    user = {"email": "an@example.com", "id": USER_ID, "name": "An"}
    return {"accessToken": fake_jwt(), "expiresAt": AT, "roles": ["engineer"], "user": user, **overrides}


def house_floor(**overrides: Any) -> dict[str, Any]:
    """Một dòng `HouseProposalStrictSchema.floors[]` hợp lệ (`strict/houseProposal.ts`)."""
    body = {"floorId": LEVEL_ID, "kind": "plan", "kindConfidence": 0.9, "storeyLabel": "ground", "storeyNo": 1}
    return {**body, "storeyConfidence": 0.95, "elevationMm": 0, "elevationSource": "plan", "questions": [], **overrides}


def house_proposal(*floors: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Thân N52 bật công tắc, mặc định một tầng (`house_floor()`)."""
    rows = list(floors) if floors else [house_floor()]
    return {"enabled": True, "floors": rows, "runIds": [f"run_{ULID}"], **overrides}


def conflict(**overrides: Any) -> dict[str, Any]:
    """409 `VERSION_CONFLICT` hợp lệ với một `remoteChanges` (W20)."""
    change = {
        "changedAt": AT,
        "changedBy": USER_ID,
        "changedByName": "An",
        "entityId": "W-ABCDEFGHIJ",
        "entityType": "wall",
        "field": "thickness_mm",
        "value": 200,
    }
    return {
        "code": "VERSION_CONFLICT",
        "currentVersion": 3,
        "remoteChanges": [change],
        "requestId": "rid-1",
        **overrides,
    }


def progress(status: str, **extra: Any) -> dict[str, Any]:
    """`ProgressSchema` của một upload."""
    return {"id": UPLOAD_ID, "progressPercent": 0, "status": status, "step": "preprocess", **extra}


def level(level_id: str = LEVEL_ID, order: int = 0) -> dict[str, Any]:
    """`LevelSchema` hợp lệ."""
    return review(elevationMm=0, heightMm=3000, id=level_id, name="Tầng", order=order)


def wall(level_id: str = LEVEL_ID) -> dict[str, Any]:
    """`WallSchema` hợp lệ trên tầng `level_id`."""
    centreline = {"end": {"x": 1000, "y": 0}, "start": {"x": 0, "y": 0}}
    return review(
        centreline=centreline,
        heightMm=3000,
        id="W-ABCDEFGHIJ",
        kind="partition",
        levelId=level_id,
        openingIds=[],
        thicknessMm=100,
    )


def layer_document(wall_level: str = LEVEL_ID) -> dict[str, Any]:
    """`FloorLayerDocumentSchema` (N16) của tầng `LEVEL_ID`."""
    layer = {"furniture": [], "openings": [], "rooms": [], "walls": [wall(wall_level)]}
    return {"axes": [], "dimensions": [], "layer": layer, "level": level(), "revision": 1}


def graph_document(levels: list[dict[str, Any]], wall_level: str = LEVEL_ID) -> dict[str, Any]:
    """`SpatialGraphDocumentSchema` (N15) với `levels` cho trước."""
    graph = {
        "axes": [],
        "building": review(datumElevationMm=0, name="Nhà A"),
        "dimensions": [],
        "furniture": [],
        "levels": levels,
        "notes": [],
        "openings": [],
        "rooms": [],
        "walls": [wall(wall_level)],
    }
    revisions = [{"floorId": item["id"], "revision": 0} for item in levels]
    return {"floorRevisions": revisions, "graph": graph}


def page(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Một trang `CursorPageSchema` không có trang sau."""
    return {"items": items}


def version(sequence: int) -> dict[str, Any]:
    """`FloorVersionSummarySchema` (N17)."""
    return {
        "createdAt": AT,
        "creatorId": USER_ID,
        "creatorName": "An",
        "floorRevision": 1,
        "hasSnapshot": True,
        "id": VERSION_ID,
        "sequence": sequence,
    }


def summary(area: float) -> dict[str, Any]:
    """`ProjectSummarySchema` (N1) của dự án 0 tầng."""
    counts = {"floorCount": 0, "wallsReviewedCount": 0, "wallsTotalCount": 0}
    return {
        "areaM2": area,
        "id": PROJECT_ID,
        "members": [],
        "name": "Nhà A",
        "status": "processing",
        "updatedAt": AT,
        **counts,
    }


def latest_upload(floor_id: str) -> dict[str, Any]:
    """`LatestFloorUploadSchema` (N7)."""
    return {"floorId": floor_id, "floorName": "Tầng", "uploadId": UPLOAD_ID}
