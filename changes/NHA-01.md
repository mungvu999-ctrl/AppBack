# NHA-01 — ba parser chữ cho mái và cốt: độ dốc, feet-inch, từ khoá cốt

Thiết kế: `docs/thiet-ke-bo-ho-so.md` §2.D mục 3, §5 (lát NHA-01; tệp thiết kế nằm trên nhánh
`feature/nha-00-dset-bench`). Người dùng duyệt GATE 1 ngày 2026-11-25. Nhánh tách từ `419130c2`, không phụ thuộc
K-33 / NHA-00 / NHA-04.

- `packages/domain/drawing_set/slope.py` (mới): `parse_slope` đọc `30%`, `i=30%`, `30°`, `6:12`, `6/12` ra tỉ số
  cao/ngang kèm chữ gốc và kiểu ghi; `1:2` (mẫu khác 12, dưới 10) được đánh dấu `ambiguous`.
- `packages/domain/drawing_set/feet_inch.py` (mới): `parse_feet_inch` đọc `10'-6"`, `10' 6 1/2"`, `EL. 100'-0"` ra mm.
- `packages/domain/drawing_set/level_words.py` (mới): `level_role` gán vai `floor` / `plate` / `roof_top` / `ridge` /
  `parapet` / `roof_slab` / `gutter` theo từ khoá, dùng lại `titles.fold`.
- Test viết trước (113 ca, ba tệp `tests/test_{slope,feet_inch,level_words}.py`). Không nối vào luồng nào, không
  đổi hợp đồng, không sửa tệp sẵn có.

## Lệch khỏi thiết kế

- **Không viết lại** phần `±0.000` và dấu phẩy thập phân mà bảng tiêu chí liệt kê: `levels.py` đã đọc và
  `tests/test_levels.py` đã kiểm (`parse_level`, `norm_zero`).
- Parser độ dốc trả cờ `ambiguous` chứ không tự đặt "độ tin ≤ 0,5": bảng độ tin theo nguồn thuộc NHA-05b.
- `T.O. ROOF`, `T.O. PLATE`, `T.O. PARAPET` phải có `T.O.` / `T/O` / `TOP OF` đứng trước; `ROOF`, `PLATE`, `PARAPET`
  trần là tên cấu kiện và "ACCESS TO ROOF" là ghi chú, không phải cốt.
- Chặt hơn ví dụ của thiết kế ở ba chỗ, vì nhận nhầm tệ hơn bỏ sót: `a/b` mẫu khác 12 bị từ chối (phân số inch);
  `a:b` mẫu từ 10 bị từ chối (tỉ lệ bản vẽ `1:50`); inch trần `6"` bị từ chối (feet bắt buộc).
- **Giới hạn đã biết, ghi thành điều kiện gọi trong docstring:** `parse_slope` không phân biệt được `1:2`, `1:5`
  trần với tỉ lệ bản vẽ, hay `3:12` với giờ — nơi gọi chỉ đưa chữ của hình mái/mặt cắt, và dạng `a:b` luôn mang cờ
  nhập nhằng. `level_role` bỏ dấu nên "cột sàn" trùng "cốt sàn" — nơi gọi chỉ đưa chữ đứng cạnh ký hiệu cốt.
- **NHA-03 (từ khoá tên hình) hoãn, chưa làm.** Chạy `titles.parse_title` trên tên hình trong đáp án `truth_v3`:
  dev (17 bộ) đúng loại 165/184, mặt bằng đọc ra nhãn tầng 64/64; test (34 bộ, chỉ xem tổng) 288/311 và 108/109. Cả
  19 ca lệch ở dev là mặt bằng mái/tổng thể mà đáp án ghi `plan` — khác quy ước nhãn, không thiếu từ khoá.

## Phạm vi kiểm

- `bash tools/verify/run.sh verify` đầy đủ (2026-11-25, cây làm việc trên `419130c2`): bước 0 node_modules, 1 ruff
  format, 2 ruff check, 3 mypy --strict, 4 lint-imports, 5 pytest + coverage_gate (12294 đạt), 5b perf + case_gate,
  6 migration, 7 hợp đồng H1 H3 H4 H5, 8 openapi — **đạt**, mã thoát 0. Tệp `changes` này sửa mục "Phạm vi kiểm" sau
  lượt đó; mã và test không đổi.
- Review: `ecc:python-reviewer` (không CRITICAL/HIGH; MEDIUM đã sửa: khoảng trắng dán số, trần `x:12`, số 0 đứng
  đầu, nháy/gạch OCR, test biên 85°; hai MEDIUM còn lại ghi thành điều kiện gọi ở "Lệch khỏi thiết kế"). Không chạy
  `security-reviewer`: hàm thuần, chưa có đầu vào người dùng nào tới được.
- Parser feet-inch và kiểu `x:12` **chưa đo trên dữ liệu thật**: không có bộ hồ sơ feet-inch nào (thiết kế §6 câu 11).

## Nợ và việc chưa làm

- NO-575 — Thước Mức 3 có tờ không chứa tên hình trong ảnh (xem tay một hình của `RE-MulFamHou-Condo_AH`: không tên
  hình, không chữ phòng, không cốt; tên chỉ có ở `title_raw` của đáp án), nên điểm loại hình/số tầng của nhóm sạch
  bị chặn trần bởi dữ liệu. Chưa đếm bao nhiêu trong 50 tờ như vậy. Câu "tên hình tiếng nước ngoài" ở
  `changes/NHA-00.md` là sai và cần sửa. Gốc: tờ ghép dựng từ ảnh CAD đã cắt mất tên. Chủ: `tools/bench/muc3`
  (K-33/NHA-10). P2. Mở.
- NO-576 — Ba parser chưa có nơi dùng trong mã sản phẩm (lệch R-10 theo thiết kế §5: lát tách riêng để NHA-05a/05b
  nhỏ lại). Nếu NHA-05a/05b đổi hướng thì xoá. Chủ: NHA-05a/05b. P3. Mở.
