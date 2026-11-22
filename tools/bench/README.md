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

## Cấu trúc

`score/` tách theo nhóm tiêu chí (`reliability` R/D, `scale` S, `walls` W, `openings` O, `rooms` P, `probe` M0/F/U,
`verdict`, `engine`, `report`; `io` đọc đáp án/độ lệch trang). `api.py`/`stack.py`/`calibrate.py` phục vụ `run_bench`.
`replay/inner.py` chạy trong container worker nên chỉ nhập `apps`/`packages` của worktree mount.

## Test

`bash tools/verify/run.sh shell` rồi `pytest tools/bench/tests`. `test_score_golden.py` chấm bộ dữ liệu tổng hợp và đòi
**đúng từng ô** `data/golden-score.json` — bản vàng do bộ chấm gốc (trước khi chuyển vào repo) sinh ra; sửa luật chấm
(vd K-11) thì phải sinh lại bản vàng có chủ đích và ghi vào `changes/`.
