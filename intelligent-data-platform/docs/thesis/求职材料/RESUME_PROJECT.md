# 简历项目描述（RESUME_PROJECT）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 用途：简历 / 网申 / 面谈时的项目描述（**中英双版**，均为 STAR 结构）
> **用词纪律**：只用"**设计 / 实现 / 构建 / 验证 / 优化 / 建立**"这一类**动作动词**；
> **禁止**"参与 / 协助 / 学习 / 帮忙 / 配合"这类弱化表述（它们会让读者无法判断你到底做了什么）。
> **数字纪律**：每一个数字都在正文里用括号标注了来源文件；**未采集的项一律不写数字**（例如并发容量、公网链路延迟、线上平均重试次数）。
> **边界纪律**：本文件全程按 `docs/thesis/EVIDENCE_MATRIX.md` 的 `Status` 与 `Limitation` 写——凡审计判为 `PARTIALLY_SUPPORTED` 的主张，一律带范围与限定词，**不写无限定的强结论**。

---

## 一、中文版

### 项目标题行（简历里直接可用）

> **基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台**（个人工程实践项目 · 全程设计并实现）
> 技术栈：Kafka 4.2.1（KRaft）· Flink 1.20.1 · Doris 4.1.4 · Spark 3.5.7 · Iceberg 1.10.2 + Hive Metastore 3.1.3 · MinIO(S3A) · Airflow 3.3.2 · FastAPI · LangGraph 1.1.0 · MCP 2.2.0 · BM25 词法检索 · Prometheus v3.13.3 + Grafana 13.2.2

---

### ① 批流一体数据底座（一条事实源、两条独立链路、一套口径）

- **S 场景**：电商数据同时以两种形态产生——秒级要求的点击/加购/下单/支付/退款事件流，与准确、可回溯的日/月级经营分析。两套系统并行会让同一指标出现两份实现，一旦实时与离线对同一个"GMV"给出不同数值，上层任何智能应用都失去判断依据。
- **T 任务**：构建统一事件入口的批流一体平台，并把"两条链路算的是同一个数"从设计原则变成**可证伪的断言**——只要有一个窗口对不上，结论就不成立。
- **A 行动**：
  - **设计**统一事件入口：Kafka 4.2.1（**KRaft 模式，不部署 ZooKeeper**），4 个 topic 各 3 分区；订单类事件以 `order_id`、行为事件以 `user_id` 为分区键，**保证同一订单/同一用户的事件落入同一分区，从而保证分区内有序**。
  - **实现**实时链路：Flink 1.20.1 以**事件时间**语义 + 1 分钟滚动窗口（TUMBLE）聚合，写入 Doris 4.1.4；Doris 侧用 UNIQUE KEY + merge-on-write 承接 Routine Load，使同一窗口的多次中间态**收敛为最新值**（这是"跑批期间暂停 Flink、恢复后从已提交位点续跑不重复计数"的基础）。
  - **实现**离线链路：Spark 3.5.7 从 MySQL 与 Kafka 抽取，按 **ODS → DWD → DWS → ADS** 四层建模；由 Airflow 3.3.2 的 DAG 按依赖调度 9 个任务（pause → ods → archive → dwd → dws → ads → reconcile → load → restore）。
  - **设计并实现逐窗口交叉对账**：两侧按 `window_start` 做 **FULL OUTER JOIN**（而非 INNER JOIN——用 INNER JOIN 会把"整行缺失"这类最严重的差异静默过滤掉），逐窗口比对，差异逐列落盘；对账区间由两侧数据范围求交并**尾部留 3 分钟安全边界**；**单边窗口单独统计**。
  - **建立**资源约束的自动化闸门：16 GB 单机上实时链路常驻约 13.7 GB，因此实现**错峰批处理**（暂停 Flink 栈释放约 1.65 GB → 内存闸门检查 → 跑批 → 强制恢复并自检），闸门阈值 3000 MB。
- **R 结果**：
  - 交易域 **11458 个分钟窗口逐窗口比对，不一致 0**（判据为 **7 个可加指标 + 1 个去重指标**，去重指标两侧采用同一近似去重语义），两侧 GMV **精确到分相等（51,890,375.77）**（`docs/sprint/SPRINT_3.md` §7；`docs/PERFORMANCE.md` §0.1 记实时 ADS 整表 11459 行，与对账口径 11458 同源不同口径）。
  - 流量域 **19643 个分钟窗口**：**7 个判据列（`uv` / `pv` / 5 个行为计数）逐窗口一致**、不一致 0、单边窗口 0/0，PV 两侧 19998 == 19998（`docs/sprint/SPRINT_5.md` §9.2）。
  - 层间一致：DWD 与 ODS **逐表行数相等**（1200 / 600 / 6000 / 5406 / 254）（`docs/sprint/SPRINT_3.md` §7.1）。
  - 实时链路状态：8 个 Flink sink 作业各 1 个实例，8 个 Doris Routine Load 全部 RUNNING 且 `errorRows = 0`（`AGENTS.md` §15.2）。
  - 内存闸门**实测主动拒绝过一次批处理**（当时可用 2258 MB）——拒绝而非硬跑（`AGENTS.md` §15.8）。
  - **边界（必须一起写）**：上述一致性结论限于**对账区间内**、**补零口径**下的**逐窗口指标值**；11458 个窗口中有 **5504 个（48.03%）两侧 GMV 均为 0（平凡一致）**，承载判别力的是 **5954 个有业务量的窗口**；交易域**没有**"单边窗口 = 0"的硬断言（只有"实时侧 ≥ 离线侧 × 90%"的覆盖率守卫），且派生比率列不在判据内（`docs/thesis/EVIDENCE_MATRIX.md` §3）。

---

### ② Lakehouse 与表格式迁移（Parquet → Iceberg）

- **S 场景**：Parquet 外部表方案可用但缺三样能力——**没有 ACID**（写入中途失败会留下半份数据，只能靠整分区重写兜底）、**没有 schema 演进**、**没有时间旅行**（无法回答"昨天的表长什么样"，出问题也无法回滚到某个快照）。
- **T 任务**：在不影响实时链路、且**可回退**的前提下完成表格式迁移，并用可核对的证据说明"迁移没有破坏数据"到什么程度。
- **A 行动**：
  - **设计**迁移策略为"**不原地改**"：Parquet 外部表保持不动，Iceberg 表用不同库名建（`iceberg.lakehouse_iceberg`），迁移期逐表对照，出问题可立刻退回——因为同名替换会覆盖元数据且**无法回滚**。
  - **实现**表格式落地：Iceberg 1.10.2 + **HiveCatalog**（复用机器上已有的 Hive Metastore 3.1.3，使两种表格式**共用同一个目录**，迁移期可逐表对照）；否决 REST Catalog（需多一个常驻进程）与 Hadoop Catalog（无中心元数据，无法与既有 HMS 表共存）。
  - **优化**分区写入的 OOM 结构性问题：现象是执行器连续 OOM、4 次 `Lost executor ... code 52`；根因是 **703 个 `dt` 分区 vs 默认 200 个 shuffle 分区，AQE 又把小分区合并回去**，导致少数 task 各持有几百个分区写入器——**数据只有 2 万行，与数据量无关**。修法是 `INSERT` 加 `DISTRIBUTE BY 分区列` + `spark.sql.shuffle.partitions` 提高到 1000 + 关掉 AQE 合并；**明确不采用"把执行器内存调大"**（那只是掩盖症状）。
  - **建立**迁移校验的判据集：每张表一项 `DESCRIBE EXTENDED` 的 `Provider == iceberg` 断言、一项行数一致断言、按金额列逐列做合计一致断言，末尾再加一项"库表数 == 迁移清单表数"的清单完整性断言；验收脚本另有**逐个表名核对**与**反向核对**（库里没有清单之外的表）。
- **R 结果**：
  - 迁移清单 **18 张表 → 23 张表**；阶段 4 首次迁移校验 **60/60**（范围 **18 张表**）、流量域建成后扩到 23 张表复跑 **70/70**（`docs/sprint/SPRINT_5.md` §8.1；`AGENTS.md` §15 Sprint 5 行）。**两个数的范围不同，不合并、不取平均。**
  - 校验构成可核对：`60 = 18 Provider + 18 行数 + 23 金额列 + 1 库表数`；`70 = 23 + 23 + 23 + 1`（`.tmp/audit-iceberg.md` §1.4 独立复算）。
  - **可主张的最大范围（带边界写）**：在**表目录身份、行数、6 个金额列（`gmv`/`amount`/`payment_amount`/`refund_amount`/`avg_order_amount`/`total_amount`）的整表合计**这三个维度上**未发现差异**（金额为 `DECIMAL` 精确比较、无容差）；**逐行内容、字符串与时间字段、`NULL`/空串、decimal 精度、分区布局、schema 等价性、重复行、快照元数据、回滚演练共 11 个维度未验证**（`docs/thesis/EVIDENCE_MATRIX.md` §4.3）。
  - 迁移过程中的两处实测结论已沉淀为规范：**catalog 名不得与任何库名同名**（同名时 `CREATE DATABASE` 解析出"目录 + 空命名空间"）；**Iceberg 表一律写三段名**（Spark 的两段名 = (命名空间, 表) 且**永远属于当前目录**，只写两段名会静默落到别处，而**行数核对读的也是同一个错地方**）。

---

### ③ 可信 AI 数据分析（Agent / 检索 / MCP / 安全边界）

- **S 场景**：让大模型直接对数据仓库写查询语句，会同时引入两类风险——**越权取数**与**编造查询结果**；而本机没有条件走 embedding + 向量库的常规检索路线。
- **T 任务**：构建一个**不持有数据库凭据**、**回答可逐条核对**、**失败与重试可数**的自然语言问数 Agent，并让"权限最小化"成为可枚举、可审查的事实。
- **A 行动**：
  - **实现**图式编排（LangGraph 1.1.0）：`retrieve → plan → execute → validate →（有阻塞性问题且还有额度）reflect → plan → …… → summarize`，实测对外可读 **6 个节点、8 条边**；设**三重终止上界**（重试额度默认 2 / 工具调用总轮次默认 6 / 总耗时默认 120 s），且**收尾调用不携带 `tools`**，否则图停不下来。关键设计：**`reflect` 之后回到 `plan` 而不是 `execute`**——错误信息通常意味着计划本身错了，回到 `execute` 只是把同一条错 SQL 重放一遍，**那是重试，不是重新规划**。
  - **实现**检索层为**词法检索**（BM25，`k1=1.2`、`b=0.75`，含 idf 与文档长度归一化）+ **CJK bigram 自实现分词**（不引入 `jieba`）+ **显式同义词表**；语料 **65 篇**（指标 22 / 通用约定 15 / 事件格式 4 / 分层说明 8 / 表结构 16），且**语料分路（channels）刻意对外暴露**——任何一路缺失都会显示原因，而不是让检索悄悄变差。**未引入向量库、未使用 embedding**（`.tmp/audit-agent.md` §C1）。
  - **实现** MCP 工具暴露（mcp 2.2.0）：暴露 **4 个只读工具**，与直连路径**共用同一个守卫、同一个只读账号、同一个 HTTP 终点**；**不做"能连 MCP 就用 MCP"的自动降级**——自动降级会让该路径在连接抖动后悄悄消失而所有断言仍然全绿。
  - **实现** SQL 守卫（服务端，5 层检查）：必须以 `SELECT` 开头 → 拒绝分号多语句与 4 类注释 → **26 个禁用关键字** → **18 张表白名单**（16 张业务表 + 2 张元数据表）→ **强制 LIMIT**（三种写法分别收敛）；数据库侧另设专用只读账号 `agent_ro`（仅 `SELECT_PRIV`）。
  - **建立**可信性的架构边界：Agent 进程**无数据库驱动依赖、无直连代码、无凭据读点**，systemd 以非 root 运行且不含 `EnvironmentFile`；回答必须附**实际执行**的 `executed_sql`（守卫会改写 LIMIT，所以返回改写后那一条）、**带文件名与行号**的口径来源 `docs`、以及**由服务端提取**的血缘 `tables`。
- **R 结果**：
  - 各阶段验收：Sprint 7 **49/49**、Sprint 8 **71/0/0**、Sprint 9 **59/0/0**、Sprint 10 **84/0/0**（`AGENTS.md` §15）。
  - **四类攻击实测被拒且原因正确**：`DELETE → NOT_SELECT`；未授权表 → `TABLE_NOT_ALLOWED`；多语句 → `MULTI_STATEMENT`；注释注入 → `COMMENT`；强制 LIMIT 实测（请求 5 行 → `executed_sql` 带 `LIMIT 5`、`row_count = 5`）（`AGENTS.md` §15.6）。
  - 同一 SQL 经 MCP 与经直连 HTTP 的结果**逐字段相同**、`executed_sql` 相同；经 MCP 的拒绝语义可透传（`is_error=False` + `NOT_SELECT` + 底层 HTTP 400 同码）（`AGENTS.md` §15.12）。
  - **反思重试路径被真实触发**：线上验收脚本断言 `retries ≥ 1` 且执行轨迹含 `reflect` 与 `plan`，失败码命中 `TABLE_NOT_ALLOWED`；`= 2` 这一具体值只出现在 mock 单元测试里；**线上平均重试次数未采集**（`.tmp/audit-agent.md` §B）。
  - 检索的**正例与负例都写成验收项**：问「最近一周卖了多少钱」（**不含 GMV 字样**）命中 `metric:gmv | sql/metadata/metrics.md:33`；负例「今天天气怎么样适合钓鱼吗」命中为空。
  - **一处已闭环处置的安全缺口（主动写）**：**P0-1（逗号连接绕过表级白名单）：已修复并线上复验通过。** 链路：代码根因定位（`_TABLE_RE` 只认 `FROM|JOIN`，且校验与血缘共用该正则）→ 纯函数探针 → **线上复现**（2026-09-27 14:56:19：同一张表仅因写在逗号后即从 `REJECT` 变 `ALLOW`、`row_count=3` 读出真实数据，且血缘漏报）→ 先补对抗用例（含**反向防过度拦截**用例）→ 改守卫（FROM/JOIN 子句内按表清单结构推进、逗号仅当前面已是表项时才算分隔符、校验与血缘同源）→ 服务器重启 → **同批探针复验**（2026-09-27 15:13:24：绕过被拦为 `REJECT TABLE_NOT_ALLOWED`；白名单内 JOIN 仍 `ALLOW` 且血缘正确 —— **无过度拦截**）。反向用例盯的是"逗号不是表名"（列分隔 / `GROUP BY` / 函数参数里的逗号不得被误判，否则多列查询会被误拒）；复验三条判据即"**绕过被拒 + 白名单内两表 `JOIN` 仍放行 + 血缘报全**"；修复落地证据为 `sqlguard.py` md5 `fab72ca2606abb3d882ad771b2421941`、mtime 15:05。措辞因此收窄为"**守卫对显式声明的数据源强制表级白名单**"（"不存在其它绕过路径"是**不可穷尽验证**的全称命题，不作此声称）（`.tmp/audit-agent.md` §A6-1；`docs/thesis/答辩材料/FINAL_AUDIT_REPORT.md` P0-1；`services/api/app/sqlguard.py` 的提取逻辑与其上方注释）。

---

### ④ 治理与可观测（数据质量 / 对账 / 监控 / 性能基线）

- **S 场景**：一次性的校验脚本在演示里能跑，但不产生**持续**的可信度——没有人会在每天批处理之后手工敲一遍；同时"性能好不好"如果没有基线，就只剩"感觉快了"。
- **T 任务**：建立**自动触发**的数据质量校验与**可复现**的性能基线，并让"校验失败"这件事本身有可执行的证明。
- **A 行动**：
  - **建立**数据质量校验体系：一份清单文件（每条含域 / 严重级 / 引擎 / SQL / 算子 / 阈值 / 说明 / 排查提示）+ 一个执行引擎 + 每条校验一个 SQL 文件（**必须返回恰好一个标量**）+ 一个可调度总入口；**25 条校验覆盖 7 类**（行数非空守卫 / 主键唯一 / 关键字段非空 / 枚举合法 / 漏斗单调 / 层间一致 / 金额关系 / 新鲜度）。分 `doris` 与 `spark` 两个引擎是**有意的内存纪律**：`doris` 引擎不起 Spark，可在实时链路运行时随便跑；`spark` 引擎默认不跑，需显式驱动并受内存闸门约束。
  - **建立**失败路径的可执行证明：`--proof-fail` **故意**把两条校验的期望值改成不可能成立的值（`999999999999`、`-1`），断言必须返回退出码 1，然后**再跑一次正常校验**证明失败来自断言而不是数据被改坏。
  - **建立**监控：Prometheus `v3.13.3`（LTS）+ Grafana `13.2.2`，`mem_limit` 各 256m（合计 **512m** 为预算上限）；抓取目标含 prometheus / doris-fe / doris-be / grafana。
  - **建立**四类性能基线（采集脚本入仓、固定 SQL、固定问题文本、现场条件完整记录）：只读接口延迟、实时 vs 离线同口径对比、批量作业耗时（取自 Airflow 元数据库既有记录，**未为此重跑流水线**）、Agent 端到端耗时分解。
- **R 结果**：
  - 质量校验实测 **24 通过 / 0 失败 / 1 跳过**（跳过的是 spark 引擎项）；`--proof-fail` **两次演示均退出码 1**，恢复后仍全绿（`AGENTS.md` §15.11）。
  - 监控实测：抓取目标 **4/4 up**（Doris FE 1564 条、BE 1019 条指标）；实测占用 Prometheus 105 MiB / Grafana 187 MiB（`AGENTS.md` §15.11）。
  - 性能基线（n=7 中位数，单机回环、热缓存）：点查 **8.5 ms**、聚合 **8.3 ms**、两表关联 **11.7 ms**、`GET /overview` **53.8 ms**（`docs/PERFORMANCE.md` §1.2）。
  - 批量作业：9 个任务 `duration` 合计 **1992.3 s**，**端到端约 81 分钟**；错峰固定成本 149.8 s，四个分层/归档作业占 **76%**（`docs/PERFORMANCE.md` §3）。
  - Agent 端到端中位数 **19187.5 ms（n=2）**，其中可单独测量的数据侧仅 **18.3 ms**（检索 2.9 + 取数 15.4）——**约 99.9% 的耗时来自外部 LLM 服务往返**，因此该数字**不应被理解为数据平台的查询性能**（`docs/PERFORMANCE.md` §4；`.tmp/audit-perf.md`）。
  - 全量自动化测试 **353 passed / 0 failed / 0 skipped / 3 xfailed**（`docs/sprint/SPRINT_12.md` §8.3）。**时点标注**：该值为 Sprint 12 终局（**修复前时点**）；P0-1 修复复跑后为 **381 passed / 0 failed**，同期 `verify-sprint-12.sh` 由 **32/0/0 → 42/0/0**（`docs/thesis/外部审查包.md` §2 验收矩阵）——**两组数字口径与时点不同，引用时必须带时点，不互相替代**。
  - **边界（必须一起写）**：性能数字全部为**单机、单请求串行、`127.0.0.1` 回环、热缓存**口径，**未做任何并发/压力测试**（本机内存不允许，采集时可用仅 3921 MB），**未采集公网链路延迟**，因此**不构成并发容量结论**；其中"两表关联 11.7 ms"那一格采集时维表为 0 行（`row_count = 0`），修好同口径重跑约 **7.4 ms**（`.tmp/audit-perf.md` §（三·补）E）。

---

### 中文版一句话总结（放在项目描述开头或结尾）

> 在一个 16 GB 单机实验环境里，设计并实现了一条完整的批流一体数据链路（Kafka → Flink → Doris 与 Spark → Iceberg → Doris），用**逐分钟窗口交叉对账**把"两条链路算的是同一个数"变成可证伪的断言（交易域 11458 个窗口、流量域 19643 个窗口，不一致 0），并在此之上构建了**不持有数据库凭据**、回答附实际执行 SQL 与口径来源的自然语言问数 Agent；全部结论均标注证据来源与适用边界（合成数据、单机回环、无并发测试）。

---

## 二、English Version

### Project headline

> **Unified Batch-and-Streaming Intelligent Data Analytics Platform with a Lakehouse and an AI Agent** (independent engineering project · designed and built end to end)
> Stack: Kafka 4.2.1 (KRaft) · Flink 1.20.1 · Doris 4.1.4 · Spark 3.5.7 · Iceberg 1.10.2 + Hive Metastore 3.1.3 · MinIO (S3A) · Airflow 3.3.2 · FastAPI · LangGraph 1.1.0 · MCP 2.2.0 · BM25 lexical retrieval · Prometheus v3.13.3 + Grafana 13.2.2

---

### ① Unified batch-and-streaming data foundation

- **Situation.** E-commerce data arrives in two forms at once: an event stream (views, cart additions, orders, payments, refunds) that demands second-level freshness, and day/month-level operational analytics that demand accuracy and traceability above freshness. Two separate systems produce two implementations per metric; once the streaming and batch paths disagree on a single number such as GMV, every intelligence layer built on top loses its basis for judgement.
- **Task.** Build a platform with a single event ingress, and turn "both paths compute the same number" from a design principle into a **falsifiable assertion** — one mismatching window invalidates the claim.
- **Action.**
  - **Designed** a single event ingress on Kafka 4.2.1 in **KRaft mode (no ZooKeeper)**, four topics with three partitions each; order events are keyed by `order_id` and behaviour events by `user_id` so that all events of one order or one user land in the same partition, **guaranteeing per-partition ordering**.
  - **Implemented** the streaming path: Flink 1.20.1 with **event-time semantics** and one-minute tumbling windows writing into Doris 4.1.4; Doris ingests via Routine Load on a **UNIQUE KEY with merge-on-write**, so repeated intermediate states of the same window **converge to the latest value** — the property that makes "suspend Flink during the batch run, resume from the committed offset" safe from double counting.
  - **Implemented** the batch path: Spark 3.5.7 extracting from MySQL and Kafka into a four-layer model (**ODS → DWD → DWS → ADS**), orchestrated by an Airflow 3.3.2 DAG of nine tasks.
  - **Designed and implemented window-level cross-path reconciliation**: both sides are joined with a **FULL OUTER JOIN on `window_start`** (not INNER JOIN — an inner join silently drops the most severe class of difference, a missing row), compared window by window with per-column diffs persisted; the reconciliation interval is the intersection of both sides' ranges with a **three-minute safety margin**, and **one-sided windows are counted separately**.
  - **Established** an automated resource gate: the streaming stack idles at roughly 13.7 GB on a 16 GB host, so batch execution is **time-staggered** (suspend the Flink stack, releasing about 1.65 GB → memory gate check → run the batch → force restore with a self-check) with a 3,000 MB gate threshold.
- **Result.**
  - Trade domain: **11,458 one-minute windows compared window by window, 0 mismatches** across **7 additive metrics plus 1 deduplicated metric** (both sides using the same approximate-dedup semantics), with GMV **equal to the cent on both sides (51,890,375.77)** (`docs/sprint/SPRINT_3.md` §7).
  - Traffic domain: **19,643 windows, 0 mismatches, 0 one-sided windows on either side**, with the **7 criteria columns** (`uv`, `pv` and five behaviour counters) matching window by window (`docs/sprint/SPRINT_5.md` §9.2).
  - Layer parity: DWD equals ODS **table by table** (1200 / 600 / 6000 / 5406 / 254) (`docs/sprint/SPRINT_3.md` §7.1).
  - Streaming status: 8 Flink sink jobs, one instance each; 8 Doris Routine Load jobs RUNNING with `errorRows = 0` (`AGENTS.md` §15.2).
  - The memory gate **actively refused a batch run once** (2,258 MB available) — refusal instead of running anyway (`AGENTS.md` §15.8).
  - **Boundary, stated together with the result:** the agreement claim holds **inside the reconciliation interval**, under the **missing-side-filled-with-zero** convention, for the **per-window metric values**. Of the 11,458 windows, **5,504 (48.03%) have zero GMV on both sides (trivially equal)**; the evidentiary weight rests on the **5,954 windows that carry business volume**. The trade domain has **no** hard assertion that one-sided windows equal zero (only a "streaming side ≥ 90% of batch side" coverage guard), and derived ratio columns are outside the criteria set (`docs/thesis/EVIDENCE_MATRIX.md` §3).

---

### ② Lakehouse and table-format migration (Parquet → Iceberg)

- **Situation.** The Parquet external-table approach worked but lacked three capabilities: **no ACID** (a failed write leaves half a dataset and the only fallback is rewriting a whole partition), **no schema evolution**, and **no time travel** (no way to ask what a table looked like yesterday, or to roll back to a snapshot).
- **Task.** Migrate the table format without disturbing the streaming path and **with a way back**, and state precisely how far "the migration did not damage the data" is supported by evidence.
- **Action.**
  - **Designed** the migration as **non-in-place**: Parquet external tables stay untouched and Iceberg tables are created under a different database name, so both formats coexist during migration and either side can be retired at once — a same-name replacement would overwrite metadata and **could not be rolled back**.
  - **Implemented** Iceberg 1.10.2 with **HiveCatalog**, reusing the existing Hive Metastore 3.1.3 so that both table formats **share one catalog** and can be compared table by table; REST Catalog (an extra resident service) and Hadoop Catalog (no central metadata) were rejected.
  - **Optimised** a structural OOM: executors failed repeatedly with `Lost executor ... code 52`; the root cause was **703 `dt` partitions against a default of 200 shuffle partitions, with AQE merging small partitions back**, leaving a few tasks holding hundreds of partition writers — **with only 20,000 rows, so unrelated to data volume**. The fix was `DISTRIBUTE BY` on the partition column, raising `spark.sql.shuffle.partitions` to 1000 and disabling AQE coalescing; **increasing executor memory was explicitly rejected** as treating the symptom.
  - **Established** the verification criteria set: per table one `DESCRIBE EXTENDED` `Provider == iceberg` assertion, one row-count assertion, per-money-column total-sum assertions, plus a manifest-completeness assertion ("Iceberg table count == migration manifest count"); the acceptance script additionally checks **each table name** and **the reverse direction** (no tables outside the manifest).
- **Result.**
  - The migration manifest grew from **18 to 23 tables**; the first migration check reported **60/60** (over **18 tables**) and the re-run after the traffic domain was modelled reported **70/70** (over **23 tables**) (`docs/sprint/SPRINT_5.md` §8.1; `AGENTS.md` §15). **The two figures cover different scopes and are neither merged nor averaged.**
  - Both counts are reproducible from the criteria: `60 = 18 provider + 18 row-count + 23 money-column + 1 catalogue-count`; `70 = 23 + 23 + 23 + 1` (independently recomputed in `.tmp/audit-iceberg.md` §1.4).
  - **Maximum defensible claim:** no differences were found on **three dimensions** — table catalogue identity, row counts, and the **whole-table totals of six money columns** (`gmv`, `amount`, `payment_amount`, `refund_amount`, `avg_order_amount`, `total_amount`; `DECIMAL` exact comparison, no tolerance). **Eleven further dimensions remain unverified**, including row-level content, string and time fields, `NULL`/empty-string handling, decimal rounding, partition layout, schema equivalence, duplicate rows, snapshot metadata and rollback drills (`docs/thesis/EVIDENCE_MATRIX.md` §4.3).
  - Two findings were promoted into project-wide rules: a **catalog name must not collide with any database name**, and **Iceberg tables must always be referenced with a three-part name** (Spark's two-part name is `(namespace, table)` and **always belongs to the current catalog**, so a two-part name silently lands elsewhere — **and the row-count check reads the same wrong place**).

---

### ③ Trustworthy AI data analysis (agent, retrieval, MCP, security boundary)

- **Situation.** Letting a large language model query a warehouse directly introduces two risks at once — **unauthorised data access** and **fabricated query results** — while the host had no room for the conventional embedding-plus-vector-store retrieval route.
- **Task.** Build a natural-language data agent that **holds no database credentials**, whose answers are **verifiable line by line**, whose **failures and retries are countable**, and whose least-privilege posture is an enumerable, auditable fact.
- **Action.**
  - **Implemented** graph-based orchestration (LangGraph 1.1.0): `retrieve → plan → execute → validate → (on blocking issues with budget left) reflect → plan → … → summarize`, exposing **6 nodes and 8 edges**; three termination bounds (retry budget 2, tool-round budget 6, total timeout 120 s) with the **final summarisation call carrying no `tools`**, otherwise the graph cannot stop. Key design: **`reflect` returns to `plan`, not to `execute`** — an error usually means the plan itself was wrong, and returning to `execute` merely replays the same bad SQL: **that is a retry, not a re-plan**.
  - **Implemented** retrieval as **lexical search** (BM25, `k1=1.2`, `b=0.75`, with idf and document-length normalisation) plus **self-implemented CJK bigram tokenisation** (no `jieba`) and an **explicit synonym table**; the corpus holds **65 documents** (22 metric, 15 convention, 4 event-format, 8 layering, 16 table-schema), and **per-channel corpus status is deliberately exposed** so a missing channel reports a reason instead of silently degrading retrieval. **No vector store and no embedding were introduced** (`.tmp/audit-agent.md` §C1).
  - **Implemented** MCP tool exposure (mcp 2.2.0): **four read-only tools** sharing **the same guard, the same read-only account and the same HTTP endpoint** as the direct path; **automatic fallback was deliberately not implemented**, because it would let the MCP path vanish silently after a connection blip while every assertion stayed green.
  - **Implemented** a five-layer server-side SQL guard: must start with `SELECT` → reject semicolon multi-statements and four comment forms → **26 forbidden keywords** → an **18-table allow-list** (16 business + 2 metadata) → **mandatory LIMIT** (three syntaxes normalised); the database side additionally uses a dedicated read-only account `agent_ro` holding `SELECT_PRIV` only.
  - **Established** the architectural boundary for trust: the agent process has **no database driver dependency, no direct-connection code and no credential read path**, runs under systemd as a non-root user without `EnvironmentFile`; every answer carries the **actually executed** `executed_sql` (the guard rewrites LIMIT, so the rewritten statement is the one returned), the matched metric sources `docs` **with file name and line number**, and lineage `tables` **extracted server-side**.
- **Result.**
  - Acceptance by stage: Sprint 7 **49/49**, Sprint 8 **71/0/0**, Sprint 9 **59/0/0**, Sprint 10 **84/0/0** (`AGENTS.md` §15).
  - **Four attack classes were rejected with correct reasons**: `DELETE → NOT_SELECT`; unauthorised table → `TABLE_NOT_ALLOWED`; multi-statement → `MULTI_STATEMENT`; comment injection → `COMMENT`; mandatory LIMIT verified (requested 5 rows → `executed_sql` carries `LIMIT 5`, `row_count = 5`) (`AGENTS.md` §15.6).
  - The same SQL returns **field-by-field identical** results and identical `executed_sql` over MCP and over direct HTTP; refusal semantics survive the MCP hop (`is_error=False` + `NOT_SELECT` + underlying HTTP 400 with the same code) (`AGENTS.md` §15.12).
  - The **reflection-retry path was genuinely triggered**: the online acceptance script asserts `retries ≥ 1` with the trace containing both `reflect` and `plan` and the failure code matching `TABLE_NOT_ALLOWED`; the exact value `= 2` appears only in a mocked unit test, and **no online retry distribution has been collected** (`.tmp/audit-agent.md` §B).
  - Retrieval has **both a positive and a negative acceptance case**: "how much did we sell last week" (**without the token GMV**) hits `metric:gmv | sql/metadata/metrics.md:33`, while an irrelevant question returns no hits.
  - **A security gap found and closed out honestly — P0-1 (comma-join bypass of the table allow-list): localised (code root cause) → reproduced against the live read-only endpoint (2026-09-27 14:56:19; the same table flipped from REJECT to ALLOW purely because it followed a comma, and real data was returned) → fix landed in code (table extraction reworked in `sqlguard.py`: walk the FROM/JOIN clause as a structured table list, treat a comma as a table separator **only** when it follows a table item, and keep **validation and lineage on one extraction result**; `tests/test_sql_guard_adversarial.py` gained comma-join rejection cases **plus a reverse set that prevents over-blocking**) → server-side three-criteria re-verification in progress.** The reverse set guards the opposite failure: commas used as column separators or inside `GROUP BY` / function arguments must **not** be mistaken for table names, otherwise every multi-column query would be wrongly refused. The three server-side criteria are **explicit rejection + complete lineage + a whitelisted two-table `JOIN` still allowed**. The wording therefore remains narrowed to "the guard enforces a table-level allow-list for **explicitly declared** data sources" — "no other bypass exists" is a **non-exhaustively verifiable** universal claim and is not made (`.tmp/audit-agent.md` §A6-1; `docs/thesis/答辩材料/FINAL_AUDIT_REPORT.md` P0-1; the extraction logic in `services/api/app/sqlguard.py`).

---

### ④ Governance and observability (quality, reconciliation, monitoring, performance baselines)

- **Situation.** A one-off validation script demos well but produces no **sustained** trustworthiness — nobody re-runs it by hand after every batch; and without a baseline, "is it fast" reduces to "it felt fast".
- **Task.** Establish **automatically triggered** data-quality checks and a **reproducible** performance baseline, and make "the check fails" itself an executable claim.
- **Action.**
  - **Established** the quality framework: one manifest (each entry carrying domain, severity, engine, SQL, operator, threshold, description and troubleshooting hint) plus an execution engine, one SQL file per check (**must return exactly one scalar**) and a schedulable entry point; **25 checks across 7 categories** (row-count non-empty guards, primary-key uniqueness, key-field non-null, enum validity, funnel monotonicity, layer parity, money relationships, freshness). Splitting into `doris` and `spark` engines is a **deliberate memory discipline**: the `doris` engine starts no Spark and can run while the streaming path is live; the `spark` engine is off by default and subject to the memory gate.
  - **Established** an executable failure proof: `--proof-fail` **deliberately** sets two checks' expected values to impossible ones (`999999999999`, `-1`), asserts exit code 1, and then **re-runs the normal checks** to show the failure came from the assertion rather than from damaged data.
  - **Established** monitoring with Prometheus `v3.13.3` (LTS) and Grafana `13.2.2`, each capped at `mem_limit 256m` (**512m total budget**), scraping prometheus / doris-fe / doris-be / grafana.
  - **Established** four performance baselines (collection script committed, fixed SQL, fixed question text, full on-site conditions recorded): read-only API latency, streaming-versus-batch at equal granularity, batch job duration (read from the Airflow metadata database — **no pipeline was re-run to measure it**), and end-to-end agent latency decomposition.
- **Result.**
  - Quality checks: **24 passed / 0 failed / 1 skipped** (the skipped item is the Spark-engine check); `--proof-fail` returned **exit code 1 on both demonstrations** and everything was green again afterwards (`AGENTS.md` §15.11).
  - Monitoring: **4/4 scrape targets up** (Doris FE 1,564 and BE 1,019 metrics); measured footprint 105 MiB (Prometheus) and 187 MiB (Grafana) (`AGENTS.md` §15.11).
  - Latency baselines (median of n=7, single host, loopback, warm cache): point query **8.5 ms**, aggregation **8.3 ms**, two-table join **11.7 ms**, `GET /overview` **53.8 ms** (`docs/PERFORMANCE.md` §1.2).
  - Batch run: nine tasks totalling **1,992.3 s** of `duration`, **about 81 minutes end to end**; the time-staggering fixed cost is 149.8 s, and the four layering/archiving jobs account for **76%** (`docs/PERFORMANCE.md` §3).
  - Agent end to end: median **19,187.5 ms (n=2)**, of which the measurable data side is only **18.3 ms** (2.9 retrieval + 15.4 fetch) — **about 99.9% of the time is external LLM round-trips**, so this figure **must not be read as the platform's query performance** (`docs/PERFORMANCE.md` §4; `.tmp/audit-perf.md`).
  - Full automated test suite: **353 passed / 0 failed / 0 skipped / 3 xfailed** (`docs/sprint/SPRINT_12.md` §8.3). **Time-point note:** that figure is the Sprint 12 end state (**pre-fix**); the post-P0-1-fix re-run reports **381 passed / 0 failed**, and `verify-sprint-12.sh` moved from **32/0/0 to 42/0/0** (`docs/thesis/外部审查包.md` §2) — **the two sets differ in both scope and time point and must be quoted with their time point, never substituted for each other**.
  - **Boundary, stated together with the result:** all latency figures are **single-host, single-request serial, `127.0.0.1` loopback and warm-cache** measurements; **no concurrency or load testing was performed** (the host's memory did not allow it — 3,921 MB available during collection) and **no public-network latency was collected**, so they **do not constitute a concurrency-capacity claim**. The "two-table join 11.7 ms" entry was measured while the dimension table held 0 rows (`row_count = 0`); after the defect was fixed, the same query measured about **7.4 ms** under the same method (`.tmp/audit-perf.md` §(三·补) E).

---

### English one-paragraph summary

> Designed and built an end-to-end batch-and-streaming data platform on a single 16 GB host (Kafka → Flink → Doris alongside Spark → Iceberg → Doris), turning "both paths compute the same number" into a falsifiable assertion through **window-level cross-path reconciliation** (11,458 trade-domain windows and 19,643 traffic-domain windows, 0 mismatches), and built on top of it a natural-language data agent that **holds no database credentials** and returns the actually executed SQL together with line-referenced metric definitions. Every claim is stated with its evidence source and its scope (synthetic data, single host, loopback, no concurrency testing).

---

## 三、面试官可能追问的 5 个点 + 一句话答法

### ① "你这数据是合成的吧？那结论还有意义吗？"

> **一句话**：全部是程序生成的合成数据，种子 `20260926` 同种子可复现、业务约束在生成期强制、Kafka 事件是基于**真实写进 MySQL 的那批数据**生成的（不是各随机一套）——**正因为这三点，逐窗口对账的结论才有资格被第三方复核**；它不代表真实业务分布，这是最大的限制，接真实数据源需要引入 CDC。
> （依据：`docs/thesis/DATA_AND_LIMITATIONS.md` 第一部分第 1、3、7 节）

### ② "两条链路真的算的是同一个数？这个结论的边界在哪？"

> **一句话**：在两侧范围求交、尾部留 3 分钟安全边界的**对账区间内**，按 `window_start` 全外连接、逐窗口比对交易域 **7 个可加指标 + 1 个去重指标**，在"缺失侧补 0"口径下差值为 0；**不覆盖区间外数据、不含两侧派生比率的逐窗口比对**，而且 11458 个窗口里有 **5504 个是两侧为 0 的平凡一致**，真正承载判别力的是 **5954 个有业务量的窗口**；流量域 19643 个窗口的 **7 个判据列（`uv`/`pv`/5 个行为计数）** 逐窗口一致、单边窗口 0/0。
> （依据：`.tmp/audit-reconcile.md` §5、§7；`docs/thesis/EVIDENCE_MATRIX.md` §3）

> **另一条边界（本问若继续追问"那有没有例外"时答）**：流量域有 **1 个实时侧窗口**的比率列与它自己的计数矛盾——完整口径见第 ⑤ 问，**此项刻意保留在面谈口径中**。

### ③ "你的 SQL 守卫听说有个绕过？修了吗？"

> **一句话**：守卫对**显式声明的**数据源（`FROM`/`JOIN`）强制 18 张表白名单 + SELECT-only + 强制 LIMIT，四类攻击实测被拒；审计另外发现 **P0-1：逗号连接（隐式 CROSS JOIN）绕过表级白名单**——**已修复并线上复验通过**。链路：代码根因定位（`_TABLE_RE` 只认 `FROM|JOIN`，校验与血缘共用该正则）→ 纯函数探针 → **线上复现**（14:56:19：同一张表仅因写在逗号后即从 `REJECT` 变 `ALLOW`、`row_count=3` 读出真实数据且血缘漏报）→ 先补对抗用例（含反向防过度拦截用例）→ 改守卫（校验与血缘同源）→ 服务器重启 → **同批探针复验**（15:13:24：绕过被拦为 `REJECT TABLE_NOT_ALLOWED`；白名单内 JOIN 仍 `ALLOW` 且血缘正确——**无过度拦截**）。所以我的表述始终是"**显式声明的数据源强制白名单**"——**不说"不存在其它绕过路径"**（那是不可穷尽验证的全称命题）。
>
> **★ 同批探针的修复前 / 修复后对照（这张表比任何形容词都有力）**
>
> | 探针（只读 `POST /query`） | 修复前 14:56:19 | 修复后 15:13:24 |
> | --- | --- | --- |
> | 显式 `FROM ecommerce.test_connection`（白名单外表） | REJECT `TABLE_NOT_ALLOWED` | REJECT `TABLE_NOT_ALLOWED` |
> | **逗号连接** `…dwd_trade_order_detail a, ecommerce.test_connection b` | **ALLOW + `row_count=3` + 返回真实数据**，血缘只报一张表 | **REJECT `TABLE_NOT_ALLOWED`**（绕过被拦） |
> | 逗号连接（纯常量写法） | REJECT | REJECT |
> | 对照：显式 JOIN 白名单内两表 | ALLOW，血缘正确 | **ALLOW，血缘正确**（**无过度拦截**） |
>
> 修复落地证据：`sqlguard.py` md5 `fab72ca2606abb3d882ad771b2421941`、mtime 15:05（修复落地并重启生效）；观测时间 2026-09-27 15:13:24 CST（只读 `POST`）。
> （依据：`.tmp/audit-agent.md` §A6-1；`docs/thesis/答辩材料/FINAL_AUDIT_REPORT.md` P0-1 与修复建议 F-01/F-02/F-03；`docs/thesis/EVIDENCE_MATRIX.md` §6）

### ④ "性能数字能代表生产吗？做过压测吗？"

> **一句话**：**没有做并发/压力测试**（本机内存不允许，采集时可用仅 3921 MB），现有数字全部是**单机、单请求串行、回环、热缓存**口径，因此**不构成并发容量结论**；其中 Agent 端到端 19187.5 ms（n=2）里 **99.9% 是外部 LLM 服务往返**、数据侧只占 18.3 ms，所以那个数字既不是平台的能力也不是平台的瓶颈；真正有价值的基线结论是"**优化 Agent 延迟只能减少 LLM 往返或换模型，优化 SQL 几乎没有意义**"。
> （依据：`docs/PERFORMANCE.md` §4、§7；`.tmp/audit-perf.md` §（二）Q1）

### ⑤ "这个项目里你做过的最难的一个判断是什么？"

> **一句话**：是**决定不修**流量域那一个 `click_rate` 缺陷。**具体数字**：窗口 `2026-03-21 19:23:00`，实时侧 `view_cnt=2`、`click_cnt=1`，`click_rate` 落库 **0.0000**，而按口径公式 `1/2` 应为 **0.5000**；**离线侧同一窗口算出 0.5000，是对的**——缺陷在实时链路。
>
> **为什么能把责任定在实时侧**：`1/2 = 0.5` 是纯算术，是一条**不需要比较两侧**的第三方判据；再加上差值在小数点后**第一位**而不是末位（**所以不能加容差**），以及"全表 `click_rate` 取值只有 `{0.0000, NULL, 1.0000}`、而满足 `0 < click_cnt < view_cnt` 的分数窗口恰好只有这 1 个"这条旁证。
>
> **为什么把判据改成"每侧按自身计数重算"**：原来的判据是"两侧比率相等"——它既不增加信息（判据列相等时比率在数学上必然相等），又会被两侧除法实现的末位差异误报；换成"**每一侧的比率必须等于用它自己的计数按公式重算的值**"之后，判据**不需要两侧一致就能判定是谁错**，而且"两侧一起错成同一个值"这种缺陷它照样能抓出来。**方向是变严，不是放容差。**
>
> **为什么不修**：修它要改并重部署 Flink 作业，而 `behavior_event` 的全部 20000 条消息**仍在 Kafka 里**（earliest = 0），重部署会把它们**从 earliest 全量重放**、覆盖实时侧全部 **19644 个窗口**——那是实时链路的一次全量重建，**重放成本远大于该缺陷的影响**（它只影响实时侧 `click_rate` 这一个派生列的一行，7 个判据列在 19643 个窗口逐窗口一致）。该取舍**经项目负责人复核后维持不变**；"只改 SQL 不重部署"的方案被明确否决（会造成仓库代码与运行作业不一致，比不改更危险）。
>
> **定性（必须说准）**：这是"**已定位、已用第三方判据定责、判据已改严、证据已落盘**（`realtime_rate_anomaly_windows = 1` 作为一等结论 + 逐窗口标记）、经成本评估后决定不修"的真实缺陷——**不是"未解决"，也不是"环境问题"**；**没有加容差，也没有把证据删掉**。
>
> ★ **注：此项刻意保留在面谈口径中，不写进简历正文**——简历正文只写"建成了什么、验证到什么程度"，缺陷与取舍留给面谈时主动交底（这是诚实性的加分项，不是遗漏）。
>
> （依据：`docs/sprint/SPRINT_5.md` §9.3、§10；`sql/metadata/metrics.md` 第 3.3 节；`docs/DECISIONS.md` ⏳10）

---

## 四、**不要**写进简历的表述（因为无对应证据）

> 这一节是自我审查清单。下面每一条都是"听起来很有力、但会被追问打穿"的类型。

```text
❌ "保障了两条链路的数据一致性"（无限定）
   → 只覆盖对账区间内的逐窗口指标值；不含派生比率列；区间外未比。

❌ "完成 11458 个窗口的独立一致性验证"
   → 其中 5504 个（48.03%）是两侧 GMV 均为 0 的平凡一致，不构成独立证据。

❌ "Iceberg 迁移实现数据无损 / 保证逐行一致"
   → 只验证了表身份、行数、6 个金额列整表合计三个维度；其余 11 个维度未验证。

❌ "实现 RAG 语义检索 / 搭建向量检索链路"
   → 实现是 BM25 词法检索 + 显式同义词表，未使用 embedding 与向量库。

❌ "Agent 无法访问授权范围外的数据 / 没有别的绕过办法"
   → 逗号连接的隐式连接表原来既不进白名单校验也不进血缘（**已修复并线上复验通过**）；
     而这类"把话说满"的全称断言不可穷尽验证，不作此声称。

❌ "性能达到生产要求 / 支持并发访问 / 完成压力测试"
   → 未做并发/压力测试；全部数字为单机、单请求、回环、热缓存口径。

❌ "处理百万级数据 / 支撑大规模数据量"
   → 实测规模在 10³~10⁴ 行量级（用户 1200 / 商品 600 / 订单 6000 / 支付 5406 / 退款 254 / 行为事件 20000）。

❌ "基于真实业务的数据 / 已在生产环境运行"
   → 全部为程序生成的合成数据；单机单副本、接口无鉴权、站点为明文 HTTP、Airflow 使用官方标注仅供开发测试的认证方案。

❌ "参与 / 协助 / 学习了解 XXX"
   → 用词纪律：一律写"设计 / 实现 / 构建 / 验证 / 优化 / 建立"。

❌ "线上问答平均重试 N 次 / 检索准确率 X%"
   → 这两个数没有采集；验收里的失败是脚本主动构造的问法，不是自然流量。
```

---

## 附：本文数字 → 来源索引

| 数字 | 含义 | 来源 |
| --- | --- | --- |
| 11458 / 0 | 交易域对账窗口数 / 不一致数 | `docs/sprint/SPRINT_3.md` §7 |
| 5504 / 5954 | 两侧 GMV 均为 0 的窗口 / 有业务量的窗口 | `docs/thesis/EVIDENCE_MATRIX.md` §3.1 |
| 19643 / 0 / 0 | 流量域窗口数 / 不一致数 / 单边窗口数 | `docs/sprint/SPRINT_5.md` §9.2 |
| 51,890,375.77 | GMV（两侧相等，精确到分） | `docs/sprint/SPRINT_3.md` §7.2 |
| 1200 / 600 / 6000 / 5406 / 254 / 20000 | 用户 / 商品 / 订单 / 支付 / 退款 / 行为事件（**表内行数 = distinct `event_id` 基数 ≠ 链路处理量**：topic latest 合计曾达 580000 = 29 × 20000，Doris 侧 UNIQUE KEY 去重后才是 20000） | `docs/sprint/SPRINT_3.md` §7.1；`AGENTS.md` §15.2、§15.8 |
| 23 / 60-60 / 70-70 | 迁移表数 / 18 张表那次校验 / 23 张表复跑那次校验 | `docs/sprint/SPRINT_5.md` §8.1；`AGENTS.md` §15 |
| 8 / 8 / 11 | Flink sink 作业数 / Routine Load 数 / health-check 项数 | `AGENTS.md` §15.2 |
| 3000 MB / 1.65 GB / 2258 MB | 内存闸门阈值 / 暂停实时链路释放量 / 闸门拒批时可用量 | `AGENTS.md` §15.8；`docs/DECISIONS.md` D11 |
| 49-49 / 71-0-0 / 59-0-0 / 84-0-0 / 61-0-0 / 32-0-0 | 各阶段验收计数 | `AGENTS.md` §15 各节 |
| 353 / 0 / 0 / 3（**修复前时点**）与 381 / 0（**修复后时点**）；`verify-sprint-12.sh` 32/0/0 → 42/0/0 | 全量 pytest 通过 / 失败 / 跳过 / xfail；验收项计数 | `docs/sprint/SPRINT_12.md` §8.3；`docs/thesis/外部审查包.md` §2（两组数字**带时点**引用，不互相替代） |
| 8.5 / 8.3 / 11.7 / 53.8 ms | 只读接口点查 / 聚合 / 关联 / overview（n=7 中位数） | `docs/PERFORMANCE.md` §1.2 |
| 1992.3 s / 约 81 分钟 / 149.8 s | 批量 9 任务合计 / 端到端 / 错峰固定成本 | `docs/PERFORMANCE.md` §3.1、§3.3 |
| 19187.5 ms / 18.3 ms / 99.9% | Agent 端到端中位数（n=2）/ 数据侧耗时 / 外部 LLM 占比 | `docs/PERFORMANCE.md` §4.2；`.tmp/audit-perf.md` |
| 65 篇 | 检索语料篇数（22+15+4+8+16） | `.tmp/audit-agent.md` §C1；`docs/sprint/SPRINT_9.md` §9.2 |
| 26 / 18 | 禁用关键字数 / 白名单表数（16 业务 + 2 元数据） | `.tmp/audit-agent.md` §A3、A4 |
| 6 / 8 / 4 | 图节点数 / 边数 / MCP 只读工具数 | `docs/sprint/SPRINT_8.md` §9.3；`AGENTS.md` §15.12 |
| 512m / 105 MiB / 187 MiB | 监控内存预算 / Prometheus 实测 / Grafana 实测 | `AGENTS.md` §15.11 |
| 20260926 | 生成器默认随机种子 | `data-generator/src/config.py` 第 140 行 |
