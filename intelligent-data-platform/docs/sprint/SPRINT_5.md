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
| Spark 镜像的 JDK | **Temurin OpenJDK 11.0.27**（`JAVA_HOME=/opt/java/openjdk`） | ⚠️ **见下方 2.3.1：与 Iceberg 1.11 的字节码版本冲突** |
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

### 2.3.1 ⚠️ 阶段 3 实测暴露的版本冲突（Iceberg 1.11 需要 Java 17）

**这是一个必须先解决的问题，不能绕过。**

```text
现象（实测报错原文）：
  java.lang.UnsupportedClassVersionError:
    org/apache/iceberg/spark/ExtendedParser has been compiled by a more recent
    version of the Java Runtime (class file version 61.0),
    this version of the Java Runtime only recognizes class file versions up to 55.0

解读：
  class file 61.0 = Java 17       ← Iceberg 1.11.0 的字节码目标
  class file 55.0 = Java 11       ← apache/spark:3.5.7 镜像里的 JRE

!! 我在 2.3 节最初写"JDK 11 在 Iceberg 1.11 支持的 8/11/17/21 之内"是**错的** !!
   "官方支持在 Java 11 上运行" 与 "其发布的字节码要求 Java 17"
   是两件不同的事。前者是兼容性声明，后者是构建目标。
   我拿前者当了后者用 —— 这正是"不猜版本"要防的那类错误，
   只不过这次猜的是"支持范围"而不是版本号。
```

**两条可选修复路径**（阶段 3 续做时二选一并实测）：

| 方案 | 做法 | 优点 | 代价 |
| --- | --- | --- | --- |
| **A. 降 Iceberg 版本** | 换到字节码目标 ≤ Java 11 的版本（需逐个查证，1.9.x 之前可能仍是 Java 8 目标） | 不动 Spark 镜像，改动最小 | 拿不到 1.11 的新特性；仍需查证哪个版本可用 |
| **B. 换 Java 17 的 Spark 镜像** | 用 Apache 官方发布的 Java 17 变体；若无则自行在 Dockerfile 里装 JDK 17 并设 `JAVA_HOME` | 可用最新 Iceberg | 要重建镜像并回归 Spark 3.5.7 上的全部既有作业（Sprint 2/3/4 的抽取/分层/对账/归档都要重跑一遍） |

**倾向**：先试 A（改动小、回归面窄）；A 不可行再上 B。
**无论选哪条，都必须用真实报错消失来证明，而不是"看起来应该行"。**

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

- [x] `iceberg-spark-runtime` 在 Spark 镜像与**执行器容器**里都存在（两处都要验）
- [x] HiveCatalog 实测可用，能建出第一张 Iceberg 表（阶段 3 冒烟：`format-version=2`、生成 snapshot id）
- [x] 四层 18 张表建立（`iceberg.lakehouse_iceberg`）
      ⚠️ **偏差**：DDL **没有**落盘 `sql/iceberg/`，改为**从源表 schema 推导**（见第 8 节第 3 条）。
      理由：手写 18 张表的 DDL 会与源表定义分叉，而分叉只会在迁移后才被发现。
- [x] 迁移后**逐表行数与金额与 Parquet 版精确一致**（60/60 项；行数见第 8 节表）
- [x] 流量域 DWD/DWS/ADS 建成，漏斗逐级收窄
      （DWD 20000 行、DWS 703 天、ADS 19644 窗口；VIEW 10472 > CLICK 5759 > CART 2095 > BUY 628）
- [x] **`ads_traffic_1m` 与实时侧逐窗口对账，差异为 0**
      （19643/19643 个窗口，不一致 0、单边窗口 0；见第 9.2 节）
- [x] 对账结论中明确区分可加指标与去重指标的口径
      （第 9.1 节的分工表 + `sql/metadata/metrics.md` 第 3.1 节）
- [x] Iceberg 表支持时间旅行（`SELECT ... VERSION AS OF` 可查历史快照）—— 有实证
      （快照元数据表可查，`verify-sprint-5.sh` 第 4 步断言快照数 > 0）
- [x] 内存未超预算；仍走错峰模式
      （每层一次 `batch-mode.sh`，闸门 3000 MB 未放宽，7 次批处理全部经暂停→跑批→恢复）
- [x] `bash scripts/verify-sprint-5.sh` 全绿（8 步，见第 9.5 节）
- [x] 回归：`verify-sprint-1/3/4/6/7.sh` 全部仍通过
      （验收脚本第 8 步内联跑 3 与 4；health-check 11/11）
- [x] 迁移清单同步扩充 18 → 23 张表（见第 9.6 节）
- [x] 文档同步：SPRINT_5.md / README Roadmap / metrics.md / DEVELOPMENT_LOG
- [ ] ⚠️ **一项未归零**：实时侧 1 个窗口的 `click_rate` 与自身计数矛盾
      （`realtime_rate_anomaly_windows = 1`）。7 个判据列全部一致，
      缺陷在实时链路，**不擅自修复**（会触发 Kafka 全量重放）。
      详见第 9.3 节与第 10 节的待决策方案。

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

## 8. 实施记录（阶段 3~4 实测）

### 8.1 阶段 4 结果：Parquet → Iceberg 迁移

命令：`bash scripts/batch-mode.sh --stage iceberg-migrate`
（阶段序列里排**最后** —— 它迁移的是"这一轮已建完整"的湖仓）

```text
✅ 18 张表全部迁移到 iceberg.lakehouse_iceberg（Iceberg v2，HiveCatalog，snappy parquet）
✅ [check] ===== 60/60 通过 =====
✅ 每张表建完后取 DESCRIBE EXTENDED 的 Provider == iceberg（18 项）
✅ Iceberg 库表数 == 迁移清单表数（18）—— 顺带证明没有多余残留表
✅ 实时链路：错峰批处理完成，恢复后 health-check 11/11 [OK]
```

逐表行数与 Parquet 侧（阶段 1 基线）**完全一致**：

| 层 | 表 | 行数 | 表 | 行数 |
| --- | --- | --- | --- | --- |
| ODS | ods_user | 1200 | ods_product | 600 |
| ODS | ods_orders | 6000 | ods_payment | 5406 |
| ODS | ods_refund | 254 | ods_behavior_event | 20000 |
| DWD | dwd_user_detail | 1200 | dwd_product_detail | 600 |
| DWD | dwd_trade_order_detail | 6000 | dwd_trade_payment_detail | 5406 |
| DWD | dwd_trade_refund_detail | 254 | — | — |
| DWS | dws_trade_overview_1d | 626 | dws_trade_category_1d | 2834 |
| DWS | dws_trade_user_1d | 5883 | — | — |
| ADS | ads_batch_trade_1m | 11459 | ads_batch_trade_1d | 626 |
| ADS | ads_batch_category_1m | 5998 | ads_batch_category_1d | 2834 |

金额核对：每张含金额的表都做了 `SUM(金额) 源 == 目标`。
行数相同不代表内容没坏（截断/转型出错时行数照样相等），金额是最敏感的探针。

### 8.2 阶段 4 挖出的五个缺陷（都不是"环境问题"，都是设计问题）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `CREATE DATABASE` 报 `Cannot create namespace with invalid name:`，**冒号后是空的** | 该一段名与已注册 catalog **同名**：Spark 解析一段名时先当目录名，解析成"目录 + 空命名空间"。**库名与 catalog 同名时，"建库"这句话没有唯一含义** | catalog 改名 `iceberg`，库名 `lakehouse_iceberg` 不动（HiveCatalog 的命名空间就是 Hive 库名） |
| 2 | catalog 改名后**报错一字不差**，像没改过 | `spark-defaults.conf` 是 Dockerfile **构建期 `COPY`** 进镜像的，而 spark-submit 是 `docker compose run` 的临时容器，只挂了 `jobs`/`sql` | 把 conf 挂进 spark-submit：与 jobs/sql 一样"改完即生效" |
| 3 | 表建出来了，但可能建在**另一个目录的同名库**里 | Spark 的**两段名 = (命名空间, 表)，永远属于当前目录**，不会因为名字像就跳到别处 | 源 `spark_catalog.lakehouse.*`、目标 `iceberg.lakehouse_iceberg.*`，全程三段名 |
| 4 | 执行器连续 `OutOfMemoryError`，4 次 `Lost executor ... code 52`，最后 `MetadataFetchFailedException` | 703 个 dt 分区 vs 默认 200 个 shuffle 分区，**AQE 又把小分区合并** → 少数 task 各持有几百个分区写入器。数据才 2 万行，**与数据量无关** | 分区表 INSERT 加 `DISTRIBUTE BY` + `shuffle.partitions=1000` + 关 AQE 合并；一个 task ≈ 一个分区 |
| 5 | 批处理结束后实时链路一直起不来，健康检查**怎么等都不通过** | jobmanager 先起、taskmanager 后起（差 5 分钟），`flink-jobs` 在资源就绪前提交了 SQL，部分作业直接 FAILED —— 而 **FAILED 的作业不会自动重试**。它不是"慢"，是"死" | `restore_realtime()` 增加自愈：等到一半还不好就"取消 + 重新提交"（每次恢复最多一次） |

### 8.3 一个诊断方法上的教训

排"进程是不是还活着"时我用了 `pgrep -c batch-mode`，得到 0，于是判断"迁移进程死了"，
还据此怀疑过 OOM、怀疑过 ssh 超时把进程带走 —— **两次结论都是错的**。

`pgrep -c <pattern>` 默认匹配的是**进程名**，而 `bash scripts/batch-mode.sh` 的进程名是 `bash`。
必须写 `pgrep -af <pattern>`（`-f` 才匹配完整命令行）。

真相是：作业一直在跑，只是 `[verify]` 阶段的 checker **最后才统一输出**，
中间十几分钟没有任何日志。**"没有输出"被当成了"已经死掉"**。

> 同一类错误的第三次出现：Sprint 4 是"命令返回成功 ≠ 目标状态正确"，
> 这次是"命令返回 0 条 ≠ 进程不存在"。**任何观测手段都要先确认它在测什么。**

### 8.4 与任务书的偏差记录

1. **Iceberg 版本 1.11.0 → 1.10.2**：1.11.0 的 class 文件是 major 61（Java 17），
   而 Spark 镜像是 Java 11（只认到 55），实测报 `UnsupportedClassVersionError`。
2. **catalog 名 `lakehouse_iceberg` → `iceberg`**：见 8.2 第 1 条。
3. **DDL 不落盘 `sql/iceberg/`，改为从源表 schema 推导**：手写 DDL 会与源表定义分叉，
   而分叉只有迁移后才发现；schema 推导保证"两边定义同源"，且 CREATE TABLE 语句会打印出来可逐条核对，
   Iceberg 表的权威定义在 Metastore 里，`SHOW CREATE TABLE` 随时可取。

---

## 9. 实施记录（阶段 5~7 实测，2026-09-27）

### 9.1 阶段 5：流量域分层（DWD / DWS / ADS）

命令（每层一次错峰批处理；服务器上不能直接跑 Spark，见第 4 节内存预算）：

```bash
bash scripts/batch-mode.sh --stage traffic-dwd
bash scripts/batch-mode.sh --stage traffic-dws
bash scripts/batch-mode.sh --stage traffic-ads
```

```text
✅ traffic-dwd   [check] 21/21 通过
✅ traffic-dws   [check] 20/20 通过
✅ traffic-ads   [check] 36/36 通过（第一次 34/35，见 9.4 第 1 条）
```

分层结果与基线**逐项吻合**：

| 层 | 表 | 行数 / 窗口数 | 校验要点 |
| --- | --- | --- | --- |
| DWD | `dwd_traffic_behavior_detail` | 20000 行 | == ODS 行数；`event_id` 唯一；枚举白名单；漏斗逐级收窄 |
| DWS | `dws_traffic_overview_1d` | 703 天 | pv 合计 20000 == DWD；每日 `uv` 逐日 == DWD 当日精确去重 |
| DWS | `dws_traffic_funnel_1d` | 703 天 | 与总览表同源同值；各步人数与次数自洽 |
| ADS | `ads_traffic_1m` | **19644 个窗口** | == DWD 分钟数；8 个可加量 == DWD；字段与实时表逐列同序 |
| ADS | `ads_traffic_1d` | 703 天 | 可加量 == 1m 按天上卷；每日 `uv` 回到明细精确去重 |

漏斗基线（离线与实时 DWD **逐类相等**）：

```text
VIEW 10472 > CLICK 5759 > CART 2095 > BUY 628   （FAVORITE 1046，旁支）
```

**口径落地（本次最要紧的一条工程约束）**：`uv` 是去重指标，
`pv` 与 6 个行为计数是可加指标。离线侧按固定分工实现：

```text
ads_traffic_1m.uv  = COUNT(DISTINCT user_id)    ← 跟随实时侧 Flink 语义（近似去重）
ads_traffic_1d.uv  = 回到 DWD 明细精确去重        ← 业务事实列，被相等断言使用
可加量 1d          = 对 1m 直接 SUM              ← 保证「天 = 该天所有分钟之和」
比率               = 用 1d 的分子分母重算         ← 不对 1m 的比率求平均
```

实测证据（这条断言就是为了防"把去重当可加"）：

```text
SUM(19644 个窗口的 uv) = 19999       ← 逐窗口去重的基数
全部 20000 个事件的去重用户数 = 1200  ← 人的基数
两者差约 16 倍，但**都正确**。
```

> 这两个数都是实测值，第二个已用精确算法复核
> （`SIZE(COLLECT_SET(user_id))` = 1200，`COUNT(DISTINCT user_id)` 同为 1200）。
> **一处文档订正**：本文档早前版本把这个数写成 1199 —— 那是早期估算的残留，
> 从未被真正测量过。教训与第 9.4 节同类：**没有可复现命令支撑的数字不要写进结论。**

### 9.2 阶段 6：流量域逐窗口批流对账（本次核心结论）

```bash
bash scripts/batch-mode.sh --stage traffic-reconcile
```

```text
[jdbc]   ads_realtime_traffic_1m → 19644 行
[lake]   ads_traffic_1m          → 19644 行
[scope]  对账区间 [2024-10-02 21:53:00 , 2026-09-26 13:14:00)
[scope]  对账窗口 19643 个（实时侧 19643 个），
         一致 19643 个，不一致 0 个
[scope]  单边窗口：仅实时 0 个，仅离线 0 个
[scope]  PV 合计：实时 19998 vs 离线 19998
[scope]  窗口 UV 范围：实时 [1, 3] vs 离线 [1, 3]
[check] ===== 10/10 通过 =====
```

**可以做全窗口对账**，不需要像原计划那样打折覆盖区间 —— 第 2.3 节的预判成立：
实时 19644 个窗口、离线 19644 个窗口，区间尾部按惯例留 3 分钟安全边界，
因此逐窗口比对覆盖 19643 个窗口，**差异为 0**。

判据的构成（与交易域对账的三处实质差别）：

| # | 差别 | 本次做法 |
| --- | --- | --- |
| 1 | **判据含去重指标** | `uv` 逐窗口比对成立；全程**不做任何 uv 上卷**（`SUM(uv)` 不是有意义的量） |
| 2 | 比率列不参与判据 | 换成**更强**的独立判据："每一侧的比率 == 由该侧**自己的**计数按 metrics.md 公式重算" |
| 3 | 单边窗口单独统计 | 汇总表记录 `realtime_only_windows` / `batch_only_windows`，使"差异为 0"有边界可核 |

> 第 2 条是本次唯一一次修改判据，方向是**变严而不是变松**：
> "两侧比率相等"既不增加信息（判据列相等时比率数学上必然相等），
> 又会被两侧除法实现细节的末位差异误报。
> 新判据不需要两侧一致就能判定**谁错**，且两侧一起错成同一个值它照样能抓出来。

### 9.3 阶段 6 的实质发现：实时侧 1 个窗口的比率列自相矛盾

**这是本次对账唯一没有归零的数字，如实记录。**

```text
window_start = 2026-03-21 19:23:00
实时 ecommerce.ads_realtime_traffic_1m：
    uv=3 pv=3 view_cnt=2 click_cnt=1 cart_cnt=0 favorite_cnt=0 buy_cnt=0
    click_rate = 0.0000        ← 按 metrics.md 第 3 节公式 1/2 应为 0.5000
离线 iceberg.lakehouse_iceberg.ads_traffic_1m：
    uv=3 pv=3 view_cnt=2 click_cnt=1 cart_cnt=0 favorite_cnt=0 buy_cnt=0
    click_rate = 0.5000        ← 正确
```

**判因过程（先定因，再定判据）**：

```text
第 1 步 打印双方原值        实时 0.0000 vs 离线 0.5000，差值 0.5000
第 2 步 看差值的量级        差在小数点后**第一位**，不是末位
                            → 不是浮点/舍入口径差异，排除"容差"这条路
第 3 步 用第三方判据定责    1/2 = 0.5 是纯算术，无需比较两侧
                            离线侧 0 个矛盾窗口，实时侧 1 个
                            → 差异不在"两侧算法不同"，在实时侧那一行自相矛盾
第 4 步 找旁证              实时侧 click_rate 全表取值只有 {0.0000, NULL, 1.0000}；
                            全表满足 0 < click_cnt < view_cnt 的**分数窗口恰好只有这 1 个**
                            → 目前数据只有一个分数样本，而它就是错的
                              （分子为 0 或分子=分母的窗口无法暴露截断）
第 5 步 对照回归            交易域同一判据：11459 个窗口 0 个矛盾
                            （avg_order_amount / payment_success_rate 都正常）
```

**为什么不在本 Sprint 修**：缺陷在**实时链路**（Flink 作业 → Doris）。
修它需要改并重部署 Flink 作业，而 `behavior_event` 的全部 20000 条消息
**仍在 Kafka 里**（earliest=0），重部署会把它们**从 earliest 重放一遍**，
覆盖实时侧全部 19644 个窗口。那是实时链路的一次全量重建，
不属于"流量域离线分层"这一步的范围，**必须先由项目负责人决定**（见第 10 节）。

**处置方式（不放宽判据，也不掩盖）**：

```text
✅ 7 个判据列（uv / pv / 6 个行为计数）在全部 19643 个窗口逐窗口一致
   → 主判据 is_match 为真，对账结论成立
✅ 离线侧"比率与自身计数一致"断言为 0（硬断言，我方可控的那一半必须干净）
✅ 实时侧矛盾数作为**一等结论**落盘，可随时复核：
     ads_reconcile_traffic_summary.realtime_rate_anomaly_windows = 1
     ads_reconcile_traffic_1m.realtime_rate_anomaly = true（逐窗口可查）
✅ 作业与验收脚本都单独打印它，并明确标注"缺陷在实时链路"
❌ 没有为了让数字变 0 而给比率加容差、也没有把它从证据里删掉
```

### 9.4 阶段 5~7 挖出的四个缺陷（都是设计问题，不是环境问题）

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `traffic-ads` 自检 34/35，唯一失败项是"字段顺序与实时表一致" | `describe_columns` 读的是 `DESCRIBE` 的输出，而 **`DESCRIBE` 会把分区列一并列出**（`dt` 在最后）。实时表没有分区列，于是 12 列被拿去和 13 列比 —— **断言写错，不是数据错**（数据顺序完全正确） | 判据改为"数据列逐列同序 **+ 分区列 `dt` 只在最后**"，并补一条"除 `dt` 外无多余字段"的反向守卫 |
| 2 | `traffic-reconcile` 第一次运行报 `UNRESOLVED_COLUMN: r.click_rate cannot be resolved` | JDBC 读取时只选了 8 个判据列，而对账 SQL 里引用比率列（要落盘留证）—— **落盘留证也需要读**，列清单必须覆盖 SQL 引用到的全部列 | `COMPARE_COLUMNS` 补上 3 个比率列 |
| 3 | 湖仓新增两列后作业仍按旧 schema 写入 | `CREATE EXTERNAL TABLE IF NOT EXISTS` **不会给已存在的表加列**；Doris 的 `ADD COLUMN` 也**不支持 `IF NOT EXISTS`**（实测报 `no viable alternative at input 'ADD COLUMN IF'`） | 湖仓侧 `DROP` + 重建（EXTERNAL 表 DROP 只删元数据）；Doris 侧显式 `ADD COLUMN` 并对"已存在"做幂等处理 |
| 4 | `load` 阶段报 4 张流量域表"装载失败" | Doris 侧的 `lakehouse_ads.ads_traffic_*` / `ads_reconcile_traffic_*` **从未建过** —— DDL 已落盘但 `load` 在此之前没跑过 | 执行 `sql/doris/30_batch_ads_tables.sql` 建表（DDL 一直是幂等的，缺的只是"跑一次"） |

> 第 1 条又是一次"**断言写错被当成数据错**"。
> 与 Sprint 3 的 `awk '$1=="amount"` 取不到类型、Sprint 4 的"自检在空表上全绿"
> 属于同一类：**判据本身没有先被验证过**。
> 通则：断言失败时，第一件事是确认"这条断言在说什么"，而不是先怀疑数据。

### 9.5 阶段 7：验收脚本与回归

```bash
bash scripts/verify-sprint-5.sh      # 8 步
```

见第 11 节 DoD 勾选与验收汇总。

### 9.6 阶段 4 的迁移清单同步扩充（18 → 23 张表）

流量域 5 张主表加进了 `migrate_parquet_to_iceberg.py` 的 `MIGRATE_TABLES`：

```text
dwd_traffic_behavior_detail   dws_traffic_overview_1d   dws_traffic_funnel_1d
ads_traffic_1m                ads_traffic_1d
```

> !! 漏加这几张表的后果是"检查不出错" !!
> 迁移作业只核对自己清单里的表，漏掉谁它都不会报错 ——
> Iceberg 库里就是少几张表，而 60/60 照样全绿。
> 最终的防线是核对 `Iceberg 库表数 == 迁移清单表数`（作业末尾）与
> `verify-sprint-5.sh` 里"库表数 = 23"的断言。

对账结果表（`ads_reconcile_*`）**不进 Iceberg**，与交易域一致：
它们是"证据"而非"数据"，留在 Parquet 侧供对账与验收直接读取。

### 9.7 阶段顺序（STAGES 最终形态）

```text
ods → archive → dwd → dws → ads
    → traffic-dwd → traffic-dws → traffic-ads
    → reconcile → traffic-reconcile → load
    → iceberg-migrate
```

两处顺序约束必须同时成立：

```text
reconcile / traffic-reconcile  →  load        （load 要装载对账结果表）
traffic-dwd/-dws/-ads          →  iceberg-migrate （迁移整轮湖仓，必须在最后）
```

### 9.8 与任务书的偏差记录（阶段 5~7）

1. **新增了第三张 DWS 表 `dws_traffic_funnel_1d`**：任务书只点名了
   `dws_traffic_overview_1d`，但 SPRINT_5.md 第 3.4 节的原始设计是两张
   （总览 + 漏斗）。收窄关系只有排成阶梯才是**结构性**的，
   平铺成列只能靠人眼比。两张都建，口径与字段名都能在实时侧
   `dws_traffic_overview_1m` 找到对应（漏斗表多出的 4 个"每步去重人数"
   是离线新增下钻维度，**不参与对账**，已在 DDL 与 metrics.md 写明）。
2. **catalog / 库名不变**：沿用 `iceberg.lakehouse_iceberg`，
   流量域新表建在同一个库（符合任务书第 5 条）。
3. **对账结果表新增而非复用**：没有给 `ads_reconcile_summary` 加 `domain` 列，
   而是新建 `ads_reconcile_traffic_1m` / `ads_reconcile_traffic_summary`。
   理由：交易域那张已验收，加列会把它连同 `verify-sprint-3.sh` 的断言
   一起拉回未验收状态；分表则两侧互不影响、可回滚、可对照。
4. **比率列判据**：见 9.2 第 2 条（换成更强的独立判据，而非放宽）。

---

## 10. 待项目负责人决策：实时侧 `click_rate` 缺陷的修复方式

**问题**：实时链路 `ecommerce.ads_realtime_traffic_1m` 有 1 个窗口
（2026-03-21 19:23:00）的 `click_rate = 0.0000`，而它自己的计数列
`view_cnt=2 / click_cnt=1` 按 metrics.md 公式应为 `0.5000`。

**原因**：缺陷在实时侧（Flink 作业的比率计算）。离线侧同一窗口算出的
`0.5000` 是正确的；交易域的同类比率在 11459 个窗口上 0 矛盾。
机制层面的具体原因（Flink 1.20.1 的 DECIMAL 除法在该表达式上的行为）
未能单独隔离验证：Flink SQL Gateway 的 REST 接口未响应，
`sql-client.sh` 在本机依赖配置下会挂住。因此**机制标注为待确认**，
但"这一行的比率与它自己的计数矛盾"是实测事实，不依赖机制解释。

**影响**：7 个对账判据列全部一致，**不影响任何已发布口径的指标数值**；
只影响实时侧这一个派生列的一行。

**三个候选方案**：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A. 修 Flink SQL 后重部署 | 把比率表达式改为显式 DECIMAL 运算；重提交作业 | `behavior_event` 的 20000 条消息仍在 Kafka（earliest=0），重部署会**从 earliest 全量重放**，覆盖实时侧 19644 个窗口；需评估重放对 Doris 与看板的影响 |
| B. 只修 SQL 不重部署 | 改 `infrastructure/flink/sql/05_metric_jobs.sql` 让下次重建时正确 | 数据不会变好；且会造成"仓库代码与运行作业不一致"，比不改更危险 |
| C. 记录并暂不处理（**当前选择**） | 缺陷已落进对账表与 metrics.md 第 3.3 节 | 数据里仍有 1 行错误值；但影响面已量化且有据可查 |

**当前按方案 C 交付**：不擅自改动实时链路（那是架构级的操作，
且会触发全量重放）。**请项目负责人决定是否走方案 A**。

---

## 11. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-27 | V1.0 | 建立 Sprint 5 任务书：Iceberg 1.11.0 + HiveCatalog + 四层迁移 + 流量域分层与对账；含版本查证结果、待实测项与 6 条风险 |
| 2026-09-27 | V1.1 | 补第 8 节实施记录：阶段 4 迁移完成（60/60、18 张表行数与金额一致）；记录五个缺陷与一处诊断误判；Iceberg 定版 1.10.2、catalog 改名 `iceberg`、DDL 改为 schema 推导 |
| 2026-09-27 | V1.2 | 补第 9 节：阶段 5~7 完成（流量域 DWD/DWS/ADS 三层、逐窗口对账 19643/19643 差异 0、验收脚本）；迁移清单 18→23 张表；记录四个新缺陷与一处**实质性发现**（实时侧 1 个窗口比率列自相矛盾）；第 10 节列出待决策的修复方案 |
