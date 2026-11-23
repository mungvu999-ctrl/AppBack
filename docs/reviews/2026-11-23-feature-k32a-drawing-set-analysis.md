# Review merge feature/k32a-drawing-set-analysis → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): aa120178
- Cổng: phạm vi đầy đủ; `run.sh verify` trên nhánh gộp chore/m3b-gop-be @d69c59ee (gồm nhánh này) mã thoát 0 (log: tools-dieu-phoi/log/m3b-gop-be.log). Lượt trước đỏ 2 test do tải máy (test đo thời gian thật — đã nới; Redis timeout tạm) và 1 lỗi ruff RUF003 của chú thích — đã sửa.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | LOG | Độ đúng phân tích còn thấp (56/102 hình, 0/10 bộ đúng tập tầng); công tắc mặc định tắt | changes/K-32a.md | NO-543, NO-546 (đang nâng ở nhánh sau) |
| 2 | P3 | RES | Mất lượt đọc thì bộ treo tới chốt 15 phút | apps/api/drawing_sets | NO-547 |

Thay bản tạm bằng 3 task nối nhau (analyze → ml.infer.drawing_set.read → publish), không route/migration/đổi dây. Sửa ngoài sở hữu: `apps/ml/text/tasks.py` (tách `open_text_reader` công khai), `packages/ml_contracts/drawing_sets.py`, payload messaging, khoá kho `drawing_set_reading`, `deploy/minio/ml-policy.json` + test. case_gate J01/J03/J05/J06 cho 3 task. Test khung tên đo thời gian thật nới lên 10 s (tránh flake khi cổng song song).

## PHÁN QUYẾT: APPROVE
