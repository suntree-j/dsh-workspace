"""Agent 主循环：问题理解 → 工具调用 → 安全检查 → 取数 → 来源可追溯的回答。

## 与项目设计文档的对应关系

`docs/PROJECT_DATA_SOURCE` 第 5.3 节写的是：

    自然语言问题 → 指标识别 → 元数据检索 → SQL 生成 → SQL 安全检查
                → Doris 查询（只读账号）→ 结果分析 → 回答 + 数据来源

本模块是它的直接实现，其中两步**不在本进程里做**，这是有意的：

| 设计步骤 | 实际落点 | 为什么放那里 |
| --- | --- | --- |
| SQL 安全检查 | `services/api/app/sqlguard.py` | 放在**服务端**，Agent 无法绕过；放在 Agent 里等于让"被约束者自己当裁判" |
| Doris 查询（只读账号） | `services/api/app/doris.py` | Agent 进程里根本没有数据库凭据 |

因此 Agent 侧的"安全检查"体现为：**它只有一条取数通道，而那条通道是受约束的**。

## 三条硬约束在代码里的落点

1. `AGENT 不得伪造查询结果` → 最终回答里附 `evidence`（每步调了什么工具、读了哪些表），
   以及 `executed_sql`（服务端返回的**实际执行**语句）。
2. `查询失败必须能重新规划` → 工具失败**不中断循环**，把错误文本交回模型，
   让它改写 SQL 后重试（最多 `max_tool_rounds` 轮）。
3. `必须能说明数据来源` → `evidence[].tables` 汇总成 `tables` 字段随回答返回。

## 为什么不用 LangGraph（现在）

项目 Roadmap 里 LangGraph 是 Sprint 8。Sprint 7 的目标是"自然语言转 SQL 的基础能力"，
一个带工具调用的循环就够了。提前引入 LangGraph 会带来一个具体问题：
图的状态机与 checkpoint 会掩盖"这一轮到底调了什么工具"，
反而不利于本 Sprint 最需要的**可观测性与可验证性**。
把循环写清楚，Sprint 8 再把它改造成图 —— 那时迁移的是已经验证过的节点，而不是猜测。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .llm import ToolSpec, assistant_message, build_client, create_completion
from .tools import TOOL_SPECS, ToolBox

__all__ = ["Agent", "AgentAnswer", "ToolCallRecord"]


# ------------------------------------------------------------
# 系统提示词
#
# 这个字符串是 Agent 行为的**唯一**总纲。它做三件事：
#   1. 交代身份与铁律（不许编、只能用 SELECT、必须给来源）；
#   2. 交代数据地图（两个库、两种粒度、表清单）；
#   3. 交代工作流（先查口径 → 再查表结构 → 再写 SQL → 需要时自己对账）。
#
# 为什么把表清单写死在提示词里：
#   表是白名单、数量固定（19 张）。让模型每次先调工具列表，
#   既多一轮开销，又可能因为漏看而选错表。直接列出更便宜也更稳。
# ------------------------------------------------------------
SYSTEM_PROMPT = """你是「批流一体智能数据分析平台」的数据问答助手。
用户用中文提问，你负责把它变成对数据仓库的只读查询，并基于**真实查询结果**回答。

# 铁律（不可违反）
1. 只允许 SELECT。任何写操作（INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE）一律不做。
2. **禁止编造数据**。每一个数字都必须来自 sql_query 的返回结果。
   查不到就说查不到，并说明你试过什么；**绝不允许**用经验值或估算值代替真实结果。
3. 回答必须能说明来源：用了哪些表、时间范围、口径。
4. 一次只能执行一条 SELECT，不能带分号或多条语句。

# 数据地图
平台有两条链路，**粒度不同，选表时要看问题的时间范围**：

- 实时链路（库 ecommerce，**1 分钟窗口**，数据秒级刷新）
  - ads_realtime_trade_1m        交易：gmv / order_cnt / order_user_cnt /
                                 payment_cnt / payment_amount / payment_fail_cnt /
                                 refund_cnt / refund_amount / payment_success_rate / refund_rate
  - ads_realtime_traffic_1m      流量：uv / pv / view_cnt / click_cnt / cart_cnt /
                                 favorite_cnt / buy_cnt / click_rate / cart_rate / buy_rate
  - ads_realtime_category_1m     类目：window_start / category_name / order_cnt / gmv /
                                 total_quantity / avg_order_amount
  - dwd_trade_order_detail       订单明细（一行一单）
  - dwd_trade_payment_detail     支付明细
  - dwd_trade_refund_detail      退款明细
  - dwd_traffic_behavior_detail  行为明细
  - dws_traffic_overview_1m      流量按分钟汇总
  - dim_product / dim_user       维表

- 离线链路（库 lakehouse_ads，**按天**，准确、可回溯）
  - ads_batch_trade_1m / ads_batch_trade_1d        交易（1 分钟 / 1 天）
  - ads_batch_category_1m / ads_batch_category_1d  类目（1 分钟 / 1 天）
  - ads_reconcile_trade_1m / ads_reconcile_summary 批流对账结果

写 SQL 时必须写**库名.表名**（例如 ecommerce.ads_realtime_trade_1m），
因为两条链路的表名相似、库不同，不写库名会查错。

# 指标口径（重要）
GMV = 窗口内**订单创建**事件的成交金额合计（下单即计入，不是支付口径）。
计算任何指标前，先用 metrics_lookup 确认口径，不要按自己的理解写公式。
可加指标（gmv/笔数/金额）无事件时为 0；比率类指标分母为 0 时为 NULL（不是 0）。
去重类指标（uv、order_user_cnt）**不能跨窗口求和**（那样得到的是人次），
要得到全量去重值必须回到明细表用 COUNT(DISTINCT user_id)。

# 工作流
1. 先判断问题涉及哪些指标 → 调 metrics_lookup 确认口径。
2. 需要确认列名或单位 → 调 tables_lookup。
3. 写 SQL 并调 sql_query。被拒绝时**读错误信息**改正后重试，不要放弃。
4. 如果用户问的是"数据准不准 / 可不可信"，调 reconciliation 用对账结论回答。
5. 拿不到数据就如实说，并说明失败原因。

# 回答风格
- 先给结论（数字 + 单位），再给依据（口径、表、时间范围）。
- 金额保留两位小数；比率用百分比并注明分母。
- 不要罗列大段原始行数据，用户要的是结论。
- 若结果被截断或行数为 0，要主动说明，不要让用户误以为是完整数据。
"""


@dataclass
class ToolCallRecord:
    """一次工具调用的记录（审计与展示用）。"""

    round_index: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    tables: list[str] = field(default_factory=list)
    # 结果摘要（截断，供前端"查看过程"折叠展示）
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "tool": self.tool,
            "arguments": self.arguments,
            "ok": self.ok,
            "tables": self.tables,
            "summary": self.summary,
        }


@dataclass
class AgentAnswer:
    """Agent 的最终产出。"""

    answer: str
    question: str
    tables: list[str] = field(default_factory=list)
    executed_sql: list[str] = field(default_factory=list)
    steps: list[ToolCallRecord] = field(default_factory=list)
    elapsed_ms: int = 0
    model: str = ""
    rounds: int = 0
    truncated: bool = False  # 是否因为轮次上限而提前收尾

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "tables": self.tables,
            "executed_sql": self.executed_sql,
            "steps": [step.as_dict() for step in self.steps],
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "rounds": self.rounds,
            "truncated": self.truncated,
        }


class Agent:
    """带工具调用的问答循环。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.toolbox = ToolBox(settings)
        self.tools: list[dict[str, Any]] = [spec.as_openai_tool() for spec in TOOL_SPECS]
        # LLM 客户端惰性创建：这样"构造 Agent"本身不需要 openai 依赖，
        # 缺依赖/缺 Key 的环境里 /health、/tools 仍可正常工作。
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_client(self.settings)
        return self._client

    # --------------------------------------------------------
    def ask(self, question: str) -> AgentAnswer:
        """回答一个问题。"""
        started = time.perf_counter()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        steps: list[ToolCallRecord] = []
        executed_sql: list[str] = []
        truncated = False

        answer_text = ""
        for round_index in range(1, self.settings.max_tool_rounds + 1):
            # 最后一个允许的轮次之前，若已经超时就收尾（见下面的 _finalize）
            if time.perf_counter() - started > self.settings.total_timeout:
                answer_text = self._finalize(messages, steps)
                truncated = True
                break

            response = create_completion(self.client, self.settings, messages, self.tools)
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)

            # 没有工具调用 ⇒ 模型给出了最终回答
            if not tool_calls:
                answer_text = message.content or ""
                break

            messages.append(assistant_message(response))

            for call in tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                    result_content = (
                        "工具参数不是合法 JSON，无法解析。请重新调用并传入合法参数。"
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result_content}
                    )
                    steps.append(
                        ToolCallRecord(round_index, call.function.name, {}, False, [], result_content)
                    )
                    continue

                result = self.toolbox.call(call.function.name, arguments)
                if result.ok and call.function.name == "sql_query":
                    executed_sql.append(str(arguments.get("sql", "")))

                steps.append(
                    ToolCallRecord(
                        round_index=round_index,
                        tool=result.tool,
                        arguments=arguments,
                        ok=result.ok,
                        tables=result.tables,
                        summary=result.content[:400],
                    )
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result.content}
                )
        else:
            # for-else：循环正常跑完（用尽轮次）仍没有最终回答
            answer_text = self._finalize(messages, steps)
            truncated = True

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        tables: list[str] = []
        for step in steps:
            for table in step.tables:
                if table not in tables:
                    tables.append(table)

        return AgentAnswer(
            answer=answer_text,
            question=question,
            tables=tables,
            executed_sql=executed_sql,
            steps=steps,
            elapsed_ms=elapsed_ms,
            model=self.settings.llm_model,
            rounds=len({s.round_index for s in steps}),
            truncated=truncated,
        )

    # --------------------------------------------------------
    def _finalize(self, messages: list[dict[str, Any]], steps: list[ToolCallRecord]) -> str:
        """用尽轮次/超时后，强制模型基于已有信息作答。

        为什么要单独收尾而不是直接报错：
            模型可能已经查到了需要的数据，只是还在继续探索。
            直接失败会让用户白等一场；强制收尾能拿到"基于已查到的数据"的回答，
            同时在响应里标记 `truncated=true`，让用户知道这可能不是完整答案。

        收尾调用**不传 tools**：这样模型只能总结已有信息，不能再发起新查询，
        保证一定能结束。
        """
        messages.append(
            {
                "role": "user",
                "content": (
                    "已达到本次分析的调用次数上限。请**只根据上面已经查到的数据**"
                    "给出当前能得到的最佳回答；如果信息不足以回答，"
                    "请明确说明还缺什么，不要猜测。"
                ),
            }
        )
        try:
            response = create_completion(self.client, self.settings, messages, [])
            return response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - 收尾失败也要给出可读信息
            return (
                "已达到调用次数上限，且收尾总结失败："
                f"{type(exc).__name__}: {exc}。已执行的查询见 steps 字段。"
            )


def describe_tools() -> list[ToolSpec]:
    """暴露工具声明（供 /tools 接口展示"Agent 能做什么"）。"""
    return list(TOOL_SPECS)
