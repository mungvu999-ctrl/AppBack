# PERF-12 — bước 5b chạy test perf trên đúng các tệp đã collect

- `tools/verify/steps.py` `step_perf`: lượt chạy pytest perf nhận danh sách **tệp** chứa test perf (lấy từ lượt
  `--collect-only` ngay trước) thay vì cả thư mục đơn vị. Trước: lượt chạy import và collect lại toàn bộ đơn vị
  (log 23/11: `14 passed, 5483 deselected in 58.13s`); nay chỉ import vài tệp perf. Kết quả, junit, `case_gate` không đổi.
- Test: `tools/tests/test_steps_commands.py` (ba ca 5b kiểm lệnh chạy theo tệp).
- Không đổi hợp đồng, DB, CI. Nợ mới: không.
