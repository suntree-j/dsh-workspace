"""图式 Agent（Sprint 8）：规划 → 取数 → 校验 → 反思重试 → 汇总结论。

## 为什么要把单轮循环改成图（Sprint 7 的三个结构性问题）

Sprint 7 的 `Agent` 是一个工具调用循环：模型自己决定查不查口径、写不写 SQL，
循环到它不再发起工具调用为止。它能回答，但有三个问题**循环本身解决不了**：

| 问题 | 循环为什么解决不了 | 图怎么解决 |
| --- | --- | --- |
| 没有显式**规划** | 循环里没有"必须先有规划"这个约束点，也没留下可审查的规划产物 | `plan` 节点产出结构化步骤（含目标表与 SQL），写进响应 |
| 没有独立**校验** | "这一轮工具没报错"就是全部结论，没人看结果是否为空、分母是否为 0 | `validate` 节点独立检查，产出 `issues` 列表 |
| **重试**不是一等公民 | 靠模型自己"愿意"再试一次；轮次用尽就强制收尾，说不清重试了几次 | `reflect` 节点 + `retries` 计数 + 显式额度，全部可见 |

## 图长什么样

```text
retrieve ─► plan ─► execute ─► validate ─┬─(有 issues 且还有额度)─► reflect ─┐
                                          │                                  │
                                          └─(通过/额度用尽/超时)─► summarize ◄┘
                                                                     │
                                                                    END
```

**为什么 `reflect` 之后回到 `plan` 而不是 `execute`：**
错误信息（"表未授权"）通常说明**计划本身错了**（选错了表）。
回到 `execute` 只会把同一条错 SQL 再跑一遍 —— 那是重放，不是重新规划。

## 四条硬约束在代码里的落点（`AGENTS.md` §10）

1. **不持有数据库凭据** → 取数只经 `ToolBox`（HTTP 调只读服务），本模块不 import 任何数据库驱动；
2. **失败必须返回真实错误** → 工具错误原样进 `steps` 与 `issues`，`summarize` 被明确要求
   "查不到就说查不到"；`_execute` 里**不存在**任何"用默认值兜底"的分支；
3. **来源可追溯** → 响应含 `docs`（命中的口径/表结构文档，带来源文件与行号）、
   `tables`、`executed_sql`；
4. **终止性** → 三重上界：`max_retries` / `max_tool_rounds` / `total_timeout`；
   收尾调用**不携带 tools**（否则模型还能继续发起调用，图停不下来）。

## 为什么把"规划"做成一次强制工具调用

让模型自由输出一段文字当计划，就得靠正则去猜它的结构（"它在第 3 行说要查哪张表？"）。
改用一次 `propose_sql` 工具调用，LangGraph 拿到的是**结构化参数**（目标表 + SQL + 理由），
不依赖解析自然语言 —— 规划因此是**数据**，不是**散文**。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from .config import Settings
from .llm import ToolSpec, assistant_message, build_client, create_completion
from .retrieval import RetrievalResult, build_retriever
from .tools import REASONING_SPEC, RETRIEVE_SPEC, TOOL_SPECS, ToolBox

__all__ = ["AgentState", "GraphAgent", "GraphTrace", "build_graph"]


# ------------------------------------------------------------
# 状态
# ------------------------------------------------------------
class AgentState(TypedDict, total=False):
    """图的状态。

    `total=False` 是因为节点只写自己关心的字段。
    刻意**不使用** LangGraph 的消息 reducer（`add_messages`）：
    本项目要的证据是"每一步调了什么工具、结果如何"，
    而 reducer 会把多轮消息合并成一条流，反而让"这一轮发生了什么"变得难读。
    因此 `messages` 由节点显式追加，`steps` 是结构化的审计记录。
    """

    question: str
    docs_context: str
    docs: list[dict[str, Any]]
    plan: str
    plan_steps: list[dict[str, Any]]
    plan_failed: bool
    # 规划器的专用消息流：只含系统提示词 + 本轮问题 + 上一版计划与真实失败。
    # 与"全局消息流"分开，是为了让重规划真的改变做法（见 node_plan 的说明）。
    planner_messages: list[dict[str, Any]]
    # 取数结果（给汇总器的干净证据，与 plan_steps 严格对应）
    results: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    executed_sql: list[str]
    issues: list[str]
    retries: int
    max_retries: int
    answer: str
    truncated: bool
    tool_rounds: int
    deadline: float
    trace: list[dict[str, Any]]


@dataclass
class GraphTrace:
    """图的执行轨迹（对审计与验收都重要：要能看出走过哪些节点）。"""

    path: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def visit(self, node: str) -> None:
        self.path.append(node)

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def as_dict(self) -> dict[str, Any]:
        return {"path": list(self.path), "elapsed_ms": self.elapsed_ms}


# ------------------------------------------------------------
# 提示词
# ------------------------------------------------------------
PLANNER_PROMPT = """你是数据问答 Agent 的**规划器**。基于给定的「口径与元数据检索结果」
与用户问题，产出一次数据查询计划。

要求：
1. **必须先读检索结果**：口径以检索到的文档为准，不要按自己的理解写公式。
   若检索结果里没有相关口径，就用 propose_sql 的 `reason` 说明"语料未覆盖"，
   并在 `notes` 里写明，**不要编造公式**。
2. 只能查询**已授权**的表（见系统提示词的数据地图）；查询必须写「库名.表名」。
3. 只写 SELECT，不要分号、不要注释、不要多条语句；不要自己写 LIMIT（服务端会强制加）。
4. 若上一步的失败反馈已给出，**必须针对该失败改变做法**（例如换表、去掉不支持的列），
   不要重复同一条 SQL。

调用 `propose_sql` 提交计划：`reasoning` 是给用户看的思路，`notes` 是要点，
`steps` 是 1~3 条 SQL（每条含 purpose / target_table / sql）。
信息不足以写 SQL 时，`steps` 传空数组，并在 `reasoning` 里说明还缺什么。"""

SUMMARIZER_PROMPT = """你是数据问答 Agent 的**汇总器**。基于上面**已经真实执行过**的查询结果
回答用户问题。

铁律：
1. **只使用已执行查询的返回结果**。禁止使用经验值、常识或估算值补全数字。
2. 查不到就如实说"查不到"，并说明试过什么（哪条 SQL、被拒的真实原因是什么）。
3. 回答里的每个数字都要能对应到某条已执行的 SQL；比率要说明分母。
4. **引用了口径就必须写出来源**，格式为 `依据 sql/metadata/metrics.md:33`；
   若没有引用任何口径，就不要编造引用。
5. 若结果行数为 0、被截断、或某列为 NULL，要主动说明，不要让用户误以为数据完整。"""


# ------------------------------------------------------------
# 图实现
# ------------------------------------------------------------
class GraphAgent:
    """LangGraph 图式数据问答 Agent。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.toolbox = ToolBox(settings)
        self.retriever = build_retriever(settings)
        self._client: Any = None
        # 暴露给模型的工具：原有 4 个 + 检索 + 计划提交（能力面可枚举）
        self.tools: list[dict[str, Any]] = [spec.as_openai_tool() for spec in TOOL_SPECS]
        self._graph: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_client(self.settings)
        return self._client

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        return create_completion(self.client, self.settings, messages, tools or [])

    def _expired(self, state: AgentState) -> bool:
        return time.perf_counter() > float(state.get("deadline", 0.0))

    # ---------------- 节点 1：检索 ----------------
    def node_retrieve(self, state: AgentState) -> AgentState:
        """检索口径/表结构/分层文档（确定性执行，不交给模型决定要不要查）。"""
        if not self.settings.retrieval_enabled:
            return {
                "docs_context": "（检索已按配置关闭）",
                "docs": [],
                "trace": state.get("trace", []) + [{"node": "retrieve", "skipped": True}],
            }

        result: RetrievalResult = self.retriever.retrieve(state["question"])
        return {
            "docs_context": result.as_context(),
            "docs": result.sources(),
            "trace": state.get("trace", [])
            + [
                {
                    "node": "retrieve",
                    "hits": len(result.hits),
                    "docs_total": result.docs_total,
                    "expanded_terms": [item["phrase"] for item in result.expanded_terms],
                    "degraded": result.degraded,
                }
            ],
        }

    # ---------------- 节点 2：规划 ----------------
    def node_plan(self, state: AgentState) -> AgentState:
        """产出显式规划（结构化步骤 + 目标表）。

        !! 规划器每轮只用"干净的上下文"，不复用全局消息流（实测踩坑）!!
            第一版实现让规划器接着上一轮的消息继续（含上一轮的查询结果），
            结果重规划时模型看到"已经有数据了"，就把同一条错 SQL 又提了一遍 ——
            实测同一张不存在的列被查了两次，重试等于重放。
            现在每轮只给：系统提示词 + 问题 + 检索结果 + **上一版计划与真实失败**。
            这样"改变做法"才有具体的对象，模型也不会被旧结果误导。
        """
        feedback = [item for item in (state.get("issues") or []) if not item.startswith("[警告]")]
        warnings = [item for item in (state.get("issues") or []) if item.startswith("[警告]")]

        planner_history = list(state.get("planner_messages") or [])
        if not planner_history:
            planner_history = [{"role": "system", "content": _graph_system_prompt()}]

        user_parts = [
            f"# 用户问题\n{state['question']}",
            "# 口径与元数据检索结果（命中即口径依据，引用时请写出来源）\n"
            f"{state.get('docs_context', '（无）')}",
        ]
        if feedback:
            user_parts.append(
                "# 上一次的失败反馈（必须据此改变做法）\n"
                + "\n".join(f"- {item}" for item in feedback[:5])
            )
            previous = state.get("plan_steps") or []
            if previous:
                lines = [
                    f"  - [{step.get('target_table') or '未标注表'}] {step.get('purpose') or ''}\n"
                    f"      {str(step.get('sql') or '')[:400]}"
                    for step in previous
                ]
                user_parts.append(
                    "# 你上一版提交的计划（**不要原样重复**，必须针对上面的失败改变做法）\n"
                    + "\n".join(lines)
                )
        if warnings:
            user_parts.append("# 上一轮需要如实说明的点\n" + "\n".join(f"- {item}" for item in warnings[:3]))
        user_parts.append("# 你的任务\n调用 propose_sql 提交本次查询计划。")

        question_message = {"role": "user", "content": "\n\n".join(user_parts)}
        messages = planner_history + [question_message]

        trace_entry: dict[str, Any] = {"node": "plan"}
        try:
            response = self._complete(messages, [REASONING_SPEC.as_openai_tool()])
        except Exception as exc:  # noqa: BLE001 - LLM 故障必须变成可见的失败
            # !! 规划失败必须**短路到 summarize**，不能继续往下走 !!
            #   若照常走 execute→validate，validate 会再加一条
            #   "没有任何查询被执行"，把真实的根因（模型调用失败）盖在下面，
            #   最终用户看到的是一个"什么都没说清"的回答。
            #   失败即失败（AGENTS.md §10.3）：直接把真实原因送到汇总节点。
            return {
                "planner_messages": planner_history,
                "plan": "",
                "plan_steps": [],
                "plan_failed": True,
                "issues": list(feedback)
                + [f"规划阶段调用模型失败：{type(exc).__name__}: {exc}"],
                "trace": state.get("trace", []) + [{**trace_entry, "error": str(exc)}],
            }

        message = response.choices[0].message
        plan_text, plan_steps, _raw = _parse_plan(message)
        trace_entry.update({"steps": len(plan_steps), "reasoning_chars": len(plan_text)})

        # !! 必须回应 propose_sql 的 tool_call !!
        #   DeepSeek（与 OpenAI 同协议）要求：带 tool_calls 的 assistant 消息
        #   后面**必须**跟齐每个 tool_call_id 对应的 tool 消息，否则整个请求被拒：
        #       "An assistant message with 'tool_calls' must be followed by tool
        #        messages responding to each 'tool_call_id'"
        #   实测后果：规划本身成功，但下一轮（汇总）直接 400，
        #   最终回答退化成"汇总阶段调用模型失败" —— 功能全做对了，
        #   却因为少一条协议消息而完全不可用。
        assistant = assistant_message(response)
        acknowledgements = [
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": (
                    f"计划已受理：共 {len(plan_steps)} 条查询步骤，接下来由系统执行。"
                    if plan_steps
                    else "计划已受理：本次没有可执行的查询步骤。"
                ),
            }
            for call in (assistant.get("tool_calls") or [])
        ]

        return {
            "planner_messages": planner_history + [question_message, assistant, *acknowledgements],
            "plan": plan_text,
            "plan_steps": plan_steps,
            "plan_failed": False,
            "issues": feedback,  # 反馈已被消化进本轮规划
            "trace": state.get("trace", []) + [trace_entry],
        }

    # ---------------- 节点 3：取数 ----------------
    def node_execute(self, state: AgentState) -> AgentState:
        """执行计划中的 SQL（唯一取数通道）。

        结果写进 `results`（给汇总器看的**干净**证据）。
        刻意不把执行结果塞进对话消息：实测那样做会让模型在重规划时
        以为"已经有数据"，从而原样重复上一条错 SQL。
        """
        steps = list(state.get("steps") or [])
        executed = list(state.get("executed_sql") or [])
        results = list(state.get("results") or [])
        issues: list[str] = []
        tool_rounds = int(state.get("tool_rounds", 0))

        plan_steps = state.get("plan_steps") or []
        trace_entry: dict[str, Any] = {"node": "execute", "planned": len(plan_steps)}

        for index, item in enumerate(plan_steps, start=1):
            sql = str(item.get("sql", "")).strip()
            purpose = str(item.get("purpose", "")) or f"步骤 {index}"
            if not sql:
                issues.append(f"规划步骤 {index}（{purpose}）没有给出 SQL")
                continue
            if tool_rounds >= self.settings.max_tool_rounds:
                issues.append(
                    f"已达工具调用上限（{self.settings.max_tool_rounds} 轮），"
                    f"步骤 {index}（{purpose}）未执行"
                )
                break

            tool_rounds += 1
            result = self.toolbox.call("sql_query", {"sql": sql})
            if result.ok:
                executed.append(sql)

            steps.append(
                {
                    "round": tool_rounds,
                    "tool": result.tool,
                    "arguments": {"sql": sql, "purpose": purpose},
                    "ok": result.ok,
                    "tables": list(result.tables),
                    "summary": result.content[:400],
                }
            )
            # 结果单独记进 `results`，**不塞回对话消息**：
            # 汇总器需要的是"这次真实查到了什么"，而且必须与这次执行的步骤严格对应。
            # 把结果混进消息流会让重规划时看到上一轮的旧数据（实测导致原样重放）。
            results.append(
                {
                    "round": tool_rounds,
                    "purpose": purpose,
                    "target_table": str(item.get("target_table", "")),
                    "sql": sql,
                    "ok": result.ok,
                    "content": result.content,
                    "tables": list(result.tables),
                }
            )
            if not result.ok:
                issues.append(f"步骤 {index}（{purpose}）执行失败：{result.content[:300]}")

        trace_entry["rounds"] = tool_rounds
        return {
            "results": results,
            "steps": steps,
            "executed_sql": executed,
            "issues": issues,
            "tool_rounds": tool_rounds,
            "trace": state.get("trace", []) + [trace_entry],
        }

    # ---------------- 节点 4：校验 ----------------
    def node_validate(self, state: AgentState) -> AgentState:
        """校验取数结果：错误、空结果、比率分母 0、结果被截断。

        为什么校验必须独立成节点：
            "工具调用成功了"只说明**请求**成功，不说明**结果能回答问题**。
            行数为 0 的查询在 HTTP 上是 200，在业务上却是"没查到" ——
            这两件事必须能被区分，否则会给出"GMV 是 0"这种看起来有依据的错答案。
        """
        issues: list[str] = list(state.get("issues") or [])
        warnings: list[str] = []
        steps = state.get("steps") or []

        if not steps:
            issues.append("没有任何查询被执行（规划未产出可执行的 SQL）")

        for step in steps:
            if not step.get("ok"):
                continue  # 执行失败已在 execute 节点记录，避免重复
            summary = str(step.get("summary", ""))
            row_count = _extract_number(summary, "row_count")
            if row_count == 0:
                warnings.append(
                    f"「{step['arguments'].get('purpose', '')}」返回 0 行 —— "
                    "查询语法正确但该条件下没有数据，回答时必须说明"
                )
            if "已截断" in summary:
                warnings.append(
                    f"「{step['arguments'].get('purpose', '')}」的结果被截断，"
                    "不能当作完整数据"
                )
            if row_count is not None and row_count > 0 and "null" in summary.lower():
                warnings.append(
                    f"「{step['arguments'].get('purpose', '')}」的结果里含 NULL —— "
                    "比率类指标分母为 0 时为 NULL（不是 0），引用时要说清"
                )

        # 失败优先：失败是必须重规划的问题，警告只是需要如实说明
        all_issues = issues + [f"[警告] {item}" for item in warnings]
        blocking = [item for item in issues if not item.startswith("[警告]")]

        return {
            "issues": all_issues,
            "trace": state.get("trace", [])
            + [
                {
                    "node": "validate",
                    "blocking": len(blocking),
                    "warnings": len(warnings),
                    "samples": all_issues[:2],
                }
            ],
        }

    # ---------------- 节点 5：反思 ----------------
    def node_reflect(self, state: AgentState) -> AgentState:
        """把真实错误交回规划器，并记一次重试。

        `retries` 只在**真正要重规划**时自增：
        这样"重试了几次"是一个可信的数字，而不是"循环跑了几轮"的别名。
        """
        retries = int(state.get("retries", 0)) + 1
        blockers = [item for item in (state.get("issues") or []) if not item.startswith("[警告]")]
        return {
            "retries": retries,
            "trace": state.get("trace", [])
            + [{"node": "reflect", "retry": retries, "blocking_issues": len(blockers)}],
        }

    # ---------------- 节点 6：汇总 ----------------
    def node_summarize(self, state: AgentState) -> AgentState:
        """汇总结论（必须附来源；查不到就如实说）。

        !! 汇总器**只拿证据，不拿中间消息**（实测踩坑）!!
            汇总上下文 = 系统提示词 + 问题 + 检索结果 + 规划 + **每一次真实执行的
            结果/错误** + 需要如实说明的点。
            刻意不拼"assistant 的 tool_calls + tool 消息"：
            那种历史一旦与当前消息序列对不齐，协议就会拒绝整个请求
            （"must be followed by tool messages"）；而且那些中间态
            对"给出结论"没有价值，只会稀释注意力。

        收尾调用**不携带 tools**：否则模型还能继续发起工具调用，图停不下来。
        """
        results = list(state.get("results") or [])
        blockers = [item for item in (state.get("issues") or []) if not item.startswith("[警告]")]
        warnings = [item for item in (state.get("issues") or []) if item.startswith("[警告]")]

        blocks = [
            {"role": "system", "content": _graph_system_prompt()},
            {
                "role": "user",
                "content": (
                    f"# 用户问题\n{state['question']}\n\n"
                    f"# 口径与元数据检索结果\n{state.get('docs_context', '（无）')}"
                ),
            },
        ]

        plan_steps = state.get("plan_steps") or []
        plan_lines = [
            f"  - [{step.get('target_table') or '未标注表'}] {step.get('purpose') or ''}\n"
            f"      {str(step.get('sql') or '')[:400]}"
            for step in plan_steps
        ]
        blocks.append(
            {
                "role": "user",
                "content": "# 本次执行的规划\n"
                + ("\n".join(plan_lines) if plan_lines else "（本次没有产出可执行的查询步骤）"),
            }
        )

        # !! 判据是"有没有**成功**的查询"，不是"有没有查过" !!
        #   实测踩坑：写成"没有结果才提醒"时，一次失败查询也会算作"有结果"，
        #   于是模型拿到的是原始错误文本而没有得到明确指示，
        #   回答里就容易出现含糊或看似有依据的表述。
        #   这里把"失败尝试"与"成功结果"分开呈现，并在一个都没成功时
        #   直接告诉模型：你没有任何数字可用。
        succeeded = [item for item in results if item.get("ok")]
        failed = [item for item in results if not item.get("ok")]

        if results:
            rendered = []
            for item in results:
                status = "成功" if item.get("ok") else "**失败**"
                rendered.append(
                    f"## 第 {item.get('round')} 次查询（{status}）｜目的：{item.get('purpose')}\n"
                    f"SQL：{str(item.get('sql') or '')[:500]}\n"
                    f"结果：{str(item.get('content') or '')[:1800]}"
                )
            blocks.append(
                {
                    "role": "user",
                    "content": "# 真实执行结果（**数字只能来自这里**）\n\n" + "\n\n".join(rendered),
                }
            )

        if not succeeded:
            blocks.append(
                {
                    "role": "user",
                    "content": (
                        "# 重要：本次**没有任何查询成功执行**\n"
                        f"（共尝试 {len(failed)} 条查询，全部失败，失败原文见上。）\n"
                        "你**无法给出任何数字**，必须如实说明查不到，并说明真实的失败原因"
                        "（例如该表不在授权范围内、列名不存在）。"
                        "**绝对不要**用经验值、常识或估算值补全，也不要给出任何具体金额或比率。"
                    ),
                }
            )

        tail = [
            "# 本次执行到的状态",
            f"- 执行成功的查询数：{len(state.get('executed_sql') or [])}",
            f"- 未被解决的失败：{len(blockers)} 条",
            f"- 需要如实说明的警告：{len(warnings)} 条",
            f"- 已用反思重试：{int(state.get('retries', 0))} 次",
        ]
        if blockers:
            tail.append(
                "未被解决的失败明细（回答里必须如实说明，不得掩盖）：\n"
                + "\n".join(f"  - {str(item)[:300]}" for item in blockers[:5])
            )
        if warnings:
            tail.append(
                "需要如实说明的点：\n" + "\n".join(f"  - {str(item)[:300]}" for item in warnings[:5])
            )
        if state.get("truncated"):
            tail.append("（本次因额度或超时提前收尾，答案可能不完整，必须说明）")
        tail.append("# 你的任务\n给出最终回答（遵守汇总器的铁律）。")
        blocks.append({"role": "user", "content": "\n".join(tail)})

        try:
            response = self._complete(blocks, [])
            answer = response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - 收尾失败也要给出可读信息，不能静默
            answer = (
                "汇总阶段调用模型失败："
                f"{type(exc).__name__}: {exc}。已执行的查询见 executed_sql 字段。"
            )

        trace_entry = {
            "node": "summarize",
            "answer_chars": len(answer),
            "results": len(results),
            "truncated": bool(state.get("truncated")),
        }
        return {
            "messages": blocks,
            "answer": answer,
            "trace": state.get("trace", []) + [trace_entry],
        }

    # --------------------------------------------------------
    # 路由
    # --------------------------------------------------------
    def route_after_plan(self, state: AgentState) -> str:
        """规划之后去哪：规划失败就直接汇总，不要空跑一圈。

        短路的意义不只是省一次调用：规划失败时 `plan_steps` 是空的，
        `execute` 什么也做不了，`validate` 只会补一条通用的
        "没有任何查询被执行"，反而把真实的失败原因（模型调用失败）
        盖在下面。用户看到的应当是根因。
        """
        return "summarize" if state.get("plan_failed") else "execute"

    def route_after_validate(self, state: AgentState) -> str:
        """校验之后去哪：需要重规划，还是直接汇总。

        三条终止判据都写在这里，一眼能看清"为什么停"：
            1. 没有"阻塞性问题"（警告不算）→ 汇总；
            2. 重试额度用尽 → 汇总，并在响应里把 `truncated` 标出来；
            3. 总超时 → 汇总。
        """
        blockers = [item for item in (state.get("issues") or []) if not item.startswith("[警告]")]
        if not blockers:
            return "summarize"
        if int(state.get("retries", 0)) >= int(state.get("max_retries", 0)):
            return "summarize"
        if self._expired(state):
            return "summarize"
        return "reflect"

    # --------------------------------------------------------
    # 构图
    # --------------------------------------------------------
    def build(self) -> Any:
        """构造 LangGraph 图（惰性导入 langgraph）。"""
        if self._graph is not None:
            return self._graph

        try:
            from langgraph.graph import END, StateGraph
        except ImportError as exc:  # pragma: no cover - 部署脚本会装好依赖
            raise RuntimeError(
                "缺少 langgraph 依赖（Sprint 8）。请在服务器执行： "
                "bash scripts/deploy-agent.sh"
            ) from exc

        graph = StateGraph(AgentState)
        graph.add_node("retrieve", self.node_retrieve)
        graph.add_node("plan", self.node_plan)
        graph.add_node("execute", self.node_execute)
        graph.add_node("validate", self.node_validate)
        graph.add_node("reflect", self.node_reflect)
        graph.add_node("summarize", self.node_summarize)

        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "plan")
        graph.add_conditional_edges(
            "plan",
            self.route_after_plan,
            {"execute": "execute", "summarize": "summarize"},
        )
        graph.add_edge("execute", "validate")
        graph.add_conditional_edges(
            "validate",
            self.route_after_validate,
            {"reflect": "reflect", "summarize": "summarize"},
        )
        # 反思之后回到 plan（而不是 execute）：错误通常意味着计划本身错了
        graph.add_edge("reflect", "plan")
        graph.add_edge("summarize", END)

        self._graph = graph.compile()
        return self._graph

    def describe(self) -> dict[str, Any]:
        """图的静态描述（供 `/graph` 接口，让编排可审查）。"""
        return {
            "engine": "langgraph",
            "nodes": [
                {"name": "retrieve", "role": "检索口径/表结构/分层文档（确定性执行）"},
                {"name": "plan", "role": "产出显式规划：目标表 + SQL 草稿 + 理由"},
                {"name": "execute", "role": "执行 SQL（唯一取数通道：POST /query）"},
                {"name": "validate", "role": "校验取数结果：错误/空结果/分母 0/截断"},
                {"name": "reflect", "role": "把真实错误交回规划器，计一次重试"},
                {"name": "summarize", "role": "汇总结论并附来源（不携带 tools，保证终止）"},
            ],
            "edges": [
                ["retrieve", "plan"],
                ["plan", "execute", "规划成功"],
                ["plan", "summarize", "规划阶段模型调用失败（短路，直接汇总真实原因）"],
                ["execute", "validate"],
                ["validate", "reflect", "有阻塞性问题且还有重试额度"],
                ["validate", "summarize", "无阻塞性问题 / 额度用尽 / 超时"],
                ["reflect", "plan", "重规划"],
                ["summarize", "<END>"],
            ],
            "limits": {
                "max_retries": self.settings.max_retries,
                "max_tool_rounds": self.settings.max_tool_rounds,
                "total_timeout": self.settings.total_timeout,
            },
        }

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def ask(self, question: str) -> AnswerBundle:
        """跑一次图并返回带证据的回答。"""
        started = time.perf_counter()
        initial: AgentState = {
            "question": question,
            "messages": [],
            "planner_messages": [],
            "results": [],
            "steps": [],
            "executed_sql": [],
            "issues": [],
            "retries": 0,
            "max_retries": self.settings.max_retries,
            "docs": [],
            "docs_context": "",
            "plan": "",
            "plan_steps": [],
            "plan_failed": False,
            "answer": "",
            "truncated": False,
            "tool_rounds": 0,
            "deadline": time.perf_counter() + self.settings.total_timeout,
            "trace": [],
        }

        final: AgentState = self.build().invoke(initial, {"recursion_limit": 25})
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        steps = final.get("steps") or []
        tables: list[str] = []
        for step in steps:
            for table in step.get("tables") or []:
                if table not in tables:
                    tables.append(table)

        blockers = [item for item in (final.get("issues") or []) if not item.startswith("[警告]")]
        # 额度/超时用尽属于"提前收尾"：必须让调用方看得出来
        truncated = bool(blockers) or int(final.get("tool_rounds", 0)) >= self.settings.max_tool_rounds

        rounds = len({step.get("round") for step in steps}) if steps else 0
        return AnswerBundle(
            question=question,
            answer=str(final.get("answer", "")),
            plan=str(final.get("plan", "")),
            plan_steps=list(final.get("plan_steps") or []),
            docs=list(final.get("docs") or []),
            tables=tables,
            executed_sql=list(final.get("executed_sql") or []),
            steps=steps,
            issues=list(final.get("issues") or []),
            retries=int(final.get("retries", 0)),
            graph_trace=list(final.get("trace") or []),
            elapsed_ms=elapsed_ms,
            model=self.settings.llm_model,
            rounds=rounds,
            truncated=truncated,
        )


# ------------------------------------------------------------
# 回答封装
# ------------------------------------------------------------
@dataclass
class AnswerBundle:
    """图的产出（同时给用户与审计）。"""

    question: str
    answer: str
    plan: str = ""
    plan_steps: list[dict[str, Any]] = field(default_factory=list)
    docs: list[dict[str, Any]] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    executed_sql: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    retries: int = 0
    graph_trace: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0
    model: str = ""
    rounds: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        blockers = [item for item in self.issues if not item.startswith("[警告]")]
        warnings = [item for item in self.issues if item.startswith("[警告]")]
        return {
            "question": self.question,
            "answer": self.answer,
            "plan": {"summary": self.plan, "steps": self.plan_steps},
            "validation": {
                "ok": not blockers,
                "blocking_issues": blockers,
                "warnings": warnings,
            },
            "retries": self.retries,
            "graph": {"path": [entry.get("node") for entry in self.graph_trace], "trace": self.graph_trace},
            "docs": self.docs,
            "tables": self.tables,
            "executed_sql": self.executed_sql,
            "steps": self.steps,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "rounds": self.rounds,
            "truncated": self.truncated,
        }


# ------------------------------------------------------------
# 辅助
# ------------------------------------------------------------
def _graph_system_prompt() -> str:
    """图式 Agent 的系统提示词。

    在 Sprint 7 的 `SYSTEM_PROMPT` 之上追加"图式工作流"一节。
    **不改写原有铁律与数据地图**：`verify-sprint-7.sh` 对提示词有断言
    （"只允许 SELECT"、"禁止编造数据"、"lakehouse_ads"、"metrics_lookup"），
    而且那几条约束本身没有任何需要修改的理由。
    """
    from .agent import SYSTEM_PROMPT

    return (
        SYSTEM_PROMPT
        + """

# 图式工作流（Sprint 8）
你现在运行在一个**图**里，而不是一个自由循环里。每一步都有明确的输入与产出：
  1. retrieve  已自动执行：口径/表结构/分层文档已检索好并附在问题后面；
  2. plan      你产出结构化规划（目标表 + SQL + 理由）—— **这一步必须先做**；
  3. execute   系统按你的规划执行 SQL，真实结果会回给你；
  4. validate  系统校验结果（空结果/分母为 0/被截断都会告诉你）；
  5. reflect   有失败时系统会把**真实错误**交回给你，要求你改变做法重规划；
  6. summarize 你基于**已执行查询的结果**汇总结论。

因此：不要跳过规划直接写 SQL；失败时**必须改变做法**（换表/改列），
不要重复提交同一条已被拒绝的 SQL；引用了口径就写出来源（`文件:行号`）。"""
    )


def _parse_plan(message: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """从模型回复里取出计划（结构化工具调用优先，纯文本 JSON 兜底）。

    为什么要有文本兜底：
        少数情况下模型会把 JSON 直接写在 `content` 里而不发起工具调用
        （实测在"步骤传空数组"这种边界输入上出现过）。
        解析失败**不报错**，而是返回空计划 —— 由 `validate` 节点产生
        "没有任何查询被执行"这条 issue，最终如实告诉用户。
        把"模型没按格式来"变成一条可读的失败原因，比抛异常更有用。
    """
    content = message.content or ""
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if call.function.name != "propose_sql":
            continue
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            continue
        steps = _normalize_steps(arguments.get("steps"))
        summary = str(arguments.get("reasoning", "")) or _summarize_steps(steps)
        notes = str(arguments.get("notes", ""))
        if notes:
            summary = f"{summary}（要点：{notes}）"
        return summary, steps, arguments

    parsed = _extract_json(content)
    if isinstance(parsed, dict) and "steps" in parsed:
        steps = _normalize_steps(parsed.get("steps"))
        return str(parsed.get("reasoning", "")) or _summarize_steps(steps), steps, parsed

    return content.strip(), [], {}


def _normalize_steps(raw: Any) -> list[dict[str, Any]]:
    """把模型给的步骤规范成统一结构（丢弃没有 SQL 的项）。"""
    if not isinstance(raw, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sql = str(item.get("sql", "")).strip()
        if not sql:
            continue
        steps.append(
            {
                "purpose": str(item.get("purpose", "")).strip(),
                "target_table": str(item.get("target_table", "")).strip(),
                "sql": sql,
            }
        )
    return steps


def _summarize_steps(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "未产出可执行的 SQL 步骤"
    return "；".join(
        f"{step.get('purpose') or '查询'} → {step.get('target_table') or '未标注表'}"
        for step in steps
    )


def _extract_json(text: str) -> Any:
    """从可能带 ```json 包裹的文本里取出第一个 JSON 对象。"""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _extract_number(text: str, key: str) -> int | None:
    """从工具结果摘要里取某个整数字段（用于校验，不做语义推断）。"""
    marker = f'"{key}"'
    position = text.find(marker)
    if position < 0:
        return None
    tail = text[position + len(marker) :]
    digits: list[str] = []
    started = False
    for char in tail:
        if char.isdigit():
            digits.append(char)
            started = True
        elif started:
            break
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None


def build_graph(settings: Settings) -> Callable[[str], AnswerBundle]:
    """便捷入口：返回一个 `question -> AnswerBundle` 的可调用对象。"""
    agent = GraphAgent(settings)
    return agent.ask
