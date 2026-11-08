"""Mã lỗi riêng của đường ghi lớp (B3-03 [2]); mã lõi ở `packages.core.error_codes`.

Ba mã này thay cho `VALIDATION` vì FE xử lý khác nhau: A5 và tầng sai là lỗi của **một
mục** (FE trỏ vào mục qua `field`), còn toàn vẹn là lỗi của cả lớp (FE chỉ đếm được).
`write_layer` ném chúng trong ngữ cảnh worker, nên module này không nhập gì của HTTP.
"""

from packages.core.errors import ERRORS

REVIEW_BY_AI_FORBIDDEN = ERRORS.define("REVIEW_BY_AI_FORBIDDEN", 422)
"""A5 (W5): một mục `source="ai"` mang `reviewed=True`; ném kèm `field` trỏ mục đầu tiên."""

LAYER_LEVEL_MISMATCH = ERRORS.define("LAYER_LEVEL_MISMATCH", 422)
"""`levelId` của một mục khác tầng đang ghi; ném kèm `field` trỏ mục đầu tiên."""

LAYER_INTEGRITY_BROKEN = ERRORS.define("LAYER_INTEGRITY_BROKEN", 422)
"""Toàn vẹn mức `critical`, hoặc id đã thuộc tầng khác cùng dự án; ném kèm `count`."""

SUGGESTION_UNKNOWN = ERRORS.define("SUGGESTION_UNKNOWN", 422)
"""`suggestionState` nêu một id đề xuất tầng không có (M2); ném kèm `field` trỏ dòng đầu tiên."""

SUGGESTION_OVERRIDE_INVALID = ERRORS.define("SUGGESTION_OVERRIDE_INVALID", 422)
"""Khoá `override` ngoài danh sách `overridable` của luật (M2); ném kèm `field` trỏ dòng đầu tiên."""

SUGGESTION_CAP = ERRORS.define("SUGGESTION_CAP", 422)
"""Tầng vượt trần 2 000 đề xuất kể cả sau khi dọn bản cũ không có dữ liệu người dùng (M2 §3 bước 5); kèm `count`."""
