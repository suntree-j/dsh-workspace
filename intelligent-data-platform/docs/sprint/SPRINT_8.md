# Sprint 8 — LangGraph Data Agent（图式编排）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 依据：[`docs/PROJECT_DESIGN_V1.md`](../PROJECT_DESIGN_V1.md) 第 7 章（Agent 架构）、
> 第 8 章 Roadmap 第 8 行
> 前置：Sprint 7（LLM + Tool Calling，验收 49/49）、Sprint 6（只读数据服务）
> 状态：✅ **已完成并验收通过**（`verify-sprint-8.sh` 71/0/0；详见第 9 节「实施记录」）

---

## 1. 目标

Sprint 7 交付的是一个**单轮工具调用循环**：模型自己决定要不要查口径、要不要查表结构、
要不要写 SQL，循环跑到模型不再发起工具调用为止。它能回答问题，但有三处结构性问题：

| 问题 | 具体表现 | 为什么循环解决不了 |
| --- | --- | --- |
| 没有显式的**规划**产物 | 模型在第一轮就可能直接写 SQL，口径查询变成"看心情" | 循环没有"必须先有规划"这个约束点，也没有留下可审查的规划 |
| 没有独立的**校验**步骤 | 查询成功（HTTP 200）就等于"这步过了"，没人检查结果是否为空、分母是否为 0 | 校验需要比"这一轮调了什么工具"更高的视角 |
| **重试**不是一等公民 | 靠模型自己看到错误文本后"愿意"再试一次；轮次用尽就强制收尾 | 循环里没有"失败计数 → 是否重规划"这个决策点，所以无法回答"到底重试了几次、为什么停" |

Sprint 8 要把它升级为**图式 Agent**：

```text
理解问题 → 规划 → 取数 → 校验 ─┬─（校验不通过且有重试额度）→ 反思 → 回到规划
                                └─（通过 / 额度用尽 / 超时）→ 汇总结论
```

四条硬约束不变（`AGENTS.md` §10）：

1. **仍然不持有数据库凭据** —— 取数只经 `POST http://127.0.0.1/data/api/query`；
2. **失败必须返回真实错误**，严禁编造数据；
3. 回答必须能说明来源（`tables` / `executed_sql` / 命中的口径文档）；
4. **Sprint 7 的 49 项验收不得回退**。

---

## 2. 版本查证（禁止 `latest`、禁止猜）

| 项 | 结论 | 依据 |
| --- | --- | --- |
| 包名 | `langgraph` | PyPI 官方包 |
| 目标版本 | `1.1.0` | 服务器实测 `pip index versions langgraph` 的可用版本列表含 `1.1.0`；PyPI JSON API `releases` 中亦含 `1.1.0` |
| 明确**不采用** | `1.2.x`（含 `1.2.0a6` 等预览版） | 派工明确要求钉 1.1.0；预览版不用于交付 |
| 运行解释器 | 宿主 `/opt/data-platform/.venv-agent`，Python **3.12.3** | `langgraph` 要求 `>=3.10`，满足 |

> ⚠️ **「查证过」不等于「装上了」**。本 Sprint 的版本纪律是：
> 以**服务器上 `pip install` 之后 `pip show` 实际装到的版本**为准，
> 并把它写进 [`docs/development-environment.md`](../development-environment.md) 第 3.1 节。

### 2.1 依赖影响（必须显式说明）

`langgraph` 不是零依赖包，它会拉入 `langchain-core` / `langgraph-checkpoint` /
`langgraph-prebuilt` / `langgraph-sdk` / `xxhash` 等。这些装在 **Agent 独立 venv
`.venv-agent`** 里，与数据服务的 `.venv` 物理隔离（理由见 SPRINT_7.md 第 2 节），
因此**不会影响看板接口的依赖集合**。

---

## 3. 架构

### 3.1 图（节点与边）

```text
                    ┌──────────────┐
   question ───────►│   retrieve   │  检索指标口径 / 表结构 / 分层说明（Sprint 9 的词法检索）
                    └──────┬───────┘  命中结果写入 state.docs，并随回答返回（来源可追溯）
                           ▼
                    ┌──────────────┐
                    │     plan     │  产出**显式规划**：要查哪些指标、用哪张表、SQL 草稿
                    └──────┬───────┘  规划步骤写进 state.plan（可审查，不只是模型脑子里的事）
                           ▼
                    ┌──────────────┐
                    │   execute    │  调用工具取数（唯一取数通道：sql_query → POST /query）
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   validate   │  结果自检：空结果？比率分母 0？行被截断？错误码是什么？
                    └──────┬───────┘  → state.issues（不通过的原因，逐条可读）
                           │
              ┌────────────┴─────────────┐
              │ 有 issues 且还有重试额度     │ 否则（通过 / 额度用尽 / 超时）
              ▼                          ▼
       ┌──────────────┐           ┌──────────────┐
       │   reflect    │           │  summarize   │  汇总为最终回答（必须附来源）
       └──────┬───────┘           └──────┬───────┘
              │ 把问题与真实错误交回模型   │
              └──────► plan（重规划）     ▼
                                        END
```

**为什么 `reflect` 之后回到 `plan`，而不是直接回到 `execute`：**

错误信息（"表未授权"）通常意味着**计划本身错了**（选错了表），
而不是"同一条 SQL 再跑一遍就会好"。回到 `plan` 才能真正改变做法；
回到 `execute` 只会把同一条错 SQL 重放一次 —— 那是重试，不是重新规划。

### 3.2 状态（`AgentState`）

| 字段 | 含义 |
| --- | --- |
| `question` | 用户原始问题 |
| `docs` | `retrieve` 命中的口径/元数据文档（含来源文件与行号） |
| `plan` | 显式规划文本 + 结构化步骤（每步：意图、目标表、SQL 草稿） |
| `messages` | 交给 LLM 的对话上下文 |
| `steps` | 每次工具调用的审计记录（工具名、入参、成功与否、血缘表） |
| `executed_sql` | 服务端返回的**实际执行**语句 |
| `issues` | `validate` 发现的问题（空结果、分母 0、行截断、错误） |
| `retries` / `max_retries` | 已重试次数与额度（**额度用尽必须显式记录**，不是悄悄结束） |
| `answer` / `truncated` | 最终回答与"是否因额度/超时提前收尾" |

### 3.3 终止性（必须有界）

图可能自环（`plan → execute → validate → reflect → plan`），因此必须有**三重上界**：

1. `retries` 达到 `AGENT_MAX_RETRIES`（默认 2）即不再反思，直接汇总；
2. 工具调用总轮次达到 `AGENT_MAX_TOOL_ROUNDS`（默认 6）即强制收尾；
3. 总耗时达到 `AGENT_TOTAL_TIMEOUT`（默认 120s）即强制收尾。

**收尾调用不携带 `tools`**（沿用 Sprint 7 的做法）：否则模型还能继续发起调用，图停不下来。

### 3.4 与 Sprint 7 的关系（复用而不是重写）

| Sprint 7 组件 | Sprint 8 处置 |
| --- | --- |
| `app/tools.py` 的 4 个工具与 `ToolBox` | **原样复用**（工具集合是安全边界，不因换编排而变） |
| `app/llm.py` 的 `build_client` / `create_completion` / `assistant_message` | **原样复用** |
| `sqlguard` 守卫 + 只读账号 `agent_ro` | **不动**（服务端边界） |
| `SYSTEM_PROMPT` 的三条铁律与数据地图 | 保留原文，工作流一节改写为图式步骤 |
| `Agent.ask()` 的单轮循环 | 由 `LangGraphAgent.ask()` 取代；**保留 `Agent` 类**作为对照实现与回归基线 |
| `/health` `/tools` `/prompt` `/ask` 契约 | 不变（只**增**字段，不改已有字段） |

---

## 4. 阶段划分

| 阶段 | 内容 | 完成判据 |
| --- | --- | --- |
| 1 | 写本任务书 | 本文件存在且含目标/架构/阶段/DoD/风险 |
| 2 | 装 `langgraph==1.1.0` 并记录**实际版本** | 服务器 `pip show langgraph` 输出 `Version: 1.1.0` |
| 3 | 实现图（`app/graph.py`）与节点 | `python -c "import app.graph"` 在 `.venv-agent` 内成功 |
| 4 | `main.py` 接线：`/ask` 走图；新增 `/graph` 暴露节点与边 | `GET /graph` 返回节点清单 |
| 5 | 单元测试（假 LLM 脚本驱动图，覆盖重试与终止） | `pytest tests/test_agent_graph.py -q` 全绿；`tests/test_agent.py` 仍全绿 |
| 6 | 部署（systemd 单元更新 + 依赖安装）并重启 | `systemctl is-active data-platform-agent` = active |
| 7 | `scripts/verify-sprint-8.sh` | 分步 `[OK]/[FAIL]`、统计、失败 exit 1 |
| 8 | 端到端实证 + **真实触发一次反思重试** | 见第 5 节 DoD |
| 9 | 回归 Sprint 7 | `bash scripts/verify-sprint-7.sh` 仍 **49/49** |
| 10 | 文档 | 本文件第 9 节 + `DEVELOPMENT_LOG.md` + `README.md` |

---

## 5. Definition of Done

| # | 判据 | 判定方式（必须有实证） |
| --- | --- | --- |
| 1 | 图式编排真实生效 | `GET /graph` 返回 5 个节点与有向边；`/ask` 的响应含 `graph` 轨迹 |
| 2 | 规划是显式产物 | 响应 `plan` 非空，且 `plan.steps` 含目标表 |
| 3 | 校验是独立步骤 | 响应含 `validation`（`ok` / `issues`） |
| 4 | **反思重试真实触发过至少一次** | 故意问一个**不可查的表**，响应里出现 `retries >= 1`、`validation.issues` 说明了真实错误码，且最终回答**如实说明查不到** |
| 5 | 不伪造数据 | 失败路径的 `answer` 不含任何编造的数值；`executed_sql` 只含真实执行过的语句 |
| 6 | 来源可追溯 | 响应含 `tables` / `executed_sql` / `docs`（命中的口径来源） |
| 7 | 端到端 ≥3 问 | 真实 LLM 提问，回答正确且附证据 |
| 8 | 安全边界未动 | 四类攻击（DELETE / 未授权表 / 多语句 / 注释）仍 400 且 `error.code` 正确 |
| 9 | 回归 | `verify-sprint-7.sh` 49/49、`pytest` 单元测试全绿 |
| 10 | 版本可复现 | `development-environment.md` 记录**实际装到**的 `langgraph` 版本 |

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| `langgraph` 拉入 langchain 生态依赖，撑大 Agent venv | 磁盘/安装时间 | 装在独立 `.venv-agent`；安装后记录 `pip freeze` 行数对比 |
| 图自环导致**停不下来** | 单次提问烧掉大量 token，甚至超时 | 三重上界（重试/轮次/超时）全部落地；收尾调用不带 tools；单元测试专门验证终止 |
| 换编排后**恰好**改了提示词措辞 | Sprint 7 提示词断言失效（49 项回退） | 复用 `SYSTEM_PROMPT`，只改「工作流」一节；`verify-sprint-7.sh` 的 5 条提示词断言逐条保留 |
| 节点里异常被吞掉 | 表现为"回答得很自信但没查数" | 节点异常必须写进 `state.issues` 与 `steps`，最终回答如实说明（禁止静默 `pass`） |
| 与 Sprint 9 同时改 `services/agent/` | 互相覆盖 | **同一个实施者、同一次交付**（派工已合并），不存在并发 |
| 服务器上 `sync-subset.ps1` 的批处理守卫误判 | 无法同步 | 守卫用 `pgrep -f 'run-batch-pipeline\|batch-mode'`；若被自身 ssh 命令行误命中则加 `-Force` 并**先确认服务器确实没有批处理** |

---

## 7. 配置项（新增）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_MAX_RETRIES` | `2` | `reflect` 最多触发几次（超过即汇总并标记） |
| `AGENT_GRAPH_ENABLED` | `true` | 置 `false` 可回退到 Sprint 7 的单轮循环（**回归与排障用**，不是长期双实现） |

---

## 8. 边界（本 Sprint 明确不做）

- **不引入 checkpoint / 持久化**：单次提问即一次图执行，会话记忆属后续 Sprint；
- **不做多轮对话**：前端仍不保存历史；
- **不接 MCP**（Sprint 10）：工具仍直接经 HTTP 调只读服务；
- **不引入向量检索**（Sprint 9 亦为词法检索，见 [SPRINT_9.md](SPRINT_9.md)）。

---

## 9. 实施记录

### 9.1 实际装到的版本（"查证过" ≠ "装上了"）

```text
/opt/data-platform/.venv-agent/bin/pip show langgraph
    Name:    langgraph
    Version: 1.1.0            ← 与 requirements.txt 里钉的 ==1.1.0 一致
    Requires: langchain-core, langgraph-checkpoint, langgraph-prebuilt,
              langgraph-sdk, pydantic, xxhash
```

- 装在 **Agent 独立 venv `.venv-agent`**，数据服务的 `.venv` 一行未动
  （实测 `.venv` 里 `langgraph` 仍不存在 —— 这是刻意的隔离，不是漏装）；
- Agent venv 包总数从 5 个变成 **53 个**（含随后装的 `pytest 9.1.1`）；
- **顺带发现并修复**：图单元测试需要 `import langgraph`，而 pytest 原本只在数据服务的
  `.venv` 里，于是验收脚本用 `.venv` 跑图测试会得到 22 条
  "缺少 langgraph 依赖" —— 看起来像"图没实现"。现在测试**统一用
  `.venv-agent/bin/python`**（运行该服务的那个解释器），并把解释器路径打进验收输出。

### 9.2 落地的文件

| 文件 | 变更 |
| --- | --- |
| `services/agent/app/graph.py` | **新增**：图、6 个节点、路由、`AnswerBundle` |
| `services/agent/app/main.py` | `/ask` 走图；新增 `/graph`、`/retrieval`、`/api/retrieve`；`/health` 报告编排与检索层 |
| `services/agent/app/tools.py` | 新增 `retrieve_docs`（检索）与 `propose_sql`（规划协议）两个工具声明 |
| `services/agent/app/config.py` | 新增 `AGENT_MAX_RETRIES` / `AGENT_GRAPH_ENABLED` / `AGENT_RETRIEVAL_*` 与 `repo_root` |
| `services/agent/requirements.txt` | 钉 `langgraph==1.1.0`（附"为什么是此刻"的说明） |
| `deploy/systemd/data-platform-agent.service` | 描述与文档链接更新；补充"Sprint 9 读语料不需要数据库权限"的边界说明 |
| `scripts/deploy-agent.sh` | 安装后**打印实际版本**；自检报告编排与检索层状态；修掉一处 `%s` 未被替换的日志 |
| `scripts/lib/verify-common.sh` | **新增**：验收脚本共用断言工具（含"JSON 走 stdin"与"非空守卫"） |
| `scripts/verify-sprint-8.sh` | **新增**：8 步验收 |
| `tests/test_agent_graph.py` | **新增**：27 条单元测试（控制流 + 两条协议踩坑的回归锁） |
| `tests/test_agent.py` | 工具集合断言更新为 6 个，并新增"取数通道必须唯一" |
| `tests/smoke/test_agent_api.py` | 同上（这条不改会让 `verify-sprint-7.sh` 从 49 掉到 48） |

### 9.3 实际落地的图

```text
retrieve → plan ─┬─(规划成功)→ execute → validate ─┬─(有阻塞性问题且还有额度)→ reflect → plan
                 └─(规划失败)→ summarize            └─(通过/额度用尽/超时)→ summarize → END
```

节点/边/上界都经 `GET /graph` 对外可读（实测返回 6 节点、8 条边、三重上界）。

### 9.4 验收结果

```text
✅ bash scripts/verify-sprint-8.sh    通过 71   失败 0   跳过 0
✅ bash scripts/verify-sprint-7.sh    通过 49   失败 0   跳过 0   ← 回归未回退
✅ bash scripts/health-check.sh       11/11 [OK]，退出码 0（实时链路未被本次改动影响）
```

> 过程记录：第一次跑 Sprint 8 验收是 **70/0/1**，唯一的跳过项是 `/query` 冒烟测试 ——
> 当时图测试已改用 `.venv-agent/bin/python`，而那个 venv 里还没有 pytest，
> 脚本按约定跳过而不是误报失败。随后在 `.venv-agent` 里装上 `pytest 9.1.1`
> （**运行该服务的解释器必须能跑它自己的测试**），冒烟测试得以在同一个 venv 内运行，
> **跳过项归零**。
>
> 另外第一次的 Sprint 8 验收还暴露了 3 条 FAIL，全部是**测试自身的问题**而非实现缺陷：
> ① 测试问法没能制造出"必须失败"的场景（问"某张表有没有"会走被授权的
> `information_schema`，全程无失败）；② 验收脚本用数据服务的 `.venv` 跑图测试；
> ③ 真实失败原因的匹配串太窄。三条都已修正并记录在 9.6 节 ——
> **断言/问法写错，看起来像产品缺陷**，这是本项目反复出现的一类问题。

### 9.5 端到端实测（5 问，全部真实调用 LLM）

| # | 问 | 图路径 | retries | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 最近一周每天的 GMV 是多少？ | retrieve→plan→execute→validate→**reflect**→plan→execute→validate→summarize | **1** | 7 天明细，合计 **4,222,722.99**，与 Sprint 7 基线一致 |
| 2 | 复购率怎么算？口径文档里有定义吗？ | 一轮通过 | 0 | **如实回答"口径文档里没有复购率的定义"**，并给出核查过程与自拟口径的边界说明 |
| 3 | 最近一周卖了多少钱？ | 一轮通过 | 0 | 同时给 GMV 4,222,722.99 与支付金额 3,301,340.39，并解释口径差异 |
| 4 | dwd_user_profile 有哪些字段？ | 一轮通过 | 0 | 表不存在，改给 `dim_user` 的真实字段清单，并说明依据 |
| 5 | 用 ads_traffic_1d 查最近 7 天 PV/UV | retrieve→plan→execute→validate→**reflect**→plan→execute→validate→**reflect**→plan→execute→validate→summarize | **2** | **两次 `TABLE_NOT_ALLOWED`**，如实回答"给不出来"并列出被拒的表与错误原文 |

**问 1 与问 5 就是"反思重试真实触发"的证据**，且两次触发的原因都是**真实的执行失败**
（问 1 是 `Unknown column 'window_start'`，问 5 是守卫拒绝未授权表），
不是模型自己说"我不查了"。

### 9.6 踩坑记录（4 条，全部由端到端实测暴露）

#### 9.6.1 `propose_sql` 的 tool_call 没有被回应 → 整个请求被拒（P0，功能完全不可用）

**现象**：功能看起来全做对了（规划产出、SQL 真实执行），但回答正文是
"汇总阶段调用模型失败：BadRequestError ... An assistant message with 'tool_calls'
must be followed by tool messages responding to each 'tool_call_id'"。

**原因**：规划阶段用了一次工具调用（`propose_sql`）作为"计划的数据格式"，
但服务端把它当成普通的工具结果消费掉了，**没有补一条 `role=tool` 的回应**。
DeepSeek（与 OpenAI 同协议）要求带 `tool_calls` 的 assistant 消息后面必须跟齐
每个 `tool_call_id` 的 tool 消息，否则**下一个请求**整体 400。

**处理**：规划节点为每个 `propose_sql` 调用补一条"计划已受理"的 tool 回应。
**为什么不是简单删掉工具、改用纯文本 JSON**：工具调用给出的是**结构化参数**
（目标表 + SQL + 理由），不依赖解析散文；那才是本 Sprint 要的"规划是数据不是散文"。

#### 9.6.2 规划器复用全局消息流 → 重规划变成重放

**现象**：问 1 第一次失败后，模型把**同一条错 SQL** 又提了一遍
（`ads_batch_trade_1m` 没有 `window_start` 列，被查了两次）。

**原因**：规划器接着上一轮的消息继续生成，上下文里既有上一版计划、
又有上一轮的查询结果 —— 模型看到"已经有数据了"，没有动机改变做法；
而且它并不知道自己上一版写了什么具体的 SQL。

**处理**：规划器每轮只用**干净的上下文**：系统提示词 + 问题 + 检索结果 +
**上一版计划原文** + **真实失败原文**。实测第二版计划明确写出了
"上一版失败原因读明白了：…Unknown column 'window_start'…属于猜测，踩坑了"，
并改成"先探测天表真实列名"——这才是重新规划。

#### 9.6.3 汇总上下文拼历史 tool 消息 → 与协议冲突且稀释注意力

**处理**：汇总上下文改为"系统提示词 + 问题 + 检索结果 + 规划 + **每一次真实执行的结果/错误**"，
不含任何 `assistant(tool_calls)` / `tool` 历史。并新增单元测试逐条检查
**每一次发出去的请求**里，每个 `tool_calls` 都有对应回应。
另外把判据从"有没有查过"改成"有没有**成功**的查询"：一次失败查询也算"查过"，
会让模型拿到原始错误却没有明确指示，容易给出含糊表述。

#### 9.6.4 验收脚本选错了测试解释器 → 22 条图测试集体失败

见 9.1 末尾。**通则**：
"装上了"与"能被用上"是两件事；验证一个服务的依赖，必须用**运行它的那个解释器**。

### 9.7 一处顺带发现，留给主控/后续 Sprint 处理

| 发现 | 位置 | 影响 | 建议 |
| --- | --- | --- | --- |
| `sqlguard.BUSINESS_TABLES` 是 **16 张**，但 `AGENTS.md` §15.6 与 SPRINT_7.md 写的是"19 张表" | `services/api/app/sqlguard.py` | 文档与代码不一致；会让人误以为白名单少了 3 张 | 主控核对后统一（本次未改，属他人文件与文档） |
| Doris `ecommerce` 库里有一张 `test_connection` 表，不在 `sql/` 任何 DDL 里 | Doris | 违反 AGENTS.md §5.3「禁止手工改容器内数据库而不落盘」 | 确认来源后删除或补 DDL |
| 数据服务把 Doris 的 SQL 错误（如 `Unknown column`）也包成 **503 DORIS_UNAVAILABLE** | `services/api/app/main.py` 的 `_handle_doris_unavailable` | Agent 侧会把"SQL 写错"误读成"数据仓库暂时不可用"，可能诱发不必要的重试 | 区分"连接不可用(503)"与"语句错误(400)"；属数据服务侧改动，本次未动 |
| `.env` 权限被改为 `600`，导致 `dpagent` 读不到配置、Agent 起不来 | `scripts/deploy-monitoring.sh:133` | 该脚本会把 `.env` 收紧到 600，而 `Group=dpagent` 的服务需要**组读**（640） | 该脚本应改成 `chmod 640` 并 `chown root:dpapi`（与 `install-web.sh` 一致）。本次已把 `.env` 恢复为 `640 root:dpapi` 并重启服务，但**未改他人脚本** |

### 9.8 本 Sprint 明确没做的事（边界）

- 未引入 checkpoint / 会话记忆（单次提问即一次图执行）；
- 未接 MCP（Sprint 10）；
- 未改 `sqlguard.py` 与 `services/api`（权限边界保持原样，避免与其他 Sprint 抢文件）。

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| — | V1.0 | 建立 Sprint 8 任务书（实现前先行） |
| 2026-09-27 | V1.1 | 回填实施记录：langgraph 实际 1.1.0；验收 **71/0/0**；5 问端到端；4 条踩坑；4 项顺带发现 |
