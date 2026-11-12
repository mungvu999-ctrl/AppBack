# M13-CATALOG — trọn danh mục ở #14, thêm 95 mẫu CC BY (hồi tố cho PR #63, 1ec29adb / f317c17e)

- `LIBRARY_LIST_MAX` mặc định 500 → 2000 (`apps/api/library/settings.py`): #14 trả mảng một lần, cắt im lặng ở `LIMIT`, danh
  mục 994 mục (16 dựng bằng mã + 978 từ tệp) nên mẫu xếp sau vị trí 500 không bao giờ tới editor. Đây là **giải pháp tạm**;
  sửa gốc là phân trang cursor của M15-LIBRARY.
- `test_list_cap.py` giữ danh mục hệ thống dưới trần (CI đỏ trước khi cắt im lặng quay lại).
- `files.json` +95 mẫu CC BY 4.0: 78 Amazon Berkeley Objects (sofa, bàn ghế ăn, tủ áo, tab đầu giường), 17 giường
  Objaverse/Sketchfab; tác giả và nguồn từng mục, `model_sha256` ghim tệp đã chuẩn hoá.
- Rollback: **không** hạ trần lại dưới tổng số mục — mẫu đã lưu xếp sau vị trí 500 sẽ không còn trong danh sách.
- Không đổi API, DB, migration, hợp đồng FE–BE. Phán quyết hồi tố: `docs/reviews/2026-11-12-feature-m14-catalog.md`.
