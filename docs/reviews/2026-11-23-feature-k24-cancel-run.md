# Review merge feature/k24-cancel-run → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): bc006d3e
- Cổng: phạm vi đầy đủ; `run.sh verify` trên nhánh gộp chore/d2a-gop-be @71180987+ (gồm nhánh này) mã thoát 0, 9/9 bước đạt, 11577 test qua (log: scratchpad/vf/d2a-be2.log). Lượt trước đỏ 2 test (đếm dòng charter, kỳ vọng e2e cũ) — đã sửa trong nhánh.
- Độ phủ: `coverage_gate: đạt`; `case_gate` đạt; bench gate K-17b xanh (agent chạy, số không tụt).

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | TEST | Chưa bấm thử đầu-cuối trên f14 | — | NO-496 |
| 2 | P3 | RES | Đoạn ML/CPU không có điểm kiểm chưa đo thời lượng | apps/ml | NO-497, NO-500 |

Kiểm đặc biệt: `cancel_upload_runs` khoá `floors` → `pipeline_runs` cùng thứ tự `start_run`/`record_step`; lượt sống → `failed` `PIPELINE_CANCELLED`, kết quả muộn bị `_usable` bỏ; cờ Redis cắm sau commit, lỗi Redis mở (chỉ tối ưu). Tầng về `completed`/`none`, không kẹt. Route `floor.upload`, idempotency auto, luôn 200. Charter commit 76fc72c7 chỉ chạm `docs/charter/*` + `changes/K-24.md`, trailer `Charter-Approved: changes/K-24.md "Người dùng duyệt 2026-11-23"` trỏ dòng có thật. openapi sinh bằng `run.sh openapi`; `APPFRONT_SHA` = e298eecf, đã có trong AppFront master (PR #105).

## PHÁN QUYẾT: APPROVE
