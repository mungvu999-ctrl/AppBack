# PERF-12 — bước 5b chạy test perf trên đúng các tệp đã collect

- `tools/verify/steps.py` `step_perf`: lượt chạy pytest perf nhận danh sách **tệp** chứa test perf (lấy từ lượt
  `--collect-only` ngay trước) thay vì cả thư mục đơn vị. Trước: lượt chạy import và collect lại toàn bộ đơn vị
  (log 23/11: `14 passed, 5483 deselected in 58.13s`); nay chỉ import vài tệp perf. Kết quả, junit, `case_gate` không đổi.
- Test: `tools/tests/test_steps_commands.py` (ba ca 5b kiểm lệnh chạy theo tệp).
- Không đổi hợp đồng, DB, CI. Nợ mới: không.

## Luật cổng (R-33b)
- Người dùng duyệt 2026-11-23: ngoại lệ "thay đổi nhỏ" (verify --fast + CI) và "gom nhánh" của R-33b, câu chữ theo `docs/dieu-tra-20261122/charter-de-xuat/R-33b.md`. PR này là lượt đầu áp ngoại lệ: 4 dòng logic, test của `steps.py` chạy trong `--fast`.
