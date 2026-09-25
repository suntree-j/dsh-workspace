#!/usr/bin/env bash
# ============================================================
# 在 Ubuntu 24.04 (noble) 上安装 Docker CE + Compose 插件
#
# 背景：get.docker.com 在本机网络下不可达（Connection reset），
#       但 download.docker.com 与国内镜像可达，因此手工配置 apt 源。
# ============================================================
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

KEYRING=/etc/apt/keyrings/docker.asc
mkdir -p /etc/apt/keyrings
chmod 0755 /etc/apt/keyrings
rm -f "$KEYRING"

echo "=== 1. 获取 Docker GPG 公钥（多源回退） ==="
GOT=0
for url in \
  "https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg" \
  "https://download.docker.com/linux/ubuntu/gpg" \
  "https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg" \
  "https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu/gpg"
do
  if curl -fsSL --max-time 25 "$url" -o "$KEYRING" && [ -s "$KEYRING" ]; then
    echo "  OK: $url"
    GOT=1
    break
  fi
  echo "  FAIL: $url"
done
if [ "$GOT" -ne 1 ]; then
  echo "ERROR: 无法获取 Docker GPG 公钥" >&2
  exit 1
fi
chmod 0644 "$KEYRING"
echo "  key fingerprint check:"
gpg --show-keys --with-fingerprint "$KEYRING" 2>/dev/null | head -4 || true

echo "=== 2. 写入 apt 源（清华镜像，仅 noble stable） ==="
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu
Suites: noble
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
cat /etc/apt/sources.list.d/docker.sources

echo "=== 3. apt-get update ==="
apt-get update -qq

echo "=== 4. 可安装版本 ==="
apt-cache madison docker-ce 2>/dev/null | head -5 || true

echo "=== 5. 安装 docker-ce / cli / containerd / buildx / compose ==="
apt-get install -y -qq \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "=== 6. 版本确认 ==="
docker --version
docker compose version
containerd --version

echo "=== 7. 配置镜像加速（Docker Hub 直连不可达） ==="
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1panel.live",
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me"
  ],
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" },
  "live-restore": true
}
EOF
cat /etc/docker/daemon.json

echo "=== 8. 启动 Docker 并设为开机自启 ==="
systemctl enable docker >/dev/null 2>&1 || true
systemctl restart docker
sleep 6
systemctl is-active docker
docker info --format 'Server Version: {{.ServerVersion}} | Storage: {{.Driver}} | RegistryMirrors: {{.RegistryConfig.Mirrors}}'
echo "=== 安装完成 ==="
