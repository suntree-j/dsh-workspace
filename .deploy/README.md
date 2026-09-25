# 部署脚本（Deployment）

腾讯云服务器上的 Sprint 0 环境部署脚本。

- 服务器：`36.151.150.140`（腾讯云轻量应用服务器，Ubuntu 24.04.2 LTS，4 核 / 16 GB / 100 GB SSD）
- 登录：`ssh -i ~/.ssh/suntree.pem root@36.151.150.140`

---

## 1. 为什么需要这些脚本

该服务器位于中国大陆网络环境，与本地开发机存在两个关键差异：

| 目标 | 服务器上的实际情况 | 处理方式 |
| --- | --- | --- |
| 安装 Docker | `get.docker.com` 被重置（Connection reset） | 改用清华镜像的 `docker-ce` apt 源 |
| 拉取镜像 | `registry-1.docker.io` / `auth.docker.io` 超时不可达 | 配置国内 registry mirror |

> `download.docker.com`、`github.com`、国内各镜像站均可正常访问。

---

## 2. 脚本说明

| 脚本 | 作用 | 是否需要 root |
| --- | --- | --- |
| `install-docker.sh` | 安装 Docker CE + Compose 插件，写入镜像加速配置，启动服务 | 是 |
| `pull-images.sh` | 拉取 Sprint 0 全部固定版本镜像（约 5 GB） | 否 |

### 2.1 `install-docker.sh`

1. 从清华镜像获取 Docker GPG 公钥（多源回退：清华 → 官方 → 阿里 → 腾讯）
2. 写入 `/etc/apt/sources.list.d/docker.sources`（仅 `noble stable`）
3. 安装 `docker-ce`、`docker-ce-cli`、`containerd.io`、`docker-buildx-plugin`、`docker-compose-plugin`
4. 写入 `/etc/docker/daemon.json`：

```json
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
```

5. `systemctl enable --now docker`

**实测版本**：Docker 29.8.1、Compose v5.5.1、containerd 2.3.5

### 2.2 `pull-images.sh`

拉取以下镜像（版本与 `docker-compose.yml` 完全一致）：

```text
mysql:8.4.11
apache/kafka:4.2.1
coollabsio/minio:RELEASE.2025-10-15T17-29-55Z
apache/doris:fe-4.1.4
apache/doris:be-4.1.4
python:3.13.14-slim-bookworm      # 供 data-generator 构建
```

任一个失败则返回非 0，并打印失败清单。

---

## 3. 使用方法

在**本地**执行（需要 `~/.ssh/suntree.pem`）：

```bash
scp -i ~/.ssh/suntree.pem .deploy/install-docker.sh root@36.151.150.140:/root/
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 "bash /root/install-docker.sh"

scp -i ~/.ssh/suntree.pem .deploy/pull-images.sh root@36.151.150.140:/root/
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 "bash /root/pull-images.sh"
```

> 注意：这些脚本必须保持 **LF 行尾**。仓库根 `.gitattributes` 已强制 `*.sh` 为 LF。

---

## 4. 已知限制

1. **镜像加速站是第三方服务**，可用性会变化。若某天拉取失败：
   - 先 `curl -s -o /dev/null -w "%{http_code}" https://docker.1panel.live/v2/` 检查；
   - 在 `/etc/docker/daemon.json` 中替换或追加 `registry-mirrors`；
   - 然后 `systemctl restart docker`。
2. 服务器**没有配置 SWAP**。若后续 Sprint 同时运行 Flink/Spark，建议增加 swap
   或升级内存规格。
3. 本目录只用于**环境部署**，业务代码与编排在 `intelligent-data-platform/`。
