#!/usr/bin/env bash
# ============================================================
# 拉取 Sprint 0 所需的全部镜像（固定版本）
# ============================================================
set -u
IMAGES=(
  "mysql:8.4.11"
  "apache/kafka:4.2.1"
  "coollabsio/minio:RELEASE.2025-10-15T17-29-55Z"
  "apache/doris:fe-4.1.4"
  "apache/doris:be-4.1.4"
  "python:3.13.14-slim-bookworm"
)

echo "=== 开始拉取镜像 ==="
FAILED=()
for img in "${IMAGES[@]}"; do
  echo "---------------------------------------------"
  echo ">>> $img"
  start=$(date +%s)
  if docker pull "$img" 2>&1 | tail -4; then
    echo "    OK ($(( $(date +%s) - start ))s)"
  else
    echo "    FAILED: $img"
    FAILED+=("$img")
  fi
done

echo
echo "=== 本地镜像列表 ==="
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'

echo
echo "=== 磁盘占用 ==="
df -h / | tail -1
docker system df

echo
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "!!! 拉取失败的镜像: ${FAILED[*]}"
  exit 1
fi
echo "=== 全部镜像拉取成功 ==="
