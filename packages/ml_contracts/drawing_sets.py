"""Hợp đồng worker `ml` ↔ tác vụ bộ hồ sơ (K-32a PR(3)): payload `ml.infer.drawing_set.read` và kết quả đọc trang.

`drawing_set.analyze` (default) dựng ảnh trang rồi gửi `DrawingSetReadPayload`; worker `ml` tách khung + OCR từng
trang, ghi `PageReadingDoc` vào `…/files/{f}/readings/{i}.json` rồi gửi `drawing_set.publish` (default). Worker
`ml` không được tin (BE-00 §9): kết quả đọc lại có trần byte và kiểm hợp đồng như mọi artifact ML; hỏng thì trang
đó coi như chưa đọc, không làm hỏng cả bộ.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from packages.core.ids import check_id
from packages.ml_contracts.artifacts import (
    ARTIFACT_JSON_MAX_BYTES,
    MASK_MAX_PIXELS,
    MAX_TEXTS,
    Confidence,
    FrozenModel,
    TextPx,
)
from packages.ml_contracts.payloads import MlPayload, ModelRef

DRAWING_SET_READ_TASK: Final = "ml.infer.drawing_set.read"
"""Tách khung + OCR các trang của một bộ (`apps/ml/drawing_set_read`); tiền tố `ml.infer.` → hàng `ml.infer`."""
READ_PAGES_MAX: Final = 40
"""Trần trang một lượt đọc = trần trang của cả bộ (K-32a §3: 40 trang/bộ)."""
PAGE_INDEX_MAX: Final = 19
"""Chỉ số trang lớn nhất của một PDF (20 trang/tệp, khớp `PAGE_INDEX_MAX` của #5)."""
BOXES_PER_PAGE_MAX: Final = 60
"""Trần khung gợi ý một trang (= trần hình một bản duyệt N59)."""
READ_BUDGET_S: Final = 480.0
"""Trần OCR một lượt đọc (8 phút), kể từ lúc chặng đầu bắt đầu, đo bằng đồng hồ tường qua mọi chặng (NO-566,
`deadline_epoch_s`): cộng dựng trang và hàng chờ vẫn dưới mốc 15 phút mà N57 coi là
treo (`drawing_set_analysis_stall_s`). Đo trên tờ DS- tới 24 Mpx: 4-32 s/tờ (`changes/K-32a.md`), tức ~15-40 tờ/lượt.
Ở hợp đồng chung vì worker `default` cũng cần nó để hẹn `drawing_set.publish` trễ làm lưới an toàn (NO-565)."""

DrawingSetId = Annotated[str, AfterValidator(lambda value: check_id("dws", value))]
ProjectId = Annotated[str, AfterValidator(lambda value: check_id("prj", value))]
SetFileId = Annotated[str, AfterValidator(lambda value: check_id("dwf", value))]
PageIndex = Annotated[int, Field(ge=0, le=PAGE_INDEX_MAX)]


class PageRef(FrozenModel):
    """Một trang của bộ: tệp và chỉ số trang trong tệp."""

    file_id: SetFileId
    page_index: PageIndex


class DrawingSetReadPayload(MlPayload):
    """`ml.infer.drawing_set.read`: các trang đã có ảnh và model đọc chữ đang kích hoạt (họ `dimensionReading`).

    Khoá ảnh trang và khoá kết quả do worker dựng lại từ id (`packages.storage.keys`), payload không mang khoá.

    Lượt đọc chạy thành **chuỗi chặng** (NO-566): mỗi task đọc vài trang đầu của `pages` rồi tự gửi task kế với
    các trang còn lại, để không task nào giữ một khe `ml.infer` cả lượt. `deadline_epoch_s` là hạn OCR của cả lượt
    (giây epoch UTC) do chặng đầu đặt (`None` = chặng đầu) và chuyền nguyên qua các chặng sau.
    """

    project_id: ProjectId
    set_id: DrawingSetId
    pages: Annotated[tuple[PageRef, ...], Field(min_length=1, max_length=READ_PAGES_MAX)]
    model: ModelRef
    deadline_epoch_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _text_model(self) -> Self:
        """Model phải thuộc họ đọc chữ; trang không trùng nhau."""
        if self.model.family != "dimensionReading":
            raise ValueError("model phải thuộc họ dimensionReading")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("trang trùng nhau")
        return self


class ReadingBox(FrozenModel):
    """Một khung máy gợi ý trên trang (điểm ảnh của ảnh trang, `x1`/`y1` loại trừ) và độ chắc của nó."""

    x0: Annotated[int, Field(ge=0)]
    y0: Annotated[int, Field(ge=0)]
    x1: Annotated[int, Field(ge=1)]
    y1: Annotated[int, Field(ge=1)]
    score: Confidence

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        """Khung rỗng hay lật ngược là đầu ra sai hợp đồng."""
        if not (self.x0 < self.x1 and self.y0 < self.y1):
            raise ValueError("khung phải có x0 < x1 và y0 < y1")
        return self


class PageReadingDoc(FrozenModel):
    """Kết quả đọc một trang (`…/files/{f}/readings/{i}.json`): khổ ảnh, khung gợi ý, chữ OCR cả trang.

    `read=False`: OCR không chạy cho trang này (hết thời gian của lượt) — khung vẫn có. Khung và hộp chữ phải
    nằm trong khổ ảnh; vượt là đầu ra sai hợp đồng.
    """

    schema_version: Literal[1] = 1
    width_px: Annotated[int, Field(ge=1)]
    height_px: Annotated[int, Field(ge=1)]
    boxes: Annotated[tuple[ReadingBox, ...], Field(max_length=BOXES_PER_PAGE_MAX)]
    texts: Annotated[tuple[TextPx, ...], Field(max_length=MAX_TEXTS)]
    read: bool

    @model_validator(mode="after")
    def _inside(self) -> Self:
        """Khổ trong trần điểm ảnh; mọi khung và hộp chữ nằm trong ảnh."""
        if self.width_px * self.height_px > MASK_MAX_PIXELS:
            raise ValueError(f"khổ trang vượt {MASK_MAX_PIXELS} điểm")
        if any(b.x1 > self.width_px or b.y1 > self.height_px for b in self.boxes):
            raise ValueError("khung vượt khổ trang")
        if any(t.box.x_max > self.width_px or t.box.y_max > self.height_px for t in self.texts):
            raise ValueError("hộp chữ vượt khổ trang")
        return self


def reading_to_json(doc: PageReadingDoc) -> bytes:
    """JSON gọn của kết quả đọc một trang."""
    return doc.model_dump_json().encode()


def reading_from_json(data: bytes) -> PageReadingDoc:
    """Giải kết quả đọc; quá `ARTIFACT_JSON_MAX_BYTES` hay sai hợp đồng → `ValueError` (kể cả `ValidationError`)."""
    if len(data) > ARTIFACT_JSON_MAX_BYTES:
        raise ValueError(f"kết quả đọc dài {len(data)} byte, trần {ARTIFACT_JSON_MAX_BYTES}")
    return PageReadingDoc.model_validate_json(data)
