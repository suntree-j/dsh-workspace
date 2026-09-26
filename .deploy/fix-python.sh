#!/usr/bin/env bash
set -uo pipefail

echo "=== 镜像现状 ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'

echo
echo "=== Doris 拉取进度 ==="
tail -3 /root/doris-pull.log 2>/dev/null || echo "(无日志)"
ps aux | grep -c '[d]ocker pull' || true

echo
echo "=== 尝试安装 python3-venv / pip ==="
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq python3-venv python3-pip 2>&1 | tail -5
echo "apt exit=$?"

echo
echo "=== 安装后的 python 工具 ==="
python3 -m pip --version 2>&1 | head -1 || echo "pip 仍不可用"
python3 -m venv --help >/dev/null 2>&1 && echo "venv: 可用" || echo "venv: 不可用"
