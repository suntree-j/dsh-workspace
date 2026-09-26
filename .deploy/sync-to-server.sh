#!/usr/bin/env bash
# ============================================================
# 将 intelligent-data-platform 同步到服务器
# 由本地以如下方式调用（Windows 环境下避免 CRLF 问题）：
#   bash .deploy/sync-to-server.sh <ssh-key> <host>
# ============================================================
set -euo pipefail

KEY="${1:?用法: sync-to-server.sh <ssh-key> <host>}"
HOST="${2:?用法: sync-to-server.sh <ssh-key> <host>}"

SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/intelligent-data-platform"
[ -d "$SRC" ] || { echo "找不到源目录: $SRC" >&2; exit 1; }

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o BatchMode=yes $HOST"

echo "=== 准备远端目录 ==="
$SSH "mkdir -p /opt/data-platform" 

echo "=== 打包（排除本地运行产物）并传输 ==="
tar -czf - \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='dataset_snapshot.json' \
  --exclude='.env' \
  --exclude='.git' \
  -C "$(dirname "$SRC")" "$(basename "$SRC")" \
| $SSH "tar -xzf - -C /opt/data-platform --strip-components=1"

echo "=== 远端文件清单 ==="
$SSH "find /opt/data-platform -type f | sort | sed 's|/opt/data-platform/||'"
echo "=== 远端脚本行尾检查（应全部 LF） ==="
$SSH "for f in \$(find /opt/data-platform -name '*.sh'); do
        if grep -q \$'\r' \"\$f\"; then echo \"CRLF: \$f\"; fi
      done; echo '行尾检查完成'"
echo "=== 同步完成 ==="
