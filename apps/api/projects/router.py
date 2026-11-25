"""Sáu route của module dự án: #23-#27 và N1 (B2-01 [2], [7]).

Router **mỏng**: mỗi hàm chỉ đọc dependency rồi gọi đúng một hàm của `service.py`. Ba điều
khai ở đây mà không ở chỗ khác được:

- **cổng quyền** (`lock` ở `cases.toml`): #23 và N1 chỉ đòi đăng nhập (`—`), #24 đòi là thành
  viên, #25 đòi khoá hệ thống `project.create`, #26 đòi `project.settings.edit` **trong
  dự án**, #27 đòi là thành viên **và** vai `admin` (BUG-513: FE chỉ mở vùng nguy hiểm cho
  admin). Cổng đặt ở `dependencies=` chứ không ở tham số handler: giá trị trả về của chúng
  không cần cho nghiệp vụ (id dự án đã có trên đường), và test case chung C10/C22 của B0-06
  ghi đè mọi cổng quyền bằng `lambda: None` — handler nào đọc kết quả cổng sẽ vỡ ở đó;
- **trần của N1 là hằng** `page_params(500)`, không phải setting: zod của FE ghim 500
  (HOP-DONG-MOI §0.2) và một biến môi trường đổi được sẽ làm dây lệch hợp đồng;
- **`request.app`** đi thẳng xuống `service`: cổng mở rộng (`project.floors`,
  `project.create_floors`) phải resolve theo **app của request**, để `extensions.override`
  của test có hiệu lực đúng như lúc chạy thật.
"""

from typing import Annotated, Final

from fastapi import Depends
from starlette.requests import Request

from apps.api.access.deps import require_permission
from apps.api.core.deps import ClockDep, CurrentPrincipal, DbSession
from apps.api.core.pagination import CursorPage, PageParams, page_params
from apps.api.core.routing import protected_router
from apps.api.projects import service
from apps.api.projects.access import require_project
from apps.api.projects.schemas import ProjectCreateIn, ProjectUpdateIn
from apps.api.projects.wire import ProjectOut, ProjectSummaryOut

SUMMARIES_MAX_LIMIT: Final = 500
"""Trần `limit` của N1 (HOP-DONG-MOI §0.2); danh sách cũ #23 dùng `PROJECTS_LIST_MAX`."""

router = protected_router(tags=["projects"])
ROUTERS: Final = (router,)

SummariesPage = Annotated[PageParams, Depends(page_params(SUMMARIES_MAX_LIMIT))]
"""`cursor` + `limit` đã kiểm biên của N1."""


@router.get("/projects")
async def projects_list_projects(request: Request, db: DbSession, principal: CurrentPrincipal) -> list[ProjectOut]:
    """#23 — danh sách cũ: mọi dự án mình là thành viên, mới sửa trước, tối đa `PROJECTS_LIST_MAX`."""
    return await service.list_projects(db, principal, app=request.app)


@router.get("/projects/{project_id}", dependencies=[Depends(require_project())])
async def projects_read_project(project_id: str, request: Request, db: DbSession) -> ProjectOut:
    """#24 — một dự án; người không phải thành viên nhận 404, kể cả admin hệ thống (K08)."""
    return await service.read_project(db, project_id, app=request.app)


@router.post("/projects", status_code=201, dependencies=[Depends(require_permission("project.create"))])
async def projects_create_project(
    body: ProjectCreateIn,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
    clock: ClockDep,
) -> ProjectOut:
    """#25 — tạo dự án (201); người tạo thành thành viên ngay, `floors` cần cổng của B2-03."""
    return await service.create_project(db, body, principal, clock, app=request.app)


@router.patch("/projects/{project_id}", dependencies=[Depends(require_project("project.settings.edit"))])
async def projects_update_project(
    project_id: str,
    body: ProjectUpdateIn,
    request: Request,
    db: DbSession,
    principal: CurrentPrincipal,
    clock: ClockDep,
) -> ProjectOut:
    """#26 — sửa dự án (last-write-wins); thân `{}` là 200 không đổi gì, không nhật ký."""
    return await service.update_project(db, project_id, body, principal, clock, app=request.app)


@router.delete("/projects/{project_id}", dependencies=[Depends(require_project("admin"))])
async def projects_delete_project(
    project_id: str, request: Request, db: DbSession, principal: CurrentPrincipal, clock: ClockDep
) -> ProjectOut:
    """#27 — xoá mềm; thân là trạng thái ngay trước khi xoá, membership và bảng đếm giữ lại."""
    return await service.delete_project(db, project_id, principal, clock, app=request.app)


@router.delete("/projects/{project_id}/creation", dependencies=[Depends(require_project())])
async def projects_cancel_create_project(
    project_id: str, request: Request, db: DbSession, principal: CurrentPrincipal, clock: ClockDep
) -> ProjectOut:
    """Huỷ tạo — Hoàn tác của toast "Đã tạo": người tạo, ≤10 phút, chưa có thành viên/hoạt động khác."""
    return await service.cancel_create_project(db, project_id, principal, clock, app=request.app)


@router.get("/project-summaries")
async def projects_list_summaries(
    page: SummariesPage, db: DbSession, principal: CurrentPrincipal
) -> CursorPage[ProjectSummaryOut]:
    """N1 — danh sách mới: một trang tóm tắt dự án của mình, `id ASC`, cursor ký theo người gọi."""
    return await service.list_summaries(db, principal, page)
