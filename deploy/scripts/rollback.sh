#!/usr/bin/env bash
# Đưa api và mọi dịch vụ ứng dụng (worker, beat, web, ml… theo COMPOSE_PROFILES) về tag
# trước, không gián đoạn api như deploy.sh, bằng compose của CHÍNH bó releases/<tag> (lùi cả
# cấu hình, không chỉ ảnh — K-35). KHÔNG migrate, không alembic downgrade (expand/contract bảo
# đảm bản cũ chạy trên lược đồ mới — BE-00 §6.1). Cách gọi: rollback.sh [<tag>] — thiếu thì
# đọc $APPBACK_DIR/previous_tag. Thứ tự: chọn releases/<tag> → kiểm appback.env → pull → đổi
# api → đổi dịch vụ ứng dụng → smoke → ghi release.env + symlink current + current_tag.
# Mã thoát: 0 đạt; 1 hỏng; 2 thiếu tag, tag sai mẫu, thiếu bó releases/<tag> hoặc appback.env.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=deploy/scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

tag="${1:-}"
if [[ -z "$tag" ]]; then
  tag="$(read_state previous_tag)"
fi
if [[ -z "$tag" ]]; then
  echo "không có tag để rollback (thiếu đối số và previous_tag)" >&2
  exit 2
fi
if ! validate_tag "$tag"; then
  echo "tag không hợp lệ: $tag" >&2
  exit 2
fi
select_release "$tag" || exit $?
assert_env_ready || exit $?

export IMAGE_TAG="$tag"
# --policy missing: tag bất biến, bỏ qua nếu ảnh đã có cục bộ (deploy.sh cùng lý do).
docker compose pull --policy missing

if ! swap_api "$tag"; then
  echo "hỏng rollback — container api mới không healthy tới timeout" >&2
  exit 1
fi
if ! swap_services "$tag"; then
  echo "hỏng rollback — không đổi được dịch vụ ứng dụng" >&2
  exit 1
fi

if ! run_smoke; then
  echo "hỏng smoke sau rollback" >&2
  exit 1
fi

record_release "$tag"
exit 0
