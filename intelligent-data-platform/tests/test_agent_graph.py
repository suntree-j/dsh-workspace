"""图式 Agent 单元测试（Sprint 8，不依赖 LLM、不依赖数据库、不依赖 langgraph 的网络侧）。

## 这一组测试真正要锁住的是什么

图最容易出错的地方不是"调没调 LLM"，而是**控制流**：

    - 校验不通过时，到底有没有走到 reflect？重规划之后有没有真的改变做法？
    - 重试额度用尽时，会不会停？停下来时有没有把"没解决"如实标出来？
    - 工具报错时，错误有没有原样回到上下文（而不是被吞掉、变成"查到了 0"）？
    - 响应里有没有留下可核对的证据（plan / validation / retries / executed_sql）？

这些都能在不调真实 LLM 的前提下测出来：把客户端换成一个按剧本返回的假对象，
把工具箱换成记录调用的假实现。真实 LLM 输出不确定，拿它做断言只会得到
时灵时不灵的测试（AGENTS.md 第 8.2 节：单元测试不得依赖外部服务）。

## 为什么不复用 tests/test_agent.py 里的测试替身

两个测试文件服务于**不同的实现**（单轮循环 vs 图）。替身共用会让
"改一个实现的测试"牵动另一个实现的测试，最后没人敢动它们。
各留一份短的、自解释的替身，代价是几十行重复，收益是两个文件都能独立演进。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent import Agent
from app.config import Settings, load_settings
from app.graph import GraphAgent, build_graph
from app.tools import TOOL_SPECS, ToolBox, ToolResult

pytestmark = pytest.mark.unit


# ============================================================
# 测试替身
# ============================================================
class _Function:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, arguments)


class _Message:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[_ToolCall] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


class _Choice:
    def __init__(self, message: _Message) -> None:
        self.message = message


class _Response:
    def __init__(self, message: _Message) -> None:
        self.choices = [_Choice(message)]


class _ScriptedCompletions:
    """按剧本逐次返回；剧本用尽后返回一句收尾文本。"""

    def __init__(self, script: list[_Message]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if not self.script:
            return _Response(_Message(content="（剧本已用尽）"))
        return _Response(self.script.pop(0))


class _FakeClient:
    def __init__(self, script: list[_Message]) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _ScriptedCompletions(script)


def _plan_message(steps: list[dict[str, str]], reasoning: str = "先查交易表") -> _Message:
    return _Message(
        content=None,
        tool_calls=[
            _ToolCall(
                "plan_1",
                "propose_sql",
                json.dumps({"reasoning": reasoning, "steps": steps}, ensure_ascii=False),
            )
        ],
    )


def _sql_result(ok: bool, content: str, tables: list[str] | None = None) -> ToolResult:
    return ToolResult(ok, content, tables=tables or [], tool="sql_query")


class _ScriptedToolBox(ToolBox):
    """按"第几次调用"返回预设结果的假工具箱（不发起任何 HTTP）。"""

    def __init__(self, results: list[ToolResult]) -> None:
        super().__init__(_settings())
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        if not self.results:
            return _sql_result(False, "（脚本里没有更多结果）")
        return self.results.pop(0)


class _OfflineRetriever:
    """离线检索器：关掉"经接口取表结构"，保证单元测试零外部依赖。"""

    def __init__(self, settings: Settings) -> None:
        from app.retrieval import build_retriever

        self._inner = build_retriever(settings)
        self._inner.include_live_tables = False

    def retrieve(self, query: str, top_k: int | None = None):
        return self._inner.retrieve(query, top_k)

    def describe(self) -> dict[str, Any]:
        return self._inner.describe()


def _settings(**overrides: Any) -> Settings:
    base = load_settings()
    return Settings(**{**base.__dict__, **overrides})


def _agent(script: list[_Message], results: list[ToolResult], **overrides: Any) -> tuple[GraphAgent, _ScriptedToolBox, _FakeClient]:
    settings = _settings(**overrides)
    agent = GraphAgent(settings)
    agent._client = _FakeClient(script)
    agent.retriever = _OfflineRetriever(settings)
    toolbox = _ScriptedToolBox(results)
    toolbox.settings = settings
    agent.toolbox = toolbox
    return agent, toolbox, agent._client


# ============================================================
# 1. 图的结构（编排本身是可审查的）
# ============================================================
def test_graph_declares_expected_nodes_and_edges() -> None:
    describe = GraphAgent(_settings()).describe()
    names = [node["name"] for node in describe["nodes"]]
    assert names == ["retrieve", "plan", "execute", "validate", "reflect", "summarize"]

    # 边可以是 (起点, 终点) 或 (起点, 终点, 条件说明)
    edges = [tuple(edge[:2]) for edge in describe["edges"]]
    assert ("retrieve", "plan") in edges
    assert ("plan", "execute") in edges
    assert ("execute", "validate") in edges
    # reflect 必须回到 plan（重新规划），而不是回到 execute（重放同一条错 SQL）
    assert ("reflect", "plan") in edges
    assert ("summarize", "<END>") in edges
    # 规划失败要短路到 summarize，否则用户看到的是"空回答"而不是根因
    assert ("plan", "summarize") in edges
    assert describe["limits"]["max_retries"] >= 1


def test_graph_compiles_with_langgraph() -> None:
    """图必须真的能被 LangGraph 编译（否则 /ask 会整体不可用）。"""
    compiled = GraphAgent(_settings()).build()
    assert hasattr(compiled, "invoke"), "编译产物必须提供 invoke"


def test_build_graph_returns_callable() -> None:
    assert callable(build_graph(_settings()))


def test_planned_retrieve_and_reasoning_tools_are_declared() -> None:
    names = [spec.name for spec in TOOL_SPECS]
    assert "retrieve_docs" in names, "Sprint 9 的检索能力必须出现在能力面上"
    assert "propose_sql" in names, "Sprint 8 的规划协议必须可审查"
    # Sprint 7 的四个取数/查询工具一个都不能少（回归）
    for legacy in ("metrics_lookup", "tables_lookup", "sql_query", "reconciliation"):
        assert legacy in names


# ============================================================
# 2. 正常路径：规划 → 取数 → 校验通过 → 汇总
# ============================================================
def test_happy_path_produces_plan_evidence_and_answer() -> None:
    agent, toolbox, fake = _agent(
        script=[
            _plan_message(
                [
                    {
                        "purpose": "最近 7 天 GMV",
                        "target_table": "lakehouse_ads.ads_batch_trade_1d",
                        "sql": "SELECT dt, SUM(gmv) AS gmv FROM lakehouse_ads.ads_batch_trade_1d GROUP BY dt",
                    }
                ]
            ),
            _Message(content="最近 7 天 GMV 合计 4,222,722.99 元。依据 sql/metadata/metrics.md:33"),
        ],
        results=[
            _sql_result(
                True,
                '{"executed_sql":"SELECT dt, SUM(gmv) ... LIMIT 200","row_count":7,"rows":[{"dt":"2026-09-20"}]}',
                tables=["lakehouse_ads.ads_batch_trade_1d"],
            )
        ],
    )

    answer = agent.ask("最近一周每天的 GMV 是多少？")
    payload = answer.as_dict()

    assert payload["retries"] == 0, "一次就过就不该有重试"
    assert payload["validation"]["ok"] is True
    assert payload["validation"]["blocking_issues"] == []
    assert payload["plan"]["steps"], "规划必须是显式产物"
    assert payload["plan"]["steps"][0]["target_table"] == "lakehouse_ads.ads_batch_trade_1d"
    assert answer.tables == ["lakehouse_ads.ads_batch_trade_1d"]
    assert answer.executed_sql == [
        "SELECT dt, SUM(gmv) AS gmv FROM lakehouse_ads.ads_batch_trade_1d GROUP BY dt"
    ]
    assert payload["graph"]["path"] == ["retrieve", "plan", "execute", "validate", "summarize"]
    assert "4,222,722.99" in answer.answer

    # 汇总那次调用不能携带 tools：否则模型能继续发起调用，图停不下来
    assert fake.chat.completions.calls[-1].get("tools") in ([], None)
    assert toolbox.calls and toolbox.calls[0][0] == "sql_query"


def test_answer_carries_retrieved_documents_with_citations() -> None:
    """来源可追溯：docs 必须带来源文件与行号（Sprint 9 与 Sprint 8 的接缝）。"""
    agent, _, _ = _agent(
        script=[
            _plan_message([{"purpose": "查 GMV", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="总额见下方查询结果。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"gmv":"1.00"}]}')],
    )
    answer = agent.ask("GMV 怎么算")
    assert answer.docs, "检索结果必须随回答返回"
    top = answer.docs[0]
    assert top["source"] == "sql/metadata/metrics.md"
    assert isinstance(top["line"], int) and top["line"] > 0
    assert top["doc_id"] == "metric:gmv"


# ============================================================
# 3. 反思重试：真实失败 → 重新规划 → 成功
# ============================================================
def test_failed_query_triggers_reflect_and_replans() -> None:
    """查询失败必须触发 reflect，并把**真实错误**交回规划器后再试。"""
    agent, toolbox, _ = _agent(
        script=[
            _plan_message(
                [{"purpose": "查用户画像", "target_table": "ecommerce.dwd_user_profile", "sql": "SELECT * FROM ecommerce.dwd_user_profile"}]
            ),
            _plan_message(
                [{"purpose": "改查订单明细", "target_table": "ecommerce.dwd_trade_order_detail", "sql": "SELECT COUNT(*) AS c FROM ecommerce.dwd_trade_order_detail"}],
                reasoning="上一版查了未授权的表，改为订单明细",
            ),
            _Message(content="该表未授权，已改为订单明细：共 6000 单。"),
        ],
        results=[
            _sql_result(
                False,
                "调用失败（HTTP 400，code=TABLE_NOT_ALLOWED）：查询了未被授权的表；详情：未授权：dwd_user_profile",
            ),
            _sql_result(True, '{"row_count":1,"rows":[{"c":6000}]}', tables=["ecommerce.dwd_trade_order_detail"]),
        ],
    )

    answer = agent.ask("有多少用户画像？")
    payload = answer.as_dict()

    # 重试是**一等公民**：次数可见，且原因真实
    assert payload["retries"] == 1
    assert payload["graph"]["path"] == [
        "retrieve", "plan", "execute", "validate", "reflect",
        "plan", "execute", "validate", "summarize",
    ], "反思之后必须回到 plan（重新规划），而不是回到 execute（重放）"

    # 第一次的真实错误必须被记录下来，且没有被改写
    assert answer.steps[0]["ok"] is False
    assert "TABLE_NOT_ALLOWED" in answer.steps[0]["summary"]
    assert answer.steps[1]["ok"] is True
    assert payload["validation"]["ok"] is True

    # 失败的查询不能污染 executed_sql 与 tables（那是"真的跑成功过"的证据）
    assert answer.executed_sql == ["SELECT COUNT(*) AS c FROM ecommerce.dwd_trade_order_detail"]
    assert answer.tables == ["ecommerce.dwd_trade_order_detail"]
    assert len(toolbox.calls) == 2, "失败后必须真的再试一次"


def test_reflect_receives_error_text_in_planner_context() -> None:
    """错误必须真的进到规划器的上下文里，否则"重新规划"只是重放。"""
    agent, _, fake = _agent(
        script=[
            _plan_message([{"purpose": "查未授权表", "target_table": "ecommerce.secret", "sql": "SELECT * FROM ecommerce.secret"}]),
            _plan_message([{"purpose": "改查合法表", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="已改查合法表。"),
        ],
        results=[
            _sql_result(False, "调用失败（HTTP 400，code=TABLE_NOT_ALLOWED）：查询了未被授权的表"),
            _sql_result(True, '{"row_count":1,"rows":[{"gmv":"2.00"}]}', tables=["ecommerce.ads_realtime_trade_1m"]),
        ],
    )
    agent.ask("随便问一个")

    # 第 2 次 plan 的入参里必须含"上一次的失败反馈"与错误原文
    planner_calls = [call for call in fake.chat.completions.calls if call.get("tools")]
    assert len(planner_calls) >= 2, "应当发生两次规划调用"
    second_context = json.dumps(planner_calls[1]["messages"], ensure_ascii=False)
    assert "失败反馈" in second_context
    assert "TABLE_NOT_ALLOWED" in second_context


# ============================================================
# 4. 终止性：额度用尽必须停，并且如实标注
# ============================================================
def test_retry_budget_is_bounded_and_reported() -> None:
    """一直失败时必须停下来，并标记 truncated（不能让用户以为答案是完整的）。"""
    failures = _sql_result(False, "调用失败（HTTP 400，code=TABLE_NOT_ALLOWED）：查询了未被授权的表")
    agent, toolbox, _ = _agent(
        script=[
            _plan_message([{"purpose": f"第 {i} 次尝试", "target_table": "ecommerce.nope", "sql": f"SELECT {i} FROM ecommerce.nope"}])
            for i in range(1, 4)
        ]
        + [_Message(content="这张表查不到，我试了 3 次都被拒绝，无法回答。")],
        results=[failures, failures, failures],
        max_retries=2,
    )

    answer = agent.ask("查一个不存在的表")
    payload = answer.as_dict()

    assert payload["retries"] == 2, f"重试额度为 2，实际 {payload['retries']}"
    assert len(toolbox.calls) == 3, "重试 2 次 ⇒ 共 3 次执行，不能再多"
    assert payload["validation"]["ok"] is False, "未解决的失败必须如实标为不通过"
    assert answer.truncated is True
    assert answer.executed_sql == [], "全部失败 ⇒ 没有任何真实执行成功的语句"
    assert "查不到" in answer.answer
    # 图路径里应当看到两次 reflect
    assert payload["graph"]["path"].count("reflect") == 2


def test_tool_round_limit_stops_execution() -> None:
    """工具轮次上限也必须生效（否则一个多步计划能无限跑）。"""
    agent, toolbox, _ = _agent(
        script=[
            _plan_message(
                [
                    {"purpose": f"步骤 {i}", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": f"SELECT {i} FROM ecommerce.ads_realtime_trade_1m"}
                    for i in range(1, 6)
                ]
            ),
            _Message(content="只完成了前两步。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"x":1}]}') for _ in range(5)],
        max_tool_rounds=2,
        max_retries=0,
    )

    answer = agent.ask("一次要求太多")
    assert len(toolbox.calls) == 2, "工具轮次上限为 2 ⇒ 只执行 2 条"
    assert any("上限" in issue for issue in answer.issues), "被截断的原因必须写进 issues"
    assert answer.truncated is True


def test_recursion_limit_is_not_reached_in_normal_use() -> None:
    """一次提问的节点访问次数远小于 recursion_limit(25)，不会撞上 LangGraph 的保护。"""
    agent, _, _ = _agent(
        script=[
            _plan_message([{"purpose": "查", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="答案。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"gmv":"1"}]}')],
    )
    answer = agent.ask("GMV")
    assert len(answer.graph_trace) <= 10


# ============================================================
# 5. 校验节点：空结果 / 截断 / NULL 要变成可读的警告
# ============================================================
def test_validate_flags_empty_result_as_warning() -> None:
    """HTTP 200 + 0 行 ≠ "这个指标是 0"。必须能被区分。"""
    agent, _, _ = _agent(
        script=[
            _plan_message([{"purpose": "查某天 GMV", "target_table": "lakehouse_ads.ads_batch_trade_1d", "sql": "SELECT SUM(gmv) FROM lakehouse_ads.ads_batch_trade_1d WHERE dt='1900-01-01'"}]),
            _Message(content="该日期没有数据。"),
        ],
        results=[_sql_result(True, '{"executed_sql":"...","row_count":0,"rows":[]}')],
    )
    payload = agent.ask("1900 年 1 月 1 日的 GMV").as_dict()

    warnings = " ".join(payload["validation"]["warnings"])
    assert "0 行" in warnings
    assert payload["validation"]["ok"] is True, "空结果是警告而非阻塞：可以照实回答"
    assert payload["retries"] == 0, "不该为空结果浪费重试额度"


def test_validate_flags_truncation_and_null() -> None:
    agent, _, _ = _agent(
        script=[
            _plan_message([{"purpose": "查全部明细", "target_table": "ecommerce.dwd_trade_order_detail", "sql": "SELECT order_id FROM ecommerce.dwd_trade_order_detail"}]),
            _Message(content="结果被截断。"),
        ],
        results=[
            _sql_result(
                True,
                '{"row_count":200,"rows":[{"click_rate":null}],"...(结果过长已截断，原始长度 9000 字符...)":0}',
            )
        ],
    )
    payload = agent.ask("所有订单").as_dict()
    warnings = " ".join(payload["validation"]["warnings"])
    assert "截断" in warnings
    assert "NULL" in warnings


def test_validate_reports_when_no_sql_was_produced() -> None:
    """模型没给出可执行 SQL 时，必须是一条可读的失败原因，而不是空手而归。"""
    agent, _, _ = _agent(
        script=[
            _Message(content="这个问题我拿不准该查哪张表。"),  # 既没调工具也没 JSON
            _Message(content="语料里没有相关口径，我无法确定，需要你补充。"),
        ],
        results=[],
        max_retries=0,
    )
    answer = agent.ask("随便问")
    assert answer.plan_steps == []
    assert any("没有任何查询被执行" in issue or "没有给出 SQL" in issue or "未产出" in issue for issue in answer.issues)
    assert answer.truncated is True
    assert answer.executed_sql == []


# ============================================================
# 6. 规划解析：结构化优先，文本 JSON 兜底，坏输入不崩
# ============================================================
def test_plan_parsed_from_json_in_content_when_no_tool_call() -> None:
    """模型偶尔把计划写在 content 里；这不能变成"没有计划"的静默失败。"""
    agent, toolbox, _ = _agent(
        script=[
            _Message(
                content='```json\n{"reasoning":"先查天表","steps":[{"purpose":"日 GMV","target_table":"lakehouse_ads.ads_batch_trade_1d","sql":"SELECT SUM(gmv) FROM lakehouse_ads.ads_batch_trade_1d"}]}\n```'
            ),
            _Message(content="答案。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"g":1}]}', tables=["lakehouse_ads.ads_batch_trade_1d"])],
    )
    answer = agent.ask("最近一周 GMV")
    assert answer.plan_steps, "文本 JSON 也必须能被解析成规划"
    assert toolbox.calls, "解析出计划后必须真的执行"


def test_plan_with_empty_steps_does_not_crash() -> None:
    agent, toolbox, _ = _agent(
        script=[
            _plan_message([], reasoning="问题与数据无关，无需查询"),
            _Message(content="这个问题不需要查数据。"),
        ],
        results=[],
        max_retries=0,
    )
    answer = agent.ask("你好")
    assert toolbox.calls == []
    assert answer.answer


def test_bad_plan_arguments_do_not_crash_the_graph() -> None:
    bad = _Message(content=None, tool_calls=[_ToolCall("p", "propose_sql", "not-json")])
    agent, _, _ = _agent(script=[bad, _Message(content="我无法解析自己的计划。")], results=[], max_retries=0)
    answer = agent.ask("随便问")
    assert answer.plan_steps == []
    assert answer.answer


def test_steps_without_sql_are_dropped() -> None:
    agent, toolbox, _ = _agent(
        script=[
            _plan_message([{"purpose": "想查点什么", "target_table": "x"}]),
            _Message(content="没有可执行的语句。"),
        ],
        results=[],
        max_retries=0,
    )
    answer = agent.ask("随便问")
    assert answer.plan_steps == [], "没有 sql 的步骤必须被丢弃，不能拿去执行"
    assert toolbox.calls == []


# ============================================================
# 7. 失败即失败：不伪造数据
# ============================================================
def test_summarizer_failure_returns_readable_error_not_fake_data() -> None:
    """收尾调用炸了，也必须给出真实错误，而不是编一个答案。"""
    agent, _, _ = _agent(
        script=[
            _plan_message([{"purpose": "查", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"gmv":"1"}]}')],
    )

    def boom(**_: Any) -> Any:
        raise RuntimeError("网络中断")

    agent._client.chat.completions.create = boom  # type: ignore[method-assign]
    payload = agent.ask("GMV").as_dict()
    assert "汇总阶段调用模型失败" in payload["answer"]
    assert "网络中断" in payload["answer"]


def test_planner_failure_is_recorded_as_blocking_issue() -> None:
    """规划阶段炸了必须给出**根因**，而不是一个空回答或一句通用失败。"""
    agent, _, _ = _agent(script=[], results=[], max_retries=0)

    def boom(**_: Any) -> Any:
        raise RuntimeError("限流")

    agent._client.chat.completions.create = boom  # type: ignore[method-assign]
    answer = agent.ask("GMV")
    assert any("规划阶段调用模型失败" in issue for issue in answer.issues)
    assert answer.truncated is True
    # 短路到 summarize：不该出现"没有任何查询被执行"这类盖住根因的通用信息
    assert answer.graph_trace and [item["node"] for item in answer.graph_trace] == [
        "retrieve", "plan", "summarize",
    ]


# ============================================================
# 8. LLM 协议正确性（两条实测踩坑的回归锁）
# ============================================================
def test_first_round_planner_messages_are_minimal() -> None:
    """首轮规划上下文只有一条 system + 一条 user（问题 + 检索结果）。"""
    agent, _, fake = _agent(
        script=[
            _plan_message([{"purpose": "查", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="答案。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"gmv":"1"}]}')],
    )
    agent.ask("GMV 怎么算")

    planner_calls = [call for call in fake.chat.completions.calls if call.get("tools")]
    assert len(planner_calls) == 1
    roles = [message["role"] for message in planner_calls[0]["messages"]]
    assert roles == ["system", "user"], f"首轮规划上下文应当最小，实际 {roles}"


def test_replan_context_carries_previous_plan_and_error_only() -> None:
    """重规划必须拿到"上一版计划 + 真实错误"，且**不能掺入上一轮的查询结果**。

    这是两条实测踩坑的回归锁：

    1. 只有错误、没有上一版计划时，模型会把同一条错 SQL 原样再提一遍
       （实测同一张不存在的表/列被查了两次）—— 所以上一版计划必须在。
    2. 把上一轮的查询结果留在上下文里时，模型看到"已经有数了"，
       更不会改变做法 —— 所以旧结果必须不在。
    """
    agent, _, fake = _agent(
        script=[
            _plan_message([{"purpose": "查不存在的东西", "target_table": "ecommerce.nope", "sql": "SELECT a FROM ecommerce.nope"}]),
            _plan_message([{"purpose": "改查合法表", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="改查成功。"),
        ],
        results=[
            _sql_result(False, "调用失败（HTTP 400，code=TABLE_NOT_ALLOWED）：查询了未被授权的表"),
            _sql_result(True, '{"row_count":1,"rows":[{"gmv":"9.99"}],"executed_sql":"SELECT SUM(gmv) ..."}'),
        ],
    )
    agent.ask("随便问一个")

    planner_calls = [call for call in fake.chat.completions.calls if call.get("tools")]
    assert len(planner_calls) == 2, "应当发生两次规划调用"
    second = json.dumps(planner_calls[1]["messages"], ensure_ascii=False)

    # (1) 上一版计划与真实错误都在
    assert "你上一版提交的计划" in second
    assert "SELECT a FROM ecommerce.nope" in second
    assert "TABLE_NOT_ALLOWED" in second
    # (2) 上一轮的**查询结果**不能在（否则模型会以为已经有数据）
    assert "9.99" not in second
    assert "row_count" not in second

    # 重规划轮次的上下文形状：上一轮的 system + 问题 + 计划 + 受理确认，再接本轮问题。
    # 注意它**不含任何查询结果**（那是被刻意排除的东西）。
    roles = [message["role"] for message in planner_calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"], f"实际 {roles}"
    # 整条上下文里不能出现"结果"类内容
    second_all = json.dumps(planner_calls[1]["messages"], ensure_ascii=False)
    assert "真实执行结果" not in second_all


def test_every_tool_call_is_answered_by_a_tool_message() -> None:
    """带 tool_calls 的 assistant 消息后面必须跟齐 tool 消息。

    !! 实测踩坑（P0）!!
        DeepSeek 与 OpenAI 同协议：assistant(tool_calls) 之后缺少对应的
        tool 消息时，**整个请求**被拒：
            "An assistant message with 'tool_calls' must be followed by tool
             messages responding to each 'tool_call_id'"
        后果极具误导性：规划全部成功、SQL 也真的执行了，
        但下一轮（汇总）直接 400，用户看到的是"汇总阶段调用模型失败"。
        因此这里逐条检查发出去的每一次请求。
    """
    agent, _, fake = _agent(
        script=[
            _plan_message([{"purpose": "查", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="答案。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"gmv":"1"}]}')],
    )
    agent.ask("GMV")

    for index, call in enumerate(fake.chat.completions.calls):
        messages = call["messages"]
        for position, message in enumerate(messages):
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            expected = {item["id"] for item in message["tool_calls"]}
            following = messages[position + 1 : position + 1 + len(expected)]
            answered = {
                item.get("tool_call_id") for item in following if item.get("role") == "tool"
            }
            assert expected <= answered, (
                f"第 {index} 次请求的第 {position} 条 assistant(tool_calls) 没有被回应齐："
                f"期望 {expected}，实际 {answered}"
            )


def test_summarizer_receives_real_results_and_no_dangling_tool_messages() -> None:
    """汇总上下文 = 系统 + 问题 + 规划 + **真实结果**，且不含悬空的 tool 消息。"""
    agent, _, fake = _agent(
        script=[
            _plan_message([{"purpose": "查总 GMV", "target_table": "ecommerce.ads_realtime_trade_1m", "sql": "SELECT SUM(gmv) AS g FROM ecommerce.ads_realtime_trade_1m"}]),
            _Message(content="总额 51,890,375.77 元。"),
        ],
        results=[_sql_result(True, '{"row_count":1,"rows":[{"g":"51890375.77"}],"executed_sql":"SELECT SUM(gmv) AS g ... LIMIT 200"}', tables=["ecommerce.ads_realtime_trade_1m"])],
    )
    answer = agent.ask("总 GMV")

    summary_call = fake.chat.completions.calls[-1]
    assert summary_call.get("tools") in ([], None), "收尾调用不能携带 tools"
    messages = summary_call["messages"]
    assert messages[0]["role"] == "system"
    # 不允许出现 role=tool（没有对应的 assistant(tool_calls) 就是悬空消息）
    assert all(message["role"] != "tool" for message in messages), "汇总上下文里不应有 tool 消息"
    assert all(not message.get("tool_calls") for message in messages), "汇总上下文里不应有 tool_calls"

    joined = json.dumps(messages, ensure_ascii=False)
    assert "51890375.77" in joined, "真实查询结果必须进入汇总上下文"
    assert "# 真实执行结果" in joined
    assert "# 本次执行的规划" in joined
    assert answer.answer


def test_summarizer_is_told_when_nothing_succeeded() -> None:
    """一次都没成功时，必须明确告诉汇总器"你没有任何数字可用"。"""
    agent, _, fake = _agent(
        script=[
            _plan_message([{"purpose": "查不存在的表", "target_table": "ecommerce.nope", "sql": "SELECT a FROM ecommerce.nope"}]),
            _Message(content="查不到。"),
        ],
        results=[_sql_result(False, "调用失败（HTTP 400，code=TABLE_NOT_ALLOWED）")],
        max_retries=0,
    )
    agent.ask("随便问")

    joined = json.dumps(fake.chat.completions.calls[-1]["messages"], ensure_ascii=False)
    assert "没有任何查询成功执行" in joined
    assert "无法给出任何数字" in joined
    assert "# 真实执行结果" in joined
    # 失败明细也必须进上下文，否则模型没法解释"为什么查不到"
    assert "TABLE_NOT_ALLOWED" in joined


# ============================================================
# 9. 与 Sprint 7 的关系：回退路径仍可用
# ============================================================
def test_single_loop_agent_still_available_as_fallback() -> None:
    """`AGENT_GRAPH_ENABLED=false` 的回退路径必须仍然可用（排障基线）。"""
    agent = Agent(_settings())
    agent._client = _FakeClient([_Message(content="无需查询即可回答。")])
    answer = agent.ask("你好")
    assert answer.answer == "无需查询即可回答。"
    assert answer.truncated is False


def test_graph_enabled_and_retrieval_flags_default_on() -> None:
    settings = load_settings()
    assert settings.graph_enabled is True, "默认走图（Sprint 8 的交付形态）"
    assert settings.retrieval_enabled is True, "默认启用检索（Sprint 9 的交付形态）"
    assert settings.max_retries >= 1
    assert settings.retrieval_top_k >= 1
