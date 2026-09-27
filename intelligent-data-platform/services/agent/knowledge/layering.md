# 数仓分层与两条链路的粒度差异

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 用途：**Sprint 9 检索语料**（随 Agent 代码分发，供 Agent 在写 SQL 前先查）
> 权威性：分层职责与 `AGENTS.md` §5.2 一致；指标口径以
> [`sql/metadata/metrics.md`](../../../sql/metadata/metrics.md) 为唯一权威。
>
> 为什么这份说明要单独成文并进语料：
> 选错表是数据问答里最常见、也最难发现的错误 ——
> 明明「最近一周每天的 GMV」该查**离线天表**，却去查了**实时分钟表**，
> SQL 能跑通、数字看起来也合理，但口径和粒度都不对。
> 表结构接口只能回答"这张表有哪些列"，回答不了"该用哪张表"。

## ODS 贴源层

结构与源系统一致，只做落地不做加工。

- 交易域：`ods_user` / `ods_product` / `ods_orders` / `ods_payment` / `ods_refund`
- 流量域：`ods_behavior_event`（Kafka `behavior_event` 归档）
- 存放于湖仓（Sprint 5 起为 `iceberg.lakehouse_iceberg`），**服务层不直接查**

## DWD 明细层

清洗、去重、维度补全；**一行一个业务事件**，是可回溯的事实层。

| 表 | 粒度 | 说明 |
| --- | --- | --- |
| `dwd_trade_order_detail` | 一行一单 | 订单创建事件明细 |
| `dwd_trade_payment_detail` | 一行一笔支付 | 支付事件明细 |
| `dwd_trade_refund_detail` | 一行一笔退款 | 退款事件明细 |
| `dwd_traffic_behavior_detail` | 一行一个行为事件 | 浏览/点击/加购/收藏/购买 |

**去重类指标（UV、下单用户数）必须回到明细层用 `COUNT(DISTINCT user_id)`**，
不能对分钟窗口的 `uv` 求和 —— 那样得到的是"人次"，不是"人数"。

## DWS 汇总层

按主题轻度聚合，便于上卷。

| 表 | 粒度 |
| --- | --- |
| `dws_traffic_overview_1m` | 分钟（实时链路） |
| `dws_traffic_overview_1d` | 天（离线链路） |
| `dws_traffic_funnel_1d` | 天（行为漏斗阶梯 + 各步去重人数） |

## ADS 应用层

直接面向指标与报表，也是**服务层唯一可查的一层**。

| 表 | 库 | 粒度 | 链路 |
| --- | --- | --- | --- |
| `ads_realtime_trade_1m` | `ecommerce` | 1 分钟窗口 | 实时（Flink） |
| `ads_realtime_traffic_1m` | `ecommerce` | 1 分钟窗口 | 实时（Flink） |
| `ads_realtime_category_1m` | `ecommerce` | 1 分钟窗口 | 实时（Flink） |
| `ads_batch_trade_1m` | `lakehouse_ads` | 1 分钟窗口 | 离线（Spark） |
| `ads_batch_trade_1d` | `lakehouse_ads` | 天 | 离线（Spark） |
| `ads_batch_category_1m` | `lakehouse_ads` | 1 分钟窗口 | 离线（Spark） |
| `ads_batch_category_1d` | `lakehouse_ads` | 天 | 离线（Spark） |
| `ads_reconcile_trade_1m` | `lakehouse_ads` | 1 分钟窗口 | 批流逐窗口对账结果 |
| `ads_reconcile_summary` | `lakehouse_ads` | 一批一行 | 对账汇总结论 |

## 两条链路的差异（选表判据）

| 维度 | 实时链路（`ecommerce`） | 离线链路（`lakehouse_ads`） |
| --- | --- | --- |
| 计算引擎 | Flink（事件时间 1 分钟 TUMBLE） | Spark（分层计算后装载进 Doris） |
| 新鲜度 | 秒级 | 按天/按批，需等批处理跑完 |
| 粒度 | 分钟窗口 | 分钟窗口 + 天 |
| 口径 | 与离线**逐字相同**（`sql/metadata/metrics.md`） | 同左 |
| 可信度判据 | 与离线逐窗口对账，不一致窗口数应为 0 | 同左 |

**选表经验规则**：

1. 问「最近 N 天每天…」→ 离线天表 `ads_batch_*_1d`（实时链路没有天粒度表）；
2. 问「刚刚 / 实时 / 最近几分钟」→ 实时分钟表 `ads_realtime_*_1m`；
3. 问「这个数准不准 / 实时和离线一致吗」→ 对账结论 `ads_reconcile_summary`；
4. 问去重人数（UV、下单用户数）的全量值 → DWD 明细层 `COUNT(DISTINCT user_id)`。

## 批流对账：数据准不准怎么回答

问「数据准不准」「实时和离线一致吗」「这个数可不可信」时，
**不要凭感觉下判断**，去看对账结论：

| 表 | 粒度 | 说明 |
| --- | --- | --- |
| `ads_reconcile_trade_1m` | 1 分钟窗口 | 逐窗口比对实时与离线的交易指标，标记不一致窗口 |
| `ads_reconcile_summary` | 一批一行 | 对账区间、窗口数、不一致窗口数、差异最大的窗口 |

判据：**不一致窗口数 = 0**，表示在**该对账区间内、按"缺失侧补 0"的统一口径**逐窗口比对时，
两条链路**纳入判据的指标**未发现差异 —— **不能**扩大成"两侧数据完全一致"、
"所有指标一致"或"全量数据一致"。
对账由 Spark 作业完成（`infrastructure/spark/jobs/reconcile_batch_realtime.py`），
服务层只读结论（`GET /batch/reconcile`）。

对账的**范围**必须说清楚，否则会给人"全都对过了"的错觉：

- 参与对账的是**交易域**的 **7 个可加指标 + 1 个去重指标**（`order_user_cnt` 是 `COUNT(DISTINCT)`，
  不能当可加指标），与流量域的 **7 个判据列**（`uv` + `pv` + **5 个行为计数**：
  view / click / cart / favorite / buy）；
- **比率列（`click_rate` / `cart_rate` / `buy_rate`）是派生指标，只作诊断性异常登记、不参与判据**；
  流量域实时侧确有 **1 个窗口**的 `click_rate` 与它自己的计数矛盾（`view_cnt=2`/`click_cnt=1`，
  实时记 0.0000、离线算 0.5000）—— 那是**已定位的真实缺陷**（缺陷在实时链路），
  被问到时应如实说明，不要回避、也不要因此说"对账不成立"；
- **去重类指标（uv、下单用户数）不能跨窗口相加**，
  因此它们不在"合计值对账"的范围内 —— 被问到时要如实说明这一点，
  不要用"对账全过"去覆盖它。

## 流量域的两个粒度陷阱

1. **`uv` 不可跨窗口相加**：逐窗口去重值的和是"人次"，不是"人数"。
   本项目实测：19644 个窗口的 `uv` 之和 = 19999，而全部 20000 个事件的去重用户数 = 1199，
   两者相差 16 倍但**都正确** —— 它们是两个不同的定义。
2. **比率不可逐窗口求平均**：`click_rate` 等派生指标必须用
   "合计后的分子 / 合计后的分母"重算。

## 维表

`dim_product` / `dim_user`：商品与用户维度，用于补全维度信息。
类目维度**不做维表 join**：`order_event` 在生成时已冗余写入 `category_name`。
