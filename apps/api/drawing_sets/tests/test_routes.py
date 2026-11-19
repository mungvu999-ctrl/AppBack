"""`GET /api/projects/{project_id}/house-proposal` (HS-02, Loại Đ: C01 C06) và ánh xạ miền → dây.

Postgres thật cho thành viên/dự án (K23); `service.read_house_proposal` được thay bằng bản giả ở những test
kiểm nhánh công tắc và hình dạng dây (R-29), còn `__real_service` chạy cả service trên dự án thật.
"""

from collections.abc import Iterator
from typing import Any, Final, get_args

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawing_sets import router as router_module
from apps.api.drawing_sets.schemas import FloorKind, LevelSource, QuestionCode, StoreyLabel, house_proposal_out
from apps.api.drawing_sets.service import HouseProposalRead
from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.projects.tests.test_routes_common import headers_of
from apps.api.spatial_read.tests._helpers import make_scene
from packages.domain.drawing_set import FloorProposal, HouseProposal, heights, storeys, titles
from packages.domain.drawing_set import proposal as domain
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user
from packages.testing.factories.projects import make_project

MISSING_PROJECT: Final = "prj_" + "0" * 26
ALL_CODES: Final = get_args(QuestionCode.__value__)


@pytest.fixture
def drawing_set_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`DRAWING_SET_ENABLED=true` cho một test, cache cấu hình đọc lại đầu và cuối."""
    monkeypatch.setenv("DRAWING_SET_ENABLED", "true")
    reset_drawing_set_settings_cache()
    yield
    reset_drawing_set_settings_cache()


@pytest.fixture
def drawing_set_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Biến vắng = mặc định tắt."""
    monkeypatch.delenv("DRAWING_SET_ENABLED", raising=False)
    reset_drawing_set_settings_cache()
    yield
    reset_drawing_set_settings_cache()


class _Spy:
    """Thay `read_house_proposal`: ghi lại lời gọi, trả kết quả dựng sẵn."""

    def __init__(self, result: HouseProposalRead) -> None:
        self.result = result
        self.calls: list[str] = []

    async def __call__(self, session: AsyncSession, storage: object, project_id: str) -> HouseProposalRead:
        """Ghi `project_id` rồi trả kết quả giả."""
        self.calls.append(project_id)
        return self.result


def _stub(monkeypatch: pytest.MonkeyPatch, result: HouseProposalRead) -> _Spy:
    """Cắm bản giả vào đúng tên router đã nhập."""
    spy = _Spy(result)
    monkeypatch.setattr(router_module, "read_house_proposal", spy)
    return spy


def _path(project_id: str) -> str:
    """Đường của route."""
    return f"/api/projects/{project_id}/house-proposal"


def _read(
    *floors: FloorProposal, run_ids: tuple[str, ...] = (), differs: frozenset[str] = frozenset()
) -> HouseProposalRead:
    """Kết quả service giả."""
    return HouseProposalRead(proposal=HouseProposal(tuple(floors)), run_ids=run_ids, order_differs=differs)


async def test_drawing_sets_read_house_proposal__disabled(
    api_client: httpx.AsyncClient, db_session: AsyncSession, drawing_set_off: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Công tắc tắt → 503 `DRAWING_SET_DISABLED` (khoá gốc `code`, có `Retry-After`) và service không được gọi."""
    scene = await make_scene(db_session)
    spy = _stub(monkeypatch, _read())

    response = await api_client.get(_path(scene.project.id), headers=headers_of(scene.owner))

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "DRAWING_SET_DISABLED"
    assert set(body) == {"code", "requestId"}
    assert response.headers["Retry-After"] == "10"
    assert spy.calls == []


async def test_drawing_sets_read_house_proposal__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, drawing_set_off: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Người ngoài (kể cả admin hệ thống) hay dự án không có → 404 `resource:"project"`, trước công tắc (K08)."""
    scene = await make_scene(db_session)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    spy = _stub(monkeypatch, _read())

    response = await api_client.get(_path(scene.project.id), headers=headers_of(outsider))
    missing = await api_client.get(_path(MISSING_PROJECT), headers=headers_of(scene.owner))

    assert (response.status_code, response.json()["resource"]) == (404, "project")
    assert (missing.status_code, missing.json()["resource"]) == (404, "project")
    assert spy.calls == []


async def test_drawing_sets_read_house_proposal__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, drawing_set_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 đúng hình dạng dây: trường tuỳ chọn vắng (không `null`), `ORDER_DIFFERS` thêm cuối cho tầng lệch."""
    scene = await make_scene(db_session)
    full = FloorProposal(
        "L-A", "plan", 0.92, "ground", 1, 0.95, 0, 0.9, "plan", 3300, 0.8, "convention", ("HEIGHT_ASSUMED",)
    )
    bare = FloorProposal("L-B", "other", 0.0, questions=("NO_TEXT",))
    spy = _stub(monkeypatch, _read(full, bare, run_ids=("run_1",), differs=frozenset({"L-A", "L-B"})))

    response = await api_client.get(_path(scene.project.id), headers=headers_of(scene.owner))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "floors": [
            {
                "floorId": "L-A",
                "kind": "plan",
                "kindConfidence": 0.92,
                "storeyLabel": "ground",
                "storeyNo": 1,
                "storeyConfidence": 0.95,
                "elevationMm": 0,
                "elevationConfidence": 0.9,
                "elevationSource": "plan",
                "heightMm": 3300,
                "heightConfidence": 0.8,
                "heightSource": "convention",
                "questions": [{"code": "HEIGHT_ASSUMED"}, {"code": "ORDER_DIFFERS"}],
            },
            {
                "floorId": "L-B",
                "kind": "other",
                "kindConfidence": 0.0,
                "storeyConfidence": 0.0,
                "questions": [{"code": "NO_TEXT"}, {"code": "ORDER_DIFFERS"}],
            },
        ],
        "runIds": ["run_1"],
    }
    assert spy.calls == [scene.project.id]


async def test_drawing_sets_read_house_proposal__viewer_reads(
    api_client: httpx.AsyncClient, db_session: AsyncSession, drawing_set_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quyền là "thành viên bất kỳ": viewer của dự án cũng đọc được (200)."""
    owner = await make_user(db_session, role="engineer")
    viewer = await make_user(db_session, role="viewer")
    project = await make_project(db_session, owner=owner, members=[viewer])
    await db_session.commit()
    _stub(monkeypatch, _read())

    response = await api_client.get(_path(project.id), headers=headers_of(viewer))

    assert response.status_code == 200, response.text


async def test_drawing_sets_read_house_proposal__empty_project(
    api_client: httpx.AsyncClient, db_session: AsyncSession, drawing_set_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dự án không tầng → 200 `{"floors": [], "runIds": []}`: mảng rỗng vẫn có khoá."""
    scene = await make_scene(db_session, floors=0)
    _stub(monkeypatch, _read())

    response = await api_client.get(_path(scene.project.id), headers=headers_of(scene.owner))

    assert (response.status_code, response.json()) == (200, {"floors": [], "runIds": []})


async def test_drawing_sets_read_house_proposal__real_service(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    drawing_set_on: None,
) -> None:
    """Service thật trên DB thật: hai tầng chưa có lượt chạy → mỗi tầng `NO_TEXT`, không lỗi, `runIds` rỗng."""
    scene = await make_scene(db_session, floors=2)

    response = await api_client.get(_path(scene.project.id), headers=headers_of(scene.owner))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runIds"] == []
    assert [f["floorId"] for f in body["floors"]] == [f.level_id for f in scene.floors]
    assert all(f["questions"] == [{"code": "NO_TEXT"}] and f["kind"] == "other" for f in body["floors"])


def _wire(floor: FloorProposal, differs: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Tầng đầu tiên của bản ánh xạ, dạng dây."""
    dumped = house_proposal_out(HouseProposal((floor,)), (), differs).model_dump(by_alias=True)
    first: dict[str, Any] = dumped["floors"][0]
    return first


@pytest.mark.parametrize("kind", get_args(FloorKind.__value__))
def test_house_proposal_out__every_kind(kind: Any) -> None:
    """Mỗi `kind` dây đi qua ánh xạ nguyên vẹn."""
    assert _wire(FloorProposal("L", kind, 0.5))["kind"] == kind


@pytest.mark.parametrize("label", get_args(StoreyLabel.__value__))
def test_house_proposal_out__every_storey_label(label: Any) -> None:
    """Mỗi `storeyLabel` dây đi qua ánh xạ nguyên vẹn."""
    assert _wire(FloorProposal("L", "plan", 0.9, label, 1, 0.9))["storeyLabel"] == label


@pytest.mark.parametrize("source", get_args(LevelSource.__value__))
def test_house_proposal_out__every_source(source: str) -> None:
    """Mỗi nguồn cốt/chiều cao dây đi qua ánh xạ nguyên vẹn."""
    floor = FloorProposal(
        "L", "plan", 0.9, elevation_mm=0, elevation_source=source, height_mm=3000, height_source=source
    )
    wire = _wire(floor)
    assert (wire["elevationSource"], wire["heightSource"]) == (source, source)


@pytest.mark.parametrize("code", ALL_CODES)
def test_house_proposal_out__every_question_code(code: str) -> None:
    """Mỗi mã câu hỏi dây đi qua ánh xạ; `ORDER_DIFFERS` của lớp API được thêm sau mã của miền."""
    assert _wire(FloorProposal("L", "plan", 0.9, questions=(code,)))["questions"] == [{"code": code}]
    flagged = _wire(FloorProposal("L", "plan", 0.9, questions=(code,)), frozenset({"L"}))["questions"]
    assert flagged == [{"code": code}, {"code": "ORDER_DIFFERS"}]


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "garage"},
        {"storey_label": "penthouse"},
        {"elevation_source": "guess"},
        {"height_source": "guess"},
        {"questions": ("TOO_TALL",)},
    ],
)
def test_house_proposal_out__domain_value_outside_wire_enum_fails(bad: dict[str, Any]) -> None:
    """Giá trị miền ngoài enum dây làm `ValidationError`, không lọt ra FE."""
    floor = FloorProposal(**{"floor_id": "L", "kind": "plan", "kind_confidence": 0.5, **bad})
    with pytest.raises(ValidationError):
        house_proposal_out(HouseProposal((floor,)), (), frozenset())


def test_wire_enums_cover_what_the_domain_can_produce() -> None:
    """Mọi `Literal` và mã câu hỏi của miền nằm trong enum dây — miền thêm giá trị mới thì test này đỏ."""
    assert set(get_args(titles.Kind)) <= set(get_args(FloorKind.__value__))
    assert set(get_args(storeys.StoreyLabel)) <= set(get_args(StoreyLabel.__value__))
    sources = set(get_args(LevelSource.__value__))
    assert set(get_args(heights.LevelSource)) | set(get_args(heights.HeightSource)) <= sources
    codes = {value for name, value in vars(domain).items() if name.isupper() and value == name}
    assert codes == set(ALL_CODES)
