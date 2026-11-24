# tools/bench — thước bench-3d

Thước đo độ đúng của pipeline dựng mô hình từ bản vẽ (K-17a). Mã nằm trong repo; **ảnh bản vẽ, đáp án và `ml-cache`
KHÔNG nằm trong repo** (ảnh lấy từ web của bên thứ ba, repo có thể công khai — Q8 a). Test chỉ dùng dữ liệu tổng hợp
(`tests/synth.py`).

## Dữ liệu ngoài repo

Mọi đường dẫn qua biến môi trường (`tools/bench/config.py`), không có mặc định theo máy:

| Biến | Ý nghĩa |
|---|---|
| `BENCH_ROOT` | gốc dữ liệu: `MANIFEST.csv`, `plans/`, `answers/`, `runs/`, `ml-cache/` (tập F: trỏ vào gốc tập F) |
| `BENCH_SECRETS` | tệp `.secrets.env` của stack f14 (`E2E_ADMIN_EMAIL`, `E2E_ADMIN_PASSWORD`) — chỉ `run_bench` cần |
| `BENCH_API`, `BENCH_ORIGIN` | mặc định `http://localhost:18081/api`, `http://localhost:18080` |
| `BENCH_WORKER_IMAGE` | ảnh chạy replay, mặc định `appback-worker:f14` |
| `BENCH_RUN_BENCH`, `BENCH_RUN_F`, `BENCH_ROOT_F` | lượt gốc của replay/check (mặc định `runs/r4b`, `tap-f/runs/f2`, gốc tập F `<root>/tap-f`) |

## Lệnh (Git Bash, từ gốc repo, `PYTHONUTF8=1` hoặc `-X utf8`)

- Chấm một lượt → `score.md`, `score.json`, `tham-my.csv`:
  `BENCH_ROOT=... python -X utf8 -m tools.bench.score <thư mục lượt> [--root DIR] [--offsets offsets.json]`
- Chạy đủ bộ trên stack f14, lần lượt từng bản vẽ (dừng giữa chừng rồi chạy lại cùng tên thì làm tiếp):
  `python -X utf8 -m tools.bench.run_bench <tên-lượt> [--group A,B] [--only ch-03] [--calibrate] [--calibrate-only]`
- Chạy nhanh phần sau-ML của một worktree trên `ml-cache` (không GPU, không mạng):
  `python -X utf8 -m tools.bench.replay.replay <worktree> <thư mục ra> [--set bench|F|all] [--jobs 4]`, rồi chấm `<ra>`.
- Kiểm replay trung thực so với lượt thật: `python -X utf8 -m tools.bench.replay.check <thư mục ra>`.
- Tải/làm mới `ml-cache` từ f14: `python -X utf8 -m tools.bench.replay.dump [--force]`.

## Cổng chống tụt (K-17b)

Replay sau-ML trên `ml-cache` rồi so với `baseline/main.json` (kết luận + im lặng từng ca của tập bench và F, kèm SHA
nguồn và sha256 của `ml-cache` lúc chụp). Đỏ khi một tập tụt **> 2 ca ĐẠT** hoặc **tăng ≥ 1 lỗi im lặng** (Q8). Cổng chỉ
bắt tụt ở phần SAU ML (dựng, trộn, quality, readiness); đổi mô hình ML không hiện ra vì `ml-cache` cố định.

- Cả chuỗi (replay → chấm → so): `python -X utf8 -m tools.bench.gate ci <worktree> <thư mục ra> [--local]`.
  `--local` chạy `inner.py` bằng venv hiện tại (CI, hoặc trong ảnh worker); không có thì dùng `BENCH_WORKER_IMAGE`.
- So hai lượt đã chấm: `python -X utf8 -m tools.bench.gate compare tools/bench/baseline/main.json --set bench=<lượt>
  --set F=<lượt> [--ml-cache $BENCH_ROOT/ml-cache]` (mã 0 xanh, 1 tụt, 2 lỗi dữ liệu).
- PR cố ý đổi kết quả (hay đổi thước, đổi `ml-cache`): chụp lại baseline trong cùng PR bằng `gate snapshot ...
  --source-sha <sha> --ml-cache ...` từ lượt replay của nhánh, ghi lý do ở `changes/`. Nên chụp trên Linux (trong ảnh
  worker hay CI): chấm trên Windows lệch số lẻ ở `info` (vd `P6_sum_m2` 59,7 so 59,6), kết luận hiện trùng.
- Workflow `.github/workflows/bench.yml` (PR chạm pipeline/readiness/`tools/bench`): công tắc biến repo `BENCH_GATE`
  = `off` | `auto` (mặc định — thiếu dữ liệu thì bỏ qua, xanh) | `required`. Runner GitHub chưa có dữ liệu (NO-468) nên
  hiện luôn bỏ qua; khi có kho riêng thêm bước tải vào `$BENCH_ROOT` rồi đặt `required`.

## Cấu trúc

`score/` tách theo nhóm tiêu chí (`reliability` R/D, `scale` S, `walls` W, `openings` O, `rooms` P, `probe` M0/F/U,
`verdict`, `engine`, `report`; `io` đọc đáp án/độ lệch trang). `api.py`/`stack.py`/`calibrate.py` phục vụ `run_bench`.
`replay/inner.py` chạy trong container worker nên chỉ nhập `apps`/`packages` của worktree mount.

## Lỗi im lặng (K-11)

`silent` = ca **TRƯỢT** mà sản phẩm vẫn báo tầng **"Sẵn sàng"** (`readiness.state == "ready"`). Tầng `needsReview`/
`notEvaluated` hay lượt thất bại không có readiness đều đã cảnh báo người dùng nên không tính. Ca hành vi (nhóm D/E có
`behaviour`) giữ luật riêng: trượt mà không báo đúng lý do. Cột `silent_old` giữ luật cũ (suy từ finding/lý do, bỏ sót
ca trượt vì ≥ 3 mốc C) để so số với các lượt trước K-11. Layer có số NaN/vô cực: R2 = T, chỉ chấm R1-R3 (`info.nonfinite`).

## Ghi chú đo: mặc định đồ đạc (L2-N05)

Mặc định `detect_furniture` lệch giữa các tầng: cài đặt dự án `False` (`project_settings`, migration `qt_furn_toggle`),
còn `BuildStepPayload.detect_furniture` mặc định `True` cho thông điệp cũ vắng trường. Luồng thường luôn truyền giá trị
dự án nên số đo trên `main` theo `False`; thông điệp tồn trong hàng đợi cũ hoặc stack tích hợp (f14) có thể dựng đồ.
Ghi trong báo cáo đo mặc định nào đang chạy và hàng đợi có trống không (L2-N06). Chốt một mặc định: theo K-20.

## Test

`bash tools/verify/run.sh shell` rồi `pytest tools/bench/tests`. `test_score_golden.py` chấm bộ dữ liệu tổng hợp và đòi
**đúng từng ô** `data/golden-score.json` — bản vàng do bộ chấm gốc (trước khi chuyển vào repo) sinh ra; sửa luật chấm
(vd K-11) thì phải sinh lại bản vàng có chủ đích và ghi vào `changes/`.
