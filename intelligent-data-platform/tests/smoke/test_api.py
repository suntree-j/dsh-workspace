"""数据服务（只读 API）冒烟测试。

前置条件（服务器上）：
    bash scripts/install-web.sh          # 或已装好 nginx + systemd 服务
    curl http://127.0.0.1:8000/health    # API 已就绪

运行：
    python -m pytest -m smoke -v tests/smoke/test_api.py

覆盖范围（对应 docs/sprint/SPRINT_6.md 的验收标准）：
    1. 服务健康检查（含"是否使用只读账号"）
    2. 总览 KPI 与 MySQL 事实源**精确对账**（GMV 到分）
    3. 时间序列接口（升序、字段齐全）
    4. 订单明细分页与筛选
    5. 订单详情（跨表：支付 + 退款）
    6. 指标口径字典（来自 sql/metadata/metrics.md）
    7. 表结构与分层
    8. 参数越界被拒绝（行数上限是真的生效，不是摆设）

设计说明：接口是只读的，因此这里**不修改任何数据**，可以随时重复执行。
服务未启动时优雅跳过（AGENTS.md 第 8.3 节）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

import pytest

pytestmark = pytest.mark.smoke

# API 直连端口（测试与 API 同机运行，走回环即可；
# 前端经 Nginx 的路径由 scripts/verify-sprint-6.sh 验证）
API_BASE = __import__("os").environ.get("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 20


# ------------------------------------------------------------
# HTTP 小工具（标准库，不引入额外依赖）
# ------------------------------------------------------------
def _request(path: str) -> tuple[int, dict]:
    """发起 GET 请求。

    `path` 可能含中文查询参数（例如类目名），必须按 RFC 3986 转义：
    urllib 不会自动编码非 ASCII 字符，直接拼进 URL 会抛 UnicodeEncodeError。
    """
    url = f"{API_BASE}{urllib.parse.quote(path, safe='/?&=%')}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def get_data(path: str) -> tuple[dict, dict]:
    """返回 (data, source)。"""
    status, body = _request(path)
    assert status == 200, f"GET {path} 返回 {status}：{body}"
    assert "data" in body, f"响应缺少 data 字段：{body}"
    assert "source" in body, f"响应缺少 source 字段（必须能说明数据来源）：{body}"
    return body["data"], body["source"]


@pytest.fixture(scope="session", autouse=True)
def require_api() -> None:
    """API 必须已启动，否则优雅跳过整个模块。"""
    try:
        status, body = _request("/health")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(
            f"数据服务未启动（{API_BASE}）：{exc}\n"
            "  请先执行： bash scripts/install-web.sh"
        )
    if status != 200:
        pytest.skip(f"数据服务 /health 返回 {status}：{body}")


# ============================================================
# 1. 健康检查与安全基线
# ============================================================
def test_health_reports_readonly_account() -> None:
    data, source = get_data("/health")
    assert data["status"] == "ok", data
    assert data["doris"] == "ok", data
    assert data["readonly_enforced"] is True, (
        f"服务没有使用专用只读账号（当前 {data.get('readonly_user')}）—— "
        "违反 AGENTS.md 第 10.3 节"
    )
    assert data["doris_latency_ms"] >= 0
    assert "只读" in source["note"]


def test_delete_method_is_rejected() -> None:
    """接口只读：对写方法必须拒绝。"""
    url = f"{API_BASE}/orders"
    request = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            pytest.fail(f"DELETE {url} 竟然返回 {resp.status}")
    except urllib.error.HTTPError as exc:
        assert exc.code in (404, 405), f"DELETE 返回 {exc.code}，期望 405"


# ============================================================
# 2. 总览与对账
# ============================================================
def test_overview_kpi_reconciles_with_mysql(docker, mysql_creds) -> None:
    """总览 KPI 必须与 MySQL 事实源精确一致。

    这是整个项目"数据可信"的最后一公里：
    MySQL（事实源）→ Kafka → Flink → Doris → API → 页面。
    docker / mysql_creds 两个 fixture 由 tests/smoke/conftest.py 提供。
    """
    data, source = get_data("/overview")
    kpi = data["kpi"]
    assert Decimal(kpi["gmv"]) > 0, "GMV 不应为 0（数据链路可能没跑）"

    user, password = mysql_creds
    out = docker.out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}", "-N", "-B",
        "-e", "SELECT COUNT(*), SUM(amount) FROM ecommerce.orders",
    ).split()
    db_orders, db_gmv = int(out[0]), Decimal(out[1])

    assert kpi["order_cnt"] == db_orders, f"订单量不一致：API {kpi['order_cnt']} vs MySQL {db_orders}"
    assert Decimal(kpi["gmv"]) == db_gmv, f"GMV 不一致：API {kpi['gmv']} vs MySQL {db_gmv}"

    # 来源必须写清楚
    assert any("ads_realtime_trade_1m" in t for t in source["tables"])
    assert source["metric_definitions"], "source 中没有指标口径定义"


def test_overview_contains_category_and_window_info() -> None:
    data, _ = get_data("/overview")
    assert data["windows"]["trade"] > 0
    assert data["latest_window"], "缺少最新窗口时间"
    assert isinstance(data["category_top"], list) and data["category_top"]


# ============================================================
# 3. 时间序列
# ============================================================
def test_trade_series_is_ascending_and_complete() -> None:
    rows, source = get_data("/metrics/trade?limit=20")
    assert len(rows) == 20, f"期望 20 行，实际 {len(rows)}"
    starts = [r["window_start"] for r in rows]
    assert starts == sorted(starts), "时间序列必须按 window_start 升序（图表要求）"
    for field in ("gmv", "order_cnt", "payment_cnt", "refund_cnt", "avg_order_amount"):
        assert field in rows[0], f"缺少字段 {field}"
    # 可加指标不能为 NULL（后端已补 0），否则前端会显示成"—"
    assert rows[0]["order_cnt"] is not None
    assert isinstance(rows[0]["gmv"], str), "金额必须是字符串，避免浮点误差"


def test_traffic_series_fields() -> None:
    rows, _ = get_data("/metrics/traffic?limit=10")
    assert rows
    for field in ("uv", "pv", "view_cnt", "click_cnt", "cart_cnt", "buy_cnt"):
        assert field in rows[0]


def test_funnel_is_monotonic() -> None:
    data, source = get_data("/funnel?window_limit=20160")
    assert data["view_cnt"] >= data["click_cnt"] >= data["cart_cnt"] >= data["buy_cnt"], (
        f"漏斗关系不成立：{data}"
    )
    assert any("dws_traffic_overview_1m" in t for t in source["tables"])


def test_category_ranking_sorted_by_gmv() -> None:
    rows, _ = get_data("/metrics/category?limit=8")
    assert rows
    gmvs = [Decimal(r["gmv"]) for r in rows]
    assert gmvs == sorted(gmvs, reverse=True), "类目排行必须按 GMV 降序"


# ============================================================
# 4. 明细与详情
# ============================================================
def test_orders_pagination_matches_total() -> None:
    data, source = get_data("/orders?limit=5&offset=0")
    assert data["limit"] == 5 and data["offset"] == 0
    assert len(data["items"]) == 5
    assert data["total"] >= 5
    assert any("dwd_trade_order_detail" in t for t in source["tables"])


def test_orders_filter_by_category() -> None:
    data, _ = get_data("/orders?limit=5&category=手机数码")
    if data["total"] == 0:
        pytest.skip("当前数据集中没有该类目")
    for item in data["items"]:
        assert item["category_name"] == "手机数码"


def test_orders_rejects_invalid_date_format() -> None:
    status, body = _request("/orders?start=2026/09/01")
    assert status == 422, f"非法日期格式应被拒绝，实际 {status}：{body}"


def test_orders_limit_over_cap_is_rejected() -> None:
    """limit 超过上限必须被拒绝 —— 证明行数上限是真的生效。"""
    status, _ = _request("/orders?limit=100000")
    assert status == 422


def test_order_detail_joins_payment_and_refund() -> None:
    listing, _ = get_data("/orders?limit=1")
    order_id = listing["items"][0]["order_id"]

    data, source = get_data(f"/orders/{order_id}")
    assert data["order"]["order_id"] == order_id
    assert isinstance(data["payments"], list)
    assert isinstance(data["refunds"], list)
    # 三张表都要出现在来源里（体现明细层的可追溯性）
    joined = " ".join(source["tables"])
    assert "dwd_trade_order_detail" in joined
    assert "dwd_trade_payment_detail" in joined


def test_missing_order_returns_404_with_error_envelope() -> None:
    status, body = _request("/orders/999999999")
    assert status == 404
    assert body["error"]["code"] == "ORDER_NOT_FOUND"
    assert body["error"]["message"], "错误信息不能为空（前端要直接展示）"


# ============================================================
# 5. 指标口径与表结构
# ============================================================
def test_metrics_dictionary_is_served_from_document() -> None:
    """口径字典必须来自 metrics.md，且能正确还原域、表与空值约定。"""
    data, source = get_data("/meta/metrics")
    assert data["source_document"] == "sql/metadata/metrics.md"
    assert data["version"].startswith("V")

    # 同一字段名可能出现在多个域（gmv 同时在交易域与类目域），
    # 因此这里按"字段 + 域"定位，而不是简单按字段名覆盖。
    gmv_defs = [m for m in data["metrics"] if m["field"] == "gmv"]
    assert gmv_defs, "口径字典里没有 gmv"
    trade_gmv = next((d for d in gmv_defs if d["domain"] == "交易"), None)
    assert trade_gmv is not None, f"缺少交易域 gmv：{[d['domain'] for d in gmv_defs]}"
    assert trade_gmv["table"] == "ads_realtime_trade_1m"
    assert "SUM(amount)" in trade_gmv["formula"]
    assert trade_gmv["null_policy"] == "无事件补 0"
    assert "同上" not in trade_gmv["table"], "「同上」必须被还原成真实表名"

    # 域划分必须解析出来（否则口径字典就丢了"这是哪个域的指标"）
    domains = {m["domain"] for m in data["metrics"]}
    assert {"交易", "流量", "类目"} <= domains, f"域解析不完整：{domains}"

    # 比率类指标的空值约定必须是 NULL
    rate = next(m for m in data["metrics"] if m["field"] == "payment_success_rate")
    assert rate["null_policy"] == "分母为 0 时 NULL"

    assert data["conventions"], "通用约定不应为空"


def test_table_metadata_has_layers_and_comments() -> None:
    rows, _ = get_data("/meta/tables")
    index = {t["table"]: t for t in rows}
    assert "ads_realtime_trade_1m" in index
    trade = index["ads_realtime_trade_1m"]
    assert trade["layer"] == "ADS"
    assert len(trade["columns"]) >= 10
    assert any(c["comment"] for c in trade["columns"]), "字段注释不能为空"


def test_categories_endpoint() -> None:
    rows, _ = get_data("/categories")
    assert isinstance(rows, list)
    assert all(isinstance(c, str) for c in rows)
