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
| Apache Spark | `3.5.7` | 离线计算（Sprint 2 引入） |
| Apache Hive Metastore | `3.1.3` | 湖仓表目录（Sprint 2 引入，只跑 Metastore） |
| FastAPI + uvicorn | `0.141.1` / `0.54.0` | 只读数据服务（Sprint 6 引入） |
| openai（SDK） | `2.54.0` | 调用 DeepSeek（OpenAI 兼容协议，Tool Calls）（Sprint 7 引入） |
| DeepSeek API | `deepseek-flash`（模型名） | 数据问答 Agent 的 LLM（Sprint 7 引入） |
| Nginx | `1.24.0`（apt） | 静态托管 + 反向代理（Sprint 6 引入；TLS 曾启用后按实测停用，见 15.7） |
| Vue + ECharts | `3.5.13` / `5.6.0`（本地 vendor，无构建步骤） | 数据看板（Sprint 6 引入） |
| systemd | 系统自带 | 守护 `data-platform-api` / `data-platform-agent`（Sprint 6/7 引入） |
| Python（宿主机） | `3.12.3` | 数据服务 / Agent / 调度（宿主 venv `.venv`、`.venv-agent`、`.venv-airflow`） |
| Python（容器） | `3.13.14` | 仅 data-generator 容器（`python:3.13.14-slim-bookworm`） |
| Apache Airflow | `3.3.2` | 离线流水线调度（Sprint 4 引入；元数据库用 MySQL，独立 venv） |
| pytest | 9.x | 测试 |

> ⚠️ **Python 版本有两条线，不要合并成一句**（Sprint 4 修正）：
> 原表写「Python | 3.13 | 数据生成器 / 数据服务」是**错的**。
> 宿主机实测 `python3 --version` = **3.12.3**（Ubuntu 24.04 自带），
> 数据服务、Agent 与 Airflow 都跑在宿主机的 venv 上；
> `3.13` 只属于 data-generator 容器。混为一谈会在排查
> "某个包在本地能装、在服务器装不上"时把人引向错误方向。

> **部署分层原则**（Sprint 6 起）：
> **数据层**（MySQL/Kafka/MinIO/Doris/Flink）继续用 Docker Compose；
> **服务层**（Nginx + FastAPI + 前端静态文件）直接装在宿主机
> （apt + systemd + Nginx），不进 Docker —— 只有两个进程，
> 用 `journalctl` / `nginx -t` / `systemctl restart` 管理更直接。

### 2.2 后续 Sprint 引入

Iceberg（S5）、
LangGraph（S8）、RAG（S9）、MCP（S10）、Prometheus + Grafana（S11）。

> Sprint 6（数据后台 + 前后端）已按项目负责人要求**前移**到 Sprint 2~5 之前，
> 理由是让数据"可访问、可验收"并为智能层预留只读接口，详见
> [`docs/sprint/SPRINT_6.md`](docs/sprint/SPRINT_6.md) 第 1.1 节。

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
| `services/api/` | 只读数据服务（FastAPI，Sprint 6） | 只允许 SELECT；使用只读账号 `agent_ro` |
| `services/agent/` | 数据问答 Agent（FastAPI + LLM，Sprint 7） | **不允许持有数据库凭据**；只能经 HTTP 调 `services/api` |
| `services/web/` | 数据看板前端（Vue + ECharts，无构建步骤，Sprint 6） | 不引用外部 CDN；`vendor/` 不入 Git |
| `deploy/` | 宿主机部署配置（Nginx / systemd，Sprint 6） | 只放配置模板，不在服务器上直接改 |
| `airflow/dags/` | Airflow DAG（Sprint 4） | 只放 DAG 代码；`airflow/` 下的日志/口令/配置属机器状态，已在 `.gitignore` |
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

# ---------- 离线链路（Sprint 2：Spark + Hive + 湖仓） ----------
bash scripts/init-lakehouse.sh       # 初始化（元数据库/镜像/服务/Metastore schema/湖仓目录）
bash scripts/submit-offline-job.sh   # 抽取 MySQL → Parquet(S3A) → Hive 外部表（作业内自带逐表对账）
bash scripts/verify-sprint-2.sh      # 离线链路验收（8 步）
bash scripts/spark-sql.sh -e 'SHOW TABLES IN lakehouse;'   # 用一次性容器查湖仓表

# ---------- 离线分层 + 批流对账（Sprint 3） ----------
# ⚠️ 内存受限：一律走错峰模式（暂停 Flink 栈 → 跑批 → 自动恢复并自检）
bash scripts/batch-mode.sh                     # 全链路：抽取→DWD→DWS→ADS→对账→装载
bash scripts/batch-mode.sh --stage ads          # 只跑一层
bash scripts/batch-mode.sh --restore-only       # 只恢复实时链路（上次异常中断后用）
bash scripts/run-batch-pipeline.sh --list       # 查看阶段顺序与依赖
bash scripts/load-batch-to-doris.sh             # 离线结果装载进 Doris（S3() TVF）
bash scripts/verify-sprint-3.sh                 # 离线分层 + 批流对账验收（8 步）
bash scripts/setup-swap.sh                      # 配置 swap 兜底（内存事故后引入）
bash scripts/spark-sql.sh -e 'SELECT * FROM lakehouse.ads_reconcile_summary;'  # 对账结论

# ---------- 服务层（Sprint 6：Nginx + FastAPI + 前端） ----------
bash scripts/install-web.sh          # 首次安装（nginx/依赖/只读账号/systemd/自签证书）
bash scripts/deploy-web.sh           # 更新代码后同步配置并重启服务
bash scripts/verify-sprint-6.sh      # 服务层验收（7 步：状态/路径/对账/安全/测试）
systemctl status data-platform-api   # 数据服务状态
journalctl -u data-platform-api -n 100 --no-pager   # 数据服务日志
nginx -t && systemctl reload nginx   # 站点配置校验与重载
curl -s http://127.0.0.1/data/api/health             # 接口健康检查（当前为明文）

# ---------- 站点协议（当前：明文 HTTP，未启用 TLS）----------
# 实测结论：关掉客户端 VPN 代理后明文 HTTP 30/30 正常，
# 当初那个空 502 的改写在 VPN 出口路径上，不在这条 IP 直连路径上。
# 所以当前走明文；**演示时不要挂 VPN / 代理**。
grep -E '^SITE_SCHEME=' .env                 # 当前值 http
# 启用 TLS 时（等注册域名并备案之后）：
#   sudo SERVER_IP=<服务器IP> bash scripts/setup-tls.sh   # 生成证书（幂等）
#   把 443 的 server 块加回 deploy/nginx/data-platform.conf（见 commit 347dfdd）
#   并把 .env 的 SITE_SCHEME 改为 https
# !! 重新启用时注意 http2 的写法随 nginx 版本变化 !!：本项目钉 1.24.0，
#    必须写 `listen 443 ssl http2;`；`http2 on;` 是 1.25.1+ 的语法，会让 nginx -t 直接失败。

# ---------- Airflow 调度（Sprint 4） ----------
bash scripts/install-airflow.sh       # 装 Airflow 3.3.2（独立 venv + apt 编译依赖）
bash scripts/setup-airflow-db.sh      # 供给 MySQL 元数据库（库 airflow + 专用账号）
bash scripts/deploy-airflow.sh        # 生成配置 → 迁移 → 装 systemd → 启动 → 自检
systemctl status data-platform-airflow-scheduler    # 调度器状态
journalctl -u data-platform-airflow-scheduler -n 100 --no-pager
# !! 人工敲 airflow 命令一律用包装脚本，不要用裸 airflow !!
#    裸命令不会加载 airflow.env，会静默落到 sqlite 并报
#    "Database migration required"（极具误导性）。
bash scripts/airflow.sh dags list
bash scripts/airflow.sh tasks list offline_lakehouse_pipeline
bash scripts/airflow.sh dags trigger offline_lakehouse_pipeline   # 手动跑一次
bash scripts/airflow.sh dags list-runs -d offline_lakehouse_pipeline
bash scripts/airflow.sh dags list-import-errors
# 登录口令（明文 JSON，0600；仅 root 可读）：
#   cat /opt/data-platform/airflow/simple_auth_passwords.json

# ---------- 数据问答 Agent（Sprint 7：LLM + Tool Calling） ----------
bash scripts/deploy-agent.sh          # 部署（幂等；会一并重启数据服务）
bash scripts/verify-sprint-7.sh       # 验收（8 步 49 项：守卫/网关/工具/端到端问答）
systemctl status data-platform-agent  # Agent 状态
journalctl -u data-platform-agent -n 100 --no-pager  # Agent 日志（含每次工具调用）
curl -s http://127.0.0.1:8100/health | python3 -m json.tool   # 是否已配置 LLM
curl -s http://127.0.0.1:8100/tools                           # Agent 能力面（4 个工具）
curl -s -X POST http://127.0.0.1:8100/ask \
     -H 'Content-Type: application/json' \
     -d '{"question":"最近一周每天的 GMV 是多少？"}'           # 提问
# 配置 LLM Key：在 .env 设 LLM_API_KEY 后 systemctl restart data-platform-agent
# 详见 services/agent/README.md

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
| **6** | **数据后台 + 前后端（服务层）** | ✅ **已完成并验收通过**（顺序前移，见 2.2 节说明） |
| **2** | **Spark + Hive + 湖仓存储（离线链路）** | ✅ **已完成并验收通过**（HDFS 因内存不足改用 S3A，见 SPRINT_2.md 2.2） |
| **3** | **ODS / DWD / DWS / ADS（离线分层 + 批流交叉对账）** | ✅ **已完成并验收通过**（11458 个分钟窗口零差异，见 SPRINT_3.md） |
| **7** | **LLM + Tool Calling（数据问答 Agent）** | ✅ **已完成并验收通过**（验收 49/49，见 SPRINT_7.md） |
| 4 | Airflow 调度 + 流量域归档 | 🔄 **进行中**（Airflow `3.3.2`，元数据库用 MySQL，见 SPRINT_4.md） |
| 5 | Iceberg Lakehouse | 未开始 |
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

### 15.3 Sprint 6 验收结果（服务层)

数据后台与前后端**直接装在宿主机**（不进 Docker）：

```text
✅ bash scripts/verify-sprint-6.sh   7/7 PASS
✅ 访问地址                          http://36.151.150.140/data/   ← 协议见 15.7
✅ 调度 UI                           http://36.151.150.140/airflow/（Sprint 4）
✅ 接口文档                          http://36.151.150.140/data/api/docs
✅ 只读账号                          agent_ro（写操作被 Doris 拒绝：Access denied CREATE）
✅ 指标对账                          API GMV == MySQL GMV（51,890,375.77，精确到分）
✅ 自动化测试                        pytest 74 单元（SQL 守卫 + 口径解析）+ 21 接口冒烟
✅ 真实浏览器验收                    6 个页面渲染正常，图表 canvas 正常
```

### 15.4 Sprint 2 验收结果（离线链路）

Spark + Hive 直接以容器形式接入既有编排（湖仓存储用 MinIO/S3A）：

```text
✅ bash scripts/verify-sprint-2.sh   8/8 PASS
    服务状态 / Spark 集群 / ODS 表 / 行数对账 / 存储落地 / 类型正确性 / 幂等性 / 回归
✅ 逐表对账                          ods_user 1200 / product 600 / orders 6000
                                    payment 5406 / refund 254 —— 与 MySQL 精确一致
✅ 存储落地                          25 个 Parquet 对象位于 s3a://lakehouse/warehouse/ods
✅ 类型正确                          amount = decimal(18,2)，ODS 层无 double/float
✅ 幂等                              重跑抽取作业后行数不变
✅ 回归                              实时链路 health-check 11/11、数据服务 /health 正常
```

一键复现：`bash scripts/init-lakehouse.sh && bash scripts/submit-offline-job.sh &&
bash scripts/verify-sprint-2.sh`（详见
[`docs/sprint/SPRINT_2.md`](docs/sprint/SPRINT_2.md)，含 11 条版本/时序踩坑记录）。

### 15.5 Sprint 3 验收结果（离线分层 + 批流交叉对账）

在 Sprint 2 的 ODS 之上建成 ODS → DWD → DWS → ADS 四层，
并用**逐窗口对账**证明离线与实时算的是同一个数：

```text
✅ bash scripts/verify-sprint-3.sh   8/8 PASS
    表清单 / 层间行数 / 类型正确 / 幂等 / 批流对账 / 服务装载 / 回归 / 自动化测试
✅ 层间行数                          DWD == ODS 逐表相等（1200/600/6000/5406/254）
✅ 批流对账                          11458 个分钟窗口，**不一致 0 个**
                                    GMV 实时 51,890,375.77 == 离线 51,890,375.77（精确到分）
✅ 服务装载                          Doris lakehouse_ads 6 张表行数 == 湖仓；
                                    agent_ro 可查、写操作被 Doris 拒绝
✅ 回归                              实时链路 health-check 11/11、数据服务 /health 正常
✅ 自动化测试                        pytest **172 passed**（含离线接口与对账契约用例）
✅ 真实浏览器验收                    7 个页面渲染正常（新增「离线与对账」页）
```

一键复现：`bash scripts/batch-mode.sh && bash scripts/verify-sprint-3.sh`
（详见 [`docs/sprint/SPRINT_3.md`](docs/sprint/SPRINT_3.md)，
含 10 条踩坑记录与一次**整机失联事故**的完整复盘）。

> ⚠️ **内存约束（Sprint 3 事故后成为硬规范）**：
> 本机实时链路常驻约 13.7 GB，离线 Spark 驱动跑在宿主机（client 模式）。
> **禁止**在实时链路运行时直接跑离线流水线 —— 必须先经
> `scripts/lib/memory-guard.sh` 的内存闸门。推荐一律走错峰模式：
> `bash scripts/batch-mode.sh`（暂停 Flink 栈 → 跑批 → 自动恢复并自检）。

### 15.6 Sprint 7 验收结果（LLM + Tool Calling：数据问答 Agent）

新增一个**只读查询节点**与一个**独立部署的问答 Agent**：

```text
✅ bash scripts/verify-sprint-7.sh   49/49 通过，0 失败，0 跳过
    服务状态 / Agent 自身 / 只读查询节点 / 网关契约 / Agent 能力面 /
    端到端问答 / 看板接入 / 自动化测试
✅ 四类攻击全部被拒（且原因正确）
    DELETE → NOT_SELECT；未授权表 → TABLE_NOT_ALLOWED；
    多语句 → MULTI_STATEMENT；注释注入 → COMMENT
✅ 强制 LIMIT                       请求 5 行 → executed_sql 带 LIMIT 5、row_count=5
✅ 网关契约                         POST /data/api/query 200；GET 同路径 403；
                                    POST /data/api/overview 仍 403（未被误伤）
✅ 端到端问答                       实测三问全部正确，且回答附
                                    tables / executed_sql / steps（可逐条核对）
✅ 自动化测试                       Agent 单元测试 19 passed；/query 冒烟测试 21 passed
```

**架构约束（Sprint 7 起成为硬规范）**：

> **Agent 进程里不允许有数据库凭据。**
> 它只能经 HTTP 调只读数据服务取数，因此「Agent 不得拥有数据库管理员权限」
> 由**进程边界**保证，而不是靠代码自觉。
> 新增可查表必须同时改两处：
> `services/api/app/sqlguard.py` 的 `BUSINESS_TABLES`（权限边界）
> 与 `services/agent/app/agent.py` 系统提示词的「数据地图」（模型认知）——
> 只改一处会出现"模型知道有表却不知道有哪些列"。

一键复现：`bash scripts/deploy-agent.sh && bash scripts/verify-sprint-7.sh`
（详见 [`docs/sprint/SPRINT_7.md`](docs/sprint/SPRINT_7.md)，
含 7 条踩坑记录；配置操作见 [`services/agent/README.md`](services/agent/README.md)）。

### 15.7 公网偶发 502 的排查结论（HTTPS 曾用于规避，当前停用）

**现象**：客户端经公网访问 `http://<ip>/data/*`，约 15~40% 的请求返回 **502**。

**判据（这一节的价值在于排查方法，不在于结论）**：

```text
1. 那个 502 **没有 `Server` 响应头、响应体为空**
     → nginx 发出的 502 必然带 `Server: nginx/1.24.0` 与一段 HTML 错误页，
       所以它**不是 nginx 发的**
2. TCP 80 十次连接全部成功（84 ms） → 不是安全组/防火墙丢包
3. 服务端本机 curl 十次全部 200；nginx 访问日志与 error 日志里没有任何 502
4. 内核 ListenOverflows = 0 → 不是 accept 队列溢出
结论：请求在**到达服务器之前**就被中间设备改写了（明文 HTTP 可被改写）

★ 5. **补做的定位**：关掉客户端 VPN 代理后重新测，
     明文 HTTP **30/30 全部正常**。
     → 改写的中间设备在 **VPN 的出口路径上**，不在这条 IP 直连路径上。
```

> **方法比结论更值得记**：一个响应头的有无，把嫌疑从"我们的服务"
> 直接缩小到"链路中间"。而最后那条"换个路径再测一次"，
> 把一个"必须上 TLS"的结论修正成了"当前网络下明文即可"。
> **结论要跟着证据走，不要跟着上一次的结论走。**

**处置经过**：

```text
第一步（当时）  站点加 443 + 自签证书，80 只留探针并 302 跳转
                公网实测 64/64 全部 200 → 现象确实消失
第二步（其后）  项目负责人决定**暂不使用 TLS**：
                按 IP 访问只能自签，浏览器每个会话都要点一次
                "继续前往"，演示观感不好；
                等注册域名 + 申请证书 + 完成备案后再启用。
                实测（上表第 5 条）支持这个决定。
当前状态        单一 HTTP 站点（:80），全部业务直接提供，
                不再监听 443；443 配置与 setup-tls.sh 保留在仓库，
                需要时按 commit 347dfdd 恢复。
```

**硬规范**：

> 1. **协议由配置决定，不由证书探测决定**。`lib/common.sh` 的
>    `site_scheme()` 读 `.env` 的 `SITE_SCHEME`（当前 `http`）。
>    理由：**配置表达意图，证书只是产物** —— 机器上有证书，
>    不等于"现在想用 https"。启用 TLS 时改这一处即可。
> 2. **验收脚本不得硬编码协议**。一律用 `site_base()` / `site_scheme()` /
>    `curl_site()`。硬编码协议会让"环境差异"被误报成"功能缺陷"。
> 3. **改 Nginx 配置必须可回滚**。`deploy-web.sh` 会先备份站点文件，
>    `nginx -t` 失败时自动还原 —— 否则机器会停在"文件是坏的、
>    进程还在跑旧的"这种最尴尬的状态，下一次 nginx 重启就直接起不来。
> 4. **若日后重新启用 TLS，注意 `http2` 的写法随 nginx 版本变化**：
>    本项目钉 `1.24.0`，必须写 `listen 443 ssl http2;`；
>    `http2 on;` 是 nginx **1.25.1+** 的语法，在 1.24.0 上会让
>    `nginx -t` 报 "unknown directive" 而站点起不来。
> 5. **明文形态下有一个前提**：演示时**不要挂 VPN / 代理**，
>    否则那个偶发 502 可能回来。此时要么关代理，要么临时启用 TLS。

### 15.8 边界要求

> **Sprint 0 / 1 / 2 / 3 / 6 / 7 已稳定，下一层是 Sprint 4（Airflow 调度）。**
> 禁止提前实现 Sprint 5 及以后的内容（Iceberg / LangGraph / RAG / MCP / 监控）。
> 指标口径以 [`sql/metadata/metrics.md`](sql/metadata/metrics.md) 为唯一权威，
> 实时链路、离线链路与 Agent 都必须引用该口径，不得自建第二份定义。
> **Agent 侧额外约束**：SQL 只能经过 `POST /query`（受守卫与只读账号约束），
> 禁止让 Agent 直连数据库或持有凭据；回答必须能给出 `tables` 与 `executed_sql`。
>
> **已知设计缺口（必须写进论文，不得隐瞒）**：
> 流量域（UV / PV / 转化率）的事实来源只有 Kafka 事件，MySQL 无对应业务表，
> 因此离线侧**无源可算**，Sprint 3 未对其对账；Sprint 7 的 Agent 在回答里
> 也会如实说明"这类指标不在对账范围内"。
> 补齐路径：Sprint 4 增加"Kafka → 湖仓 ODS"归档作业后再纳入对账。
> **禁止**为了"看起来完整"而伪造离线流量指标。
