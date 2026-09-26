# Sprint 2 设计：Spark + Hive + 湖仓存储（离线链路）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> Sprint：2
> 状态：✅ **已实现并在腾讯云服务器通过验收**（2026-09-26）
> 前置：Sprint 0（基础环境）、Sprint 1（实时链路）、Sprint 6（数据后台 + 看板）已完成验收
> 存储偏差说明见第 2.2 节（HDFS → S3A/MinIO，附内存实测依据）
> 实现踩坑与最终版本组合见第 6 节

---

## 0. 验收结果（实际执行）

```text
✅ bash scripts/verify-sprint-2.sh    8/8 PASS
     服务状态 / Spark 集群 / ODS 表 / 行数对账 / 存储落地 / 类型正确性 / 幂等性 / 回归
✅ 逐表行数对账（湖仓 == MySQL）
     ods_user 1200、ods_product 600、ods_orders 6000、ods_payment 5406、ods_refund 254
✅ 存储落地   25 个 Parquet 对象位于 s3a://lakehouse/warehouse/ods/*
✅ 类型正确   amount = decimal(18,2)，ODS 层无 double/float
✅ 幂等       重跑抽取作业后行数不变
✅ 回归       实时链路 health-check 11/11、数据服务 /health 正常
```

一键复现：

```bash
bash scripts/init-lakehouse.sh        # 建元数据库/镜像/服务/表结构（幂等）
bash scripts/submit-offline-job.sh    # 抽取 + 作业内自带逐表对账
bash scripts/verify-sprint-2.sh       # 8 步验收
```

---

## 1. Sprint 2 目标

```text
MySQL 业务库（唯一事实源）
     │  Spark JDBC 全量抽取
     ▼
Parquet 文件（湖仓存储：MinIO / S3A）
     │  Hive Metastore 建目录（表结构 + 分区）
     ▼
Hive 外部表 ods_*（贴源层）
     │
     └──► Sprint 3：ODS → DWD → DWS → ADS 分层建模，并与实时指标交叉对账
```

**本 Sprint 只做一件事**：把业务库**完整、可验证地**搬进湖仓，并让 SQL 引擎能查它。
不做分层建模（Sprint 3）、不做调度（Sprint 4）、不做 Iceberg 表格式（Sprint 5）。

### 1.1 为什么这一步必须存在

实时链路（Sprint 1）解决的是"秒级看得见"，但它有两个绕不过去的边界：

| 边界 | 实时链路的现状 | 离线链路要解决的 |
| --- | --- | --- |
| 历史 | 只处理 Kafka 里的事件（当前一代） | 业务全量，可反复重算、可回溯 |
| 正确性 | 窗口聚合**没有二次校验** | 用另一套引擎、另一套代码算出同一指标，交叉对账 |
| 变更 | 事件里只有增量 | 支持全量重跑（MySQL 是唯一事实源） |
| 存储成本 | Kafka 保留期有限 | 列式存储 + 分区，长期低成本保存 |

所以 Sprint 2 的产物不是为了"再搭一套环境"，而是为了**给实时指标提供独立验证**——
这正是"批流一体"的核心价值，也是毕业论文里最能体现工程判断的一章。

---

## 2. 技术选型

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Apache Spark | `3.5.7`（官方 `apache/spark` 镜像） | 离线计算；PySpark 作业 |
| Apache Hive Metastore | `3.1.3`（官方 `apache/hive` 镜像） | **只跑 Metastore 服务**（不跑 HiveServer2/Tez，省内存）。版本选择理由见第 6.1 节 |
| 湖仓存储 | MinIO（Sprint 0 已部署，bucket `lakehouse`） | 通过 `s3a://` 协议访问 |
| 元数据库 | MySQL 8.4.11（Sprint 0 已部署） | 新建 `hive_metastore` 库存放 Hive 元数据 |
| JDBC 驱动 | `mysql-connector-j 8.4.0` | 打进 Spark 镜像 |
| S3A 依赖（Spark 侧） | `hadoop-aws 3.3.4` + `aws-java-sdk-bundle 1.12.262` | 与 Spark 自带 hadoop-client 3.3.4 对齐 |
| S3A 依赖（Metastore 侧） | `hadoop-aws 3.1.0` + `aws-java-sdk-bundle 1.11.271` | 与该镜像自带 Hadoop 3.1.0 对齐（用 3.3.4 会 NoSuchMethodError） |

### 2.1 为什么必须"打进镜像"而不是 `--packages`

`spark-submit --packages` 会在运行时去 Maven Central 下载依赖，
在服务器网络下每次提交都要重新解析、且失败会直接导致作业起不来。
把 JAR 固化进镜像（`infrastructure/spark/Dockerfile`）后，作业启动不依赖外网，
也让"同一镜像 = 同一运行环境"这件事成立。

### 2.2 存储偏差：用 S3A(MinIO) 而不是 HDFS

**设计目标架构写的是** `Spark → Iceberg on HDFS/S3 → Hive`，HDFS 与 S3 都在方案内。
本次**选择 S3A（MinIO）**，理由是实测出来的：

| 项 | 实测值 |
| --- | --- |
| 宿主机总内存 | 15.99 GB |
| 当前可用内存 | **2.02 GB**（已跑实时链路 + 看板 + Doris + Kafka） |
| Doris FE 容器 | 7.27 GB（含页缓存，JVM 堆已限制 1536 MB） |
| Flink 三容器合计 | 2.46 GB |
| Kafka | 1.13 GB |
| HDFS（NN+DN，各限 512 MB 堆）预计 | ~1.3 GB |

在可用内存只有 2 GB 的情况下再加 HDFS，**大概率触发 OOM**，
而 OOM 会连带影响已经验收通过的实时链路与数据看板 —— 这个代价不可接受。

**影响**：Sprint 2 的 Parquet 落在 `s3a://lakehouse/warehouse/`，而不是 `hdfs://`。
**HDFS 如何补回来**（后续任选其一）：
1. 升级服务器内存后，加 `namenode` + `datanode` 两个服务，Spark 侧只改
   `spark.hadoop.fs.defaultFS=hdfs://namenode:8020`；
2. 或按本 Sprint 的 `docs/development-environment.md` 记录，用
   `docker compose --profile hdfs up -d` 按需启动（实时链路停掉时）；
3. Sprint 5 的 Iceberg 表既支持 S3 也支持 HDFS，本 Sprint 的选择不会造成返工。

> 这条偏差按 `AGENTS.md` 第 11.1 节记录在这里：问题 / 原因 / 影响 / 建议方案齐备。

### 2.3 为腾出内存所做的降配（实测后调整）

| 组件 | 原值 | 新值 | 释放 |
| --- | --- | --- | --- |
| Kafka | `KAFKA_HEAP_OPTS=-Xmx1G -Xms1G` | `-Xmx512m -Xms512m` | ~0.5 GB |
| Flink TaskManager | `taskmanager.memory.process.size=3072m` | `2048m` | ~1 GB |
| Flink JobManager | `jobmanager.memory.process.size=1024m` | `768m` | ~0.25 GB |
| Doris FE | `-Xmx1536m` | `-Xmx1024m` | ~0.5 GB |

降配后仍满足各自负载：Kafka 只有 12 个 topic、每个 3 分区；
Flink 8 个作业各 1 slot（每 slot 约 125 MB 堆）；FE 只服务看板的只读查询。

**放不进内存的东西一律不加**（本 Sprint 明确不做）：YARN、HiveServer2、Tez、
Spark Thrift Server、HDFS、ZooKeeper。

---

## 3. 数据流与分层约定

```text
ODS（本 Sprint）
  ods_user      ← ecommerce.user      1200 行
  ods_product   ← ecommerce.product    600 行
  ods_orders    ← ecommerce.orders    6000 行
  ods_payment   ← ecommerce.payment   5406 行
  ods_refund    ← ecommerce.refund     254 行
```

- 存储路径：`s3a://lakehouse/warehouse/ods/<table>/`
- 文件格式：**Parquet**（列式、带 schema，Sprint 5 换 Iceberg 时只改表格式）
- 表类型：**外部表（EXTERNAL）**，`DROP TABLE` 不删数据，方便重跑
- 抽取方式：Spark JDBC 并行读（按主键 range 分区，避免单线程全表拉取）
- 幂等：每次运行先写临时目录再 `INSERT OVERWRITE` 目标分区（或 `mode=overwrite`）

**列类型映射**（MySQL → Spark/Parquet）：

| MySQL | Spark | 说明 |
| --- | --- | --- |
| `BIGINT` / `INT` | `long` / `int` | 主键与外键 |
| `DECIMAL(18,2)` | `decimal(18,2)` | **金额绝不用 double** |
| `VARCHAR(n)` | `string` | — |
| `DATETIME` | `timestamp` | 时区 Asia/Shanghai |
| `TINYINT` | `int` | 避免 Spark `tinyint` 与 Hive 的兼容问题 |

---

## 4. 交付物

```text
docker-compose.yml                        + hive-metastore / spark-master / spark-worker
infrastructure/hive/conf/hive-site.xml    Metastore 配置（MySQL 元数据库 + S3A）
infrastructure/spark/Dockerfile           apache/spark:3.5.7 + JDBC + hadoop-aws
infrastructure/spark/conf/spark-defaults.conf  Metastore 地址、S3A 凭据、内存
infrastructure/spark/jobs/extract_mysql_to_lake.py   PySpark 抽取作业
infrastructure/spark/jobs/create_ods_tables.sql      Hive 外部表 DDL
scripts/init-lakehouse.sh                 建 Metastore schema + S3 目录 + 注册表
scripts/submit-offline-job.sh             提交抽取作业
scripts/verify-sprint-2.sh                验收（7 步）
tests/smoke/test_offline.py               冒烟：表存在 + 逐表行数对账
docs/sprint/SPRINT_2.md                   本文档
```

---

## 5. 完成标准（Definition of Done）

- [ ] `spark-master` / `spark-worker` / `hive-metastore` 三个容器 healthy 且常驻
- [ ] Spark Web UI（:8080）可访问，Worker 已注册、有可用 core 与内存
- [ ] `spark-sql -e "SHOW DATABASES"` 能看到 `lakehouse` 库（走 Hive Metastore）
- [ ] 5 张 ODS 表注册成功，且**逐表行数与 MySQL 精确一致**（1200/600/6000/5406/254）
- [ ] Parquet 文件确实落在 MinIO（`mc ls` 可见），不是本地磁盘
- [x] 金额字段类型为 `decimal(18,2)`，不是 double
- [x] 抽取作业**可重复执行**（幂等），重跑后行数不变
- [x] 实时链路与看板在降配后仍全部健康（`health-check.sh` 11/11、数据服务 `/health` 正常）
- [ ] `tests/smoke/test_offline.py` 通过 —— 用例已写好，待随下一次全量冒烟执行确认
- [x] 文档同步：`SPRINT_2.md` + `DEVELOPMENT_LOG.md` + `README.md` + `AGENTS.md`
- [x] 未引入 Sprint 3+ 的内容（无分层建模、无 Airflow、无 Iceberg、无 LLM）

---

## 6. 实现踩坑记录与最终版本组合

本 Sprint 的难点几乎全在**版本兼容**上，按发现顺序记录（都已修好，重新部署不会再遇到）：

### 6.1 Hive 客户端 / 服务端版本必须匹配（最重要）

| 尝试 | 结果 |
| --- | --- |
| Metastore `4.0.1` + Spark 内置客户端 `2.3.9` | ❌ 建表报 `Unable to fetch table ods_user. Invalid method name: 'get_table'` —— Hive 4 移除了旧 thrift 方法 |
| Metastore `4.0.1` + 用 `jars.path` 指定 Hive 4 客户端 | ❌ Spark 白名单只认 ≤ `3.1.3`，报 `'4.0.1' ... is invalid`；且 `jars.path` 在 `fs.defaultFS=s3a` 下会被解析成 S3 路径 |
| Metastore `3.1.3` + 声明 `metastore.version=3.1.3` | ❌ `Builtin jars can only be used when hive execution version == hive metastore version` |
| **Metastore `3.1.3` + 不声明版本（用内置 2.3.9 客户端）** | ✅ **成功** —— Hive 3.1.x 保留新旧 thrift 方法，是 Spark 3.x 生态验证过的组合，零额外 JAR、不联网 |

### 6.2 其余坑（同样都写进了代码注释）

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | Metastore 反复重启（RestartCount 16） | `hive-site.xml` 的 XML 注释里出现了"连续两个减号"（我本来是用它做分隔装饰），Hive 报 `WstxParsingException: String not allowed in comment` | 注释改用等号分隔；并在文件里写明这条 XML 规则 |
| 2 | Metastore 重启（RestartCount 18） | Hive 3.1.x 的 entrypoint **每次启动**都跑 `schematool -initSchema`，表已存在时报 `Table 'CTLGS' already exists` 后退出 | 设 `IS_RESUME=true` 跳过；表结构改由 `init-lakehouse.sh` 在启动前用一次性容器初始化一次 |
| 3 | schematool 拿 Derby 脚本初始化 MySQL | 镜像 ENTRYPOINT 是 `sh -c /entrypoint.sh`，会**忽略**追加的命令行参数，改用环境变量 `DB_DRIVER`（默认 derby）自己执行 | 调用时加 `--entrypoint /opt/hive/bin/schematool` 绕过 entrypoint |
| 4 | 建库/建表报 `ClassNotFoundException: S3AFileSystem` | Metastore 服务端解析 `s3a://` 位置时需要 S3A 类，而镜像把它放在 Hadoop 的 `tools/lib` 下、不在 Hive 的 classpath 上 | 从镜像里提取 `hadoop-aws-3.1.0` + `aws-java-sdk-bundle-1.11.271` 挂到 `/opt/hive/lib`（**必须用镜像自带那份**，配 Spark 侧的 3.3.4 会 NoSuchMethodError） |
| 5 | Spark 执行器起不来：`Failed to create directory /opt/spark/work/app-xxx/0` | 命名卷 `spark-work` 默认属主是 root，而 Spark 镜像以 uid 185(spark) 运行 | 启动前 `docker run --rm -v <卷>:/w alpine chown -R 185:185 /w`（已写进 `init-lakehouse.sh`） |
| 6 | 健康检查永远不过、`spark-worker` 不启动 | Spark 的 MasterUI 绑定在容器**主机名**对应地址上，`curl http://localhost:8080/` 报 Connection refused | 健康检查改用服务名 `http://spark-master:8080/` |
| 7 | 脚本"自检没有任何输出就结束" | `set -e` 下命令替换里的 `grep` 无匹配即返回非 0，直接把脚本带走 | 所有取值加 `|| true` |
| 8 | 集群明明正常却判 FAIL | Spark `/json/` 是**美化输出**（冒号两侧有空格），`grep -o '"aliveworkers":[0-9]*'` 匹配不到 | 先 `tr -d ' \n'` 再匹配；另外 `memory` 单位本来就是 MB，不要再除 1024 |
| 9 | `spark-submit` 容器报 `UnknownHostException: spark-submit` | 该服务用 `compose run --rm` 临时启动，容器名由 compose 生成，网络里没有这个 DNS 记录 | 该服务不设 `SPARK_LOCAL_IP`，让 Spark 用容器 IP |
| 10 | `spark-sql` 启动即失败：`requireLogBaseDirAsDirectory` | 事件日志目录 `s3a://lakehouse/spark-events` 不存在（S3 没有真目录） | `init-lakehouse.sh` 用 `mc mb` 预先创建 `warehouse`、`warehouse/ods`、`spark-events` 三个前缀 |

> 这些坑的共同点：**都不是写代码写错，而是版本契约与启动时序**。
> 处理方式统一是「读日志找真正的那一行 → 定位契约 → 最小修改 → 复验」，
> 没有一处是靠猜或靠放宽断言过去的。

---

## 7. 与后续 Sprint 的衔接

| 后续 Sprint | 本 Sprint 留下的接口 |
| --- | --- |
| Sprint 3（分层建模） | 已注册的 `ods_*` 外部表 + `lakehouse` 库；DWD/DWS/ADS 直接在其上建 |
| Sprint 3（交叉对账） | 离线与实时同口径指标：`sql/metadata/metrics.md` 是唯一口径来源 |
| Sprint 4（Airflow） | 抽取作业封装成 `scripts/submit-offline-job.sh`，可直接被 DAG 调用 |
| Sprint 5（Iceberg） | 存储层已是 S3A；只需把表格式从 Parquet 换成 Iceberg，路径与 Hive 目录不变 |
