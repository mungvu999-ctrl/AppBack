# Review merge feature/k32d-floor-stacking → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 8743f9be
- Cổng: phạm vi đầy đủ; `run.sh verify` trên nhánh gộp chore/m3-gop-be @ea89ced9 (gồm nhánh này) mã thoát 0, 12013 test qua, bước 7 hợp đồng đạt (log: tools-dieu-phoi/log/m3-gop-be.log). Lượt `--fast` trước đỏ bước 7 (resource 404 mới) — đã sửa AppFront #106.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | MNT | `register_v` trùng thuật toán với `sections.side_levels` (HS-03) | packages/vision/stacking | NO-526 |

Đã chuyển từ `packages/domain` sang `packages/vision/stacking` (commit 8743f9be) vì cần numpy/opencv — domain trên main không nhập hai thư viện này; vision đã khai sẵn, không đổi `uv.lock` (đóng NO-521). Khớp từng trường với đầu ra nghiên cứu trên 6 cặp tầng + 4 mặt cắt thật; 71 test, phủ 99 %.

## PHÁN QUYẾT: APPROVE
