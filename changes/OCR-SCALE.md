# OCR-SCALE — tỉ lệ đọc từ số đo trên bản vẽ không bắt xác nhận

- Hạng tỉ lệ mới `measured` (`floor_documents.scale_source`, migration nới CHECK): lượt dựng có `scaleBasis=chain` (≥ 3 số đo đồng thuận) và không nghi vấn `scaleChecks` (`BuiltLayer.persisted_scale_source`). `layer.json` giữ `scaleSource=pipeline`.
- N16: `measured` → vắng `scaleStatus`, `scaleSource`; `scaleBasis` vẫn `chain`. Hình dạng dây không đổi, không cần cập nhật `openapi.json`.
- `paired` (2 số), `note`, ước lượng cửa/tường, mặc định: giữ nguyên, vẫn cần người kiểm.
- Nghi vấn mới `scaleChecks.implausibleWalls`: tường trung vị ngoài 50-500 mm hay khung < 3 m → giữ `pipeline`. Cần vì FE bỏ qua kiểm "tỉ lệ vô lý" khi không có `scaleStatus`.
- Trang đổi → `measured` hạ về `pipeline` như `human`. Người đổi tỉ lệ `measured` → co giãn cả mục đã duyệt như tỉ lệ tạm.
- Tầng cũ không backfill, thành `measured` khi chạy lại pipeline.
