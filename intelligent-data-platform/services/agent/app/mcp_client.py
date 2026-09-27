"""经 MCP 取数的客户端（Sprint 10）。

## 这个模块解决什么

    Sprint 7~9 的取数路径是 `ToolBox` → `urllib` → `POST /data/api/query`。
    Sprint 10 增加**第二条**取数路径：`ToolBox` → **MCP** → MCP 服务 →
    `POST /data/api/query`。两条路径的终点、守卫、只读账号完全相同，
    差别只在"谁来发起这次 HTTP"。

    ```text
    路径 A（直连）  Agent ── HTTP ─────────────────────────► 只读数据服务 ──► Doris
    路径 B（MCP）   Agent ── MCP(streamable-http) ──► MCP 服务 ── HTTP ──► 只读数据服务 ──► Doris
                                                                  └─ 无凭据
    ```

    MCP 在这里的价值不是"多一跳"，而是**能力面可枚举**：
    MCP 服务器声明的工具就是它能做的一切，客户端可以 `list_tools()` 把它读出来
    并交给安全审查（`/mcp` 接口就是这么做的）。直连 HTTP 做不到这一点 ——
    那时"Agent 能做什么"只能靠读 Agent 的源码。

## 为什么用「后台事件循环 + 长连接」而不是每次调用新建连接

    官方 SDK 的 `Client` 是**异步**的，而 Agent 的工具层是同步的。
    三种桥接方式的取舍：

    | 方式 | 问题 |
    | --- | --- |
    | 每次调用 `asyncio.run(...)` | 每个工具调用都要重新握手一次 MCP（initialize + tools/list），代价高且会丢掉会话 |
    | 在请求处理线程里起事件循环 | uvicorn 的工作线程里再嵌一层循环，信号与取消语义会打架 |
    | **后台守护线程持有长连接**（本实现） | 需要一个线程与一把锁，但语义最干净：连接生命周期与请求解耦 |

    这与 MCP 的设计意图一致：**MCP 会话是长连接**，streamable-http 也是
    围绕会话与事件流设计的，而不是"一问一挂"的 RPC。

## 失败即失败

    MCP 不可用时，工具调用**不会**悄悄回退到直连 HTTP。
    理由：`AGENTS.md` §10.3「失败即失败」。若把 MCP 不可达变成
    "那就直连吧"，那么"MCP 路径到底有没有在工作"将永远无法验收 ——
    验收脚本会全绿，而实际上一次 MCP 调用都没发生。
    排障时把 `AGENT_DATA_PATH=mcp` 改回 `http` 是**显式**动作，不是隐式兜底。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from .config import Settings

__all__ = ["McpToolClient", "McpUnavailable", "McpToolError"]

log = logging.getLogger("agent.mcp")


class McpUnavailable(RuntimeError):
    """MCP 服务不可达（连接失败、握手失败、调用超时）。"""


class McpToolError(RuntimeError):
    """MCP 工具返回了业务错误（例如 SQL 被守卫拒绝）。"""

    def __init__(self, tool: str, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.tool = tool
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class _Session:
    """后台线程里活着的那个 MCP 会话。"""

    loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    ready: concurrent.futures.Future[Any]
    client: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class McpToolClient:
    """同步门面：`call_tool(name, arguments) -> dict`。

    线程安全：同一个实例可以被多个请求线程共用（内部一把锁串行化调用）。
    MCP 会话本身不保证并发安全，串行化是**正确性**要求，不是性能取舍：
    把一批并发请求丢进同一条会话的事件流里，出错时的定位成本远高于省下的那点延迟。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: _Session | None = None
        self._lock = threading.Lock()
        # 连接失败后不再无限重试：记住最后一次失败原因，便于 /health 如实上报
        self.last_error: str = ""

    # --------------------------------------------------------
    # 后台线程
    # --------------------------------------------------------
    def _ensure_session(self) -> _Session:
        with self._lock:
            if self._session is not None:
                return self._session

            loop = asyncio.new_event_loop()
            ready: concurrent.futures.Future[Any] = concurrent.futures.Future()

            def _runner() -> None:
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._connect_and_serve(ready))
                except Exception as exc:  # noqa: BLE001 - 线程内异常必须回传
                    if not ready.done():
                        ready.set_exception(exc)
                    log.warning("MCP 会话线程退出：%s", exc)

            thread = threading.Thread(target=_runner, name="mcp-session", daemon=True)
            thread.start()
            self._session = _Session(loop=loop, thread=thread, ready=ready)
            return self._session

    async def _connect_and_serve(self, ready: concurrent.futures.Future[Any]) -> None:
        """在后台线程的事件循环里连接 MCP 并**保持**连接直到进程退出。"""
        from mcp import Client

        client = Client(self.settings.mcp_server_url) if self._use_http() else Client(
            self._stdio_params()
        )
        async with client:
            assert self._session is not None
            self._session.client = client
            if not ready.done():
                ready.set_result(client)
            # 用"永不完成的 future"顶住异步上下文，让会话一直活着。
            # 为什么不是 `while True: await asyncio.sleep(1)`：轮询会白烧 CPU，
            # 也不表达"这里在等一个信号"的意图。
            await asyncio.Future()

    def _use_http(self) -> bool:
        return self.settings.mcp_transport == "streamable-http"

    def _stdio_params(self) -> Any:
        from mcp import StdioServerParameters

        return StdioServerParameters(
            command=self.settings.mcp_stdio_command,
            args=list(self.settings.mcp_stdio_args),
            # !! 子进程**不继承**父进程环境（SDK 只传一个最小允许列表）!!
            #   因此这里必须显式把数据服务地址传进去，否则 MCP 子进程会
            #   落到默认值 —— 在本机恰好也是 127.0.0.1:8000，看起来"能跑"，
            #   换一台机器就会静默指向错误的地方。
            env={"MCP_DATA_API_BASE": self.settings.data_api_base},
        )

    # --------------------------------------------------------
    # 调用
    # --------------------------------------------------------
    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用一个 MCP 工具，返回它声明的结构化结果。

        抛出：`McpUnavailable`（连不上/超时）、`McpToolError`（工具返回了业务拒绝）。
        两者必须分开：前者是链路问题（要去看 MCP 服务与数据服务），
        后者是**业务反馈**（要去看 SQL 写错了什么），排障方向完全相反。
        """
        return self._call(name, dict(arguments or {}))

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._ensure_session()

        # 等连接就绪（首次调用时会有一次握手；此后走已建立的会话）
        try:
            session.ready.result(timeout=self.settings.mcp_timeout)
        except concurrent.futures.TimeoutError as exc:
            self.last_error = f"连接 MCP 超时（{self.settings.mcp_timeout}s）"
            raise McpUnavailable(self.last_error) from exc
        except Exception as exc:  # noqa: BLE001 - 连接失败的原因要原样带出
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise McpUnavailable(self.last_error) from exc

        future = asyncio.run_coroutine_threadsafe(
            session.client.call_tool(name, arguments), session.loop
        )
        try:
            result = future.result(timeout=self.settings.mcp_timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            self.last_error = f"调用 MCP 工具 {name} 超时（{self.settings.mcp_timeout}s）"
            raise McpUnavailable(self.last_error) from exc
        except Exception as exc:  # noqa: BLE001 - MCP 层错误（含未知工具）
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise McpUnavailable(self.last_error) from exc

        self.last_error = ""
        return _unwrap(result, name)

    # --------------------------------------------------------
    # 供 /health 与 /mcp 用的自述
    # --------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        """列出 MCP 服务声明的工具（能力面可枚举）。"""
        session = self._ensure_session()
        try:
            session.ready.result(timeout=self.settings.mcp_timeout)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise McpUnavailable(self.last_error) from exc

        future = asyncio.run_coroutine_threadsafe(session.client.list_tools(), session.loop)
        try:
            listed = future.result(timeout=self.settings.mcp_timeout)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise McpUnavailable(self.last_error) from exc

        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": _jsonable(getattr(tool, "input_schema", None)),
            }
            for tool in listed.tools
        ]

    def close(self) -> None:
        """关闭会话（进程退出或测试清理时用）。"""
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            session.loop.call_soon_threadsafe(lambda: None)
        except RuntimeError:  # 循环已关
            return


# ------------------------------------------------------------
# 结果解包
# ------------------------------------------------------------
def _unwrap(result: Any, tool: str) -> dict[str, Any]:
    """把 MCP 的 `CallToolResult` 还原成工具返回的 dict。

    !! 为什么优先看 `structured_content` 再退回解析文本 !!
        同一个工具，SDK 在两种情况下给出的形状不同（实测 v2.2.0）：
          - 返回**标量**（str/int）：`structured_content = {"result": ...}`；
          - 返回**对象**：`structured_content` 可能是 `None`，
            真正的结构化数据以 JSON 文本放在 `content[0].text` 里。
        只认一种就会在另一种上静默拿到空字典 —— 表现为"取到了数据但全是空的"，
        这比报错更难查。因此这里两种都认，且都失败时明确报错。
    """
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        text = _text_of(result)
        raise McpToolError(tool, "MCP_TOOL_ERROR", text or "MCP 工具返回了错误")

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured:
        inner = structured.get("result") if set(structured) == {"result"} else structured
        if isinstance(inner, str):
            parsed = _try_json(inner)
            return parsed if isinstance(parsed, dict) else {"result": inner}
        if isinstance(inner, dict):
            return inner

    text = _text_of(result)
    parsed = _try_json(text)
    if isinstance(parsed, dict):
        if parsed.get("ok") is False:
            error = parsed.get("error") or {}
            raise McpToolError(
                tool,
                str(error.get("code") or "MCP_TOOL_ERROR"),
                str(error.get("message") or "MCP 工具返回了拒绝原因"),
                str(error.get("detail") or ""),
            )
        return parsed
    raise McpToolError(tool, "BAD_MCP_PAYLOAD", f"MCP 工具 {tool} 的返回无法解析为结构化数据")


def _text_of(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _try_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _jsonable(value: Any) -> Any:
    """把 SDK 的 pydantic 模型转成纯 JSON（接口要能直接序列化它）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)
