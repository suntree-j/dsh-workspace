"""SQL 安全守卫（服务层唯一允许构造 SQL 的入口）。

对应 AGENTS.md 第 10.2 / 10.3 节：
    只允许 SELECT；拒绝多语句、注释注入、DDL/DML；
    必须带 LIMIT；必须能说明数据来源。

设计取舍：
    这里做的是**白名单式**校验（表名必须在白名单里、语句必须是 SELECT），
    而不是黑名单式"禁止某些关键字" —— 黑名单永远列不全。
    参数值一律走数据库驱动的占位符绑定，绝不字符串拼接。

本模块是**纯函数**，不依赖数据库，因此可以完整跑单元测试
（tests/test_sql_guard.py）。
"""

from __future__ import annotations

import re

__all__ = ["SqlRejected", "validate_select", "extract_tables", "ALLOWED_TABLES"]


class SqlRejected(ValueError):
    """SQL 未通过安全校验。code 用于接口返回，便于前端与测试断言。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# ------------------------------------------------------------
# 白名单：只允许查询这些表（全部在 ecommerce 库内）
# 分层前缀即权限边界 —— 新增表必须显式加进来，避免"顺手查了别的库"
# ------------------------------------------------------------
BUSINESS_TABLES = {
    # DWD 明细层
    "dwd_trade_order_detail",
    "dwd_trade_payment_detail",
    "dwd_trade_refund_detail",
    "dwd_traffic_behavior_detail",
    # DWS 汇总层
    "dws_traffic_overview_1m",
    # ADS 应用层（实时链路）
    "ads_realtime_trade_1m",
    "ads_realtime_traffic_1m",
    "ads_realtime_category_1m",
    # 维表
    "dim_product",
    "dim_user",
    # --------------------------------------------------------
    # ADS 应用层（离线链路，Sprint 3）
    #
    # 这些表在**另一个库** lakehouse_ads 里（见 sql/doris/30_batch_ads_tables.sql）。
    # 为什么仍放进同一份白名单：
    #   "哪些表允许被查"由业务属性决定，与库名无关；
    #   库名在 SQL 里由 repository 常量写死，用户输入无法影响它。
    #   把库名也做成可配置的校验项，只会增加一处可能与实际部署不一致的地方。
    # --------------------------------------------------------
    "ads_batch_trade_1m",
    "ads_batch_trade_1d",
    "ads_batch_category_1m",
    "ads_batch_category_1d",
    # 批流对账结果（Sprint 3）：服务层只读，用于展示"两链路是否一致"
    "ads_reconcile_trade_1m",
    "ads_reconcile_summary",
}

# 元数据查询（只在 /meta/tables 接口里由本服务自己拼接，不接受用户输入）
METADATA_TABLES = {"tables", "columns"}

ALLOWED_TABLES = BUSINESS_TABLES | METADATA_TABLES

# 明确禁止出现在语句中的关键字（即使以白名单表为目标也不允许）
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "set", "use", "call", "load", "export", "import",
    "replace", "rename", "backup", "restore", "kill", "shutdown",
    "into outfile", "into dumpfile", "load_file", "benchmark", "sleep",
)

# 注释与多语句：任何情况下都不允许（注释注入是绕过校验的常见手法）
_FORBIDDEN_PATTERNS = (
    (re.compile(r";"), "MULTI_STATEMENT", "不允许出现分号（多语句）"),
    (re.compile(r"--"), "COMMENT", "不允许 SQL 注释（--）"),
    (re.compile(r"/\*"), "COMMENT", "不允许 SQL 注释（/* */）"),
    (re.compile(r"\*/"), "COMMENT", "不允许 SQL 注释（/* */）"),
    (re.compile(r"#"), "COMMENT", "不允许 SQL 注释（#）"),
    # !! 为什么这条规则要用"顺序无关"的写法 !!
    #
    #   原写法是 `\bunion\s+all\s+select\b.*\bfrom\s+information_schema\b`，
    #   它只覆盖"information_schema 出现在 UNION **后面**那一支"的情形。
    #   实测缺口（Sprint 12 用 xfail(strict=True) 固化过）：
    #
    #     SELECT table_name FROM information_schema.tables
    #     UNION ALL SELECT gmv FROM ecommerce.ads_realtime_trade_1m
    #
    #   第一支就是元数据探测，却因为**语序不同**而被放行。
    #   教训很直接：**判据一旦写成"按顺序匹配"，同一件事换个语序就绕过去了。**
    #   所以改成组合判据 —— 只要同时出现 UNION 与 information_schema 就拒绝，
    #   与两者谁先谁后无关（lookahead 不消耗字符，因此与位置无关）。
    #
    #   注意：**单独的 information_schema 查询是允许的**（Agent 问"有没有这张表"
    #   会走它，且它本来就只暴露库表结构）。这里禁的是"拿 UNION 把元数据
    #   与业务数据拼在一起"这种探测手法。
    (re.compile(r"(?=.*\bunion\b)(?=.*\binformation_schema\b)", re.I | re.S),
     "METADATA_PROBE", "不允许通过 UNION 探测元数据"),
)

# 提取语句中出现的目标表：FROM / JOIN 后面的标识符，**以及 FROM 子句内逗号分隔的后续表**。
# 需要支持 `库`.`表`、库.表、`表`、[表] 等写法 —— 少识别一种写法就等于少一道防线，
# 因此这里把"标识符"拆成可重复的"点分片段"。
_IDENT = r"(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w$]*)"

# !! 为什么不能只用一条 `\b(?:from|join)\s+(表名)` 正则（Sprint 13A 修的真实缺口）!!
#
#   旧实现就是那一条正则。它只认 `FROM` / `JOIN` **关键字**，
#   于是逗号连接（隐式 CROSS JOIN）里**第二张及以后的表永远不会被提取**：
#
#       SELECT b.id FROM ecommerce.dwd_trade_order_detail a,
#                        ecommerce.test_connection b LIMIT 3
#       → 旧实现 tables = ['dwd_trade_order_detail']  → 放行，且返回真实数据
#
#   而校验（`validate_select` 第 4 步）与血缘（`extract_tables`）**共用这套提取规则**，
#   所以"没识别到"同时等于**没校验**（权限边界被绕过）与**漏报血缘**（审计失真）。
#   线上铁证（2026-09-27）：单独查 `ecommerce.test_connection` → REJECT TABLE_NOT_ALLOWED；
#   与白名单表逗号连接后 → ALLOW 且 row_count=3。对抗用例见
#   `tests/test_sql_guard_adversarial.py` 第 7 节。
#
#   修法：**只在 FROM / JOIN 子句内部**按"表清单"的结构推进 ——
#   一个表项（表名 + 可选别名）后面跟逗号，逗号后面又是表项，就继续。
#   ⚠️ 关键是不能"见逗号就当表"：`SELECT a, b FROM t` 里的逗号是**列分隔**，
#   它前面的 `b` 不是表项（前面没有 FROM/JOIN），因此不会被当成表。
#   判据是结构位置，不是字符本身 —— 这也是"越权表放在第一个/中间/最后"
#   三种写法能被同一条规则覆盖的原因。
_TABLE_RE = re.compile(rf"({_IDENT}(?:\s*\.\s*{_IDENT})*)")

# FROM 子句的结束标志：出现这些关键字说明表清单已经结束，逗号不再可能是表分隔符。
# 说明：这里**有意只列"能跟在表清单后面"的子句与连接词**，靠"正向识别边界"而不是
# "排除所有可能"—— 后者永远列不全（本项目在 METADATA_PROBE 上已经吃过一次同样的亏）。
_CLAUSE_BOUNDARY = frozenset(
    {
        "where", "group", "order", "having", "limit", "union", "join", "inner",
        "left", "right", "full", "outer", "cross", "natural", "straight_join",
        "on", "using", "offset", "window", "qualify", "into", "for", "lateral",
    }
)

# 提取 LIMIT 子句。
# MySQL/Doris 有三种写法，必须分别处理（语义不同，混淆会把 offset 当 count）：
#   LIMIT count              —— 最常见
#   LIMIT count OFFSET skip  —— 标准 SQL
#   LIMIT skip, count        —— MySQL 方言
_LIMIT_COUNT_RE = re.compile(r"\blimit\s+(\d+)\s*$", re.I)
_LIMIT_OFFSET_RE = re.compile(r"\blimit\s+(\d+)\s+offset\s+(\d+)\s*$", re.I)
_LIMIT_COMMA_RE = re.compile(r"\blimit\s+(\d+)\s*,\s*(\d+)\s*$", re.I)


def _normalize_table(raw: str) -> str:
    """把 `ecommerce`.`ads_xxx` / ecommerce.ads_xxx / [ads_xxx] 统一成表名。"""
    last = re.split(r"\s*\.\s*", raw.strip())[-1]
    return last.strip().strip("`\"[]")


def _paren_depth(sql: str) -> list[int]:
    """每个字符位置上的**括号嵌套深度**（depth[i] = 第 i 个字符之前的深度）。

    为什么需要它：逗号在 SQL 里至少有三种含义 —— 列分隔、函数参数分隔、表分隔，
    而"表分隔"只可能出现在**当前这一层**的 FROM 子句里。有了深度表，
    才能回答"这个逗号与那个 FROM 是不是同一层"（子查询里的 FROM 要单独算）。
    """
    depths = [0] * (len(sql) + 1)
    depth = 0
    for index, char in enumerate(sql):
        depths[index] = depth
        if char == "(":
            depth += 1
        elif char == ")":
            depth = depth - 1 if depth > 0 else 0
    depths[len(sql)] = depth
    return depths


def _skip_spaces(sql: str, index: int) -> int:
    """跳过空白，返回下一个非空白字符的位置。"""
    while index < len(sql) and sql[index].isspace():
        index += 1
    return index


def _word_at(sql: str, index: int) -> str:
    """取 `index` 处（或其后的空白之后）的**单词**（小写）；不是单词则返回空串。

    用途：判断表项后面跟的是 `AS 别名` / 裸别名 / 子句关键字 / 逗号。
    """
    index = _skip_spaces(sql, index)
    match = re.match(r"[A-Za-z_]\w*", sql[index:])
    return match.group(0).lower() if match is not None else ""


def extract_tables(sql: str) -> list[str]:
    """提取语句里引用的表名（去重、保持出现顺序）。

    用途：把"这段 SQL 读了哪些表"写进响应的血缘信息与审计日志。
    与校验用的是**同一套提取规则**（`validate_select` 第 4 步直接调它），
    保证"校验了什么"和"报告了什么"永远一致 —— 如果两者用不同规则，
    就可能出现"校验放行了 A 表、血缘却报告 B 表"。

    提取范围（两类，缺一不可）：
        1. `FROM <表>` / `JOIN <表>` 后面紧跟的表名；
        2. **同一个 FROM 子句里用逗号分隔的后续表**（隐式 CROSS JOIN）。
           例：`FROM dwd_trade_order_detail a, ods_orders b` → 两张表都要报。
           这是 Sprint 13A 修的权限绕过缺口，见文件上方 `_TABLE_RE` 处的长注释。

    ⚠️ 逗号**只在"前面已经是一个表项"时才算表分隔符**：
        `SELECT a, b FROM t` 里的逗号前面是列（不是表项），因此不产生表名。
        "见逗号就当表"会让所有多列查询变成 `TABLE_NOT_ALLOWED` —— 那不是更安全，
        而是更快被人加白名单绕过。反向用例见 `tests/test_sql_guard_adversarial.py` 第 7 节。
    """
    seen: list[str] = []
    if not sql:
        return seen

    depths = _paren_depth(sql)

    def _add(raw: str) -> None:
        name = _normalize_table(raw)
        if name and name not in seen:
            seen.append(name)

    for keyword in re.finditer(r"\b(?:from|join)\b", sql, re.I):
        depth = depths[keyword.start()]
        # 从关键字之后开始，按"表清单"的结构推进：表名 [别名] (, 表名 [别名])*
        index = _skip_spaces(sql, keyword.end())
        while True:
            # 每一轮都先跳过空白：第一轮跳 `FROM` 与表名之间的，后续轮跳逗号与表名之间的。
            index = _skip_spaces(sql, index)
            match = _TABLE_RE.match(sql, index)
            if match is None:
                # 子查询（`FROM (SELECT ...)`）或写法无法识别 —— 停止本子句的推进，
                # **绝不猜**：猜错的代价是"把列名当表名"（误杀）或"漏一张表"（放行）。
                break
            _add(match.group(1))
            index = match.end()

            # 跳过表别名：`AS t` 或裸 `t`；`FROM t WHERE ...` 这类没有别名。
            # 判据是"这个单词在不在子句边界集合里"，因此 `FROM t WHERE ...`
            # 不会把 `where` 当别名吃掉（那会让后面的逗号误判成表分隔符）。
            index = _skip_spaces(sql, index)
            word = _word_at(sql, index)
            if word == "as":
                index = _skip_spaces(sql, index + 2)
                word = _word_at(sql, index)
            if word and word not in _CLAUSE_BOUNDARY:
                index += len(word)

            # 表项之间必须是逗号，且这个逗号与 FROM 在**同一层括号**内；
            # 括号内的逗号（函数参数 / 子查询内部）不是表分隔符。
            index = _skip_spaces(sql, index)
            if index >= len(sql) or sql[index] != "," or depths[index] != depth:
                break
            index += 1

    return seen


def validate_select(sql: str, max_limit: int = 1000) -> str:
    """校验并规范化一条 SELECT 语句。

    返回：补上/收敛 LIMIT 之后的语句（仍应使用参数绑定执行）。
    抛出：SqlRejected（带 code / message / detail）。
    """
    if not sql or not sql.strip():
        raise SqlRejected("EMPTY_SQL", "SQL 为空")

    text = sql.strip()
    lowered = text.lower()

    # 1) 必须以 SELECT 开头（不接受 WITH / SHOW / EXPLAIN 等）
    if not lowered.startswith("select"):
        raise SqlRejected(
            "NOT_SELECT",
            "只允许 SELECT 查询",
            f"实际语句以 {text.split()[0]!r} 开头",
        )

    # 2) 注释与多语句
    for pattern, code, message in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            raise SqlRejected(code, message, f"命中：{pattern.pattern}")

    # 3) 危险关键字（带词边界，避免误伤列名如 create_time）
    for keyword in _FORBIDDEN_KEYWORDS:
        pattern = r"\b" + keyword.replace(" ", r"\s+") + r"\b"
        if re.search(pattern, lowered):
            raise SqlRejected("FORBIDDEN_KEYWORD", f"语句中包含禁止的关键字：{keyword}")

    # 4) 表白名单
    #
    # !! 这里直接调 extract_tables，而不是自己再写一遍提取 !!
    #   旧实现是"校验"与"血缘"各写一次同样的 finditer —— 表面同一套规则，
    #   实际是**两份代码**。而逗号连接缺口恰好证明了这有多危险：
    #   修好血缘却忘了改校验（或反过来），会出现"血缘报了表、校验却没看"或
    #   "校验放行、血缘漏报"这类**只在一边成立**的状态，比两边都错更难发现。
    #   现在两者共用同一个函数：改了提取规则，校验与血缘**必然同时**改变。
    tables = set(extract_tables(text))
    if not tables:
        raise SqlRejected("NO_TABLE", "语句中没有可识别的表名")
    illegal = sorted(t for t in tables if t not in ALLOWED_TABLES)
    if illegal:
        raise SqlRejected(
            "TABLE_NOT_ALLOWED",
            "查询了未被授权的表",
            f"未授权：{', '.join(illegal)}；允许的表见 sqlguard.ALLOWED_TABLES",
        )

    # 5) 强制 LIMIT：没有就补，太大就收
    if max_limit <= 0:
        raise SqlRejected("BAD_LIMIT", "max_limit 配置非法")

    # 5.1 LIMIT count OFFSET skip
    match = _LIMIT_OFFSET_RE.search(text)
    if match is not None:
        count = min(int(match.group(1)), max_limit)
        return f"{text[:match.start()]} LIMIT {count} OFFSET {int(match.group(2))}"

    # 5.2 LIMIT skip, count（MySQL 方言：前一个是偏移量）
    match = _LIMIT_COMMA_RE.search(text)
    if match is not None:
        count = min(int(match.group(2)), max_limit)
        return f"{text[:match.start()]} LIMIT {int(match.group(1))}, {count}"

    # 5.3 LIMIT count
    match = _LIMIT_COUNT_RE.search(text)
    if match is not None:
        return f"{text[:match.start()]} LIMIT {min(int(match.group(1)), max_limit)}"

    # 5.4 完全没有 LIMIT（例如 COUNT(*)）——补一个上限
    return f"{text} LIMIT {max_limit}"
