# Data Generator — 电商业务数据与 Kafka 事件生成器

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> Sprint：0
> 依据：`docs/sprint/SPRINT_0.md` 第 12、13 节

用 Python 生成**业务关系正确**的电商数据：写入 MySQL 业务库，并向 Kafka 产生实时事件。

---

## 1. 两个正式入口

```bash
# 生成 MySQL 业务数据（user / product / orders / payment / refund）
python -m src.generate_mysql_data

# 生成 Kafka 实时事件（4 个 Topic）
python -m src.generate_events
```

需要在 `data-generator/` 目录下执行（`src` 为包目录，位于其上一级）。

也可一次性跑完整链路：

```bash
python -m src          # 等价于先 generate_mysql_data 再 generate_events
```

---

## 2. 运行方式

### 2.1 通过 Docker（推荐，无需本地装依赖）

```bash
# 在仓库根目录执行
docker compose run --rm data-generator python -m src.generate_mysql_data
docker compose run --rm data-generator python -m src.generate_events
```

`data-generator` 服务在 compose 的 `tools` profile 中，**不会**随
`docker compose up -d` 自动启动，这是有意设计：避免无人值守地写入大量数据。

容器内环境变量由 compose 注入（`MYSQL_HOST=mysql`、`KAFKA_BOOTSTRAP_SERVERS=kafka:9092` 等），
**容器内不使用 localhost**。

### 2.2 在宿主机运行

```bash
cd data-generator

python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
# source .venv/bin/activate        # Linux / WSL / macOS

pip install -r requirements.txt

python -m src.generate_mysql_data
python -m src.generate_events
```

宿主机运行需要仓库根目录的 `.env`，其中：

```env
MYSQL_HOST=localhost
KAFKA_BOOTSTRAP_SERVERS=localhost:19092   # 宿主机使用外部监听器
```

> Kafka 为什么有两个端口？见根目录 `README.md` 第 7.1 节。

---

## 3. 生成规模

默认值（可通过 `.env` 或命令行覆盖）：

| 数据 | 默认 | Sprint 0 要求 |
| --- | --- | --- |
| 用户 `user` | 1200 | 1000+ |
| 商品 `product` | 600 | 500+ |
| 订单 `orders` | 6000 | 5000+ |
| 支付 `payment` | 约 5400（92% 订单已支付） | 5000+ |
| 退款 `refund` | 约 250（已支付订单的 5%） | 少量 |

相关环境变量：

```env
GEN_USER_COUNT=1200
GEN_PRODUCT_COUNT=600
GEN_ORDER_COUNT=6000
GEN_BEHAVIOR_EVENT_COUNT=20000
GEN_PAY_RATIO=0.92
GEN_REFUND_RATIO=0.05
GEN_DISCOUNT_RATIO=0.15
GEN_RANDOM_SEED=20260926
GEN_BATCH_SIZE=1000
```

---

## 4. 业务一致性

生成器**不是完全随机**。以下关系由代码保证，并由单元测试断言：

```text
orders.user_id     ∈ user.user_id
orders.product_id  ∈ product.product_id
payment.order_id   ∈ orders.order_id
refund.order_id    ∈ orders.order_id
payment.user_id    = 对应订单的 user_id
refund.refund_amount ≤ orders.amount
product.price × orders.quantity ≈ orders.amount     （按概率模拟优惠折扣）
product.price > product.cost                        （保证毛利为正）
user.register_time ≤ orders.create_time ≤ orders.pay_time ≤ refund.refund_time
```

订单状态分布（默认）：

```text
PAID       约 86%     已支付
CREATED    约  8%     已创建未支付
REFUNDED   约  4%     已退款
CANCELLED  约  2%     已取消
```

### 4.1 Kafka 事件与 MySQL 的一致性

`generate_mysql_data` 完成后会把本次生成结果写入
`state/dataset_snapshot.json`；`generate_events` **读取该快照**产生事件。

因此：

```text
order_event.order_id / user_id / product_id / amount  ↔  MySQL orders 行
payment_event.payment_id / order_id / amount          ↔  MySQL payment 行
refund_event.refund_id / order_id / refund_amount     ↔  MySQL refund 行
```

> 若未先生成 MySQL 数据就直接运行 `generate_events`，会提示找不到快照并退出，
> 这是有意设计——**不允许产生与业务库不一致的事件**。

---

## 5. Kafka 事件格式

统一 JSON，ISO-8601 带时区。四个 Topic：

| Topic | 事件类型 | 分区键 |
| --- | --- | --- |
| `order_event` | `ORDER_CREATED` | `order_id` |
| `payment_event` | `PAYMENT_SUCCESS` / `PAYMENT_FAILED` | `order_id` |
| `refund_event` | `REFUND_CREATED` | `order_id` |
| `behavior_event` | `VIEW` / `CLICK` / `CART` / `FAVORITE` / `BUY` | `user_id` |

完整字段定义见 [`../sql/metadata/kafka_topics.md`](../sql/metadata/kafka_topics.md)。

### 5.1 行为漏斗

行为事件强制**逐级收窄**：

```text
VIEW  >  CLICK  >  CART  >  BUY
                    ↑
               FAVORITE（旁支，少于 VIEW）
```

`BUY` 事件只针对**真实下过单的 (user_id, product_id) 组合**，保证与 `orders` 表语义一致。

---

## 6. 命令行参数

### 6.1 `src.generate_mysql_data`

| 参数 | 说明 |
| --- | --- |
| `--reset` | 写入前清空全部业务表 |
| `--users N` | 用户数量 |
| `--products N` | 商品数量 |
| `--orders N` | 订单数量 |
| `--seed N` | 随机种子（保证可复现） |
| `--batch-size N` | 每批写入行数 |
| `--no-verify` | 跳过写入后的业务关系校验 |
| `--no-snapshot` | 不保存数据集快照 |

写入后会在**数据库层面**校验业务关系（连表查询），失败则抛错。

### 6.2 `src.generate_events`

| 参数 | 说明 |
| --- | --- |
| `--topics a,b` | 只发送指定 Topic |
| `--behavior N` | 覆盖行为事件条数 |
| `--continuous` | 持续发送，直到 Ctrl+C |
| `--round-behavior N` | 持续模式下每轮行为事件条数 |
| `--rate N` | 限速（条/秒），0 为不限速 |
| `--count N` | 最多发送 N 条后退出 |
| `--seed N` | 随机种子 |
| `--snapshot PATH` | 指定数据集快照路径 |

### 6.3 示例

```bash
# 先清空再生成基线数据
python -m src.generate_mysql_data --reset

# 更大规模、可复现
python -m src.generate_mysql_data --users 2000 --orders 10000 --seed 42

# 只发订单与支付事件
python -m src.generate_events --topics order_event,payment_event

# 持续发送（模拟实时流量）
python -m src.generate_events --continuous --round-behavior 2000

# 限速压测
python -m src.generate_events --rate 500 --count 10000
```

---

## 7. 目录结构

```text
data-generator/
├── src/
│   ├── __init__.py
│   ├── __main__.py              一站式入口（先 MySQL 再 Kafka）
│   ├── common.py                日志、时区、ID 生成
│   ├── config.py                配置与 .env 加载
│   ├── db.py                    MySQL 连接与批量写入
│   ├── dataset.py               业务数据模型与生成逻辑
│   ├── generate_mysql_data.py   入口 1：写 MySQL
│   ├── generate_events.py       入口 2：发 Kafka
│   ├── kafka_events.py          事件构造与漏斗分配
│   └── kafka_producer.py        Kafka 生产者封装
├── state/                       运行时快照（不入 Git）
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## 8. 依赖

| 包 | 版本约束 | 用途 |
| --- | --- | --- |
| `Faker` | `>=30,<40` | 仿真数据 |
| `mysql-connector-python` | `>=9,<10` | 写 MySQL |
| `confluent-kafka` | `>=2,<3` | 发 Kafka |
| `python-dotenv` | `>=1,<2` | 读 `.env` |
| `pytest` | `>=8,<10` | 测试 |

已实测可安装的版本（Python 3.13.14）：Faker 39.1.0、mysql-connector-python 9.7.0、
confluent-kafka 2.15.1、python-dotenv 1.2.3、pytest 9.1.1。

---

## 9. 测试

```bash
# 单元测试（不需要容器），覆盖业务关系、金额逻辑、时间因果、漏斗单调性
python -m pytest ../../tests/test_data_generator.py -m unit

# 或在仓库根目录
python -m pytest -m unit
```

---

## 10. 设计说明

### 10.1 为什么用快照而不是重新随机生成事件？

如果 `generate_events` 重新随机生成一套订单，就会出现
「Kafka 里的 order_id 在 MySQL 中不存在」的情况，
下游 Flink 作业做维表关联时会大量丢失。
Sprint 0 明确要求「不要生成完全无业务意义的随机数据」，
因此事件必须源自**真实写入的业务数据**。

### 10.2 为什么金额用 Decimal？

金额必须精确到分。使用 `float` 会在累加与折扣计算中引入浮点误差，
导致 `price × quantity ≠ amount` 这类对账问题。
项目规范（`AGENTS.md` 第 4.2 节）要求金额相关计算使用 `Decimal`。

### 10.3 为什么时间要满足因果链？

真实业务中「先注册才能下单，先下单才能支付，先支付才能退款」。
若时间随机，下游实时数仓的窗口统计与漏斗分析会得到无意义的结果。
