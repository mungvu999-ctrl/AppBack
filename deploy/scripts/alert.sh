#!/usr/bin/env bash
# Cảnh báo dùng chung (L5-N03): hàm `alert()` — một dòng stderr (systemd đưa vào journal) + POST JSON
# {"text","content"} tới $ALERT_WEBHOOK_URL nếu có (thân hợp đồng B0-10 §4; Slack đọc `text`, Discord
# đọc `content`). POST hỏng chỉ in cảnh báo, không làm hỏng người gọi.
#
# Hai cách dùng:
#   source deploy/scripts/alert.sh     # healthcheck.sh, về sau backup/restore — định nghĩa alert(),
#                                      # alert_once/alert_resolve (khử lặp theo khoá, K-39)
#   alert.sh --test-alert              # gửi MỘT tin thử rồi thoát 0 (kiểm webhook sau khi đặt secret)
# Chạy không đối số hoặc đối số lạ → in cách dùng, thoát 2.
set -euo pipefail

# json_escape (thân cảnh báo) nằm ở lib.sh, dùng chung với backup.sh (NO-189).
# shellcheck source=deploy/scripts/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

# alert <tin>: in "cảnh báo: <tin>" ra stderr rồi POST nếu ALERT_WEBHOOK_URL không rỗng.
alert() {
  local msg="$1" payload
  echo "cảnh báo: $msg" >&2
  if [[ -n "${ALERT_WEBHOOK_URL:-}" ]]; then
    payload="{\"text\": \"$(json_escape "$msg")\", \"content\": \"$(json_escape "$msg")\"}"
    curl -fsS --max-time 10 -X POST -H 'Content-Type: application/json' -d "$payload" "$ALERT_WEBHOOK_URL" >/dev/null \
      || echo "cảnh báo: gửi ALERT_WEBHOOK_URL hỏng" >&2
  fi
}

# Khử lặp theo khoá (K-39 — chủ duy nhất của ALERT_REPEAT_S; L5-N09 dùng lại): timer chạy mỗi phút,
# báo mọi lượt là spam 60 tin/giờ cho một sự cố. Trạng thái "đang hỏng" là một tệp mỗi khoá dưới
# $APPBACK_STATE_DIR/alerts chứa giờ (epoch) lần báo gần nhất — sống qua các lượt timer.
: "${ALERT_REPEAT_S:=3600}"

# alert_state_file <khoá>: đường tệp trạng thái của khoá (tạo thư mục nếu chưa có).
alert_state_file() {
  local dir="${APPBACK_STATE_DIR:-/var/lib/appback}/alerts"
  mkdir -p "$dir"
  printf '%s/%s' "$dir" "$1"
}

# alert_once <khoá> <tin>: báo khi khoá chuyển sang hỏng, rồi nhắc lại mỗi ALERT_REPEAT_S giây
# nếu vẫn hỏng; trong khoảng đó im lặng. Nội dung tệp hỏng (không phải số) → coi như chưa báo.
alert_once() {
  local key="$1" msg="$2" f last now
  f="$(alert_state_file "$key")"
  now="$(date +%s)"
  if [[ -f "$f" ]]; then
    last="$(cat "$f")"
    [[ "$last" =~ ^[0-9]+$ ]] || last=0
    (( now - last < ALERT_REPEAT_S )) && return 0
    msg="$msg (vẫn còn)"
  fi
  alert "$msg"
  printf '%s' "$now" > "$f"
}

# alert_resolve <khoá> <tin>: khoá đang ở trạng thái hỏng → gửi một tin "đã hồi phục" và xoá
# trạng thái; khoá không hỏng thì không làm gì (không spam tin hồi phục mỗi lượt).
alert_resolve() {
  local f
  f="$(alert_state_file "$1")"
  [[ -f "$f" ]] || return 0
  rm -f "$f"
  alert "đã hồi phục: $2"
}

# test_alert: một tin thử mang tên máy và giờ UTC; luôn thoát 0 (hỏng webhook đã in ở alert()).
test_alert() {
  alert "tin thử từ $(hostname 2>/dev/null || echo appback) lúc $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# Chỉ chạy khi được gọi trực tiếp, không khi `source`.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    --test-alert) test_alert ;;
    *)
      echo "dùng: alert.sh --test-alert   (hoặc: source alert.sh rồi gọi alert \"<tin>\")" >&2
      exit 2
      ;;
  esac
fi
