# 性能基线（Sprint 12）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 采集脚本：[`scripts/perf/measure-latency.sh`](../scripts/perf/measure-latency.sh)
> 验收脚本：[`scripts/verify-sprint-12.sh`](../scripts/verify-sprint-12.sh)
> 任务书：[`docs/sprint/SPRINT_12.md`](sprint/SPRINT_12.md)
> 采集主机：腾讯云 **lavm-txyjj7xzr7**（Ubuntu 24.04.2 LTS / 4 核 / 16 GB）
> 采集时间：**2026-09-27 09:11 ~ 09:15（+0800）**

---

## 0. 采样方式（先说清口径，再给数字）

| 项 | 取值 | 说明 |
| --- | --- | --- |
| 采样次数 | **只读接口类每项 7 次**（另有 1 次**预热**不计入）；**Agent `/ask` 实际 n=2** | `REPEAT=7` 只对只读接口一节有效；预热用于排除"首次调用含 Doris 元数据加载与连接建立"的冷启动偏差。⚠️ **`/ask` 一节：脚本循环写死 2 次、且没有预热调用**，原始采集日志 `/tmp/perf-bc.log` 记的也是 `{"n": 2, ...}` —— 早期本文件写"3 次（含 1 次预热）"与代码/日志不符，此处按日志订正 |
| 统计量 | **中位数**（同时给 min/max） | 不报平均值：单次延迟受 JIT / 页缓存 / 邻近查询影响大，平均值会被离群值拉走 |
| 双端耗时 | 客户端 `wall_ms`（curl `%{time_total}`）+ 服务端 `elapsed_ms` | 两者之差 = HTTP + JSON 编解码开销。只报一端会得出片面结论 |
| 采集路径 | 全部经**本机回环**（`127.0.0.1`） | **不含公网链路**。§15.7 记录的中间设备改写问题不在该路径上，因此本表**不代表**用户从浏览器访问的体验 |
| **并发** | **无** | 采集脚本**没有任何并发原语**（全是顺序 `for` + 单次 `curl`，无 `-parallel` / `xargs -P` / 后台 `&` / 连接数参数）。**本 Sprint 不做并发与压力测试**（见第 7 节），因此全部数字**不构成生产高并发证明** |
| **缓存状态** | 只读接口为**热缓存** | 每项 1 次预热 + `REPEAT=7` = **同一条 SQL 连打 8 次**，目标表只有万行级 → 这组数字**结构上不可能包含冷缓存代价**，不能外推为冷启动延迟。OS 页缓存 / Doris BE 页缓存的影响**未单独测量**（源码只把"冷启动"限定为"连接与元数据"） |
| 批量作业耗时 | 取自 **Airflow 元数据库 `task_instance`** 的既有运行记录（`SELECT ... ROUND(duration,1)`） | **不是任务日志**（日志分支只在目录存在时才额外作参考）。**没有**为采这个数字重跑流水线（内存铁律 §15.5）。口径是 **9 个任务各自 `duration` 的算术和**，故**不等于墙钟端到端**，且**含 1 次重试 attempt**（该 run 的 `ads_layers` 为 `try_number=2`） |
| Agent 问答 | **n=2**（**无预热**） | 每次真实调用 DeepSeek，单次约 18~20 s；跑更多次只是烧 token。该次测量 `retries=1`，即 19.2 s 里**含一次反思重试的代价** |

### 0.1 采集时的机器状态（测量现场）

```text
时间          2026-09-27 09:11:32 ~ 09:15 (+0800)
load average  1.46 / 3.06 / 3.85（1/5/15 分钟）
内存          总 15989 MB / 已用 12067 MB / **可用 3921 MB**
Spark 作业    **0 个**（SparkSubmit 计数 = 0，采集前已确认）
实时链路      8 个 Flink sink 作业 RUNNING（常驻约 12 GB）
数据量快照    ads_realtime_trade_1m = 11459 行；dwd_trade_order_detail = 6000 行；
              ads_batch_trade_1d = 626 行
```

> **为什么必须记现场**：同一台机器上，一次 Spark 作业就能把只读接口延迟改变数倍。
> 没有现场的"中位数 8 ms"和"中位数 40 ms"看起来是性能差异，
> 实际可能只是"测的时候有没有别的东西在跑"。

---

## 1. ① 只读接口查询延迟

**怎么测**：`POST /data/api/query`（与 Agent 直连路径**同一个接口**）。

### 1.1 三种典型 SQL 形态

```sql
-- A 点查：10 行明细
SELECT window_start, gmv FROM ecommerce.ads_realtime_trade_1m
ORDER BY window_start DESC LIMIT 10;

-- B 聚合：全量 SUM/COUNT
SELECT COUNT(*) AS windows, SUM(gmv) AS total_gmv, SUM(order_cnt) AS orders
FROM ecommerce.ads_realtime_trade_1m;

-- C 关联：两表 JOIN + GROUP BY
SELECT c.category_name, SUM(o.amount) AS gmv
FROM ecommerce.dwd_trade_order_detail o
JOIN ecommerce.dim_product c ON o.product_id = c.product_id
GROUP BY c.category_name ORDER BY gmv DESC LIMIT 10;
```

### 1.2 结果表（n=7，中位数）

| 项目 | 中位数 (ms) | min (ms) | max (ms) | 服务端 elapsed_ms | 行数 | HTTP |
| --- | --- | --- | --- | --- | --- | --- |
| A 点查（10 行明细） | **8.5** | 7.8 | 16.7 | 6 | 10 | 200 |
| B 聚合（全量 SUM/COUNT） | **8.3** | 7.7 | 10.1 | 5 | 1 | 200 |
| C 两表关联 + GROUP BY | **11.7** ⚠️ | 7.8 | 20.9 | 5 | **0 ⚠️** | 200 |
| `GET /overview` | **53.8** | 46.0 | 72.7 | — | — | 200 |
| `GET /metrics/trade?limit=60` | **11.2** | 9.6 | 17.8 | — | — | 200 |
| `GET /funnel?window_limit=1440` | **8.4** | 8.1 | 10.2 | — | — | 200 |
| `GET /meta/metrics`（口径字典） | **1.7** | 1.6 | 2.9 | — | — | 200 |
| `GET /meta/tables`（表结构） | **43.2** | 36.8 | 57.9 | — | — | 200 |
| `GET /batch/reconcile`（批流对账） | **21.4** | 19.9 | 22.5 | — | — | 200 |

### 1.3 结论

1. **SQL 层很快**：点查 / 聚合 / 关联三种形态都在 **8~12 ms**（客户端实测），
   服务端自述 `elapsed_ms` 只有 **4~6 ms** —— 说明 Doris 侧命中得很快，
   **约 3~6 ms 的差值是 HTTP + JSON 编解码**，这部分是常数开销，不随查询变化。

   > ⚠️ **C 行（11.7 ms）必须加注，否则会被误引**：采集时 `dim_product` 在 Doris 里是 **0 行**
   > （§5 记录的缺陷），该次查询 `row_count = 0` —— 也就是说**这一格测到的是"JOIN 一张空维表"的代价，
   > 而不是两表关联真正发生的代价**。该缺陷已于 2026-09-27 修复（提交 `b195d19`）；
   > 按同一口径重跑，同样的查询返回 **10 行**、中位数约 **7.4 ms**。
   > 上表保留原值 11.7 ms 是**为了不改历史观察值**，但它**不是**"两表关联"的真实延迟量级；
   > 引用时应写"11.7 ms（采集时维表为空；修复后同口径约 7.4 ms）"。
2. **`GET /overview` 是最慢的接口（53.8 ms，约为最简接口的 30 倍）**。
   原因是它一次拼了 **6 条查询**（交易 KPI + 流量 KPI + 去重 UV + 窗口统计 + 类目排行），
   而 `GET /meta/metrics` 只解析本地文档（1.7 ms）。
   → **优化方向明确**：若将来要压总览页延迟，应把 6 条查询合并/并行，
   而不是去优化 SQL 本身（SQL 已经是 5 ms 级）。
3. **`GET /meta/tables` = 43.2 ms**：它查 `information_schema` 拿全部授权表的列
   （16 张表 × 多列），属于预期的元数据开销；Agent 侧对它按 TTL 缓存
   （`TABLE_TTL_SECONDS`），因此不是每次问答都付这个成本。
4. ⚠️ **C 查询返回 0 行 —— 这是一个真实的数据缺陷，不是性能问题**（见 §5）。

---

## 2. ② 实时侧 vs 离线侧查询延迟

**为什么这是同口径比较**：两侧的表结构由**同一份**
[`sql/metadata/metrics.md`](../sql/metadata/metrics.md) 定义，
差别只在粒度与数据来源（Flink 1 分钟窗口 vs Spark 按天）。

| 项目 | 表 | 中位数 (ms) | min | max | 服务端 elapsed_ms | 行数 |
| --- | --- | --- | --- | --- | --- | --- |
| 实时 窗口聚合 | `ecommerce.ads_realtime_trade_1m` | **7.1** | 6.7 | 8.3 | 4 | 1 |
| 离线 按天聚合 | `lakehouse_ads.ads_batch_trade_1d` | **7.1** | 6.7 | 7.5 | 4 | 1 |
| 实时 类目排行 | `ecommerce.ads_realtime_category_1m` | **7.6** | 6.9 | 8.5 | 5 | 10 |
| 离线 类目排行 | `lakehouse_ads.ads_batch_category_1d` | **8.0** | 7.0 | 10.6 | 5 | 10 |
| 实时 明细扫描 | `ecommerce.dwd_trade_order_detail` | **9.4** | 9.1 | 10.5 | 7 | 200 |

### 2.1 结论

**两侧没有可测量的差异**（同口径聚合都是 **7.1 ms**，类目排行 7.6 vs 8.0 ms，差 0.4 ms
—— 小于 min/max 的波动范围，不构成结论）。

这**符合预期**，原因是两侧的数据量在这两个聚合上同量级：

```text
实时 ads_realtime_trade_1m      11459 行（1 分钟窗口）→ 全表 SUM/COUNT 扫 11459 行
离线 ads_batch_trade_1d           626 行（按天）        → 全表 SUM/COUNT 扫   626 行
```

两者都在 Doris 的**单 BE 内存/本地盘**上，行数量级（10³~10⁴）远没到需要读放大的程度，
因此"离线更慢/更快"这类结论在这个数据规模下**测不出来** ——
如实记录为"无显著差异"，不编造趋势。

> 一个**未做**但高价值的对比：离线 ADS 目前是 **Doris 内表**，
> 而 Sprint 5 另建了 **Iceberg** 副本。Iceberg 与 Parquet 的查询延迟对比
> 需要 Spark 会话（会与实时链路抢内存），属独立任务，见 §6。

---

## 3. ③ 批量作业耗时（取自既有 Airflow 运行记录）

**数据来源**：Airflow 元数据库 `task_instance` 表的既有运行记录
（`duration` 字段）。**没有为此重跑任何作业**（内存铁律 §15.5）。

### 3.1 一次完整的成功运行：`scheduled__2026-09-26T19:00:00+00:00`

| # | 任务 | 对应脚本阶段 | 开始 | 结束 | 耗时 (s) |
| --- | --- | --- | --- | --- | --- |
| 1 | `pause_realtime` | —（错峰：暂停 Flink 栈） | 03:00:00 | 03:00:18 | **18.5** |
| 2 | `ods_extract` | `ods` | 03:00:19 | 03:01:20 | **61.6** |
| 3 | `archive_behavior` | `archive` | 03:01:21 | 03:07:25 | **363.3** |
| 4 | `dwd_layers` | `dwd` | 03:07:26 | 03:12:29 | **303.5** |
| 5 | `dws_layers` | `dws` | 03:12:30 | 03:19:10 | **399.7** |
| 6 | `ads_layers` | `ads` | 04:07:11 | 04:14:38 | **446.8** |
| 7 | `reconcile` | `reconcile` | 04:14:39 | 04:16:58 | **138.8** |
| 8 | `load_to_doris` | `load` | 04:16:59 | 04:19:07 | **128.8** |
| 9 | `restore_realtime` | —（错峰：恢复 Flink + 自检） | 04:19:08 | 04:21:19 | **131.3** |
| | **9 个任务耗时合计** | | | | **1992.3** |

**端到端**（首任务开始 → 末任务结束）：`03:00:00 → 04:21:19` = **约 81 分钟**。

> 其中 `ads_layers` 的**开始时间**（04:07:11）与 `dws_layers` 的结束时间（03:19:10）
> 之间有 **48 分钟的间隔** —— 那是当天的重试/等待窗口（该 run 的 `ads_layers`
> 有多次 attempt），因此"9 个任务耗时合计 1992 s"与"端到端 81 分钟"**不相等**。
> 两个数都给出来，并把差异说明白，而不是只报小的那个。

### 3.2 对照：一次人工触发的运行 `manual__2026-09-26T17:05:38+00:00`

| 任务 | 耗时 (s) | 备注 |
| --- | --- | --- |
| `pause_realtime` | 10.8 | |
| `ods_extract` | 38.8 | |
| `archive_behavior` | **0.6** | ⚠️ 归档**几乎瞬间返回** —— 与 3.1 的 363.3 s 相差 600 倍 |
| `dwd_layers` | 212.3 | |
| `dws_layers` | 340.0 | |
| `ads_layers` | 437.8 | |
| `reconcile` | 132.2 | |
| `load_to_doris` | 124.9 | |
| `restore_realtime` | 194.3 | |
| **合计** | **1491.7** | 端到端约 25 分钟 |

> ⚠️ `archive_behavior` 的 0.6 s 值得警惕：这正是 Sprint 4 记录过的
> **"归档阶段静默空转"** 的特征（阶段通过、耗时近零、但没真干活）。
> 该 run 的所有任务状态都是 `success`，所以**只能靠耗时异常发现**——
> 这条记录保留在此，供后续复核（本 Sprint 不改动流水线文件）。

### 3.3 结论

1. **固定成本**：`pause_realtime` + `restore_realtime` = **18.5 + 131.3 = 149.8 s**，
   这是错峰模式的固有开销（暂停/恢复 Flink 栈并自检），**与数据量无关**。
2. **真正的耗时大头是三个分层作业**：`archive`(363) + `dwd`(304) + `dws`(400) + `ads`(447)
   = **1514 s**，占 9 个任务耗时合计的 **76%**。
   这是 Spark 作业的启动与 shuffle 成本（本机单 worker、driver 768m）。
3. `load_to_doris` 只要 **128.8 s**（S3() TVF 直读 Parquet），`reconcile` **138.8 s**。
4. **本表的价值**：它是"要不要优化批处理"的判据 ——
   若要压缩端到端时间，应当从**三个分层作业的 Spark 启动与分区数**入手，
   而不是去优化装载或对账（那两项加起来不到 270 s）。

---

## 4. ④ Agent 单次问答端到端耗时（分解）

**问题**：`最近一周每天的 GMV 是多少？`
**引擎**：LangGraph 图（`AGENT_GRAPH_ENABLED=true`）
**模型**：`deepseek-flash`（`LLM_THINKING=false`，非思考模式）

### 4.1 分解方法（每一项都在可核对的接口上单独量到）

```text
总量          POST /ask 的客户端实测 wall time；响应里的 elapsed_ms 作为对照
取数          execute 节点实际执行的 SQL，单独打 POST /query
检索          retrieve 节点：同一句问题打 GET /api/retrieve?q=...&k=5
规划 + 汇总    = 总量 − 检索 − 取数     ← 两次 LLM 调用的**差值**
```

### 4.2 结果表

| 分段 | 对应图节点 | n | 中位数 (ms) | min (ms) | max (ms) |
| --- | --- | --- | --- | --- | --- |
| **总量** | 整图 | **2** ⚠️ | **19187.5** | 18175.8 | 20199.2 |
| 服务端自述总量 | 整图 | — | **18173** | — | — |
| 检索 | `retrieve` | 7 | **2.9** | 2.6 | 3.1 |
| 取数 | `execute` | 7 | **15.4** | 14.3 | 18.3 |
| **规划 + 汇总（差值）** | `plan` + `summarize` | — | **≈ 19169** | — | — |

> ⚠️ **"总量"这一行的 n 是 2，不是 3，而且没有预热。**
> 采集脚本对 `/ask` **没有预热调用**，循环次数写死为 2（`REPEAT` 对该节无效）；
> 原始采集日志 `/tmp/perf-bc.log` 记的也是 `{"n": 2, ...}`。
> 本文件早期写"3 次（含 1 次预热）"与代码/日志不符，此处按**原始日志**订正为 **n=2**。
> 中位数 19187.5 ms 不受影响（去掉一个样本不改变两个样本的中位数取法），
> 但**样本量只有 2**、且每次真实调用外部 LLM 的耗时在 18~20 s 间波动，
> 因此该数字只能作**量级参考**，不是稳定的性能指标。

> **差值怎么算的**：`19187.5 − 2.9 − 15.4 = 19169.2 ms`。
> **这不是对模型内部推理时间的测量**，而是"总量减去我们能单独量的两段"。
> 它包含两次 DeepSeek API 调用的网络往返 + 服务端推理 + token 生成，
> 无法在我们的机器上进一步分解。**不得**把它表述为"模型推理耗时"。

其它观测项（从 `/ask` 响应里直接读，不是估计）：

| 项 | 值 |
| --- | --- |
| 引擎 | `langgraph` |
| 模型 | `deepseek-flash` |
| 重试次数 `retries` | **1** |
| 执行 SQL 条数 | 2 |
| 该次首个 `executed_sql` | `SELECT table_name, column_name, ... FROM information_schema.columns WHERE table_schema='lakehouse_ads' AND table_name IN (...)` |
| 是否 `truncated` | 否 |

### 4.3 结论

1. **Agent 的耗时几乎全部来自 LLM：约 19.17 s / 19.19 s ≈ 99.9%。**
   数据侧（检索 2.9 ms + 取数 15.4 ms = **18.3 ms**）只占 **0.1%**。
2. → **优化 Agent 延迟的方向只有一个：减少 LLM 往返次数或换更快的模型。**
   优化 SQL 与索引在 Agent 端到端延迟上**几乎没有意义**（这是本基线最有价值的结论）。
3. **两次 LLM 调用 ≈ 19.2 s，即单次约 9.6 s**（规划一次 + 汇总一次）。
   本次还发生了 **1 次反思重试**，意味着实际可能调用了更多次 ——
   `retries=1` 说明图**多走了一轮 `reflect → plan`**，
   而这正是延迟的主要放大器：**每一次重试 = 多一次规划 LLM 调用**。
4. 首个 `executed_sql` 是 `information_schema.columns` 的元数据探测 ——
   说明模型在这一问上**先探表结构再取数**（Sprint 9 的 9.6 节记录过同样的行为）。
   这是 2 条 SQL 中的第 1 条。

---

## 5. ⚠️ 采集过程中发现的一处**真实数据缺陷**（不是性能问题）

**现象**：§1 的 C 查询（`dwd_trade_order_detail` JOIN `dim_product`）返回 **0 行**，
而 `dwd_trade_order_detail` 有 6000 行。

**定位**（用只读查询逐步收敛）：

```text
SELECT COUNT(*) FROM ecommerce.dwd_trade_order_detail   →  6000   ✅
SELECT COUNT(*) FROM ecommerce.dim_product              →  **0**  ⚠️
SELECT COUNT(*) FROM ecommerce.dim_user                 →  **0**  ⚠️
MySQL 侧：product = 600，user = 1200                    →  事实源有数据
两表 LEFT JOIN 抽样：o.product_id 有值，c.product_id 全为 null
```

**影响**：

1. `dim_product` / `dim_user` 两张维表在 Doris 里是**空的**（MySQL 里有 600 / 1200 行）；
2. 任何依赖维表 JOIN 的查询都会**静默返回 0 行**（HTTP 200 + `row_count=0`）——
   这正是本项目反复强调的"空集合上的断言必须加非空守卫"的现实版本；
3. **Agent 侧受影响**：`services/agent/app/agent.py` 的系统提示词「数据地图」
   明确写有 `dim_product`（类目维表），因此模型会按提示词去 JOIN 它，
   拿到 0 行后只能如实回答"查不到" —— 表现为"Agent 答不出类目问题"，
   而根因在**数据装载**，不在 Agent。

**本 Sprint 的处置**：**只记录，不修复**。理由是
`dim_product` / `dim_user` 的装载属离线链路（`load_to_doris` 阶段与 Spark 作业），
那些文件在本次派工里是**禁止碰**的；且"维表该由谁装载、装载频率如何"
属数据链路设计，需要主控决策。

> 📌 **修复方案见 [`docs/DECISIONS.md`](DECISIONS.md) ⏳11** ——
> 已由主控登记为待项目负责人决策的事项（涉及 `load-batch-to-doris.sh`、
> `sql/doris/**` 与 Agent 的「数据地图」，跨 Sprint 边界，不在本 Sprint 处理）。

**为什么这条值得单独写在这里（而不只是记进缺陷清单）**：

> **静默返回 0 行比报错更危险。** 这条查询的 HTTP 状态是 **200**、
> `row_count` 是 **0**、响应信封完整、`source.tables` 也正确 ——
> 从任何一个"接口是否健康"的角度看它都是正常的。
> 只有把结果**与预期行数对照**（维表 JOIN 不该是 0 行）才能发现问题。
>
> 这与本项目反复记录的失效模式是同一类（§15.8「成功信号不可信」、
> Sprint 4 的"自检在空表上空洞通过"）：**"调用成功"不等于"结果可用"**。
> 也正因如此，本 Sprint 的性能采集脚本在比较两条路径一致性之前，
> 先加了**非空守卫**（"两边都为空"也是完全一致，但毫无意义）。

**建议主控**：把它作为一条独立缺陷处理 —— 先确认 `load-batch-to-doris.sh`
是否本来就该带维表（对照 `sql/doris/*` 的建表与 `run-batch-pipeline.sh` 的 `load` 阶段），
再决定是补装载还是从 Agent 的「数据地图」里移除这两张表。

---

## 6. 复现方式

```bash
# 一次性采集全部四类（务必先确认没有批处理在跑）
bash scripts/perf/measure-latency.sh all | tee /tmp/perf-$(date +%F).txt

# 分节采集
bash scripts/perf/measure-latency.sh api                 # ① 只读接口延迟
bash scripts/perf/measure-latency.sh realtime-offline    # ② 实时 vs 离线
bash scripts/perf/measure-latency.sh batch               # ③ 批量作业耗时（读 Airflow 元数据库 task_instance）
bash scripts/perf/measure-latency.sh agent               # ④ Agent 端到端

# 调整重复次数（默认 7）
REPEAT=15 bash scripts/perf/measure-latency.sh api
```

采集脚本自带**前置闸门**：`.env` 缺失或可用内存 < 800 MB 时**直接拒跑**
（§15.5 内存铁律）。这是刻意的 ——
在一个配置不可用或内存告急的机器上测出来的数字，写进文档只会误导后来者。

> ⚠️ **第 3 类基线的一个口径订正（早期写错了，此处按代码订正）**：
> 早期版本说"`measure-latency.sh batch` **读的是**任务日志"，**这与代码不符** ——
> **脚本读的是 Airflow 元数据库**（`docker exec mysql ... airflow -e "SELECT ... ROUND(duration,1) FROM task_instance ..."`）；
> 日志分支只是**另一条参考路径**，仅当 `/opt/data-platform/airflow/logs/dag_id=offline_lakehouse_pipeline`
> 存在时才额外列出首尾时间戳。该日志目录在本轮 `.env` 事故中随机器状态一起丢失
> （被 `.gitignore` 覆盖，全量同步不会恢复），但**这不影响 §3 的数字** ——
> 它本来就取自元数据库（那是更权威的来源）。
> **`SPRINT_12.md` 已记录"来源说明已修正"**；本节此前那两行过期说明一并订正，不再是待办。

---

## 7. 后续（本 Sprint 不做，但应记录）

| 事项 | 为什么不在本 Sprint |
| --- | --- |
| 并发/压力测试 | 本机内存不允许（实时链路常驻约 12~13.7 GB，采集时可用仅 3.9 GB）。要做必须另开一台 |
| 公网链路延迟（含 nginx 一跳） | 与本文的回环口径不同，需单独测并说明 §15.7 的中间设备问题 |
| **Iceberg vs Doris 内表 查询延迟对比** | 高价值（Sprint 5 已建 Iceberg 副本），但需要 Spark 会话，会与实时链路抢内存 |
| 性能**优化** | 本 Sprint 的交付是**基线**。没有基线就谈优化，等于把"感觉快了"当结论 |
| `dim_product` / `dim_user` 空表的修复 | 属离线链路（禁止碰的文件），见 §5。**后续状态**：已于 2026-09-27 修复并服务器验证（提交 `b195d19`），§1.3 与 §5 已按"不改历史观察值、只追加后续状态"处理 |

> ⚠️ **本节全部数字的共同边界（必须与任何一处数字一起引用）**：
> 它们是**单机实验环境**（腾讯云 4 核 / 16 GB）、**单请求串行**、**本机回环**、
> **热缓存**口径下测得的；`/ask` 仅 **n=2** 且无预热。
> 因为**未做任何并发 / 压力测试**（本表的第 1 行），
> **这些数字不构成对生产环境高并发能力的证明，也不能作为容量规划依据**。
> 可以主张的只有：在单机、单请求、预热后、回环路径下，只读接口点查/聚合/关联的
> 中位数为 8.5 / 8.3 / 11.7 ms（n=7）。

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-27 | V1.0 | 建立性能基线文档骨架（方法、纪律、四张结果表、复现方式）。数字待补 |
| 2026-09-27 | **V1.1** | **回填四类基线的实测数字**（采集于 09:11~09:15，n=7 中位数，现场：load 1.46 / 可用内存 3921 MB / 无 Spark 作业）；新增 §5 采集过程中发现的维表空数据缺陷；§3 改从 Airflow 元数据库取值并说明日志目录丢失 |
