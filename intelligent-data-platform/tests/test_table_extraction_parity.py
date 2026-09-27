"""表名提取的**跨模块一致性**测试（Sprint 13A 修复的血缘漂移）。

## 这个测试防的是什么

项目里有**两份**表名提取实现，是**有意**分开的：

| 实现 | 位置 | 谁在用 |
| --- | --- | --- |
| 权威实现 | `services/api/app/sqlguard.py::extract_tables` | 守卫的**表白名单校验** + 数据服务的**血缘**（`tables` 字段） |
| Agent 侧副本 | `services/agent/app/tools.py::extract_tables` | Agent 回答里的 `tables`（"我用了哪些表"） |

之所以不共用一份：两个服务是**独立部署单元**，`services/api/app` 与
`services/agent/app` **同名**（见 `tests/conftest.py` 的说明），
且 Agent 进程里出现守卫模块会给人"Agent 也持有权限判断"的错觉 ——
真实边界是"Agent 只发 HTTP"。

代价是两份规则**可能漂移**。Sprint 13A 实测就漂了：守卫侧修好逗号连接后，
Agent 侧仍是旧的一条正则，于是同一条 SQL 出现

    sqlguard → ['dwd_trade_order_detail', 'dim_user']
    Agent    → ['dwd_trade_order_detail']            ← 少一张

危害是**审计失真**：回答里少报了自己读过的表。而当时的验收
（`scripts/verify-sprint-10.sh` 第 5 步）只逐字段比对 `rows`、**不比 `tables`**，
所以这种漂移**不会被任何现有断言抓到**。

## 设计取舍：为什么不比"集合"而比"列表"

两个用途的语义不同：
  * 校验只关心"有没有未授权的表" → 集合语义就够；
  * 血缘要给人看"读了哪些表、按什么顺序" → **顺序**也是信息。

因此这里断言**列表逐项相等**（含顺序）。比集合更严，能抓住
"顺序漂移"这类更隐蔽的不一致；若日后某侧有意改成不同顺序，
这个测试会红 —— 那正是希望被讨论的时刻，而不是让它悄悄发生。

## 为什么不测"能不能导入对方"

不测。两边**故意**不互相 import（见上表），本测试只做**行为等价**断言，
不改变部署边界。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_agent_extract_tables():
    """按**文件路径**加载 Agent 侧实现。

    为什么不能用 `from app.tools import extract_tables`：
      `tests/conftest.py` 把 `services/agent` 放进了 `sys.path`，
      但本测试还要同时拿到 `services.api.app.sqlguard`，
      而两边的包名都是 `app` —— 直接按包名导入必然会撞车。
      按路径加载是唯一能**同时**拿到两份实现的方式。
    """
    pkg = types.ModuleType("_parity_agent_app")
    pkg.__path__ = [str(REPO_ROOT / "services" / "agent" / "app")]
    sys.modules["_parity_agent_app"] = pkg
    spec = importlib.util.spec_from_file_location(
        "_parity_agent_app.tools", REPO_ROOT / "services" / "agent" / "app" / "tools.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_parity_agent_app.tools"] = module
    spec.loader.exec_module(module)
    return module.extract_tables


def _load_guard_extract_tables():
    """按**文件路径**加载权威实现（理由同上）。"""
    spec = importlib.util.spec_from_file_location(
        "_parity_sqlguard", REPO_ROOT / "services" / "api" / "app" / "sqlguard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_parity_sqlguard"] = module
    spec.loader.exec_module(module)
    return module.extract_tables


agent_extract_tables = _load_agent_extract_tables()
guard_extract_tables = _load_guard_extract_tables()


# ----------------------------------------------------------------------
# 语料：分成四组，每组都对应一类"曾经出错或容易出错"的写法
# ----------------------------------------------------------------------
COMPARISON_CORPUS: list[tuple[str, str]] = [
    # ---- 组 1：逗号连接（隐式 CROSS JOIN）—— 本次漂移的正主 ----
    ("comma_two_tables",
     "SELECT 1 FROM dwd_trade_order_detail a, dim_user b WHERE a.user_id = b.user_id"),
    ("comma_three_tables",
     "SELECT 1 FROM dwd_trade_order_detail a, dim_user b, dim_product c"),
    ("comma_with_schema",
     "SELECT 1 FROM dwd_trade_order_detail a, ecommerce.dim_user b"),
    ("comma_no_alias",
     "SELECT 1 FROM dwd_trade_order_detail, dim_user"),
    ("comma_multiline",
     "SELECT 1\nFROM dwd_trade_order_detail a,\n     dim_user b\nWHERE a.user_id = b.user_id"),
    ("comma_then_where_and_group",
     "SELECT a.user_id, COUNT(*) AS c FROM dwd_trade_order_detail a, dim_user b "
     "WHERE a.user_id = b.user_id GROUP BY a.user_id ORDER BY c DESC"),

    # ---- 组 2：显式 JOIN（原有能力，防回退） ----
    ("join_inner",
     "SELECT 1 FROM dwd_trade_order_detail a JOIN dim_user b ON a.user_id = b.user_id"),
    ("join_left",
     "SELECT 1 FROM dwd_trade_order_detail a LEFT JOIN dim_user b ON a.user_id = b.user_id"),
    ("join_using",
     "SELECT 1 FROM dwd_trade_order_detail JOIN dim_user USING (user_id)"),
    ("join_three",
     "SELECT 1 FROM a1 JOIN b1 ON a1.x = b1.x JOIN c1 ON b1.y = c1.y"),
    ("join_comma_mixed",
     "SELECT 1 FROM dwd_trade_order_detail a, dim_user b JOIN dim_product c ON b.x = c.x"),

    # ---- 组 3：逗号**不是**表分隔符（防"见逗号就当表"的过度抽取） ----
    ("select_multi_column",
     "SELECT a.user_id, a.order_id, b.gender FROM dwd_trade_order_detail a, dim_user b"),
    ("function_args_comma",
     "SELECT COALESCE(a.gmv, 0), SUM(b.cnt) FROM ads_realtime_trade_1m a, dim_user b"),
    ("case_when_commas",
     "SELECT CASE WHEN a.gmv > 0 THEN 1 ELSE 0 END AS f, b.x FROM t1 a, t2 b"),
    ("single_table_multi_column",
     "SELECT user_id, gender, age, city FROM dim_user"),

    # ---- 组 4：结构与写法 ----
    ("subquery_in_where",
     "SELECT COUNT(*) AS c FROM dwd_trade_order_detail "
     "WHERE product_id IN (SELECT product_id FROM dim_product)"),
    ("subquery_in_from",
     "SELECT COUNT(*) AS c FROM (SELECT user_id FROM dim_user) t"),
    ("backtick_schema",
     "SELECT 1 FROM `ecommerce`.`dwd_trade_order_detail` a, `ecommerce`.`dim_user` b"),
    ("bracket_table",
     "SELECT 1 FROM [dwd_trade_order_detail] a, [dim_user] b"),
    ("uppercase_keywords",
     "SELECT 1 FROM DWD_TRADE_ORDER_DETAIL A, DIM_USER B WHERE A.USER_ID = B.USER_ID"),
    ("newline_and_tabs",
     "SELECT 1 FROM\tdwd_trade_order_detail\ta,\n\tdim_user\tb"),
    ("trailing_limit",
     "SELECT 1 FROM dwd_trade_order_detail a, dim_user b LIMIT 10"),
    ("union_two_selects",
     "SELECT 1 AS c FROM dim_user UNION SELECT 2 AS c FROM dim_product"),
    ("empty_sql", ""),
    ("whitespace_only", "   \n\t  "),
    ("no_from", "SELECT 1 AS c"),
    ("as_alias",
     "SELECT 1 FROM dwd_trade_order_detail AS a, dim_user AS b"),
    ("same_table_twice",
     "SELECT 1 FROM dim_user a, dim_user b"),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "sql"),
    COMPARISON_CORPUS,
    ids=[case for case, _ in COMPARISON_CORPUS],
)
def test_agent_and_guard_extract_the_same_tables(case: str, sql: str) -> None:
    """Agent 侧副本必须与权威实现**逐项相等**（含顺序）。

    这是防漂移的主断言：任何一侧改了规则而另一侧没跟上，这里立刻变红。
    """
    guard = guard_extract_tables(sql)
    agent = agent_extract_tables(sql)
    assert agent == guard, (
        f"[{case}] 表名提取发生漂移：\n"
        f"  sqlguard(权威) = {guard}\n"
        f"  agent(副本)    = {agent}\n"
        f"  SQL: {sql}\n"
        "处置：把两侧算法对齐（权威实现是 services/api/app/sqlguard.py 的 extract_tables），"
        "不要只改一边 —— 这正是 Sprint 13A 修复的缺口。"
    )


@pytest.mark.unit
def test_comma_join_tables_are_both_reported() -> None:
    """逗号连接的第二张表**必须**被报出来（回归锁）。

    这是 Sprint 13A 那个 P0 的**血缘侧**判据：守卫侧已经能拦住了，
    这里锁住"报得全"。少报一张表 = 审计失真。
    """
    sql = (
        "SELECT 1 FROM ecommerce.dwd_trade_order_detail a, "
        "ecommerce.test_connection b LIMIT 3"
    )
    assert guard_extract_tables(sql) == ["dwd_trade_order_detail", "test_connection"]
    assert agent_extract_tables(sql) == ["dwd_trade_order_detail", "test_connection"]


@pytest.mark.unit
def test_comma_in_select_list_is_not_treated_as_table_separator() -> None:
    """反向判据：`SELECT a, b FROM t` 的逗号是**列分隔**，不能抽出多余表。

    没有这条，把提取器改"宽"就能让上面那条通过 —— 但会引入
    "多列查询被报成多表"的血缘失真（与漏报同一类问题的另一个方向）。
    """
    sql = "SELECT a.user_id, a.order_id, b.gender FROM dwd_trade_order_detail a, dim_user b"
    expected = ["dwd_trade_order_detail", "dim_user"]
    assert guard_extract_tables(sql) == expected
    assert agent_extract_tables(sql) == expected


@pytest.mark.unit
def test_order_is_preserved_so_parity_check_is_meaningful() -> None:
    """顺序也要一致 —— 血缘是给人看的，"先读哪张表"是信息。

    这条同时证明下面的"比集合"退化写法**不是**本测试的判据：
    若哪天有人把参数化成集合比较，顺序漂移就会漏过。
    """
    sql = "SELECT 1 FROM dim_user a, dwd_trade_order_detail b"
    assert guard_extract_tables(sql) == ["dim_user", "dwd_trade_order_detail"]
    assert agent_extract_tables(sql) == ["dim_user", "dwd_trade_order_detail"]


@pytest.mark.unit
def test_both_implementations_dedupe_preserving_first_occurrence() -> None:
    """同一张表出现多次只报一次，且保留**首次**出现的顺序。"""
    sql = "SELECT 1 FROM dim_user a, dim_user b, dwd_trade_order_detail c"
    expected = ["dim_user", "dwd_trade_order_detail"]
    assert guard_extract_tables(sql) == expected
    assert agent_extract_tables(sql) == expected
