# 性能基线（Sprint 12）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 采集脚本：[`scripts/perf/measure-latency.sh`](../scripts/perf/measure-latency.sh)
> 验收脚本：[`scripts/verify-sprint-12.sh`](../scripts/verify-sprint-12.sh)
> 任务书：[`docs/sprint/SPRINT_12.md`](sprint/SPRINT_12.md)

> ## ⏳ 本文档的数字状态：**待补**
>
> **本文档目前不含实测数字。** 这不是遗漏，是一个明确的取舍：
>
> 采集性能基线要求机器处于**配置可用**的状态（服务能重启、能自检）。
> 实施本 Sprint 期间，服务器 `/opt/data-platform/.env` 因一次同步事故被删除
> （完整复盘见 [`SPRINT_12.md`](sprint/SPRINT_12.md) 第 8.2 节），
> 恢复由主控指派的另一路独家负责。在配置恢复之前采集的数字
> **不可比、不可复现**，而且压测会与实时链路抢内存（`AGENTS.md` §15.5）。
>
> **宁可留"待补"，也不写一个没测过的数。** 用旧数字、估算值或
> "看起来合理"的量纲把表格填满，正是本项目反复记录的那类失效
> （`AGENTS.md` §15.8：「成功信号」不可信）。
>
> 恢复后一条命令即可采集，并把输出按下面"四张表"的骨架填入：
>
> ```bash
> bash scripts/perf/measure-latency.sh all | tee /tmp/perf-$(date +%F).txt
> ```

---

## 1. 测量方法与纪律

### 1.1 五条硬纪律

| # | 纪律 | 为什么 |
| --- | --- | --- |
| 1 | **报中位数，不报平均值** | 单次延迟受 JIT / 页缓存 / 邻近查询影响很大；平均值会被一次离群值拉走。同时报 min/max 以暴露离散程度 |
| 2 | **先预热 1 次再测 N 次** | 第一次调用含 Doris 元数据加载与连接建立，那是"冷启动"而不是稳态。两者要分开说 |
| 3 | **同时记录服务端 `elapsed_ms` 与客户端 `wall_ms`** | 两者之差 = HTTP + JSON 编解码开销。只看"接口很快"或"接口很慢"都是片面结论 |
| 4 | **统计用 `python3` 算，不在 bash 里手算** | bash 里写排序与中位数必然在某个边界出错，而出错的表现是"数字看起来正常" |
| 5 | **记录测量现场**：时间、负载、可用内存、当前运行的作业 | 性能数字脱离现场就没有意义。没有现场的基线是不可比的基线 |

### 1.2 必须写明的边界（诚实条款）

1. **路径**：全部经**本机回环**（`127.0.0.1`）测量。
   公网链路（含 §15.7 记录的中间设备改写问题）**不在**这条路径上，
   因此这里的数字**不代表**用户从浏览器访问的体验。
   加公网一跳的结论必须另测，不能从本文推算。
2. **不压测**：每项只重复 7 次（`REPEAT=7`）。本机 16 GB、
   实时链路常驻约 13.7 GB（§15.5），并发压测会直接威胁实时链路 ——
   不拿演示环境换一组好看的数字。
3. **LLM 延迟无法分解**：Agent 的"规划 + 汇总"两段是**两次外部 LLM 调用**，
   其耗时是**差值**（总量 − 检索 − 取数），不是对模型内部推理时间的测量。
   第 4 张表里必须写成"差值"，不得表述为"模型推理耗时"。
4. **批量耗时取自既有日志**：为测一个数字重跑整条流水线要 6~10 分钟且必须
   暂停 Flink（错峰模式）。因此第 3 张表是 **peek**（读既有 Airflow 任务日志），
   不是本轮的实测。

---

## 2. ① 只读接口查询延迟

**测什么**：三种典型 SQL 形态（点查 / 聚合 / 两表关联）+ 六个固定只读接口。
**怎么测**：`POST /data/api/query`（与 Agent 直连路径同一个接口）。

### 2.1 固定 SQL（三次测量用同一组语句，保证可比）

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

### 2.2 结果表

| 项目 | n | 中位数 (ms) | min (ms) | max (ms) | 服务端 elapsed_ms | 行数 |
| --- | --- | --- | --- | --- | --- | --- |
| A 点查（10 行明细） | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| B 聚合（全量 SUM/COUNT） | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| C 两表关联 + GROUP BY | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| `GET /overview` | 7 | ⏳ 待补 | ⏳ | ⏳ | — | — |
| `GET /metrics/trade?limit=60` | 7 | ⏳ 待补 | ⏳ | ⏳ | — | — |
| `GET /funnel?window_limit=1440` | 7 | ⏳ 待补 | ⏳ | ⏳ | — | — |
| `GET /meta/metrics` | 7 | ⏳ 待补 | ⏳ | ⏳ | — | — |
| `GET /meta/tables` | 7 | ⏳ 待补 | ⏳ | ⏳ | — | — |
| `GET /batch/reconcile` | 7 | ⏳ 待补 | ⏳ | ⏳ | — | — |

### 2.3 结论（待补）

（恢复后填写：哪一类最慢、慢在哪一跳、`wall_ms − elapsed_ms` 的开销是否稳定。）

---

## 3. ② 实时侧 vs 离线侧查询延迟

**为什么这是同口径比较**：两侧的表结构由**同一份**
[`sql/metadata/metrics.md`](../sql/metadata/metrics.md) 定义，
差别只在粒度与数据来源（Flink 1 分钟窗口 vs Spark 按天）。

| 项目 | 表 | n | 中位数 (ms) | min | max | 服务端 elapsed_ms | 行数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 实时 窗口聚合 | `ecommerce.ads_realtime_trade_1m` | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| 离线 按天聚合 | `lakehouse_ads.ads_batch_trade_1d` | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| 实时 类目排行 | `ecommerce.ads_realtime_category_1m` | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| 离线 类目排行 | `lakehouse_ads.ads_batch_category_1d` | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |
| 实时 明细扫描 | `ecommerce.dwd_trade_order_detail` | 7 | ⏳ 待补 | ⏳ | ⏳ | ⏳ | ⏳ |

### 3.1 结论（待补）

**必须回答的两个问题**（否则这张表只是数字堆砌）：

1. 离线侧是否**更慢**？如果更慢，是"数据量更大"还是"表格式不同"造成的？
   （离线 ADS 是 Parquet 外部表，Sprint 5 另有 Iceberg 副本 —— 值得对比一次。）
2. 实时侧是否因**持续写入**而变慢？对照方法：记录测量时刻
   `ads_realtime_trade_1m` 的行数，并在两次测量（不同写入量）之间比较。

---

## 4. ③ 批量作业耗时（取自既有 Airflow 日志）

**数据来源**：`/opt/data-platform/airflow/logs/dag_id=offline_lakehouse_pipeline/<run_id>/<task_id>/attempt=*.log`
**方法**：取每个任务日志的首行时间戳与最后一个时间戳之差（**peek，不重跑流水线**）。

| 阶段（DAG 任务） | 对应脚本阶段 | 运行 | 开始 | 结束 | 耗时 (s) |
| --- | --- | --- | --- | --- | --- |
| `pause_realtime` | —（错峰模式：暂停 Flink） | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `ods_extract` | `ods` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `archive` | `archive` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `dwd` | `dwd` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `dws` | `dws` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `ads` | `ads` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `reconcile` | `reconcile` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `load` | `load` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| `restore_realtime` | —（错峰模式：恢复 Flink + 自检） | ⏳ | ⏳ | ⏳ | ⏳ 待补 |
| **端到端合计** | `bash scripts/batch-mode.sh` | ⏳ | ⏳ | ⏳ | ⏳ 待补 |

### 4.1 结论（待补）

必须说明：**暂停 + 恢复**这两段占了多少（它是错峰模式的固定成本，
与数据量无关），以及哪一层是真正的瓶颈。

---

## 5. ④ Agent 单次问答端到端耗时（分解）

**问题**：`最近一周每天的 GMV 是多少？`
**引擎**：LangGraph 图（`AGENT_GRAPH_ENABLED=true`）
**模型**：`deepseek-flash`（记录实际使用的模型名与是否思考模式）

### 5.1 分解方法

```text
总量          POST /ask 的客户端 wall time；响应里的 elapsed_ms 作为对照
取数          execute 节点那一条 executed_sql，单独打 POST /query
检索          retrieve 节点：同一句问题打 GET /api/retrieve?q=...&k=5
规划 + 汇总    = 总量 − 检索 − 取数     ← 两次 LLM 调用的**差值**（见 §1.2 第 3 条）
```

### 5.2 结果表

| 分段 | 对应图节点 | n | 中位数 (ms) | min | max | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| **总量** | 整图 | 3 | ⏳ 待补 | ⏳ | ⏳ | 服务端 `elapsed_ms` = ⏳ |
| 检索 | `retrieve` | 7 | ⏳ 待补 | ⏳ | ⏳ | 与图内同一段逻辑 |
| 取数 | `execute` | 7 | ⏳ 待补 | ⏳ | ⏳ | 与图内同一个接口 |
| **规划 + 汇总（差值）** | `plan` + `summarize` | — | ⏳ 待补 | — | — | **两次 LLM 调用的差值**，不是模型内部耗时 |

其它观测项（从 `/ask` 响应里直接读，不是估计）：

| 项 | 值 |
| --- | --- |
| 引擎 | ⏳ 待补 |
| 模型 | ⏳ 待补 |
| 重试次数 `retries` | ⏳ |
| 执行 SQL 条数 | ⏳ |
| 校验警告/阻塞数 | ⏳ |
| 是否 `truncated` | ⏳ |

### 5.3 结论（待补）

必须说明：**Agent 的耗时主要是 LLM 还是数据？**
这个比例决定了"优化 Agent 延迟"应该往哪边使劲 ——
若 LLM 占九成，优化 SQL 与索引几乎没有意义。

---

## 6. 测量现场记录（每次测量都要填）

| 项 | 值 |
| --- | --- |
| 测量时间 | ⏳ 待补 |
| 内核负载（load average） | ⏳ |
| 可用内存（`MemAvailable`） | ⏳ |
| 同时运行的作业（Spark / 批处理） | ⏳ |
| 实时链路状态（Flink 8 作业是否 RUNNING） | ⏳ |
| 数据量快照（`ads_realtime_trade_1m` 行数等） | ⏳ |

> **为什么这张表不能省**：同一台机器上，一次 Spark 作业就能把只读接口的
> 延迟改变数倍。没有现场的"中位数 12 ms"和"中位数 40 ms"看起来是性能差异，
> 实际可能只是"测的时候有没有别的东西在跑"。

---

## 7. 复现方式

```bash
# 一次性采集全部四类（建议先确认没有批处理在跑）
bash scripts/perf/measure-latency.sh all | tee /tmp/perf-$(date +%F).txt

# 分节采集
bash scripts/perf/measure-latency.sh api                 # ① 只读接口延迟
bash scripts/perf/measure-latency.sh realtime-offline    # ② 实时 vs 离线
bash scripts/perf/measure-latency.sh batch               # ③ 批量作业耗时（读日志）
bash scripts/perf/measure-latency.sh agent               # ④ Agent 端到端

# 调整重复次数（默认 7）
REPEAT=15 bash scripts/perf/measure-latency.sh api
```

采集脚本自带**前置闸门**：`.env` 缺失或可用内存 < 800 MB 时**直接拒跑**
（`AGENTS.md` §15.5 内存铁律）。这是刻意的 ——
在一个配置不可用或内存告急的机器上测出来的数字，写进文档只会误导后来者。

---

## 8. 后续（本 Sprint 不做，但应记录）

| 事项 | 为什么不在本 Sprint |
| --- | --- |
| 并发/压力测试 | 本机内存不允许（实时链路常驻约 13.7 GB）。要做必须另开一台 |
| 公网链路延迟（含 nginx 一跳） | 与本文的回环口径不同，需单独测并说明 §15.7 的中间设备问题 |
| Iceberg vs Parquet 查询延迟对比 | Sprint 5 已建 Iceberg 副本，这是一个**高价值**的对比实验，但需要 Spark 会话，属独立任务 |
| 性能**优化** | 本 Sprint 的交付是基线。没有基线就谈优化，等于把"感觉快了"当结论 |

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-27 | V1.0 | 建立性能基线文档骨架：测量方法、纪律、四张结果表、复现方式。**数字待补**（服务器配置事故恢复中，见 SPRINT_12.md 8.2） |
