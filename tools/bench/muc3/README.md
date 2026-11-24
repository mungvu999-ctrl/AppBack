# tools/bench/muc3 — thước Mức 3 (hồ sơ nhiều tầng)

Chấm đầu ra của chuỗi dựng bộ hồ sơ nhiều tầng (loại hình, số tầng, chiều cao tầng, chồng tầng, lõi thang, tách hình
trên tờ) theo đáp án (K-33). Cùng khuôn thước bench mặt bằng (`tools/bench/README.md`, K-17a): mã trong repo,
**ảnh bản vẽ, đáp án và `split.json` KHÔNG nằm trong repo** (ảnh lấy từ web của bên thứ ba, repo có thể công khai).
Test chỉ dùng dữ liệu tổng hợp (`tools/bench/tests/test_muc3_*.py`).

Mã chuyển từ `F:/runs/dwglab/muc3/score_set.py` (380 dòng) và viết lại cho đạt ruff/mypy strict của repo; chấm 9 tổ hợp
(3 thư mục đáp án × 3 thư mục dự đoán) cho ra **cùng từng ký tự** (bảng chữ và `--json`) với bản gốc, trừ dấu `\` / `/`
trong đường dẫn.

## Dữ liệu ngoài repo

| Biến / tham số | Ý nghĩa |
|---|---|
| `BENCH_MUC3_ROOT` | bắt buộc, không mặc định theo máy: chứa `split.json`, `truth/`, `truth_v2/`, `truth_v3/`, `sheets/` |
| `--pred DIR` | thư mục dự đoán, mỗi bộ một `<bộ>.json` (dạng ở `SCHEMA.md`) |
| `--truth-dir DIR` | đáp án từng bộ; mặc định `<gốc>/truth`. Nhãn tờ lấy `<truth-dir>/sheets` nếu có, không thì `<gốc>/sheets` |
| `--split-file`, `--part` | mặc định `<gốc>/split.json`; phần `test`/`dev`/`test2` (mặc định test và dev) |

## Lệnh (Git Bash, từ gốc repo)

`BENCH_MUC3_ROOT=F:/runs/dwglab/muc3 python -X utf8 -m tools.bench.muc3 --pred <thư mục dự đoán> --truth-dir <gốc>/truth_v3 --part test [--json out.json]`

In bảng: loại hình, số tầng, bộ đúng hoàn toàn, chiều cao (`plan+section` là chỉ tiêu chính, `plan` riêng), chồng tầng
`xanh`, lõi thang, kèm số dự đoán thừa, độ phủ, lỗi im lặng (sai trong số dự đoán chắc >= 0,9) và tách hình trên tờ.

## Đáp án nào dùng khi nào

`truth/` (bản đầu, quy ước số tầng cũ), `truth_v2` và `truth_v3` (đã soát, quy ước số tầng 19/11: 1 = trệt, tầng lửng
không có số nguyên). Số đo mới dùng `truth_v3`. Bộ DS- và DN-/DM- đã bị nhìn khi sửa luật, nên không phải bộ giữ riêng sạch.

## Chưa làm

Lượt chấm nền trên bản giả quét của một bộ kiểm mới chưa từng nhìn (K-33 phần 3, ~20 bộ) chờ chủ dự án quyết;
`run_set_img.py` (chạy chuỗi từ ảnh để sinh dự đoán) vẫn ở `F:/runs/dwglab/muc3/kernel/`, chưa chuyển vào repo.
