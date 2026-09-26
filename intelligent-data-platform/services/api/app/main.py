"""数据服务（FastAPI）：只读指标 API。

定位（对应 AGENTS.md 第 10 节 Agent 安全规范）：
    这是「数据后台」—— 前端 Dashboard 与未来的 Agent 都通过它读数据。
    它在架构上就**没有写权限**：只用 Doris 只读账号，且所有 SQL 先过安全守卫。

为什么用 FastAPI 而不是 Spring Boot：
    Sprint 6 的设计方案里写的是 "FastAPI / Spring Boot 二选一"。
    本项目的实时链路（Flink SQL、数据生成器）已经是 Python 生态，
    选 FastAPI 可以让"数据 + 服务"共用同一套语言与 venv，
    不必为了一个只读接口再引入 JVM 技术栈（违反"不偷偷增加技术栈"）。

启动：
    uvicorn app.main:app --host 127.0.0.1 --port 8000
    （生产由 systemd 托管，见 deploy/systemd/data-platform-api.service）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import ConfigError, Settings, load_settings
from .doris import DorisClient, DorisUnavailable
from .envelope import envelope, error_body, now_iso
from .metrics_doc import MetricsDoc, load_metrics_doc
from .repository import Repository
from .sqlguard import SqlRejected

# ------------------------------------------------------------
# 应用与依赖
# ------------------------------------------------------------
try:
    SETTINGS: Settings = load_settings()
    CONFIG_ERROR: str = ""
except ConfigError as exc:  # 配置缺失：服务照常启动，但接口返回明确错误
    SETTINGS = None  # type: ignore[assignment]
    CONFIG_ERROR = str(exc)

app = FastAPI(
    title=SETTINGS.title if SETTINGS else "数据服务",
    version=SETTINGS.version if SETTINGS else "0.0.0",
    description=(
        "批流一体智能数据分析平台的只读数据服务。\n\n"
        "所有接口遵循同一响应信封，并在 `source` 中声明数据来源（表、指标口径、时间范围）。"
    ),
    root_path="/data/api",  # 经 Nginx 反向代理后的对外路径，保证 /docs 链接正确
)

# 演示环境：只读接口允许跨域，便于本地静态页面直接联调。
# 注意：这里开放的只有 SELECT 能力，且带行数上限与超时。
#
# allow_methods 里必须带上 POST：/query 用 POST 承载 SQL 语句（原因见该接口的说明）。
# 放开 POST 不改变"只读"这一性质 —— 写操作在 SQL 守卫与数据库权限两层都被拦住。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_metrics_cache: dict[str, Any] = {"mtime": None, "doc": None}


def get_metrics_doc() -> MetricsDoc:
    """读取指标口径文档（按 mtime 缓存）。

    缓存键用文件修改时间：改了 metrics.md 不必重启服务，
    但也不会每次请求都重新解析。
    """
    if SETTINGS is None:
        raise DorisUnavailable(CONFIG_ERROR)
    path: Path = SETTINGS.metrics_doc
    mtime = path.stat().st_mtime if path.is_file() else None
    if _metrics_cache["doc"] is None or _metrics_cache["mtime"] != mtime:
        _metrics_cache["doc"] = load_metrics_doc(path)
        _metrics_cache["mtime"] = mtime
    return _metrics_cache["doc"]


def get_repository() -> Repository:
    if SETTINGS is None:
        raise DorisUnavailable(CONFIG_ERROR)
    return Repository(DorisClient(SETTINGS))


# ------------------------------------------------------------
# 统一异常处理
# ------------------------------------------------------------
@app.exception_handler(SqlRejected)
async def _handle_sql_rejected(_: Request, exc: SqlRejected) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_body(exc.code, exc.message, exc.detail),
    )


@app.exception_handler(DorisUnavailable)
async def _handle_doris_unavailable(_: Request, exc: DorisUnavailable) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body("DORIS_UNAVAILABLE", "数据仓库暂时不可用，请稍后重试", str(exc)),
    )


@app.exception_handler(ValueError)
async def _handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_body("INTERNAL_DATA_ERROR", "数据处理失败", str(exc)),
    )


def _limit(value: int, settings: Settings, default: int | None = None) -> int:
    """把 limit 收敛到 [1, max_limit]，避免一次拉爆内存。"""
    base = value or default or settings.default_limit
    return max(1, min(int(base), settings.max_limit))


# ------------------------------------------------------------
# 接口
# ------------------------------------------------------------
@app.get("/", summary="接口索引")
def index() -> dict[str, Any]:
    return envelope(
        {
            "service": "数据服务（只读）",
            "version": SETTINGS.version if SETTINGS else "unknown",
            "endpoints": [
                "/health", "/overview", "/metrics/trade", "/metrics/traffic",
                "/metrics/category", "/funnel", "/orders", "/orders/{order_id}",
                "/meta/metrics", "/meta/tables", "/categories",
                "/batch/overview", "/batch/reconcile",
                "POST /query（Agent 专用：动态只读 SQL）",
            ],
            "docs": "/docs",
        },
        note=(
            "所有接口均为只读（Doris 只读账号 + SQL 安全守卫）；"
            "/batch/* 为离线链路；POST /query 是 LLM Agent 的执行入口，"
            "同样只放行 SELECT"
        ),
    )


@app.get("/health", summary="健康检查")
def health() -> dict[str, Any]:
    """服务与数据仓库连通性。

    同时报告是否使用了专用只读账号 —— 这是安全底线，
    不应该等到出事故才发现服务在用 root 连库。
    """
    if SETTINGS is None:
        return envelope({"status": "degraded", "doris": "unconfigured",
                         "message": CONFIG_ERROR, "server_time": now_iso()})
    elapsed = DorisClient(SETTINGS).ping()
    return envelope(
        {
            "status": "ok",
            "doris": "ok",
            "doris_latency_ms": elapsed,
            "doris_host": f"{SETTINGS.doris_host}:{SETTINGS.doris_port}",
            "readonly_user": SETTINGS.doris_user,
            "readonly_enforced": SETTINGS.is_readonly_user,
            "version": SETTINGS.version,
            "server_time": now_iso(),
        },
        tables=[],
        note="只读账号 + SQL 守卫（仅 SELECT、强制 LIMIT）",
    )


@app.get("/overview", summary="总览 KPI")
def overview(
    window_limit: int = Query(60, ge=1, le=20160, description="类目排行的回看窗口数（分钟）"),
) -> dict[str, Any]:
    """总览页数据：全量 KPI + 类目排行 + 窗口新鲜度。"""
    repo = get_repository()
    doc = get_metrics_doc()
    assert SETTINGS is not None

    trade, trade_tables, trade_fields = repo.trade_kpi()
    traffic, traffic_tables, traffic_fields = repo.traffic_kpi()
    distinct, distinct_tables, distinct_fields = repo.distinct_users()
    stats, stats_tables = repo.window_stats()
    top, top_tables, top_fields = repo.category_top(limit=5)

    kpi = {**(trade or {}), **(traffic or {}), **(distinct or {})}
    return envelope(
        {
            "kpi": kpi,
            "windows": {
                "trade": stats.get("trade"),
                "traffic": stats.get("traffic"),
                "category": stats.get("category"),
            },
            "latest_window": stats.get("latest_window"),
            "earliest_window": stats.get("earliest_window"),
            "category_top": top,
        },
        tables=trade_tables + traffic_tables + distinct_tables + stats_tables + top_tables,
        metric_definitions=doc.definitions_for(
            [f for f in trade_fields + traffic_fields + distinct_fields + top_fields]
        ),
        time_range={"start": stats.get("earliest_window"), "end": stats.get("latest_window")},
        note=(
            "可加指标（GMV/订单量/支付/退款/PV 等）为全量口径：对所有 1 分钟窗口求和；"
            "去重类指标（UV、下单用户数）取自 DWD 明细的 COUNT(DISTINCT user_id)，"
            "**不能**用逐窗求和（那样得到的是人次）。"
        ),
    )


@app.get("/metrics/trade", summary="交易指标时间序列")
def metrics_trade(limit: int = Query(0, ge=0, le=1000)) -> dict[str, Any]:
    repo = get_repository()
    doc = get_metrics_doc()
    assert SETTINGS is not None
    rows, tables, fields = repo.trade_series(_limit(limit, SETTINGS))
    return envelope(
        rows,
        tables=tables,
        metric_definitions=doc.definitions_for(fields),
        time_range={
            "start": rows[0]["window_start"] if rows else None,
            "end": rows[-1]["window_start"] if rows else None,
        },
        note="按 window_start 升序返回最近 N 个 1 分钟窗口",
    )


@app.get("/metrics/traffic", summary="流量指标时间序列")
def metrics_traffic(limit: int = Query(0, ge=0, le=1000)) -> dict[str, Any]:
    repo = get_repository()
    doc = get_metrics_doc()
    assert SETTINGS is not None
    rows, tables, fields = repo.traffic_series(_limit(limit, SETTINGS))
    return envelope(
        rows,
        tables=tables,
        metric_definitions=doc.definitions_for(fields),
        time_range={
            "start": rows[0]["window_start"] if rows else None,
            "end": rows[-1]["window_start"] if rows else None,
        },
        note="按 window_start 升序返回最近 N 个 1 分钟窗口",
    )


@app.get("/metrics/category", summary="类目销售排行")
def metrics_category(
    limit: int = Query(10, ge=1, le=100),
    window_limit: int = Query(0, ge=0, le=20160, description="0 表示全量口径"),
) -> dict[str, Any]:
    repo = get_repository()
    doc = get_metrics_doc()
    assert SETTINGS is not None
    rows, tables, fields = repo.category_top(limit=limit, window_limit=window_limit or None)
    return envelope(
        rows,
        tables=tables,
        metric_definitions=doc.definitions_for(fields),
        note=("全量口径" if not window_limit else f"最近 {window_limit} 分钟窗口口径"),
    )


@app.get("/funnel", summary="行为漏斗")
def funnel(window_limit: int = Query(1440, ge=1, le=20160)) -> dict[str, Any]:
    repo = get_repository()
    doc = get_metrics_doc()
    data, tables, fields = repo.funnel(window_limit)
    return envelope(
        data,
        tables=tables,
        metric_definitions=doc.definitions_for(fields),
        note=f"最近 {window_limit} 分钟窗口的行为漏斗（浏览→点击→加购→购买）",
    )


@app.get("/orders", summary="订单明细分页")
def orders(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
    category: str | None = Query(None, max_length=50),
    start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    repo = get_repository()
    doc = get_metrics_doc()
    assert SETTINGS is not None
    data, tables, fields = repo.orders(_limit(limit, SETTINGS), offset, category, start, end)
    return envelope(
        data,
        tables=tables,
        metric_definitions=doc.definitions_for(fields),
        time_range={"start": start, "end": end},
        note="DWD 明细层，按事件时间倒序；分页由 limit/offset 控制",
    )


@app.get("/orders/{order_id}", summary="订单详情（含支付与退款）")
def order_detail(order_id: int) -> JSONResponse:
    repo = get_repository()
    data, tables, fields = repo.order_detail(order_id)
    if data is None:
        return JSONResponse(
            status_code=404,
            content=error_body("ORDER_NOT_FOUND", f"订单 {order_id} 不存在", "请检查订单号是否正确"),
        )
    return JSONResponse(
        content=envelope(
            data,
            tables=tables,
            metric_definitions=get_metrics_doc().definitions_for(fields),
            note="订单 + 支付 + 退款三表关联（体现 DWD 明细层的可追溯性）",
        )
    )


@app.get("/categories", summary="类目列表")
def categories() -> dict[str, Any]:
    rows, tables = get_repository().categories()
    return envelope(rows, tables=tables, note="用于前端筛选下拉框")


@app.get("/meta/metrics", summary="指标口径字典")
def meta_metrics() -> dict[str, Any]:
    """指标口径字典（解析自 sql/metadata/metrics.md）。

    这是「Agent 必须知道指标定义」的落地：口径只有一份，
    接口返回的就是文档里的原文，不存在第二份副本。
    """
    doc = get_metrics_doc()
    return envelope(
        {
            "metrics": [m.as_dict() for m in doc.metrics],
            "conventions": [c.as_dict() for c in doc.conventions],
            "version": doc.version,
            "updated_at": doc.updated_at,
            "source_document": "sql/metadata/metrics.md",
        },
        tables=[],
        note="口径唯一权威来源：sql/metadata/metrics.md（接口直接解析该文档）",
    )


@app.get("/meta/tables", summary="表结构与分层")
def meta_tables() -> dict[str, Any]:
    from .sqlguard import BUSINESS_TABLES

    ordered = sorted(BUSINESS_TABLES)
    rows, tables = get_repository().table_metadata(ordered)
    return envelope(
        rows,
        tables=tables,
        note=(
            "来自 Doris information_schema（只读），含字段注释与所属分层；"
            "同时覆盖实时链路库 ecommerce 与离线链路库 lakehouse_ads"
        ),
    )


# ============================================================
# 动态只读查询（Sprint 7：LLM Agent 的执行入口）
# ============================================================
class SqlQueryRequest(BaseModel):
    """一次动态只读查询的请求体。"""

    sql: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="只允许 SELECT；守卫会拒绝多语句、注释、DDL/DML 与白名单外的表",
    )
    limit: int = Field(
        200,
        ge=1,
        le=1000,
        description="本次查询的行数上限（守卫会在此基础上再收敛一次）",
    )


@app.post("/query", summary="执行只读 SQL（Agent 专用入口）")
def run_query(payload: SqlQueryRequest) -> dict[str, Any]:
    """执行一条**外部传入**的只读 SQL，并返回结果 + 血缘 + 实际执行的语句。

    ## 为什么这个接口用 POST，而其它接口全是 GET

    项目里其它接口都是 GET（只读语义最直白，也方便直接贴链接）。
    这里破例用 POST，原因是**长度**而不是语义：
    LLM 生成的 SQL 常常带多列、多个聚合与 GROUP BY，实测很容易超过 2KB；
    而 GET 的 SQL 放在查询串里，受 nginx `large_client_header_buffers`
    与各级代理的 URL 长度限制（通常几 KB），会出现"短问题能问、复杂问题 502"
    这种极难排查的失败模式。POST 把语句放在请求体里，没有这个上限。

    语义上它仍然是**只读**的：语句必须过 `sqlguard`，
    只放行 SELECT、表白名单、强制 LIMIT，且连接用的是只读账号 `agent_ro`。
    Nginx 侧对该路径单独放行 POST，其余路径仍是 `limit_except GET HEAD OPTIONS`。

    ## 为什么返回 `executed_sql`

    守卫会改写语句（补/收 LIMIT）。返回**实际执行的那一条**，
    才能满足 `AGENTS.md` 第 10.1 节「Agent 不得伪造查询结果」——
    "我查了这句话"必须可核对，而不是靠调用方自述。

    ## 为什么表名由服务端从 SQL 里提取

    血缘信息用于展示与审计。如果让调用方自报读了哪些表，
    这个字段就失去了可信度；统一从实际执行的 SQL 里正则提取，
    与守卫用的是同一套规则（见 `sqlguard.extract_tables`）。
    """
    repo = get_repository()
    doc = get_metrics_doc()

    data, tables, executed_sql = repo.sql_query(payload.sql, max_limit=payload.limit)

    # 从结果列里反推可能涉及的指标口径，让回答能引用"这个数是怎么定义的"。
    # 只做字段名匹配，不做语义推断 —— 猜错口径比不给出处更糟。
    columns: list[str] = []
    if data["rows"]:
        columns = list(data["rows"][0].keys())

    return envelope(
        data,
        tables=tables,
        metric_definitions=doc.definitions_for(columns),
        note=(
            f"动态只读查询，实际执行：{executed_sql}；"
            "语句已通过 SQL 守卫（仅 SELECT、表白名单、强制 LIMIT），"
            "并使用只读账号，写操作会被 Doris 拒绝。"
        ),
    )


# ============================================================
# 离线（批处理）链路 —— Sprint 3
#
# 为什么单独一组接口而不是给已有接口加 source 参数：
#   两条链路的**时间语义不同**：实时是 1 分钟窗口（秒级新鲜度），
#   离线是按天（准确、可回溯）。把它们塞进同一个响应里，
#   前端必然要写一堆 if 去解释"这个数是哪来的"。
#   分开之后，路径本身就是数据来源（/batch/* 即离线），
#   血缘信息（tables）也随之清晰。
# ============================================================
@app.get("/batch/overview", summary="离线链路总览")
def batch_overview(
    days: int = Query(60, ge=1, le=1000, description="返回最近多少个自然日的按天指标"),
) -> dict[str, Any]:
    """离线链路（Spark 分层计算 → 湖仓 → Doris）的总览数据。

    与 `/overview` 的区别：这里的数据来自批处理，按天粒度，准确且可回溯；
    `/overview` 来自实时链路，按分钟粒度，秒级新鲜但可能仍在追赶。
    """
    repo = get_repository()
    doc = get_metrics_doc()

    kpi, kpi_tables, kpi_fields = repo.batch_trade_kpi()
    daily, daily_tables, daily_fields = repo.batch_trade_daily(limit=days)
    top, top_tables, top_fields = repo.batch_category_top(limit=10)

    latest_day = daily[-1]["dt"] if daily else None
    earliest_day = daily[0]["dt"] if daily else None

    return envelope(
        {
            "kpi": kpi or {},
            "daily": daily,
            "category_top": top,
            "days": len(daily),
        },
        tables=kpi_tables + daily_tables + top_tables,
        metric_definitions=doc.definitions_for(
            [f for f in kpi_fields + daily_fields + top_fields]
        ),
        time_range={"start": earliest_day, "end": latest_day},
        note=(
            "离线链路指标由 Spark 分层计算（ODS→DWD→DWS→ADS）后装载进 Doris，"
            "口径与实时链路**逐字相同**（见 sql/metadata/metrics.md），"
            "两者逐窗口对账结果见 /batch/reconcile。"
        ),
    )


@app.get("/batch/reconcile", summary="批流对账结论")
def batch_reconcile() -> dict[str, Any]:
    """实时链路与离线链路的逐窗口对账结论。

    这是"数据是否可信"的直接证据：同一条业务事实，
    走 Flink 实时链路与走 Spark 离线链路，算出来的指标必须完全一致。
    差异明细逐窗口落盘（lakehouse_ads.ads_reconcile_trade_1m），
    这里返回最近一批的结论与差异最大的若干窗口。

    口径说明：**查询失败即失败**，不做任何"看起来成功"的兜底
    （AGENTS.md 第 10.3 节）。
    """
    repo = get_repository()
    doc = get_metrics_doc()
    data, tables, fields = repo.reconciliation()

    latest = data.get("latest") or {}
    totals = data.get("totals") or {}
    realtime = totals.get("realtime") or {}
    batch = totals.get("batch") or {}

    return envelope(
        {
            **data,
            "deltas": {
                "gmv": _decimal_delta(batch.get("gmv"), realtime.get("gmv")),
                "order_cnt": _decimal_delta(batch.get("order_cnt"), realtime.get("order_cnt")),
                "payment_amount": _decimal_delta(
                    batch.get("payment_amount"), realtime.get("payment_amount")
                ),
                "refund_amount": _decimal_delta(
                    batch.get("refund_amount"), realtime.get("refund_amount")
                ),
            },
        },
        tables=tables,
        metric_definitions=doc.definitions_for(fields),
        time_range={"start": latest.get("scope_start"), "end": latest.get("scope_end")},
        note=(
            "对账由 Spark 作业完成（infrastructure/spark/jobs/reconcile_batch_realtime.py），"
            "服务层只读结论。不一致窗口数 = 0 表示两条链路在这些窗口上完全一致。"
        ),
    )


def _decimal_delta(batch_value: Any, realtime_value: Any) -> Any:
    """离线值减实时值；任一侧缺失时返回 None（而不是假装是 0）。

    为什么不用 `or 0` 兜底：
        把"没有数据"当成 0 会让差异看起来是 0，从而**掩盖真实的不一致**。
        这正是 AGENTS.md 第 10.3 节「失败即失败」要防的事。
    """
    if batch_value is None or realtime_value is None:
        return None
    try:
        from decimal import Decimal

        return str(Decimal(str(batch_value)) - Decimal(str(realtime_value)))
    except Exception:  # noqa: BLE001 - 值不是数字时如实返回 None，不猜
        return None
