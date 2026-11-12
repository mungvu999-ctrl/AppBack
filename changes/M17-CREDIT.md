# M17-CREDIT — ghi công CC BY đủ TASL (đóng NO-437)

- `FileItem.attribution` của mẫu CC BY (891/978 mục) nay là một dòng nối bằng `" · "`: `[“tên gốc”] · tác giả · giấy phép ·
  URL giấy phép CC · URL nguồn… · đã chỉnh sửa: chuyển đổi, chuẩn hoá tệp GLB`. CC0 và giấy phép khác giữ `"<ai> · <giấy phép>"`.
- Không đổi kiểu dây: `LibraryItemOut.attribution` vẫn `string | null`, `openapi.json` không đổi. Đổi nội dung chuỗi; FE
  (`ModelCreditCard`) tách theo `" · "` và hiện phần URL thành liên kết.
- Không đổi DB, migration. Cổng áp dụng: lint/type, test `packages/domain/library`, `apps/api/library`.
- "CC-BY" không ghi bản (7 mục bản demo Blender) trỏ `https://creativecommons.org/licenses/` — cần kiểm lại bản giấy phép.
