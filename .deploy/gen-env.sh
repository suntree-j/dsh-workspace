#!/usr/bin/env bash
# ============================================================
# 在服务器上生成 .env（随机强口令，不入 Git）
#
# 注意：上一版用 `tr -dc ... < /dev/urandom | head -c N` 生成口令，
#       在 `set -e` 下 tr 会因 SIGPIPE 退出，导致 sed 未执行、
#       口令仍是 change_me_* 占位符。这里改用 openssl rand。
# ============================================================
set -euo pipefail

cd /opt/data-platform
[ -f .env.example ] || { echo "ERROR: 找不到 .env.example" >&2; exit 1; }

if [ -f .env ]; then
  bak=".env.bak.$(date +%Y%m%d%H%M%S)"
  mv .env "$bak"
  echo "旧 .env 已备份为 $bak"
fi
cp .env.example .env

gen() {
  # 48 个十六进制字符 = 192 bit 熵
  openssl rand -hex 24
}

MYSQL_ROOT_PW="$(gen)"
MYSQL_APP_PW="$(gen)"
MINIO_PW="$(gen)"
DORIS_PW="$(gen)"

# 用 awk 做替换，避免 sed 对特殊字符敏感
awk -v r="$MYSQL_ROOT_PW" -v a="$MYSQL_APP_PW" -v m="$MINIO_PW" -v d="$DORIS_PW" '
  /^MYSQL_ROOT_PASSWORD=/ { print "MYSQL_ROOT_PASSWORD=" r; next }
  /^MYSQL_PASSWORD=/      { print "MYSQL_PASSWORD=" a; next }
  /^MINIO_ROOT_PASSWORD=/ { print "MINIO_ROOT_PASSWORD=" m; next }
  /^DORIS_ROOT_PASSWORD=/ { print "DORIS_ROOT_PASSWORD=" d; next }
  { print }
' .env > .env.tmp
mv .env.tmp .env
chmod 600 .env

echo "=== 校验：不应再有 change_me 占位符 ==="
if grep -q 'change_me' .env; then
  echo "  FAIL: 仍存在 change_me" >&2
  grep -n 'change_me' .env >&2
  exit 1
fi
echo "  OK"

echo
echo "=== 口令长度（应为 48） ==="
for k in MYSQL_ROOT_PASSWORD MYSQL_PASSWORD MINIO_ROOT_PASSWORD DORIS_ROOT_PASSWORD; do
  v=$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)
  printf '  %-24s len=%s\n' "$k" "${#v}"
done

echo
echo "=== 非口令关键变量 ==="
grep -E '^(TZ|MYSQL_VERSION|MYSQL_DATABASE|MYSQL_USER|KAFKA_VERSION|KAFKA_EXTERNAL_PORT|MINIO_VERSION|MINIO_ROOT_USER|MINIO_BUCKET|DORIS_VERSION|DATA_PLATFORM_SUBNET|DORIS_FE_IP|DORIS_BE_IP)=' .env

echo
echo "=== 权限 ==="
ls -l .env

echo
echo "=== docker compose config 校验 ==="
docker compose config --quiet && echo "  OK: compose 配置有效"
