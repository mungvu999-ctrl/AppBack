# Review merge feature/k65-title-block-wire → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): a91f7bab
- Cổng: verify đầy đủ 9/9 đạt trên nhánh gộp chore/m3c-gop-be @4b39eee3 (2026-11-23 22:22)

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | TEST | Khung tên trên dây chỉ đúng tới đâu độ đúng tách hình cho phép; chưa đo trên bản quét | changes/K-65.md | NO-515..517 |

Đưa `sheetNo`, `titleBlock` (kiểu mới `DrawingSetTitleBlockOut`) lên `DrawingSetOut`/`DrawingSetViewOut`; N59 nhận `name` để đổi tên bộ. Hiến chương đợt 2 (BE-BIND N57/N59, HOP-DONG-MOI §14, BE-00 §8) có trailer duyệt. openapi sinh lại, so khác chỉ thêm khoá tuỳ chọn; APPFRONT_SHA 23d3fe33 (AppFront #108 đã gộp, schema FE nhận khoá mới trước).

## PHÁN QUYẾT: APPROVE
