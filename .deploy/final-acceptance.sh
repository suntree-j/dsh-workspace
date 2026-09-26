#!/usr/bin/env bash
# Sprint 0 最终验收 + 持久化验证
set -uo pipefail
cd /opt/data-platform

echo "############ 1. compose config ############"
docker compose config --quiet && echo "OK"

echo
echo "############ 2. health-check.sh ############"
bash scripts/health-check.sh; echo "exit=$?"

echo
echo "############ 3. pytest 全部 ############"
.venv/bin/python -m pytest -q 2>&1 | tail -12

echo
echo "############ 4. 服务状态 ############"
docker compose ps --all

echo
echo "############ 5. 数据现状（持久化前） ############"
PW=$(grep '^MYSQL_ROOT_PASSWORD=' .env | cut -d= -f2-)
docker exec mysql mysql -uroot -p"$PW" -N -B -e "
  SELECT CONCAT('user=',(SELECT COUNT(*) FROM ecommerce.user),
                ' product=',(SELECT COUNT(*) FROM ecommerce.product),
                ' orders=',(SELECT COUNT(*) FROM ecommerce.orders),
                ' payment=',(SELECT COUNT(*) FROM ecommerce.payment),
                ' refund=',(SELECT COUNT(*) FROM ecommerce.refund));" 2>/dev/null
docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot -N -B \
  -e "SELECT CONCAT('doris.test_connection rows=', COUNT(*)) FROM ecommerce.test_connection;" 2>/dev/null

echo
echo "############ 6. 持久化验证：down 后 up ############"
docker compose down 2>&1 | tail -3
echo "--- down 完成，重新 up ---"
docker compose up -d 2>&1 | tail -4

echo "--- 等待就绪（最多 240s） ---"
deadline=$(( $(date +%s) + 240 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if bash scripts/health-check.sh >/dev/null 2>&1; then break; fi
  printf '.'; sleep 10
done
echo

echo "############ 7. 持久化结果 ############"
bash scripts/health-check.sh; echo "health exit=$?"
echo "--- 数据是否保留 ---"
docker exec mysql mysql -uroot -p"$PW" -N -B -e "
  SELECT CONCAT('user=',(SELECT COUNT(*) FROM ecommerce.user),
                ' product=',(SELECT COUNT(*) FROM ecommerce.product),
                ' orders=',(SELECT COUNT(*) FROM ecommerce.orders),
                ' payment=',(SELECT COUNT(*) FROM ecommerce.payment),
                ' refund=',(SELECT COUNT(*) FROM ecommerce.refund));" 2>/dev/null
docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot -N -B \
  -e "SELECT CONCAT('doris.test_connection rows=', COUNT(*)) FROM ecommerce.test_connection;" 2>/dev/null
echo "--- Kafka Topic 消息数（应保留） ---"
for t in order_event payment_event refund_event behavior_event; do
  n=$(docker exec kafka /opt/kafka/bin/kafka-run-class.sh kafka.tools.GetOffsetShell \
      --bootstrap-server kafka:9092 --topic "$t" 2>/dev/null | awk -F: '{s+=$3} END {print s+0}')
  printf '  %-16s %s\n' "$t" "$n"
done
echo "--- MinIO bucket（应保留） ---"
U=$(grep '^MINIO_ROOT_USER=' .env | cut -d= -f2-)
P=$(grep '^MINIO_ROOT_PASSWORD=' .env | cut -d= -f2-)
docker exec minio sh -c "MC_HOST_local='http://$U:$P@localhost:9000'; export MC_HOST_local; mc ls local" 2>&1

echo
echo "############ 8. 重启计数（稳定性） ############"
for c in mysql kafka minio doris-fe doris-be; do
  printf '  %-10s RestartCount=%s Health=%s\n' "$c" \
    "$(docker inspect $c --format '{{.RestartCount}}' 2>/dev/null)" \
    "$(docker inspect $c --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null)"
done
