"""MCP 服务（Sprint 10）：把**只读数据服务**的能力用 MCP 暴露出来。

## 这个服务是什么

    一个 **stdio / streamable-http** 两种传输都支持的 MCP 服务器，
    对外只暴露 4 个**只读**工具，每个工具都是 `services/api` 已有接口的
    一对一映射。它自己没有任何数据能力 —— 所有数据都来自
    `POST /data/api/query` 与两个 `/meta/*` 只读接口。

## 工具集合为什么是这 4 个（`AGENTS.md` §10.3「权限最小化」）

    metrics_lookup    ← GET  /data/api/meta/metrics      查口径定义
    tables_lookup     ← GET  /data/api/meta/tables       查表结构（限授权表）
    sql_query         ← POST /data/api/query             唯一的取数通道
    reconciliation    ← GET  /data/api/batch/reconcile   查批流对账结论

    **只读接口有的能力就是全部能力**。刻意**没有**的东西：
      - 没有写工具（数据服务本身就没有写接口，不是"我们没写"）；
      - 没有"列出所有表"（表清单是固定白名单，多一个工具就多一份要审的权限面）；
      - 没有"执行任意 HTTP"这类通用出口（那等于把最小权限一次还回去）；
      - 没有数据库凭据（见 `client.py` 的模块说明）。

## 为什么同时支持两种传输（而不是只留一种）

    **streamable-http 是部署形态**：MCP 服务以独立 systemd 单元常驻
    127.0.0.1:8200，Agent 与验收脚本按 URL 接入。选它的理由是可运维性：
       - 能 `systemctl status` / `journalctl` 看它，与项目里其它服务同一套手法；
       - 崩溃重启不影响 Agent，也不影响数据服务；
       - 一个进程可被多个客户端共用，连接生命周期与 Agent 请求解耦。
    对比 stdio：stdio 由宿主把服务拉成**子进程**，生命周期绑在调用方身上 ——
    排障时"服务没起来"和"调用写错了"看起来一模一样，
    而且 stdout 就是协议线路，任何一行杂散输出都会破坏数据流。

    **stdio 保留为本地形态**（`--transport stdio`）：桌面宿主（Claude Desktop
    等）与 `mcp dev` 检查器按约定就是 stdio，留一个开关比事后再加便宜。
    两者暴露的是**同一份工具实现**（本模块的 `build_server`），
    所以"换个传输就换了能力面"这种事不会发生。

## 工具报错的形状（刻意的选择）

    守卫拒绝、表未授权这类**业务拒绝**不由本模块抛异常，
    而是返回 `{"ok": false, "error": {"code": ..., "message": ...}}`。
    理由与 Agent 侧一致：这是**有价值的反馈**，模型看到
    "你查了未授权的表 / 不能有分号"就能自己改正（`AGENTS.md` §10.1 第 6 条）。
    异常只留给"服务不可达"这种真故障 —— 那时链路本身有问题，无法继续。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from mcp.server import MCPServer

from .client import DataServiceClient, DataServiceError
from .config import Settings, load_settings

__all__ = ["build_server", "main", "server_settings"]

log = logging.getLogger("mcp.server")

# !! 为什么用线程局部而不是 `server._config = settings` !!
#   给 SDK 对象挂私有属性会让"配置从哪来"变成只有读过源码的人才知道的事，
#   而且 `build_server()` 在一个进程里被调两次（测试里很常见）就会互相覆盖。
#   线程局部 + 显式访问器把这件事写成可读的代码。
_local = threading.local()

INSTRUCTIONS = """这是「批流一体智能数据分析平台」的**只读**数据 MCP 服务。

能力边界（与只读数据服务完全一致）：
  - 只能查已授权的表（白名单见 services/api/app/sqlguard.py 的 ALLOWED_TABLES）；
  - 只能执行 SELECT，且服务端强制加行数上限；
  - 没有写能力，也没有数据库凭据。

推荐用法：
  1. 先 tables_lookup 确认表名与列名，metrics_lookup 确认指标口径；
  2. 再用 sql_query 取数（写「库名.表名」，不要分号、不要注释、不要自己写 LIMIT）；
  3. 回答"这个数准不准"时用 reconciliation 拿批流对账结论。
"""


def server_settings(server: MCPServer) -> Settings:
    """取回 `build_server()` 时使用的配置。"""
    config = getattr(_local, "settings", None)
    if config is None:
        raise RuntimeError("build_server() 尚未在本线程调用，无法取得 MCP 配置")
    return config  # type: ignore[no-any-return]


def build_server(settings: Settings | None = None) -> MCPServer:
    """构造 MCP 服务器（两种传输共用这一份实现）。

    工具**返回值统一为 dict**：MCP 客户端拿到的是结构化对象而不是一段需要
    再解析的文本 —— "取数结果"应当是数据，不是散文（与 Sprint 8 把规划做成
    结构化工具调用是同一个理由）。
    """
    config = settings or load_settings()
    _local.settings = config
    api = DataServiceClient(config)

    server = MCPServer(
        name=config.name,
        version=config.version,
        instructions=INSTRUCTIONS,
    )

    # --------------------------------------------------------
    # 工具 1：指标口径
    # --------------------------------------------------------
    @server.tool(structured_output=True)
    def metrics_lookup(keyword: str = "") -> dict[str, Any]:
        """查询指标口径字典（唯一权威：sql/metadata/metrics.md）。

        在生成任何指标类 SQL **之前**用它确认口径。keyword 留空返回全部口径。
        """
        data = api.metrics()
        metrics = list(data.get("metrics") or [])
        conventions = list(data.get("conventions") or [])

        matched = metrics
        if keyword:
            low = keyword.strip().lower()
            matched = [m for m in metrics if low in str(m).lower()]
            if not matched:
                return {
                    "keyword": keyword,
                    "found": False,
                    "available_fields": sorted(
                        {str(m.get("field", "")) for m in metrics if m.get("field")}
                    ),
                    "note": f"口径字典里没有匹配「{keyword}」的指标。",
                }

        return {
            "ok": True,
            "keyword": keyword,
            "found": True,
            "source_document": data.get("source_document", ""),
            "version": data.get("version", ""),
            "conventions": conventions,
            "metrics": matched,
            "metrics_total": len(metrics),
        }

    # --------------------------------------------------------
    # 工具 2：表结构
    # --------------------------------------------------------
    @server.tool(structured_output=True)
    def tables_lookup(table: str = "") -> dict[str, Any]:
        """查询表结构与分层（字段名、类型、注释、所属库；只含授权表）。

        table 留空返回全部表的结构；给精确表名则只返回该表。
        """
        rows = api.tables()
        if table:
            exact = [t for t in rows if str(t.get("table", "")) == table]
            if not exact:
                return {
                    "ok": True,
                    "table": table,
                    "found": False,
                    "available_tables": sorted(
                        str(t.get("table", "")) for t in rows if t.get("table")
                    ),
                    "note": f"没有名为「{table}」的授权表；未授权表也不会出现在这里。",
                }
            rows = exact

        return {
            "ok": True,
            "table": table,
            "found": True,
            "tables": rows,
            "tables_total": len(rows),
        }

    # --------------------------------------------------------
    # 工具 3：执行只读 SQL（唯一取数通道）
    # --------------------------------------------------------
    @server.tool(structured_output=True)
    def sql_query(sql: str) -> dict[str, Any]:
        """执行一条只读 SELECT 并返回结果行（经 SQL 守卫与只读账号）。

        限制：只能 SELECT；不能有注释、分号或多条语句；只能查已授权的表；
        行数会被服务端强制限制。被拒绝时返回真实的拒绝原因（code/message），
        请据此改写后重试 —— 本服务**不会**用任何兜底值代替真实结果。
        """
        try:
            data = api.query(sql)
        except DataServiceError as exc:
            # 业务拒绝 → 结构化错误返回（模型可据此改写），不是异常
            return {"ok": False, "error": exc.as_dict()}

        rows = list(data.get("rows") or [])
        return {
            "ok": True,
            "executed_sql": data.get("executed_sql", ""),
            "row_count": data.get("row_count", len(rows)),
            "elapsed_ms": data.get("elapsed_ms", 0),
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows,
        }

    # --------------------------------------------------------
    # 工具 4：批流对账结论
    # --------------------------------------------------------
    @server.tool(structured_output=True)
    def reconciliation() -> dict[str, Any]:
        """查询实时链路与离线链路的逐窗口对账结论（回答"这个数准不准"）。"""
        data = api.reconcile()
        latest = data.get("latest") or {}
        return {
            "ok": True,
            "latest_batch": latest,
            "deltas": data.get("deltas") or {},
            "totals": data.get("totals") or {},
            "mismatch_count": len(data.get("mismatches") or []),
            "scope": {"start": latest.get("scope_start"), "end": latest.get("scope_end")},
        }

    # --------------------------------------------------------
    # 健康检查端点（HTTP 传输下可用）
    #
    # 为什么要有它：MCP 协议本身没有"服务健康"这种概念，
    # 而 systemd / 验收脚本需要一个**能一眼看出它是否连着数据服务**的探针。
    # 只报地址与工具名，不报任何凭据（这里根本没有凭据可报）。
    # --------------------------------------------------------
    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        tools: list[str] = []
        detail = "ok"
        try:
            tools = sorted(tool.name for tool in await server.list_tools())
        except Exception as exc:  # noqa: BLE001 - 探测失败必须可见
            detail = f"{type(exc).__name__}: {exc}"
        return JSONResponse(
            {
                "data": {
                    "status": "ok" if detail == "ok" else "degraded",
                    "server": config.name,
                    "version": config.version,
                    "transports": ["streamable-http", "stdio"],
                    "mcp_path": config.http_path,
                    "data_api_base": config.data_api_base,
                    "tools": tools,
                    "capabilities": {"read_only": True, "database_credentials": False},
                    "detail": detail,
                },
                "source": {
                    "tables": [],
                    "metric_definitions": [],
                    "time_range": {"start": None, "end": None},
                    "note": (
                        "MCP 服务自身无数据库凭据；所有取数经只读数据服务，"
                        "受 sqlguard 与只读账号约束。"
                    ),
                },
            }
        )

    return server


def main(argv: list[str] | None = None) -> int:
    """命令行入口：`python -m app.main --transport streamable-http|stdio`。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="data-platform-mcp", description="只读数据 MCP 服务（Sprint 10）"
    )
    parser.add_argument(
        "--transport",
        choices=("streamable-http", "stdio"),
        default="streamable-http",
        help="streamable-http（部署形态，默认）或 stdio（本地宿主形态）",
    )
    parser.add_argument("--host", default=None, help="覆盖 MCP_HOST")
    parser.add_argument("--port", type=int, default=None, help="覆盖 MCP_PORT")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    server = build_server()
    config = server_settings(server)
    host = args.host or config.host
    port = args.port or config.port

    if args.transport == "stdio":
        # stdio：stdout 就是协议线路，日志必须走 stderr（logging 默认就是）
        log.info("MCP 服务以 stdio 启动（数据出口 %s）", config.data_api_base)
        server.run(transport="stdio")
        return 0

    _serve_http(server, config, host, port)
    return 0


def _serve_http(server: MCPServer, config: Settings, host: str, port: int) -> None:
    """以 streamable-http 形态提供服务（MCP 端点与 /healthz 同一个端口）。

    ## !! 这里踩过一个把"健康"伪装成"正常"的坑，必须写清楚 !!

    第一版实现是：

        mcp_app = server.streamable_http_app()
        app = Starlette(routes=[Mount("/", app=mcp_app)])   # ← 错

    它**能启动、`/healthz` 返回 200**，但每一次 MCP 调用都失败：

        RuntimeError: Task group is not initialized. Make sure to use run().

    原因：`streamable_http_app()` 返回的 Starlette 应用把会话任务组
    **挂在自己的 lifespan 上**（`lifespan=lambda app: session_manager.run()`）。
    而 **ASGI 的 `Mount` 不会向下传递 lifespan** —— 子应用的 lifespan
    永远不会被进入，于是 `_task_group` 一直是 `None`。
    更坏的是它**看起来是好的**：`/healthz` 是我自己加的普通路由，
    不走 session manager，所以探针全绿、`systemctl is-active` 也绿，
    只有真正说 MCP 协议的那条路是坏的。

    ## 正确地把它组装起来（本实现）

      1. **不套 Mount**：直接把 `starlette_app` 交给 uvicorn ——
         它自己的 lifespan 就会正常执行，`/mcp` 可用；
      2. 顶层 Starlette **显式提供 lifespan**，转调子应用的 lifespan，
         这样 `/healthz` 与自定义路由还在同一个端口上；
      3. 顶层应用**必须**在调用 `streamable_http_app()` 之后再构造，
         因为那个调用才会创建 session manager。

    > **教训**：一个"健康探针正常"不等于"服务真的能干活"。
    > 探针必须打到**真实协议路径**上 —— 这也是为什么验收脚本
    > 第 2 步用 `list_tools()` 而不是只看 `/healthz`。
    """
    import uvicorn
    from starlette.applications import Starlette

    # 这一步会创建 session manager，并把自定义路由（/healthz）一起放进子应用
    starlette_app = server.streamable_http_app(streamable_http_path=config.http_path)

    session_manager = server.session_manager
    assert session_manager is not None, "streamable_http_app() 之后 session manager 必须已创建"

    async def lifespan(_app: object) -> Any:
        # 转调 MCP 会话管理器的 lifespan：它负责建/拆任务组。
        # 少了这一步，/mcp 上每一次调用都会 RuntimeError（见上面的说明）。
        async with session_manager.run():
            yield

    # 顶层应用：同一个端口上既提供 MCP，也提供健康探针。
    # 路由在子应用里，这里用 Mount 只做请求转发（lifespan 由上面的 lifespan 显式接管）。
    from starlette.routing import Mount

    app = Starlette(routes=[Mount("/", app=starlette_app)], lifespan=lifespan)

    log.info(
        "MCP 服务启动：http://%s:%d%s（数据出口 %s，只读，无数据库凭据）",
        host,
        port,
        config.http_path,
        config.data_api_base,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
