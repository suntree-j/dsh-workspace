# Final Audit Report

> **Sprint 13A 收口审计（PHASE D · 汇总线）**
> 汇总日期：2026-09-27（审计素材 14:40–15:01 CST）
> 事实源：本报告的配套文件 **`EVIDENCE_MATRIX.md`**（论文与答辩的唯一事实源）。
> 本报告只做**汇总与定级**：**未修改任何既有文件**（本文件与 `EVIDENCE_MATRIX.md` 是两个新增文件）、
> **未执行 `git`**、服务器侧只做只读操作（`GET`/`SELECT`/`COUNT(*)`/`cat`/`ls`）。
> **本报告不含任何修复动作** —— 修复属 PHASE F，建议见 §10。

**计数（与 `EVIDENCE_MATRIX.md` §9 完全一致）**

| 级别 | 数量 | 备注 |
| --- | --- | --- |
| **P0** | **1** | 逗号连接绕过（线上复现），待在 PHASE F 修复 |
| **P1** | **32** | 原 34 条中 **P1-13 / P1-26 / P1-27** 已在审计窗口内闭环（见 §4.1） |
| **P2** | **42** | 原 43 条中 **P2-9** 已在审计窗口内闭环（见 §5.1） |
| **UNVERIFIABLE** | **26** | |

> **冻结时点（务必先读）**：本报告与 `EVIDENCE_MATRIX.md` 的判定**冻结在 2026-09-27 15:12 CST**。
> 冻结前后有**并发工作流在同时修改仓库文档**（`AGENTS.md` 15:04:46、`REPORT_DRAFT.md` 15:10:26、
> `外部审查包.md` 15:08:58、`QA_PREP.md` 15:08:48、`REPORT_OUTLINE.md` 15:08:52，
> 以及新增的 `DEFENSE_QA.md` / `DEFENSE_DEMO.md` / `RESUME_PROJECT.md`）。
> → **本报告引用的行号以审计/冻结时点为准**；判断某条是否仍成立，请以文件**当前内容**为准。
> 已在窗口内闭环的条目统一记入 §4.1 与 §5.1（**不删除**，保留可追溯性）。

> **审计窗口内的并发变更（只读复核）**：`AGENTS.md` 于 **2026-09-27 15:04:46** 被更新
> （1079 → 1093 行）：§15.9 改写为"Sprint 0~12 已全部稳定并验收通过"、
> 原"禁止提前实现 Sprint 8 及以后"降为**〔历史条款，已失效〕**、流量域分层改为
> "已由 Sprint 5 建成并验收"、并显式保留"仍未归零的一项（`click_rate`）"；
> Sprint 13 行改为"🔄 进行中（Prove + Audit + Close）"。
> → 本报告原 **P1-13 已闭环**（移入 §4.1），但**新增一处悬空引用**：
> `AGENTS.md:858` 指向不存在的 `docs/sprint/SPRINT_13.md`（P1-18）。
> **行号提示**：§15.9 之后的内容整体下移约 14 行，本报告引用的 `AGENTS.md` 行号以审计时点（≤15:01）为准；
> 已复核锚点：`60/60 通过` = `:885`、`Iceberg 5 张交易表` = `:925`、§15.12 首句 = `:1019`、
> `全量 pytest 353` = `:1061`。

---

## 1. Current Project State

**项目**：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台（电商场景）。
**代码与文档位置**：`intelligent-data-platform/`（创作副本；`dsh-workspace/intelligent-data-platform` 为其单向镜像）。

### 1.1 交付面（截至审计时点）

| 面 | 状态 | 证据 |
| --- | --- | --- |
| Sprint 0~12 | **全部 ✅ 已完成并验收通过**，逐项带验收计数（5/5、11/11、8/8、8/8、40/0/0、7/7、49/49、71/0/0、59/0/0、84/0/0、61/0/0、32/0/0） | `AGENTS.md:597-609`；13 个 `scripts/verify-sprint-*.sh` 实际存在 |
| Sprint 13（毕业论文 + 答辩） | 材料**已成稿**（`docs/thesis/` 8 个文件 + 1 个 144 KB `.docx`）；`AGENTS.md` 已于审计窗口内把 Sprint 13 标为"🔄 进行中（Prove + Audit + Close）"，但**其链接指向不存在的 `docs/sprint/SPRINT_13.md`**（P1-18），且仓库根 README 仍写"🔄 材料整理中"（P1-17）、项目 README 无标注（P2-23） | `docs/thesis/` 目录实测；`AGENTS.md:858`；`Test-Path SPRINT_13.md` = False |
| 实时链路 | Kafka（4 topic）→ Flink（8 个 sink，各 1 实例）→ Doris（Routine Load） | `AGENTS.md` §15.2；S8 §2.5 |
| 离线链路 | Spark → Iceberg（23 张表）→ Doris（S3 TVF 装载） + Airflow 9 任务调度 | `AGENTS.md` §15.8/§15.10；S3 |
| 智能层 | 只读数据服务（FastAPI + `sqlguard` + 只读账号 `agent_ro`）→ LangGraph Agent（反思重试）→ MCP（4 个只读工具） + 词法检索（BM25，无向量库） | `AGENTS.md` §15.6/§15.12；S4 |
| 治理 | 数据质量 25 条清单（Doris 侧实跑 24 通过/0 失败/1 跳过）+ Prometheus/Grafana | `AGENTS.md` §15.11；S6 |

### 1.2 运行现场（只读观测）

```text
服务器     36.151.150.140（腾讯云 lavm-txyjj7xzr7，Ubuntu 24.04.2 LTS，4 核 / 16 GB）
容器       14 个全部 Up（kafka / doris-fe / doris-be / mysql / minio / flink-* / spark-* / hive-metastore …）
内存       2026-09-27 14:54:18：total 15989 / used 12917 / free 1145 / available 3071 MB；Swap 4095 used 3238
站点       单一 HTTP 站点（:80），443 已按实测停用；TLS 配置与 setup-tls.sh 保留在仓库
数据       合成数据集，GEN_RANDOM_SEED=20260926（可复现）；无真实业务数据
```

> **swap 一条**：`Swap: total 4095 used 3238`（S8 §2.1）证明服务器**已有 swap 且正在使用**，
> 因此 `SPRINT_0_VERIFICATION_STATUS.md:195` 的"服务器无 SWAP"是**已被实测否证**的过期项（P2-28）。

### 1.3 审计期新查出的结构性事实（本次首见，必须写进论文口径）

```text
Flink 作业被重放 29 次（全链路事实，不是行为表独有）：
  dwd_traffic_behavior_detail topic  latest 合计 = 580000 = 29 × 20000
  dwd_trade_order_detail   topic     latest 合计 = 174000 = 29 × 6000
  dwd_trade_payment_detail topic     latest 合计 = 156774 = 29 × 5406
  dwd_trade_refund_detail  topic     latest 合计 =  7366 = 29 × 254
  Doris Routine Load loadedRows 与上述逐项吻合（行为表 580000、errorRows 0）
→ 而 Doris 实时表行数为：行为 20000 / 订单 6000 / 支付 5406 / 退款 254
→ 机制：Doris 侧为 UNIQUE KEY + merge-on-write，重复键被 upsert 覆盖
  （行为表 `UNIQUE KEY(event_id)`，sql/doris/10_dwd_tables.sql:101-117）
→ 结论：**"实时表行数"= 按主键去重后的行数，不等于"链路处理量"**（P1-34）
```

来源：`S8 §2.2 / §2.4 / §2.5 / §2.6`（只读 Kafka 位点 + Doris `SELECT` + `SHOW ROUTINE LOAD`）。

### 1.4 一句话状态

> **数据链路本身是可运行、可对账、可复现的；审计发现的问题集中在"证据边界没有写进结论"与"文档状态滞后"两类，
> 以及一个必须先修的权限边界缺口（P0-1 逗号连接绕过）。没有任何一条 P0 涉及数据正确性本身。**

---

## 2. Audit Method

### 2.1 六条审计线（全部只读，全部有独立产出）

| # | 审计线 | 产出 | 覆盖 | 状态 |
| --- | --- | --- | --- | --- |
| L1 | 批流一致性（7 问） | `.tmp/audit-reconcile.md`（552 行） | 两域对账作业与判据 SQL、两条验收脚本、`Checker` 断言层、`metrics.md` 口径 | **DONE** |
| L2 | 文档状态一致性 + 绝对化表述 | `.tmp/audit-doc-status.md`（872 行） | 项目 README（全文 1080 行）、仓库根 README、`AGENTS.md` §15、15 份 Sprint 文档头部、REPORT_DRAFT、外部审查包、DECISIONS | **DONE** |
| L3 | Iceberg 验证范围 | `.tmp/audit-iceberg.md`（381 行） | 迁移作业 336 行全文、`spark-defaults.conf` 118 行、`SPRINT_5.md` §8.1/§9.6/§11、`_common.py` L244-286、逐表 DDL | **DONE** |
| L4 | Agent 安全边界 + 反思重试 + 检索表述 | `.tmp/audit-agent.md`（425 行） | `sqlguard.py` 全文、对抗测试 340 行、Agent tools/config/graph、系统提示路径、检索实现与语料 | **DONE** |
| L5 | 性能数据边界 | `.tmp/audit-perf.md`（416 行） | `measure-latency.sh` 405 行、`PERFORMANCE.md` 342 行、`SPRINT_12.md`、`REPORT_DRAFT` 性能章、`verify-sprint-12.sh`；**并做服务器只读取证**（原始采集日志 + Airflow 元数据库 + Doris 计数） | **DONE** |
| L6 | 跨文档状态与数字一致性 | `.tmp/audit-doc-consistency.md`（210 行） | 仓库根 README、项目 README、`AGENTS.md`、12+2 份 Sprint 文档、DECISIONS、PERFORMANCE、DEVELOPMENT_LOG、REPORT_DRAFT、DATA_AND_LIMITATIONS、外部审查包 | **DONE** |
| L7 | **数据差异专项（20000 vs 40000）** | `.tmp/audit-traffic-20000-40000.md`（**741 行，§0–§8 完整**） | 仓库侧 R1–R5（生成器编号规则、config、Flink sink、归档幂等、Doris 表与 Routine Load）+ 服务器侧 S1–S17（Kafka 位点/全量 `event_id` 直方图/`CreateTime`/段文件、Doris 行数与分布、Routine Load、Hive Metastore `numRows`、Iceberg 快照历史、归档作业自打印日志、MinIO 目录）+ 三方对照表 + 判定 | **DONE**（2026-09-27 15:08:54 落盘；**判定 = B：40000 判为错误，基准值 20000**） |
| — | **主控实测**（不属于任何子线） | 本报告 §3、`EVIDENCE_MATRIX.md` §3 | 对账汇总/明细只读 `SELECT`（2026-09-27 14:55:12）；逗号连接线上复现三组对照（2026-09-27 14:56:19） | **DONE** |

### 2.2 证据优先级（冲突时按此裁定）

```text
代码  >  实际验证脚本  >  验证结果  >  Sprint 文档  >  README/论文
```

本次共有 **7 处**"文档 vs 代码/实测"冲突按此裁定，全部以**代码/实测**为准并把冲突记入 §8。
最重要的一例：`08_traffic_reconcile.sql:36-39` 的文件头注释（"等于零才算真正全部一致"）
与作业代码（`reconcile_traffic_batch_realtime.py:319-334` 对实时侧 anomaly **不做断言**）冲突 → **以代码为准**（P1-5）。

### 2.3 只读纪律与本次披露的副作用

- 服务器侧仅 `GET` / `SELECT` / `COUNT(*)` / `SHOW` / `cat` / `ls` / `free` / `df`；
  **未**重跑任何流水线、**未**重启任何服务、**未**改任何配置、**未**删任何 topic/表；
  **未**向 `/ask` 发压（避免外部 LLM 计费）；**未**执行 `git`。
- **已披露的只读副作用（各线如实登记）**：L4 探针 `import` 触发 CPython 写入
  `services/api/app/__pycache__/sqlguard.cpython-313.pyc`，审计结束时**已删除并恢复目录原状**；
  L5 在服务器 `/tmp` 写入 3 个临时文件、向只读接口 `POST /query` 发了 8 次相同 `SELECT`（1 次带 `@` 前缀的失败请求被正常拒绝，未执行 SQL）。
- **工具坑（值得记录，影响证据可信度）**：Kafka 4.2.1 里 `kafka.tools.GetOffsetShell` **类名已变更**，
  旧写法 `kafka-run-class.sh kafka.tools.GetOffsetShell` 会 `ClassNotFoundException` 且**输出为空**，
  极易被误读为"位点取不到/topic 为空"；正确类名 `org.apache.kafka.tools.GetOffsetShell` 或 `bin/kafka-get-offsets.sh`。
  → **上一代理"max event_id = evt_420000"这条线索本身正确，但它的位点探针是坏的**（S8 §2.2）。

### 2.4 本次审计**没有**覆盖的范围（如实声明，避免"看着像全审过"）

```text
✗ Prometheus 告警规则 / SLO（六条线均未审计；只审了采集与看板）      → UNVERIFIABLE-26
✗ 前端/看板 UI 代码质量与可访问性（brief §5 把 UI 列为 P2 范畴，本次未审）
✗ 服务器站点形态与 agent_ro 当前授权的独立验证（L2 明确限定为文档线，未开服务器会话）
✗ 离线 DWD 明细表 `lakehouse.dwd_traffic_behavior_detail` 的行数（L7 未跑 Spark：
  内存纪律，可用 3071 MB / swap 已用 3238 MB）→ U-22
   （注：**归档 ODS `ods_behavior_event` 的行数已由三源实测 = 20000**，不再是缺口）
✗ 2026-09-26 13:18 Kafka topic 重建之前的历史状态（无快照、无更早日志）→ U-21
✗ 论文 `.docx` 与 REPORT_DRAFT.md 的同步性（未打开 .docx）
✗ 17 份 Sprint 文档之外的 docs（如 `PROJECT_DESIGN_V1.md` 除被 grep 命中的旧表述外未通读）
```

---

## 3. P0

### 数量：**1**

---

#### 【P0-1】逗号连接（隐式 CROSS JOIN）绕过表白名单，且血缘漏报 —— **线上复现**

| 项 | 内容 |
| --- | --- |
| **位置** | `intelligent-data-platform/services/api/app/sqlguard.py:115`（`_TABLE_RE = \b(?:from|join)\s+(...)`，**不含逗号分支**）；同一正则被 `extract_tables`（`:133-145`）与白名单校验（`:180`）共用 |
| **线上证据（主控实测，观测时间 2026-09-27 14:56:19 CST）** | ① `SELECT … FROM ecommerce.test_connection` → **REJECT `TABLE_NOT_ALLOWED`**（对照：证明该表确在白名单外）<br>② `SELECT … FROM dwd_trade_order_detail a, ecommerce.test_connection b` → **ALLOW，`row_count=3`，返回真实数据**，且血缘**只报** `['dwd_trade_order_detail']`（**漏报被连接的表**）<br>③ 对照：显式 `JOIN` 白名单内两表 → ALLOW 且血缘正确 |
| **代码级证据（L4 探针）** | `.tmp/probe-sqlguard-gaps.py`：`FROM dwd_trade_order_detail o, mysql_user u …` → `tables: ['dwd_trade_order_detail']`、输出追加 `LIMIT 50`（放行）；三表逗号连接中中间那张非法表同样未被识别。全仓库 grep `逗号连接\|comma.?join\|隐式连接\|CROSS JOIN` → 仅命中 2 处 Spark SQL（`04_reconcile.sql:135`、`08_traffic_reconcile.sql:190`），**与守卫无关** → 该缺口**既未修复、也未记录、也未测试** |
| **为什么是 P0** | ① 它**直接否证项目最核心的安全主张**"Agent 只能读到白名单里的 16 张表"——凡 `agent_ro` 在 Doris 上有 `SELECT_PRIV` 的表（`ecommerce.*` **整库**授权 + `lakehouse_ads.*` + `information_schema.*`，见 `install-web.sh:198-200`、`load-batch-to-doris.sh:352-362`）都可用逗号连接读出，包括 `ecommerce` 的 ODS 原始层与业务表；<br>② 它**同时破坏血缘信息**（`source.tables` 漏报），而论文/答辩的"回答可追溯"正是建立在 `tables` + `executed_sql` 上；<br>③ 它**让既有验收断言失效而非报错**：`verify-sprint-10.sh` 的"MCP 与 HTTP 两路径 tables 一致"断言**对逗号连接会一起"绿"**（两侧都不识别逗号表），属"成功信号不可信"这一类，正是本项目 §15.8 自己总结过的最危险模式 |
| **级别演变（如实记录）** | L4 审计线基于**代码 + 纯函数探针**评为 **P1**（当时未打线上接口）；主控随后**线上复现**，按 `audit-brief.md` §5 的 P0 定义（"会导致答辩核心结论错误"）**升为 P0**。两处定级不一致的原因已说明，不取平均 |

**修复要求（PHASE F，先测后改）**：**先补对抗用例**（含把 `xfail(strict=True)` 迁移为正式断言的路径），
再改守卫（`_TABLE_RE` 增逗号分支，或改为"把 `FROM` 到下一个子句之间的所有点分标识符全部提取"）；
同时把"逗号连接 SQL"补进 `verify-sprint-10.sh` 的两路径一致性断言。
⚠️ 改完必须**同时**看两件事：`ALLOW/REJECT` 与 `source.tables`（血缘），只验前者会漏掉漏报。
建议按 `AGENTS.md` §11.1 记录本次变更（涉及权限边界）。

---

## 4. P1

### 数量：**32**

> 分组：A 批流对账证据边界（5）｜B Iceberg 主张边界（2）｜C 数字同名不同范围（4）｜
> D 文档状态一致性（**8**）｜E 论文 / 外部审查包（**5**）｜F 性能口径（5）｜G Agent 主张边界（2）｜
> H 材料引用一致性（1）。
> **原 34 条中的 P1-13 / P1-26 / P1-27 已在审计窗口内闭环 → 见 §4.1，不计入本计数。**

### A. 批流对账证据边界

**【P1-1】交易域没有"仅实时 / 仅离线窗口 = 0"断言，只有单侧 ≥90% 覆盖率守卫**
证据：`reconcile_batch_realtime.py:172-176`（守卫只查 `batch_windows > 0`）、`:38`（`MIN_REALTIME_COVERAGE = 0.9`，**不是 1.0**）、`:177-182`；对照流量域 `reconcile_traffic_batch_realtime.py:275-276` **有**两条单边断言。
影响：`mismatched_windows = 0` **不能**推出"两侧窗口集合完全相同"；"覆盖率容差 10%"是独立于 mismatch 的第二条容忍线。→ 论文措辞必须限定为"区间内逐窗口指标一致"。

**【P1-2】交易域 `is_match` 是"补零后相等"：`NULL` 与真实 `0` 不可区分**
证据：`04_reconcile.sql:94-129`（全部 `COALESCE(...,0)`）、`:121-130`（八列判据）。
影响：`is_match = true` 的语义是"**补零后数值相等**"，**不是**"两侧都产出了该窗口"。若一侧缺窗口、另一侧八指标全 0，会被判为 match → "0 mismatch"与"单边窗口 = 0"**不严格等价**。该漏洞**在本次数据下未发生**（主控实测两侧窗口数相等：11458 = 11458），但结论强度**来自数据分布而非断言本身** → 必须写进论文局限。

**【P1-3】交易域"8 个可加指标"表述错误（实为 7 可加 + 1 去重）**
证据：`reconcile_batch_realtime.py:10`（文件头"逐窗口比对 8 个可加指标"）；`order_user_cnt` 实现是 `COUNT(DISTINCT user_id)`（`03_ads_metrics.sql:46`、`05_metric_jobs.sql:88`）；`sql/metadata/metrics.md:79` 把它与金额/笔数**并列在"可加指标"行**，而同文件 `:35` 又写明它是去重用户数；项目别处**知道**此坑（`03_ads_metrics.sql:101-103`"为什么 order_user_cnt 不能 SUM"）。
影响：论文写"8 个可加指标"会被追问；正确写法是"**7 个可加 + 1 个去重（两侧同为近似去重）**"，并声明去重指标**只支持逐窗口比较、不支持跨窗口上卷**。

**【P1-4】交易域验收脚本弱于作业层，且取"最新批次"而非本次批次**
证据：`verify-sprint-3.sh:292-320`（只有"查不到汇总行"守卫，`：300-305`；`：313-315` 仅断言 mismatch=0 / GMV 合计 / is_pass）—— **没有**非空守卫、覆盖率、单边窗口断言、SCOPE 区间比对；`:296-298` 用 `ORDER BY compared_at DESC LIMIT 1` 取**最新批次**，历史通过批次也能让本步变绿。
对照：`verify-sprint-5.sh:262-265,516-517,527-528,539-540` 守卫与单边断言齐全 → **两域验收强度不对称，不能合并陈述**。

**【P1-5】流量域文件头注释与代码行为冲突（实时侧 anomaly 不阻断退出码）**
证据：`08_traffic_reconcile.sql:36-39` 称"作业会显式统计'比率列也不一致的窗口数'并打印 —— **等于零才算真正全部一致**"；而代码 `reconcile_traffic_batch_realtime.py:319-321` **只断言离线侧**、`:323-334` **刻意不断言实时侧**（作者原话：写成会失败的 check 会让离线作业替实时链路"背锅"）。
判定：按证据优先级**以代码为准** → 实时侧 anomaly **不阻断**；`:37` 那句是**不成立的表述**，答辩照抄有风险。

### B. Iceberg 主张边界

**【P1-6】"Iceberg 迁移未改变数据"被写成无限定主张，且把"23 张表"与"`60/60`（18 张表那次）"并列**
证据：`docs/thesis/REPORT_DRAFT.md:1151`（结论列"迁移未改数据"、实测列"迁移清单 23 张表；阶段 4 校验 60/60"）；`DEFENSE_SLIDES.md:201`；对照 `DEFENSE_SCRIPT.md:316`（已标范围，可作范式）。
影响：同行两个数字**范围不同**却未标注，与 5.4.1 已写好的范围说明**自相矛盾**；"未改变数据"会被读成"逐行/所有字段一致"（见 `EVIDENCE_MATRIX.md` §4.3 的 V1–V11 全部未验证）。

**【P1-7】`70/70` 只有一句汇总，无原始 `[check]` 输出、无逐项细目**
证据：唯一出处 `AGENTS.md:604`；而 `60/60` 有原文行（`SPRINT_5.md:338`、`DEVELOPMENT_LOG.md:913`），`SPRINT_5.md` §9.6 **未记录**复跑结果。
判定：构成**可推导（本次复算 23+23+23+1 = 70 吻合）但未逐项记录**；"两次运行之间判据未变"只能由 `SPRINT_5.md` §11 变更记录推断（**本阶段禁止 git，无法核对提交历史**）。
→ 论文可保留 `70/70`，但**必须标注证据等级**（"仅汇总记录，无逐项细目"）。

### C. 数字同名不同范围

**【P1-8】`dwd_traffic_behavior_detail` 的 20000 vs 40000（已定案，见 §7）**
证据：当前实测 **20000**（Kafka 源 topic latest 合计 6506+6592+6902 = 20000，earliest 全 0；Doris `COUNT(*)` = 20000，14:54 与 14:5x 两次一致）；`SPRINT_3.md:52` 记 **40000**（"实时 DWD（Doris ecommerce）… 与 MySQL 1:1"）。
判定：**20000 是当前事实**；`40000` **无法从当前状态复现**（其来源不可考 → UNVERIFIABLE-21）。`SPRINT_3.md:52` 该行必须改写或加注，**不得**再作为"当前行数"引用。

**【P1-9】交易域窗口数 11458 vs 11459 未标口径**
证据：`ads_realtime_trade_1m` = **11459** 行（`PERFORMANCE.md:31`；L5 与 L7 两次只读复核一致）；对账汇总 `realtime_windows` = **11458**（主控实测 + `AGENTS.md:817`）；`REPORT_DRAFT.md:429/451` 两个数并列出现。流量域同型：表 19644 窗口、对账比 19643。
判定：差 **1 个窗口**，来源是对账区间的**尾部安全边界**（`reconcile_batch_realtime.py:97-119`）截断 —— 是**口径差，不是数据缺失**；但必须在首次出现处加口径注。

**【P1-10】两个维表缺陷的状态在四份文档里分成"已修/未修"两派**
证据：已修派 `AGENTS.md:109`、`DECISIONS.md:133`、`DATA_AND_LIMITATIONS.md:246`、`REPORT_DRAFT.md:1325`；未修派 `PERFORMANCE.md:265-276,337`、`SPRINT_12.md:290,413`、`DEVELOPMENT_LOG.md:1551`。
判定：**同一事实两种现状**。处置：**不修改历史观察值**（采集时确为 0 行），只在各节**追加**后续状态（2026-09-27 提交 `b195d19` 已修并服务器验证）。

**【P1-11】层间"不一致表数 0"实际只覆盖 Iceberg 5 张交易表**
证据：`AGENTS.md:911`明写范围（"Iceberg 5 张交易表"）；根 `README.md:61` 与项目 README 未带该范围，且与 `:64` 的"23 张表 70/70 / 19643 窗口不一致 0"连读 → 读者会读成全层零差异。

### D. 文档状态一致性

**【P1-12】项目 README Roadmap 三处 🔄 残留在已完成项上**
证据：`README.md:872`（Sprint 5 🔄"仅剩一处实时侧缺陷待决策"）、`:887`（Sprint 10 🔄 + `:893`"⏳ 服务器端验收待补"）、`:902`（Sprint 12 🔄 + `:905`"数字待补"）vs `AGENTS.md:604/607/609` 三者均 ✅（84/0/0、四类基线已实测）。
注：同一 Roadmap 内 Sprint 11 用 ✅ → **标记体系自身不自洽**；且"数字待补"在答辩现场是**自伤**（评委会以为性能章是空的）。"仅剩一处实时侧缺陷"应移到**已知缺陷注记**，不得让一处未修缺陷把整个 Sprint 降级。

**【P1-13】`AGENTS.md` §15.9 与 §15 状态表自相矛盾 —— ✅ 审计窗口内已闭环（见 §4.1，不计入 33 条）**

**【P1-14】README TLS / 端口反向描述（指向已停用的 443）**
证据：`README.md:451-452`（"80 …其余 302 跳转到 HTTPS"、"443 **唯一业务入口**"）与**同文件** `:29`（"当前形态是明文 HTTP，没有启用 TLS"）互相否证；`:978-994`"站点启用 HTTPS"整段仍在跑，且把 502 结论写成"上 HTTPS 后该现象消失 → 已解决"（AGENTS §15.7 已修正为 **VPN 出口路径改写**，明文 **30/30** 正常）；`:993` 的备用手段指向**当前不监听的 443**（照做必失败）；`:56`"公网 HTTPS 64/64 返回 200"未标时点/已回退。
处置：L451-452 → 80 = 唯一业务入口（明文）、443 = 未监听/已停用；L985 → 结论改为"VPN 出口改写"；L993 删除或标注"仅重新启用 TLS 后可用"。

**【P1-15】`SPRINT_3.md` / `SPRINT_4.md` / `SPRINT_5.md` 头部状态仍写"进行中"**
证据：`SPRINT_3.md:8`"🚧 进行中"、`SPRINT_4.md:5`"进行中"、`SPRINT_5.md:5`"进行中" vs `AGENTS.md:601/603/604` 全 ✅；且**文件末尾已有完成结算**（`SPRINT_5.md:648` V1.2 变更记录写"阶段 5~7 完成…迁移清单 18→23 张表"）→ **文件内部自相矛盾**。
⚠️ 改 SLA 时**必须保留** `SPRINT_5.md:309-312` 的 `[ ] ⚠️ 一项未归零`（禁止删 unresolved）。

**【P1-16】`SPRINT_4.md:435-441` §10.3「仍待完成」三项**实际已全部关闭****
证据：① 流量域三层 + 逐窗口对账 → `AGENTS.md:604`（DWD 21/21、DWS 20/20、ADS 36/36、对账 10/10）；② `scripts/verify-sprint-4.sh` **文件实际存在**且 `AGENTS.md:858` 记 40/0/0；③ 文档收口 → `README.md:866-870` 与 §15.8 已建。
影响：与头部"进行中"叠加后，**`SPRINT_4.md` 通篇看起来像个没做完的 Sprint**。

**【P1-17】仓库根 `README.md:70` Sprint 13"材料整理中"过期**
证据：`docs/thesis/` 实测 8 个文件 + 1 个 144 KB `.docx` 均在位（REPORT_DRAFT 142 KB、REPORT_OUTLINE 54 KB、QA_PREP 43 KB、DEFENSE_SCRIPT 23 KB、DEFENSE_SLIDES 22 KB、外部审查包 23 KB、DATA_AND_LIMITATIONS 40 KB、md_to_thesis_docx.py 45 KB）。另注：同文件 `:53` 标题"0~12 全部完成"与表格列 13 **自相矛盾**。

**【P1-18】`docs/sprint/SPRINT_13.md` 不存在，而 `AGENTS.md` 已引用它（悬空引用）**
证据：`Test-Path intelligent-data-platform/docs/sprint/SPRINT_13.md` = **False**；`docs/sprint/` 实际只有 `SPRINT_0 ~ SPRINT_12` + 2 份 `VERIFICATION_STATUS`；
而 `AGENTS.md:858`（2026-09-27 15:04:46 更新后新增）与 §15 状态行的 Sprint 13 行**均已链接** `docs/sprint/SPRINT_13.md` → **悬空引用**。
判定：这是"缺失"而非"冲突"，但它解释了 P1-17 为什么容易漂 —— Sprint 13 是**唯一没有设计文档作为锚点**的 Sprint，只有 README/AGENTS 的表述在描述它的状态，没有文件可交叉核对。
处置：补 `SPRINT_13.md`（任务书 + 结算），或**同时**把 `AGENTS.md:858` 与 §15 的链接改为"以 `docs/thesis/` 为准"（**二者必须同向**，否则仍是指向空处）。

### 4.1 审计窗口内**已闭环**的项（不计入 §3–§5 的计数）

**【P1-13（原）】`AGENTS.md` §15.9 与 §15 状态表自相矛盾 → ✅ 已闭环**

- **原问题**：`AGENTS.md:855`"Sprint 5 **进行中**（阶段 4 已完成）"、`:856`"**禁止提前实现 Sprint 8 及以后**（LangGraph / RAG / MCP / 监控）"、`:863-865`"流量域的 DWD / DWS / ADS 分层**尚未建模**" vs 同文件 `:604`（Sprint 5 ✅、流量域 DWD 21/21、DWS 20/20、ADS 36/36）与 `:605-609`（Sprint 8~12 全 ✅）→ **同一条规范禁止了自己已完成的工作**。
- **闭环证据（本报告只读复核，2026-09-27 15:05）**：`AGENTS.md` 于 **15:04:46** 更新（1079 → 1093 行）：
  `:855` 改为"Sprint 0 ~ 12 已全部稳定并验收通过"；`:864-869` 原禁令降为 **〔历史条款，已失效〕**（**保留沿革、未删除**，并注明"不得再据此认定后续 Sprint 的工作违规"）；`:871-876` 流量域改为"**已由 Sprint 5 建成并验收**"；`:877-880` **显式保留**"仍未归零的一项：实时侧 1 个窗口的 `click_rate` 自相矛盾"；Sprint 13 行改为"🔄 进行中（Prove + Audit + Close）"。
- **复核结论**：三条子问题全部关闭，且**没有**删除任何 unresolved 项（符合 `audit-brief.md` §1.5/§1.6）。
- **附带影响**：该更新使 `AGENTS.md` §15.9 之后的内容整体下移约 14 行（行号已在本报告顶部与 §8 注明）；
  并**新引入**一处悬空引用（`AGENTS.md:858` → 不存在的 `docs/sprint/SPRINT_13.md`）→ 计为 **P1-18**（未闭环）。

**【P1-26（原）】`REPORT_DRAFT.md` 把合成数据标为"真实数据" → ✅ 已闭环**

- **原问题**：`:443`"各层表与行数（**真实数据**，来源 `docs/sprint/SPRINT_3.md` 第 7.1 节）"，
  与同文件摘要 `:18`（"全部数据为程序生成的**合成数据**"）与 `:1359`（§7.3 不足第 1 项）**直接矛盾**；
  且仓库根 `README.md:98` 自己就写着"**不要写成真实业务数据**"——该条属**违犯自家纪律**。
  这是审计认定的**最高价值单条修复**（改 4 个字即可消除论文核心章节与摘要的冲突）。
- **闭环证据（15:10:26 版本）**：该处（现 `:457`）已改为
  "各层表与行数（**程序生成的合成数据集，种子 `GEN_RANDOM_SEED=20260926`**；下表是**该合成数据集上的实测值**，
  来源 `docs/sprint/SPRINT_3.md` 第 7.1 节；数据规模**不代表真实业务分布**，见 §7.3 第 1 项）"
  → **比审计建议的措辞更完整**（同时补了"实测值"与"不代表真实业务分布"两层限定）。

**【P1-27（原）】`REPORT_DRAFT.md` §6.6 标题"真实数据缺陷"歧义 → ✅ 已闭环**

- **原问题**：标题"采集过程中发现的一处**真实数据**缺陷"有两读（"真实数据里的缺陷" vs "真实存在的缺陷"）。
- **闭环证据（15:10:26 版本）**：该处（现 `:1367`）已改为
  "## 6.6 采集过程中发现的一处**真实缺陷**：派生表为空使 JOIN 静默返回 0 行"
  → 正是审计建议的"把'真实'从'数据'移到'缺陷'"，**歧义消除且未丢强调**。

> ⚠️ **闭环的一个必要提醒**：`docs/thesis/毕业论文_*.docx`（144 KB，13:10:40）**早于**上述修改，
> 因此 **P1-26/P1-27 的修复必须连同 `.docx` 重新导出**，否则 Word 终稿里仍留着"真实数据"
> （`md_to_thesis_docx.py` 在仓库内，重导出成本低）。

**【P1-19】项目 README 文首"当前进度"只列到 Sprint 7**
证据：`README.md:7-10` 只列 Sprint 0/1/2/3/6/7，未提 4/5/8~12；对照 `:866-906` Roadmap 列到 12、`dsh-workspace/README.md:53` 写"0~12 全部完成"。
分级说明：L2 评为 **P2**、L6 评为 **P1** → 本次**取 P1**（理由：README 是评委最先读的文件，文首这句是第一印象，且与**同文件** Roadmap 直接冲突）。两线分歧如实记录，不取平均。

**【P1-20】项目 README"下一步：Sprint 10 与 Sprint 12 的服务器端收口"整段过期 + `191/138 skipped`**
证据：`README.md:911-919`（含 `:913`"pytest **191 passed / 138 skipped / 3 xfailed**"）vs `AGENTS.md:609`（**353/0/0/3 xfail**）、`SPRINT_12.md:282`。
影响：`191/138` 与 `353/0/0` 是**同一份测试的两个时点**，并列出现会被误读成"数字对不上"。
分级说明：L2 评 P2、L6 评 P1 → 本次**取 P1**（同名不同范围的数字对可信度影响更大）。

### E. 论文 / 外部审查包

**【P1-21】外部审查包三个数字无出处：`verify-sprint-12.sh` 42/0/0、全量 pytest 381 passed、`verify-sprint-5.sh` 88/0/0**
证据：`外部审查包.md:122-131`（标题"## 2. 验收矩阵（**每个数字都是服务器上跑出来的**）"）vs 权威值 `AGENTS.md:609`（32/0/0）、`:1047`（353 passed / 0 failed / 0 skipped / 3 xfailed）、`SPRINT_12.md:287`（32/0/0）、`:411`（353）。
判定：三个数字在仓库其他文档中**没有任何出处** → 对应主张 **NOT_SUPPORTED**（`EVIDENCE_MATRIX.md` C-20）。
为什么严重：**该审查包的用途正是供外部核对"有没有编数字"**，它自己带了三个无出处的数字 → 本次审计发现的**最严重的文档可信度问题**。
处置：要么给出脚本输出原文作为出处，要么改为与 `AGENTS.md` 一致的 32/0/0 与 353。

**【P1-22】外部审查包验收矩阵缺 Sprint 5 行，却宣称"每个数字都是服务器上跑出来的"；`:177` 与 §5.4 自陈冲突**
证据：`:117-132`（矩阵无 Sprint 5 行）、`:124`（88/0/0）、`:177`（"逐表行数与 Parquet 侧｜**完全一致**"）vs `:258-263`（§5.4 自陈"`dwd_traffic_behavior_detail` 的行数在两个文档来源里差 **2 倍**（未定性，请勿当作结论）"）。
影响：同包内一处宣称"完全一致"、另一处声明"未定性"，外部审查者会先撞上矛盾。

**【P1-23】"13 个验收脚本" / "13 个 Sprint" / "12 个开发阶段" 口径混用**
证据：`外部审查包.md:299`（"13 个验收脚本"）、`REPORT_OUTLINE.md:422,567`（"13 个 Sprint"）vs 实际：`verify-sprint-0.sh … verify-sprint-12.sh` = **13 个脚本 / 12 个编号 Sprint + 0**；`AGENTS.md:610` 把 Sprint 13 标为未开始。
处置：统一为"**12 个开发阶段（Sprint 0~12），13 个验收脚本**"，或明确"Sprint 13 无验收脚本"。当前写法会招来"第 13 个 Sprint 的验收脚本在哪"。

**【P1-24】`DECISIONS.md` ⏳12 / ⏳13 的服务器验收状态三处不一致**
证据：`DECISIONS.md:144`（⏳12"服务器验收待跑"）vs `DATA_AND_LIMITATIONS.md:267`（"**已跑**（`--stage load` 后逐表行数与湖仓一致，`ads_traffic_1m 19644 == 湖仓`）"）；`DECISIONS.md:154`（⏳13"待跑"）vs `外部审查包.md:239`（"**服务器实测**…对抗用例 63 passed、0 xfail"）。
判定：**由主控裁定后只改一处并互相引用**；⏳13 建议以 `外部审查包.md:239` 为准（有实测细节），⏳12 建议以 `DATA_AND_LIMITATIONS.md:267` 为准（有具体值）。**本阶段禁止 git，无法判定谁后写**（UNVERIFIABLE-27）。

**【P1-25】外部审查包"DECISIONS.md｜14 项待确认（⏳）"实为 13 条**
证据：`外部审查包.md:60` vs `DECISIONS.md` 实际 ⏳ 编号 `1,2,3,4,5,6,7,8,10,11,12,13,14`（**缺 ⏳9**）= 13 条；其中 `:112`"无需任何动作"、`:133`"已修并已服务器验证"、`:144`"已修"、`:147/:158` 标题"已修"、`:87`"进展（Sprint 9 已完成）" → 至少 5~7 条已关闭。
处置：改为"⏳ 编号 1~14（缺 9，实为 13 条）；其中已实施/已修 7 条，仍待确认 6 条"；`外部审查包.md:103`"不修的 4 项"需按同一口径重述（其 §5.2 表只有 3 行，见 P2-35）。

**【P1-26】`REPORT_DRAFT.md:443` 把合成数据标为"真实数据" —— ✅ 审计窗口内已闭环（见 §4.1，不计入 32 条）**

**【P1-27】`REPORT_DRAFT.md:1305` §6.6 标题"真实数据缺陷" —— ✅ 审计窗口内已闭环（见 §4.1，不计入 32 条）**

### F. 性能口径

**【P1-28】Agent `/ask` 实际 n=2 且**无预热**，文档写"3 次（含 1 次预热）"**
证据：代码 `measure-latency.sh:335-344`（`curl` 1 次 + 循环写死 2 次，**无预热调用**，`REPEAT` 对该节无效）；**原始日志 `/tmp/perf-bc.log`：`{"n": 2, "median_ms": 19187.5, …}`**；文档 `PERFORMANCE.md:21,210`、`REPORT_DRAFT.md:1197,1290`。
影响：中位数 19187.5 与 min/max **不受影响**（去一个样本不改变中位数）；但"n=3 含预热"与原始日志不符，且 `REPORT_DRAFT.md:1192`"每项 7 次（另有 1 次预热）"在 Agent 行上不成立（同表内自相矛盾）。
更实质的两点：① 那"第一次"**既没单独报告、也没标注冷启动、也没计入统计**，被静默丢弃 —— 而它恰是唯一反映"冷 Agent 首次问答"的样本；② 文档把"发了 3 次、用了 2 次"说成"3 次采样含 1 次预热"。→ 若论文要 n=3，正确做法是**明确写出三个数**。

**【P1-29】批量"9 个任务耗时合计"含 1 次重试 attempt，源码/文档均未说明**
证据：只读元数据库复核 `SELECT COUNT(*), ROUND(SUM(duration),1), COUNT(DISTINCT task_id), GROUP_CONCAT(DISTINCT try_number) FROM task_instance WHERE dag_id='offline_lakehouse_pipeline' AND run_id='scheduled__2026-09-26T19:00:00+00:00'` → **`9 | 1992.2 | 9 | 1,2`**；逐任务显示 `ads_layers` 为 **try_number=2**；代码 `measure-latency.sh:296-302` **不过滤 `try_number`**；文档 `PERFORMANCE.md:152-155` 只解释了 48 分钟间隔。
判定：1992.3 s 的准确语义是"**一次成功 run 中各任务最终 attempt 的 `duration` 之和**"，不是"9 个逻辑阶段各跑一次"。文档未说明 → 表述须改。

**【P1-30】关联 11.7 ms 采集时维表为空、JOIN 未真正发生**
证据：`PERFORMANCE.md:69`（**行数 0 ⚠️**）、`:256-261`（空表事实）；修复 `DECISIONS.md:133`（提交 `b195d19`）；L5 只读同口径重跑 → `row_count=10`、中位数约 **7.4 ms**。
判定：**不是数字错，而是该格的"指标含义"与用途不符** —— 论文/答辩把它当作"两表关联能力"的证据（`REPORT_DRAFT.md:1209`、`DEFENSE_SCRIPT.md:185`、`DEFENSE_SLIDES.md:214`），而采集时**关联没有真的发生**。
修法（二选一，成本都很低）：**重采该指标**（脚本未改、缺陷已修）或在表下加注"采集时维表为空，该值为 JOIN 空表开销；修复后同查询中位数约 7.4 ms（2026-09-27 复核）"。

**【P1-31】批量耗时的来源说明过期（文档称"日志"，代码读"元数据库"）**
证据：`PERFORMANCE.md:320-325` 仍称"`measure-latency.sh batch` 读的是 `/opt/data-platform/airflow/logs/...` 下的任务日志…脚本里的日志路径分支**需要后续校正**"、脚本文件头 `measure-latency.sh:15`"取自既有日志" vs 代码 `:215-224`（已改为元数据库）与 `:262-312`；而服务器上 `dag_id=offline_lakehouse_pipeline` 日志目录**不存在（DAGDIR-ABSENT）**，当时采集日志第 3 类只有 `[SKIP]`；`SPRINT_12.md:289,412` 已记"✅ 已修正"。
判定：**代码对、两处文档过期** —— 不影响 1992.3 s 的数值，但会被外部审查者当成"文档与实现不一致"的反例。

**【P1-32】性能结论缺"不构成生产 / 高并发证明"的限定语**
证据：`REPORT_DRAFT.md:1186-1303`（§6.5 全节无此限定；"未做并发/压力测试"只写在**第 7 章不足表** `:1367`，离性能表很远）；`DEFENSE_SCRIPT.md:181-193`（第 10 页全文无并发边界）；`DEFENSE_SLIDES.md:208-217`（第 10 页 4 条均无）；`PERFORMANCE.md:333` 只说"内存不允许"。
判定：**数字一个都不用改**，只需加一句边界（范本句见 §11）。

### G. Agent 主张边界

**【P1-33】"Agent 无法读取白名单外的表"主张大于证据范围**
证据：同 P0-1（线上复现）；另 `verify-sprint-10.sh` 的两路径一致性断言对逗号表**两侧一起"绿"**；L4 明确写：**不可写**"Agent 无法读取白名单外的任何表"。
修法：改为"Agent 进程**不持有可用的数据库凭据**，其全部取数必须经过 SQL 守卫与只读账号；守卫对**显式声明的**数据源（`FROM`/`JOIN`）强制表级白名单"。

**【P1-35（审计期新增）】并发产出的答辩/续作材料引用了审计结论的**旧状态**，会与最终判定直接冲突**
证据：`docs/thesis/DEFENSE_QA.md`（**15:09:16 新增**，88 KB）Q4"行为事件到底是 20000 还是 40000？"仍写
"**本次审计未能定案**…只标注'未定性'，两个数都保留、不合并、不取平均、不擅自选一个"，
并在"依据"里指向 `docs/thesis/EVIDENCE_MATRIX.md 第 2 节与 §2.1（`UNRESOLVED`）`" ——
而 L7 已于 **15:08:54** 判定 **B（40000 判为错误，基准值 20000）**，本文件 §2.1 也已同步为"已定案"。
判定：这是**审计结论落盘后产生的新不一致**（不是审计线漏审）；风险等级 P1，因为它是**答辩现场的问答口径** ——
若照着念，会在"核心数字"上给出与事实源相反的答案，并引用一份**已经不这么写**的文件。
处置：把 Q4 改写为按 §7.1 的口径（20000 = 基准值，五条独立证据；40000 保留并标注为当时的观测误差；
补充"行数 = distinct `event_id` 基数 ≠ 链路处理量"）；其余并发产出物（`DEFENSE_DEMO.md`、
`RESUME_PROJECT.md`、`外部审查包.md`、`QA_PREP.md`、`REPORT_OUTLINE.md`）需做一次**同口径复查**。

**【P1-34（审计期新增）】"实时链路行数与源端 1:1"的表述忽略"重放 29 次 + `UNIQUE KEY` 去重收敛"口径**
证据：Kafka `dwd_traffic_behavior_detail` topic latest 合计 = **580000 = 29 × 20000**；交易域三表同比值（174000/6000、156774/5406、7366/254）；Doris `SHOW ROUTINE LOAD` `rl_dwd_traffic_behavior_detail`：`loadedRows=580000`、`totalRows=580000`、`errorRows=0`、`unselectedRows=0`、`committedTaskNum=29`；而 Doris 表 `COUNT(*)` 分别为 20000 / 6000 / 5406 / 254；表定义为 `UNIQUE KEY(event_id)` + `enable_unique_key_merge_on_write=true`（`sql/doris/10_dwd_tables.sql:101-117`）。
判定："实时表行数"= **按主键去重后的行数**，**不等于**链路处理量；"与 MySQL 或源端 1:1"只在"按主键去重后的行数"这一口径下成立。
影响：论文若写"实时链路处理了 20000 条行为事件""实时 DWD 与 MySQL 1:1 一致"而不带口径，会被追问"Flink 实际产出了多少"（答案是 29 倍）。→ 必须补一句口径说明。

---

## 5. P2

### 数量：**42**

> 说明：P1/P2 的编号在 `EVIDENCE_MATRIX.md` §9 有索引；以下逐条给出"位置 + 一句话"。
> 部分条目是**审计线之间对同一事实的分级分歧**，已按"就高"处理并在条目内注明。
> **原 43 条中的 P2-9 已在审计窗口内闭环 → 见 §5.1，不计入本计数。**

**对账脚本与注释（4）**

| 编号 | 内容 |
| --- | --- |
| P2-1 | `verify-sprint-5.sh:870` 的 `CONCAT(batch_windows, " 个窗口，不一致 ", …)` 里 `"` 是 **U+0022**（已用字符码核对），在**单引号**实参内不会被 shell 剥离 → 进入 Spark SQL 后 `"…"` 是**标识符**而非字符串字面量，该 `CONCAT` 很可能解析失败，并被 `lake_scalar` 的 `\|\| true` 吞成空串 → **这一行不可能打印出"19643 个窗口，不一致 0 个"**。对照 `:532` 的 `CONCAT_WS("\|", …)` 无内嵌字面量故无事。**"是否真报错"属未实测**（本阶段只读且未跑该脚本） |
| P2-2 | `verify-sprint-3.sh:281-282` 幂等检查无绝对值断言：两次查询都返回空时 `lake_scalar` 的 `\|\| true` + `${out:-0}`（`:74-76`）让两侧都是 `0` → `0 = 0` **误报 OK**。对照 `verify-sprint-5.sh:262-265` 是正确写法 |
| P2-3 | `08_traffic_reconcile.sql:25,30` 注释写"**6 个**行为计数""上面 **8 个列**"，代码实为 **5 个**行为计数、共 **7 个**判据列（`:145-153`）→ 注释不可当证据 |
| P2-4 | `verify-sprint-5.sh:532-540` 只验 `mismatched_windows` / `is_pass` / `batch_windows>0`，**未断言** `realtime_windows ≥ batch_windows × 0.9`（作业 `:265-273` 有、脚本没有） |

**Iceberg 代码与文档（5）**

| 编号 | 内容 |
| --- | --- |
| P2-5 | `migrate_parquet_to_iceberg.py:250-253` 的"源端非空守卫"**恒真**：把说明字符串 `"行数 > 0"` 传进 `actual` 形参，`_common.py:262-265` 的 `ok = bool(actual)` → `bool("行数 > 0")` 恒为 `True`，`lambda: False` 从未被调用。实测两次运行各表都非空，故**未污染** 60/60 与 70/70 的分母；但"防线设计意图未生效"。修法：`checker.check_true(name, src_n > 0, "行数 > 0")` |
| P2-6 | **分区布局与 schema 等价性无任何迁移后断言**，且存在两条静默路径：① `partition_columns()` 解析失败 → `plan.partition_cols == []` → 建出**无分区表**，而行数/金额/Provider/库表数**四项照样全绿**（`:145-172`、`:186-189`）；② `CREATE TABLE **IF NOT EXISTS**`（`:203`）在目标表已存在时**静默复用旧定义**，而 `INSERT OVERWRITE` 是**位置映射**（`:210-211`）→ 列序/类型不同也不报错 |
| P2-7 | 把**配置属性**写成**实测结果**：`SPRINT_5.md:337`"Iceberg v2，HiveCatalog，snappy parquet"（`format-version`/codec 无任何读回断言）；`migrate_parquet_to_iceberg.py:16-22` docstring"迁移后类型不一致这一类问题**从根上不会出现**"属**设计论证**（`V2/V6/V10` 未验证） |
| P2-8 | `SPRINT_5.md` §9.6 L574 称验收脚本里有"**库表数 = 23** 的断言"，实际代码**刻意不用数量断言**（`:400-410` 注释解释"判据要能定位问题"），改为**逐个表名核对（`:429`）+ 反向无多余表（`:445`）**；库里实际表数只 `printf` 打印（`:450-451`）、**无 `check`** → **实际检查更强**，改正后对论文有利 |
| ~~P2-9~~ | ~~`AGENTS.md` §15.10 用"18 张表 / 60/60"描述阶段 4，与 §15 状态行"23 张表 / 70/70"并存，两处都没注明"这是两次范围不同的运行"~~ → **已闭环（见 §5.1）** |

**Agent（6）**

| 编号 | 内容 |
| --- | --- |
| P2-10 | `services/agent/app/config.py:96-102`：`load_env()` 读 `.env` **文件体**时不按 `_AGENT_KEYS` 过滤（白名单只作用于进程环境变量 `:103-109`）→ Agent 进程内存里确实短暂持有 `MYSQL_ROOT_PASSWORD`/`DORIS_ROOT_PASSWORD` 等。**当前非可利用漏洞**（无驱动、无读点、无直连）；但**不可写**"Agent 进程从不接触 .env 中的库口令" |
| P2-11 | `knowledge/synonyms.json` 实测 `synonyms` **55** 条，而 `SPRINT_9.md:448`、`QA_PREP.md:448`、`REPORT_OUTLINE.md:364` 写 **54** 条 |
| P2-12 | `docs/PROJECT_DESIGN_V1.md:171` 仍写"检索 \| **RAG（向量检索）**"，与实现（词法检索、`vector_store: None`）冲突 → 改为"RAG（词法检索：BM25 + 同义词表）" |
| P2-13 | `sqlguard.py:122-124` 的 LIMIT 正则以 `$` **锚定句尾**：UNION 前序各支的 LIMIT **不受 `max_limit` 约束**（探针：`LIMIT 100000 UNION ALL … LIMIT 5` 中 100000 未被收敛）；LIMIT 后跟子句时守卫**追加**第二个 LIMIT 生成**非法 SQL**（fail-closed，但错误信息指向语法、排障方向被带偏） |
| P2-14 | 对抗用例缺口：`export/import/replace/rename/backup/restore/kill/shutdown` + 语句中间位置的 `drop` **无任何用例**（探针实测这 8 个**当前都返回 `FORBIDDEN_KEYWORD`** → 规则有效、缺的是回归保护）；孤立 `*/`、子查询内越权 `FROM`、非句尾 LIMIT 亦无用例；非 ASCII/全角**无判据**（亦未发现可利用手法） |
| P2-15 | Agent 侧 `extract_tables`（`tools.py:74-99`）与守卫侧是**两份独立正则**（**有意为之**，注释说明"引入守卫模块会给人 Agent 也持有权限判断的错觉"）；代价是规则可能漂移，而验收时的"两路径 tables 一致"断言**不覆盖逗号连接**（P0-1 修好后应把该 SQL 补进去） |

**性能（7）**

| 编号 | 内容 |
| --- | --- |
| P2-16 | 三处"**每项** n=7"把 Agent 项也包进去：`PERFORMANCE.md:16`、`:342`、`SPRINT_12.md:285`（Agent 实际 n=2）→ 改为"只读接口类每项 7 次；Agent 项 n=2" |
| P2-17 | 合计 1992.3（**逐项四舍五入到 0.1 s 后相加**）vs 元数据库 `SUM(duration)` = **1992.2** → 差 0.1 s，属口径，非编造；引用时注明求和口径 |
| P2-18 | 1992.3 s 的**原始取数输出缺失**：当时采集日志第 3 类只有 `[SKIP]`，该数字是另取的（元数据库查询或 09:22 之后的补跑），仓库与 `/tmp` **无留痕** → 建议补存一份输出（本次只读复核可作补登记依据） |
| P2-19 | 缓存边界未说明：源码只把"冷启动"限定为"连接与元数据"（`measure-latency.sh:20-21`），**未说明 OS/DB 页缓存**；`REPEAT=7` + 1 次预热 = **同一 SQL 连打 8 次**、表仅万行级 → 这组数字**结构上不可能包含冷缓存代价**；`DATA_AND_LIMITATIONS.md` D 组**无此条** |
| P2-20 | `verify-sprint-12.sh:395-432` 对性能文档只做"**有没有数字**"（≥12 处 + 关键词 + `check_not_contains "⏳待补"`），**不校验数字是否等于采集输出** → 无闭环 |
| P2-21 | `docs/thesis/外部审查包.md` **没有性能小节**（只在 `:68` 一句索引点名 `docs/PERFORMANCE.md`）→ 若需审查性能，应补一节"性能边界" |
| P2-22 | `scripts/perf/measure-latency.sh` 健壮性：Agent 分支 `code="${out%% *}"` 提取了 HTTP 码但**未用于判定失败**（`:338,345`，与 `measure_get`/`measure_sql` 同样只打印）→ 超时/限流可能被当成"延迟数字"；`REPEAT` 对第 4 节**无效** |

**文档状态与残留（14）**

| 编号 | 内容 |
| --- | --- |
| P2-23 | Sprint 13 状态**口径不一**：项目 `README.md:908` 无标记 / 根 `README.md:70`"🔄 材料整理中" / `AGENTS.md` §15 Sprint 13 行已改为"🔄 进行中（Prove + Audit + Close）"（15:04:46）但**其链接指向不存在的 `docs/sprint/SPRINT_13.md`** → 三处需统一（建议 🔄 进行中 + 材料已定稿 + 链接可用） |
| P2-24 | 项目 `README.md:43-57` 验收块标题只到"Sprint 0/1/2/3/6/7"（缺 4/5/8~12），且 `:56` 含"公网 HTTPS 64/64 返回 200"（当前明文形态下会误导） |
| P2-25 | 项目 `README.md` §14 测试仍是 Sprint 0 数字（`:696` 单元 24 / `:701` 冒烟 27），与 `:913` 的 191/138、`AGENTS.md:609` 的 353 并存 → **同一文件里"测试"有三个互不一致的数字**；`:703-715` 的 10 项冒烟清单也已不是完整清单 |
| P2-26 | 项目 `README.md:69-86` TOC 缺"数据质量校验与监控（Sprint 11）""文档索引"两节；`:1061-1073` 文档索引表缺 `SPRINT_4/5/10/12` 四行；`:921-994` 是**无归属章节的散文**（§16 code fence 之后）→ 这正是"过期内容长期留存"的**机制性原因** |
| P2-27 | `SPRINT_8.md:7`/`SPRINT_9.md:7`/`SPRINT_10.md:7`/`SPRINT_12.md:7` 头部状态一律"见第 N 节「实施记录」"，**无一行可被四方比对的结论** → 建议各补一行（71/0/0、59/0/0、84/0/0、32/0/0）并保留"详见第 N 节" |
| P2-28 | `SPRINT_0_VERIFICATION_STATUS.md:195`"服务器无 SWAP"**已被实测否证**（`Swap: total 4095 used 3238`，S8 §2.1；且 `scripts/setup-swap.sh` 存在、`AGENTS.md:493` 有引用）→ 改为"已处理：Sprint 3 内存事故后引入 swap 兜底（当前实测 4095 MB，已用 3238 MB）" |
| P2-29 | 项目 `README.md` 技术栈与架构三处冲突：`:210`/`:235` Python 版本写法正是 `DECISIONS.md:230`（D12）判定为**错的**那条（宿主 3.12.3 / 容器 3.13.14 应拆两行）；`:219-221` 把**已落地**组件列为"后续 Sprint 引入"；`:4/:105/:166` 仍写 **HDFS**（`AGENTS.md:600` 明写"HDFS 因内存不足改用 S3A"） |
| P2-30 | 项目 `README.md:939-948`"一键验收"清单只有 10 条，**缺 `verify-sprint-0.sh`、`verify-sprint-10.sh`、`verify-sprint-12.sh`**（脚本实数 13） |
| P2-31 | 口径/精度写法不统一：质量校验"25 条"（`README.md:896,1011`）vs Doris 实跑 24（`AGENTS.md:905`）**未加口径注**；`3.9 GB`（`PERFORMANCE.md:333`、`REPORT_DRAFT.md:1367`）与 `3921 MB`（`DATA_AND_LIMITATIONS.md:280`）是同一读数的两种精度 |
| P2-32 | 命名与残留：根 `README.md` 与项目 `README.md` **同名**；`AGENTS.md:593` §15"当前 Sprint 状态"与 `:853` §15.9"边界要求"标题语义重叠（后者含进度句）；仓库根存在 **0 字节**悬空文件 `DATA_AND_LIMITATIONS.md`（易被误认为论文材料副本）；根 `README.md:59-60` 把 Sprint 6 排在 Sprint 2 前**未说明**（`AGENTS.md:104-106` 有说明） |
| P2-33 | `SPRINT_12.md:284`"**63 条**（60 passed + 3 xfailed）" vs `外部审查包.md:239`"对抗用例 **63 passed、0 xfail**" —— 是**两个时点的真实值**，非矛盾，但**必须带时点**；另 `AGENTS.md:1019`（§15.12 首句）"本节只汇总有书面或日志证据的数字；取不到证据的一律写'进行中（待补证据）'"而本节已无任何"待补"占位 → 该句应改为历史说明或删除 |
| P2-34 | `DECISIONS.md` 第三节已知限制表两条过期：`:272`"流量域离线指标（**Sprint 4 正在补**）"（同文件 `:70` 已写"归档（有源）已完成"）；`:271`"MinIO root 凭据…计划 **Sprint 11**"而 Sprint 11 已结束 |
| P2-35 | `外部审查包.md:103`"判断'**不修的 4 项**'"而其 §5.2 表只有 **3 行**；`:300`"**12 份**阶段任务书与实施记录"与 `docs/sprint/` 实际（12 份 `SPRINT_N.md` + 2 份 `VERIFICATION_STATUS`）不符 |
| P2-36 | `DEVELOPMENT_LOG.md:1529-1557` 最后一条是 2026-09-27 事故记录，**恢复过程、Sprint 10/11/12 服务器端收口、Sprint 13 均未入日志**，文末 7 条 `- [ ]` 待办仍挂着已完成项（如 `requirements-dev.txt` 已建） |

**绝对化表述与措辞风险（7）**

| 编号 | 内容 |
| --- | --- || P2-37 | A-03 `REPORT_DRAFT.md:369`"能做的查询与人类用户**完全相同**""**想绕过也没有入口**" → ① 约束相同 ≠ 能力相同；② "不存在绕过路径"是**不可穷尽验证**的全称断言（且 P0-1 已证存在绕过）。**不可写** |
| P2-38 | A-04 + A-06"**零丢失**"×2：`README.md:870`、根 `README.md:63`（判断与数字都在，缺范围）→ 改为"**本次验收窗口内未观察到丢失**"，并**补一句判据性质**：该等式是**归档作业自己的自检断言**（`archive_behavior_events.py:186-192`），**不是两条独立证据**；以 `REPORT_DRAFT.md:1150` 为**范本句式** |
| P2-39 | A-05 `README.md:989`"nginx 自己发的 502 **必然**带 `Server: …`" → 改为"**在默认配置下**必定带"（若 `error_page` 被改写或 `server_tokens` 关闭即不成立）；并随 P1-14 一并改到 VPN 结论 |
| P2-40 | A-07 `外部审查包.md:177`"逐表行数与 Parquet 侧｜**完全一致**"（同表已有限定，但与本包 §5.4 的 20000/40000"未定性"直接冲突）→ 改为"**在迁移的 18 / 23 张表范围内，逐表行数与金额 SUM 均未发现差异**" |
| P2-41 | A-09 `REPORT_DRAFT.md:642`"逐表行数与 Parquet 侧…**完全一致**"（紧接的下句自己就否认了行数的证明力："行数相同不代表内容没坏"）、`:731`"（**零丢失**）" → 前者改"**未发现差异**（范围：迁移清单内的 18 / 23 张表）"，后者改"（**本次验收未观察到丢失**）" |
| P2-42 | 跨文档绝对化表述群（L6 清单，逐行位置见该文件"绝对化表述清单"）：`README.md:23/48`"**精确对账**"（未说去重指标不可上卷）、`:40`"所以当前走明文是**安全的**"、`:50`"ODS 5 张表逐表与 MySQL **精确一致**"（行数相等是弱判据）、`:612-625`"读取**真实写入 MySQL 的数据**"、`:879`"三重上界**保证一定停得下来**"、`:993`（443 隧道）；`REPORT_DRAFT.md:12/28`"**秒级**指标"（介质能力 ≠ 实测时效）、`:16`"12 个开发阶段**全部验收通过**"（未提 Sprint 5 一项未归零）、`:101`"四类攻击**全部被拒**"、`:1166`"下表是本项目的**全部**验收记录"；`外部审查包.md:25`"**每一步**都能复现"、`:115`"**每个数字都是服务器上跑出来的**"、`:225`"**它为什么能支撑**逐窗口对账"、`:237`"返回 10 行**真实**类目 GMV"；`DEVELOPMENT_LOG.md:122`"**100% 保留**"；`SPRINT_0_VERIFICATION_STATUS.md:140`、`SPRINT_5.md:344`、`SPRINT_3.md:51/61/133`、`SPRINT_6.md:278`、`SPRINT_4.md:119` 的"完全一致" |
| P2-43 | （审计期新增）**`event_id` 非全局唯一 + 实测出现两代编号**：代码 `common.py:99-118` + `kafka_events.py:264,275-276`（每次 `behavior_events()` 都新建 `EventIdGenerator(prefix=4)`，**从 `evt_40001` 重新开始**）；而当前 topic 内 20000 条消息的 `event_id` **全部互不相同**，落在两段：`40001–49999`（9999 个，4~5 位）与 `410000–420000`（10001 个，6 位），中间 1 个缺口（`49999 → 410000`）。→ 说明"每轮从 40001 重排"与实测分布**不完全相容**（可能是同一进程连续编号进入 41 万段、或 topic 曾被重建）；无论哪种，**"`event_id` 全局唯一"不成立**，下游正确性依赖 `UNIQUE KEY` upsert 去重（成因未定 → UNVERIFIABLE-23）。论文/文档**不得**把 `event_id` 当作全局唯一键，也不得用它证明"1:1 一致" |

---

### 5.1 审计窗口内**已闭环**的 P2（不计入 §5 的计数）

| 编号 | 原问题 | 闭环证据（只读复核） |
| --- | --- | --- |
| **P2-9（原）** | `AGENTS.md` §15.10 用"18 张表 / 60/60"描述阶段 4，与 §15 状态行"23 张表 / 70/70"并存，两处都**未注明"这是两次范围不同的运行"** | `AGENTS.md` 更新后 §15.10 已加**适用范围直注**："这是**阶段 4 的首次迁移**，清单 **18 张表**、校验 **60/60**…之后清单扩到 **23 张表**并复跑，校验 **70/70**…**两次的运行范围不同，不是'同一判据的两次结果'，也不能互相替代**；且 `70/70` 只有**汇总记录、无逐项细目**"，并补了结论范围限定（只覆盖表身份/行数/6 个金额列整表合计，**不覆盖**逐行/全字段/分区/schema 等价性）；§15.12 同步标注 → **本条闭环** |
| （同时闭环的相关表述） | `AGENTS.md` §15.8 / §15.9 的"零丢失"与"表行数"口径 | §15.8 已补"判据是**归档作业内的自检**、单次验收窗口等式、Kafka **副本数为 1**，写'本次验收窗口内未观察到丢失'"，并补"行数 = distinct `event_id` 基数 ≠ 链路处理量（580000 = 29 × 20000）"；§15.9 同步 → **与 W-12 / P1-34 同向**，属**表述已达标**（本报告仍保留 W-12 作为**写作规范**，供其它文档对齐） |

> **未闭环的相邻项（仍需处理）**：`外部审查包.md:124/:131/:177`（无出处数字 + "完全一致" + §5.4 仍写"未定性"）
> → 见 **P1-21 / P1-22 / P2-40**；`DEFENSE_QA.md` Q4 的旧口径 → 见 **P1-35**。

---

## 6. Core Claim Audit

> 每项给 **Status / Evidence / Boundary**。Status 取值与 `EVIDENCE_MATRIX.md` 一致。

### 6.1 Data Reliability

- **Status：PARTIALLY_SUPPORTED**
- **Evidence**：MySQL 事实源与三层落地规模一致（user 1200 / product 600 / orders 6000 / payment 5406 / refund 254 —— 前 3 项经 Doris 只读 `COUNT(*)` 复核，payment/refund 经 **Kafka 源 topic 位点**独立复核：`payment_event` latest 合计 = 5406、`refund_event` = 254）；行为事件 20000（Kafka `behavior_event` latest 合计 = 20000）；数据质量校验 25 条清单（Doris 侧实跑 24 通过 / 0 失败 / 1 跳过）；`--proof-fail` 失败路径可复现（退出码 1）；批流对账 0 mismatch（§6.2）；Iceberg 迁移行数与金额合计无差异（§6.3）。
- **Boundary**：① "一致"的口径是**按主键去重后的行数**，而 Flink 实际产出 29 倍记录（P1-34）；② `ods_behavior_event` 归档的**具体行数本次未取到**（L7 §3 待补，仅目录级 703 分区 / 19M）；③ 数据是**合成数据**，不含真实业务分布；④ 质量校验"25 条"未逐段点算 `checks.conf`；⑤ 20000 这个数**只是当前值**，`SPRINT_3.md:52` 的 40000 无法复现（P1-8）。

### 6.2 Batch-Stream Consistency

- **Status：PARTIALLY_SUPPORTED**（两域均为该状态，但**强度不对称**，见 Boundary）
- **Evidence**：交易域 `realtime_windows = batch_windows = matched = 11458`、`mismatched = 0`、`is_pass = 1`、明细表 11458 行不一致 0（主控 2026-09-27 14:55:12 只读实测）；其中 **5504 个窗口两侧 GMV 均为 0（平凡一致，48.03%）**、**5954 个窗口有业务量**；最近 3 个批次两侧 GMV 均为 **51890375.77**。流量域 `19643/19643`、`realtime_only = 0`、`batch_only = 0`、`mismatched = 0`、`is_pass = 1`。判据是**逐窗口**（`FULL OUTER JOIN` on `window_start`，不是只比汇总），单边窗口不会被静默过滤，作业内 `Checker` 失败即非 0 退出。
- **Boundary**：
  1. 交易域**没有**"单边窗口 = 0"断言（只有 ≥90% 覆盖率容差）→ 不能写"两侧窗口集合完全相同"（P1-1）；
  2. `is_match` 是**补零后相等**，`NULL` ≡ `0` → 结论强度**来自数据分布**（本次两侧窗口数恰好相等），不是来自断言（P1-2）；
  3. **5504 个零-零窗口是平凡一致**，不能算作独立证据；承载判别力的是 5954 个窗口；
  4. 交易域**派生比率列完全没进对账**；流量域比率列**不进 `is_match`**、且实时侧 anomaly **不阻断退出码**；
  5. **`click_rate` anomaly 必须显式保留**：`realtime_rate_anomaly_windows = 1`（`view_cnt=2`、`click_cnt=1`、`click_rate` 记 0.0000 而按口径应为 0.5000）、`batch_rate_anomaly_windows = 0` —— 即 "0 mismatch" 与 "实时链路存在已知数据缺陷" **同时成立**；
  6. 证据只覆盖**对账区间**（区间外含尾部安全边界**明确未比**）；区间内比对窗口数比表行数少 1（11458 vs 11459、19643 vs 19644），是口径截断（P1-9）；
  7. 结论建立在**合成数据集**上 → 不能写"生产环境已验证"。

### 6.3 Lakehouse-Iceberg

- **Status：PARTIALLY_SUPPORTED**（"迁移未改变数据"无限定表述）；**次级主张中 4 项 SUPPORTED、1 项 PARTIALLY_SUPPORTED、1 项 UNVERIFIABLE**
- **Evidence**：23 张表 `Provider == iceberg`（逐表 `DESCRIBE EXTENDED` 读回，取不到值也算失败）、23 张标 `COUNT(*)` 相等、23 条金额列整表合计相等（`DECIMAL` 精确比较、无容差）、库表数 == 清单表数；验收脚本另有**逐个表名核对 + 反向无多余表**、5 张流量表行数、1 张表的 `Provider`、快照可查与时间旅行行数一致；`60/60 = 18+18+23+1` **经代码 + 逐表 DDL 独立复算完全吻合**（SUPPORTED）；`70/70 = 23+23+23+1` 复算吻合但**无原始日志/逐项细目**（PARTIALLY_SUPPORTED）；**断言失败即非 0 退出**（真门禁）。
- **Boundary**：**全部证据都是聚合量与表身份，没有一条触及行内容** —— 字符串字段、时间字段（含 Spark `timestamp` ↔ Iceberg with/without zone **未判定**）、NULL/空串、decimal 精度（6 个金额列之外）、分区布局与分区值、schema 等价性（列序/列名/可空性）、重复行与唯一性、快照元数据、回滚可行性、压缩编码与 `format-version`、行级完整性 —— **V1–V11 全部未验证**；工程内"源端非空守卫"**恒真无效**（P2-5）；分区解析失败会**静默退化为无分区表**而四项判据仍全绿（P2-6）。→ **不能写**"完全无损 / 逐行一致 / 字节级一致 / schema 已验证 / 分区一致 / 可回滚已验证 / snappy 已核实"。

### 6.4 Agent Security

- **Status：PARTIALLY_SUPPORTED**（"Agent 不能越权取数"这一最高层主张）；其中**组件级**主张 SUPPORTED，**边界完整性**主张 **NOT_SUPPORTED**
- **Evidence**：Agent 进程**无 DB 驱动依赖、无直连代码、无凭据读点**（四重证据）+ systemd 非 root 加固；取数只有 HTTP / MCP 两条路径且终点同一个 `sqlguard` + 只读账号 `agent_ro`（仅 `SELECT_PRIV`，写操作被 Doris 拒绝）；守卫 5 层（SELECT-only / 无分号 / 无注释 / 26 关键字 / 18 张表白名单 / 强制 LIMIT）；**已实测被拒**：写与管理类语句、多语句、注释注入、元数据探测、**显式**未授权表、文件读写与 DoS 函数、`max_limit<=0`；**端到端**：DELETE 经 MCP 透传后返回 `NOT_SELECT`，与直接 HTTP 400 同码。
- **Boundary**：
  1. **P0-1（线上复现）**：逗号连接的隐式连接表**不校验、不报血缘** → "Agent 只能读到白名单内的表"**不成立**；且既有"两路径 tables 一致"断言对此**会一起变绿**；
  2. `agent_ro` 的授权是**整库**（`ecommerce.*`），因此"守卫白名单"与"数据库授权"之间有落差；
  3. Agent 进程内存里**短暂持有** `.env` 全量键值（P2-10），虽不可利用，但**不可写**"从不接触库口令"；
  4. LIMIT 收敛只对句尾生效（UNION 前支不受限，P2-13）；
  5. 10 个禁用关键字/孤立 `*/`/子查询越权/非句尾 LIMIT **无对抗用例**（规则有效、缺回归保护，P2-14）；
  6. **不可写**："Agent 无法读取白名单外的任何表"／"不存在绕过路径"。

### 6.5 Reflection-Retry

- **Status：SUPPORTED（能力与触发）；production usage 为 UNVERIFIABLE**
- **Evidence**：图结构 `retrieve → plan → execute → validate → reflect → plan → execute → validate → summarize`（`reflect → plan` 回到 plan **而非** execute）；`retries` **只在真正要重规划时自增**（`graph.py:439`，设计自述"这样'重试了几次'是可信的数字，而不是'循环跑了几轮'的别名"）；三重终止上界（`max_retries` / `max_tool_rounds` / `total_timeout`）；进 `reflect` 需**同时**满足"存在 blocking issue（警告不算）"+"额度未用尽"+"未超时"；失败信息是**真实错误原文**（含守卫 `code`），并被拼进**下一轮规划上下文**（`node_plan:229-244`，且规划器每轮用干净上下文，`:210-215` 记录了"复用全局消息流导致模型原样重放同一条错 SQL"的实测踩坑）。**mock 单测**断言精确图路径与 `retries == 2`、`len(toolbox.calls) == 3`、`path.count("reflect") == 2`；**线上验收**独立要求轨迹含 `reflect` 与 `plan`，并断言失败码命中 `TABLE_NOT_ALLOWED`。
- **Boundary**：① `= 2` **只出现在 mock 单测**（LLM 与工具均为预设替身，不发 HTTP）；线上验收断言的是 **`retries ≥ 1`**；② **没有**任何线上 `retries` 分布统计 → "平均重试 2 次 / 生产重试率"**不可写**；③ 触发用的失败是脚本**主动构造**的问法，不是用户自然提问；④ 警告（0 行 / 截断 / NULL）**不触发重试**，只要求如实说明 —— 论文不得把"0 行"说成"触发了反思"。

### 6.6 Retrieval-RAG

- **Status：SUPPORTED**
- **Evidence**：BM25 三要素齐全（`k1=1.2`、`b=0.75`、idf 含 `+0.5` 平滑、文档长度归一化、词频饱和、标题加成）；中文 **CJK bigram 自实现**（不依赖第三方分词库，`_CJK_RUN_RE`，孤立单字默认丢弃）；**显式同义词表**（`synonyms.json`：`synonyms` 55 条、`stopwords` 47 条、`term_weight` 16 项）；**零向量 / 零 embedding**（全目录 grep 仅命中 3 处"不引入"注释；`retrieval.py:254` `vector_store: None`；`backend = "lexical-bm25"`）；语料 = `metrics.md`（155 行）+ `kafka_topics.md`（196 行）+ `layering.md`（63 行）+ 16 张表结构（HTTP，10 分钟 TTL）；命中理由可解释（`hit.matched_terms`），结果带**来源文件与行号**。
- **Boundary**：① 主张"检索是词法检索"的范围**小于**已有证据（证据还额外证明了无向量成分）→ 故为 SUPPORTED；② 同义词条数文档写 54、实测 55（P2-11）；③ `PROJECT_DESIGN_V1.md:171` 残留"RAG（向量检索）"（P2-12）；④ 语料"65 篇"的分解自洽但**未逐篇装载复算**；⑤ 检索单测"26 passed"未重跑；⑥ **不可写**"向量数据库 RAG / Embedding RAG / 语义检索 / 语义相似度召回 / 混合检索（BM25+向量）/ reranker 重排 / 用 jieba 分词"。**可以放心写**："RAG + Metadata（**词法检索**）；BM25 + 显式同义词表，未引入向量库" —— "RAG"作**架构角色**成立，"词法检索"作**实现**成立。

### 6.7 MCP

- **Status：SUPPORTED**
- **Evidence**：MCP 服务暴露 **4 个只读工具**（与 Agent 路由集合一致）；**同一 SQL 经 MCP 与直接 HTTP 的结果逐字段 IDENTICAL，`executed_sql` 也相同**；`verify-sprint-10.sh` **84/0/0**；拒绝语义**真实透传且来自 sqlguard**（`is_error=False` + `structured_content.error.code = NOT_SELECT` + 底层 HTTP 400 同码）；MCP 失败**不回退** HTTP（`tools.py:148-162`）；`mcp==2.2.0` 钉版。
- **Boundary**：① 比对口径是**规范化 `rows` 逐字段 + `executed_sql`**，不是字节级报文；② "两路径 tables 一致"断言**不覆盖逗号连接**（两侧都不识别逗号表 → 会一起"绿"），因此该断言**不能**发现 P0-1；③ Agent 侧 `extract_tables` 与守卫侧是**两份独立正则**（有意为之），漂移只在验收时暴露（P2-15）。

### 6.8 Data Quality

- **Status：PARTIALLY_SUPPORTED**
- **Evidence**：`verify-sprint-11.sh` **61/0/0（8 步）**；25 条校验覆盖 7 类（行数非空守卫 / 主键唯一 / 关键字段非空 / 枚举合法 / 层间一致 / 金额关系 / 新鲜度，交易域与流量域都覆盖）；Doris 侧 **24 通过 / 0 失败 / 1 跳过**（spark 项需 `--with-lake` 另驱动）；**失败路径可复现**（`--proof-fail` 故意改坏期望值 → `[FAIL]` → **退出码 1**，恢复后仍全绿 → 证明失败来自断言而非数据被改坏）；湖仓层间 `DWD == ODS` 逐表不一致表数 0。
- **Boundary**：① 层间校验范围是 **Iceberg 5 张交易表**，不是全层（P1-11）；② "25 条"未逐段点算 `checks.conf`（"25 vs 24 vs 21"三种计数并存，P2-31）；③ 校验覆盖的是**合成数据**的约束，不能外推为真实业务分布下的质量结论。

### 6.9 Performance

- **Status：PARTIALLY_SUPPORTED**；分项：只读接口三项与 `/overview` **SUPPORTED（关联项须加注）**、实时 vs 离线 **SUPPORTED**、批量合计 **PARTIALLY_SUPPORTED**、`/ask` 中位数 **SUPPORTED（n 描述须订正）**、"生产可用/高并发" **NOT_SUPPORTED**、"19.2 s = 数据平台耗时" **NOT_SUPPORTED**
- **Evidence**：**原始采集输出** `/tmp/perf-a.log` 与 `PERFORMANCE.md` §1/§2 逐项吻合（含 n 与 min/max）；硬件（腾讯云 4 核 / 16 GB）、时间戳、现场 load 与可用内存、路径口径（`127.0.0.1`）均已记录；**中位数**（显式拒绝均值）+ min/max；**批量数字经元数据库逐行复核**（9 行、9 个 task_id、`SUM(duration) = 1992.2`）；`/ask` 的 `{"n": 2, "median_ms": 19187.5, "min_ms": 18175.8, "max_ms": 20199.2}`、`retries=1`、SQL 2 条均可核对；**差值法透明**（明写"LLM 延迟不可单独测""不得表述为模型推理耗时"）；实时 vs 离线**主动说明"这个数据规模下测不出来"**并登记 D5 —— 这是本次审计中**边界写得最正确的一条**。
- **Boundary**：① **零并发/压力测试**（三重独立证据）→ 不能声称高并发/生产可用；② 11.7 ms 采集时**维表为空、JOIN 未真正发生**（P1-30）；③ `/ask` **实际 n=2 且无预热**（P1-28）；④ 批量求和**含 1 次重试 attempt**（P1-29）、来源说明过期（P1-31）、原始输出缺失（P2-18）；⑤ **热缓存**口径（同 SQL 连打 8 次）；**冷缓存、公网链路、缓存贡献**均未测；⑥ 论文 §6.5 与答辩第 10 页**缺"不构成生产证明"限定**（P1-32）；⑦ 单机单副本、只读接口无鉴权、Airflow 用官方标注 not for production 的认证方案。

### 6.10 Observability

- **Status：PARTIALLY_SUPPORTED**（已实证的部分）；**告警与 SLO 未审计 → UNVERIFIABLE-26**
- **Evidence**：Prometheus `v3.13.3`（LTS，禁用了 EOL 只剩 3 天的 3.14.0）与 Grafana `13.2.2`，`mem_limit` 各 256m（实测占用 105 MiB / 187 MiB，合计在 512 MB 预算内）；**4/4 抓取目标 up**（prometheus / doris-fe / doris-be / grafana），Doris FE 1564 条、BE 1019 条指标；Grafana 面板 23 个（匿名只读）；`/metrics/` 只放行 `GET/HEAD/OPTIONS`；`/metrics/-/healthy`、`/data/`、`/airflow/` 均 200。
- **Boundary**：① **告警规则 / SLO / 通知链路**在本次六条审计线中**均未覆盖**（只审了采集与看板）；② 公网入口可达性只由文档记录（未做线上探测）；③ `/metrics` 与 Grafana 的**只读**是通过 Nginx 方法白名单实现的，未做渗透测试。

---

## 7. Numeric Consistency

> 规则：同名不同范围的数字**必须给出具体口径**；给不出口径就写 `UNRESOLVED`。**不许"两种口径都对"这类平均化结论。**

### 7.1 `dwd_traffic_behavior_detail`：20000 vs 40000 —— **A/B/C 三选一 → 选定 B**

> **判定来源**：L7（数据差异专项）**§4 判定 = B**（审查完成后于 2026-09-27 15:08:54 落盘，本报告只读复核）。

| 候选 | 命题 | 判定 |
| --- | --- | --- |
| **A** | 两个数**都对**——只是口径不同（"生成量/归档量" vs "表行数"），可并存 | ❌ **NOT_SUPPORTED**：**没有**任何口径能把 40000 解释成当前值。该表是 `UNIQUE KEY(event_id)` + merge-on-write，行数 = **distinct `event_id` 基数**；当前所有只读路径一致给 20000。A 的实质是"平均化矛盾"，本报告**不采纳** |
| **B** | **`40000` 判为错误；基准值是 `20000`** | ✅ **选定**（L7 §4）。依据见下"五条互相独立的证据" |
| **C** | `20000` 判为错误；基准值是 `40000` | ❌ **NOT_SUPPORTED**：无任何当前证据支持（三级链路 + 归档产物 + Iceberg 元数据全部给 20000） |

**选 B 的五条互相独立的证据（全部只读，2026-09-27 14:54–15:0x）**

| # | 证据 | 值 | 出处 |
| --- | --- | --- | --- |
| 1 | Kafka 源 topic `behavior_event` 位点（earliest 全 0 / latest 合计） | **20000** | `6506+6592+6902`（S8 §2.2）；全量消费计数亦 20000（§2.8） |
| 2 | Doris 实时表 `COUNT(*)` / `COUNT(DISTINCT event_id)`（**三次观测一致**） | **20000 / 20000** | S8 §2.4、S8 §3 |
| 3 | **归档作业自己打印**：`[archive] Kafka 实际消息数 = 20000` / `解析完成，有效行数 = 20000`（任务 `success`） | **20000** | S8 §2.11（服务器 `syslog` 留存的 stdout） |
| 4 | Hive Metastore `TABLE_PARAMS.numRows`（`table_type = ICEBERG`） | **20000** | S8 §2.12 |
| 5 | Iceberg 快照历史 **3 个快照**（每次 `overwrite`、703 分区整体重写）`total-records` **全部 20000** | **20000 × 3** | S8 §2.13（从未写出过 40000） |

**为什么 40000 不可能是"两轮累计"**：归档作业读 `earliest → latest` 全量 topic 并以
`INSERT OVERWRITE` + **动态分区覆盖**写入（幂等、不累加，S8 §1.4）；
Iceberg 三次快照的 `added-records`/`deleted-records` 都是 20000/20000（整体替换）。
**40000 会让归档作业自己的硬断言 `归档行数 == Kafka 消息数` 直接失败**，而三次归档运行全部 success（S8 §1.6/§2.11）。

**40000 的唯一出处与性质**：`docs/sprint/SPRINT_3.md:52` 的"事实核查"行，**无观测时间**，
且同行还写着"`dwd_traffic_behavior_detail` … 与 MySQL 1:1" —— 而**流量域在 MySQL 没有源表**
（这正是 Sprint 3 自述、Sprint 4 才归档补齐的缺口）→ 该行的口径表述本身也不成立（S8 §4.1）。
**处理原则**：**保留该数字并标注为当时的观测误差**，不抹掉（符合"禁止删除 unresolved issue"）；
措辞模板见 S8 §7.1。

**40000 的成因：`UNRESOLVED`（历史状态不可回溯），但**不影响**当前值判定**

- 可确认的机制：该表行数 = distinct `event_id` 基数；只要历史上装载过的 distinct `event_id` 一度达到 40000，
  表里就会是 40000 行（S8 §4.2）。
- 合理但**未证实**的推测：2026-09-26 13:18 重建/重灌之前曾有一批**不同** `event_id` 的行为事件被装载，
  "旧批次 ∪ 新批次" 的 distinct 一度达到 40000。
- **无法证实的原因**：13:18 的重建把 Kafka 旧状态整个换掉（earliest = 0、无 `DeleteRecords` 痕迹、
  4 个源 topic 的 `partition.metadata` 时间戳集中在 13:18:11–13:18:17、20000 条消息的 `CreateTime`
  全部落在 **586 ms** 内）；Iceberg 仅保留 3 个快照；`syslog` 只回溯到 2026-09-27 00:00
  → **没有任何一处留存"重建前的 event_id 集合"**。
- 结论：历史侧记为 **UNRESOLVED DATA-STATE DISCREPANCY**；**当前值以 20000 为准**。

**口径澄清（防止把序号当条数）**：`evt_420000` 是**生成器进程内序号**，与 topic 条数**无关** ——
两段编号 `40001–49999`（9999 个）与 `410000–420000`（10001 个）**CreateTime 首尾相接**
（在 13:18:36.934 处切换）、**中间 36 万个序号缺失**、全过程仅 586 ms（S8 §2.3/§2.14）。
→ **不得**写"实时链路累计处理 42 万条行为事件"（S8 §4.3 判为 NOT_SUPPORTED）。

### 7.2 其余同名不同范围的数字（逐对给出具体口径）

| 数字对 | 具体口径（结论） |
| --- | --- |
| **11459 vs 11458**（交易域窗口） | `ads_realtime_trade_1m` 表 **11459** 行；对账区间内实际比对 **11458** 个窗口（两侧求交 + 尾部安全边界截断）→ 差 **1**，属**区间口径**，不是数据缺失 |
| **19644 vs 19643**（流量域窗口） | 同上：`ads_realtime_traffic_1m` / `dws_traffic_overview_1m` = **19644**，对账比对 **19643** → 差 **1**，同为区间口径 |
| **60/60 vs 70/70**（Iceberg） | **两次范围不同的运行**：18 张表那次 `60 = 18+18+23+1`；扩到 23 张表复跑 `70 = 23+23+23+1`。**两个都保留**，不得合并、不得互相替代 |
| **18 vs 23 张表** | 迁移清单的**首轮**与**复跑**范围；5 张新增流量域表不含任何 `MONEY_COLUMNS` 列，故金额条数两次都是 23 |
| **353 vs 381 vs 191/138**（pytest） | **353 passed / 0 failed / 0 skipped / 3 xfailed** = 服务器终局值（`AGENTS.md:1047`）；**191 passed / 138 skipped / 3 xfailed** = 事故恢复前**本机 Docker 未运行**时的历史值（`README.md:913`）；**381 passed** = `外部审查包.md:131`，**仓库内无任何出处** → 该数字对应主张 **NOT_SUPPORTED**（P1-21） |
| **32 vs 42**（`verify-sprint-12.sh`） | **32/0/0** 是权威值（`AGENTS.md:609`、`SPRINT_12.md:287`）；**42/0/0** 只见于 `外部审查包.md:131`，无出处 |
| **84 vs 88** | **84/0/0** 是 `verify-sprint-10.sh`（MCP）的权威计数；`外部审查包.md:124` 的"`verify-sprint-5.sh` **88/0/0**"无出处（Sprint 5 的验收脚本计数在仓库中**未记录**） |
| **25 vs 24 vs 21**（质量校验） | **25** 条清单；Doris 侧默认实跑 **24**（+1 条 spark 引擎项需 `--with-lake`）；`DATA_AND_LIMITATIONS.md:298`（E5）登记的是 **25 vs 21** → 三处口径未统一 |
| **1992.3 vs 1992.2**（批量耗时） | **1992.3** = 逐项四舍五入到 0.1 s 后相加；**1992.2** = 元数据库 `SUM(duration)` 原始和 → 差 0.1 s，属求和口径，须注明 |
| **580000 / 174000 / 156774 / 7366 vs 20000 / 6000 / 5406 / 254** | 前者 = Kafka DWD topic 的**累计消息数**（含 29 次重放）；后者 = Doris 实时表**按主键去重后**的行数 → **不是"数字对不上"，是两个不同量**（P1-34） |
| **3.9 GB vs 3921 MB** | 同一现场读数的两种精度写法 → 统一为 `3921 MB（≈3.9 GB）` |
| **14 项 vs 13 条**（DECISIONS ⏳） | ⏳ 编号 1~14 **缺 9** → 实为 **13 条**（P1-25） |
| **13 个验收脚本 vs 13 个 Sprint** | 实际是 **12 个编号 Sprint（0~12）+ 13 个验收脚本**（P1-23） |

---

## 8. Documentation Consistency

### 8.1 状态一致性（L2 任务 1：**18 条** = P1 × 10 / P2 × 8）+（L6：P1/P2 若干）

| 判定 | 内容 |
| --- | --- |
| **四方基准** | `AGENTS.md:597-609` 是四方中唯一"全 ✅ + Sprint 13 未开始"的状态表；`dsh-workspace/README.md` 同向；**项目 `README.md` 是一条整体滞后的状态线**（12 处残留集中在同一文件）；`docs/sprint/` 侧**无 Sprint 13 文档** |
| **过期内容的机制（不是"忘了"）** | 项目 README 的 §16 Roadmap 与"数据质量校验与监控"是**末尾追加**的；其后 `L921-994` 是**无归属章节的散文**（P2-26），所以没有"改这一节时顺手更新"的锚点；`SPRINT_8/9/10/12` 头部用"见第 N 节"外推（P2-27），同样规避了回改 |
| **最容易被评委抓住的一处（已闭环）** | `AGENTS.md` §15 曾**内部自相矛盾**：状态表说 Sprint 0~12 全 ✅，§15.9 仍写"Sprint 5 进行中"+"**禁止提前实现 Sprint 8 及以后**" → **已于 2026-09-27 15:04:46 修复**（§4.1 复核确认）；但同一更新**新引入**一处悬空引用（`AGENTS.md:858` → 不存在的 `docs/sprint/SPRINT_13.md`，P1-18） |
| **看起来最像"没做完"的一份** | `SPRINT_4.md`：头部"进行中"（P1-15）+ §10.3"仍待完成"三项 ⏳（实际全已关闭，P1-16） |
| **数字可信度最严重的一处** | `外部审查包.md` 的 42/0/0、381 passed、88/0/0 **无出处**（P1-21），而该包正是供外部核对"有没有编数字"用的 |
| **Sprint 13 三处口径不一** | `AGENTS.md:610`"未开始" / 根 `README.md:70`"🔄 材料整理中" / 项目 `README.md:908` 无标记（P2-23） |
| **过期状态被实测否证的一处** | `SPRINT_0_VERIFICATION_STATUS.md:195`"服务器无 SWAP" vs `Swap: 4095 used 3238`（P2-28） |
| **正面结论（同样重要）** | `生产可用` / `生产环境` / `高并发` / `低延迟` / `真实业务数据` / `全部验证通过` / `完全无损` / `完全证明` / `充分证明` / `已证明` / `无一例外` —— 五份被扫文件**全部 0 命中**；真正的绝对化表述只有 **9 条**（其中 2 条 P1），且两条都是**与本文摘要自相矛盾**，而非"把没做的说成做了" |

### 8.2 `docs/thesis/` 侧

| 文件 | 一致性状态 |
| --- | --- |
| `REPORT_DRAFT.md` | 主体口径克制（§7.3 不足表齐全、`:1359` 是全文最规范的样板段）；**审计发现的两处"真实数据"已在窗口内闭环**（现 `:457` / `:1367`，见 §4.1） | 仍存 `:369` "完全相同 / 想绕过也没有入口"（P2-37）、`:642`/`:731` "完全一致 / 零丢失"（P2-41）、`:16` "全部验收通过"未提 Sprint 5 一项未归零（P2-42 群） |
| `DATA_AND_LIMITATIONS.md` | **本次扫描中边界写得最规范的一份**（`:14` 合成数据结论、`:240` 给了判据数与分母、`:294` "两个都保留、不合并、不取平均"）→ **合格样板，不要改**（仅 E2 的 20000/40000 需按已定案口径收敛） |
| `外部审查包.md` | **尚未闭环**：三个无出处数字（P1-21）+ 缺 Sprint 5 行（P1-22）+ `:177` "完全一致" 与 §5.4 "未定性" **均未按已定案口径更新**（P2-40）+ "不修的 4 项"实为 3 行（P2-35）+ 无性能小节（P2-21） |
| `QA_PREP.md` / `DEFENSE_SCRIPT.md` / `DEFENSE_SLIDES.md` | **本次绝对化扫描未覆盖**（L2 任务 2 的范围被限定为四个文件）；L5 单独发现**第 10 页缺"未做并发测试"边界**（P1-32）→ 建议补一轮扫描 |
| `DEFENSE_QA.md`（**15:09:16 新增**）/ `DEFENSE_DEMO.md`（15:10:40）/ `RESUME_PROJECT.md`（15:09:27） | **并发产出的材料**：`RESUME_PROJECT.md` 已正确引用本事实源（P0-1 与取证）；**`DEFENSE_QA.md` Q4 仍用旧口径并指向 `EVIDENCE_MATRIX.md §2.1（UNRESOLVED）`** → **P1-35**，必须按 §7.1 改写 |
| `毕业论文_*.docx`（144 KB，**13:10:40**） | **未打开且早于本轮修改**：P1-26/P1-27 的修复**必须连同 `.docx` 重新导出**，否则 Word 终稿里仍留着旧措辞 |

### 8.3 文档可信度的总体判断

> 本项目的文档**不是"吹"出来的**：核心数字（11458 / 19643 / 51890375.77 / 353 / 1992.3 / 8.5 ms …）
> 在 AGENTS §15、Sprint 文档、REPORT_DRAFT、DATA_AND_LIMITATIONS 四处**互相自洽**，且多数能被
> 原始采集输出、元数据库或只读 `SELECT` 复核。问题集中在**三类**：
> ① **状态滞后**（README 一条线 + 三份 Sprint 头部）；
> ② **同名不同范围未标注**（11458/11459、60/60 vs 70/70、353/381/191、20000/40000）；
> ③ **把"聚合量相等"说成"数据未变"**（Iceberg、层间一致、零丢失）。
> 这三类都**不改数字**，改的是**措辞与口径**。

---

## 9. Unverifiable Claims

### 数量：**26**

> **这些不是"未发现差异"，而是"没有测量过"或"当前环境无法证明"。**
> `原因` 一栏说清"为什么无法证明"；`要验证它需要什么` 一栏给出**只读**或 PHASE F 可执行的动作。

| 编号 | 未被验证的结论 | 原因 | 要验证它需要什么 |
| --- | --- | --- | --- |
| U-1 | Iceberg 迁移后**字符串字段内容**一致 | 没有任何断言读这些列的值 | 按主键排序后 `MD5(CONCAT_WS('|', …))` 聚合指纹两侧比对；或抽样逐字段 diff |
| U-2 | **时间字段**（时区/精度/类型；Spark `timestamp` ↔ Iceberg with/without zone）一致 | 迁移作业完全不碰时间列；映射未判定 | 两侧 `DESCRIBE` 逐列比对类型串 + 关键时间列 `MIN/MAX/COUNT(DISTINCT)` + 转字符串哈希 |
| U-3 | **NULL 与空串**未变化 | `SUM()` 忽略 NULL，金额判据抓不到 | 逐列比对 `COUNT(*) - COUNT(col)` 与空串计数 |
| U-4 | **decimal 精度与舍入**（6 个金额列之外）未变化 | `MONEY_COLUMNS` 之外的小数列一律不核对 | 两侧 `DESCRIBE` 比对 `decimal(p,s)`；全部小数列逐列合计 + 分位/抽样 |
| U-5 | **分区布局与分区值**一致 | 分区列只被用于建表，无任何断言（解析失败会静默退化为无分区表） | `SHOW PARTITIONS` 两侧比对；Iceberg `<表>.partitions`/`.files` 的 `spec_id`；补"分区表必须有分区列"断言 |
| U-6 | **schema 等价性**（列序/列名/可空性） | 只靠"DDL 与 SELECT 共用 `plan.columns`"的构造性保证；`CREATE TABLE IF NOT EXISTS` 会静默复用旧定义 | 迁移后 `DESCRIBE dst` 列名序列与类型串逐列断言 == `plan.columns`；再比 Iceberg `current-schema` 可空标志 |
| U-7 | **重复行与唯一性** | `COUNT(*)` 相等对"等量替换"完全免疫 | 每表主键 `COUNT(*) == COUNT(DISTINCT pk)`；更强是双向 `EXCEPT ALL` |
| U-8 | **Iceberg 快照与元数据正确性** | 作业内未验证；验收脚本仅 1 张表、仅"快照数 > 0"与时间旅行行数 | `<表>.snapshots` 的 `summary` 与 `.files`/`.manifests` 记录数，与 `COUNT(*)` 三方对齐 |
| U-9 | **回滚可行性** | 无回滚演练、无回滚断言、无"源表未被改动"的断言 | 做一次"删库重建"演练并留日志（PHASE F，非只读） |
| U-10 | **压缩编码与 `format-version`** | `SPRINT_5.md:337` 把配置写成结果；无读回断言 | 读 parquet 元数据确认 codec；`SHOW TBLPROPERTIES` / `<表>.properties` |
| U-11 | **行级完整性**（逐行/字节级一致） | 现有证据全是聚合量；当前方法集**原理上无法**证明 | 双向 `EXCEPT ALL` 均为空，或全表有序哈希比对 |
| U-12 | **并发 / 吞吐 / P99 / 排队行为** | 零并发测试（脚本、文档、全库检索三重证据） | 需压力测试（PHASE F 可选，且需先定 SLA） |
| U-13 | **冷缓存 / 冷启动延迟** | 未测量；`REPEAT=7` + 预热使数字结构上不可能包含冷缓存代价 | 需在清缓存/重启 BE 后按同口径采一次 |
| U-14 | **公网链路（含 nginx 一跳）延迟** | 未采集（D2 已登记）；§15.7 记录了中间设备改写问题 | 从公网侧按同口径采集（注意演示时不要挂 VPN/代理） |
| U-15 | **Doris/Iceberg 缓存对本次数字的具体贡献** | 源码未记录；分离实验会把缓存改得更热、反而不可比 | 需专门实验设计（或明确声明"不分离"） |
| U-16 | 该 run **第 1 次 `ads_layers` 失败尝试的耗时** | `task_instance` 只留最终 attempt 一行（Airflow 覆盖语义） | **无法补测**（历史数据不存在）→ 只能声明"含重试的总代价不可得" |
| U-17 | 1992.3 s 的**原始取数输出是否存在过** | 当时采集日志第 3 类只有 `[SKIP]`，该数字是另取的、无留痕 | 只能证明"与元数据库当前状态逐行吻合"；**不能**证明当时如何打印 |
| U-18 | `外部审查包.md` 的 **42/0/0、381 passed、88/0/0 是否曾经真实存在** | 仓库内无任何出处；无法从文档证真或证伪 | 需找到那几次运行的原始输出；否则按"无出处"处理 |
| U-19 | **线上 `retries` 的真实分布 / 生产重试率** | 未找到任何对线上 Agent 日志的 `retries` 统计或归档；验收触发是脚本**主动构造**的问法 | 只读取证：`journalctl -u data-platform-agent` 统计（PHASE F） |
| U-20 | **是否存在逗号连接之外的其它绕过路径** | "不存在绕过路径"是**不可穷尽验证**的全称命题 | 不可证明；只能持续补对抗用例（P0-1 修复时一并做） |
| U-21 | **40000 的成因 / 2026-09-26 13:18 Kafka 重建之前的历史状态** | 该次重建把旧状态整个换掉（earliest = 0、无 `DeleteRecords` 痕迹、4 个源 topic 的 `partition.metadata` 时间戳集中在 13:18:11–13:18:17；20000 条消息 `CreateTime` 全落在 **586 ms** 内）；Iceberg 仅留 3 个快照；`syslog` 只回溯到 2026-09-27 00:00 | 需卷快照 / 当时的 Kafka dump（**本项目均无**）→ 记为 **UNRESOLVED DATA-STATE DISCREPANCY**；**不影响当前值判定（= 20000）** |
| U-22 | **离线 DWD 明细表 `lakehouse.dwd_traffic_behavior_detail` 的行数** | L7 未跑 Spark：观测时可用内存 3071 MB、swap 已用 3238 MB，Spark 查询会与实时链路抢内存（Sprint 3 有整机失联事故），按纪律未执行；仅确认表存在（`TBLS`: `TBL_ID=41`, `DB=lakehouse`） | 一条**错峰只读**查询即可闭环：`bash scripts/spark-sql.sh -e "SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail"` |
| U-23 | **`event_id` 两段编号**（`40001–49999` 共 9999 个 与 `410000–420000` 共 10001 个，中间 **36 万个序号缺失**，`CreateTime` 在 13:18:36.934 处首尾相接，全过程 586 ms）**的成因** | 生成器 `EventIdGenerator` 每轮从 1 起（`common.py:110-118`），单轮 `--behavior 20000` 只会得到 `evt_40001..evt_60000`（S8 §1.1）→ 该序号的成因**在当前仓库代码里无法复现**；`syslog`/`syslog.1` 中**无任何 `generate_events` 的 stdout**（只有 dockerd 容器启停记录），13:18 那次运行已被滚掉 | 需生成器运行日志 / topic 重建记录；**当前不可得**（分布已核清，**不影响 20000 的判定**） |
| U-24 | 服务器**站点形态**（:80 明文、:443 未监听）与 `agent_ro` 的**当前实际授权范围** | L2 明确限定为文档线、未开服务器会话；本次未做独立验证（文档侧口径一致） | 只读：`grep -E '^SITE_SCHEME=' .env`、`ss -lntp \| grep -E ':(80\|443)'`、`SHOW GRANTS FOR 'agent_ro'` |
| U-25 | `verify-sprint-*.sh` 的**实际执行输出**（40/0/0、84/0/0、32/0/0、61/0/0 等） | 未重跑（纪律禁止为验证数字重跑流水线）；各线只核实了**脚本存在**与**书面记录** | 只读重跑验收脚本（PHASE F 可选；不得为此重跑数据流水线） |
| U-26 | **Prometheus 告警规则 / SLO / 通知链路** | 本次六条审计线**均未覆盖**（只审了采集与看板） | 补一条只读审计线：读 `infrastructure/monitoring/` 下的 rules 与 Grafana 告警配置 |

> **另有两项"未验证"属方法学层面，不单独编号**：① 语料"65 篇"未逐篇装载复算；
> ② 检索单测"26 passed"、对抗用例"60+3 xfail / 63 passed"未重跑。

---

## 10. Recommended Fixes

> **本阶段只列建议，不执行任何修复。** 顺序 = 建议执行顺序；每条给出"改什么 + 怎么验证改对了"。
> 所有修复必须遵守 `AGENTS.md` §11.1（涉及权限边界/架构的先说明再改）与 §8.5（**禁止为通过测试而弱化断言**）。

### 10.1 第一批：P0（先测后改，权限边界）

| # | 建议动作 | 验证方式 |
| --- | --- | --- |
| F-01 | **补对抗用例**：逗号连接 SQL（含两表、三表中间越权、`FROM a, b WHERE …`、跨库名）→ 期望 `TABLE_NOT_ALLOWED`；同时断言 `source.tables` **报出全部表** | 用例先以 `xfail(strict=True)` 固化现状，再改守卫使其转正（`test_sql_guard_adversarial.py` 已有该模式） |
| F-02 | **改守卫**：`_TABLE_RE` 增逗号分支，或改为"提取 `FROM` 到下一个子句之间的所有点分标识符"；**血缘与校验必须同源**（同一份提取结果） | 本地探针 + 线上只读复现（同 §P0-1 三组对照），要求：显式拒绝 + 血缘完整 + 白名单内两表 JOIN 仍放行 |
| F-03 | 把"逗号连接 SQL"补进 `verify-sprint-10.sh` 的**两路径 tables 一致**断言 | 重跑 `verify-sprint-10.sh`（只读），确认 MCP 与 HTTP 两侧都拒且血缘一致 |
| F-04 | 按 `AGENTS.md` §11.1 记录本次变更（权限边界），更新 §15.6 白名单说明 | 文档审查 |

### 10.2 第二批：P1（口径与证据边界，多数是"改措辞不改数字"）

| # | 建议动作 | 关联 |
| --- | --- | --- |
| F-05 | 交易域**补两条单边窗口断言**（仅实时 = 0 / 仅离线 = 0），与流量域对齐 | P1-1 |
| F-06 | 论文/文档统一写"**7 个可加指标 + 1 个去重指标（两侧同为近似去重）**"，并声明去重指标只支持逐窗口比较 | P1-3 |
| F-07 | 交易域验收脚本补非空守卫、覆盖率与单边断言；批次选取改为**本次 `batch_id`** | P1-4 |
| F-08 | 修 `08_traffic_reconcile.sql:36-39` 的文件头注释（实时侧 anomaly **不阻断**）；**保留** `SPRINT_5.md:309-312` 的"一项未归零" | P1-5 |
| F-09 | `REPORT_DRAFT.md:1151` / `DEFENSE_SLIDES.md:201` 改用**有界表述**并标注"60/60 = 18 张表那次；23 张表复跑为 70/70" | P1-6、P2-9 |
| F-10 | `70/70` **加证据等级标注**（"仅汇总记录，无逐项细目"）；可选：检索 Airflow/批处理历史日志里的那次 `[check] ===== 70/70`（**只读取证，不得重跑**） | P1-7 |
| F-11 | `SPRINT_3.md:52` 的 40000 改写或加注；论文按 §7.1 的最终口径陈述（含"行数 = 去重后行数 ≠ 处理量"） | P1-8、P1-34 |
| F-12 | 11458/11459 与 19643/19644 首次出现处加**区间口径注** | P1-9 |
| F-13 | `PERFORMANCE.md` §5/§7、`SPRINT_12.md` §8.7 各行**追加**后续状态（"2026-09-27 提交 `b195d19` 已修并服务器验证"）——**不改历史观察值** | P1-10 |
| F-14 | 层间一致/对账结论处补"（Iceberg 5 张交易表）""（对账区间）"等范围 | P1-11 |
| F-15 | 项目 README：三处 🔄 → ✅ 并引用实测数字；"仅剩一处实时侧缺陷"移入**已知缺陷**注记 | P1-12 |
| F-16 | ~~`AGENTS.md` §15.9 改为长期约束~~ → **✅ 已在审计窗口内完成**（15:04:46，见 §4.1）。**仍需做的一步**：让 `AGENTS.md:858` 与 §15 的 Sprint 13 链接与 `docs/sprint/SPRINT_13.md` **同向**（补文件，或改成"以 `docs/thesis/` 为准"） | P1-18 |
| F-17 | 项目 README §7.3 端口表 + L978-994 TLS 段 + `:56` + `:993` 四处订正到"当前明文、443 未监听、502 结论 = VPN 出口改写" | P1-14 |
| F-18 | `SPRINT_3/4/5.md` 头部改 ✅（带验收数字）；`SPRINT_4.md` §10.3 改为"原待办（均已关闭）" | P1-15、P1-16 |
| F-19 | 根 `README.md:70` 改 ✅（材料成稿；终稿待答辩前定稿）；`:53` 标题同步 | P1-17 |
| F-20 | 补 `SPRINT_13.md`（或明确声明"以 `docs/thesis/` 为准"） | P1-18 |
| F-21 | 项目 README 文首"当前进度"扩到 Sprint 0~12；"下一步"段改为 Sprint 13；`191/138` 标注为历史口径 | P1-19、P1-20 |
| F-22 | **外部审查包**：三个无出处数字**给出出处或改为 32/0/0 + 353**；补 Sprint 5 行；`13 个 Sprint` 口径统一；"14 项 ⏳"改为"13 条（缺 ⏳9）" | P1-21、P1-22、P1-23、P1-25 |
| F-23 | `DECISIONS.md` ⏳12/⏳13 状态由主控裁定后**只改一处并互相引用** | P1-24 |
| F-24 | ~~`REPORT_DRAFT.md:443` → 合成数据集；`:1305` 标题把"真实"移到"缺陷"~~ → **✅ 已在审计窗口内完成**（15:10:26 版本，见 §4.1）。**仍需做的一步**：**重新导出 `.docx`**（当前 144 KB 版早于本次修改，仍含旧措辞） | P1-26、P1-27 |
| F-25 | `PERFORMANCE.md`/`REPORT_DRAFT`/`DEFENSE_*` 的 `/ask` 口径改为 **n=2、无预热**（或补一次真预热并标注）；批量改为"**各任务最终 attempt 的 `duration` 之和**"（含 `ads_layers` try 2） | P1-28、P1-29 |
| F-26 | 关联指标**重采一次**（脚本未改、缺陷已修）或在表下加注"采集时维表为空；修复后约 7.4 ms" | P1-30 |
| F-27 | 订正 `PERFORMANCE.md:320-325` 与 `measure-latency.sh:15` 的"取自日志"措辞（纯文档订正） | P1-31 |
| F-28 | 论文 §6.5 开头/结论 + 答辩第 10 页加"**未做并发压测，不构成生产容量结论**"（范本句见 §11） | P1-32 |
| F-29 | Agent 安全主张措辞收窄为"守卫对**显式声明的**数据源强制白名单" | P1-33 |

### 10.3 第三批：P2（工程质量与文档卫生）

| # | 建议动作 | 关联 |
| --- | --- | --- |
| F-30 | 修 `migrate_parquet_to_iceberg.py:250-253` 的**恒真守卫**（`check_true(name, src_n > 0, "行数 > 0")`）；补分区与 schema 两条迁移后断言；处理 `IF NOT EXISTS` 的静默复用 | P2-5、P2-6 |
| F-31 | 把"配置为"与"实测为"分开写（snappy / `format-version` v2）；docstring 的"从根上不会出现"加"由构造保证"限定 | P2-7 |
| F-32 | `SPRINT_5.md` §9.6 措辞改为"逐个表名校验（23 张全在）+ 反向校验（无清单之外的表）" | P2-8 |
| F-33 | `verify-sprint-5.sh:870` 改 `CONCAT_WS` 或转义引号；`verify-sprint-3.sh:281-282` 补非空守卫；`verify-sprint-5.sh` 补覆盖率断言；修流量域注释计数 | P2-1、P2-2、P2-3、P2-4 |
| F-34 | `sqlguard.py` LIMIT 正则改为**逐支收敛**；"已有 LIMIT 但形态不匹配"时**拒绝**而非追加；补对抗用例（含 10 个缺用例的关键字、孤立 `*/`、子查询越权、非句尾 LIMIT） | P2-13、P2-14 |
| F-35 | （可选）`config.py` 的 `.env` 读取结果按 `_AGENT_KEYS` 过滤后再入 `merged` | P2-10 |
| F-36 | 同义词条数统一为 **55**（或注明差异）；`PROJECT_DESIGN_V1.md:171` 改为"RAG（词法检索：BM25 + 同义词表）" | P2-11、P2-12 |
| F-37 | 性能文档：三处"每项 n=7"限定为"只读接口类"；合计注明求和口径；补存元数据库查询输出；§6.5 加"热缓存口径"；D 组补"未测冷缓存" | P2-16~P2-19 |
| F-38 | 外部审查包补"性能边界"一节；`verify-sprint-12.sh` 可选加"文档 n 与采集日志一致"的断言 | P2-20、P2-21 |
| F-39 | Sprint 13 状态三处统一；Sprint 8/9/10/12 头部各补一行结论；`SPRINT_0_VERIFICATION_STATUS.md:195` 的 swap 行改为"已处理" | P2-23、P2-27、P2-28 |
| F-40 | 项目 README：§14 标注历史时点 + 补当前 353；TOC/文档索引补齐；把 L921-994 收进命名章节；技术栈拆 Python 两行、删 HDFS、改"历史计划"；补 3 个验收脚本命令 | P2-24~P2-26、P2-29、P2-30 |
| F-41 | `DECISIONS.md` 第三节两条计划列订正；`外部审查包.md` "不修的 4 项/12 份任务书"口径订正；`DEVELOPMENT_LOG.md` 追加恢复与 Sprint 10/11/12/13 记录并勾掉已完成待办；删除仓库根 0 字节悬空文件 | P2-32、P2-34、P2-35、P2-36 |
| F-42 | 绝对化表述逐条按 §11 改写（**9 条 A 类 + 跨文档群**）；`:1150` 作为"零丢失"类主张的**标准句式**推广 | P2-37~P2-42 |
| F-43 | **只读补齐三项**：① `SELECT COUNT(*) FROM lakehouse.ods_behavior_event`（U-22）；② `journalctl -u data-platform-agent` 统计 `retries`（U-19）；③ 站点形态与 `agent_ro` 授权（U-24） | U-19、U-22、U-24 |
| F-44 | 补一条"Prometheus 告警规则/SLO"的只读审计线 | U-26 |
| F-45 | **把 `DEFENSE_QA.md` Q4（及 `DEFENSE_DEMO.md` / `RESUME_PROJECT.md` / `QA_PREP.md` / `REPORT_OUTLINE.md` / `外部审查包.md`）按 §7.1 的口径统一复查**：20000 = 基准值；40000 保留并标注为当时观测误差；补"行数 = distinct `event_id` 基数 ≠ 链路处理量"；删除"本次审计未能定案/未定性"的旧口径 | P1-35、P2-40 |
| F-46 | 修复上述文档后**重新导出 `毕业论文_*.docx`**（当前版本早于本轮修改） | P1-26/P1-27 的收尾 |

### 10.4 明确**不建议**做的（防止改坏）

```text
✗ 不要为了"统一"而删除 60/60 或 70/70 中的任一个（两次范围不同）
✗ 不要为了"干净"而删除 20000 或 40000 中的任一个（历史值 + 未定性，两者都保留）
✗ 不要为了"状态好看"而删除 SPRINT_5.md 的"一项未归零"（click_rate anomaly）
✗ 不要修改 PERFORMANCE.md / SPRINT_12.md 中的历史观察值（维表 0 行），只追加后续状态
✗ 不要为验证任何一个数字而重跑整条流水线（audit-brief §1.7）
✗ 不要改 DECISIONS.md:130/190/210 的"必然/保证"（正确用法）
✗ 不要改 DATA_AND_LIMITATIONS.md:14/240/294、PERFORMANCE.md:14-21、REPORT_DRAFT.md:1359、DEFENSE_SCRIPT.md:290（合格样板）
```

---

## 11. Thesis Wording Risks

> **论文、PPT、讲稿、问答准备中不得继续使用的绝对化表述**，逐条给出"改成什么"。
> 优先采用各审计线已给出的**范本句**（右侧加 ★ 者为推荐替换）。
> 判定标准：**句子的断言语力 > 实测覆盖范围**。

### 11.1 高危（会直接越界，必须改）

| # | ❌ 不能继续写 | 为什么（证据边界） | ✅ 改成什么 |
| --- | --- | --- | --- |
| W-01 | "批流两侧数据**完全一致**" / "**全部一致**" / "**零差异**" | 交易域无单边窗口断言（只有 ≥90% 覆盖率容差）；`is_match` 是补零口径；比率列不进判据；证据只覆盖对账区间 | ★"在两侧数据范围求交并尾部留出安全边界后的**对账区间内**，逐窗口比对交易域 **7 个可加指标 + 1 个去重指标**（去重指标两侧采用同一近似去重语义），在**缺失侧补 0** 的统一口径下，不一致窗口数为 0" |
| W-02 | "**全量数据一致**（11458 / 19643 个窗口全部独立验证一致）" | 11458 个窗口里 **5504 个两侧 GMV 均为 0（平凡一致，48.03%）**，只有 5954 个有业务量；且区间外未比 | ★"11458 个窗口中有业务量的 **5954 个**逐窗口一致；另有 **5504 个窗口两侧均为 0**（平凡一致，不构成独立证据）。该结论不覆盖对账区间之外的数据" |
| W-03 | "两侧**比率也一致**" / "比率列也全部一致" | 比率列（`click_rate`/`cart_rate`/`buy_rate`）**不在 `is_match`** 内，只作 `diff_*` 留证 | ★"判据覆盖 7 个计数列；派生比率列为**留证列**，其中实时侧 1 个窗口存在计数与比率自相矛盾，已如实记录" |
| W-04 | "证明了两侧**产出的窗口集合完全相同**" | 交易域**没有**单边窗口 = 0 的断言 | ★"交易域实时侧窗口数 ≥ 离线侧 × 90%（覆盖率断言）；流量域另有两侧单边窗口 = 0 的硬断言" |
| W-05 | 隐藏 / 省略 `click_rate` anomaly，或写成"没有任何差异" | `realtime_rate_anomaly_windows = 1` **实测存在**；审计纪律明令不得隐藏 | ★"流量域同时发现 **1 个实时侧窗口**的计数列与比率列自相矛盾（`view_cnt=2`、`click_cnt=1`，`click_rate` 记 0.0000 而按口径应为 0.5000）；该缺陷**不影响可加指标一致性结论**，修复需重部署 Flink 作业并重放，列为待决策事项" |
| W-06 | "**生产环境验证**" / "在生产环境已验证" | 结论建立在**程序生成的合成数据集**上（`GEN_RANDOM_SEED=20260926`），且现场是单机 4 核/16 GB | ★"在**单机实验环境的合成数据集**上验证；单机单副本，不构成对生产环境的证明" |
| W-07 | "Iceberg 迁移**完全无损**" / "数据**逐行一致**" / "**字节级一致**" / "**所有字段一致**" | 断言只覆盖：表身份、`COUNT(*)`、**6 个金额列的整表合计**、表名集合、1 张表的快照可见性；V1–V11 全部未验证 | ★"在行数、**6 个金额列的整表合计**、以及每张表的 Iceberg `Provider` 这三个维度上未发现差异（18 张表那次校验计数 `60/60`，23 张表复跑 `70/70`，两次范围不同）；**合计相等不等于逐行相等**，行内容未验证" |
| W-08 | "**schema 等价性已验证**" / "**分区布局一致**" / "**可回滚已验证**" / "snappy / Iceberg v2 已核实" | 这四项都**没有迁移后断言**；分区解析失败会静默退化成无分区表而四项判据仍全绿 | ★"schema 同源**由构造保证**（DDL 与 SELECT 共用同一列清单）；分区与回滚**未做迁移后断言**；`format-version` 与 `snappy` 为**配置值**，未读回校验" |
| W-09 | "**高并发**" / "**生产级**" / "满足生产性能要求" / "**低延迟**" | **零并发/压力测试**（三重独立证据）；无 SLA 定义；单机单副本；只读接口无鉴权；Airflow 用官方标注 not for production 的认证方案 | ★"在**单机实验环境、单请求、预热后、本机回环、热缓存**条件下，只读接口点查/聚合/关联中位数为 8.5 / 8.3 / 11.7 ms；**未做任何并发或压力测试，因此不构成对生产环境高并发能力的证明**" |
| W-10 | "**向量 RAG**" / "基于向量数据库的检索" / "Embedding RAG" / "**语义检索**" / "语义相似度召回" / "混合检索（BM25 + 向量）" / "reranker 重排" / "用 jieba 分词" | 代码里**零向量成分**（无 embedding、无向量库、`vector_store: None`、依赖 0 个相关包）；打分是 `idf × tf 饱和 × 长度归一化`，**不是余弦相似度**；自实现 CJK bigram | ★"检索增强采用**词法检索后端**：BM25 排序（`k1=1.2`、`b=0.75`，含 idf 与文档长度归一化）+ 自实现 **CJK bigram** 分词 + **显式同义词表**（55 条）做查询扩展；**本项目未使用向量检索或 embedding**" |
| W-11 | "Agent **无法读取白名单外的任何表**" / "**想绕过也没有入口**" / "与人类用户能做的**完全相同**" | **P0-1 已线上复现**：逗号连接可读出白名单外的已授权表；"不存在绕过路径"是不可穷尽验证的全称命题 | ★"Agent 进程**不持有可用的数据库凭据**，其全部取数必须经过 SQL 守卫与只读账号；守卫对**显式声明的**数据源（`FROM`/`JOIN`）强制表级白名单。已实测的四类攻击均被拒；**已知一个绕过路径（逗号连接）已复现并列为修复项**" |
| W-12 | "**零丢失**"（无限定） | ① 判据是**单次**等式"归档行数 == Kafka latest offset 合计"；② 更关键：该等式**就是归档作业自己的硬断言**（`archive_behavior_events.py:186-192`：`checker.check("归档行数 == Kafka 消息数", …)`），所以"归档 20000 行"与"Kafka 20000 条"是**同一个守护条件**，**不是两条独立证据**；③ Kafka 副本数 = 1 | ★"归档行数 20000 == Kafka 消息数 20000（**作业内自检断言**，读 `earliest→latest` 全量 topic 后硬校验；三次归档运行均通过、退出码 0；703 个 `dt` 分区）。判据是'两端行数相等'，**不是**逐条 end-to-end 追踪；**本次验收窗口内未观察到丢失**；副本数 1，不构成多副本容灾口径" |
| W-13 | "（真实数据，来源 …）" / "真实业务数据" / "§6.6 一处真实数据缺陷" | 全部数据为**合成数据**（摘要与 §7.3 已声明）；仓库根 README 自己就写着"不要写成真实业务数据" | ★"（**程序生成的合成数据集，种子 `GEN_RANDOM_SEED=20260926`**；来源 …）"；★"§6.6 一处真实缺陷：**派生表为空**使 JOIN 静默返回 0 行" |
| W-14 | 用 "`/ask` 19.2 s" 说明**数据平台查询耗时**或平台瓶颈 | 数据侧仅 18.3 ms（0.1%），99.9% 在外部 LLM 往返 | ★"`/ask` 端到端 19187.5 ms（**n=2、无预热**）中，可单独测量的数据侧仅 18.3 ms（检索 2.9 + 取数 15.4），约 **99.9% 的耗时来自外部 LLM 服务往返（含 1 次反思重试）**，故该数字**不应被理解为数据平台的查询性能**" |
| W-15 | "实时链路**处理了 20000 条**行为事件" / "实时 DWD 与源端 **1:1**" | Flink 侧累计产出 **580000 条**（= 29 × 20000）；Doris 行数 20000 是 **`UNIQUE KEY` 去重后**的结果 | ★"Doris 实时表按 **`UNIQUE KEY` 去重后**为 20000 行（与源 topic 当前 20000 条消息一致）；Flink 侧累计产出 580000 条（源于作业被重放 29 次）。**行数 = 去重后键数，不等于链路处理量**" |

### 11.2 中低危（措辞收紧即可，信息量不损失）

| # | ❌ 现有写法 | ✅ 改成什么 |
| --- | --- | --- |
| W-16 | "逐表行数与 Parquet 侧**完全一致**"（`REPORT_DRAFT.md:642`、`外部审查包.md:177`、`SPRINT_5.md:344`、`SPRINT_3.md:51/61/133`、`SPRINT_0_VERIFICATION_STATUS.md:140`） | "在迁移清单内的 **18 / 23 张表**范围内逐表行数**未发现差异**，并对含金额表做 `SUM` 源==目标核对"（★ 以 `REPORT_DRAFT.md:1150` 的判据写法为范本） |
| W-17 | "**精确对账**" / "**精确一致**"（`README.md:23/48/50`） | "**可加与计数指标**与 MySQL 事实源逐项对账（GMV 51,890,375.77，精确到分）；**去重指标（UV）按逐窗口口径核对，不做上卷**"；行数相等处补"内容一致性由金额 SUM 与类型断言补充覆盖" |
| W-18 | "四类攻击**全部被拒**" / "12 个开发阶段**全部验收通过**" / "下表是本项目的**全部**验收记录" / "**每一步**都能复现" / "**每个数字都是服务器上跑出来的**" | ★"**已覆盖的**四类攻击全部被拒"；"12 个开发阶段全部通过验收**（其中 Sprint 5 有一项按方案 C 留证不归零）**"；"全部**有验收脚本的**阶段记录（Sprint 0~12）"；"**已交付的每一处对账结论**都能复现，未采集项见 D 组" |
| W-19 | "三重上界**保证一定停得下来**" / "nginx 的 502 **必然**带 `Server:`" | "三重上界（重试 2 / 轮次 6 / 超时 120 s）在**图结构上**保证可终止；LLM 调用本身另有超时"；"**在默认配置下**必定带 `Server:`" |
| W-20 | "所以当前走明文是**安全的**" | "在当前网络路径下（**不挂代理**）可用；传输仍未加密，**不是'安全'的通用结论**" |
| W-21 | "down / up 后数据 **100% 保留**" | "down/up 后 8 张业务表**逐表行数不变**" |
| W-22 | "用 1 分钟滚动窗口产出**秒级**指标" | "以 1 分钟滚动窗口产出**近实时**指标（本机回环下只读接口点查中位数 8.5 ms；**含公网的端到端时效未测**）" |
| W-23 | "**它为什么能支撑**逐窗口对账" | "它**具备**被逐窗口复核的**前提**（可复现 + 事件与业务行对应）；对账结论本身仍是**本次运行下的实测值**" |
| W-24 | "返回 10 行**真实**类目 GMV" | "返回 10 行类目 GMV（**合成数据中实际存在的组合**）" |

### 11.3 论文可直接引用的"安全句式"（建议全文统一）

```text
① 范围句   ："在 <区间/范围> 内、按 <粒度>、对 <判据列>、在 <补零/去重> 口径下，未发现差异。"
② 口径句   ："该行数为按主键去重后的行数，不等于链路处理量。"
③ 边界句   ："本结论不覆盖 <区间外/未验证维度>；未做 <并发/公网/冷缓存> 测试。"
④ 缺陷句   ："同时发现 <1 个窗口的 click_rate 自相矛盾>，已如实落表，不影响 <可加指标一致性> 结论。"
⑤ 双数保留 ："两个来源的值分别为 A 与 B，**两个都保留、不合并、不取平均**"（**适用前提：尚未定案**）。
              —— 对 **20000/40000** 已**不适用**：基准值 = 20000，40000 保留为**当时的观测误差**（见 §7.1）。
⑥ 证据等级 ："`70/70` 为汇总记录，**无逐项细目**；`60/60` 的构成可由代码与 DDL 复算。"
⑦ 自检句   ："归档行数 == Kafka 消息数**是归档作业自己的自检断言**（同一个守护条件），**不是两条独立证据**。"
⑧ 口径句（去重）："该行数是 **distinct `event_id` 基数**（`UNIQUE KEY` + merge-on-write），**不等于链路处理量**。"
```

---

## 12. Final Audit Verdict

# `READY FOR PHASE F`

**判定依据（只按任务给出的定义：READY 意味着"所有审计线已完成，可进入修复阶段"）**

| 检查项 | 状态 | 说明 |
| --- | --- | --- |
| L1 批流一致性 | ✅ DONE | 552 行，7 问全答，含代码级断言核对 |
| L2 文档状态一致性 + 绝对化表述 | ✅ DONE | 872 行，18 条 + 9 条，含正面结论（11 个词 0 命中） |
| L3 Iceberg 验证范围 | ✅ DONE | 381 行，60/60 与 70/70 独立复算，V1–V11 清单 |
| L4 Agent 安全边界 + 反思重试 + 检索 | ✅ DONE | 425 行，含纯函数探针与 P0 线索 |
| L5 性能数据边界 | ✅ DONE | 416 行，含原始采集日志与元数据库只读复核 |
| L6 跨文档一致性 | ✅ DONE | 210 行，含"最严重的数字可信度问题"（外部审查包） |
| L7 数据差异专项（20000 vs 40000） | ✅ **DONE** | **741 行**（§0–§8 + 只读命令清单），2026-09-27 15:08:54 落盘；**判定 = B：40000 判为错误，基准值 20000**（五条独立只读证据） |
| 主控实测（对账 + P0 复现） | ✅ DONE | 14:55:12 / 14:56:19 两组证据已写入两份文件 |

**结论**：
> **七条审计线全部完成，主控两条实测到位，两份文件（`EVIDENCE_MATRIX.md` + 本报告）已互相一致。
> 因此按定义判定为 `READY FOR PHASE F` —— 可以进入修复阶段。**

> **⚠️ 判定在本报告定稿过程中的变化（如实记录）**：本报告初稿写成时 L7 的 §3/§4 尚未落盘，
> 当时按"审计线未全部完成"判为 `NOT READY`；**L7 于 2026-09-27 15:08:54 写入 §2.11–§2.17、
> §3 三方对照表与 §4 判定**，本报告随即只读复核（741 行）并**改判为 `READY`**。
> 这不是标准变化，而是**前提发生变化**。

### 12.1 进入 PHASE F 时的四条必读约束

```text
① 先测后改（P0）：F-01（补对抗用例，含 xfail(strict=True) → 正式断言的迁移路径）
   必须先于 F-02（改守卫）；改完必须同时验 ALLOW/REJECT 与 source.tables（血缘）。
② 不许删 unresolved：click_rate anomaly（1 个实时侧窗口）、60/60 与 70/70、
   20000 与 40000（40000 保留为历史观测值 + 标注为误差）—— 详见 §10.4。
③ 不许为验证数字重跑整条流水线；只读查询优先（audit-brief §1.7）。
④ ⚠️ 并发改动风险：审计窗口内 AGENTS.md 已被更新一次（15:04:46）。
   开工前建议先确认没有其它代理在改同一批文档，或先把待改文件的行号重新核对一遍
   （本报告引用的行号以审计时点为准）。
```

### 12.2 不阻塞开工、但应尽快并行完成的三项**只读**补齐

| # | 动作 | 关联 |
| --- | --- | --- |
| 1 | 错峰只读查离线 DWD 行数：`bash scripts/spark-sql.sh -e "SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail"` | U-22 |
| 2 | 主控裁定 `DECISIONS.md` ⏳12/⏳13 的口径（"待跑"vs"已跑"），只改一处并互相引用 | P1-24 |
| 3 | 只读统计线上 Agent `retries`：`journalctl -u data-platform-agent` | U-19 |

**同时必须说清的两件事（避免误读本判定）**

1. **`READY` ≠ 没有问题**：P0 有 **1** 条（逗号连接绕过，权限边界，**不涉及数据正确性**）、
   P1 **32** 条、P2 **42** 条、UNVERIFIABLE **26** 条。
   文档侧真正的问题只有三类：**状态滞后**、**同名不同范围未标注**、
   **把"聚合量相等"说成"数据未变"** —— 都**不改数字**，改的是措辞与口径。
   `READY` 的含义仅是"审计已完成、可以开工修"，不是"结论都已成立"。
2. **本报告不含任何修复动作**：所有建议在 §10，执行属 PHASE F。
3. **并发改动风险（务必先读）**：冻结时点（15:12）前后，仓库文档正在被**另一个工作流同时修改**
   （`AGENTS.md`、`REPORT_DRAFT.md`、`外部审查包.md`、`QA_PREP.md`、`REPORT_OUTLINE.md`
   以及新增的 `DEFENSE_QA.md` / `DEFENSE_DEMO.md` / `RESUME_PROJECT.md`）。
   本轮已因此**闭环了 P1-13 / P1-26 / P1-27 / P2-9**，同时**新产生 1 条 P1-35**
   （`DEFENSE_QA.md` Q4 仍用旧口径并引用本事实源的旧状态）。
   → 开工前建议：**先冻结一次文件集**（或明确"以哪一份为基线"），再按 §10 执行；
   否则会出现"按旧行号改一个已被改过的文件"。

---

> **文件关系**：`EVIDENCE_MATRIX.md` 是**事实源**（论文/PPT/讲稿引用它）；
> `FINAL_AUDIT_REPORT.md`（本文件）是**审计结论与建议源**（修复阶段引用它）。
> 两者的 **P0 = 1 / P1 = 32 / P2 = 42 / UNVERIFIABLE = 26** 必须保持一致；任何一处变更必须同时更新两份文件。
> **冻结时点：2026-09-27 15:12 CST**（此后仍有并发修改，见 §12.2 第 3 条）。
>
> **数据差异专项的关键结论（已写入两份文件，供论文直接引用）**：
> `dwd_traffic_behavior_detail` 的基准值是 **20000**（Kafka 位点、Doris 三次 `SELECT`、
> 归档作业自打印日志、Hive Metastore `numRows`、Iceberg 三次快照 `total-records` 五源一致）；
> `SPRINT_3.md:52` 的 **40000 判为当时的观测误差**（保留并标注，不抹掉）；
> 该表行数是 **distinct `event_id` 基数**（`UNIQUE KEY` + merge-on-write），
> 而 Flink 侧累计产出 **580000** 条（= 29 × 20000，源于作业被重放 29 次）
> → **"行数 = 去重后键数 ≠ 链路处理量"**；
> **不得**写"实时链路累计处理 42 万条行为事件"（`evt_420000` 是生成器进程内序号，与 topic 条数无关）。
