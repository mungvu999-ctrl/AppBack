"""Route đọc đề xuất tầng, cốt, chiều cao cho cả nhà (HS-02): `GET /projects/{project_id}/house-proposal`.

Router mỏng, nghiệp vụ ở `service.py` (miền thuần ở `packages.domain.drawing_set`). `discover_routers()` tự nạp
`ROUTERS`. Chỉ đọc: backend không bao giờ tự ghi vào tầng, "Nhận" của FE đi qua đường PATCH sẵn có.

`require_project()` ở `dependencies=` chạy **trước** nhánh công tắc: người ngoài dự án nhận 404 dù công tắc
tắt (K08). Công tắc tắt → 200 `{"enabled": false, "floors": [], "runIds": []}` (mẫu N47 của design_director),
không 503: FE tự thử lại 503 của GET (W9), mà "tắt" là trạng thái thường, không phải lỗi.
"""

from typing import Annotated, Final

from fastapi import Depends

from apps.api.core.deps import DbSession, Storage
from apps.api.core.routing import protected_router
from apps.api.drawing_sets.schemas import DISABLED, HouseProposalOut, house_proposal_out
from apps.api.drawing_sets.service import read_house_proposal
from apps.api.drawing_sets.settings import DrawingSetSettings, get_drawing_set_settings
from apps.api.projects.access import require_project

router = protected_router(tags=["drawing_sets"])
ROUTERS: Final = (router,)

_member_gate: Final = Depends(require_project())
DrawingSetConfig = Annotated[DrawingSetSettings, Depends(get_drawing_set_settings)]


@router.get("/projects/{project_id}/house-proposal", dependencies=[_member_gate])
async def drawing_sets_read_house_proposal(
    project_id: str, db: DbSession, storage: Storage, settings: DrawingSetConfig
) -> HouseProposalOut:
    """Đề xuất cho cả nhà tính ngay lúc đọc; công tắc tắt → `enabled: false`, rỗng (service không được gọi)."""
    if not settings.drawing_set_enabled:
        return DISABLED
    read = await read_house_proposal(db, storage, project_id)
    return house_proposal_out(read.proposal, read.run_ids, read.section_ids)
