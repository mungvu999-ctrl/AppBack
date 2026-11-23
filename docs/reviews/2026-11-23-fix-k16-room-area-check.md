# Review merge fix/k16-room-area-check → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 6aee4549
- Cổng: phạm vi đầy đủ; `run.sh verify` trên nhánh gộp chore/d2a-gop-be @71180987+ (gồm nhánh này) mã thoát 0, 9/9 bước đạt, 11577 test qua (log: scratchpad/vf/d2a-be2.log). Lượt trước đỏ 2 test (đếm dòng charter, kỳ vọng e2e cũ) — đã sửa trong nhánh.
- Độ phủ: `coverage_gate: đạt`; `case_gate` đạt; bench gate K-17b xanh (agent chạy, số không tụt).

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | MNT | Hai kiểm mới (`OPENING_SIZE_IMPLAUSIBLE`, `ROOM_AREA_TEXT_MISMATCH`) chỉ vào `quality.json`, người dùng chưa thấy | pipeline_quality | Đã ghi NO-488 |

Ngưỡng có căn cứ số đo bench (`changes/K-15.md`, `changes/K-16.md`): cửa đi [550; 3600] mm, 0/48 ca ĐẠT bị báo nhầm; diện tích dung sai 15 %, 6 ca bật đều xem tay. Khoá mới `areaLabels` trong layer.json tuỳ chọn, tài liệu cũ đọc được. Không route/DB/hợp đồng.

## PHÁN QUYẾT: APPROVE
