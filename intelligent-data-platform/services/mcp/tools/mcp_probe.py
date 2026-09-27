"""MCP 验收探针（Sprint 10）：供 `scripts/verify-sprint-10.sh` 调用的最小 MCP 客户端。

## 为什么需要它（而不是让 bash 直接说 MCP）

    MCP 是双向的 JSON-RPC over 一条会话流，**不是**一个能用 `curl` 打一下的
    REST 接口（streamable-http 需要先 initialize 握手、拿 session id、
    再走 SSE 事件流）。在 bash 里用 curl 拼这套握手，会得到一份
    "只在 happy path 上能跑、出错时给出误导信息"的实现。

    因此把"说 MCP"这件事交给官方 SDK，bash 只负责调它、断言它的输出。
    这也让验收脚本自己成了 MCP 的真实客户端 —— 与 Agent 用的是同一个 SDK、
    同一段协议路径。

## 子命令

    list                         列出 MCP 服务声明的工具（JSON）
    read <sql> [limit]           经 MCP 执行只读 SQL，打印**归一化**后的结果
    write <sql>                  经 MCP 执行一条非 SELECT，打印拒绝原因（期望被拒）
    probe                        打印已安装 mcp SDK 版本与是否可导入

## ⚠️ 不持有任何凭据

    本探针只连 `MCP_URL` 指向的 MCP 服务；它没有数据库连接信息，
    也不读 `.env`。与 `services/agent/app/mcp_client.py` 同源。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import sys
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8200/mcp"


def _emit(payload: Any) -> None:
    """统一 JSON 输出（验收脚本用 python3 解析它，不靠正则抠文本）。"""
    json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def _text_of(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _payload_of(result: Any) -> dict[str, Any]:
    """把 `CallToolResult` 还原成工具返回的 dict（与 Agent 侧同一套规则）。"""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured:
        inner = structured.get("result") if set(structured) == {"result"} else structured
        if isinstance(inner, str):
            try:
                parsed = json.loads(inner)
            except json.JSONDecodeError:
                return {"result": inner}
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        if isinstance(inner, dict):
            return inner
    text = _text_of(result)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


async def _run(args: argparse.Namespace) -> int:
    from mcp import Client

    async with Client(args.url) as client:
        if args.command == "list":
            listed = await client.list_tools()
            _emit(
                {
                    "ok": True,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description or "",
                        }
                        for tool in listed.tools
                    ],
                }
            )
            return 0

        if args.command == "probe":
            try:
                version = importlib.metadata.version("mcp")
            except importlib.metadata.PackageNotFoundError:  # pragma: no cover
                version = "unknown"
            _emit({"ok": True, "mcp_version": version, "url": args.url})
            return 0

        if args.command == "read":
            result = await client.call_tool("sql_query", {"sql": args.sql})
            payload = _payload_of(result)
            if getattr(result, "is_error", False) or payload.get("ok") is False:
                _emit({"ok": False, "payload": payload})
                return 0
            _emit(
                {
                    "ok": True,
                    "executed_sql": payload.get("executed_sql", ""),
                    "row_count": payload.get("row_count", 0),
                    "rows": payload.get("rows") or [],
                }
            )
            return 0

        if args.command == "write":
            # 期望被拒：把拒绝原因原样打出来供断言（拒绝对，也是能力边界的一部分）
            result = await client.call_tool("sql_query", {"sql": args.sql})
            payload = _payload_of(result)
            _emit(
                {
                    "ok": not (getattr(result, "is_error", False) or payload.get("ok") is False),
                    "is_error": bool(getattr(result, "is_error", False)),
                    "payload": payload,
                }
            )
            return 0

    _emit({"ok": False, "error": f"未知子命令 {args.command}"})
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp_probe", description="Sprint 10 MCP 验收探针")
    parser.add_argument("command", choices=("list", "read", "write", "probe"))
    parser.add_argument("sql", nargs="?", default="", help="read/write 子命令要执行的 SQL")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP streamable-http 端点")
    args = parser.parse_args(argv)

    if args.command in ("read", "write") and not args.sql:
        _emit({"ok": False, "error": "缺少 SQL 参数"})
        return 2

    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - 连不上也要给出**结构化**输出，便于断言
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
