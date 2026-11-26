#!/usr/bin/env bash
# Lệnh cổng thật trên máy Windows (Git Bash). "just verify" trong mọi tài
# liệu nghĩa là `bash tools/verify/run.sh verify`. BE-01 [6]A.
#
# Việc: verify [--steps 1,2,4] | lock | openapi | merge-heads <tên> | shell | gc | hold -- <lệnh>
# (`shell` làm trên bản chép /tmp/w, không phải worktree — xem tools/verify/README.md)
set -euo pipefail
export MSYS_NO_PATHCONV=1
# PERF-03: mốc giờ từng pha khởi động ra stderr (không vào log cổng), để đo chi phí trước bước 1.
mark() { echo "[mốc] $(date +%s.%N) $*" >&2; }
mark "run.sh bắt đầu"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

win_path() {
  if [[ "$(uname -s)" == MINGW* ]]; then
    (cd "$1" && pwd -W)
  else
    (cd "$1" && pwd)
  fi
}

VERIFY_TASK="${1:-verify}"
shift || true

# ---------------------------------------------------------------------------
# Chuẩn bị chung (verify, openapi, merge-heads, shell dùng chung — [2])
# ---------------------------------------------------------------------------

: "${VERIFY_NAME:=$(basename "$REPO_ROOT")}"
VERIFY_NAME="$(printf '%s' "$VERIFY_NAME" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-')"
export VERIFY_NAME

# `hold -- <lệnh>` (PERF-11): chạy lệnh trên host (dựng ảnh web f14, build nặng…) khi giữ khoá cổng ở chế độ một
# mình — xếp hàng chung với `verify`, thay vòng chờ "không còn container verify + RAM trống". Một container nhỏ giữ
# khoá tới khi stdin của nó đóng: lệnh xong, hay `run.sh` bị giết, đều đóng stdin nên khoá nhả. Không cần tar nguồn.
if [[ "$VERIFY_TASK" == hold ]]; then
  [[ "${1:-}" == -- ]] && shift
  (( $# )) || { echo "hold cần lệnh: run.sh hold -- <lệnh>" >&2; exit 1; }
  coproc HOLDER {
    docker run --rm -i --name "appback-verify-hold-$VERIFY_NAME-$$" --entrypoint bash \
      -v appback-work:/work -v "$(win_path "$SCRIPT_DIR"):/gate:ro" appback-verify:local \
      -c "source /gate/gate_lock.sh && gate_lock x 'hold $VERIFY_NAME' && echo held && cat > /dev/null"
  }
  holder_pid=$HOLDER_PID
  read -r held <&"${HOLDER[0]}" || held=""
  [[ "$held" == held ]] || { echo "hold: không giữ được khoá cổng" >&2; exit 1; }
  mark "giữ khoá cổng (hold)"
  rc=0
  "$@" || rc=$?
  eval "exec ${HOLDER[1]}>&-"
  wait "$holder_pid" || true
  exit "$rc"
fi

if [[ -n "${VERIFY_BRANCH:-}" ]]; then
  case "$VERIFY_BRANCH" in
    integration|worker) ;;
    *) echo "VERIFY_BRANCH='$VERIFY_BRANCH' không hợp lệ (integration|worker)" >&2; exit 1 ;;
  esac
else
  current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
  if [[ "$current_branch" == "main" ]]; then
    VERIFY_BRANCH="integration"
  else
    VERIFY_BRANCH="worker"
  fi
fi
export VERIFY_BRANCH

# Gốc so sánh = tổ tiên chung GẦN NHẤT với `main` hoặc `origin/main` (PERF-11): `main` local ở checkout chính hay chậm
# hơn origin (01/11: 30 commit), nhánh tách từ origin/main khi đó thấy 47 tệp "đổi" thay vì 7 và `--fast` chạy quá nửa
# bộ test. `merge-base HEAD A B` lấy gốc chung với bản gộp giả của A và B, tức gốc mới hơn trong hai nhánh.
merge_base="$(git merge-base HEAD main origin/main 2>/dev/null || git merge-base HEAD main 2>/dev/null || git rev-parse HEAD)"
# PERF-13: nhánh xếp chồng (cắt từ một lát chưa vào `main`) không trả lại giá của các lát trước nó ở cổng nhanh — một
# migration hay hợp đồng của lát trước từng kéo cả bộ test cho MỌI lát sau. Gốc so sánh của `--fast` tiến tới đầu nhánh
# `origin/*` gần nhất đã nằm trọn trong HEAD (nhánh đã đẩy là nhánh đã qua cổng của chính nó), trừ nhánh cùng tên: đẩy
# rồi vẫn kiểm phần của mình. Cổng đầy đủ và CI vẫn so với `main`.
# shortcut: phần của chính mình đã đẩy dưới TÊN KHÁC bị coi là lát trước (chỉ kiểm phần chưa đẩy); cần thì loại thêm @{u}.
if [[ "$VERIFY_TASK" == verify && " $* " == *" --fast "* ]]; then
  own_ref="refs/remotes/origin/$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  mapfile -t pushed < <(git for-each-ref --merged HEAD --format='%(refname)' refs/remotes/origin | grep -vxF "$own_ref")
  if (( ${#pushed[@]} )); then
    # Nhánh đã gộp thêm `main` hay một nhánh khác có nhiều gốc gần nhất ngang hàng: lấy gốc cho ít tệp đổi nhất.
    stacked_base="$merge_base" fewest=-1
    for base in $(git merge-base --all HEAD "$merge_base" "${pushed[@]}" 2>/dev/null); do
      count="$(git diff --name-only "$base" | wc -l)"
      if (( fewest < 0 || count < fewest )); then fewest=$count stacked_base=$base; fi
    done
    if [[ "$stacked_base" != "$merge_base" ]]; then
      echo "cổng nhanh: so với lát trước đã đẩy $(git log -1 --format='%h (%D)' "$stacked_base"), không phải main" >&2
      merge_base="$stacked_base"
    fi
  fi
fi
VERIFY_CHANGED="$(
  { git diff --name-only "$merge_base" 2>/dev/null; git ls-files --others --exclude-standard; } \
    | sort -u
)"
export VERIFY_CHANGED

CACHE_DIR="$REPO_ROOT/.cache"
mkdir -p "$CACHE_DIR"

# Nguồn cổng đi vào container bằng MỘT tệp tar, không `cp -r /src/. /tmp/w` qua mount Windows→WSL2
# (đo trước: ~3.300 tệp, 169 s nguội — mỗi tệp ~50 ms qua mount). `git ls-files -co --exclude-standard`
# cho đúng tập cổng cần (đã theo dõi + chưa theo dõi không bị ignore) và bỏ luôn `.cache` (1.552 tệp
# không ai đọc trong container) cùng `backend/` bị ignore ở checkout chính (20.327 tệp / 341 MB).
# Tệp tar chứ không phải stdin: `run.sh shell < script` còn cần stdin cho script của người dùng (NO-299).
# Mỗi lượt một thư mục tar riêng (`mktemp -d`), xoá khi thoát: hai lượt song song của cùng worktree không
# ghi chung tệp tạm, không lượt nào giải nén tar của lượt kia (review PERF-01 F3).
# `--ignore-failed-read`: tệp đã theo dõi bị `rm` mà chưa stage vẫn có trong `git ls-files -c`; thiếu cờ này
# tar trả mã 2 và `pipefail` làm hỏng cả lượt (review PERF-01 F2) — cổng phải chạy đúng cây đang có trên đĩa.
# Dọn thư mục tar là việc phụ như dọn log cũ (NO-092): `rm` hỏng chỉ cảnh báo, không đổi mã thoát của cổng.
mkdir -p "$CACHE_DIR/src-tar"
SRC_TAR_DIR="$(mktemp -d "$CACHE_DIR/src-tar/run.XXXXXX")"
trap 'rm -rf "$SRC_TAR_DIR" || echo "dọn tar nguồn $SRC_TAR_DIR hỏng — bỏ qua, cổng vẫn chạy" >&2' EXIT
mark "tar nguồn"
git ls-files -co --exclude-standard -z | tar --null --no-recursion --ignore-failed-read -T - -cf "$SRC_TAR_DIR/src.tar"
VERIFY_SRC_TAR_DIR="$(win_path "$SRC_TAR_DIR")"
mark "tar nguồn xong"

# Số log cổng tối đa trong .cache/src-out/verify sau một lượt, tính cả log của lượt đó: 20 lượt ≈ 0,5 MB lượt
# đạt, đủ so lượt đỏ với vài lượt trước nó; log cần giữ lâu hơn thì chép ra ngoài (NO-090).
VERIFY_LOG_KEEP=20

source "$SCRIPT_DIR/appfront_repo.sh"

# AppFront @ APPFRONT_SHA — ENV §2 bước 3. Chưa có file SHA → thư mục rỗng.
APPFRONT_SHA_FILE="$REPO_ROOT/tools/contract/APPFRONT_SHA"
if [[ -f "$APPFRONT_SHA_FILE" ]]; then
  sha="$(tr -d '[:space:]' < "$APPFRONT_SHA_FILE")"
  # PERF-04: cache theo SHA dùng chung mọi worktree (trong `.git` chung) — worktree mới không giải nén lại
  # AppFront (~40 s). Thư mục tạm cùng ổ với đích nên `mv` là đổi tên, không chép từ /tmp (ổ C:) sang; hai
  # lượt cùng giải nén thì lượt sau thấy đích đã có (`mv -T` hỏng) và bỏ bản của mình.
  appfront_root="$(git rev-parse --path-format=absolute --git-common-dir)/verify-appfront"
  appfront_cache="$appfront_root/$sha"
  if [[ ! -d "$appfront_cache" ]]; then
    mkdir -p "$appfront_root"
    tmp_dir="$(mktemp -d "$appfront_root/tmp.XXXXXX")"
    git -c core.autocrlf=false -c core.eol=lf -C "${APPFRONT_REPO:-$(appfront_repo_default "$REPO_ROOT")}" archive "$sha" src package.json \
      | tar -x -C "$tmp_dir"
    mv -T "$tmp_dir" "$appfront_cache" 2> /dev/null || rm -rf "$tmp_dir"
  fi
  VERIFY_APPFRONT_DIR="$(win_path "$appfront_cache")"
else
  empty_dir="$CACHE_DIR/appfront-empty"
  mkdir -p "$empty_dir"
  VERIFY_APPFRONT_DIR="$(win_path "$empty_dir")"
fi
export VERIFY_APPFRONT_DIR

mkdir -p "$REPO_ROOT/contract-samples"
VERIFY_SRC_DIR="$(win_path "$REPO_ROOT")"
VERIFY_CONTRACT_SAMPLES_DIR="$(win_path "$REPO_ROOT/contract-samples")"
export VERIFY_SRC_DIR VERIFY_CONTRACT_SAMPLES_DIR

COMPOSE=(docker compose -p "appback-verify-$VERIFY_NAME" -f "$VERIFY_SRC_DIR/deploy/compose/verify.yml")

# PERF-10: stack compose đang chạy (mặc định `f14`, `VERIFY_SHARED_STACK=off` để tắt) → test dùng thẳng Postgres,
# hai Redis, MinIO của nó thay vì dựng 4 container (`packages/testing/fixtures/services.py`). Địa chỉ là IP trên
# mạng `<stack>_appback` (container verify tự nối vào lúc test cần); mật khẩu đọc từ env của container stack lúc
# chạy và vào container verify qua biến môi trường (`verify.yml`), không qua đối số hay log.
# Thiếu một trong bốn dịch vụ → không đặt gì, test dựng container như cũ.
: "${VERIFY_SHARED_STACK:=f14}"
urlenc() {  # mã hoá phần trăm (user/mật khẩu trong URL)
  local s="$1" out="" c i
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in [a-zA-Z0-9._~-]) out+="$c" ;; *) printf -v c '%%%02X' "'$c"; out+="$c" ;; esac
  done
  printf '%s' "$out"
}
stack_running() { [[ "$(docker inspect -f '{{.State.Running}}' "$VERIFY_SHARED_STACK-$1-1" 2> /dev/null)" == true ]]; }
stack_ip() {
  docker inspect -f "{{(index .NetworkSettings.Networks \"${VERIFY_SHARED_STACK}_appback\").IPAddress}}" \
    "$VERIFY_SHARED_STACK-$1-1"
}
stack_env() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$VERIFY_SHARED_STACK-$1-1" | sed -n "s/^$2=//p"; }
if [[ "$VERIFY_SHARED_STACK" != off ]] && stack_running postgres && stack_running redis-broker \
  && stack_running redis-cache && stack_running minio; then
  pg_user="$(urlenc "$(stack_env postgres POSTGRES_USER)")"
  pg_password="$(urlenc "$(stack_env postgres POSTGRES_PASSWORD)")"
  TEST_SHARED_NETWORK="${VERIFY_SHARED_STACK}_appback"
  TEST_SHARED_POSTGRES_URL="postgresql+asyncpg://$pg_user:$pg_password@$(stack_ip postgres):5432/postgres"
  TEST_SHARED_REDIS_BROKER_URL="redis://$(stack_ip redis-broker):6379/0"
  TEST_SHARED_REDIS_CACHE_URL="redis://$(stack_ip redis-cache):6379/0"
  TEST_SHARED_MINIO="$(stack_ip minio):9000,$(stack_env minio MINIO_ROOT_USER),$(stack_env minio MINIO_ROOT_PASSWORD)"
  export TEST_SHARED_NETWORK TEST_SHARED_POSTGRES_URL TEST_SHARED_REDIS_BROKER_URL TEST_SHARED_REDIS_CACHE_URL \
    TEST_SHARED_MINIO
  echo "dịch vụ test: dùng Postgres/Redis/MinIO của stack '$VERIFY_SHARED_STACK' (VERIFY_SHARED_STACK=off để tắt)" >&2
fi

# Volume `/work` dùng chung giữa mọi worktree (NO-108) là `external` trong verify.yml: tạo tường minh ở đây
# (đã có thì không làm gì) để lượt đầu trên máy sạch chạy được và compose không cảnh báo nhãn project (NO-185).
docker volume create appback-work > /dev/null

# Build riêng, stdin đóng, rồi mới `run`: `compose run --build` đọc stdin cho `buildx bake -f -` và nuốt script
# của `shell` (NO-299); mọi việc đi qua đây nên không việc nào bị nuốt.
# Ảnh verify chỉ dựng từ verify.Dockerfile (ảnh gốc ghim digest, không COPY gì từ repo): nhãn
# `appback.verify-key` = băm Dockerfile; trùng thì bỏ `compose build` (~33 s dù toàn CACHED, PERF-03).
VERIFY_IMAGE_KEY="$({ cat "$REPO_ROOT/deploy/docker/verify.Dockerfile" 2> /dev/null || true; } | sha256sum | cut -c1-16)"
export VERIFY_IMAGE_KEY
run_container() {
  mark "compose build"
  if [[ "$(docker image inspect -f '{{index .Config.Labels "appback.verify-key"}}' appback-verify:local 2> /dev/null)"     != "$VERIFY_IMAGE_KEY" ]]; then
    "${COMPOSE[@]}" build verify < /dev/null
  fi
  mark "compose run"
  "${COMPOSE[@]}" run --rm -v "$VERIFY_SRC_TAR_DIR:/src-tar:ro" "$@"
}

# ---------------------------------------------------------------------------
case "$VERIFY_TASK" in
  verify)
    # Log cổng ra host (NO-080): container tự xoá khi thoát, output chỉ đi qua client compose — shell
    # bọc bị cắt là mất bảng, file này thì còn. steps.py ghi song song stdout, không thay stdout.
    log_dir="$CACHE_DIR/src-out/verify"
    # Cổng nhanh ghi log ở thư mục riêng (review PERF-03 #5): không đẩy log cổng đầy đủ ra khỏi trần
    # `VERIFY_LOG_KEEP`, không bị lấy nhầm làm bằng chứng cổng đầy đủ.
    [[ " $* " == *" --fast "* ]] && log_dir="$CACHE_DIR/src-out/verify-fast"
    mkdir -p "$log_dir"
    # Trần log (NO-090): mỗi lượt một file, không dọn thì checkout sống lâu dồn mãi (in_container.sh còn chép
    # cả .cache mỗi lượt). Giữ VERIFY_LOG_KEEP - 1 log mới nhất (mtime) trước khi lượt này tạo log của nó.
    # Dọn là việc phụ (NO-092): log cũ đang bị giữ trên Windows ("Device or resource busy") làm `rm` hỏng,
    # file biến mất giữa lúc `find` đọc thư mục làm `find` hỏng — cảnh báo rồi vẫn chạy cổng.
    { find "$log_dir" -maxdepth 1 -type f -name '*.log' -printf '%T@\t%p\n' \
        | sort -rn | tail -n +"$VERIFY_LOG_KEEP" | cut -f2- | xargs -r -d '\n' rm -- \
      && # junit của bước 5 (NO-324) đi cùng log: log đã bị dọn thì junit của nó cũng đi
      find "$log_dir" -maxdepth 1 -type f -name '*.junit.xml' \
        | while IFS= read -r junit; do base="${junit%.junit.xml}"; [[ -e "${base%.perf}.log" ]] || rm -- "$junit"; done; } \
      || echo "dọn log cổng cũ hỏng — bỏ qua, cổng vẫn chạy" >&2
    log_name="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=12 HEAD).log"
    echo "log cổng: $(win_path "$log_dir")/$log_name"
    run_container -v "$(win_path "$log_dir"):/src-out/verify" -e "VERIFY_LOG_FILE=/src-out/verify/$log_name" \
      verify verify "$@"
    ;;

  lock)
    out_dir="$CACHE_DIR/src-out/lock"
    mkdir -p "$out_dir"
    run_container -v "$(win_path "$out_dir"):/src-out/lock" verify lock "$@"
    cp "$out_dir/uv.lock" "$REPO_ROOT/uv.lock"
    echo "đã chép $out_dir/uv.lock -> $REPO_ROOT/uv.lock"
    ;;

  openapi)
    out_dir="$CACHE_DIR/src-out/openapi"
    mkdir -p "$out_dir"
    run_container -v "$(win_path "$out_dir"):/src-out/openapi" verify openapi "$@"
    echo "Xong. Chép kết quả:  cp $out_dir/openapi.json $REPO_ROOT/openapi.json"
    ;;

  merge-heads)
    name="${1:-}"
    if [[ -z "$name" ]]; then
      echo "merge-heads cần <tên> (r<yyyymmdd>_merge_w<nn>_<k>)" >&2
      exit 1
    fi
    out_dir="$CACHE_DIR/src-out/merge-heads"
    mkdir -p "$out_dir"
    run_container -v "$(win_path "$out_dir"):/src-out/merge-heads" verify merge-heads "$name"
    echo "Xong. Chép revision mới:  cp $out_dir/*.py $REPO_ROOT/packages/db/migrations/versions/"
    ;;

  shell)
    # có TTY → bash tương tác; không có (vd `run.sh shell <<< 'lệnh'`) → đọc lệnh từ stdin
    if [[ -t 0 ]]; then tty_flag=-it; else tty_flag=-T; fi
    echo "shell: làm việc trên BẢN CHÉP /tmp/w của worktree — sửa file, ruff --fix, new_revision… ở đây KHÔNG về worktree (NO-190)." >&2
    run_container "$tty_flag" verify shell
    ;;

  gc)
    valid_names="$(
      git worktree list --porcelain \
        | awk '/^worktree /{print $2}' \
        | xargs -n1 -r basename \
        | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_\n-' '-' \
        | paste -sd, -
    )"
    run_container -e VERIFY_VALID_NAMES="$valid_names" verify gc
    ;;

  *)
    echo "việc lạ: $VERIFY_TASK (verify|lock|openapi|merge-heads|shell|gc|hold)" >&2
    exit 1
    ;;
esac
