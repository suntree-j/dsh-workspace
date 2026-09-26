"""Sprint 7 冒烟测试：动态只读查询节点与数据问答 Agent。

覆盖范围与分工：
    - `POST /query` 的**安全边界**：这是本 Sprint 唯一接受外部 SQL 的入口，
      一旦被绕过，前面所有只读设计都失去意义。因此四类攻击逐个验证，
      并且断言"拒绝原因是守卫给出的那一个"，而不只是"非 200"——
      500 也是非 200，但那说明服务崩了，不是守卫生效了。
    - `POST /query` 的**正常能力**：真的能查到库（不只看连通性）。
    - Agent 的**能力面**：工具齐全、提示词含铁律、契约可读。
    - `/ask` 的**未配置行为**：没有 Key 时必须明确拒绝（503），
      绝不允许"假装回答"。

Agent 的**循环逻辑**由单元测试覆盖（tests/test_agent.py），
这里只测跨进程的真实行为 —— 那才是这两个服务真实的交互方式。

依据 AGENTS.md 第 8 节：冒烟测试必须真实执行、校验数据内容，
服务未启动时优雅跳过。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

import pytest

pytestmark = pytest.mark.smoke

# 两个服务都在本机回环（脚本/测试与它们同机运行）
API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
AGENT_BASE = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8100")
TIMEOUT = 30


# ------------------------------------------------------------
# HTTP 小工具（标准库，不引入额外依赖）
# ------------------------------------------------------------
def _request(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def run_sql(sql: str, limit: int = 10) -> tuple[int, dict]:
    return _request("POST", f"{API_BASE}/query", {"sql": sql, "limit": limit})


def query_data(sql: str, limit: int = 10) -> dict:
    """执行一条应当成功的查询，返回 data 段。"""
    status, body = run_sql(sql, limit)
    assert status == 200, f"查询应当成功，实际 {status}：{body}"
    assert "data" in body and "source" in body, f"响应缺少统一信封：{body}"
    return body["data"]


@pytest.fixture(scope="session", autouse=True)
def require_query_endpoint() -> None:
    """/query 必须可用，否则跳过本模块（未部署 Sprint 7 时不应误报失败）。"""
    try:
        status, body = _request("POST", f"{API_BASE}/query", {"sql": "SELECT 1", "limit": 1})
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(
            f"数据服务未启动（{API_BASE}）：{exc}\n  请先执行： bash scripts/install-web.sh"
        )
    # SELECT 1 没有 FROM，会被守卫以 NO_TABLE 拒绝 —— 这说明 /query 存在且守卫在工作。
    # 若返回 404，则说明是旧版本服务（没有这个路由），跳过而不是报失败。
    if status == 404:
        pytest.skip("数据服务没有 /query 路由（Sprint 7 尚未部署）")
    assert status == 400, f"/query 对无表语句应返回 400（守卫拒绝），实际 {status}：{body}"


# ============================================================
# 1. 正常查询能力
# ============================================================
def test_query_returns_rows_and_envelope() -> None:
    data = query_data("SELECT COUNT(*) AS windows FROM ecommerce.ads_realtime_trade_1m")
    assert data["row_count"] == 1
    assert data["rows"][0]["windows"] > 0, "ADS 表应当有数据"
    # executed_sql 是"不伪造结果"的可核对证据，必须返回
    assert "executed_sql" in data and data["executed_sql"].strip()


def test_query_reports_lineage_tables() -> None:
    """血缘必须来自实际执行的 SQL，而不是调用方自报。"""
    status, body = run_sql("SELECT COUNT(*) AS c FROM ecommerce.dwd_trade_order_detail", 5)
    assert status == 200, body
    assert "dwd_trade_order_detail" in body["source"]["tables"]


def test_query_reconciles_with_known_total() -> None:
    """动态查询的结果必须与看板/接口的口径一致（GMV 精确到分）。

    这条是真正的数据断言：它证明 /query 不是"能返回点东西"，
    而是返回与其它接口同一个数 —— 否则 Agent 的回答会和看板对不上。
    """
    data = query_data("SELECT SUM(gmv) AS gmv FROM ecommerce.ads_realtime_trade_1m")
    total = Decimal(str(data["rows"][0]["gmv"]))

    status, body = _request("GET", f"{API_BASE}/overview")
    assert status == 200, body
    overview_total = Decimal(str(body["data"]["kpi"]["gmv"]))

    assert total == overview_total, (
        f"/query 的 GMV 合计 {total} 与 /overview 的 {overview_total} 不一致 —— "
        "同一个口径出现了两个数"
    )


def test_query_forces_limit() -> None:
    """没有 LIMIT 的语句必须被补上 LIMIT，且行数受约束。"""
    data = query_data("SELECT order_id FROM ecommerce.dwd_trade_order_detail", limit=5)
    assert data["row_count"] == 5, f"应被限制为 5 行，实际 {data['row_count']}"
    assert "LIMIT 5" in data["executed_sql"].upper().replace("limit", "LIMIT")


def test_query_accepts_both_databases() -> None:
    """实时库与离线库都要能查（Agent 需要按问题的时间范围选表）。"""
    for sql, table in (
        ("SELECT COUNT(*) AS c FROM ecommerce.ads_realtime_trade_1m", "realtime"),
        ("SELECT COUNT(*) AS c FROM lakehouse_ads.ads_batch_trade_1d", "batch"),
    ):
        status, body = run_sql(sql, 5)
        assert status == 200, f"{table} 查询失败：{body}"
        assert body["data"]["rows"][0]["c"] > 0, f"{table} 表应当有数据"


# ============================================================
# 2. 安全边界：四类攻击必须被守卫拒绝（且原因正确）
# ============================================================
@pytest.mark.parametrize(
    "sql,expected_code",
    [
        ("DELETE FROM ecommerce.ads_realtime_trade_1m", "NOT_SELECT"),
        ("UPDATE ecommerce.ads_realtime_trade_1m SET gmv = 0", "NOT_SELECT"),
        ("DROP TABLE ecommerce.ads_realtime_trade_1m", "NOT_SELECT"),
        ("SELECT * FROM mysql.user", "TABLE_NOT_ALLOWED"),
        ("SELECT * FROM ecommerce.secret_table", "TABLE_NOT_ALLOWED"),
        ("SELECT 1; SELECT 2", "MULTI_STATEMENT"),
        ("SELECT * FROM ecommerce.ads_realtime_trade_1m -- x", "COMMENT"),
        ("SELECT * FROM ecommerce.ads_realtime_trade_1m /* x */", "COMMENT"),
    ],
)
def test_query_rejects_dangerous_sql(sql: str, expected_code: str) -> None:
    """被拒 + **拒绝原因正确**。

    只断言"非 200"是不够的：500 也是非 200，但那说明服务出错了，
    而不是守卫生效。必须同时校验 HTTP 400 与 error.code。
    """
    status, body = run_sql(sql, 10)
    assert status == 400, f"应当返回 400，实际 {status}：{body}"
    assert body.get("error", {}).get("code") == expected_code, (
        f"拒绝原因应为 {expected_code}，实际 {body.get('error')}"
    )


def test_query_requires_from_clause() -> None:
    """无 FROM 的语句没有可识别的表，必须被拒（防止绕过白名单）。"""
    status, body = run_sql("SELECT 1", 10)
    assert status == 400
    assert body["error"]["code"] == "NO_TABLE"


def test_query_rejects_empty_sql() -> None:
    status, body = run_sql("   ", 10)
    assert status == 400
    assert body["error"]["code"] == "EMPTY_SQL"


def test_query_rejects_over_cap_limit() -> None:
    """limit 超过接口上限时由 FastAPI 校验层拒绝（422）。"""
    status, _ = run_sql("SELECT COUNT(*) AS c FROM ecommerce.ads_realtime_trade_1m", limit=999999)
    assert status == 422, "超出上限的 limit 应被参数校验拒绝"


# ============================================================
# 3. Agent 能力面
# ============================================================
@pytest.fixture(scope="session")
def agent_available() -> None:
    try:
        status, _ = _request("GET", f"{AGENT_BASE}/health")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"Agent 未启动（{AGENT_BASE}）：{exc}\n  请先执行： bash scripts/deploy-agent.sh")
    if status != 200:
        pytest.skip(f"Agent /health 返回 {status}")


def test_agent_health_envelope(agent_available: None) -> None:
    status, body = _request("GET", f"{AGENT_BASE}/health")
    assert status == 200
    # 与数据服务同一种响应形状，前端才能用一套代码处理
    assert "data" in body and "source" in body, f"Agent 未使用统一信封：{body}"
    assert body["data"]["data_api"]["ok"] is True, "Agent 应能连通数据服务"
    assert "configured" in body["data"]["llm"]


def test_agent_exposes_exactly_four_tools(agent_available: None) -> None:
    """工具集合就是权限边界，增删都必须是有意为之。"""
    status, body = _request("GET", f"{AGENT_BASE}/tools")
    assert status == 200
    names = [t["name"] for t in body["data"]["tools"]]
    assert names == ["metrics_lookup", "tables_lookup", "sql_query", "reconciliation"], names


def test_agent_prompt_states_hard_rules(agent_available: None) -> None:
    """提示词必须可审计，且含三条铁律。"""
    status, body = _request("GET", f"{AGENT_BASE}/prompt")
    assert status == 200
    text = body["data"]["system_prompt"]
    assert "只允许 SELECT" in text
    assert "禁止编造数据" in text
    assert "lakehouse_ads" in text, "提示词必须交代离线库，否则模型会选错粒度"


def test_agent_ask_refuses_without_key(agent_available: None) -> None:
    """/ask 在未配置 LLM 时必须明确拒绝，绝不"假装回答"。

    这条是行为契约，无论 Key 有没有配都要成立：
      - 没配 → 503 + LLM_NOT_CONFIGURED（并给出配置步骤）
      - 配了 → 200，且回答里带 tables / executed_sql 等可核对痕迹
    """
    status, body = _request("GET", f"{AGENT_BASE}/health")
    configured = bool(body.get("data", {}).get("llm", {}).get("configured"))

    status, body = _request("POST", f"{AGENT_BASE}/ask", {"question": "最近一周每天的 GMV 是多少？"})

    if not configured:
        assert status == 503, f"未配置 LLM 时应返回 503，实际 {status}：{body}"
        assert body["error"]["code"] == "LLM_NOT_CONFIGURED"
        assert "LLM_API_KEY" in body["error"]["detail"], "错误详情应说明配置方法"
        return

    assert status == 200, f"已配置 LLM 时应返回 200，实际 {status}：{body}"
    data = body["data"]
    assert data["answer"].strip(), "回答不应为空"
    assert data["model"], "应报告使用的模型"
    # 「不得伪造查询结果」的可核对证据
    assert "steps" in data
    assert isinstance(data["tables"], list)
    assert isinstance(data["executed_sql"], list)


def test_agent_ask_validates_question_length(agent_available: None) -> None:
    status, _ = _request("POST", f"{AGENT_BASE}/ask", {"question": ""})
    assert status == 422, "空问题应被参数校验拒绝"
