# Sprint 5 设计：Iceberg Lakehouse

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 前置：Sprint 0 / 1 / 2 / 3 / 4 / 6 / 7 均已验收通过
> 状态：**进行中**

---

## 1. Sprint 5 目标

Sprint 2 的湖仓用的是**Hive 外部表 + Parquet**（`EXTERNAL TABLE ... STORED AS PARQUET`）。
它能用，但缺三样东西：

```text
问题 1  没有 ACID          → 写入中途失败会留下半份数据（靠"重跑覆盖"兜底，
                             但那要求每次整分区重写）
问题 2  没有 schema 演进    → 源表加字段只能改 DDL，历史数据与新数据的一致性靠人管
问题 3  没有时间旅行        → 无法回答"昨天的表长什么样"，
                             出问题时也无法回滚到某个快照
```

Sprint 5 做两件事：

```text
A. 引入 Iceberg 作为湖仓的表格式，把 ODS / DWD / DWS / ADS 四层迁上去
B. 把**流量域的分层建模**一并做掉（原属 Sprint 4，见决策记录第 6 条）
```

> **为什么 B 并到这里做**：Iceberg 会改变湖仓的存储与表管理方式。
> 先按 Parquet 把流量域分层建好、再整体迁到 Iceberg，等于同一件事做两遍，
> 且迁移期间容易出现"两套表并存、口径分叉"。
> 归档（有源）才是 Sprint 3 记录缺口的实质，那部分 Sprint 4 已经完成。

### 1.1 明确不做

```text
❌ LangGraph / RAG / MCP（Sprint 8/9/10）
❌ Prometheus / Grafana（Sprint 11）
❌ 把实时链路也换成 Iceberg —— Doris 仍是实时查询层，
   Iceberg 只服务离线/湖仓侧（"批流一体"里两者分工不变）
❌ 引入 Flink-Iceberg 连接器 —— 当前没有"流式写湖"的需求，
   归档走 Spark 批读（Sprint 4 已验证）。不为了"看起来完整"而加。
```

---

## 2. 版本与兼容性

### 2.1 已查证（Maven Central 官方数据）

| 项 | 值 | 查证方式 |
| --- | --- | --- |
| Apache Iceberg | **1.11.0** | Maven Central `org.apache.iceberg:iceberg-spark-runtime-3.5_2.12`，2026-05-15 发布 |
| 对应 Spark | **3.5.x** | 构件名里的 `3.5` 即 Spark 次版本；本项目 Spark 3.5.7 |
| Scala | **2.12** | 构件名里的 `2.12`；与 Spark 3.5 官方发行版一致 |

获取方式沿用 Sprint 4 刚建立的模式：
`infrastructure/spark/lib/download-connector.sh` 增加 Iceberg 一个 jar，
Dockerfile COPY 进 `/opt/spark/jars/`。

### 2.2 待实测确认（不写成结论）

```text
⚠️ Iceberg 1.11.0 与本机 Hive Metastore 3.1.3 的兼容性
     Iceberg 的 HiveCatalog 要求 Hive Metastore 2.x/3.x。
     3.1.3 在支持范围内，但**必须实测**（Sprint 4 的教训：
     "在支持范围内"不等于"这次能用"）。
     实测点：Spark 能否用 HiveCatalog 建出第一张 Iceberg 表。

⚠️ 运行镜像里的 Java 版本
     Iceberg 1.11 需要 JDK 8/11/17/21。apache/spark:3.5.7 自带哪个
     要实测确认（`java -version`），不猜。

⚠️ S3A 与 Iceberg 的 FileIO
     Iceberg 走 S3A 需要 hadoop-aws（镜像里已有）。
     但 Iceberg 的 S3 FileIO 与 Hadoop S3A 是两条路径，
     需要确认 `warehouse=s3a://...` 这种写法可用的组合。
```

### 2.3 阶段 1 查证结果（已实测，2026-09-27）

| 待测点 | 实测结果 | 结论 |
| --- | --- | --- |
| Spark 镜像的 JDK | **Temurin OpenJDK 11.0.27**（`JAVA_HOME=/opt/java/openjdk`） | ✅ 在 Iceberg 1.11 支持的 8/11/17/21 之内 |
| Hive Metastore | 容器 `running (healthy)`，9083 端口在监听；经 `scripts/spark-sql.sh` 执行 `SHOW DATABASES` 返回 `default` / `lakehouse` | ✅ HiveCatalog 的连通前提成立（**真正的建表仍待阶段 3 验证**） |
| Maven Central | `iceberg-spark-runtime-3.5_2.12-1.11.0.jar` HEAD 返回 **HTTP 206**、1.09s | ✅ 可下载 |
| 磁盘 | 根分区余 **55 GB**（42% 已用） | ✅ 足够 Iceberg 元数据与快照 |
| 内存 | 可用 **2481 MB** < 闸门 3000 MB | ⚠️ 预算未变，**必须继续走错峰模式** |

**迁移对照基准**（Iceberg 迁移后必须逐表对齐这些数字）：

```text
ODS   ods_user 1200   ods_product 600   ods_orders 6000
      ods_payment 5406   ods_refund 254   ods_behavior_event 20000
DWD   dwd_trade_order_detail 6000   dwd_trade_payment_detail 5406
      dwd_trade_refund_detail 254   dwd_user_detail 1200   dwd_product_detail 600
DWS   dws_trade_overview_1d 626   dws_trade_category_1d 2834   dws_trade_user_1d 5883
ADS   ads_batch_trade_1m 11459   ads_batch_trade_1d 626   ads_batch_category_1d 2834
      （ads_batch_category_1m 查询超时未取到，阶段 4 再补）
```

**对账窗口范围已确认可比**：

```text
实时侧 ecommerce.ads_realtime_traffic_1m
     19644 个分钟窗口，2024-10-02 21:53:00 → 2026-09-26 13:16:00
离线侧 lakehouse.ods_behavior_event
     20000 行，        2024-10-02 21:53:33 → 2026-09-26 13:16:13
```

两者的时间范围**几乎完全重合** —— 意味着流量域可以**全窗口对账**，
不需要像原计划那样打折覆盖区间。这是个好消息，但要在阶段 6 用数字证明。

### 2.4 阶段 1 顺带发现的一条通则

实测时我裸调了 `spark-sql`，立刻报：

```text
com.amazonaws.SdkClientException: Unable to load AWS credentials
```

原因：**S3A 凭据由 `scripts/spark-sql.sh` 通过 `--conf` 注入**，
裸调 `spark-sql` 必然缺凭据。这不是缺陷，是调用方式错了。

> **项目里已经有第二个"必须走包装脚本"的工具了**：
> `airflow` 必须经 `scripts/airflow.sh`（否则静默落到 sqlite），
> `spark-sql` 必须经 `scripts/spark-sql.sh`（否则缺 S3A 凭据）。
>
> 通则：**凡是有包装脚本的工具，都必须走包装脚本**；
> 裸调它们不会报"你少了一层配置"，而是报一个看起来像别的问题的错误
> （sqlite 说"需要迁移"、S3A 说"缺 AWS 凭据"），
> 都会把人往错误方向带。

---

## 3. 架构设计

### 3.1 Catalog 选型

| 方案 | 评价 |
| --- | --- |
| **Hive Metastore**（`type=hive`） | **选它**。本机已经有 Hive Metastore 3.1.3 在跑，Sprint 2 的 Parquet 外部表也在它里面；复用意味着**两种表格式共用同一个目录**，迁移期可以并存对照 |
| REST Catalog | 需要额外部署一个服务（+1 个常驻进程）。本机可用内存只够勉强跑现有栈，不值得 |
| Hadoop Catalog | 用文件系统当目录，没有中心元数据；无法与既有 HMS 表共存，迁移期会很别扭 |

**配置形态**（写在 `spark-defaults.conf` 或提交参数里，二者选一，任务书阶段不定死）：

```text
spark.sql.catalog.lakehouse            org.apache.iceberg.spark.SparkCatalog
spark.sql.catalog.lakehouse.type       hive
spark.sql.catalog.lakehouse.uri        thrift://hive-metastore:9083
spark.sql.catalog.lakehouse.warehouse  s3a://lakehouse/warehouse
```

### 3.2 表命名与迁移策略

**不原地改**：Parquet 外部表保持不动，Iceberg 表用**不同的库名**建，
这样迁移期可以逐表对照行数与金额，出问题能立刻退回。

```text
现状（Sprint 2/3/4）        Sprint 5 新增
lakehouse.ods_*       →     lakehouse_iceberg.ods_*   （Iceberg 表）
lakehouse.dwd_*       →     lakehouse_iceberg.dwd_*
lakehouse.dws_*       →     lakehouse_iceberg.dws_*
lakehouse.ads_*       →     lakehouse_iceberg.ads_*
                            lakehouse_iceberg.dwd_traffic_*   ← 流量域（新）
                            lakehouse_iceberg.dws_traffic_*
                            lakehouse_iceberg.ads_traffic_*
```

> **为什么换库名而不是原地换格式**：
> Iceberg 的 `CREATE TABLE ... USING iceberg` 与 Hive 的
> `CREATE EXTERNAL TABLE ... STORED AS PARQUET` 是两种表，
> 同名替换会覆盖元数据、且无法回滚。
> 用新库名 + 逐表对照，是"可验证、可回滚"的最小步做法。

### 3.3 分区与属性

```text
ODS/DWD  分区 dt（由事件时间推导，与 Parquet 版一致）
DWS/ADS  分区 dt / part_dt（沿用现有粒度）
表属性    write.format.default = parquet
          write.parquet.compression-codec = snappy
          （与现有 Parquet 的编码保持一致，便于对照体积）
```

### 3.4 流量域分层（B 部分）

源：`lakehouse.ods_behavior_event`（Sprint 4 归档，20000 行）

```text
DWD  dwd_traffic_behavior_detail   清洗后的行为明细
        （去重 event_id、补维度、统一 device/province）

DWS  dws_traffic_overview_1d       按天汇总（PV、UV、设备分布）
     dws_traffic_funnel_1d         按天漏斗（VIEW/CLICK/CART/BUY 各步人数）

ADS  ads_traffic_1m                1 分钟窗口的 PV / UV —— **用于与实时对账**
     ads_traffic_1d                按天的 UV / PV / 转化率
```

> **`ads_traffic_1m` 是本次对账的关键表**：
> 实时侧 `ecommerce.ads_realtime_traffic_1m` 已有 19644 个分钟窗口，
> 离线侧用同一份事件、同样的 TUMBLE(1min) 窗口算出来的结果**必须逐窗口一致**，
> 这才叫"批流一体"，否则只是"两套数"。

### 3.5 对账的口径陷阱（必须处理）

```text
PV 是可加指标   → 逐窗口比对成立
UV 是去重指标   → **逐窗口比对成立，但窗口之间不能相加**
                  （Sprint 7 的 Agent 已经主动指出过这一点）
转化率是比率     → 必须"先合计分子分母再相除"，不能对逐窗口比率求平均
```

对账作业要**按同一口径**计算两侧，并在结论里写明哪些指标可比、
哪些只是参考 —— 不允许为了"看起来全绿"而放宽口径。

---

## 4. 内存预算

与 Sprint 3/4 完全相同的约束，**没有任何放宽**：

```text
可用内存      约 2.4 GB（实时链路常驻时）
批处理闸门    MIN_AVAILABLE_MB_FOR_BATCH = 3000
执行方式      必须先暂停实时链路（释放约 1.65 GB）再跑批
```

新增的 Iceberg 作业**不引入常驻进程**（只是 Spark 作业换表格式），
因此内存预算不变。**若实测发现 Iceberg 写入内存显著高于 Parquet，
必须如实记录并调整批处理规模，而不是放宽闸门。**

---

## 5. 阶段划分

```text
阶段 1  查证 2.2 的三个待实测点（HMS 兼容 / JDK 版本 / FileIO 组合）
阶段 2  iceberg-spark-runtime 进镜像 —— **先验证镜像里有 jar 再跑作业**
          （Sprint 4 的教训：构建成功 ≠ 容器换了镜像）
阶段 3  建 Iceberg 库与 ODS/DWD/DWS/ADS 表（DDL 落盘 sql/iceberg/）
阶段 4  迁移作业：Parquet → Iceberg，逐表核对行数与金额
阶段 5  流量域分层（DWD/DWS/ADS）—— 新作业
阶段 6  流量域逐窗口对账（ads_traffic_1m ↔ ecommerce.ads_realtime_traffic_1m）
阶段 7  验收脚本 scripts/verify-sprint-5.sh + 文档收口
```

每个阶段都走完 `理解 → 规划 → 小步实现 → 测试 → 验证 → 文档 → 提交`。

---

## 6. 验收标准（DoD）

- [ ] `iceberg-spark-runtime` 在 Spark 镜像与**执行器容器**里都存在（两处都要验）
- [ ] HiveCatalog 实测可用，能建出第一张 Iceberg 表
- [ ] `lakehouse_iceberg` 四层表建立，DDL 落盘 `sql/iceberg/` 并进 Git
- [ ] 迁移后**逐表行数与金额与 Parquet 版精确一致**
- [ ] Iceberg 表支持时间旅行（`SELECT ... VERSION AS OF` 可查历史快照）—— 有实证
- [ ] 流量域 DWD/DWS/ADS 建成，漏斗逐级收窄
- [ ] **`ads_traffic_1m` 与实时侧逐窗口对账，差异为 0**（或如实说明覆盖区间）
- [ ] 对账结论中明确区分可加指标与去重指标的口径
- [ ] 内存未超预算；仍走错峰模式
- [ ] 回归：`verify-sprint-1/3/4/6/7.sh` 全部仍通过
- [ ] `bash scripts/verify-sprint-5.sh` 全绿
- [ ] 文档同步：SPRINT_5.md / README Roadmap / AGENTS §2.1+§15 / DEVELOPMENT_LOG

---

## 7. 已知风险

| # | 风险 | 影响 | 对策 |
| --- | --- | --- | --- |
| 1 | Iceberg 1.11 与 HMS 3.1.3 不兼容 | 无法用 HiveCatalog | 阶段 1 先实测；不行则退到 HadoopCatalog（并记录偏差） |
| 2 | 镜像里缺 jar 或容器没换镜像 | 作业运行期才失败 | **阶段 2 显式验证镜像与执行器容器两处**（Sprint 4 踩过两次） |
| 3 | Iceberg 写入内存高于 Parquet | 打穿内存 | 阶段 4 实测峰值；必要时缩小批处理规模，**不放宽闸门** |
| 4 | 迁移期两套表并存导致口径分叉 | 数不一致 | 用不同库名 + 逐表对照；验收要求两版行数与金额一致 |
| 5 | UV 是去重指标，跨窗口不可加 | 对账口径错 | 明确写进口径说明；比率类必须先合计分子分母 |
| 6 | 磁盘 | Iceberg 会额外保存元数据与快照 | 现余约 58 GB，Iceberg 元数据量小；快照设置过期策略 `history.expire.max-snapshot-age-ms` |

---

## 8. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-27 | V1.0 | 建立 Sprint 5 任务书：Iceberg 1.11.0 + HiveCatalog + 四层迁移 + 流量域分层与对账；含版本查证结果、待实测项与 6 条风险 |
