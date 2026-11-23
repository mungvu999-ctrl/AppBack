# Review merge fix/no566-drawing-set-read-slot → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 6fa7544f
- Cổng: verify đầy đủ 9/9 đạt trên fix/no566-drawing-set-read-slot @6fa7544f (2026-11-23 23:26)

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | OPS | Hàng `ml.infer` đọng > ~2 phút thì publish trễ (600 s) tới trước, bộ ra duyệt thiếu gợi ý trang chưa đọc; hạn 480 s tính cả thời gian chờ hàng giữa các chặng | apps/api/drawing_sets/jobs.py, apps/ml/drawing_set_read/tasks.py | Đo trên f14 khi có tải (K-32g) |
| 2 | P3 | CFG | Trần giải mã 80 Mpx do agent chọn (A1 300 dpi lọt, A0 300 dpi vẫn `IMAGE_TOO_LARGE`) | apps/api/drawing_sets/jobs.py | Chủ dự án chỉnh nếu cần |

Ba mã xếp chồng: NO-565 `de657d69` (`send_task(countdown=)` + publish trễ làm lưới an toàn; publish no-op ngoài `analyzing`, đã kiểm), NO-567 `03caed40` (`load_raster_fit`: JPEG `draft`, PNG co hộp; chặn bom trước giải mã), NO-566 `6fa7544f` (đọc theo chặng 2 trang, cùng hạn; không đổi API/hiến chương/migration). `countdown` 600 s < `celery_visibility_timeout_s` 7200 s. Một PR cho cả ba vì cùng `jobs.py`.

## PHÁN QUYẾT: APPROVE
