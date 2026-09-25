# Sprint 0 开发任务书：项目初始化与基础数据链路

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台  
> 版本：Sprint 0 / V1.0  
> 目标：建立可重复、可验证、可扩展的本地开发基础环境，为后续 Kafka → Flink → Doris、Spark → Iceberg、Agent → MCP 开发提供稳定基线。

---

## 1. Sprint 0 目标

本 Sprint 不实现完整数仓、Flink 作业或 Agent。

只完成以下内容：

1. 建立 Git 项目结构。
2. 建立 Docker Compose 基础环境。
3. 启动 MySQL。
4. 启动 Kafka。
5. 启动 MinIO。
6. 启动 Doris FE/BE。
7. 建立统一 Docker 网络。
8. 建立持久化 Volume。
9. 建立 `.env.example`，禁止密码硬编码进 Git。
10. 建立 MySQL 业务数据库及基础表。
11. 建立 Kafka 基础 Topic。
12. 建立 Doris 基础数据库。
13. 建立数据生成器的最小骨架。
14. 编写统一启动、停止、状态检查脚本。
15. 编写 README。
16. 编写 `AGENTS.md`。
17. 完成基础健康检查。
18. 所有内容提交到 Git。

---

# 2. 技术栈

Sprint 0 只使用：

- Docker / Docker Compose
- MySQL 8.x
- Kafka
- MinIO
- Apache Doris
- Python 3.x
- SQL
- Git
- Bash / PowerShell（根据宿主环境选择）

暂时不要引入：

- Hadoop
- Hive
- Spark
- Flink
- Airflow
- Iceberg
- LangGraph
- MCP
- 向量数据库
- Kubernetes

这些属于后续 Sprint。

---

# 3. 宿主机环境

推荐开发环境：

```text
Windows 11
    ↓
WSL2
    ↓
Ubuntu
    ↓
Docker Desktop
```

如果实际开发环境是 Linux，也必须保证项目脚本可以运行。

要求：

```bash
docker --version
docker compose version
git --version
python3 --version
```

所有版本信息最终记录到：

```text
docs/development-environment.md
```

---

# 4. 项目目录

创建：

```text
intelligent-data-platform/
│
├── README.md
├── AGENTS.md
├── docker-compose.yml
├── .env.example
├── .gitignore
│
├── docs/
│   ├── PROJECT_DESIGN_V1.md
│   ├── development-environment.md
│   └── sprint/
│       └── SPRINT_0.md
│
├── infrastructure/
│   ├── mysql/
│   │   └── init/
│   ├── kafka/
│   │   └── init/
│   ├── minio/
│   └── doris/
│
├── sql/
│   ├── mysql/
│   ├── doris/
│   └── metadata/
│
├── data-generator/
│   ├── src/
│   ├── config/
│   ├── requirements.txt
│   └── README.md
│
├── scripts/
│   ├── start.sh
│   ├── stop.sh
│   ├── status.sh
│   └── health-check.sh
│
├── volumes/
│   └── .gitkeep
│
└── tests/
    └── smoke/
```

不要提交真实数据和运行时文件。

---

# 5. Docker 网络

统一创建：

```yaml
networks:
  data-platform:
    name: data-platform
    driver: bridge
```

所有服务必须加入：

```text
data-platform
```

容器之间禁止依赖：

```text
localhost
127.0.0.1
```

容器间必须使用 Docker service name。

例如：

```text
mysql:3306
kafka:9092
minio:9000
doris-fe:9030
```

---

# 6. 环境变量

创建：

```text
.env.example
```

示例：

```env
MYSQL_ROOT_PASSWORD=change_me
MYSQL_DATABASE=ecommerce
MYSQL_USER=app
MYSQL_PASSWORD=change_me

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=change_me

DORIS_ROOT_PASSWORD=change_me

KAFKA_BROKER_ID=1
KAFKA_PORT=9092

TZ=Asia/Shanghai
```

注意：

- `.env` 必须加入 `.gitignore`
- `.env.example` 可以提交
- 不允许在源码、SQL、Compose 文件中硬编码真实密码
- 不允许提交 API Key
- 不允许提交 LLM Token

---

# 7. MySQL

数据库：

```text
ecommerce
```

创建以下业务表：

```text
user
product
orders
payment
refund
```

## user

```sql
CREATE TABLE user (
    user_id BIGINT PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    gender TINYINT,
    age INT,
    province VARCHAR(50),
    city VARCHAR(50),
    user_level VARCHAR(20),
    register_time DATETIME,
    update_time DATETIME
);
```

## product

```sql
CREATE TABLE product (
    product_id BIGINT PRIMARY KEY,
    product_name VARCHAR(200) NOT NULL,
    category_id BIGINT,
    category_name VARCHAR(100),
    brand VARCHAR(100),
    price DECIMAL(18,2),
    cost DECIMAL(18,2),
    status TINYINT DEFAULT 1,
    create_time DATETIME,
    update_time DATETIME
);
```

## orders

```sql
CREATE TABLE orders (
    order_id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    amount DECIMAL(18,2) NOT NULL,
    status VARCHAR(30),
    create_time DATETIME,
    pay_time DATETIME,
    update_time DATETIME
);
```

## payment

```sql
CREATE TABLE payment (
    payment_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    amount DECIMAL(18,2) NOT NULL,
    payment_method VARCHAR(30),
    payment_status VARCHAR(30),
    payment_time DATETIME
);
```

## refund

```sql
CREATE TABLE refund (
    refund_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    refund_amount DECIMAL(18,2),
    refund_reason VARCHAR(200),
    refund_status VARCHAR(30),
    refund_time DATETIME
);
```

---

# 8. Kafka

Kafka 只需要完成基础可用性。

创建 Topic：

```text
order_event
payment_event
refund_event
behavior_event
```

推荐配置：

```text
partition = 3
replication factor = 1
```

Sprint 0 不追求高可用。

每个 Topic 需要有简单说明：

```text
order_event
    订单创建/状态变化

payment_event
    支付成功/失败

refund_event
    退款事件

behavior_event
    浏览/点击/搜索/加购/收藏/购买行为
```

---

# 9. Kafka 事件格式

统一 JSON。

## order_event

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

## payment_event

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

## refund_event

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

## behavior_event

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

---

# 10. MinIO

建立 Bucket：

```text
lakehouse
```

后续规划：

```text
lakehouse/
├── warehouse/
├── metadata/
├── checkpoint/
└── archive/
```

Sprint 0 只要求：

- MinIO 正常启动
- Console 可以访问
- Bucket 可以创建
- API 可以访问

---

# 11. Doris

启动：

```text
Doris FE
Doris BE
```

建立：

```text
ecommerce
```

Sprint 0 只创建一个测试表：

```sql
CREATE TABLE test_connection (
    id BIGINT,
    message VARCHAR(255),
    create_time DATETIME
)
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES (
    "replication_num" = "1"
);
```

插入测试数据：

```sql
INSERT INTO test_connection VALUES
(1, 'doris connection ok', NOW());
```

能够执行：

```sql
SELECT * FROM test_connection;
```

即视为 Doris 基础环境成功。

---

# 12. 数据生成器

创建 Python 项目：

```text
data-generator/
```

要求：

```text
Python
Faker
mysql-connector-python
kafka-python 或 confluent-kafka
```

第一版支持：

```bash
python -m src.generate_mysql_data
```

生成：

```text
用户：1000+
商品：500+
订单：5000+
支付：5000+
退款：随机少量
```

以及：

```bash
python -m src.generate_events
```

持续向 Kafka 发送事件。

---

# 13. 数据生成规则

不要完全随机。

必须保证基本业务逻辑。

例如：

```text
订单 user_id 必须来自 user
订单 product_id 必须来自 product
payment.order_id 必须来自 orders
refund.order_id 必须来自 orders
```

金额逻辑：

```text
product.price × quantity ≈ order.amount
```

行为逻辑：

```text
VIEW
 ↓
CLICK
 ↓
CART
 ↓
BUY
```

不是每个用户都必须完整经历全部流程，但事件分布要合理。

---

# 14. Scripts

## start.sh

功能：

```bash
docker compose up -d
```

## stop.sh

功能：

```bash
docker compose down
```

## status.sh

功能：

```bash
docker compose ps
```

## health-check.sh

检查：

```text
MySQL
Kafka
MinIO
Doris FE
Doris BE
```

输出：

```text
[OK] MySQL
[OK] Kafka
[OK] MinIO
[OK] Doris FE
[OK] Doris BE
```

如果任意组件失败：

```text
exit 1
```

---

# 15. Docker Compose 要求

不要使用 `latest` 标签。

所有镜像必须使用明确版本。

版本选择必须：

1. 优先使用官方稳定版本。
2. 确保各组件之间兼容。
3. 将实际使用版本记录到 `docs/development-environment.md`。
4. 如果某组件版本发生变化，更新文档。

如果不确定版本兼容性，不要猜测；先查官方文档。

---

# 16. Healthcheck

MySQL：

```text
mysqladmin ping
```

Kafka：

使用 Kafka 官方可用性检查方式。

MinIO：

检查健康接口。

Doris：

检查 FE/BE 服务状态。

所有健康检查都必须有：

```text
interval
timeout
retries
start_period
```

---

# 17. 启动顺序

基础依赖关系：

```text
network
   │
   ├── MySQL
   │
   ├── Kafka
   │
   ├── MinIO
   │
   └── Doris FE
            │
            ▼
         Doris BE
```

不要假设：

```text
container started
=
service ready
```

必须通过 healthcheck / retry 解决服务启动时序问题。

---

# 18. README 最低要求

README 必须包含：

```text
项目简介
架构图
技术栈
环境要求
项目目录
快速启动
服务端口
账号密码说明
健康检查
数据生成
常见问题
后续 Roadmap
```

快速启动必须可以写成：

```bash
cp .env.example .env

docker compose up -d

bash scripts/status.sh

bash scripts/health-check.sh
```

Windows 环境必须补充 PowerShell 等价命令，或者明确说明通过 WSL 执行 Bash 脚本。

---

# 19. Smoke Test

必须实现基础冒烟测试：

```text
1. MySQL 可以连接
2. ecommerce 数据库存在
3. 业务表存在
4. Kafka Topic 存在
5. Kafka 可以生产消息
6. Kafka 可以消费消息
7. MinIO Bucket 存在
8. Doris 可以连接
9. Doris 可以创建表
10. Doris 可以查询测试数据
```

测试失败时必须返回非 0 exit code。

---

# 20. Git 要求

第一次提交至少拆成：

```text
chore: initialize project structure

chore: add docker compose infrastructure

feat: add mysql ecommerce schema

feat: add kafka topics and event schema

feat: add minio lakehouse bucket

feat: add doris initialization

feat: add data generator skeleton

test: add infrastructure smoke tests

docs: add sprint 0 documentation
```

如果实际开发过程中不适合拆这么多 commit，可以合并，但最终必须保持清晰的提交历史。

---

# 21. 禁止事项

## 禁止 1：一次性实现所有系统

Sprint 0 不实现：

```text
Flink
Spark
Hive
Airflow
Iceberg
Agent
MCP
Frontend
```

---

## 禁止 2：偷偷增加技术栈

如果发现必须增加：

```text
Redis
Elasticsearch
ClickHouse
Trino
Milvus
PostgreSQL
```

必须先说明原因，不允许直接加入。

---

## 禁止 3：硬编码 Secret

禁止：

```python
API_KEY = "sk-xxxx"
```

禁止：

```yaml
password: my-real-password
```

统一使用 `.env`。

---

## 禁止 4：为了通过测试删除测试

不能通过删除测试解决问题。

---

## 禁止 5：修改架构文档而不说明

如果实现和架构文档产生冲突：

```text
先停止
↓
说明冲突
↓
提出修改方案
↓
等待确认
```

---

# 22. 完成标准 Definition of Done

Sprint 0 只有满足以下条件才算完成：

### 基础设施

- [ ] Docker Compose 可以启动
- [ ] MySQL 正常
- [ ] Kafka 正常
- [ ] MinIO 正常
- [ ] Doris FE 正常
- [ ] Doris BE 正常

### 数据

- [ ] MySQL 数据库创建成功
- [ ] MySQL 五张业务表创建成功
- [ ] Kafka 四个 Topic 创建成功
- [ ] MinIO Bucket 创建成功
- [ ] Doris 数据库创建成功
- [ ] Doris 测试表创建成功

### 数据生成器

- [ ] 可以生成基础业务数据
- [ ] 可以生成 Kafka 事件
- [ ] 数据关系合理

### 工程

- [ ] `.env.example`
- [ ] `.gitignore`
- [ ] README
- [ ] AGENTS.md
- [ ] start.sh
- [ ] stop.sh
- [ ] status.sh
- [ ] health-check.sh

### 测试

- [ ] 基础 Smoke Test 全部通过
- [ ] docker compose 重启后数据仍然存在
- [ ] 不依赖宿主机 localhost 访问容器内部服务

---

# 23. Sprint 0 输出物

最终必须得到：

```text
intelligent-data-platform/
```

可以执行：

```bash
docker compose up -d
```

然后：

```bash
bash scripts/health-check.sh
```

输出：

```text
====================================
 Data Platform Health Check
====================================

[OK] MySQL
[OK] Kafka
[OK] MinIO
[OK] Doris FE
[OK] Doris BE

====================================
 All services are healthy
====================================
```

再执行：

```bash
python -m data_generator
```

可以产生业务数据和 Kafka 事件。

---

# 24. 下一 Sprint

Sprint 0 完成以后进入：

## Sprint 1：Kafka + Flink + Doris 实时数仓

目标：

```text
Kafka
 ↓
Flink
 ↓
Doris
 ↓
实时 GMV
 ↓
实时订单
 ↓
实时用户数
 ↓
实时 Dashboard 数据接口
```

Sprint 1 不提前实现 Spark、Airflow、Agent。

---

# 25. 总体 Roadmap

```text
Sprint 0
基础环境
    ↓
Sprint 1
Kafka + Flink + Doris
    ↓
Sprint 2
Spark + Hive + HDFS
    ↓
Sprint 3
ODS/DWD/DWS/ADS
    ↓
Sprint 4
Airflow
    ↓
Sprint 5
Iceberg Lakehouse
    ↓
Sprint 6
Backend + Dashboard
    ↓
Sprint 7
LLM + Tool Calling
    ↓
Sprint 8
LangGraph Data Agent
    ↓
Sprint 9
RAG + Metadata
    ↓
Sprint 10
MCP
    ↓
Sprint 11
Data Quality + Monitoring
    ↓
Sprint 12
测试 + 性能优化
    ↓
Sprint 13
毕业论文 + 答辩
```

---

# 26. AI Agent 开发规则

后续开发 Agent 时：

```text
Agent 不允许直接拥有数据库管理员权限。

Agent SQL 默认只允许 SELECT。

Agent 必须知道指标定义。

Agent 必须知道表结构。

Agent 生成 SQL 后必须进行安全检查。

Agent 查询失败必须能够重新规划。

Agent 的最终回答必须能够说明数据来源。

Agent 不得伪造查询结果。
```

最终目标：

```text
自然语言
    ↓
问题理解
    ↓
指标识别
    ↓
元数据检索
    ↓
SQL生成
    ↓
SQL安全检查
    ↓
Doris查询
    ↓
结果分析
    ↓
自然语言回答
```

---

# 27. 项目核心原则

整个项目遵守：

> **先工程，再智能。**

也就是：

```text
数据可靠
    ↓
数据准确
    ↓
数据可查询
    ↓
数据可治理
    ↓
Agent 使用数据
```

而不是：

```text
先做一个聊天机器人
    ↓
再想办法找数据
```

---

# 28. 当前任务边界

DeepSeek Harness 在 Sprint 0 中：

**只允许完成本文件规定的内容。**

如果某个功能属于后续 Sprint：

```text
不要提前实现
不要偷偷引入
不要为了“完整”增加
```

发现问题时：

```text
记录问题
说明影响
提出建议
等待确认
```

---

# 29. 最终检查命令

```bash
docker compose config
```

确认 Compose 配置有效。

```bash
docker compose up -d
```

启动。

```bash
docker compose ps
```

查看状态。

```bash
bash scripts/health-check.sh
```

健康检查。

```bash
python -m pytest
```

运行测试。

---

# 30. Sprint 0 完成后的状态

最终状态：

```text
                    Sprint 0
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      MySQL          Kafka           Doris
        │              │              │
        │              │              │
        └──────────────┼──────────────┘
                       │
                     MinIO

数据生成器
    │
    ├── MySQL业务数据
    │
    └── Kafka实时事件
```

**到这里停止。**

下一阶段再开始：

```text
Kafka → Flink → Doris
```

---

# 31. 给开发 Agent 的最终要求

开发过程中始终遵守：

> 不求一次完成，求每一步都可运行、可验证、可回滚。

每完成一个模块：

```text
代码
 ↓
测试
 ↓
验证
 ↓
文档
 ↓
Git Commit
```

只有上一层稳定，才能进入下一层。
