"""Agent 工具集：Agent 能做的每一件事都在这里，且**只经 HTTP 或 MCP**取数。

## 为什么工具只走 HTTP / MCP，不直连 Doris

    进程边界即权限边界。
    Agent 进程里**没有**数据库凭据，也没有 mysql 客户端依赖 ——
    它能做的查询，与"一个人类用户在浏览器里点看板"能做的完全相同：
    都要过 `sqlguard`（仅 SELECT / 表白名单 / 强制 LIMIT）与只读账号 `agent_ro`。
    这样"Agent 不得拥有数据库管理员权限"（AGENTS.md 第 10.1 节）
    就不是靠代码自觉，而是靠架构保证 —— 想绕过也没有入口。

## 两条取数路径（Sprint 10）

    `AGENT_DATA_PATH=http`（默认）  取数直接打只读数据服务
    `AGENT_DATA_PATH=mcp`          取数经 MCP 服务（`app/mcp_client.py`）

    两条路径的**终点完全相同**（都是 `POST /data/api/query` + sqlguard +
    只读账号），差别只在中间多一跳 MCP。做成开关而不是自动探测，是为了
    让"这一次走了哪条路"永远是一个可断言的事实（见 `config.Settings.data_path`）。

## 工具集合刻意保持最小（AGENTS.md 第 10.3 节「权限最小化」）

    metrics_lookup     查口径定义      —— 回答"这个指标怎么算的"
    tables_lookup      查表结构        —— 让 LLM 知道有哪些列可写
    sql_query          执行只读 SQL    —— 唯一的取数通道
    reconciliation     查批流对账结论  —— 回答"这个数可不可信"
    retrieve_docs      词法检索口径与元数据（Sprint 9）—— 让说法与术语对齐
    propose_sql        规划协议（Sprint 8）—— 规划器的输出格式，不执行 IO

    没有"列出所有表"这类工具：表清单是固定的白名单，
    直接写进系统提示词比让模型多绕一轮更省 token 也更稳定。

    `propose_sql` 与其余五个不同：它是**图（Sprint 8）内部的规划协议**，
    不是取数能力。列在工具表里是为了让 `/tools` 能把这个协议也暴露成
    可审查的接口声明 —— "Agent 能做什么"应当一眼看全，包括它和编排层怎么对话。

## 每个工具都返回 ToolResult，而不是裸字符串

    因为 Agent 需要**同时**给 LLM 一段文本、给用户一份证据（读了哪些表）。
    把两者分开记录，才能做到"最终回答必须能够说明数据来源"
    而不用去正则解析自己的输出。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .llm import ToolSpec

__all__ = [
    "TOOL_SPECS",
    "RETRIEVE_SPEC",
    "REASONING_SPEC",
    "ToolResult",
    "ToolBox",
    "extract_tables",
]

# 单次数据服务调用的超时。取 30s：数据服务侧 Doris 查询超时是 15s，
# 这里必须比它大，否则会出现"数据服务还在正常查、Agent 已经超时报错"的假失败。
API_TIMEOUT = 30

# 走 MCP 的工具名（Sprint 10）。刻意**不含** `retrieve_docs` / `propose_sql`：
# 前者查 Agent 自己的语料，后者是图内部的规划协议，两者都不是取数。
_MCP_TOOL_NAMES = frozenset({"metrics_lookup", "tables_lookup", "sql_query", "reconciliation"})

# !! 为什么从 SQL 里抠表名要在这儿再写一份正则 !!
#   权威实现在 `services/api/app/sqlguard.py` 的 `extract_tables`（守卫与血缘
#   用的是同一套规则）。但 Agent 不能 import 它：
#     - 那是**另一个部署单元**的包（`services/api/app`），而 `services/agent`
#       与它各自的 `app` 包同名，tests/conftest.py 里已经记录过这个坑；
#     - Agent 进程里引入守卫模块会给人"Agent 也持有权限判断"的错觉，
#       而真实边界是"Agent 只发 HTTP"。
#   代价是两处规则可能漂移。对策不是"记得同步"，而是**可发现**：
#   `scripts/verify-sprint-10.sh` 会断言两条取数路径对同一条 SQL 报出
#   完全相同的 tables，漂移会在验收时暴露。
_IDENT = r"(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w$]*)"
_TABLE_RE = re.compile(rf"\b(?:from|join)\s+({_IDENT}(?:\s*\.\s*{_IDENT})*)", re.I)


def extract_tables(sql: str) -> list[str]:
    """从 SQL 里提取目标表名（去重、保持出现顺序）。

    与 `sqlguard.extract_tables` 规则一致：支持 `库.表`、反引号、方括号写法。
    少识别一种写法就等于少一份血缘信息，因此标识符按"点分片段"来匹配。
    """
    seen: list[str] = []
    for match in _TABLE_RE.finditer(sql or ""):
        name = re.split(r"\s*\.\s*", match.group(1).strip())[-1].strip().strip("`\"[]")
        if name and name not in seen:
            seen.append(name)
    return seen


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
    """工具的实现集合。持有一个 Settings（数据服务地址）即可。

    Sprint 10 起还持有一个**惰性创建**的 MCP 客户端：
    `data_path == "mcp"` 时所有取数改经它，否则完全不建连接
    （默认路径不该因为多了一个开关就多出一个后台线程）。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._mcp: Any = None

    # --------------------------------------------------------
    # 取数路径（Sprint 10）
    # --------------------------------------------------------
    @property
    def data_path(self) -> str:
        """本次运行实际使用的取数路径：`http` 或 `mcp`。"""
        return self.settings.data_path

    @property
    def mcp(self) -> Any:
        """MCP 客户端（首次访问时才创建，且只在 `data_path == "mcp"` 时被用到）。"""
        if self._mcp is None:
            from .mcp_client import McpToolClient

            self._mcp = McpToolClient(self.settings)
        return self._mcp

    @staticmethod
    def _mcp_failure(tool: str, arguments: dict[str, Any], exc: Exception) -> ToolResult:
        """把 MCP 侧失败转成给模型看的失败结果（**不回退到直连**）。

        与 `_send` 的取舍一致：这是有价值的反馈而不是崩溃，
        但要如实带上"这是 MCP 这一跳出的问题"，否则排障会找错地方。
        """
        return ToolResult(
            False,
            f"MCP 取数失败（{type(exc).__name__}）：{exc}。"
            f"本次运行配置的取数路径是 mcp，不会自动回退到直连 —— "
            f"如需直连请把 AGENT_DATA_PATH 改回 http 后重启 Agent。",
            tool=tool,
            arguments=arguments,
        )

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
    # 工具 5：元数据/口径检索（Sprint 9）
    # --------------------------------------------------------
    def retrieve_docs(self, query: str = "") -> ToolResult:
        """词法检索口径/表结构/分层文档（BM25 + 同义词扩展）。"""
        arguments = {"query": query}
        try:
            # 惰性导入：检索层依赖语料文件，让"没有语料"不至于影响其它工具可用
            from .retrieval import build_retriever

            result = build_retriever(self.settings).retrieve(query)
        except Exception as exc:  # noqa: BLE001 - 检索失败必须可见，不能假装没查到
            return ToolResult(
                False,
                f"检索失败：{type(exc).__name__}: {exc}",
                tool="retrieve_docs",
                arguments=arguments,
            )

        payload = {
            "query": result.query,
            "backend": result.backend,
            "docs_total": result.docs_total,
            "hits": [hit.as_dict() for hit in result.hits],
            "expanded_terms": result.expanded_terms,
            "channels": result.channels,
            "degraded": result.degraded,
        }
        if not result.hits:
            return ToolResult(
                True,
                "检索没有命中任何口径/表结构文档。请如实说明语料没有覆盖该问题，"
                "不要凭常识给出公式。",
                tool="retrieve_docs",
                arguments=arguments,
            )
        return ToolResult(
            True,
            _dump(payload),
            tables=[],
            tool="retrieve_docs",
            arguments=arguments,
        )

    # --------------------------------------------------------
    # 分发
    # --------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """按名字调用工具。未知工具返回失败结果而不是抛异常。

        ## 取数路径的分流点就在这里（Sprint 10）

        `data_path == "mcp"` 时，**所有取数类工具**改经 MCP 客户端调用，
        方法名与 MCP 工具名一一对应。放在这一处分流而不是逐个方法内部判断，
        理由有两条：

          1. 只有一个分流点，"哪些工具走了 MCP"是一眼可读的事实，
             而不是散在五个函数体里的五处 if；
          2. 直连实现（`_get` / `_post`）**一行都不用改** ——
             Sprint 7 已验收的路径保持原样，回归风险最小。

        `retrieve_docs` 与 `propose_sql` 不走 MCP：前者查的是 Agent 自己的
        语料文件与检索索引，后者是图内部的规划协议，**两者本来就不是取数**。
        把它们也塞进 MCP 会让"最小能力集合"这个说法失真。
        """
        if self.data_path == "mcp" and name in _MCP_TOOL_NAMES:
            return self._call_via_mcp(name, arguments)

        handlers = {
            "metrics_lookup": lambda a: self.metrics_lookup(str(a.get("keyword", ""))),
            "tables_lookup": lambda a: self.tables_lookup(str(a.get("table", ""))),
            "sql_query": lambda a: self.sql_query(str(a.get("sql", ""))),
            "reconciliation": lambda a: self.reconciliation(),
            "retrieve_docs": lambda a: self.retrieve_docs(str(a.get("query", ""))),
            # `propose_sql` 不是可执行的工具，而是**规划器的输出协议**：
            # 它由图的 plan 节点消费，不在这里执行任何 IO。
            # 放在这个分发表里只是为了给出可读的提示，避免模型误调。
            "propose_sql": lambda a: ToolResult(
                False,
                "propose_sql 是规划协议，由规划器在 plan 阶段调用，不在此处执行。",
                tool="propose_sql",
                arguments=a,
            ),
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

    # --------------------------------------------------------
    # 经 MCP 取数（Sprint 10）
    # --------------------------------------------------------
    def _call_via_mcp(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """经 MCP 调用同名工具，并把结果**归一成与直连完全相同的形状**。

        !! 归一化是必需的，不是美化 !!
            直连 `/meta/tables` 返回的是 rows 列表，MCP 的 `tables_lookup`
            返回 `{"tables": [...]}`；`reconciliation` 直连返回 data 里的
            `latest`/`deltas`/`totals`，MCP 返回 `latest_batch`/`deltas`/`totals`。
            若不复原成直连形状，图（Sprint 8）里读 `result.tables` 与
            `_extract_number(content, "row_count")` 的地方会**静默拿到空值** ——
            表现为"回答里没有数字"，而工具其实成功了。这类"两条路径形状不同"
            比功能性缺陷更难定位，因此在边界上一次抹平。
        """
        try:
            payload = self.mcp.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - MCP 故障必须可见，不回退
            return self._mcp_failure(name, arguments, exc)

        if name == "tables_lookup":
            rows = list(payload.get("tables") or [])
            if payload.get("found") is False:
                available = "、".join(payload.get("available_tables") or [])
                return ToolResult(
                    False,
                    f"没有名为「{arguments.get('table', '')}」的表。可用的表：{available}",
                    tool=name,
                    arguments=arguments,
                )
            return ToolResult(
                True,
                _dump(rows),
                tables=[str(t.get("table", "")) for t in rows],
                tool=name,
                arguments=arguments,
            )

        if name == "reconciliation":
            normalised = {
                "latest_batch": payload.get("latest_batch") or {},
                "deltas": payload.get("deltas") or {},
                "totals": payload.get("totals") or {},
                "mismatch_count": payload.get("mismatch_count", 0),
            }
            return ToolResult(
                True,
                _dump(normalised),
                tables=[],
                tool=name,
                arguments=arguments,
            )

        if name == "sql_query":
            # sql_query 的失败在 MCP 侧已经是结构化错误（守卫拒绝），
            # 客户端会抛 McpToolError —— 交给 `_mcp_failure` 之外的分支处理，
            # 这里只处理成功路径。
            normalised = {
                "executed_sql": payload.get("executed_sql", ""),
                "row_count": payload.get("row_count", 0),
                "elapsed_ms": payload.get("elapsed_ms", 0),
                "rows": payload.get("rows") or [],
            }
            return ToolResult(
                True,
                _dump(normalised),
                # 血缘信息与直连路径同源：都从实际执行的 SQL 里提取，
                # 而不是让 MCP 自报（自报的表名不可核对）。
                tables=extract_tables(str(payload.get("executed_sql") or "")),
                tool=name,
                arguments=arguments,
            )

        return ToolResult(True, _dump(payload), tool=name, arguments=arguments)


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
    ToolSpec(
        name="retrieve_docs",
        description=(
            "在指标口径、表结构、数仓分层说明里做**词法检索**（BM25 + 同义词扩展），"
            "返回命中的条目及其来源文件与行号。"
            "当问题里的说法与项目术语不一致时特别有用"
            "（例如用户说「卖了多少钱」「客单价」「口径」，检索层有显式同义词映射）。"
            "注意：图中已自动做了一次检索并把结果附在问题后，"
            "只有需要**换关键词再查一次**时才调用本工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，可用用户原话（会自动做同义词扩展）",
                }
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="propose_sql",
        description=(
            "**规划协议**：提交本次的查询计划（思路 + 1~3 条 SQL 步骤）。"
            "规划阶段必须调用它；它本身不执行查询，执行由后续步骤完成。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "给用户看的思路：为什么查这些指标、为什么选这些表",
                },
                "steps": {
                    "type": "array",
                    "description": "1~3 条 SQL 步骤；信息不足时传空数组并说明缺什么",
                    "items": {
                        "type": "object",
                        "properties": {
                            "purpose": {"type": "string", "description": "这一步要回答什么"},
                            "target_table": {
                                "type": "string",
                                "description": "目标表，写「库名.表名」",
                            },
                            "sql": {"type": "string", "description": "一条完整 SELECT，不带分号"},
                        },
                        "required": ["purpose", "sql"],
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "要点或不确定之处（例如语料未覆盖该口径）",
                },
            },
            "required": ["reasoning", "steps"],
        },
    ),
]

# 单独暴露，便于图（Sprint 8）与接口按名引用，避免靠下标取列表
RETRIEVE_SPEC: ToolSpec = next(spec for spec in TOOL_SPECS if spec.name == "retrieve_docs")
REASONING_SPEC: ToolSpec = next(spec for spec in TOOL_SPECS if spec.name == "propose_sql")


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
