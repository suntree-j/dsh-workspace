#!/usr/bin/env bash
set -uo pipefail
cd /opt/data-platform

echo "=== .env 关键变量（口令脱敏） ==="
while IFS= read -r line; do
  case "$line" in
    MYSQL_ROOT_PASSWORD=*|MYSQL_PASSWORD=*|MINIO_ROOT_PASSWORD=*|DORIS_ROOT_PASSWORD=*)
      printf '%s********\n' "${line%%=*}="
      ;;
    MYSQL_ROOT_USER=*|MYSQL_USER=*|MYSQL_DATABASE=*|MINIO_ROOT_USER=*|MINIO_BUCKET=*|DATA_PLATFORM_SUBNET=*|DORIS_FE_IP=*|DORIS_BE_IP=*|TZ=*|KAFKA_VERSION=*|MYSQL_VERSION=*|DORIS_VERSION=*|MINIO_VERSION=*|KAFKA_EXTERNAL_PORT=*)
      printf '%s\n' "$line"
      ;;
  esac
done < .env

echo
echo "=== 口令长度校验（应为 28，且不含 change_me） ==="
for k in MYSQL_ROOT_PASSWORD MYSQL_PASSWORD MINIO_ROOT_PASSWORD DORIS_ROOT_PASSWORD; do
  v=$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)
  printf '  %-24s len=%s\n' "$k" "${#v}"
done
if grep -q 'change_me' .env; then echo "  !! 仍存在 change_me 占位符"; else echo "  OK: 无 change_me 占位符"; fi

echo
echo "=== 权限 ==="
chmod 600 .env
ls -l .env

echo
echo "=== docker compose config 校验 ==="
docker compose config --quiet && echo "  OK: compose 配置有效" || echo "  FAIL: compose 配置有误"
