#!/usr/bin/env bash
set -uo pipefail
cd /opt/data-platform
Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=20 "$@"; }

echo "=== 1. 等待 BE 心跳稳定 ==="
for i in 1 2 3; do
  echo "  heartbeats:"
  Q -N -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' '{printf "    host=%-16s id=%s lastHb=%s\n", $2,$1,$8}'
  sleep 6
done

echo
echo "=== 2. 建表（较长超时，观察真实报错） ==="
Q -e "CREATE TABLE IF NOT EXISTS ecommerce._ck (id BIGINT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1');" 2>&1 | head -5
echo "  --- 表是否创建 ---"
Q -N -B -e "SHOW TABLES FROM ecommerce;" 2>&1

echo
echo "=== 3. BE 端建 tablet 的日志（最近） ==="
docker exec doris-be bash -c 'grep -iE "create tablet|tablet.*fail|publish.*fail|meta.*fail|error" /opt/apache-doris/be/log/be.WARNING 2>/dev/null | tail -12'

echo
echo "=== 4. FE 端建表相关日志 ==="
docker logs doris-fe 2>&1 | grep -iE "create table|tablet|timeout|backend" | tail -12

echo
echo "=== 5. 保活包装是否已停止空转 ==="
docker logs doris-be 2>&1 | grep -E '\[keepalive\]' | tail -8

echo
echo "=== 6. 集群健康 ==="
Q -N -B -e "SHOW PROC '/backends';" 2>/dev/null | awk -F'\t' '{print "  "$1" "$2" "$3" alive="$9}' | head -5
