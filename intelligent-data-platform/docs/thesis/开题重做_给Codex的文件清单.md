# 给 Codex 的文件清单与目录说明

> 用途：把这份文件连同 `开题重做_Codex提示词.md` 一起交给 Codex。
> 目标：让 Codex 知道**该读什么、不该读什么**，以及项目目录长什么样。
> 一句话原则：**只给它"设计资料"，不给它"结果资料"。**

---

## 1. 怎么交接（三种方式，选一种）

| 方式 | 做法 | 适用 |
| --- | --- | --- |
| **A. 打开项目目录（推荐）** | 让 Codex 以 `C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform` 作为工作目录，然后把第 6 节的"清单段"和提示词一起粘进去 | Codex 能自己读写文件、跑脚本 |
| B. 逐文件添加 | 把第 2 节的 4 个文件 + 第 3 节的 3–4 个参考文件逐个拖进对话 | 只能聊天、不能跑脚本的版本 |
| C. 只给路径 | 粘贴第 6 节清单段（全是绝对路径），让 Codex 自己按路径读 | 已配置本地文件访问权限时 |

**注意**：学校模板和参考稿在 **微信文件夹**里，不在仓库内。Codex 若无法访问，需要你先把它复制到项目里（建议复制到 `docs/thesis/校内模板/`，该目录已存在）。

---

## 2. 必须给（4 个，缺一个就可能跑偏）

| # | 文件（绝对路径） | 是什么 | 为什么必须 |
| --- | --- | --- | --- |
| 1 | `docs/thesis/开题重做_需求说明.md` | 验收标准母版 | 所有硬约束、字数、禁词、结构都在这，**优先级高于提示词** |
| 2 | `docs/thesis/forms_content_2024.py` | 开题报告与任务书的**正文文案常量** | 任务 A 只改这个文件；改之前必须先看清 `VALUE` / `LIT` / `METHOD` / `REFS` 的现有结构与引注写法 |
| 3 | `docs/thesis/build_forms_2024.py` | 组装脚本（表格 XML 深拷贝 + 填格 + 几何修正） | 决定"文案怎么写才不会被 Word 裁掉"；里面的 `allow_row_split` / `top_align_table` / `normalize_table_geometry` 是它必须理解的约束，**但不许乱改** |
| 4 | `C:\Users\jsy28\Documents\xwechat_files\wxid_fxi9a10yivq222_3bb1\msg\file\2026-11\附件2：湖南工商大学本科生毕业论文管理规范汇总（2024年修订版）.docx` | 学校 2024 修订版规范原件（含任务书/开题报告空白表） | 表格结构、字号、页边距的唯一来源。**组装脚本直接从这份文件里深拷贝表格**，路径变了脚本就跑不起来 |

---

## 3. 建议给（参考资料，各有用处）

| # | 文件 | 给它看什么 | 提醒 |
| --- | --- | --- | --- |
| 5 | `…\msg\file\2026-11\9c6f27ad6313262a4c704e184ed04f98_7167257059442164262_m_2223030065_唐子扬_开题报告.docx` | **结构标杆**：1 绪论 / 1.1 / 1.2.1–1.2.5 / 1.3 技术概述 / 1.4 关键挑战 / 1.5 研究内容与结构，正文带 `[1]–[20]` 上标引注 | 这是另一个专业的同学写的，**只学结构，不抄内容** |
| 6 | `C:\Users\jsy28\Documents\xwechat_files\wxid_fxi9a10yivq222_3bb1\temp\RWTemp\2026-11\9e20f478899dc29eb19741386f9343c8\湖南工商大学开题答辩PPT模板(1).pptx` | 校内 PPT 模板的版式骨架（封面信息带、左上栏目、底部通栏、右上校徽） | **取骨架，不取过渡页**（5 分钟讲不完 5 张章节过渡页） |
| 7 | `docs/thesis/答辩材料/项目技术讲解_十二讲.md` | 每层技术"是什么 / 在哪一段 / 为什么这么选"的口径来源 | 文件开头已写"开题口径使用须知"，但仍要口头强调：**文中实测值不许搬进开题** |
| 8 | `README.md`（仓库根） | 技术栈与**钉死的版本号**清单、目录说明 | 只取版本号和架构图文字，**不取进度、不取数字** |
| 9 | `sql/metadata/metrics.md` | 指标名称与定义（口径字典） | 开题里最多写"将以唯一定义文件约束指标口径"，**不要展开成判据表** |
| 10 | `AGENTS.md` | 分层与模块职责（哪一层干什么） | 只取职责描述 |

---

## 4. 项目目录说明（请原样告诉 Codex）

```
intelligent-data-platform/                    项目根（下同）
├── services/
│   ├── api/          FastAPI 只读数据服务（15 个接口）
│   ├── web/          Vue 3 + ECharts 零构建看板（index.html / app.js / styles.css）
│   ├── agent/        LangGraph 问答 Agent（retrieve→plan→execute→validate→reflect→summarize）
│   └── mcp/          MCP 工具服务（把只读接口暴露给 Agent）
├── data-generator/   数据装载入口：src/load_real_data.py（读 data/real/*.csv 入 MySQL 并发布 Kafka）
├── infrastructure/
│   ├── flink/sql/    实时链路：源表、DWD 落库、指标聚合（01/02/03/04/05）
│   ├── spark/sql/    离线链路：DWD 构建、DWS/ADS 聚合、对账（01…08）
│   ├── spark/jobs/   离线作业（含 reconcile_batch_realtime.py）
│   ├── quality/      数据质量校验（checks.conf + checks/*.sql）
│   ├── doris/ Kafka/ MinIO/ 各组件配置
│   └── ...
├── sql/
│   ├── mysql/        业务库建表（订单/支付/退款/用户/商品）
│   ├── hive/         湖仓分层建表（ODS/DWD/DWS/ADS/对账）
│   ├── doris/        Doris 表与 Routine Load
│   └── metadata/     metrics.md（指标字典）、kafka_topics.md（事件契约）
├── scripts/          运维与验收脚本（batch-mode.sh、run-quality-checks.sh、verify-sprint-*.sh、perf/）
├── docs/
│   ├── thesis/       ← 本次要改的都在这里
│   │   ├── 开题重做_需求说明.md
│   │   ├── 开题重做_Codex提示词.md
│   │   ├── 开题重做_给Codex的文件清单.md   （本文件）
│   │   ├── forms_content_2024.py           （任务 A 改这个）
│   │   ├── build_forms_2024.py             （组装脚本，别乱改）
│   │   ├── 校内模板/                        （学校模板副本，若 Codex 读不到微信目录就复制到这）
│   │   ├── 答辩材料/                        （PPT、准备包、十二讲、演示截图）
│   │   └── 毕业论文材料_蒋树阳/             （交付件输出目录）
│   └── ...
├── airflow/dags/     调度 DAG（离线全链路）
└── tests/            单元测试与验收测试
```

**输出位置**（必须写到这两个路径，不要另存新名字）：
- `docs/thesis/毕业论文材料_蒋树阳/02_附件1_任务书.docx`
- `docs/thesis/毕业论文材料_蒋树阳/03_附件2_开题报告.docx`

**PPT 输出**：`docs/thesis/答辩材料/开题答辩_蒋树阳.pptx`（16:9，9–11 页）

---

## 5. ⛔ 绝对不要给的文件（给了就会把"已完成"带回来）

| 不要给 | 原因 |
| --- | --- |
| `docs/thesis/REPORT_DRAFT.md`（论文正文） | 全篇是"已建成、已测出"的完成时叙述，还有大量实测数字 |
| `docs/thesis/REAL_DATA_FACTS.md` | 唯一的权威数字表（订单量、GMV、窗口数…），最容易被抓去填进开题 |
| `docs/thesis/答辩材料/FINAL_ACCEPTANCE_REPORT*.md`、`FINAL_AUDIT_REPORT.md` | 验收/审计报告，通篇结果 |
| `docs/sprint/*.md`、`DEVELOPMENT_LOG.md`、`DECISIONS.md` | 开发过程记录，都是"做完了什么" |
| `docs/thesis/毕业论文材料_蒋树阳/01_毕业论文正文.docx` | 同上，且体量太大 |
| 旧的 `开题答辩_蒋树阳.pptx` | 上一版的口径是错的（结果卡片、"已完成的验证"），只可作为**反面样例**口头提及，不要让它当参考 |
| `.tmp/realdata/**`（1 GB CSV）、`.venv*`、`node_modules/`、截图目录 | 与文档无关，白占上下文 |

**口头补一句**：如果 Codex 问"能不能参考一下论文正文"，回答 **不能**，只能参考第 3 节的资料。

---

## 6. 现成清单段（连同提示词一起粘贴）

```
【项目根】C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform
【学校规范原件】C:\Users\jsy28\Documents\xwechat_files\wxid_fxi9a10yivq222_3bb1\msg\file\2026-11\附件2：湖南工商大学本科生毕业论文管理规范汇总（2024年修订版）.docx
【必须读的 4 个文件】
  1) docs\thesis\开题重做_需求说明.md
  2) docs\thesis\forms_content_2024.py
  3) docs\thesis\build_forms_2024.py
  4) 上面那份学校规范原件
【可参考，但只学结构/只取事实】
  5) …\9c6f27ad6313262a4c704e184ed04f98_…_唐子扬_开题报告.docx（结构标杆）
  6) …\湖南工商大学开题答辩PPT模板(1).pptx（版式骨架，取骨架不取过渡页）
  7) docs\thesis\答辩材料\项目技术讲解_十二讲.md（技术口径，但不许搬其中实测数字）
  8) README.md（版本号与架构）
  9) sql\metadata\metrics.md（指标名称与定义）
【禁止读取】docs\thesis\REPORT_DRAFT.md、docs\thesis\REAL_DATA_FACTS.md、
  docs\thesis\答辩材料\FINAL_ACCEPTANCE_REPORT*.md、docs\sprint\*、
  DEVELOPMENT_LOG.md、01_毕业论文正文.docx、旧版开题答辩_蒋树阳.pptx
【输出】docs\thesis\毕业论文材料_蒋树阳\02_附件1_任务书.docx
        docs\thesis\毕业论文材料_蒋树阳\03_附件2_开题报告.docx
        docs\thesis\答辩材料\开题答辩_蒋树阳.pptx
```

---

## 7. 怎么快速确认它真的读了

开工前让它先回答三个问题（答不上来说明没读文件）：

1. `forms_content_2024.py` 里正文引注用什么标记？组装脚本把它转成什么？（答案：`[[n]]` → 上标 `[n]`）
2. 开题报告表格是几行几列？哪几行是要填的三格正文？（答案：8 行 × 9 列；`选  题  价  值` / `文  献  综  述` / `研究方法与思路`）
3. 这次材料的时态要求是什么？举三个禁词。（答案：拟/将/计划；已完成、已实现、已跑通）

答对再让它动手，能省掉一轮返工。
