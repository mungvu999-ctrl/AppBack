# tools/verify

Cổng verify chạy qua `bash tools/verify/run.sh <việc>` (xem `CLAUDE.md`).

- `verify`: ghi log cổng và `<log>.junit.xml` của bước 5 vào `.cache/src-out/verify/` (NO-324).
- Khoá cổng (PERF-11, `gate_lock.sh`): khoá đọc/ghi `flock /work/gate-full.lock` giữ suốt lượt trong `in_container.sh`.
  `verify` (kể cả `--steps`, trừ `--fast`) và `hold` giữ **một mình**; `verify --fast` và `shell` giữ **dùng chung** (chạy
  cùng nhau, không cạnh lượt một mình). Cửa quay `/work/gate-turn.lock`: lượt một mình đang chờ chặn lượt dùng chung
  mới đến, nên lượt nhẹ không chen mãi trước cổng đầy đủ. Lượt chờ in chủ khoá (`/work/gate-full.owner`). Khoá nhả khi
  tiến trình chết, không treo.
- `hold -- <lệnh>` (PERF-11): một container nhỏ (`appback-verify-hold-<worktree>-<pid>`) giữ khoá một mình tới khi
  stdin đóng; `run.sh` chạy lệnh host trong lúc đó và trả mã thoát của lệnh. Dùng cho dựng ảnh web f14 thay vòng chờ
  "không còn container verify + RAM trống".
- `verify --fast` (PERF-03, PERF-04): bước 1, 2, 4; pytest các tệp test bị ảnh hưởng (`affected.py`: test của đơn vị
  bị chạm + test nhập bắc cầu module đổi theo đồ thị grimp + test xin fixture mà module fixture nhập module đổi; đổi
  `conftest.py`, `pyproject.toml`, `uv.lock`, `packages/testing/`, migration hay đầu vào bước 7 thì cả bộ) với độ phủ +
  `coverage_gate` chỉ trên đơn vị bị chạm; mypy nền trên cache ấm `/work/mypy-fast-<worktree>`; bước 6/7/8 chỉ khi đầu
  vào của chúng đổi (`FAST_STEP_INPUTS`); không 5b. Không thấy nạp động và `python -m` — cổng đầy đủ của CI trên PR
  vẫn là phán quyết (R-33b).
- Bước 3: cổng đầy đủ (không `--steps`, hoặc `--steps` gồm **mọi** bước 1–8 và `5b`) chạy mypy
  **nguội** (`MYPY_CACHE_DIR=/dev/null`, ~212 s cả repo); `--steps` thiếu bước và `shell` dùng cache ấm
  `/work/mypy-<worktree>` (~5 s) — chỉ là xem trước: cache ấm có thể phát lại kết quả cũ cả hai chiều.
  Nghi thì `mypy --cache-dir /dev/null` trong `shell` (NO-306).
- Gửi nguồn: `run.sh` dựng `.cache/src-tar/run.<ngẫu nhiên>/src.tar` (mỗi lượt một bản, xoá khi thoát) từ `git ls-files -co --exclude-standard` (đã theo dõi +
  chưa theo dõi không bị ignore), mount `/src-tar:ro`, và `in_container.sh` giải nén vào `/tmp/w` — thay
  `cp -r /src/. /tmp/w` (~3.300 tệp qua mount Windows→WSL2, đo riêng 169 s; nay 13 MB
  đọc một lần — `verify --steps 1` toàn lượt 95 s nguội / 39–42 s ấm → 6 s). Tệp bị `.gitignore` (`.cache/`, `backend/`, `node_modules/`) **không**
  vào `/tmp/w` nữa; thứ cổng cần mà git không theo dõi đi qua mount riêng như trước (`/appfront`,
  `/src-out/contract-samples`, volume `/work`). Gọi compose tay (không qua `run.sh`) thì không có tar và
  `in_container.sh` tự về `cp -r /src`.
- `shell`: làm việc trên **bản chép** `/tmp/w` của worktree. Kết quả sửa trong đó (`ruff --fix`,
  `ruff format`, `python -m packages.db.new_revision`, file mới) **không** về worktree — chép tay ra
  `/src-out/...` như `lock` làm, hoặc sửa ở worktree rồi chạy lại (NO-190).
- Volume `appback-work` (`/work`, dùng chung mọi worktree) là `external`: `run.sh` tự `docker volume create` trước
  mỗi lượt; chạy `docker compose -f deploy/compose/verify.yml` tay trên máy sạch thì phải tạo volume trước (NO-185).
- Dịch vụ test dùng chung với stack (PERF-10): `run.sh` thấy `<stack>-{postgres,redis-broker,redis-cache,minio}-1` đang
  chạy (`VERIFY_SHARED_STACK`, mặc định `f14`; `off` để tắt) thì đặt `TEST_SHARED_*` (IP trên `<stack>_appback`, mật khẩu
  đọc từ env container lúc chạy, không vào log) và test không dựng 4 container đó; container verify tự `network connect`
  vào mạng stack. `in_container.sh` giữ `flock /work/shared-stack.lock`: lượt thứ hai cùng lúc gỡ biến, dựng container
  riêng. Redis stack cần `--databases 128` (`deploy/compose/base.yml`); stack cũ 16 DB thì Redis tự về container (cảnh
  báo trong tóm tắt pytest). Mailpit và `ephemeral_*` luôn là container.
- Repo AppFront mặc định là `<cha của checkout chính>/AppFront`; `APPFRONT_REPO` ghi đè (NO-325).
