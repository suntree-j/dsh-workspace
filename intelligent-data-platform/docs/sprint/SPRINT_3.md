# Sprint 3 — 离线数仓分层建模（ODS / DWD / DWS / ADS）与批流交叉对账

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 依据：[`docs/PROJECT_DESIGN_V1.md`](../PROJECT_DESIGN_V1.md) 第 4 章（分层与数据模型）、
> 第 8 章 Roadmap 第 3 行
> 前置：Sprint 0（基础环境）、Sprint 1（实时链路）、Sprint 2（Spark + Hive 湖仓）、
> Sprint 6（只读数据服务，顺序前移）
> 状态：🚧 进行中

---

## 1. 目标

Sprint 2 已经把 MySQL 落到了湖仓的 **ODS** 层（贴源、未清洗）。
Sprint 3 要做的是把 ODS 往上建成完整的分层模型，并回答本项目最核心的一个问题：

> **同一条业务事实，走实时链路和走离线链路，算出来的指标是不是同一个数？**

因此本 Sprint 有两条并行目标：

| # | 目标 | 验收方式 |
| --- | --- | --- |
| 1 | 建立 **ODS → DWD → DWS → ADS** 四层离线模型 | 四层表可在 Hive Metastore 中查到，且逐层行数关系自洽 |
| 2 | 离线 ADS 与实时 ADS **同名同口径、可精确对账** | 同窗口逐条比对，差异为 0（金额精确到分） |

### 1.1 为什么"交叉对账"是本 Sprint 的核心

`AGENTS.md` 第 1.1 节写的是「先工程，再智能」，`sql/metadata/metrics.md` 第 6 节
写的是「同一指标只能有一个定义」。这两句话能不能成立，**只能靠实测证明**：

```text
如果实时说 GMV 100 万、离线说 95 万
   ↓
Agent（Sprint 7+）引用哪一个都是错的
   ↓
整条智能链路失去意义
```

所以本 Sprint 不满足于"离线也能算出 GMV"，而是要求
**逐窗口比对、差异逐条列出、差异为 0 才算通过**。

---

## 2. 事实核查（动手前的现状）

> 遵循 `AGENTS.md` 第 11 章：先理解，再规划。

| 项 | 实测结果 | 对设计的影响 |
| --- | --- | --- |
| MySQL `ecommerce` 5 表 | `orders` 6000 / `payment` 5406 / `refund` 254 / `user` 1200 / `product` 600 | ODS 抽取基准 |
| ODS 层（Sprint 2 落地） | 行数与 MySQL 完全一致（6000 / 5406 / 254 / 1200 / 600） | 可直接作为 DWD 输入 |
| 实时 DWD（Doris `ecommerce`） | `dwd_trade_order_detail` 6000、`dwd_trade_payment_detail` 5406、`dwd_trade_refund_detail` 254、`dwd_traffic_behavior_detail` 40000 | 与 MySQL 1:1，具备对账基础 |
| 实时 ADS（Doris `ecommerce`） | `ads_realtime_trade_1m` 11459 行、`gmv` 合计 **51,890,375.77**、`order_cnt` 6000、`refund_cnt` 254 | 与 MySQL 精确一致 |
| 实时 ADS 时间跨度 | `2024-10-25 08:16:00` ~ `2026-09-26 11:37:00`（分钟级稀疏） | 对账窗口必须取"两端都已封闭"的区间 |
| 订单时间范围 | `2024-10-25` ~ `2026-09-26`（约 700 天有数据） | 离线按天分区，跨度大但每天量小 |
| `payment.status` | `PAID` 5152 / `CANCELLED` 101 / `CREATED` 493 / `REFUNDED` 254 | 见第 3.2 节的支付事件映射 |
| Kafka `__consumer_offsets` | 存在，说明四个实时作业仍在消费 | 实时链路仍在追赶，对账需设水位截断 |

### 2.1 关键结论

1. **实时链路与 MySQL 事实源目前完全一致**（订单 6000 / 支付 5406 / 退款 254、
   GMV 51,890,375.77），说明 Sprint 1 的链路没有漂移，可以作为对账基准。
2. **ODS 与 MySQL 一致**，DWD 可以直接从 ODS 构建，不需要重新抽取。
3. 实时 ADS 是**分钟级稀疏**的（只有发生过事件的分钟才有行），
   而离线按天聚合。两者要能逐条比对，离线 ADS 必须**同样输出 1 分钟粒度**，
   另出一张按天的表供看板使用。这是本 Sprint 的一个重要设计决定。

---

## 3. 分层设计

```text
MySQL（唯一事实源）
  │
  │ Sprint 2：Spark JDBC 抽取（贴源）
  ▼
ODS   lakehouse.ods_user / ods_product / ods_orders / ods_payment / ods_refund
  │   结构与源系统一致，不做清洗
  │
  │ Sprint 3：Spark SQL 清洗 / 去重 / 维度补全
  ▼
DWD   lakehouse.dwd_user_detail          dwd_product_detail
      lakehouse.dwd_trade_order_detail   dwd_trade_payment_detail
      lakehouse.dwd_trade_refund_detail
  │   一行一条业务事实，维度补全，按 dt 分区
  │
  │ Sprint 3：Spark SQL 轻度聚合（按天 / 按天×维度）
  ▼
DWS   lakehouse.dws_trade_overview_1d    dws_trade_category_1d
      lakehouse.dws_trade_user_1d
  │
  │ Sprint 3：Spark SQL 汇总为指标口径
  ▼
ADS   lakehouse.ads_batch_trade_1m       ← 与实时 ads_realtime_trade_1m **逐窗口可比**
      lakehouse.ads_batch_trade_1d       ← 看板 / 报表用
      lakehouse.ads_batch_category_1m    ← 与实时 ads_realtime_category_1m 可比
      lakehouse.ads_batch_category_1d
      lakehouse.ads_reconcile_trade_1m   ← 对账结果表（差异留痕）
```

### 3.1 分层职责边界

| 层 | 只做 | 不做 |
| --- | --- | --- |
| ODS | 类型归一、落地 | 不做清洗、不做维度补全 |
| DWD | 去重、空值过滤、维度补全、统一命名 | 不做聚合 |
| DWS | 按主题轻度聚合（天粒度） | 不做跨主题加工、不做比率指标 |
| ADS | 按指标口径出数（含比率），对齐实时表结构 | 不做明细查询 |

**为什么 DWS 不出比率指标**：比率的分子分母来自不同聚合，
放在 DWS 会出现"两个 DWS 表各算一半比例"的口径分裂风险。
DWS 只出**可加指标**，比率一律在 ADS 层按 `metrics.md` 的定义计算。

### 3.2 事件口径映射（本 Sprint 最容易出错的地方）

实时链路的指标是在 **Kafka 事件**上算的，离线链路的指标是在
**MySQL 业务表**上算的。两张表要能对账，必须先证明**事件与业务行一一对应**：

| 实时事件（Kafka） | 计数口径 | 离线等价来源（MySQL/ODS） | 映射规则 |
| --- | --- | --- | --- |
| `ORDER_CREATED` | 1 事件 = 1 订单 | `orders` 表 1 行 | `event_time = orders.create_time` |
| `PAYMENT_SUCCESS` | 成功支付 | `payment` 行且 `pay_time IS NOT NULL` | `event_time = payment.pay_time` |
| `PAYMENT_FAILED` | 失败支付 | `payment` 行且 `pay_time IS NULL` | `event_time = orders.create_time` 后的尝试时间 |
| `REFUND_CREATED` | 1 事件 = 1 退款 | `refund` 表 1 行 | `event_time = refund.refund_time` |

> `PAYMENT_FAILED` 的 `event_time` 在业务表里没有独立字段
> （MySQL 只有 `pay_time`，失败则为空）。处理方式见第 4.3 节，
> **不允许**用 `create_time` 冒充支付尝试时间，否则对账必然错位。

### 3.3 ADS 与实时表的字段级对齐

离线 `ads_batch_trade_1m` 的字段名、类型、顺序**必须**与
`ecommerce.ads_realtime_trade_1m` 完全一致（见
[`sql/doris/12_ads_tables.sql`](../../sql/doris/12_ads_tables.sql)），
否则"同名同口径"就只是口号。空值约定同样沿用 `metrics.md` 第 2.1 节：

```text
可加指标（gmv / order_cnt / ...）无事件时 = 0
比率指标（avg_order_amount / payment_success_rate / refund_rate）分母为 0 时 = NULL
```

---

## 4. 实现方案

### 4.1 作业组织

沿用 Sprint 2 的"Spark 作业容器"模式，按层拆成独立 SQL 与独立作业，
每层可单独重跑、单独对账：

```text
infrastructure/spark/jobs/
├── _common.py                   公共工具（建表 / 执行分层 SQL / 自检器 / 精确去重）
├── extract_mysql_to_lake.py     Sprint 2：MySQL → ODS
├── build_dwd.py                 Sprint 3：ODS → DWD
├── build_dws.py                 Sprint 3：DWD → DWS
├── build_ads.py                 Sprint 3：DWD → ADS
└── reconcile_batch_realtime.py  Sprint 3：批流对账
infrastructure/spark/sql/
├── 01_dwd_build.sql             Sprint 3：ODS → DWD
├── 02_dws_build.sql             Sprint 3：DWD → DWS
├── 03_ads_metrics.sql           Sprint 3：DWD → ADS（指标公式）
└── 04_reconcile.sql             Sprint 3：逐窗口对账 + 批次汇总
sql/hive/
├── 01_ods_tables.sql            Sprint 2
├── 02_dwd_tables.sql            Sprint 3
├── 03_dws_tables.sql            Sprint 3
├── 04_ads_tables.sql            Sprint 3
└── 05_reconcile_tables.sql      Sprint 3（对账结果表）
sql/doris/
└── 30_batch_ads_tables.sql      Sprint 3（离线指标库 lakehouse_ads）
scripts/
├── lib/spark-job.sh             5 个阶段的作业提交封装（凭据注入只有一处实现）
├── lib/memory-guard.sh          内存闸门（见第 8 节事故记录）
├── run-batch-pipeline.sh        一键跑 抽取→DWD→DWS→ADS→对账→装载
├── batch-mode.sh                错峰批处理（暂停实时链路 → 跑批 → 恢复）
├── load-batch-to-doris.sh       用 Doris S3() TVF 直读 Parquet 装载
├── setup-swap.sh                swap 兜底
├── submit-offline-job.sh        单阶段重跑（--stage）
└── verify-sprint-3.sh           8 步验收
```

**为什么用 PySpark 脚本 + `spark.sql()` 而不是纯 `.sql` 文件**：
需要在作业内做**自校验**（每层跑完立即断言行数关系，
不一致就以非 0 退出），这与 Sprint 2 抽取作业的做法保持一致，
也让 `verify-sprint-3.sh` 有真实的失败信号可用。
纯 SQL 交给 `spark-sql -f` 跑就只能"跑完不报错"，无法回答"算得对不对"。

### 4.2 离线结果如何进 Doris

实时链路的结果天然在 Doris（Flink → Kafka → Routine Load），
离线链路的结果在湖仓（Parquet on MinIO）。服务层要同时查两者，就必须把离线结果搬进 Doris。

**选型：Doris 的 `S3()` 表函数直读，不用 Stream Load / 外部 loader。**

```sql
INSERT INTO lakehouse_ads.ads_batch_trade_1m (列清单)
SELECT 列清单 FROM S3(
    "uri" = "s3://lakehouse/warehouse/ads/batch_trade_1m/**/*.parquet",
    "format" = "parquet", "provider" = "S3",
    "s3.endpoint" = "http://minio:9000", "s3.region" = "us-east-1",
    "s3.access_key" = "...", "s3.secret_key" = "...",
    "use_path_style" = "true");
```

理由：Stream Load 要求把文件推到 BE 的 HTTP 端口，就得再起一个
"读 S3 → POST" 的 loader 进程（多一个要维护、要监控、可能挂掉的东西）；
TVF 一条 SQL 解决，不引入任何新组件、新端口、新进程。

### 4.3 幂等性

- DWD / DWS / ADS 全部按分区覆盖（`INSERT OVERWRITE` + 动态分区）；
- 表为 EXTERNAL TABLE，`DROP` 不删数据；
- Doris 侧事实表先 `TRUNCATE` 再 `INSERT`（各自动作原子），
  对账汇总表只追加不清空（历史批次就是证据链）；
- 对账明细按 `window_start` 唯一键覆盖，同一窗口只有一个对账结论。

### 4.4 内存约束（本 Sprint 最硬的约束）

服务器 16 GB，实时链路常驻约 13.7 GB。离线 Spark 批处理的驱动跑在**宿主机**上
（client 模式），加执行器合计约 1.5 GB —— 直接叠加会打穿内存，
**实测导致整机失联**（第 8 节事故 1）。

因此离线批处理采用**错峰执行**：

```text
scripts/batch-mode.sh
  1) 暂停 Flink 栈（jobmanager / taskmanager / sql-gateway / jobs）  → 释放约 2.8 GB
  2) 内存闸门检查（可用 < 3 GB 直接拒绝启动）
  3) 跑离线流水线
  4) 恢复 Flink 栈并跑 health-check.sh 验证实时链路回到健康
```

暂停期间事件继续堆在 Kafka，Flink 从已提交位点继续消费，
重复部分由 Doris UNIQUE KEY 幂等覆盖 —— **不丢数据**，
代价只是"暂停期间的实时曲线会延后追上"。

---

## 5. 边界与已知缺口（诚实记录）

### 5.1 行为域（UV / PV / 转化率）暂无法离线对账

`behavior_event` 只存在于 Kafka，MySQL 中没有对应业务表
（Sprint 0 的生成器设计如此：行为埋点本身就是事件，不落业务库）。
因此：

```text
交易域（GMV / 订单 / 支付 / 退款）  →  MySQL 有事实表  →  离线可算、可对账  ✅
流量域（UV / PV / 点击 / 加购 / 转化率）→  MySQL 无事实表  →  离线无源，本 Sprint 不对账  ⚠️
```

处理原则（`AGENTS.md` 第 10.3 节「失败即失败」）：

- **不伪造**离线流量指标；
- 离线 ADS 只覆盖交易域与类目域；
- 流量域的离线化留到 **Sprint 4**：由 Airflow 调度"Kafka → 归档到湖仓 ODS"
  的归档作业（Kafka 的事件才是流量域的事实源），
  归档落地后即可用同一套 DWD/DWS/ADS 逻辑补齐并纳入对账。

> 这是**设计缺口**而非实现失败，必须写进文档与论文，
> 不能靠"看起来也有了"糊过去。

### 5.2 不做的事

```text
❌ 不引入 Airflow（Sprint 4）
❌ 不引入 Iceberg（Sprint 5）
❌ 不改动实时链路的口径与表结构（Sprint 1 已验收）
❌ 不为流量域编造离线口径
```

---

## 6. 验收标准

| # | 验收项 | 判定方式 |
| --- | --- | --- |
| 1 | 四层表齐全 | 湖仓 ODS 5 / DWD 5 / DWS 3 / ADS 6，Doris 离线库 6 张 |
| 2 | 层间行数自洽 | DWD = ODS（逐表相等）；ADS 分钟窗口数 = 事件分钟数；天表 = 分钟表上卷 |
| 3 | 类型正确 | 金额全部 `decimal(18,2)`，四层无 `double`/`float` |
| 4 | 幂等 | 重跑 ADS 层后行数与 GMV 不变 |
| 5 | **批流对账** | 逐窗口比对，不一致窗口 = 0；两侧 GMV 合计精确相等 |
| 6 | 服务装载 | Doris 离线库行数 == 湖仓；只读账号 `agent_ro` 可查且写操作被拒 |
| 7 | 回归 | health-check 11/11；数据服务 `/health` 正常；看板首页 200 |
| 8 | 自动化测试 | `pytest`（单元 + 冒烟）全部通过 |

一键复现：

```bash
bash scripts/batch-mode.sh            # 错峰跑完整离线流水线（含对账与装载）
bash scripts/verify-sprint-3.sh       # 8 步验收
```

---

## 7. 验收结果

```text
✅ bash scripts/verify-sprint-3.sh   8/8 PASS
```

| 步骤 | 结果 |
| --- | --- |
| 1 表清单 | ODS 5 / DWD 5 / DWS 3 / ADS 6；Doris `lakehouse_ads` 6 张 |
| 2 层间行数 | DWD == ODS 逐表相等；ADS 窗口 11459 == 事件分钟 11459；天表 GMV == 分钟表 |
| 3 类型正确 | 无 double/float；`amount` / `gmv` = `decimal(18,2)`，`refund_rate` = `decimal(10,4)` |
| 4 幂等 | 重跑 ADS 层后窗口数与 GMV 不变 |
| 5 **批流对账** | 对账区间 `2024-10-25 08:16:00 ~ 2026-09-26 11:35:00`，窗口 **11458** 个，**不一致 0 个**；GMV 实时 `51890375.77` == 离线 `51890375.77` |
| 6 服务装载 | 6 张表 Doris 行数 == 湖仓；`agent_ro` 可查，写操作被 Doris 拒绝 |
| 7 回归 | health-check 11/11；`/data/api/health` = ok；看板首页 200 |
| 8 自动化测试 | `pytest` **172 passed**（含新增的离线接口与对账契约用例；用项目 `.venv` 解释器） |

### 7.1 各层行数（真实数据）

| 层 | 表 | 行数 |
| --- | --- | --- |
| ODS | ods_user / ods_product / ods_orders / ods_payment / ods_refund | 1200 / 600 / 6000 / 5406 / 254 |
| DWD | dwd_user_detail / dwd_product_detail / dwd_trade_order_detail / dwd_trade_payment_detail / dwd_trade_refund_detail | 1200 / 600 / 6000 / 5406 / 254 |
| DWS | dws_trade_overview_1d / dws_trade_category_1d / dws_trade_user_1d | 626 / 2834 / 5851 |
| ADS | ads_batch_trade_1m / ads_batch_trade_1d / ads_batch_category_1m / ads_batch_category_1d | 11459 / 626 / 5998 / 2834 |
| ADS | ads_reconcile_trade_1m / ads_reconcile_summary | 11458 / 1 |

### 7.2 核心指标对账（精确到分）

| 指标 | 实时链路（Flink → Doris） | 离线链路（Spark → 湖仓） | 差异 |
| --- | --- | --- | --- |
| GMV | 51,890,375.77 | 51,890,375.77 | **0.00** |
| 订单量 | 6000 | 6000 | 0 |
| 支付成功笔数 / 金额 | 5287 / 45,615,110.02 | 5287 / 45,615,110.02 | 0 / **0.00** |
| 支付失败笔数 | 119 | 119 | 0 |
| 退款笔数 / 金额 | 254 / 1,825,511.73 | 254 / 1,825,511.73 | 0 / **0.00** |

> 两个引擎、两套计算框架、两条独立链路，同一个数。
> 这是本项目"批流一体"最直接、也最难伪造的证据。

---

## 8. 踩坑记录

### 8.1 事故：批处理打穿宿主机内存，整机失联

**现象**

第一次跑分层批处理时，服务器（16 GB）在几分钟内完全失去响应：
SSH 无法建立会话（TCP 能连、banner 交换超时）、ICMP 无响应、
nginx 与 FastAPI 全部超时。只能从云控制台**强制重启**。

**原因（三层叠加）**

1. 实时链路常驻约 **13.7 GB**，空闲只剩约 **2.0 GB**；
2. Spark 驱动在 **client 模式**下跑在宿主机上，默认 1 GB 堆，
   加执行器 1 GB = 约 2 GB —— 正好把余量吃干；
3. 宿主机**没有 swap**，内核无处回收页面，
   于是内存压力不是表现为"变慢"，而是**整机假死**。

先出现的症状其实是 Doris BE 拒绝了所有查询：

```text
errCode = 2, detailMessage = (doris-be)[MEM_ALLOC_FAILED]Create Expr failed because
[E11] Allocator sys memory check failed ... sys available memory 510.64 MB
(= 510.64 MB[proc/available] - 0[reserved] - 0B[waiting_refresh]),
low water mark 799.46 MB, warning water mark 1.56 GB
```

即"看板报数据仓库暂时不可用"——**这才是第一现场**，只是当时误以为是 Doris 的偶发问题，
没有立刻停下来算内存账，才演变成整机失联。

**影响**

- 离线批处理被迫中断（DWD 已成功，DWS/ADS 未跑）；
- 实时链路与看板不可用约 20 分钟，直到控制台强制重启；
- 数据无损失（重启后所有容器自动恢复，`health-check.sh` 11/11）。

**处理（四层防护，从"事前拒绝"到"事后兜底"）**

| 层 | 措施 | 文件 |
| --- | --- | --- |
| 1 拒绝启动 | 可用内存 < 3000 MB 直接拒绝跑批；已有 Spark 作业时拒绝叠加 | `scripts/lib/memory-guard.sh` |
| 2 错峰执行 | 跑批前暂停 Flink 栈（释放约 2.8 GB），跑完自动恢复并验证健康 | `scripts/batch-mode.sh` |
| 3 限制上限 | 驱动/执行器各 768m；`spark-submit` 容器补 `mem_limit`（原先无上限） | `scripts/lib/spark-job.sh`、`docker-compose.yml` |
| 4 兜底 | 4 GB swap（swappiness=10）给内核留回收空间；**故意不写 fstab**，因为 Doris BE 不允许在有 swap 的机器上启动 | `scripts/setup-swap.sh` |

**留下的教训**

> 在一台已经跑满的机器上，"再加一个批处理"不是加一个进程，
> 而是把系统的安全余量直接归零。**先算内存账，再动手。**

### 8.2 分层 SQL 不能挂在 `/opt/jobs` 下

`spark-submit` 服务已把 `infrastructure/spark/jobs` 挂在 `/opt/jobs:ro`（只读）。
Docker **不允许在只读挂载点下再创建挂载点**：

```text
error mounting ".../infrastructure/spark/sql" to rootfs at "/opt/jobs/sql":
  mkdirat .../opt/jobs/sql: read-only file system
```

→ 分层 SQL 改挂到平级目录 `/opt/layer-sql`，
由 `spark-submit --conf spark.jobs.layerSqlDir=/opt/layer-sql` 告知作业。

### 8.3 `COUNT(DISTINCT ...)` 是近似算法，不能拿来做相等断言

**现象**：ADS 层自检报

```text
[check] FAIL 1d order_user_cnt == DWD 全局去重   5883  (期望 1195)
[check] FAIL 类目 1d 条数 == 1m 条数              2834  (期望 5998)
```

看起来像"离线少算了一半数据"，实际数据完全正确。

**原因**：Spark SQL 的 `COUNT(DISTINCT x)` 默认走 **HyperLogLog 近似实现**
（误差上限约 5%）。它把"不同（日 × 类目）组合"算成 5998，
而真实值是 2834 —— **是断言用的函数不准，不是数据错**。
排查花了两轮，因为"2834 对 5998"这种数字太像真的丢数据了。

**处理**

- **做相等断言**的地方一律改用精确实现 `size(collect_set(x))`
  （见 `infrastructure/spark/jobs/_common.py` 的 `exact_distinct`）；
- **指标列本身**仍保留 `COUNT(DISTINCT user_id)`，因为它要与实时链路的
  同语义实现对齐 —— 换成精确去重反而会造成"两边算法不同而对不上"；
- 这一取舍写进了 `03_ads_metrics.sql` 的注释。

### 8.4 另外两处"断言写错"（同样会误导排查方向）

| 写错的断言 | 为什么错 | 正确写法 |
| --- | --- | --- |
| `SUM(1d.order_user_cnt)` == DWD 全局去重用户数 | 去重计数**不可加**：同一用户可能多天下单，逐日之和（5883）必然大于全局去重（1195） | 逐日核对 `1d.order_user_cnt` == DWD 当日去重；另加"全局 ≥ 单日最大值"的单调性检查 |
| 类目 `1d` 行数 == 类目 `1m` 行数 | 两者粒度不同：`1m` 是"分钟 × 类目"，`1d` 是"日 × 类目" | 比 `1d` 行数与 `1m` 的 **（日 × 类目）组合数** |

> 断言写错比没有断言更危险：它会把人引向"数据错了"的错误结论，
> 浪费时间去查一个并不存在的问题。

### 8.5 Doris 的 `CREATE DATABASE ... COMMENT`

```text
ERROR 1105 (HY000): errCode = 2, detailMessage =
mismatched input 'COMMENT' expecting {<EOF>, ';'}
```

Doris **不支持** MySQL 那种 `CREATE DATABASE x COMMENT '...'` 写法，
库级说明只能写成 SQL 注释。

### 8.6 Doris S3() TVF 的 `*.parquet` 不递归

离线 ADS 按 `dt` 分区写出，Parquet 在子目录里：

```text
<表目录>/dt=2026-09-26/part-*.parquet
```

```sql
-- 匹配到 0 个文件，静默返回空结果集（不报错）
SELECT COUNT(*) FROM S3("uri" = ".../batch_trade_1m/*.parquet");   -- → 0
-- 递归匹配，正确
SELECT COUNT(*) FROM S3("uri" = ".../batch_trade_1m/**/*.parquet"); -- → 22918（含残留 staging）
```

更麻烦的是**后续报错会误导**：`INSERT` 因为源为空而拿不到列，报的是

```text
Unknown column 'window_start' in 'table list' in PROJECT clause
```

看起来像列名写错，实际是"一个文件都没匹配上"。

### 8.7 Doris 不允许在事务里 TRUNCATE

```text
ERROR 1105 (HY000): errCode = 2, detailMessage =
This is in a transaction, only insert, update, delete, commit, rollback is acceptable.
```

最初的装载写法是 `BEGIN; TRUNCATE ...; INSERT ...; COMMIT;`，第一张表就失败。
→ 改成 `TRUNCATE` 与 `INSERT` 分开执行，各自原子。

### 8.8 吞掉 stderr 会让排查多花好几轮

上面 8.5 / 8.7 两个错误的定位都被拖延了，原因相同：
`doris_q()` 里写了 `2>/dev/null`，把 `Using a password ...` 这类无害警告
藏起来的同时，也把真实错误一起吞了，脚本只打印自己那句"装载失败"。

→ 改为**按内容过滤**已知警告：

```bash
... mysql ... "$@" 2> >(grep -v 'Using a password' >&2)
```

### 8.9 Spark 被中断后留在 S3 上的临时目录会被重复读取

带分区的 `INSERT OVERWRITE` 会先写
`<表目录>/.spark-staging-<uuid>/_temporary/.../dt=.../part-*.parquet`，
成功后再原子搬到 `dt=...` 并删除 staging。
**作业中途被杀时 staging 目录会留在 S3 上**，而下游 Doris 的
`**/*.parquet` 递归读取会把这份残留**再读一遍** —— 表现为"装载行数凭空翻倍"。

→ `build_ads.py` 在开始前调用 `clean_stale_spark_temp()` 清理
`.spark-staging-*` 与 `_temporary`（只清这两类，不碰正式分区）。

### 8.10 流水线阶段顺序即依赖

`load` 要装载的 6 张表里包含 `ads_reconcile_trade_1m` / `ads_reconcile_summary`，
它们是**对账阶段的产物**。曾经把 `load` 排在 `reconcile` 之前，
结果前 4 张表装载成功、第 5 张"装载失败"（Parquet 目录此时还不存在）。

→ `STAGES` 固定为 `ods dwd dws ads reconcile load`。

---

## 9. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-26 | V1.0 | 建立 Sprint 3 任务书：四层离线模型 + 批流交叉对账 |
| 2026-09-26 | V1.1 | 回填验收结果（8/8 PASS、11458 窗口零差异）与 10 条踩坑记录；补充内存事故与错峰执行方案 |
