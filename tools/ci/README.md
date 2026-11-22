# `tools/ci` — hướng dẫn cài đặt repo GitHub cho người

B0-09 chỉ tạo được file trong repo (`.github/workflows/*`, `.github/dependabot.yml`,
`.gitleaks.toml`, `tools/ci/*`). Các mục dưới đây nằm ở **Settings** của repo
GitHub, ngoài tầm của mã — **admin repo** (không phải CI) phải bấm tay một lần
sau khi đẩy nhánh này lên, theo đúng thứ tự (ruleset trước, để PR đầu tiên đã
bị chặn đúng luật).

## 1. Ruleset nhánh `main`

**Ai:** admin repo (`mungvu999-ctrl`). **Ở đâu:** Settings → Rules → Rulesets → New
branch ruleset, target `main` (hoặc pattern `main`).

Bật:

- **Restrict deletions** — cấm xoá `main`.
- **Block force pushes** — cấm force push.
- **Require linear history** — bắt lịch sử thẳng (không merge commit lẫn vào,
  chỉ squash — mục 3).
- **Require a pull request before merging**:
  - Required approvals: `0` khi làm một mình (GitHub không cho tự duyệt PR của
    chính mình — đặt `1` không có tác dụng khi chỉ một người, PR sẽ kẹt mãi),
    nâng lên `1` ngay khi có người thứ hai;
  - **Dismiss stale pull request approvals when new commits are pushed** — bật;
  - **Require conversation resolution before merging** — bật (giải quyết hết
    hội thoại review).
- **Require status checks to pass**: chọn **mọi** job của `ci.yml` (`lint`,
  `typecheck`, `unit`, `integration`, `ml`, `contract`, `build`, `coverage`),
  job `commits` của `commits.yml` và job `analyze` của `codeql.yml` (mỗi ngôn ngữ trong `matrix`
  hiện thành một check riêng — chọn cả hai); bật **Require branches to be up
  to date before merging**.
- **Bypass list**: thêm **Repository admin** — nếu không, ngoại lệ hẹp của
  R-36 (commit thẳng lên `main` cho `DEBT.md` và `docs/reviews/*`, không qua
  cổng nào vì không phải mã) sẽ bị chính ruleset này chặn, vì nó không phân
  biệt "commit hành chính" với mã (`/merge-review` lượt 1 #11).

**Kiểm bằng `gh api`:**

```bash
gh api repos/mungvu999-ctrl/AppBack/rulesets --jq '.[] | {id, name, target}'
gh api repos/mungvu999-ctrl/AppBack/rulesets/<id>
```

## 2. Ruleset tag `v*`

**Ai:** admin repo. **Ở đâu:** Settings → Rules → Rulesets → New tag ruleset,
target pattern `v*`.

Bật **Restrict creations**, **Restrict updates**, **Restrict deletions**, giới
hạn bypass list chỉ **Repository admin** — tag `v*` kích hoạt luồng production
(B0-10), không ai khác được tạo/sửa/xoá.

**Kiểm:**

```bash
gh api repos/mungvu999-ctrl/AppBack/rulesets --jq '.[] | select(.target=="tag")'
```

## 3. Cài đặt chung (General → Pull Requests)

**Ai:** admin repo. **Ở đâu:** Settings → General → Pull Requests.

- Tắt **Allow merge commits**, tắt **Allow rebase merging** — chỉ bật **Allow
  squash merging**.
- **Default commit message** cho squash: chọn **"Pull request title and commit
  details"** — giữ được trailer `Prompt:` (và `Fix:` nếu có) của các commit gốc
  trong thân commit squash, để `git log --grep 'Prompt:'` còn tra được chủ sau
  khi gộp (BE-00 §13.2).
- Bật **Automatically delete head branches** — tự xoá nhánh sau khi gộp.
- PR của Dependabot hệ `docker` (`directory: /deploy/docker`): Dependabot nối
  thêm `" in /deploy/docker"` vào tiêu đề, dễ vượt 72 ký tự — người điều phối
  rút gọn tiêu đề PR về ≤ 72 ký tự **trước khi** squash (job `commits` chỉ
  miễn dòng đầu từng commit cho nhánh `dependabot/**`, không miễn tiêu đề PR —
  `/merge-review` lượt 1 #4). `commits.yml` khai `pull_request.types` gồm
  `edited` (NO-110): sửa tiêu đề PR ngay trên GitHub tự chạy lại job `commits`
  với tiêu đề mới, không cần đẩy commit rỗng hay bấm re-run (mặc định
  `pull_request` không có `edited` sẽ giữ payload cũ). `ci.yml` không có
  `edited` (NO-182): check `skipped` trùng tên sẽ thoả required check.
- Nhóm Dependabot đặt tên ngắn (`uv`, `actions`, `docker`) để tiêu đề PR nhóm còn ≤ 72 ký tự
  (L8-N02; `test_github_config.py` kiểm công thức). PR **đơn lẻ** của gói tên dài (vd
  `opentelemetry-exporter-otlp-proto-grpc`) vẫn có thể vượt 72: sửa tiêu đề PR trước khi squash.

**Kiểm:**

```bash
gh api repos/mungvu999-ctrl/AppBack --jq '{allow_squash_merge, allow_merge_commit, allow_rebase_merge, squash_merge_commit_title, delete_branch_on_merge}'
```

## 4. Bảo mật (Settings → Code security)

**Ai:** admin repo.

- **Dependency graph** — bật.
- **Dependabot alerts** — bật.
- **Dependabot security updates** — bật (PR vá tự động cho lỗ hổng đã biết,
  cộng thêm lịch cập nhật thường của `dependabot.yml`).
- **Secret scanning** — bật.
- **Secret scanning → Push protection** — bật (chặn push khi phát hiện bí mật
  dạng biết trước, bổ sung cho `gitleaks` chạy ở job `lint`, hai lớp khác
  nguồn luật).
- **Code scanning** — dùng **workflow có sẵn** (`codeql.yml` của prompt này);
  **không** bấm "Set up" → "Default" (default setup sẽ tạo thêm một workflow
  CodeQL do GitHub quản lý, chạy trùng và có thể ghim SHA khác nhánh này).

**Kiểm:**

```bash
gh api repos/mungvu999-ctrl/AppBack/vulnerability-alerts        # 204 = Dependabot alerts bật
gh api repos/mungvu999-ctrl/AppBack/automated-security-fixes    # bật security updates
gh api repos/mungvu999-ctrl/AppBack | jq '.security_and_analysis'
```

## 5. Actions (Settings → Actions → General)

**Ai:** admin repo.

- **Workflow permissions** — chọn **Read repository contents permission**
  (mặc định `GITHUB_TOKEN` chỉ đọc; mọi job trong `ci.yml`/`codeql.yml` đã tự
  khai `permissions:` cần dùng ở mức job, không dựa vào mặc định rộng).
- **Fork pull request workflows** — bật **Require approval for all outside
  collaborators** (hoặc chặt hơn) — PR từ fork phải được duyệt thủ công trước
  khi Actions chạy, tránh lộ `ACTIONS_RUNTIME_TOKEN`/cache qua PR độc hại.

**Kiểm:**

```bash
gh api repos/mungvu999-ctrl/AppBack/actions/permissions/workflow
gh api repos/mungvu999-ctrl/AppBack/actions/permissions/access
```

## 6. Chạy job trên máy

Mọi job gọi `tools/ci/job.sh <job> [--no-scan]` — chạy được cả trên runner
GitHub lẫn tại chỗ:

```bash
# Trong container verify (Python/coverage/mypy/ruff/pytest có sẵn ở đó):
bash tools/verify/run.sh shell
bash tools/ci/job.sh lint        # hay typecheck, unit, integration, ml, contract, commits, coverage

# `build` cần Docker thật (Docker Desktop) — chạy trực tiếp trên Git Bash,
# KHÔNG trong container verify (ảnh verify không có docker CLI):
bash tools/ci/job.sh build
bash tools/ci/job.sh build --no-scan   # bỏ trivy — B0-10 dùng cho diễn tập khôi phục
```

Bảng trạng thái từng bước con in ra stdout; job đọc `$GITHUB_STEP_SUMMARY` khi
biến này có (trên runner) và tự bỏ qua khi chạy tại chỗ.

Job `unit`/`integration`/`ml` chạy `pytest -n <N> --dist loadfile --cov` (xdist, như bước 5 của
verify), ghi độ phủ vào `.coverage.<nhóm>`. `N` mặc định 3 (`ml`: 2); đặt `CI_PYTEST_WORKERS=<số>`
để đổi. Bước `build` kiểm `nginx -v` của ảnh `web` với sàn bản vá **theo dòng phát hành**
(`nginx_floor_for_line` trong `job.sh`); đổi dòng nginx của `web.Dockerfile` thì tra
nginx.org/en/security_advisories.html rồi thêm dòng sàn mới, nếu không job `build` hỏng kín.
