"""Mã lỗi của module bản vẽ (B2-04 [2]); mã lõi (`VALIDATION`, `FILE_CORRUPT`, …) ở
`packages.core.error_codes`, mã pipeline không phải HTTP ở `packages.core.pipeline`.
"""

from typing import Final

from packages.core.errors import ERRORS

UPLOAD_NOT_RECEIVING = ERRORS.define("UPLOAD_NOT_RECEIVING", 422)
"""#6: khúc gửi vào lượt tải đã `complete` hoặc `rejected` — không còn nhận thêm byte."""

UPLOAD_CHUNKS_CHANGED = ERRORS.define("UPLOAD_CHUNKS_CHANGED", 409)
"""#7: khúc bị ghi đè giữa lúc kiểm và lúc nối tệp; FE thử lại (`src/lib/errors/kinds.ts:108`)."""

FLOOR_DELETED: Final = "FLOOR_DELETED"
"""`error` của lượt chạy gặp tầng quá cửa sổ khôi phục hay dự án đã xoá mềm (BE-00 §7).

Hằng chuỗi, **không** `ERRORS.define`: nó không bao giờ là status của một response, chỉ là
giá trị cột `pipeline_runs.error_code` và trường `error` của `Progress`.
"""

PIPELINE_STALLED: Final = "PIPELINE_STALLED"
"""`error` của lượt chạy đã hết trần quét bù (BE-00 §7); hằng chuỗi như `FLOOR_DELETED`."""

UPLOAD_NOT_COMPLETE = ERRORS.define("UPLOAD_NOT_COMPLETE", 409)
"""N51: chạy lại lượt tải chưa `complete` (đang nhận khúc hay đã `rejected`) — chưa có bản gốc."""

UPLOAD_SUPERSEDED = ERRORS.define("UPLOAD_SUPERSEDED", 409)
"""N51: tầng đã có lượt tải `complete` mới hơn; chạy lại bản cũ sẽ thay lượt của bản mới."""

PIPELINE_RUNNING = ERRORS.define("PIPELINE_RUNNING", 409)
"""N51: lượt mới nhất của lượt tải còn `pending|running` (bấm đôi, hay đã có người chạy lại)."""

PIPELINE_NOT_FAILED = ERRORS.define("PIPELINE_NOT_FAILED", 409)
"""N51: lượt mới nhất đã `completed` — chỉ chạy lại lượt hỏng, không ghi đè kết quả đã có."""

UPLOAD_SOURCE_GONE = ERRORS.define("UPLOAD_SOURCE_GONE", 409)
"""N51: tệp gốc `original.*` không còn trong kho; người dùng phải tải lên lại (W8 không có 410)."""
