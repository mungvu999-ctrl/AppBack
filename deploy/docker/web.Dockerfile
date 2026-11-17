# syntax=docker/dockerfile:1
# Ảnh web: nginx không root đón front cho SPA AppFront + /api cùng origin
# (BE-00 §8, W19). Build context = gốc repo; build-context phụ "appfront" do
# deploy/docker/web-context.sh xuất.
FROM node:26-bookworm-slim@sha256:582460f614631b59b824ac6020533b9bf339c7fdf3a6d7db31abb6b4065f0212 AS build
WORKDIR /src
COPY --from=appfront . .
# Ảnh node không còn kèm corepack (đo: node:26-bookworm-slim = v26.9.0, npm
# 11.19.1, /usr/local/bin chỉ có node/npm/npx) — `corepack enable` thoát 127
# nên cả ảnh web không build được (NO-174, FIX-100). npm có sẵn trong mọi ảnh
# node; ghim đủ ba số để hai lần build dùng cùng một bản pnpm.
RUN npm install -g pnpm@9.4.0 \
    && pnpm install --frozen-lockfile \
    && pnpm build \
    && test -f dist/draco/draco_decoder.wasm \
    && test -f dist/assets/pascal/pascal-mount.js

# Dòng stable. Sàn theo advisory nginx.org (2026-09-24): 1.30.5 là bản stable
# đầu tiên "not vulnerable" ở MỌI dòng advisory, kể cả CVE-2026-90439
# (ngx_http_v3_module, vá 1.30.5+/1.31.6+) mà mainline 1.31.5 còn dính.
# Nâng: chỉ lên tag stable 1.30.x mới hơn; sang mainline phải ≥ 1.31.6.
FROM nginxinc/nginx-unprivileged:1.31.7-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e

# trivy CRITICAL (CVE-2026-111497, 02/11/2026): ảnh nền, kể cả 1.30.6-alpine, còn brotli-libs 1.2.0-r1.
# Sàn `>=` chứ không ghim `=`: Alpine gỡ bản cũ khỏi kho. Bỏ khối này khi ảnh nền tự có ≥ 1.2.1-r0.
USER root
RUN apk add --no-cache --upgrade 'brotli-libs>=1.2.1-r0'
USER 101

# Ảnh nền giữ server mặc định (server_name localhost, listen 8080) tại
# /etc/nginx/conf.d/default.conf: mọi yêu cầu Host: localhost khớp nó TRƯỚC
# app.conf, nuốt /api (404 HTML) và mất CSP của SPA (review 2026-09-22 #1,
# probe Q1 — localhost là URL PUBLIC_BASE_URL/e2e dùng). Lệnh chạy bằng uid 101
# của ảnh nền (USER 10001 của prompt này chưa có hiệu lực ở đây) — uid đó là
# chủ /etc/nginx/conf.d nên xoá được default.conf mà không cần root.
RUN rm /etc/nginx/conf.d/default.conf

ARG APPFRONT_SHA
RUN [ -n "$APPFRONT_SHA" ] || { echo "APPFRONT_SHA is required" >&2; exit 1; }
LABEL org.opencontainers.image.revision.appfront=${APPFRONT_SHA}

# Compose prod đổi sang templates/prod (chứng chỉ TLS, vhost MinIO).
ENV NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/appback/templates/dev

COPY --from=build /src/dist /usr/share/nginx/html
COPY deploy/nginx/ /etc/nginx/appback/
COPY --chmod=0755 deploy/nginx/docker-entrypoint.d/15-s3-public-host.envsh /docker-entrypoint.d/15-s3-public-host.envsh

EXPOSE 8080 8443
# Location không phụ thuộc api: index.html tĩnh, phục vụ được cả dev và prod
# (prod 8080 trả 301 — không phải lỗi, curl -f không coi 3xx là hỏng).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --start-interval=2s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/index.html -o /dev/null || exit 1

USER 10001
