#!/usr/bin/env bash
# Dựng "bó phát hành" bất biến cho một lần triển khai (K-35): tar.gz giữ nguyên cấu trúc repo
# của mọi tệp máy đích cần — deploy/compose/{base.yml,prod.yml} (prod.yml `extends: file:
# base.yml`), deploy/minio/ (base.yml gắn `../minio/*` theo đường TƯƠNG ĐỐI với base.yml),
# deploy/scripts/, deploy/backup/. Bỏ tests/ và __pycache__/ (không chạy trên máy đích).
#
# Cách gọi: bundle.sh <tệp ra .tar.gz>. deploy.yml giải nén vào /opt/appback/releases/<tag>/
# rồi gọi releases/<tag>/deploy/scripts/deploy.sh <tag>.
# Đóng gói cây làm việc của checkout chứa script (CI checkout đúng sha nên cây = commit), không
# `git archive`: cổng verify chạy trên bản chép không có .git — test bó phải chạy được ở đó.
# In đường dẫn tuyệt đối của tệp ra. Mã thoát: 0 đạt; 1 tar hỏng; 2 đối số sai.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)"

if [[ "$#" -ne 1 || -z "$1" ]]; then
  echo "cách dùng: bundle.sh <tệp ra .tar.gz>" >&2
  exit 2
fi

mkdir -p "$(dirname -- "$1")"
out="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
tar -czf "$out" -C "$REPO_ROOT" --exclude='tests' --exclude='__pycache__' --exclude='*.pyc' \
  deploy/compose/base.yml deploy/compose/prod.yml deploy/minio deploy/scripts deploy/backup
echo "$out"
