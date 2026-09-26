#!/usr/bin/env bash
# 最终验证：修复 USE 反引号 + 完整测试 + 持久化
set -uo pipefail
cd /opt/data-platform

echo "############ 1. 整个容器生命周期只执行一次初始化 ############"
echo "（当前 BE 未重建，直接验证现有数据）"
Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=20 "$@"; }
Q -B -e "SELECT * FROM ecommerce.test_connection;" 2>&1

echo
echo "############ 2. 验证幂等：重复执行初始化目录 ############"
echo "--- 再跑一次 seed（应跳过插入） ---"
docker exec doris-be bash -c 'FE_HOST=172.28.0.10 bash /docker-entrypoint-initdb.d/03_seed_test_connection.sh' 2>&1
echo "--- 行数（应仍为 1） ---"
Q -N -B -e "SELECT COUNT(*) FROM ecommerce.test_connection;" 2>&1

echo
echo "############ 3. 全服务健康 ############"
bash scripts/health-check.sh
echo "exit=$?"

echo
echo "############ 4. 完整测试 ############"
.venv/bin/python -m pytest -q 2>&1 | tail -8

echo
echo "############ 5. 数据完整性 ############"
PW=$(grep '^MYSQL_ROOT_PASSWORD=' .env | cut -d= -f2-)
docker exec mysql mysql -uroot -p"$PW" -N -B -e "
  SELECT CONCAT('mysql: user=',(SELECT COUNT(*) FROM ecommerce.user),
    ' product=',(SELECT COUNT(*) FROM ecommerce.product),
    ' orders=',(SELECT COUNT(*) FROM ecommerce.orders),
    ' payment=',(SELECT COUNT(*) FROM ecommerce.payment),
    ' refund=',(SELECT COUNT(*) FROM ecommerce.refund));" 2>/dev/null
Q -N -B -e "SELECT CONCAT('doris: test_connection=', COUNT(*)) FROM ecommerce.test_connection;" 2>/dev/null
T=0
for t in order_event payment_event refund_event behavior_event; do
  n=$(docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server kafka:9092 --topic "$t" 2>/dev/null | awk -F: '{s+=$3} END {print s+0}')
  T=$((T+n))
done
echo "kafka: ${T} 条事件"

echo
echo "############ 6. 容器稳定性 ############"
for c in mysql kafka minio doris-fe doris-be; do
  printf '  %-10s RestartCount=%s Health=%s\n' "$c" \
    "$(docker inspect $c --format '{{.RestartCount}}' 2>/dev/null)" \
    "$(docker inspect $c --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null)"
done

echo
echo "############ 7. 资源 ############"
df -h / | tail -1
free -h | head -2
swapon --show 2>/dev/null || echo "  swap: 已禁用（Doris 要求）"
