# WEB-NGINX-CVE — job `build` đỏ ở `trivy image web` (22/11/2026)

- Nguyên nhân: nginx 1.30.5-r1 trong `nginx-unprivileged:1.30.5-alpine` dính 5 CVE CRITICAL
  (CVE-2026-97205 vá 1.30.6; CVE-2026-113425, -121810, -123007, -123633 vá 1.30.7/1.31.9).
- `deploy/docker/web.Dockerfile`: nền `1.30.6-alpine@sha256:f5116ed4…` (digest tra `imagetools inspect`,
  trùng `1.30-alpine`/`stable-alpine`). Chưa có ảnh 1.30.7 trên Docker Hub; 1.31-alpine mới là 1.31.7.
- `tools/ci/job.sh`: `nginx_floor_for_line` 1.30 → 1.30.6, 1.31 → 1.31.7 (advisory nginx.org tra 22/11).
- Miễn tạm bốn CVE còn lại: `tools/ci/web.trivyignore` (`exp:2026-12-06`, 14 ngày), mount vào `docker run` của
  `job_build_trivy` chỉ cho ảnh `web` bằng `--ignorefile`. Giữ `--severity CRITICAL --ignore-unfixed --exit-code 1`.
- Test: `tools/ci/tests/test_workflows.py` (sàn mới, tệp miễn đúng bốn mã và hạn ≤ 14 ngày, mount chỉ ở `web`).

## Nợ

- NO mở: nâng `web.Dockerfile` lên nginx-unprivileged 1.30.7 ngay khi ảnh có, rồi xoá `tools/ci/web.trivyignore`,
  phần mount/`--ignorefile` trong `job_build_trivy`, test miễn tương ứng, và nâng sàn `nginx_floor_for_line` lên 1.30.7.
  Hạn cứng 2026-12-06: quá hạn thì trivy tính lại và job `build` đỏ.
- Rủi ro tạm: ảnh `web` mang bốn CVE CRITICAL của nginx 1.30.6 trong tối đa 14 ngày. Stack chưa có prod nên
  chưa có bề mặt bị khai thác; không được triển khai ảnh này ra ngoài trước khi nâng 1.30.7.
- Không đổi API, DB, hợp đồng FE–BE.
