"""Huỷ lượt xử lý đang chạy của một lượt tải (K-24, gồm L1-N04; phương án B: dừng hẳn phần AI).

Hai nửa, một nguồn sự thật:

- **DB** (`runs.cancel_upload_runs`, dưới khoá `floors` → `pipeline_runs`): lượt `pending|running`
  thành `failed` `PIPELINE_CANCELLED`. Mọi task pipeline (`start`, `step_done`, dựng, ghi lớp,
  kiểm chất lượng) mở bằng `lock_run` nên dừng ở ranh giới kế tiếp, và kết quả muộn bị bỏ.
- **Worker `ml`** không có DB: sau commit API cắm cờ Redis (`packages.messaging.run_cancel`), bước
  ML dừng ở điểm kiểm gần nhất (trước nạp model, trước đọc trang, giữa từng ô ảnh, trước ghi),
  task còn xếp hàng thoát ngay khi tới lượt — giải phóng tiến trình ML.

Idempotent theo nghĩa kết quả: sau lời gọi, lượt tải không còn lượt chạy sống. Bấm đôi, huỷ lượt
đã xong hay đã huỷ đều 200 với `Progress` hiện tại (lượt đã `completed` giữ nguyên — kết quả không
bị bỏ chỉ vì người dùng bấm chậm). Không ghi nhật ký hoạt động (cột Nhật ký "—", như N51).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.progress import progress_wire
from apps.api.drawings.runs import cancel_upload_runs
from apps.api.drawings.uploads import load_upload
from packages.core.clock import Clock


async def cancel_upload(db: AsyncSession, *, project_id: str, upload_id: str, clock: Clock) -> dict[str, object]:
    """Huỷ lượt chạy sống của lượt tải; trả `Progress` sau huỷ. 404 `upload` khi không thuộc dự án/tầng đã xoá."""
    facts = await load_upload(db, project_id=project_id, upload_id=upload_id)
    await cancel_upload_runs(db, upload_id=facts.id, clock=clock)
    return await progress_wire(db, facts.id)
