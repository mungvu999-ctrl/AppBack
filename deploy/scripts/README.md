# `deploy/scripts` — hướng dẫn cho người

Tài liệu này dành cho người vận hành (không phải CI). Mỗi mục ghi **ai làm,
lệnh, cách kiểm** — làm đúng thứ tự vì mục sau có thể cần mục trước (vd secret
GitHub cần VPS đã có SSH key). Tham chiếu biến môi trường đầy đủ: bảng ở
[§ Biến môi trường](#biến-môi-trường) (theo `hop-dong.md` §1).

## 1. Chuẩn bị VPS

**Ai:** người vận hành có quyền tạo máy chủ (ngoài phạm vi CI/Orca — không
agent nào tự làm việc này).

1. Tạo VPS Linux (Debian/Ubuntu khuyến nghị), cài Docker Engine + Docker
   Compose plugin.
2. Tạo người dùng `deploy`, thêm vào nhóm `docker` (không `sudo` cho các script
   triển khai — chúng chạy bằng `deploy`, không leo quyền):
   ```bash
   sudo adduser --disabled-password deploy
   sudo usermod -aG docker deploy
   ```
3. Mở tường lửa **chỉ** ba cổng: `22` (SSH), `80`, `443` (HTTP/HTTPS — nginx
   redirect 80→443 và phục vụ 443 thật).
4. Tạo `/opt/appback/` (chủ `deploy`). Mọi thứ bên trong do `deploy.yml` và các script
   tự tạo (K-35 — "bó phát hành" bất biến theo tag):
   ```
   /opt/appback/
     releases/<tag>/deploy/   # bó của từng tag (bundle.sh dựng, deploy.yml giải nén):
       compose/{base.yml,prod.yml}  minio/  scripts/  backup/
     current -> releases/<tag> # symlink tới bản đang chạy (deploy.sh/rollback.sh đổi sau smoke)
     release.env              # IMAGE_TAG=<tag đang chạy> — nguồn sự thật duy nhất của tag
     current_tag              # tag đang chạy (deploy.sh ghi; previous_tag đọc từ đây)
     previous_tag             # tag trước đó (rollback.sh đọc mặc định)
   ```
   ```bash
   sudo install -d -o deploy -g deploy /opt/appback
   ```
   Mỗi lần deploy để lại một `releases/<tag>/` (~vài trăm KB); xoá tay bản cũ khi cần, **không**
   xoá bản mà `current` hay `previous_tag` đang trỏ (rollback cần compose của chính bản đó).
5. Tạo `/etc/appback/appback.env` (chủ `deploy`, quyền **600** — chứa
   `SECRET_KEY`, mật khẩu Postgres, khoá MinIO...):
   ```bash
   sudo install -o deploy -g deploy -m 600 /dev/null /etc/appback/appback.env
   ```
   - **Không** đặt `IMAGE_TAG` trong tệp này: tag do `deploy.sh` ghi vào
     `/opt/appback/release.env`; mọi script (`deploy.sh`, `rollback.sh`, `backup.sh`,
     `restore.sh`) tự nạp cả hai tệp cho compose (`COMPOSE_ENV_FILES`) và **thoát 2** nếu
     appback.env có dòng `IMAGE_TAG=` (L5-N07).
   - Đặt `COMPOSE_PROFILES` theo dịch vụ muốn chạy, vd `COMPOSE_PROFILES=minio,ml` (MinIO tự
     chạy trên VPS + ML CPU). `deploy.sh` tự dựng hạ tầng theo profile (postgres, hai redis;
     minio + minio-init khi có `minio`) và đổi mọi dịch vụ ứng dụng compose liệt kê (worker,
     beat, web; ml, ml-train khi có `ml`). Dùng S3 ngoài thì bỏ `minio`.
6. Tạo `/etc/appback/ml.env` (chủ `deploy`, quyền **600**) — `env_file` riêng
   của `ml`/`ml-gpu` (NO-085), chép từ `deploy/compose/ml.env.example`:
   ```bash
   sudo install -o deploy -g deploy -m 600 /dev/null /etc/appback/ml.env
   ```
   Chỉ chứa `ML_BACKEND`, `ML_MODELS_DIR`, `ML_ORT_THREADS`, `METRICS_HOST`,
   `METRICS_PORT`, `METRICS_MAX_SERIES`. **KHÔNG** chép `SECRET_KEY`,
   `DATABASE_URL`, `SMTP_*`/`MAIL_*`, `REDIS_CACHE_URL`, `PUBLIC_BASE_URL` hay
   khoá MinIO gốc vào đây — `ml` nạp trọng số ngoài, không được cầm khoá ký JWT
   hay DSN (BE-00 §2.1/§9). `APP_ENV`, `REDIS_BROKER_URL`, `S3_ML_*`, `ML_DEVICE`
   vẫn đặt trong `appback.env`: `environment:` của `base.yml` chuyển đúng các biến
   đó vào `ml` và đè `ml.env`.

**Kiểm:**
```bash
ssh deploy@<host> 'id; ls -la /opt/appback; stat -c "%a %U" /etc/appback/appback.env /etc/appback/ml.env'
# id có nhóm docker; cả hai tệp → "600 deploy"
ssh deploy@<host> "grep -vE '^(#|\$|ML_|METRICS_)' /etc/appback/ml.env"
# không in dòng nào (grep thoát 1): danh sách CHO PHÉP, nên biến lạ nào lọt vào cũng bị bắt
```

## 2. Khoá SSH riêng cho CI

**Ai:** người vận hành.

1. Sinh một cặp khoá **chỉ dùng cho CI** (không dùng khoá cá nhân):
   ```bash
   ssh-keygen -t ed25519 -f ci-appback -C "ci@appback" -N ""
   ```
2. `cat ci-appback.pub` → thêm vào `~deploy/.ssh/authorized_keys` trên VPS.
3. Lấy `known_hosts` cho host thật (**không** dùng `StrictHostKeyChecking=no`
   — hợp đồng B0-10 §6 bắt `StrictHostKeyChecking=yes`):
   ```bash
   ssh-keyscan -t ed25519 <host> > known_hosts_appback
   ```

**Kiểm:**
```bash
ssh -i ci-appback -o UserKnownHostsFile=known_hosts_appback deploy@<host> 'echo ok'
```

## 3. Secret GitHub

**Ai:** admin repo GitHub. **Ở đâu:** Settings → Environments (mục 5) rồi
Settings → Secrets and variables → Actions, hoặc secret theo từng
Environment.

| Secret | Giá trị | Dùng ở |
|---|---|---|
| `STAGING_SSH_KEY` | nội dung `ci-appback` (khoá **riêng**) | job `staging` của `deploy.yml` |
| `STAGING_HOST` | host hoặc IP VPS staging | " |
| `STAGING_KNOWN_HOSTS` | nội dung `known_hosts_appback` | " |
| `PRODUCTION_SSH_KEY` | khoá riêng cho production (khuyến nghị khác staging) | job `production` |
| `PRODUCTION_HOST` | host hoặc IP VPS production | " |
| `PRODUCTION_KNOWN_HOSTS` | `known_hosts` của host production | " |
| `ALERT_WEBHOOK_URL` | URL webhook cảnh báo — mục 4; đặt ở **Environment `alerts`** (không phải secret repo) | `notify.yml` (`environment: alerts`), `alert.sh`, `healthcheck.sh` |
| `BACKUP_TARGET` | đường đích sao lưu (cục bộ hoặc điểm gắn đã đồng bộ ra xa) | `backup.sh` trên VPS |
| `BACKUP_AGE_RECIPIENT` | khoá công khai `age` — mục 6 | `backup.sh` trên VPS |

Thiếu `STAGING_*`/`PRODUCTION_*` → `deploy.yml` tự bỏ qua job đó với
`::notice::`, thoát 0 (không đỏ CI) — đúng hành vi khi VPS chưa có.

**Kiểm:** `gh secret list --env staging`, `gh secret list --env production`
(cần quyền admin repo).

## 4. Lấy `ALERT_WEBHOOK_URL`

Một URL duy nhất dùng chung cho `healthcheck.sh` và `notify.yml`; thân gửi
luôn có cả `text` (Slack) và `content` (Discord) — chọn nền tảng nào cũng
chạy được không cần đổi mã.

**Telegram:**
1. Tạo bot qua [@BotFather](https://t.me/BotFather) → `/newbot` → lấy token.
2. Lấy `chat_id`: gửi một tin cho bot rồi gọi
   `https://api.telegram.org/bot<token>/getUpdates`, đọc `message.chat.id`.
3. URL: `https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat_id>`.

**Discord:** Server settings → Integrations → Webhooks → New Webhook → copy
URL (dạng `https://discord.com/api/webhooks/…`).

**Slack:** tạo Incoming Webhook cho app Slack (`https://hooks.slack.com/services/…`).

**Gửi thử (cả ba nền tảng đọc được thân này):**
```bash
curl --fail --max-time 10 -H 'Content-Type: application/json' \
  -d '{"text":"thử ALERT_WEBHOOK_URL","content":"thử ALERT_WEBHOOK_URL"}' \
  "$ALERT_WEBHOOK_URL"
```

**Đặt secret (người, một lần):** `gh secret set ALERT_WEBHOOK_URL --env alerts` rồi dán URL từ bàn phím (không đưa
vào chat/log); khi có VPS thêm dòng `ALERT_WEBHOOK_URL=…` vào `/etc/appback/appback.env`.

**Gửi tin thử bằng chính mã của repo (L5-N03):**
```bash
gh workflow run notify.yml -f message="thử cảnh báo"   # từ GitHub Actions: tin tới kênh trong <= 60 giây
deploy/scripts/healthcheck.sh --test-alert              # trên VPS, sau khi nạp appback.env
deploy/scripts/alert.sh --test-alert                    # cùng hàm `alert()`
```
Cả hai cờ thoát 0; webhook hỏng chỉ in cảnh báo ra stderr. `alert()` nằm ở `deploy/scripts/alert.sh`;
`healthcheck.sh` (và các script sau này) `source` nó.

**Kênh dự phòng khi webhook rỗng hoặc POST hỏng:** `notify.yml` không im lặng — nó in `::warning::` (lượt hiện màu
cảnh báo) và mở một **issue nhãn `alert`** bằng `GITHUB_TOKEN` (quyền `issues: write` chỉ ở mức job; workflow-level
vẫn `{}`). Lần hỏng sau của cùng workflow được **bình luận vào issue đang mở** (tiêu đề `Cảnh báo: <tên workflow>`),
không tạo issue mới; chủ repo nhận email của GitHub. Thử: tạm xoá secret rồi
`gh workflow run notify.yml -f message=thu` → có issue `Cảnh báo: Notify (tin thử)`. Đóng issue khi đã xử lý xong.

## 5. GitHub Environment `staging`, `production`

**Ai:** admin repo. **Ở đâu:** Settings → Environments → New environment.

- `staging`: không bắt buộc người duyệt (deploy tự động sau CI xanh trên
  `main`), nhưng gắn `STAGING_*` secret ở đây (phạm vi environment, không lộ
  cho job khác).
- `production`: bật **Required reviewers** — ít nhất một người phải bấm
  duyệt trước khi job `promote`/`production` chạy (K4). Gắn `PRODUCTION_*` +
  không cho môi trường này chạy trên nhánh khác ngoài tag `v*` (deployment
  branch policy → "Selected branches and tags" → thêm pattern `v*`).

**Kiểm:** Settings → Environments → `production` → xác nhận "Required
reviewers" đã bật và có ít nhất một người.

## 6. Gói GHCR (GitHub Container Registry)

**Ai:** admin repo (đặt public) hoặc người vận hành VPS (nếu để private).

- **Cách A — public:** sau lượt đẩy ảnh đầu tiên, vào
  `github.com/mungvu999-ctrl?tab=packages` → từng gói `appback-{api,worker,ml,web}`
  → Package settings → Change visibility → Public. VPS `docker pull` không
  cần đăng nhập.
- **Cách B — giữ private:** tạo Personal Access Token (classic) phạm vi
  `read:packages`, rồi trên VPS:
  ```bash
  echo "<PAT>" | docker login ghcr.io -u <tài khoản GitHub> --password-stdin
  ```
  (chạy một lần, Docker lưu credential ở `~deploy/.docker/config.json`).

**Kiểm:** `docker pull ghcr.io/mungvu999-ctrl/appback-api:<tag>` trên VPS không
hỏi mật khẩu và tải được.

## 7. Tên miền, chứng chỉ TLS

**Ai:** người vận hành.

1. Trỏ DNS của tên miền (vd `app.example.com`) về IP VPS.
2. Lấy chứng chỉ TLS (vd `certbot`) — **phải phủ cả host phục vụ tệp tĩnh
   MinIO** (vhost console/API MinIO nếu lộ ra ngoài qua cùng domain hoặc
   subdomain riêng, tuỳ cấu hình `S3_PUBLIC_ENDPOINT`).
3. Đặt thư mục chứng chỉ vào `TLS_CERT_DIR` (mặc định
   `/etc/appback/tls`, biến trong `appback.env`) — `prod.yml` mount thư mục
   này vào `web` (nginx 8443 HTTPS thật, khác cổng 8080 dev/ci).

**Kiểm:** `curl -vI https://<domain>/api/health` không cảnh báo chứng chỉ.

## 8. Khoá `age` và bản sao `SECRET_KEY`

**Ai:** người vận hành. **Bắt buộc mã hoá trước khi bản sao lưu rời máy** —
không có ngoại lệ.

1. Sinh cặp khoá `age` (không cài trên VPS nếu không cần giải mã tại chỗ):
   ```bash
   age-keygen -o appback-backup.key   # in ra "# public key: age1…"
   ```
2. Đặt khoá công khai vào secret `BACKUP_AGE_RECIPIENT` (mục 3) và biến
   `BACKUP_AGE_RECIPIENT` trong `appback.env` trên VPS (chỉ khoá công khai —
   `backup.sh` chỉ cần khoá này để mã hoá).
3. **Khoá riêng** (`appback-backup.key`) và **bản sao `SECRET_KEY`** (từ
   `appback.env`) cất ở nơi **ngoài** VPS (kho mật của tổ chức, két vật lý,
   máy quản trị riêng) — mất VPS không kéo theo mất khả năng giải mã hoặc mất
   khả năng xác thực phiên đã ký.
4. Khi cần khôi phục thật: đặt `BACKUP_AGE_IDENTITY=<đường khoá riêng>` tạm
   thời trước khi gọi `restore.sh`, xoá lại sau khi xong.

**Kiểm:** `backup.sh` chạy xong, mở một tệp `.age` trong bản sao lưu bằng
`age -d -i appback-backup.key <tệp>.age | head -c1` (đọc được vài byte, không
lỗi "no identity matched").

## 9. Bật lịch sao lưu và cảnh báo sức khoẻ

**Ai:** người vận hành, trên VPS.

Unit systemd nằm ở `deploy/backup/systemd/` (`appback-backup.service`,
`appback-backup.timer`) và `deploy/scripts/systemd/`
(`appback-health.service`, `appback-health.timer`) — chép vào
`/etc/systemd/system/`, rồi:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now appback-backup.timer appback-health.timer
```

Cả hai unit chạy script của **bản đang chạy** (`/opt/appback/current/deploy/…`, gọi qua
`/bin/bash` vì bó giữ mode 644); `backup.sh` lấy tag từ `/opt/appback/release.env` — không
cần (và không được) đặt `IMAGE_TAG` trong appback.env. Chưa deploy lần nào thì `backup.sh`
thoát 2 "chưa có bản phát hành nào".

- `appback-backup.timer`: chạy `backup.sh` 02:30 hằng ngày (giờ hệ thống VPS).
- `appback-health.timer`: chạy `healthcheck.sh` mỗi 5 phút — `/api/ready`
  hỏng hai lần liên tiếp hoặc đĩa > 85% → POST `ALERT_WEBHOOK_URL`. `healthcheck.sh --test-alert` gửi
  một tin thử (xem §4).
- Bản sao lưu tạo trước khi `backup.sh` chạy bằng `deploy` (umask 077, `mc --user`) có thể còn
  thư mục `objects/**` thuộc root, làm xoay vòng hỏng EPERM: một lần khi nâng cấp,
  `sudo chown -R deploy: "$BACKUP_TARGET"`.

**Kiểm:**
```bash
systemctl list-timers | grep appback
journalctl -u appback-health.service -n 20   # có dòng chạy gần nhất
```

## 10. Deploy / rollback tay

**Ai:** người vận hành (bình thường CI làm qua `deploy.yml`; mục này cho lúc
CI không chạy được).

```bash
# Máy có checkout đúng commit của tag: dựng bó rồi chép lên VPS.
bash deploy/scripts/bundle.sh /tmp/deploy-bundle-sha-abc123def456.tar.gz
scp /tmp/deploy-bundle-sha-abc123def456.tar.gz deploy@<host>:/opt/appback/

# Trên VPS, người dùng deploy:
t=sha-abc123def456; d=/opt/appback/releases/$t
mkdir -p "$d" && tar -xzf /opt/appback/deploy-bundle-$t.tar.gz -C "$d"
bash "$d/deploy/scripts/deploy.sh" "$t"             # deploy (máy trống cũng chỉ cần lệnh này)
bash "$d/deploy/scripts/deploy.sh" "$t" --dry-run   # chỉ in kế hoạch, không đổi gì
bash /opt/appback/current/deploy/scripts/rollback.sh          # về previous_tag
bash /opt/appback/releases/v1.2.2/deploy/scripts/rollback.sh v1.2.2   # về tag chỉ định
```

**Lần đầu trên máy trống** (L5-N01): sau khi có appback.env, ml.env và chứng chỉ, `deploy.sh
<tag>` là lệnh **duy nhất** — nó tự `up -d --wait` postgres + hai redis (+ minio, minio-init
khi `COMPOSE_PROFILES` có `minio`) trước migrate, rồi tạo mới api, worker, beat, web (+ ml).
Lần sau bước này không tạo lại gì nếu cấu hình hạ tầng không đổi.

Thứ tự `deploy.sh`: kiểm tag → có `releases/<tag>/` chưa (không thì thoát 2) → kiểm appback.env
→ pull → dựng hạ tầng → migrate → publish thư viện → đổi api không gián đoạn → đổi dịch vụ ứng
dụng → smoke → ghi `release.env`, symlink `current`, `current_tag`. Hỏng sau migrate thì gọi
`rollback.sh` **của bản trước** (`releases/<previous_tag>/…`) — lùi cả compose lẫn ảnh.
`rollback.sh <tag>` dùng compose của `releases/<tag>` và đổi api + **mọi** dịch vụ ứng dụng
(cả web, ml). `web` giữ cổng 80/443 nên không đổi kiểu hai bản như api: mỗi lần deploy/rollback
có vài giây gián đoạn HTTP lúc container web được tạo lại (chấp nhận ở F1).

`deploy.sh` tự chạy `python -m apps.api.library.cli publish` sau migrate (thư viện `.glb`
không rỗng tới lượt lịch đầu); hỏng bước này chỉ cảnh báo, beat publish lại theo lịch.
Khi triển khai tay không qua `deploy.sh`, chạy lệnh đó sau migrate + seed.
`restore.sh` **không** chạy `publish`: nó đổ lại cả CSDL lẫn bucket/volume object từ cùng một
bản sao lưu nên thư viện `.glb` đã có đủ; chạy lại `publish` chỉ cần khi khôi phục sang kho
object trống.

**Kiểm:** `cat /opt/appback/release.env`; `readlink /opt/appback/current`;
`curl -fsS http://127.0.0.1/api/health`; `docker compose ps --format '{{.Service}} {{.Image}}'`
(chạy với `COMPOSE_FILE=/opt/appback/current/deploy/compose/prod.yml` và
`COMPOSE_ENV_FILES=/etc/appback/appback.env,/opt/appback/release.env`) chỉ có một `api` và mọi
dịch vụ ứng dụng cùng một tag.

## 11. Chạy `drill.sh` ở mỗi mốc M1–M4

**Ai:** người vận hành (hoặc CI qua `restore-drill.yml` hằng tuần).

```bash
bash tools/ci/job.sh build --no-scan   # dựng ảnh IMAGE_TAG=ci cục bộ trước
bash deploy/scripts/drill.sh           # diễn tập trên deploy/compose/ci.yml
```

Đọc bảng so sánh in ra cuối lượt (`bảng/đối tượng | gốc | pha A | pha B |
khớp`) — mọi dòng phải `có`; mã thoát khác 0 nghĩa là khôi phục không đúng,
**dừng lại điều tra trước khi đi tiếp mốc M**. Chạy lại ở mỗi mốc M1 (compose
tại chỗ), M2–M4 (khi đã có VPS staging/production) để chắc quy trình sao
lưu/khôi phục vẫn đúng sau mỗi đợt đổi lớn.

## 12. Việc người phải làm trước M1

- [ ] VPS (§1): Docker, người dùng `deploy`, tường lửa 22/80/443, thư mục
      `/opt/appback/`, `appback.env` (có `COMPOSE_PROFILES`, không có `IMAGE_TAG`) và `ml.env`
      quyền 600.
- [ ] `appback.env` có `SMTP_HOST` và `MAIL_FROM` (bắt buộc — thiếu thì `docker compose` trên prod
      hỏng ngay) cùng các biến `SMTP_*`/`MAIL_*` còn lại (bảng "Biến môi trường").
- [ ] Khoá SSH riêng cho CI + `known_hosts` (§2).
- [ ] Secret GitHub `STAGING_*`, `BACKUP_TARGET`, `BACKUP_AGE_RECIPIENT` (§3);
      `ALERT_WEBHOOK_URL` đặt ở Environment `alerts` (§4).
- [ ] Environment `staging` tạo, gắn secret (§5).
- [ ] Gói GHCR public hoặc PAT + `docker login` trên VPS (§6).
- [ ] Tên miền + chứng chỉ TLS phủ cả vhost MinIO (§7).
- [ ] Cặp khoá `age` sinh xong; khoá riêng + bản sao `SECRET_KEY` cất ngoài
      VPS; `BACKUP_AGE_RECIPIENT` đã đặt (§8).
- [ ] `appback-backup.timer`, `appback-health.timer` bật (§9).
- [ ] `bash deploy/scripts/drill.sh` chạy đạt trên máy tại chỗ (M1, không cần
      VPS — dùng `deploy/compose/ci.yml`).

`production` (M3+) còn cần thêm: Environment `production` có người duyệt,
`PRODUCTION_*` secret, VPS production riêng (§1–§8 lặp lại cho máy đó).

## Biến môi trường

Đầy đủ ở `backend/dieu-phoi/chay/B0-10/hop-dong.md` §1; các biến người vận
hành cần đặt tay trong `appback.env` hoặc secret GitHub:

| Biến | Mặc định | Đặt ở đâu |
|---|---|---|
| `APPBACK_DIR` | `/opt/appback` | môi trường shell trên VPS (thường không cần đổi) |
| `APPBACK_ENV_FILE` | `/etc/appback/appback.env` | môi trường shell (thường không cần đổi) — tệp script tự nạp cho compose |
| `COMPOSE_PROFILES` | rỗng (chỉ postgres, redis, api, worker, beat, web) | `appback.env`, vd `minio,ml` (§1) |
| `IMAGE_TAG` | — | **không** đặt tay: `deploy.sh`/`rollback.sh` ghi vào `/opt/appback/release.env` |
| `IMAGE_REGISTRY` | `ghcr.io/mungvu999-ctrl` | `appback.env` (để trống nếu chạy ảnh cục bộ) |
| `APPBACK_BASE_URL` | `http://127.0.0.1` | `appback.env` → domain thật sau khi có TLS (§7) |
| `APPBACK_HEALTH_TIMEOUT_S` | `180` | `appback.env`, chỉnh nếu máy chậm khởi động |
| `ALERT_WEBHOOK_URL` | rỗng | Environment `alerts` của GitHub + `appback.env` (§4) |
| `BACKUP_TARGET` | `/var/backups/appback` | secret GitHub + `appback.env` (§3) |
| `BACKUP_AGE_RECIPIENT` | rỗng → `backup.sh` thoát 1 ở mọi môi trường, trừ khi `BACKUP_ALLOW_PLAINTEXT=1` | secret GitHub + `appback.env` (§8, **bắt buộc** trước khi bản sao lưu rời máy) |
| `BACKUP_ALLOW_PLAINTEXT` | rỗng — chỉ `1` mới cho sao lưu bản rõ (dev, diễn tập; không đặt trên VPS) | `drill.sh` tự đặt; không vào `appback.env` |
| `BACKUP_AGE_IDENTITY` | rỗng | chỉ đặt tạm lúc khôi phục thật (§8) |
| `SMTP_HOST` | **bắt buộc** | `appback.env` — máy chủ SMTP; thiếu thì mọi lệnh `docker compose` trên prod hỏng ngay lúc nội suy (`base.yml`) |
| `MAIL_FROM` | **bắt buộc** | `appback.env` — địa chỉ người gửi; thiếu như `SMTP_HOST` |
| `MAIL_BACKEND`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_TIMEOUT_S` | `smtp`, `587`, `true`, rỗng, rỗng, `10` | `appback.env` (rỗng cả hai `SMTP_USERNAME`/`SMTP_PASSWORD` = SMTP không xác thực) |
| `APPBACK_STORAGE` | `s3` | `appback.env` (`local` nếu dùng volume `local-storage` thay MinIO) |
| `APPBACK_API_SWAP_SETTLE_S` | `11` | không cần đặt tay — chỉnh chỉ khi đổi `valid=…` của `resolver` trong `deploy/nginx/templates/{dev,prod}/app.conf.template` (>= TTL mới + 1s biên); `0` để tắt khi kiểm |
