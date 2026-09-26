# Sprint 4 设计：Airflow 调度 + 流量域归档

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 前置：Sprint 0 / 1 / 2 / 3 / 6 / 7 均已验收通过
> 状态：**进行中**

---

## 1. Sprint 4 目标

Sprint 3 结束时留下两个问题，Sprint 4 一并收口：

```text
问题 1  离线流水线只能**手工触发**（bash scripts/batch-mode.sh）
        → 无法无人值守，也没有"哪一层失败了、重试了几次"的记录
问题 2  流量域（UV / PV / 转化率）在离线侧**无源可算**
        → MySQL 没有行为事实表，离线链路只覆盖交易域
```

因此 Sprint 4 做两件事：

```text
A. 编排   用 Airflow 把离线流水线变成按依赖调度的 DAG，含内存闸门与实时链路暂停/恢复
B. 补源   新增 Kafka → 湖仓 ODS 的**行为事件归档**，让流量域也能离线计算并纳入对账
```

> **为什么 B 必须由 A 来带**：归档作业同样是 Spark 作业，同样要吃内存。
> 如果归档是手工跑的，问题 2 只是从"没有源"变成"有源但要记得手工跑"。
> 两者合在一起才构成"无人值守的批流一体"。

### 1.1 明确不做

```text
❌ Iceberg（Sprint 5）
❌ LangGraph / RAG / MCP（Sprint 8/9/10）
❌ Prometheus / Grafana 监控（Sprint 11）
❌ 告警通知（邮件 / 钉钉）—— Sprint 11 的可观测性一并做
❌ 把 Flink 作业交给 Airflow 管 —— 实时链路是**常驻**的，不是"跑一次"的任务
```

---

## 2. 版本与环境（均已实测查证，未使用 latest）

| 组件 | 版本 | 查证方式与结论 |
| --- | --- | --- |
| Apache Airflow | `3.3.2` | PyPI 官方 JSON `info.version`；`requires_python = !=3.15,>=3.10` |
| Python（宿主机） | `3.12.3` | `python3 --version` 实测（Ubuntu 24.04 自带） |
| MySQL（元数据库） | `8.4.11` | 官方文档：Airflow 支持 `MySQL: 8.0, 8.4`；**不支持 MariaDB**，本项目是真 MySQL |
| pip 索引 | 清华镜像 | 已确认 `apache-airflow` 3.x 全系（含 3.3.2）在镜像上有 wheel |
| Airflow 安装位置 | `/opt/data-platform/.venv-airflow` | 与 `.venv`（数据服务）、`.venv-agent`（Agent）同一命名约定 |

> ⚠️ **AGENTS.md 第 2.1 节 "Python 3.13" 是错的，Sprint 4 一并修正**：
> `3.13` 只适用于 **data-generator 容器**（`FROM python:3.13.14-slim-bookworm`）；
> **数据服务 / Agent / Airflow** 跑在宿主机的 venv 上，是 **3.12.3**。
> 原表述把两个运行时合并成一句，排查版本问题时会被误导。

### 2.1 为什么不用 PostgreSQL

Airflow 官方"首选"是 PostgreSQL，但：

```text
AGENTS.md 2.3 未经批准禁止引入：Redis / Elasticsearch / ClickHouse / Trino / Milvus / PostgreSQL
```

MySQL 8.4 是 Airflow **明确支持**的后端，而服务器上**已经有**这个 MySQL。
为零成本满足需求而引入一个被规范禁止的新组件，是把"技术选型"当成"照抄官方推荐"。
**结论：用 MySQL，新增独立库 `airflow`。**

### 2.2 为什么用 LocalExecutor

```text
LocalExecutor   单机多进程，不需要外部消息队列          ← 选它
CeleryExecutor  需要 Redis/RabbitMQ 做 broker（两者都被禁止）
KubernetesExecutor  需要 K8s（被禁止）
```

本机 4 核、单机部署，LocalExecutor 是唯一不引入新组件又能并发的选择。

---

## 3. 部署形态

沿用 Sprint 6 建立的**部署分层原则**：

```text
数据层（MySQL/Kafka/MinIO/Doris/Flink）→ Docker Compose
服务层（Nginx + FastAPI + 前端 + Agent）→ 宿主机 apt + systemd
Sprint 4：Airflow → **宿主机 + systemd**（新增一层"编排层"，与数据层解耦）
```

### 3.1 为什么 Airflow 装宿主机而不是 Docker

**决定性理由：Airflow 要调的就是人手工跑的那些脚本。**

```bash
# 人手工执行（Sprint 3 建立的入口）
bash scripts/batch-mode.sh

# Airflow 执行（同一个入口，只是换成按 stage 拆开）
bash scripts/run-batch-pipeline.sh --stage ods
```

如果 Airflow 跑在容器里，它要完成同样的事就必须：

```text
❌ 挂载 docker.sock（等于把宿主机容器控制权交给一个 Web 应用）
❌ 把 scripts/ 与 Spark 客户端挂进容器（路径与用户映射问题）
❌ 容器内调宿主机 docker 的路径/权限差异，行为与人手工跑**不一致**
```

最后一条是根本问题：**调度应该复用人工入口，而不是平行实现一套**。
两者行为一旦分叉，就再也说不清"我手工跑是好的，为什么调度跑就错"。

### 3.2 为什么单独一个 venv

Airflow 是本项目依赖最多的组件（约 200 个包，含 SQLAlchemy / Flask / 各 provider）。
与只读数据服务共用 `.venv` 意味着 **一次 Airflow 升级就可能打断线上看板**。
这与 `.venv-agent` 分出来的理由完全一致。

### 3.3 systemd 单元

Airflow 3 的进程模型与 2.x 不同，**常驻进程是分开的**：

| 单元 | 作用 | 是否常驻 |
| --- | --- | --- |
| `data-platform-airflow-apiserver.service` | Web UI + REST API | 是 |
| `data-platform-airflow-scheduler.service` | 解析 DAG、调度任务 | 是 |
| `data-platform-airflow-dagprocessor.service` | 独立解析 DAG 文件 | 是 |
| ~~`triggerer`~~ | 仅 deferrable operator 需要 | **不启动**（本项目用不到，省一份常驻内存） |

> `triggerer` 不启动是**有意的取舍**：它只为"可延迟算子"服务，
> 而本项目的任务全是短时 shell/Spark 任务。少一个常驻进程，
> 在 3 GB 可用内存的机器上是实打实的收益。

---

## 4. 内存预算（本 Sprint 最大的约束）

Sprint 3 的事故（整机失联）已经证明：**这台机器不能在实时链路运行时直接跑批**。

实测基线（Sprint 4 开工时）：

```text
总内存        15 GiB
已用          12 GiB
可用          3.0 GiB        ← 注意这个数
swap          4 GiB（已用 955 MiB）
核数          4
```

而批处理的内存闸门是：

```bash
MIN_AVAILABLE_MB_FOR_BATCH=3000     # scripts/lib/memory-guard.sh
```

**问题**：可用内存 3.0 GiB（≈3072 MB）**刚好在闸门线上**。
Airflow 常驻约 1 GB 之后，可用会降到 ~2 GB ——
**直接后果是 `run-batch-pipeline.sh` 会拒绝启动批处理**，
表现为"Airflow 里的任务一跑就失败，报可用内存不足"。

**解法（不是调低闸门，而是按原设计错峰）**：

```text
batch-mode.sh 暂停实时链路 → 释放约 2.8 GB
  → 可用 ≈ 2.0 + 2.8 = 4.8 GB
  → 减去 Airflow 常驻 ~1 GB
  → 可用 ≈ 3.8 GB  >  3000 MB 闸门  ✅
```

所以 DAG **必须**是"暂停 → 跑批 → 恢复"三段式，而不是把跑批当成一个普通任务。
这也正好是 `batch-mode.sh` 已经实现的语义。

> **验收时必须实测这条链路**：在 Airflow 触发 DAG 的**同时**记录
> `/proc/meminfo` 的 `MemAvailable`，证明全程没有跌破闸门。
> 不接受"跑通了就算"—— 跑通可能是运气，Sprint 3 的教训就是这么来的。

### 4.1 遗留风险

```text
Airflow 三个进程 + Doris BE + Spark 驱动同时在场时，余量比 Sprint 3 更薄。
缓解：① 不启动 triggerer；② 限制 Airflow 并发（parallelism / max_active_tasks）；
      ③ 归档作业与交易分层**串行**跑，不并行；④ swap 已在（4 GB）。
```

---

## 5. DAG 设计

### 5.1 任务图

```text
                    ┌─────────────────────┐
                    │ pause_realtime      │  暂停 Flink 栈（释放约 2.8 GB）
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                 │
     ┌────────▼────────┐              ┌─────────▼─────────┐
     │ ods_extract     │              │ archive_behavior  │   ← Sprint 4 新增
     │ MySQL → ODS     │              │ Kafka → ODS(行为) │
     └────────┬────────┘              └─────────┬─────────┘
              │                                 │
              └────────────────┬────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │ dwd_layers          │  ODS → DWD（交易 + 流量）
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ dws_layers          │  DWD → DWS
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ ads_layers          │  DWD → ADS（1 分钟 + 1 天）
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ reconcile           │  实时 ↔ 离线 逐窗口对账
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ load_to_doris       │  S3() TVF 装载进服务库
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ restore_realtime    │  恢复 Flink 并自检（trigger_rule=all_done）
                    └─────────────────────┘
```

**关键设计点**：

1. **每个 stage 是独立 Airflow 任务**，而不是把 `batch-mode.sh` 整个塞进一个任务。
   理由：Airflow 的价值就在于**每层的可见性、独立重试与失败定位**。
   塞成一个任务的话，Airflow 只是个定时器，退化成 cron。
2. **`ods_extract` 与 `archive_behavior` 并行**：数据源不同（MySQL vs Kafka），无依赖。
   但两者**都必须在 `pause_realtime` 之后**（内存闸门）。
3. **`restore_realtime` 用 `trigger_rule=all_done`**：
   无论中间哪一步失败都必须恢复实时链路 ——
   否则一次失败的批处理会让看板一直停在那里，这比批处理失败本身严重得多。
4. **恢复后自检**：调用 `scripts/health-check.sh`，它的退出码就是任务结果，
   满足 AGENTS.md「健康检查类脚本必须正确返回退出码」。
5. **`max_active_runs=1`**：不允许两次批处理重叠（重叠必然打穿内存）。

### 5.2 调度时间

错峰：**每天 03:00**（`0 3 * * *`）。

```text
为什么是凌晨：批处理期间实时链路是暂停的（看板曲线会延后追上）。
             选在使用者最少的时间窗，把"暂停"的影响降到最低。
```

---

## 6. 流量域归档设计（Sprint 4 新增）

### 6.1 为什么用 Spark 批读 Kafka，而不是 Flink 流式写湖

| 方案 | 评价 |
| --- | --- |
| Flink 作业持续写 S3A/Parquet | 又一个**常驻**作业，常驻内存 +1~2 GB；小文件问题需要额外调优 |
| **Spark 批读 Kafka → Parquet(S3A) → Hive** | **选它**：落在既有的批处理窗口内，不新增常驻进程；与 Sprint 2 的 ODS 抽取同构，复用同一套内存闸门与脚本骨架 |
| Spark Structured Streaming 常驻 | 同为常驻作业，且引入 checkpoint 管理复杂度 |

**核心理由**：本项目的"实时"已经由 Flink → Doris 承担；
湖仓的角色是**准确、可回溯的离线副本**。批读归档完全满足这个定位，
且不需要为它多养一个常驻进程 —— 在一台可用内存 3 GB 的机器上，这是决定性的。

### 6.2 ODS 表结构

对照 `sql/metadata/kafka_topics.md` 第 8 节的 `behavior_event` 信封：

```text
lakehouse.ods_behavior_event
  event_id       string      事件 ID（幂等键）
  event_type     string      VIEW / CLICK / CART / BUY / FAVORITE / SEARCH
  user_id        bigint
  product_id     bigint      可空（SEARCH 事件为 null）
  device         string      PC / APP / H5 / MINI_PROGRAM
  province       string      可空
  event_time     timestamp   事件时间（窗口计算基准）
  dt             date        分区列，由 event_time 推导
```

**分区**：按 `dt` 分区（`PARTITIONED BY (dt)`）。
**幂等**：重跑时 `INSERT OVERWRITE` 覆盖当天分区，而不是追加。
**为什么用事件时间而不是处理时间分区**：与 `metrics.md` 的时间语义一致
（事件时间保证乱序/重放结果一致），也让离线窗口与 Flink 窗口可比。

### 6.3 与实时链路对账的前提（必须先实测）

离线要能与 Flink 的实时指标逐窗口对齐，前提是**归档拿到的行为事件
与 Flink 消费到的是同一批**。这取决于两件事：

```text
① Kafka 的 retention 是否还留着最初的 20000 条行为事件（Sprint 0 生成）
② behavior_event 各分区的 earliest / latest offset
```

> **验收标准据此设定**：只承诺对账**归档区间内**的窗口，
> 不承诺覆盖全部历史。若 Kafka 已丢弃早期事件，则如实说明
> "离线流量域从 X 时刻起可对账"，**不伪造完整覆盖**。

---

## 7. 阶段划分

```text
阶段 1  安装 Airflow（venv + MySQL 元数据库 + 初始化）
阶段 2  systemd 三单元 + Web UI 可达 + 只读账号隔离复核
阶段 3  DAG：交易域离线流水线（pause → 6 stage → restore）
阶段 4  归档作业：Kafka → ODS(behavior)（含幂等与逐分区校验）
阶段 5  流量域 DWD / DWS / ADS + 逐窗口对账
阶段 6  内存实测取证 + 验收脚本 scripts/verify-sprint-4.sh
阶段 7  文档（SPRINT_4.md 结果 / README / AGENTS / DEVELOPMENT_LOG）+ 提交
```

每个阶段都走完 `理解 → 规划 → 小步实现 → 测试 → 验证 → 文档 → 提交`，
**只有上一层稳定才进入下一层**。

---

## 8. 验收标准（DoD）

- [ ] `apache-airflow` 3.3.2 装于 `.venv-airflow`，`airflow version` 输出 3.3.2
- [ ] 元数据库为 **MySQL**（新增库 `airflow`），**未引入 PostgreSQL**
- [ ] 三个 systemd 单元 active 且开机自启；`triggerer` **未**启动（有意的取舍）
- [ ] Web UI 经 Nginx 以子路径可达（与 `/data/` 并存，不抢根路径）
- [ ] DAG `offline_lakehouse_pipeline` 可在 UI 中手动触发，并成功跑完一次全链路
- [ ] DAG 的每个 stage 是**独立任务**（不是一个大任务）
- [ ] `restore_realtime` 在**任一**上游失败时仍会执行（`all_done` 语义实测）
- [ ] 归档作业幂等：重跑后 `ods_behavior_event` 行数不变
- [ ] 归档行数与 Kafka 实际消息数一致（逐分区核对，不接受"看起来对"）
- [ ] 流量域 ADS 与实时 ADS **逐窗口对账**，差异为 0（或如实说明覆盖区间）
- [ ] 内存取证：DAG 运行全程 `MemAvailable` 未跌破 `MIN_AVAILABLE_MB_FOR_BATCH`
- [ ] `bash scripts/verify-sprint-4.sh` 全绿
- [ ] 回归：Sprint 6 与 Sprint 7 验收仍通过（HTTPS 入口不受影响）
- [ ] 文档同步：SPRINT_4.md 结果 + README Roadmap + AGENTS §2.1/§15 + DEVELOPMENT_LOG
- [ ] **顺带修正** AGENTS.md §2.1 的 Python 版本错误（3.13 → 分运行时表述）

---

## 9. 已知风险

| # | 风险 | 影响 | 对策 |
| --- | --- | --- | --- |
| 1 | Airflow 常驻 ~1 GB，可用内存余量变薄 | 批处理被内存闸门拒绝 | 三段式 DAG（先暂停实时链路）；不启动 triggerer；限制并发；实测取证 |
| 2 | Kafka 可能已丢弃早期行为事件 | 流量域对账覆盖不全 | 先实测 earliest offset；验收只承诺归档区间，**不伪造全覆盖** |
| 3 | Airflow 依赖多，升级可能连带影响 | 装错会污染其他服务 | 独立 venv `.venv-airflow`，与 `.venv` / `.venv-agent` 完全隔离 |
| 4 | Web UI 也是 Web 服务，暴露面增加 | 与 Sprint 6 的"接口无鉴权"同类问题 | **只经 Nginx 反代、且不开放公网**（或加 basic auth）；与只读数据服务一样不直连公网端口 |
| 5 | uv/新依赖可能与宿主机 Python 3.12 冲突 | 安装失败 | 安装阶段已实测（阶段 1 单独验证），失败则记录并调整而非绕过 |

---

## 10. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-26 | V1.0 | 建立 Sprint 4 任务书：Airflow 3.3.2 + MySQL 元数据库 + 宿主机 systemd；流量域 Kafka→湖仓归档；含内存预算与风险清单 |
