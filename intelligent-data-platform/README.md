# 基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台

> 面向电商场景的**批流一体**数据平台，最终包含实时链路（Kafka → Flink → Doris）、
> 离线链路（Spark → Iceberg on MinIO/S3A → Hive）、以及基于 **LangGraph + LLM + MCP**
> 的智能数据分析 Agent。
>
> **当前进度：Sprint 0 ~ 12 已在腾讯云服务器上完成并验收通过**（逐项见 §16 Roadmap
> 与 [`AGENTS.md`](AGENTS.md) §15 状态表）。其中 Sprint 4 Airflow 调度与流量域归档、
> Sprint 5 Iceberg 湖仓 + 流量域分层、Sprint 8/9/10 图式 Agent / 词法检索 / MCP、
> Sprint 11 数据质量与监控、Sprint 12 测试与性能基线均已落地（Sprint 5 尚留
> **1 项未归零**的实时侧缺陷，见 §16 后的说明，不隐藏）。
> **Sprint 13（毕业论文 + 答辩）的性质是 Prove + Audit + Close**，材料已成稿，
> 见 [`docs/sprint/SPRINT_13.md`](docs/sprint/SPRINT_13.md) 与 [`docs/thesis/`](docs/thesis/)。
>
> 核心原则：**先工程，再智能。**
> 数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据。

> 🖥 **在线数据大屏**：<http://36.151.150.140/data/>
> 数据问答 Agent：<http://36.151.150.140/data/#/ask>
> 接口文档：<http://36.151.150.140/data/api/docs> ｜
> Agent 文档：<http://36.151.150.140/data/agent/docs>
> 调度（Airflow）：<http://36.151.150.140/airflow/>
>
> 架构：`浏览器 → Nginx(:80) → /data/ 静态看板 + /data/api/ 只读数据服务 + /data/agent/ 问答 Agent + /airflow/ 调度 UI → Doris`
> 数据服务使用**专用只读账号**（`agent_ro`）+ SQL 安全守卫（仅 SELECT、强制 LIMIT），
> 指标与 MySQL 事实源精确对账（GMV 51,890,375.77 元，精确到分；
> 注意口径边界：可加指标的逐窗口求和才是全量口径，**去重指标不可跨窗口相加**，见 §17.2）。
> 看板含**实时链路**与**离线链路**两套视角，并展示两者的逐窗口对账结论
> （11458 个分钟窗口，不一致 0）。
> **Agent 进程里没有数据库凭据** —— 它只能经只读数据服务取数，
> 回答附"用了哪些表 + 实际执行的 SQL"，可逐条核对。
>
> ⚠️ **当前形态是明文 HTTP，没有启用 TLS**（有意为之，不是漏配）。
> 站点按 IP 访问，受信任的 CA 不为裸 IP 签发证书，只能自签 ——
> 而自签证书会让浏览器每个会话都弹一次"继续前往"，演示观感不好。
> 因此决定后续注册域名 + 申请证书 + 完成备案之后再启用。
>
> 这个决定有实测依据：Sprint 7 期间曾出现约 40% 的请求返回**空 502**
> （该 502 无 `Server` 响应头，而 nginx 自己发的 502 必带
> `Server: nginx/1.24.0` 与 HTML 错误页，所以不是我们的服务发的）。
> 定位到中间设备改写明文响应后一度启用过 HTTPS 规避。
> 但后续实测发现：**关掉客户端 VPN 代理之后，明文 HTTP 30/30 全部正常** ——
> 改写发生在 VPN 的出口路径上，不在这条 IP 直连路径上。
> 所以当前**按实测**走明文，**前提是演示时不要挂 VPN / 代理**
> （这是那次实测的结论，不是对任意网络环境的保证）。
> 完整排查过程见 [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md) 第 8 节。

> ✅ **Sprint 0 ~ 12 验收结果（摘要）**（腾讯云 36.151.150.140 / Ubuntu 24.04.2 LTS；
> 逐项见 §16 Roadmap 与 [`AGENTS.md`](AGENTS.md) §15 状态表）
>
> ```text
> ✅ Sprint 0  docker compose up -d 5 个核心服务 healthy；health-check 5/5；pytest 51 passed
> ✅ Sprint 1  Flink 8 作业 + Routine Load 8/8 RUNNING；health-check 11/11；
>              pytest 74 passed；ADS 与 MySQL 精确对账（GMV 精确到分）
> ✅ Sprint 2  Spark + Hive 离线链路；verify-sprint-2.sh 8/8 PASS；
>              ODS 5 张表逐表与 MySQL 精确一致（1200/600/6000/5406/254）
> ✅ Sprint 3  ODS→DWD→DWS→ADS 四层 + 批流交叉对账；verify-sprint-3.sh 8/8 PASS；
>              11458 个分钟窗口逐条比对，不一致 0
> ✅ Sprint 6  Nginx + systemd 直装上线；/data/ 看板可访问；API GMV == MySQL GMV；
>              只读账号写操作被 Doris 拒绝；pytest 74 单元 + 21 接口冒烟
> ✅ Sprint 7  数据问答 Agent；verify-sprint-7.sh 49/49 通过；
>              四类攻击全拒、强制 LIMIT、端到端三问正确；公网明文 HTTP 30/30 正常
>              （关掉 VPN/代理后实测；当前站点不监听 443，见 §7.3 与 §16 后的"站点协议"）
> ✅ Sprint 4  Airflow 3.3.2 调度 + 流量域归档；verify-sprint-4.sh 40/0/0；
>              归档 20000 行 == Kafka latest offset 合计
> ✅ Sprint 5  Iceberg 23 张表 70/70；流量域 DWD 21/21、DWS 20/20、ADS 36/36、对账 10/10
>              （19643 个窗口不一致 0；**1 项未归零**：实时侧 1 个窗口 click_rate 自相矛盾）
> ✅ Sprint 8  LangGraph 图式编排；verify-sprint-8.sh 71/0/0
> ✅ Sprint 9  元数据/口径检索（BM25 + 显式同义词表，未引入向量库）；verify-sprint-9.sh 59/0/0
> ✅ Sprint 10 MCP；verify-sprint-10.sh 84/0/0；MCP 与直接 HTTP 路径逐字段 IDENTICAL
> ✅ Sprint 11 数据质量 + 监控；verify-sprint-11.sh 61/0/0
> ✅ Sprint 12 测试 + 性能基线；verify-sprint-12.sh 32/0/0；全量 pytest 353 passed / 0 failed
>              （性能口径与边界见 [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md)：单机、
>               单请求、回环、热缓存，**未做任何并发/压力测试**）
> ```
>
> 逐项证据见
> [`docs/sprint/SPRINT_0_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_0_VERIFICATION_STATUS.md)、
> [`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_1_VERIFICATION_STATUS.md)、
> [`docs/sprint/SPRINT_2.md`](docs/sprint/SPRINT_2.md)、
> [`docs/sprint/SPRINT_3.md`](docs/sprint/SPRINT_3.md)、
> [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md)、
> [`docs/sprint/SPRINT_7.md`](docs/sprint/SPRINT_7.md)。

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
- [17. 收口状态与站点协议](#17-收口状态与站点协议)
- [数据质量校验与监控（Sprint 11）](#数据质量校验与监控sprint-11)
- [文档索引](#文档索引)

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
- **Spark + Iceberg on MinIO/S3A** 做离线计算与湖仓存储
  （原计划的 **HDFS 因内存不足改用 S3A**，见 Sprint 2 与 `AGENTS.md` §15 状态表）；
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
✅ 已实测   docker compose up -d（5 个核心服务 healthy）/ health-check.sh（5/5）/
            27 个冒烟测试 —— Sprint 0 已验收，逐项证据见
            docs/sprint/SPRINT_0_VERIFICATION_STATUS.md
```

> 上表是 **Sprint 0 时点的验证快照**（24 单元 / 27 冒烟）。当前全量测试
> 已是 **353 passed / 0 failed / 3 xfailed**（Sprint 12 服务器实测，见 §14）。

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
│   MinIO/S3A (湖仓存储 + Hive Metastore 元数据)   Doris (服务层指标)     │
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
| 语言（宿主机） | Python | **3.12.3** | 数据服务 / Agent / Airflow（宿主 venv） |
| 语言（容器） | Python | **3.13.14** | 仅 data-generator 容器（`python:3.13.14-slim-bookworm`） |
| 库 | Faker / mysql-connector-python / confluent-kafka | 见 `requirements.txt` | 数据生成与写入 |
| 测试 | pytest | 9.x | 单元 + 冒烟测试 |
| 脚本 | Bash | — | 启停、状态、健康检查 |

> 版本选型依据与 digest 记录见 [`docs/development-environment.md`](docs/development-environment.md)。

### 3.2 Sprint 1 ~ 11 引入的组件（**均已落地并验收**，不再是"后续计划"）

Apache Flink（S1）、Apache Spark + Hive（S2，湖仓存储用 **MinIO/S3A**；
原计划的 HDFS 因内存不足改用 S3A）、Airflow（S4）、Iceberg（S5）、
FastAPI + Vue（S6）、LLM + Tool Calling（S7）、LangGraph（S8）、
词法检索 RAG（S9，**未引入向量库**）、MCP（S10）、Prometheus + Grafana（S11）。

> 原本节写作"后续 Sprint 引入"，是 Sprint 0 时期的计划表 ——
> 现在这些组件已全部落地，保留本节是为了给出**引入顺序**，
> 当前版本与用途见 [`AGENTS.md`](AGENTS.md) §2.1。

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

### 7.3 服务层端口（宿主机直装，不进 Docker）

Sprint 6 起，**服务层**（Nginx + FastAPI + 前端静态文件 + 问答 Agent）
直接装在宿主机上，用 systemd 守护：

| 服务 | 监听地址 | 对外 | 用途 |
| --- | --- | --- | --- |
| Nginx (HTTP) | `0.0.0.0:80` | ✅ 公网 | **唯一业务入口**：`http://<ip>/data/`（当前**未启用 TLS**，见文首说明与 §16 后的"站点协议"） |
| Nginx (HTTPS) | `0.0.0.0:443` | ❌ **未监听** | 已按实测停用；443 配置与 `scripts/setup-tls.sh` 保留在仓库，等注册域名并备案后按 commit `347dfdd` 恢复 |
| 数据服务 | `127.0.0.1:8000` | ❌ 仅回环 | FastAPI 只读数据服务（`systemd: data-platform-api`） |
| 问答 Agent | `127.0.0.1:8100` | ❌ 仅回环 | FastAPI + LLM 工具调用（`systemd: data-platform-agent`） |
| MCP 服务 | `127.0.0.1:8200` | ❌ 仅回环 | MCP streamable-http 传输（`systemd: data-platform-mcp`，Sprint 10） |
| Prometheus | `127.0.0.1:9090` | ❌ 仅回环 | 指标采集，公网经 `/metrics/` 反代（Sprint 11） |
| Grafana | `127.0.0.1:3001` | ❌ 仅回环 | 监控面板，公网经 `/grafana/` 反代（Sprint 11） |

> **为什么 8000 / 8100 只绑回环**：它们只应由 Nginx 反代访问。
> 直接暴露到公网既绕过了 Nginx 的方法限制（`limit_except`），
> 也让"Agent 进程无数据库凭据、只能经只读服务取数"这道进程边界失去意义。

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

此外，`generate_events` 从 `state/dataset_snapshot.json` 读取**由生成器实际写入 MySQL 的那批数据**，
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

### 14.1 单元测试（Sprint 0 时点：24 个，无需容器）

覆盖生成器的业务正确性：外键关系、金额逻辑、时间因果、
行为漏斗单调性、确定性（同种子同结果）。

### 14.2 冒烟测试（Sprint 0 时点：27 个，需要容器）

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

### 14.3 当前全量测试（Sprint 12 服务器实测）

```text
python -m pytest  →  **353 passed / 0 failed / 0 skipped / 3 xfailed**
```

- 本节 §14.1 / §14.2 的 24 / 27 是 **Sprint 0 时点的快照**，不是当前数量；
  Sprint 1~12 各自都加了用例（实时链路、离线分层与对账、API 守卫、Agent、
  MCP、SQL 守卫对抗用例等）。
- 早期文档里出现过的 `191 passed / 138 skipped` 是**本机未启动 Docker 时**的历史值
  （138 skipped 全是"本机 Docker 未运行"的冒烟用例按 §8.3 优雅跳过），
  与 353 不是同一环境下的同一个数，引用时**必须带环境**。
- "353 全绿"是**环境恢复后**的结果：同一天同一份测试曾依次出现
  `306/47 skipped`、`331/22 skipped`、`346/7 failed`，全部是环境中间态所致
  —— 完整序列与解释见 [`AGENTS.md`](AGENTS.md) §15.12。

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
             （期间曾启用自签 TLS 规避公网偶发 502，后按实测停用，见 SPRINT_6.md 第 8.6 节）
   ↓
Sprint 2  ✅ 离线链路：Spark + Hive + 湖仓存储（MinIO/S3A，HDFS 因内存不足暂缓）
             MySQL → Parquet(S3A) → Hive 外部表 ods_*，逐表与 MySQL 精确对账
   ↓
Sprint 3  ✅ 离线分层建模 ODS → DWD → DWS → ADS + **批流交叉对账**
             Spark 分层计算 → Parquet on S3A → Doris S3() TVF 装载
             11458 个分钟窗口逐条比对：不一致 0，GMV 两条链路均为 51,890,375.77
   ↓
Sprint 7  ✅ LLM + Tool Calling：**数据问答 Agent**
             自然语言 → 查口径 → 生成 SQL → 过守卫 → 只读执行 → 带来源的回答
             Agent 进程无数据库凭据；回答附"用了哪些表 + 实际执行的 SQL"可核对
             验收 49/49；实测三问（日 GMV / 支付成功率与退款率 / 数据准不准）全部正确
   ↓
Sprint 4  ✅ Airflow 调度 + 流量域归档
             宿主机 + systemd 三单元（apiserver/scheduler/dagprocessor）
             元数据库用 MySQL（不引入 PostgreSQL）；UI 在 /airflow/
             DAG：暂停实时链路 → 逐层批处理 → 对账 → 装载 → 恢复（all_done）
              归档 20000 行 == Kafka latest offset 合计（**本次验收窗口内未观察到丢失**：20000 == 20000，703 个 dt 分区；该判据不覆盖验收之后的增量事件）；验收 40/0/0
   ↓
Sprint 5  ✅ Iceberg Lakehouse + 流量域分层（验收通过；剩一处实时侧缺陷**已登记未修**）
              18 张交易域表 → Iceberg（**阶段 4 首次迁移的校验为 60/60**），随后扩容到 **23 张**
             （23 张表那次复跑校验为 **70/70**；两次范围不同，不要混读成一个判据的两次结果）
              流量域离线分层：DWD 20000 行 / DWS 703 天 / ADS 19644 窗口
              **流量域逐窗口对账：19643/19643 个窗口，不一致 0、单边窗口 0**
             ⚠️ 未归零项（**不隐藏**）：实时侧 1 个窗口的 click_rate 与自身计数矛盾
                （view_cnt=2 / click_cnt=1 / click_rate 记 0.0000，按口径应为 0.5000；
                 离线侧同窗口为 0.5000）。它**不阻断对账作业退出码**（实时侧 anomaly
                 只登记、不参与 is_pass），修复需重部署 Flink 作业并重放，待决策 ——
                 见 metrics.md 第 3.3 节与 SPRINT_5.md 第 10 节
   ↓
Sprint 8  ✅ LangGraph Data Agent：**图式编排**
              检索 → 规划 → 取数 → 校验 →（反思 → 重规划）→ 汇总
              规划与校验是**显式产物**；重试次数可数；三重上界（规划/反思/步数）
              使图在代码上**必然终止**（上界取自 `GET /graph`，可逐项核对）
              仍然**只有一条取数通道**（POST /query + SQL 守卫 + 只读账号）
   ↓
Sprint 9  ✅ RAG + Metadata：**元数据/口径检索（词法后端）**
              BM25 + 显式同义词表（"卖了多少钱"→GMV），语料 = 指标口径 + 表结构 + 分层说明
              回答同时给出 docs（命中的口径来源，带文件与行号）/ tables / executed_sql
              **不引入向量库**（无 embeddings 端点、本机放不下本地模型，见 DECISIONS ⏳7）
   ↓
Sprint 10 ✅ MCP：**只读数据能力经 MCP 暴露（能力最小化）**
              4 个只读工具（metrics_lookup / tables_lookup / sql_query / reconciliation）
              = 只读接口的一对一映射；**MCP 进程里没有数据库凭据**（系统单元不读 .env）
              两种传输：streamable-http（部署，127.0.0.1:8200）/ stdio（本地宿主）
              Agent 侧新增取数路径 `AGENT_DATA_PATH=mcp`：与直连**终点相同**、**不自动回退**
              `mcp==2.2.0`（钉死；PyPI + 服务器双重查证，见 development-environment §3.3）
              ✅ 服务器端验收 **84/0/0**（含"MCP ↔ 直接 HTTP 逐字段一致"实证：
                 同一 SQL 两条路径的 rows 逐字段 IDENTICAL、executed_sql 相同，
                 证据见 AGENTS.md §15.12）
   ↓
Sprint 11 ✅ Data Quality + Monitoring
              数据质量：25 条校验（行数/主键/非空/枚举/层间一致/金额/新鲜度），
                        清单+引擎结构，**失败真的 exit 1**（有可复现的 --proof-fail 证明）
              监控：Prometheus v3.13.3（LTS）+ Grafana 13.2.2，内存上限 256m+256m=512m
                    Grafana 面板 23 个，数据源指向真实 Doris（只读账号 agent_ro）+ Prometheus
                    公网：http://<ip>/grafana/  与  http://<ip>/metrics/
   ↓
Sprint 12 ✅ 测试 + 性能：**基线**（已实测，数字已补）
              测试：补齐 MCP 单元测试（28 条）与 SQL 守卫**对抗**用例（60 条 + 3 条 strict xfail）
              性能：四类基线采集脚本 `scripts/perf/measure-latency.sh`（只读，含内存闸门）
              `docs/PERFORMANCE.md` 四类基线**已实测**：只读接口点查 8.5 ms / 聚合 8.3 ms /
                 关联 11.7 ms（采集时维表为空、关联未真的发生，同口径重跑约 7.4 ms）/ overview 53.8 ms；
                 实时 vs 离线"无可测量差异"；批量 9 任务 1992.3 s（端到端约 81 分钟，含 48 分钟重试等待）；
                 `/ask` 中位数 19187.5 ms，其中 LLM 占 99.9%（**n=2**）
              ⚠️ 口径边界：单机 4 核/16 GB、回环、单请求串行、热缓存，**未做任何并发/压力测试**，
                 不构成生产高并发证明；`/ask` 不构成数据平台的查询性能
              结构债（两份阶段 `case`）：**未做**，原因与方案写在 SPRINT_12.md 第 4 节（不阻塞验收）
   ↓
Sprint 13    🔄 毕业论文 + 答辩（性质：**Prove + Audit + Close**，禁止新增组件）
             材料已成稿：报告正文 / 大纲 / PPT 逐页大纲 / 讲稿 / 问答准备 /
             外部审查包 / 证据矩阵 / 终审报告 / Word 终稿（见 docs/thesis/）
             逐项见 [`docs/sprint/SPRINT_13.md`](docs/sprint/SPRINT_13.md)
```

---

## 17. 收口状态与站点协议

> 本节收编原先散在 §16 code fence 之后、**没有归属章节**的几段（下一步与历史时点值、
> 批流对账口径、一键验收、离线重跑与 Agent 运维、站点协议）。
> 它们过去因为**没有章节锚点**而长期停留在旧状态 ——
> 这正是"过期内容能留在文件里、且改别处时想不起来顺手更新"的机制性原因。

### 17.1 下一步与历史时点值（**不要当当前值引用**）

**下一步：Sprint 13 论文与答辩材料定稿**（材料已在
[`docs/thesis/`](docs/thesis/)，终稿待答辩前定稿）。
Sprint 10 与 Sprint 12 的服务器端收口**均已完成**：
MCP 端到端实证 84/0/0（MCP ↔ 直接 HTTP 逐字段 IDENTICAL），
全量 pytest 与四类性能基线均已实测（见 §14.3 与 §16 的 Sprint 12 段）。

> **历史时点值，不要当当前值引用**：本节早期版本写过
> "下一步：Sprint 10 与 Sprint 12 的服务器端收口"与
> `pytest` **191 passed / 138 skipped / 3 xfailed** —— 那是**本机未启动 Docker** 时
> 的历史值（138 skipped 全是冒烟用例按 §8.3 优雅跳过），
> 与服务器实测的 **353 passed / 0 failed / 3 xfailed** 不是同一环境下的同一个数。
### 17.2 批流对账口径（当前值，可当场核对）

Sprint 8/9 已经把"问数"这条链路做成了**可审查的图**与**可追溯的来源**：
每次提问都会返回规划、校验结论、重试次数、命中的口径文档与实际执行的 SQL。

```text
交易域  11458 个分钟窗口，不一致 0，GMV 两条链路均为 51,890,375.77
        （口径：两侧数据范围求交并尾部留 3 分钟安全边界后的对账区间内；
          逐窗口比对 7 个可加指标 + 1 个去重指标 order_user_cnt（两侧同为近似去重），
          缺失侧补 0 后比较）
流量域  19643 个分钟窗口，不一致 0，单边窗口 0
        （判据：uv / pv / 5 个行为计数，共 7 列；比率列只留证、不参与 is_match）
        ⚠️ 未归零项（不隐藏）：实时侧 1 个窗口的 click_rate 与自身计数矛盾
           （view_cnt=2 / click_cnt=1 / click_rate 记 0.0000，按口径应为 0.5000；
            离线侧同窗口 0.5000）。该 anomaly **不阻断对账作业退出码**
            （实时侧只登记、不参与 is_pass），修复需重部署 Flink 作业并重放，
            待项目负责人决策，见 metrics.md 第 3.3 节与 SPRINT_5.md 第 10 节
```

> **结论边界（引用时必须一起写）**：证据只覆盖**对账区间之内**（尾部安全边界**明确未比**）；
> 交易域**没有**"仅实时/仅离线窗口 = 0"的断言（只有"实时侧 ≥ 离线侧 × 90%"），
> 因此**不能**写成"两侧窗口集合完全相同"；`is_match` 是**补零后数值相等**，
> `NULL` 与真实 `0` 不可区分，故该结论的强度部分来自数据分布
> （交易域 11458 个窗口中 **5504 个两侧 GMV 均为 0（48.03%，平凡一致）**，
> 承载判别力的是其余 **5954 个有非零业务量的窗口**）；
> 交易域的派生比率列**完全没有进对账**，流量域的比率列**不进 `is_match`**
> （实时侧 anomaly 只登记、不参与 `is_pass`，故"不一致 0"与"实时侧仍有 1 个已知缺陷"同时成立）；
> 去重指标仅支持逐窗口比较、**不支持跨窗口上卷**；全部结论建立在**合成数据集**上。
>
> **行数口径（防误读）**：实时 DWD 的"行为 20000 行"是 **distinct `event_id` 的基数**，
> **不等于链路处理量** —— 该 topic 的 `latest` 合计为 **580000 = 29 × 20000**
> （Flink 作业被重放 29 轮），交易域三表同理约 29 倍（174000 / 156774 / 7366），
> Doris 侧 `UNIQUE KEY` + merge-on-write 把重复主键 upsert 覆盖后才等于表内行数。
> 因此**不能**说"实时链路只处理了 20000 条事件"，也**不能**把 `event_id` 当全局唯一键
> （`evt_420000` 是生成器**进程内序号**，不等于 42 万条消息）。

### 17.3 一键验收

```bash
bash scripts/verify-sprint-0.sh     # Sprint 0 全链路（config+up+ps+health+pytest）
bash scripts/verify-sprint-1.sh     # 实时数仓（Kafka → Flink → Doris）
bash scripts/verify-sprint-2.sh     # 离线链路（MySQL → Spark → 湖仓 → Hive）
bash scripts/verify-sprint-3.sh     # 离线分层 + 批流交叉对账（8 步）
bash scripts/verify-sprint-4.sh     # Airflow 调度 + 流量域归档（8 步）
bash scripts/verify-sprint-5.sh     # Iceberg Lakehouse + 流量域分层与对账（8 步）
bash scripts/verify-sprint-6.sh     # 数据服务与前端（Nginx + API + 对账 + 安全）
bash scripts/verify-sprint-7.sh     # 数据问答 Agent（守卫 + 工具 + 端到端问答）
bash scripts/verify-sprint-8.sh     # 图式编排（图结构 + 端到端 + **反思重试** + 安全回归）
bash scripts/verify-sprint-9.sh     # 元数据/口径检索（语料 + **同义词生效** + 无向量库）
bash scripts/verify-sprint-10.sh    # MCP（4 个只读工具 + 两路径逐字段一致 + 不回退）
bash scripts/verify-sprint-11.sh    # 数据质量 + 监控（8 步：容器/版本/内存/Prometheus/Grafana/质量/失败路径/Nginx）
bash scripts/verify-sprint-12.sh    # 测试 + 性能基线（32/0/0）
```

> 共 **13 个** `verify-sprint-*.sh`（与 13 个 Sprint 编号对应：0~12；
> 每个脚本的步骤数不同，不要用"8 步"去概括全部）。
> 三个 Agent 验收脚本的关系：7 验"基础问答与安全边界"，8 验"编排与重试"，
> 9 验"检索与来源"。**8 与 9 都必须以 7 的 49/49 不回退为前提**。

### 17.4 离线链路重跑与 Agent 运维

离线链路重跑（内存受限，走错峰模式）：

```bash
bash scripts/batch-mode.sh          # 暂停实时链路 → 跑完整离线流水线 → 恢复并自检
```

Agent 部署与配置：

```bash
bash scripts/deploy-agent.sh        # 部署（幂等；会一并重启数据服务）
# 配置 LLM Key： 在 .env 设置 LLM_API_KEY 后 systemctl restart data-platform-agent
# 详见 services/agent/README.md
```

Agent 自检与交互（Sprint 8/9 新增的接口）：

```bash
curl -s http://127.0.0.1:8100/graph   | python3 -m json.tool  # 图长什么样（节点/边/上界）
curl -s http://127.0.0.1:8100/retrieval | python3 -m json.tool  # 语料与检索后端画像
# 检索单独可测（同义词是否生效一眼可见）
curl -s --get http://127.0.0.1:8100/api/retrieve \
     --data-urlencode 'q=最近一周卖了多少钱' --data-urlencode 'k=3' | python3 -m json.tool
```

### 17.5 站点协议（**当前：明文 HTTP；TLS 已按实测暂停用**）

**当前形态**：单一 HTTP 站点（`:80`），全部业务直接提供，**不监听 443**。
443 的配置与 `scripts/setup-tls.sh` 保留在仓库，需要时按 commit `347dfdd` 恢复。
协议由 `.env` 的 `SITE_SCHEME` 决定（当前 `http`），见 `AGENTS.md` §15.7 硬规范 1。

```bash
grep -E '^SITE_SCHEME=' .env                 # 当前值 http
# 启用 TLS 时（等注册域名并备案之后）：
#   sudo SERVER_IP=<服务器IP> bash scripts/setup-tls.sh   # 生成自签证书（幂等）
#   bash scripts/deploy-web.sh                            # 安装配置并 reload Nginx
#   ! 重新启用时注意 http2 写法随 nginx 版本变化：本项目钉 1.24.0，
#     必须写 `listen 443 ssl http2;`（`http2 on;` 是 1.25.1+ 语法，会让 nginx -t 失败）
```

> ⚠️ **公网偶发 502 的结论已被修正（不要照抄"上 HTTPS 就解决了"）**：
> 当时观察到约 15~40% 的请求返回 502，而服务器 nginx 访问日志里**没有任何 502**、
> `ListenOverflows` 为 0、服务端自测全部 200。
> 关键判据是那个 502 **不带 `Server` 响应头、响应体为空** ——
> nginx **在默认配置下**发出的 502 必定带 `Server: nginx/1.24.0` 和一段 HTML 错误页，
> 所以它**不是我们的服务发的**，而是明文 HTTP 在传输途中被中间设备改写。
> 当时上 HTTPS 后现象确实消失（实测公网 **64/64 全部 200**），
> 但那是**另一条路径上的旁证**，不足以支持"必须上 TLS"。
> **真正的定位是补做的那一步**：关掉客户端 VPN 代理后重新测，
> **明文 HTTP 30/30 全部正常** —— 改写的中间设备在 **VPN 的出口路径上**，
> 不在这条 IP 直连路径上。
> 因此当前按实测走明文，**前提是演示时不要挂 VPN / 代理**。
> 完整判据见 [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md) 第 8 节与
> [`AGENTS.md`](AGENTS.md) §15.7。
> **备用手段（仅在临时启用 TLS 时可用，当前 443 未监听，这条命令现在会失败）**：
> `ssh -N -L 18080:127.0.0.1:443 root@<ip>`，然后访问 `https://127.0.0.1:18080/data/`。

### 17.6 交付物清单（Sprint 13）

```text
docs/thesis/REPORT_DRAFT.md          论文正文草稿（含摘要 / 正文 / 不足）
docs/thesis/REPORT_OUTLINE.md        章节大纲
docs/thesis/DEFENSE_SLIDES.md        答辩 PPT 逐页大纲
docs/thesis/DEFENSE_SCRIPT.md        答辩讲稿（带时间轴）
docs/thesis/QA_PREP.md               答辩问答准备
docs/thesis/DATA_AND_LIMITATIONS.md  数据来源与已知限制
docs/thesis/外部审查包.md             外部审查包（主张 / 证据 / 边界对照）
docs/thesis/EVIDENCE_MATRIX.md        证据矩阵（主张 → 证据 → 结论分类）
docs/thesis/FINAL_AUDIT_REPORT.md     终审报告（P0/P1/P2 + 未决项）
docs/thesis/毕业论文_*.docx            Word 终稿（由 md_to_thesis_docx.py 导出）
docs/sprint/SPRINT_13.md             本 Sprint 的任务书与结算
```

---

## 数据质量校验与监控（Sprint 11）

### 数据质量校验

```bash
bash scripts/run-quality-checks.sh              # 全部 Doris 侧校验（快，不启 Spark，可随时跑）
bash scripts/run-quality-checks.sh --with-lake  # 追加湖仓 Iceberg 层间校验（需 ≈1 GB 空闲内存）
bash scripts/run-quality-checks.sh --list       # 只看校验清单（不连数据库）
bash scripts/run-quality-checks.sh --proof-fail # **失败路径演示**：证明失败真的 exit 1
```

退出码：`0` 通过 / `1` 有校验失败 / `2` 湖仓侧被闸门**拒绝**（内存不足或检测到批处理在跑）。

校验清单在 [`infrastructure/quality/checks.conf`](infrastructure/quality/checks.conf)（25 条），
覆盖 7 类：行数非空守卫、主键唯一、关键字段非空、枚举合法、层间一致、金额关系、新鲜度；
**交易域与流量域都覆盖**。每条都会打印 `[OK]/[FAIL]` 与**实测值**。

调度接法（**未改 `run-batch-pipeline.sh`**，理由见
[`docs/sprint/SPRINT_11.md`](docs/sprint/SPRINT_11.md) 第 6 节）：

```bash
# 批处理之后接一步
bash scripts/batch-mode.sh && bash scripts/run-quality-checks.sh --with-lake
# 或每天巡检
0 4 * * * cd /opt/data-platform && bash scripts/run-quality-checks.sh >> /var/log/dp-quality.log 2>&1
```

### 监控

```bash
bash scripts/deploy-monitoring.sh        # 部署（幂等；7 步，含 Nginx 反代）
bash scripts/deploy-monitoring.sh --recreate   # 改了 provisioning 文件后强制重建
bash scripts/verify-sprint-11.sh         # 验收（8 步）
docker compose logs -f prometheus        # 抓取日志
docker compose logs -f grafana           # 面板日志
docker stats --no-stream prometheus grafana    # 实测内存占用（预算 512 MB）
```

| 入口 | 地址 | 说明 |
| --- | --- | --- |
| Grafana 面板 | http://36.151.150.140/grafana/ | 23 个面板；匿名只读浏览，改配置需登录 |
| Prometheus 指标 | http://36.151.150.140/metrics/ | 原始指标（Nginx 只放行 GET/HEAD/OPTIONS） |
| Prometheus（仅本机） | http://127.0.0.1:9090/ | 端口只绑回环，公网不可达 |
| Grafana（仅本机） | http://127.0.0.1:3001/ | 同上 |

镜像版本（**禁止 latest**）：`prom/prometheus:v3.13.3`（LTS 线，支持到 2027-07-31）、
`grafana/grafana:13.2.2`。每个容器的 `mem_limit` 都是 `256m`，合计 512 MB ——
本机实时链路常驻约 13.7 GB，**这个预算不要调大**。

> ⚠️ 面板默认时间范围是 `now-90d`：本机数据是 `data-generator` **一次性生成**的历史数据，
> 默认用 6h/24h 窗口会看到空面板（那是时间范围问题，不是面板坏了）。

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
| [`docs/sprint/SPRINT_3.md`](docs/sprint/SPRINT_3.md) | Sprint 3 设计：离线分层建模 + 批流交叉对账（含 10 条踩坑与内存事故记录） |
| [`docs/sprint/SPRINT_4.md`](docs/sprint/SPRINT_4.md) | Sprint 4 设计：Airflow 调度 + 流量域归档（含 10 条踩坑与 5 个系统性缺陷） |
| [`docs/sprint/SPRINT_5.md`](docs/sprint/SPRINT_5.md) | Sprint 5 设计：Iceberg Lakehouse + 流量域分层与对账（含 1 项未归零缺陷的待决策方案） |
| [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md) | Sprint 6 设计：数据后台 + 前后端（服务层） |
| [`docs/sprint/SPRINT_7.md`](docs/sprint/SPRINT_7.md) | Sprint 7 设计：LLM + Tool Calling 数据问答 Agent（含 7 条踩坑） |
| [`docs/sprint/SPRINT_8.md`](docs/sprint/SPRINT_8.md) | Sprint 8 设计：LangGraph 图式编排（节点/边/上界与端到端实证） |
| [`docs/sprint/SPRINT_9.md`](docs/sprint/SPRINT_9.md) | Sprint 9 设计：元数据/口径检索（词法方案依据与同义词表） |
| [`docs/sprint/SPRINT_10.md`](docs/sprint/SPRINT_10.md) | Sprint 10 设计：MCP（4 个只读工具、两种传输、两路径一致性实证） |
| [`docs/sprint/SPRINT_11.md`](docs/sprint/SPRINT_11.md) | Sprint 11 设计：数据质量 + 监控（版本选型、内存预算、9 条踩坑） |
| [`docs/sprint/SPRINT_12.md`](docs/sprint/SPRINT_12.md) | Sprint 12 设计：测试 + 性能基线（含 `.env` 事故复盘与四类基线口径） |
| [`docs/sprint/SPRINT_13.md`](docs/sprint/SPRINT_13.md) | **Sprint 13 性质与收口范围（Prove + Audit + Close；审计线清单与 P0/P1/P2 分级）** |
| [`infrastructure/quality/checks.conf`](infrastructure/quality/checks.conf) | **数据质量校验清单（25 条，含阈值依据）** |
| [`services/agent/knowledge/synonyms.json`](services/agent/knowledge/synonyms.json) | **检索同义词表（每条都带理由，可增删可测试）** |
| [`services/api/README.md`](services/api/README.md) | 数据服务说明（接口、安全、部署） |
| [`services/agent/README.md`](services/agent/README.md) | **Agent 配置说明（申请 Key、写入、验证、排查）** |
| [`services/web/README.md`](services/web/README.md) | 前端看板说明（无构建步骤、部署、空值约定） |
| [`sql/metadata/metrics.md`](sql/metadata/metrics.md) | **指标口径字典（唯一权威）** |
| [`sql/metadata/kafka_topics.md`](sql/metadata/kafka_topics.md) | Topic 与事件格式定义 |
| [`docs/thesis/`](docs/thesis/) | **毕业设计报告与答辩材料**（大纲、正文草稿、PPT 逐页大纲、讲稿、问答准备、数据来源与已知限制） |
| [`data-generator/README.md`](data-generator/README.md) | 数据生成器使用说明 |
