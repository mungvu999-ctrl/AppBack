# PERF-13 — cổng nhanh của nhánh xếp chồng so với lát trước, không so với `main`

- Vấn đề (đo 2026-11-26, chuỗi NHA 7 lát chưa vào `main`): `run.sh` tính tệp "đổi" từ gốc chung với `main`, nên lát nào
  trong chuỗi cũng thấy migration, `docs/contracts/openapi.json` và `tools/contract/*` của các lát trước là của mình →
  `verify --fast` chạy cả bộ (12.630 test, 19 phút) cho một lát chỉ sửa vài tệp. Ví dụ `nha-05b`: 39 tệp "đổi" so với
  `main`, 7 tệp so với lát trước nó.
- `tools/verify/run.sh`: với `verify --fast`, gốc so sánh tiến tới đầu nhánh `origin/*` gần nhất đã nằm trọn trong HEAD
  (`git for-each-ref --merged HEAD` + `git merge-base --all`, cùng mẹo PERF-11), trừ nhánh cùng tên với nhánh đang đứng;
  nhánh đã gộp thêm `main` hay nhánh khác có nhiều gốc ngang hàng thì lấy gốc cho ít tệp đổi nhất. In
  một dòng "cổng nhanh: so với lát trước đã đẩy …" khi gốc khác `main`. `verify` đầy đủ, `--steps` và CI không đổi.
- Test: `tools/tests/test_run_sh.py::test_verify_fast__stacked_branch_compares_with_its_pushed_parent`.
- Tài liệu: `CLAUDE.md` (dòng `verify --fast`), `tools/verify/README.md`, docstring `steps._changed_files`.
- Không đổi hợp đồng, DB, CI. Nợ mới: không.

## Không đổi (cố ý)
- Lát **tự nó** đổi `docs/contracts/`, `tools/contract/`, migration hay hạ tầng test vẫn chạy cả bộ: bước 7 cần mẫu
  golden của mọi thao tác (`_missing_success`), chỉ cả bộ mới ghi đủ.

## Giới hạn
- Phần của chính mình đã đẩy dưới **tên nhánh khác** bị coi là lát trước: cổng nhanh chỉ kiểm phần chưa đẩy. Cổng đầy
  đủ của CI vẫn kiểm tất cả (R-33b).
- Nhánh `origin/*` chưa qua cổng mà là tổ tiên của HEAD cũng được coi là lát trước. Quy trình hiện tại chỉ đẩy sau
  GATE 2 nên không gặp; đổi quy trình thì xem lại.

## Luật cổng (R-33b)
- Chủ dự án yêu cầu 2026-11-26: "điều chỉnh luật để phù hợp thực tế đi" (sau lượt cổng nhanh 19 phút của NHA-02).
  `tools/verify/*` là tệp bảo vệ (B0-01 [7]); sửa theo yêu cầu trực tiếp này, như PERF-11 và PERF-12.
- Phạm vi kiểm: fast + CI.
