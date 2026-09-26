#!/usr/bin/env bash
# ============================================================
# 修正：移除 SWAP（Doris BE 要求禁用 swap 才启动）
# 并彻底重置 Doris 存储，重建干净集群
#
# 背景（实测）：
#   Doris 的 start_be.sh 在检测到系统启用了 swap 时会直接拒绝启动：
#     "Disable swap memory before starting be"
#   这正是加入 8G swap 后 BE 无法启动、tablet 变为坏副本的原因。
#
#   取舍：Doris BE 是本项目的核心依赖，且 Doris 官方明确要求关闭 swap，
#   因此这里移除 swap，改用 Doris 自身的低内存配置控制内存占用，
#   并保留后续「加内存/升配」这一正确路径。
# ============================================================
set -uo pipefail
cd /opt/data-platform

echo "############ 1. 移除 SWAP ############"
if swapon --show | grep -q .; then
  swapoff /swapfile 2>/dev/null || swapoff -a
  echo "  已关闭 swap"
fi
if [ -f /swapfile ]; then
  rm -f /swapfile
  echo "  已删除 /swapfile，释放 8G"
fi
# 从 fstab 移除
if grep -q '^/swapfile' /etc/fstab; then
  sed -i '/^\/swapfile/d' /etc/fstab
  echo "  已从 /etc/fstab 移除"
fi
# 记录约束，避免后续再误加
cat > /etc/sysctl.d/99-data-platform.conf <<'EOF'
# 由 data-platform 部署脚本写入
# Apache Doris 要求 vm.max_map_count >= 2000000
vm.max_map_count = 2000000

# 降低交换倾向：数据库/OLAP 场景应优先使用物理内存
vm.swappiness = 10

# !! 重要：不要为本机启用 swap !!
# Doris 的 start_be.sh 在检测到 swap 已启用时会拒绝启动：
#   "Disable swap memory before starting be"
# 因此本机必须保持 swap 关闭。
EOF
sysctl --system >/dev/null 2>&1 || true
echo
echo "--- 当前 swap ---"
swapon --show || echo "  (无 swap)"
free -h | tail -2

echo
echo "############ 2. 停止并彻底删除 Doris 容器与存储卷 ############"
docker compose rm -sf doris-be doris-fe 2>&1 | tail -3
docker volume rm data-platform-doris-be-storage data-platform-doris-be-log \
                data-platform-doris-fe-meta data-platform-doris-fe-log 2>&1 | tail -4

echo
echo "############ 3. 重建 Doris ############"
docker compose up -d doris-fe 2>&1 | tail -2
for i in $(seq 1 48); do
  [ "$(docker inspect doris-fe --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 5
done
echo "  FE health=$(docker inspect doris-fe --format '{{.State.Health.Status}}')"

docker compose up -d doris-be 2>&1 | tail -2
for i in $(seq 1 60); do
  [ "$(docker inspect doris-be --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 5
done
echo "  BE health=$(docker inspect doris-be --format '{{.State.Health.Status}}')"

echo
echo "############ 4. 等待注册稳定（60s） ############"
sleep 60
Q() { docker exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot --connect-timeout=20 "$@"; }
Q -N -B -e "SHOW BACKENDS;" 2>/dev/null | awk -F'\t' '{printf "  host=%-16s id=%s alive=%s tabletNum=%s\n", $2,$1,$9,$11}'

echo
echo "############ 5. BE 是否真的在监听 ############"
docker exec doris-be bash -c 'ss -ltn 2>/dev/null | grep -E "8040|9050|9060" || echo "  未监听!"'

echo
echo "############ 6. 初始化结果 ############"
echo "--- 数据库 ---"; Q -N -B -e "SHOW DATABASES;" 2>&1
echo "--- ecommerce 表 ---"; Q -N -B -e "SHOW TABLES FROM ecommerce;" 2>&1
echo "--- test_connection ---"; Q -B -e "SELECT * FROM ecommerce.test_connection;" 2>&1

echo
echo "############ 7. 建表/写入/查询 ############"
Q -e "CREATE TABLE IF NOT EXISTS ecommerce._verify (id BIGINT, v VARCHAR(32)) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1');" 2>&1 | head -2
echo "  create rc=$?"
Q -e "INSERT INTO ecommerce._verify VALUES (1,'ok');" 2>&1 | head -2
echo "  insert rc=$?"
echo -n "  select -> "; Q -N -B -e "SELECT * FROM ecommerce._verify;" 2>&1
Q -e "DROP TABLE IF EXISTS ecommerce._verify;" 2>&1 | head -1

echo
echo "############ 8. 全服务健康 ############"
bash scripts/health-check.sh
echo "exit=$?"
