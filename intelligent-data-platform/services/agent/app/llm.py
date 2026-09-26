"""LLM 客户端（DeepSeek 官方 API，OpenAI 兼容协议）。

## 为什么用 openai SDK 而不是自研 HTTP 封装

DeepSeek 提供 OpenAI 兼容接口，官方 Tool Calls 示例本身就是
`from openai import OpenAI; client = OpenAI(base_url="https://api.deepseek.com")`。
用官方 SDK 能直接拿到 `tool_calls` 的类型化对象与重试逻辑，
比自己拼 multipart/JSON 更不容易在边界情况下出错。

## !! 思考模式默认关闭，这是有意的决定 !!

DeepSeek 的思考模式默认**开启**，而且官方文档明确写着：

> 携带了 `tools` 参数的请求，在后续所有请求中，必须完整回传
> `reasoning_content` 给 API —— 即使该轮模型未实际进行工具调用。
> 若未正确回传，API 会返回 400 报错。

也就是说，思考模式 + 工具调用会把"多轮上下文拼接"变成必须严格正确的操作，
而且每一轮的思维链都要随请求回传 —— token 消耗成倍增加。

对本项目（SQL 生成）来说，思考模式的收益有限、代价明确：
  - 收益：复杂多跳问题可能更准；
  - 代价：token 翻倍、必须回传 reasoning_content（漏了就 400）、
          同一问题两次回答可能生成不同 SQL（不利于复现与排错）。

因此默认 `LLM_THINKING=false`，走非思考模式：
上下文拼接简单、结果更可复现、成本约为思考模式的 1/3。
需要时用 `LLM_THINKING=true` 打开 —— **打开后由本模块统一负责回传
`reasoning_content`**，调用方不需要关心这个细节
（`_assistant_message` 就是为此存在的）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import Settings

if TYPE_CHECKING:  # 只为类型标注引入，运行时不导入
    from openai import OpenAI

__all__ = ["build_client", "create_completion", "assistant_message", "ToolSpec"]


class ToolSpec:
    """一个工具的声明（名字 + 描述 + JSON Schema）。

    为什么不用 OpenAI SDK 的 `pydantic_function_tool` 助手：
        那个助手要求把参数定义成 pydantic 模型，而我们的工具参数只有 1~2 个字段；
        直接写 schema 更短、更好读，也避免为"工具声明"再引入一层类型推导。
        工具**返回值**才是需要严格约束的部分，那部分我们用 dataclass 管。
    """

    __slots__ = ("name", "description", "parameters")

    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def build_client(settings: Settings) -> OpenAI:
    """构造 OpenAI 兼容客户端（指向 DeepSeek）。

    刻意不设置全局超时以外的重试策略：LLM 调用失败时我们更希望
    **把错误原样交给用户**，而不是悄悄重试三次再报一个模糊的失败
    （AGENTS.md 第 10.3 节「失败即失败」）。

    `openai` 包在这里**惰性导入**（而不是模块顶部）：
        这样 `/health`、`/tools`、`/prompt` 三个接口在没有装 openai、
        或没有配 Key 的环境里也能正常返回，便于部署后先做自检。
        只有在真正要调模型时才要求依赖存在 —— 并且失败信息直指原因。
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - 部署脚本会装好依赖
        raise RuntimeError(
            "缺少 openai 依赖。请在服务器执行： "
            "bash scripts/deploy-agent.sh"
        ) from exc

    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=1,
    )


def create_completion(
    client: OpenAI,
    settings: Settings,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> Any:
    """发起一次对话补全（带工具声明）。

    思考模式的开关通过 `extra_body` 传递（OpenAI 格式下 DeepSeek 要求如此）。
    关闭时不传该字段 —— 用服务端默认值，避免"传了 disabled 但服务端
    某个版本不认"这种版本差异。
    """
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "tools": tools,
        "max_tokens": settings.llm_max_tokens,
    }

    if settings.llm_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        # 思考模式不支持 temperature / presence_penalty / frequency_penalty：
        # 传了不报错但也不生效，干脆不传，避免"以为设置了其实没用"。
    else:
        kwargs["temperature"] = settings.temperature

    return client.chat.completions.create(**kwargs)


def assistant_message(response: Any) -> dict[str, Any]:
    """把一轮模型回复转成下一轮可回传的 assistant 消息。

    !! 为什么不能只取 content !!
        DeepSeek 文档明确：携带 tools 的请求，历史轮次的 `reasoning_content`
        必须回传给 API，否则返回 400。
        `response.choices[0].message` 已经带齐 content / reasoning_content /
        tool_calls，直接用它最不容易漏字段（官方示例也是这么做）。
        这里显式构造 dict 而不是直接 append SDK 对象，是为了让
        "回传了哪些字段"在日志与测试里可见、可断言。
    """
    message = response.choices[0].message
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        payload["reasoning_content"] = reasoning
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ]
    return payload
