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

## Đo đường sản phẩm (NHA-00)

Ba bước, đều chạy trong ảnh có `onnxruntime` + `rapidocr-onnxruntime` (ảnh verify), gốc dữ liệu mount vào container:

1. Dựng đệm đọc trang **một lần** (chậm: OCR 4-110 s/tờ; tờ đã có đệm không đọc lại):
   `python -X utf8 -m tools.bench.muc3.readings --out <đệm>` — mỗi tờ `sheets/<bộ>__sNN.png` qua `_render_page` →
   `_read_one` của sản phẩm, ghi `<đệm>/<tờ>.json` và `MANIFEST.json` (sha256 ảnh nguồn + kết quả đọc).
2. Đệm → dự đoán (thuần, vài giây, chạy lại ra tệp trùng từng byte):
   `python -X utf8 -m tools.bench.muc3.predict --readings <đệm> --out <dự đoán>` — `solve_set` của nhánh đang đứng.
3. Chấm, tách sạch / đã lộ bằng hai tệp bước 2 ghi cạnh dự đoán:
   `python -X utf8 -m tools.bench.muc3 --pred <dự đoán> --truth-dir <gốc>/truth_v3 --split-file <dự đoán>/split_clean.json`
   (và `split_exposed.json`). 27 bộ đã lộ = bộ K-32a dùng để chỉnh luật tách hình (`predict.EXPOSED`).

So trước/sau một thay đổi luật: giữ nguyên đệm (cùng `MANIFEST.json`), chạy bước 2-3 trên hai nhánh. Khung máy
không khớp khung đáp án nào (IoU < 0,5) không có mã hình nên không vào `views`: cột "dự đoán thừa" của loại/tầng
không đếm chúng — tách thừa chỉ hiện ở precision của phần tách hình.

## Chưa làm

Lượt chấm nền trên bản giả quét của một bộ kiểm mới chưa từng nhìn (K-33 phần 3, ~20 bộ) chờ chủ dự án quyết;
`run_set_img.py` (chuỗi **nghiên cứu** sinh dự đoán từ ảnh) vẫn ở `F:/runs/dwglab/muc3/kernel/`, không chuyển vào repo:
đường sản phẩm đã có `readings.py` + `predict.py`. `score_roof.py` chưa chuyển (sản phẩm chưa sinh mái, NO-569).
