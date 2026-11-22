#!/usr/bin/env bash
# Thư viện dùng chung cho deploy.sh, rollback.sh, backup.sh, restore.sh (R-07) — CHỈ
# được `source`, không tự chạy. Mặc định biến môi trường, nạp biến cho compose
# (appback.env + release.env — K-35, L5-N07), chọn bó phát hành releases/<tag>, dựng
# hạ tầng (L5-N01), đổi api và các dịch vụ ứng dụng, gọi smoke, đọc/ghi trạng thái
# phát hành, và hai hàm run()/plan() cho --dry-run (in lệnh thay vì chạy — B0-10 §1,§2).
#
# Cấu trúc máy đích (README §1): /opt/appback/releases/<tag>/deploy/{compose,minio,
# scripts,backup} (bó bất biến do bundle.sh dựng), symlink current → releases/<tag>
# đang chạy, release.env (IMAGE_TAG=<tag> đang chạy — nguồn sự thật duy nhất của tag).
set -euo pipefail

: "${APPBACK_DIR:=/opt/appback}"
: "${APPBACK_ENV_FILE:=/etc/appback/appback.env}"
APPBACK_RELEASE_ENV="$APPBACK_DIR/release.env"
# Giá trị người gọi tự đặt (drill.sh, kiểm tay trên máy dev) được tôn trọng nguyên vẹn; chỉ
# khi trống lib mới tự chọn compose của bó phát hành và tự nạp biến (K-35 D2). Giữ lại để
# deploy.sh trả đúng môi trường gốc cho rollback.sh của bản trước (không rò compose bản mới).
LIB_CALLER_COMPOSE_FILE="${COMPOSE_FILE:-}"
LIB_CALLER_ENV_FILES="${COMPOSE_ENV_FILES:-}"
LIB_MANAGES_ENV=0
[[ -z "$LIB_CALLER_ENV_FILES" ]] && LIB_MANAGES_ENV=1

# env_file_value <KHOÁ>: giá trị cuối cùng của KHOÁ trong $APPBACK_ENV_FILE (bỏ một cặp nháy
# bao ngoài); rỗng nếu tệp/khoá không có. Đọc từng khoá chứ không `source` tệp: appback.env là
# tệp env của compose (giá trị không trích dẫn, có thể chứa `$`, dấu cách) — bash không đọc đúng.
env_file_value() {
  local v
  [[ -r "$APPBACK_ENV_FILE" ]] || return 0
  v="$(sed -n "s/^$1=//p" "$APPBACK_ENV_FILE" | tail -n 1)"
  v="${v%$'\r'}"
  if [[ "$v" =~ ^\"(.*)\"$ || "$v" =~ ^\'(.*)\'$ ]]; then
    v="${BASH_REMATCH[1]}"
  fi
  printf '%s' "$v"
}

# Script được gọi qua `ssh … deploy.sh` (deploy.yml) không có EnvironmentFile của systemd:
# biến script tự đọc (URL smoke, hạn chờ, registry, profile compose) phải lấy từ appback.env
# trước khi mặc định bên dưới chiếm chỗ — không thì shell xuất mặc định và ĐÈ giá trị trong
# tệp (compose ưu tiên biến shell hơn --env-file). Biến đã có trong môi trường giữ nguyên.
if [[ "$LIB_MANAGES_ENV" == "1" ]]; then
  for _key in IMAGE_REGISTRY APPBACK_BASE_URL APPBACK_HEALTH_TIMEOUT_S APPBACK_API_SWAP_SETTLE_S COMPOSE_PROFILES; do
    if [[ -z "${!_key+x}" ]] && grep -q "^$_key=" "$APPBACK_ENV_FILE" 2>/dev/null; then
      printf -v "$_key" '%s' "$(env_file_value "$_key")"
      export "${_key?}"
    fi
  done
  unset _key
fi
# Không dùng ":=" — drill.sh cố ý đặt IMAGE_REGISTRY="" (ảnh cục bộ, không registry);
# ":=" coi rỗng như chưa đặt và sẽ ghi đè lại registry mặc định.
: "${IMAGE_REGISTRY=ghcr.io/mungvu999-ctrl}"
: "${APPBACK_BASE_URL:=http://127.0.0.1}"
: "${APPBACK_HEALTH_TIMEOUT_S:=180}"
: "${APPBACK_HEALTH_POLL_S:=2}"
# Chờ nginx tự dịch lại "api" trước khi xoá container cũ. Khai ĐÚNG MỘT LẦN ở đây
# (NO-120: trước đây hằng 11 có ba bản — dòng kế hoạch dry-run, lệnh sleep thật và
# README — nên hạ mặc định của sleep mà test vẫn xanh).
#
# RÀNG BUỘC (R-05): 11 = TTL cache DNS của nginx (10s, `resolver 127.0.0.11
# valid=10s` ở deploy/nginx/templates/{dev,prod}/app.conf.template) + 1s biên an
# toàn; test_deploy_default_swap_settle_s_covers_nginx_resolver_ttl chốt quan hệ đó.
# Dùng `:=`: "0" (tắt hẳn lúc kiểm, bài học NO-114) vẫn được giữ vì không rỗng, còn
# `APPBACK_API_SWAP_SETTLE_S=` rỗng trong appback.env về mặc định thay vì làm
# `sleep ""` hỏng giữa swap_api (NO-200).
: "${APPBACK_API_SWAP_SETTLE_S:=11}"
export APPBACK_DIR IMAGE_REGISTRY APPBACK_BASE_URL APPBACK_HEALTH_TIMEOUT_S APPBACK_API_SWAP_SETTLE_S
export COMPOSE_FILE="${COMPOSE_FILE:-$APPBACK_DIR/current/deploy/compose/prod.yml}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-appback}"
# Nạp CÙNG appback.env cho nội suy `${VAR}` của compose (prod.yml:5-12 "lệnh chuẩn") và
# release.env cho IMAGE_TAG — thiếu thì `${VAR:?}` của base.yml/prod.yml làm mọi lệnh compose
# hỏng (K-35 D2, L5-N07). Chỉ ghép tệp đang có: compose từ chối tệp env không tồn tại;
# assert_env_ready/assert_release_env báo lỗi rõ trước khi tới compose.
if [[ "$LIB_MANAGES_ENV" == "1" ]]; then
  COMPOSE_ENV_FILES=""
  for _f in "$APPBACK_ENV_FILE" "$APPBACK_RELEASE_ENV"; do
    [[ -f "$_f" ]] && COMPOSE_ENV_FILES="${COMPOSE_ENV_FILES:+$COMPOSE_ENV_FILES,}$_f"
  done
  unset _f
fi
export COMPOSE_ENV_FILES

# Hạ tầng (bootstrap_infra dựng), việc một lần và api (swap_api đổi riêng) — mọi dịch vụ còn
# lại mà `docker compose config --services` liệt kê theo COMPOSE_PROFILES là dịch vụ ứng dụng
# đổi theo tag (worker, beat, web, ml, ml-train, ml-gpu, bake…). Suy từ compose thay vì viết
# cứng danh sách: thêm dịch vụ vào prod.yml thì deploy/rollback tự đổi theo (K-35 D3).
APPBACK_NON_APP_SERVICES="postgres redis-broker redis-cache minio minio-init mailpit migrate api"

LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APPBACK_TAG_RE='^(v[0-9]+\.[0-9]+\.[0-9]+|sha-[0-9a-f]{12}|[a-z0-9][a-z0-9._-]{0,63})$'

DRY_RUN="${DRY_RUN:-0}"
STEP_NO=0

# Thoát chuỗi "$1" thành thân một chuỗi JSON (không có dấu nháy bao): `\`, `"` và ký tự điều
# khiển thường gặp. Thay `python3 json.dumps` — máy Windows chỉ có shim rỗng của Microsoft
# Store (NO-189); dùng chung cho manifest.json (backup.sh) và thân cảnh báo (healthcheck.sh).
# Mẫu/thay thế đặt trong biến có nháy vì dạng `${s//\\/…}` đếm sai số dấu `\` giữa các bản bash.
json_escape() {
  local s="$1" bs=$'\\' q='"'
  s=${s//"$bs"/"$bs$bs"}
  s=${s//"$q"/"$bs$q"}
  s=${s//$'\n'/"${bs}n"}
  s=${s//$'\r'/"${bs}r"}
  s=${s//$'\t'/"${bs}t"}
  printf '%s' "$s"
}

# Ngược của json_escape cho một khoá chuỗi đọc từ manifest.json: `\"` → `"`, còn `\\`, `\n`,
# `\uXXXX`… do `printf %b` giải (đọc được cả manifest cũ do python json.dumps ghi, vốn thoát
# ký tự ngoài ASCII thành \uXXXX).
json_unescape() {
  local bs=$'\\' q='"'
  local s=${1//"$bs$q"/"$q"}
  printf '%b' "$s"
}

# Đúng mẫu tag hợp đồng §2 (release, sha-<12>, hoặc nhánh tự do cho diễn tập).
validate_tag() {
  [[ "$1" =~ $APPBACK_TAG_RE ]]
}

# Chạy lệnh thật; ở chế độ dry-run chỉ in bước đánh số kèm lệnh, không chạy.
run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    STEP_NO=$((STEP_NO + 1))
    printf '%d. %s\n' "$STEP_NO" "$*"
    return 0
  fi
  "$@"
}

# In một dòng kế hoạch không gắn với lệnh docker cụ thể (vd "chờ healthy").
plan() {
  if [[ "$DRY_RUN" == "1" ]]; then
    STEP_NO=$((STEP_NO + 1))
    printf '%d. %s\n' "$STEP_NO" "$*"
  fi
}

# Chờ một container cụ thể (id "$1") thành "healthy" tới APPBACK_HEALTH_TIMEOUT_S
# (docker inspect, mỗi APPBACK_HEALTH_POLL_S giây). Dùng chung cho swap_api (chờ
# container mới của một service đang chạy 2 bản) và wait_healthy (chờ container
# duy nhất của một service, sau restore.sh) — R-07, trước đây restore.sh tự chép
# vòng lặp này.
wait_container_healthy() {
  local cid="$1" elapsed=0 status
  while :; do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || true)"
    [[ "$status" == "healthy" ]] && return 0
    if (( elapsed >= APPBACK_HEALTH_TIMEOUT_S )); then
      return 1
    fi
    sleep "$APPBACK_HEALTH_POLL_S"
    elapsed=$((elapsed + APPBACK_HEALTH_POLL_S))
  done
}

# Chờ container hiện tại (đúng một bản) của dịch vụ compose "$1" thành healthy.
wait_healthy() {
  local service="$1" cid
  cid="$(docker compose ps -q "$service")"
  if ! wait_container_healthy "$cid"; then
    echo "loi: $service khong healthy sau ${APPBACK_HEALTH_TIMEOUT_S}s" >&2
    return 1
  fi
}

# Đổi container api không gián đoạn: scale 2 → chờ mới healthy → xoá cũ → scale 1
# (thiết kế 6 bước đã chốt, hop-dong.md §2). Thoát khác 0 nếu không healthy tới
# timeout; khi đó KHÔNG dừng container cũ, container mới bị xoá.
swap_api() {
  local tag="$1"
  export IMAGE_TAG="$tag"
  if [[ "$DRY_RUN" == "1" ]]; then
    run docker compose up -d --no-deps --no-recreate --scale api=2 api
    plan "chờ container api mới healthy (timeout ${APPBACK_HEALTH_TIMEOUT_S}s)"
    plan "chờ nginx tự dịch lại DNS (${APPBACK_API_SWAP_SETTLE_S}s)"
    plan "xoá container api cũ, trả về scale api=1"
    return 0
  fi
  local old_ids new_ids new_id id
  old_ids="$(docker compose ps -q api)"
  docker compose up -d --no-deps --no-recreate --scale api=2 api
  new_ids="$(docker compose ps -q api)"
  new_id=""
  for id in $new_ids; do
    case " $old_ids " in
      *" $id "*) ;;
      *) new_id="$id" ;;
    esac
  done
  if [[ -z "$new_id" ]]; then
    # `--no-recreate --scale api=2` không tạo container mới nào (vd đã có 2 bản từ trước) —
    # không có gì để chờ healthy; hỏng ngay thay vì quay đủ APPBACK_HEALTH_TIMEOUT_S rồi mới
    # báo (review round 1 P3 RES-01).
    echo "loi: swap_api không tạo được container api mới (--scale api=2 không thêm bản nào)" >&2
    return 1
  fi
  if ! wait_container_healthy "$new_id"; then
    docker rm -f "$new_id" >/dev/null 2>&1 || true
    return 1
  fi
  # Chờ nginx tự dịch lại "api" trước khi xoá container cũ — không chờ thì nginx còn giữ
  # cache DNS trỏ container cũ, dừng nó gây 502/refused thật cho request đang tới dù thiết
  # kế 6 bước (hop-dong.md §2) đã đúng thứ tự. Mặc định và ràng buộc: xem khai báo đầu file.
  sleep "$APPBACK_API_SWAP_SETTLE_S"
  for id in $old_ids; do
    docker stop -t 30 "$id"
    docker rm "$id"
  done
  docker compose up -d --no-deps --no-recreate --scale api=1 api
}

# Nạp mảng toàn cục APP_SERVICES = dịch vụ ứng dụng của compose hiện hành (xem
# APPBACK_NON_APP_SERVICES). `docker compose config --services` chỉ ĐỌC (cả ở --dry-run) và
# đã lọc theo COMPOSE_PROFILES: ml/ml-train chỉ có khi profile "ml" bật. Hỏng → thoát 1.
load_app_services() {
  local all svc
  if ! all="$(docker compose config --services)"; then
    echo "lỗi: docker compose config --services hỏng — không biết dịch vụ nào cần đổi" >&2
    return 1
  fi
  APP_SERVICES=()
  for svc in $all; do
    case " $APPBACK_NON_APP_SERVICES " in
      *" $svc "*) ;;
      *) APP_SERVICES+=("$svc") ;;
    esac
  done
}

# Đổi mọi dịch vụ ứng dụng trừ api sang tag "$1" (worker/beat: acks_late giao lại task đang
# chạy; web: giữ cổng 80/443 nên không scale 2 được — gián đoạn vài giây khi đổi, chấp nhận ở
# F1, README §10). Lần đầu `up` tạo mới web (L5-N01). `--no-deps`: hạ tầng do bootstrap_infra lo.
swap_services() {
  export IMAGE_TAG="$1"
  load_app_services || return 1
  (( ${#APP_SERVICES[@]} > 0 )) || return 0
  run docker compose up -d --no-deps "${APP_SERVICES[@]}"
}

# profile_enabled <tên>: profile compose có trong COMPOSE_PROFILES (danh sách phẩy) không.
profile_enabled() {
  [[ ",${COMPOSE_PROFILES:-}," == *",$1,"* ]]
}

# Dựng hạ tầng (postgres, hai redis; minio + minio-init khi profile "minio" bật) và chờ
# healthy — gọi MỌI lần deploy (L5-N01): máy trống thì tạo, đang chạy đúng cấu hình thì compose
# không tạo lại; prod.yml đổi cấu hình hạ tầng thì container đó được tạo lại (gián đoạn ngắn,
# hiện ở kế hoạch --dry-run). `--wait-timeout` chặn treo vô hạn nếu một healthcheck không bao
# giờ đạt. Hỏng → thoát 1, chưa đổi container ứng dụng nào.
bootstrap_infra() {
  local wait_opts=(--wait --wait-timeout "$APPBACK_HEALTH_TIMEOUT_S")
  run docker compose up -d "${wait_opts[@]}" postgres redis-broker redis-cache || return 1
  if profile_enabled minio; then
    run docker compose up -d "${wait_opts[@]}" minio || return 1
    run docker compose run --rm minio-init || return 1
  fi
}

# Chọn bó phát hành của tag "$1": COMPOSE_FILE = releases/<tag>/deploy/compose/prod.yml để
# deploy/rollback chạy ĐÚNG compose của bản đó (lùi cả cấu hình, không chỉ ảnh — K-35). Người
# gọi đã tự đặt COMPOSE_FILE (kiểm tay) thì giữ nguyên. Thiếu bó → thoát 2 (đối số/môi trường sai).
select_release() {
  local compose="$APPBACK_DIR/releases/$1/deploy/compose/prod.yml"
  [[ -n "$LIB_CALLER_COMPOSE_FILE" ]] && return 0
  if [[ ! -f "$compose" ]]; then
    echo "lỗi: chưa có bó phát hành $APPBACK_DIR/releases/$1 (giải nén deploy-bundle-$1.tar.gz vào đó trước — README §10)" >&2
    return 2
  fi
  export COMPOSE_FILE="$compose"
}

# Kiểm appback.env trước khi gọi compose (chỉ khi lib tự nạp biến): thiếu tệp → mọi `${VAR:?}`
# hỏng giữa chừng; có dòng IMAGE_TAG= → tag tĩnh lệch release.env (L5-N07: tag chỉ nằm ở
# release.env do deploy.sh ghi). Thoát 2 kèm thông báo, chưa chạm docker.
assert_env_ready() {
  [[ "$LIB_MANAGES_ENV" == "1" ]] || return 0
  if [[ ! -f "$APPBACK_ENV_FILE" ]]; then
    echo "lỗi: thiếu $APPBACK_ENV_FILE (README §1) — compose không nội suy được biến" >&2
    return 2
  fi
  if grep -Eq '^[[:space:]]*(export[[:space:]]+)?IMAGE_TAG[[:space:]]*=' "$APPBACK_ENV_FILE"; then
    echo "lỗi: $APPBACK_ENV_FILE có IMAGE_TAG= — xoá dòng đó; tag do deploy.sh ghi vào $APPBACK_RELEASE_ENV" >&2
    return 2
  fi
}

# Script chạy trên bản đang chạy (backup.sh, restore.sh) cần release.env để compose có
# IMAGE_TAG (prod.yml `${IMAGE_TAG:?}`) — chưa deploy lần nào thì không có gì để sao lưu/khôi phục.
assert_release_env() {
  [[ "$LIB_MANAGES_ENV" == "1" ]] || return 0
  if [[ ! -f "$APPBACK_RELEASE_ENV" ]]; then
    echo "lỗi: chưa có bản phát hành nào ($APPBACK_RELEASE_ENV không tồn tại) — chạy deploy.sh trước" >&2
    return 2
  fi
}

# Ghi trạng thái "tag $1 đang chạy" sau smoke đạt: release.env (IMAGE_TAG cho mọi lệnh compose
# về sau), symlink current → releases/<tag> (systemd chạy script của bản đang chạy), current_tag.
# Ghi tệp tạm rồi `mv -T` (rename nguyên tử): lệnh compose chạy song song (timer sao lưu) không
# bao giờ thấy release.env dở hay symlink vắng. Bỏ qua ở --dry-run.
record_release() {
  local tag="$1"
  [[ "$DRY_RUN" == "1" ]] && return 0
  printf 'IMAGE_TAG=%s\n' "$tag" > "$APPBACK_RELEASE_ENV.tmp"
  mv -f "$APPBACK_RELEASE_ENV.tmp" "$APPBACK_RELEASE_ENV"
  ln -sfn "releases/$tag" "$APPBACK_DIR/current.tmp"
  mv -fT "$APPBACK_DIR/current.tmp" "$APPBACK_DIR/current"
  write_state current_tag "$tag"
}

# Gọi smoke.sh cạnh script này với $APPBACK_BASE_URL; chỉ in kế hoạch ở dry-run.
run_smoke() {
  if [[ "$DRY_RUN" == "1" ]]; then
    plan "smoke: smoke.sh $APPBACK_BASE_URL"
    return 0
  fi
  bash "$LIB_DIR/smoke.sh" "$APPBACK_BASE_URL"
}

# Đọc $APPBACK_DIR/<tên tệp trạng thái>; rỗng nếu chưa có. Luôn thoát 0 (an
# toàn dưới set -e khi gọi trong $(...)).
read_state() {
  local f="$APPBACK_DIR/$1"
  [[ -f "$f" ]] && cat "$f"
  return 0
}

# Ghi giá trị vào $APPBACK_DIR/<tên tệp trạng thái>.
write_state() {
  printf '%s' "$2" > "$APPBACK_DIR/$1"
}
