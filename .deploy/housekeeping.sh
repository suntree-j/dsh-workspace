#!/usr/bin/env bash
# ============================================================
# 服务器收尾：清理遗留 backend + 添加 SWAP
# ============================================================
set -uo pipefail
cd /opt/data-platform

Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=15 "$@"; }

echo "############ 1. 清理前：BACKENDS 列表 ############"
Q -N -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' '{printf "  id=%s host=%s alive=%s tablets=%s lastHeartbeat=%s\n", $1,$2,$9,$11,$8}'

echo
echo "############ 2. 清理遗留 backend ############"
# 排查期间临时注册过的探测容器地址
# 注意：Doris 官方禁止使用 DROP BACKEND，要求改用 DROPP
for addr in "172.28.0.12:9050"; do
  echo "--- 尝试 DROPP BACKEND ${addr} ---"
  Q -e "ALTER SYSTEM DROPP BACKEND \"${addr}\";" 2>&1 | head -3
done

echo
echo "############ 3. 清理后：BACKENDS 列表 ############"
Q -N -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' '{printf "  id=%s host=%s alive=%s lastHeartbeat=%s\n", $1,$2,$9,$8}'

echo
echo "############ 4. 添加 SWAP ############"
if swapon --show | grep -q .; then
  echo "  已存在 SWAP："; swapon --show
else
  echo "  当前无 SWAP，开始创建 8G /swapfile ..."
  # 使用 fallocate（快）；若文件系统不支持则回退 dd
  if ! fallocate -l 8G /swapfile 2>/dev/null; then
    echo "  fallocate 不可用，改用 dd ..."
    dd if=/dev/zero of=/swapfile bs=1M count=8192 status=none
  fi
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  # 持久化
  if ! grep -q '^/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  echo "  SWAP 创建完成"
fi
echo
echo "--- swapon --show ---"
swapon --show
echo "--- /etc/fstab 中的 swap ---"
grep -i swap /etc/fstab || echo "  (无)"
echo "--- 内存总览 ---"
free -h

echo
echo "############ 5. 调整 swappiness ############"
# 已由 99-data-platform.conf 设为 10；此处确认
sysctl -n vm.swappiness

echo
echo "############ 6. 服务健康 ############"
bash scripts/health-check.sh
echo "health exit=$?"

echo
echo "############ 7. 数据完整性 ############"
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
echo "############ 8. 磁盘 ############"
df -h / | tail -1
