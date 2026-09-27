# 开发环境说明（Development Environment）

> 文档版本：V1.3
> 更新日期：2026-09-27
> 适用 Sprint：Sprint 0 起（各 Sprint 增量补充）
>
> 本文档记录**实际使用的运行环境、镜像版本、端口分配与版本选型依据**。
> 原则：**不使用 `latest`，不猜测版本**。所有版本均来自官方仓库实际查证，
> 并记录镜像 digest 以便复现。

---

## 0. 运行环境（Sprint 0 验收环境）

Sprint 0 的完整验收在**腾讯云服务器**上完成。

| 项目 | 值 |
| --- | --- |
| 实例 | 腾讯云轻量应用服务器 `lavm-txyjj7xzr7` |
| 规格 | 4 核 / 16 GB / 100 GB 通用型 SSD |
| 操作系统 | Ubuntu 24.04.2 LTS（内核 6.8.0-53-generic） |
| CPU | Intel Xeon Gold 6148（支持 AVX2 / AVX512F） |
| 公网 IP | `36.151.150.140` |
| 内网 IP | `172.16.0.10` |
| 应用目录 | `/opt/data-platform` |
| 登录 | `ssh -i ~/.ssh/suntree.pem root@36.151.150.140` |

容器运行时（实测版本）：

| 项目 | 版本 |
| --- | --- |
| Docker Engine | 29.8.1 |
| Docker Compose | v5.5.1 |
| containerd | 2.3.5 |
| Python（宿主，仅用于 pytest） | 3.12.3（venv 在 `/opt/data-platform/.venv`） |

### 0.1 服务器网络特殊性（重要）

该服务器位于中国大陆网络环境，与本地开发机有两个关键差异：

| 目标 | 服务器实测 | 处理方式 |
| --- | --- | --- |
| 安装 Docker | `get.docker.com` 被重置（Connection reset） | 改用清华镜像的 `docker-ce` apt 源 |
| 拉取镜像 | `registry-1.docker.io` / `auth.docker.io` 超时不可达 | 配置国内 registry mirror |

**镜像源实测结论**：

```text
docker.1panel.live     可用（MySQL / Kafka / MinIO 拉取成功）
docker.m.daocloud.io   可用且较快（Doris FE 拉取成功）
docker.1ms.run         可用但超时
docker.xuanyuan.me     需付费（提示 free-vs-pro）
其余（nju / rat.dev / dockerhub.icu / amingg / ketches / fast360）不可用
```

**加速手段**：SSH 反向隧道共享本地代理可显著提速——
隧道建立后 Doris FE（1.6 GB）仅 12 秒拉取完成，而经镜像站需 30 分钟以上。

```bash
# 本地执行，把本地代理暴露给服务器
ssh -i ~/.ssh/suntree.pem -N -R 127.0.0.1:7890:127.0.0.1:7890 root@36.151.150.140
# 服务器上让 docker 走代理
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1,minio,mysql,kafka,doris-fe,doris-be,172.16.0.0/12,10.0.0.0/8"
EOF
systemctl daemon-reload && systemctl restart docker
```

### 0.2 内核参数与内存策略

Apache Doris 要求 `vm.max_map_count >= 2000000`。服务器初始值为 1048576，
已通过 `/etc/sysctl.d/99-data-platform.conf` 持久化调整：

```ini
vm.max_map_count = 2000000
vm.swappiness = 10
```

> ⚠️ **本机必须保持 swap 关闭**（实测结论，不是偏好）：
> Doris BE 的 `start_be.sh` 启动时会检查 swap，一旦发现启用就直接打印
> `Disable swap memory before starting be` 并拒绝启动。
> Sprint 0 曾按常规建议加过 8 GB swap，**直接导致 BE 起不来**，
> 进而引发 tablet 副本损坏，最终只能重建 BE 存储卷。
> 因此本项目**不加 swap**，改为「限制各组件内存上限」的方式控制内存：

| 组件 | 内存上限 | 设置位置 |
| --- | --- | --- |
| Doris FE JVM | `-Xmx1536m -Xms768m -XX:MaxMetaspaceSize=384m` | `.env` 的 `DORIS_FE_JAVA_OPTS` |
| Doris BE | `BE_MEM_LIMIT`（默认 80%） | `.env` |
| Flink JobManager | `jobmanager.memory.process.size=1024m` | `infrastructure/flink/conf/config.yaml` |
| Flink TaskManager | `taskmanager.memory.process.size=3072m` | 同上（可用 `FLINK_TASKMANAGER_MEMORY` 覆盖） |

实测效果：限制 FE 堆之前 FE 常驻 5.93 GB、系统可用内存仅 4.9 GB；
限制后可用内存回到 9.9 GB。

---

## 1. 本地开发机环境（实测）

项目同时在本地 Windows 开发机上维护源码。

| 项目 | 实测值 |
| --- | --- |
| 操作系统 | Microsoft Windows 11 Home China（Build 26200） |
| CPU / 内存 | 16 逻辑核心 / 15.7 GB |
| Git | 2.54.0.windows.1 |
| Python | 3.13.14 |
| Bash | Git for Windows（`D:\Program Files\Git\bin\bash.exe`） |

> 本地 Docker Desktop 4.91.0 已安装，但启用 WSL2 组件需要重启系统，
> 因此**容器验证统一在服务器上执行**。

### 1.1 本地已知问题

| 问题 | 说明 |
| --- | --- |
| `~/.ssh/config` 带 UTF-8 BOM | Git Bash 的 OpenSSH 报 `Bad configuration option: \357\273\277host`；Windows 自带 OpenSSH 容忍 BOM 所以此前未暴露。已备份为 `config.bom-backup-*` 并移除 BOM |
| 全局 `core.autocrlf=true` | 会导致 Shell 脚本被检出为 CRLF；已由仓库 `.gitattributes` 强制 `*.sh`/`*.sql`/`*.yml` 为 LF |

### 1.2 本地 Docker Desktop（安装记录）

本地 Docker Desktop 通过 **winget 安装成功**：

```powershell
winget install --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
```

注意事项（实测）：

- `--scope user` **不受支持**（Docker 的 EXE 安装包只提供 machine scope），
  使用时会返回 `0x8A150010 找不到适用的安装程序`。
- 安装需要**管理员提权（UAC）**。
- 安装完成后 `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending`
  被置位，**必须重启系统**，WSL2 / VirtualMachinePlatform 组件才会生效。
- Docker Desktop **未加入系统 PATH**，需要手动补充：
  `C:\Program Files\Docker\Docker\resources\bin`

| 项目 | 实测值 |
| --- | --- |
| Docker Desktop 版本 | 4.91.0 |
| Docker Client 版本 | 29.8.0（API 1.56） |
| Docker Compose 版本 | v5.5.1 |
| Docker Engine | 重启前不可用（daemon 未运行） |

> ⚠️ **重启前 daemon 不可用**：
> `docker info` 会报
> `failed to connect to the docker API at npipe:////./pipe/docker_engine`。
> 这是预期行为，不是配置错误。
>
> **Sprint 0 的容器验收已在腾讯云服务器上完成**（见第 0 节），
> 不依赖本地 daemon。

#### 磁盘空间建议

| 盘符 | 总量 | 可用 |
| --- | --- | --- |
| C: | 145.4 GB | 38.7 GB |
| **D:** | **329.1 GB** | **296.3 GB** |

Doris BE 镜像约 9.3 GB、FE 约 4.3 GB，全部镜像合计约 15 GB；
加上运行数据，**建议把镜像存储位置改到 D 盘**：

```text
Docker Desktop → Settings → Resources → Disk image location → D:\DockerData
```

---

## 2. 端口分配

所有端口均通过 `.env` 可覆盖。默认值如下。

| 服务 | 容器内端口 | 宿主映射 | 变量 | 用途 |
| --- | --- | --- | --- | --- |
| MySQL | 3306 | 3306 | `MYSQL_HOST_PORT` | MySQL 协议 |
| Kafka | 9092 | 9092 | `KAFKA_PORT` | Kafka broker（同时作为对外 advertised listener） |
| MinIO | 9000 | 9000 | `MINIO_API_HOST_PORT` | S3 API |
| MinIO | 9001 | 9001 | `MINIO_CONSOLE_HOST_PORT` | Web Console |
| Doris FE | 8030 | 8030 | `DORIS_FE_HTTP_PORT` | FE Web UI |
| Doris FE | 9030 | 9030 | `DORIS_FE_QUERY_PORT` | MySQL 协议（查询入口） |
| Doris FE | 9010 | — | — | FE edit_log / 内部通信（仅集群内） |
| Doris BE | 8040 | 8040 | `DORIS_BE_HTTP_PORT` | BE Web UI / 心跳 |
| Doris BE | 9050 | — | — | BE 心跳与注册端口（仅集群内） |
| Doris BE | 9060 | — | — | BE brpc 端口（仅集群内） |
| Flink JobManager | 8081 | 8081 | `FLINK_REST_PORT` | Flink Web UI / REST API |
| Flink JobManager | 6123 | — | — | 内部 RPC（Pekko/Akka，仅集群内） |
| Flink SQL Gateway | 8083 | 8083 | `FLINK_SQL_GATEWAY_PORT` | SQL Gateway REST（作业提交入口） |
| Prometheus | 9090 | **127.0.0.1:9090** | `PROMETHEUS_HOST_PORT` | 指标抓取与查询 API（**只绑回环**，公网走 Nginx 的 `/metrics/`） |
| Grafana | 3000 | **127.0.0.1:3001** | `GRAFANA_HOST_PORT` | 可视化面板（**只绑回环**，公网走 Nginx 的 `/grafana/`） |

Sprint 0 启动前已实测以上宿主端口**全部空闲**。

---

## 3. 镜像版本矩阵（已查证）

所有容器镜像均加入统一网络 `data-platform`。

| 服务 | 镜像 | 版本 / Tag | 多架构 digest | 架构 |
| --- | --- | --- | --- | --- |
| MySQL | `mysql` | `8.4.11` | `sha256:0744ee5ef89ce6ccfa13de3e579fe6b9e27f93dd70da9c06d2c908b1b193fb8d` | amd64, arm64 |
| Kafka | `apache/kafka` | `4.2.1` | `sha256:9916d60eca5d599550e2c320230808fda342124ba550bb4ac4ea8591803262a0` | amd64, arm64 |
| MinIO | `coollabsio/minio` | `RELEASE.2025-10-15T17-29-55Z` | `sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9` | amd64, arm64 |
| Doris FE | `apache/doris` | `fe-4.1.4` | `sha256:3cbfc9b6293057a0b7f10c2e597f20f4a6bafed0729dfa9795db21eae2e136bb` | amd64, arm64 |
| Doris BE | `apache/doris` | `be-4.1.4` | `sha256:08c139f3d0ec45090a5214fd76f19f84b871d05f1a2db83b5e35a85c193617b2` | amd64, arm64 |
| Flink（基础镜像） | `flink` | `1.20.1-scala_2.12-java17` | 见构建产物 | amd64, arm64 |
| Flink（本项目镜像） | `data-platform/flink` | `1.20.1`（由 `infrastructure/flink/Dockerfile` 构建） | 本地构建 | amd64 |
| Flink Kafka SQL Connector | `flink-sql-connector-kafka` | `3.4.0-1.20` | Maven Central 官方 artifact | — |
| Prometheus（Sprint 11） | `prom/prometheus` | `v3.13.3` | `sha256:6976aa8a60fec930796ce5772b8d12da7a318a5daa8d40d69c5c7819a05eeed7` | amd64, arm64 |
| Grafana（Sprint 11） | `grafana/grafana` | `13.2.2` | `sha256:ac461fb352abc50da10a51c7d02462e9c05488f11f53f14b3ad79a8145f638a0` | amd64, arm64 |

> Flink 镜像**不直接使用官方镜像**，而是在其之上加装 Kafka SQL connector
> （`infrastructure/flink/Dockerfile`），这样 TaskManager / JobManager /
> SQL Gateway 三个容器都自带 connector，避免运行时再分发 JAR。
> JAR 通过 `infrastructure/flink/lib/download-connector.sh` 从 Maven Central 获取，
> 仓库不提交二进制（`lib/.gitignore` 忽略 `*.jar`）。

> digest 为 **multi-arch manifest list（OCI image index）** 的摘要，
> 由 Docker Hub Registry API 实测获得，可作为复现依据。
> 如需锁定架构，可改用 `image@sha256:<platform-digest>`。

### 3.1 Python 依赖

| 包 | 版本约束 | 说明 |
| --- | --- | --- |
| `Faker` | `>=30,<40` | 生成用户/商品/地址等仿真数据 |
| `mysql-connector-python` | `>=9,<10` | 写入 MySQL |
| `confluent-kafka` | `>=2,<3` | 生产 Kafka 事件 |
| `python-dotenv` | `>=1,<2` | 读取 `.env` |
| `pytest` | `>=8,<10` | 测试框架 |

### 3.2 服务层 Python 依赖（宿主机 venv，**实测版本**）

服务层不进 Docker，跑在宿主机的三个独立 venv 上（见 AGENTS §2.1 的部署分层原则）。
下表是**服务器上 `pip show` 实测到的版本**，不是 `requirements.txt` 里写的约束 ——
"钉了某个版本"与"装到的是那个版本"是两件事。

| venv | 包 | 实测版本 | 引入 Sprint |
| --- | --- | --- | --- |
| `.venv`（数据服务） | `fastapi` / `uvicorn` | `0.141.1` / `0.54.0` | S6 |
| `.venv-agent`（Agent） | `openai` | `2.54.0` | S7 |
| `.venv-agent` | `fastapi` / `uvicorn` / `httpx` | `0.141.1` / `0.54.0` / `0.28.1` | S7 |
| `.venv-agent` | **`langgraph`** | **`1.1.0`** | **S8** |
| `.venv-agent` | **`mcp`**（服务器 + 客户端同一个包） | **`2.2.0`** | **S10** |
| `.venv-agent` | `pytest` | `9.1.1` | S8 |
| `.venv-airflow` | `apache-airflow` | `3.3.2` | S4 |
| 容器 `data-generator` | Python | `3.13.14` | S0 |

**Sprint 8 引入 `langgraph==1.1.0`**（图式编排）。版本依据：

- PyPI 官方 JSON API 的 `releases` 与服务器实测 `pip index versions langgraph`
  都确认存在 `1.1.0`；明确**不使用** `1.2.x`（含 `1.2.0a6` 等预览版）；
- 装在 **Agent 独立 venv `.venv-agent`**，由 `services/agent/requirements.txt` 钉版本；
- 它带来 6 个传递依赖：`langchain-core` / `langgraph-checkpoint` /
  `langgraph-prebuilt` / `langgraph-sdk` / `pydantic` / `xxhash`。
  该 venv 包总数因此从 5 个变为 **53 个** —— 这是引入图式编排的**实际代价**，
  记在这里而不是含糊带过。

> ⚠️ **验证依赖时必须用运行该服务的解释器**（Sprint 8 实测踩坑）：
> 图单元测试要 `import langgraph`，而 pytest 原本只装在数据服务的 `.venv` 里，
> 于是用 `.venv/bin/python` 跑图测试会得到 22 条"缺少 langgraph 依赖"，
> 看起来像"图没实现"。`verify-sprint-8.sh` / `verify-sprint-9.sh` 现在统一优先用
> `.venv-agent/bin/python`，并把选中的解释器路径打印出来。

**Sprint 9 的检索层不引入任何新依赖**：BM25 + 同义词表 + 中文分词全部用标准库实现，
**未引入 Milvus / FAISS / Elasticsearch / chromadb 等向量检索组件**
（实测 Agent venv 里此类包数量为 0）。方案依据见
[`DECISIONS.md` ⏳7](DECISIONS.md) 与 [`sprint/SPRINT_9.md`](sprint/SPRINT_9.md) 第 2 节。

### 3.3 Sprint 10 引入 `mcp==2.2.0`（MCP 服务 + Agent 客户端）

**同一个包承担两个角色**：`services/mcp` 用它的**服务器**侧
（`MCPServer`），`services/agent` 用它的**客户端**侧（`Client`）。
因此两处 `requirements.txt` 钉的版本**必须一致** ——
版本不同会出现"协议版本对不上"这种握手期就失败的故障（比运行期失败更难定位）。
`tests/test_mcp.py::test_agent_and_mcp_pin_the_same_mcp_version` 锁住这一点。

**版本依据（查证，不是猜）**：

| 证据 | 内容 |
| --- | --- |
| PyPI 官方 JSON API（`https://pypi.org/pypi/mcp/json`） | `info.version = 2.2.0`；官方 README 写明 2.x 是 *"the current stable release line"*，1.x 只收关键修复与安全补丁 |
| 服务器实测 `pip index versions mcp` | `2.2.0, 2.1.1, 2.1.0, 2.0.1, 2.0.0, 1.30.0, 1.29.1, …` |
| 实测安装（`.venv-agent`） | `Successfully installed … mcp-2.2.0 mcp-types-2.2.0 …`；`pip show mcp` → `Version: 2.2.0` |

> ⚠️ **v1 → v2 是破坏性重构**：`FastMCP` → `MCPServer`、
> `ClientSession` → `Client`、`httpx` → `httpx2`、`streamablehttp_client` → `streamable_http_client`。
> 这正是必须**钉死 `==2.2.0`** 而不是给区间的原因：
> 给区间等于允许在一次部署里换掉整套 API 形状。
> 版本依据全文写在 [`services/mcp/requirements.txt`](../services/mcp/requirements.txt) 顶部。

**传递依赖与"不新增技术栈"的核对**（`AGENTS.md` §2.3）：

实测带入 13 个包：`mcp-types` / `pydantic` / `starlette` / `uvicorn` / `anyio` /
`httpx2` / `jsonschema` / `pyjwt[crypto]` / `cryptography` / `cffi` / `pycparser` /
`opentelemetry-api` / `sse-starlette` / `attrs` / `referencing` / `rpds-py` /
`python-multipart`。

其中 **`starlette` 与 `uvicorn` 项目里本来就有**（数据服务用的就是 FastAPI 那一套），
所以**没有引入新的技术栈类型** —— 只是把同一门 HTTP/ASGI 技术多接了一个官方 SDK。

**MCP 服务的部署形态**：独立 systemd 单元 `data-platform-mcp`
（127.0.0.1:8200，专用用户 `dpmcp`，**不读 `.env`**）。
单元文件在 [`services/mcp/deploy/`](../services/mcp/deploy/)。
它**不进 `docker-compose.yml`**（服务层部署原则，`AGENTS.md` §2.1），
也不改 Nginx。

---

## 4. 版本选型依据

### 4.1 MySQL 8.4.11（LTS）

- 镜像来源：Docker 官方库镜像 `mysql`（`library/mysql`），非 `mysql/mysql-server`。
- **排除 `mysql/mysql-server`**：该仓库最后一次推送为 **2023-01-18（8.0.32）**，已停止维护。
- **选择 8.4 LTS 而非 9.x / 26.x**：
  - MySQL 的发布模型为 **LTS + Innovation**，偶数版本 `8.4` 为 LTS；
    9.x 与 26.x 属 Innovation 系列，生命周期短；
  - 本项目为毕业设计，需要长期稳定、可复现的环境。
- **备选（兼容性优先场景）**：如需与较老的连接器/生态严格对齐，
  可改用 `mysql:8.0.46`（8.0 系列最后一个补丁版本）。

### 4.2 Apache Kafka 4.2.1（KRaft）

- 镜像来源：**Apache 官方镜像 `apache/kafka`**（非第三方 `confluentinc/*` / `bitnami/*`）。
- **选择 4.x 的理由**：Kafka 4.0 起 **ZooKeeper 已被移除**，
  官方镜像默认以 **KRaft** 模式运行，无需额外部署 ZooKeeper，
  对单机开发环境是最简且最持久的方案。
- 选择 `4.2.1` 而非 `latest`：`latest` 当前指向 `4.3.1`，
  但开发环境应固定明确版本；同时避免使用 `-rc*` 候选版本。

### 4.3 MinIO —— 官方镜像已停止分发（重要变更）

**事实（已核实）：**

- MinIO 自 2025-10 起**停止免费分发 Docker 镜像**；
- Docker Hub 上 `minio/minio`、`minio/mc`、`minio/minio-operator` 均返回 **404**（整个 `minio` 组织已下架）；
- 官方下载站 `dl.min.io` 返回 **410 Gone**；
- `registry.min.io` / `quay.io/minio/minio` 需鉴权（401）。

**本项目采取的方案：**

使用社区再托管镜像 `coollabsio/minio:RELEASE.2025-10-15T17-29-55Z`。

- 该 tag 即为 MinIO 停发前的**最后一个官方发行版**，内容与官方发行版一致，仅分发者不同；
- 该镜像为多架构（linux/amd64、linux/arm64），与 `mysql` / `apache/kafka` 架构一致。

**供应链风险说明（如实记录）：**

该镜像由第三方（Coolify 作者）再托管，**不属于 MinIO 官方发布渠道**。
已记录其 manifest digest 以固定复现。若后续需要更严格的供应链保证，
可改为：从 MinIO 源码 tag `RELEASE.2025-10-15T17-29-55Z` 自行 `docker build`，
或使用企业已有的 MinIO 镜像 `docker save` / `docker load` 导入。

> 若后续 Sprint 需要 MinIO Client（`mc`），需一并解决 `minio/mc` 同样下架的问题。

### 4.4 Apache Doris 4.1.4（FE + BE 同版本）

- 镜像来源：**Apache 官方镜像 `apache/doris`**。
- Tag 命名：`apache/doris:fe-<version>` 与 `apache/doris:be-<version>`。
- **FE 与 BE 必须使用完全相同的版本号**，否则注册或 RPC 会失败。
- 选择 `4.1.4`：截至 2026-09-26，该 tag 为 Docker Hub 上 `fe-*` / `be-*` 的**最新稳定发布**
  （更新于 2026-09-11）；同版本的 `ms-4.1.4` 为 Meta Service，
  仅在存算分离（disaggregated）架构下需要，**Sprint 0 单机存算一体模式不需要**。

### 4.5 Doris 容器配置依据（来自镜像内实际脚本）

以下内容**不是猜测**，而是从 `apache/doris:fe-4.1.4` / `be-4.1.4` 镜像中
实际提取 `/usr/local/bin/init_fe.sh`、`/usr/local/bin/init_be.sh`、
`/usr/local/bin/entry_point.sh` 后确认的契约：

| 容器 | Entrypoint | 必需环境变量 | 说明 |
| --- | --- | --- | --- |
| FE | `bash init_fe.sh` | `FE_SERVERS=<name>:<IPv4>:9010`、`FE_ID=1` | ELECTION 模式；`FE_ID=1` 即 master |
| BE | `bash entry_point.sh` | `FE_SERVERS=<同上>`、`BE_ADDR=<BE IPv4>:9050` | BE 自动向 FE 注册 |

关键约束：

1. **`FE_SERVERS` 中必须是 IPv4 地址，不能是主机名。**
   `init_fe.sh` 使用正则 `^.+:<ipv4>:<port>(,...)*$` 校验，
   传入服务名（如 `doris-fe`）会直接以 `Invalid FE_SERVERS format` 退出。
   因此 FE/BE 容器在网络中必须使用**固定 IP**。
2. **`priority_networks` 由脚本自动推导**为 `<该 IP 前三段>.0/24`，
   并追加写入 `fe.conf` / `be.conf`（仅在元数据/存储目录不存在时）。
   所以**静态 IP 的网段必须与该推导结果一致**。
3. BE 容器会在启动后读取 **`/docker-entrypoint-initdb.d/`** 下的 `*.sql` / `*.sh`，
   通过 `mysql -h <MASTER_FE_IP> -P 9030 -uroot` 执行。
   本项目的 Doris 初始化 SQL 即通过此机制自动执行。
4. FE 容器**不**提供 `/docker-entrypoint-initdb.d` 钩子（其 entrypoint 为 `init_fe.sh`）。
5. Doris 默认 `root` 账户**空密码**。本项目通过 SQL 显式设置 `root` 口令，
   口令来自 `.env` 的 `DORIS_ROOT_PASSWORD`，不硬编码。

### 4.6 Docker 网络与子网
- 统一网络名：`data-platform`（`driver: bridge`）。
- 因上述第 1、2 条约束，本项目为 `data-platform` 指定**固定子网** `172.28.0.0/24`，
  并为 Doris FE/BE 分配固定 IP：

| 服务 | 静态 IP |
| --- | --- |
| `doris-fe` | `172.28.0.10` |
| `doris-be` | `172.28.0.11` |

- 其余服务（MySQL / Kafka / MinIO）**不分配静态 IP**，通过 Docker DNS 以**服务名**互访。
- 容器间通信**禁止使用 `localhost` / `127.0.0.1`**，必须使用服务名或上述固定 IP。
- `172.28.0.0/24` 属于 Docker 默认地址池 (`172.17.0.0/16` ~ `172.31.0.0/16`) 范围，
  且启动前未与已有网络冲突。若目标机器上该网段被占用，修改 `.env` 中的
  `DATA_PLATFORM_SUBNET` / `DORIS_FE_IP` / `DORIS_BE_IP` 三个变量即可。

### 4.7 Apache Flink 1.20.1（Sprint 1 引入）

- 基础镜像：`flink:1.20.1-scala_2.12-java17`（官方镜像，多架构）。
  - 选 `scala_2.12` + `java17`：Flink 1.20 起 Java 17 是推荐运行时；
    Scala 版本只影响 DataStream API 的 Scala 封装，本项目用 SQL 因此无实质影响，
    但**必须固定**，否则同一个 tag 的不同后缀行为不一致。
  - 选 `1.20.1` 而非 `2.x`：Doris 4.1 的 Flink Connector 与生态仍以 1.20.x 为主，
    且 1.20 是 1.x 的最后一个特性版本（稳定）。
- Kafka 连接器：`flink-sql-connector-kafka-3.4.0-1.20.jar`
  - **必须用 `flink-sql-connector-kafka`（带 `sql`）** —— 它是 shaded 的
    "all-in-one" JAR，可以直接丢进 `/opt/flink/lib`；
    `flink-connector-kafka` 是 DataStream 版，SQL 用不了。
  - 版本号后缀 `-1.20` 表示适配 Flink 1.20；本项目实测可用版本为 **3.4.0-1.20**
    （`3.2.0-1.20` 在 Maven Central 上不存在，属记录错误）。
- 部署形态：**Session 集群 + SQL Gateway**
  - `flink-jobmanager` / `flink-taskmanager` 组成常驻 session 集群；
  - `flink-sql-gateway` 提供 REST 提交入口（`:8083`）；
  - `flink-jobs` 是常驻提交容器（`python:3.12-alpine` + 标准库脚本），
    启动时清理历史作业 → 提交 SQL → 保活 session。
- TaskManager slots：默认 **8**（`FLINK_TASKMANAGER_SLOTS`），
  与 8 个 sink 作业（4 DWD + 4 指标）一一对应。
  ⚠️ **没有余量**：任何历史作业残留都会让新作业拿不到 slot 而失败，
  因此提交前必须先清场（见 `scripts/cancel-flink-jobs.sh` 的说明）。

> **SQL Gateway 是 session 模式**（重要运维约束）：
> 作业生命周期绑定在 session 上，**重启 `flink-jobs` 容器不会取消作业**。
> 这会同时造成「新作业抢不到 slot」与「旧作业把重放事件累加到旧窗口状态导致指标翻倍」
> 两类问题。当前由提交脚本启动时自动清场兜底。

---

## 5. 持久化

数据通过 **Docker 命名卷**持久化，容器 `down` / 重建后数据保留。

| 卷名 | 挂载点 | 内容 |
| --- | --- | --- |
| `data-platform-mysql-data` | `/var/lib/mysql` | MySQL 数据目录 |
| `data-platform-kafka-data` | `/var/lib/kafka/data` | Kafka 日志段与元数据 |
| `data-platform-minio-data` | `/data` | MinIO 对象数据 |
| `data-platform-doris-fe-meta` | `/opt/apache-doris/fe/doris-meta` | FE 元数据 |
| `data-platform-doris-fe-log` | `/opt/apache-doris/fe/log` | FE 日志 |
| `data-platform-doris-be-storage` | `/opt/apache-doris/be/storage` | BE 数据存储 |
| `data-platform-doris-be-log` | `/opt/apache-doris/be/log` | BE 日志 |
| `data-platform-data-generator-state` | `/app/state` | 数据生成器快照（保证 Kafka 事件与 MySQL 数据一致） |
| `data-platform-flink-checkpoints` | `/opt/flink/checkpoints` | Flink 检查点（作业恢复用） |

> `docker compose down -v` 会**删除全部数据**，仅在需要完全重置环境时使用。

---

## 6. 初始化脚本位置

| 组件 | 文件 | 执行方式 |
| --- | --- | --- |
| MySQL | `sql/mysql/01_schema.sql` + `infrastructure/mysql/init/` | 容器首次初始化时由 `/docker-entrypoint-initdb.d` 自动执行 |
| Kafka | `infrastructure/kafka/init/01-create-topics.sh` | 由 `kafka-init` 一次性容器执行 |
| MinIO | `infrastructure/minio/init/01-create-bucket.sh` | 由 `minio-init` 一次性容器执行 |
| Doris | `sql/doris/02_doris_init.sql`、`10~12_*.sql`、`13_routine_load.sh` | 由 BE 容器 `/docker-entrypoint-initdb.d` 通过 `be-keepalive.sh` 执行（每个脚本幂等） |
| Flink | `infrastructure/flink/sql/01~05_*.sql` | 由 `flink-jobs` 容器启动时经 SQL Gateway REST 提交（见 `init/01-submit-jobs.py`） |

> MySQL 与 Doris 的初始化脚本**只在数据目录为空时执行一次**。
> 修改脚本后如需重新初始化，必须先删除对应卷（会丢失数据）。
>
> `sql/doris/13_routine_load.sh` 与 `10~12_*.sql` 是幂等的
> （`CREATE TABLE IF NOT EXISTS` / 先 `SHOW` 后创建），
> 因此每次 BE 容器重启都会重跑一遍并自动补齐缺失的表与导入作业。

---

## 7. 环境变量

所有口令与端口通过 `.env` 注入，`.env` 已在 `.gitignore` 中。

```bash
cp .env.example .env
```

`.env.example` 中所有口令均为 `change_me_*` 占位符，**仓库中不含任何真实凭据**。

---

## 8. 已知环境风险

### 8.1 已在服务器上实测解决的问题

| 问题 | 根因 | 处理 |
| --- | --- | --- |
| Doris BE 每 ~15 秒重启 | 上游 `be-4.1.4` 的 `entry_point.sh` 在 `check_be_status` 成功后即返回，容器 PID 1 退出 | `infrastructure/doris/be-keepalive.sh` 保活包装；已验证 150 秒 0 重启 |
| Doris 初始化 SQL 中断 | `ALTER SYSTEM SET default_replication_num` 在 4.1.4 不存在该系统变量 | 删除该语句，改为每张表显式 `PROPERTIES("replication_num"="1")` |
| MinIO 初始化失败 | minio 镜像基于 BusyBox，**无 sed/awk/grep/curl/tar** | init 脚本改 POSIX 参数展开；healthcheck 改用 `mc ls` |
| 数据生成器写快照失败 | 非 root 用户 + 宿主机属主挂载 | 快照目录改用命名卷 |
| `vm.max_map_count` 偏低 | 默认 1048576 < Doris 要求 2000000 | `/etc/sysctl.d/99-data-platform.conf` |
| Docker Hub 不可达 | 中国大陆网络 | 国内 registry mirror + SSH 反向隧道代理 |
| Doris FE 占用 5.93 GB 内存 | FE JVM 默认堆无上限 | `.env` 的 `DORIS_FE_JAVA_OPTS` 限制堆与 Metaspace，可用内存 4.9 GB → 9.9 GB |
| Doris 拒绝在启用 swap 时启动 | `start_be.sh` 显式检查 swap | 移除 swap；改为限制各组件内存上限 |
| `flink-sql-connector-kafka:3.2.0-1.20` 下载 404 | 该版本不存在（记录错误） | 改用 Maven Central 上真实存在的 `3.4.0-1.20` |
| Flink 作业因 slot 不足提交失败 | 8 个作业 × `parallelism.default=3` 远超 8 个 slot | 在 source SQL 中 `SET 'parallelism.default' = '1'` |
| 重启 `flink-jobs` 后出现重复作业、指标翻倍 | SQL Gateway 为 session 模式，重启容器不取消作业；旧作业占 slot、旧窗口状态把重放事件重复累加 | 提交脚本启动时先取消所有非终态作业；新增 `scripts/cancel-flink-jobs.sh` |
| 健康检查误报 Routine Load 全部缺失 | mysql 客户端 `-N` 会去掉 `Name:` / `State:` 字段标签，解析必然失败 | 去掉 `-N`；`SHOW ROUTINE LOAD` 还需带 `USE <db>` |
| `SHOW ROUTINE LOAD` 报 `No database selected` | 该语句需要当前库上下文 | 一律写成 `USE ecommerce; SHOW ROUTINE LOAD;` |
| Agent 重启后起不来：`PermissionError: '/opt/data-platform/.env'` | `.env` 的权限被改成 `600 root:dpapi`，而 `data-platform-agent` 以 `User=dpagent` 运行、靠**组读**拿配置（部署脚本刻意如此设计：可读不可写） | 恢复 `chown root:dpapi` + `chmod 640`（与 `install-web.sh` 一致）。注意 `scripts/deploy-monitoring.sh` 会把 `.env` 收紧到 600，跑过它之后需要复原 |
| 图单元测试 22 条集体报"缺少 langgraph 依赖" | 验收脚本用数据服务的 `.venv` 跑图测试，而 `langgraph` 只装在 `.venv-agent` | 测试统一用 `.venv-agent/bin/python`，并把选中的解释器路径打印出来 |

### 8.2 仍需注意的风险

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| `apache/doris:be-4.1.4` 的 entrypoint 上游缺陷 | 若移除保活包装会重新出现重启循环 | 保留 `be-keepalive.sh`；上游修复后可移除 `entrypoint` 覆盖 |
| MinIO 镜像为社区再托管 | 供应链风险 | 已固定 digest，并记录替代方案 |
| 服务器**不能加 SWAP** | Doris BE 在有 swap 时拒绝启动，内存只能用「限上限」的方式控制 | 已为 FE/BE/Flink 分别设置内存上限；若后续同时跑 Flink + Spark 内存仍紧张，只能升级内存规格（不可加 swap） |
| 镜像加速站为第三方服务 | 可用性会变化 | `.deploy/README.md` 记录了可用源清单与切换方法 |
| 内存 16 GB | Doris FE (JVM) + BE + Kafka + Flink JM/TM 同时运行占用较高（实测已用 12.6 GB / 可用 3.3 GB） | 已限制各组件上限；Flink TaskManager 内存可用 `FLINK_TASKMANAGER_MEMORY` 调节 |
| `172.28.0.0/24` 网段冲突 | Doris FE/BE 无法启动 | 通过 `.env` 改子网与静态 IP |
| Flink slots 只有 8 个，与 8 个作业**刚好相等** | 任何历史作业残留都会让新作业拿不到 slot | 提交前清场（脚本已内置）；如需余量可调大 `FLINK_TASKMANAGER_SLOTS` |
| 源 topic 采用 `earliest-offset` 可重放 | 重复生产同一批事件会让窗口指标累加（翻倍） | 验收用 `verify-sprint-1.sh --replay` 构造"恰好一代事件"；根治方案（按 `event_id` 去重）记录在 `docs/sprint/SPRINT_1.md` 第 11.1 节 |

---

## 9. 版本变更记录

| 日期 | 组件 | 变更 | 原因 |
| --- | --- | --- | --- |
| 2026-09-26 | 全部 | 初始记录 | Sprint 0 环境建立 |
| 2026-09-26 | Docker / Doris / MinIO | 补记腾讯云服务器环境、镜像源适配、内核调优 | 在服务器完成 Sprint 0 全部容器验收 |
| 2026-09-26 | Doris BE | 新增保活包装，记录上游 entrypoint 缺陷 | 实测 BE 每 15 秒重启 |
| 2026-09-26 | MySQL | 确认使用官方 `mysql` 库镜像（非 `mysql/mysql-server`） | 后者 2023 年起停更 |
| 2026-09-26 | Flink | 新增 `1.20.1-scala_2.12-java17` + `flink-sql-connector-kafka 3.4.0-1.20` | Sprint 1 实时链路 |
| 2026-09-26 | Doris FE | 限制 JVM 堆（`-Xmx1536m`） | FE 实测占用 5.93 GB，挤占 Flink/TM 内存 |
| 2026-09-26 | Doris | 明确**不可启用 swap** | BE 的 `start_be.sh` 拒绝在有 swap 时启动 |
| 2026-09-27 | **`langgraph`** | **新增 `langgraph==1.1.0`（`.venv-agent`，实测 Version: 1.1.0）** | **Sprint 8 图式编排（规划→取数→校验→反思→汇总）** |
| 2026-09-27 | **检索层** | **零新增依赖**（BM25 + 显式同义词表，纯标准库） | **Sprint 9；明确不引入向量库，依据 DECISIONS ⏳7** |
| 2026-09-27 | **`mcp`** | **新增 `mcp==2.2.0`（`.venv-agent`，实测 Version: 2.2.0；服务端与客户端同一个包、同一个版本）** | **Sprint 10 MCP：把只读数据能力用 MCP 暴露，Agent 多一条可枚举的取数路径** |

---

## 10. Sprint 11 监控组件（Prometheus + Grafana）

### 10.1 实际装到的版本（实测，非计划值）

在服务器上用 `docker inspect` / `prometheus --version` / `GET /api/health` 三种方式交叉核对：

```text
prom/prometheus:v3.13.3     →  自报 prometheus 3.13.3
grafana/grafana:13.2.2      →  自报 {"database":"ok","version":"13.2.2","commit":"1bea008f7e4e..."}
```

| 组件 | 镜像 | Tag | digest | 容器内监听 | 宿主映射 | mem_limit |
| --- | --- | --- | --- | --- | --- | --- |
| Prometheus | `prom/prometheus` | `v3.13.3` | `sha256:6976aa8a60fec930796ce5772b8d12da7a318a5daa8d40d69c5c7819a05eeed7` | 9090 | `127.0.0.1:9090` | `256m` |
| Grafana | `grafana/grafana` | `13.2.2` | `sha256:ac461fb352abc50da10a51c7d02462e9c05488f11f53f14b3ad79a8145f638a0` | 3000 | `127.0.0.1:3001` | `256m` |

> **Tag 写法不同不是笔误**：Prometheus 的官方 tag 带 `v`（`v3.13.3`），
> Grafana 的不带（`13.2.2`）。写成 `prom/prometheus:3.13.3` 会拉不到镜像。

网络：两者都加入 `data-platform`；Grafana 使用**固定 IP `172.28.0.20`**（理由见 §10.4）。
命名卷：`data-platform-prometheus-data`（TSDB）/ `data-platform-grafana-data`（sqlite + 面板状态）。
healthcheck：Prometheus `wget -q -O /dev/null http://127.0.0.1:9090/-/healthy`；
Grafana `wget -q -O /dev/null http://127.0.0.1:3000/api/health`；
两者都含 `interval` / `timeout` / `retries` / `start_period`（AGENTS §4.4）。

### 10.2 版本选型依据（查证过程，可复现）

| 组件 | 候选 | 决策 | 理由 |
| --- | --- | --- | --- |
| Prometheus | `3.14.0`（最新） | ❌ | 2026-08-17 发布，**EOL 2026-09-30** —— 只剩 3 天，钉在毕设里等于一开始就过期 |
| Prometheus | **`3.13.3`** | ✅ | **3.13 是 LTS 线**（3.13.0 于 2026-07-01 发布），LTS 支持到 **2027-07-31**。3.13.3 是该线最新补丁（2026-09-07） |
| Grafana | **`13.2.2`** | ✅ | 13.2 是**当前开发生命周期内的最新 minor**（2026-08-18 发布），13.2.2 为其最新补丁（2026-09-15），支持到 2027-05-18 |
| Grafana | `12.4.11` | ⚠️ 备选 | EOL 2027-05-24，比 13.2 更长；但 13.2 已是当前主线，无理由倒退 |

**查证方式**：`endoflife.date` 的 `prometheus.md` / `grafana.md`（其 `releasePolicyLink`
指向官方 release-cycle 文档），再用镜像仓库的 tag 列表核对目标 tag **确实存在**，
最后以容器内程序**自报版本**为准（标签存在 ≠ 实际装到的是那个版本）。

### 10.3 镜像拉取：本机直连 Docker Hub 不通

```text
实测: registry-1.docker.io       → 超时（TCP 443 被拒）
      api.github.com             → 无输出（同样不通）
用 的: /etc/docker/daemon.json 里配置的镜像加速站（docker.1panel.live 等）→ 200/401
```

因此 `deploy-monitoring.sh` 在拉取失败时会明确提示"本机直连 Docker Hub 不通，
靠 daemon.json 的镜像加速"，而不是让人去怀疑网络正常与否。

### 10.4 Grafana 固定 IP `172.28.0.20` 的由来

Grafana 的子路径部署需要 `root_url`（含 `%(domain)s`），
而 `%(domain)s` 在容器内解析为 `localhost`，于是它把 `/metrics`
**301 到 `http://localhost:3000/grafana/metrics`**。
Prometheus 会跟随重定向，而它容器里的 localhost 指它自己 →
抓取目标永远 `down`。修法是让那个重定向能走通：

```text
Grafana  在 data-platform 网络里固定为 172.28.0.20（.env 的 GRAFANA_IP）
Prometheus  extra_hosts: localhost:172.28.0.20
```

**为什么不是 `.12`**：一开始写成 `.12`（紧邻 Doris 的 `.10/.11`，看着很自然），
但**本子网里除 Doris 外都是 Docker 动态分配**，`mysql` 恰好分到了 `.12`，
Grafana 直接创建失败并报 `Address already in use`。
最终取 `.20`，远离动态段。实测当前占用：

```text
spark-master .2  minio .3  spark-worker .4  flink-jobmanager .5  kafka .6
flink-sql-gateway .7  flink-taskmanager .8  flink-jobs .9
doris-fe .10（静态） doris-be .11（静态） mysql .12  hive-metastore .13
prometheus .15（动态） grafana .20（静态）
```

### 10.5 Grafana 面板与数据源

| 数据源 | uid | 类型 | 指向 | 账号 |
| --- | --- | --- | --- | --- |
| Prometheus | `prometheus` | prometheus | `http://prometheus:9090` | — |
| Doris（只读） | `doris-mysql` | mysql | `172.28.0.10:9030` | **`agent_ro`（只读，非 root）** |

仪表盘 `data-platform-overview`（uid 同名，23 个面板，5 个分区）：

```text
一、数据新鲜度      最新交易窗口 / 最新流量窗口 / 最新天分区
二、交易域          每日 GMV、每日订单量、交易指标快照（含支付成功率与退款率）
三、流量域          每日 PV/UV、行为漏斗（VIEW>CLICK>CART>FAVORITE>BUY）
四、批流对账        交易域不一致窗口数、流量域不一致窗口数、对账窗口总数
五、监控自身        抓取目标数、各目标 up、抓取耗时、Doris FE 查询错误、Grafana 存活
```

> `allowUiUpdates: false` 是刻意的：UI 里的改动不会写回仓库，
> 打开它会造成"代码与运行态分叉"（改面板 → 重建容器后改动神秘消失）。
> 唯一事实源是仓库里的 JSON。

### 10.6 公网入口

```text
http://36.151.150.140/grafana/    面板（匿名只读 Viewer）
http://36.151.150.140/metrics/    Prometheus 原始指标（Nginx 只放行 GET/HEAD/OPTIONS）
```

两个端口本身都只绑 `127.0.0.1`，公网唯一通道是 Nginx 的这两条 location。
Nginx 配置的改动遵循 AGENTS §15.7 硬规范第 3 条：
**先备份、`nginx -t` 失败自动还原**（由 `scripts/deploy-web.sh` 实现）。
