# Sprint 13 任务书与结算：毕业论文 + 答辩（**Prove + Audit + Close**）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 前置：Sprint 0 ~ 12 **全部已完成并验收通过**（逐项见
> [`AGENTS.md`](../../AGENTS.md) §15 状态表与 §15.1~15.12）
> 状态：🔄 **进行中** —— 材料已成稿（报告正文 / 大纲 / PPT 逐页大纲 / 讲稿 /
> 问答准备 / 外部审查包 / 证据矩阵 / 终审报告 / Word 导出），终稿待答辩前定稿
> 本文件的性质：**本 Sprint 的唯一文档锚点**（Sprint 13 没有"新功能设计"，
> 因此本文件替代通常的"设计文档 + 实施记录"两份）

---

## 1. 本 Sprint 的性质（先读这一节）

**不是继续 Build，而是 Prove + Audit + Close。**

```text
成功标准不是"增加了多少代码"，
而是：代码、数据、验证、文档、论文、答辩材料之间
      形成一个可信、可追溯、可复现的**证据闭环**。
```

**绝对禁止（本 Sprint 期间全程有效）**：

```text
1. 新增任何技术组件
   （HDFS / CDC / Milvus / ES / Chroma / pgvector / Spring Boot / K8s /
     新消息队列 / 新 OLAP）
   —— 若某问题必须靠新组件才能解决：**不实施**，记为 Future Work / Out of Scope
2. 修改历史验证结果数字而不保留原因
3. 编造线上结果、服务器执行结果、数据库数据
4. 把"无法验证"写成"验证通过"；把"当前代码支持"写成"已经在线验证"；
   把"合成数据集上成立"写成"生产环境成立"
5. 删除 unresolved issue 让项目看起来更干净
6. 隐藏 `click_rate` anomaly；扩大 Iceberg migration 的结论范围
7. 大规模重做已完成的 Sprint；为验证一个数字而重跑整条流水线
8. 为了让测试通过而修改历史结果或弱化断言
```

> 与 §1 的对应关系：**允许的修复范围 = 措辞、状态、口径、证据边界**；
> **不允许的 = 数字、断言强度、未决项的存在性**。

---

## 2. 交付物清单（`docs/thesis/`）

| 文件 | 内容 | 状态 |
| --- | --- | --- |
| [`REPORT_DRAFT.md`](../thesis/REPORT_DRAFT.md) | 论文正文草稿（摘要 / 正文 / 不足） | 已成稿 |
| [`REPORT_OUTLINE.md`](../thesis/REPORT_OUTLINE.md) | 章节大纲 | 已成稿 |
| [`DEFENSE_SLIDES.md`](../thesis/DEFENSE_SLIDES.md) | 答辩 PPT 逐页大纲 | 已成稿 |
| [`DEFENSE_SCRIPT.md`](../thesis/DEFENSE_SCRIPT.md) | 答辩讲稿（带时间轴） | 已成稿 |
| [`QA_PREP.md`](../thesis/QA_PREP.md) | 答辩问答准备 | 已成稿 |
| [`DATA_AND_LIMITATIONS.md`](../thesis/DATA_AND_LIMITATIONS.md) | 数据来源与已知限制 | 已成稿 |
| [`外部审查包.md`](../thesis/外部审查包.md) | 外部审查包（主张 / 证据 / 边界对照） | 已成稿 |
| **[`EVIDENCE_MATRIX.md`](../thesis/EVIDENCE_MATRIX.md)** | **证据矩阵：主张 → 证据（文件:行）→ 结论分类 → 边界** | 已成稿 |
| **[`FINAL_AUDIT_REPORT.md`](../thesis/FINAL_AUDIT_REPORT.md)** | **终审报告：P0/P1/P2 + UNVERIFIABLE + 修复建议 + 论文章法风险** | 已成稿 |
| `毕业论文_*.docx` | Word 终稿，由 [`md_to_thesis_docx.py`](../thesis/md_to_thesis_docx.py) 导出（脚本自带断言） | 已导出 |
| **本文件** | Sprint 13 的任务书与结算 | 本文件 |

---

## 3. 审计线清单（六条子线 + 一条主控实测）

> 全部**只读**：服务器侧仅 `GET` / `SELECT` / `COUNT(*)` / `SHOW` / `cat` / `ls` /
> `free` / `df`；**未**重跑流水线、**未**重启服务、**未**改配置、**未**执行 `git`。

| # | 审计线 | 独立产出 | 覆盖范围 | 状态 |
| --- | --- | --- | --- | --- |
| L1 | 批流一致性（7 问） | `.tmp/audit-reconcile.md` | 两域对账作业与判据 SQL、两条验收脚本、`Checker` 断言层、`metrics.md` 口径 | DONE |
| L2 | 文档状态一致性 + 绝对化表述 | `.tmp/audit-doc-status.md` | 项目 README 全文、仓库根 README、`AGENTS.md` §15、15 份 Sprint 文档头部、REPORT_DRAFT、外部审查包、DECISIONS | DONE |
| L3 | Iceberg 验证范围 | `.tmp/audit-iceberg.md` | 迁移作业全文、`spark-defaults.conf`、`SPRINT_5.md` §8.1/§9.6/§11、`_common.py` 断言层、逐表 DDL | DONE |
| L4 | Agent 安全边界 + 反思重试 + 检索表述 | `.tmp/audit-agent.md` | `sqlguard.py` 全文、对抗测试、Agent tools/config/graph、系统提示路径、检索实现与语料 | DONE |
| L5 | 性能数据边界 | `.tmp/audit-perf.md` | `measure-latency.sh`、`PERFORMANCE.md`、`SPRINT_12.md`、论文性能章、`verify-sprint-12.sh`；**含服务器只读取证** | DONE |
| L6 | 跨文档状态与数字一致性 | `.tmp/audit-doc-consistency.md` | 仓库根 README、项目 README、`AGENTS.md`、12+2 份 Sprint 文档、DECISIONS、PERFORMANCE、DEVELOPMENT_LOG、REPORT_DRAFT、DATA_AND_LIMITATIONS、外部审查包 | DONE |
| L7 | **数据差异专项（20000 vs 40000）** | `.tmp/audit-traffic-20000-40000.md` | 仓库侧 R1–R5 + 服务器侧 S1–S7（Kafka 位点/消息、Doris 行数与分布、Routine Load、MinIO 目录） | **DONE（已定案）**：**20000 是当前事实**（Kafka 累计位点 20000、归档作业日志 20000、Iceberg 三个快照 total-records 均 20000、Doris `COUNT(*)` 与 `COUNT(DISTINCT event_id)` 均 20000）；**40000 判为当时的观测误差**（唯一出处 `SPRINT_3.md:52`、无观测时间、且与同行"与 MySQL 1:1"自相矛盾 —— 流量域在 MySQL 没有源表）。**历史侧状态保留 UNRESOLVED**（topic 重建之前的真实状态不可考） |
| — | **主控实测**（不属于任何子线） | `FINAL_AUDIT_REPORT.md` §3、`EVIDENCE_MATRIX.md` §3 | 对账汇总/明细只读 `SELECT`；逗号连接线上复现三组对照 | DONE |

**证据优先级（冲突时按此裁定）**：

```text
代码  >  实际验证脚本  >  验证结果  >  Sprint 文档  >  README/论文
```

文档描述与代码冲突时，**以代码为准**，并把冲突记录下来（不得只改文档不改记录）。

---

## 4. 分级定义（P0 / P1 / P2 / UNVERIFIABLE）

| 级别 | 定义 | 例 |
| --- | --- | --- |
| **P0** | **会导致答辩核心结论错误** | 核心数字错；核心实验无法复现；代码与论文核心结论冲突；对账实际存在大量未发现 mismatch |
| **P1** | **严重影响可信度但不一定推翻项目** | 数字未解释的差异（如 20000 vs 40000）；README/AGENTS 状态冲突；论文绝对化表述；Iceberg 结论范围过大；安全测试覆盖不足 |
| **P2** | **文档 / 工程质量 / 未来优化** | 过期 roadmap、措辞、非核心代码重复、告警缺失、UI |
| **UNVERIFIABLE** | **当前环境无法证明** —— 必须写清"为什么无法证明"，**不得**折算成 P1/P2 或写"应该没问题" | 线上告警链路是否可用（本次未覆盖）；某次历史运行的逐项细目（仓库未记录） |

**结论分类（每条核心主张必须归入其一）**：

```text
SUPPORTED             证据充分，且证据边界与主张范围一致
PARTIALLY_SUPPORTED   有证据，但主张范围大于证据范围（**最常见**）
NOT_SUPPORTED         代码/验证不支持该主张
UNVERIFIABLE          当前环境无法证明
```

> 定义来源：`.tmp/audit-brief.md` §5/§6（三条审计线共用章程）。
> 定级分歧按"**就高**"处理，并在条目内注明分歧原因（不取平均）。

---

## 5. 修复纪律（PHASE F）

每次修复前必须先写清五段式：

```text
[ISSUE]       问题是什么
[ROOT CAUSE]  根因（不是"我忘了"，而是机制）
[EVIDENCE]    定位证据（文件:行 / 命令输出）
[FIX]         改什么
[VALIDATION]  怎么验证改对了
```

**禁止**"发现问题 → 直接改 → 写成已解决"。

**允许改**：措辞、状态、口径、证据边界、注释文字。
**不允许改**：验收数字、断言强度、未决项的存在性、历史观察值
（要更新历史项的**后续状态**时，只能**追加**，不改原值 —— 例如
`PERFORMANCE.md` 的维表缺陷行）。

---

## 6. 结算（截至本文件定稿）

```text
✅ 材料已成稿：docs/thesis/ 下 10 个文件 + 1 个 Word 终稿（由脚本导出）
✅ 证据矩阵与终审报告已落盘（EVIDENCE_MATRIX.md / FINAL_AUDIT_REPORT.md）
✅ Sprint 状态四方对齐：AGENTS.md §15 状态表、项目 README §16 Roadmap、
   仓库根 README、docs/sprint/SPRINT_0~12 头部 —— 均为 ✅（Sprint 13 为 🔄）
✅ 文档侧修复（PHASE F · 文档线）已完成，逐条五段式记录在回报中
⏳ 待答辩前定稿：Word 终稿重新导出与通读
⏳ 未决项（**不得隐藏**，逐条留在文档里）：
   - 实时侧 1 个窗口 click_rate 与自身计数矛盾（不阻断作业退出码；
     修复需重部署 Flink 作业并重放，待项目负责人决策）
   - dwd_traffic_behavior_detail 行数**已定性为 20000**（40000 判为当时的观测误差）；
     **历史侧（2026-09-26 13:18 topic 重建之前）状态保留 UNRESOLVED**
   - event_id 出现两代编号（40001–49999 与 410000–420000）的成因未定（UNVERIFIABLE）
   - 交易域缺"仅实时/仅离线窗口 = 0"断言（只有 ≥90% 覆盖率容差）
   - Iceberg 迁移的未验证维度 V1~V11（字符串内容 / 时间 / NULL /
     decimal 精度 / 分区 / schema 等价性 / 重复行 / 快照元数据 / 回滚 /
     编码与表格式版本 / 行级完整性）
   - Agent 逗号连接绕过（P0-1，权限边界，已线上复现）
```

---

## 8. 本次收口登记的工具坑（不属代码缺陷，但会让取证结论反掉）

```text
Kafka 4.x 的 kafka.tools.GetOffsetShell 类名已变更
  旧写法：kafka-run-class.sh kafka.tools.GetOffsetShell
          → ClassNotFoundException，且**输出为空**
  正确：  org.apache.kafka.tools.GetOffsetShell
          或直接用 bin/kafka-get-offsets.sh
后果：  "输出为空"极易被读成"位点取不到 / topic 为空"，
        从而把一个正常的 topic 判成异常。
本项目的实例：一条用于推断行为事件条数的探针就是这样半坏的 ——
        它给出的"max event_id = evt_420000"这条线索本身正确，
        但由它推断出"42 万条消息"是错的：
        evt_420000 是生成器**进程内序号**，当前 topic 的累计条数是 20000。
结论：  用"输出为空"当作证据之前，先确认那个工具真的跑起来了。
```

---

## 7. 相关文档

- 证据矩阵（**论文与答辩的唯一事实源**）：
  [`docs/thesis/EVIDENCE_MATRIX.md`](../thesis/EVIDENCE_MATRIX.md)
- 终审报告（P0/P1/P2、修复建议、论文章法风险）：
  [`docs/thesis/答辩材料/FINAL_AUDIT_REPORT.md`](../thesis/FINAL_AUDIT_REPORT.md)
- 数据来源与已知限制：
  [`docs/thesis/DATA_AND_LIMITATIONS.md`](../thesis/DATA_AND_LIMITATIONS.md)
- 项目状态基准：[`AGENTS.md`](../../AGENTS.md) §15
- 指标口径（唯一权威）：[`sql/metadata/metrics.md`](../../sql/metadata/metrics.md)
- 性能口径与边界：[`docs/PERFORMANCE.md`](../PERFORMANCE.md)
