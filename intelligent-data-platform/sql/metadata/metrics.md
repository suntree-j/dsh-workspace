# 指标口径字典（Metrics Dictionary）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 版本：V1.2（Sprint 5 流量域分层与批流对账后同步）
> 状态：**唯一权威口径定义**
>
> 规则：**同一指标在全项目只能有一个定义。**
> 实时链路（Flink）与离线链路（Sprint 3 起 Spark）必须产生同名同口径的指标；
> Agent（Sprint 7+）回答问题时必须引用本文件的定义。
> 任何口径变更都必须先改本文件，再改代码。

---

## 1. 通用约定

| 约定 | 取值 | 说明 |
| --- | --- | --- |
| 时间语义 | **事件时间（`event_time`）** | 不用处理时间，保证乱序/重放结果一致 |
| 窗口类型 | 滚动窗口（TUMBLE） | 长度 1 分钟，无重叠 |
| 时区 | `Asia/Shanghai`（+08:00） | 与 Sprint 0 事件格式一致 |
| 金额类型 | `DECIMAL(18,2)` | 禁止用 float |
| 比率精度 | `DECIMAL(10,4)` | 保留 4 位小数 |
| 分母为 0 | 结果为 `NULL` | **不置 0**，避免把「无数据」误读为「比率为 0」 |
| 可加指标无事件 | 结果为 `0` | GMV / 金额 / 笔数在「该窗口确实没有这类事件」时补 0，见第 2.1 节 |
| 窗口字段 | `window_start` / `window_end` | 左闭右开 `[start, end)` |

---

## 2. 交易域指标

| 指标 | 字段名 | 口径 | 计算方式 | 所在表 |
| --- | --- | --- | --- | --- |
| GMV | `gmv` | 窗口内**订单创建**事件的成交金额合计 | `SUM(amount)` WHERE `event_type='ORDER_CREATED'` | `ads_realtime_trade_1m` |
| 订单量 | `order_cnt` | 窗口内订单创建事件数 | `COUNT(*)` WHERE `event_type='ORDER_CREATED'` | 同上 |
| 下单用户数 | `order_user_cnt` | 窗口内产生订单的去重用户数 | `COUNT(DISTINCT user_id)` | 同上 |
| 客单价 | `avg_order_amount` | 平均每单金额 | `gmv / order_cnt`，分母 0 时 NULL | 同上 |
| 支付笔数 | `payment_cnt` | 窗口内**支付成功**事件数 | `COUNT(*)` WHERE `event_type='PAYMENT_SUCCESS'` | 同上 |
| 支付金额 | `payment_amount` | 窗口内支付成功金额合计 | `SUM(amount)` WHERE 成功 | 同上 |
| 支付失败笔数 | `payment_fail_cnt` | 窗口内支付失败事件数 | `COUNT(*)` WHERE `event_type='PAYMENT_FAILED'` | 同上 |
| 支付成功率 | `payment_success_rate` | 成功占全部支付尝试的比例 | `payment_cnt / (payment_cnt + payment_fail_cnt)`，分母 0 时 NULL | 同上 |
| 退款笔数 | `refund_cnt` | 窗口内退款发起事件数 | `COUNT(*)` WHERE `event_type='REFUND_CREATED'` | 同上 |
| 退款金额 | `refund_amount` | 窗口内退款金额合计 | `SUM(refund_amount)` | 同上 |
| 退款率 | `refund_rate` | 退款金额占支付金额的比例 | `refund_amount / payment_amount`，分母 0 时 NULL | 同上 |

### 2.1 重要口径决策

#### GMV 以「订单创建」为准

```text
gmv = SUM(orders.amount) WHERE event_type = 'ORDER_CREATED'
```

**理由**：与主流电商口径一致（下单即计入 GMV）。
若需要「支付 GMV」，应另立指标 `payment_amount`，**不得复用 `gmv` 字段**。

#### 退款率的两种口径

本文件定义的是**金额口径**（退款金额 / 支付金额）。
若业务要「笔数口径」（退款笔数 / 支付笔数），
必须新建字段 `refund_cnt_rate`，不得覆盖 `refund_rate`。

#### 可加指标补 0，比率指标留 NULL（Sprint 1 实测后补充）

1 分钟滚动窗口是**固定骨架**：只要窗口内发生过订单 / 支付 / 退款中的任一类事件，
该窗口在 `ads_realtime_trade_1m` 中就会有一行。不同业务流的事件时间天然错位
（支付发生在下单之后的另一分钟），因此会出现：

```text
window_start = 11:32:00   gmv = 23616.00   order_cnt = 1   payment_cnt = ?
```

此处 `payment_cnt` 的语义是「这一分钟没有支付成功事件」，即 **0**，不是「未知」。
若置 NULL，看板会显示空白、下游 `SUM()` 会忽略该行，容易被误判为丢数据。

因此约定：

| 类别 | 字段 | 无事件时 |
| --- | --- | --- |
| 可加指标 | `gmv` / `order_cnt` / `order_user_cnt` / `payment_cnt` / `payment_amount` / `payment_fail_cnt` / `refund_cnt` / `refund_amount` | `0` |
| 比率指标 | `avg_order_amount` / `payment_success_rate` / `refund_rate` / `click_rate` / `cart_rate` / `buy_rate` | `NULL`（分母为 0） |

**离线链路（Sprint 3 起）必须遵循同一约定**，否则实时与离线无法对账。

---

## 3. 流量域指标

| 指标 | 字段名 | 口径 | 计算方式 | 所在表 |
| --- | --- | --- | --- | --- |
| UV | `uv` | 窗口内去重用户数 | `COUNT(DISTINCT user_id)` | `ads_realtime_traffic_1m` |
| PV | `pv` | 窗口内行为事件总数 | `COUNT(*)` | 同上 |
| 浏览次数 | `view_cnt` | `VIEW` 事件数 | `COUNT(*)` WHERE `event_type='VIEW'` | 同上 |
| 点击次数 | `click_cnt` | `CLICK` 事件数 | 同上 | 同上 |
| 加购次数 | `cart_cnt` | `CART` 事件数 | 同上 | 同上 |
| 收藏次数 | `favorite_cnt` | `FAVORITE` 事件数 | 同上 | 同上 |
| 购买次数 | `buy_cnt` | `BUY` 事件数 | 同上 | 同上 |
| 点击率 | `click_rate` | 点击占浏览比例 | `click_cnt / view_cnt`，分母 0 时 NULL | 同上 |
| 加购率 | `cart_rate` | 加购占点击比例 | `cart_cnt / click_cnt`，分母 0 时 NULL | 同上 |
| 购买转化率 | `buy_rate` | 购买占加购比例 | `buy_cnt / cart_cnt`，分母 0 时 NULL | 同上 |

> **PV 定义说明**：本项目中 PV = 全部行为事件数（含 VIEW/CLICK/CART/FAVORITE/BUY）。
> 这与「仅页面浏览」的狭义 PV 不同，是**有意选择**：
> 行为埋点本身就是事件流，统一计数更便于与 DWD 对账。
> 如需狭义 PV，请直接使用 `view_cnt`。

### 3.1 可加 / 去重 / 派生：三类指标的运算规则（Sprint 5 补充）

上表的指标**不是同一类东西**，运算规则完全不同。跨窗口、跨层级汇总时
必须先看清它属于哪一类 —— 这是流量域最容易出错的地方：

| 类别 | 字段 | 可以做什么 | **不可以**做什么 |
| --- | --- | --- | --- |
| **可加** | `pv` / `view_cnt` / `click_cnt` / `cart_cnt` / `favorite_cnt` / `buy_cnt` | 逐窗口比对；跨窗口/跨天直接 `SUM` 上卷 | — |
| **去重** | `uv` | 逐窗口比对；要天粒度就**回到明细**重新去重 | **跨窗口相加**（`SUM(uv)` 不是任何有意义的量） |
| **派生** | `click_rate` / `cart_rate` / `buy_rate` | 用「合计后的分子 / 合计后的分母」重算 | 对逐窗口比率求平均（先算比率再平均 = 口径错误） |

**实测证据（本项目真实数据，可复核）**：

```text
SUM(19644 个窗口的 uv) = 19999        ← 逐窗口去重的基数
全部 20000 个事件的去重用户数 = 1200   ← 人的基数
两者相差约 16 倍，但**都正确** —— 它们是两个不同的定义。
若把「天 UV」写成 SUM(分钟 uv)，会系统性偏大且看起来仍然合理
（19999 < 20000 的 PV，不会有人怀疑），属于最难发现的口径错误。
```

> 上面两个数字都是**实测值**，且第二个已用精确算法复核：
> `SIZE(COLLECT_SET(user_id))` = 1200、`COUNT(DISTINCT user_id)` = 1200
> （Parquet 侧与 Iceberg 侧一致）。
> 顺带一条教训：本项目文档里一度把这个数写成 **1199** ——
> 那是一次早期估算的残留，**从未被真正测量过**，却因为"看起来像个真实数字"
> 而在多处被引用。写入文档的数字必须能指回一条可复现的命令。

因此离线侧的分工是固定的（见 `infrastructure/spark/sql/07_traffic_ads.sql`）：

```text
ads_traffic_1m.uv   = COUNT(DISTINCT user_id)      ← 跟随实时侧 Flink 的实现语义
ads_traffic_1d.uv   = 回到 dwd 明细精确去重         ← 业务事实列，且被相等断言使用
可加量 1d           = 对 1m 直接 SUM                ← 保证「天 = 该天所有分钟之和」
比率                = 用 1d 的分子分母重算          ← 不对 1m 的比率求平均
```

> `ads_traffic_1m.uv` 用**近似**去重（`COUNT(DISTINCT)`）是**刻意的**，不是遗漏：
> 它是参与批流对账的列，必须与实时侧 Flink 的同类实现保持同一语义，
> 否则会因「算法不同」而对不上，且失败原因极难定位。
> 与之相对，**用来做相等断言的去重**（DWS 的 `uv`、作业自检里的天数）
> 一律用精确实现（`SIZE(COLLECT_SET(x))`）。

### 3.2 流量域离线汇总表（Sprint 5 新增）

| 表 | 粒度 | 说明 |
| --- | --- | --- |
| `iceberg.lakehouse_iceberg.dwd_traffic_behavior_detail` | 事件 | 行为事件明细（去重 `event_id` + 清洗 + `category_name` 补全） |
| `iceberg.lakehouse_iceberg.dws_traffic_overview_1d` | 天 | 与实时侧 `dws_traffic_overview_1m` 同口径同字段名，只差粒度 |
| `iceberg.lakehouse_iceberg.dws_traffic_funnel_1d` | 天 | 行为漏斗阶梯（VIEW→CLICK→CART→BUY）+ 各步去重人数 |
| `iceberg.lakehouse_iceberg.ads_traffic_1m` | 分钟 | 与实时 `ecommerce.ads_realtime_traffic_1m` **同形**，用于逐窗口对账 |
| `iceberg.lakehouse_iceberg.ads_traffic_1d` | 天 | 看板/报表用 |

> `dws_traffic_funnel_1d` 里的 `view_user_cnt` / `click_user_cnt` /
> `cart_user_cnt` / `buy_user_cnt` 是**离线新增的下钻维度**，
> 实时侧没有对应列，因此**不参与批流对账**。
> 加它们的理由是它们真正有用（漏斗每一步的真实人数，能区分
> 「次数收窄」是人数减少还是少数用户重复行为），不是为了"看起来完整"。

### 3.3 已知实时侧数据缺陷：单窗口 `click_rate`（Sprint 5 对账发现）

Sprint 5 的流量域逐窗口对账发现**实时链路存在 1 个窗口的比率列与它自己的计数列矛盾**：

```text
window_start = 2026-03-21 19:23:00
实时 ecommerce.ads_realtime_traffic_1m：
    view_cnt = 2   click_cnt = 1   click_rate = 0.0000   ← 按本节公式应为 0.5000
离线 iceberg.lakehouse_iceberg.ads_traffic_1m：
    view_cnt = 2   click_cnt = 1   click_rate = 0.5000   ← 正确
```

**判定依据**（不是猜测）：本节的公式就是判据 —— `click_cnt / view_cnt = 1/2 = 0.5`。
同一个判据在离线侧 0 个窗口不成立、在实时侧 1 个窗口不成立，
说明差异不在「两侧算法不同」，而在实时侧那一行**自相矛盾**。

**旁证**：实时侧 `click_rate` 的全表取值只有 `{0.0000, NULL, 1.0000}`，
而全表满足 `0 < click_cnt < view_cnt` 的**分数窗口恰好只有这 1 个** ——
即目前数据里只有一个分数样本，而它就是错的
（分子为 0 或分子等于分母的窗口无法暴露截断）。

**影响范围**：7 个对账判据列（`uv` / `pv` / 6 个行为计数）在全部
19643 个窗口**逐窗口一致**，因此该缺陷**不影响任何已发布的指标口径**，
只影响实时侧 `click_rate` 这一个派生列的一行。

**处置**：对账作业把它作为**一等结论**落盘
（`ads_reconcile_traffic_summary.realtime_rate_anomaly_windows` 与
`ads_reconcile_traffic_1m.realtime_rate_anomaly`），**不阻断离线流水线** ——
重跑离线一万次也改不了实时写下的那个值。
修复需要在实时侧 Flink 作业中处理，见 `docs/sprint/SPRINT_5.md` 第 8 节。

---

## 4. 类目域指标

| 指标 | 字段名 | 口径 | 计算方式 | 所在表 |
| --- | --- | --- | --- | --- |
| 类目订单数 | `order_cnt` | 窗口内该类目订单数 | `COUNT(*)` GROUP BY 类目 | `ads_realtime_category_1m` |
| 类目 GMV | `gmv` | 窗口内该类目下单金额 | `SUM(amount)` | 同上 |
| 类目件数 | `total_quantity` | 窗口内该类目商品件数 | `SUM(quantity)` | 同上 |
| 类目客单价 | `avg_order_amount` | 类目平均每单金额 | `gmv / order_cnt`，分母 0 时 NULL | 同上 |

---

## 5. 指标与数据来源的对应关系

| 指标 | 事实来源 | 中间层 | 目标层 |
| --- | --- | --- | --- |
| `gmv` / `order_cnt` / `order_user_cnt` / `avg_order_amount` | Kafka `order_event` | Flink 1min TUMBLE | `ads_realtime_trade_1m` |
| `payment_*` | Kafka `payment_event` | Flink 1min TUMBLE | `ads_realtime_trade_1m` |
| `refund_*` | Kafka `refund_event` | Flink 1min TUMBLE | `ads_realtime_trade_1m` |
| `uv` / `pv` / 行为计数 | Kafka `behavior_event` | Flink 1min TUMBLE | `dws_traffic_overview_1m` → `ads_realtime_traffic_1m` |
| 类目维度 | Kafka `order_event` 中的冗余字段 `category_name` | Flink 1min TUMBLE（**不做维表 join**） | `ads_realtime_category_1m` |
| 会员等级 / 商品维度 | Doris `dim_user` / `dim_product` | Flink lookup join（Sprint 3 引入） | （Sprint 3 补齐 DWS） |

> **为什么类目不做 lookup join**：订单事件在生成时就冗余写入 `category_name`
> （见 `sql/metadata/kafka_topics.md`）。Sprint 1 用冗余字段即可完成类目聚合，
> 少一次维表 join 就少一个启动时序与外部依赖风险；
> 维表 join 能力留给 Sprint 3 的 DWS 层使用。

---

## 6. 为什么指标定义要独立成文档

这是「先工程，再智能」的直接体现：

```text
如果指标定义散落在 SQL、看板配置、Agent 提示词里
   ↓
同一个「GMV」会出现 3 种算法
   ↓
实时说 100 万、离线说 95 万
   ↓
Agent 引用哪一个都是"错的"
   ↓
数据不可信，智能层无从谈起
```

因此：

1. **本文件是唯一权威**，代码与看板都必须引用它；
2. Sprint 9 的 RAG 知识库**直接索引本文件**，
   让 Agent 在生成 SQL 前先获得口径定义（对应 `AGENTS.md` 第 10.1 节
   「Agent 必须知道指标定义」）；
3. 口径变更流程：改本文件 → 改 Flink/Spark 作业 → 改测试 → 提交。

---

## 7. 变更记录

| 日期 | 版本 | 变更 | 说明 |
| --- | --- | --- | --- |
| 2026-09-26 | V1.0 | 初始版本 | 建立 Sprint 1 实时指标口径 |
| 2026-09-26 | V1.1 | 补充空值约定 | 可加指标补 0、比率指标留 NULL；类目来源改为事件冗余字段 |
