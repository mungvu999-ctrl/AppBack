#!/usr/bin/env bash
# Sao lưu Postgres + kho object của AppBack (B0-10 [2], hợp đồng §3).
#
# Gọi: backup.sh            — tạo bản sao lưu mới rồi xoay vòng.
#      backup.sh --rotate-only — chỉ chạy xoay vòng trên BACKUP_TARGET hiện có (để kiểm độc lập).
#
# Biến môi trường (đều có mặc định, hợp đồng B0-10 §1, §3):
#   APPBACK_DIR, COMPOSE_FILE, COMPOSE_ENV_FILES, COMPOSE_PROJECT_NAME — mặc định của lib.sh:
#                            compose của bản đang chạy ($APPBACK_DIR/current/…/prod.yml), nạp
#                            appback.env + release.env (IMAGE_TAG — L5-N07). Người gọi tự đặt
#                            (drill.sh) thì giữ nguyên.
#   BACKUP_TARGET           /var/backups/appback   — thư mục gốc chứa các bản sao lưu theo ngày giờ.
#   APPBACK_STORAGE          s3                     — "s3" mirror bucket MinIO; "local" tar volume.
#   BACKUP_AGE_RECIPIENT     (rỗng)                 — mã hoá db.dump/objects.tar bằng age -r; rỗng → thoát 1
#                                                   (bắt buộc mã hoá ở mọi môi trường) trừ khi:
#   BACKUP_ALLOW_PLAINTEXT   (rỗng)                 — đặt đúng "1" mới cho ghi bản rõ (chỉ dev, diễn tập).
#
# Mã thoát: 0 đạt; 1 hỏng (thư mục dở bị xoá trước khi thoát); 2 thiếu appback.env/release.env.
set -euo pipefail
# Bản sao lưu chứa hash mật khẩu và mọi bản vẽ: thư mục/tệp do script tạo chỉ chủ đọc được (SEC-040).
umask 077

: "${BACKUP_TARGET:=/var/backups/appback}"
: "${BACKUP_AGE_RECIPIENT:=}"
: "${APPBACK_STORAGE:=s3}"

# json_escape (manifest.json) dùng chung với healthcheck.sh qua lib.sh — không còn python3 (NO-189).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${APPBACK_SCRIPTS_DIR:=$script_dir/../scripts}"
# shellcheck source=deploy/scripts/lib.sh
source "$APPBACK_SCRIPTS_DIR/lib.sh"

case "$APPBACK_STORAGE" in
  s3 | local) ;;
  *)
    echo "loi: APPBACK_STORAGE phai la s3 hoac local, dang la: $APPBACK_STORAGE" >&2
    exit 1
    ;;
esac

# Giữ 7 bản mới nhất + bản mới nhất của mỗi tuần ISO trong 4 tuần gần nhất (hợp đồng §3);
# chỉ xoá thư mục khớp mẫu tên yyyymmddThhmmssZ, thư mục lạ giữ nguyên.
rotate_backups() {
  local target="$1"
  mkdir -p "$target"
  local name week i path
  local -a names=() keep=()
  local -A week_of=() kept=()
  # Tên yyyymmddThhmmssZ sắp theo chữ = sắp theo thời gian; mới nhất trước. Tên có ngày giờ
  # không hợp lệ (`date` từ chối) hoặc không khớp mẫu thì giữ nguyên, không xét.
  for path in "$target"/*/; do
    name="${path%/}"
    name="${name##*/}"
    [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || continue
    week="$(date -u -d "${name:0:8} ${name:9:2}:${name:11:2}:${name:13:2}" +%G-%V 2>/dev/null)" || continue
    names+=("$name")
    week_of["$name"]="$week"
  done
  (( ${#names[@]} > 0 )) || return 0
  # Tên yyyymmddThhmmssZ sắp theo chữ = sắp theo thời gian; mới nhất trước.
  mapfile -t names < <(printf '%s\n' "${names[@]}" | LC_ALL=C sort -r)
  keep=("${names[@]:0:7}")
  for i in 0 1 2 3; do
    week="$(date -u -d "$i weeks ago" +%G-%V)"
    for name in "${names[@]}"; do
      if [[ "${week_of[$name]}" == "$week" ]]; then
        keep+=("$name")
        break
      fi
    done
  done
  for name in "${keep[@]}"; do kept["$name"]=1; done
  for name in "${names[@]}"; do
    path="$target/$name"
    [[ -n "${kept[$name]:-}" ]] || rm -rf "$path"
  done
}

if [[ "${1:-}" == "--rotate-only" ]]; then
  rotate_backups "$BACKUP_TARGET"
  exit 0
fi

# Mã hoá age là mặc định bắt buộc ở MỌI môi trường (B0-10 [6], C-18, C1): thiếu recipient thì dừng trước pg_dump,
# không ghi bản rõ — trừ khi đặt tường minh BACKUP_ALLOW_PLAINTEXT=1 (chỉ dev, diễn tập; production vẫn thoát 1).
if [[ -z "$BACKUP_AGE_RECIPIENT" && ( "${BACKUP_ALLOW_PLAINTEXT:-}" != "1" || "${APP_ENV:-}" == "production" ) ]]; then
  echo "loi: thieu BACKUP_AGE_RECIPIENT (sao luu phai ma hoa age); chi dev/dien tap moi dat BACKUP_ALLOW_PLAINTEXT=1" >&2
  exit 1
fi

# Compose của bản đang chạy cần appback.env (biến) và release.env (IMAGE_TAG) — thiếu thì mọi
# `docker compose exec` dưới đây hỏng ở nội suy `${…:?}`; báo rõ trước khi tạo thư mục (L5-N07).
assert_env_ready || exit $?
assert_release_env || exit $?

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage_dir="$BACKUP_TARGET/$timestamp"

if [[ -e "$stage_dir" ]]; then
  echo "loi: thu muc da ton tai: $stage_dir" >&2
  exit 1
fi
mkdir -p "$stage_dir"

# Xoá thư mục dở khi có lỗi giữa chừng (B0-10 [2]), giữ nguyên mã thoát gốc.
cleanup() {
  local ec=$?
  trap - EXIT
  if [[ "$ec" -ne 0 && -d "$stage_dir" ]]; then
    rm -rf "$stage_dir"
  fi
  exit "$ec"
}
trap cleanup EXIT

# 1) pg_dump trước mọi thao tác object (B0-10 [2]).
# shellcheck disable=SC2016 # biến $POSTGRES_USER/$POSTGRES_DB phải giãn TRONG container postgres.
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$stage_dir/db.dump"

# 2) đồng bộ object: s3 -> mc mirror (không --remove); local -> tar volume local-storage.
object_count=0
objects_dir=""
case "$APPBACK_STORAGE" in
  s3)
    objects_dir="$stage_dir/objects"
    mkdir -p "$objects_dir"
    # shellcheck disable=SC2016 # $S3_ENDPOINT/$S3_BUCKET/... phải giãn TRONG container minio-init.
    docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -e MC_CONFIG_DIR=/tmp/.mc \
      --entrypoint sh -v "$objects_dir:/backup-objects" minio-init -c \
      'mc alias set local "$S3_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror local/"$S3_BUCKET" /backup-objects'
    object_count="$(find "$objects_dir" -type f | wc -l | tr -d ' ')"
    ;;
  local)
    # tar ra stdout: stage_dir là 0700 của người chạy script, container `api` (uid 10001) không ghi vào đó được.
    docker compose run --rm --no-deps -T --entrypoint tar api \
      cf - -C /var/lib/appback/storage . > "$stage_dir/objects.tar"
    # Thư mục trong tar kết thúc bằng "/"; còn lại là tệp. `tar -tf` hỏng thì `set -e` dừng ở dòng gán.
    tar_listing="$(tar -tf "$stage_dir/objects.tar")"
    object_count="$(printf '%s\n' "$tar_listing" | { grep -v '/$' || true; } | { grep -c . || true; })"
    ;;
esac

# 3) alembic head — revision đang áp dụng cho CSDL vừa dump (để khôi phục biết nâng lên head nào).
# shellcheck disable=SC2016 # $POSTGRES_USER/$POSTGRES_DB phải giãn TRONG container postgres.
alembic_head="$(docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select version_num from alembic_version"')"
alembic_head="$(printf '%s' "$alembic_head" | tr -d '[:space:]')"

# 4) mã hoá nếu có BACKUP_AGE_RECIPIENT: tar hoá objects/ (nếu là s3), age -r từng tệp dữ liệu, xoá bản rõ.
encrypted=false
if [[ -n "$BACKUP_AGE_RECIPIENT" ]]; then
  encrypted=true
  if [[ "$APPBACK_STORAGE" == "s3" ]]; then
    tar -cf "$stage_dir/objects.tar" -C "$objects_dir" .
    rm -rf "$objects_dir"
  fi
  age -r "$BACKUP_AGE_RECIPIENT" -o "$stage_dir/db.dump.age" "$stage_dir/db.dump"
  rm -f "$stage_dir/db.dump"
  age -r "$BACKUP_AGE_RECIPIENT" -o "$stage_dir/objects.tar.age" "$stage_dir/objects.tar"
  rm -f "$stage_dir/objects.tar"
fi

# 5) manifest.json — dựng bằng bash thuần (không python3, NO-189), băm đúng tệp còn lại trên đĩa.
# Khoá theo thứ tự chữ cái, thụt 2 dấu cách — cùng khuôn json.dumps(indent=2, sort_keys=True)
# của bản cũ để restore.sh đọc được cả hai.
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
manifest_files=""
while IFS= read -r -d '' file; do
  rel="${file#"$stage_dir"/}"
  digest="$(sha256sum "$file")"
  manifest_files+="${manifest_files:+,
}    \"$(json_escape "$rel")\": \"${digest%% *}\""
done < <(find "$stage_dir" -type f -print0 | LC_ALL=C sort -z)
if [[ -n "$manifest_files" ]]; then
  manifest_files="{
$manifest_files
  }"
else
  manifest_files="{}"
fi
cat > "$stage_dir/manifest.json" <<EOF
{
  "alembic_head": "$(json_escape "$alembic_head")",
  "created_at": "$created_at",
  "encrypted": $encrypted,
  "files": $manifest_files,
  "object_count": $object_count,
  "storage": "$APPBACK_STORAGE"
}
EOF

# 6) xoay vòng bản cũ trong cùng BACKUP_TARGET.
rotate_backups "$BACKUP_TARGET"

# Đường dẫn bản sao lưu vừa tạo phải là dòng stdout cuối (hợp đồng B0-10 §2).
echo "$stage_dir"
