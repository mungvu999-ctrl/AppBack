#!/usr/bin/env bash
# Kiểm sức khoẻ định kỳ (systemd timer 5 phút — appback-health.{service,timer}):
# /api/ready lỗi 2 lần liên tiếp, hoặc đĩa > 85%, thì cảnh báo (một dòng stderr,
# systemd đưa vào journal, + POST $ALERT_WEBHOOK_URL nếu có — thân hợp đồng B0-10
# §4). POST hỏng chỉ in cảnh báo, không làm hỏng lượt chạy. Không đối số.
# Bộ đếm lỗi liên tiếp: ${APPBACK_STATE_DIR:-/var/lib/appback}/health-failures.
# Mã thoát: 0 luôn, trừ khi đối số/môi trường hỏng.
set -euo pipefail

# alert() dùng chung (L5-N03) nằm ở alert.sh; nó tự nạp lib.sh (json_escape, không python3 — NO-189).
# shellcheck source=deploy/scripts/alert.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/alert.sh"

# `--test-alert`: gửi một tin thử (kiểm ALERT_WEBHOOK_URL) rồi thoát, không đụng bộ đếm lỗi.
if [[ "${1:-}" == "--test-alert" ]]; then
  test_alert
  exit 0
fi

state_dir="${APPBACK_STATE_DIR:-/var/lib/appback}"
mkdir -p "$state_dir"
state_file="$state_dir/health-failures"
base_url="${APPBACK_BASE_URL:-http://127.0.0.1}"
disk_path="${APPBACK_DISK_PATH:-/}"

failures=0
[[ -f "$state_file" ]] && failures="$(cat "$state_file")"
# Nội dung hỏng (không phải số nguyên, vd đĩa đầy ghi dở, tệp trống) → coi là 0 thay vì làm
# `$((failures + 1))` lỗi số học và script thoát khác 0 (review round 1 P3 RES-03; hợp đồng
# §2 đòi script luôn thoát 0).
[[ "$failures" =~ ^[0-9]+$ ]] || failures=0

code="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$base_url/api/ready" || echo "000")"
if [[ "$code" == "200" ]]; then
  failures=0
else
  failures=$((failures + 1))
  if (( failures >= 2 )); then
    alert "/api/ready hỏng $failures lần liên tiếp (mã $code)"
  fi
fi
printf '%s' "$failures" > "$state_file"

disk_pct="$(df -P "$disk_path" | awk 'NR==2 { gsub("%", "", $5); print $5 }')"
if [[ -n "$disk_pct" ]] && (( disk_pct > 85 )); then
  alert "đĩa $disk_path đầy ${disk_pct}%"
fi

exit 0
