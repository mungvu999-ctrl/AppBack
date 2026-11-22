# Review merge feature/k17a-bench-tools → main

- Ngày: 2026-11-22 · Reviewer: phiên /merge-review · Commit đầu nhánh: be2e13da
- Cổng: Phạm vi kiểm: đầy đủ — cổng đầy đủ trên nhánh tích hợp integ/d1-be (gồm nhánh này); `run.sh verify` mã thoát 0 (EXIT=0, 9 bước 0–8 đạt; log: C:/Users/mxuan/AppData/Local/Temp/claude/ae7f68c39ac4980e/9b83f78c-54ce-4350-8787-de9ce46ccdbf/scratchpad/vf/d1-be-full3.log)
- Độ phủ: cổng đầy đủ đạt ngưỡng 90/90 trên nhánh tích hợp; không đo riêng cho nhánh này
- Trailer `Prompt: K-17a` có trên mọi commit; tên nhánh khớp mẫu và không dính mẫu mã prompt; mã nợ NO-xxx không trùng giữa các nhánh.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | MNT | dump.py gắn cứng f14-api-1, minio:9000, trái lời "hết đường dẫn/cổng gắn cứng" | tools/bench/replay/dump.py:~24-34,110 | lấy từ config |
| 2 | P3 | MNT | 10 chỗ `# noqa` có mã (hợp lệ) và `# fmt: skip` | tools/bench/** | soát lại khi có K-11 |
| 3 | Nit | TEST | Hai commit sửa (8a316827 chuyển conftest.py thành fixtures.py nạp qua pytest_plugins; be2e13da ép int mẫu số _overlap_ratio) nhỏ, đúng, không đổi hành vi; golden không đổi | tools/bench/tests/fixtures.py, tools/bench/score/rooms.py:110 | không |

## Điểm
Không P0/P1. Tổng: 4 / 5. Nợ NO-466/467/468 do tác giả tự khai.

## PHÁN QUYẾT: APPROVE WITH COMMENTS
Diff đọc hết, có `changes/K-17a.md`, không chạm tệp cấm; cổng đầy đủ xanh.
