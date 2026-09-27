"""SQL 守卫的**对抗**用例（Sprint 12）。

## 与 `tests/test_sql_guard.py` 的分工

`test_sql_guard.py`（Sprint 6）逐条覆盖 `AGENTS.md` §10.2 的"必须拒绝"清单 ——
它回答的是"清单上的每一项都拦住了吗"。

本文件回答一个更不客气的问题：

    **清单以外**的写法能不能绕过？

即"攻击者知道守卫是怎么写的，于是专门挑它的形态下手"。这里按**绕过手法**分类，
而不是按关键字分类：

| 手法 | 思路 |
| --- | --- |
| 大小写与空白变形 | 正则大小写敏感或锚点写死 → 变形即漏 |
| 分隔符/标识符变形 | 表名提取正则少认一种写法 = 少一道防线 |
| 关键字藏在标识符里 | 用**误伤**换安全（反过来：用安全换误伤） |
| 元数据探测 | `information_schema` 是允许的，于是它成了最自然的跳板 |
| 注释与时序 | 注释本身就是"让解析器与数据库看到不同语句"的工具 |

## 本 Sprint 的处置方式（重要）

**发现的问题不在这里偷偷修**：`services/api/app/sqlguard.py` 是数据服务侧的
权限边界，改它属于另一个决定（Sprint 9 的 9.6 节已经记过一次同类事项）。
本文件的作用是：

    1. 把**已经拦住**的对抗用例固化成回归测试；
    2. 把**没拦住**的用 `xfail(strict=True)` 明确标出来 —— 它会在
       "某天被人修好了"时变成 XPASS 而**主动报错**，逼人回来更新结论。

`strict=True` 是关键：没有它，xfail 会变成一个"永远绿"的假测试，
而这条已知缺口就再也没人记得了。
"""

from __future__ import annotations

import pytest

from services.api.app.sqlguard import SqlRejected, extract_tables, validate_select

pytestmark = pytest.mark.unit

GOOD_TABLE = "dwd_trade_order_detail"


def _rejected(sql: str) -> SqlRejected:
    with pytest.raises(SqlRejected) as excinfo:
        validate_select(sql, max_limit=100)
    return excinfo.value


# ============================================================
# 1. 大小写与空白变形
# ============================================================
@pytest.mark.parametrize(
    "sql",
    [
        "DeLeTe FrOm dwd_trade_order_detail",
        "delete from dwd_trade_order_detail",
        "DELETE FROM dwd_trade_order_detail",
        "dElEtE\nFROM\ndwd_trade_order_detail",
    ],
)
def test_write_keywords_are_case_and_whitespace_insensitive(sql: str) -> None:
    """关键字匹配必须大小写不敏感，且不能被换行/制表符拆开。

    为什么单列一条：`_FORBIDDEN_KEYWORDS` 的匹配用的是 `lowered` 文本 +
    `\\b` 词边界 + `\\s+` 空白折叠。这三件事任何一件写错，
    都会漏掉上面四种写法中的某一种 —— 而它们是最容易想到的绕过方式。

    四种写法都以非 SELECT 开头，因此拒绝码是 `NOT_SELECT`（第一条判据就拦住了）。
    真正要证明的是"**变形不影响判定**"：四种都拒、且拒绝码一致。
    """
    assert _rejected(sql).code == "NOT_SELECT"


def test_write_keyword_in_the_middle_is_caught_by_keyword_rule() -> None:
    """写关键字出现在**语句中间**时，第一条判据（必须以 SELECT 开头）管不着，
    必须靠关键字规则拦住 —— 两条判据缺一不可。
    """
    assert _rejected(f"SELECT 1 FROM {GOOD_TABLE} UNION DELETE FROM {GOOD_TABLE}").code == (
        "FORBIDDEN_KEYWORD"
    )


@pytest.mark.parametrize("prefix", [" ", "   ", "\t", "\n", "\r\n", " \t\n "])
def test_leading_whitespace_does_not_hide_non_select(prefix: str) -> None:
    """前置空白不能让非 SELECT 语句通过（`lowered.startswith("select")` 用的是 trim 后的文本）。"""
    assert _rejected(f"{prefix}DROP TABLE {GOOD_TABLE}").code == "NOT_SELECT"


@pytest.mark.parametrize(
    "sql",
    [
        f"INSERT INTO {GOOD_TABLE} VALUES (1)",
        f"TRUNCATE TABLE {GOOD_TABLE}",
        f"ALTER TABLE {GOOD_TABLE} ADD COLUMN x INT",
        f"CREATE TABLE {GOOD_TABLE} (a INT)",
        f"GRANT SELECT ON {GOOD_TABLE} TO 'x'@'%'",
        f"REVOKE SELECT ON {GOOD_TABLE} FROM 'x'@'%'",
        "SET GLOBAL max_connections = 1",
        "USE ecommerce",
        "CALL some_proc()",
        f"LOAD DATA INFILE '/etc/passwd' INTO TABLE {GOOD_TABLE}",
    ],
)
def test_ddl_dml_and_admin_statements_are_rejected(sql: str) -> None:
    """`AGENTS.md` §10.2 清单里的语句一律拒绝（不论目标表是否在白名单里）。"""
    assert _rejected(sql).code in ("NOT_SELECT", "FORBIDDEN_KEYWORD")


# ============================================================
# 2. 分隔符与标识符变形（表名提取的边界）
# ============================================================
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (f"SELECT 1 FROM `{GOOD_TABLE}`", [GOOD_TABLE]),
        (f"SELECT 1 FROM `ecommerce`.`{GOOD_TABLE}`", [GOOD_TABLE]),
        (f'SELECT 1 FROM "{GOOD_TABLE}"', [GOOD_TABLE]),
        (f"SELECT 1 FROM [{GOOD_TABLE}]", [GOOD_TABLE]),
        (f"SELECT 1 FROM ecommerce . {GOOD_TABLE}", [GOOD_TABLE]),
        (f"SELECT 1 FROM\n{GOOD_TABLE}", [GOOD_TABLE]),
        (f"SELECT 1 FROM {GOOD_TABLE} AS t", [GOOD_TABLE]),
    ],
)
def test_table_extraction_covers_identifier_variants(sql: str, expected: list[str]) -> None:
    """每一种合法的标识符写法都必须被**识别出来**。

    为什么这属于对抗用例而不是普通用例：
        表名提取正则少认一种写法 = 白名单校验少一道防线。
        "没识别到表"看起来只是"血缘信息为空"，而实际上意味着**校验被跳过**。
    """
    assert extract_tables(sql) == expected
    # 识别到了就必须校验通过（表在白名单里）
    assert GOOD_TABLE in validate_select(sql, max_limit=10)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM dwd_user_profile",
        "SELECT 1 FROM `dwd_user_profile`",
        "SELECT 1 FROM ecommerce.dwd_user_profile",
        "SELECT 1 FROM lakehouse_ads.dwd_user_profile",
        "SELECT 1 FROM [dwd_user_profile]",
    ],
)
def test_unlisted_table_in_any_notation_is_rejected(sql: str) -> None:
    """未授权表换一种写法也必须被拒（不能靠"只认裸表名"拦住）。"""
    assert _rejected(sql).code == "TABLE_NOT_ALLOWED"


def test_join_with_one_illegal_table_is_rejected() -> None:
    """JOIN 里只有一张表越权，整条语句也必须失败（不能只看第一张表）。"""
    sql = f"SELECT 1 FROM {GOOD_TABLE} j JOIN dwd_user_profile u ON 1 = 1"
    assert _rejected(sql).code == "TABLE_NOT_ALLOWED"


def test_multiple_statements_via_semicolon_are_rejected() -> None:
    """分号拼接（无论后面是 SELECT 还是写操作）一律拒绝。"""
    assert _rejected(f"SELECT 1 FROM {GOOD_TABLE}; SELECT 2").code == "MULTI_STATEMENT"
    assert _rejected(f"SELECT 1 FROM {GOOD_TABLE}; DROP TABLE {GOOD_TABLE}").code == "MULTI_STATEMENT"


def test_trailing_semicolon_is_also_rejected() -> None:
    """**已确认的取舍**：连结尾那个无害的分号也会被拒。

    记录在这里是为了让它是"已知行为"而不是"某天被当成 bug 修掉"：
    守卫用的是黑白名单式判断，无法区分"结尾分号"与"注入分号"，
    而放行结尾分号的代价是必须做词法分析（`;` 是否在字符串字面量里）。
    Agent 侧的对策是系统提示词明确要求"不要带分号"，两条路径都已验收通过。
    """
    assert _rejected(f"SELECT 1 FROM {GOOD_TABLE};").code == "MULTI_STATEMENT"


# ============================================================
# 3. 关键字藏在标识符里（误伤边界）
# ============================================================
@pytest.mark.parametrize(
    "column",
    ["create_time", "update_time", "delete_flag", "insert_time", "load_time", "set_time"],
)
def test_columns_containing_keywords_are_not_false_positives(column: str) -> None:
    """列名里含 `create` / `update` / `delete` 等字样**不该**被误伤。

    这一条与"必须拒绝"同样重要：守卫如果把合法查询误判为攻击，
    它会被人加白名单绕过，最终**真的**不再拦任何东西。
    `\\b` 词边界正是为这件事存在的 —— 这条用例锁住它。
    """
    sql = f"SELECT {column} FROM {GOOD_TABLE}"
    result = validate_select(sql, max_limit=10)
    assert result.endswith("LIMIT 10")


def test_identical_column_and_forbidden_keyword_boundaries() -> None:
    """反例：真的是关键字时必须拦住（证明上一条不是"词边界失效导致的假通过"）。"""
    assert _rejected(f"SELECT 1 FROM {GOOD_TABLE} WHERE 1=1 SET x=1").code == "FORBIDDEN_KEYWORD"


# ============================================================
# 4. 元数据探测
# ============================================================
def test_information_schema_alone_is_allowed_by_design() -> None:
    """`information_schema` 是**授权表**（服务侧 `/meta/tables` 自己也用它）。

    所以"它能被查"不是缺陷，是设计。真正要挡的是"借它当跳板"（下一条）。
    """
    result = validate_select(
        "SELECT table_name FROM information_schema.tables LIMIT 5", max_limit=100
    )
    assert "information_schema" in result


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT table_name FROM information_schema.tables UNION ALL SELECT gmv FROM ecommerce.ads_realtime_trade_1m",
        "SELECT 1 FROM information_schema.columns UNION SELECT 2 FROM ecommerce.ads_realtime_trade_1m",
        "SELECT 1 FROM information_schema.columns UNION ALL SELECT 2 FROM ecommerce.ads_realtime_trade_1m",
    ],
)
def test_reversed_union_probe_is_rejected(sql: str) -> None:
    """`information_schema` 写在 UNION **前面**的探测也必须被拒（**缺口已修**）。

    !! 这条用例的历史必须保留，它比用例本身更有价值 !!
        它最初是 `xfail(strict=True)` 记录的**已知缺口**：旧的 METADATA_PROBE 正则写成
        `union all select ... from information_schema`，只覆盖"元数据写在 UNION **后面**那一支"
        的顺序；反序拼接时两张表都在授权集合里，于是整条语句被放行。
        `strict=True` 的用意就是**修好那天会 XPASS 报错**，逼人回来更新结论 ——
        本次修复后它如期 XPASS，因此这里摘掉 xfail、改成正式的拒绝断言。

    修法：判据从"按顺序匹配"改成"**同时**出现 UNION 与 information_schema 就拒绝"
    （用 lookahead，与先后顺序无关），见 `services/api/app/sqlguard.py`。
    **教训**：判据一旦写成"按顺序匹配"，同一件事换个语序就绕过去了。

    注意：`information_schema` **单独**查询仍然允许（见上一条用例）——
    Agent 问"有没有这张表"要走它，禁的是"用 UNION 把元数据与业务数据拼起来"这种探测手法。
    """
    _rejected(sql)


# ============================================================
# 5. 注释与时序
# ============================================================
@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT 1 FROM {GOOD_TABLE} -- comment",
        f"SELECT 1 FROM {GOOD_TABLE} # comment",
        f"SELECT /* comment */ 1 FROM {GOOD_TABLE}",
        f"SELECT 1 FROM {GOOD_TABLE} /* c */ WHERE 1=1",
        f"-- leading comment\nSELECT 1 FROM {GOOD_TABLE}",
        f"/* leading */ SELECT 1 FROM {GOOD_TABLE}",
    ],
)
def test_comment_injection_is_rejected_everywhere(sql: str) -> None:
    """注释无论出现在开头、中间、结尾都必须被拒。

    为什么放在对抗用例里：注释是**让守卫与数据库看到不同语句**的经典工具
    （守卫看到的是截断后的文本，数据库看到的是拼接后的）。
    `--` / `/* */` / `#` 三种都要拦 —— 少拦一种，那种就是绕过的开口。
    """
    assert _rejected(sql).code in ("COMMENT", "NOT_SELECT")


def test_semicolon_inside_string_literal_is_rejected() -> None:
    """**已确认的取舍**：字符串字面量里的分号也会被拒。

    这是一个**误伤**（合法查询被拒），而不是漏洞。守卫不做词法分析，
    因此无法区分"字面量里的 `;`"与"注入的 `;`"。
    记录在此：它是已知代价，Agent 侧的行为是"被拒后改写 SQL 重试"。
    """
    assert _rejected(f"SELECT 'a;b' AS x FROM {GOOD_TABLE}").code == "MULTI_STATEMENT"


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT 1 FROM {GOOD_TABLE} INTO OUTFILE '/tmp/x'",
        f"SELECT 1 FROM {GOOD_TABLE} INTO DUMPFILE '/tmp/x'",
        f"SELECT LOAD_FILE('/etc/passwd') FROM {GOOD_TABLE}",
        f"SELECT BENCHMARK(1000000, MD5('x')) FROM {GOOD_TABLE}",
        f"SELECT SLEEP(10) FROM {GOOD_TABLE}",
    ],
)
def test_file_and_timing_attacks_are_rejected(sql: str) -> None:
    """读写文件、CPU 炸弹、拖时间——这三类与"取数"无关，必须一律拒绝。

    `SLEEP` / `BENCHMARK` 尤其重要：它们能把一次只读查询变成对 Doris 的
    **拒绝服务**（守卫的超时只保护进程，不保护集群）。
    """
    assert _rejected(sql).code == "FORBIDDEN_KEYWORD"


# ============================================================
# 6. LIMIT 强制（对抗面：绕过行数上限）
# ============================================================
def test_limit_cannot_be_removed_or_raised() -> None:
    """任何形态的 LIMIT 都会被收敛到 max_limit 以内。

    三种写法被守卫改写成的**形状不同**（这是刻意的：offset 与 count 的语义
    必须各自保留），所以断言的是"原来的大数字没了" + "上限值在结果里"，
    而不是断言某一种固定形状 —— 后者会把守卫的正确改写误报成缺陷。
    """
    cases = (
        f"SELECT order_id FROM {GOOD_TABLE} LIMIT 100000",
        f"SELECT order_id FROM {GOOD_TABLE} LIMIT 100000 OFFSET 0",
        f"SELECT order_id FROM {GOOD_TABLE} LIMIT 0, 100000",
    )
    for sql in cases:
        result = validate_select(sql, max_limit=50)
        assert "100000" not in result, f"没能收敛 LIMIT：{result}"
        assert "50" in result, f"上限值没进结果：{result}"
        assert result.count("LIMIT") == 1, f"改写后出现了多个 LIMIT：{result}"


@pytest.mark.parametrize("max_limit", [0, -1])
def test_illegal_max_limit_is_rejected_instead_of_silently_allowing(max_limit: int) -> None:
    """`max_limit` 配错时必须**报错**，不能变成"没有上限"。

    这是一次真实的绕过思路：如果 `max_limit<=0` 时走的是"不补 LIMIT"的分支，
    那么一次配置失误就等于关掉了行数上限，而且没有任何告警。

    断言的是**精确的拒绝码**（`BAD_LIMIT`）而不是"抛了异常就行" ——
    否则任何一条别的规则先拦下来，这条用例也会绿，而它要证明的事没被证明。
    """
    with pytest.raises(SqlRejected) as excinfo:
        validate_select(f"SELECT order_id FROM {GOOD_TABLE}", max_limit=max_limit)
    assert excinfo.value.code == "BAD_LIMIT"


def test_missing_limit_is_added_for_aggregate_queries() -> None:
    """聚合查询（天然一行）也要补 LIMIT —— 规则不因"看起来安全"而开口子。"""
    result = validate_select(
        f"SELECT COUNT(*) AS c FROM {GOOD_TABLE}", max_limit=200
    )
    assert result.endswith("LIMIT 200")


# ============================================================
# 7. FROM/JOIN 子句里的**逗号连接**（隐式 CROSS JOIN）—— 本 Sprint 修复的缺口
# ============================================================
BAD_TABLE = "ods_orders"


def test_comma_joined_table_bypass_was_a_real_gap() -> None:
    """!! 这条用例的历史必须保留，和 §4 的 reversed UNION 那条同样重要 !!

    缺口（Sprint 13A 审计发现、线上复现）：
        `_TABLE_RE` 只认 `FROM|JOIN` 关键字，**逗号后面的表永远不会被提取**；
        而校验与血缘 `extract_tables` 共用该正则 —— 所以"没识别到"同时意味着
        **没校验**（权限绕过）与**漏报血缘**（审计失真）。

    线上铁证（2026-09-27）：
        `SELECT id FROM ecommerce.test_connection LIMIT 3`            → REJECT TABLE_NOT_ALLOWED
        `SELECT b.id FROM ecommerce.dwd_trade_order_detail a,
                         ecommerce.test_connection b LIMIT 3`         → ALLOW + 返回真实数据，
                                                                        血缘只报 ['dwd_trade_order_detail']

    修法（`services/api/app/sqlguard.py`）：
        表名提取改成"扫出 FROM 子句里的**逗号分隔项**"——
        先按关键字定位 FROM / JOIN，再在该子句内按"表名（可带别名）+ 逗号"的
        **表清单结构**推进；逗号只在"它前面是一个表项"时才算分隔符。
        `SELECT a, b FROM t` 里的逗号是**列分隔**（前面不是表项），因此不受影响。

    下面几条参数化用例把该缺口的四种写法固化成**正式断言**。
    """
    sql = f"SELECT a.order_id FROM {GOOD_TABLE} a, {BAD_TABLE} b"
    # ① 血缘必须报出两张表（修复前只报一张）
    assert extract_tables(sql) == [GOOD_TABLE, BAD_TABLE]
    # ② 白名单必须拦住（修复前整条放行）
    assert _rejected(sql).code == "TABLE_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("name", "sql"),
    [
        (
            "两表逗号连接",
            f"SELECT a.order_id FROM {GOOD_TABLE} a, {BAD_TABLE} b WHERE a.id = b.id",
        ),
        (
            "三表逗号连接（越权表在中间）",
            f"SELECT 1 FROM {GOOD_TABLE} a, {BAD_TABLE} b, dim_user c",
        ),
        (
            "三表逗号连接（越权表在最后）",
            f"SELECT 1 FROM {GOOD_TABLE} a, dim_user b, {BAD_TABLE} c",
        ),
        (
            "逗号连接 + 子查询里也有越权表",
            f"SELECT 1 FROM {GOOD_TABLE} a, (SELECT id FROM {BAD_TABLE}) s",
        ),
        (
            "换行写法的逗号连接",
            f"SELECT 1\nFROM {GOOD_TABLE} a,\n     {BAD_TABLE} b\nWHERE 1 = 1",
        ),
        (
            "多空格 / 制表符写法的逗号连接",
            f"SELECT 1 FROM {GOOD_TABLE} a\t,\t\t{BAD_TABLE} b",
        ),
        (
            "库名前缀 + 反引号的逗号连接",
            f"SELECT 1 FROM `ecommerce`.`{GOOD_TABLE}` a, `ecommerce`.`{BAD_TABLE}` b",
        ),
        (
            "越权表紧跟在 FROM 后的逗号连接",
            f"SELECT 1 FROM {BAD_TABLE} a, {GOOD_TABLE} b",
        ),
    ],
)
def test_comma_joined_tables_are_extracted_and_rejected(name: str, sql: str) -> None:
    """逗号连接的**每一种写法**都必须既报血缘、又拦得住。

    为什么参数化到 8 种写法而不是只留一条：审计已经吃过一次教训 ——
    `METADATA_PROBE` 的旧判据写成"按顺序匹配"，于是"同一件事换个语序"就绕过去了
    （见 §4 的历史）。逗号连接同理：换行、制表符、库名前缀、
    越权表放前面/中间/后面，任何一种没被覆盖，那种写法就是活的绕过口子。
    """
    tables = extract_tables(sql)
    assert BAD_TABLE in tables, f"{name}：血缘漏报了 {BAD_TABLE}，实际 tables={tables}"
    assert _rejected(sql).code == "TABLE_NOT_ALLOWED", f"{name}：未被白名单拦住"


@pytest.mark.parametrize(
    ("name", "sql"),
    [
        # 列分隔的逗号：绝不能当表名（"见逗号就当表"是最容易犯的过度拦截错误）
        ("SELECT 列清单", f"SELECT order_id, user_id, gmv FROM {GOOD_TABLE}"),
        ("SELECT 列清单 + 别名", f"SELECT order_id AS oid, gmv AS amount FROM {GOOD_TABLE}"),
        ("GROUP BY 逗号列表", f"SELECT 1 FROM {GOOD_TABLE} GROUP BY order_id, user_id"),
        ("ORDER BY 逗号列表", f"SELECT 1 FROM {GOOD_TABLE} ORDER BY order_id, gmv DESC"),
        ("COUNT(DISTINCT) + 聚合", f"SELECT COUNT(DISTINCT user_id), SUM(gmv) FROM {GOOD_TABLE}"),
        ("函数参数里的逗号", f"SELECT COALESCE(gmv, 0), IFNULL(user_id, 0) FROM {GOOD_TABLE}"),
        ("子查询在 FROM 里 + 外层列清单", f"SELECT order_id, gmv FROM (SELECT * FROM {GOOD_TABLE}) t"),
        ("子查询在 WHERE 里", f"SELECT 1 FROM {GOOD_TABLE} WHERE user_id IN (SELECT id FROM dim_user)"),
    ],
)
def test_commas_outside_the_from_clause_are_not_tables(name: str, sql: str) -> None:
    """反向用例（防**过度拦截**）：列分隔的逗号不能变成"表"。

    !!! 这条与上面那条同等重要 !!!
        "见逗号就当表"会把 `SELECT a, b FROM t` 里的 `b` 当成表名，
        于是**所有**多列查询都变成 `TABLE_NOT_ALLOWED`。
        这种守卫不会更难绕过，只会更快被人加白名单绕过 —— 最终真的不再拦任何东西。
        所以：逗号只有在"它前面是一个**表项**"时才是表分隔符。
    """
    tables = extract_tables(sql)
    assert all(t in ("dwd_trade_order_detail", "dim_user") for t in tables), (
        f"{name}：逗号被误当成表名，tables={tables}"
    )
    # 放行必须仍然成立（这些查询全在白名单内）
    validate_select(f"{sql} LIMIT 10" if "LIMIT" not in sql.upper() else sql, max_limit=10)


@pytest.mark.parametrize(
    ("name", "sql", "expected"),
    [
        (
            "显式 INNER JOIN 两张白名单表",
            f"SELECT 1 FROM {GOOD_TABLE} a JOIN dim_user b ON a.user_id = b.user_id",
            [GOOD_TABLE, "dim_user"],
        ),
        (
            "显式逗号连接两张白名单表（修复后必须仍放行）",
            f"SELECT 1 FROM {GOOD_TABLE} a, dim_user b WHERE a.user_id = b.user_id",
            [GOOD_TABLE, "dim_user"],
        ),
        (
            "三张白名单表逗号连接",
            f"SELECT 1 FROM {GOOD_TABLE} a, dim_user b, dim_product c",
            [GOOD_TABLE, "dim_user", "dim_product"],
        ),
        (
            "逗号连接 + WHERE 里再带一个白名单表",
            f"SELECT 1 FROM {GOOD_TABLE} a, dim_user b WHERE a.product_id IN (SELECT product_id FROM dim_product)",
            [GOOD_TABLE, "dim_user", "dim_product"],
        ),
    ],
)
def test_comma_and_explicit_joins_of_whitelisted_tables_still_pass(
    name: str, sql: str, expected: list[str]
) -> None:
    """**正常查询必须仍放行，且血缘必须完整**（防过度拦截的正面断言）。

    修复一个安全缺口最容易的副作用就是"顺手把合法用法也一起拦掉"。
    这条用例专门盯住它：显式 JOIN 与逗号连接（表全在白名单内）都必须放行，
    且 `extract_tables` 必须把**每一张**表都报出来（血缘完整）。
    """
    assert extract_tables(sql) == expected, f"{name}：血缘不完整"
    result = validate_select(sql, max_limit=10)
    assert result.endswith("LIMIT 10") or "LIMIT 10" in result, f"{name}：未补 LIMIT"
