# BENCH3D-FURN-ON — nhận diện đồ đạc mặc định bật

- `detectFurniture` của cài đặt dự án (N5/N6) nay mặc định `true`: dự án chưa có dòng cài đặt và dòng tạo mới đều dựng đồ AI.
- Migration `r20261103_furn_default_on`: đổi `server_default` của `project_settings.detect_furniture` sang `true`; dòng đã lưu giữ nguyên.
- Người dùng vẫn tắt được ở màn tải lên; có hiệu lực từ lượt xử lý sau.
