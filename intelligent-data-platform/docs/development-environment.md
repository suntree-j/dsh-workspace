# 开发环境说明（Development Environment）

> 文档版本：V1.0
> 更新日期：2026-09-26
> 适用 Sprint：Sprint 0
>
> 本文档记录**实际使用的宿主机环境、镜像版本、端口分配与版本选型依据**。
> 原则：**不使用 `latest`，不猜测版本**。所有版本均来自官方仓库实际查证，
> 并记录镜像 digest 以便复现。

---

## 1. 宿主机环境（实测）

以下为 Sprint 0 开发时实际探测到的环境信息。

| 项目 | 实测值 |
| --- | --- |
| 操作系统 | Microsoft Windows 11 Home China |
| 系统版本 | 10.0.26200（Build 26200） |
| CPU | 16 逻辑核心 |
| 内存 | 15.7 GB |
| 系统盘可用空间 | 43.1 GB（C:） |
| 当前用户是否管理员 | **否**（`IsInRole(Administrator) = False`） |
| Hyper-V 虚拟机监控程序 | 未呈现（`HypervisorPresent = False`） |
| 固件虚拟化 | 已启用（`VirtualizationFirmwareEnabled = True`） |
| Git | 2.54.0.windows.1 |
| Python | 3.13.14（`PythonSoftwareFoundation.Python.3.13`，另有 3.10 并存） |
| pip | 26.1.2 |
| winget | 1.29.380.0（源：winget / msstore） |

### 1.1 容器运行时

Sprint 0 的 `docker compose up -d` 需要**容器运行时**。本机初始状态为：

```text
Docker Desktop   : 未安装
Docker CLI       : 不在 PATH
Podman / nerdctl : 未安装
WSL 发行版       : 无（`wsl -l -v` 返回 REGDB_E_CLASSNOTREG）
```

Docker Desktop 通过 **winget 安装成功**：

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

安装结果：

| 项目 | 实测值 |
| --- | --- |
| Docker Desktop 版本 | 4.91.0 |
| Docker Client 版本 | 29.8.0（API 1.56） |
| Docker Compose 版本 | v5.5.1 |
| Docker Engine 版本 | _待回填（重启并启动 Docker Desktop 后确认）_ |

> ⚠️ **重启前 daemon 不可用**：
> `docker info` 会报
> `failed to connect to the docker API at npipe:////./pipe/docker_engine`。
> 这是预期行为，不是配置错误。
> 重启后需首次启动 Docker Desktop、接受服务条款，并确认使用 **WSL 2 backend**。

#### 磁盘空间建议（重要）

| 盘符 | 总量 | 可用 |
| --- | --- | --- |
| C: | 145.4 GB | 38.7 GB |
| **D:** | **329.1 GB** | **296.3 GB** |

Doris BE 镜像约 2.9 GB、FE 约 1.6 GB、Kafka 约 0.2 GB、MySQL 约 0.2 GB、
MinIO 约 0.06 GB，**镜像合计约 5 GB**；加上 Kafka 日志段、Doris BE 存储与
MySQL 数据文件，运行时占用可能达到 10~15 GB。

C 盘可用空间偏紧，**建议在首次启动 Docker Desktop 前**把镜像存储位置改到 D 盘：

```text
Docker Desktop → Settings → Resources → Disk image location → D:\DockerData
```

若保持默认（`%LOCALAPPDATA%\Docker\wsl`），需确保 C 盘剩余空间 > 20 GB。

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

> `docker compose down -v` 会**删除全部数据**，仅在需要完全重置环境时使用。

---

## 6. 初始化脚本位置

| 组件 | 文件 | 执行方式 |
| --- | --- | --- |
| MySQL | `sql/mysql/01_schema.sql` + `infrastructure/mysql/init/` | 容器首次初始化时由 `/docker-entrypoint-initdb.d` 自动执行 |
| Kafka | `infrastructure/kafka/init/01-create-topics.sh` | 由 `kafka-init` 一次性容器执行 |
| MinIO | `infrastructure/minio/init/01-create-bucket.sh` | 由 `minio-init` 一次性容器执行 |
| Doris | `sql/doris/02_doris_init.sql` | 由 BE 容器 `/docker-entrypoint-initdb.d` 自动执行 |

> MySQL 与 Doris 的初始化脚本**只在数据目录为空时执行一次**。
> 修改脚本后如需重新初始化，必须先删除对应卷（会丢失数据）。

---

## 7. 环境变量

所有口令与端口通过 `.env` 注入，`.env` 已在 `.gitignore` 中。

```bash
cp .env.example .env
```

`.env.example` 中所有口令均为 `change_me_*` 占位符，**仓库中不含任何真实凭据**。

---

## 8. 已知环境风险

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| Docker Desktop 需管理员安装并重启 | 无法立即执行 `compose up` 验证 | 安装后按 README 快速启动步骤执行 |
| C 盘可用空间 43.1 GB | Doris BE 镜像约 2.9 GB，FE 约 1.6 GB，全部镜像合计约 5 GB；加上运行数据可能超过 15 GB | 启动前确认剩余空间 > 20 GB；必要时 `docker system prune` |
| 内存 15.7 GB | Doris FE (JVM) + BE + Kafka 同时运行占用较高 | 已为 FE/BE 设置合理 JVM/内存参数，避免默认值过大 |
| `172.28.0.0/24` 网段冲突 | Doris FE/BE 无法启动 | 通过 `.env` 改子网与静态 IP |
| MinIO 镜像为社区再托管 | 供应链风险 | 已固定 digest，并记录替代方案 |

---

## 9. 版本变更记录

| 日期 | 组件 | 变更 | 原因 |
| --- | --- | --- | --- |
| 2026-09-26 | 全部 | 初始记录 | Sprint 0 环境建立 |
