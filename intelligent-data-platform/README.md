# 基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台

> 面向电商场景的**批流一体**数据平台，最终包含实时链路（Kafka → Flink → Doris）、
> 离线链路（Spark → Iceberg on HDFS/S3 → Hive）、以及基于 **LangGraph + LLM + MCP**
> 的智能数据分析 Agent。
>
> **当前进度：Sprint 0（基础环境）、Sprint 1（实时数仓）、Sprint 2（离线链路）、
> Sprint 6（数据后台 + 前后端）均已在腾讯云服务器上验收通过。**
>
> 核心原则：**先工程，再智能。**
> 数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据。

> 🖥 **在线数据大屏**：<http://36.151.150.140/data/>
> 接口文档：<http://36.151.150.140/data/api/docs>
>
> 架构：`浏览器 → Nginx(:80) → /data/ 静态看板 + /data/api/ 只读数据服务 → Doris`
> 数据服务使用**专用只读账号**（`agent_ro`）+ SQL 安全守卫（仅 SELECT、强制 LIMIT），
> 指标与 MySQL 事实源精确对账（GMV 51,890,375.77 元，精确到分）。

> ✅ **Sprint 0 / 1 / 2 / 6 验收结果**（腾讯云 36.151.150.140 / Ubuntu 24.04.2 LTS）
>
> ```text
> ✅ Sprint 0  docker compose up -d 5 个核心服务 healthy；health-check 5/5；pytest 51 passed
> ✅ Sprint 1  Flink 8 作业 + Routine Load 8/8 RUNNING；health-check 11/11；
>              pytest 74 passed；ADS 与 MySQL 精确对账（GMV 精确到分）
> ✅ Sprint 2  Spark + Hive 离线链路；verify-sprint-2.sh 8/8 PASS；
>              ODS 5 张表逐表与 MySQL 精确一致（1200/600/6000/5406/254）
> ✅ Sprint 6  Nginx + systemd 直装上线；/data/ 看板可访问；API GMV == MySQL GMV；
>              只读账号写操作被 Doris 拒绝；pytest 55 单元 + 17 接口冒烟
> ```
>
> 逐项证据见
> [`docs/sprint/SPRINT_0_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_0_VERIFICATION_STATUS.md)、
> [`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_1_VERIFICATION_STATUS.md)、
> [`docs/sprint/SPRINT_2.md`](docs/sprint/SPRINT_2.md)、
> [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md)。

---

## 目录

- [1. 项目介绍](#1-项目介绍)
- [2. 项目架构](#2-项目架构)
- [3. 技术栈](#3-技术栈)
- [4. 环境要求](#4-环境要求)
- [5. 目录结构](#5-目录结构)
- [6. 快速启动](#6-快速启动)
- [7. 服务端口](#7-服务端口)
- [8. 账号与密码说明](#8-账号与密码说明)
- [9. 停止服务](#9-停止服务)
- [10. 查看状态](#10-查看状态)
- [11. 健康检查](#11-健康检查)
- [12. 数据生成](#12-数据生成)
- [13. 数据与事件模型](#13-数据与事件模型)
- [14. 测试](#14-测试)
- [15. 常见问题](#15-常见问题)
- [16. 后续 Roadmap](#16-后续-roadmap)

---

## 1. 项目介绍

电商业务的数据天然同时具备两种形态：

| 形态 | 特征 | 典型诉求 |
| --- | --- | --- |
| 实时流 | 点击、加购、下单、支付、退款持续产生 | 实时 GMV、实时订单量、实时在线人数 |
| 批量离线 | 日/月级经营分析、用户画像、商品分析 | 准确、可回溯、口径统一 |

传统 Lambda 架构把两者做成两套系统，代价是**同一指标两份实现、口径不一致、维护成本翻倍**。

本项目构建**批流一体**的数据平台：

- **Kafka** 作为统一事件入口；
- **Flink** 做实时计算，写入 **Doris** 提供亚秒级查询；
- **Spark + Iceberg on HDFS/MinIO** 做离线计算与湖仓存储；
- **Airflow** 编排批处理；
- 用 **LLM + LangGraph + Tool Calling + MCP** 构建数据分析 Agent，让业务人员用自然语言问数。

### 1.1 Sprint 0 已完成的内容

Sprint 0 只建立**基础数据环境**，为后续 Sprint 提供稳定基线：

```text
✅ Git 项目结构与文档基线
✅ Docker Compose 基础环境（统一网络 data-platform + 命名卷持久化）
✅ MySQL   8.4.11   —— ecommerce 库 + 5 张业务表
✅ Kafka   4.2.1    —— KRaft 模式 + 4 个 Topic
✅ MinIO            —— lakehouse bucket
✅ Doris   4.1.4    —— FE + BE + ecommerce 库 + test_connection 表
✅ Python 数据生成器 —— MySQL 业务数据 + Kafka 实时事件
✅ 启动/停止/状态/健康检查脚本
✅ 冒烟测试（pytest，真实执行）
```

**验证状态**：

```text
✅ 已实测   docker compose config 校验通过
✅ 已实测   Bash 脚本语法检查通过
✅ 已实测   24 个单元测试通过（生成器业务逻辑）
✅ 已实测   .env 未被追踪、脚本为 LF 行尾
⏳ 待执行   docker compose up -d / health-check.sh / 27 个冒烟测试
```

> Sprint 0 **不包含** Flink / Spark / Hive / HDFS / Airflow / Iceberg /
> LangGraph / RAG / MCP / Vue / Spring Boot / FastAPI / Prometheus / Grafana。

---

## 2. 项目架构

### 2.1 目标架构（完整形态）

```text
┌──────────────────────────────────────────────────────────────────┐
│                  应用与智能层 (Application & AI)                  │
│   Vue Dashboard   FastAPI/Spring Boot   LangGraph Agent + LLM     │
│                            │                    │                 │
│                            └────────┬───────────┘                 │
│                                     ▼                             │
│                        MCP Server / RAG（元数据与指标）             │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ 只读 SQL（默认仅 SELECT）
┌─────────────────────────────────▼────────────────────────────────┐
│                     统一查询与指标层 (Serving)                     │
│            Apache Doris (FE + BE)  ADS / DWS / DWD               │
└──────────▲───────────────────────────────────▲───────────────────┘
           │ 实时写入                           │ 批量写入
┌──────────┴──────────────┐        ┌───────────┴───────────────────┐
│   实时计算链路 (Speed)    │        │     离线计算链路 (Batch)        │
│   Kafka → Flink          │        │   Spark → Iceberg → Airflow   │
└──────────▲──────────────┘        └───────────▲───────────────────┘
           │                                   │
┌──────────┴───────────────────────────────────┴───────────────────┐
│                        存储层 (Storage)                           │
│         HDFS (Hive 元数据/明细)   MinIO (S3 兼容湖仓存储)           │
└─────────────────────────────────▲────────────────────────────────┘
                                  │
┌─────────────────────────────────┴────────────────────────────────┐
│                        数据源层 (Source)                          │
│   MySQL (user/product/orders/payment/refund) + Python 数据生成器   │
└──────────────────────────────────────────────────────────────────┘

治理与可观测：数据质量校验  |  Prometheus + Grafana
```

### 2.2 Sprint 0 实际架构

```text
                    Docker network: data-platform
                                │
     ┌──────────────┬───────────┼───────────┬──────────────┐
     ▼              ▼           ▼           ▼              ▼
  ┌──────┐     ┌────────┐   ┌───────┐  ┌────────┐    ┌────────┐
  │MySQL │     │ Kafka  │   │ MinIO │  │Doris FE│───►│Doris BE│
  │:3306 │     │ :9092  │   │ :9000 │  │ :9030  │    │ :8040  │
  └───▲──┘     └───▲────┘   └───▲───┘  └────────┘    └────────┘
      │            │            │
      │            │            │
  ┌───┴────────────┴────────────┴───┐
  │   Python Data Generator          │
  │   ├── 业务数据 ──► MySQL           │
  │   └── 实时事件 ──► Kafka           │
  └──────────────────────────────────┘
```

---

## 3. 技术栈

### 3.1 Sprint 0 已使用

| 层次 | 组件 | 版本 | 说明 |
| --- | --- | --- | --- |
| 容器 | Docker / Docker Compose | Compose v5.5.1+ | 统一编排 |
| 数据源 | MySQL | `8.4.11` (LTS) | 电商业务库 |
| 消息 | Apache Kafka | `4.2.1` (KRaft) | 事件总线，无需 ZooKeeper |
| 对象存储 | MinIO | `RELEASE.2025-10-15T17-29-55Z` | S3 兼容湖仓存储 |
| OLAP | Apache Doris | `4.1.4` (FE + BE) | 统一查询与指标服务 |
| 语言 | Python | 3.13 | 数据生成器 |
| 库 | Faker / mysql-connector-python / confluent-kafka | 见 `requirements.txt` | 数据生成与写入 |
| 测试 | pytest | 9.x | 单元 + 冒烟测试 |
| 脚本 | Bash | — | 启停、状态、健康检查 |

> 版本选型依据与 digest 记录见 [`docs/development-environment.md`](docs/development-environment.md)。

### 3.2 后续 Sprint 引入

Apache Flink（S1）、Apache Spark + Hive + HDFS（S2）、Iceberg（S5）、
Airflow（S4）、FastAPI / Spring Boot / Vue（S6）、LLM + Tool Calling（S7）、
LangGraph（S8）、RAG（S9）、MCP（S10）、Prometheus + Grafana（S11）。

---

## 4. 环境要求

### 4.1 最低要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 11（WSL2 后端）/ Linux / macOS |
| 容器运行时 | **Docker Desktop**（或 Docker Engine + Compose v2） |
| 内存 | **≥ 8 GB**（推荐 16 GB；Doris FE/BE 较吃内存） |
| 磁盘 | **≥ 20 GB 可用**（镜像约 5 GB + 运行数据） |
| Python | 3.10+（推荐 3.13，与镜像一致） |
| Git | 2.30+ |

### 4.2 Windows 用户（重要）

本项目在 Windows + WSL2 下开发，**Bash 脚本必须通过 WSL 或 Git Bash 执行**。

#### 步骤 1：安装 Docker Desktop

```powershell
winget install --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
```

> ⚠️ Docker Desktop 需要**管理员权限**安装，且**通常需要重启系统**才能启用
> WSL2 / VirtualMachinePlatform 组件。
>
> `--scope user` **不受支持**：Docker 的安装包只提供 machine scope，
> 使用时会报 `0x8A150010 找不到适用的安装程序`。

#### 步骤 2：首次启动

1. 重启系统。
2. 启动 **Docker Desktop**，接受服务条款。
3. 确保设置为 **Use WSL 2 based engine**。
4. 等待左下角显示 **Engine running**。

验证：

```powershell
docker --version
docker compose version
docker info
```

如果提示 `docker: 无法将"docker"项识别为 cmdlet`，把以下路径加入 PATH：

```text
C:\Program Files\Docker\Docker\resources\bin
```

#### 步骤 3：选择 Bash 环境

| 方式 | 说明 |
| --- | --- |
| **Git Bash**（推荐，最轻量） | 安装 Git for Windows 后自带，直接运行 `bash scripts/xxx.sh` |
| **WSL2 + Ubuntu** | `wsl --install -d Ubuntu`，在 WSL 内可访问 `/mnt/c/...` 下的项目 |

> **磁盘空间提示**：Docker 镜像默认存储在系统盘（C:）。
> 若 C 盘空间紧张，可在 Docker Desktop → Settings → Resources →
> Disk image location 中改到其他磁盘。

#### PowerShell 等价命令

无法使用 Bash 时，可用以下等价命令：

```powershell
Copy-Item .env.example .env
docker compose up -d
docker compose ps
docker compose logs -f
docker compose down
```

> 健康检查脚本 `health-check.sh` 目前没有 PowerShell 版本，
> 请通过 Git Bash 执行：`bash scripts/health-check.sh`

---

## 5. 目录结构

```text
intelligent-data-platform/
├── README.md                      项目说明（本文件）
├── AGENTS.md                      人机协作与编码规范
├── docker-compose.yml             Sprint 0 全部服务编排
├── .env.example                   环境变量模板（可提交）
├── .env                           本地环境变量（已 gitignore，禁止提交）
├── .gitignore / .gitattributes    忽略规则 / 行尾统一
├── pytest.ini                     测试配置
│
├── docs/
│   ├── PROJECT_DESIGN_V1.md       总体架构基线
│   ├── development-environment.md 环境、镜像版本与选型依据
│   └── sprint/
│       └── SPRINT_0.md            Sprint 0 任务书
│
├── infrastructure/                各组件容器初始化配置
│   ├── mysql/init/                （schema 由 sql/mysql 挂载）
│   ├── kafka/init/
│   │   └── 01-create-topics.sh    创建 4 个 Topic
│   ├── minio/init/
│   │   └── 01-create-bucket.sh    创建 lakehouse bucket
│   └── doris/
│
├── sql/                           所有 DDL 必须进 Git
│   ├── mysql/01_schema.sql        ecommerce 库与 5 张业务表
│   ├── doris/02_doris_init.sql    ecommerce 库与 test_connection 表
│   └── metadata/kafka_topics.md   Topic 与事件格式定义
│
├── data-generator/                Python 数据生成器
│   ├── src/                       生成逻辑
│   ├── state/                     运行时快照（不入 Git）
│   ├── requirements.txt
│   ├── Dockerfile
│   └── README.md
│
├── scripts/
│   ├── start.sh                   启动服务
│   ├── stop.sh                    停止服务
│   ├── status.sh                  查看状态
│   ├── health-check.sh            健康检查
│   └── lib/common.sh              共用函数
│
├── tests/
│   ├── test_data_generator.py     24 个单元测试（无需容器）
│   └── smoke/test_infrastructure.py  27 个冒烟测试（需容器）
│
└── volumes/                       本地绑定挂载点占位
```

---

## 6. 快速启动

### 6.1 标准流程

```bash
# 1. 准备环境变量
cp .env.example .env

# 2. 启动全部服务
bash scripts/start.sh
#    等价于： docker compose up -d

# 3. 查看状态
bash scripts/status.sh
#    等价于： docker compose ps

# 4. 健康检查
bash scripts/health-check.sh
```

> ⚠️ **Doris 首次启动较慢**（需初始化元数据并注册 BE），
> 通常需要 1~3 分钟。若健康检查显示 Doris 未就绪，请等待后重试。

### 6.2 期望输出

```text
=====================================
 Data Platform Health Check
=====================================

[OK] MySQL
[OK] Kafka
[OK] MinIO
[OK] Doris FE
[OK] Doris BE

=====================================
 All services are healthy
=====================================
```

### 6.3 不使用脚本

```bash
docker compose config      # 校验编排文件语法
docker compose up -d       # 启动
docker compose ps          # 查看状态
docker compose down        # 停止（保留数据）
```

---

## 7. 服务端口

| 服务 | 容器内地址 | 宿主机访问 | 用途 |
| --- | --- | --- | --- |
| MySQL | `mysql:3306` | `localhost:3306` | MySQL 协议 |
| Kafka | **`kafka:9092`** | `localhost:19092` | 事件总线（见下方说明） |
| MinIO API | `minio:9000` | `http://localhost:9000` | S3 API |
| MinIO 控制台 | `minio:9001` | `http://localhost:9001` | Web 管理界面 |
| Doris FE | `doris-fe:9030` | `localhost:9030` | MySQL 协议查询入口 |
| Doris FE Web | `doris-fe:8030` | `http://localhost:8030` | FE 管理界面 |
| Doris BE Web | `doris-be:8040` | `http://localhost:8040` | BE 状态 |

### 7.1 Kafka 连接方式（重要）

Kafka 使用**双监听器**，因为容器与宿主机能解析的地址不同：

| 场景 | Bootstrap Server | 说明 |
| --- | --- | --- |
| **容器之间**（推荐） | `kafka:9092` | 符合项目网络规范，禁止用 localhost |
| **宿主机上的 Python / CLI** | `localhost:19092` | 外部监听器 |

> Sprint 0 的数据生成器在容器内运行，因此 `.env` 中的
> `KAFKA_BOOTSTRAP_SERVERS` 默认是 `localhost:19092`（宿主机模式），
> 而 `docker-compose.yml` 会为容器显式注入 `kafka:9092`。

### 7.2 网络规范

所有服务加入统一网络 **`data-platform`**。
**容器之间禁止使用 `localhost` / `127.0.0.1`**，必须使用服务名。

唯一例外：Doris FE/BE 必须使用固定 IP（`172.28.0.10` / `172.28.0.11`），
因为 Doris 的初始化脚本用正则严格校验 `FE_SERVERS=<name>:<IPv4>:<port>`，
**不接受主机名**。详见
[`docs/development-environment.md`](docs/development-environment.md) 第 4.5 节。

---

## 8. 账号与密码说明

**仓库中不含任何真实凭据。** 所有口令通过 `.env` 注入，`.env` 已在 `.gitignore` 中。

| 服务 | 变量 | `.env.example` 默认值 |
| --- | --- | --- |
| MySQL root | `MYSQL_ROOT_PASSWORD` | `change_me_root` |
| MySQL 业务账号 | `MYSQL_USER` / `MYSQL_PASSWORD` | `app` / `change_me_app` |
| MinIO | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | `minio` / `change_me_minio` |
| Doris root | `DORIS_ROOT_PASSWORD` | `change_me_doris` |

```bash
cp .env.example .env
# 然后编辑 .env，把所有 change_me_* 改成自己的口令
```

> Doris 默认 `root` 空密码。本项目在初始化 SQL 中为其设置口令（取自 `.env`）。
> 若为本地纯离线开发，也可保持空密码，但**不要**把带真实口令的 `.env` 提交到 Git。

---

## 9. 停止服务

```bash
bash scripts/stop.sh
# 等价于： docker compose down
```

**默认保留数据卷**，下次启动后数据仍在。

### 完全重置（会删除全部数据）

```bash
bash scripts/stop.sh --volumes
# 等价于： docker compose down -v
```

> 该命令需要输入 `YES` 二次确认。
> MySQL / Kafka / MinIO / Doris 的数据将**全部丢失且不可恢复**。

---

## 10. 查看状态

```bash
bash scripts/status.sh
```

输出容器状态、镜像版本、数据卷与端口映射。等价于 `docker compose ps --all`。

---

## 11. 健康检查

```bash
bash scripts/health-check.sh
```

检查 5 个核心服务，任一失败则 `exit 1`（可用于 CI 或部署门禁）：

| 检查项 | 检查方式 |
| --- | --- |
| MySQL | `mysqladmin ping` + 确认 `ecommerce` 库存在 |
| Kafka | `kafka-broker-api-versions.sh` + 确认 4 个 Topic 存在 |
| MinIO | `/minio/health/live` + 确认 `lakehouse` bucket 存在 |
| Doris FE | FE HTTP 接口（8030）与 MySQL 协议（9030） |
| Doris BE | 通过 FE 执行 `SHOW BACKENDS`，确认 BE 已注册且 `Alive=true` |

> 所有检查都在**容器内部**执行，因此不要求宿主机安装 mysql / kafka 客户端。

退出码：

```text
0  全部健康
1  存在不健康服务
```

---

## 12. 数据生成

数据生成器位于 `data-generator/`，提供两个入口：

```bash
cd data-generator

# 1. 生成 MySQL 业务数据
python -m src.generate_mysql_data

# 2. 生成 Kafka 实时事件
python -m src.generate_events
```

### 12.1 通过 Docker 运行（推荐）

无需在宿主机安装 Python 依赖：

```bash
docker compose run --rm data-generator python -m src.generate_mysql_data
docker compose run --rm data-generator python -m src.generate_events
```

### 12.2 在宿主机运行

```bash
cd data-generator
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
# source .venv/bin/activate        # Linux / WSL / macOS
pip install -r requirements.txt

python -m src.generate_mysql_data
python -m src.generate_events
```

> 宿主机运行需要 `.env` 中的 `MYSQL_HOST=localhost`、`KAFKA_BOOTSTRAP_SERVERS=localhost:19092`。

### 12.3 生成规模（默认）

| 数据 | 数量 | Sprint 0 要求 |
| --- | --- | --- |
| 用户 | 1200 | 1000+ ✅ |
| 商品 | 600 | 500+ ✅ |
| 订单 | 6000 | 5000+ ✅ |
| 支付 | 约 5400 | 5000+ ✅ |
| 退款 | 约 250（约 5% 的已支付订单） | 少量 ✅ |

规模与比例可在 `.env` 中调整（`GEN_USER_COUNT` / `GEN_ORDER_COUNT` / `GEN_REFUND_RATIO` 等）。

### 12.4 常用参数

```bash
# 先清空再生成
python -m src.generate_mysql_data --reset

# 自定义规模与随机种子（保证可复现）
python -m src.generate_mysql_data --users 2000 --orders 10000 --seed 42

# 持续向 Kafka 发送事件
python -m src.generate_events --continuous

# 只发某几个 Topic
python -m src.generate_events --topics order_event,payment_event

# 限速 500 条/秒
python -m src.generate_events --rate 500
```

### 12.5 数据一致性保证

生成器**不是完全随机**，严格遵守业务关系：

```text
orders.user_id     ∈ user.user_id
orders.product_id  ∈ product.product_id
payment.order_id   ∈ orders.order_id
refund.order_id    ∈ orders.order_id
product.price × orders.quantity ≈ orders.amount   （按概率模拟优惠）
user.register_time ≤ orders.create_time ≤ orders.pay_time ≤ refund.refund_time
refund.refund_amount ≤ orders.amount
```

此外，`generate_events` 从 `state/dataset_snapshot.json` 读取**真实写入 MySQL 的数据**，
保证 Kafka 事件与 MySQL 记录一一对应，而不是另生成一套不一致的数据。

---

## 13. 数据与事件模型

### 13.1 MySQL 业务表

```text
user ──1:N──► orders ──1:1──► payment
                 │
                 └──1:N──► refund

product ──1:N──► orders
```

字段定义见 [`sql/mysql/01_schema.sql`](sql/mysql/01_schema.sql)。

### 13.2 Kafka Topic

| Topic | 分区 | 副本 | 语义 |
| --- | --- | --- | --- |
| `order_event` | 3 | 1 | 订单创建 / 状态变化 |
| `payment_event` | 3 | 1 | 支付成功 / 失败 |
| `refund_event` | 3 | 1 | 退款事件 |
| `behavior_event` | 3 | 1 | 浏览 / 点击 / 加购 / 收藏 / 购买 |

事件 JSON 格式与字段定义见
[`sql/metadata/kafka_topics.md`](sql/metadata/kafka_topics.md)。

分区键：订单类事件用 `order_id`，行为事件用 `user_id`（保证分区内有序）。

### 13.3 手工查看数据

```bash
# 查看 Topic 列表
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --list

# 消费 behavior_event
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic behavior_event \
  --from-beginning --max-messages 5

# 查询 MySQL
docker compose exec mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" ecommerce \
  -e "SELECT COUNT(*) AS users FROM user; SELECT COUNT(*) AS orders FROM orders;"

# 查询 Doris
docker compose exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot \
  -e "SELECT * FROM ecommerce.test_connection;"

# MinIO 控制台
# 浏览器打开 http://localhost:9001
```

---

## 14. 测试

```bash
# 全部测试
python -m pytest

# 只跑单元测试（不需要容器，验证生成器业务逻辑）
python -m pytest -m unit

# 只跑冒烟测试（需要 docker compose up -d 且服务已就绪）
python -m pytest -m smoke -v
```

### 14.1 单元测试（24 个，无需容器）

覆盖生成器的业务正确性：外键关系、金额逻辑、时间因果、
行为漏斗单调性、确定性（同种子同结果）。

### 14.2 冒烟测试（27 个，需要容器）

对应 SPRINT_0.md 第 19 节的 10 项要求，**真实执行**而非检查文件存在：

```text
1.  MySQL 可以连接（mysqladmin ping + 版本检查）
2.  ecommerce 数据库存在
3.  5 张业务表存在，且关键字段齐全
4.  Kafka Topic 存在（并校验分区数 = 3、副本 = 1）
5.  Kafka 可以生产消息
6.  Kafka 可以消费消息（端到端 Producer → Kafka → Consumer）
7.  MinIO Bucket 存在（通过 mc 校验）
8.  Doris 可以连接
9.  Doris 可以建表（建表后校验并清理）
10. Doris 可以查询测试数据（test_connection）
```

额外校验：Doris BE 已注册且 Alive、`default_replication_num = 1`、
业务账号 `app` 可访问 `ecommerce`。

> 未启动容器时，冒烟测试会**跳过**（不会误报失败）。

---

## 15. 常见问题

### Q1. `docker: command not found` / 无法识别 docker

Docker Desktop 未安装，或未加入 PATH。

```powershell
winget install --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
```

安装后**需要重启**，然后启动 Docker Desktop。
若仍找不到命令，把 `C:\Program Files\Docker\Docker\resources\bin` 加入 PATH。

### Q2. `无法连接 Docker daemon` / `open //./pipe/docker_engine: The system cannot find the file specified`

Docker Desktop 没有运行。启动它并等待显示 **Engine running**。

若启动后仍报错且系统提示需要重启：

```text
安装 Docker Desktop 时会启用 WSL2 / VirtualMachinePlatform 组件，
必须重启系统后这些组件才生效。请重启后重新启动 Docker Desktop。
```

### Q3. `wsl -l -v` 报 `REGDB_E_CLASSNOTREG`

WSL 组件尚未启用（需重启），或系统未启用虚拟化。
请确认 BIOS 中已开启虚拟化（VT-x / AMD-V），重启后再试。

### Q4. 健康检查显示 `[FAIL] Doris FE` / `[FAIL] Doris BE`

Doris 首次启动需要初始化元数据，**通常要 1~3 分钟**。请：

```bash
# 等待后重试
sleep 60 && bash scripts/health-check.sh

# 查看日志定位
docker compose logs --tail=100 doris-fe
docker compose logs --tail=100 doris-be
```

常见原因：

- BE 尚未完成向 FE 注册 —— 等待后重试；
- 内存不足 —— 在 `.env` 中调小 `DORIS_FE_JAVA_OPTS` 与 `DORIS_BE_MEM_LIMIT`；
- 子网 `172.28.0.0/24` 与已有网络冲突 —— 在 `.env` 中修改
  `DATA_PLATFORM_SUBNET` / `DORIS_FE_IP` / `DORIS_BE_IP` 后重建。

### Q5. 端口被占用（3306 / 9092 / 9000 / 9030 等）

修改 `.env` 中对应的宿主端口变量，例如：

```env
MYSQL_HOST_PORT=3307
DORIS_FE_QUERY_PORT=9031
```

然后重新 `bash scripts/start.sh`。

### Q6. MySQL 初始化 SQL 没有执行 / 表不存在

初始化脚本**只在数据目录为空时执行一次**。若需重新初始化：

```bash
bash scripts/stop.sh --volumes    # 删除数据卷（会丢失数据！）
bash scripts/start.sh
```

### Q7. 修改了 SQL 但数据没变化

同上 —— 必须清空对应数据卷后重建才会重新执行初始化脚本。

### Q8. `docker compose` 报变量未设置（如 `MYSQL_PASSWORD is required`）

没有 `.env` 文件。执行：

```bash
cp .env.example .env
```

### Q9. Windows 下脚本报 `$'\r': command not found`

脚本被检出为 CRLF 行尾。本项目已通过 `.gitattributes` 强制 `.sh` 使用 LF。
若本地仍异常：

```bash
git rm --cached -r . && git reset --hard
```

### Q10. 拉取 MinIO 镜像失败（`minio/minio not found`）

MinIO 自 2025-10 起**停止免费分发官方 Docker 镜像**，Docker Hub 上
`minio/minio`、`minio/mc` 均已下架（404），官方下载站返回 410。

本项目使用社区再托管的**最后一个官方发行版**：
`coollabsio/minio:RELEASE.2025-10-15T17-29-55Z`。

供应链说明与替代方案见
[`docs/development-environment.md`](docs/development-environment.md) 第 4.3 节。

### Q11. 磁盘空间不足

Doris BE 镜像约 2.9 GB、FE 约 1.6 GB，全部镜像合计约 5 GB。

```bash
docker system df            # 查看占用
docker image prune -a       # 清理未使用镜像
```

或在 Docker Desktop → Settings → Resources 中把
**Disk image location** 改到空间更大的磁盘。

---

## 16. 后续 Roadmap

```text
Sprint 0  ✅ 基础环境（已在腾讯云服务器验收通过）
             MySQL / Kafka / MinIO / Doris / 数据生成器 / 脚本 / 测试
   ↓
Sprint 1  ✅ Kafka → Flink → Doris 实时数仓
             实时 GMV / 订单量 / 支付 / 退款 / UV-PV / 类目销售
             8 个 Flink 作业 + 8 个 Routine Load，指标与 MySQL 精确对账
   ↓
Sprint 6  ✅ 数据后台 + 前后端（**顺序前移**，先让数据可访问）
             Nginx + FastAPI 只读 API + Vue 看板，访问 http://<ip>/data/
   ↓
Sprint 2  ✅ 离线链路：Spark + Hive + 湖仓存储（MinIO/S3A，HDFS 因内存不足暂缓）
             MySQL → Parquet(S3A) → Hive 外部表 ods_*，逐表与 MySQL 精确对账
   ↓
Sprint 3     ODS / DWD / DWS / ADS 分层建模（与实时指标交叉对账）
   ↓
Sprint 4     Airflow 调度
   ↓
Sprint 5     Iceberg Lakehouse
   ↓
Sprint 7     LLM + Tool Calling
   ↓
Sprint 8     LangGraph Data Agent
   ↓
Sprint 9     RAG + Metadata
   ↓
Sprint 10    MCP
   ↓
Sprint 11    Data Quality + Monitoring
   ↓
Sprint 12    测试 + 性能优化
   ↓
Sprint 13    毕业论文 + 答辩
```

**下一步：Sprint 3 —— 离线分层建模（ODS → DWD → DWS → ADS）并与实时指标交叉对账。**
Sprint 2 已经把业务全量搬进湖仓（`lakehouse.ods_*`，逐表与 MySQL 精确一致），
Sprint 3 将在其上建 DWD/DWS/ADS，产出与实时链路**同名同口径**的指标，
用第二套引擎验证实时指标的正确性 —— 这正是"批流一体"的价值所在。

一键验收：

```bash
bash scripts/verify-sprint-1.sh     # 实时数仓（Kafka → Flink → Doris）
bash scripts/verify-sprint-2.sh     # 离线链路（MySQL → Spark → 湖仓 → Hive）
bash scripts/verify-sprint-6.sh     # 数据服务与前端（Nginx + API + 对账 + 安全）
```

---

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 项目规范、编码规范、Agent 安全规范 |
| [`docs/PROJECT_DESIGN_V1.md`](docs/PROJECT_DESIGN_V1.md) | 总体架构基线（V1） |
| [`docs/development-environment.md`](docs/development-environment.md) | 环境实测、镜像版本与选型依据 |
| [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md) | 开发日志（每个阶段做了什么、踩了什么坑） |
| [`docs/data-source-design.md`](docs/data-source-design.md) | 数据来源设计与后续演进 |
| [`docs/sprint/SPRINT_0.md`](docs/sprint/SPRINT_0.md) | Sprint 0 任务书 |
| [`docs/sprint/SPRINT_0_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_0_VERIFICATION_STATUS.md) | Sprint 0 逐项验证状态（已实测 / 待执行） |
| [`docs/sprint/SPRINT_1.md`](docs/sprint/SPRINT_1.md) | Sprint 1 设计（含实现偏差记录） |
| [`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_1_VERIFICATION_STATUS.md) | Sprint 1 逐项验证状态与证据 |
| [`docs/sprint/SPRINT_2.md`](docs/sprint/SPRINT_2.md) | Sprint 2 设计：离线链路（含 11 条踩坑记录） |
| [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md) | Sprint 6 设计：数据后台 + 前后端（服务层） |
| [`services/api/README.md`](services/api/README.md) | 数据服务说明（接口、安全、部署） |
| [`services/web/README.md`](services/web/README.md) | 前端看板说明（无构建步骤、部署、空值约定） |
| [`sql/metadata/metrics.md`](sql/metadata/metrics.md) | **指标口径字典（唯一权威）** |
| [`sql/metadata/kafka_topics.md`](sql/metadata/kafka_topics.md) | Topic 与事件格式定义 |
| [`data-generator/README.md`](data-generator/README.md) | 数据生成器使用说明 |
