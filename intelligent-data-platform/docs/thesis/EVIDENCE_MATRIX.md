# Evidence Matrix

> **本文件是论文与答辩的事实源。**
> 论文正文、PPT、讲稿、问答准备中出现的任何数字、状态与结论，必须以本文件的
> `Status` 与 `Limitation` 为准；与本文件冲突的表述一律以本文件为准，不得反过来用论文措辞覆盖本文件。
>
> **观测时间**
>
> | 事件 | 时间（CST） | 说明 |
> | --- | --- | --- |
> | 六条审计线素材落盘 | 2026-09-27 14:40–14:56 | `.tmp/audit-*.md`（见 §0 来源清单） |
> | 对账事实只读复核（`SELECT`） | **2026-09-27 14:55:12** | 主控实测：交易域 / 流量域对账汇总与明细表 |
> | 逗号连接绕过线上复现 | **2026-09-27 14:56:19** | 主控实测：只读 `POST /query` 三组对照 |
> | 数据规模只读复核（`COUNT(*)`） | 2026-09-27 14:5x | 性能线只读 SSH（`audit-perf.md` §（三·补）D） |
> | 性能原始采集日志 | 2026-09-27 09:11 / 09:13 | 服务器 `/tmp/perf-a.log`、`/tmp/perf-b.log`、`/tmp/perf-bc.log` |
>
> **证据优先级（判断冲突时按此裁定）**
>
> ```text
> 代码  >  实际验证脚本  >  验证结果  >  Sprint 文档  >  README/论文
> ```
>
> **纪律声明**：本文件为 Sprint 13A 收口审计（PHASE D）**新增**文件；汇总过程**未修改任何既有文件**，
> **未执行 `git`**，服务器侧只做只读操作（`GET` / `SELECT` / `COUNT(*)` / `cat` / `ls`）。
> 文中每个数字都标注来源；**没有来源的数字一律不写**，无法判定的写 `UNRESOLVED`，证据不存在的写 `UNVERIFIABLE`。
>
> **Status 取值**（`audit-brief.md` §6）：`SUPPORTED` / `PARTIALLY_SUPPORTED` / `NOT_SUPPORTED` / `UNVERIFIABLE`。

---

## 0. 来源清单（本文件的全部证据出处）

| 编号 | 来源 | 覆盖范围 | 状态 |
| --- | --- | --- | --- |
| S0 | `.tmp/audit-brief.md` | 审计纪律（分级、结论分类、证据优先级、只读范围） | DONE |
| S1 | `.tmp/audit-reconcile.md`（552 行） | 批流一致性：7 问 + 代码级断言核对 | DONE |
| S2 | `.tmp/audit-doc-status.md`（872 行） | 文档状态一致性 18 条（D-01~D-18）+ 绝对化表述 9 条（A-01~A-09） | DONE |
| S3 | `.tmp/audit-iceberg.md`（381 行） | Iceberg 验证范围（18→23 张、60/60、70/70 独立复算、V1–V11） | DONE |
| S4 | `.tmp/audit-agent.md`（425 行） | Agent 安全边界 + 反思重试 + 检索表述（含探针） | DONE |
| S5 | `.tmp/audit-perf.md`（416 行） | 性能数据边界（含原始采集日志与元数据库只读复核） | DONE |
| S6 | `.tmp/audit-doc-consistency.md`（210 行） | 跨文档状态与数字一致性（PHASE C） | DONE |
| S7 | **主控实测** | 对账事实（14:55:12）+ 逗号连接线上复现（14:56:19） | DONE |
| S8 | `.tmp/audit-traffic-20000-40000.md`（741 行，§0–§8 完整） | 数据差异专项（20000 vs 40000）：仓库侧 R1–R5 + 服务器侧 S1–S17 + 三方对照表 + **判定 §4（选定 B）** | **DONE**（2026-09-27 15:08:54 落盘） |

> **S8 状态说明（2026-09-27 15:09 复核）**：该审计线**已完成**（**741 行**，§0–§8 + 只读命令清单）。
> 仓库侧 §1.1–§1.5 + 服务器侧 §2.1–§2.17 + 三方对照表 §3 + **判定 §4（选定 B）** + 结论分类 §5
> + 无法验证点 §6 + 改法建议 §7 全部落盘。
> → **20000 vs 40000 已定案（§2.1，选定 B：40000 判为错误，基准值 20000）**；
> 仅"40000 的成因（2026-09-26 13:18 重建前的历史状态）"与"离线 DWD 行数"等
> 少量项仍为 `UNVERIFIABLE`（见 §9.4 的 U-21/U-22/U-23）。
>
> **⚠️ 审计窗口内的并发变更（只读复核，必须记录）**：`AGENTS.md` 在本汇总过程中被更新
> （mtime **2026-09-27 15:04:46**，行数 1079 → **1093**）。变化点：
> §15.9 已改写为"Sprint 0~12 已全部稳定并验收通过"、原"禁止提前实现 Sprint 8 及以后"降为
> **〔历史条款，已失效〕**、流量域分层改为"**已由 Sprint 5 建成并验收**"、
> 并显式保留"**仍未归零的一项**：实时侧 1 个窗口的 `click_rate` 自相矛盾"；
> Sprint 13 行由"未开始"改为"🔄 进行中（Prove + Audit + Close）"。
> → **本文件原 P1-13（AGENTS §15.9 与状态表自相矛盾）已在审计窗口内闭环**，移入 §9.6；
> 但同时**新增一处悬空引用**：`AGENTS.md:858` 指向 `docs/sprint/SPRINT_13.md`，而该文件**不存在**（P1-18）。
> 由于 §15.9 之后的内容整体下移约 14 行，本文件引用的 **AGENTS.md 行号以审计时点（≤15:01）为准**；
> 已复核的关键锚点（15:05 版本）：`[check] 60/60 通过` = **:885**、`18 张表迁到…Iceberg` = **:886**、
> `Doris 侧 24 通过 / 0 失败 / 1 跳过` = **:919**、`不一致表数 0（Iceberg 5 张交易表）` = **:925**、
> §15.12 首句"本节只汇总有书面或日志证据的数字" = **:1019**、`阶段 4 校验 60/60 通过` = **:1025**、
> `verify-sprint-10.sh 84/0/0` = **:1048**、`verify-sprint-12.sh 32/0/0` = **:1060**、
> `全量 pytest 353 passed` = **:1061**。

---

## 1. Core Claims

> `Scope` = 证据实际覆盖的范围；`Limitation` = 该结论**不能**外推到哪里。
> **禁止把 `PARTIALLY_SUPPORTED` 读成 `SUPPORTED`。**

| # | Claim | Evidence | Scope | Status | Limitation |
| --- | --- | --- | --- | --- | --- |
| C-01 | Agent 进程不持有**可用的**数据库凭据，取数只能经只读数据服务 + SQL 守卫 + 只读账号 | `services/agent/requirements.txt` 6 项依赖无任何 DB 驱动；`services/agent/app/tools.py:48-50,167-185` 唯一出口是 `DATA_API_BASE`；`config.py:47-77` `_AGENT_KEYS` 不含 DB 键；`deploy/systemd/data-platform-agent.service:46-47,65-71`（非 root + 加固，无 `EnvironmentFile`）；`install-web.sh:198-200` `agent_ro` 仅 `SELECT_PRIV`（S4 §A1/A2） | 代码 + 部署文件（未连服务器复核 systemd 运行时） | **SUPPORTED** | Agent 进程**内存里确实短暂持有** `.env` 全量键值（含 `MYSQL_ROOT_PASSWORD` 等），因为 `config.py:96-102` 读文件时不做键白名单过滤（S4 §A1-补注）。故**不可写**"Agent 进程从不接触 .env 中的库口令" |
| C-02 | **Agent 只能读取白名单内的 18 张表** | 白名单 `sqlguard.py:37-73`（`BUSINESS_TABLES` 16 + `METADATA_TABLES` 2） | — | **NOT_SUPPORTED** | **已被线上证据否证**：`sqlguard.py:115` 的 `_TABLE_RE` 只认 `FROM|JOIN`，**逗号连接的隐式连接表既不校验也不报血缘**；主控 2026-09-27 14:56:19 线上复现读出白名单外的 `ecommerce.test_connection`（`row_count=3`，真实数据，血缘只报 `['dwd_trade_order_detail']`）。见 P0-1 |
| C-03 | 守卫对**显式声明**的数据源（`FROM`/`JOIN`）强制表级白名单，并强制 SELECT-only + 强制 LIMIT | `sqlguard.py:148-213`（5 层顺序检查）；对抗用例 `tests/test_sql_guard_adversarial.py`（340 行） | 18 张表白名单、26 个禁用关键字、LIMIT 三形态、`max_limit<=0` | **SUPPORTED** | 只对显式 `FROM|JOIN` 生效（C-02）；`LIMIT` 正则以 `$` 锚定句尾，UNION 前序各支不受 `max_limit` 约束（P2-13） |
| C-04 | MCP 路径与直连 HTTP 路径取数结果**逐字段一致**，且 MCP 失败**不回退** HTTP | `AGENTS.md:607,1036-1037`（`verify-sprint-10.sh` **84/0/0**；4 个只读工具；`mcp==2.2.0`）；`tools.py:139-146,148-162`；`services/mcp/app/server.py:56` 终点同一个 `/query`（S4 §A2） | 规范化 `rows` 逐字段 + `executed_sql`；验收脚本主动构造的用例 | **SUPPORTED** | 比对口径是"规范化 rows + executed_sql"，非字节级报文；**验收脚本的"两路径 tables 一致"断言对逗号连接 SQL 会一起"绿"**（两侧都不识别逗号表），故该断言**不能**发现 P0-1 |
| C-05 | 图式 Agent 具备**可计数**的反思重试能力，默认额度 2，且该路径被真实触发 | 能力：`graph.py:622,639,439,595`、`config.py:257`；触发（mock 单测）：`test_agent_graph.py:293-296,346,352`；触发（线上验收）：`verify-sprint-8.sh:243,254-257`（`retries≥1` + 轨迹含 `reflect`/`plan` + 失败码命中 `TABLE_NOT_ALLOWED`） | 代码 + mock 单测 + 线上验收脚本各 1 次 | **SUPPORTED** | 线上验收断言的是 **`retries ≥ 1`**，**不是 `= 2`**；`= 2` 只出现在 mock 单测（LLM 与工具均为预设替身，不发 HTTP） |
| C-06 | 线上问答中"平均重试 N 次 / 重试率 X%" | 未找到任何对线上 Agent 日志中 `retries` 的统计或归档（S4 §B3） | — | **UNVERIFIABLE** | 验收用的失败是脚本**主动构造**的问法（`verify-sprint-8.sh:221-225`），不是用户自然提问；**没有这个数据** |
| C-07 | 检索层是**词法检索**（BM25 + CJK bigram + 显式同义词表），**未引入向量库** | `lexical.py:52-53,304-311,326,340`（BM25 k1=1.2/b=0.75 + idf + 长度归一化 + 标题加成）；`lexical.py:57,105-115`（CJK bigram 自实现）；`lexical.py:123-178` + `knowledge/synonyms.json`（`synonyms` 55 条）；`retrieval.py:63,243-259`（`backend="lexical-bm25"`、`vector_store=None`）；全目录 grep `embedding|vector|faiss|chroma|milvus|pgvector|cosine` → 仅命中 3 处"不引入"注释（S4 §C1） | 代码 + 依赖清单（未重跑检索单测） | **SUPPORTED** | 同义词条数文档写 54、实测 55（P2-11）；`docs/PROJECT_DESIGN_V1.md:171` 残留"RAG（向量检索）"旧表述（P2-12）。**不可写**"向量 RAG / Embedding RAG / 语义检索 / 混合检索 / jieba 分词" |
| C-08 | 批流一致（**交易域**）：区间内 1 分钟窗口逐窗口指标一致，不一致 0 | 代码：`04_reconcile.sql:134`（`FULL OUTER JOIN` on `window_start`）、`:121-130`（8 个判据列）、`:154`（`mismatched_windows`）、`:158`（`is_pass`）；作业断言：`reconcile_batch_realtime.py:172-185`、`_common.py:280-286`（失败即非 0 退出） | **对账区间内**（两侧 `MIN/MAX(window_start)` 求交 + 尾部留 3 分钟）、**8 个判据列**、**补零口径**；主控实测 11458/11458、mismatched 0、is_pass 1 | **PARTIALLY_SUPPORTED** | ① 交易域**没有**"仅实时 / 仅离线窗口 = 0"的断言，只有"实时侧 ≥ 离线侧 ×90%"（`reconcile_batch_realtime.py:38,177-182`）→ **不能**推出"两侧窗口集合完全相同"；② `is_match` 是"**补零后**数值相等"，`NULL` 与真实 `0` 不可区分（`04_reconcile.sql:94-129`）→ **不能**推出"两侧都产出了该窗口"；③ 交易域**派生比率列完全没进对账**（`reconcile_batch_realtime.py:81-91`）；④ 证据只覆盖对账区间（区间外含最后 3 分钟**明确未比**）；⑤ 建立在**合成数据集**上 → **不能**写"生产环境已验证" |
| C-09 | 批流一致（**流量域**）：19643 个窗口逐窗口计数一致、单边窗口 0、不一致 0 | 代码：`08_traffic_reconcile.sql:145-153`（7 个判据列）、`:232`（`is_pass`）；作业断言：`reconcile_traffic_batch_realtime.py:274-279`（含 `realtime_only_windows==0`、`batch_only_windows==0` 两条硬断言，**交易域没有**）、`:319-321`（离线侧比率自洽硬断言）；主控实测 19643/19643、`realtime_only=0`、`batch_only=0`、`mismatched=0`、`is_pass=1` | 对账区间内、7 个判据列（`uv` + 6 个可加计数）、补零口径 | **PARTIALLY_SUPPORTED** | 判据列**不含比率列**（`click_rate`/`cart_rate`/`buy_rate` 只落 `diff_*` 留证，`:136-144`）→ **不能**写"两侧比率也一致"；**实时侧 1 个窗口存在真实数据缺陷且不阻断退出码**（`reconcile_traffic_batch_realtime.py:319-334`）→ "0 mismatch" 与"实时链路存在已知缺陷"**必须一起说** |
| C-10 | Iceberg 迁移**未改变数据**（无限定表述） | 迁移作业断言：`migrate_parquet_to_iceberg.py:246-248`（逐表 `COUNT(*)`）、`:255-260` + `:117-125`（6 个金额列整表合计）、`:314-316`（`Provider == iceberg`）、`:326-328`（库表数 == 清单表数）；`verify-sprint-5.sh:411-448`（逐表名 + 反向无多余表） | **18 张（首轮）→ 23 张表**；**只覆盖**：表身份、行数、6 个金额列整表合计、表名集合、1 张表的快照可见性 | **PARTIALLY_SUPPORTED** | 全部证据都是**聚合量与表身份**，**没有任何一条断言触及行内容**；`SUM` 可正负抵消、行数相等对"等量替换"免疫（S3 §2 的 V1–V11 全部未验证）→ **不能**写"完全无损 / 逐行一致 / 字节级一致 / schema 已验证 / 分区已验证 / 可回滚已验证" |
| C-11 | Iceberg 迁移"所有字段 / 逐行 / 字节级一致" | — | — | **UNVERIFIABLE** | 当前方法集**原理上无法**证明（无逐行 / 哈希 / 双向 `EXCEPT ALL` 证据）。这是"**没有测量过**"，不是"未发现差异" |
| C-12 | `60/60` 的构成 = 18 Provider + 18 行数 + 23 金额列 + 1 库表数 | 判据构成 `migrate_parquet_to_iceberg.py:314,248,255-260,328` + 按 DDL 逐表数列（S3 §1.4 表） | 18 张表那次运行 | **SUPPORTED** | 独立复算得 60，**完全吻合**；可当"可核对事实"引用 |
| C-13 | `70/70` 的构成 = 23 + 23 + 23 + 1 | 复算同法（前提：两次运行之间 `MONEY_COLUMNS` 与判据代码未变） | 23 张表那次复跑 | **PARTIALLY_SUPPORTED** | 数值自洽且复算得 70，但**原始逐项细目与 `[check]` 输出行文档未记录**（唯一出处是 `AGENTS.md:604` 一句汇总）；"判据未变"只能由 `SPRINT_5.md` §11 变更记录推断（本阶段**禁止执行 git**，无法核对提交历史） |
| C-14 | 数据为**程序生成的合成数据**，可用固定种子复现 | `GEN_RANDOM_SEED=20260926`（`config.py:140`）；`data-generator/state/dataset_snapshot.json:3-4`（`seed: 20260926`、`generated_at: 2026-09-26T07:01:09+08:00`）；`REPORT_DRAFT.md:18,1359`、根 `README.md:86,98`（S2/S6） | 仓库 + 生成器配置 | **SUPPORTED** | 数据**不含真实业务分布**。审计时 `REPORT_DRAFT.md:443` 曾把合成数据标为"真实数据"（原 P1-26）→ **已在窗口内闭环**（现 `:457` 已改为"程序生成的合成数据集…不代表真实业务分布"，见 §9.6）；`毕业论文_*.docx` 需重新导出 |
| C-15 | 流量域归档"零丢失"（20000 行 == Kafka latest offset 合计） | `AGENTS.md:867`、`README.md:870`、`REPORT_DRAFT.md:1150`（判据 + 数字 + 703 个 `dt` 分区 + 脚本） | **单次**归档验收的一次等式；Kafka 副本 1 | **PARTIALLY_SUPPORTED** | 支持"该次验收窗口内未观察到丢失"，**不支持**无限定的"零丢失"；**不可**写"任何时候都不会丢" |
| C-16 | 湖仓层间一致（不一致表数 0） | `AGENTS.md:911`：`DWD == ODS 逐表，不一致表数 0（Iceberg 5 张交易表）` | **仅 Iceberg 5 张交易表** | **PARTIALLY_SUPPORTED** | 根 `README.md:61` 与项目 `README.md` 未带此范围，连读会被读成"全层零差异"（P1-11） |
| C-17 | 只读接口延迟：点查 8.5 ms / 聚合 8.3 ms / 关联 11.7 ms / `GET /overview` 53.8 ms | **原始采集输出** `/tmp/perf-a.log`（n=7 中位数 + min/max 逐项吻合）；方法 `measure-latency.sh:129-159`；数值 `PERFORMANCE.md:67-70`（S5 §（一）与 §（三·补）A） | 单机 4 核/16 GB、`127.0.0.1` 回环、单请求串行、n=7、**热缓存**、`ads_realtime_trade_1m` 11459 行 | **SUPPORTED（关联项须加注）** | **11.7 ms 那一格采集时 `dim_product` 为 0 行、`row_count=0`**，即测到的是"JOIN 空表"的代价而非两表关联；缺陷已修（`DECISIONS.md:133`），同口径重跑返回 10 行、中位数约 **7.4 ms**（2026-09-27 只读复核，S5 §（三·补）E）→ 该格**必须**加注或重采（P1-30） |
| C-18 | 平台"生产可用" / "支持高并发" | 未做任何并发/压力测试（`measure-latency.sh:112-115,142-146,192-196,339-344` 全为串行；`SPRINT_12.md:225-226,415`、`PERFORMANCE.md:333`、`DATA_AND_LIMITATIONS.md:280` 明写"不做"）；单机单副本（`DATA_AND_LIMITATIONS.md:206`）；Airflow `SimpleAuthManager` 官方标注 not for production（`:227`）；只读接口无鉴权（`REPORT_DRAFT.md:1362`） | — | **NOT_SUPPORTED** | 该主张**从未被任何实验触碰**；**不应以任何形式声称** |
| C-19 | Agent `/ask` 的 19187.5 ms 可解释为"数据平台查询性能" | 数据侧可单独测量部分仅 18.3 ms（检索 2.9 + 取数 15.4）= **0.1%**；其余 99.9% 为外部 LLM 服务往返（含 1 次反思重试）；`measure-latency.sh:326` 自陈"LLM 延迟不可单独测"；`PERFORMANCE.md:217-219`、`REPORT_DRAFT.md:1303` 明写差值"不得表述为模型推理耗时" | 1 个问题（`最近一周每天的 GMV 是多少？`）的 2 个有效样本 | **NOT_SUPPORTED** | 该数字**既不是数据平台的能力，也不是数据平台的瓶颈**；**必须显式否定**这一读法 |
| C-20 | 外部审查包 §2 验收矩阵的数字（`verify-sprint-12.sh` 42/0/0、pytest 381 passed、`verify-sprint-5.sh` 88/0/0） | `外部审查包.md:122-131` vs 权威值 `AGENTS.md:609,1047`、`SPRINT_12.md:287,445`（32/0/0、353 passed、Sprint 5 未记录脚本计数） | — | **NOT_SUPPORTED** | 三个数字在仓库其他文档中**没有任何出处**（S6 §P1）；而该审查包的用途正是供外部核对"有没有编数字" |
| C-21 | 数据规模 1200 users / 600 products / 6000 orders / 5406 payments / 254 refunds / 20000 behavior events | 见 §2 逐项来源（payments/refunds 另有 Kafka 源 topic 位点独立复核） | 前 5 项为事实源与落地规模；第 6 项：**已定案** | **全部 SUPPORTED** | 行为事件基准值 = **20000**（五源一致，见 §2.1，**选定 B：40000 判为错误**）；仍需注意：行数 = distinct `event_id` 基数 ≠ 链路处理量（P1-34） |
| C-22 | `Sprint 0~12 全部完成并验收通过` | `AGENTS.md:597-609` 状态表（逐行带验收计数 5/5、11/11、8/8、8/8、40/0/0、7/7、49/49、71/0/0、59/0/0、84/0/0、61/0/0、32/0/0）；13 个 `scripts/verify-sprint-*.sh` 实际存在；12 份 `SPRINT_N.md` 实施记录同向（S2 §C1、S6） | 文档 + 脚本存在性 | **SUPPORTED** | 反证是**文档滞后**（Sprint 3/4/5 头部写"进行中"、项目 README 三处 🔄），不是"未完成"的证据；`SPRINT_5.md:309` 保留"一项未归零"（`click_rate`），**不得因状态改 ✅ 而删除** |

---

## 2. Data Scale

> `Verified` 取值：✅ = 已被**独立只读复核**（Doris `COUNT(*)` / 事实源核对）；⚠️ = 仅有文档记录，本次未独立复核；❌ = 存在冲突，未定案。

| Item | Value | Source | Verified |
| --- | --- | --- | --- |
| `dim_user`（用户） | **1200** | `config.py:110`（`GEN_USER_COUNT`，S8 §1.2）；`SPRINT_0_VERIFICATION_STATUS.md:140`；`README.md:50` | ✅ Doris 只读 `COUNT(*)` = 1200（2026-09-27 14:5x，S5 §（三·补）D） |
| `dim_product`（商品） | **600** | `config.py:111`（`GEN_PRODUCT_COUNT`）；`SPRINT_0_VERIFICATION_STATUS.md:140` | ✅ Doris 只读 = 600（同上；**性能采集当时为 0 行**，缺陷已修 `DECISIONS.md:133`） |
| 订单（orders） | **6000** | `config.py:112`（`GEN_ORDER_COUNT`）；`SPRINT_0_VERIFICATION_STATUS.md:140` | ✅ Doris `dwd_trade_order_detail` = 6000（同上） |
| 支付（payments） | **5406** | `SPRINT_0_VERIFICATION_STATUS.md:140`；`README.md:50`；`AGENTS.md` §15.4；**新增独立复核**：Kafka `payment_event` latest 合计 = `1739+1830+1837 = 5406`（S8 §2.2） | ✅ Kafka 源 topic 位点独立复核（2026-09-27 14:5x） |
| 退款（refunds） | **254** | 同上；Kafka `refund_event` latest 合计 = `69+94+91 = 254`（S8 §2.2） | ✅ Kafka 源 topic 位点独立复核（同上） |
| 行为事件（behavior events）**当前值** | **20000** | Kafka `behavior_event`：earliest 全 0、latest 合计 = `6506+6592+6902 = **20000**`（S8 §2.2）；全量消费实测 **20000 条、去重后仍 20000 条**（S8 §2.8）；Doris `SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail` = **20000**、`COUNT(DISTINCT event_id)` = 20000（S8 §2.4，14:54 与 14:5x 两次一致）；行为分布 `VIEW 10472 / CLICK 5759 / CART 2095 / FAVORITE 1046 / BUY 628 = 20000` 与文档逐项吻合 | ✅ **已定案（选定 A，见 §2.1）**：Kafka 侧 + Doris 侧两条独立观测一致 |
| 行为事件 —— `SPRINT_3.md:52` 的 **40000** | `dwd_traffic_behavior_detail` = 40000（"与 MySQL 1:1"） | `SPRINT_3.md:52` | ❌ **NOT_SUPPORTED（无法从当前状态复现）**：当前 Kafka 与 Doris 两侧均为 20000；成因不可考（U-21）。**该行必须改写或加注** |
| `event_id` 上限 `evt_420000` 的含义 | **生成器进程内序号**，**不是** topic 累计条数 | Kafka 全量 `event_id` 直方图：两段 `40001–49999`（9999 条）与 `410000–420000`（10001 条），唯一断点 `49999 → 410000`（S8 §2.8）；段文件起点 offset = 0、无 `DeleteRecords`/retention 截断（S8 §2.9）；解读见 S8 §2.10 | ⚠️ 口径已澄清：`evt_420000` 只证明"**某一次运行内至少产出过 420000 个行为事件**"（420000 = 21 × 20000）；**不能**推出"Kafka 里有 42 万条"。两段编号如何与"topic 只写过 20000 条"完全相容**仍无中间证据**（U-23） |
| Flink 侧累计产出（**不是**表行数） | `dwd_traffic_behavior_detail` topic = **580000** = 29 × 20000；交易域三表同比值（174000/6000、156774/5406、7366/254） | S8 §2.2/§2.6（Kafka latest 合计）；`SHOW ROUTINE LOAD` `loadedRows=580000`、`errorRows=0`、`committedTaskNum=29`（S8 §2.5） | ✅ 已核实（决定"行数 = 去重后行数 ≠ 处理量"的口径，见 P1-34） |
| 离线 ODS 归档行数（`lakehouse.ods_behavior_event`） | **20000**，**703** 个 `dt` 分区 | ① 归档作业自打印日志：`[archive] Kafka 实际消息数 = 20000`、`解析完成，有效行数 = 20000`（任务 `success`，S8 §2.11）；② Hive Metastore `TABLE_PARAMS.numRows = 20000`（`table_type = ICEBERG`，`numFiles = 703`，S8 §2.12）；③ Iceberg 快照 `total-records = 20000` × **3 个快照**（每次 `overwrite`、703 分区整体重写，S8 §2.13）；目录级：19M、范围 `2024-10-02 … 2026-09-26`（S8 §2.7） | ✅ 三源一致（作业日志 / Metastore / Iceberg 元数据） |
| 离线 DWD 行数（`lakehouse.dwd_traffic_behavior_detail`） | **未直读** | Hive 表存在（`TBLS`：`TBL_ID=41`, `DB=lakehouse`），但本轮**未跑 Spark**（内存纪律：可用 3071 MB、swap 已用 3238 MB） | ❌ **UNVERIFIED**（U-22）；补法：错峰只读 `bash scripts/spark-sql.sh -e "SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail"` |
| `ads_realtime_trade_1m` | **11459** 行（2026-09-27 快照） | `PERFORMANCE.md:31`；S5 §（三·补）D | ✅ Doris 只读 = 11459 |
| `ads_reconcile_trade_1m`（明细） | **11458** 行，不一致 **0** | 主控实测 2026-09-27 14:55:12（S7） | ✅ 只读 `SELECT` |
| `ads_batch_trade_1d` | **626** | S5 §（三·补）D | ✅ Doris 只读 |
| `ads_batch_category_1d` | **2834** | S5 §（三·补）D | ✅ Doris 只读 |
| 随机种子 | **20260926** | `config.py:140`；`state/dataset_snapshot.json:4`；`generated_at=2026-09-26T07:01:09+08:00`（`:3`） | ✅ 仓库实测 |
| 质量校验条目 | **25**（Doris 侧默认实跑 **24** + 1 条 spark 引擎项需 `--with-lake`） | `AGENTS.md:905`（24 通过 / 0 失败 / 1 跳过）；`DATA_AND_LIMITATIONS.md:298`（E5 登记 25 vs 21）；S6 §P2 | ⚠️ 25 这个数字未逐段点算 `infrastructure/quality/checks.conf`（S6 §U5） |

### 2.1 20000 vs 40000 争议状态（**单独标注 → 已定案：B**）

**结论（L7 §4 判定）：选定 B —— `40000` 判为错误；基准值是 `20000`。**

| 候选 | 内容 | 判定 |
| --- | --- | --- |
| **A** | 两个数**都对**，只是口径不同，可并存 | ❌ **NOT_SUPPORTED**：没有任何口径能把 40000 解释成当前值；该表行数 = distinct `event_id` 基数，当前所有只读路径一致给 20000。"两者都对"属平均化矛盾，**不采纳** |
| **B** | **`40000` 判为错误；基准值 = `20000`** | ✅ **选定**。五条互相独立的只读证据：① Kafka 源 topic 位点 latest 合计 = 20000（earliest 全 0，全量消费亦 20000）；② Doris `COUNT(*)` / `COUNT(DISTINCT event_id)` = 20000 / 20000（**三次观测一致**）；③ **归档作业自己打印** `[archive] Kafka 实际消息数 = 20000`、`有效行数 = 20000`（任务 success，服务器 `syslog`）；④ Hive Metastore `TABLE_PARAMS.numRows = 20000`（`table_type = ICEBERG`）；⑤ Iceberg **3 个快照** `total-records` **全部 = 20000**（从未写出 40000） |
| **C** | `20000` 判为错误；基准值 = `40000` | ❌ **NOT_SUPPORTED**：无任何当前证据支持 |

**为什么 40000 不可能是"两轮累计"**：归档作业读 `earliest → latest` **全量** topic，
以 `INSERT OVERWRITE` + **动态分区覆盖**写入（幂等、不累加，S8 §1.4）；
Iceberg 三次快照的 `added-records` / `deleted-records` 均为 20000 / 20000（整体替换）。
**40000 会让归档作业自己的硬断言 `归档行数 == Kafka 消息数` 直接失败**，而三次归档运行全部 success。

**40000 的唯一出处与性质**：`docs/sprint/SPRINT_3.md:52` 的"事实核查"行，**无观测时间**，
且同行还写"`dwd_traffic_behavior_detail` … 与 MySQL 1:1" —— 而**流量域在 MySQL 没有源表**
（Sprint 3 自述的设计缺口，Sprint 4 才归档补齐）→ 该行口径本身也不成立。
**处理原则：保留该数字并标注为当时的观测误差，不抹掉**（措辞模板见 S8 §7.1）。

**40000 的成因：`UNRESOLVED`（历史不可回溯），但不影响当前值判定**：
可确认的机制是"行数 = distinct `event_id` 基数"；合理但**未证实**的推测是
2026-09-26 13:18 重建前曾装载过一批 `event_id` 不同的行为事件（旧 ∪ 新 → distinct 一度 40000）。
无法证实的原因：13:18 的重建把 Kafka 旧状态整个换掉（earliest = 0、无 `DeleteRecords` 痕迹、
4 个源 topic 的 `partition.metadata` 时间戳集中在 13:18:11–13:18:17、20000 条消息 `CreateTime`
全部落在 **586 ms** 内）；Iceberg 仅留 3 个快照；`syslog` 只回溯到 2026-09-27 00:00
→ **没有任何一处留存"重建前的 event_id 集合"**。历史侧记为 **UNRESOLVED DATA-STATE DISCREPANCY**。

**⚠️ 同时必须澄清的一条口径（防误读）**：`evt_420000` 是**生成器进程内序号**，
与 topic 条数**无关** —— 两段编号 `40001–49999`（9999 个）与 `410000–420000`（10001 个）
**`CreateTime` 首尾相接**（在 13:18:36.934 处切换）、**中间 36 万个序号缺失**、全过程仅 586 ms
→ 它只说明"该次生产运行内**事件序号与写入条数解耦**"，**不能**推出"Kafka 里有 42 万条"。
当前可核对的消息条数**只有一个口径：20000 条**。**不得**写"实时链路累计处理 42 万条行为事件"。

---

## 3. Batch / Stream Reconciliation

> 数据来源：**主控只读实测 2026-09-27 14:55:12（S7）** + 代码/断言核对（S1）。
> 判据代码：交易域 `04_reconcile.sql`、`reconcile_batch_realtime.py`；流量域 `08_traffic_reconcile.sql`、`reconcile_traffic_batch_realtime.py`。

### 3.1 交易域（Trade）

| 指标 | 值（主控实测） | 判据出处 |
| --- | --- | --- |
| `realtime_windows` | **11458** | `04_reconcile.sql:151` |
| `batch_windows` | **11458** | `04_reconcile.sql:152` |
| `matched_windows` | **11458** | `04_reconcile.sql:153` |
| `mismatched_windows` | **0** | `04_reconcile.sql:154` |
| `is_pass` | **1（true）** | `04_reconcile.sql:158` |
| 明细表 `ads_reconcile_trade_1m` | **11458 行、不一致 0** | :92-130 |
| 其中**两侧 GMV 均为 0**（平凡一致） | **5504 个窗口（48.03%）** | 主控实测派生 |
| 其中**有业务量**（具判别力） | **5954 个窗口（51.97%）** | 主控实测派生 |
| 最近 3 个批次两侧 GMV | **均为 51890375.77** | 主控实测 |

- **可比对窗口数 = 11458 与 11459 的关系**：`ads_realtime_trade_1m` 为 **11459** 行，
  对账区间由两侧 `MIN/MAX(window_start)` 求交、**尾部留 3 分钟安全边界**（`reconcile_batch_realtime.py:36` 的
  `TAIL_GUARD_MINUTES = 3`，区间终点 = `min(两侧最大窗口) + 1 分钟 − 3 分钟`），
  实际比对 **11458** 个窗口（源表最新窗口 `11:37:00`，比对覆盖到 `11:32:00`）。
  两数**同源不同口径**，引用时必须带口径（P1-9）。**注意：差 2 个窗口（不是 1 个），
  且这是判据成立的前提**（只比对两端都已封窗的窗口），不是数据缺失、也不是时点漂移。
- **5504 个零-零窗口的证据权重（关键）**：这 48.03% 的窗口"两侧都是 0"，属于**平凡一致**——
  它们**不能**用来支持"两条链路算得一样"；真正承载证据的是 **5954 个有业务量的窗口**。
  报告口径应写成"11458 个窗口中 5954 个有业务量，逐窗口一致；另有 5504 个窗口两侧均为 0（平凡一致）"，
  **不得**用"11458 个窗口全部独立验证一致"这类把平凡窗口算作独立证据的写法。
- **零-零窗口与理论漏洞的叠加（必须写进论文局限）**：交易域判据是"补零后相等"（`04_reconcile.sql:94-129`），
  因此"一侧缺窗口 + 另一侧 8 个指标全为 0"会被判为 `is_match`。
  在 5504 个零-零窗口的背景下，该理论漏洞的**现实边界**是：`mismatched_windows = 0`
  **不严格等价于**"单边窗口 = 0"。交易域**没有**单边窗口断言（见 §3.3）。

### 3.2 流量域（Traffic）

| 指标 | 值（主控实测） | 判据出处 |
| --- | --- | --- |
| `realtime_windows` | **19643** | `08_traffic_reconcile.sql` 汇总段 |
| `batch_windows` | **19643** | 同上 |
| `realtime_only_windows` | **0** | 硬断言 `reconcile_traffic_batch_realtime.py:275` |
| `batch_only_windows` | **0** | 硬断言 `:276` |
| `mismatched_windows` | **0** | `08_traffic_reconcile.sql:232`（`is_pass` 判据） |
| `realtime_rate_anomaly_windows` | **1** | `08_traffic_reconcile.sql:158-171`；落表 `:230` |
| `batch_rate_anomaly_windows` | **0** | `08_traffic_reconcile.sql:172-185`；硬断言 `reconcile_traffic_batch_realtime.py:319-321` |
| `is_pass` | **1（true）** | `08_traffic_reconcile.sql:232` |

### 3.3 `is_pass` 与 diagnostic anomaly 的严格区分（**本节是全文件最容易被误读的一处**）

| 项 | 是否进入 `is_pass`？ | 是否影响作业退出码？ | 证据 |
| --- | --- | --- | --- |
| 交易域 8 个判据列逐窗口相等（7 可加 + `order_user_cnt` 去重） | **是**（`is_pass` = 不一致窗口数 == 0） | **是**（`reconcile_batch_realtime.py:183,185`） | `04_reconcile.sql:121-130,154,158` |
| 交易域"实时侧覆盖率 ≥ 90%" | 否（独立断言） | **是** | `reconcile_batch_realtime.py:38,177-182` |
| 交易域"两侧 GMV 合计相等" | 否（独立断言） | **是** | `:184` |
| 流量域 7 个判据列逐窗口相等 | **是** | **是** | `08_traffic_reconcile.sql:145-153,232` |
| 流量域"仅实时 / 仅离线窗口 = 0" | 否（推论，非 `is_match` 组成） | **是** | `:275-276` |
| 流量域**离线侧**比率自洽（`batch_rate_anomaly == 0`） | 否 | **是**（硬断言） | `reconcile_traffic_batch_realtime.py:319-321` |
| 流量域**实时侧**比率自洽（`realtime_rate_anomaly`） | **否** | **否 —— 刻意不 check，只打印 + 落表** | `reconcile_traffic_batch_realtime.py:323-334`（作者原话：写成会失败的 check 会让离线作业替实时链路"背锅"） |
| 比率列（`click_rate`/`cart_rate`/`buy_rate`）两侧比对 | **否**（不入 `is_match`，只在 `:136-144` 落 `diff_*` 留证） | 否 | `08_traffic_reconcile.sql:136-153` |

**因此以下三句话同时成立，必须一起说（缺一即为隐藏已知缺陷）：**

```text
① 流量域 19643 个窗口的 7 个判据列逐窗口一致，不一致 0、单边窗口 0，is_pass = 1；
② 同一个作业同时报出 1 个「实时侧」窗口的计数列与比率列自相矛盾
   （realtime_rate_anomaly_windows = 1；具体为 view_cnt=2、click_cnt=1，click_rate 记 0.0000，按口径应为 0.5000）；
③ 该缺陷位于实时链路，不进入 is_pass、不阻断作业退出码，修复需重部署 Flink 作业并重放，列为待决策事项。
```

> **`click_rate` anomaly 在本次收口审计中显式保留，不得删除、不得淡化、不得合并进"零差异"结论**
> （`audit-brief.md` §1.6 明令）。落点：`metrics.md` 第 3.3 节、`SPRINT_5.md:309-312`、
> `DEFENSE_SCRIPT.md:290`（"❌『两套系统完全一致，没有任何差异』← 有 1 个窗口的比率列缺陷，主动说"）。

### 3.4 对账结论的**可成立范围**（一句话版，与 S1 §7.1 一致）

> 在**本次对账区间**内（两侧数据范围求交 + 尾部留 3 分钟安全边界），于 **1 分钟窗口粒度**上，
> 对实时链路与离线链路产出的指标表按 `window_start` 做 **FULL OUTER JOIN**（不是 INNER JOIN，
> 单边窗口不会被静默过滤），在**缺失侧补 0 的统一口径**下逐窗口比较：
> 交易域 **7 个可加指标 + 1 个去重指标**（两侧同为近似去重）、流量域 **6 个可加计数 + `uv`**，
> 得到 `mismatched_windows = 0`、`is_pass = true`。
>
> **不能**由此推出：两侧窗口集合完全相同（交易域无此断言）／两侧派生比率一致／整个数据集一致
> （区间外未比）／实时链路数据正确（流量域已知 1 个缺陷）／生产环境成立（合成数据集）。

### 3.5 两条"看起来对不上"的数字，其实是口径（**必须写进口径注**）

| 数字对 | 解释（已核实） |
| --- | --- |
| 交易域 **11459**（表行数）vs **11458**（对账比对窗口数） | `ads_realtime_trade_1m` = 11459 行（`PERFORMANCE.md:31`；L5/L7 两次只读复核一致）；对账区间 = 两侧 `MIN/MAX(window_start)` 求交 + 尾部 3 分钟安全边界（`reconcile_batch_realtime.py:36`），实际比对 **11458** 个 → **差 2 个窗口属区间截断**（最新窗口 `11:37:00`，覆盖到 `11:32:00`），**不是数据缺失**；该截断是判据前提 |
| 流量域 **19644**（表行数）vs **19643**（对账比对窗口数） | `dws_traffic_overview_1m` / `ads_realtime_traffic_1m` = 19644（S8 §2.4，与文档 19644 一致），对账比对 **19643**（覆盖到 `13:13:00`，最新窗口 `13:16:00`）→ 同型口径差，同为 **2 个**尾部窗口 |

### 3.6 ⚠️ 对账结论**不覆盖**的一条运行事实：Flink 作业被重放 29 次

```text
Kafka 侧（DWD topic，Flink 输出）        Doris 实时表（按主键去重后）
  dwd_traffic_behavior_detail  580000  →  dwd_traffic_behavior_detail  20000   （29×）
  dwd_trade_order_detail       174000  →  dwd_trade_order_detail        6000   （29×）
  dwd_trade_payment_detail     156774  →  dwd_trade_payment_detail      5406   （29×）
  dwd_trade_refund_detail        7366  →  dwd_trade_refund_detail        254   （29×）
  Routine Load: loadedRows / totalRows 与左侧逐项吻合，errorRows = 0、unselectedRows = 0
```

- **含义**：Doris 实时表的行数是**按主键去重后**的结果（`UNIQUE KEY` + merge-on-write，
  `sql/doris/10_dwd_tables.sql:101-117`），**不等于链路处理量**。
- **对账结论为何仍然成立**：对账比较的是**当前状态**的两侧窗口指标（离线批次 vs 实时最新窗口），
  与"实时侧历史上重放过几次"无关；但**凡涉及"处理量/吞吐/与源端 1:1"的表述必须带口径**（P1-34）。
- **同样必须说的**：这 29 次重放是**开发/验收期间的运维事实**（`cancel-flink-jobs.sh` + 重放的组合），
  不是缺陷；它之所以要写清楚，是因为它会**改变读者对"行数"的理解**。

---

## 4. Iceberg

### 4.1 规模与计数

| 项 | 值 | 来源 |
| --- | --- | --- |
| 迁移清单 | **18 张表（阶段 4 首轮）→ 23 张表（+ 5 张流量域表后复跑）** | `migrate_parquet_to_iceberg.py:87-115`（`MIGRATE_TABLES` 显式列 23 张）；`SPRINT_5.md` §11 变更记录 |
| 首轮校验 | **`60/60`** | `SPRINT_5.md:338` 原文行 `✅ [check] ===== 60/60 通过 =====`；`AGENTS.md:871`；`DEVELOPMENT_LOG.md:913` |
| 复跑校验 | **`70/70`** | **仅** `AGENTS.md:604` 一句汇总额（**无原始 `[check]` 输出行**） |
| 计数构成（已独立复算） | `60 = 18 Provider + 18 行数 + 23 金额列 + 1 库表数`；`70 = 23 + 23 + 23 + 1` | S3 §1.4（按代码判据 + 逐表 DDL 数列；`70−60 = 10 = 5 张新表 × 2 项`） |
| 两次范围不同的原因 | 5 张新增流量域表**一个 `MONEY_COLUMNS` 列都没有** → 金额条数两次都是 23 | S3 §1.4 逐表列清单 |

### 4.2 真实检查维度（逐项"验了 / 没验"）

**作业内（`migrate_parquet_to_iceberg.py`）只有 5 项检查，其中 1 项无效：**

| # | 检查项 | 位置 | 判据强度 | 验了？ |
| --- | --- | --- | --- | --- |
| A | `Provider == iceberg` | `:314-316`（`DESCRIBE EXTENDED` 读回，取不到值 → FAIL） | **强** | ✅ 验了（23/23 表） |
| B | 行数 `COUNT(*)` 相等 | `:246-248` | 中（只约束基数） | ✅ 验了（23/23 表） |
| C | **6 个**金额列整表合计相等 | `:235-241,255-260`（`MONEY_COLUMNS` = `gmv/amount/payment_amount/refund_amount/avg_order_amount/total_amount`） | 中偏强但**覆盖面窄**：可正负抵消、**合计相等 ≠ 逐行相等** | ✅ 验了（23 条；`price`/`cost`/5 个 decimal(10,4) 比率列**完全不覆盖**） |
| D | 库表数 == 清单表数 | `:326-328`（能发现"清单漏表"与"多出表"，**抓不到"等量替换"**） | 强 | ✅ 验了（1 条） |
| E | 源端非空守卫 | `:250-253` 调 `check_true(name, "行数 > 0", lambda: False)`，而 `_common.py:262-265` 实现是 `ok = bool(actual)` → `bool("行数 > 0")` **恒为 True** | **恒真，等于没有** | ❌ **无效**（实测两次运行各表都非空，故**未污染** 60/60 与 70/70 的分母；但"防线意图未生效"） |
| F | 幂等性 | `:209-232` `INSERT OVERWRITE`（是**写法**，不是断言） | — | ⚠️ 无断言（文档若声称幂等，依据在此写法） |

**作业内之外（`verify-sprint-5.sh` 第 4 步 `step_iceberg`，L394-506）另有 5 类检查 —— 文档未记录，属"白捡的证据"：**

| # | 检查 | 位置 | 验了？ |
| --- | --- | --- | --- |
| G | 23 张表**逐个表名**都在 | `:411-429` | ✅ |
| H | **反向**：库里没有清单之外的表 | `:436-448` | ✅（比"数量 == 23"更强；`SPRINT_5.md` §9.6 把这条误写成"库表数 = 23 的断言"，见 P2-8） |
| I | 5 张流量表 Parquet == Iceberg 行数 | `:453-460` | ✅ |
| J | `Provider == iceberg`（**只 1 张表**） | `:480-485` | ✅（覆盖 1/23） |
| K | 快照表可查 + 时间旅行 `VERSION AS OF` 最早快照行数一致 | `:487-505` | ✅（只 1 张表，且只验"快照数 > 0"与行数） |

### 4.3 **未验证**的维度（V1–V11，全部"没验"）

| # | 维度 | 状态 | 要验证它需要什么 |
| --- | --- | --- | --- |
| V1 | 字符串字段内容（`username`/`product_name`/`brand`/`category_name`/`province`/`event_type`…） | **未验证**（没有任何断言读这些列的值） | 按主键排序后 `MD5(CONCAT_WS('|', …))` 聚合指纹两侧比对，或抽样逐字段 diff |
| V2 | 时间字段（时区 / 精度 / 类型映射，含 Spark `timestamp` ↔ Iceberg with/without zone **未判定**） | **未验证** | 两侧 `DESCRIBE` 逐列比对类型串；关键时间列 `MIN/MAX/COUNT(DISTINCT)` + 转字符串哈希 |
| V3 | NULL 与空串（`SUM()` **忽略 NULL**，故"NULL→0"这类变化金额判据抓不到） | **未验证** | 逐列比对 `COUNT(*) - COUNT(col)` 与空串计数 |
| V4 | decimal 精度与舍入（6 个金额列之外的小数列一律不核对） | **仅"DDL 同源"声明性保证，无断言** | 两侧 `DESCRIBE` 比对 `decimal(p,s)`；全部小数列逐列合计 + 分位/抽样 |
| V5 | 分区布局与分区值（解析失败会**静默退化为无分区表**，而行数/金额/Provider/库表数**四项照样全绿**） | **未验证**（分区列只被用于建表，无任何断言） | `SHOW PARTITIONS` 两侧比对；Iceberg 侧查 `<表>.partitions` / `.files` 的 `spec_id`；并补"分区表必须有分区列"断言 |
| V6 | schema 等价性（列顺序/列名/可空性）；`CREATE TABLE **IF NOT EXISTS**` 在表已存在时**静默复用旧定义**，而 `INSERT OVERWRITE` 是**位置映射** | **部分靠构造、无断言**（可空性完全没传递） | 迁移后 `DESCRIBE dst` 列名序列与类型串逐列断言 == `plan.columns`；再比 Iceberg `current-schema` 的可空标志 |
| V7 | 重复行与唯一性（`COUNT(*)` 相等对"多一行 A、少一行 B"**完全免疫**） | **未验证** | 每表主键 `COUNT(*) == COUNT(DISTINCT pk)` 或 `GROUP BY pk HAVING COUNT(*) > 1`；更强是双向 `EXCEPT ALL` |
| V8 | Iceberg 快照与元数据正确性 | 作业内**未验证**；验收脚本仅 1 张表弱覆盖（见 §4.2 K） | 逐表查 `<表>.snapshots` 的 `summary`（added/total-records）与 `.files`/`.manifests` 记录数，与 `COUNT(*)` 三方对齐 |
| V9 | 回滚可行性（代码层面只能读出"源库只被读、没被写"；**无回滚演练、无回滚断言、无"源表未被改动"的断言**） | **未验证**（`spark-defaults.conf:56-61` 自称"可验证、可回滚"属**设计论证**） | 做一次"删库重建"演练并留日志 |
| V10 | 压缩编码与表格式版本（`SPRINT_5.md:337` 把"Iceberg v2、snappy parquet"写成迁移**结果**） | **未验证**（无任何迁移后读回断言） | 读 parquet 元数据确认 codec；`SHOW TBLPROPERTIES` / `<表>.properties` 确认 `format-version` |
| V11 | 行级完整性（V1–V7 的并集） | **未验证**（现有证据全是聚合量，**没有一条能定位到行**） | 双向 `EXCEPT ALL` 均为空，或全表有序哈希比对 |

### 4.4 论文可主张的最大范围（有界表述，可直接使用）

> 在 Parquet → Iceberg 迁移后，对迁移清单内的 18 张（后扩至 23 张）表逐表核对了
> ①表目录身份（`DESCRIBE EXTENDED` 的 `Provider == iceberg`）、②行数（`COUNT(*)`）
> 与 ③ 6 个金额列（`gmv`/`amount`/`payment_amount`/`refund_amount`/`avg_order_amount`/`total_amount`）
> 的**整表合计**，三项在源、目标两侧**均未发现差异**（18 张表那次校验计数 `60/60`，
> 23 张表复跑那次 `70/70`，**两次范围不同**）；其中金额合计为 `DECIMAL` 精确比较、无容差。
> 该结论**不覆盖**行内容、字符串字段、时间字段、NULL/空串、decimal 精度与舍入、
> 分区布局、schema 等价性、重复行、快照元数据与回滚可行性（逐项见 `EVIDENCE_MATRIX.md` §4.3）。

---

## 5. Agent

| 维度 | 观测值 | Evidence | Status |
| --- | --- | --- | --- |
| **tables（可查表）** | 白名单 **18 张** = 16 业务（DWD 4 + DWS 1 + ADS 实时 3 + 维表 2 + ADS 离线 4 + 对账 2）+ 2 元数据（`tables`/`columns`）；`ast` 实测 `len(BUSINESS_TABLES)=16`、`len(ALLOWED_TABLES)=18` | `sqlguard.py:37-73,115,180,183`（S4 §A3） | **PARTIALLY_SUPPORTED**：白名单**存在且对显式 `FROM`/`JOIN` 强制生效**，但**逗号连接的表不进校验也不进血缘**（P0-1，线上复现）→ "Agent 只能读到白名单内的表"**不成立** |
| **executed_sql** | 一次 `/ask` 实测**执行 2 条 SQL**，首条为 `information_schema.columns` 元数据探测 | `PERFORMANCE.md:221-230`（从 `/ask` 响应直接读）；`/tmp/perf-bc.log`"执行 SQL 条数=2"（S5 §（三·B）） | **SUPPORTED（单次观测）**：样本为 1 个问答（n=2 次调用中的有效样本），**不能**外推为"平均执行 SQL 条数" |
| **docs（检索语料）** | **65 篇** = metric 22 / convention 15 / topic 4 / layering 8 / table 16 | `REPORT_OUTLINE.md:364` 分解；代码侧 `corpus.py:341-344`（`CORPUS_FILES`：`metrics.md` 155 行、`kafka_topics.md` 196 行、`layering.md` 63 行）+ `:348` `fetch_table_metadata`（16 张表结构，10 分钟 TTL，`retrieval.py:48`）（S4 §C1） | **PARTIALLY_SUPPORTED**：分解自洽、语料文件与代码一致，但"65 篇"**未逐篇装载复算**（需 HTTP 取表结构） |
| **graph trace** | 精确路径 `retrieve → plan → execute → validate → reflect → plan → execute → validate → summarize`；线上验收另断言轨迹含 `reflect` 且含 `plan` | `test_agent_graph.py:293-296`（精确路径）、`:336-359`；`verify-sprint-8.sh:254-257`；`GraphTrace` 由各节点自行 append（`graph.py:104-105,443-444`）（S4 §B1/B2） | **SUPPORTED**（mock 单测 + 线上验收两层；线上层断言的是"轨迹含 reflect"，不是精确路径） |
| **validation** | 只有 **blocking** issue（不以 `[警告]` 前缀开头）才触发 `reflect`；警告（`row_count == 0` / "已截断" / 含 `null`）**不触发重试**，只要求如实说明；三重终止上界（`max_retries` / `max_tool_rounds` / `total_timeout`） | `graph.py:399-417`（警告判定与前缀）、`:592-599`（路由三判据）、`:177-178`（超时） | **SUPPORTED**（代码级） |
| **retries** | 默认上限 **2**（`AGENT_MAX_RETRIES` 默认）；线上验收实测 **≥1**；`== 2` 仅出现在 **mock 单测**；线上真实分布**无数据** | `config.py:257`；`verify-sprint-8.sh:230,243`；`test_agent_graph.py:346,352`（剧本化 LLM/工具替身，不发 HTTP） | **PARTIALLY_SUPPORTED**：能力与触发 SUPPORTED；**"线上平均重试 2 次 / 重试率"= UNVERIFIABLE** |
| **MCP** | 4 个只读工具；MCP 与直连 HTTP 路径**逐字段 IDENTICAL**；`verify-sprint-10.sh` **84/0/0**；MCP 失败**不回退** HTTP；拒绝语义可透传（`is_error=False` + `NOT_SELECT` + 底层 HTTP 400） | `AGENTS.md:607,1036-1037`、`REPORT_DRAFT.md:927`；`tools.py:139-162`；`services/mcp/app/server.py:56`（S4 §A2、S6） | **SUPPORTED**（边界：比对为规范化 rows + `executed_sql`；"两路径 tables 一致"断言**不覆盖逗号连接**，对 P0-1 会一起"绿"） |

---

## 6. Security

> 判据代码全部在 `services/api/app/sqlguard.py`（5 层顺序检查，`:148-213`）；对抗用例在 `tests/test_sql_guard_adversarial.py`（340 行）。

| # | attack type | guard | test evidence | limitations |
| --- | --- | --- | --- | --- |
| 1 | 写/管理类语句（26 个禁用关键字：`insert update delete drop truncate alter create grant revoke set use call load export import replace rename backup restore kill shutdown into outfile into dumpfile load_file benchmark sleep`） | 第 1 层 `NOT_SELECT`（`:161-166`）+ 第 3 层 `FORBIDDEN_KEYWORD`（`:76-81`，带 `\b` 词边界 + 空白折叠） | 对抗用例覆盖 16 个（含大小写/换行/前置空白变形）；**探针实测其余 8 个**（`export/import/replace/rename/backup/restore/kill/shutdown`）**全部返回 `FORBIDDEN_KEYWORD`** | `drop` 只出现在句首路径；**10 个关键字无对抗用例**（规则有效、缺回归保护，P2-14） |
| 2 | 多语句拼接（`;`） | `MULTI_STATEMENT`（`:85`） | 含结尾分号与字符串字面量内分号的取舍用例（`test:162-176,269-276`） | 字面量内分号被拒属**刻意保守**（记录在测试里） |
| 3 | 注释注入（`--` / `/*` / `*/` / `#`） | `COMMENT`（`:86-89`） | 6 个位置用例（`:248-266`） | 孤立 `*/` 只被 `/* c */` 间接命中（P2-14） |
| 4 | 元数据探测（`union` + `information_schema`） | `METADATA_PROBE`（`:107`，双前瞻正则） | 3 个变体 + 一段修复史（`:217-242`）；单独查 `information_schema` 设计上放行 | 两种语序已覆盖；`WITH`/`SHOW`/`EXPLAIN` 无通道（第 1 层直接拒） |
| 5 | 未授权表（**显式** `FROM`/`JOIN`） | 第 4 层 `TABLE_NOT_ALLOWED`（`:179-189`，`_normalize_table` 去库名/反引号/方括号） | 5 种标识符写法 + JOIN 中仅一张表越权 → 整条拒绝（`:141-159`） | 仅覆盖 `FROM`/`JOIN` 显式引用 |
| 6 | **逗号连接（隐式 CROSS JOIN）** | **无守卫**：`_TABLE_RE`（`:115`）只认 `from|join`，**不含逗号分支**；`extract_tables`（`:133-145`）与校验（`:180`）**共用同一正则** → "没识别到" = "没校验" + "血缘漏报" | **主控线上复现 2026-09-27 14:56:19（S7）**：<br>① `SELECT … FROM ecommerce.test_connection` → **REJECT `TABLE_NOT_ALLOWED`**（对照，证明该表确在白名单外）<br>② `SELECT … FROM dwd_trade_order_detail a, ecommerce.test_connection b` → **ALLOW，`row_count=3`，返回真实数据，血缘只报 `['dwd_trade_order_detail']`（漏报）**<br>③ 对照：显式 `JOIN` 白名单内两表 → ALLOW 且血缘正确 | **P0-1**。影响：凡 `agent_ro` 在 Doris 上有 `SELECT_PRIV` 的表（`ecommerce.*` 全库 + `lakehouse_ads.*` + `information_schema.*`）都可用逗号连接读出，包括白名单外的同库表；**血缘信息同时漏报**，使 `verify-sprint-10.sh` 的"两路径 tables 一致"断言一起"绿"。全仓库 grep 无任何记录或测试（S4 §A6-1） |
| 7 | 行数上限（DoS / 全表拉取） | 第 5 层强制 LIMIT（`:191-213`，三种写法收敛；`max_limit<=0` → `BAD_LIMIT`） | 三种形态 + 聚合补 LIMIT + 精确错误码断言（`:301-317,320-332,335-340`） | 正则以 `$` **锚定句尾**：UNION 前序各支不受 `max_limit` 约束；LIMIT 后跟子句时守卫**追加**第二个 LIMIT 生成非法 SQL（fail-closed，但排障方向被带偏；P2-13） |
| 8 | 文件读写与时序攻击 | `into outfile`/`into dumpfile`/`load_file`/`benchmark`/`sleep`（`:76-81`） | 对抗用例（`:282-286`） | — |
| 9 | 凭据可达性 | 无驱动依赖 + 无直连代码 + 无凭据读点 + systemd 非 root 加固 | 四重证据（`requirements.txt` 6 项；`tools.py:48-50,167-185`；`config.py:47-77`；service `:46-47,65-71`） | Agent 进程**内存里短暂持有** `.env` 全量键值（`config.py:96-102` 未按白名单过滤），但**不可利用**（无驱动、无读点）（P2-10） |
| 10 | 越权写 / 权限过大 | 只读账号 `agent_ro` 仅 `SELECT_PRIV` | `install-web.sh:198-200`（`ecommerce.*` + `information_schema.*`）、`load-batch-to-doris.sh:352-362`（+ `lakehouse_ads.*`）；写操作被 Doris 拒绝（`verify-sprint-6.sh:169`、`verify-sprint-3.sh:352`） | 授权范围是**整库**，因此攻击 6 可读出白名单外的**同库**表；这是"守卫白名单"与"数据库授权"两层之间的落差 |
| 11 | 接口层绕过 | `/query` 只接受 POST，GET 同路径 403（网关契约） | `AGENTS.md` §15.6 记录 | 未做接口层模糊测试（本阶段只读） |
| 12 | 非 ASCII / 全角 / 同形字 | **无任何判据** | 探针未发现可利用手法（Doris 与守卫看同一文本） | "未覆盖但无可利用性"（P2-14 群）；若日后接入 Unicode 规范化会变成新面 |

**安全边界结论（措辞模板，论文/答辩必须按此写）：**

> Agent 进程**不持有可用的数据库凭据**，其全部取数必须经过 SQL 守卫与只读账号；
> 守卫对**显式声明的**数据源（`FROM`/`JOIN`）强制表级白名单，并强制 SELECT-only 与行数上限。
> **已实测**：写/管理类语句、多语句、注释注入、元数据探测、显式未授权表、文件读写与 DoS 函数均被拒。
> **已知缺口**：逗号连接（隐式连接）的表**不进白名单校验且不进血缘**，已线上复现（可读出白名单外的已授权表），列为 P0 待修。
> **不可写**："Agent 无法读取白名单外的任何表"／"不存在绕过路径"。

---

## 7. Performance

| metric | dataset | hardware | method | scope | limitations |
| --- | --- | --- | --- | --- | --- |
| 点查（10 行明细）= **8.5 ms**（min 7.8 / max 16.7；服务端 6 ms；10 行） | 目标表 `ecommerce.ads_realtime_trade_1m` = 11459 行；SQL 自带 `LIMIT 10` | 腾讯云 `lavm-txyjj7xzr7`，Ubuntu 24.04.2 LTS，**4 核 / 16 GB** | 中位数（同时给 min/max），客户端 `curl %{time_total}` + 服务端 `elapsed_ms`；**REPEAT=7 + 1 次预热不计入** | 2026-09-27 09:11:32–09:15 +0800；load 1.46/3.06/3.85；可用内存 3921 MB；Spark 作业 0；8 个 Flink sink RUNNING；**本机回环 127.0.0.1** | **热缓存**（同一 SQL 连打 8 次，表仅万行级）；**无并发**（单请求串行）；**冷缓存/冷启动未测**；**不含公网链路** |
| 聚合（全量 SUM/COUNT）= **8.3 ms**（min 7.7 / max 10.1；服务端 5 ms；1 行） | 同表全表扫 11459 行（无 WHERE） | 同上 | 同上（n=7 + 1 预热） | 同上 | 同上 |
| 两表关联 + GROUP BY = **11.7 ms**（min 7.8 / max 20.9；服务端 5 ms；**行数 0 ⚠️**） | `dwd_trade_order_detail` 6000 行 ⨝ `dim_product`；**测量时 `dim_product` 在 Doris 为 0 行** | 同上 | 同上（n=7 + 1 预热） | 同上 | **该次 JOIN 实际产出 0 行 → 测到的是"JOIN 空表"的代价，不是"两表关联"的代价**。缺陷已修（`DECISIONS.md:133`，提交 `b195d19`）；2026-09-27 只读复核同口径重跑 → `row_count=10`、中位数约 **7.4 ms**（S5 §（三·补）E） |
| `GET /overview` = **53.8 ms**（min 46.0 / max 72.7） | 未记录数据规模（文档只说明"一次拼了 6 条查询"） | 同上 | 中位数 + min/max（n=7 + 1 预热） | 同上 | 数据规模一格**未记录** |
| 批量作业 **1992.3 s**（端到端约 **81 分钟**） | 9 个 Airflow 任务 `duration` 之和：pause 18.5 / ods 61.6 / archive 363.3 / dwd 303.5 / dws 399.7 / ads 446.8 / reconcile 138.8 / load 128.8 / restore 131.3 | 本机单 worker、driver 768m（文档所述） | **非采样**：单次既有运行的 `task_instance.duration` 逐任务列出再求和；来源 = **Airflow 元数据库**（不是日志）；**未为此重跑流水线** | `scheduled__2026-09-26T19:00:00+00:00`，运行窗口 03:00:00→04:21:19；**n=1 次运行**（另给 1 次 manual run 对照） | ① 求和 **含 1 次重试 attempt**（该 run `try_number ∈ {1,2}`，`ads_layers` 是 try 2）→ 语义是"各任务**最终 attempt** 的 `duration` 之和"（P1-29）；② DB 实际 `SUM(duration)` = **1992.2**，文档 1992.3 是逐项四舍五入后相加（P2-17）；③ **原始取数输出缺失**（当时采集日志该分支 `[SKIP]`，P2-18）；④ 求和 ≠ 墙钟（差 48 分钟重试/等待窗口，文档已说明） |
| Agent `POST /ask` = **19187.5 ms**（min 18175.8 / max 20199.2；服务端自述 18173 ms） | 1 个问题：`最近一周每天的 GMV 是多少？`；引擎 LangGraph；模型 `deepseek-flash`（`LLM_THINKING=false`）；`retries=1`；执行 SQL 2 条 | 同上；**主体耗时在外部 LLM 服务（DeepSeek API），不受本机硬件支配** | 中位数 + min/max；客户端 `curl %{time_total}`；**原始日志 `{"n": 2, ...}`** | 同上现场；每次真实调用外部 LLM（单次约 18–20 s） | **实际 n=2，且脚本对 `/ask` 无预热**（文档写"3 次含 1 次预热"与日志不符，P1-28）；中位数 19187.5 **不受影响**（去样本不改变中位数） |
| 检索 `GET /api/retrieve?q=..&k=5` = **2.9 ms**（min 2.6 / max 3.1） | 语料 65 篇（§5） | 同上 | 中位数，**有预热**，n=7 | 同上 | 与图内 `retrieve` 节点是**另一次调用**（同逻辑，非同一次） |
| 取数 `POST /query`（复打 `executed_sql[0]`）= **15.4 ms**（min 14.3 / max 18.3） | 同一 `executed_sql` | 同上 | 中位数，**有预热**，n=7 | 同上 | 与图内 `execute` 节点是**两次不同调用** |
| 规划 + 汇总 ≈ **19169 ms** | — | — | **不是测量**：`19187.5 − 2.9 − 15.4`（差值法） | — | 脚本自陈"LLM 延迟不可单独测"（`measure-latency.sh:326`）；**不得**表述为模型推理耗时（`PERFORMANCE.md:217-219`） |
| 实时侧 vs 离线侧 | 7.1 ms vs 7.1 ms | 同上 | 同口径 n=7 中位数 | 同上 | 文档**主动说明"这个数据规模下测不出来"**，并登记为 D5（`DATA_AND_LIMITATIONS.md:284`）—— 本次审计中**边界写得最正确的一条** |
| **并发 / 吞吐 / P99** | — | — | **零并发测试**（脚本无任何并发原语，三重独立证据：脚本、文档自述、全库检索） | — | **完全没有数据**；"高并发/生产可用"**NOT_SUPPORTED** |

**性能结论的一句话说清（可直接使用）：**

> 在当前**单机实验环境**（腾讯云 4 核 / 16 GB、`127.0.0.1` 回环、无并发压力、单请求串行、
> 只读接口每项 n=7 取中位数且数据处于**热缓存**状态）下测得只读接口点查 / 聚合 / 两表关联
> 中位数为 **8.5 / 8.3 / 11.7 ms**、`GET /overview` **53.8 ms**（其中 11.7 ms 采集时维表为空，
> 修复后同查询约 7.4 ms）；批量作业 9 个任务 `duration` 合计 **1992.3 s**
> （对应端到端约 **81 分钟**，含 48 分钟重试等待窗口，取自 Airflow 元数据库既有记录、未重跑）。
> 上述结果**均为单机、单请求、回环、热缓存口径，未做任何并发或压力测试，
> 因此不构成对生产环境高并发能力的证明**。Agent 端到端 **19187.5 ms**（n=2）中，
> 可单独测量的数据侧仅 **18.3 ms**（检索 2.9 + 取数 15.4），即 **约 99.9% 的耗时来自
> 外部 LLM 服务往返（含 1 次反思重试）**，故该数字**不应被理解为数据平台的查询性能**。

---

## 8. Documentation

> 三条文档线的合并结论（S2 状态一致性 18 条 + S6 跨文档一致性 + S2 绝对化 9 条 + S6 绝对化清单）。
> 逐条编号与建议改法见 `FINAL_AUDIT_REPORT.md` §8 与 §11。

| 文档 | 与事实源的一致性 | 关键问题（编号） |
| --- | --- | --- |
| `AGENTS.md`（§15 状态表） | **与事实源最接近**（Sprint 0~12 全 ✅ 且带验收计数），是四方中的基准；§15.9 已于审计窗口内更新（见 §9.6） | §15.10 的 18 张表 / `60/60`（`:885`）与 §15 状态行的 23 张表 / `70/70` **未注明是两次范围不同的运行**（P2-9）；§15.12 首句（`:1019`）与实际不符（P2-33）；`:858` 引用不存在的 `docs/sprint/SPRINT_13.md`（P1-18） |
| `intelligent-data-platform/README.md` | **一条整体滞后的状态线**：文首进度只到 Sprint 7、验收块只到 Sprint 7、§7.3 端口表写"443 唯一入口"、§14 测试仍是 24+27、Roadmap 三处 🔄、"下一步"段停在 Sprint 10/12、L978-994 TLS 段、L131-133 ⏳、TOC/索引缺项 —— **共 12 处集中在同一文件** | Roadmap 🔄（P1-12）、TLS/端口（P1-14）、文首进度（P1-19）、"下一步"+191/138（P1-20）、§14（P2-24）、TOC/索引（P2-25）、⏳ 块（P2-26）；技术栈 Python/HDFS（P2-29）、缺 3 个验收脚本（P2-30） |
| `dsh-workspace/README.md`（仓库根） | 与 AGENTS 同向（Sprint 0~12 全 ✅），但 Sprint 13 状态过期 | `:70` "🔄 材料整理中" 与 `docs/thesis/` 已成稿（含 144 KB docx）不符（P1-17）；`:53` 标题"0~12 全部完成"与表格列 13 自相矛盾；层间零差异未带"仅 Iceberg 5 张交易表"范围（P1-11） |
| `docs/sprint/SPRINT_*.md`（0~12，共 15 个文件） | 尾部结算与 AGENTS 一致；**头部状态多处未回改** | **不存在 `SPRINT_13.md`**（P1-18）；`SPRINT_3.md:8`/`SPRINT_4.md:5`/`SPRINT_5.md:5` 头部"进行中"（P1-15）；`SPRINT_4.md:435-441` 三项待办实际已关闭（P1-16）；8/9/10/12 头部用"见第 N 节"外推（P2-27）；`SPRINT_0_VERIFICATION_STATUS.md:195` 的"服务器无 SWAP"**已被实测否证**（`Swap: total 4095 used 3238`，S8 §2.1；且 `scripts/setup-swap.sh` 存在）→ P2-28；`SPRINT_12.md` 对抗用例 60/63 两计数无时点（P2-33） |
| `docs/thesis/REPORT_DRAFT.md` | 主体口径已相当克制（§7.3 不足表、`:1359` 样板段），但有两处**与摘要直接矛盾** | `:443` "（真实数据，来源…）"（P1-26）；`:1305` §6.6 标题"真实数据缺陷"（P1-27）；`:369` "完全相同 / 想绕过也没有入口"（P2-37）；`:642`/`:731` "完全一致 / 零丢失"（P2-41）；`:16` "全部验收通过"未提 Sprint 5 一项未归零（P2-42 群） |
| `docs/thesis/外部审查包.md` | **本包是给外部核对"有没有编数字"用的，它自己带了三个无出处数字** —— 本次审计最严重的文档可信度问题 | `:131` 42/0/0 + 381 passed、`:124` 88/0/0 **无出处**（P1-21，NOT_SUPPORTED）；验收矩阵缺 Sprint 5 行却称"每个数字都是服务器上跑出来的"（P1-22）；`13 个验收脚本` vs `13 个 Sprint` 口径混用（P1-23）；"14 项待确认"实为 13 条（P1-25）；`:177` "完全一致"与 §5.4 自陈的 20000/40000 未定性冲突（P2-40） |
| `docs/DECISIONS.md` | 判定口径清晰；⏳ 条目状态与其它文档不一致 | ⏳12/⏳13 的"待跑"vs"已跑"（P1-24，UNVERIFIABLE）；第三节两条计划列过期（P2-34）；`:130/190/210` 的"必然/保证"**属正确用法，不改动**（S2 A-08） |
| `docs/PERFORMANCE.md` | 数字**真实且可复核**（`/tmp/perf-a.log` 逐项吻合、元数据库逐行吻合），口径写得比一般项目诚实 | `/ask` n 口径（P1-28）；批量含重试未说明（P1-29）；维表空表（P1-30）；来源说明过期（P1-31）；缺"不构成生产证明"（P1-32）；热缓存边界（P2-19） |
| `docs/DEVELOPMENT_LOG.md` | 末尾停在 2026-09-27 事故记录，恢复过程与 Sprint 10/11/12 收口、Sprint 13 均未入日志 | P2-36；`:122` "100% 保留"（P2-42 群） |
| 绝对化表述扫描（正面结论） | `生产可用`/`生产环境`/`高并发`/`低延迟`/`真实业务数据`/`全部验证通过`/`完全无损`/`完全证明`/`充分证明`/`已证明`/`无一例外` —— 五份被扫文件**全部 0 命中** | 真正的绝对化表述只有 **9 条**（A-01~A-09），其中 **2 条 P1**（`:443`、`:1305` 的"真实数据"），且都与**本文摘要自相矛盾**，而非"把没做的说成做了"（P2-37~P2-42） |

---

## 9. Unresolved Issues

> 逐条的**完整文字（文件:行 + 证据 + 为什么是这个级别）**见 `FINAL_AUDIT_REPORT.md` §3/§4/§5/§9。
> 本表只做索引与计数，**两份文件的编号与计数必须一致**。

### 9.1 P0（**1** 条）

| 编号 | 一句话 | 定级理由 |
| --- | --- | --- |
| **P0-1** | **逗号连接（隐式 CROSS JOIN）绕过表白名单，且血缘漏报** —— 线上复现 2026-09-27 14:56:19（S7） | 使核心安全主张"Agent 只能读到白名单内的表"**不成立**；且血缘漏报会让既有验收断言一起"绿" → 属"会导致答辩核心结论错误" |

### 9.2 P1（**32** 条）

| 组 | 编号 | 一句话 |
| --- | --- | --- |
| 批流对账证据边界 | P1-1 | 交易域**没有**"仅实时/仅离线窗口 = 0"断言，只有"实时侧 ≥ 离线侧 ×90%" |
| | P1-2 | 交易域 `is_match` 是"补零后相等"：`NULL` 与真实 `0` 不可区分 |
| | P1-3 | 交易域"**8 个可加指标**"表述错误（实为 7 可加 + 1 去重；`metrics.md:79` 同错） |
| | P1-4 | 交易域验收脚本**无非空/覆盖率/单边断言**，且取"最新批次"而非本次批次 |
| | P1-5 | 流量域文件头注释（"等于零才算真正全部一致"）与代码行为冲突（实时侧 anomaly 不阻断） |
| Iceberg 主张边界 | P1-6 | "Iceberg 迁移未改变数据"被写成无限定主张，且把"23 张表"与"`60/60`（18 张表那次）"并列 |
| | P1-7 | `70/70` 只有一句汇总，**无原始 `[check]` 输出、无逐项细目** |
| 数字同名不同范围 | P1-8 | `dwd_traffic_behavior_detail` 的 **20000 vs 40000**：**已定案（选定 B）= 40000 判为错误、基准值 20000**（五条独立只读证据，见 §2.1）；`SPRINT_3.md:52` 该行**必须改写**（保留 40000 作历史观测值 + 标注为误差 + 补"流量域 MySQL 无源表"的口径更正，模板见 S8 §7.1） |
| | P1-9 | 交易域窗口数 **11458 vs 11459**（及流量域 19643 vs 19644）未标口径（对账区间尾部截断，差 **2** 个未封窗窗口） |
| | P1-10 | 两个维表缺陷状态在四份文档里分成"已修/未修"两派 |
| | P1-11 | 层间"不一致表数 0"实际只覆盖 **Iceberg 5 张交易表**（`AGENTS.md:925`），根/项目 README 未带范围 |
| 文档状态一致性 | P1-12 | 项目 README Roadmap **三处 🔄**（Sprint 5/10/12）与 AGENTS 状态表冲突 |
| | ~~P1-13~~ | ~~`AGENTS.md` §15.9 与 §15 状态表自相矛盾~~ → **已在审计窗口内闭环，见 §9.6** |
| | P1-14 | README TLS/端口反向描述（`:451-452`、`:978-994`、`:56`、`:993` 指向已停用的 443） |
| | P1-15 | `SPRINT_3.md:8` / `SPRINT_4.md:5` / `SPRINT_5.md:5` 头部"进行中"与文末结算矛盾 |
| | P1-16 | `SPRINT_4.md:435-441` §10.3 三项待办**实际已全部关闭** |
| | P1-17 | 仓库根 `README.md:70` Sprint 13"材料整理中"过期（材料已成稿含 docx） |
| | P1-18 | `docs/sprint/SPRINT_13.md` **不存在**（`Test-Path` = False），而 `AGENTS.md:858` 与 §15 状态行**已引用它 → 悬空引用** |
| | P1-19 | 项目 README 文首"当前进度"只列到 Sprint 7（与同文件 Roadmap 冲突；两线分级分歧，取 P1） |
| | P1-20 | 项目 README"下一步：Sprint 10/12 收口"整段过期 + `191/138 skipped` 与 `353/0/0` 同名不同范围（两线分级分歧，取 P1） |
| 论文 / 审查包 | P1-21 | 外部审查包 **42/0/0、381 passed、88/0/0 三个数字无出处** |
| | P1-22 | 外部审查包验收矩阵**缺 Sprint 5 行**，却称"每个数字都是服务器上跑出来的"；`:177` 与 §5.4 自陈冲突 |
| | P1-23 | "13 个验收脚本" / "13 个 Sprint" / "12 个开发阶段" 口径混用 |
| | P1-24 | `DECISIONS.md` ⏳12 / ⏳13 服务器验收状态三处不一致 |
| | P1-25 | 外部审查包"14 项待确认（⏳）"实为 **13 条**（⏳ 编号 1~14 缺 9） |
| | ~~P1-26~~ | ~~`REPORT_DRAFT.md:443` 把合成数据标为"真实数据"~~ → **已闭环（见 §9.6）** |
| | ~~P1-27~~ | ~~`REPORT_DRAFT.md:1305` §6.6 标题"真实数据缺陷"歧义~~ → **已闭环（见 §9.6）** |
| 性能 | P1-28 | `/ask` 实际 **n=2 且无预热**，文档写"3 次（含 1 次预热）" |
| | P1-29 | 批量"9 个任务合计"**含 1 次重试 attempt**（`ads_layers` try 2），源码/文档均未说明 |
| | P1-30 | 关联 11.7 ms 采集时**维表为空、JOIN 未真正发生**（缺陷已修，同口径重跑约 7.4 ms） |
| | P1-31 | 批量耗时**来源说明过期**（文档称"日志"，代码读"元数据库"，日志目录已不存在） |
| | P1-32 | 性能结论**缺"不构成生产/高并发证明"限定语**（论文 §6.5 与答辩第 10 页均无） |
| Agent 主张边界 | P1-33 | "Agent 无法读取白名单外的表"主张**大于证据范围**；`verify-sprint-10.sh` 两路径一致性断言对逗号连接会一起"绿" |
| | **P1-34** | **"实时链路行数与源端 1:1"忽略"重放 29 次 + `UNIQUE KEY` 去重收敛"口径**：Flink 侧累计产出 580000 / 174000 / 156774 / 7366 条，Doris 表行数 20000 / 6000 / 5406 / 254（S8 §2.2/§2.5/§2.6）→ **行数 = 去重后行数 ≠ 处理量**，凡"处理量/吞吐/1:1"表述必须带口径 |
| 材料引用一致性 | **P1-35** | **并发产出的答辩/续作材料引用了审计结论的旧状态**：`DEFENSE_QA.md`（15:09:16 新增）Q4 仍写"本次审计未能定案/未定性…两个数都保留"，并指向 `EVIDENCE_MATRIX.md §2.1（UNRESOLVED）` —— 而 L7 已于 15:08:54 判定 **B**、本文件 §2.1 已改为"已定案"。**答辩口径与事实源相反**，必须按 §2.1 改写；`DEFENSE_DEMO.md` / `RESUME_PROJECT.md` / `QA_PREP.md` / `REPORT_OUTLINE.md` / `外部审查包.md` 需同口径复查 |

> **计数说明**：审计线原始产出 34 条 P1，其中 **P1-13（`AGENTS.md` §15.9）、P1-26 与 P1-27
> （`REPORT_DRAFT.md` 的两处"真实数据"）已在审计窗口内闭环**（见 §9.6），
> 另新增 2 条审计期发现（P1-34、P1-35）→ **净计 32 条**。

### 9.3 P2（**42** 条）

> 明细见 `FINAL_AUDIT_REPORT.md` §5（同编号）。
> **P2-9（`AGENTS.md` §15.10 未注明"18 张表 / 60 与 23 张表 / 70 是两次运行"）已在审计窗口内闭环**（见 §9.6）。

`P2-1`~`P2-4` 对账脚本与注释；`P2-5`~`P2-8` Iceberg 代码/文档（**P2-9 已闭环**）；`P2-10`~`P2-15` Agent；
`P2-16`~`P2-22` 性能；`P2-23`~`P2-36` 文档状态与残留；`P2-37`~`P2-42` 绝对化表述与措辞风险；
**`P2-43` 审计期新增**：`event_id` 非全局唯一 + 实测两段编号（`40001–49999` / `410000–420000`）。

### 9.4 UNVERIFIABLE（**26** 条）

> 明细见 `FINAL_AUDIT_REPORT.md` §9（同编号）。分类计数：**Iceberg 维度 11（V1–V11）**、
> **性能 7**、**Agent 2**、**数据来源与历史状态 3**（40000 的成因、离线 DWD 行数、两段编号的成因）、
> **线上状态与验收输出 2**（站点形态/`agent_ro` 授权、`verify-sprint-*.sh` 实际输出）、
> **监控告警 1**（Prometheus 告警规则/SLO 未审计）。
> **这些结论不是"未发现差异"，而是"没有测量过"或"当前环境无法证明"。**
>
> **口径提示**：`ods_behavior_event` 归档行数**已不再是** UNVERIFIABLE（三源实测 = 20000，见 §2）；
> 仍不可验证的是**离线 DWD 明细表行数**（未跑 Spark，内存纪律）。

### 9.5 本次**保留不改**的反向结论（防止后续"修文档时顺手改坏"）

| 项 | 出处 | 处置 |
| --- | --- | --- |
| `DECISIONS.md:130/190/210` 的"必然/保证" | S2 A-08 | **不改动**（确定性推理 / 版本区间不相容 / Airflow `trigger_rule=all_done` 框架契约语义） |
| `click_rate` anomaly（1 个实时侧窗口） | S1 §5.4、S7、`metrics.md` 第 3.3 节、`SPRINT_5.md:309-312`、`AGENTS.md:877-880` | **必须保留**，状态改 ✅ 时**不得**删除该未归零项（`audit-brief.md` §1.5/§1.6） |
| `PERFORMANCE.md` 与 `SPRINT_12.md` 中"维表空表：只记录，未修" | S6 §P1 | **不改历史观察值**（采集当时确为 0 行），只在各节**追加**后续状态（2026-09-27 提交 `b195d19` 已修并服务器验证） |
| 60/60 与 70/70 两个数字 | S3 §1.4、S6 | **两个都保留**、不合并、不取平均、不互相替代 |
| 20000 与 40000 | S8 §2.2–§2.17、§4、`DATA_AND_LIMITATIONS.md:294` | **两个都保留**：20000 = 基准值（五条独立证据）；40000 = **判为错误的当时观测值**（须保留 + 标注 + 补口径更正）。**不得**抹掉 40000，也**不得**把它当作当前值 |
| `REPORT_DRAFT.md:1359`、`DATA_AND_LIMITATIONS.md:14/240`、`PERFORMANCE.md:14-21`、`DEFENSE_SCRIPT.md:290` | S6 §绝对化清单 | **合格样板，不要改** |

### 9.6 审计窗口内**已闭环**的项（不计入 §9.1–§9.4 的计数）

| 编号 | 原问题 | 闭环证据（只读复核） |
| --- | --- | --- |
| **P1-13（原）** | `AGENTS.md` §15.9 写"Sprint 5 进行中"+"禁止提前实现 Sprint 8 及以后"+"流量域 DWD/DWS/ADS 尚未建模"，与同文件 §15 状态表（全 ✅）**自相矛盾** | `AGENTS.md` **2026-09-27 15:04:46** 更新（1079 → 1093 行）：`:855` 改为"Sprint 0 ~ 12 已全部稳定并验收通过"；`:864-869` 原禁令降为 **〔历史条款，已失效〕**（**保留沿革、未删除**，且注明"不得再据此认定后续 Sprint 的工作违规"）；`:871-876` 流量域改为"**已由 Sprint 5 建成并验收**"；`:877-880` **显式保留**"仍未归零的一项：实时侧 1 个窗口的 `click_rate` 自相矛盾"；Sprint 13 行改为"🔄 进行中（Prove + Audit + Close）"。→ 本次复核确认**三条子问题全部关闭**，且**没有**删掉 unresolved 项 |
| （附带） | 上述更新**新引入**一处悬空引用 | `AGENTS.md:858` 指向 `docs/sprint/SPRINT_13.md`，而该文件**不存在** → 计为 **P1-18**（未闭环） |
| **P1-26（原）** | `REPORT_DRAFT.md:443`（审计时行号）"各层表与行数（**真实数据**…）"与本文摘要 `:18`"全部为**合成数据**"直接矛盾 | `REPORT_DRAFT.md` **15:10:26** 版本（现 `:457`）已改为"（**程序生成的合成数据集，种子 `GEN_RANDOM_SEED=20260926`**；下表是**该合成数据集上的实测值**…**不代表真实业务分布**）" → **闭环**，且比审计建议更完整 |
| **P1-27（原）** | `REPORT_DRAFT.md:1305` §6.6 标题"采集过程中发现的一处**真实数据**缺陷"歧义 | 同版本（现 `:1367`）已改为"## 6.6 采集过程中发现的一处**真实缺陷**：派生表为空使 JOIN 静默返回 0 行" → **闭环** |
| **P2-9（原）** | `AGENTS.md` §15.10 的"18 张表 / `60/60`"与 §15 状态行的"23 张表 / `70/70`"未注明是两次范围不同的运行 | `AGENTS.md` 更新后 §15.10 已加**适用范围直注**（"阶段 4 的首次迁移…扩到 23 张表并复跑…**两次的运行范围不同，不是'同一判据的两次结果'，也不能互相替代**；`70/70` 只有汇总记录、无逐项细目"）+ 结论范围限定；§15.12 同步 → **闭环** |
| （附带） | `AGENTS.md` §15.8/§15.9 的"零丢失"与"表行数"口径（W-12 / P1-34 同向） | 已补"判据是**归档作业内的自检**、单次验收窗口等式、Kafka **副本数为 1**"与"行数 = distinct `event_id` 基数 ≠ 链路处理量（580000 = 29 × 20000）" → **表述达标**（W-12 仍保留作写作规范，供其它文档对齐） |

> **⚠️ 闭环的收尾提醒**：`docs/thesis/毕业论文_*.docx`（144 KB，**13:10:40**）**早于**上述 REPORT_DRAFT 修改，
> 因此 **P1-26/P1-27 的修复必须连同 `.docx` 重新导出**（`md_to_thesis_docx.py` 在仓库内）。
>
> **冻结时点**：本文件与 `FINAL_AUDIT_REPORT.md` 的判定**冻结在 2026-09-27 15:12 CST**；
> 此后仍有并发修改，判断某条是否仍成立请以文件**当前内容**为准。**未闭环的相邻项**：
> `外部审查包.md:124/:131/:177` 与 §5.4（P1-21/P1-22/P2-40）、`DEFENSE_QA.md` Q4（P1-35）。
