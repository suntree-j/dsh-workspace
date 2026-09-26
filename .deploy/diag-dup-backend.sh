#!/usr/bin/env bash
set -uo pipefail
cd /opt/data-platform
Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=15 "$@"; }

echo "=== 两个 backend 的详细字段（判断哪个在真正服务） ==="
Q -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) h[i]=$i; next} {printf "--- row ---\n"; for(i=1;i<=NF;i++) if(h[i] ~ /Id|Host|Alive|TabletNum|LastHeartbeat|ErrMsg|Version|Status/) printf "  %s = %s\n", h[i], $i}'

echo
echo "=== BE 自身配置：be_host / priority_networks ==="
docker exec doris-be bash -c 'grep -iE "^be_host|^priority_networks|^heartbeat_service_port|^webserver_port" /opt/apache-doris/be/conf/be.conf 2>/dev/null || echo "  (be.conf 中无显式 be_host)"'

echo
echo "=== BE 对外上报的自身地址（日志） ==="
docker exec doris-be bash -c 'grep -iE "local host|hostname|BE start time|register" /opt/apache-doris/be/log/be.INFO 2>/dev/null | tail -6'

echo
echo "=== 哪个 backend 有 tablet（真正在服务） ==="
Q -N -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' '{printf "  host=%-16s id=%s tabletNum=%s dataUsed=%s\n", $2,$1,$11,$12}'

echo
echo "=== 建表测试（验证当前可用性） ==="
Q -e "CREATE TABLE IF NOT EXISTS ecommerce._ck (id BIGINT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1');" 2>&1 | head -2
echo "  create rc=$?"
Q -e "DROP TABLE IF EXISTS ecommerce._ck;" 2>&1 | head -1

echo
echo "=== 保活包装是否重复注册：查看上一轮日志 ==="
docker logs doris-be 2>&1 | grep -E '\[keepalive\]|add backend|Check myself' | tail -12
