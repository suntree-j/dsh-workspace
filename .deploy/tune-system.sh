#!/usr/bin/env bash
# ============================================================
# 应用 Doris 必需的宿主机内核参数
#
# 依据 Apache Doris 官方部署要求：
#   vm.max_map_count >= 2000000
#
# 说明：容器内的 ulimit nofile 通过 docker-compose.yml 的
#       ulimits 配置提升，此处只处理宿主机 sysctl。
# ============================================================
set -euo pipefail

CONF=/etc/sysctl.d/99-data-platform.conf

echo "=== 调整前 ==="
printf '  vm.max_map_count = %s\n' "$(sysctl -n vm.max_map_count)"
printf '  vm.swappiness    = %s\n' "$(sysctl -n vm.swappiness)"

cat > "$CONF" <<'EOF'
# 由 data-platform 部署脚本写入
# Apache Doris 要求 vm.max_map_count >= 2000000
vm.max_map_count = 2000000

# 降低交换倾向：数据库/OLAP 场景应优先使用物理内存
vm.swappiness = 10
EOF

echo
echo "=== 写入 $CONF ==="
cat "$CONF"

echo
echo "=== 应用 ==="
sysctl -p "$CONF"

echo
echo "=== 调整后 ==="
printf '  vm.max_map_count = %s\n' "$(sysctl -n vm.max_map_count)"
printf '  vm.swappiness    = %s\n' "$(sysctl -n vm.swappiness)"

echo
mmc=$(sysctl -n vm.max_map_count)
if [ "$mmc" -ge 2000000 ]; then
  echo "OK: vm.max_map_count 满足 Doris 要求"
else
  echo "FAIL: vm.max_map_count 仍偏低" >&2
  exit 1
fi
