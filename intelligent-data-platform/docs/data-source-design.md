# 数据来源设计（Data Source Design）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 版本：V1.0
> 更新日期：2026-09-26
> 适用 Sprint：0 起
>
> 本文回答一个核心问题：**这个平台的数据是从哪来的，怎么保证它可靠、准确、可追溯。**
> 这是「先工程，再智能」原则的落地说明 —— Agent 能问出正确答案的前提，
> 是数据来源本身是可定义、可校验、可追溯的。

---

## 1. 一句话结论

```text
MySQL 是业务事实的唯一来源（Source of Truth）；
Kafka 是业务事件与行为事件的实时通道；
Python 数据生成器是当前阶段的「业务系统替身」；
Flink 负责把 Kafka 事件加工成实时指标写入 Doris；
后续 Spark/Iceberg 负责把 MySQL 与 Kafka 沉淀成可信的离线湖仓；
Agent 只读 Doris（Sprint 7 起），且只允许 SELECT。
```

---

## 2. 数据来源分层图

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 第 0 层：业务系统（现实中是电商后台，本项目由数据生成器扮演）           │
│                                                                      │
│   Python Data Generator  (data-generator/)                           │
│     ├── generate_mysql_data  ──► 写 MySQL 业务库                      │
│     └── generate_events      ──► 写 Kafka 事件                        │
│                                                                      │
│   关键约束：事件不是凭空造的，而是基于「真实写进 MySQL 的那批订单/支付」  │
│             生成的（通过 state/dataset_snapshot.json 传递）            │
└───────────────┬──────────────────────────────┬───────────────────────┘
                │                              │
                ▼                              ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│ 第 1 层：业务事实库 (MySQL)     │  │ 第 1 层：事件流 (Kafka)           │
│                               │  │                                  │
│  ecommerce 库                 │  │  order_event      订单创建/变更   │
│    user     用户主数据         │  │  payment_event    支付成功/失败   │
│    product  商品主数据         │  │  refund_event     退款            │
│    orders   订单事实           │  │  behavior_event   行为埋点        │
│    payment  支付事实           │  │                                  │
│    refund   退款事实           │  │  分区键：订单类=order_id          │
│                               │  │         行为=user_id（保证有序）  │
│  ← 唯一事实来源 (SoT)          │  │  3 分区 / 副本 1（单机开发）      │
└───────────────┬───────────────┘  └──────────────┬───────────────────┘
                │                                 │
                │ 批量抽取                         │ 实时消费
                │ （Sprint 2 起 Spark）            │ （Sprint 1 起 Flink）
                ▼                                 ▼
┌───────────────────────────────┐  ┌──────────────────────────────────┐
│ 第 2 层：离线湖仓               │  │ 第 2 层：实时计算                 │
│  HDFS / MinIO + Iceberg        │  │  Flink                           │
│  Hive Metastore                │  │   窗口聚合 / 去重 / 维表关联       │
│  （Sprint 2 / Sprint 5）        │  │  （Sprint 1）                     │
└───────────────┬───────────────┘  └──────────────┬───────────────────┘
                │                                 │
                └────────────┬────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 第 3 层：统一查询与指标层 (Apache Doris)                              │
│                                                                      │
│   ADS  应用层   ads_realtime_gmv / ads_realtime_order / ads_user_*    │
│   DWS  汇总层   dws_trade_* / dws_traffic_*                           │
│   DWD  明细层   dwd_trade_order_detail / dwd_trade_payment_detail ... │
│                                                                      │
│   ← 对外唯一查询入口（Dashboard / Agent 都查这里）                     │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 只读 SQL（默认仅 SELECT）
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 第 4 层：消费方                                                       │
│   Sprint 6  Vue Dashboard + FastAPI/Spring Boot                      │
│   Sprint 7+ LangGraph Agent（经 MCP 工具，走只读账号）                 │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. 每类数据的来源与用途

| 数据 | 来源 | 写入方式 | 最终去向 | 引入 Sprint |
| --- | --- | --- | --- | --- |
| 用户主数据 | 数据生成器 | 批量 INSERT | MySQL → （未来）DWD 维表 | 0 |
| 商品主数据 | 数据生成器 | 批量 INSERT | MySQL → （未来）DWD 维表 | 0 |
| 订单事实 | 数据生成器 | 批量 INSERT | MySQL + Kafka `order_event` | 0 |
| 支付事实 | 数据生成器 | 批量 INSERT | MySQL + Kafka `payment_event` | 0 |
| 退款事实 | 数据生成器 | 批量 INSERT | MySQL + Kafka `refund_event` | 0 |
| 用户行为 | 数据生成器 | 仅事件 | Kafka `behavior_event` | 0 |
| 实时 GMV / 订单 / 用户数 | Flink 聚合 | 流式写入 | Doris ADS | 1 |
| 离线全量快照 | Spark 抽取 | 批写入 | Iceberg（MinIO/HDFS） | 2 / 5 |
| 指标定义 / 表结构 | 人工维护 | 文件入库 | `sql/metadata/` → RAG 知识库 | 3 / 9 |

---

## 4. 为什么这样设计

### 4.1 为什么 MySQL 是唯一事实来源

- **订单、支付、退款是可对账的业务事实**，必须有唯一权威副本。
  如果 Kafka 与 MySQL 各存一份且互不校验，就会出现「Kafka 里有单、MySQL 里没有」，
  下游做维表关联时大量丢数据，指标不可信。
- 因此本项目规定：

```text
orders.user_id     ∈ user.user_id
orders.product_id  ∈ product.product_id
payment.order_id   ∈ orders.order_id
refund.order_id    ∈ orders.order_id
product.price × orders.quantity ≈ orders.amount
```

这些约束在 MySQL 里是**外键**，在生成器里是**代码保证**，
在测试里是**断言**（`tests/test_data_generator.py`）。

### 4.2 为什么行为事件只走 Kafka

用户行为（浏览/点击/加购/收藏）**特征与交易不同**：

| | 交易事实 | 行为事件 |
| --- | --- | --- |
| 量级 | 低（每单一条） | 高（每次交互一条） |
| 是否需强一致 | 是 | 否 |
| 是否需落业务库 | 是 | 否 |
| 主要用途 | 对账、营收 | 漏斗、画像、推荐 |

因此行为事件只进 Kafka，不落 MySQL —— 避免把高写入量的埋点数据塞进业务库。

### 4.3 为什么加一层「快照文件」

`generate_events` 不重新随机生成一套订单，而是读取
`data-generator/state/dataset_snapshot.json` —— 那是
`generate_mysql_data` **实际写入 MySQL 的那批数据**。

这样做的好处：

```text
Kafka 事件里的 order_id / payment_id / refund_id / amount
        ↓ 必然对得上
MySQL 里真实存在的记录
```

否则就会出现「Kafka 事件的 order_id 在 MySQL 中不存在」，
实时链路与离线链路永远对不上账。

> 代价：`generate_events` 必须在 `generate_mysql_data` 之后运行。
> 这是**有意设计**——不允许产生与业务库不一致的事件。

### 4.4 为什么用统一时区与统一时间语义

所有时间字段使用 `Asia/Shanghai`（+08:00），事件用 ISO-8601 带偏移。
且保证时间因果链：

```text
register_time ≤ order.create_time ≤ order.pay_time ≤ refund.refund_time
```

若时间随机，实时窗口统计与漏斗分析会得出无意义结果
（例如「先退款后支付」）。

---

## 5. 数据流的三条链路

### 5.1 实时链路（Sprint 1 起）

```text
generate_events
      │  JSON 事件（按 order_id / user_id 分区）
      ▼
   Kafka (4 topics × 3 partitions)
      │
      ▼
   Flink
      ├── 解析 JSON、按 event_time 分配 watermark
      ├── 订单/支付/退款流：窗口聚合 GMV、订单数、支付数
      ├── 行为流：窗口去重 UV、漏斗计数
      └── 维表关联（用户/商品，来自 MySQL 或 Doris）
      │
      ▼
   Doris DWD / DWS / ADS
      │
      ▼
   实时指标：GMV / 订单量 / 支付量 / 退款额 / UV
```

### 5.2 离线链路（Sprint 2 起）

```text
MySQL (ecommerce)
      │  Spark JDBC 全量 + 增量抽取
      ▼
   Iceberg on MinIO/HDFS
      │  分层建模
      ▼
   ODS → DWD → DWS → ADS
      │
      ▼
   Doris（批量导入，与实时口径对齐）
```

> **口径对齐原则**：同一指标（如 GMV）在实时与离线**只能有一个定义**，
> 定义沉淀在 `sql/metadata/`，两条链路都必须遵守。这是避免
> 「实时说 100 万、离线说 95 万」的根本手段。

### 5.3 智能链路（Sprint 7+）

```text
自然语言问题
      ▼
   LangGraph Agent
      ├── 指标识别   ← RAG 检索 sql/metadata/ 中的指标定义
      ├── 元数据检索 ← MCP 提供表结构 / 血缘
      ├── SQL 生成
      ├── SQL 安全检查（只读校验、危险语法拦截、强制 LIMIT）
      ├── Doris 查询（专用只读账号，非 root）
      └── 结果分析 + 数据来源说明
```

---

## 6. 数据可靠性保证（分层）

| 层次 | 手段 | 落地位置 |
| --- | --- | --- |
| 生成期 | 外键关系、金额一致性、时间因果 | `data-generator/src/dataset.py` |
| 生成期 | 同种子可复现 | `GEN_RANDOM_SEED` |
| 入库期 | 外键约束（MySQL FOREIGN KEY） | `sql/mysql/01_schema.sql` |
| 入库期 | 写入后连表校验 | `generate_mysql_data` 的 `verify()` |
| 测试期 | 24 个单元测试断言业务关系 | `tests/test_data_generator.py` |
| 测试期 | 27 个冒烟测试断言落地内容 | `tests/smoke/test_infrastructure.py` |
| 运行期 | 5 个服务健康检查 | `scripts/health-check.sh` |
| 运行期 | 事件与业务库一一对应 | `state/dataset_snapshot.json` |
| 治理期 | 数据质量规则（完整性/一致性/及时性） | Sprint 11 |

---

## 7. 当前阶段的已知局限

诚实记录，避免后续误判：

| 局限 | 说明 | 计划 |
| --- | --- | --- |
| 数据生成器是「业务系统替身」 | 没有真实电商后台，生成器同时扮演业务写入方与埋点方 | 论文中说明为仿真数据源 |
| 无 CDC 实时同步 | MySQL → Kafka 目前靠生成器双写，未用 Flink CDC / Canal | Sprint 1 可评估，Sprint 3 后视需要引入 |
| 单机单副本 | Kafka 副本 1、Doris 单 BE，无高可用 | 明确为开发环境；论文中说明生产部署差异 |
| 行为事件不落库 | 仅存在于 Kafka，未长期存储 | Sprint 2/5 由 Spark 落 Iceberg |
| 无数据质量校验任务 | 目前只有生成期与测试期校验 | Sprint 11 |
| 无数据血缘 | 表级血缘尚未采集 | Sprint 9（MCP 元数据） |

---

## 8. 后续 Sprint 如何扩展数据来源

| Sprint | 新增数据来源 | 说明 |
| --- | --- | --- |
| 1 | Flink 计算结果 | Kafka → Doris 实时指标 |
| 2 | Spark 抽取结果 | MySQL → Iceberg/HDFS |
| 3 | Hive 分层表 | ODS/DWD/DWS/ADS |
| 4 | Airflow 调度元数据 | 任务依赖与运行记录 |
| 5 | Iceberg 表快照 | 时间旅行与 ACID |
| 6 | 服务层查询日志 | Dashboard 访问行为 |
| 7+ | LLM 调用日志、Agent 会话 | Agent 可观测性 |
| 9 | 向量化指标/文档 | RAG 知识库 |
| 11 | 数据质量结果、监控指标 | 治理闭环 |

**扩展原则**：任何新数据来源都必须先回答三个问题 ——

```text
1. 谁是这条数据的唯一事实来源？
2. 它和已有数据的一致性约束是什么？
3. 失败时如何发现、如何回溯？
```

答不出来就先不引入。

---

## 9. 相关文档

| 文档 | 内容 |
| --- | --- |
| [`PROJECT_DESIGN_V1.md`](PROJECT_DESIGN_V1.md) | 总体架构与分层模型 |
| [`../sql/metadata/kafka_topics.md`](../sql/metadata/kafka_topics.md) | Topic 与事件格式定义 |
| [`../sql/mysql/01_schema.sql`](../sql/mysql/01_schema.sql) | 业务表 DDL 与外键约束 |
| [`../data-generator/README.md`](../data-generator/README.md) | 生成器实现与一致性保证 |
| [`DEVELOPMENT_LOG.md`](DEVELOPMENT_LOG.md) | 开发日志（每阶段进展与决策） |
