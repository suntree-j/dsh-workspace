#!/usr/bin/env bash
set -uo pipefail
cd /opt/data-platform

install -m 0644 /tmp/test_infrastructure.py tests/smoke/test_infrastructure.py

echo "=== 运行冒烟测试 ==="
.venv/bin/python -m pytest -m smoke -v 2>&1 | tail -45
