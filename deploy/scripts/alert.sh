#!/usr/bin/env bash
# Cảnh báo dùng chung (L5-N03): hàm `alert()` — một dòng stderr (systemd đưa vào journal) + POST JSON
# {"text","content"} tới $ALERT_WEBHOOK_URL nếu có (thân hợp đồng B0-10 §4; Slack đọc `text`, Discord
# đọc `content`). POST hỏng chỉ in cảnh báo, không làm hỏng người gọi.
#
# Hai cách dùng:
#   source deploy/scripts/alert.sh     # healthcheck.sh, về sau backup/restore — chỉ định nghĩa alert()
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
