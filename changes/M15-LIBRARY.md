# M15-LIBRARY — #14 phân trang cursor + dấu phiên bản; gói mẫu thư viện tái tạo được

- #14 `GET /api/library` nhận `limit` (1..500) và `cursor` tuỳ chọn (hợp đồng duyệt 12/11/2026, mẫu #19): keyset `(sort_order, id)`,
  cursor ký kèm user (`CURSOR_INVALID` 422), `X-Next-Cursor` khi còn trang sau. Mọi lượt có `X-Library-Revision` = md5 của
  `(id, tên, nhóm, sort_order)` các mục người gọi thấy, cùng câu SQL với các dòng của trang. Không tham số = hành vi cũ (trọn tới
  `LIBRARY_LIST_MAX`, nay thêm `X-Next-Cursor` nếu vượt trần). Thân vẫn `LibraryItem[]`; openapi đổi (thêm 2 query, 2 header).
- CLI `build-pack <out> --from <dir>…` (`apps/api/library/pack.py`): gom `<id>.glb/.png` của `files.json`, hỏng cả lượt nếu thiếu/lệch sha.
  `migrate` chạy `import-files "$LIBRARY_PACK_DIR"` sau seed khi biến có giá trị; `deploy/compose/library-pack.yml` gắn gói chỉ đọc.
- Không đổi DB, migration. Cổng áp dụng: lint/type, test `apps/api/library`, `deploy/tests`, bước 7–8 (openapi đổi).
- Nợ / quy trình — NO-436 (mở, P1 quy trình): PR #63 (f317c17e) merge vào `main` không qua `/merge-review`, merge commit thay vì squash,
  thiếu `changes/`; phán quyết hồi tố REJECT (quy trình), mã đạt 3,85/5 — `docs/reviews/2026-11-12-feature-m14-catalog.md`;
  `changes/M13-CATALOG.md` bổ sung trong nhánh này. Chờ người dùng ghi waiver dưới phán quyết.
- NO-437 (mở, P2): ghi công CC BY 4.0 thiếu liên kết nguồn, liên kết giấy phép, ghi chú "đã chỉnh sửa" (`packages/domain/library/files.py:33-43`
  + FE `ModelCreditCard`); chưa sửa trong vòng này. Finding #4/#6 của phán quyết (trần tạm, mục `mine` vượt trần) đóng bằng phân trang ở đây.
- `DEBT.md` đã bỏ khỏi repo ở 6b2e4d52 (TIDY-ROOT) nên nợ ghi ở đây, như `changes/UX41-B1.md`.
