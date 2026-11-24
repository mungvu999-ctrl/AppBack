# Review merge chore/verify-perf-reuse-collect → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh: 2ea114c1
- Phạm vi kiểm: fast + CI — thay đổi nhỏ (4 dòng logic `tools/verify/steps.py`; test của tệp chạy trong `--fast`), theo ngoại lệ R-33b người dùng duyệt 2026-11-23 (chính nhánh này đưa ngoại lệ vào). `verify --fast` mã thoát 0 (bước 1–5, 46 tệp test, cov `tools`). CI: điều kiện merge là mọi check bắt buộc xanh.

## Finding
Không có. Lệnh 5b nay nhận tệp từ node id đã collect (`split("::")[0]`, khử trùng, sắp xếp) — cùng tập test, cùng `-m`, cùng junit; `case_gate` không đổi. Câu chữ R-33b khớp bản duyệt `docs/dieu-tra-20261122/charter-de-xuat/R-33b.md`.

## PHÁN QUYẾT: APPROVE
Điều kiện: CI xanh mọi check bắt buộc trước khi merge.
