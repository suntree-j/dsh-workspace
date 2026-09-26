# AGENTS.md — 项目开发与 Agent 协作规范

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 适用范围：**所有参与本项目的开发者与 AI Agent（包括 DeepSeek Harness）**
> 版本：V1.0（Sprint 0）
>
> 本文件是**强制性规范**。任何代码、SQL、配置、文档的改动都必须遵守。
> 若规范与实际需求冲突，**先说明、再讨论、后修改**，禁止直接绕过。

---

## 1. 项目目标

构建面向电商场景的**批流一体**数据平台，最终具备：

```text
实时链路   Kafka → Flink → Doris          （秒级指标）
离线链路   Spark → Iceberg → Hive/HDFS    （准确、可回溯）
调度       Airflow
服务       FastAPI / Spring Boot + Vue
智能       LLM + Tool Calling + MCP + LangGraph Agent
治理       数据质量 + Prometheus/Grafana
```

### 1.1 核心原则

> **先工程，再智能。**

```text
数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据
```

**明确反对**的路径：

```text
先做聊天机器人 → 再想办法找数据
```

### 1.2 工作原则

> 不求一次完成，求每一步都可运行、可验证、可回滚。

每个模块都必须走完：

```text
理解 → 规划 → 小步实现 → 测试 → 验证 → 文档 → Git Commit
```

**只有上一层稳定，才能进入下一层。**

---

## 2. 技术栈

### 2.1 当前（Sprint 0 + Sprint 1）

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Docker / Docker Compose | Compose v2+ | 容器编排 |
| MySQL | `8.4.11` LTS | 电商业务库（唯一事实源） |
| Apache Kafka | `4.2.1` KRaft | 事件总线（无 ZooKeeper） |
| MinIO | `RELEASE.2025-10-15T17-29-55Z` | S3 兼容对象存储 |
| Apache Doris | `4.1.4`（FE + BE） | OLAP 查询与指标 |
| Apache Flink | `1.20.1`（SQL + SQL Gateway） | 实时清洗与窗口聚合（Sprint 1 引入） |
| flink-sql-connector-kafka | `3.4.0-1.20` | Flink 读写 Kafka |
| Python | 3.13 | 数据生成器 |
| pytest | 9.x | 测试 |

### 2.2 后续 Sprint 引入

Spark + Hive + HDFS（S2）、Airflow（S4）、Iceberg（S5）、
FastAPI / Spring Boot / Vue（S6）、LLM + Tool Calling（S7）、LangGraph（S8）、
RAG（S9）、MCP（S10）、Prometheus + Grafana（S11）。

### 2.3 未经批准禁止引入

```text
Redis / Elasticsearch / ClickHouse / Trino / Milvus / PostgreSQL
Kubernetes / Nacos / RabbitMQ / MongoDB ...
```

如确有必要，必须：

```text
说明原因 → 说明影响 → 给出方案 → 等待确认 → 再实施
```

---

## 3. 目录结构与职责

| 目录 | 职责 | 约束 |
| --- | --- | --- |
| `docs/` | 设计、环境、Sprint 文档 | 架构变更必须更新 `PROJECT_DESIGN_V1.md` |
| `infrastructure/` | 各组件容器初始化配置 | 一个组件一个子目录 |
| `sql/` | DDL / DML / 元数据定义 | **必须进 Git**，禁止只改容器内数据库 |
| `scripts/` | 启停、状态、健康检查 | Bash，兼容 Git Bash / WSL / Linux |
| `data-generator/` | Python 数据生成器 | 业务关系必须正确 |
| `tests/` | 单元测试与冒烟测试 | 冒烟测试必须真实执行 |
| `volumes/` | 本地绑定挂载占位 | 内容不入 Git |

**禁止**为后续 Sprint 的组件提前创建目录或占位文件。

---

## 4. 编码规范

### 4.1 通用

1. **小步提交**：一次提交只做一件明确的事。
2. **可读优先**：命名清晰，避免过度抽象。
3. **不引入未声明的依赖**。
4. **不留调试残留**：提交前清理 `print`、临时文件、注释掉的代码。
5. **注释解释「为什么」，而不是「是什么」**。

### 4.2 Python

1. 遵循 **PEP 8**，使用 4 空格缩进。
2. **所有函数必须有类型注解**。
3. 使用 `from __future__ import annotations` 以支持前向引用。
4. 使用模块级 logger，禁止用 `print` 输出日志：
   ```python
   from .common import get_logger
   log = get_logger("module_name")
   log.info("已写入 %d 行", count)
   ```
5. 优先使用 `dataclass` 表达数据结构。
6. 文件读写**必须显式指定 `encoding="utf-8"`**（否则 Windows 下会乱码）。
7. 金额相关计算使用 `decimal.Decimal`，**禁止用 `float` 做金额累加**。
8. 对外部依赖的调用必须有**超时与重试**（容器启动时序不可靠）。
9. 禁止在异常处理中静默 `pass`，至少记录日志。

### 4.3 Bash

1. 使用 `#!/usr/bin/env bash`。
2. 必须启用严格模式：`set -euo pipefail`。
3. 所有变量引用加双引号：`"${VAR}"`。
4. 路径通过 `REPO_ROOT` 计算，**不要假设当前工作目录**。
5. 脚本必须 LF 行尾（由 `.gitattributes` 保证）。
6. 健康检查类脚本必须**正确返回退出码**，禁止「无论结果都返回 0」。

### 4.4 YAML（Docker Compose）

1. `image` **禁止使用 `latest`**，必须写明确版本。
2. 所有服务必须加入 `data-platform` 网络。
3. 数据目录必须使用**命名卷**。
4. 所有长期运行的服务必须配置 `healthcheck`，
   且包含 `interval` / `timeout` / `retries` / `start_period`。
5. 敏感信息一律用 `${VAR}` 引用 `.env`，禁止硬编码。

---

## 5. SQL 规范

### 5.1 命名

```text
库名    小写下划线                  ecommerce
表名    小写下划线 + 分层前缀        dwd_trade_order_detail
字段名  小写下划线                  order_id / create_time
主键    <实体>_id                   user_id / order_id / payment_id
时间    create_time / update_time / <动作>_time
```

### 5.2 数仓分层（Sprint 3 起）

| 分层 | 职责 |
| --- | --- |
| ODS | 贴源层，结构与源系统一致 |
| DWD | 明细层，清洗、去重、维度补全 |
| DWS | 汇总层，按主题轻度聚合 |
| ADS | 应用层，直接面向指标与报表 |

### 5.3 强制要求

1. **所有 DDL 必须存入 `sql/` 并提交 Git。**
   **禁止**手工修改容器内数据库结构而不落盘到 `sql/`。
2. 建表必须显式指定：字符集 `utf8mb4`、排序规则 `utf8mb4_unicode_ci`、表注释。
3. 每个字段必须有 `COMMENT`。
4. 初始化脚本必须**幂等**（`IF NOT EXISTS` / 先删后插）。
5. 单 BE 环境下建表必须指定 `replication_num = 1`。
6. 金额字段统一 `DECIMAL(18,2)`。
7. 时间字段统一 `DATETIME`，时区 `Asia/Shanghai`。
8. 禁止 `SELECT *` 进入生产代码（元数据探查除外）。
9. 大批量写入必须分批，避免超过 `max_allowed_packet`。

### 5.4 文档中的 SQL 与代码中的 SQL 必须一致

修改 `sql/` 下脚本后，必须同步检查 `README.md`、`docs/` 中的相关说明。

---

## 6. Docker 规范

1. **统一网络 `data-platform`**（`driver: bridge`），显式声明子网。
2. **容器间禁止使用 `localhost` / `127.0.0.1`**，必须使用服务名。
   - 例外：Doris FE/BE 必须使用**固定 IP**（`172.28.0.10` / `172.28.0.11`），
     因为 Doris 初始化脚本用正则校验 `FE_SERVERS=<name>:<IPv4>:<port>`，
     不接受主机名，且会把 `priority_networks` 推导为 `<IP 前三段>.0/24`。
   - 详见 `docs/development-environment.md` 第 4.5 / 4.6 节。
3. **镜像版本必须明确**，禁止 `latest`；变更版本后同步更新
   `docs/development-environment.md`。
4. **持久化必须使用命名卷**，容器重建后数据必须保留。
5. **区分「容器启动」与「服务就绪」**，通过 `healthcheck` + 重试解决时序问题。
6. `depends_on` 必须配合 `condition: service_healthy`，不要只写服务名。
7. 修改 `docker-compose.yml` 前**必须先阅读现有内容**，保留有效配置，
   禁止整体覆盖重写。
8. 排查启动失败时：**读日志 → 定位服务 → 判断原因 → 最小修改 → 重启 → 再验证**。
   禁止「整个 compose 推倒重来」。

---

## 7. Secret 规范（零容忍）

### 7.1 绝对禁止

```text
❌ 代码中硬编码密码          API_KEY = "sk-xxxx"
❌ 硬编码 API Key / Token
❌ compose 中写真实口令      password: my-real-password
❌ 提交 .env
❌ 提交 LLM API Key
❌ 把真实凭据写进文档、日志、测试用例
```

### 7.2 正确做法

```text
✅ 所有凭据通过 .env 注入
✅ 仓库只提交 .env.example（全部为 change_me_* 占位符）
✅ .env 必须在 .gitignore 中
✅ 新增配置项时同步更新 .env.example
✅ 日志中输出连接信息时必须脱敏（不含口令）
```

### 7.3 提交前自检

```bash
# 确认 .env 未被追踪
git status --porcelain | grep -E '(^|/)\.env$' && echo "危险：.env 被追踪！"

# 搜索可疑凭据
grep -rInE '(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*["'"'"'][^"'"'"']{6,}' \
  --include='*.py' --include='*.yml' --include='*.yaml' --include='*.sh' --include='*.sql' .
```

---

## 8. 测试规范

1. **测试必须真实执行**，禁止只检查文件是否存在。
2. 单元测试（`-m unit`）**不得依赖外部服务**。
3. 冒烟测试（`-m smoke`）在服务未启动时必须**优雅跳过**，而不是误报失败。
4. 冒烟测试必须校验**数据内容**（字段、行数、关系），不只是连通性。
5. **禁止为了通过测试而删除或弱化测试。**
   测试失败时必须定位并修复根因。
6. 新增功能必须附带测试。

### 8.1 运行方式

```bash
python -m pytest                  # 全部
python -m pytest -m unit          # 单元测试
python -m pytest -m smoke -v      # 冒烟测试
bash scripts/health-check.sh      # 健康检查
docker compose config --quiet     # 编排语法校验
```

### 8.2 数据正确性断言（必须覆盖）

```text
orders.user_id     ∈ user.user_id
orders.product_id  ∈ product.product_id
payment.order_id   ∈ orders.order_id
refund.order_id    ∈ orders.order_id
product.price × orders.quantity ≈ orders.amount
user.register_time ≤ orders.create_time ≤ orders.pay_time ≤ refund.refund_time
行为漏斗  count(VIEW) > count(CLICK) > count(CART) > count(BUY)
```

---

## 9. Git 规范

### 9.1 Commit Message

格式：`<type>: <简短描述>`

| type | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档 |
| `chore` | 构建、配置、依赖等杂项 |
| `test` | 测试 |
| `refactor` | 重构（不改变行为） |
| `perf` | 性能优化 |

**禁止**的提交信息：

```text
❌ update
❌ test
❌ aaa
❌ fix
❌ final
❌ 111
❌ .
```

### 9.2 要求

1. 一次提交只做一件事，**提交历史必须清晰**。
2. 提交前必须：**代码可运行 + 测试通过 + 文档同步**。
3. 禁止提交：`.env`、真实数据、运行时产物、`__pycache__`、虚拟环境。
4. 禁止 `git push --force` 到共享分支。
5. 涉及架构的改动必须在提交信息中说明原因。

---

## 10. Agent 安全规范

> 本节约束**所有 AI Agent**（含未来的 LangGraph Data Agent），是项目的安全底线。

### 10.1 强制规则

```text
1. Agent 不允许直接拥有数据库管理员权限。
2. Agent SQL 默认只允许 SELECT。
3. Agent 必须知道指标定义。
4. Agent 必须知道表结构。
5. Agent 生成 SQL 后必须进行安全检查。
6. Agent 查询失败必须能够重新规划。
7. Agent 的最终回答必须能够说明数据来源。
8. Agent 不得伪造查询结果。
```

### 10.2 SQL 安全守卫（必须实现）

**允许：**

```sql
SELECT ... FROM ... [WHERE ...] [GROUP BY ...] [ORDER BY ...] [LIMIT n]
```

**必须拒绝：**

```text
INSERT / UPDATE / DELETE / DROP / TRUNCATE / ALTER / CREATE
GRANT / REVOKE / SET / USE / CALL / LOAD / EXPORT
多语句（分号拼接）
注释注入（-- , /* */）
INTO OUTFILE / INTO DUMPFILE
information_schema 之外的元数据探测（未授权时）
```

### 10.3 其他约束

1. **独立只读账号**：Agent 连接 Doris 必须使用专用只读账号，
   **不得复用 `root`**。
2. **强制 `LIMIT`**：所有生成 SQL 必须带行数上限。
3. **查询超时**：必须设置超时，避免拖垮集群。
4. **失败即失败**：查询失败必须返回真实错误，
   **严禁回退到编造数据或经验值**。
5. **来源可追溯**：回答必须给出所使用的库、表、时间范围与口径。
6. **权限最小化**：MCP / Tool 暴露的能力必须是最小必要集合。

---

## 11. 开发工作流

```text
1. 理解      阅读 README / AGENTS.md / docs / 当前 Sprint 任务书
2. 规划      明确本次改动范围与验收标准
3. 实现      小步修改，一次只做一件事
4. 测试      python -m pytest（新增功能必须补测试）
5. 验证      docker compose config / health-check.sh / 实际执行
6. 文档      同步更新 README / docs / Sprint 文档
7. 提交      git commit -m "<type>: <描述>"
```

### 11.1 发现问题时的处理

若发现**版本冲突 / 端口冲突 / 镜像不存在 / 架构冲突 / 配置冲突**：

```text
不要偷偷绕过。

问题：
原因：
影响：
建议方案：
```

- 可以**无风险修复**的：直接修复并在提交信息与文档中记录；
- 涉及**架构变化**的：**必须先暂停并等待确认**。

### 11.2 边界要求

属于后续 Sprint 的功能：

```text
❌ 不要提前实现
❌ 不要偷偷引入
❌ 不要为了"完整"而增加
```

---

## 12. 禁止事项

```text
❌ 一次性生成整个系统
❌ 提前实现后续 Sprint 的内容（Flink / Spark / Hive / Airflow / Iceberg /
   LangGraph / RAG / MCP / Vue / Spring Boot / FastAPI / Prometheus / Grafana / K8s）
❌ 偷偷增加技术栈（Redis / ES / ClickHouse / Trino / Milvus / PostgreSQL ...）
❌ 硬编码任何 Secret / 密码 / API Key / Token
❌ 提交 .env、真实数据、运行时产物
❌ 为了通过测试而删除或弱化测试
❌ 修改架构文档而不说明
❌ 未经阅读就整体覆盖已有文件（尤其 docker-compose.yml / AGENTS.md / README.md）
❌ 手工修改容器内数据库而不把 DDL 落盘到 sql/
❌ 使用 latest 镜像标签
❌ 容器间使用 localhost / 127.0.0.1
❌ 让 Agent 使用 root 账号或执行非 SELECT 语句
❌ 编造查询结果
```

---

## 13. 快速命令参考

```bash
# ---------- 环境 ----------
cp .env.example .env                 # 准备环境变量（必须先做）

# ---------- 生命周期 ----------
bash scripts/start.sh                # 启动（等价 docker compose up -d）
bash scripts/status.sh               # 状态（等价 docker compose ps）
bash scripts/stop.sh                 # 停止（保留数据卷）
bash scripts/stop.sh --volumes       # 停止并删除数据卷（会丢数据！）
bash scripts/health-check.sh         # 健康检查，失败 exit 1
bash scripts/verify-sprint-0.sh      # Sprint 0 全链路验收（config+up+ps+health+pytest）
bash scripts/verify-sprint-1.sh      # Sprint 1 实时链路验收（就绪+health+pytest+对账）
bash scripts/verify-sprint-1.sh --replay   # 重建链路并重放数据后再验收
bash scripts/cancel-flink-jobs.sh    # 取消全部 Flink 作业（重新提交作业前必跑）

# ---------- 编排校验 ----------
docker compose config                # 校验并打印解析后的配置
docker compose config --quiet        # 仅校验语法

# ---------- 数据生成 ----------
docker compose run --rm -T data-generator python -m src.generate_mysql_data --reset
docker compose run --rm -T data-generator python -m src.generate_events
# 说明：加 --reset 先清空业务表，避免重复运行造成主键冲突；
#       -T 关闭 TTY 分配（CI / 非交互环境必需）

# ---------- 测试 ----------
python -m pytest                     # 全部
python -m pytest -m unit             # 单元测试（无需容器）
python -m pytest -m smoke -v         # 冒烟测试（需要容器）

# ---------- 排查 ----------
docker compose logs --tail=100 <service>
docker compose ps --all
docker compose exec mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW DATABASES;"
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list
docker compose exec doris-be mysql -h 172.28.0.10 -P 9030 -uroot -e "SHOW BACKENDS;"
```

---

## 14. 文档维护要求

| 变更类型 | 必须同步更新 |
| --- | --- |
| 新增服务 / 修改镜像版本 | `docs/development-environment.md` |
| 修改架构 / 技术选型 | `docs/PROJECT_DESIGN_V1.md` |
| 修改端口 / 启动方式 | `README.md` |
| 修改表结构 | `sql/` + `README.md` |
| 修改事件格式 | `sql/metadata/kafka_topics.md` |
| 修改规范 | 本文件 `AGENTS.md` |
| 完成一个 Sprint | `docs/sprint/SPRINT_N.md` + `README.md` Roadmap |

---

## 15. 当前 Sprint 状态

| Sprint | 主题 | 状态 |
| --- | --- | --- |
| **0** | **项目初始化与基础数据环境** | ✅ **已完成并验收通过** |
| **1** | **Kafka + Flink + Doris 实时数仓** | ✅ **已完成并验收通过** |
| 2 | Spark + Hive + HDFS | ⏳ 下一步 |
| 3 | ODS / DWD / DWS / ADS | 未开始 |
| 4 | Airflow | 未开始 |
| 5 | Iceberg Lakehouse | 未开始 |
| 6 | Backend + Dashboard | 未开始 |
| 7 | LLM + Tool Calling | 未开始 |
| 8 | LangGraph Data Agent | 未开始 |
| 9 | RAG + Metadata | 未开始 |
| 10 | MCP | 未开始 |
| 11 | Data Quality + Monitoring | 未开始 |
| 12 | 测试 + 性能优化 | 未开始 |
| 13 | 毕业论文 + 答辩 | 未开始 |

### 15.1 Sprint 0 验收结果

已在**腾讯云服务器**（Ubuntu 24.04.2 LTS / Docker 29.8.1 / Compose v5.5.1）
完成全部容器验收：

```text
✅ docker compose config         通过
✅ docker compose up -d          5 个核心服务全部 healthy
✅ scripts/health-check.sh       5/5 [OK]，退出码 0
✅ python -m pytest              51 passed（24 单元 + 27 冒烟）
✅ 数据生成                      MySQL 5 表 + Kafka 4 Topic（31,660 条事件）
✅ 数据持久化                    down / up 后数据完全保留
```

逐项证据见
[`docs/sprint/SPRINT_0_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_0_VERIFICATION_STATUS.md)。

### 15.2 Sprint 1 验收结果

同一服务器上完成实时链路验收（Kafka → Flink → Doris）：

```text
✅ 8 个 Flink sink 作业各 1 个实例，8 个 Routine Load 全部 RUNNING、errorRows=0
✅ scripts/health-check.sh       11/11 [OK]（Sprint 0 五项 + Sprint 1 六项）
✅ python -m pytest -m smoke     74 passed（27 基础设施 + 47 实时链路）
✅ DWD 落库                      订单 6000 / 支付 5406 / 退款 254 / 行为 20000
✅ ADS 与 MySQL 精确对账         GMV 51,890,375.77 == 51,890,375.77（精确到分）
```

一键复现：`bash scripts/verify-sprint-1.sh`（详见
[`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md`](docs/sprint/SPRINT_1_VERIFICATION_STATUS.md)）。

### 15.3 边界要求

> **Sprint 1 已稳定，下一层是 Sprint 2（Spark + Hive + HDFS）。**
> 仍然禁止提前实现 Sprint 3 及以后的内容（Airflow / Iceberg / Agent /
> RAG / MCP / 前端 / 监控）。
> 指标口径以 [`sql/metadata/metrics.md`](sql/metadata/metrics.md) 为唯一权威，
> 离线链路（Sprint 3 起）必须产生同名同口径指标并与实时链路交叉对账。
