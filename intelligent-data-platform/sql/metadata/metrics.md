# 指标口径字典（Metrics Dictionary）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 版本：V1.0（Sprint 1）
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
