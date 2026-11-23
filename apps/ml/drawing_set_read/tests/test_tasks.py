"""J-case của task `ml.infer.drawing_set.read` (K-32a PR(3), BE-00 §7 "Test task", CASE §2.3).

Kho đĩa `local_storage` thật, ảnh trang tổng hợp (ba tấm giấy xám), bộ tách khung thật; bộ đọc chữ là bộ giả
cố định (OCR thật đã có test riêng ở `apps/ml/text`). Hàm task mỏng chạy bằng `.apply()` (không worker), thông
điệp `drawing_set.publish` bắt lại tại chỗ gửi.
"""

import asyncio
from typing import Final

import cv2
import numpy as np
import pytest

from apps.ml.drawing_set_read import tasks
from apps.ml.drawing_set_read.tasks import READ_PAGES_PER_TASK, read_chunk, read_drawing_set, read_pages
from apps.ml.runtime.settings import MlSettings
from apps.ml.runtime.tasks_util import InferContext
from packages.core.clock import SystemClock
from packages.core.ids import new_id
from packages.messaging import PermanentError
from packages.ml_contracts.artifacts import BoxPx, TextPx
from packages.ml_contracts.drawing_sets import (
    BOXES_PER_PAGE_MAX,
    DRAWING_SET_READ_TASK,
    DrawingSetReadPayload,
    PageRef,
    reading_from_json,
)
from packages.ml_contracts.payloads import ModelRef
from packages.storage.keys import drawing_set_page, drawing_set_reading
from packages.storage.local import LocalDiskStorage
from packages.vision.preprocess import RgbImage, encode_png

PANELS: Final = [(50, 50, 450, 400), (500, 60, 1300, 420), (80, 500, 700, 850)]
CLASSIC: Final = ModelRef(
    version_id=None, family="dimensionReading", weights_key=None, pinned_name=None, checksum_sha256=""
)
WORD: Final = TextPx(text="MẶT BẰNG TẦNG 1", box=BoxPx(x_min=100, y_min=360, x_max=300, y_max=390), confidence=0.9)


class FixedReader:
    """Bộ đọc giả: một dòng chữ cố định cho mọi trang, đếm số lần đọc."""

    def __init__(self, items: tuple[TextPx, ...] = (WORD,)) -> None:
        self.items, self.calls = items, 0

    def read(self, image: np.ndarray) -> tuple[TextPx, ...]:
        """Chữ cố định."""
        self.calls += 1
        return self.items


class BrokenReader:
    """Bộ đọc giả ném lỗi có mã (model hỏng giữa chừng)."""

    def read(self, image: np.ndarray) -> tuple[TextPx, ...]:
        """Luôn ném `PermanentError`."""
        raise PermanentError("MODEL_FORMAT_UNSUPPORTED")


def _sheet_png() -> bytes:
    """PNG RGB của tờ ba tấm giấy xám có nét mực (ca `selfcheck` của nhân tách hình)."""
    sheet = np.full((900, 1400), 255, np.uint8)
    rng = np.random.default_rng(0)
    for i, (x0, y0, x1, y1) in enumerate(PANELS):
        sheet[y0:y1, x0:x1] = 225 + 6 * i
        for _ in range(25):
            a, b = rng.integers(x0 + 10, x1 - 60), rng.integers(y0 + 10, y1 - 50)
            cv2.rectangle(sheet, (int(a), int(b)), (int(a) + 40, int(b) + 30), 40, 2)
    return encode_png(RgbImage(np.repeat(sheet[..., None], 3, axis=2)))


def _payload(pages: int = 2) -> DrawingSetReadPayload:
    """Payload hợp lệ: `pages` trang của một tệp, model cổ điển."""
    clock = SystemClock()
    file_id = new_id("dwf", clock)
    refs = tuple(PageRef(file_id=file_id, page_index=i) for i in range(pages))
    return DrawingSetReadPayload(
        project_id=new_id("prj", clock), set_id=new_id("dws", clock), pages=refs, model=CLASSIC
    )


def _store_pages(storage: LocalDiskStorage, payload: DrawingSetReadPayload, skip: int | None = None) -> None:
    """Ghi ảnh trang cho mọi trang của payload (trừ trang `skip`)."""
    png = _sheet_png()
    for page in payload.pages:
        if page.page_index != skip:
            key = drawing_set_page(payload.project_id, payload.set_id, page.file_id, page.page_index)
            asyncio.run(storage.put(key, png, content_type="image/png", max_bytes=len(png)))


def _reading(storage: LocalDiskStorage, payload: DrawingSetReadPayload, index: int) -> bytes | None:
    """Bytes kết quả đọc của trang `index`, `None` khi chưa ghi."""
    page = payload.pages[index]
    key = drawing_set_reading(payload.project_id, payload.set_id, page.file_id, page.page_index)

    async def _get() -> bytes | None:
        """Đọc cả object (nhỏ) hay `None` khi vắng."""
        if await storage.stat(key) is None:
            return None
        return b"".join([chunk async for chunk in storage.open_read(key)])

    return asyncio.run(_get())


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    """Bắt mọi thông điệp task gửi (tên, payload JSON) thay vì đẩy lên broker."""
    out: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(tasks, "send_task", lambda name, body: out.append((name, body.model_dump(mode="json"))))
    return out


@pytest.fixture
def context(local_storage: LocalDiskStorage, monkeypatch: pytest.MonkeyPatch) -> LocalDiskStorage:
    """Trỏ ngữ cảnh worker `ml` vào kho tạm của test, bộ đọc giả cố định."""
    settings = MlSettings(ml_backend="fake", ml_ort_threads=1)
    monkeypatch.setattr(tasks, "infer_context", lambda: InferContext(storage=local_storage, settings=settings))
    return local_storage


def test_read_drawing_set__J01(
    context: LocalDiskStorage,
    sent: list[tuple[str, dict[str, object]]],
    messaging_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task thật (`.apply`): mỗi trang một kết quả đọc (3 khung, chữ của bộ đọc), rồi đúng một `drawing_set.publish`."""
    reader = FixedReader()

    async def opened(model: ModelRef) -> FixedReader:
        """Bộ đọc giả thay model đang kích hoạt."""
        return reader

    monkeypatch.setattr(tasks, "open_text_reader", opened)
    payload = _payload()
    _store_pages(context, payload)
    result = read_drawing_set.apply(args=[payload.model_dump(mode="json")])
    assert result.state == "SUCCESS"
    for index in range(2):
        raw = _reading(context, payload, index)
        assert raw is not None
        doc = reading_from_json(raw)
        assert (doc.width_px, doc.height_px, doc.read, doc.texts) == (1400, 900, True, (WORD,))
        assert [(b.x0, b.y0) for b in doc.boxes] == [(50, 50), (500, 60), (80, 500)]
    assert sent == [("drawing_set.publish", {"schema_version": 1, "set_id": payload.set_id})]
    assert reader.calls == 2


def test_read_drawing_set__J06(context: LocalDiskStorage) -> None:
    """Giao lặp: lượt hai ghi đè đúng cùng bytes (khoá theo trang), không thêm object nào."""
    payload = _payload(pages=1)
    _store_pages(context, payload)
    assert asyncio.run(read_pages(context, payload, FixedReader())) == 1
    first = _reading(context, payload, 0)
    assert asyncio.run(read_pages(context, payload, FixedReader())) == 1
    assert _reading(context, payload, 0) == first


def test_read_drawing_set__J05(context: LocalDiskStorage) -> None:
    """Hết `budget_s`: trang sau hạn vẫn có khung nhưng không OCR (`read=False`); bộ đọc không bị gọi lần hai."""
    payload = _payload(pages=2)
    _store_pages(context, payload)
    ticks = iter([0.0, 0.0, 0.0, 100.0])
    reader = FixedReader()
    assert asyncio.run(read_chunk(context, payload, reader, budget_s=10.0, clock=lambda: next(ticks))) is None
    assert reader.calls == 1
    late = reading_from_json(_reading(context, payload, 1) or b"")
    assert (late.read, late.texts, len(late.boxes)) == (False, (), len(PANELS))


def test_read_chain_hands_rest_and_deadline_to_next_task(
    context: LocalDiskStorage,
    sent: list[tuple[str, dict[str, object]]],
    messaging_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO-566: 5 trang → ba task nối nhau (2 + 2 + 1 trang), mỗi task chỉ đọc chặng của nó, chuyền trang còn lại
    và **cùng** hạn OCR; chặng cuối gửi đúng một `drawing_set.publish`; mọi trang có kết quả đọc."""
    reader = FixedReader()

    async def opened(model: ModelRef) -> FixedReader:
        """Bộ đọc giả thay model đang kích hoạt."""
        return reader

    monkeypatch.setattr(tasks, "open_text_reader", opened)
    payload = _payload(pages=5)
    _store_pages(context, payload)
    body: dict[str, object] = payload.model_dump(mode="json")
    chunks: list[int] = []
    deadlines: list[object] = []
    for _ in range(3):
        before = reader.calls
        assert read_drawing_set.apply(args=[body]).state == "SUCCESS"
        chunks.append(reader.calls - before)
        _, body = sent[-1]
        deadlines.append(body.get("deadline_epoch_s"))
    assert chunks == [READ_PAGES_PER_TASK, READ_PAGES_PER_TASK, 1]
    assert [name for name, _ in sent] == [DRAWING_SET_READ_TASK, DRAWING_SET_READ_TASK, "drawing_set.publish"]
    assert [len(b["pages"]) for _, b in sent[:2]] == [3, 1]  # type: ignore[arg-type]
    assert deadlines[0] is not None
    assert deadlines[0] == deadlines[1]
    assert all(_reading(context, payload, i) is not None for i in range(5))


def test_read_chain_past_deadline_finishes_rest_in_one_task(context: LocalDiskStorage) -> None:
    """Chặng sau bắt đầu khi đã quá hạn chuyền tới → mọi trang còn lại làm luôn (khung, không OCR), hết chuỗi."""
    payload = _payload(pages=5).model_copy(update={"deadline_epoch_s": 50.0})
    _store_pages(context, payload)
    reader = FixedReader()
    assert asyncio.run(read_chunk(context, payload, reader, clock=lambda: 60.0)) is None
    assert reader.calls == 0
    docs = [reading_from_json(_reading(context, payload, i) or b"") for i in range(5)]
    assert [(d.read, len(d.boxes)) for d in docs] == [(False, len(PANELS))] * 5


def test_read_chunk_first_task_sets_deadline_from_budget(context: LocalDiskStorage) -> None:
    """Chặng đầu đặt hạn = bây giờ + `budget_s`; hạn đã có thì giữ nguyên (không gia hạn theo từng chặng)."""
    payload = _payload(pages=3)
    _store_pages(context, payload)
    following = asyncio.run(read_chunk(context, payload, FixedReader(), budget_s=480.0, clock=lambda: 1000.0))
    assert following is not None
    assert (following.deadline_epoch_s, len(following.pages)) == (1480.0, 1)
    assert following.pages == payload.pages[READ_PAGES_PER_TASK:]
    assert asyncio.run(read_chunk(context, following, FixedReader(), budget_s=480.0, clock=lambda: 1200.0)) is None


def test_read_drawing_set__J03(
    context: LocalDiskStorage,
    sent: list[tuple[str, dict[str, object]]],
    messaging_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lỗi vĩnh viễn: model không nạp được → vẫn tách khung, không chữ; `on_failed` vẫn gửi `publish` cho bộ."""

    async def broken(model: ModelRef) -> FixedReader:
        """Model hỏng (checksum, định dạng)."""
        raise PermanentError("MODEL_CHECKSUM_MISMATCH")

    monkeypatch.setattr(tasks, "open_text_reader", broken)
    payload = _payload(pages=1)
    _store_pages(context, payload)
    assert read_drawing_set.apply(args=[payload.model_dump(mode="json")]).state == "SUCCESS"
    doc = reading_from_json(_reading(context, payload, 0) or b"")
    assert (doc.read, len(doc.boxes)) == (False, len(PANELS))
    tasks._on_failed(payload, "RETRY_EXHAUSTED")
    assert [name for name, _ in sent] == ["drawing_set.publish", "drawing_set.publish"]


def test_one_page_failure_does_not_stop_the_set(context: LocalDiskStorage) -> None:
    """Trang vắng ảnh → không kết quả; trang có ảnh mà bộ đọc ném lỗi → khung, không chữ; trang khác vẫn đọc."""
    payload = _payload(pages=3)
    _store_pages(context, payload, skip=1)
    assert asyncio.run(read_pages(context, payload, None)) == 2
    assert _reading(context, payload, 1) is None
    asyncio.run(read_pages(context, payload, BrokenReader()))
    assert reading_from_json(_reading(context, payload, 2) or b"").read is False


def test_corrupt_page_image_is_skipped(context: LocalDiskStorage) -> None:
    """Ảnh trang hỏng (không phải PNG) → trang bỏ qua, không ném."""
    payload = _payload(pages=1)
    page = payload.pages[0]
    key = drawing_set_page(payload.project_id, payload.set_id, page.file_id, page.page_index)
    asyncio.run(context.put(key, b"rac", content_type="image/png", max_bytes=3))
    assert asyncio.run(read_pages(context, payload, FixedReader())) == 0


def test_texts_outside_image_are_dropped(context: LocalDiskStorage) -> None:
    """Chữ có hộp vượt khổ ảnh (bộ đọc lệch) bị bỏ, phần còn lại của trang vẫn ghi."""
    outside = TextPx(text="X", box=BoxPx(x_min=1390, y_min=10, x_max=1500, y_max=20), confidence=0.9)
    payload = _payload(pages=1)
    _store_pages(context, payload)
    asyncio.run(read_pages(context, payload, FixedReader((WORD, outside))))
    assert reading_from_json(_reading(context, payload, 0) or b"").texts == (WORD,)


def test_box_cap_keeps_largest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quá `BOXES_PER_PAGE_MAX` khung → giữ các khung lớn nhất, theo thứ tự đọc."""
    from packages.vision.preprocess.views import ViewBox

    many = [ViewBox(i * 10, 0, i * 10 + 5 + i, 10, 0.9) for i in range(BOXES_PER_PAGE_MAX + 2)]
    monkeypatch.setattr(tasks, "split_views", lambda gray: many)
    kept = tasks._boxes(np.zeros((10, 10), np.uint8))
    assert [b.x0 for b in kept] == [i * 10 for i in range(2, BOXES_PER_PAGE_MAX + 2)]
