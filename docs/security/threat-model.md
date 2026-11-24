# Mô hình mối đe doạ — B7-02

Commit đã soát `2a63cfc5420f`. Dẫn chiếu `tệp:dòng` trỏ lại theo mã sau DEBT-02 W2–W9 (nhánh `fix/debt-02-w8-charter-docs`, FIX-311); dòng `lỗ` và chỗ ghi `(ở `2a63cfc`)` giữ số dòng ở commit soát vì mô tả mã trước khi sửa (xem `fixes/SEC-*.md`). Biện pháp dẫn `#` của `asvs-checklist.md`.

## Tài sản

- Tài khoản và phiên: băm mật khẩu argon2id, token refresh (chỉ SHA-256 trong DB), access token, cookie refresh/luồng, token một lần (lời mời, đặt lại).
- `SECRET_KEY` và khoá con HKDF (`access`, `stream`, `refresh`, `file`, `token`, `cursor`, `lookup`), `SECRET_KEY_PREVIOUS`.
- Dữ liệu dự án: bản vẽ, tầng, tài liệu không gian, phiên bản, đo đạc trong Postgres và MinIO; URL ký `/api/files`.
- Tài sản ML: trọng số ghim SHA-256, dataset (CubiCasa5K), job huấn luyện, phiên bản mô hình kích hoạt.
- Secret CI/triển khai (SSH, webhook), ảnh GHCR, bản sao lưu mã hoá `age`.
- Tính sẵn sàng: hàng đợi Celery, Redis, executor băm, GPU.

## Ranh giới tin cậy

1. trình duyệt ↔ nginx
2. nginx ↔ api
3. api ↔ Postgres, redis-broker, redis-cache, MinIO
4. api → hàng đợi → worker
5. worker ↔ ml
6. tiến trình con huấn luyện
7. quản trị viên → N26 (trọng số), CLI CubiCasa (tệp nén)
8. quản trị viên → admin/*
9. /api/telemetry công khai
10. URL ký và /api/files
11. CI → GHCR → VPS
12. sao lưu và khôi phục

## STRIDE theo ranh giới

### trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền)

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền) | S | Mạo danh: dò mật khẩu, nhồi thông tin đăng nhập, đánh cắp cookie refresh, giả access token | A-01, A-03, A-05, A-06, A-08, A-09, A-16, A-18 | Hạn mức đăng nhập theo IP gom /64; kẻ có nhiều IPv4 vẫn thử được 5 lần/(email, IP)/900 s, chặn bởi hạn mức mềm theo email (A-05) · B1-01 |
| trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền) | T | CSRF lên route ghi dùng cookie; sửa id trên đường/thân để ghi chỗ khác | A-10, A-23; header/CSP/HSTS: D-01, D-06, D-07 | — |
| trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền) | R | Người dùng chối đã đăng nhập/refresh; dùng lại token không để vết | A-08 (log `refresh_reuse_revoked` có `sid`, `userId`), A-05 (log `login_throttled` chỉ `emailKey`) | Không có nhật ký đăng nhập thành công bền (chỉ `last_active_at`, `created_ip`) · B1-01 |
| trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền) | I | Lộ sự tồn tại tài khoản (dò email), lộ dự án/tài nguyên người khác qua 403 thay 404 | A-03, A-04, A-19, A-21 | — |
| trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền) | D | Dồn đăng nhập làm cạn CPU argon2; Redis cache chập chờn làm FE đăng xuất mọi thẻ | A-05, A-11 (`on_error="open"` cho refresh), `apps/api/auth/passwords.py:81-92` (executor riêng, chờ > 2 s → 503) | Postgres hỏng → refresh 503 → FE đăng xuất mọi thẻ (`refresh.ts:243,265`); không tránh được ở BE · B1-01 / phía FE (F-*) |
| trình duyệt ↔ nginx/`api` (xác thực, phiên, quyền) | E | Người thường gọi route admin; giữ quyền cũ sau khi bị hạ vai/vô hiệu | A-14, A-15, A-22, A-24 | Cửa sổ ≤ 5 s cache `Principal` (đúng hiến chương); ảnh chụp đọc từ DB rồi mới ghi cache, một lần hạ vai commit đúng giữa hai bước kéo dài thêm ≤ 5 s (`apps/api/auth/sessions.py:613-618`) — chặn bởi `bump_token_version` (A-15) · B1-01 |

### quản trị viên → `admin/*

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| quản trị viên → `admin/*` | S | Chiếm phiên admin (cookie bị đánh cắp) để dùng `admin/ml`, `users` | A-08, A-09, A-10, A-16 | Không có xác thực bước hai (MFA) cho admin — ASVS cấp 2 khuyến nghị; ngoài phạm vi v1 · B1-01 (Nợ hiến chương N-A1) |
| quản trị viên → `admin/*` | T | Admin sửa vai/trạng thái người khác sai đích (đường ≠ thân); tự hạ vai admin cuối cùng | A-23; `apps/api/users/service.py:148-157` (`WriteScope.forbid_self`: tự sửa mình → `USER_SELF_MODIFICATION`, admin `active` duy nhất → `USER_LAST_ADMIN`; gọi ở `:289,302,450`) | — |
| quản trị viên → `admin/*` | R | Admin chối đã đổi vai/vô hiệu | `apps/api/users/service.py:293` (`_log_activity` `USER_ROLE_CHANGE`) | Nhật ký hoạt động nằm cùng DB admin ghi được; không chống sửa · B1-05 |
| quản trị viên → `admin/*` | I | Engineer/viewer đọc danh mục mô hình, dataset, job qua `admin/ml` | A-22 | Lỗ có chủ ý v1: admin dựng dataset từ tầng của mọi dự án, xuyên ranh giới thành viên · B6-02 |
| quản trị viên → `admin/*` | D | Admin bị vô hiệu/hạ vai nhầm làm hệ thống mất admin | `apps/api/users/service.py:148-157` (`forbid_self` → `USER_LAST_ADMIN`; `lock_admin_set` `:162` đòi người thực hiện là admin `active`, nên admin cuối chỉ có thể là chính họ); CLI `python -m apps.api.auth.cli create-admin` khôi phục | — |
| quản trị viên → `admin/*` | E | Người bị hạ vai/vô hiệu vẫn dùng token cũ gọi `admin/*` | A-14, A-15, A-22 | xem dòng E ở trên · B1-01 |

### api → hàng đợi → worker

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| api → hàng đợi → worker | S | Giả mạo thông điệp trên Redis/Celery | Payload kiểm bằng `FrozenModel` `extra="forbid"` + `schema_version` (B-18); Redis ở mạng nội bộ compose | Ai ghi được Redis tạo được thông điệp hợp lệ về cấu trúc · B0-08 |
| api → hàng đợi → worker | T | Sửa payload (khoá lạ, id chéo dự án) | `extra="forbid"`, mẫu `ObjectKey`/`Sha256`/`RunId` (B-18); worker kiểm lại dưới `lock_run` (B-19) | không đáng kể · B5-06 |
| api → hàng đợi → worker | R | Chối tác vụ | `pipeline_result_ignored` log có `run_id` (`pipeline_persist/service.py:70`), `record_step` | không đáng kể · B5-06 |
| api → hàng đợi → worker | I | Rò thông tin qua log/lỗi | Log chỉ id và mã (B-22) | không đáng kể · B5-06 |
| api → hàng đợi → worker | D | Dồn hàng bằng tải lên hàng loạt | Một lượt dở/tầng, `rate_limit` 60/phút/người, `prefetch=1` (B-26) | Không hạn mức theo dự án, một hàng `ml.infer` concurrency 1 → dự án khác chờ lâu; đo sống: FIFO concurrency 1, lượt `running` không bị quét bù đánh hỏng (B-26, W10/L-07) · B2-05a/B5-06 |
| api → hàng đợi → worker | E | Payload nâng quyền | Worker tải lại dòng DB, không tin id trong payload (B-19) | không đáng kể · B5-06 |

### worker ↔ ml (kết quả không tin)

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| worker ↔ ml (kết quả không tin) | S | `ml` giả danh lượt/dự án khác | `run_id` + `lock_run` + ghim `pins`; `model_version_id` phải khớp ghim (B-19) | không đáng kể · B5-06c |
| worker ↔ ml (kết quả không tin) | T | Khoá artifact ngoài lượt, sửa kết quả | `_keys_ok` tiền tố `run_prefix/step/` (B-19), `check_key` (B-11); `ml` chỉ ghi `runs/*` theo chính sách (B-24) | Thực thi chính sách MinIO chưa dò sống (B-25) · B0-08 |
| worker ↔ ml (kết quả không tin) | R | `ml` chối kết quả | `duration_ms`, `model_version_id` ghi vào `used` (B-19) | không đáng kể · B5-06c |
| worker ↔ ml (kết quả không tin) | I | `ml` đọc dữ liệu dự án khác | Khoá `ml` chỉ GetObject `pages/*`, `ml/models/*`, `ml/datasets/*` (B-24); không DSN (B-21) | `GetObject` `pages/*` của mọi dự án (suy luận cần) — chấp nhận theo BE-00 §8 · B0-08 |
| worker ↔ ml (kết quả không tin) | D | `ml` treo giữ GPU | Khoá GPU có TTL + gia hạn + `GPU_LOCK_LOST` (B-20); `RLIMIT_AS`/timeout con (B-28) | không đáng kể · B5-01 |
| worker ↔ ml (kết quả không tin) | E | Kết quả độc làm worker ghi sai tầng | Kiểm dưới khoá, kết quả sai → `PIPELINE_RESULT_INVALID` (B-19) | không đáng kể · B5-06 |

### tiến trình con huấn luyện

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| tiến trình con huấn luyện | S | Con giả danh job khác | `claim_token`, gia hạn/xoá chỉ khi đúng token (B-20) | không đáng kể · B6-03b |
| tiến trình con huấn luyện | T | Dữ liệu huấn luyện/`.pt` độc | SHA-256 trước khi nhập `ultralytics`, loại `.pt` ở byte đầu, không `pickle`/`torch.load` (B-14) | không đáng kể · B6-03b |
| tiến trình con huấn luyện | R | Job chối kết quả | Báo `finished` qua cầu nối; log mẫu cố định (B-22) | không đáng kể · B6-03a |
| tiến trình con huấn luyện | I | Con đọc bí mật môi trường | Con thừa hưởng env của `ml` (**B-23 lỗ, SEC-020**); `ml` không có DSN, khoá S3 giới hạn tiền tố | Chưa sửa: con có `S3_ML_SECRET_KEY` + URL Redis (lỗ B-23, `SEC-020`) · B6-03b |
| tiến trình con huấn luyện | D | Con ăn hết RAM/GPU | `start_new_session`, watchdog hết hạn (`training_runner/watchdog.py:107`), khoá GPU TTL (B-20), `mem_limit` compose | Không `RLIMIT_AS` cho con huấn luyện (chỉ `ml_eval` có) · B6-03b |
| tiến trình con huấn luyện | E | Thoát hộp, nâng quyền | Không `shell=True`, đối số cố định (B-15) | Container không root/mạng `internal` không kiểm sống (B-25) · B0-08 |

### quản trị viên → N26 (trọng số)

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| quản trị viên → N26 (trọng số) | S | Giả danh quản trị viên | Kiểm vai trò route admin thuộc nhóm A | — · nhóm A |
| quản trị viên → N26 (trọng số) | T | Trọng số độc (pickle, `external_data`, `Loop`/`Scan`) | B-27: byte đầu + SHA-256 + `has_external_data` + luật cấu trúc | ONNX hợp lệ nhưng sai nghĩa do quản trị viên tin cậy · B6-01 |
| quản trị viên → N26 (trọng số) | R | Chối việc tải lên | Bản ghi `model_versions` có checksum và người tạo | không đáng kể · B6-01 |
| quản trị viên → N26 (trọng số) | I | Lộ trọng số | Chỉ quản trị viên đọc; tải về qua token `attachment` (B-03) | không đáng kể · B6-01 |
| quản trị viên → N26 (trọng số) | D | Tải tệp khổng lồ | `MODEL_UPLOAD_MAX_BYTES`, `METADATA_MAX_BYTES`, parser luồng không gom RAM (B-05) | không đáng kể · B6-01 |
| quản trị viên → N26 (trọng số) | E | Trọng số chạy mã trong `ml` | ONNX chạy trong `onnxruntime`: cấm `external_data`, `Loop`/`Scan`, miền op lạ (B-27); `ml_eval` có hộp (B-28) | Lỗ 0-day của `onnxruntime` · B5-01 (theo dõi bản vá) |

### CLI CubiCasa (tệp nén)

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| CLI CubiCasa (tệp nén) | S | Nguồn zip giả danh | Tệp do người vận hành chỉ định trên máy chủ, không qua mạng | Tin người vận hành · B6-02b |
| CLI CubiCasa (tệp nén) | T | Zip slip, symlink, bom, SVG DTD | B-08, B-09, B-10 | không đáng kể · B6-02b |
| CLI CubiCasa (tệp nén) | R | Chối kết quả nhập | Bộ đếm `unsafe_path`/`bad_id`/`svg_rejected` in ra (`archive.py:173-259`) | không đáng kể · B6-02b |
| CLI CubiCasa (tệp nén) | I | Đọc tệp ngoài cây qua symlink | `O_NOFOLLOW`, `lstat`/`fstat`, `is_relative_to` (B-09) | không đáng kể · B6-02b |
| CLI CubiCasa (tệp nén) | D | Bom giải nén, hết đĩa | Trần số tệp, tổng, từng tệp, tỉ lệ nén, `disk_usage` (B-08) | không đáng kể · B6-02b |
| CLI CubiCasa (tệp nén) | E | Ghi ra ngoài thư mục đích | `resolve()`+`is_relative_to`, mở `"xb"`, không `extractall` (B-08) | không đáng kể · B6-02b |

### api ↔ Postgres

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| api ↔ Postgres | S | Kẻ mạo danh Postgres/dịch vụ DB giả | mạng compose nội bộ, DSN từ môi trường; không tải URL người dùng | tồn dư thấp (TLS tới DB không dùng trong mạng nội bộ) · B0-08 |
| api ↔ Postgres | T | Sửa dữ liệu qua SQL chèn | SQL tham số (ngoài phạm vi C: V5 của việc B); idempotency ghi cùng giao dịch nghiệp vụ (C-40) | — |
| api ↔ Postgres | R | Chối hành vi ghi | log JSON có `requestId`, `routeTemplate` (C-05, C-07); idempotency lưu dấu vết (C-40) | nhật ký kiểm toán nghiệp vụ ngoài phạm vi v1 · B1-01 |
| api ↔ Postgres | I | Lộ bí mật qua log lỗi DB (DSN trong thông điệp) | che khoá và JWT/Bearer (C-05); DSN trong chuỗi tự do (C-06: DSN trong chuỗi tự do không che → SEC-042) |  |
| api ↔ Postgres | D | DB sập hay chậm giữ cạn pool | 503 `DEPENDENCY_UNAVAILABLE` + `Retry-After` (C-03); bộ đếm đăng nhập đặt ở `redis-broker` chứ không ở DB | — |
| api ↔ Postgres | E | Ghi đè idempotency của người khác | khoá idempotency gồm `user_id` (C-40) | — |

### api ↔ redis-broker

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| api ↔ redis-broker | S | Giả broker | mạng nội bộ, URL từ môi trường | tồn dư thấp (không TLS/ACL Redis) · B0-05 |
| api ↔ redis-broker | T | Sửa bộ đếm khoá đăng nhập | Lua nguyên khối `INCR`+`EXPIRE`, kho `noeviction` (C-23) | — |
| api ↔ redis-broker | R | Chối lượt thử đăng nhập | bộ đếm theo (email băm HMAC, IP) (C-23) | — |
| api ↔ redis-broker | I | Lộ email qua khoá Redis | `email_key` = HMAC khoá `lookup` (BE-00 §11), không email thô; thuộc việc A | — |
| api ↔ redis-broker | D | Broker chết → khoá đăng nhập mở | `on_error=closed` cho đăng nhập, N13, N14 (C-23, C-28, C-29): 503 thay vì mở | — |
| api ↔ redis-broker | E | Vượt hạn mức bằng khoá IPv6 xoay | gom IPv6 về /64 (`apps/api/core/ratelimit.py:33-34,68-77`; C-23) | — |

### api ↔ redis-cache

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| api ↔ redis-cache | S | Giả cache | như broker | tồn dư thấp · B0-05 |
| api ↔ redis-cache | T | Đầu độc giá trị cache | cache chỉ chứa bộ đếm refresh/telemetry/chỗ SSE; không chứa quyền | — |
| api ↔ redis-cache | R | Không | không áp dụng — cache không lưu bằng chứng hành vi (bằng chứng ở Postgres/log) | — |
| api ↔ redis-cache | I | Lộ khoá qua `KEYS` | khoá cache theo `sid`/IP băm, không bí mật (C-24) | — |
| api ↔ redis-cache | D | `allkeys-lru` đẩy mất khoá hạn mức | đăng nhập ở `safe`; refresh/telemetry `on_error=open` có chủ ý (C-24, C-34) | hạn mức telemetry/refresh mất khi cache đầy (chủ ý v1) · B7-01, B1-01 |
| api ↔ redis-cache | E | Vượt số kết nối SSE khi cache chết | luồng mở được khi cache chết nhưng vẫn có trần toàn cục theo tiến trình (C-35) | không có trần theo người dùng khi cache chết (tồn dư thấp) · B4-01 |

### api ↔ MinIO

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| api ↔ MinIO | S | Giả MinIO | mạng nội bộ, khoá riêng `ml` theo tiền tố (`deploy/minio/ml-policy.json`; thuộc việc D) | — |
| api ↔ MinIO | T | Ghi đè object bằng khoá `..` | `check_key` chặn `..`/ký tự điều khiển (C-21) | — |
| api ↔ MinIO | R | Chối tải lên | metadata SHA-256 ở `ObjectInfo` (`packages/storage/local.py:108-115`) | — |
| api ↔ MinIO | I | Lộ object cho người ngoài | URL ký ≥ 60 phút, token tệp có MAC (C-19, C-20); S3 khác origin + `nosniff` → việc D | S3 khác origin + vhost `nosniff`/`CSP sandbox` đo sống đạt (D-08, W10/L-01, L-02) · B0-08 |
| api ↔ MinIO | D | MinIO chết/đĩa đầy | 503 `DEPENDENCY_UNAVAILABLE` (C-03); trần thân (C-38) | — |
| api ↔ MinIO | E | `ml` ghi ngoài tiền tố | chính sách MinIO theo tiền tố — thuộc việc D (V1) | — |

### URL ký và `/api/files

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| URL ký và `/api/files` | S | Giả token | HMAC-SHA256 khoá con `file`, `compare_digest` (C-20) | — |
| URL ký và `/api/files` | T | Sửa một byte hay đổi khoá trong token | MAC trên `k\|e\|d\|n`; sửa → 404 (C-20, C-21) | — |
| URL ký và `/api/files` | R | Chối truy cập | token không vào log (cố ý): truy vết bằng `requestId`/`routeTemplate` (C-07) | không truy được ai đã dùng token · B0-04 (chủ ý) |
| URL ký và `/api/files` | I | Token lộ qua log/metric/referrer | log chỉ `routeTemplate` (C-07); nhãn metric là mẫu đường (C-45); `Referrer-Policy: same-origin`; nginx `access_log off` (C-08) | log nginx/uvicorn sống: token không vào log (C-08, W10/L-01) · B0-08 |
| URL ký và `/api/files` | D | Liên tục thử token rác | cùng 404, không chạm đĩa với token hỏng (`verify_token` trước `stat`) — không có hạn mức riêng | không hạn mức IP cho `/api/files` (chi phí HMAC thấp) · B0-06 |
| URL ký và `/api/files` | E | Đọc object ngoài khoá được ký | khoá nằm trong MAC; `..` bị chặn; `inline` chỉ ảnh đã sniff (`apps/api/files/router.py:51-53`) | — |
| liên kết chia sẻ `/share/{token}` | I | Token chia sẻ (quyền đọc mặt bằng + địa chỉ) lộ qua log nginx | nginx `access_log off` + `error_log … crit` cho `/api/share/` và `/share/`, map `$appback_access_log` bỏ `/share/` ở lớp tiền-location (K-41); uvicorn `--no-access-log`; `Referrer-Policy: same-origin` | log nginx không còn token chia sẻ (test `…__k41`); hạn dùng mặc định thuộc L6-N05 · K-41 |

### /api/telemetry công khai

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| /api/telemetry công khai | S | Giả nguồn | không xác thực có chủ ý; kiểm `Origin` lạ (`reject_foreign_origin`, `apps/api/telemetry/router.py:55`) | không phân biệt được người gửi · B7-01 (chủ ý) |
| /api/telemetry công khai | T | Đầu độc metric | tên sự kiện/lý do trong tập đóng, trường chuỗi theo mẫu, số có trần (C-47, C-45) | — |
| /api/telemetry công khai | R | Không áp dụng — không ghi nhận người gửi (chủ ý: không IP, không UA, C-10) | — | — |
| /api/telemetry công khai | I | Lộ IP/PII qua log | không log IP/thân/UA (C-10) | — |
| /api/telemetry công khai | D | Làm ngập thân, tốc độ hay log | 64 KiB + 1 → 413 (C-38), 429 theo IP (C-34), xô log (C-43) | tồn dư: hạn mức mở khi cache chết (C-34) · B7-01 |
| /api/telemetry công khai | E | Không áp dụng — route không có quyền; không ghi DB, không gửi task (`ingest.py:1-12`) | — | — |

### sao lưu và khôi phục

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| sao lưu và khôi phục | S | Giả bản sao lưu khi khôi phục | SHA-256 theo manifest kiểm trước khi dừng dịch vụ (`deploy/backup/restore.sh`, B0-10) | — |
| sao lưu và khôi phục | T | Sửa bản sao lưu | manifest SHA-256; `age` xác thực tính toàn vẹn khi giải (C-16) | — |
| sao lưu và khôi phục | R | Không áp dụng — không có hành vi người dùng; nhật ký chạy ở systemd/journald | — | — |
| sao lưu và khôi phục | I | Lộ bản sao lưu (hash mật khẩu, dữ liệu dự án) | `age` khi có recipient (C-16); quyền tệp (C-17) | quyền 0644 mặc định → SEC-040; production không mã hoá (C-18) → SEC-044, đã vá FIX-339 (staging: Nợ hiến chương) · B0-10 |
| sao lưu và khôi phục | D | Bản sao lưu dở làm đầy đĩa | xoá thư mục dở khi lỗi, xoay vòng 7 bản + tuần (`backup.sh:46-80,111`, B0-10) | — |
| sao lưu và khôi phục | E | Khôi phục chạy bởi người không có quyền | chạy dưới `User=deploy` (`appback-backup.service`); khoá `age` cất ngoài máy (B0-10 [6]) | — |

### trình duyệt ↔ nginx

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| trình duyệt ↔ nginx | S | Giả mạo origin để ghi bằng cookie | `Origin` bắt buộc cho ghi cookie, lạ → 403 (A-10); luồng chặn `Origin` lạ (D-11) | cờ cookie đo tầng app ở A-09; qua nginx chưa đo (thiếu ảnh) · B1-01 |
| trình duyệt ↔ nginx | T | Hạ cấp http, chèn nội dung | 301 sang https, HSTS (D-07) | HSTS + 301 đo sống đạt (D-07, W10/L-01) · B0-08 |
| trình duyệt ↔ nginx | R | Không truy vết yêu cầu | `X-Request-Id` ở mọi phản hồi kể cả lỗi nginx (D-01, D-05) | — |
| trình duyệt ↔ nginx | I | Lộ nội dung qua sniff, framing, referrer, cache lỗi | App đủ bốn header (D-01…D-03); CSP (D-06); lỗi do nginx sinh thiếu header (D-05) | thân lỗi nginx thiếu header · B0-08 (SEC-060) |
| trình duyệt ↔ nginx | D | Thân lớn, dồn yêu cầu | `client_max_body_size 8m` (`app_locations.conf:3`), 413 của app (D-02), 429 (D-03) | trần 8 MiB/512 MiB N26 đo sống đạt (C-39, W10/L-01) · B0-08 |
| trình duyệt ↔ nginx | E | Gọi chéo origin để chiếm quyền | không CORS (D-09) | — |

### nginx ↔ api

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| nginx ↔ api | S | Giả `X-Request-Id`, `X-Forwarded-For` | nginx đặt lại `X-Forwarded-For`, chuẩn hoá `X-Request-Id` (`proxy_common.conf:9-10`, `templates/prod/app.conf.template:2-5`) | — |
| nginx ↔ api | T | Sửa thân giữa đường | cùng mạng bridge nội bộ, không TLS nội bộ (theo hiến chương) | không mã hoá nội bộ · B0-08 |
| nginx ↔ api | R | Mất vết khi api chết | log nginx giữ `request_id`; token tệp không vào log (`app_locations.conf:39-45`) | — |
| nginx ↔ api | I | Lộ `/metrics`, cổng exporter | không location `/metrics`, 9464 không công bố (D-13) | đo sống chưa làm · B0-08 |
| nginx ↔ api | D | api chết → treo client | `proxy_connect_timeout 1s`, thử lại 2 lần, 503 JSON (`proxy_common.conf:26-29`) | 503 thiếu header nền (D-05, SEC-060) · B0-08 |
| nginx ↔ api | E | Gọi route nội bộ qua nginx | chỉ `/api/`, `/`, `/assets/`, `/draco/` được mở; quét route (D-26, D-27) | — |

### CI → GHCR → VPS

| ranh giới | chữ | mối đe doạ | biện pháp (dẫn #) | rủi ro tồn dư · chủ |
|---|---|---|---|---|
| CI → GHCR → VPS | S | Giả workflow/action | action ghim SHA (D-14), ảnh ghim digest (D-16) | — |
| CI → GHCR → VPS | T | Ảnh bị thay, thư viện độc | trivy chỉ CRITICAL + ignore-unfixed (D-17); `opencv` headless (D-18); trọng số ghim SHA-256 (D-19) | trivy mù CVE nginx, ngưỡng chỉ CRITICAL · B0-09 |
| CI → GHCR → VPS | R | Không rõ ai triển khai | production qua Environment có duyệt (`deploy.yml:145,180`) | — |
| CI → GHCR → VPS | I | Lộ secret CI | `persist-credentials: false`, quyền tối thiểu (D-23); webhook cấp repo (D-22) | `ALERT_WEBHOOK_URL` cấp repository · B0-10 (SEC-061) |
| CI → GHCR → VPS | D | Workflow bị lạm dụng chiếm runner | không `pull_request_target` (D-23) | — |
| CI → GHCR → VPS | E | Ảnh chạy root; mã nạp thêm từ mạng | USER 10001 (D-15); ultralytics offline (D-20); HF offline chưa cưỡng chế bằng biến (D-21) | thiếu `HF_HUB_OFFLINE` · B6-04a (SEC-062) |

## Rủi ro tồn dư có chủ

Mỗi dòng STRIDE có cột tồn dư kèm prompt chủ. Ba lỗ có chủ ý của v1 (không mở `SEC-*`):

- admin dựng dataset từ tầng của mọi dự án, xuyên ranh giới thành viên · B6-02;
- giấy phép CC BY-NC (CubiCasa), NVIDIA phi thương mại (mit-b0/b1), AGPL (YOLO) chảy vào bản được kích hoạt (D-24, D-25; D5 của kế hoạch; giấy phép CubiCasa nay in ở báo cáo nhập — FIX-343, SEC-063, chưa chặn kích hoạt) · B5-01, B6-01, B6-02b;
- bản sao trang trong `ml/datasets/*` còn sống sau khi dự án bị xoá · B6-02, B2-01.
