# ML-ACC-R10 (lần C, BE) — hồ sơ review: `scaleChecks` hỏng dạng không được tin hơn

- Vấn đề: `scale_state_of` coi `quality_summary.scaleChecks` không phải list là rỗng và bỏ phần tử không phải chuỗi, nên với gốc `chain`/`paired` dữ liệu hỏng lại ra `determined` (tin hơn khi dữ liệu xấu).
- Cách sửa: `scaleChecks` không phải danh sách chuỗi (chuỗi, số, dict, bool, list lẫn phần tử khác chuỗi) → `("unresolved", ("basisUnknown",))`. Vắng khoá, `null`, `[]` giữ nguyên (hợp lệ). `readiness_of` dùng chung hàm nên `scaleUnresolved` đi theo.
- Phạm vi KHÔNG gồm: không đổi `scaleStatus`/`scaleBasis`/số mm/px; không đổi hợp đồng OpenAPI (chỉ mô tả `scaleReasons` trong `wire.py`).
- Test: `test_readiness.py::test_scale_state_of_table` (+7 ca), tính chất 128 tổ hợp vẫn đúng; `test_routes_scale_state.py` (+1 ca HTTP `chain-malformed`).
- Nợ: không có.
