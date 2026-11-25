"""`progress_wire`/`progress_of` khớp từng hàng bảng "Nguồn trạng thái"."""

from datetime import UTC, datetime
from typing import Any, Final

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.drawings.progress import progress_of, progress_wire
from apps.api.drawings.schemas import (
    CompleteUploadBody,
    InitUploadBody,
    LatestFloorUploadOut,
    LatestFloorUploadPage,
    ProgressOut,
    UploadChunkBody,
    clean_file_name,
)
from apps.api.drawings.tests._helpers import make_scene
from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.db.models.drawings import PipelineRunRow
from packages.testing.factories.drawings import make_upload

STARTED: Final = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
ENDED: Final = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
UPLOAD_ID: Final = "upl_00000000000000000000000000"

_NO_RUN: Final[dict[str, Any]] = {
    "run_status": None,
    "current_step": None,
    "progress_percent": None,
    "error_code": None,
    "started_at": None,
    "ended_at": None,
}


def _fields(**overrides: Any) -> dict[str, Any]:
    """Bộ cột đầy đủ của một lượt tải `complete` có lượt chạy, ghi đè phần đang thử."""
    base: dict[str, Any] = {
        "upload_status": "complete",
        "rejected_code": None,
        "run_status": "running",
        "current_step": "wallSegmentation",
        "progress_percent": 5,
        "error_code": None,
        "started_at": STARTED,
        "ended_at": None,
    }
    return base | overrides


def test_progress_of_pending_while_upload_is_receiving() -> None:
    """Hàng 1: chưa tải xong thì luôn `pending`/`preprocess`/0, dù chưa có lượt chạy nào."""
    assert progress_of(UPLOAD_ID, upload_status="receiving", rejected_code=None, **_NO_RUN) == {
        "id": UPLOAD_ID,
        "status": "pending",
        "step": "preprocess",
        "progressPercent": 0,
    }


def test_progress_of_pending_while_run_is_queued() -> None:
    """Hàng 1: lượt đã xếp hàng nhưng worker chưa nhận — vẫn `pending`, không `startedAt`."""
    wire = progress_of(UPLOAD_ID, **_fields(run_status="pending", current_step="preprocess", progress_percent=0))
    assert wire == {"id": UPLOAD_ID, "status": "pending", "step": "preprocess", "progressPercent": 0}


def test_progress_of_failed_when_upload_rejected() -> None:
    """Hàng 2: upload bị từ chối ở #7 → `failed` mang `rejected_code`, không `endedAt`."""
    wire = progress_of(UPLOAD_ID, upload_status="rejected", rejected_code="FILE_CORRUPT", **_NO_RUN)
    assert wire == {
        "id": UPLOAD_ID,
        "status": "failed",
        "step": "preprocess",
        "progressPercent": 0,
        "error": "FILE_CORRUPT",
    }


def test_progress_of_running_carries_step_and_started_at() -> None:
    """Hàng 3: `step` là bước đang chạy, phần trăm lấy nguyên từ cột."""
    assert progress_of(UPLOAD_ID, **_fields()) == {
        "id": UPLOAD_ID,
        "status": "running",
        "step": "wallSegmentation",
        "progressPercent": 5,
        "startedAt": "2026-01-01T10:00:00.000Z",
    }


def test_progress_of_completed_is_always_hundred_at_last_step() -> None:
    """Hàng 4: xong thì `qualityCheck`/100/`endedAt`, kể cả khi cột còn số cũ."""
    wire = progress_of(
        UPLOAD_ID, **_fields(run_status="completed", current_step="qualityCheck", progress_percent=90, ended_at=ENDED)
    )
    assert wire == {
        "id": UPLOAD_ID,
        "status": "completed",
        "step": "qualityCheck",
        "progressPercent": 100,
        "startedAt": "2026-01-01T10:00:00.000Z",
        "endedAt": "2026-01-01T10:05:00.000Z",
    }


def test_progress_of_failed_run_keeps_last_percent_and_drops_ended_at() -> None:
    """Hàng 5: lượt hỏng giữ phần trăm cuối, có `error`, **không** `endedAt` (K33)."""
    wire = progress_of(
        UPLOAD_ID,
        **_fields(run_status="failed", progress_percent=35, error_code="PIPELINE_SUPERSEDED", ended_at=ENDED),
    )
    assert "endedAt" not in wire
    assert wire["status"] == "failed"
    assert wire["error"] == "PIPELINE_SUPERSEDED"
    assert wire["progressPercent"] == 35


def test_progress_of_completed_without_started_at_omits_both_keys() -> None:
    """Lượt xong mà thiếu mốc thời gian: khoá vắng hẳn, không `null` (W2)."""
    wire = progress_of(UPLOAD_ID, **_fields(run_status="completed", started_at=None, ended_at=None))
    assert "startedAt" not in wire
    assert "endedAt" not in wire


@pytest.mark.parametrize(
    "fields",
    [
        {"upload_status": "receiving", "rejected_code": None, **_NO_RUN},
        {"upload_status": "rejected", "rejected_code": "CAD_NOT_SUPPORTED", **_NO_RUN},
        _fields(),
        _fields(run_status="completed", ended_at=ENDED),
        _fields(run_status="failed", error_code="FLOOR_DELETED"),
    ],
)
def test_every_progress_row_validates_against_the_wire_model(fields: dict[str, Any]) -> None:
    """Mọi hàng của bảng §4 dựng được `ProgressOut` — `extra="forbid"` bắt khoá thừa (K01)."""
    assert ProgressOut.model_validate(progress_of(UPLOAD_ID, **fields)).id == UPLOAD_ID


async def test_progress_wire_reads_upload_without_any_run(db_session: AsyncSession) -> None:
    """Lượt tải vừa init: một truy vấn, không lượt chạy, `pending`."""
    scene = await make_scene(db_session)
    upload = await make_upload(db_session, project=scene.project, floor=scene.floor)
    assert await progress_wire(db_session, upload.id) == {
        "id": upload.id,
        "status": "pending",
        "step": "preprocess",
        "progressPercent": 0,
    }


async def test_progress_wire_reads_rejected_upload(db_session: AsyncSession) -> None:
    """Upload `rejected` thắng mọi lượt chạy: FE thấy mã từ chối, không thấy tiến độ cũ."""
    scene = await make_scene(db_session)
    upload = await make_upload(
        db_session, project=scene.project, floor=scene.floor, status="rejected", rejected_code="FILE_TYPE_MISMATCH"
    )
    wire = await progress_wire(db_session, upload.id)
    assert wire["status"] == "failed"
    assert wire["error"] == "FILE_TYPE_MISMATCH"


async def test_progress_wire_takes_the_newest_run_of_the_upload(db_session: AsyncSession) -> None:
    """Lượt tải bị chạy lại: `Progress` theo lượt **mới nhất**, không phải lượt bị thay."""
    scene = await make_scene(db_session)
    upload = await make_upload(db_session, project=scene.project, floor=scene.floor, status="complete")
    old = PipelineRunRow(
        id=new_id("run", SystemClock()),
        upload_id=upload.id,
        floor_pk=scene.floor.pk,
        status="failed",
        current_step="preprocess",
        progress_percent=0,
        error_code="PIPELINE_SUPERSEDED",
        created_at=STARTED,
    )
    db_session.add(old)
    await db_session.flush()
    new = PipelineRunRow(
        id=new_id("run", SystemClock()),
        upload_id=upload.id,
        floor_pk=scene.floor.pk,
        status="running",
        current_step="dimensionReading",
        progress_percent=55,
        started_at=STARTED,
        created_at=ENDED,
    )
    db_session.add(new)
    await db_session.flush()

    wire = await progress_wire(db_session, upload.id)
    assert wire["status"] == "running"
    assert wire["step"] == "dimensionReading"
    assert wire["progressPercent"] == 55


def _pending_wire() -> dict[str, object]:
    """Khung `pending` gốc của `UPLOAD_ID`."""
    return {"id": UPLOAD_ID, "status": "pending", "step": "preprocess", "progressPercent": 0}


def test_progress_of_carries_queue_ahead_only_for_live_runs() -> None:
    """UX41 U02: `queueAhead` đi kèm lượt `pending`/`running`; lượt xong, hỏng hay chưa có thì vắng."""
    assert progress_of(UPLOAD_ID, **_fields(), queue_ahead=2)["queueAhead"] == 2
    pending = progress_of(UPLOAD_ID, **_fields(run_status="pending"), queue_ahead=0)
    assert pending == _pending_wire() | {"queueAhead": 0}
    assert "queueAhead" not in progress_of(UPLOAD_ID, **_fields(run_status="completed"), queue_ahead=3)
    assert "queueAhead" not in progress_of(UPLOAD_ID, **_fields(run_status="failed", error_code="X"), queue_ahead=3)
    assert "queueAhead" not in progress_of(UPLOAD_ID, upload_status="receiving", rejected_code=None, **_NO_RUN)
    assert ProgressOut.model_validate(progress_of(UPLOAD_ID, **_fields(), queue_ahead=1)).queue_ahead == 1


async def test_progress_wire_counts_live_runs_opened_earlier(db_session: AsyncSession) -> None:
    """UX41 U02: lượt sống mở trước (kể cả dự án khác) đứng trước; lượt xong hoặc mở sau không tính."""
    clock = SystemClock()
    scene = await make_scene(db_session)
    other = await make_scene(db_session)

    async def run_at(upload_floor: Any, at: datetime, status: str) -> str:
        upload = await make_upload(
            db_session, project=upload_floor.project, floor=upload_floor.floor, status="complete"
        )
        db_session.add(
            PipelineRunRow(
                id=new_id("run", clock),
                upload_id=upload.id,
                floor_pk=upload_floor.floor.pk,
                status=status,
                current_step="wallSegmentation",
                progress_percent=5,
                created_at=at,
            )
        )
        await db_session.flush()
        return upload.id

    first = await run_at(other, STARTED, "running")
    await run_at(other, STARTED.replace(minute=1), "completed")
    mine = await run_at(scene, STARTED.replace(minute=2), "running")
    await run_at(other, STARTED.replace(minute=3), "pending")

    assert (await progress_wire(db_session, first))["queueAhead"] == 0
    assert (await progress_wire(db_session, mine))["queueAhead"] == 1


async def test_progress_wire_rejects_unknown_upload(db_session: AsyncSession) -> None:
    """Không có dòng upload là lỗi lập trình của người gọi, không phải 404."""
    with pytest.raises(ValueError, match="không có lượt tải"):
        await progress_wire(db_session, UPLOAD_ID)


# ---------------------------------------------------------------------------
# Schema dây của module (cùng nhà với `ProgressOut`, B2-04 [2])
# ---------------------------------------------------------------------------


def test_clean_file_name_normalises_to_nfc() -> None:
    """C16: tên gửi dạng NFD được lưu NFC, và khoảng trắng hai đầu bị cắt."""
    assert clean_file_name("  bàn-vẽ.png  ") == "bàn-vẽ.png"


def test_clean_file_name_passes_non_strings_to_pydantic() -> None:
    """Giá trị không phải chuỗi để Pydantic báo lỗi kiểu, không biến thành lỗi tên tệp."""
    assert clean_file_name(7) == 7


@pytest.mark.parametrize("value", ["   ", "x" * 256, "ban\u0000ve.png", "ban‮ve.png", "a/b.png", "a\b.png"])
def test_clean_file_name_rejects_dangerous_names(value: str) -> None:
    r"""Rỗng, quá 255, ký tự điều khiển, đảo chiều, `/`, `\` → 422 `field:"fileName"` ([6] #5)."""
    with pytest.raises(ValueError, match="fileName"):
        clean_file_name(value)


def test_init_upload_body_defaults_page_index_to_zero() -> None:
    """`pageIndex` vắng = 0 (F-03); thân đọc theo khoá camelCase của FE."""
    body = InitUploadBody.model_validate(
        {
            "fileName": "ban-ve.png",
            "floorId": "L-0000000001",
            "mimeType": "image/png",
            "projectId": "prj_00000000000000000000000000",
            "sizeBytes": 1,
        }
    )
    assert body.page_index == 0
    assert body.file_name == "ban-ve.png"


@pytest.mark.parametrize(
    ("field", "value"), [("sizeBytes", 0), ("pageIndex", -1), ("pageIndex", 20), ("mimeType", "x" * 256)]
)
def test_init_upload_body_rejects_out_of_range_fields(field: str, value: object) -> None:
    """Biên kiểm được ở schema; trần `sizeBytes` (413) và luật đuôi/MIME là việc của route."""
    payload: dict[str, Any] = {
        "fileName": "ban-ve.png",
        "floorId": "L-0000000001",
        "mimeType": "image/png",
        "projectId": "prj_00000000000000000000000000",
        "sizeBytes": 1,
    }
    with pytest.raises(ValidationError):
        InitUploadBody.model_validate(payload | {field: value})


def test_init_upload_body_rejects_unknown_keys() -> None:
    """C03: khoá lạ trong thân là 422, không phải im lặng bỏ qua."""
    with pytest.raises(ValidationError):
        InitUploadBody.model_validate(
            {
                "fileName": "ban-ve.png",
                "floorId": "L-0000000001",
                "mimeType": "image/png",
                "projectId": "prj_00000000000000000000000000",
                "sizeBytes": 1,
                "rogue": 1,
            }
        )


def test_chunk_and_complete_bodies_mirror_their_path_ids() -> None:
    """Thân #6 không mang id của đường; thân #7 khai `uploadId` cho guard W21."""
    chunk = UploadChunkBody.model_validate({"chunk": "AAA=", "chunkIndex": 0})
    assert (chunk.chunk, chunk.chunk_index) == ("AAA=", 0)
    assert CompleteUploadBody.model_validate({"uploadId": UPLOAD_ID}).upload_id == UPLOAD_ID


@pytest.mark.parametrize(("field", "value"), [("chunk", ""), ("chunkIndex", -1)])
def test_chunk_body_rejects_empty_chunk_and_negative_index(field: str, value: object) -> None:
    """Khúc rỗng và chỉ số âm là 422 kèm đúng `field` ([6] #6 bước 2)."""
    with pytest.raises(ValidationError):
        UploadChunkBody.model_validate({"chunk": "AAA=", "chunkIndex": 0} | {field: value})


def test_latest_floor_upload_drops_the_missing_url() -> None:
    """W2: `sourceImageUrl` vắng thì vắng hẳn, và trang N7 giữ khuôn `{items, nextCursor?}`."""
    item = LatestFloorUploadOut(floor_id="L-0000000001", floor_name="Tầng 1", upload_id=UPLOAD_ID)
    assert item.model_dump(by_alias=True) == {
        "floorId": "L-0000000001",
        "floorName": "Tầng 1",
        "uploadId": UPLOAD_ID,
    }
    assert LatestFloorUploadPage(items=[item]).model_dump(by_alias=True) == {"items": [item.model_dump(by_alias=True)]}
