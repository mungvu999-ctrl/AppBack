#!/usr/bin/env bash
# Chuỗi thật F-14 (M4): dựng stack compose `f14`, nạp dữ liệu, chạy e2e fullstack của AppFront. Chạy trong Git Bash.
# Pha: build | up | seed | run | down   (mặc định: build up seed run)
#   down chạy `docker compose down -v` — XOÁ volume của project f14 (DB, MinIO, Redis) — nên đòi cờ --yes:
#   bash tools/f14/chuoi.sh down --yes
# Biến môi trường (script không ghim giá trị cá nhân nào):
#   BE        worktree AppBack để dựng ảnh và chạy compose (mặc định: repo chứa script này)
#   FE        worktree AppFront chạy `pnpm e2e:fullstack` (mặc định: <BE>/../AppFront)
#   WEB_SHA   SHA AppFront để dựng ảnh web (mặc định efbda6bd — thường cần đặt lại cho SHA hiện hành)
#   F14_DIR   thư mục trạng thái cục bộ NGOÀI git: .secrets.env, drawing.png, meta.json, second.onnx, web-ctx-*
#             (mặc định: <BE>/backend/dieu-phoi/chay/F-14 — `backend/` bị .gitignore)
#   F14_LIMITS  tệp override hạn mức (mặc định: qa-limits.override.example.yml cạnh script)
#   F14_AI_STYLE=1  thêm ai-style.override.example.yml; cần $F14_DIR/.ai-style.env (khoá AI, KHÔNG commit)
#   F14_DC    thay hẳn lệnh compose (dùng để thử không phá huỷ, ví dụ F14_DC=echo)
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BE=${BE:-$(cd "$HERE/../.." && pwd)}
FE=${FE:-$BE/../AppFront}
D=${F14_DIR:-$BE/backend/dieu-phoi/chay/F-14}
LIMITS=${F14_LIMITS:-$HERE/qa-limits.override.example.yml}
export POSTGRES_HOST_PORT=15432 WEB_HTTP_PORT=18081 PUBLIC_BASE_URL=http://localhost:18080 IMAGE_TAG=f14 ML_BACKEND=fake
export F14_DIR=$D   # ai-style.override.example.yml đọc biến này
if [ -n "${F14_DC:-}" ]; then
  DC=$F14_DC
else
  DC="docker compose -p f14 -f deploy/compose/ci.yml -f $LIMITS"
  [ "${F14_AI_STYLE:-}" = 1 ] && DC="$DC -f $HERE/ai-style.override.example.yml"
  DC="$DC --env-file deploy/compose/env.example --profile ml"
fi
BASE=http://localhost:18081   # container web (cổng /api); trình duyệt dùng 18080 = Vite dev (`pnpm dev:be`)
ORIGIN=$PUBLIC_BASE_URL
SECRETS=$D/.secrets.env   # không in, không commit (ngoài repo)
DOWN_REFUSAL="down chạy 'down -v' và XOÁ volume f14 (DB, MinIO, Redis); thêm --yes để xác nhận"
step() { echo "== $1 $(date -u +%H:%M:%SZ)"; }
die() { echo "HỎNG: $1"; echo "ma thoat: 1"; exit 1; }

load_secrets() {
  mkdir -p "$D"
  [ -f "$SECRETS" ] || { printf 'E2E_ADMIN_EMAIL=e2e-admin@example.test\nE2E_ADMIN_PASSWORD=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')" > "$SECRETS"; }
  set -a; . "$SECRETS"; set +a
}

phase_build() {
  cd "$BE" || die "không vào $BE"
  step "build api";    docker build -t appback-api:f14 -f deploy/docker/api.Dockerfile . || die "build api"
  step "build worker"; docker build -t appback-worker:f14 -f deploy/docker/worker.Dockerfile . || die "build worker"
  CTX=$D/web-ctx-${WEB_SHA:-efbda6bd}; [ -d "$CTX" ] || APPFRONT_REPO=${APPFRONT_REPO:-$FE} bash deploy/docker/web-context.sh "${WEB_SHA:-efbda6bd}" "$CTX"
  step "build web";    docker build -t appback-web:f14 -f deploy/docker/web.Dockerfile --build-context appfront="$CTX" --build-arg APPFRONT_SHA="${WEB_SHA:-efbda6bd}" . || die "build web"
}
phase_up() {
  load_secrets
  cd "$BE" || die "không vào $BE"; step "up"; $DC up -d --wait || { $DC ps; die "compose up"; }
  $DC ps --format '{{.Service}} {{.Status}}'
  step "ML_BACKEND trong ml"; $DC exec -T ml printenv ML_BACKEND
  step "cờ"; curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/health
  step "CSP"; curl -sI $BASE/ | grep -i content-security-policy | tr ';' '\n' | grep -E "worker-src|script-src"
  step "pascal-mount cache"; curl -sI $BASE/assets/pascal/pascal-mount.js | grep -i -E "^HTTP|cache-control"
}
token() {
  local jar=$D/.jar; rm -f "$jar"
  curl -s -c "$jar" -o /dev/null -w '%{http_code}' -H "Origin: $ORIGIN" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$E2E_ADMIN_EMAIL\",\"password\":\"$E2E_ADMIN_PASSWORD\",\"rememberMe\":false}" $BASE/api/auth/login >&2; echo >&2
  # cookie Secure trên http: curl vẫn gửi cho localhost
  curl -s -b "$jar" -H "Origin: $ORIGIN" -X POST $BASE/api/auth/refresh | PYTHONUTF8=1 python -c "import json,sys; d=json.load(sys.stdin); print(d.get('accessToken') or d.get('access_token') or '')"
}
phase_seed() {
  load_secrets
  cd "$BE" || die "không vào $BE"
  step "create-admin"; printf '%s' "$E2E_ADMIN_PASSWORD" | $DC exec -T api python -m apps.api.auth.cli create-admin --email "$E2E_ADMIN_EMAIL" --name E2E --password-stdin || echo "(create-admin lỗi — có thể admin đã có)"
  step "PNG render_plan(7)"
  $DC exec -T ml python -c "import sys; from packages.ml_contracts.synthetic import render_plan; sys.stdout.buffer.write(render_plan(7).image_png)" > $D/drawing.png || die "xuất PNG"
  python -c "import sys; b=open(sys.argv[1],'rb').read(8); assert b==b'\x89PNG\r\n\x1a\n', b; print('PNG ok')" $D/drawing.png || die "PNG hỏng"
  step "N26 bản thứ hai"
  TOK=$(token); [ -n "$TOK" ] || die "không lấy được access token"
  W=$D/second.onnx
  $DC exec -T ml python -c "
import sys
try:
    import onnx, onnx.helper as h
    g=h.make_graph([h.make_node('Identity',['x'],['y'])],'g',[h.make_tensor_value_info('x',1,[1])],[h.make_tensor_value_info('y',1,[1])])
    sys.stdout.buffer.write(h.make_model(g).SerializeToString())
except ImportError:
    sys.stdout.buffer.write(bytes([8,7])+b'\x00'*64)
" > $W
  SUM=$(sha256sum $W | cut -d' ' -f1)
  printf '{"checksumSha256":"%s","family":"openingAndFurnitureDetection","label":"e2e-%s","weightsFormat":"onnx"}' "$SUM" "$(date -u +%m%d%H%M)" > $D/meta.json
  curl -s -w '\nHTTP %{http_code}\n' -H "Origin: $ORIGIN" -H "Authorization: Bearer $TOK" \
    -F "metadata=@$D/meta.json;type=application/json" -F "weights=@$W;type=application/octet-stream" \
    $BASE/api/admin/ml/model-versions | tail -c 600
  step "chờ eval completed (≤ 5')"
  for i in $(seq 1 30); do
    S=$(curl -s -H "Authorization: Bearer $TOK" "$BASE/api/admin/ml/model-versions?family=openingAndFurnitureDetection" | PYTHONUTF8=1 python -c "
import json,sys; d=json.load(sys.stdin); print(' '.join(f\"{v.get('label')}:{v.get('evaluationStatus')}\" for v in d.get('items',[])))")
    echo "$S"; case "$S" in *pending*|*running*|"") sleep 10;; *) break;; esac
  done
}
phase_run() {
  load_secrets
  cd "$FE" || die "không vào $FE"; step "pnpm e2e:fullstack"
  E2E_FULLSTACK_BASE_URL=$BASE E2E_DRAWING_PNG=$D/drawing.png pnpm e2e:fullstack; echo "ma thoat: $?"
}
phase_down() {
  [ "$YES" = 1 ] || die "$DOWN_REFUSAL"
  cd "$BE" || die "không vào $BE"; $DC down -v
}

YES=0; PHASES=()
for a in "$@"; do
  case "$a" in
    --yes) YES=1 ;;
    build|up|seed|run|down) PHASES+=("$a") ;;
    *) die "pha lạ: $a (build | up | seed | run | down [--yes])" ;;
  esac
done
[ ${#PHASES[@]} -gt 0 ] || PHASES=(build up seed run)
# Từ chối `down` thiếu --yes TRƯỚC khi chạy bất kỳ pha nào (kẻo pha trước chạy rồi mới dừng giữa chừng).
for p in "${PHASES[@]}"; do
  if [ "$p" = down ] && [ "$YES" != 1 ]; then die "$DOWN_REFUSAL"; fi
done
for p in "${PHASES[@]}"; do phase_"$p"; done
