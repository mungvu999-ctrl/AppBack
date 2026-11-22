# Review merge feature/bench-replay-gate → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh: 9643ffed9ef0 (xếp chồng trên K-11 d63042e1)
- Cổng: phạm vi đầy đủ; `run.sh verify` trên integ/d1b-be, mã thoát 0, 9/9 bước
- Độ phủ: theo cổng đầy đủ đạt

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 (đã có nợ) | TEST/OBS | Job CI luôn 'bỏ qua, xanh' vì dữ liệu bench ngoài repo; chưa chặn được gì, chưa là check bắt buộc | .github/workflows/bench.yml, changes/K-17b.md:45 (NO-468) | — |
| 2 | P3 | TEST | Baseline chụp trong ảnh f14 (Linux), CI dùng uv sync: có thể lệch số; ngưỡng 2 ca cần đo khi có dữ liệu | tools/bench/baseline/main.json | Ghi nhận |

## PHÁN QUYẾT: APPROVE WITH COMMENTS
Không P0/P1; P2 đã có nợ NO-468. Merge sau K-11 (nhánh chứa K-11).
