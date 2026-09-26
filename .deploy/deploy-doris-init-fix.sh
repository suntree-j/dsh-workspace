#!/usr/bin/env bash
# 部署修正后的 Doris 初始化方案并重建验证
set -uo pipefail
cd /opt/data-platform

echo "############ 1. 安装文件 ############"
install -m 0755 /tmp/be-keepalive.sh infrastructure/doris/be-keepalive.sh
install -m 0644 /tmp/02_doris_init.sql sql/doris/02_doris_init.sql
install -m 0755 /tmp/03_seed_test_connection.sh sql/doris/03_seed_test_connection.sh
install -m 0755 /tmp/health-check.sh scripts/health-check.sh
bash -n infrastructure/doris/be-keepalive.sh && echo "  keepalive 语法 OK"
bash -n sql/doris/03_seed_test_connection.sh && echo "  seed 语法 OK"
bash -n scripts/health-check.sh && echo "  health-check 语法 OK"

echo
echo "############ 2. 重建 BE（触发初始化目录执行） ############"
docker compose stop doris-be 2>&1 | tail -1
docker volume rm data-platform-doris-be-storage data-platform-doris-be-log 2>&1 | tail -2
docker compose rm -sf doris-be 2>&1 | tail -1
docker compose up -d doris-be 2>&1 | tail -2

echo
echo "############ 3. 等待 BE 就绪 + 初始化执行 ############"
for i in $(seq 1 60); do
  [ "$(docker inspect doris-be --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 5
done
echo "  BE health=$(docker inspect doris-be --format '{{.State.Health.Status}}')"
sleep 20

echo
echo "############ 4. keepalive + 初始化日志 ############"
docker logs doris-be 2>&1 | grep -E '\[keepalive\]|\[seed\]|Executing|ERROR' | tail -25

echo
echo "############ 5. Doris 内容 ############"
Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=20 "$@"; }
echo "--- 数据库 ---"; Q -N -B -e "SHOW DATABASES;" 2>&1
echo "--- ecommerce 表 ---"; Q -N -B -e "SHOW TABLES FROM ecommerce;" 2>&1
echo "--- test_connection ---"; Q -B -e "SELECT * FROM ecommerce.test_connection;" 2>&1

echo
echo "############ 6. 建表/写入/查询 ############"
Q -e "CREATE TABLE IF NOT EXISTS ecommerce._verify (id BIGINT, v VARCHAR(32)) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1');" 2>&1 | head -2
echo "  create rc=$?"
Q -e "INSERT INTO ecommerce._verify VALUES (1,'ok');" 2>&1 | head -2
echo "  insert rc=$?"
echo -n "  select -> "; Q -N -B -e "SELECT * FROM ecommerce._verify;" 2>&1
Q -e "DROP TABLE IF EXISTS ecommerce._verify;" 2>&1 | head -1

echo
echo "############ 7. 全服务健康检查 ############"
bash scripts/health-check.sh
echo "exit=$?"

echo
echo "############ 8. keepalive 是否空转 ############"
docker logs doris-be 2>&1 | grep -c '尝试重新拉起' || true
