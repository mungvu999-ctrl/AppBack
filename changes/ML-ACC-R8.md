# ML-ACC-R8 — hồ sơ review: sửa GEOSException ở `rooms.py::_merge_fragments` (commit 3f27d3a8, mã R5)

- Vấn đề: `build_layer` ném `GEOSException` ("Unable to determine overlay result geometry dimension", GEOS 3.13) tại `score = shared.intersection(weak).length` trên cis-429 (cả baseline lẫn `rect="fill"`, lỗi có sẵn ở prod) và cvcfp-IIa_SL0801 (chỉ `fill`).
- Nguyên nhân gốc: mảnh nhỏ chạm phòng kề theo một cạnh **và** một điểm rời → `shared` là GeometryCollection lẫn đường + điểm; không có đoạn nối khe dài nên `weak` rỗng; OverlayNG của GEOS 3.13 assert khi giao GC lẫn chiều với hình rỗng. Đa giác đều hợp lệ.
- Cách sửa: `_shared_edges(a, b)` chỉ giữ phần đường (`get_dimensions == 1`) của giao hai viền, trả `MultiLineString`; điểm không có độ dài nên mọi điểm số (giao `weak`, hiệu thân tường, `shared.length`) giữ nguyên. Không nuốt ngoại lệ. +12 dòng `rooms.py`.
- Phạm vi KHÔNG gồm: detector/model, OCR, tỉ lệ, ngưỡng, API/DB/OpenAPI/hợp đồng, phụ thuộc. Chỉ `rooms.py` + test.
- Test hồi quy: `test_rooms.py::test_merge_fragments__edge_plus_point_contact_without_long_closers[cis-429|cvcfp-IIa_SL0801-fill]` (hai cặp phòng thật, toạ độ inline). Trước sửa 2 failed (GEOSException); sau sửa 2 passed, cả `apps/worker/pipeline_build/tests` 850 passed.
- Bằng chứng (ML-ACC R5/R6, ngoài repo): chạy lại 18 ảnh x 2 chế độ = 36 ca; cis-429 (2 chế độ) và SL0801-fill từ CRASH -> completed; 36/36 giống hệt trước sửa sau khi bỏ ID ngẫu nhiên (layer, readiness, dropped, scale, quality). Nguồn: `F:/runs/mlr-next/r5-rooms/compare.txt`, `docs/ml-accuracy-research-next/evidence/R5-rooms-fix.txt`, `RESULTS-vong6.md`.
- Rủi ro: thấp. Chỉ đổi cách tính khi `shared` có thành phần điểm; điểm vốn có độ dài 0. Khi hợp hai phòng có lỗ thì luật sẵn có ("hợp không thành đa giác đặc thì bỏ qua") vẫn giữ cả hai phòng. Chưa kiểm GEOS khác 3.13.1 (chỉ f14 image).
- Nợ: không phát sinh nợ mới. Không chạy cổng đầy đủ tại chỗ (CI chạy khi mở PR).
