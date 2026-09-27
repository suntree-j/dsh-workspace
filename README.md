# dsh-workspace

DeepSeek Harness 公开工作区（suntree-j）。

**主项目：[`intelligent-data-platform/`](intelligent-data-platform/) ——
《基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台》（毕业设计）**

```text
C:\Users\jsy28\Desktop\dsh-workspace
https://github.com/suntree-j/dsh-workspace
```

---

## 这个项目做了什么

面向电商场景的**批流一体**数据平台：同一条业务数据分别走实时与离线两条链路，
再用**逐窗口对账**证明两条链路算出来的是同一个数，最后让 AI Agent 在这份可信数据上回答问题。

```text
实时链路   MySQL → Kafka → Flink → Doris         （秒级指标）
离线链路   MySQL/Kafka → Spark → Parquet/Iceberg → Hive Metastore
存储       MinIO（S3A）
调度       Airflow 3.3.2
服务       Nginx + FastAPI（只读）+ Vue 看板
智能       LLM Tool Calling → LangGraph 图式 Agent → 元数据/口径检索（RAG）→ MCP
治理       25 条数据质量校验 + Prometheus/Grafana
```

核心原则：**先工程，再智能。**

```text
数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据
```

### 在线演示（部署在腾讯云轻量服务器）

| 入口 | 地址 |
| --- | --- |
| 数据看板 | http://36.151.150.140/data/ |
| 智能问答 | http://36.151.150.140/data/#/ask |
| 只读接口文档 | http://36.151.150.140/data/api/docs |
| 调度 UI（Airflow） | http://36.151.150.140/airflow/ |
| 监控看板（Grafana） | http://36.151.150.140/grafana/ |
| 指标端点（Prometheus） | http://36.151.150.140/metrics/ |

> ⚠️ 当前是**明文 HTTP**（未启用 TLS，等注册域名并备案后再上）。
> **演示时请不要挂 VPN / 代理** —— 那份偶发 502 的改写发生在 VPN 出口路径上，
> 完整判据见 `AGENTS.md` §15.7。

---

## 当前进度：Sprint 0 ~ 12 **全部完成并在服务器上验收通过**

| Sprint | 主题 | 验收结果（服务器实测） |
| --- | --- | --- |
| 0 | 项目初始化与基础数据环境 | 5/5 健康、pytest 51 passed、数据持久化验证 |
| 1 | Kafka + Flink + Doris 实时数仓 | 8 个 Flink 作业各 1 实例、Routine Load errorRows=0、GMV 与 MySQL 精确一致 |
| 6 | 数据后台 + 前后端（服务层） | `verify-sprint-6.sh` 7/7；只读账号 `agent_ro` 写操作被拒 |
| 2 | Spark + Hive + 湖仓（S3A） | `verify-sprint-2.sh` 8/8；逐表行数与 MySQL 一致 |
| 3 | ODS/DWD/DWS/ADS + 批流对账 | **32/0/0**；11458 个分钟窗口**不一致 0** |
| 7 | LLM + Tool Calling（问答 Agent） | **49/0/0**；四类攻击全部被拒且原因正确 |
| 4 | Airflow 调度 + 流量域归档 | **40/0/0**；归档 20000 行 == Kafka offset（零丢失） |
| 5 | Iceberg Lakehouse + 流量域分层 | 迁移 **23 张表 70/70**；流量域 DWD 21/21、DWS 20/20、ADS 36/36、对账 **10/10**（19643 窗口不一致 0） |
| 8 | LangGraph Data Agent | **71/0/0**；反思重试真实触发（`retries=2`） |
| 9 | RAG + Metadata（词法检索） | **59/0/0**；语料 65 篇、零新增依赖、未引入向量库 |
| 10 | MCP | **84/0/0**；MCP 路径与直接 HTTP 路径结果**逐字段 IDENTICAL** |
| 11 | 数据质量 + 监控 | **61/0/0**；25 条校验、失败路径实测 exit 1；Prometheus/Grafana 合计 512 MiB |
| 12 | 测试 + 性能优化 | **32/0/0**；全量 pytest **353 passed / 0 failed**；四类性能基线已实测 |
| 13 | 毕业论文 + 答辩 | 🔄 材料整理中（见 `docs/thesis/`） |

### 几个可以当场核对的结果

```text
批流对账（交易域）   11458 个分钟窗口，不一致 0；GMV 实时 == 离线 == 51,890,375.77（精确到分）
批流对账（流量域）   19643 个窗口，不一致 0、单边窗口 0；PV 两边都是 19998
数据质量             25 条校验（行数/主键/非空/枚举/层间/金额/新鲜度），失败路径实测退出码 1
Agent                回答附 tables / executed_sql / 命中口径来源（文件:行号）/ 图执行路径
性能基线            只读接口点查 8.5ms、聚合 8.3ms、关联 11.7ms；Agent /ask 中位数 19.2s（其中 LLM 占 99.9%）
```

---

## 数据说明（重要）

**平台里的数据是程序生成的合成数据，不是真实电商数据。**

- 生成器：`intelligent-data-platform/data-generator/`（Python + Faker `zh_CN` + `random`）
- **可复现**：随机种子由 `GEN_RANDOM_SEED` 控制（默认 `20260926`），同一种子生成同一份数据
- **不是"随便随机"**：生成时强制满足业务约束与时间因果，因此可以逐表对账 ——
  `订单.user_id ∈ 用户表`、`订单.product_id ∈ 商品表`、`支付/退款.order_id ∈ 订单表`、
  `商品单价 × 数量 ≈ 订单金额`（`Decimal` 计算）、
  `注册时间 ≤ 下单 ≤ 支付 ≤ 退款`
- 规模：用户 1200 / 商品 600 / 订单 6000 / 支付 5406 / 退款 254 / 行为事件 20000
- ID 段：用户 `1001+`、商品 `2001+`、订单 `10001+`、支付 `30001+`、退款 `40001+`

> 论文与答辩中应**如实说明数据为合成数据**，并说明"可复现 + 满足业务约束"正是
> 它能支撑逐窗口对账的原因；不要写成真实业务数据。

---

## 快速开始

```bash
cd intelligent-data-platform
cp .env.example .env          # 全部为 change_me_* 占位符；真实凭据只在服务器上

docker compose config         # 编排语法校验
docker compose up -d          # 启动数据层（MySQL/Kafka/MinIO/Doris/Flink/...）
bash scripts/health-check.sh  # 健康检查，失败 exit 1

# 一键验收（按 Sprint 编号）
bash scripts/verify-sprint-2.sh    # 离线链路
bash scripts/batch-mode.sh         # 错峰跑批（暂停 Flink → 跑批 → 自动恢复）
bash scripts/verify-sprint-3.sh    # 分层 + 批流对账
bash scripts/verify-sprint-7.sh    # 数据问答 Agent
bash scripts/verify-sprint-11.sh   # 数据质量 + 监控
python -m pytest                   # 全部测试
```

> ⚠️ **内存约束**：本机实时链路常驻约 13.7 GB，离线 Spark 驱动跑在宿主机。
> 任何批处理**必须**走 `bash scripts/batch-mode.sh`（错峰模式），
> 它带内存闸门与"不叠加作业"保护 —— 这是 Sprint 3 一次整机失联事故后立的硬规范。

完整说明见 [`intelligent-data-platform/README.md`](intelligent-data-platform/README.md)，
开发规范见 [`intelligent-data-platform/AGENTS.md`](intelligent-data-platform/AGENTS.md)。

### 部署目标服务器

| 项目 | 值 |
| --- | --- |
| 实例 | 腾讯云轻量应用服务器 `lavm-txyjj7xzr7` |
| 规格 | 4 核 / 16 GB / 100 GB SSD |
| 系统 | Ubuntu 24.04.2 LTS |
| 公网 IP | `36.151.150.140` |
| 内网 IP | `172.16.0.10` |

服务器环境部署（Docker 安装 + 镜像源配置）见 [`.deploy/README.md`](.deploy/README.md)。

---

## 工作区约定

1. **行尾统一**：`.gitattributes` 强制 `*.sh` / `*.sql` / `*.yml` 为 LF。
2. **不提交敏感信息**：`.env` 已被忽略，仓库中只保留 `.env.example`；**真实凭据只存在于服务器**。
3. **不提交运行时产物**：虚拟环境、缓存、数据快照、`*.jar` 均已忽略。
4. **提交信息规范**：`<type>: <描述>`（feat / fix / docs / chore / test / refactor）。

> 一个踩过的坑值得写在这里：**机器状态（`.env` / venv / 挂载的 jar）不在仓库里，
> 因此任何"整树替换式"的同步都会把它们删掉。** 本项目真实发生过一次，
> 恢复过程与硬规范见 `AGENTS.md` §15.11。

各子项目可在自己的 `AGENTS.md` 中定义更细的规范。
