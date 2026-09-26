"""Agent 工具集：Agent 能做的每一件事都在这里，且**只经 HTTP 调数据服务**。

## 为什么工具只走 HTTP，不直连 Doris

    进程边界即权限边界。
    Agent 进程里**没有**数据库凭据，也没有 mysql 客户端依赖 ——
    它能做的查询，与"一个人类用户在浏览器里点看板"能做的完全相同：
    都要过 `sqlguard`（仅 SELECT / 表白名单 / 强制 LIMIT）与只读账号 `agent_ro`。
    这样"Agent 不得拥有数据库管理员权限"（AGENTS.md 第 10.1 节）
    就不是靠代码自觉，而是靠架构保证 —— 想绕过也没有入口。

## 工具集合刻意保持最小（AGENTS.md 第 10.3 节「权限最小化」）

    metrics_lookup     查口径定义      —— 回答"这个指标怎么算的"
    tables_lookup      查表结构        —— 让 LLM 知道有哪些列可写
    sql_query          执行只读 SQL    —— 唯一的取数通道
    reconciliation     查批流对账结论  —— 回答"这个数可不可信"

    没有"列出所有表"这类工具：表清单是固定的白名单，
    直接写进系统提示词比让模型多绕一轮更省 token 也更稳定。

## 每个工具都返回 ToolResult，而不是裸字符串

    因为 Agent 需要**同时**给 LLM 一段文本、给用户一份证据（读了哪些表）。
    把两者分开记录，才能做到"最终回答必须能够说明数据来源"
    而不用去正则解析自己的输出。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .llm import ToolSpec

__all__ = ["TOOL_SPECS", "ToolResult", "ToolBox"]

# 单次数据服务调用的超时。取 30s：数据服务侧 Doris 查询超时是 15s，
# 这里必须比它大，否则会出现"数据服务还在正常查、Agent 已经超时报错"的假失败。
API_TIMEOUT = 30


@dataclass
class ToolResult:
    """一次工具调用的结果。"""

    ok: bool
    # 给 LLM 看的内容（字符串；结构化数据会先 JSON 序列化并截断）
    content: str
    # 给用户的证据：这次调用读了哪些表
    tables: list[str] = field(default_factory=list)
    # 审计用：工具名与入参（不含任何凭据）
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_evidence(self) -> dict[str, Any]:
        return {"tool": self.tool, "arguments": self.arguments, "tables": self.tables}


class ToolBox:
    """工具的实现集合。持有一个 Settings（数据服务地址）即可。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --------------------------------------------------------
    # 底层 HTTP
    # --------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        query = ""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            query = "?" + urllib.parse.urlencode(clean)
        url = f"{self.settings.data_api_base}{path}{query}"
        request = urllib.request.Request(url, method="GET")
        return self._send(request)

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.data_api_base}{path}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return self._send(request)

    @staticmethod
    def _send(request: urllib.request.Request) -> tuple[int, dict[str, Any]]:
        """发请求并解析 JSON。

        !! 为什么把 HTTP 错误也解析成 (status, body) 而不是抛异常 !!
            数据服务的业务错误（SQL 被守卫拒绝、表未授权）都返回 4xx + 结构化
            `{"error": {...}}`。这些**不是 Agent 的故障，而是有价值的反馈**：
            模型看到"你查了未授权的表"就能自己改正。
            如果当成异常直接中断，就失去了"查询失败必须能重新规划"
            （AGENTS.md 第 10.1 节第 6 条）的能力。
        """
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                return exc.code, {"error": {"code": "BAD_RESPONSE", "message": raw[:500]}}
        except (urllib.error.URLError, OSError) as exc:
            # 数据服务不可达：这是真故障，如实上报（不伪造数据）
            return 0, {
                "error": {
                    "code": "DATA_API_UNREACHABLE",
                    "message": f"无法连接数据服务 {request.full_url}：{exc}",
                }
            }

    # --------------------------------------------------------
    # 工具 1：指标口径
    # --------------------------------------------------------
    def metrics_lookup(self, keyword: str = "") -> ToolResult:
        """查指标口径字典（来自 sql/metadata/metrics.md，唯一权威）。"""
        arguments = {"keyword": keyword}
        status, body = self._get("/meta/metrics")
        if status != 200:
            return ToolResult(False, _error_text(status, body), tool="metrics_lookup", arguments=arguments)

        data = body.get("data") or {}
        metrics = data.get("metrics") or []
        conventions = data.get("conventions") or []

        if keyword:
            low = keyword.strip().lower()
            metrics = [
                m
                for m in metrics
                if low in json.dumps(m, ensure_ascii=False).lower()
            ]

        if not metrics and keyword:
            return ToolResult(
                True,
                f"口径字典里没有匹配「{keyword}」的指标。可用的指标有："
                + "、".join(sorted({m.get("field", "") for m in (data.get("metrics") or [])})),
                tool="metrics_lookup",
                arguments=arguments,
            )

        payload = {
            "version": data.get("version", ""),
            "source_document": data.get("source_document", ""),
            "conventions": conventions,
            "metrics": metrics,
        }
        return ToolResult(
            True,
            _dump(payload),
            tool="metrics_lookup",
            arguments=arguments,
        )

    # --------------------------------------------------------
    # 工具 2：表结构
    # --------------------------------------------------------
    def tables_lookup(self, table: str = "") -> ToolResult:
        """查表结构与分层（字段名、类型、注释、所属库）。"""
        arguments = {"table": table}
        status, body = self._get("/meta/tables")
        if status != 200:
            return ToolResult(False, _error_text(status, body), tool="tables_lookup", arguments=arguments)

        rows = body.get("data") or []
        if table:
            rows = [t for t in rows if t.get("table") == table]
            if not rows:
                available = "、".join(sorted(t.get("table", "") for t in (body.get("data") or [])))
                return ToolResult(
                    False,
                    f"没有名为「{table}」的表。可用的表：{available}",
                    tool="tables_lookup",
                    arguments=arguments,
                )

        return ToolResult(
            True,
            _dump(rows),
            tables=[t.get("table", "") for t in rows],
            tool="tables_lookup",
            arguments=arguments,
        )

    # --------------------------------------------------------
    # 工具 3：执行只读 SQL（唯一取数通道）
    # --------------------------------------------------------
    def sql_query(self, sql: str) -> ToolResult:
        """执行一条 SELECT（经数据服务的 SQL 守卫与只读账号）。"""
        arguments = {"sql": sql}
        status, body = self._post("/query", {"sql": sql, "limit": self.settings.query_limit})

        if status != 200:
            # 把守卫的拒绝原因原样交给模型 —— 它能据此改写 SQL
            return ToolResult(False, _error_text(status, body), tool="sql_query", arguments=arguments)

        data = body.get("data") or {}
        source = body.get("source") or {}
        rows = data.get("rows") or []

        payload = {
            "executed_sql": data.get("executed_sql", ""),
            "row_count": data.get("row_count", 0),
            "elapsed_ms": data.get("elapsed_ms", 0),
            "rows": rows,
        }
        return ToolResult(
            True,
            _dump(payload),
            tables=list(source.get("tables") or []),
            tool="sql_query",
            arguments=arguments,
        )

    # --------------------------------------------------------
    # 工具 4：批流对账结论
    # --------------------------------------------------------
    def reconciliation(self) -> ToolResult:
        """查实时链路与离线链路的对账结论（回答"这个数可不可信"）。"""
        status, body = self._get("/batch/reconcile")
        if status != 200:
            return ToolResult(False, _error_text(status, body), tool="reconciliation", arguments={})

        data = body.get("data") or {}
        source = body.get("source") or {}
        latest = data.get("latest") or {}
        payload = {
            "latest_batch": latest,
            "deltas": data.get("deltas") or {},
            "totals": data.get("totals") or {},
            "mismatch_count": len(data.get("mismatches") or []),
        }
        return ToolResult(
            True,
            _dump(payload),
            tables=list(source.get("tables") or []),
            tool="reconciliation",
            arguments={},
        )

    # --------------------------------------------------------
    # 分发
    # --------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """按名字调用工具。未知工具返回失败结果而不是抛异常。"""
        handlers = {
            "metrics_lookup": lambda a: self.metrics_lookup(str(a.get("keyword", ""))),
            "tables_lookup": lambda a: self.tables_lookup(str(a.get("table", ""))),
            "sql_query": lambda a: self.sql_query(str(a.get("sql", ""))),
            "reconciliation": lambda a: self.reconciliation(),
        }
        handler = handlers.get(name)
        if handler is None:
            return ToolResult(
                False,
                f"没有名为「{name}」的工具。可用工具：{'、'.join(sorted(handlers))}",
                tool=name,
                arguments=arguments,
            )
        try:
            return handler(arguments)
        except Exception as exc:  # noqa: BLE001 - 工具内部异常必须变成可见的失败，不能吞掉
            return ToolResult(
                False,
                f"工具 {name} 执行异常：{type(exc).__name__}: {exc}",
                tool=name,
                arguments=arguments,
            )


# ------------------------------------------------------------
# 工具声明（给 LLM 看的 JSON Schema）
#
# 描述里刻意写了"什么时候用 / 什么时候不要用"：
# 模型选错工具是 Agent 最常见的问题，而工具描述是唯一能引导它的地方。
# ------------------------------------------------------------
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="metrics_lookup",
        description=(
            "查询指标口径字典，返回指标的权威定义（计算方式、空值约定、所在表）。"
            "**在生成任何指标类 SQL 之前必须先调用它**，确保口径与项目定义一致。"
            "例如 GMV、订单量、客单价、支付成功率、退款率、UV、PV、转化率。"
            "keyword 留空则返回全部口径。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "指标名或字段名关键词，例如 gmv、支付成功率；留空返回全部",
                }
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="tables_lookup",
        description=(
            "查询表结构与分层信息，返回字段名、类型、注释与所属库。"
            "写 SQL 之前用它确认列名是否存在、单位与含义。"
            "table 留空则返回全部表的结构。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "精确表名，例如 ads_realtime_trade_1m；留空返回全部",
                }
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="sql_query",
        description=(
            "执行一条只读 SELECT 并返回结果行。这是唯一能取到数据的工具。"
            "限制：只能 SELECT；不能有注释、分号或多条语句；只能查已授权的表；"
            "行数会被服务端强制限制。若被拒绝，错误信息会说明原因，请据此改写后重试。"
            "提示：project 里同时存在实时链路（ecommerce 库，分钟粒度）"
            "与离线链路（lakehouse_ads 库，天粒度），选表时注意粒度与问题的时间范围是否匹配。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "一条完整的 SELECT 语句，不要带分号",
                }
            },
            "required": ["sql"],
        },
    ),
    ToolSpec(
        name="reconciliation",
        description=(
            "查询实时链路与离线链路的逐窗口对账结论，返回对账区间、窗口数、"
            "不一致窗口数与两侧指标合计。"
            "当用户问「这个数据准不准」「实时和离线一致吗」「数据可信吗」时使用它，"
            "用它来支撑「数据可信」的结论，而不是自己下判断。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    ),
]


def _dump(payload: Any, max_chars: int = 12000) -> str:
    """把结构化结果序列化给 LLM，并做长度保护。

    为什么要截断：sql_query 可能返回 200 行 × 多列，直接塞进上下文
    既贵又容易把真正重要的部分挤出注意力。截断时明确写出"已截断"
    以及总行数，让模型知道数据不完整、可以改用聚合查询 —— 而不是
    让它以为自己看到了全部数据从而给出错误结论。
    """
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n...(结果过长已截断，原始长度 {len(text)} 字符。"
        "如需完整信息，请改用聚合查询（COUNT/SUM/GROUP BY）减少返回行数)"
    )


def _error_text(status: int, body: dict[str, Any]) -> str:
    """把数据服务的错误信封转成给模型看的一句话。"""
    error = body.get("error") or {}
    code = error.get("code", "UNKNOWN")
    message = error.get("message", "未知错误")
    detail = error.get("detail", "")
    parts = [f"调用失败（HTTP {status}，code={code}）：{message}"]
    if detail:
        parts.append(f"详情：{detail}")
    return "；".join(parts)
