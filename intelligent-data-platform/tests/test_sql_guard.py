"""SQL 安全守卫与指标口径解析的单元测试（不需要任何容器）。

对应 AGENTS.md 第 10.2 节的"必须拒绝"清单：
    INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE、GRANT/REVOKE/SET/USE/CALL/LOAD/EXPORT、
    多语句（分号拼接）、注释注入（-- 与 /* */）、INTO OUTFILE。
这里逐条给出用例 —— 安全规则必须有测试，否则"写了但没生效"没人知道。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api.app.metrics_doc import load_metrics_doc
from services.api.app.sqlguard import SqlRejected, validate_select

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
METRICS_MD = REPO_ROOT / "sql" / "metadata" / "metrics.md"


# ============================================================
# 1. 合法查询
# ============================================================
def test_valid_select_is_accepted_and_limit_added() -> None:
    sql = "SELECT order_id, amount FROM dwd_trade_order_detail ORDER BY order_id"
    result = validate_select(sql, max_limit=100)
    assert result.endswith("LIMIT 100")
    assert "dwd_trade_order_detail" in result


def test_limit_is_clamped_to_max() -> None:
    result = validate_select(
        "SELECT order_id FROM dwd_trade_order_detail LIMIT 99999", max_limit=200
    )
    assert result.endswith("LIMIT 200")


def test_limit_offset_form_keeps_offset_and_clamps_count() -> None:
    result = validate_select(
        "SELECT order_id FROM dwd_trade_order_detail ORDER BY order_id LIMIT 500 OFFSET 40",
        max_limit=50,
    )
    assert result.endswith("LIMIT 50 OFFSET 40")


def test_limit_comma_form_clamps_count_only() -> None:
    """MySQL 方言 LIMIT skip, count —— 前一个是偏移量，不能被当成行数上限截断。"""
    result = validate_select(
        "SELECT order_id FROM dwd_trade_order_detail LIMIT 40, 500", max_limit=50
    )
    assert result.endswith("LIMIT 40, 50")


def test_small_limit_is_untouched() -> None:
    sql = "SELECT window_start FROM ads_realtime_trade_1m LIMIT 5"
    assert validate_select(sql, max_limit=1000).endswith("LIMIT 5")


# ============================================================
# 2. 必须拒绝的语句
# ============================================================
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dwd_trade_order_detail",
        "UPDATE dwd_trade_order_detail SET amount = 0",
        "INSERT INTO dwd_trade_order_detail (order_id) VALUES (1)",
        "DROP TABLE dwd_trade_order_detail",
        "TRUNCATE TABLE dwd_trade_order_detail",
        "ALTER TABLE dwd_trade_order_detail ADD COLUMN x INT",
        "CREATE TABLE evil (id INT)",
        "GRANT SELECT_PRIV ON ecommerce.* TO 'x'",
        "SET GLOBAL something = 1",
        "USE mysql",
        "CALL some_proc()",
        "LOAD DATA INFILE '/etc/passwd' INTO TABLE t",
        "SELECT order_id FROM dwd_trade_order_detail; DROP TABLE dwd_trade_order_detail",
        "SELECT order_id FROM dwd_trade_order_detail -- comment",
        "SELECT /* comment */ order_id FROM dwd_trade_order_detail",
        "SELECT order_id # comment\nFROM dwd_trade_order_detail",
        "SELECT order_id FROM dwd_trade_order_detail INTO OUTFILE '/tmp/x'",
    ],
)
def test_dangerous_sql_is_rejected(sql: str) -> None:
    with pytest.raises(SqlRejected) as excinfo:
        validate_select(sql, max_limit=100)
    assert excinfo.value.code in {
        "NOT_SELECT", "MULTI_STATEMENT", "COMMENT", "FORBIDDEN_KEYWORD",
        "TABLE_NOT_ALLOWED", "NO_TABLE",
    }


def test_unlisted_table_is_rejected() -> None:
    """白名单之外的表一律拒绝（例如系统库或未授权业务表）。"""
    with pytest.raises(SqlRejected) as excinfo:
        validate_select("SELECT * FROM mysql.user", max_limit=10)
    assert excinfo.value.code == "NOT_SELECT" or excinfo.value.code == "TABLE_NOT_ALLOWED"


def test_unknown_business_table_is_rejected() -> None:
    with pytest.raises(SqlRejected) as excinfo:
        validate_select("SELECT id FROM secret_salary_table", max_limit=10)
    assert excinfo.value.code == "TABLE_NOT_ALLOWED"
    assert "secret_salary_table" in excinfo.value.detail


def test_select_without_table_is_rejected() -> None:
    with pytest.raises(SqlRejected) as excinfo:
        validate_select("SELECT 1", max_limit=10)
    assert excinfo.value.code == "NO_TABLE"


def test_empty_sql_is_rejected() -> None:
    with pytest.raises(SqlRejected) as excinfo:
        validate_select("   ", max_limit=10)
    assert excinfo.value.code == "EMPTY_SQL"


def test_schema_qualified_table_is_allowed() -> None:
    """`ecommerce`.`ads_realtime_trade_1m` 这类写法应被识别为白名单表。"""
    result = validate_select(
        "SELECT window_start FROM `ecommerce`.`ads_realtime_trade_1m` LIMIT 3", max_limit=10
    )
    assert result.endswith("LIMIT 3")


def test_information_schema_metadata_query_is_allowed() -> None:
    """元数据接口需要读 information_schema，属于显式授权范围。"""
    result = validate_select(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        max_limit=10,
    )
    assert "information_schema.tables" in result


# ============================================================
# 3. 指标口径文档解析
# ============================================================
def test_metrics_doc_is_parsed() -> None:
    doc = load_metrics_doc(METRICS_MD)
    assert doc.version.startswith("V")
    assert len(doc.metrics) >= 20, f"解析到的指标太少：{len(doc.metrics)}"

    # 域必须被解析出来（曾经因为域标题正则漏掉编号后的点，
    # 所有指标的 domain 都退化成"通用"，而当时的测试没有断言域）
    domains = {m.domain for m in doc.metrics}
    assert {"交易", "流量", "类目"} <= domains, f"域解析不完整：{domains}"

    index = doc.by_field()
    for field in ("gmv", "order_cnt", "avg_order_amount", "payment_success_rate",
                  "refund_rate", "uv", "pv", "buy_rate", "total_quantity"):
        assert field in index, f"缺少指标字段 {field}"

    gmv = index["gmv"]
    assert gmv.domain == "交易", f"gmv 主口径应属于交易域，实际 {gmv.domain}"
    assert gmv.table == "ads_realtime_trade_1m"
    assert "SUM(amount)" in gmv.formula
    assert gmv.null_policy == "无事件补 0"

    # 「同上」必须还原成真实表名，否则血缘信息不可用
    assert all(m.table and m.table != "同上" for m in doc.metrics), "存在未还原的「同上」"

    # 比率类指标的空值约定必须是 NULL 而不是 0
    assert index["payment_success_rate"].null_policy == "分母为 0 时 NULL"
    assert index["refund_rate"].null_policy == "分母为 0 时 NULL"


def test_metrics_doc_conventions_are_parsed() -> None:
    doc = load_metrics_doc(METRICS_MD)
    names = {c.name for c in doc.conventions}
    assert any("窗口" in n for n in names), f"未解析到窗口约定：{names}"
    assert any("分母" in n for n in names), f"未解析到空值约定：{names}"


def test_metrics_doc_missing_file_raises() -> None:
    with pytest.raises(ValueError):
        load_metrics_doc(REPO_ROOT / "sql" / "metadata" / "not-exist.md")
