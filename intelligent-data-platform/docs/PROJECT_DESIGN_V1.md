# 基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台 — 总体设计 V1

> 文档版本：V1.0
> 状态：**架构基线（Architecture Baseline）**
> 适用范围：Sprint 0 ~ Sprint 13
> 重要约束：本文档描述**最终目标架构**。任何单个 Sprint 只实现其中被该 Sprint 任务书明确授权的部分，
> **不得因为本文档提到了某个组件就提前实现它。**

---

## 1. 项目背景与目标

### 1.1 背景

电商业务的数据天然同时具备两种形态：

| 形态 | 特征 | 典型诉求 |
| --- | --- | --- |
| 实时流 | 点击、加购、下单、支付、退款等事件持续产生 | 实时 GMV、实时订单量、实时在线人数、风控 |
| 批量离线 | 日/月级别的经营分析、用户画像、商品分析 | 准确、可回溯、口径统一 |

传统做法是把实时链路和离线链路分成两套系统（Lambda 架构），代价是**同一指标两份实现、口径不一致、维护成本翻倍**。

本项目要构建的是**批流一体（Kappa/Lakehouse 融合）**的数据平台：

- 用 **Kafka** 作为统一的事件入口；
- 用 **Flink** 做实时计算，写入 **Doris** 提供亚秒级查询；
- 用 **Spark + Iceberg on HDFS/S3** 做离线计算与湖仓存储，保证 ACID 与时间旅行；
- 用 **Airflow** 编排批处理；
- 最终在 **Doris** 之上暴露统一指标层；
- 用 **LLM + LangGraph + Tool Calling + MCP** 构建数据分析 Agent，让业务人员用自然语言问数。

### 1.2 核心目标

1. **工程优先**：先把数据链路做可靠、准确、可查询、可治理，再让 Agent 使用数据。
2. **批流一体**：实时与离线共用一套分层模型（ODS/DWD/DWS/ADS）与统一指标口径。
3. **可被 Agent 消费**：元数据、指标定义、表结构必须结构化沉淀，供 Agent 检索与生成 SQL。
4. **可验证**：每一步都有健康检查、冒烟测试与文档，做到可运行、可验证、可回滚。

### 1.3 核心原则

> **先工程，再智能。**

```text
数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据
```

明确**反对**的路径：

```text
先做聊天机器人 → 再想办法找数据
```

---

## 2. 总体架构

### 2.1 分层架构图

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                       应用与智能层 (Application & AI)                     │
│                                                                          │
│   Vue Dashboard        FastAPI / Spring Boot 后端                         │
│        │                        │                                        │
│        └────────────┬───────────┘                                        │
│                     ▼                                                    │
│          LangGraph Data Agent  ◄──► LLM (Tool Calling)                    │
│                     │                                                    │
│                     ├── MCP Server (元数据 / 指标 / 查询工具)               │
│                     └── RAG (指标定义、表结构、业务口径检索)                  │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │  只读 SQL (默认仅 SELECT)
┌──────────────────────────────────▼───────────────────────────────────────┐
│                          统一查询与指标层 (Serving)                        │
│                                                                          │
│   Apache Doris (FE + BE)                                                  │
│     ├── ADS  应用层：指标结果表（实时 GMV / 订单 / 用户）                     │
│     ├── DWS  汇总层：轻度汇总（人/货/场）                                    │
│     └── DWD  明细层：清洗后的明细宽表                                        │
└───────────────▲──────────────────────────────▲───────────────────────────┘
                │ 实时写入                      │ 批量写入
┌───────────────┴───────────────┐  ┌───────────┴───────────────────────────┐
│      实时计算链路 (Speed)      │  │        离线计算链路 (Batch)            │
│                               │  │                                       │
│  Kafka  ──►  Flink             │  │  Spark  ──►  Iceberg                  │
│  (事件)      (窗口/聚合/去重)    │  │  (ETL)       (湖仓表格式)              │
│                               │  │      ▲                                │
│                               │  │      │  编排                            │
│                               │  │  Airflow                              │
└───────────────▲───────────────┘  └───────────▲───────────────────────────┘
                │                              │
                │                        ┌─────┴──────────────────────────┐
                │                        │  存储层 (Storage)               │
                │                        │  HDFS (Hive 元数据 / 明细)       │
                │                        │  MinIO (S3 兼容：warehouse/      │
                │                        │         metadata/checkpoint/    │
                │                        │         archive)               │
                │                        └─────┬──────────────────────────┘
                │                              │
┌───────────────┴──────────────────────────────┴───────────────────────────┐
│                          数据源层 (Source)                                │
│                                                                          │
│   MySQL (ecommerce: user / product / orders / payment / refund)           │
│        │                                                                 │
│        ├── CDC / 批量抽取 ──► Kafka / 离线链路                            │
│        └── 业务行为埋点 ──► Kafka (behavior_event)                        │
│                                                                          │
│   Python Data Generator (业务数据 + 实时事件)                              │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                     治理与可观测层 (Governance & Observability)            │
│   数据质量 (规则校验 / 对账)      Prometheus + Grafana (监控告警)           │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流向（端到端）

```text
1. Python Data Generator
     ├── 写入 MySQL 业务库 (user/product/orders/payment/refund)
     └── 写入 Kafka 事件 (order_event/payment_event/refund_event/behavior_event)

2. 实时链路
     Kafka ──► Flink ──► Doris (DWD/DWS/ADS)  ──► 秒级指标

3. 离线链路
     MySQL/Kafka ──► Spark ──► Iceberg (on MinIO/HDFS) ──► Hive 元数据
                              └──► Doris (批量导入，口径对齐)

4. 服务链路
     Doris ──► FastAPI/Spring Boot ──► Vue Dashboard

5. 智能链路
     自然语言 ──► LangGraph Agent
                   ├── 指标识别 (RAG: 指标定义)
                   ├── Schema 检索 (MCP: 元数据服务)
                   ├── SQL 生成
                   ├── SQL 安全检查 (默认只允许 SELECT)
                   ├── Doris 执行
                   └── 结果分析 ──► 自然语言回答 + 数据来源说明
```

---

## 3. 技术栈

> 版本策略：**不使用 `latest`**，全部使用明确版本或版本摘要（digest）。
> 实际落地版本记录在 `docs/development-environment.md`。

| 层次 | 组件 | 用途 | 引入 Sprint |
| --- | --- | --- | --- |
| 数据源 | MySQL 8.4 LTS | 电商业务库 | **Sprint 0** |
| 消息 | Apache Kafka (KRaft) | 统一事件总线 | **Sprint 0** |
| 对象存储 | MinIO | S3 兼容湖仓存储 | **Sprint 0** |
| OLAP | Apache Doris (FE + BE) | 统一查询与指标服务 | **Sprint 0** |
| 工具 | Python 3.13 + Faker | 数据生成器 | **Sprint 0** |
| 实时计算 | Apache Flink | 流式 ETL 与聚合 | Sprint 1 |
| 离线计算 | Apache Spark | 批处理 ETL | Sprint 2 |
| 存储 | HDFS | 分布式文件系统 | Sprint 2 |
| 元数据 | Hive Metastore | 表元数据管理 | Sprint 2 |
| 湖仓格式 | Apache Iceberg | ACID / 时间旅行 / Schema 演进 | Sprint 5 |
| 编排 | Apache Airflow | 批处理调度 | Sprint 4 |
| 后端 | FastAPI | 指标与 Agent 服务接口 | Sprint 6 / 7 |
| 后端 | Spring Boot | 业务服务（可选） | Sprint 6 |
| 前端 | Vue 3 | 可视化 Dashboard | Sprint 6 |
| Agent | LangGraph | 数据分析 Agent 编排 | Sprint 8 |
| LLM | 大语言模型 | 意图理解 / SQL 生成 / 结果解释 | Sprint 7 |
| 工具协议 | MCP | 标准化工具与元数据暴露 | Sprint 10 |
| 检索 | RAG（向量检索） | 指标定义与文档检索 | Sprint 9 |
| 质量 | 数据质量规则引擎 | 完整性 / 一致性 / 及时性校验 | Sprint 11 |
| 监控 | Prometheus + Grafana | 指标采集与告警看板 | Sprint 11 |

### 3.1 明确不在 Sprint 0 范围内的组件

Hadoop / HDFS / Hive / Spark / Flink / Airflow / Iceberg / LangGraph / RAG / MCP / Vue /
Spring Boot / FastAPI / Prometheus / Grafana / Kubernetes / Redis / Elasticsearch /
ClickHouse / Trino / Milvus / PostgreSQL。

**Sprint 0 不实现、不引入、不为它们预建目录。**

---

## 4. 数仓分层与数据模型

### 4.1 分层规范

| 分层 | 全称 | 职责 | 示例 |
| --- | --- | --- | --- |
| ODS | Operational Data Store | 贴源层，与源系统结构一致，只做落地不做清洗 | `ods_order` |
| DWD | Data Warehouse Detail | 明细层，清洗、去重、维度补全、统一命名 | `dwd_trade_order_detail` |
| DWS | Data Warehouse Summary | 汇总层，按主题轻度聚合 | `dws_trade_user_1d` |
| ADS | Application Data Store | 应用层，直接面向指标与报表 | `ads_realtime_gmv` |

规定：

- 分层前缀必须体现在表名上。
- 同一指标只能有一个权威定义，定义沉淀在 `sql/metadata/`（Sprint 3 起）。
- 实时与离线必须能对齐到同一分层与同一口径。

### 4.2 命名规范

```text
数据库/库名 : 小写下划线          ecommerce
表名        : 小写下划线 + 分层前缀  dwd_trade_order_detail
字段名      : 小写下划线          order_id / create_time
主键        : <实体>_id           user_id / order_id / payment_id
时间字段    : create_time / update_time / <动作>_time
```

### 4.3 Sprint 0 业务模型（源系统，MySQL）

Sprint 0 只建立**贴源业务表**（相当于未来的 ODS 来源），不建立 DWD/DWS/ADS。

```text
user ──1:N──► orders ──1:1──► payment
                 │
                 └──1:N──► refund

product ──1:N──► orders
```

| 表 | 主键 | 关键外键 | 说明 |
| --- | --- | --- | --- |
| `user` | `user_id` | — | 用户主数据 |
| `product` | `product_id` | — | 商品主数据 |
| `orders` | `order_id` | `user_id` → `user`, `product_id` → `product` | 订单事实 |
| `payment` | `payment_id` | `order_id` → `orders`, `user_id` → `user` | 支付事实 |
| `refund` | `refund_id` | `order_id` → `orders`, `user_id` → `user` | 退款事实 |

> 注意：`orders` / `payment` / `refund` 中的 `user_id` 为**冗余维度**，
> 便于下游流式计算免 join，需与 `orders.user_id` 保持一致。

---

## 5. 事件模型（Kafka）

统一 JSON 编码，统一字段命名，统一时区 `Asia/Shanghai`（ISO-8601 带偏移）。

### 5.1 Topic 规划

| Topic | 分区 | 副本 | 语义 | 关键事件类型 |
| --- | --- | --- | --- | --- |
| `order_event` | 3 | 1 | 订单创建与状态变化 | `ORDER_CREATED` |
| `payment_event` | 3 | 1 | 支付成功 / 失败 | `PAYMENT_SUCCESS` |
| `refund_event` | 3 | 1 | 退款事件 | `REFUND_CREATED` |
| `behavior_event` | 3 | 1 | 用户行为埋点 | `VIEW` / `CLICK` / `CART` / `FAVORITE` / `BUY` |

Sprint 0 为单机开发环境，`replication.factor = 1`，不追求高可用。

### 5.2 公共信封字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `event_id` | string | 是 | 全局唯一事件 ID，用于幂等去重 |
| `event_type` | string | 是 | 事件类型枚举 |
| `event_time` | string | 是 | 事件发生时间，ISO-8601 带时区 |

### 5.3 分区键（Partitioning）

```text
order_event     → key = order_id
payment_event   → key = order_id
refund_event    → key = order_id
behavior_event  → key = user_id
```

保证同一订单/同一用户的事件落到同一分区，从而保证**分区内有序**。

---

## 6. 存储规划

### 6.1 MinIO Bucket 布局

```text
lakehouse/
├── warehouse/     Iceberg / Hive 表数据与元数据文件
├── metadata/      表结构、指标定义等元数据资产
├── checkpoint/    Flink 状态快照与 savepoint
└── archive/       历史归档、冷数据
```

> Sprint 0 **只创建 bucket 并验证 API 可用**，不创建上述前缀目录、不接入 Iceberg。

### 6.2 HDFS 规划（Sprint 2 起）

```text
/user/hive/warehouse/     Hive 数仓目录
/user/spark/              Spark 作业与日志
/user/iceberg/            Iceberg warehouse
```

---

## 7. Agent 架构（Sprint 7 起）

### 7.1 处理流程

```text
自然语言问题
   ↓  问题理解（LLM）
   ↓  指标识别（RAG 检索指标定义）
   ↓  元数据检索（MCP 提供表结构 / 血缘）
   ↓  SQL 生成（LLM + Few-shot）
   ↓  SQL 安全检查（只读校验 / 危险语法拦截 / 权限校验）
   ↓  Doris 查询（只读账号）
   ↓  结果分析（LLM）
   ↓  自然语言回答 + 数据来源说明
```

### 7.2 Agent 安全规范（强制）

```text
1. Agent 不允许直接拥有数据库管理员权限。
2. Agent SQL 默认只允许 SELECT。
3. Agent 必须知道指标定义。
4. Agent 必须知道表结构。
5. Agent 生成 SQL 后必须进行安全检查。
6. Agent 查询失败必须能够重新规划。
7. Agent 的最终回答必须能够说明数据来源。
8. Agent 不得伪造查询结果。
```

具体约束：

- 连接 Doris 使用**独立只读账号**，不复用 `root`。
- SQL 守卫必须拒绝：`INSERT` / `UPDATE` / `DELETE` / `DROP` / `TRUNCATE` / `ALTER` /
  `CREATE` / `GRANT` / `REVOKE` / 多语句 / 注释注入。
- 强制 `LIMIT`，限制扫描行数与超时时间。
- 查询失败必须返回真实错误，**禁止回退到编造数据**。

---

## 8. Sprint 规划（Roadmap）

| Sprint | 主题 | 关键交付 |
| --- | --- | --- |
| **0** | **项目初始化与基础数据环境** | **MySQL / Kafka / MinIO / Doris + 数据生成器 + 脚本 + 文档** |
| 1 | Kafka + Flink + Doris 实时数仓 | 实时 GMV / 实时订单 / 实时用户数 / Dashboard 接口 |
| 2 | Spark + Hive + HDFS | 离线计算与元数据基础 |
| 3 | ODS / DWD / DWS / ADS | 分层建模与统一指标口径 |
| 4 | Airflow | 批处理调度与依赖编排 |
| 5 | Iceberg Lakehouse | 湖仓一体、ACID、时间旅行 |
| 6 | Backend + Dashboard | FastAPI / Spring Boot + Vue |
| 7 | LLM + Tool Calling | 自然语言转 SQL 基础能力 |
| 8 | LangGraph Data Agent | 多步推理 Agent |
| 9 | RAG + Metadata | 指标与元数据检索增强 |
| 10 | MCP | 标准化工具与元数据协议 |
| 11 | Data Quality + Monitoring | 质量规则 + Prometheus/Grafana |
| 12 | 测试 + 性能优化 | 端到端测试与压测 |
| 13 | 毕业论文 + 答辩 | 论文与答辩材料 |

---

## 9. 工程规范

### 9.1 目录职责

| 目录 | 职责 |
| --- | --- |
| `docs/` | 设计、环境、Sprint 任务与验收文档 |
| `infrastructure/` | 各组件的 Docker 初始化与配置文件 |
| `sql/` | DDL / DML / 元数据定义（必须进 Git） |
| `scripts/` | 启停、状态、健康检查脚本 |
| `data-generator/` | Python 数据生成器 |
| `tests/` | 冒烟测试与集成测试 |
| `volumes/` | 本地绑定挂载点（内容不入 Git） |

### 9.2 关键约束

1. **无 Secret 进 Git**：口令、Token、API Key 一律通过 `.env` 注入，`.env` 必须在 `.gitignore` 中。
2. **镜像版本固定**：禁止 `latest`，使用明确版本，必要时使用 digest。
3. **统一网络**：所有服务加入 `data-platform` 网络，容器间使用**服务名**互访，禁止 `localhost` / `127.0.0.1`。
4. **持久化**：数据库数据必须使用命名卷，容器重建后数据保留。
5. **初始化 SQL 独立存放并进 Git**：不允许手工修改容器内部数据库结构。
6. **健康检查必须区分「容器启动」与「服务就绪」**，通过 `healthcheck` + `start_period` + 重试解决时序问题。
7. **先理解 → 再规划 → 小步实现 → 测试 → 验证 → 文档 → Git Commit**。

### 9.3 架构变更流程

任何涉及架构的变更必须：

```text
说明问题 → 说明原因 → 说明影响 → 给出方案 → 等待确认 → 再实施
```

禁止「偷偷绕过」，也禁止「为了完整而提前实现后续 Sprint 的内容」。

---

## 10. 文档索引

| 文档 | 内容 |
| --- | --- |
| `README.md` | 项目介绍、快速启动、端口、FAQ |
| `AGENTS.md` | 人机协作与编码规范（含 Agent 安全规范） |
| `docs/PROJECT_DESIGN_V1.md` | 本文档：总体架构基线 |
| `docs/development-environment.md` | 实际环境、镜像版本与选型依据 |
| `docs/sprint/SPRINT_0.md` | Sprint 0 任务书与验收标准 |

---

## 11. 变更记录

| 版本 | 日期 | 变更 | 说明 |
| --- | --- | --- | --- |
| V1.0 | 2026-09-26 | 初始版本 | 建立总体架构基线与 Sprint 0~13 Roadmap |
