#!/usr/bin/env bash
# ============================================================
# 通过本地反向隧道代理拉取 Doris 镜像
#
# 背景：服务器直连 Docker Hub 不可达，镜像站速率仅约 1.5 MB/s，
#       Doris BE 镜像 2.9 GB 需要 30+ 分钟。
#       本地开发机通过代理可快速拉取，因此用 SSH 反向隧道
#       (-R 127.0.0.1:7890:127.0.0.1:7890) 把本地代理共享给服务器。
#
# 注意：docker 拉取走 HTTP(S)_PROXY 环境变量；
#       必须把内部地址放入 NO_PROXY，否则 registry mirror 也会走代理。
# ============================================================
set -uo pipefail

echo "=== 1. 隧道是否可用 ==="
if curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 -x http://127.0.0.1:7890 https://registry-1.docker.io/v2/ | grep -qE '200|401'; then
  echo "  隧道可用（通过代理可访问 Docker Hub）"
else
  echo "  隧道不可用，退出" >&2
  exit 1
fi

echo
echo "=== 2. 清理旧的慢速 pull ==="
pkill -f 'docker.m.daocloud.io/apache/doris' 2>/dev/null && echo "  已清理" || echo "  无需清理"
pkill -f 'docker pull apache/doris' 2>/dev/null || true

echo
echo "=== 3. 配置 docker 走代理 ==="
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1,minio,mysql,kafka,doris-fe,doris-be,172.16.0.0/12,10.0.0.0/8"
EOF
systemctl daemon-reload
systemctl restart docker
sleep 6
systemctl is-active docker
docker info --format 'Server: {{.ServerVersion}}' 2>&1

echo
echo "=== 4. 拉取 Doris 镜像（经代理） ==="
for img in apache/doris:fe-4.1.4 apache/doris:be-4.1.4; do
  echo "---------------------------------------------"
  echo ">>> $img"
  start=$(date +%s)
  if docker pull "$img" 2>&1 | tail -3; then
    echo "    OK ($(( $(date +%s) - start ))s)"
  else
    echo "    FAILED: $img"
  fi
done

echo
echo "=== 5. 镜像列表 ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'
