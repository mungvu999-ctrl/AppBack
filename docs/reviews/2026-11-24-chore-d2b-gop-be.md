# Review merge chore/d2b-gop-be → main

- Ngày: 2026-11-24 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 58c0a53b
- Cổng: verify đầy đủ 9/9 đạt trên chore/d2b-gop-be @58c0a53b (2026-11-24 10:56)

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P3 | OPS | K-41 chưa kiểm sống trên nginx f14 (`nginx -t`, curl, đếm log) | deploy/nginx/snippets/app_locations.conf | Làm khi dựng lại ảnh web lần tới |
| 2 | P3 | CFG | L6-N01: môi trường đang bật design director sẽ tự tắt tới khi đặt `DESIGN_DIRECTOR_DISCLOSURE_ACK=true`; worker không kiểm `available` | apps/api/design_director/settings.py | Ghi trong changes/L6-N01.md |

Gộp bốn mã Đ2 đợt b: K-41 `65391e09` (token chia sẻ không vào log nginx), L6-N06 `994cf695`+`f123d4fd` (mở liên kết công khai không trả địa chỉ), L6-N01 `75a9059d` (cần xác nhận đã thông báo trước khi gửi ảnh tới Vertex), L2-N05 `b9fbbfc6` (ghi chú + test ghim mặc định đồ đạc, không đổi hành vi). Đường `/api/share/{token}` và `/share/:token` đã đối chiếu với route BE/FE. Không đổi hợp đồng, không migration.

## PHÁN QUYẾT: APPROVE
