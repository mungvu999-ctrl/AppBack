"""N61 — "Bỏ bộ này": xoá mềm một bộ hồ sơ (K-32a §3).

Chỉ đặt `deleted_at`: tầng, lượt tải và lượt chạy đã tạo từ bộ đã xác nhận giữ nguyên (quan hệ một chiều từ bộ
xuống). Bộ đang `analyzing` thì tác vụ phân tích thấy `deleted_at` ở chặng cuối và dừng. Object trong kho của bộ ở lại
tới lịch dọn dự án (`projects/{p}/` phủ `drawing-sets/`). Chạy cả khi công tắc tắt (dọn dữ liệu đã có, L4-N04).
Gọi lại sau khi đã xoá → 404 (C08); thử lại an toàn đi qua `Idempotency-Key`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.access.activity import record_activity
from apps.api.access.kinds import ActivityKind
from apps.api.core.auth import Principal
from apps.api.drawing_sets.set_read import load_set, set_wire
from apps.api.drawing_sets.set_schemas import DrawingSetOut
from packages.core.clock import Clock
from packages.storage.port import ObjectStorage


async def delete_set(
    db: AsyncSession, storage: ObjectStorage, *, project_id: str, set_id: str, principal: Principal, clock: Clock
) -> DrawingSetOut:
    """Xoá mềm dưới khoá dòng bộ, ghi nhật ký; trả ảnh chụp bộ **ngay trước** khi ẩn (mẫu #11)."""
    drawing_set = await load_set(db, project_id=project_id, set_id=set_id, for_update=True)
    snapshot = await set_wire(db, storage, drawing_set)
    drawing_set.deleted_at = clock.now()
    await record_activity(
        db,
        actor_id=principal.user_id,
        kind=ActivityKind.DRAWING_SET_DELETE,
        object_code=set_id,
        object_label=drawing_set.name or set_id,
        clock=clock,
        project_id=project_id,
    )
    await db.flush()
    return snapshot
