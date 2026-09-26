#!/usr/bin/env bash
# ============================================================
# 重置 Doris 存储（清理崩溃循环期留下的损坏 tablet）
# 并验证建表 / 写入 / 查询
# ============================================================
set -uo pipefail
cd /opt/data-platform

echo "############ 1. 安装修正后的保活包装 ############"
install -m 0755 /tmp/be-keepalive.sh infrastructure/doris/be-keepalive.sh
bash -n infrastructure/doris/be-keepalive.sh && echo "  语法 OK"

echo
echo "############ 2. 停止 Doris 并重置存储卷 ############"
docker compose stop doris-be doris-fe 2>&1 | tail -3
echo "--- 删除 BE 存储卷（脏 tablet 状态）---"
docker volume rm data-platform-doris-be-storage 2>&1 | tail -1
docker volume rm data-platform-doris-be-log 2>&1 | tail -1 || true

echo
echo "############ 3. 重新创建 Doris（FE -> BE） ############"
docker compose up -d doris-fe 2>&1 | tail -3
echo "--- 等待 FE healthy ---"
for i in $(seq 1 40); do
  h=$(docker inspect doris-fe --format '{{.State.Health.Status}}' 2>/dev/null)
  [ "$h" = "healthy" ] && break
  sleep 5
done
echo "  FE health=$(docker inspect doris-fe --format '{{.State.Health.Status}}')"

docker compose up -d doris-be 2>&1 | tail -3
echo "--- 等待 BE 就绪 ---"
for i in $(seq 1 60); do
  h=$(docker inspect doris-be --format '{{.State.Health.Status}}' 2>/dev/null)
  [ "$h" = "healthy" ] && break
  printf '.'; sleep 5
done
echo
echo "  BE health=$(docker inspect doris-be --format '{{.State.Health.Status}}')"

echo
echo "############ 4. 等待 BE 注册并稳定 ############"
sleep 30
Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=20 "$@"; }
Q -N -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' '{printf "  host=%-16s id=%s alive=%s tabletNum=%s\n", $2,$1,$9,$11}'

echo
echo "############ 5. 保活包装是否已停止空转 ############"
docker logs doris-be 2>&1 | grep -E '\[keepalive\]' | tail -6

echo
echo "############ 6. 初始化结果 ############"
Q -N -B -e "SHOW DATABASES;" 2>&1
echo "--- ecommerce 表 ---"
Q -N -B -e "SHOW TABLES FROM ecommerce;" 2>&1
echo "--- test_connection 内容 ---"
Q -B -e "SELECT * FROM ecommerce.test_connection;" 2>&1

echo
echo "############ 7. 建表 / 写入 / 查询 验证 ############"
Q -e "CREATE TABLE IF NOT EXISTS ecommerce._verify (id BIGINT, v VARCHAR(32)) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1');" 2>&1 | head -2
echo "  create rc=$?"
Q -e "INSERT INTO ecommerce._verify VALUES (1,'ok');" 2>&1 | head -2
echo "  insert rc=$?"
echo "  select:"; Q -N -B -e "SELECT * FROM ecommerce._verify;" 2>&1
Q -e "DROP TABLE IF EXISTS ecommerce._verify;" 2>&1 | head -1

echo
echo "############ 8. 全服务健康 ############"
bash scripts/health-check.sh
echo "exit=$?"
