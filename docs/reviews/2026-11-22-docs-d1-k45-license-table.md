# Review merge docs/d1-k45-license-table → main

- Ngày: 2026-11-22 · Reviewer: phiên /merge-review · Commit đầu nhánh: a028d9a8
- Cổng: Phạm vi kiểm: đầy đủ — cổng đầy đủ trên nhánh tích hợp integ/d1-be (gồm nhánh này); `run.sh verify` mã thoát 0 (EXIT=0, 9 bước 0–8 đạt; log: C:/Users/mxuan/AppData/Local/Temp/claude/ae7f68c39ac4980e/9b83f78c-54ce-4350-8787-de9ce46ccdbf/scratchpad/vf/d1-be-full3.log)
- Độ phủ: cổng đầy đủ đạt ngưỡng 90/90 trên nhánh tích hợp; không đo riêng cho nhánh này
- Trailer `Prompt: K-45` có trên mọi commit; tên nhánh khớp mẫu và không dính mẫu mã prompt; mã nợ NO-xxx không trùng giữa các nhánh.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | LOG | Bảng chưa quét từng gói uv.lock, SegFormer chưa đọc nguyên văn, torchvision chưa mở nguồn (NO-463) | docs/legal/THIRD-PARTY-LICENSES.md | bổ sung lượt sau; không phải tư vấn pháp lý |
| 2 | P3 | MNT | SOURCE_LICENSE lệch Zenodo (CC BY-NC-SA 4.0) (NO-464) | apps/worker/datasets_cubicasa/importer.py:114 | sửa chuỗi ở lượt riêng |

## Điểm
Không P0/P1. Tổng: 4 / 5. Chỉ tài liệu, cổng không áp dụng trực tiếp.

## PHÁN QUYẾT: APPROVE WITH COMMENTS
Diff đọc hết, có `changes/K-45.md`, không chạm tệp cấm; cổng đầy đủ xanh.
