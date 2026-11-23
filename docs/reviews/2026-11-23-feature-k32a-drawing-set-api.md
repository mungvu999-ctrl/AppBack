# Review merge feature/k32a-drawing-set-api → main

- Ngày: 2026-11-23 · Reviewer: phiên /merge-review · Commit đầu nhánh (trước commit phán quyết): 7f3295df
- Cổng: phạm vi đầy đủ; `run.sh verify` trên nhánh gộp chore/m3-gop-be @ea89ced9 (gồm nhánh này) mã thoát 0, 12013 test qua, bước 7 hợp đồng đạt (log: tools-dieu-phoi/log/m3-gop-be.log). Lượt `--fast` trước đỏ bước 7 (resource 404 mới) — đã sửa AppFront #106.

## Finding
| # | Mức | ID | Mô tả | Vị trí | Đề xuất |
|---|---|---|---|---|---|
| 1 | P2 | MNT | Tác vụ `drawing_set.analyze` là bản tạm (mỗi trang một hình, loại `other`) cho tới khi nối K-14/K-32d/K-32e | apps/api/drawing_sets | NO-537 |
| 2 | P3 | PERF | N56 xử lý tệp ngay trong request | apps/api/drawing_sets | NO-538 |
| 3 | P3 | MNT | Sửa nhỏ ngoài thư mục sở hữu: access/kinds.py, core/errors.py, storage/keys.py, floors/jobs.py, core/tests/test_common.py | — | Chấp nhận (thêm từ vựng loại/mã/khoá kho), ghi trong changes |

Charter commit e18da186 chỉ chạm `docs/charter/*` + `changes/K-32a.md`, trailer `Charter-Approved: changes/K-32a.md "Người dùng duyệt 2026-11-23"` trỏ dòng có thật (hợp đồng + 7 chỗ chỉnh sau duyệt). openapi sinh bằng `run.sh openapi`: so JSON với main không mất/đổi đường nào, thêm 6 đường (8 thao tác). `APPFRONT_SHA` = 00523752 đã ở AppFront master (#106). `tools/case_gate.py` thêm `N60: {C14}` khớp CASE.md. Công tắc `DRAWING_SET_ENABLED` mặc định tắt: lệnh ghi 503. Chỉ N60 tạo tầng; mặt cắt → `is_section` ẩn; thay bản vẽ chỉ khi `replace: true`, luật duyệt lại L2-N03 áp qua `start_run`.

## PHÁN QUYẾT: APPROVE
