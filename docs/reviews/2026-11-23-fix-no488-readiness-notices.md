# Review merge fix/no488-readiness-notices → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 7c9ffa0b
- Cổng: phạm vi đầy đủ; `run.sh verify` trên nhánh gộp chore/m3b-gop-be @d69c59ee (gồm nhánh này) mã thoát 0 (log: tools-dieu-phoi/log/m3b-gop-be.log). Lượt trước đỏ 2 test do tải máy (test đo thời gian thật — đã nới; Redis timeout tạm) và 1 lỗi ruff RUF003 của chú thích — đã sửa.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | MNT | Dòng mặt cắt vẫn có `drawings`/`quality_assessments` | pipeline | NO-549 |
| 2 | P3 | LOG | Đánh dấu `isSection` sau khi lượt xong không dọn tường, phải `rerun` | pipeline | NO-548 |

Nhánh gồm K-32c (tầng mặt cắt chỉ chạy OCR, bỏ tách tường/dò đồ đạc; readiness `sectionOnly` → ready; đóng NO-481; bench không tụt) và NO-488 (người dùng duyệt 2026-11-23: `readiness.notices?` cho `OPENING_SIZE_IMPLAUSIBLE`/`ROOM_AREA_TEXT_MISMATCH`, danh sách trắng thay vì mọi kiểm không chặn — đo 96 ca; openapi chỉ thêm khoá; `APPFRONT_SHA` 16155389 đã ở AppFront master #107). Charter không mô tả readiness nên không có commit charter.

## PHÁN QUYẾT: APPROVE
