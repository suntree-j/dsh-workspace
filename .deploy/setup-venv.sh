#!/usr/bin/env bash
# ============================================================
# 在服务器上建立 Python 测试环境
#
# Ubuntu 24.04 的 python3 默认不含 pip（PEP 668），
# 因此先安装 python3-venv / python3-pip，再建 venv 装依赖。
# ============================================================
set -uo pipefail

echo "=== 1. 安装 python3-venv / python3-pip ==="
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq python3-venv python3-pip 2>&1 | tail -3
python3 -m pip --version 2>&1 | head -1

echo
echo "=== 2. 创建 venv ==="
cd /opt/data-platform
[ -d .venv ] || python3 -m venv .venv
VPY=/opt/data-platform/.venv/bin/python
echo "  $($VPY --version)"

echo
echo "=== 3. 安装依赖（清华 PyPI 镜像） ==="
$VPY -m pip install -q --upgrade pip 2>&1 | tail -2
$VPY -m pip install -q \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  "pytest>=8,<10" \
  "python-dotenv>=1,<2" \
  "Faker>=30,<40" \
  "mysql-connector-python>=9,<10" \
  "confluent-kafka>=2,<3" 2>&1 | tail -5

echo
echo "=== 4. 已安装 ==="
$VPY -m pip list 2>/dev/null | grep -iE 'pytest|dotenv|faker|mysql|confluent' || true

echo
echo "=== 5. 冒烟测试前置：单元测试 ==="
$VPY -m pytest -m unit -q 2>&1 | tail -5
