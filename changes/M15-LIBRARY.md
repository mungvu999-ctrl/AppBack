# M15-LIBRARY — #14 phân trang cursor + dấu phiên bản; gói mẫu thư viện tái tạo được

- #14 `GET /api/library` nhận `limit` (1..500) và `cursor` tuỳ chọn (hợp đồng duyệt 12/11/2026, mẫu #19): keyset `(sort_order, id)`,
  cursor ký kèm user (`CURSOR_INVALID` 422), `X-Next-Cursor` khi còn trang sau. Mọi lượt có `X-Library-Revision` = md5 của
  `(id, tên, nhóm, sort_order)` các mục người gọi thấy, cùng câu SQL với các dòng của trang. Không tham số = hành vi cũ (trọn tới
  `LIBRARY_LIST_MAX`, nay thêm `X-Next-Cursor` nếu vượt trần). Thân vẫn `LibraryItem[]`; openapi đổi (thêm 2 query, 2 header).
- CLI `build-pack <out> --from <dir>…` (`apps/api/library/pack.py`): gom `<id>.glb/.png` của `files.json`, hỏng cả lượt nếu thiếu/lệch sha;
  dựng vào thư mục tạm, chỉ đổi tên thành `out` khi xong. `migrate` chạy `import-files --strict "$LIBRARY_PACK_DIR"` sau seed khi
  biến có giá trị (gói rỗng/thiếu mục → `migrate` thất bại); `deploy/compose/library-pack.yml` gắn gói chỉ đọc.
- Không đổi DB, migration. Cổng áp dụng: lint/type, test `apps/api/library`, `deploy/tests`, bước 7–8 (openapi đổi).
- Nợ / quy trình — NO-436 (mở, P1 quy trình): PR #63 (f317c17e) merge vào `main` không qua `/merge-review`, merge commit thay vì squash,
  thiếu `changes/`; phán quyết hồi tố REJECT (quy trình), mã đạt 3,85/5 — `docs/reviews/2026-11-12-feature-m14-catalog.md`;
  `changes/M13-CATALOG.md` bổ sung trong nhánh này. Chờ người dùng ghi waiver dưới phán quyết.
- NO-437 (đóng bởi `changes/M17-CREDIT.md`, P2): ghi công CC BY 4.0 thiếu liên kết nguồn, liên kết giấy phép, ghi chú "đã chỉnh sửa" (`packages/domain/library/files.py:33-43`
  + FE `ModelCreditCard`); chưa sửa trong vòng này. Finding #4/#6 của phán quyết (trần tạm, mục `mine` vượt trần) đóng bằng phân trang ở đây.
- `DEBT.md` đã bỏ khỏi repo ở 6b2e4d52 (TIDY-ROOT) nên nợ ghi ở đây, như `changes/UX41-B1.md`.
- Duyệt hợp đồng #14: người dùng (chủ dự án) duyệt nguyên văn câu chữ dưới đây ngày 12/11/2026 trong phiên điều phối vòng 14,
  lựa chọn "Duyệt như đề xuất" (phương án bị bỏ: thân phong bì `CursorPageSchema`). Tiền lệ #19 (G1-15).
  > `GET /api/library` (#14) nhận thêm `limit` (1..500) và `cursor` tuỳ chọn. Thân vẫn là `LibraryItem[]`. Còn trang sau → header
  > `X-Next-Cursor`. Mọi trang có header `X-Library-Revision` (chuỗi mờ, đổi khi tập mục người dùng thấy đổi). Thứ tự `(sort_order, id)`
  > tăng dần, cursor ký bằng khoá `cursor`; sai/giả → 422 `CURSOR_INVALID`. Không gửi limit lẫn cursor = hành vi cũ (trả trọn tới trần
  > setting, W22). Client đọc tới hết `X-Next-Cursor`; revision khác nhau giữa các trang → đọc lại từ đầu (có giới hạn lần thử). Mẫu đã
  > lưu resolve bằng #15 `GET /api/library/{id}` (đã có, kiểm quyền) — không đổi.
