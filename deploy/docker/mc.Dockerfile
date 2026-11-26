# MinIO Client (`mc`) dựng từ mã nguồn, cho `minio-init`, backup/restore và drill.
#
# MinIO đã lưu trữ dự án mã nguồn mở (02/11/2026): `quay.io/minio/mc` trả 401, `dl.min.io` trả 410 cho mọi bản
# cộng đồng. Repo `github.com/minio/mc` (AGPL-3.0) vẫn clone được, nên dựng lại đúng tag đang ghim. Khung ảnh theo
# `Dockerfile.release` của chính mc ở tag đó: ubi9-micro — `sh` là bash (`deploy/minio/init.sh` dùng `${x//a/b}`).
# Workflow `minio-images.yml` dựng và đẩy lên GHCR; nhãn `org.opencontainers.image.source` nối gói vào repo public.
FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS build
ARG MC_TAG=RELEASE.2025-08-13T08-35-41Z
ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN git clone --depth 1 --branch "${MC_TAG}" https://github.com/minio/mc /src \
    && test "$(git -C /src rev-parse HEAD)" = "${MC_COMMIT}"
WORKDIR /src
# Cùng lệnh với `make build` của mc ở tag này (cờ `kqueue`, ldflags ghi phiên bản).
RUN go build -tags kqueue -trimpath -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/mc \
    && /out/mc --version

FROM registry.access.redhat.com/ubi9/ubi-minimal@sha256:dfa0eb04abc59221544e74862432496add1f491f915c9b531880f91affc0e12c AS certs
RUN microdnf install ca-certificates --nodocs --assumeyes

FROM registry.access.redhat.com/ubi9/ubi-micro@sha256:5be877a2a280ae36eb9ab80ca4efb28bbf77c57abbba38137cdc37a03b8465e6
LABEL org.opencontainers.image.source="https://github.com/mungvu999-ctrl/AppBack" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="RELEASE.2025-08-13T08-35-41Z"
COPY --from=certs /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem /etc/pki/ca-trust/extracted/pem/
COPY --from=build /src/LICENSE /src/CREDITS /licenses/
COPY --from=build /out/mc /usr/bin/mc
ENTRYPOINT ["mc"]
