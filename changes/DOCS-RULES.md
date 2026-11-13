# DOCS-RULES — khôi phục luật viết mã và bảng chấm review

- Vấn đề: `RULE.md`, `RULE-CODE.md`, `DEBT.md` bị xoá 18/10/2026 (`6b2e4d52`, dọn thư mục gốc)
  nhưng `CLAUDE.md`, `docs/SETUP.md` và skill `merge-review` vẫn trỏ tới. Bước "kiểm sổ nợ"
  và bảng chấm §5 của mọi lượt `/merge-review` từ đó tới nay không có nguồn.
- Làm: khôi phục nguyên văn hai file luật vào `docs/RULE-CODE.md`, `docs/RULE.md` (thư mục gốc
  vẫn sạch theo ý của TIDY-ROOT). `DEBT.md` **không** khôi phục — nợ nay ghi ở mục
  "Nợ và việc chưa làm" của `changes/<mã prompt>.md` (thực tế đang dùng: NO-430..438).
  R-33b, R-34, R-35, R-36, R-38 và skill đổi theo.
- Chỉ sửa tài liệu, không chạm mã. Bước 1–8 của cổng không có đầu vào nào đổi.
- Nợ và việc chưa làm: `tools/ci/job.sh:316` vẫn cho `DEBT.md` đẩy thẳng lên `main` (mục chết,
  vô hại, đổi thì phải chạy lại test CI) — không sửa ở đây.

Prompt: DOCS-RULES
