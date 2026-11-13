"""Dữ liệu và fixture chung của test nướng ánh sáng."""

import struct
from collections.abc import Iterator
from typing import Final

import pytest

from packages.messaging.redis import SyncRedis, broker_redis_sync

BAKE_QUEUE: Final = "bake"
MISSING_PROJECT: Final = "prj_" + "0" * 26
MISSING_BAKE: Final = "bak_" + "0" * 26


def glb_bytes(extra: int = 64) -> bytes:
    """GLB tối thiểu mà `sniff` nhận: `glTF`, phiên bản 2, độ dài, rồi phần đệm."""
    body = b"\x00" * extra
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def bakes_path(project_id: str) -> str:
    """Đường N42."""
    return f"/api/projects/{project_id}/lighting-bakes"


def latest_path(project_id: str) -> str:
    """Đường N43."""
    return f"/api/projects/{project_id}/lighting-bakes/latest"


def availability_path(project_id: str) -> str:
    """Đường N49."""
    return f"/api/projects/{project_id}/lighting-bakes/availability"


def file_path(project_id: str, bake_id: str, name: str) -> str:
    """Đường N44."""
    return f"/api/projects/{project_id}/lighting-bakes/{bake_id}/files/{name}"


@pytest.fixture
def bake_broker(messaging_env: None) -> Iterator[SyncRedis]:
    """Broker thật với hàng `bake` rỗng ở đầu và cuối test."""
    client = broker_redis_sync()
    client.delete(BAKE_QUEUE)
    try:
        yield client
    finally:
        client.delete(BAKE_QUEUE)
        client.close()
