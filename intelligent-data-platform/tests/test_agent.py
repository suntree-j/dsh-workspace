"""Agent 单元测试（不依赖 LLM、不依赖数据库）。

## 为什么要专门测 Agent 的"循环逻辑"

Agent 最容易出错的地方不是"调用 LLM"，而是**循环本身**：
    - 工具报错后有没有把错误交回模型，让它有机会重新规划？
    - 用尽轮次时会不会死循环？有没有一个必然终止的收尾路径？
    - 最终回答有没有带上"读了哪些表"这类可核对的痕迹？

这些都能在不调用真实 LLM 的前提下测出来：把 LLM 客户端换成一个
按剧本返回 tool_calls 的假对象即可。这样做的好处是**测试稳定且免费** ——
真实 LLM 的输出不确定，拿它做断言只会得到时灵时不灵的测试。

依据：AGENTS.md 第 8.2 节（单元测试不得依赖外部服务）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent import SYSTEM_PROMPT, Agent
from app.config import Settings, load_settings
from app.llm import ToolSpec, assistant_message
from app.tools import TOOL_SPECS, ToolBox, ToolResult

# 本模块全部是单元测试：不连数据库、不调 LLM、不需要容器。
# 必须显式打标，否则 `python -m pytest -m unit`（仓库约定的单元测试入口）选不到它们。
pytestmark = pytest.mark.unit

# ============================================================
# 测试替身
# ============================================================


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[_FakeToolCall] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _ScriptedCompletions:
    """按预设剧本逐次返回的假 completions 接口。"""

    def __init__(self, script: list[_FakeMessage]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self.script:
            # 剧本用尽：返回一个"最终回答"，模拟模型不再调工具
            return _FakeResponse(_FakeMessage(content="（剧本已用尽）"))
        return _FakeResponse(self.script.pop(0))


class _FakeClient:
    def __init__(self, script: list[_FakeMessage]) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _ScriptedCompletions(script)


def _settings(**overrides: Any) -> Settings:
    base = load_settings()
    return Settings(**{**base.__dict__, **overrides})


def _tool_call_message(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> _FakeMessage:
    return _FakeMessage(
        content=None,
        tool_calls=[_FakeToolCall(call_id, name, json.dumps(arguments, ensure_ascii=False))],
    )


class _RecordingToolBox(ToolBox):
    """记录调用、按脚本返回结果的假工具箱（不发起任何 HTTP）。"""

    def __init__(self, script: dict[str, ToolResult]) -> None:
        super().__init__(_settings())
        self.script = script
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        result = self.script.get(name)
        if result is None:
            return ToolResult(False, f"（脚本里没有 {name}）", tool=name, arguments=arguments)
        return result


# ============================================================
# 1. 配置
# ============================================================
def test_settings_defaults() -> None:
    settings = load_settings()
    assert settings.llm_model == "deepseek-flash", "默认模型应为 flash（成本与能力平衡）"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_thinking is False, "思考模式默认必须关闭（见 llm.py 说明）"
    assert settings.max_tool_rounds >= 1
    assert settings.query_limit >= 1
    assert settings.data_api_base.startswith("http")


def test_missing_api_key_is_not_a_startup_failure() -> None:
    """缺 Key 时服务仍可启动，只是 llm_configured=False。

    这条是行为契约：部署脚本据此先装服务、后配 Key。
    """
    settings = _settings(llm_api_key="")
    assert settings.llm_configured is False
    assert _settings(llm_api_key="sk-real").llm_configured is True


# ============================================================
# 2. 工具声明
# ============================================================
def test_tool_specs_are_minimal_and_well_formed() -> None:
    names = [spec.name for spec in TOOL_SPECS]
    assert names == ["metrics_lookup", "tables_lookup", "sql_query", "reconciliation"], (
        "工具集合是权限边界的一部分，增删都必须是有意为之"
    )

    for spec in TOOL_SPECS:
        schema = spec.as_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == spec.name
        assert schema["function"]["description"].strip(), f"{spec.name} 缺少描述"
        assert schema["function"]["parameters"]["type"] == "object"


def test_sql_query_tool_requires_sql_argument() -> None:
    spec = next(s for s in TOOL_SPECS if s.name == "sql_query")
    assert spec.parameters["required"] == ["sql"]


def test_tool_spec_rejects_unknown_tool() -> None:
    box = ToolBox(_settings())
    result = box.call("drop_everything", {})
    assert result.ok is False
    assert "没有名为" in result.content


# ============================================================
# 3. 系统提示词：三条铁律必须在
# ============================================================
@pytest.mark.parametrize(
    "keyword",
    ["只允许 SELECT", "禁止编造数据", "必须能说明来源", "metrics_lookup", "lakehouse_ads"],
)
def test_system_prompt_contains_hard_rules(keyword: str) -> None:
    assert keyword in SYSTEM_PROMPT, f"系统提示词缺少关键约束：{keyword}"


# ============================================================
# 4. LLM 消息拼接（思考模式回传 reasoning_content）
# ============================================================
def test_assistant_message_carries_tool_calls() -> None:
    response = _FakeResponse(
        _FakeMessage(content=None, tool_calls=[_FakeToolCall("call_9", "sql_query", '{"sql":"SELECT 1"}')])
    )
    payload = assistant_message(response)
    assert payload["role"] == "assistant"
    assert payload["tool_calls"][0]["id"] == "call_9"
    assert payload["tool_calls"][0]["function"]["name"] == "sql_query"


def test_assistant_message_keeps_reasoning_content_when_present() -> None:
    """思考模式下必须回传 reasoning_content，否则 DeepSeek 返回 400。

    这里锁定的是"有没有带上"这件事 —— 漏了字段就会在生产环境变成
    一个只在开启思考模式时才出现的 400 错误。
    """
    response = _FakeResponse(_FakeMessage(content="答案", reasoning_content="我的推理过程"))
    payload = assistant_message(response)
    assert payload["reasoning_content"] == "我的推理过程"
    # 非思考模式下该字段为空，不应出现（空字符串传回去同样可能触发校验）
    empty = assistant_message(_FakeResponse(_FakeMessage(content="答案")))
    assert "reasoning_content" not in empty


# ============================================================
# 5. Agent 循环
# ============================================================
def test_agent_returns_answer_after_tool_call() -> None:
    """一次工具调用后给出最终回答，并把来源记录下来。"""
    agent = Agent(_settings())
    agent._client = _FakeClient(
        [
            _tool_call_message("metrics_lookup", {"keyword": "gmv"}),
            _FakeMessage(content="GMV 是窗口内下单金额合计。"),
        ]
    )
    agent.toolbox = _RecordingToolBox(
        {
            "metrics_lookup": ToolResult(
                True, '{"metrics":[{"field":"gmv"}]}', tool="metrics_lookup", arguments={}
            )
        }
    )

    answer = agent.ask("GMV 怎么算？")

    assert answer.answer.startswith("GMV 是")
    assert answer.rounds == 1
    assert answer.truncated is False
    assert [step.tool for step in answer.steps] == ["metrics_lookup"]
    assert answer.steps[0].ok is True
    assert answer.model == "deepseek-flash"


def test_agent_records_tables_and_executed_sql() -> None:
    """来源可追溯：回答必须带上读了哪些表与实际执行的 SQL。"""
    agent = Agent(_settings())
    agent._client = _FakeClient(
        [
            _tool_call_message("sql_query", {"sql": "SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"}),
            _FakeMessage(content="总 GMV 为 51,890,375.77 元。"),
        ]
    )
    agent.toolbox = _RecordingToolBox(
        {
            "sql_query": ToolResult(
                True,
                '{"executed_sql":"SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m LIMIT 200","row_count":1}',
                tables=["ecommerce.ads_realtime_trade_1m"],
                tool="sql_query",
                arguments={},
            )
        }
    )

    answer = agent.ask("总 GMV 是多少？")

    assert answer.tables == ["ecommerce.ads_realtime_trade_1m"]
    assert answer.executed_sql == ["SELECT SUM(gmv) FROM ecommerce.ads_realtime_trade_1m"]
    assert "51,890,375.77" in answer.answer


def test_tool_failure_is_fed_back_and_agent_can_recover() -> None:
    """工具失败不能中断循环：错误必须回到模型，让它改写后重试。

    对应 AGENTS.md 第 10.1 节第 6 条「查询失败必须能够重新规划」。
    """
    agent = Agent(_settings())
    agent._client = _FakeClient(
        [
            _tool_call_message("sql_query", {"sql": "SELECT * FROM secret_table"}, call_id="c1"),
            _tool_call_message("sql_query", {"sql": "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m"}, call_id="c2"),
            _FakeMessage(content="共有 11459 个窗口。"),
        ]
    )

    class _FlakyToolBox(_RecordingToolBox):
        def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
            self.calls.append((name, arguments))
            if "secret_table" in str(arguments.get("sql", "")):
                return ToolResult(
                    False,
                    "调用失败（HTTP 400，code=TABLE_NOT_ALLOWED）：查询了未被授权的表",
                    tool=name,
                    arguments=arguments,
                )
            return ToolResult(
                True,
                '{"row_count":1,"rows":[{"c":11459}]}',
                tables=["ecommerce.ads_realtime_trade_1m"],
                tool=name,
                arguments=arguments,
            )

    toolbox = _FlakyToolBox({})
    agent.toolbox = toolbox

    answer = agent.ask("有多少个窗口？")

    assert len(toolbox.calls) == 2, "第一次失败后应当再试一次"
    assert answer.steps[0].ok is False
    assert answer.steps[1].ok is True
    assert "11459" in answer.answer
    # 失败那一步不应污染来源列表
    assert answer.tables == ["ecommerce.ads_realtime_trade_1m"]


def test_agent_terminates_when_rounds_exhausted() -> None:
    """模型一直调工具时必须终止，并强制收尾（不能死循环）。

    最后那次收尾调用**不能带 tools**，否则模型还能继续发起工具调用。
    """
    limit = 3
    agent = Agent(_settings(max_tool_rounds=limit))
    # 每一轮都返回工具调用；最后收尾时会返回 content
    script = [_tool_call_message("reconciliation", {}, call_id=f"c{i}") for i in range(limit)]
    script.append(_FakeMessage(content="根据对账结论，数据可信。"))
    fake = _FakeClient(script)
    agent._client = fake
    agent.toolbox = _RecordingToolBox(
        {"reconciliation": ToolResult(True, '{"mismatched_windows":0}', tool="reconciliation", arguments={})}
    )

    answer = agent.ask("数据可信吗？")

    assert answer.truncated is True, "用尽轮次必须标记 truncated，让用户知道答案可能不完整"
    assert answer.answer
    # 收尾那次调用不应携带 tools
    assert fake.chat.completions.calls[-1].get("tools") in ([], None)


def test_bad_tool_arguments_do_not_crash_the_loop() -> None:
    """模型给出一段不是 JSON 的参数时，必须变成可读反馈而不是异常。"""
    agent = Agent(_settings())
    bad_call = _FakeMessage(content=None, tool_calls=[_FakeToolCall("c1", "sql_query", "not-json")])
    agent._client = _FakeClient([bad_call, _FakeMessage(content="我无法解析参数，请重试。")])
    agent.toolbox = _RecordingToolBox({})

    answer = agent.ask("随便问")

    assert answer.steps[0].ok is False
    assert "JSON" in answer.steps[0].summary
    assert answer.answer


def test_agent_evidence_is_json_serialisable() -> None:
    """整个过程必须可序列化（要经 HTTP 返回给看板）。"""
    agent = Agent(_settings())
    agent._client = _FakeClient([_FakeMessage(content="无需查询即可回答。")])
    answer = agent.ask("你好")
    payload = json.dumps(answer.as_dict(), ensure_ascii=False)
    assert "question" in payload
    assert answer.tables == []


# ============================================================
# 6. 工具结果 → LLM 文本的截断保护
# ============================================================
def test_dump_truncates_and_says_so() -> None:
    from app.tools import _dump

    big = [{"i": i, "text": "x" * 200} for i in range(500)]
    text = _dump(big, max_chars=1000)
    assert len(text) < 1400
    assert "已截断" in text, "截断必须显式告知，否则模型会以为看到了全部数据"
