# 毕业设计报告大纲（REPORT_OUTLINE）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 用途：**报告章节树 + 每节写作要点 + 每节可引用的实测数字与图表（附来源文件）**
> 数字纪律：本大纲出现的每一个数字都必须能在括注的来源文件中查到；查不到的写"未采集"。
> 生成日期：2026-09-28（依据仓库现有文档整理，未新增任何测量）

---

## 0. 使用说明

### 0.1 三层结构

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 大纲 | 本文件 | 章节树、每节要点、数字与图表清单 |
| 正文 | `docs/thesis/REPORT_DRAFT.md` | 可直接使用的学术书面语正文 |
| 事实底座 | `docs/thesis/DATA_AND_LIMITATIONS.md` | 数据来源与已知限制（答辩防守用） |

### 0.2 引用格式约定

- 正文引用实测数字时，句末用括注：`（见 docs/sprint/SPRINT_3.md 第 7 节）`。
- 引用口径定义时统一写：`（口径依据：sql/metadata/metrics.md 第 2 节）`。
- **不要**把数字写成"约""大约"再配一个精确到分的值；精确值来自文档就写精确值，来自估算就写"未采集"。

### 0.3 全文自查清单（写作时逐条过）

```text
[ ] 每个技术选择都写了：为什么选它 / 否决了什么 / 代价是什么
[ ] 每个结论后面有实测数字，或明确写"未采集"
[ ] 未使用"赋能、闭环、抓手、在当今时代、全方位、深度赋能"一类套话
[ ] 限制章节（第 8 章 + 附录 C）没有为了好看而删减
[ ] 报告里出现的数字与 DATA_AND_LIMITATIONS.md 的台账一致
```

---

## 1. 摘要（Abstract）

### 1.1 中文摘要（400~600 字）

写作要点（按顺序）：

1. 问题：电商场景的数据同时以"流"和"批"两种形态产生，传统 Lambda 架构对同一指标维护两套实现，会产生口径分歧。
2. 本文做的事：搭一套**批流一体**平台，实时链路 Kafka → Flink → Doris，离线链路 MySQL/Kafka → Spark → Iceberg，两条链路共用一份指标口径文件；在其上构建一个**只读**的自然语言问答 Agent。
3. 关键的验证方式：不是"跑通了"，而是**逐窗口对账**——交易域 11458 个分钟窗口、流量域 19643 个分钟窗口，两侧逐窗口比对，不一致 0。
4. 关键的工程取舍：16 GB 单机同时承载实时与离线，采用**错峰批处理**（暂停 Flink 栈 → 跑批 → 恢复并自检），内存闸门阈值 3000 MB。
5. 结果概览：12 个 Sprint 全部验收通过；Iceberg 迁移 23 张表逐表行数与金额一致；全量 pytest 353 passed / 0 failed / 0 skipped / 3 xfailed。
6. 限制（摘要里必须出现一句）：合成数据、明文 HTTP、Airflow SimpleAuth、实时侧 1 个窗口的比率列缺陷。

可引用数字：

| 数字 | 含义 | 来源 |
| --- | --- | --- |
| 11458 / 不一致 0 | 交易域逐窗口对账 | `docs/sprint/SPRINT_3.md` 第 7 节；`AGENTS.md` §15.5 |
| 19643 / 不一致 0 | 流量域逐窗口对账 | `docs/sprint/SPRINT_5.md` 第 9.2 节；`AGENTS.md` §15.12 |
| 23 张表 / **70、70**（范围 23 张表） | Iceberg 迁移校验（流量域建成后复跑） | `AGENTS.md` §15（Sprint 5 行）；`docs/sprint/SPRINT_5.md` 第 9.6 节 |
| **60、60**（范围 **18 张表**） | Iceberg 迁移校验（阶段 4 首次成功，当时流量域未建模） | `docs/sprint/SPRINT_5.md` 第 8.1 节 |
| 353 / 0 / 0 / 3 xfail | 全量 pytest | `docs/sprint/SPRINT_12.md` §8.3 |
| 3000 MB | 内存闸门阈值 | `docs/DECISIONS.md` D11；`docs/sprint/SPRINT_3.md` §8.1 |

### 1.2 关键词

批流一体；Lakehouse；Apache Iceberg；流批对账；数据质量；检索增强生成（词法检索）；自然语言转 SQL；只读权限边界。

### 1.3 Abstract（英文，200~300 words）

与中文摘要逐段对应，数字一致。避免机器直译，`对账` 统一译作 `cross-path reconciliation`，`错峰批处理` 译作 `time-staggered batch execution`。

---

## 2. 绪论（第 1 章）

### 2.1 研究背景（1.1）

要点：电商业务同时产生两类数据（实时事件流 / 日级经营分析），二者对"同一指标"的诉求不同：实时要快，离线要准。传统做法是两套系统。

可引用：

- 项目对两类形态的定义表（实时流 vs 批量离线）——`docs/PROJECT_DESIGN_V1.md` 第 1.1 节。
- 行为事件的量级特征（低写入量 vs 高写入量，是否需强一致）——`docs/data-source-design.md` 第 4.2 节。

### 2.2 研究意义（1.2）

要点：把"实时与离线算的是同一个数"从口号变成可证伪的断言。若不做，Agent 引用任何一个口径都是错的。

可引用：

- 口径分裂的因果链图（GMV 三种算法 → 实时 100 万 vs 离线 95 万 → 智能层失去意义）——`sql/metadata/metrics.md` 第 6 节。
- 同一逻辑的另一种表述——`docs/sprint/SPRINT_3.md` 第 1.1 节。

### 2.3 国内外研究现状（1.3）

> **素材缺口，必须如实写。** 仓库文档中**没有任何**关于国内外研究现状、同类系统对比、参考文献的整理（无文献列表、无引用记录）。本节需要作者自行检索补充，**不得**从本仓库内伪造文献。

可用的**技术侧**事实（属"技术现状"而非"研究现状"）：

- 湖仓表格式要解决的三个问题：无 ACID、无 schema 演进、无时间旅行——`docs/sprint/SPRINT_5.md` 第 1 节。
- Lambda 架构的代价（同一指标两份实现）——`docs/PROJECT_DESIGN_V1.md` 第 1.1 节。
- 向量检索在本项目被否决的三条事实依据——`docs/sprint/SPRINT_9.md` 第 2.1 节。

### 2.4 本文主要工作（1.4）

用一张表列 5 项工作与对应章节，每项后跟一句"凭什么可信"：

| # | 工作 | 可信依据 | 来源 |
| --- | --- | --- | --- |
| 1 | 实时链路 Kafka→Flink→Doris | 8 个 Flink sink 作业各 1 实例；8 个 Routine Load RUNNING、errorRows=0；GMV 与 MySQL 精确到分相等 | `AGENTS.md` §15.2；`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` |
| 2 | 离线四层 ODS/DWD/DWS/ADS | DWD == ODS 逐表相等；ADS 与实时逐窗口对账 | `docs/sprint/SPRINT_3.md` 第 7.1/7.2 节 |
| 3 | Iceberg Lakehouse | 23 张表迁移，逐表行数与金额一致 | `docs/sprint/SPRINT_5.md` 第 8.1、9.6 节 |
| 4 | Airflow 调度 | DAG 9 任务真实驱动流水线；内存闸门实测拒绝过一次 | `AGENTS.md` §15.8；`docs/sprint/SPRINT_4.md` §10.1 |
| 5 | 只读数据服务 + 问答 Agent | 四类攻击全部被拒；回答附 tables/executed_sql/docs；MCP 与直连逐字段 IDENTICAL | `AGENTS.md` §15.6、§15.12；`docs/sprint/SPRINT_10.md` |

### 2.5 论文组织结构（1.5）

一段话说明第 3~8 章的分工。

---

## 3. 相关技术（第 2 章）

> 写作原则：**只写本项目真正用到的机制**，每个组件写"用了它的哪个能力 + 为什么不是别的"。

### 3.1 技术栈总览（2.1）

一张表列全部组件与**实际落地版本**：来源 `AGENTS.md` §2.1（该表是版本权威）。

必须包含：MySQL 8.4.11 / Kafka 4.2.1(KRaft) / MinIO RELEASE.2025-10-15T17-29-55Z / Doris 4.1.4 / Flink 1.20.1 / flink-sql-connector-kafka 3.4.0-1.20 / Spark 3.5.7 / Hive Metastore 3.1.3 / Iceberg 1.10.2（**注意：定版为 1.10.2，不是任务书里写的 1.11.0**，见 `docs/sprint/SPRINT_5.md` §8.4）/ FastAPI 0.141.1 / uvicorn 0.54.0 / openai SDK 2.54.0 / DeepSeek `deepseek-flash` / Nginx 1.24.0 / Vue 3.5.13 / ECharts 5.6.0 / Airflow 3.3.2 / langgraph 1.1.0 / mcp 2.2.0 / Prometheus v3.13.3 / Grafana 13.2.2 / pytest 9.x。

**Python 版本有两条线**：宿主机 3.12.3（数据服务 / Agent / Airflow），容器 3.13.14（仅 data-generator）——`AGENTS.md` §2.1 的警告框。

### 3.2 实时计算：Kafka + Flink + Doris（2.2）

要点：

- Kafka 用 KRaft 模式（无 ZooKeeper）；4 个 topic × 3 分区；分区键设计（订单类 = `order_id`，行为 = `user_id`）保证分区内有序——`docs/PROJECT_DESIGN_V1.md` 第 5.3 节；`sql/metadata/kafka_topics.md` 第 4 节。
- Flink 用**事件时间** + 1 分钟 TUMBLE 窗口；不用处理时间，目的是让乱序与重放结果一致——`sql/metadata/metrics.md` 第 1 节。
- Doris 用 Routine Load + UNIQUE KEY + merge-on-write 实现幂等收敛——`docs/data-source-design.md` 第 5.1 节。
- 事件时间格式是 `yyyy-MM-dd HH:mm:ss.SSS` 而非 ISO-8601 带偏移：**实测** Flink JSON 解析器不接受后者，报 `Fail to deserialize at field: event_time`——`docs/data-source-design.md` 第 4.4 节。

### 3.3 湖仓与表格式：Spark + Iceberg（2.3）

要点：Iceberg 提供三项 Parquet 外部表没有的能力（ACID / schema 演进 / 时间旅行），逐条对应到项目里的具体后果——`docs/sprint/SPRINT_5.md` 第 1 节。

否决项与代价：

- Catalog 选 HiveCatalog（复用既有 HMS 3.1.3，两种表格式共用同一目录、迁移期可并存对照）；否决 REST Catalog（要多一个常驻进程，内存不够）与 Hadoop Catalog（无法与既有 HMS 表共存）——`docs/sprint/SPRINT_5.md` 第 3.1 节。
- 不引入 Flink-Iceberg 连接器：没有流式写湖的需求，归档走 Spark 批读——同文件第 1.1 节。

### 3.4 调度：Airflow（2.4）

要点：

- LocalExecutor 而非 Celery/K8s：Celery 要 Redis/RabbitMQ（被技术栈禁令排除），K8s 同理——`docs/sprint/SPRINT_4.md` 第 2.2 节。
- 元数据库用 MySQL 而非 PostgreSQL：Airflow 官方支持 MySQL 8.0/8.4，本机已有的就是 8.4.11，**零成本满足需求**——`docs/DECISIONS.md` D2。
- 独立 venv 是硬冲突而非洁癖：Airflow 3.3.2 钉 `fastapi>=0.129,<0.137`，数据服务用 0.141.1——`docs/DECISIONS.md` D3。
- 不部署 triggerer（只为 deferrable operator 服务）——`docs/DECISIONS.md` D4。

### 3.5 大模型应用：Tool Calling / LangGraph / 词法检索 / MCP（2.5）

要点：

- 选 `deepseek-flash` 的理由：上下文短（口径字典 + 表结构 + 一条 SQL），支持 Tool Calls，价格约 pro 的 1/4.5——`services/agent/README.md` 第 1 节。
- **思考模式默认关闭**及其代价（若不关闭必须完整回传 `reasoning_content`，漏传 API 报 400）——`docs/sprint/SPRINT_7.md` 第 4.4 节。
- LangGraph 相对单轮循环补的三件事：显式规划产物、独立校验步骤、重试成为一等公民——`docs/sprint/SPRINT_8.md` 第 1 节。
- 词法检索（BM25 + CJK bigram + 显式同义词表）而非向量检索的三条事实依据——`docs/sprint/SPRINT_9.md` 第 2.1 节。
- MCP 不是第二条取数通道，而是同一份只读能力的另一种暴露方式——`docs/sprint/SPRINT_10.md` 第 2 节。

### 3.6 治理：数据质量与可观测性（2.6）

要点：质量校验做成"清单 + 引擎 + 每条一个标量 SQL"，25 条覆盖 7 类——`docs/sprint/SPRINT_11.md` 第 4.1 节。Prometheus 只做抓取与展示，**告警未接入**（如实写）——同文件第 10 节。

---

## 4. 需求分析（第 3 章）

### 4.1 功能性需求（3.1）

一张表：需求 → 落地形态 → 验收证据。

| 需求 | 落地 | 证据来源 |
| --- | --- | --- |
| 实时指标秒级可见 | Kafka→Flink→Doris，DWD/DWS/ADS 8 张表 | `docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` 第 2 节 |
| 离线结果准确可回溯 | Spark→Iceberg 四层 23 张表 | `docs/sprint/SPRINT_5.md` §8.1、§9.6 |
| 两条链路口径一致 | 逐窗口对账表 `ads_reconcile_*` | `docs/sprint/SPRINT_3.md` §7.2；`SPRINT_5.md` §9.2 |
| 指标可查、可看 | 11 个只读接口 + 6 个看板页 | `docs/sprint/SPRINT_6.md` §3.2、§4.2 |
| 自然语言问数 | Agent 4 个工具 + 图式编排 6 节点 8 条边 | `docs/sprint/SPRINT_7.md` §4.1；`SPRINT_8.md` §9.3 |
| 数据可治理 | 25 条质量校验 + 23 面板监控 | `docs/sprint/SPRINT_11.md` §4.1、§4.2 |

### 4.2 非功能性需求（3.2）

- **资源约束是硬需求**：单机 16 GB，实时链路常驻约 13.7 GB，可用内存常在 2.4~4.3 GB 波动——`docs/sprint/SPRINT_11.md` 第 5 节。
- 安全需求：Agent 不得持有数据库凭据；只读账号 `agent_ro`；SQL 守卫仅允许 SELECT。
- 可复现需求：同一种子可复现生成数据；验收脚本一键复现。

### 4.3 约束与假设（3.3）

明确写出：单机单副本（Kafka 副本 1、Doris 单 BE）、无 CDC（生成器双写）、合成数据、按 IP 访问。来源：`docs/data-source-design.md` 第 7 节；`docs/DECISIONS.md` 第三节。

---

## 5. 系统设计（第 4 章）

### 5.1 总体架构（4.1）

- 分层架构图：直接引用 `docs/PROJECT_DESIGN_V1.md` 第 2.1 节的 ASCII 图，重新绘制为矢量图。
- 端到端数据流五段（生成 → 实时 → 离线 → 服务 → 智能）——同文件第 2.2 节。

### 5.2 部署分层原则（4.2）

**这是本项目一条贯穿始终的设计取舍**：数据层（MySQL/Kafka/MinIO/Doris/Flink）继续用 Docker Compose；服务层（Nginx/FastAPI/前端/Agent/Airflow）直装宿主机，用 apt + systemd。理由：进程少，`journalctl`/`systemctl restart` 更直接；且 Airflow 要调人手工跑的同一个脚本入口，跑容器里就得挂 `docker.sock`。来源：`docs/DECISIONS.md` D1；`docs/sprint/SPRINT_4.md` 第 3.1 节。

代价（必须写）：换机器要多跑几个安装脚本（已全部脚本化、幂等）。

### 5.3 数仓分层与命名（4.3）

- ODS/DWD/DWS/ADS 四层职责边界表（含"只做/不做"两列）——`docs/sprint/SPRINT_3.md` §3.1；`AGENTS.md` §5.2。
- **DWS 不出比率指标**的理由（避免两个 DWS 表各算一半比例）——`docs/sprint/SPRINT_3.md` §3.1。
- 命名规范与 SQL 强制要求（金额 `DECIMAL(18,2)`、时间 `DATETIME` 且 `Asia/Shanghai`、单 BE 必须 `replication_num = 1`）——`AGENTS.md` §5.1、§5.3。

### 5.4 存储选型（4.4）

一张"选了什么 / 否决了什么 / 代价"表：

| 决策点 | 选择 | 否决 | 代价 | 来源 |
| --- | --- | --- | --- | --- |
| 湖仓文件系统 | MinIO（S3A） | HDFS | 与"湖仓在 HDFS"的原始设计不一致，需在报告中说明偏差 | `AGENTS.md` §15 表格（Sprint 2 行）；`docs/DEVELOPMENT_LOG.md` 第 382 行 |
| 湖仓表格式 | Iceberg 1.10.2 + HiveCatalog | Parquet 外部表（无 ACID/schema 演进/时间旅行）；REST Catalog；Hadoop Catalog | 需处理 Java 字节码版本冲突 | `docs/sprint/SPRINT_5.md` §1、§2.3.1、§3.1 |
| 离线结果进 Doris 的方式 | Doris `S3()` TVF 直读 Parquet | Stream Load（要额外一个 loader 进程与端口） | `*.parquet` 不递归，必须写 `**/*.parquet` | `docs/sprint/SPRINT_3.md` §4.2、§8.6 |
| 对象存储凭据 | MinIO root + `--conf` 传密钥 | 专用最小权限账号（**未做**） | `ps` 可见明文密钥；论文里必须作为限制写出 | `docs/DECISIONS.md` ⏳4 |

### 5.5 指标口径的唯一权威（4.5）

要点：`sql/metadata/metrics.md` 是唯一口径定义；实时、离线、Agent 三处都必须引用它，不得自建第二份。三条具体机制：

1. 可加指标无事件补 0、比率指标分母为 0 留 NULL（否则实时与离线无法对账）——`sql/metadata/metrics.md` §2.1。
2. 三类指标的运算规则（可加 / 去重 / 派生）与"不可以做什么"——同文件 §3.1。
3. `/meta/metrics` 接口**运行时解析该文档**，不存第二份口径——`docs/sprint/SPRINT_6.md` §3.5。

### 5.6 安全设计（4.6）

- 三道防线：SQL 守卫（服务端）→ 只读账号 `agent_ro` → 连接级超时 15s——`docs/sprint/SPRINT_7.md` §3.2。
- 进程边界即权限边界：Agent 进程里没有数据库凭据，也没有 mysql 客户端依赖——同文件 §2。
- 网关契约：`/data/api/query` 只放行 POST，其余路径 `limit_except GET HEAD OPTIONS`——同文件 §3.4。
- 端口纪律：8000 / 8100 / 8200 只绑回环——`README.md` §7.3；`docs/sprint/SPRINT_10.md` §7。

### 5.7 内存预算与错峰模式（4.7）

要点：实时链路常驻约 13.7 GB；批处理闸门 3000 MB；错峰释放约 1.65 GB（2282 → 3935 MB）。**调低闸门是把保护拆掉，错峰才是正解**。来源：`docs/DECISIONS.md` D11；`docs/sprint/SPRINT_4.md` §10.1。

---

## 6. 关键实现（第 5 章）

> 本章是报告的主体，按"一条链路一节"组织。每节固定三段：**机制 / 为什么这么做 / 实测证据**。

### 6.1 实时链路（5.1）：Kafka → Flink → Doris

- 8 个 sink 作业（4 个 DWD 清洗 + 4 个指标聚合）各 1 个实例。
- 8 个 Routine Load 全部 RUNNING、errorRows=0。
- 一次真实故障与修复：重启 `flink-jobs` 不会取消旧作业（SQL Gateway 是 session 模式），旧作业把重放事件累加到旧窗口状态，表现为 PV 合计 39,987 vs 事件 20,000（近 2 倍）。
  - 修法：新增 `scripts/cancel-flink-jobs.sh`，提交脚本启动时自动清场；健康检查增加"Flink 作业唯一性"检查项。
  - 来源：`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` 第 5 节。
- 类目作业提交失败：Flink 写入按位置对齐，SELECT 列顺序与 Doris 表列顺序不一致（UNIQUE KEY 必须是有序前缀）——同节。

图表建议：实时链路数据流图（Kafka 4 topic → 8 作业 → Doris 8 表）；Routine Load 状态截图或表格。

### 6.2 离线分层建模（5.2）：ODS → DWD → DWS → ADS

- 各层行数表（必须逐表列出）：来源 `docs/sprint/SPRINT_3.md` §7.1。
- 层间行数关系：DWD == ODS 逐表相等（1200/600/6000/5406/254）。
- **事件口径映射表**是本层最容易出错的地方：实时指标算在 Kafka 事件上，离线指标算在 MySQL 业务行上，两者要能对账必须先证明一一对应；`PAYMENT_FAILED` 的 `event_time` 在业务表里没有独立字段，**不允许**用 `create_time` 冒充——`docs/sprint/SPRINT_3.md` §3.2。
- 幂等策略：DWD/DWS/ADS 按分区 `INSERT OVERWRITE`；Doris 侧事实表先 `TRUNCATE` 再 `INSERT`（**注意：这个顺序后来被证明是破坏性的，见 6.5 节的事故**）——同文件 §4.3。
- **为什么用 PySpark 脚本 + `spark.sql()` 而不是纯 `.sql` 文件**：需要在作业内做自校验，纯 SQL 只能回答"跑完没报错"，不能回答"算得对不对"——同文件 §4.1。

### 6.3 批流交叉对账（5.3）—— 本文核心

这一节要写透，建议 4 个二级小节。

#### 6.3.1 判据的设计（交易域）

- 对账区间 `2024-10-25 08:16:00 ~ 2026-09-26 11:35:00`，窗口 11458 个，不一致 0 个。
- 两侧 GMV 精确到分相等：51,890,375.77 == 51,890,375.77。
- 五项指标差异全为 0：GMV / 订单量 6000 / 支付成功 5287 与 45,615,110.02 / 支付失败 119 / 退款 254 与 1,825,511.73。
- 来源：`docs/sprint/SPRINT_3.md` §7、（`docs/sprint/SPRINT_4.md` §10.1 用 Airflow 编排复现同一结论）。

#### 6.3.2 判据被打磨的两次（**这是本节最有价值的素材**）

**第一次打磨：离线表多一个分区列。**
`traffic-ads` 自检 34/35，唯一失败项是"字段顺序与实时表一致"。根因是 `describe_columns` 读的是 `DESCRIBE` 输出，而 `DESCRIBE` **会把分区列一并列出**（`dt` 在最后）；实时表没有分区列，于是 12 列被拿去和 13 列比。判据改为"数据列逐列同序 + 分区列 `dt` 只在最后"，并补一条"除 `dt` 外无多余字段"的反向守卫。来源：`docs/sprint/SPRINT_5.md` §9.4 第 1 条。

**第二次打磨：比率列按各自计数重算。**
原判据是"两侧比率相等"。改为**更强**的独立判据："每一侧的比率 == 由该侧**自己的**计数按 metrics.md 公式重算"。理由："两侧比率相等"既不增加信息（判据列相等时比率数学上必然相等），又会被两侧除法实现细节的末位差异误报；新判据不需要两侧一致就能判定**谁错**，且两侧一起错成同一个值它照样能抓出来。来源：`docs/sprint/SPRINT_5.md` §9.2 第 2 条。

> 写作提示：这两次打磨都要写成"判据本身先被验证"的过程，并接到本项目反复出现的同一类问题上——**断言写错比没有断言更危险**（`docs/sprint/SPRINT_3.md` §8.4、`SPRINT_5.md` §9.4 末、`SPRINT_9.md` §9.4~9.6）。

#### 6.3.3 流量域对账与口径陷阱

- 结果：`[scope] 对账窗口 19643 个，一致 19643 个，不一致 0 个；单边窗口：仅实时 0 个，仅离线 0 个；PV 合计：实时 19998 vs 离线 19998`。来源：`docs/sprint/SPRINT_5.md` §9.2。
- 三类指标的运算规则：`uv` 是**去重**指标（跨窗口不可加）；`pv` 与 5 个行为计数（view/click/cart/favorite/buy）是**可加**指标；比率是**派生**指标（用合计后的分子分母重算，不对逐窗口比率求平均）——`sql/metadata/metrics.md` §3.1。另：交易域对账判据是 **7 个可加 + 1 个去重（共 8 列）**，不是"8 个可加"。
- 支撑这条规则的两条实测证据（**非常有说服力，务必引用**）：
  - `SUM(19644 个窗口的 uv) = 19999`（逐窗口去重的基数），而全部 20000 个事件的去重用户数 = 1200（人的基数）。两者相差约 16 倍，**都正确**——它们是两个不同的定义。若把"天 UV"写成 `SUM(分钟 uv)`，会系统性偏大且看起来仍然合理（19999 < 20000 的 PV，不会有人怀疑）。
  - 本项目文档里一度把这个数写成 **1199**，那是早期估算的残留，从未被真正测量过。**写入文档的数字必须能指回一条可复现的命令。**
  - 来源：`sql/metadata/metrics.md` §3.1；`docs/sprint/SPRINT_5.md` §9.1。

#### 6.3.4 一处未归零的发现（必须写）

实时侧 `2026-03-21 19:23:00` 这一个窗口 `view_cnt=2 / click_cnt=1`，`click_rate` 落库 **0.0000**（按公式应为 0.5000）；离线侧同窗口算出 0.5000，是对的。定位过程五步：打印双方原值 → 看差值量级（在小数点后第一位，不是末位，排除舍入）→ 用第三方判据定责（1/2 = 0.5 是纯算术）→ 找旁证（实时侧 `click_rate` 全表取值只有 `{0.0000, NULL, 1.0000}`，而满足 `0 < click_cnt < view_cnt` 的分数窗口恰好只有这 1 个）→ 对照回归（交易域 11459 个窗口 0 矛盾）。

处置：判据改严，矛盾数落盘为 `ads_reconcile_traffic_summary.realtime_rate_anomaly_windows = 1`，逐窗口标记；**没有加容差、没有删证据**。来源：`docs/sprint/SPRINT_5.md` §9.3、§10；`docs/DECISIONS.md` ⏳10；`sql/metadata/metrics.md` §3.3。

### 6.4 Iceberg 迁移（5.4）

- 迁移清单 18 → 23 张表；**两次迁移校验各按自身范围计**：18 张表那次 `60/60`（`docs/sprint/SPRINT_5.md` §8.1），扩到 23 张表复跑那次 `70/70`（`AGENTS.md` §15 Sprint 5 行）。**两个数字都要写、都要带范围**——它们不是同一判据的两个结果，而是两次范围不同的运行；删掉其中一个等于丢掉一次真实验收。判据构成（可核对）：每张表一项 `Provider == iceberg` + 一项行数一致 + 按金额列逐列合计一致，末尾一项"库表数 == 迁移清单表数"；18 张表那次分解为 `60 = 18 + 18 + 23 + 1`。**`70/70` 无逐项细目**（仓库只记总数），推算的分解只能作为口径解释、不能当实测细目引用。
- **三段名与 catalog 命名陷阱**（写成一个完整的小节）：
  - 陷阱一：`CREATE DATABASE` 报 `Cannot create namespace with invalid name:`，**冒号后是空的**。根因是库名与已注册 catalog **同名**——Spark 解析一段名时先把它当目录名，解析成"目录 + 空命名空间"。**库名与 catalog 同名时，"建库"这句话没有唯一含义。** 修法：catalog 改名 `iceberg`，库名 `lakehouse_iceberg` 不动。
  - 陷阱二：catalog 改名后**报错一字不差**，像没改过。因为 `spark-defaults.conf` 是 Dockerfile **构建期 COPY** 进镜像的，而 `spark-submit` 是 `docker compose run` 的临时容器，只挂了 `jobs`/`sql`。修法：把 conf 挂进 spark-submit。
  - 陷阱三：表建出来了，但可能建在**另一个目录的同名库**里。Spark 的**两段名 = (命名空间, 表)，永远属于当前目录**，不会因为名字像就跳到别处。修法：全程三段名（源 `spark_catalog.lakehouse.*`、目标 `iceberg.lakehouse_iceberg.*`）。
  - 来源：`docs/sprint/SPRINT_5.md` §8.2 第 1~3 条；`docs/DECISIONS.md` D13、D14。
- **分区写入器 OOM 的结构性修复**：
  - 现象：执行器连续 `OutOfMemoryError`，4 次 `Lost executor ... code 52`，最后 `MetadataFetchFailedException`。
  - 根因：703 个 dt 分区 vs 默认 200 个 shuffle 分区，**AQE 又把小分区合并** → 少数 task 各持有几百个分区写入器。数据才 2 万行，**与数据量无关**。
  - 修法：分区表 `INSERT` 加 `DISTRIBUTE BY` + `spark.sql.shuffle.partitions=1000` + 关 AQE 合并；一个 task ≈ 一个分区。
  - **不采用**"把执行器内存调大"：调大内存只是掩盖症状，分区数一涨还会复发。
  - 来源：`docs/sprint/SPRINT_5.md` §8.2 第 4 条；`docs/DECISIONS.md` D15。
- 偏差记录（必须写）：Iceberg 版本 1.11.0 → **1.10.2**。1.11.0 的 class 文件是 major 61（Java 17），而 Spark 镜像是 Java 11（只认到 55），实测报 `UnsupportedClassVersionError`。作者在任务书里曾写"JDK 11 在 Iceberg 1.11 支持的 8/11/17/21 之内"——**"官方支持在 Java 11 上运行"与"其发布的字节码要求 Java 17"是两件不同的事**。来源：`docs/sprint/SPRINT_5.md` §2.3.1、§8.4。
- DDL 落盘方式的偏差：DDL **没有**落盘 `sql/iceberg/`，改为**从源表 schema 推导**。理由：手写 18 张表的 DDL 会与源表定义分叉，而分叉只会在迁移后才被发现。来源：`docs/sprint/SPRINT_5.md` §8.4 第 3 条。

### 6.5 调度（5.5）：Airflow DAG

- DAG 9 任务：`pause → ods → archive → dwd → dws → ads → reconcile → load → restore`。
- 六条硬规范（都是实测教训，逐条可讲）：
  1. 调度必须复用人工入口（每个 stage 调 `run-batch-pipeline.sh --stage X`），手工跑与调度跑受同一套约束。
  2. 重试与超时必须显式写在算子上：Airflow 3.3.2 里 `@dag(default_args={...})` 的 `retries`/`execution_timeout` **不生效**，实测会让 DAG run 永久卡死（状态停在 `up_for_retry` 却已无重试次数），而恢复任务依赖它，于是实时链路一直停着。
  3. 暂停与恢复必须是两个独立任务，恢复用 `trigger_rule=all_done`。
  4. 切换 `SITE_SCHEME` 后必须重跑 `deploy-airflow.sh`，否则 `api.base_url` 残留旧协议，任务执行回调静默失效。
  5. 人工敲 airflow 命令一律用 `scripts/airflow.sh`。
  6. 不要覆盖正在运行的 shell 脚本，也不要在 DAG run 进行中改 DAG 文件。
- 来源：`AGENTS.md` §15.8；`docs/sprint/SPRINT_4.md` §5.1、§10.4。
- **「成功信号」不可信**这条主线：六层障碍里有三层属于同一类（`AGENTS.md` §15.8 末）。

### 6.6 服务层（5.6）：FastAPI + Vue

- 11 个只读接口；统一响应信封 `{data, source, generated_at}`；每个响应都带 `source.tables` 与 `metric_definitions`。来源：`docs/sprint/SPRINT_6.md` §3.2、§3.3。
- 前端**无构建步骤**（Vue 全局构建 + 浏览器内模板；vendor 本地化、不引用外部 CDN）；代价与理由——`docs/sprint/SPRINT_6.md` §4.1。
- 两个与口径一致的前端约定：金额按字符串处理、跨行累加先转整数分（`BigInt`）；`null` 显示为 `—` 而不是 0——同文件 §4.3。
- 一次真实事故：前端把绝对路径当相对前缀，拼出 `/data/api/api/...`，整站 404，看起来像后端挂了。修法：显式定义两个完整基址，`buildUrl` 只拼一次——`docs/sprint/SPRINT_7.md` §9.3。

### 6.7 Agent 图（5.7）：检索 → 规划 → 取数 → 校验 → 反思 → 汇总

- 6 个节点、8 条边、三重上界：`retries`（默认 2）、工具调用总轮次（默认 6）、总超时（默认 120s）；收尾调用**不携带 tools**，否则图停不下来。来源：`docs/sprint/SPRINT_8.md` §3.3、§9.3。
- **为什么 `reflect` 之后回到 `plan` 而不是 `execute`**：错误信息（"表未授权"）通常意味着**计划本身错了**（选错了表），回到 `execute` 只会把同一条错 SQL 重放一次——那是重试，不是重新规划。来源：同文件 §3.1。
- **反思重试的实测值**（写成正文时要写准）：SPRINT_8 §9.5 的问 1 是 `retries=1`（真实错误 `Unknown column 'window_start'`），问 5 是 `retries=2`（两次 `TABLE_NOT_ALLOWED`）；`PERFORMANCE.md` §4.2 记录的另一次 `/ask` 是 `retries=1`。**三处不要混写成同一个数。**
- 一个 P0 缺陷及修法：`propose_sql` 的 tool_call 没有被回应 → 整个请求被拒（`An assistant message with 'tool_calls' must be followed by tool messages ...`）。修法：规划节点为每个 `propose_sql` 补一条"计划已受理"的 tool 回应。**为什么不是删掉工具改用纯文本 JSON**：工具调用给出的是结构化参数（目标表 + SQL + 理由），不依赖解析散文。来源：同文件 §9.6.1。
- 第二个真问题：规划器复用全局消息流 → 重规划变成重放（模型把同一条错 SQL 又提了一遍）。修法：规划器每轮只用干净上下文（系统提示词 + 问题 + 检索结果 + **上一版计划原文** + **真实失败原文**）。来源：同文件 §9.6.2。
- 实测的反思重试（DoD 要求"真实触发过至少一次"）：
  - 问 1「最近一周每天的 GMV 是多少？」→ `retrieve→plan→execute→validate→reflect→plan→execute→validate→summarize`，`retries=1`，真实失败原因是 `Unknown column 'window_start'`。
  - 问 5「用 ads_traffic_1d 查最近 7 天 PV/UV」→ 两次 `reflect`，`retries=2`，两次 `TABLE_NOT_ALLOWED`，如实回答"给不出来"并列出被拒的表与错误原文。
  - 来源：`docs/sprint/SPRINT_8.md` §9.5。
  - 另注：`PERFORMANCE.md` §4.2 记录了性能采集时的一次 `/ask` 也是 `retries=1`。**关于"最大重试次数 2 是否被跑满到 2"**：SPRINT_8 §9.5 的问 5 实测值为 `retries=2`，但任务要求里提到的 `retries=2` 需以该行为准；性能采集那一次是 1。

### 6.8 检索（5.8）：BM25 词法检索 + 显式同义词表

- 语料 65 条（metric 22 / convention 15 / topic 4 / layering 8 / table 16）；同义词表 **55 条**（以 `services/agent/knowledge/synonyms.json` 的 `synonyms` 字段实测为准），每条带理由；**零新增依赖**（只用标准库），向量库相关包数量 = 0。来源：`docs/sprint/SPRINT_9.md` §9.1、§9.2、§9.4。
- 同义词真实生效的实证：问「最近一周卖了多少钱」（**不含 GMV 字样**）→ `expanded: ('卖了多少钱', ['gmv','payment_amount'], w=1.00)` → 命中 `metric:gmv | sql/metadata/metrics.md:33`；负例「今天天气怎么样适合钓鱼吗」→ hits = []。来源：同文件 §9.3。
- 四条检索质量踩坑（都是只有真实问句能暴露的）：提到某词的文档压过定义该词的文档（加标题命中加成）；短词项 `amount` 是噪声（长词项的子串直接丢弃）；孤立中文单字是噪声（`算`/`的`/`是`）；markdown 表头被当成一条数据（在分隔行处回退上一行）。来源：同文件 §9.5。
- 一条自证诚实的取舍：**不声称语义检索**。文档写清这是 BM25 词法检索（含 idf 与长度归一化）。来源：同文件 §6 风险表末行。

### 6.9 MCP（5.9）

- 两条路径终点、守卫、账号完全相同，差别只在"谁来发起那次 HTTP"。**如果 MCP 变成"另一条能取数的路"，"不得绕过 sqlguard"这条铁律立刻就被绕过了——因为绕过的方式恰恰是"再实现一遍"。** 来源：`docs/sprint/SPRINT_10.md` §2。
- 4 个只读工具，与 Agent 的取数工具**逐名相等**；systemd 单元**没有** `EnvironmentFile=.env`；验收扫描单元文件 + `/proc/<pid>/environ` + 源码，都不许出现任何口令键名。来源：同文件 §3、§7。
- 端到端一致性实证：同一 SQL 经 MCP 路径与直接 HTTP 路径的结果**逐字段 IDENTICAL**，`executed_sql` 也相同；比对前先加**非空守卫**（避免"两边都空"的假通过）。来源：`AGENTS.md` §15.12；同文件 §7 第 5、6 条。
- 拒绝经 MCP 透传语义未丢：DELETE 的真实返回是 `is_error=False` + `structured_content={"ok":false,"error":{"code":"NOT_SELECT"}}`，底层 HTTP 400 同码。来源：`AGENTS.md` §15.12。
- 为什么不做"能连 MCP 就用 MCP"的自动降级：自动降级会让 MCP 路径在任何一次连接抖动后**悄悄消失**，而所有断言仍然全绿。来源：同文件 §5.2。

### 6.10 质量与监控（5.10）

- 25 条校验覆盖 7 类；每条打印 `[OK]/[FAIL]` 与**实测值**（不是只有结论）。来源：`docs/sprint/SPRINT_11.md` §4.1。
- **失败路径是可复现的证明**：`--proof-fail` 故意改坏期望值，两次断言都必须返回退出码 1，然后**再跑一次正常校验**证明失败来自断言而不是数据被改坏。来源：同文件 §1.2、§10.1。
- 一个"恒红比没有校验更糟"的实测：新鲜度阈值第一版定 26h，实测数据的事件时间最后到 `T 日 13:16`，而按日调度的批处理在 `T+1 凌晨` 才算进 ADS，稳态滞后 **31.8h**，四条新鲜度校验全红。最终定 48h（漏跑一整天会变成 50~56h，必然报警）。来源：同文件 §7.6。
- 监控：Prometheus v3.13.3（LTS）+ Grafana 13.2.2，`mem_limit` 各 256m、合计 512m（预算上限）；实测占用 prometheus 105 MiB / grafana 187 MiB；4/4 抓取目标 up；Doris FE 1564 条、BE 1019 条指标；Grafana 23 个面板、匿名只读。来源：`AGENTS.md` §15.11。
- 为什么禁用 3.14.0：它是最新版（2026-08-17），但 EOL 为 **2026-09-30**，只剩 3 天，不适合钉在毕设里。来源：`docs/sprint/SPRINT_11.md` §2。
- 一个"用健康端点验证凭据会得出相反结论"的实测：MinIO `/minio/health/live` 对不带凭据返回 200、对**正确**凭据返回 400、对错误凭据也返回 400——它根本不看 Authorization 头。正确判据是"真的做一次 S3 操作"，并且带对照组。**验证"某个值对不对"时，判据必须能区分对与错。** 来源：同文件 §7.13。

### 6.11 一次真实事故的完整复盘（5.11）

> **这一节要单列，是"工程判断力"最好的素材。**

- 事件：为绕过 `sync-subset.ps1` 的"Spark 运行中"守卫，写了一个"原子换树"同步脚本，远端步骤含 `mv /opt/data-platform /opt/data-platform.old` → `tar 解到 .new` → `mv .new 到位` → **`rm -rf .old`**。因为只打包了 `services/mcp` 一个子目录，`rm -rf .old` 把 **`.env`（唯一存有真实凭据的文件）、三个 venv（`.venv`/`.venv-agent`/`.venv-airflow`）、`airflow/airflow.env`** 一起删掉了；连带还删掉三个由 compose 挂载的第三方 JAR。
- 根因不是"环境问题"，而是**一个绕开守卫的决定**：守卫拦的是"运行中的脚本被半覆盖"，原子换树确实防住了"半覆盖"，却把**子集同步**变成了**整树替换**——子集之外的机器状态不再受保护。**绕过一个守卫时，必须重新论证它原本防的是什么，以及新方案是否引入了别的、可能更大的风险。**
- 连带损失的误导性症状（两条很有价值）：
  1. **Docker 会在目标文件不存在时把挂载点创建成空目录**。于是"文件丢失"在磁盘上表现为"一个空目录"，`ls -la` 看上去像有东西（有那一行）。判断挂载型文件是否存在要用 `[ -f ]` + `[ -s ]`。
  2. **报错指向的不是根因**：Metastore 起不来时报的是 `DatastoreDriverNotFoundException: com.mysql.cj.jdbc.Driver was not found in the CLASSPATH`——它说的是"CLASSPATH 配错了"，真相是"那个 JAR 文件被删了"。
- 沉淀出的硬规范（5 条）：
  1. 共享文件的权限必须按**所有读者**定：`.env` 必须是 `640 root:dpapi` 而不是 600（`data-platform-api` 以 `dpapi` 组身份运行、`data-platform-agent` 以 `dpagent` 用户运行，两者都需要组读）。实测后果：600 会让 Agent 起不来（`PermissionError: /opt/data-platform/.env`），而症状出现在"别人负责的服务"上——**故障现场与改动现场不在一起**。
  2. 禁止任何"原子换树 / 镜像式"同步；只覆盖需要覆盖的文件，用 `mv` 做**单文件**原子替换。
  3. 在确认凭据已落盘之前，禁止 `docker compose up / restart`（进程内存里可能还留着上一份凭据，重建容器会把**唯一的一份**丢掉）。
  4. 改共享编排文件（`deploy/**`、`docker-compose.yml`）必须先落创作副本再同步；验收时必须核对 live 与仓库副本的 **md5 一致**（实测 `b5197e5121e08d689daebb72d3e626e2`）。
  5. 会改机器状态的验收脚本，开头必须检查 `.env` 是否存在，不存在就 `exit 1`；反之，只做解析/只读的入口（如 `--list`）不应依赖 `.env`。
- 来源：`AGENTS.md` §15.11；`docs/sprint/SPRINT_12.md` §8.2；`docs/sprint/SPRINT_11.md` §7.12、§10.2、§10.3、§10.4；`docs/DEVELOPMENT_LOG.md` 第 1529~1546 行。
- 事故的价值（写作收尾句）：它是"共享状态 + 并行改动"这一类风险的完整样本——一个**看起来更干净**的动作（清理旧树 / 收紧权限）跨过服务边界就变成了故障。三条判据：**这个文件还有谁在读？这个目录里还有什么我没看见的状态？我现在做的动作，别人会不会依赖它的旧值？**

---

## 7. 测试与验证（第 6 章）

### 7.1 测试体系（6.1）

- 三层：单元测试（零外部依赖）/ 冒烟测试（服务未启动时优雅跳过）/ 验收脚本（每 Sprint 一个，分步 `[OK]/[FAIL]` + 统计 + 失败 `exit 1`）。
- 全量 pytest：**353 passed / 0 failed / 0 skipped / 3 xfailed**（268.43 s，用 `.venv-agent` 解释器）。单元测试 `-m unit`：**215 passed / 138 deselected / 3 xfailed**。
- **这个数字必须连着"漂移史"一起读**（这是本章最有价值的写作点）：同一份测试在事故期间出现过四种面目——`346 passed / 7 failed`（hive-metastore 容器刚重启、9083 未监听，7 条失败全是 `ConnectException`，测试连断言都没跑到）→ `331 passed / 22 skipped`（metastore 恢复，Doris ADS 表被清空，冒烟用例优雅跳过）→ `306 passed / 47 skipped` → `353 passed / 0 failed / 0 skipped`（ADS 装载完成）。**数字变好不是因为我们改了什么，而是因为外部依赖恢复了。** 来源：`docs/sprint/SPRINT_12.md` §8.4、§8.4.1。
- 补充一条：`skipped` 的语义在这里很重要——冒烟测试在服务未就绪时**优雅跳过而不是误报失败**（`AGENTS.md` §8.3），所以"跳过数上升"是环境信号，不是代码信号。
- 新增的两个测试文件：`tests/test_mcp.py`（28 passed）、`tests/test_sql_guard_adversarial.py`（63 条 = 60 passed + 3 xfailed，按**绕过手法**分类而非按关键字分类）。来源：`docs/sprint/SPRINT_12.md` §2.2、§8.3。

### 7.2 逐窗口对账（6.2）

本章的核心证据表，直接复用第 6.3 节的两组数字，但要换成"验证方法"的叙述：对账区间如何截断（尾部留 3 分钟安全边界）、单边窗口如何单独统计（`realtime_only_windows` / `batch_only_windows`，使"差异为 0"有边界可核）、判据列清单（交易域 5 项、流量域 7 项 + 3 个比率列独立判据）。来源：`docs/sprint/SPRINT_5.md` §9.2。

### 7.3 验收矩阵（6.3）

一张大表，把 13 个 Sprint 的验收脚本与结果列全（**这是报告里最有说服力的一张表**）：

| Sprint | 主题 | 验收脚本 | 结果 | 来源 |
| --- | --- | --- | --- | --- |
| 0 | 项目初始化与基础数据环境 | `verify-sprint-0.sh` | 5 个核心服务 healthy；health-check 5/5；pytest 51 passed（24 单元 + 27 冒烟）；31,660 条事件；down/up 后数据保留 | `AGENTS.md` §15.1 |
| 1 | Kafka + Flink + Doris 实时数仓 | `verify-sprint-1.sh` | 8 作业各 1 实例；8 Routine Load RUNNING、errorRows=0；health-check 11/11；smoke 74 passed；GMV 精确相等 | `AGENTS.md` §15.2 |
| 6 | 数据后台 + 前后端 | `verify-sprint-6.sh` | 7/7 PASS；agent_ro 写操作被拒；API GMV == MySQL GMV；74 单元 + 21 接口冒烟；6 页面浏览器验收 | `AGENTS.md` §15.3 |
| 2 | Spark + Hive + 湖仓 | `verify-sprint-2.sh` | 8/8 PASS；逐表对账 1200/600/6000/5406/254；25 个 Parquet 对象；类型正确；幂等 | `AGENTS.md` §15.4 |
| 3 | 离线分层 + 批流交叉对账 | `verify-sprint-3.sh` | 8/8 PASS；DWD == ODS；**11458 窗口不一致 0**；pytest 172 passed；7 页面 | `AGENTS.md` §15.5 |
| 7 | LLM + Tool Calling | `verify-sprint-7.sh` | **49/49**；四类攻击被拒且原因正确；强制 LIMIT；网关契约；19 + 21 测试 | `AGENTS.md` §15.6 |
| 4 | Airflow 调度 + 流量域归档 | `verify-sprint-4.sh` | **40/0/0**；DAG 9 任务；归档 20000 行 == Kafka latest offset 合计；703 个 dt 分区；内存闸门主动拒绝一次 | `AGENTS.md` §15.8 |
| 5 | Iceberg + 流量域分层 | `verify-sprint-5.sh` | Iceberg 迁移校验**两次运行各按自身范围**：18 张表那次 `60/60`、23 张表复跑那次 `70/70`；DWD 21/21、DWS 20/20、ADS 36/36、对账 10/10；**19643 窗口不一致 0**；1 个 click_rate 异常窗口 | `AGENTS.md` §15、§15.12；`docs/sprint/SPRINT_5.md` §8.1、§9 |
| 8 | LangGraph Data Agent | `verify-sprint-8.sh` | **71/0/0**；langgraph 1.1.0（实际装到的）；回归 49/49 | `AGENTS.md` §15.12 |
| 9 | RAG + Metadata | `verify-sprint-9.sh` | **59/0/0**；语料 65 篇；向量库包数量 0 | `AGENTS.md` §15.12 |
| 10 | MCP | `verify-sprint-10.sh` | **84/0/0**；4 个只读工具；MCP 与直连逐字段 IDENTICAL | `AGENTS.md` §15.12 |
| 11 | Data Quality + Monitoring | `verify-sprint-11.sh` | **61/0/0**；质量校验 24 通过 / 0 失败 / 1 跳过；`--proof-fail` 两次退出码 1；Prometheus 4/4 up | `AGENTS.md` §15.11 |
| 12 | 测试 + 性能优化 | `verify-sprint-12.sh` | **32/0/0**；全量 pytest 353/0/0/3 xfail | `AGENTS.md` §15.12 |

> 写作提示：这张表要配一句说明——**"验收通过"的判据是脚本的退出码与实测值，不是"看起来跑通了"**。

### 7.4 性能基线（6.4）

必须先把**采样口径**写清楚，再给数字（这是 `PERFORMANCE.md` 第 0 节的做法，值得照抄这个顺序）：

| 项 | 取值 | 来源 |
| --- | --- | --- |
| 采样次数 | 每项 7 次（另有 1 次预热不计入） | `docs/PERFORMANCE.md` §0 |
| 统计量 | 中位数（同时给 min/max），不报平均值 | 同上 |
| 双端耗时 | 客户端 `wall_ms` + 服务端 `elapsed_ms` | 同上 |
| 采集路径 | 全部经本机回环，**不含公网链路**，不代表浏览器访问体验 | 同上 |
| 采集现场 | 2026-09-27 09:11~09:15；load 1.46 / 3.06 / 3.85；可用内存 3921 MB；Spark 作业 0 个；8 个 Flink sink 作业 RUNNING | `docs/PERFORMANCE.md` §0.1 |
| Agent 采样 | 3 次（含 1 次预热） | `docs/PERFORMANCE.md` §0 |

四类结果：

1. **只读接口**：点查 8.5 ms / 聚合 8.3 ms / 两表关联 11.7 ms；`/overview` 53.8 ms；`/meta/metrics` 1.7 ms；`/meta/tables` 43.2 ms；`/batch/reconcile` 21.4 ms；`/funnel` 8.4 ms；`/metrics/trade` 11.2 ms。服务端 `elapsed_ms` 仅 4~6 ms → **约 3~6 ms 是 HTTP + JSON 常数开销**。优化方向：`/overview` 一次拼了 6 条查询，应合并/并行，而不是优化 SQL（SQL 已经是 5 ms 级）。
2. **实时 vs 离线**：同口径聚合两侧都是 7.1 ms；类目排行 7.6 vs 8.0 ms（差 0.4 ms，小于 min/max 波动范围）→ **无可测量差异**。原因是两侧数据量在这两个聚合上同量级（实时 11459 行 / 离线 626 行），都在单 BE 内存/本地盘上，未到读放大的量级。**如实记录为"无显著差异"，不编造趋势。**
3. **批量作业**：一次成功运行 9 个任务耗时合计 **1992.3 s**，端到端约 81 分钟。固定成本（暂停 + 恢复）149.8 s；三个分层作业合计 1514 s，占 76%。对照的人工触发那次合计 1491.7 s、端到端约 25 分钟。**两个数都给出来，并把差异说明白，而不是只报小的那个**（`ads_layers` 有多次 attempt，与 `dws_layers` 结束时间之间有 48 分钟间隔）。
   - ⚠️ 一处保留观察：对照那次 `archive_behavior` 只有 **0.6 s**（主线那次是 363.3 s，相差 600 倍），且该 run 所有任务状态都是 `success`，**只能靠耗时异常发现**——这正是 Sprint 4 记录过的"归档阶段静默空转"的特征。已登记为待复核项，判据应以**行数与分区数**为准，不以"阶段返回成功 + 耗时短"为准。来源：`docs/PERFORMANCE.md` §3.2；`docs/sprint/SPRINT_12.md` §8.8。
4. **Agent 端到端**：`POST /ask` 中位数 **19187.5 ms**（min 18175.8 / max 20199.2）；检索 2.9 ms + 取数 15.4 ms = 18.3 ms，**占 0.1%**；规划 + 汇总（差值）≈ 19169 ms，占 **99.9%**。结论：**优化 Agent 延迟只能从减少 LLM 往返次数或换模型入手，优化 SQL 在 Agent 端到端延迟上几乎没有意义。**
   - **诚实边界（必须写）**：这个"规划 + 汇总"是**差值**，不是对模型内部推理时间的测量，它包含两次 DeepSeek API 调用的网络往返 + 服务端推理 + token 生成，无法在本机进一步分解。**不得**把它表述为"模型推理耗时"。来源：`docs/PERFORMANCE.md` §4.1、§4.3。

### 7.5 采集过程中发现的一处真实缺陷：派生表为空使 JOIN 静默返回 0 行（6.6）

**建议单列一节**，因为它示范了"性能采集顺手做数据校验"的价值：

- 现象：`dwd_trade_order_detail JOIN dim_product` 返回 **0 行**，而明细表有 6000 行。
- 定位：`SELECT COUNT(*) FROM ecommerce.dim_product → 0`；MySQL 侧 product = 600、user = 1200（事实源有数据）；两表 LEFT JOIN 抽样，`c.product_id` 全为 null。
- 危害：**静默返回 0 行比报错更危险**。HTTP 200、`row_count=0`、响应信封完整、`source.tables` 也正确——从"接口是否健康"的任何角度看都正常。只有把结果**与预期行数对照**才能发现问题。
- 当时的处置：只记录不修复（文件所有权边界）。**后续状态**：`DECISIONS.md` ⏳11 记录该问题**已修并已服务器验证**（提交 `b195d19` + 通配修正）：`--stage load` 后 12 张表行数全部通过，`dim_product 600 == 湖仓 600`、`dim_user 1200 == 湖仓 1200`；经只读服务查那条曾恒为 0 行的 JOIN → 返回 10 行真实类目 GMV（电脑办公 15,457,835.79），`source.tables` 正确列出两张表。
- 来源：`docs/PERFORMANCE.md` §5；`docs/DECISIONS.md` ⏳11。

---

## 8. 总结与展望（第 7 章）

### 8.1 工作总结（7.1）

按"数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据"五步各写一段，每段一句实测结论。

### 8.2 主要贡献（7.2）

建议归纳为三条（**要具体，不要形容词**）：

1. **把"批流一体"做成了可证伪的断言**：交易域 11458 个窗口、流量域 19643 个窗口逐窗口比对，不一致 0；并给出对账判据的设计过程（含两次判据打磨）。
2. **在 16 GB 单机上同时承载实时与离线**：错峰批处理 + 内存闸门（阈值 3000 MB，实测释放 1.65 GB），并在闸门真的拒绝过一次批处理时选择"拒绝而非硬跑"。
3. **Agent 的可信性由架构保证而非提示词保证**：进程边界（Agent 无数据库凭据）、回答附 `tables`/`executed_sql`/`docs`（带文件与行号）、反思重试可数（实测 `retries=1` 与 `retries=2` 各一次）、四类攻击被拒且原因正确、MCP 路径与直连路径逐字段 IDENTICAL。

### 8.3 不足（7.3）

**这一节不能写成道歉，要写成清单。** 逐条给"影响 / 现状 / 计划"（正文直接复用 `docs/thesis/DATA_AND_LIMITATIONS.md` 第二部分的表）。必写：

1. 合成数据（无真实业务分布）。
2. 明文 HTTP（传输不加密）。
3. Airflow SimpleAuthManager 仅供开发（口令明文、无轮换）。
4. 维表曾长期为空（已修，但暴露"静默 0 行"这类失效模式的检测缺口）。
5. 实时侧 1 个窗口 `click_rate` 缺陷未修（修复需 Kafka 全量重放，性价比不成立）。
6. 结构债：两份阶段 `case` 重复（`run-batch-pipeline.sh` 与 `submit-offline-job.sh`），未做重构。
7. `dim_*` 与 sqlguard 的历史缺口（UNION 反序元数据探测曾放行，已修；白名单数量文档与代码曾不一致 16 vs 19，已以代码为准）。
8. 无并发/压测、无 Iceberg 与 Doris 内表的查询延迟对比、无公网链路延迟（内存不允许 / 属独立任务）。

### 8.4 展望（7.4）

按"补短板"与"扩能力"两组写。补短板：真实数据源接入（CDC）、告警接入（Grafana alerting，数据源已就绪）、维表装载纳入常规流水线、结构债方案 A。扩能力：Iceberg 时间旅行的实际使用场景（当前只有快照数 > 0 的断言）、Agent 会话记忆与 checkpoint、向量检索替换点（`Retriever.retrieve(query) -> docs` 一个接口）。

来源：`docs/sprint/SPRINT_11.md` §10；`docs/sprint/SPRINT_12.md` §4.3、§7；`docs/PERFORMANCE.md` §7；`docs/sprint/SPRINT_9.md` §2.3。

---

## 9. 参考文献（References）

> **素材缺口。** 仓库中没有任何参考文献记录（无 BibTeX、无引用列表）。

可**有依据地**列入的条目类型（需作者补全著录信息）：

1. 各组件官方文档：Apache Kafka（KRaft）、Apache Flink（事件时间与窗口）、Apache Spark、Apache Doris（Routine Load / S3 TVF / UNIQUE KEY）、Apache Iceberg（表规范 v2、HiveCatalog）、Apache Airflow（3.x 任务执行 API）、Prometheus、Grafana。
2. 规范与文档：Model Context Protocol 规范（2026-07-28 版，本项目 `mcp==2.2.0` 支持该版，见 `docs/sprint/SPRINT_10.md` §4.1）；OpenAI 兼容的 Tool Calls 协议（`docs/sprint/SPRINT_8.md` §9.6.1 引用了它的强制约束）。
3. 本项目内部规范文件（作为"工程规范"引用，可单列一节"项目文档"）：`AGENTS.md`、`sql/metadata/metrics.md`、`docs/PROJECT_DESIGN_V1.md`、`docs/DECISIONS.md`、`docs/PERFORMANCE.md`。
4. BM25 原始文献（`docs/sprint/SPRINT_9.md` §3.2 使用了 k1=1.2、b=0.75 的标准参数）。

**注意**：第 4 条的文献著录信息（作者、年份、出处）需作者自行补齐，**不要从本仓库推断**。

---

## 10. 附录（Appendix）

### 附录 A：术语与缩略语

ODS / DWD / DWS / ADS / GMV / UV / PV / TUMBLE / AQE / HMS / TVF / MCP / RAG / BM25 / xfail。

### 附录 B：完整验收矩阵与关键实测数字台账

直接引用 `docs/thesis/DATA_AND_LIMITATIONS.md` 的"数字 → 来源文件"表（该表与本大纲第 7.3 节互补：本节按 Sprint 组织，附录按数字组织）。

### 附录 C：已知限制汇总表

引用 `docs/thesis/DATA_AND_LIMITATIONS.md` 第二部分（按"影响 / 现状 / 计划"三列）。

### 附录 D：复现命令清单

| 目标 | 命令 | 来源 |
| --- | --- | --- |
| 实时链路验收 | `bash scripts/verify-sprint-1.sh` | `AGENTS.md` §13 |
| 离线分层 + 对账 | `bash scripts/batch-mode.sh && bash scripts/verify-sprint-3.sh` | `AGENTS.md` §13 |
| Iceberg 迁移 | `bash scripts/batch-mode.sh --stage iceberg-migrate` | `docs/sprint/SPRINT_5.md` §8.1 |
| Agent 验收 | `bash scripts/deploy-agent.sh && bash scripts/verify-sprint-7.sh` | `AGENTS.md` §15.6 |
| 质量校验 | `bash scripts/run-quality-checks.sh --with-lake` | `docs/sprint/SPRINT_11.md` §6 |
| 失败路径证明 | `bash scripts/run-quality-checks.sh --proof-fail` | `docs/sprint/SPRINT_11.md` §1.2 |
| 性能采集 | `bash scripts/perf/measure-latency.sh all` | `docs/PERFORMANCE.md` §6 |

### 附录 E：图表清单

| 编号 | 图/表名 | 数据来源 |
| --- | --- | --- |
| 图 2-1 | 总体分层架构 | `docs/PROJECT_DESIGN_V1.md` §2.1 |
| 图 2-2 | 端到端数据流五段 | 同文件 §2.2 |
| 图 4-1 | 部署分层（数据层容器 / 服务层宿主机） | `docs/DECISIONS.md` D1 |
| 图 4-2 | 内存预算与错峰模式时序 | `docs/sprint/SPRINT_3.md` §4.4、§8.1 |
| 图 5-1 | 实时链路（4 topic → 8 作业 → Doris 8 表） | `docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` §2 |
| 图 5-2 | 离线四层与对账表 | `docs/sprint/SPRINT_3.md` §3 |
| 图 5-3 | Agent 图（6 节点 8 边 三重上界） | `docs/sprint/SPRINT_8.md` §3.1、§9.3 |
| 图 5-4 | MCP 两条路径 | `docs/sprint/SPRINT_10.md` §2 |
| 表 5-1 | Iceberg 迁移逐表行数（23 张） | `docs/sprint/SPRINT_5.md` §8.1、§9.1 |
| 表 5-2 | 批流对账结论（两个域） | `docs/sprint/SPRINT_3.md` §7.2；`SPRINT_5.md` §9.2 |
| 表 5-3 | 三类指标运算规则 | `sql/metadata/metrics.md` §3.1 |
| 表 6-1 | 验收矩阵（13 个 Sprint） | 本大纲 §7.3 |
| 表 6-2 | 性能基线四类 | `docs/PERFORMANCE.md` §1~§4 |
| 图 6-1 | 只读接口延迟对比柱状图 | `docs/PERFORMANCE.md` §1.2 |
| 图 6-2 | Agent 耗时分解（0.1% vs 99.9%） | `docs/PERFORMANCE.md` §4.2 |
| 图 6-3 | 批量作业 9 任务耗时堆叠图 | `docs/PERFORMANCE.md` §3.1 |

> 图表约定：所有图表下方标注数据来源文件与采集日期（性能类图表标注"2026-09-27 09:11~09:15，n=7 中位数，本机回环"）。

---

## 11. 篇幅与进度建议

| 章节 | 建议页数 | 优先级 |
| --- | --- | --- |
| 摘要 + 绪论 | 5 | 高 |
| 相关技术 | 6 | 中 |
| 需求分析 | 5 | 中 |
| 系统设计 | 10 | 高 |
| 关键实现 | 20 | **最高**（差异化价值集中在此） |
| 测试与验证 | 12 | **最高** |
| 总结与展望 | 5 | 高 |
| 参考文献 + 附录 | 7 | 中 |
| 合计 | 约 70 | |

写作顺序建议：先写第 5 章与第 6 章（素材最全、最难编），再回头写第 3、4 章，最后写摘要（摘要必须最后写，否则数字对不上）。
