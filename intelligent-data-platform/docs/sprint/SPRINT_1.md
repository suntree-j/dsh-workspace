# Sprint 1 设计：Kafka + Flink + Doris 实时数仓

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> Sprint：1
> 状态：✅ **已实现并在腾讯云服务器通过验收**（2026-09-26）
> 实现与本文档的偏差统一记录在第 11 节（先改文档、再改代码）
> 前置：Sprint 0 已完成（MySQL / Kafka / MinIO / Doris 全部 healthy）

---

## 1. Sprint 1 目标

```text
Kafka ──► Flink ──► Doris (DWD / DWS / ADS)
                        │
                        ├── 实时 GMV
                        ├── 实时订单量 / 支付量
                        ├── 实时退款额
                        └── 实时用户数（UV）
```

**只做这一条链路。** 明确不引入：Spark、Hive、HDFS、Airflow、Iceberg、
LangGraph、RAG、MCP、Vue、Spring Boot、FastAPI、Prometheus、Grafana。

---

## 2. 技术选型与版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Apache Flink | `1.20.1`（`scala_2.12` / `java17`） | 镜像站可获取；1.20.x 是 Doris Flink Connector 支持矩阵中的高版本 |
| flink-sql-connector-kafka | `3.4.0-1.20` | 从 Maven Central 获取（服务器实测可达）。**注意：`3.2.0-1.20` 并不存在**，最初按记忆写下的版本在 Maven Central 上是 404，已按官方发布列表改为 `3.4.0-1.20` |
| Doris | `4.1.4`（已有） | 不改版本 |
| Kafka | `4.2.1`（已有） | 不改版本 |

### 2.1 关键选型决策：不用 Doris Flink Connector

**决策**：Flink 的聚合结果**写回 Kafka**，再由 Doris 的
**Routine Load** 原生导入 Doris，**不使用** `doris-flink-connector`。

**理由**：

1. Doris Flink Connector 需要额外下载并维护版本匹配的 JAR
   （connector 版本 ⟷ Doris 版本 ⟷ Flink 版本三者绑定），
   升级任一组件都要重新验证；
2. `Routine Load` 是 Doris **内置**的 Kafka 导入能力，
   零额外依赖、官方长期支持，且天然具备 exactly-once 与断点续传；
3. 保留 Kafka 作为中间层，便于后续 Spark/Iceberg 复用同一份指标流。

**代价**：多一个 Kafka topic（`metrics_*`）。这是可接受的。

**备选方案**（若后续需要 Flink 直写 Doris）：
引入 `org.apache.doris:flink-doris-connector-1.20`，已确认该 artifact 存在。

---

## 3. 分层设计

### 3.1 ODS（贴源层）

**不建 ODS 表**。Sprint 1 的数据源是 Kafka 事件流，Flink 直接消费，
不落 ODS。ODS 层留给 Sprint 3 从 MySQL/HDFS 落地的贴源数据。

### 3.2 DWD（明细层）

把 Kafka 事件清洗、补维后落成明细表。

| 表 | 来源 | 粒度 | 说明 |
| --- | --- | --- | --- |
| `dwd_trade_order_detail` | `order_event` + 商品维表 | 一行一订单 | 事件时间、用户、商品、金额 |
| `dwd_trade_payment_detail` | `payment_event` | 一行一支付 | 支付方式、状态、金额 |
| `dwd_trade_refund_detail` | `refund_event` | 一行一退款 | 退款金额、原因 |
| `dwd_traffic_behavior_detail` | `behavior_event` | 一行一行为 | 行为类型、设备、省份 |

> Doris 中实现方式：Kafka topic `dwd_*` → Routine Load → Doris `dwd_*` 表。
> 由 Flink 负责「清洗 + 补维 + 字段裁剪」，即 Flink 是 DWD 的生产者。

### 3.3 DWS（汇总层）

按主题做轻度聚合，**1 分钟滚动窗口**。

| 表 | 维度 | 指标 | 窗口 | 实现状态 |
| --- | --- | --- | --- | --- |
| `dws_traffic_overview_1m` | 无（全局） | UV、PV、各行为计数 | 1 min | ✅ 已实现 |
| `dws_trade_category_1m` | 商品类目 | 订单数、下单金额 | 1 min | ⏳ 推迟到 Sprint 3 |
| `dws_trade_user_level_1m` | 会员等级 | 订单数、下单金额、支付金额 | 1 min | ⏳ 推迟到 Sprint 3 |

> **为什么推迟两个 DWS 表**：Sprint 1 的 ADS 已经按「窗口 × 类目」产出
> `ads_realtime_category_1m`，再建一个口径相同的 `dws_trade_category_1m`
> 属于**同一份数据的两个副本**，不产生任何新信息，却会带来两个口径漂移的风险。
> 会员等级维度的聚合依赖维表 join（见第 6 节），同样留到 Sprint 3 与
> `dim_user` 一起做。Sprint 1 只保留流量域这一张真正被 ADS 复用的 DWS 表。

### 3.4 ADS（应用层）

直接面向指标看板，**1 分钟滚动窗口**。

| 表 | 指标 | 说明 |
| --- | --- | --- |
| `ads_realtime_trade_1m` | GMV、订单量、下单用户数、客单价、支付笔数/金额、支付成功率、退款笔数/金额、退款率 | 交易域**一张宽表** |
| `ads_realtime_traffic_1m` | UV、PV、各行为计数、点击率/加购率/购买转化率 | 流量域 |
| `ads_realtime_category_1m` | 窗口 × 类目：订单数、GMV、件数、类目客单价 | 类目域 |

> **为什么不是 4 张（gmv / payment / refund / user）**：
> 设计稿按指标域拆成 4 张表，实际实现合并为 3 张宽表，理由有两点：
> 1. 交易域的 4 类指标**共享同一个窗口骨架**（`window_start`），拆表后
>    看板要 join 4 张表才能画出「今天这一分钟发生了什么」，而宽表一次查询即可；
> 2. 订单 / 支付 / 退款是三条独立事件流，若拆成 3 张表，前端必须处理
>    「某窗口只有订单没有支付」的空行；合成宽表后这类语义在**写入侧**
>    一次说清（可加指标补 0、比率指标留 NULL，见 `sql/metadata/metrics.md`）。
>
> 表的**粒度**与设计稿一致：一行一个 1 分钟窗口。

---

## 4. 指标口径定义（唯一权威）

> 原则：**同一指标只能有一个定义**，实时与离线必须遵守同一口径。
> 本表是 Sprint 3 离线链路对齐的依据，也是 Sprint 9 RAG 的知识源。

| 指标 | 英文名 | 口径 | 计算方式 |
| --- | --- | --- | --- |
| GMV | `gmv` | 窗口内**已创建订单**的成交金额合计 | `SUM(order_event.amount)` where `event_type='ORDER_CREATED'` |
| 订单量 | `order_cnt` | 窗口内订单创建事件数 | `COUNT(*)` where `event_type='ORDER_CREATED'` |
| 客单价 | `avg_order_amount` | GMV / 订单量 | `SUM(amount)/COUNT(*)` |
| 支付笔数 | `payment_cnt` | 窗口内支付成功事件数 | `COUNT(*)` where `event_type='PAYMENT_SUCCESS'` |
| 支付金额 | `payment_amount` | 窗口内支付成功金额合计 | `SUM(amount)` |
| 支付成功率 | `payment_success_rate` | 支付成功 / (成功+失败) | 见下 |
| 退款笔数 | `refund_cnt` | 窗口内退款发起事件数 | `COUNT(*)` where `event_type='REFUND_CREATED'` |
| 退款金额 | `refund_amount` | 窗口内退款金额合计 | `SUM(refund_amount)` |
| 退款率 | `refund_rate` | 退款金额 / 支付金额 | 见下 |
| UV | `uv` | 窗口内去重用户数 | `COUNT(DISTINCT user_id)` |
| PV | `pv` | 窗口内行为事件总数 | `COUNT(*)` |

**重要口径约定**：

1. **GMV 以「订单创建」为准**，不以支付为准（与多数电商口径一致，可在看板另出「支付 GMV」）。
2. 时间语义统一用**事件时间（`event_time`）**，不用处理时间 —— 保证
   乱序与重放时结果一致。
3. 退款率与支付成功率的分母若为 0，结果置 `NULL` 而非 0（避免误导）。
4. 金额统一 `DECIMAL(18,2)`。

---

## 5. 数据流

```text
                        ┌──────────────────────────────┐
   data-generator       │  MySQL ecommerce (Sprint 0)   │
        │               │    user / product (维表)       │
        │               └───────────────┬──────────────┘
        │  写事件                        │ 一次性初始化（Sprint 1 用脚本导入）
        ▼                               ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Kafka                                                    │
   │  事件 topic：order_event / payment_event /                │
   │              refund_event / behavior_event                │
   │  指标 topic：metrics_dwd_order / metrics_dwd_payment /    │
   │              metrics_dwd_refund / metrics_dwd_behavior /  │
   │              metrics_ads_realtime                         │
   └───────────────┬─────────────────────────┬───────────────┘
                   │ 消费事件                  │ Routine Load
                   ▼                          │
   ┌───────────────────────────────┐         │
   │ Flink 1.20.1 (SQL)             │         │
   │  1) Kafka source（事件时间 +    │         │
   │     watermark）                │         │
   │  2) 维表关联（Doris 商品/用户）  │         │
   │  3) 1 分钟滚动窗口聚合          │         │
   │  4) 结果写回 Kafka（指标 topic） │         │
   └───────────────┬───────────────┘         │
                   │                          │
                   └──────────┬───────────────┘
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Doris 4.1.4                                              │
   │   dwd_trade_order_detail / dwd_trade_payment_detail /     │
   │   dwd_trade_refund_detail / dwd_traffic_behavior_detail   │
   │   dws_trade_category_1m / dws_trade_user_level_1m /       │
   │   dws_traffic_overview_1m                                 │
   │   ads_realtime_gmv_1m / ads_realtime_payment_1m /         │
   │   ads_realtime_refund_1m / ads_realtime_user_1m           │
   └─────────────────────────────┬───────────────────────────┘
                                 │ 只读查询
                                 ▼
                        指标验证 / 冒烟测试
```

---

## 6. 维表方案

Sprint 1 需要「商品类目」与「会员等级」两个维度。

**设计稿方案**：把 MySQL 的 `product` / `user` 同步到 Doris，
Flink 用 **lookup join** 关联。

**实际实现（Sprint 1）**：**不做 lookup join**，改用事件里的冗余维度字段。

| 维度 | 设计稿 | 实际实现 |
| --- | --- | --- |
| 商品类目 `category_name` | Doris `dim_product` + lookup join | 生成器写事件时冗余带上 |
| 会员等级 `user_level` | Doris `dim_user` + lookup join | 生成器写事件时冗余带上（Sprint 3 用于 DWS） |
| 省份 `province` | 同上 | 同上 |

**为什么改**（属于实现期的架构决策，已记录在 `docs/DEVELOPMENT_LOG.md`）：

1. 维表 join 会引入**启动时序依赖**：Flink 作业启动时 Doris 维表必须已就绪，
   否则作业起不来甚至静默丢维度。Sprint 1 的目标是先把「事件 → 窗口 → 指标」
   跑通，不应同时引入外部系统依赖；
2. 订单/行为事件本身携带商品与用户上下文（下单时点的类目、等级），
   这在数仓里是**合法的退化维度（degenerate dimension）**做法，
   而且比 lookup join 更准确 —— lookup join 拿到的是"现在"的维度值，
   不是"事件发生当时"的值（缓慢变化维问题）；
3. 维表 join 能力留给 Sprint 3 的 DWS 层（`dws_trade_user_level_1m`），
   届时 `dim_product` / `dim_user` 已由离线链路同步好。

> `sql/doris/10_dwd_tables.sql` 里的 `dim_product` / `dim_user` 表结构已按设计稿建好，
> 只是 Sprint 1 尚未用它们做 join（表在，能力留到 Sprint 3）。

---

## 7. 验证方案

| 验证项 | 方法 |
| --- | --- |
| Flink 作业运行 | JobManager/TaskManager healthy，作业状态 RUNNING |
| DWD 落库 | Doris `dwd_*` 表行数与 Kafka topic 累计偏移量一致（允许延迟） |
| DWS/ADS 指标正确性 | 用 MySQL 同口径 SQL 对账，误差在可解释范围内 |
| Routine Load 状态 | `SHOW ROUTINE LOAD` 全部为 RUNNING 且无 error 行 |
| 端到端 | `generate_events` → 等待窗口触发 → 查询 ADS 表非空 |
| 冒烟测试 | 新增 `tests/smoke/test_realtime.py`，纳入 pytest |

### 7.1 对账口径说明（实际采用）

实时链路的窗口基于**事件时间**，MySQL 侧是**全量事实**；由于事件时间
就是业务时间（`orders.create_time` / `payment.payment_time`），
**所有窗口的指标求和必然等于 MySQL 的全量口径**，因此对账采用**精确相等**，
不设"允许偏差"：

```text
SUM(ads_realtime_trade_1m.order_cnt)   == COUNT(ecommerce.orders)
SUM(ads_realtime_trade_1m.gmv)         == SUM(ecommerce.orders.amount)
SUM(ads_realtime_trade_1m.payment_cnt) == COUNT(ecommerce.payment WHERE status='SUCCESS')
SUM(ads_realtime_trade_1m.refund_cnt)  == COUNT(ecommerce.refund)
SUM(ads_realtime_category_1m.gmv)      == SUM(ecommerce.orders.amount)
SUM(ads_realtime_traffic_1m.pv)        == behavior_event topic 消息总数
COUNT(dwd_trade_order_detail)          == COUNT(ecommerce.orders)   （UNIQUE KEY 幂等）
```

**为什么能做到精确相等**：可加指标在每个窗口内求和，窗口不重叠且覆盖全天，
因此逐窗求和回到全量。若某次对账**不相等**，说明确实丢了数据或重复计算，
必须定位，而不是用"允许偏差"掩盖。

> 前提：**源 topic 里只有一代事件**。Flink source 使用
> `scan.startup.mode='earliest-offset'`（可重放历史），若把同一批事件重复生产进
> 源 topic，窗口指标会按事件条数累加。因此验收脚本提供 `--replay`：
> 重建源 + 下游 topic 后重新生成，得到"恰好一代"的规范数据集。
> 该现象与改进方案见第 11 节第 7 条。

---

## 8. 交付物（实际清单）

```text
docker-compose.yml                    + flink-jobmanager / flink-taskmanager
                                        flink-sql-gateway / flink-jobs
infrastructure/flink/
  ├── Dockerfile                      基于 flink:1.20.1，内置 Kafka SQL connector
  ├── conf/config.yaml                内存 / slot / checkpoint 配置
  ├── lib/download-connector.sh       获取 flink-sql-connector-kafka-3.4.0-1.20.jar
  ├── sql/01_source_tables.sql        4 个 Kafka source（事件时间 + watermark）
  ├── sql/02_dwd_sink_tables.sql      4 个 DWD sink（普通 kafka sink）
  ├── sql/03_metric_sink_tables.sql   4 个指标 sink（upsert-kafka + PRIMARY KEY）
  ├── sql/04_dwd_jobs.sql             4 个 DWD 清洗作业
  ├── sql/05_metric_jobs.sql          4 个窗口聚合作业（DWS + ADS）
  └── init/01-submit-jobs.py          清场 + 提交 + session 保活
sql/doris/
  ├── 10_dwd_tables.sql               DWD 明细表 + 维表
  ├── 11_dws_tables.sql               DWS 汇总表
  ├── 12_ads_tables.sql               ADS 指标表
  └── 13_routine_load.sh              8 个 Routine Load 作业（幂等）
sql/metadata/
  ├── metrics.md                      指标口径字典（唯一权威）
  └── kafka_topics.md                 事件格式定义
scripts/
  ├── health-check.sh                 Sprint 0 + Sprint 1 全量健康检查
  ├── verify-sprint-1.sh              Sprint 1 验收（含 --replay 重放）
  └── cancel-flink-jobs.sh            取消全部作业与会话（重新提交前必跑）
tests/smoke/
  ├── conftest.py                     冒烟测试共用前置条件
  └── test_realtime.py                实时链路冒烟测试（47 个用例）
docs/sprint/
  ├── SPRINT_1.md                     本文档
  └── SPRINT_1_VERIFICATION_STATUS.md 验收证据
```

> 与设计稿的差异：设计稿写的是 `01_dim_tables.sql` / `02_kafka_sources.sql` /
> `03_sink_tables.sql` / `04_jobs.sql` / `init/01-submit-jobs.sh`，
> 实际按"sink 与 job 分开、DWD 与指标分开"拆成 5 个 SQL 文件
> （一个文件只做一件事，便于单条语句定位失败），
> 提交脚本用 **Python 而不是 bash** —— 容器内没有 curl，
> 且 Python 标准库能精确处理 SQL 文本里的引号与 `$` 符号。

---

## 9. 风险与备选

| 风险 | 影响 | 备选 |
| --- | --- | --- |
| Flink 镜像较大（~1.5 GB），镜像站较慢 | 拉取耗时 | 用 SSH 反向隧道共享本地代理（Sprint 0 已验证有效） |
| Flink 内存占用与 Doris 争抢（16 GB 机器） | 稳定性 | 限制 Flink TaskManager 内存（`taskmanager.memory.process.size`），必要时减少 slot |
| Routine Load 因某分区无数据而暂停 | 指标延迟 | 监控 `SHOW ROUTINE LOAD`，或调整 `max_batch_interval` |
| 维表 JOIN 增加复杂度 | 调试成本 | 先实现不带维表的 ADS 核心指标，维表关联作为第二步 |
| 事件时间水位线导致窗口延迟输出 | 验证等待时间长 | 窗口设为 1 分钟 + `allowedLateness`，验证时等待 2~3 分钟 |

---

## 10. 完成标准（Definition of Done）

- [x] Flink JobManager / TaskManager 正常运行且健康检查通过
- [x] Doris 中 DWD / DWS / ADS 表全部创建（8 张 + 2 张维表）
- [x] Routine Load 任务全部 RUNNING，无 error 行（8/8）
- [x] Flink 作业持续运行（8 个 sink 作业各 1 个实例），指标 topic 有数据产出
- [x] ADS 指标表有数据且与 MySQL **精确对账一致**（GMV 51890375.77）
- [x] `tests/smoke/test_realtime.py` 通过（47 个用例）
- [x] `scripts/health-check.sh` 增加 Flink 检查项且全部 [OK]（10 项）
- [x] `sql/metadata/metrics.md` 指标口径文档完成
- [x] `docs/DEVELOPMENT_LOG.md` 追加 Sprint 1 记录
- [x] 未引入 Sprint 2+ 的任何组件

逐项证据见
[`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md`](SPRINT_1_VERIFICATION_STATUS.md)。

---

## 11. 实现偏差与决策记录

> 原则：**先改文档、再改代码**。本节记录实现期与设计稿不一致的地方及原因。

| # | 设计稿 | 实现 | 原因 |
| --- | --- | --- | --- |
| 1 | connector `3.2.0-1.20` | `3.4.0-1.20` | 该版本在 Maven Central 不存在（404），属记忆错误，已按官方发布列表更正 |
| 2 | DWS 3 张表 | 只做 `dws_traffic_overview_1m` | 类目/会员等级 DWS 与 ADS 口径重复，且依赖维表 join；留到 Sprint 3 |
| 3 | ADS 4 张表（按指标域） | ADS 3 张宽表（按主题） | 交易域指标共享同一窗口骨架，宽表一次查询即可；空窗口语义在写入侧统一 |
| 4 | 维度用 lookup join 补 | 事件冗余维度字段 | 避免 Sprint 1 引入 Doris 外部依赖与启动时序问题；且冗余字段是"事件发生时点"的维度，语义更准确 |
| 5 | 指标 topic 命名 `metrics_*` | `dwd_*` / `dws_*` / `ads_*`（与 Doris 表同名） | 与分层表名一一对应，排查时不必在两套命名之间翻译 |
| 6 | `scripts/realtime-check.sh` | 并入 `scripts/health-check.sh` + `scripts/verify-sprint-1.sh` | 检查项集中在同一个健康检查入口，避免运维要记两个脚本 |
| 7 | （未涉及） | 新增 `scripts/cancel-flink-jobs.sh`，提交脚本启动时自动清场 | **实测踩坑**：SQL Gateway 是 session 模式，重启 `flink-jobs` 容器不会取消旧作业，旧作业既占 slot 又会让重放的事件累加到旧窗口状态上导致指标翻倍 |
| 8 | 对账"允许偏差" | **精确相等** | 事件时间即业务时间，逐窗口求和必然回到全量；能精确就不该留偏差口径 |

### 11.1 已知限制（Sprint 2+ 改进项）

| 限制 | 影响 | 计划 |
| --- | --- | --- |
| 源 topic 使用 `earliest-offset`，重复生产同一批事件会使窗口指标累加 | 重放同一批事件时指标翻倍 | 在 source 后加 `ROW_NUMBER() OVER (PARTITION BY event_id)` 去重算子，使整条链路对重复事件幂等（Sprint 2 或数据质量阶段） |
| 流式去重/窗口状态无 TTL 上限 | 长跑后状态持续增长 | 配置 `table.exec.state.ttl` |
| 无 checkpoint 恢复演练 | 作业失败后从 Kafka 位点重建，窗口会重算 | Sprint 11（治理）补 savepoint/恢复演练 |
| 实时链路与离线链路尚未对账 | 实时数仓可信度只与 MySQL 对过账 | Sprint 3 用 Spark 产出同名指标并交叉对账 |
