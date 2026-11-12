# SETUP — Cài đặt, chuẩn bị và cách chạy (AppBack + AppFront trên Windows)

Dành cho lập trình viên mới hay agent mới cần dựng máy và chạy được mọi thứ. Nguồn sự thật là mã và
tài liệu được trích ở cuối từng dòng (`tệp:dòng`). Chỗ nào chưa được kiểm thì ghi **(chưa kiểm)**.
Luật viết mã không nằm ở đây: AppBack xem `CLAUDE.md`, `RULE-CODE.md`, `docs/charter/*`; AppFront xem
`CLAUDE.md`, `RULE.md` của repo đó.

## 1. Tổng quan

| Repo | Đường | Ngăn xếp | Chạy bằng |
|---|---|---|---|
| AppBack | `F:/App/AppBack` (`github.com/mungvu999-ctrl/AppBack`) | Python 3.12, FastAPI + Celery worker/beat + ML (onnxruntime, torch CPU), Postgres 16, Redis 7 ×2, MinIO, Mailpit, nginx (`web`) (`docs/charter/BE-00.md:41-60`; Mailpit, nginx: `deploy/compose/base.yml`) | Docker: compose cho dịch vụ, container `verify` cho cổng |
| AppFront | `F:/App/AppFront` (`github.com/mungvu999-ctrl/AppFront`, nhánh chính `master`) | React 19 + Vite 5, TypeScript, pnpm workspace (gồm `vendor/pascal/*`) (`package.json`, `pnpm-workspace.yaml`) | Node + pnpm trên máy |

Ai gọi ai:

- Trình duyệt → `web` (nginx, phục vụ bản dựng AppFront) → `/api/` proxy sang `api:8000` (`tools/ci/job.sh:418-419`).
- `api` / `worker` / `beat` / `ml` nói chuyện qua Postgres, `redis-broker` (Celery, Streams), `redis-cache`, MinIO (`BE-00.md:48-53`).
- Ảnh `web` dựng từ **mã AppFront tại một SHA** (`deploy/docker/web-context.sh` xuất bằng `git archive`) (`deploy/compose/dev.yml:183-198`).
- Cổng verify của AppBack đọc AppFront @ `tools/contract/APPFRONT_SHA` để kiểm hợp đồng FE–BE (bước 7) (`tools/verify/run.sh:64-82`).
- Khi chạy `pnpm dev`, Vite proxy `/api` tới `VITE_API_PROXY_TARGET`, mặc định `http://localhost:8080` (`AppFront/vite.config.ts:31-42`).

Cổng mạng mặc định (đổi được bằng biến trong `deploy/compose/env.example:92-101`):

| Dịch vụ | Cổng host | Biến |
|---|---|---|
| `web` (nginx + FE) | 8080 | `WEB_HTTP_PORT` |
| `api` (chỉ `dev.yml`; `ci.yml` không mở) | 8000 | `API_HOST_PORT` (`dev.yml:94-95`) |
| Postgres | 5432 | `POSTGRES_HOST_PORT` |
| Redis broker / cache | 6379 / 6380 | `REDIS_BROKER_HOST_PORT` / `REDIS_CACHE_HOST_PORT` |
| MinIO API / console | 9000 / 9001 | `MINIO_*_HOST_PORT` |
| Mailpit SMTP / UI | 1025 / 8025 | `MAILPIT_*_HOST_PORT` |
| Vite dev / `pnpm e2e` | 5173 | `E2E_PORT` cho e2e (`AppFront/scripts/run-playwright.mjs:32`) |

**Trên máy này 5432 và 8080 đã bị tiến trình khác của Windows giữ** (đo bằng `netstat -ano` ngày 2026-10-07; README F-14 ghi đó là Postgres và Apache, `AppFront/e2e/fullstack/README.md:28`). Chạy compose thì dời sang 15432 / 18080 (mục 4, 5.4).

## 2. Yêu cầu máy

| Thứ | Đo trên máy (2026-10-07) | Kiểm bằng | Ghi chú |
|---|---|---|---|
| Windows 11, 12 luồng, 23,7 GB RAM, RTX 4050 6 GB | — | — | `docs/charter/ENV.md:20` |
| Docker Desktop (WSL2) | 29.6.1; Compose v5.3.0; VM 12 CPU, 11,5 GiB | `docker --version`, `docker compose version`, `docker info --format '{{.ServerVersion}}'` | GPU vào được container (`ENV.md:21`) |
| Git for Windows + Git Bash | git 2.54.0, bash 5.3.9 | `git --version`, `bash --version` | Mọi lệnh cổng chạy trong **Git Bash** (`ENV.md:29`) |
| Node | v24.16.0 | `node --version` | CI của AppFront dùng Node 20 (`AppFront/.github/workflows/ci.yml:47`); container verify tự có Node 20 (`ENV.md:38`) |
| pnpm | 9.4.0 | `pnpm --version` | CI dùng pnpm 9 (`ci.yml:44`); lockfile v9.0 |
| Python máy | 3.11.9 | `python --version` | Chỉ dùng cho vài dòng `python -c` trong `chuoi.sh`; cổng **không** dùng Python/uv/Node của máy (`CLAUDE.md:106`) |
| uv máy | 0.4.18 | `uv --version` | Quá cũ, không dùng (`ENV.md:24`); uv chạy trong container |
| `just` | không có | — | Dùng `bash tools/verify/run.sh <việc>` (`CLAUDE.md:16-31`) |
| Google Chrome | có (`C:\Program Files\Google\Chrome\Application\chrome.exe`) | — | Playwright chạy `channel: 'chrome'` (`AppFront/playwright.config.ts:20`) |
| Đĩa | `C:` còn **15–18 GB** (96–97% đầy), `F:` còn 57 GB | `df -h /c /f` | ENV ghi `C:` còn 127 GB — **đã lỗi thời**. Worktree Orca nằm ở `C:` (`ENV.md:22`), mỗi worktree FE chép `node_modules` ~434 MB (`ENV.md:23`), mỗi venv verify ~1,3 GB (`ENV.md:53`), ảnh `appback-ml` ~3,5 GB |

Máy mới, cài theo thứ tự:
1. **Docker Desktop** bản WSL2; Settings → Resources: VM ≥ 11 GB RAM, ≥ 8 CPU (cổng verify AppBack và chuỗi F-14 đều chạy trong VM này).
2. **Git for Windows** (kèm Git Bash).
3. **Node** ≥ 20 (CI dùng 20) và **pnpm 9** (`npm i -g pnpm@9`).
4. **Google Chrome** (Playwright dùng kênh `chrome`).

Python và uv của máy không cần cho cổng.

Git: hệ thống đặt `core.autocrlf=true`, `init.defaultBranch=master` (`ENV.md:24-25`). Không cần đổi: `run.sh`
và `web-context.sh` tự tắt autocrlf khi `git archive` (`run.sh:71`, `web-context.sh:28`).

## 3. Chuẩn bị lần đầu

1. **Vị trí thư mục.** AppFront phải nằm **cạnh checkout chính** của AppBack: `<cha>/AppBack` ↔ `<cha>/AppFront`.
   `appfront_repo.sh` suy ra đường từ `git rev-parse --git-common-dir`, nên đứng trong worktree phụ ở `C:` vẫn
   tìm đúng `F:/App/AppFront` (`tools/verify/appfront_repo.sh:2-12`). Đặt chỗ khác thì ghi đè bằng
   `APPFRONT_REPO=<đường>` (`tools/verify/README.md:15`).
   ```bash
   cd /f/App
   git clone https://github.com/mungvu999-ctrl/AppBack.git
   git clone https://github.com/mungvu999-ctrl/AppFront.git
   ```
   Thư mục `AppBack/backend/` (tài liệu điều phối, `prompts/`, `dieu-phoi/`) bị `.gitignore` (`.gitignore:1-2`):
   clone mới **không có** nó, kể cả `chuoi.sh` của F-14.
2. **Hook commit của AppBack** (một lần cho mọi worktree) (`.githooks/commit-msg:4-5`, `CLAUDE.md:82-83`):
   ```bash
   git -C /f/App/AppBack config core.hooksPath .githooks
   ```
   Trên máy này đã bật. AppFront không có `.githooks`; `core.hooksPath` của AppFront đang trỏ đường cũ
   `F:\AppFront\.githooks` (không tồn tại) — vô hại **(chưa kiểm)** có cần gỡ hay không.
   **Checkout chính `F:/App/AppFront` trên máy này đang ở nhánh cũ `e2e/integrate`** (2026-10-07: chậm `master`
   140 commit). Làm việc với AppFront thì tạo worktree từ `origin/master`
   (`git -C /f/App/AppFront fetch && git -C /f/App/AppFront worktree add <đường> -b <nhánh> origin/master`);
   đọc mã AppFront mới nhất bằng `git show origin/master:<tệp>`, đừng đọc thẳng thư mục checkout chính.
3. **Phụ thuộc AppFront** (chạy trong worktree FE):
   ```bash
   pnpm install --frozen-lockfile
   ```
   (`backend/dieu-phoi/chay/DEBT-03/chung.md:35`). Sửa `eslint-rules/**` xong phải `pnpm install` lại, vì pnpm
   sao chép cứng thư mục đó (`AppFront/CLAUDE.md:156-159`).
4. **Biến môi trường compose.** Mẫu ở `deploy/compose/env.example`; sao thành `.env` (bị `.gitignore`, không
   commit) rồi điền giá trị (`env.example:1-3`, `.gitignore:26-28`). Các lệnh trong repo đều truyền tường minh
   `--env-file deploy/compose/env.example` (`tools/ci/job.sh:407`, `chuoi.sh:9`) và đè cổng bằng biến shell.
   Ba khoá MinIO phải khác nhau (`env.example:33-38`). Biến shell thắng `--env-file` (chuỗi F-14 dựa vào đó).
   `env.example` toàn giá trị giữ chỗ `change-me*`: chỉ dùng cho dev/ci trên máy, **không** dùng cho môi trường thật.
5. **Ảnh Docker.** Không cần kéo tay: `run.sh` tự `build` ảnh `appback-verify:local` và tạo volume
   `appback-work` mỗi lượt (`run.sh:93-100`, `deploy/compose/verify.yml:8,54-58`). Ảnh dịch vụ dựng từ
   `deploy/docker/{api,worker,ml,web}.Dockerfile` (mục 4, 5.4). Kiểm Docker sống trước mọi việc:
   ```bash
   docker info --format '{{.ServerVersion}}'   # phải in version, không lỗi
   ```

## 4. Chạy dev

### 4.1 BE bằng compose dev

`dev.yml` dựng ảnh tại chỗ (`appback-api:dev`, `appback-worker:dev`, `appback-web:dev`; `ml` chỉ khi
`--profile ml`, `ml-gpu` khi `--profile gpu`) (`deploy/compose/dev.yml:1-2,146-181`). Dịch vụ `web` **bắt buộc**
`APPFRONT_CONTEXT` và `APPFRONT_SHA` — thiếu thì compose hỏng ngay khi nạp file, kể cả khi chỉ gọi dịch vụ khác
(đã kiểm bằng `docker compose … config -q`; `dev.yml:191-193`).

```bash
# Git Bash, gốc AppBack
SHA=$(tr -d '[:space:]' < tools/contract/APPFRONT_SHA)          # hoặc SHA AppFront bạn cần
bash deploy/docker/web-context.sh "$SHA" .cache/web-ctx-$SHA     # đích chưa được tồn tại (web-context.sh:15-18)
export APPFRONT_CONTEXT=$PWD/.cache/web-ctx-$SHA APPFRONT_SHA=$SHA
export POSTGRES_HOST_PORT=15432 WEB_HTTP_PORT=18080 PUBLIC_BASE_URL=http://localhost:18080   # máy này bận 5432/8080
docker compose -f deploy/compose/dev.yml --env-file deploy/compose/env.example up -d --wait
```
**(chưa kiểm)** — lệnh trên ghép từ `dev.yml`, `web-context.sh` và cách `chuoi.sh` đè cổng; chưa có lượt
`dev.yml` nào được ghi lại trên máy này. Lượt đã chạy thật là `ci.yml` (mục 5.4).

- `migrate` chạy `alembic upgrade` trước `api`/`worker` (`dev.yml:74-108`); migration registry seed sẵn "Bản gốc" của mỗi họ ML (`AppFront/e2e/fullstack/README.md:102`).
- **Không có đăng ký công khai.** Admin đầu tiên (DB trắng) (`BE-00.md:209-212`):
  ```bash
  printf '%s' "$MAT_KHAU" | docker compose -f deploy/compose/dev.yml --env-file deploy/compose/env.example \
    exec -T api python -m apps.api.auth.cli create-admin --email <email> --name <tên> --password-stdin
  ```
- Thư viện nội thất (M15, tái tạo được, không `docker cp`): tệp `.glb`/`.png` nằm ngoài git, `packages/domain/library/files.json` (trong git) ghim `model_sha256` của từng id.
  1. Dựng gói (một lần mỗi bản `files.json`): `python -m apps.api.library.cli build-pack <out> --from <nguồn> [--from <nguồn> …]` — lấy `<id>.glb` (+ `<id>.png`) từ nguồn đầu tiên khớp sha; thiếu hay lệch một id → thoát 1, không ghi gì. Đặt tên `library-<12 ký tự đầu sha256(files.json)>`. Trên máy này: `F:/assets-pack/library-bcbc8c05ac16/` (978 mẫu), nguồn `F:/runs/can-mau/library-import/out`, `F:/runs/library-import-2/out`, `F:/runs/library-import-3/{pack3/out,pack4/all,plants/out}`, `F:/runs/library-import-4/{abo,objaverse}/out`.
  2. Nạp: chồng `-f deploy/compose/library-pack.yml` lên lệnh compose và đặt `LIBRARY_PACK_HOST_DIR=<thư mục gói>`. Gói gắn **chỉ đọc** vào `migrate` ở `/library-pack` (`LIBRARY_PACK_DIR`); sau seed, `migrate` chạy `import-files --strict` (thiếu tệp của mục nào cũng làm `migrate` thất bại; chạy lại không `put` lại object đã khớp sha; mục lệch → `failed`, `migrate` thoát 1). Không có file chồng → `migrate` như cũ. Cần stack có `minio-init` (dev, f14; prod bật profile `minio`). `api`/`worker` chờ `migrate` xong mới chạy, nên nạp gói hỏng (MinIO trục trặc, gói cũ sau khi đổi `files.json`) thì cả stack không lên: dựng lại gói đúng `files.json`, hoặc bỏ file chồng để lên trước rồi nạp tay (bước 3).
  3. Chạy tay vẫn được: `python -m apps.api.library.cli import-files <thư mục>` trong container `api` (thư mục gắn vào container).
- Thư gửi đi đọc ở Mailpit `http://localhost:8025`.
- Trình duyệt phải vào đúng origin `PUBLIC_BASE_URL` (`localhost`, không `127.0.0.1`); lệch thì `POST /api/auth/*` trả 403 `ORIGIN_MISMATCH` (`BE-00.md:245-247`, `e2e/fullstack/README.md:17,35`).

### 4.2 FE

```bash
cd /f/App/AppFront
pnpm dev        # = pnpm pascal && vite (package.json:7); mặc định http://localhost:5173
```

| Chế độ | Cách bật | Nguồn |
|---|---|---|
| Client giả (không cần BE) | `VITE_USE_MOCK_API=true` (chỉ đúng chuỗi `'true'`, chỉ dưới `DEV`) | `AppFront/src/api/appClient.ts:47-65` |
| BE thật qua proxy Vite | `VITE_API_PROXY_TARGET=<gốc BE>`; BE phải có `PUBLIC_BASE_URL` = origin của Vite (`http://localhost:5173`) | `vite.config.ts:33-40` |
| BE thật qua nginx | mở thẳng `http://localhost:<WEB_HTTP_PORT>` (bản dựng FE trong ảnh `web`) | mục 4.1 |

**(chưa kiểm)** đường "BE thật qua proxy Vite" trên máy này (ví dụ `VITE_API_PROXY_TARGET=http://localhost:8000`
tới `api` của `dev.yml`). Bộ mẫu dựng sẵn cho dev/test: `createSampleBuilding()` (`AppFront/CLAUDE.md:109`).
Route `/demo` chỉ có ở bản dựng phát triển (`AppFront/CLAUDE.md:190`). `pnpm e2e` tự làm ấm cả đồ thị module
của Vite trước bài đầu (`scripts/warm-dev-server.mjs`, gọi ở `scripts/run-playwright.mjs:196`).

## 5. Cổng kiểm

### 5.1 AppBack — `bash tools/verify/run.sh <việc>` (Git Bash)

| Việc | Làm gì | Ai |
|---|---|---|
| `verify [--steps 1,2,4]` | Cổng 8 bước trong container Linux | worker trước khi báo xong (`CLAUDE.md:22-29`) |
| `lock` | `uv lock` (không `--upgrade`), chép `uv.lock` ra | khi thêm thư viện ngoài BE-00 §13.1 |
| `shell` | bash trong container trên **bản chép** `/tmp/w`; sửa ở đó không về worktree (`tools/verify/README.md:10-12`) | gỡ lỗi; `run.sh shell < lệnh.sh` chạy không tương tác |
| `openapi`, `merge-heads <tên>` | xuất `openapi.json`; revision merge hai head | chỉ người điều phối |
| `gc` | xoá venv/mypy cache của worktree đã xoá (~1,3 GB mỗi venv) | người điều phối (`ENV.md:53`) |

Bước (`BE-00.md:464-474`, `tools/verify/steps.py:301-324`):

| # | Bước |
|---|---|
| 0 | làm ấm `node_modules` của `tools/contract` — tự thêm khi có bước 5 hoặc 7 (`steps.py:369-372`) |
| 1 / 2 | `ruff format --check` / `ruff check` |
| 3 | `mypy --strict` — cổng đầy đủ chạy nguội (~212 s); `--steps` thiếu bước dùng cache ấm, chỉ là xem trước (`tools/verify/README.md:6-9`) |
| 4 | `lint-imports` |
| 5 | `coverage run -m pytest -n 6` → `coverage_gate` (≥ 90% dòng **và** nhánh); `VERIFY_PYTEST_WORKERS` đổi số tiến trình (`steps.py:175-192`, `verify.yml:28`) |
| 5b | `pytest -m perf` → `case_gate` |
| 6 | migration: lint → upgrade → seed → downgrade → upgrade → khớp model |
| 7 | hợp đồng FE–BE H1/H3/H4/H5 (`tools.contract.check`) |
| 8 | OpenAPI (nhánh `main` so với `docs/contracts/openapi.json`) |

- Nhánh `main` → `VERIFY_BRANCH=integration`, còn lại `worker` (`run.sh:33-46`). File đổi tính so với `git merge-base HEAD main` trên máy chủ (`run.sh:48-53`).
- Log mỗi lượt: `.cache/src-out/verify/<giờ>-<sha>.log` (+ `.junit.xml` của bước 5), giữ 20 lượt (`run.sh:55-60,104-122`).
- Mount: **toàn bộ checkout** → `/src` (chỉ đọc), rồi `cp -r /src/. /tmp/w/` và `chmod 644` (`run.sh:85`, `verify.yml:17`, `tools/verify/in_container.sh:13-14`).
- Thời gian: bước 5 đầy đủ ~505 s (8383 test, `-n 6`, đo 2026-10-05) (`ENV.md:69`).
- Trước lượt verify đầu của một prompt: tạo `changes/<mã>.md` 3–10 dòng (`CLAUDE.md:64-67`).
- Phạm vi "đầy đủ" hay "đích" theo `RULE-CODE.md:68` (R-33b).

### 5.2 AppFront

| Lệnh | Việc | Nguồn |
|---|---|---|
| `pnpm verify` | 7 bước tuần tự, dừng ở bước hỏng đầu: typecheck → lint → import vòng → test + độ phủ → build → kích thước gói → độ dài file; ~300 s, ăn hết CPU | `scripts/verify.mjs:19-65`, `AppFront/CLAUDE.md:21`, `ENV.md:71` |
| `pnpm typecheck` / `lint` / `cycles` / `length` / `size` | từng bước lẻ | `package.json:10-19` |
| `pnpm test` / `pnpm coverage` | vitest; chỉ `coverage` đối chiếu ngưỡng | `AppFront/CLAUDE.md:23-24` |
| `pnpm e2e [tệp]` | Playwright, tự dựng Vite ở `127.0.0.1:${E2E_PORT:-5173}` | `scripts/run-playwright.mjs:32-36,97` |
| `pnpm e2e:visual` | như trên nhưng ghi đè ảnh chuẩn | `package.json:21` |
| `pnpm e2e:fullstack` | chuỗi FE + BE thật (mục 5.4) | `package.json:22` |

`E2E_PORT` cô lập cổng giữa hai worktree, **không** cô lập thư mục dựng: trong một worktree chỉ một lượt e2e
một lúc (`run-playwright.mjs:9-31`). `E2E_SKIP_PASCAL=1` bỏ lượt dựng Pascal khi đã có `pascal-mount.js`.

### 5.3 Hợp đồng FE–BE (bước 7)

- `tools/contract/APPFRONT_SHA` ghim một commit AppFront (thường là merge commit mới nhất trên `master`). `run.sh` xuất `src` + `package.json` của commit đó vào `.cache/appfront/<sha>` và mount làm `/appfront` (`run.sh:64-82`). Chỉ người điều phối sửa file SHA (`CLAUDE.md:46-49`).
- Mẫu golden (`CONTRACT_SAMPLES_DIR=/tmp/contract-samples`) **mới mỗi lượt** và do test bước 5 ghi; cuối lượt chép ra `contract-samples/` của worktree (`verify.yml:31`, `ENV.md:51`, `steps.py:378-383`). Vì vậy kiểm hợp đồng phải đi cùng bước 5:
  ```bash
  bash tools/verify/run.sh verify --steps 5,7
  ```

### 5.4 Chuỗi thật F-14 (BE compose + web build + e2e fullstack)

Hướng dẫn gốc: `AppFront/e2e/fullstack/README.md` (bảy điều kiện, biến, cách đọc kết quả). Script điều phối:
`backend/dieu-phoi/chay/F-14/chuoi.sh` (chỉ có trên máy này, ngoài git).

```bash
# Git Bash
bash F:/App/AppBack/backend/dieu-phoi/chay/F-14/chuoi.sh                 # = build up seed run
BE=<worktree AppBack> FE=<worktree AppFront> WEB_SHA=<sha AppFront> bash …/chuoi.sh build up seed run
bash …/chuoi.sh down                                                      # docker compose down -v — XOÁ volume của project f14 (DB, MinIO, Redis)
```

| Biến | Mặc định | Nguồn |
|---|---|---|
| `BE` | `C:/Users/mxuan/orca/workspaces/AppBack/fix378-x` | `chuoi.sh:6` |
| `FE` | `C:/Users/mxuan/orca/workspaces/AppFront/f14-w` | `chuoi.sh:7` |
| `WEB_SHA` | `efbda6bd` | `chuoi.sh:21-22` |
| cố định | `POSTGRES_HOST_PORT=15432 WEB_HTTP_PORT=18080 PUBLIC_BASE_URL=http://localhost:18080 IMAGE_TAG=f14 ML_BACKEND=fake`; compose `-p f14 -f deploy/compose/ci.yml --env-file deploy/compose/env.example --profile ml` | `chuoi.sh:8-9` |

Mặc định của `BE`/`FE`/`WEB_SHA` trỏ worktree và SHA cũ, có thể đã bị xoá — luôn đặt cả ba tường minh.

| Pha | Làm gì | Nguồn |
|---|---|---|
| `build` | dựng `appback-api:f14`, `appback-worker:f14`; tạo `web-ctx-<WEB_SHA>` nếu chưa có rồi dựng `appback-web:f14`. **Không dựng `appback-ml:f14`** — ảnh đó lấy từ `backend/dieu-phoi/chay/F-14/build-images.sh` (ngoài git) | `chuoi.sh:17-23`, `build-images.sh:6` |
| `up` | `up -d --wait`, in `ML_BACKEND`, `/api/health`, CSP, cache `pascal-mount.js` | `chuoi.sh:24-31` |
| `seed` | `create-admin` (tài khoản sinh vào `.secrets.env` cạnh script, không in), xuất `drawing.png` = `render_plan(7)`, nạp model thứ hai qua N26, chờ eval `completed` (≤ 5') | `chuoi.sh:11-13,39-68` |
| `run` | `E2E_FULLSTACK_BASE_URL=$BASE E2E_DRAWING_PNG=… pnpm e2e:fullstack` trong `$FE` | `chuoi.sh:69-72` |

Chuỗi chạy 1 worker, không retry, báo cáo ở `playwright-report/fullstack` (`AppFront/e2e/fullstack/playwright.config.ts`).
Trace/báo cáo chứa cookie phiên — không đính vào báo cáo (`e2e/fullstack/README.md:147`). Chạy bằng Git Bash:
`>` của PowerShell 5.1 ghi UTF-16, làm hỏng PNG/ONNX (`e2e/fullstack/README.md:39`).

## 6. Git và merge (tóm tắt)

| Mục | AppBack | Nguồn |
|---|---|---|
| Nhánh | `<loại>/<mã viết thường>-<mô tả>`, loại ∈ feature fix chore docs refactor test; không viết thẳng lên `main` | `BE-00.md:539`, `RULE-CODE.md:76` |
| Commit | `<type>(<scope>): <tóm tắt tiếng Anh>` ≤ 72 ký tự; trailer `Prompt: <mã>` (FIX thêm `Fix: FIX-<nnn>`) ở đoạn cuối, một dòng trống trước, không dòng trống bên trong; không `--no-verify` | `BE-00.md:535-538`, `RULE-CODE.md:77`, `.githooks/commit-msg` |
| Review | `/merge-review` ở **phiên riêng**; phán quyết ở `docs/reviews/<ngày>-<nhánh>.md`; không `APPROVE` thì không merge | `RULE-CODE.md:78`, `.claude/skills/merge-review/` |
| Gộp | một prompt → squash; nhiều prompt → `--no-ff` (giữ mọi trailer) | `RULE-CODE.md:76` |
| Ngoại lệ thẳng lên `main` | chỉ dòng `DEBT.md` và `docs/reviews/*` | `RULE-CODE.md:76` |
| Nợ | mỗi nợ một dòng `NO-<nnn>` trong `DEBT.md` trước khi phiên kết thúc; không xoá dòng | `RULE-CODE.md:74-75`, `CLAUDE.md:69-75` |
| Không commit | `/openapi.json` gốc, `.coverage`, `contract-samples/**` (trừ khi prompt đòi), `.cache/`, `backend/` | `BE-00.md:541`, `.gitignore` |

AppFront: commit cũng theo dạng `<type>(<scope>): …` và vào `master` qua pull request trên GitHub (thấy trong
`git log master`); luật chi tiết **(chưa kiểm)** — xem `AppFront/RULE.md`, `AppFront/CLAUDE.md`.

## 7. Sự cố thường gặp

| # | Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|---|
| 1 | `just: command not found`; `uv` máy báo lỗi lạ | Máy không có `just`; uv máy 0.4.18 quá cũ (`ENV.md:24`) | Luôn `bash tools/verify/run.sh <việc>` trong Git Bash (`CLAUDE.md:16-20`) |
| 2 | Chạy riêng `--steps 7` ra **0 mẫu golden** / H1 báo thiếu mẫu 2xx | Golden do test bước 5 ghi vào thư mục mới mỗi lượt (`verify.yml:31`) | `bash tools/verify/run.sh verify --steps 5,7` |
| 3 | Bước chép đầu container treo rất lâu (đã thấy > 25 phút) | `run.sh` mount **cả checkout** và `cp -r /src/. /tmp/w/` (`in_container.sh:13`), kể cả thư mục bị `.gitignore` như `backend/`. Mỗi `backend/dieu-phoi/chay/F-14/web-ctx*` ~81 MB (`chuoi.sh` tự tạo lại khi cần) | Xoá `web-ctx*` trước khi verify. Đường dài quá giới hạn Windows → PowerShell: `Remove-Item -LiteralPath "\\?\F:\App\AppBack\backend\dieu-phoi\chay\F-14\web-ctx-<sha>" -Recurse -Force` |
| 4 | `docker` báo không nối được daemon sau khi máy ngủ | Docker Desktop chết | `docker info --format '{{.ServerVersion}}'` phải in version trước khi chạy gì; không thì khởi động lại Docker Desktop |
| 5 | Verify chậm bất thường, OOM; e2e trang trắng, `net::ERR_NO_BUFFER_SPACE`, bài đỏ ở hạn first paint | Hai cổng AppBack đầy đủ cùng lúc vượt RAM VM (`ENV.md:69`); e2e chồng vitest/verify của phiên khác làm Windows cạn bộ đệm socket cho request module Vite (`DEBT.md` NO-408) | Một verify AppBack đầy đủ trên cả máy; tối đa 2 `pnpm verify`, tổng 4 (`ENV.md:65-77`); không chạy e2e chồng vitest/verify. Khoá `e2e.lock` (`mkdir` lấy, `rmdir` trả, kể cả khi hỏng) là **quy ước điều phối**, không có trong mã, và chỉ chặn e2e với e2e (`backend/dieu-phoi/chay/DEBT-03/chung.md:36-39`). Lệnh nền bằng `&` có thể sống sót sau khi shell thoát và chạy trùng — kiểm tiến trình còn sống trước khi chạy lại |
| 6 | `git worktree remove` / xoá thư mục báo `Filename too long` | Đường > 260 ký tự trên Windows | `git worktree remove <đường>` rồi xoá phần còn lại bằng tiền tố `\\?\` như mục 3; **(chưa kiểm)** `git config core.longpaths true` có tránh được không (máy hiện chưa đặt) |
| 7 | Test đỏ thất thường, lượt sau xanh | Chập chờn đã biết: NO-403 (đã sửa FIX-491, phần dư NO-412), NO-404, NO-410 — BE dưới tải `-n`; NO-409 — `e2e/pascal-viewer.spec.ts` B-3 (`DEBT.md:423-432`) | Đối chiếu id trong `DEBT.md` trước khi kết luận lỗi mới; không nới assert, không retry để giấu (`RULE-CODE.md:75`) |
| 8 | Commit kéo theo tệp lạ | Index của checkout `main` có thể chứa tệp phiên khác đã stage | Commit bằng pathspec: `git commit -F - -- <tệp>`; không `git add -A`; không `git stash` khi có nhiều worktree |
| 9 | `POST /api/auth/*` → 403 `ORIGIN_MISMATCH` | Origin trình duyệt ≠ `PUBLIC_BASE_URL` (hay dùng `127.0.0.1` thay `localhost`) | Đổi cổng thì đổi **cùng nhau** `WEB_HTTP_PORT`, `PUBLIC_BASE_URL`, `E2E_FULLSTACK_BASE_URL` (+ `POSTGRES_HOST_PORT`) (`e2e/fullstack/README.md:28-35`) |
| 10 | `docker run`/`orca` nhận đường `C:/Program Files/Git/...` | MSYS tự đổi tham số bắt đầu bằng `/` | `export MSYS_NO_PATHCONV=1` (`ENV.md:82`; `run.sh:8` tự đặt) |
| 11 | `pnpm e2e` báo XANH nhưng kiểm nhầm mã | Hai worktree cùng cổng 5173 | Đặt `E2E_PORT` riêng mỗi worktree; cổng bận thì lượt dừng (`run-playwright.mjs:9-17`) |
| 12 | `EPERM … public/assets/pascal` | Hai lượt e2e trong cùng một worktree xoá/ghi chung thư mục dựng Pascal | Một lượt e2e mỗi worktree (`run-playwright.mjs:18-30`) |
