"""Fixture lượt xuất GLB đang xếp hàng: dự án thật, dòng `model_exports`, `snapshot.json` trong kho (M1-01)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.messaging.payloads.model_export import MODEL_EXPORT_SNAPSHOT
from packages.storage.keys import model_export_object
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.model_export import make_model_export
from packages.testing.factories.projects import make_project

EMPTY_SNAPSHOT = b'{"floors":[],"projectId":"","schemaVersion":1,"versions":{}}'


@dataclass(frozen=True, slots=True)
class QueuedExport:
    """Một lượt `queued` đã có `snapshot.json` trong kho."""

    project_id: str
    export_id: str


QueueExport = Callable[..., Awaitable[QueuedExport]]


@pytest.fixture
def queue_model_export(
    db_sessionmaker: async_sessionmaker[AsyncSession], local_storage: LocalDiskStorage
) -> QueueExport:
    """Hàm dựng lượt `queued`; truyền `project_id` để thêm lượt vào dự án sẵn có."""

    async def factory(*, project_id: str | None = None, status: str = "queued") -> QueuedExport:
        """Dự án (mới nếu không truyền) + dòng `model_exports` + `snapshot.json`."""
        async with db_sessionmaker() as db:
            if project_id is None:
                owner = await make_user(db)
                project = await make_project(db, owner=owner)
                await db.commit()
                project_id = project.id
            row = await make_model_export(db, project_id=project_id, status=status)
        key = model_export_object(project_id, row.id, MODEL_EXPORT_SNAPSHOT)
        await local_storage.put(key, EMPTY_SNAPSHOT, content_type="application/json", max_bytes=len(EMPTY_SNAPSHOT) + 1)
        return QueuedExport(project_id=project_id, export_id=row.id)

    return factory
