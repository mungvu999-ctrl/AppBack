# K-58 (bước 6, phần AppBack) — workflow `stale`

- `.github/workflows/stale.yml`: `actions/stale` v11.0.0 ghim SHA `4391f3da…`; hằng ngày gắn nhãn `stale` cho PR im 14 ngày,
  `days-before-close: -1` (không tự đóng), bỏ qua issue và PR nhãn `keep-open`; quyền workflow rỗng,
  job chỉ `pull-requests: write`.
- `tools/ci/tests/test_workflows.py`: hai test (ghim SHA + quyền tối thiểu; chỉ gắn nhãn).
- Chờ chủ dự án: lần chạy đầu sau khi vào `main` sẽ gắn nhãn hàng loạt cho PR cũ (`workflow_dispatch` có sẵn).
