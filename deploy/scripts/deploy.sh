#!/usr/bin/env bash
# Triển khai tag ảnh mới lên $APPBACK_DIR (mặc định /opt/appback) bằng bó phát hành
# releases/<tag>/ (bundle.sh dựng, deploy.yml chép lên — K-35). Cách gọi:
# deploy.sh <tag> [--dry-run]. Thứ tự cố định: kiểm tag → chọn releases/<tag> → kiểm
# appback.env → pull → dựng hạ tầng (L5-N01) → ghi previous_tag → migrate (ảnh mới) →
# publish thư viện → đổi api không gián đoạn → đổi các dịch vụ ứng dụng (worker, beat, web,
# ml… theo COMPOSE_PROFILES) → smoke → ghi release.env + symlink current + current_tag.
# Hỏng ở hạ tầng/migrate thì dừng, không đổi container ứng dụng; hỏng ở đổi api/dịch vụ/smoke
# thì gọi rollback.sh CỦA BẢN TRƯỚC (releases/<previous_tag>) để lùi cả compose lẫn ảnh.
# --dry-run chỉ in kế hoạch (chỉ gọi lệnh đọc `docker compose config`), không ghi trạng thái.
# Mã thoát: 0 đạt; 1 hỏng; 2 đối số/môi trường sai (tag, thiếu bó, appback.env).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=deploy/scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"

tag="${1:-}"
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=1

if [[ -z "$tag" ]]; then
  echo "cách dùng: deploy.sh <tag> [--dry-run]" >&2
  exit 2
fi
if ! validate_tag "$tag"; then
  echo "tag không hợp lệ: $tag" >&2
  exit 2
fi
select_release "$tag" || exit $?
if [[ "$DRY_RUN" != "1" ]]; then
  assert_env_ready || exit $?
fi

previous_tag=""
if [[ "$DRY_RUN" != "1" ]]; then
  previous_tag="$(read_state current_tag)"
fi

# Lùi về previous_tag bằng rollback.sh nằm trong bó của chính bản đó — compose, script và ảnh
# cùng một phiên bản. Trả môi trường gốc của người gọi (COMPOSE_FILE/COMPOSE_ENV_FILES) để lib
# của bản trước tự chọn bó của nó thay vì kế thừa compose bản mới mà deploy.sh vừa export.
rollback_to_previous() {
  if [[ -z "$previous_tag" ]]; then
    echo "không có previous_tag — không tự rollback được" >&2
    return 1
  fi
  # bash <script>, không exec đường dẫn trực tiếp: git archive giữ mode 644 của repo, và container
  # verify chmod 644 mọi file khi chép từ mount Windows (ENV.md §2) — bit thực thi không đáng tin.
  COMPOSE_FILE="$LIB_CALLER_COMPOSE_FILE" COMPOSE_ENV_FILES="$LIB_CALLER_ENV_FILES" \
    bash "$APPBACK_DIR/releases/$previous_tag/deploy/scripts/rollback.sh" "$previous_tag"
}

export IMAGE_TAG="$tag"
# --policy missing: tag bất biến (sha-.../v...) nên bỏ qua nếu đã có cục bộ — đúng cho ảnh
# đã build tại chỗ lúc kiểm không gián đoạn (vd IMAGE_TAG=ci2), không kéo lại từ registry.
run docker compose pull --policy missing

if ! bootstrap_infra; then
  echo "hỏng dựng hạ tầng — không đổi container ứng dụng" >&2
  exit 1
fi

if [[ "$DRY_RUN" != "1" && -n "$previous_tag" ]]; then
  write_state previous_tag "$previous_tag"
fi

if ! run docker compose run --rm migrate; then
  echo "hỏng migrate — không đổi container" >&2
  exit 1
fi

# Thư viện .glb rỗng tới lượt lịch đầu nếu không publish ngay (NO-242). Không chí mạng:
# beat chạy default.library.publish_library_assets bù lại.
run docker compose run --rm --no-deps api python -m apps.api.library.cli publish   || echo "cảnh báo: publish thư viện hỏng — beat sẽ publish lại theo lịch" >&2

if ! swap_api "$tag"; then
  echo "hỏng đổi api — container mới không healthy tới timeout" >&2
  rollback_to_previous || true
  exit 1
fi

if ! swap_services "$tag" || ! run_smoke; then
  echo "hỏng đổi dịch vụ hoặc smoke — rollback về previous_tag" >&2
  rollback_to_previous || true
  exit 1
fi

record_release "$tag"
exit 0
