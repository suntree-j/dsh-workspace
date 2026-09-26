# 部署脚本（Deployment）

腾讯云服务器上的环境部署脚本（Sprint 0 + Sprint 1）。

- 服务器：`36.151.150.140`（腾讯云轻量应用服务器，Ubuntu 24.04.2 LTS，4 核 / 16 GB / 100 GB SSD）
- 登录：`ssh -i ~/.ssh/suntree.pem root@36.151.150.140`
- 应用目录：`/opt/data-platform`

> 本目录只承载「服务器环境准备」相关脚本。
> **功能验收**（健康检查、冒烟测试、Sprint 验收）统一走
> `intelligent-data-platform/scripts/` 下的正式脚本，不在这里重复实现。

---

## 1. 为什么需要这些脚本

该服务器位于中国大陆网络环境，与本地开发机存在两个关键差异：

| 目标 | 服务器上的实际情况 | 处理方式 |
| --- | --- | --- |
| 安装 Docker | `get.docker.com` 被重置（Connection reset） | 改用清华镜像的 `docker-ce` apt 源 |
| 拉取镜像 | `registry-1.docker.io` / `auth.docker.io` 超时不可达 | 配置国内 registry mirror |

> `download.docker.com`、`github.com`、清华/阿里/腾讯镜像站均可正常访问。

**镜像源实测结论**（供后续排查参考）：

```text
docker.1panel.live    可用（MySQL / Kafka / MinIO 拉取成功，但 server 较慢）
docker.m.daocloud.io  可用且较快（Doris FE 拉取成功）
docker.1ms.run        可用但超时
docker.xuanyuan.me    需要付费（提示 free-vs-pro）
其他（nju / rat.dev / dockerhub.icu / amingg / ketches / fast360）均不可用
```

---

## 2. 脚本清单

按执行顺序：

| # | 脚本 | 作用 | 说明 |
| --- | --- | --- | --- |
| 1 | `tune-system.sh` | 内核参数调优 | Doris 要求 `vm.max_map_count >= 2000000`；降低 `vm.swappiness` |
| 2 | `install-docker.sh` | 安装 Docker CE + Compose | 清华源 + 多源回退获取 GPG 公钥，写入镜像加速配置 |
| 3 | `pull-images.sh` | 拉取全部镜像 | 约 15 GB；任一个失败返回非 0 |
| 4 | `gen-env.sh` | 生成 `.env` | 用 `openssl rand` 生成 192-bit 随机口令，权限 600 |
| 5 | `verify-env.sh` | 校验 `.env` | 确认无 `change_me` 占位符、口令长度、compose 配置有效 |
| 6 | `setup-venv.sh` | 建 Python venv | 先装 `python3-venv`/`python3-pip`（PEP 668），再装测试依赖 |
| 7 | `sync-to-server.sh` | 同步项目到服务器 | **本地执行**；tar over ssh，排除 `.venv`/快照/`.env` |
| 8 | `run-smoke.sh` | 运行冒烟测试 | 部署更新后的测试文件并执行 `pytest -m smoke` |
| 9 | `final-acceptance.sh` | 完整验收 + 持久化验证 | config → health-check → pytest → 数据校验 → `down`/`up` 后复查 |

补充：

| 脚本 | 作用 |
| --- | --- |
| `pull-doris-via-proxy.sh` | 通过 SSH 反向隧道代理拉取 Doris 镜像（镜像站过慢时使用） |

### 2.1 Sprint 1 实时链路的部署与验收

Sprint 1（Kafka → Flink → Doris）**不需要新增服务器端部署脚本**，
全部通过仓库内的正式脚本完成：

```bash
# 1) 构建 Flink 镜像（内置 Kafka SQL connector）
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 \
  "cd /opt/data-platform && docker compose build flink-jobmanager"

# 2) 启动（flink-jobs 会自动提交全部 SQL 作业并保活 session）
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 \
  "cd /opt/data-platform && docker compose up -d"

# 3) 验收（就绪检查 + 健康检查 + 冒烟测试 + 与 MySQL 对账）
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 \
  "cd /opt/data-platform && bash scripts/verify-sprint-1.sh"

# 4) 需要重放数据时（会重建 12 个 topic 并清空下游派生数据）
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 \
  "cd /opt/data-platform && bash scripts/verify-sprint-1.sh --replay"
```

> 排查期间一次性使用的诊断脚本（`diag-*` / `sprint1-*` / `check-*` 等）
> 已在 Sprint 1 验收后清理，避免仓库里堆积调试残留。
> 可复用的检查逻辑已固化进 `scripts/health-check.sh`（Sprint 1 六项）
> 与 `scripts/verify-sprint-1.sh`。

### 2.2 `install-docker.sh` 写入的镜像配置

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

**实测版本**：Docker 29.8.1、Compose v5.5.1、containerd 2.3.5

### 2.2 拉取 Doris 镜像的推荐方式

镜像站拉取 Doris（FE 1.6 GB / BE 2.9 GB）实测仅约 1.5 MB/s，耗时 30 分钟以上。
**推荐用 SSH 反向隧道共享本地代理**，实测 FE 仅 12 秒完成：

```bash
# 本地：把本地 7890 代理暴露到服务器
ssh -i ~/.ssh/suntree.pem -N -R 127.0.0.1:7890:127.0.0.1:7890 root@36.151.150.140

# 服务器：执行（脚本会自动写入 docker 代理配置并拉取）
bash pull-doris-via-proxy.sh
```

> ⚠️ **代理用完务必清理**：该脚本会写入
> `/etc/systemd/system/docker.service.d/http-proxy.conf` 让 docker 走隧道。
> 隧道断开后若不清除配置，**后续所有镜像拉取都会失败**（因为代理指向已断开的隧道）。
>
> 清理方法（服务器上执行）：
>
> ```bash
> rm -f /etc/systemd/system/docker.service.d/http-proxy.conf
> systemctl daemon-reload && systemctl restart docker
> docker info --format '{{.RegistryConfig.Mirrors}}'   # 应只剩镜像站
> ```
>
> 服务器当前状态：**已清理代理配置，仅依赖 registry mirror**，
> 不再依赖本地隧道（已实测服务与数据在 daemon 重启后完全正常）。

---

## 3. 使用方法

### 3.1 从零部署

在**本地**执行（需要 `~/.ssh/suntree.pem`）：

```bash
cd dsh-workspace

# 1) 同步项目代码
bash .deploy/sync-to-server.sh ~/.ssh/suntree.pem root@36.151.150.140

# 2) 依次执行服务器端脚本
for s in tune-system install-docker pull-images gen-env verify-env setup-venv; do
  scp -i ~/.ssh/suntree.pem .deploy/$s.sh root@36.151.150.140:/root/
  ssh -i ~/.ssh/suntree.pem root@36.151.150.140 "bash /root/$s.sh"
done

# 3) 启动并验收
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 \
  "cd /opt/data-platform && docker compose up -d && bash scripts/health-check.sh"
```

### 3.2 日常更新代码后

```bash
bash .deploy/sync-to-server.sh ~/.ssh/suntree.pem root@36.151.150.140
ssh -i ~/.ssh/suntree.pem root@36.151.150.140 \
  "cd /opt/data-platform && docker compose up -d --force-recreate minio-init kafka-init"
```

> ⚠️ 注意：`sync-to-server.sh` **不会**传输 `.env`（服务器上有独立生成的 `.env`）。

---

## 4. 已知限制与注意事项

1. **镜像加速站是第三方服务**，可用性会变化。拉取失败时：
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://docker.1panel.live/v2/
   # 修改 /etc/docker/daemon.json 的 registry-mirrors 后：
   systemctl restart docker
   ```
2. **Doris 镜像较大**（FE 1.6 GB / BE 2.9 GB），在服务器上通过镜像站拉取
   实测速率约 1.5 MB/s，需要较长时间；建议耐心等待或改用代理。
3. 服务器**不能配置 SWAP**（实测结论）：Doris BE 的 `start_be.sh` 检测到 swap
   会直接打印 `Disable swap memory before starting be` 并拒绝启动；
   Sprint 0 期间加过 8 GB swap，直接导致 BE 起不来、tablet 副本损坏。
   内存控制改用「限制各组件上限」：Doris FE `-Xmx1536m`、Flink TaskManager 3072m。
   实测 Sprint 0 + Sprint 1 全量运行后可用内存约 3.2 GB。
   **后续 Sprint 若内存不足，只能升级规格，不要加 swap。**
4. **本地 `.ssh/config` 的 BOM 问题**：该文件曾带 UTF-8 BOM，
   导致 Git Bash 的 OpenSSH 报
   `Bad configuration option: \357\273\277host`。
   已备份为 `config.bom-backup-*` 并移除 BOM。
   Windows 自带 OpenSSH 容忍 BOM，所以此前一直未暴露。
5. 本目录只用于**环境部署**，业务代码与编排在 `intelligent-data-platform/`。
