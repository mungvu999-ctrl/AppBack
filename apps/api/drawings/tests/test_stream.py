"""Luồng S1 `upload_progress` với nhà cung cấp **thật** của `apps.api.drawings` (cổng case).

B4-01 đã kiểm trung tâm SSE bằng provider giả; ở đây chỉ còn hai câu hỏi của B2-04: người
ngoài dự án có mở được luồng của một lượt tải không (S06), và khung đầu có đúng `Progress`
hiện tại không (S08). `stream_app(providers=[...])` nhận provider thật nên `lifespan` không
rơi về `DenyUploads`.
"""

import json
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.stream_providers import PROVIDERS
from apps.api.drawings.tests._helpers import make_scene
from apps.api.drawings.tests._upload_helpers import stream_path
from packages.testing.factories.drawings import make_upload
from packages.testing.factories.floors import make_floor
from packages.testing.factories.projects import make_project
from packages.testing.fixtures.streams import (
    SseOpen,
    StreamAppFactory,
    record_stream_frames,
    signed_stream_user,
)

OP: Final = "streams_open_progress"
FAST: Final = {"stream_heartbeat_s": "0.2", "stream_recheck_s": "0.2", "stream_read_block_ms": "100"}
WAIT_S: Final = 3.0


async def test_streams_open_progress__S06_drawings(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Người ngoài dự án mở luồng tiến độ → 404 `resource:"upload"`, không 403."""
    scene = await make_scene(db_session)
    upload = await make_upload(db_session, project=scene.project, floor=scene.floor)
    await db_session.commit()
    app = stream_app(providers=list(PROVIDERS), **FAST)

    async with (
        signed_stream_user(app, db_session) as outsider,
        sse_open(app, stream_path(scene.project.id, upload.id), cookies=outsider.cookies) as stream,
    ):
        assert stream.status == 404
        body = json.loads(stream.body)

    assert body["code"] == "NOT_FOUND"
    assert body["resource"] == "upload"


async def test_streams_open_progress__S08_drawings(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Mở mới → khung đầu là ảnh chụp `Progress` hiện tại của lượt tải (`progress_wire`)."""
    app = stream_app(providers=list(PROVIDERS), **FAST)

    async with signed_stream_user(app, db_session) as member:
        project = await make_project(db_session, owner=member.user)
        floor = await make_floor(db_session, project=project)
        upload = await make_upload(db_session, project=project, floor=floor)
        await db_session.commit()

        async with sse_open(app, stream_path(project.id, upload.id), cookies=member.cookies) as stream:
            frames = await stream.next_frames(1, WAIT_S)
            record_stream_frames(OP, "S08_drawings", frames)

    assert json.loads(frames[0].data) == {
        "id": upload.id,
        "status": "pending",
        "step": "preprocess",
        "progressPercent": 0,
    }


async def test_streams_open_progress__S06_drawings_other_project(
    stream_app: StreamAppFactory, sse_open: SseOpen, db_session: AsyncSession
) -> None:
    """Thành viên mở luồng của lượt tải thuộc **dự án khác** qua đường dự án mình → 404 ([7])."""
    app = stream_app(providers=list(PROVIDERS), **FAST)

    async with signed_stream_user(app, db_session) as member:
        mine = await make_project(db_session, owner=member.user)
        scene = await make_scene(db_session)
        upload = await make_upload(db_session, project=scene.project, floor=scene.floor)
        await db_session.commit()

        async with sse_open(app, stream_path(mine.id, upload.id), cookies=member.cookies) as stream:
            assert stream.status == 404
            body = json.loads(stream.body)

    assert body["resource"] == "upload"
