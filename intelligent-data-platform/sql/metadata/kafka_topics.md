# Kafka Topic 与事件格式定义

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> Sprint：0
> 依据：`docs/sprint/SPRINT_0.md` 第 8、9 节
> 状态：**Sprint 0 基线，后续 Sprint 的流式计算必须以此为准**

---

## 1. Topic 列表

由 `infrastructure/kafka/init/01-create-topics.sh` 幂等创建。

| Topic | 分区 | 副本 | 说明 |
| --- | --- | --- | --- |
| `order_event` | 3 | 1 | 订单创建 / 状态变化 |
| `payment_event` | 3 | 1 | 支付成功 / 失败 |
| `refund_event` | 3 | 1 | 退款事件 |
| `behavior_event` | 3 | 1 | 浏览 / 点击 / 搜索 / 加购 / 收藏 / 购买行为 |

- 分区数：`KAFKA_TOPIC_PARTITIONS`，默认 `3`
- 副本数：`KAFKA_TOPIC_REPLICATION_FACTOR`，默认 `1`

> Sprint 0 为单机开发环境，`replication-factor = 1`，不追求高可用。

---

## 2. 编码与时区约定

| 项目 | 约定 |
| --- | --- |
| 序列化 | JSON（UTF-8） |
| 时间字段 | ISO-8601 **带时区偏移**，如 `2026-09-26T10:00:00+08:00` |
| 时区 | `Asia/Shanghai`（+08:00） |
| 金额 | JSON number，单位为元，保留 2 位小数 |
| ID | JSON number（整数）；`event_id` 为 string |

---

## 3. 公共信封字段

所有事件的公共字段。下游 Flink 作业应先解析这三个字段。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `event_id` | string | 是 | 全局唯一事件 ID，用于幂等去重 |
| `event_type` | string | 是 | 事件类型枚举，大写蛇形 |
| `event_time` | string | 是 | 事件发生时间，ISO-8601 带时区 |

---

## 4. 分区键（Partition Key）

| Topic | Key | 取值 |
| --- | --- | --- |
| `order_event` | `order_id` | 字符串化 |
| `payment_event` | `order_id` | 字符串化 |
| `refund_event` | `order_id` | 字符串化 |
| `behavior_event` | `user_id` | 字符串化 |

**理由**：保证同一订单 / 同一用户的事件落入同一分区，从而保证**分区内有序**。

---

## 5. `order_event`

### 5.1 事件类型

| 取值 | 含义 |
| --- | --- |
| `ORDER_CREATED` | 订单创建 |
| `ORDER_PAID` | 订单已支付 |
| `ORDER_CANCELLED` | 订单取消 |
| `ORDER_REFUNDED` | 订单已退款 |

> Sprint 0 的数据生成器只产生 `ORDER_CREATED`；
> 其余类型由后续 Sprint 的 Flink 作业或状态变更逻辑产生。

### 5.2 示例

```json
{
  "event_id": "evt_10001",
  "event_type": "ORDER_CREATED",
  "order_id": 10001,
  "user_id": 1001,
  "product_id": 2001,
  "quantity": 2,
  "amount": 199.90,
  "event_time": "2026-09-26T10:00:00+08:00"
}
```

### 5.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `order_id` | number | 是 | 订单 ID，对应 MySQL `orders.order_id` |
| `user_id` | number | 是 | 用户 ID，必须存在于 `user` |
| `product_id` | number | 是 | 商品 ID，必须存在于 `product` |
| `quantity` | number | 是 | 购买数量 |
| `amount` | number | 是 | 订单金额，应满足 `product.price × quantity ≈ amount` |

---

## 6. `payment_event`

### 6.1 事件类型

| 取值 | 含义 |
| --- | --- |
| `PAYMENT_SUCCESS` | 支付成功 |
| `PAYMENT_FAILED` | 支付失败 |

> Sprint 0 的数据生成器只产生 `PAYMENT_SUCCESS`。

### 6.2 示例

```json
{
  "event_id": "evt_20001",
  "event_type": "PAYMENT_SUCCESS",
  "payment_id": 30001,
  "order_id": 10001,
  "user_id": 1001,
  "amount": 199.90,
  "payment_method": "ALIPAY",
  "event_time": "2026-09-26T10:01:00+08:00"
}
```

### 6.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `payment_id` | number | 是 | 支付 ID，对应 MySQL `payment.payment_id` |
| `order_id` | number | 是 | 订单 ID，必须存在于 `orders` |
| `user_id` | number | 是 | 用户 ID，与订单用户一致 |
| `amount` | number | 是 | 支付金额，默认等于订单金额 |
| `payment_method` | string | 是 | 支付方式 |

`payment_method` 取值：`ALIPAY` / `WECHAT` / `UNIONPAY` / `CREDIT_CARD` / `COD`（货到付款）

---

## 7. `refund_event`

### 7.1 事件类型

| 取值 | 含义 |
| --- | --- |
| `REFUND_CREATED` | 退款发起 |
| `REFUND_SUCCESS` | 退款成功 |
| `REFUND_FAILED` | 退款失败 |

> Sprint 0 的数据生成器只产生 `REFUND_CREATED`。

### 7.2 示例

```json
{
  "event_id": "evt_30001",
  "event_type": "REFUND_CREATED",
  "refund_id": 40001,
  "order_id": 10001,
  "user_id": 1001,
  "refund_amount": 199.90,
  "event_time": "2026-09-26T11:00:00+08:00"
}
```

### 7.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `refund_id` | number | 是 | 退款 ID，对应 MySQL `refund.refund_id` |
| `order_id` | number | 是 | 订单 ID，必须存在于 `orders` |
| `user_id` | number | 是 | 用户 ID，与订单用户一致 |
| `refund_amount` | number | 是 | 退款金额，不得超过订单金额 |

---

## 8. `behavior_event`

### 8.1 事件类型与漏斗

```text
VIEW  →  CLICK  →  CART  →  BUY
                   ↑
              FAVORITE（收藏，旁支）

SEARCH（搜索，入口行为）
```

| 取值 | 含义 | 是否属于主漏斗 |
| --- | --- | --- |
| `VIEW` | 浏览商品详情 | 是 |
| `CLICK` | 点击商品 | 是 |
| `CART` | 加入购物车 | 是 |
| `BUY` | 购买 | 是 |
| `FAVORITE` | 收藏商品 | 旁支 |
| `SEARCH` | 搜索 | 入口 |

**行为分布要求**：漏斗必须逐级收窄，即

```text
count(VIEW) > count(CLICK) > count(CART) > count(BUY)
```

且 `FAVORITE` 数量应显著少于 `VIEW`。
不是每个用户都必须完整走完全部流程。

### 8.2 示例

```json
{
  "event_id": "evt_40001",
  "event_type": "VIEW",
  "user_id": 1001,
  "product_id": 2001,
  "device": "PC",
  "province": "Zhejiang",
  "event_time": "2026-09-26T10:05:00+08:00"
}
```

### 8.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | number | 是 | 用户 ID，必须存在于 `user` |
| `product_id` | number | 是 | 商品 ID，必须存在于 `product`；`SEARCH` 事件可为 `null` |
| `device` | string | 是 | 设备类型 |
| `province` | string | 否 | 省份，取自用户所在省份 |

`device` 取值：`PC` / `APP` / `H5` / `MINI_PROGRAM`

---

## 9. 与 MySQL 的关系（数据一致性约束）

事件不是凭空产生的，必须与 MySQL 业务数据保持一致：

| 事件 | 约束 |
| --- | --- |
| `order_event` | `order_id` / `user_id` / `product_id` / `amount` 必须与 MySQL `orders` 对应行一致 |
| `payment_event` | `order_id` 必须存在于 `orders`；`payment_id` 对应 `payment` 表 |
| `refund_event` | `order_id` 必须存在于 `orders`；`refund_id` 对应 `refund` 表 |
| `behavior_event` | `user_id` 必须存在于 `user`；`product_id` 必须存在于 `product` |

**金额一致性**：`product.price × orders.quantity ≈ orders.amount`
（允许存在少量因优惠导致的偏差，由数据生成器按概率模拟）。

Sprint 0 的冒烟测试会对上述约束做抽样校验。

---

## 10. 手工验证命令

```bash
# 列出 Topic
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --list

# 查看某个 Topic 的分区与副本
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --describe --topic order_event

# 从宿主机消费（观察数据生成器产生的事件）
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic behavior_event \
  --from-beginning --max-messages 10
```
