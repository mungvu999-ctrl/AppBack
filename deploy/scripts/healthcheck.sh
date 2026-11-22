#!/usr/bin/env bash
# Kiểm sức khoẻ định kỳ (systemd timer 1 phút — appback-health.{service,timer}). Mỗi lượt:
#   - /api/ready lỗi 2 lần liên tiếp;
#   - đĩa > 85%;
#   - (K-39) mọi dịch vụ compose của bản đang chạy (trừ việc một lần migrate/minio-init): container
#     vắng, không `running` (chết, `paused` = treo giả lập) hoặc `unhealthy` (ml có healthcheck ping);
#   - (K-39) hàng Celery kẹt: còn việc, không giảm qua APPBACK_QUEUE_STUCK_RUNS lượt liên tiếp VÀ dịch vụ
#     nghe hàng đó không chạy việc nào (`celery inspect active` — chỉ gọi khi đã đủ số lượt, ~1 nhân/lần).
# Cảnh báo qua alert_once/alert_resolve (alert.sh): báo khi chuyển sang hỏng, nhắc lại mỗi ALERT_REPEAT_S,
# một tin "đã hồi phục" khi hết. Lượt không thấy vấn đề nào thì gọi APPBACK_PING_URL (dead man's switch
# ngoài máy — bắt được cả VPS chết; rỗng = tắt). Trạng thái: ${APPBACK_STATE_DIR:-/var/lib/appback}.
# `--test-alert`: gửi một tin thử rồi thoát. Mã thoát: 0 luôn, trừ khi đối số/môi trường hỏng.
set -euo pipefail

# alert()/alert_once()/alert_resolve() ở alert.sh; nó tự nạp lib.sh (COMPOSE_FILE/COMPOSE_ENV_FILES của bản
# đang chạy — K-35 —, json_escape, không python3 — NO-189).
# shellcheck source=deploy/scripts/alert.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/alert.sh"

# `--test-alert`: gửi một tin thử (kiểm ALERT_WEBHOOK_URL) rồi thoát, không đụng bộ đếm lỗi.
if [[ "${1:-}" == "--test-alert" ]]; then
  test_alert
  exit 0
fi

state_dir="${APPBACK_STATE_DIR:-/var/lib/appback}"
mkdir -p "$state_dir"
base_url="${APPBACK_BASE_URL:-http://127.0.0.1}"
disk_path="${APPBACK_DISK_PATH:-/}"
: "${APPBACK_QUEUE_STUCK_RUNS:=3}"
# <hàng>:<dịch vụ nghe hàng>:<app Celery của dịch vụ đó> — khớp `-Q` trong deploy/compose/base.yml.
: "${APPBACK_WATCH_QUEUES:=default:worker:apps.worker.celery_main pipeline.cpu:worker:apps.worker.celery_main ml.infer:ml:apps.ml.celery_main ml.training:ml-train:apps.ml.celery_main}"
problems=0
services=""

# /api/ready: bộ đếm lỗi liên tiếp trong tệp (nội dung hỏng → 0, hợp đồng §2 "luôn thoát 0" —
# review round 1 P3 RES-03); báo khi đủ 2 lần, hồi phục khi lại 200.
check_ready() {
  local f="$state_dir/health-failures" failures=0 code
  [[ -f "$f" ]] && failures="$(cat "$f")"
  [[ "$failures" =~ ^[0-9]+$ ]] || failures=0
  code="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$base_url/api/ready" || echo "000")"
  if [[ "$code" == "200" ]]; then
    failures=0
    alert_resolve api-ready "/api/ready trả 200"
  else
    failures=$((failures + 1))
    problems=1
    if (( failures >= 2 )); then
      alert_once api-ready "/api/ready hỏng $failures lần liên tiếp (mã $code)"
    fi
  fi
  printf '%s' "$failures" > "$f"
}

# Đĩa của $disk_path quá 85%.
check_disk() {
  local pct
  pct="$(df -P "$disk_path" | awk 'NR==2 { gsub("%", "", $5); print $5 }')"
  if [[ -n "$pct" ]] && (( pct > 85 )); then
    problems=1
    alert_once disk "đĩa $disk_path đầy ${pct}%"
  else
    alert_resolve disk "đĩa $disk_path còn chỗ"
  fi
}

# Trạng thái từng dịch vụ compose (danh sách theo COMPOSE_PROFILES, đọc vào biến toàn cục `services`
# cho check_queues). `docker compose ps -a`: container đã thoát vẫn hiện; không có container → "vắng".
check_services() {
  local status name state health svc
  local -A seen=()
  if ! services="$(docker compose config --services 2>/dev/null)" \
    || ! status="$(docker compose ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null)"; then
    services=""
    problems=1
    alert_once compose "không đọc được trạng thái compose ($COMPOSE_FILE) — Docker hỏng?"
    return 0
  fi
  alert_resolve compose "đọc lại được trạng thái compose"
  while read -r name state health; do
    [[ -n "$name" ]] && seen["$name"]="$state ${health:-}"
  done <<< "$status"
  for svc in $services; do
    case " migrate minio-init " in *" $svc "*) continue ;; esac
    read -r state health <<< "${seen[$svc]:-vắng}"
    if [[ "$state" != "running" || "${health:-}" == "unhealthy" ]]; then
      problems=1
      alert_once "svc-$svc" "dịch vụ $svc hỏng (trạng thái: $state${health:+, $health})"
    else
      alert_resolve "svc-$svc" "dịch vụ $svc chạy lại"
    fi
  done
}

# Độ dài hàng "$1" trên broker (redis-cli trong container redis-broker, URL kèm mật khẩu/DB lấy từ
# REDIS_BROKER_URL của chính container). Thoát 1 nếu không đọc được (broker hỏng — /api/ready báo).
queue_length() {
  local out
  # shellcheck disable=SC2016 # $REDIS_BROKER_URL/$1 giãn TRONG container redis-broker.
  out="$(docker compose exec -T redis-broker sh -c \
    'redis-cli -u "${REDIS_BROKER_URL:-redis://127.0.0.1:6379/0}" --no-auth-warning LLEN "$1"' sh "$1" 2>/dev/null)" \
    || return 1
  out="${out//[[:space:]]/}"
  [[ "$out" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$out"
}

# Dịch vụ "$1" (app Celery "$2") đang chạy ít nhất một việc không. Hỏng (container chết) = không.
queue_has_active() {
  local out
  # shellcheck disable=SC2016 # $HOSTNAME/$1 giãn TRONG container của dịch vụ.
  out="$(docker compose exec -T "$1" sh -c \
    'celery -A "$1" inspect active -d "celery@$HOSTNAME" --timeout 5 --json' sh "$2" 2>/dev/null)" || return 1
  [[ "$out" == *'"id"'* ]]
}

# Hàng kẹt: còn việc và không giảm qua APPBACK_QUEUE_STUCK_RUNS lượt (tệp queue-<hàng>: "<độ dài> <số
# lượt>"), trong khi dịch vụ nghe hàng không chạy việc nào. Hàng của dịch vụ không bật (profile) bỏ qua.
check_queues() {
  local entry q svc app len prev runs f
  for entry in $APPBACK_WATCH_QUEUES; do
    IFS=: read -r q svc app <<< "$entry"
    [[ $'\n'"$services"$'\n' == *$'\n'"$svc"$'\n'* ]] || continue
    len="$(queue_length "$q")" || continue
    f="$state_dir/queue-$q"
    prev=0 runs=0
    [[ -f "$f" ]] && read -r prev runs < "$f"
    [[ "$prev" =~ ^[0-9]+$ && "$runs" =~ ^[0-9]+$ ]] || { prev=0; runs=0; }
    if (( len > 0 && len >= prev )); then runs=$((runs + 1)); else runs=0; fi
    printf '%s %s\n' "$len" "$runs" > "$f"
    if (( runs >= APPBACK_QUEUE_STUCK_RUNS )) && ! queue_has_active "$svc" "$app"; then
      problems=1
      alert_once "queue-$q" "hàng $q kẹt: $len việc chờ, không giảm qua $runs lượt, $svc không chạy việc nào"
    else
      alert_resolve "queue-$q" "hàng $q chạy lại"
    fi
  done
}

check_ready
check_disk
# Chưa deploy lần nào (không release.env) thì compose không nội suy được — chưa có dịch vụ để kiểm.
if assert_env_ready 2>/dev/null && assert_release_env 2>/dev/null; then
  check_services
  check_queues
else
  echo "bỏ qua kiểm dịch vụ/hàng đợi — chưa có bản phát hành hoặc thiếu appback.env" >&2
fi

if (( problems == 0 )) && [[ -n "${APPBACK_PING_URL:-}" ]]; then
  curl -fsS --max-time 10 -o /dev/null "$APPBACK_PING_URL" || echo "cảnh báo: gọi APPBACK_PING_URL hỏng" >&2
fi
exit 0
