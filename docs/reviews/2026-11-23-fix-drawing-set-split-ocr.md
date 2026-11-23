# Review merge fix/drawing-set-split-ocr → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 279e894a
- Cổng: verify đầy đủ 9/9 đạt trên nhánh gộp chore/m3c-gop-be @4b39eee3 (2026-11-23 22:22)

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | ACC | Bộ tầng đúng mới 2/10; còn nợ cắt hình | packages/vision/preprocess/views.py | NO-564..568 |

Cắt hình tại đường khung chung và khe trống. Bộ thử: số hình đúng 56→76/102, độ chính xác 67 %→76 %; bộ đối chứng 56→65/84. "Sai mà không hỏi" vẫn 0. Chỉ chạm `packages/vision` (numpy/cv2 khai ở đó), không route/DB/hợp đồng.

## PHÁN QUYẾT: APPROVE
