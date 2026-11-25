"""Case của route đọc đề xuất ngôi nhà `drawing_sets_read_building` (Đ) — NHA-06a.

Bộ `review` dựng bằng factory; kết quả đọc từng trang ghi thẳng vào kho đúng khoá worker `ml` ghi. Loại hình và khung
lấy từ dòng `drawing_set_views` (thứ người duyệt đã sửa), không đoán lại từ chữ.
"""

from typing import Any, Final, get_args

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawing_sets import building_read
from apps.api.drawing_sets.building_schemas import BuildingSource, ConflictField, RuleLevel, set_building_out
from apps.api.drawing_sets.settings import reset_drawing_set_settings_cache
from apps.api.drawing_sets.tests._set_helpers import MISSING_SET, ReviewSet, seed_review_set, set_path
from apps.api.drawing_sets.tests._set_helpers import (
    drawing_set_on as drawing_set_on,
)
from apps.api.drawings.tests._upload_helpers import Stage, headers_of, make_stage
from packages.core.error_codes import DEPENDENCY_UNAVAILABLE
from packages.domain.drawing_set import building, rules
from packages.ml_contracts.artifacts import BoxPx, TextPx
from packages.ml_contracts.drawing_sets import PageReadingDoc, reading_to_json
from packages.storage.keys import drawing_set_reading
from packages.storage.local import LocalDiskStorage
from packages.testing.factories.auth import make_user

pytestmark = pytest.mark.usefixtures("drawing_set_on")

PAGE: Final = (2000, 1200)
HALF: Final = {"x_ratio": 0.5, "y_ratio": 0.0, "width_ratio": 0.5, "height_ratio": 1.0}
LEFT: Final = {"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 0.5, "height_ratio": 1.0}


def _path(stage: Stage, set_id: str) -> str:
    """Đường của route đọc đề xuất ngôi nhà."""
    return f"{set_path(stage.project_id, set_id)}/building"


def _text(value: str, x0: float, y0: float, x1: float, y1: float) -> TextPx:
    """Một dòng chữ OCR."""
    return TextPx(text=value, box=BoxPx(x_min=x0, y_min=y0, x_max=x1, y_max=y1), confidence=0.9)


def _drawing(title: str, *marks: str, x0: int = 0) -> list[TextPx]:
    """Chữ của một hình rộng nửa trang đặt tại `x0`: vài nhãn nhỏ, các cốt/độ dốc, tên lớn ở giữa dưới hình."""
    texts = [_text(f"P{i}", x0 + 60 + 90 * i, 100, x0 + 120 + 90 * i, 130) for i in range(6)]
    texts += [_text(mark, x0 + 60 + 200 * i, 300, x0 + 180 + 200 * i, 330) for i, mark in enumerate(marks)]
    return [*texts, _text(title, x0 + 250, 1000, x0 + 750, 1080)]


async def _store(storage: LocalDiskStorage, stage: Stage, seeded: ReviewSet, index: int, texts: list[TextPx]) -> None:
    """Ghi kết quả đọc trang `index` của tệp duy nhất trong bộ."""
    doc = PageReadingDoc(width_px=PAGE[0], height_px=PAGE[1], boxes=(), texts=tuple(texts), read=True)
    key = drawing_set_reading(stage.project_id, seeded.drawing_set.id, seeded.file.id, index)
    body = reading_to_json(doc)
    await storage.put(key, body, content_type="application/json", max_bytes=len(body))


async def _house(db: AsyncSession, storage: LocalDiskStorage, **overrides: Any) -> tuple[Stage, ReviewSet]:
    """Bộ ba trang: hai mặt bằng (±0.000, +3.300) và một mặt bằng mái ghi `i=30%`, loại hình đã chốt chắc."""
    stage = await make_stage(db)
    seeded = await seed_review_set(db, storage, stage, views=3, pages=3, **overrides)
    for view, kind in zip(seeded.views, ("plan", "plan", "roof"), strict=True):
        view.kind, view.kind_confidence = kind, 0.9
    await db.commit()
    await _store(storage, stage, seeded, 0, _drawing("MẶT BẰNG TẦNG 1", "±0.000"))
    await _store(storage, stage, seeded, 1, _drawing("MẶT BẰNG TẦNG 2", "+3.300"))
    await _store(storage, stage, seeded, 2, _drawing("MẶT BẰNG MÁI", "i=30%"))
    return stage, seeded


async def _get(client: httpx.AsyncClient, stage: Stage, seeded: ReviewSet) -> dict[str, Any]:
    """Gọi route, khẳng định 200, trả thân."""
    response = await client.get(_path(stage, seeded.drawing_set.id), headers=stage.headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_drawing_sets_read_building__C01(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Bộ `review` → 200: tầng theo mã hình, một mái có nguồn `roofPlan`, vi phạm theo mã; không ghi gì."""
    stage, seeded = await _house(db_session, local_storage)
    ground, upper, roof_plan = (view.id for view in seeded.views)
    wire = await _get(api_client, stage, seeded)
    proposal = wire["proposal"]
    assert wire["enabled"] is True
    assert [(f["floorId"], f["storeyNo"], f["elevationMm"]) for f in proposal["floors"]] == [
        (ground, 1, 0),
        (upper, 2, 3300),
    ]
    assert proposal["unitSystem"] == {"value": "metric", "confidence": 0.9, "source": "plan", "viewIds": []}
    (roof,) = proposal["roofs"]
    assert roof["levelRef"] == upper
    assert roof["pitchDeg"] == {"value": 16.7, "confidence": 0.8, "source": "roofPlan", "viewIds": [roof_plan]}
    assert (roof["form"]["value"], roof["eaveElevationMm"]["value"], roof["overhangMm"]["value"]) == (
        "gable",
        6600,
        300,
    )
    assert proposal["conflicts"] == []
    unsure = [hit for hit in proposal["violations"] if hit["code"] == "M-18"]
    assert unsure == [{"code": "M-18", "level": "warning", "floorRefs": [roof_plan], "values": ["convention"]}]
    await db_session.refresh(seeded.drawing_set)
    assert seeded.drawing_set.revision == 0


async def test_drawing_sets_read_building__C17(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Trường tuỳ chọn không có giá trị → vắng khoá, không `null` (W2): mái chưa có cốt đỉnh, parapet, vật liệu."""
    stage, seeded = await _house(db_session, local_storage)
    (roof,) = (await _get(api_client, stage, seeded))["proposal"]["roofs"]
    assert set(roof) == {"levelRef", "form", "pitchDeg", "quarterTurns", "eaveElevationMm", "overhangMm"}


async def test_reviewed_kind_decides_what_is_read(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Người duyệt đổi hình mái thành "khác" và loại một mặt bằng: mái hết nguồn `roofPlan`, tầng bị loại vắng."""
    stage, seeded = await _house(db_session, local_storage)
    seeded.views[2].kind = "other"
    seeded.views[0].excluded = True
    await db_session.commit()
    proposal = (await _get(api_client, stage, seeded))["proposal"]
    assert [floor["floorId"] for floor in proposal["floors"]] == [seeded.views[1].id]
    assert [(roof["form"]["value"], roof["form"]["source"]) for roof in proposal["roofs"]] == [("flat", "default")]
    assert "M-18" not in {hit["code"] for hit in proposal["violations"]}


async def test_unsure_roof_kind_is_not_a_roof_source(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Hình máy xếp là mái nhưng chưa chắc (< 0,8) và người duyệt chưa chốt: chưa dùng làm nguồn mái."""
    stage, seeded = await _house(db_session, local_storage)
    seeded.views[2].kind_confidence = 0.5
    await db_session.commit()
    (roof,) = (await _get(api_client, stage, seeded))["proposal"]["roofs"]
    assert roof["pitchDeg"]["source"] == "default"


async def test_crop_decides_which_text_belongs_to_a_view(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Hai hình trên một trang (nửa trái, nửa phải): mỗi hình chỉ nhận chữ trong khung của nó."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, views=2, pages=1, kind="plan", kind_confidence=0.9)
    seeded.views[0].crop, seeded.views[1].crop = LEFT, HALF
    await db_session.commit()
    texts = [*_drawing("MẶT BẰNG TẦNG 1", "±0.000"), *_drawing("MẶT BẰNG TẦNG 2", "+3.300", x0=1000)]
    await _store(local_storage, stage, seeded, 0, texts)
    floors = (await _get(api_client, stage, seeded))["proposal"]["floors"]
    assert [(f["storeyNo"], f["elevationMm"]) for f in floors] == [(1, 0), (2, 3300)]


async def test_page_without_a_reading_is_a_view_without_text(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Trang không có kết quả đọc trong kho: hình đó ra `NO_TEXT`, cả route không hỏng."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, views=1, pages=1, kind="plan", kind_confidence=0.9)
    (floor,) = (await _get(api_client, stage, seeded))["proposal"]["floors"]
    assert [question["code"] for question in floor["questions"]] == ["NO_TEXT"]


@pytest.mark.parametrize("status", ["receiving", "analyzing", "failed"])
async def test_set_not_analysed_yet_has_no_proposal(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage, status: str
) -> None:
    """Bộ chưa qua phân tích (chưa có hình, chưa có kết quả đọc): 200 không có khoá `proposal`."""
    stage = await make_stage(db_session)
    seeded = await seed_review_set(db_session, local_storage, stage, status=status)
    assert await _get(api_client, stage, seeded) == {"enabled": True}


async def test_drawing_sets_read_building__disabled(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Công tắc tắt → 200 `{"enabled": false}`, không đọc kho (mẫu N52), không `Retry-After`."""
    stage, seeded = await _house(db_session, local_storage)
    monkeypatch.setenv("DRAWING_SET_ENABLED", "false")
    reset_drawing_set_settings_cache()
    response = await api_client.get(_path(stage, seeded.drawing_set.id), headers=stage.headers)
    assert (response.status_code, response.json()) == (200, {"enabled": False})
    assert "Retry-After" not in response.headers


async def test_drawing_sets_read_building__C06(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Người ngoài dự án → 404 `resource:"project"`; bộ không có → 404 `resource:"drawingSet"`."""
    stage, seeded = await _house(db_session, local_storage)
    outsider = await make_user(db_session, role="admin")
    await db_session.commit()
    denied = await api_client.get(_path(stage, seeded.drawing_set.id), headers=headers_of(outsider))
    missing = await api_client.get(_path(stage, MISSING_SET), headers=stage.headers)
    assert (denied.status_code, denied.json()["resource"]) == (404, "project")
    assert (missing.status_code, missing.json()["resource"]) == (404, "drawingSet")


async def test_reading_past_the_deadline_is_a_retryable_503(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Đọc + tính quá `DEADLINE_S` → 503 `DEPENDENCY_UNAVAILABLE` có `Retry-After`, không nửa kết quả."""
    stage, seeded = await _house(db_session, local_storage)
    monkeypatch.setattr(building_read, "DEADLINE_S", 0.0)
    response = await api_client.get(_path(stage, seeded.drawing_set.id), headers=stage.headers)
    assert (response.status_code, response.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    assert response.headers["Retry-After"] == str(building_read.RETRY_AFTER_S)


def test_wire_enums_cover_what_the_domain_can_produce() -> None:
    """Mọi nguồn và mức luật của miền nằm trong enum dây — miền thêm giá trị mới thì test này đỏ."""
    assert set(get_args(building.Source.__value__)) == set(get_args(BuildingSource.__value__))
    assert set(get_args(rules.RuleLevel.__value__)) == set(get_args(RuleLevel.__value__))
    fields = building.Conflict.model_fields["field"].annotation
    assert set(get_args(fields)) == set(get_args(ConflictField.__value__))


def test_no_proposal_is_an_absent_key() -> None:
    """Không có đề xuất → khoá `proposal` vắng (W2)."""
    assert set_building_out(None).model_dump(by_alias=True) == {"enabled": True}


async def test_set_of_another_project_is_not_found(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Thành viên dự án A hỏi bộ của dự án B qua đường của A → 404 `drawingSet`: không đọc được bộ của dự án khác."""
    stage, _ = await _house(db_session, local_storage)
    _, theirs = await _house(db_session, local_storage)
    response = await api_client.get(_path(stage, theirs.drawing_set.id), headers=stage.headers)
    assert (response.status_code, response.json()["resource"]) == (404, "drawingSet")


@pytest.mark.parametrize(("confidence", "source"), [(0.9, "section"), (0.5, "default")])
async def test_sure_section_feeds_the_level_ladder(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    confidence: float,
    source: str,
) -> None:
    """Mặt cắt chắc loại có ba cốt cho chiều cao tầng trên cùng (nguồn `section`); mặt cắt chưa chắc thì không.

    Khung của hình mặt cắt để trống trong DB (= cả trang).
    """
    stage, seeded = await _house(db_session, local_storage)
    side = seeded.views[2]
    side.kind, side.kind_confidence, side.crop = "section", confidence, None
    await db_session.commit()
    marks = [("±0.000", 1000), ("+3.300", 670), ("+6.600", 340)]
    await _store(local_storage, stage, seeded, 2, [_text(mark, 1500, y - 15, 1620, y + 15) for mark, y in marks])
    proposal = (await _get(api_client, stage, seeded))["proposal"]
    assert [floor["heightSource"] for floor in proposal["floors"]][-1] == source
    assert len(proposal["floors"]) == 2


async def test_set_with_every_view_excluded_proposes_nothing(
    api_client: httpx.AsyncClient, db_session: AsyncSession, local_storage: LocalDiskStorage
) -> None:
    """Người duyệt loại hết hình: 200 với đề xuất rỗng, không đọc trang nào."""
    stage, seeded = await _house(db_session, local_storage)
    for view in seeded.views:
        view.excluded = True
    await db_session.commit()
    proposal = (await _get(api_client, stage, seeded))["proposal"]
    assert (proposal["floors"], proposal["roofs"], proposal["conflicts"]) == ([], [], [])


async def test_storage_failure_surfaces_as_the_first_error(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    local_storage: LocalDiskStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kho lỗi tạm thời khi đọc trang: lỗi đầu tiên lan nguyên ra (503 thử lại được), không phải 500 gói nhóm lỗi."""
    stage, seeded = await _house(db_session, local_storage)

    async def down(*_: object) -> list[object]:
        """Kho không đọc được."""
        raise DEPENDENCY_UNAVAILABLE.error(retry_after=3)

    monkeypatch.setattr(building_read, "page_readings", down)
    response = await api_client.get(_path(stage, seeded.drawing_set.id), headers=stage.headers)
    assert (response.status_code, response.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    assert response.headers["Retry-After"] == "3"
