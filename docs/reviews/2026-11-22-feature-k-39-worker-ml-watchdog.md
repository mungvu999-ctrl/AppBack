# Review merge feature/k-39-worker-ml-watchdog → main

- Ngày: 2026-11-22 · Reviewer: phiên /merge-review · Commit đầu nhánh: 9f97366a
- Cổng: Phạm vi kiểm: đầy đủ — cổng đầy đủ trên nhánh tích hợp integ/d1-be (gồm nhánh này); `run.sh verify` mã thoát 0 (EXIT=0, 9 bước 0–8 đạt; log: C:/Users/mxuan/AppData/Local/Temp/claude/ae7f68c39ac4980e/9b83f78c-54ce-4350-8787-de9ce46ccdbf/scratchpad/vf/d1-be-full3.log)
- Độ phủ: cổng đầy đủ đạt ngưỡng 90/90 trên nhánh tích hợp; không đo riêng cho nhánh này
- Trailer `Prompt: K-39` có trên mọi commit; tên nhánh khớp mẫu và không dính mẫu mã prompt; mã nợ NO-xxx không trùng giữa các nhánh.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | RES | healthcheck ml ping --timeout 5 (đo 3,4 s khi rảnh) có thể báo unhealthy giả khi ml tải nặng | deploy/compose/base.yml:~29 | nâng timeout lệnh ~8 s |
| 2 | P2 | TEST | NO-454 mở: chưa có biên bản thử thật ≤ 300 s | changes/K-39.md | đo trên staging (K-44) |
| 3 | P3 | MNT | ml-gpu không nằm trong APPBACK_WATCH_QUEUES; trạng thái alerts/svc-* mồ côi khi gỡ dịch vụ | deploy/scripts/healthcheck.sh | bổ sung sau |

## Điểm
Không P0/P1. Tổng: 4 / 5. Xếp chồng K-35: merge sau K-35.

## PHÁN QUYẾT: APPROVE WITH COMMENTS
Diff đọc hết, có `changes/K-39.md`, không chạm tệp cấm; cổng đầy đủ xanh.
